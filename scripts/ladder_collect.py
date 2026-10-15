#!/usr/bin/env python3
"""Pack and merge sharded harness-lift-ladder runs (CI: .github/workflows/ladder.yml).

Each CI shard runs scripts/ablation_awos_vs_aider.py with --shard I/N into its own
out dir. Two steps move the results back into one out dir that `combine` reads:

  stage   (on the runner) copy the small files of one shard's out dir into an
          artifact dir: results (job_series_*.json), report.md, health.json,
          run.log, driver.log, llm_calls.jsonl, each arm's state/report.json and
          the driver's stdout (*.log at the top). Never a project/ copy. Writes
          shard.json (assigned issues, done issues, spend).

  merge   (on the laptop) merge the shard dirs from `gh run download` into one
          out dir. Files keep their r<rep>/<issue>/... paths; the same path in
          two shards must be identical. Top-level files of a shard go under
          _shards/<shard>/. Prints assigned vs done issues and the total spend.

  ci-config   print ci/ladder.env as GITHUB_OUTPUT lines (shard list, per-shard cap)
  check-data  check an extracted series: READY, issue count, cached preflight

    gh run download <run-id> -D ~/.awos-harness/ladder_haiku_ci_dl
    python scripts/ladder_collect.py merge --out ~/.awos-harness/ladder_haiku \\
        --arms off,mini ~/.awos-harness/ladder_haiku_ci_dl
    python scripts/ablation_awos_vs_aider.py combine --out ~/.awos-harness/ladder_haiku --arms off,mini

merge accepts shard dirs directly or parents of them (`gh run download` puts
several artifacts in <dir>/<artifact-name>/, a single one straight into <dir>).
"""
from __future__ import annotations

import argparse
import filecmp
import fnmatch
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = "shard.json"
SHARDS_DIR = "_shards"         # not r*, so `combine` never mistakes it for a repeat
MAX_FILE_BYTES = 50 * 1024 * 1024

# Relative to a shard's out dir. Any path with a `project` component is skipped.
KEEP_NAMES = ("job_series_*.json", "report.md", "health.json", "run.log", "driver.log",
              "llm_calls.jsonl")
KEEP_TOP = ("*.log", "*.txt", MANIFEST)


def _ablation():
    spec = importlib.util.spec_from_file_location(
        "ablation_for_ladder_collect", REPO / "scripts" / "ablation_awos_vs_aider.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def keep(rel: Path) -> bool:
    """Whether a file (path relative to the shard out dir) goes into the artifact."""
    if "project" in rel.parts[:-1] or "__pycache__" in rel.parts:
        return False
    if len(rel.parts) == 1:
        return any(fnmatch.fnmatch(rel.name, p) for p in KEEP_TOP)
    if rel.name == "report.json" and rel.parent.name == "state":
        return True
    return any(fnmatch.fnmatch(rel.name, p) for p in KEEP_NAMES)


def set_arms(ab, arms: str) -> None:
    ab.ARMS = tuple(x.strip() for x in arms.split(",") if x.strip())


def stage(out: Path, dest: Path, series_root: Path | None = None, shard: str | None = None,
          limit: int | None = None, arms: str = "off,mini", repeat: int = 1) -> dict:
    ab = _ablation()
    set_arms(ab, arms)
    dest.mkdir(parents=True, exist_ok=True)
    copied, skipped = 0, []
    if out.is_dir():
        for f in sorted(out.rglob("*")):
            if not f.is_file() or f.is_symlink():
                continue
            rel = f.relative_to(out)
            if not keep(rel):
                continue
            if f.stat().st_size > MAX_FILE_BYTES:
                skipped.append(str(rel))
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            copied += 1
    assigned: list[str] = []
    if series_root and series_root.is_dir():
        assigned = [x for _, x in ab.select(ab.discover(series_root), ab.parse_shard(shard), limit)]
    present = sorted({p.name for p in out.glob("r*/*") if p.is_dir()}) if out.is_dir() else []
    done = sorted({i for i in present for rep in range(1, repeat + 1)
                   if ab.done(ab.load_rows(ab.result_file(out, rep, i)))})
    spent = ab.spend(out, present, repeat)[0] if present else 0.0
    manifest = {"shard": shard, "limit": limit, "arms": list(ab.ARMS), "repeat": repeat,
                "assigned": assigned, "present": present, "done": done,
                "spent_usd": round(spent, 6), "files": copied, "skipped_large": skipped}
    (dest / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[ladder_collect] staged {copied} file(s) from {out} -> {dest}; "
          f"{len(done)}/{len(assigned) or len(present)} issue(s) done, ${spent:.4f}"
          + (f"; skipped {len(skipped)} file(s) > {MAX_FILE_BYTES >> 20} MB" if skipped else ""))
    return manifest


def find_shards(paths: list[Path]) -> list[Path]:
    """Shard dirs: a dir holding shard.json or r<rep>/ dirs, else its subdirs that do."""
    def is_shard(d: Path) -> bool:
        return (d / MANIFEST).is_file() or any(p.is_dir() and p.name[1:].isdigit()
                                               for p in d.glob("r*"))
    found: list[Path] = []
    for p in paths:
        if is_shard(p):
            found.append(p)
        elif p.is_dir():
            found.extend(sorted(d for d in p.iterdir() if d.is_dir() and is_shard(d)))
    return found


class MergeConflict(Exception):
    pass


def merge(out: Path, sources: list[Path], arms: str = "off,mini") -> dict:
    ab = _ablation()
    set_arms(ab, arms)
    shards = find_shards(sources)
    if not shards:
        raise MergeConflict(f"no shard dirs (shard.json or r<rep>/) under {[str(s) for s in sources]}")
    out.mkdir(parents=True, exist_ok=True)
    assigned: set[str] = set()
    manifests = []
    copied = same = 0
    for shard in shards:
        try:
            m = json.loads((shard / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            m = {}
        manifests.append({"dir": str(shard), **m})
        assigned.update(m.get("assigned") or [])
        for f in sorted(shard.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(shard)
            target = (out / rel) if len(rel.parts) > 1 else out / SHARDS_DIR / shard.name / rel
            if target.exists():
                if filecmp.cmp(f, target, shallow=False):
                    same += 1
                    continue
                if len(rel.parts) > 1:
                    raise MergeConflict(f"{rel} differs between {shard} and an earlier shard/out dir")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            copied += 1
    present = sorted({p.name for p in out.glob("r*/*") if p.is_dir()})
    repeats = max([int(p.name[1:]) for p in out.glob("r*") if p.name[1:].isdigit()] or [1])
    done = sorted({i for i in present
                   if all(ab.done(ab.load_rows(ab.result_file(out, rep, i)))
                          for rep in range(1, repeats + 1))})
    spent = ab.spend(out, present, repeats)[0] if present else 0.0
    missing = sorted(assigned - set(done))
    summary = {"shards": manifests, "assigned": sorted(assigned), "present": present,
               "done": done, "not_done": missing, "spent_usd": round(spent, 6),
               "copied": copied, "identical_skipped": same}
    (out / SHARDS_DIR).mkdir(parents=True, exist_ok=True)
    (out / SHARDS_DIR / "merge.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[ladder_collect] merged {len(shards)} shard(s) into {out}: {copied} file(s) copied, "
          f"{same} identical skipped; {len(done)} issue(s) done of {len(assigned) or len(present)} "
          f"assigned; spend ${spent:.4f}")
    if missing:
        print(f"[ladder_collect] NOT DONE ({len(missing)}): {', '.join(missing)}")
    return summary


CONFIG_KEYS = ("ARMS", "N_SHARDS", "SHARDS", "CAP", "LIMIT", "REPEAT", "MODEL",
               "DATA_TAG", "DATA_ASSET", "SERIES_DIR")


def read_config(path: Path) -> dict:
    """ci/ladder.env: KEY=VALUE lines, # comments; returns the workflow's settings
    with the shard list (JSON) and the per-shard cap (CAP is the total)."""
    raw: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            k, _, v = line.partition("=")
            raw[k.strip()] = v.strip()
    unknown = sorted(set(raw) - set(CONFIG_KEYS))
    if unknown:
        raise ValueError(f"unknown key(s) in {path}: {unknown}")
    n = int(raw.get("N_SHARDS") or 8)
    if n < 1:
        raise ValueError("N_SHARDS must be >= 1")
    spec = (raw.get("SHARDS") or "all").strip()
    shards = list(range(n)) if spec == "all" else sorted({int(x) for x in spec.split(",") if x.strip()})
    if not shards or any(not 0 <= k < n for k in shards):
        raise ValueError(f"SHARDS={spec!r} must be 'all' or indices in 0..{n - 1}")
    cap = float(raw.get("CAP") or 0)
    if cap <= 0:
        raise ValueError("CAP (total USD over all N_SHARDS shards) must be > 0")
    limit = (raw.get("LIMIT") or "").strip()
    if limit and int(limit) < 1:
        raise ValueError("LIMIT must be empty or >= 1")
    arms = raw.get("ARMS") or "off,mini"
    if len([a for a in arms.split(",") if a.strip()]) != 2:
        raise ValueError("ARMS takes exactly two arms")
    return {"arms": arms, "n_shards": n, "shards": json.dumps(shards), "cap_total": cap,
            # each shard spends from its own out dir, so the total cap is split evenly
            "cap_shard": f"{cap / n:.4f}", "limit": limit, "repeat": int(raw.get("REPEAT") or 1),
            "model": raw.get("MODEL") or "claude-haiku-5-5",
            "data_tag": raw.get("DATA_TAG") or "bench-data-fresh-v1",
            "data_asset": raw.get("DATA_ASSET") or "fresh_series.tar.gz.enc",
            # the dir the asset unpacks to under ~/.awos-harness
            "series_dir": _series_dir(raw.get("SERIES_DIR") or "fresh_series")}


def _series_dir(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in (".", ".."):
        raise ValueError(f"SERIES_DIR must be a plain directory name, got {name!r}")
    return name


def check_data(series_root: Path, min_issues: int = 15, shard: str | None = None,
               limit: int | None = None) -> int:
    """The extracted series: READY, enough issues, and a cached preflight verdict
    for the selected issues (validate.json newer than their files). 0 when usable;
    issues that would validate live are only a warning (slower, same verdict)."""
    ab = _ablation()
    issues, why = ab.gate(series_root, min_issues)
    if why:
        print(f"[ladder_collect] data NOT usable: {why}")
        return 1
    spec = importlib.util.spec_from_file_location("job_series_for_ladder_collect",
                                                  REPO / "scripts" / "job_series.py")
    js = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(js)
    cache = series_root / "_tools" / "validate.json"
    picked = [x for _, x in ab.select(issues, ab.parse_shard(shard), limit)]
    live = [x for x in picked if js.cached_preflight(
        cache, x, series_root / x, [n for n, _ in js.discover_jobs(series_root / x)]) is None]
    print(f"[ladder_collect] data ok: {len(issues)} issue(s); shard {shard or '-'} runs "
          f"{len(picked)}: {', '.join(picked)}; cached preflight for "
          f"{len(picked) - len(live)}/{len(picked)}")
    if live:
        print(f"[ladder_collect] WARNING: preflight will validate live for: {', '.join(live)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("stage", help="pack one shard's small files into an artifact dir")
    s.add_argument("--out", required=True, help="the shard's driver --out dir")
    s.add_argument("--dest", required=True, help="artifact dir to fill")
    s.add_argument("--series-root", default=None)
    s.add_argument("--shard", default=None, metavar="I/N")
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--repeat", type=int, default=1)
    m = sub.add_parser("merge", help="merge downloaded shard dirs into one out dir")
    m.add_argument("--out", required=True)
    m.add_argument("sources", nargs="+", help="shard dirs, or dirs holding them")
    for x in (s, m):
        x.add_argument("--arms", default="off,mini")
    c = sub.add_parser("ci-config", help="print ci/ladder.env as GITHUB_OUTPUT lines")
    c.add_argument("path", nargs="?", default=str(REPO / "ci" / "ladder.env"))
    d = sub.add_parser("check-data", help="check an extracted series (READY, preflight cache)")
    d.add_argument("--series-root", required=True)
    d.add_argument("--min-issues", type=int, default=15)
    d.add_argument("--shard", default=None, metavar="I/N")
    d.add_argument("--limit", type=int, default=None)
    a = p.parse_args(argv)
    if a.command == "ci-config":
        try:
            cfg = read_config(Path(a.path))
        except (OSError, ValueError) as exc:
            print(f"[ladder_collect] bad config: {exc}", file=sys.stderr)
            return 2
        for k, v in cfg.items():
            print(f"{k}={v}")
        return 0
    if a.command == "check-data":
        return check_data(Path(a.series_root).expanduser(), a.min_issues, a.shard, a.limit)
    if a.command == "stage":
        stage(Path(a.out).expanduser(), Path(a.dest).expanduser(),
              Path(a.series_root).expanduser() if a.series_root else None,
              a.shard, a.limit, a.arms, a.repeat)
        return 0
    try:
        merge(Path(a.out).expanduser(), [Path(x).expanduser() for x in a.sources], a.arms)
    except MergeConflict as exc:
        print(f"[ladder_collect] ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
