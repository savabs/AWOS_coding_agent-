"""
m2w_tasks.py — Mind2Web TRAIN tasks -> a teacher task file for the live-web harness (``--split train``).

    # 1. task headers of the train shards, streamed (a shard is ~600 MB of HTML; only the headers are kept)
    curl -sL https://huggingface.co/datasets/osunlp/Mind2Web/resolve/main/data/train/train_0.json \\
        | python -m scaffold.rl.web.m2w_tasks headers - >> m2w_headers.jsonl
    # 2. the deterministic sample, Online-Mind2Web excluded
    python -m scaffold.rl.web.m2w_tasks build --headers m2w_headers.jsonl \\
        --exclude Online_Mind2Web.json [--exclude another_copy.json] [--dev-ids scaffold/rl/web/data/dev_ids.json] \\
        [--exclude-websites dev|all] --n 300 [--repeats 1] [--seed teacher-v1] [--start-urls urls.json] \\
        --out teach_tasks.json
    python -m scaffold.rl.web run --model anthropic:claude-haiku-5-5 --split train --tasks-json teach_tasks.json ...

Output: a JSON list in the Online_Mind2Web.json shape that source.py reads, one entry per episode:
    {"task_id": "m2w-<annotation_id>[-r<k>]", "confirmed_task", "website": <start URL>, "level": "m2w",
     "reference_length": len(action_reprs), "source": "mind2web-train", "annotation_id", "m2w_website",
     "base_task": "m2w-<annotation_id>", "start_url_how"}
plus ``<out>.stats.json`` (counts only: no task text).

Contamination (Online-Mind2Web is the test set; docs/specs/web_teacher.md):
  * text: a Mind2Web task whose text fuzzy-matches ANY Online-Mind2Web task (all 300, every copy passed
    with --exclude) is dropped (mind2web_data.Exclusion: SequenceMatcher ratio >= 0.75 or token Jaccard
    >= 0.7). --exclude is required: no exclusion list, no task file;
  * website: ``--exclude-websites dev`` (default) drops tasks on any website (site key or brand) of the 100
    dev tasks; ``all`` drops tasks on any Online-Mind2Web website (stricter; ~half of Mind2Web train);
  * ids: a task id in the dev id file is a hard error (source.py refuses it again at run time).

Start URL: Mind2Web stores a site name ("united", "sports.yahoo", "nps.gov"), not a URL. Resolution:
--start-urls JSON {name: url} first, then START_URLS below, then a dotted name with a known TLD as the host
("nps.gov" -> https://www.nps.gov/), a dotted name without one + ".com" ("sports.yahoo" ->
https://sports.yahoo.com/), else https://www.<name>.com/. A wrong guess costs nothing: the start page fails
to load (blocked: load_failed) before the first model call. ``start_url_how`` records which rule applied.

Sample: eligible tasks in a seeded order, round-robin over websites (each website's tasks shuffled with
random.Random(f"{seed}:{website}"), websites ordered by a seeded hash), so N tasks spread over the sites.
--repeats K runs each task up to K times (ids -r1, -r2, ...; same base_task, so the SFT validation split
keeps repeats together) when N exceeds the eligible tasks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import IO, Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .source import TRAIN_SOURCE, committed_dev_ids, load_entries

ID_PREFIX = "m2w-"
LEVEL = "m2w"
DEFAULT_SEED = "teacher-v1"
#: site names whose www.<name>.com guess is wrong (add more with --start-urls; unknown names fall through)
START_URLS = {
    "aa": "https://www.aa.com/",
    "nps.gov": "https://www.nps.gov/",
    "last.fm": "https://www.last.fm/",
    "new.mta.info": "https://new.mta.info/",
    "qatarairways": "https://www.qatarairways.com/",
    "student": "https://www.student.com/",
}
_HOST_TLDS = ("com", "org", "net", "gov", "edu", "io", "fm", "info", "co", "us", "uk", "ca", "de", "tv", "me", "ai")
_HEAD = re.compile(rb'\{"website"\s*:')
_ACTIONS = re.compile(rb',\s*"actions"\s*:\s*\[')


# ------------------------------------------------------------------ headers --

def iter_headers(fh: IO[bytes], chunk: int = 1 << 22) -> Iterator[dict]:
    """Task headers of a Mind2Web train JSON stream: every key before ``"actions"`` (website, domain, subdomain,
    annotation_id, confirmed_task, action_reprs). The actions (raw HTML, ~MBs per task) are skipped, never
    parsed. Inside JSON strings every quote is escaped, so an unescaped ``{"website":`` only starts a task
    (the same property mind2web_data.iter_tasks relies on)."""
    buf = b""
    while True:
        data = fh.read(chunk)
        buf += data
        pos = 0
        while True:
            m = _HEAD.search(buf, pos)
            if not m:
                buf = buf[-32:] if not data else buf[max(pos, len(buf) - 32):]
                break
            a = _ACTIONS.search(buf, m.start())
            if not a:
                buf = buf[m.start():]
                break
            text = buf[m.start():a.start()].decode("utf-8", errors="replace") + "}"
            try:
                d = json.loads(text)
            except ValueError:
                d = None
            if isinstance(d, dict) and d.get("annotation_id") and d.get("confirmed_task"):
                yield d
            pos = a.end()
        if not data:
            return


def read_headers(paths: Sequence[Path]) -> List[dict]:
    """Header JSONL files (the ``headers`` output) or Mind2Web train JSON files; deduplicated by annotation_id."""
    out: Dict[str, dict] = {}
    for p in paths:
        p = Path(p)
        with open(p, "rb") as fh:
            head = fh.read(1)
        if head == b"[":
            with open(p, "rb") as fh:
                rows: Iterable[dict] = list(iter_headers(fh))
        else:
            rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        for r in rows:
            aid = str(r.get("annotation_id") or "").strip()
            if aid and aid not in out:
                out[aid] = r
    return list(out.values())


# ------------------------------------------------------------------ start URL --

def start_url(website: str, overrides: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """-> (URL, how) for a Mind2Web site name; how = override | table | url | host | dotted | com."""
    w = (website or "").strip().lower()
    if not w:
        return "", "none"
    if overrides and w in overrides:
        return overrides[w], "override"
    if w in START_URLS:
        return START_URLS[w], "table"
    if "://" in w:
        return w, "url"
    parts = w.split(".")
    if len(parts) >= 2 and parts[-1] in _HOST_TLDS:
        host = w if len(parts) >= 3 else "www." + w
        return f"https://{host}/", "host"
    if len(parts) >= 2:
        return f"https://{w}.com/", "dotted"
    return f"https://www.{w}.com/", "com"


# ------------------------------------------------------------------ build --

def _hash(seed: str, s: str) -> str:
    return hashlib.sha256(f"{seed}:{s}".encode()).hexdigest()


def sample_order(tasks: List[dict], seed: str) -> List[dict]:
    """Seeded round-robin over websites (see the module docstring)."""
    by_site: Dict[str, List[dict]] = defaultdict(list)
    for t in sorted(tasks, key=lambda t: str(t["annotation_id"])):
        by_site[str(t.get("website") or "")].append(t)
    for site, ts in by_site.items():
        random.Random(f"{seed}:{site}").shuffle(ts)
    sites = sorted(by_site, key=lambda s: _hash(seed, s))
    out: List[dict] = []
    i = 0
    while len(out) < len(tasks):
        for s in sites:
            if i < len(by_site[s]):
                out.append(by_site[s][i])
        i += 1
    return out


def build_tasks(headers: List[dict], bench_paths: Sequence[Path], *, dev_ids_path: Optional[Path] = None,
                exclude_websites: str = "dev", n: int = 300, repeats: int = 1, seed: str = DEFAULT_SEED,
                overrides: Optional[Dict[str, str]] = None, threshold: Optional[float] = None,
                jaccard: Optional[float] = None) -> Tuple[List[dict], Dict[str, Any]]:
    """-> (teacher task entries, stats). Raises ValueError without an exclusion list."""
    from scaffold.rl.train import mind2web_data as MD       # lazy: pulls in the dataset tooling
    if not bench_paths:
        raise ValueError("--exclude (Online-Mind2Web task file) is required: refusing to build without it")
    if exclude_websites not in ("dev", "all"):
        raise ValueError(f"exclude_websites must be dev | all, got {exclude_websites!r}")
    bench = MD.load_benchmark(bench_paths)
    excl = MD.Exclusion(bench, threshold if threshold is not None else MD.DEFAULT_THRESHOLD,
                        jaccard if jaccard is not None else MD.DEFAULT_JACCARD)
    dev_path = Path(dev_ids_path) if dev_ids_path else None
    dev = set(committed_dev_ids(dev_path)) if dev_path else set(committed_dev_ids())
    if not dev:
        raise ValueError("no committed dev ids: the dev websites cannot be excluded (freeze-dev first)")
    dev_sites, dev_brands = set(), set()
    for p in bench_paths:
        for d in load_entries(Path(p)):
            if str(d.get("task_id") or "").strip() in dev and d.get("website"):
                dev_sites.add(MD.site_key(str(d["website"])))
                dev_brands.add(MD.brand(str(d["website"])))
    dev_sites.discard("")
    dev_brands.discard("")
    stats: Dict[str, Any] = {"headers": len(headers), "benchmark_tasks": len(bench), "dev_ids": len(dev),
                             "dev_websites": len(dev_sites), "exclude_websites": exclude_websites,
                             "excluded_text": 0, "excluded_website": Counter(), "excluded_no_url": 0}
    keep: List[dict] = []
    for h in headers:
        site = str(h.get("website") or "")
        if excl.match(str(h.get("confirmed_task") or "")) is not None:
            stats["excluded_text"] += 1
            continue
        if exclude_websites == "all":
            hit = excl.website_overlap(site) or excl.brand_overlap(site)
        else:
            hit = MD.site_key(site) in dev_sites or MD.brand(site) in dev_brands
        if hit:
            stats["excluded_website"][site] += 1
            continue
        if not start_url(site, overrides)[0]:
            stats["excluded_no_url"] += 1
            continue
        keep.append(h)
    order = sample_order(keep, seed)
    out: List[dict] = []
    for r in range(max(1, int(repeats))):
        for h in order:
            if len(out) >= n:
                break
            base = ID_PREFIX + str(h["annotation_id"]).strip()
            tid = base if r == 0 else f"{base}-r{r}"
            if tid in dev or base in dev:
                raise ValueError(f"task id {tid!r} is an Online-Mind2Web dev id: refusing")
            url, how = start_url(str(h.get("website") or ""), overrides)
            out.append({"task_id": tid, "confirmed_task": str(h["confirmed_task"]).strip(), "website": url,
                        "level": LEVEL, "reference_length": max(1, len(h.get("action_reprs") or [])),
                        "source": TRAIN_SOURCE, "annotation_id": str(h["annotation_id"]),
                        "m2w_website": str(h.get("website") or ""), "base_task": base, "start_url_how": how})
    stats.update(eligible=len(keep), eligible_websites=len({h.get("website") for h in keep}), selected=len(out),
                 shortfall=max(0, n - len(out)),
                 selected_websites=dict(Counter(t["m2w_website"] for t in out).most_common()),
                 start_url_how=dict(Counter(t["start_url_how"] for t in out)),
                 excluded_website=dict(stats["excluded_website"].most_common()), seed=seed, repeats=repeats, n=n)
    return out, stats


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.web.m2w_tasks", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("headers", help="task headers of a Mind2Web train JSON (path or - for stdin) -> JSONL on stdout")
    h.add_argument("input")
    b = sub.add_parser("build", help="the teacher task file (deterministic sample, Online-Mind2Web excluded)")
    b.add_argument("--headers", action="append", type=Path, required=True,
                   help="header JSONL (the headers output) or a Mind2Web train JSON (repeatable)")
    b.add_argument("--exclude", action="append", type=Path, required=True,
                   help="Online-Mind2Web task file(s); REQUIRED (the test set)")
    b.add_argument("--dev-ids", type=Path, default=None, help="default scaffold/rl/web/data/dev_ids.json")
    b.add_argument("--exclude-websites", choices=("dev", "all"), default="dev")
    b.add_argument("--n", type=int, default=300)
    b.add_argument("--repeats", type=int, default=1)
    b.add_argument("--seed", default=DEFAULT_SEED)
    b.add_argument("--start-urls", type=Path, default=None, help="JSON {m2w site name: start URL}")
    b.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "headers":
        fh = sys.stdin.buffer if a.input == "-" else open(a.input, "rb")
        k = 0
        try:
            for d in iter_headers(fh):
                sys.stdout.write(json.dumps(d, ensure_ascii=False) + "\n")
                k += 1
        finally:
            if fh is not sys.stdin.buffer:
                fh.close()
        print(f"{k} task headers", file=sys.stderr)
        return 0
    overrides = json.loads(a.start_urls.read_text()) if a.start_urls else None
    try:
        tasks, stats = build_tasks(read_headers(a.headers), a.exclude, dev_ids_path=a.dev_ids,
                                   exclude_websites=a.exclude_websites, n=a.n, repeats=a.repeats, seed=a.seed,
                                   overrides=overrides)
    except (OSError, ValueError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 2
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(tasks, indent=1, ensure_ascii=False) + "\n")
    Path(str(a.out) + ".stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    show = {k: stats[k] for k in ("headers", "excluded_text", "eligible", "eligible_websites", "selected",
                                  "shortfall", "start_url_how")}
    print(json.dumps(show | {"excluded_website_tasks": sum(stats["excluded_website"].values())}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
