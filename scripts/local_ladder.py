#!/usr/bin/env python3
"""local_ladder.py — the local "does AWOS make local better" run (N arms, one llama-server).

Pre-registration: docs/specs/harness_lift_ladder.md, amendment 6.

    scripts/local_model.sh start                      # one llama-server, AWOS_LOCAL_SLOTS=1
    caffeinate -dims python3 scripts/local_ladder.py run \\
        --series-root ~/.awos-harness/fresh_series --out ~/.awos-harness/ladder_local_h2
    python3 scripts/local_ladder.py combine --out ~/.awos-harness/ladder_local_h2
    python3 scripts/stats_audit.py ~/.awos-harness/ladder_local_h2/combined_H2.json \\
        --a mini-local --b loop-local --cost-budget 0.5
    python3 scripts/local_ladder.py select --series-root ~/.awos-harness/fresh_series   # the list

Issues: the first 15 sorted issue names under the series root whose reference fix
(jobs/*/reference/) touches exactly one source file (test files, __pycache__ and
.pyc never count). The list is written to <out>/issues.txt before the first job; a
resumed run refuses to start if the recomputed list differs.

Order: every issue runs all arms back to back (one job_series call per issue × arm,
so a crash loses at most one job). The arm order rotates per issue by a Williams
balanced Latin square (for 4 arms each arm takes each position, and follows each
other arm, equally often over 4 consecutive issues).

Unattended: before every issue × arm the driver records the power source (pmset) and,
on battery, the battery energy (ioreg), waits while on battery (--on-battery wait,
the default: the run is pre-registered on AC), and health-checks the llama-server
(/health, waiting up to --health-wait s); a server that stays down is restarted
with scripts/local_model.sh stop/start, up to --max-restarts times in a row, then the
driver stops (exit 5; rerun to resume). The server's /metrics counters are read
before and after each job (prompt and generated tokens, seconds).

Resumable: an issue × arm whose results file has a row for the arm and was not
stopped by job_series (a backend failure) is done and skipped. A stopped or missing
one reruns (up to --max-attempts per driver session), after the server check.

`combine` writes, from every done issue × arm (job = 1-based issue position in
issues.txt, as stats_audit pools by job):
  combined_all.json            all arms + input-consistency violations across arms
  combined_<pair>.json         one job_series-shaped file per pre-registered pair
                               (H2, H2-g, H2-p) for scripts/stats_audit.py
  secondary.json               per-arm secondary metrics (amendment 6)
No model calls here; --dry-run passes job_series' no-op child through.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
JOB_SERIES = REPO / "scripts" / "job_series.py"
LOCAL_MODEL_SH = REPO / "scripts" / "local_model.sh"

ARMS = ("loop-local", "mini-local", "loop-local-nogrammar", "pipeline-local")
#: name -> (base a, candidate b) for stats_audit --a a --b b (amendment 6).
PAIRS = {
    "H2": ("mini-local", "loop-local"),               # primary, T1 + observed Δ >= 15 pp
    "H2-g": ("loop-local-nogrammar", "loop-local"),   # grammar ablation, descriptive at n = 15
    "H2-p": ("loop-local", "pipeline-local"),         # exploratory, descriptive only
}
N_ISSUES = 15
ISSUES_FILE = "issues.txt"
SIDECAR = "ladder.json"
DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_LOCAL_MODEL = "qwen3.5-9b"


# ── issue selection ───────────────────────────────────────────────────────────


def is_test_path(rel: Path) -> bool:
    parts = rel.parts
    name = rel.name
    return (any(p in ("tests", "test", "testing") for p in parts[:-1])
            or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py")


def source_files(ref_dir: Path) -> list[str]:
    """Files a reference fix touches, minus tests and bytecode."""
    if not ref_dir.is_dir():
        return []
    out = []
    for p in sorted(ref_dir.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        rel = p.relative_to(ref_dir)
        if not is_test_path(rel):
            out.append(rel.as_posix())
    return out


def discover(series_root: Path) -> list[str]:
    return sorted(p.name for p in series_root.iterdir()
                  if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "jobs").is_dir())


def issue_source_files(series_root: Path, issue: str) -> list[str]:
    files: list[str] = []
    for ref in sorted((series_root / issue / "jobs").glob("*/reference")):
        files += source_files(ref)
    return sorted(set(files))


def single_file_issues(series_root: Path, n: int = N_ISSUES) -> list[str]:
    """The first n sorted issue names whose reference fix touches exactly one source file."""
    return [i for i in discover(series_root) if len(issue_source_files(series_root, i)) == 1][:n]


def read_issues(out: Path) -> list[str] | None:
    p = out / ISSUES_FILE
    if not p.is_file():
        return None
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def write_issues(out: Path, issues: list[str], series_root: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    head = (f"# amendment 6: first {len(issues)} sorted issues of {series_root} whose reference "
            f"fix touches exactly one source file (local_ladder.single_file_issues)\n")
    (out / ISSUES_FILE).write_text(head + "\n".join(issues) + "\n", encoding="utf-8")


# ── arm order ─────────────────────────────────────────────────────────────────


def williams_base(n: int) -> list[int]:
    """First row of a Williams design: 0, 1, n-1, 2, n-2, ..."""
    seq, lo, hi = [0], 1, n - 1
    take_lo = True
    while len(seq) < n:
        if take_lo:
            seq.append(lo)
            lo += 1
        else:
            seq.append(hi)
            hi -= 1
        take_lo = not take_lo
    return seq


def arm_order(index: int, arms: tuple[str, ...] | list[str] = ARMS) -> list[str]:
    """Issue `index`'s arm order: row index % n of a Williams Latin square."""
    n = len(arms)
    return [arms[(b + index) % n] for b in williams_base(n)]


# ── results on disk ───────────────────────────────────────────────────────────


def arm_dir(out: Path, issue: str, arm: str, rep: int = 1) -> Path:
    return out / f"r{rep}" / issue / arm


def result_files(out: Path, issue: str, arm: str, rep: int = 1) -> list[Path]:
    return sorted(arm_dir(out, issue, arm, rep).glob("job_series_*.json"))


def load_json(path: Path | None) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        return {}


def status(out: Path, issue: str, arm: str, rep: int = 1) -> tuple[str, dict | None]:
    """("done", row) | ("stopped", row-or-None) | ("missing", None), from the newest
    results file. "stopped": job_series gave up on the backend (rerun it)."""
    files = result_files(out, issue, arm, rep)
    if not files:
        return "missing", None
    data = load_json(files[-1])
    rows = [r for r in data.get("results") or [] if r.get("arm") == arm]
    row = rows[-1] if rows else None
    if data.get("stopped"):
        return "stopped", row
    if row is None:
        return "missing", None     # killed mid-job: a partial file without the row
    return "done", row


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def read_sidecar(out: Path, issue: str, arm: str, rep: int = 1) -> dict:
    return load_json(arm_dir(out, issue, arm, rep) / SIDECAR) or {"attempts": []}


# ── server, power ─────────────────────────────────────────────────────────────


def base_url() -> str:
    return (os.environ.get("AWOS_LOCAL_BASE_URL", "").strip() or DEFAULT_BASE_URL).rstrip("/")


def server_root(url: str | None = None) -> str:
    url = (url or base_url()).rstrip("/")
    return url[: -len("/v1")] if url.endswith("/v1") else url


def http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (127.0.0.1)
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (OSError, ValueError):
        return 0, ""


def server_healthy(get=http_get, root: str | None = None) -> bool:
    code, _ = get(server_root(root) + "/health")
    return code == 200


def wait_healthy(wait_s: float, get=http_get, sleep=time.sleep, poll_s: float = 5.0) -> bool:
    waited = 0.0
    while True:
        if server_healthy(get):
            return True
        if waited >= wait_s:
            return False
        step = min(poll_s, wait_s - waited)
        sleep(step)
        waited += step


def restart_server(run=subprocess.run) -> bool:
    """scripts/local_model.sh stop, then start (start waits for /health itself)."""
    env = {**os.environ}
    run(["bash", str(LOCAL_MODEL_SH), "stop"], cwd=REPO, env=env, check=False)
    proc = run(["bash", str(LOCAL_MODEL_SH), "start"], cwd=REPO, env=env, check=False)
    return getattr(proc, "returncode", 1) == 0


def ensure_server(wait_s: float, max_restarts: int, get=http_get, run=subprocess.run,
                  sleep=time.sleep, log=print) -> tuple[bool, int]:
    """(healthy, restarts used): wait up to wait_s for /health; if it stays down,
    restart via local_model.sh and wait again, up to max_restarts times."""
    if wait_healthy(wait_s, get, sleep):
        return True, 0
    for k in range(1, max_restarts + 1):
        log(f"[local_ladder] server down after {wait_s:.0f}s; restart {k}/{max_restarts} "
            f"via {LOCAL_MODEL_SH.name}", flush=True)
        restart_server(run)
        if wait_healthy(wait_s, get, sleep):
            return True, k
        sleep(min(60.0 * k, 300.0))
    return False, max_restarts


_METRIC = re.compile(r"^(llamacpp:[a-z_]+)(?:\{[^}]*\})?\s+([0-9.eE+-]+)\s*$", re.M)


def parse_metrics(text: str) -> dict:
    out: dict[str, float] = {}
    for name, val in _METRIC.findall(text or ""):
        try:
            out[name] = out.get(name, 0.0) + float(val)
        except ValueError:
            continue
    return out


def server_metrics(get=http_get) -> dict | None:
    code, body = get(server_root() + "/metrics")
    return parse_metrics(body) if code == 200 else None


def metrics_delta(before: dict | None, after: dict | None) -> dict | None:
    """Counter deltas over one job; None when either side is missing or the server
    restarted in between (a counter went down)."""
    if not before or not after:
        return None
    keys = ("llamacpp:prompt_tokens_total", "llamacpp:prompt_seconds_total",
            "llamacpp:tokens_predicted_total", "llamacpp:tokens_predicted_seconds_total")
    d = {}
    for k in keys:
        if k in before and k in after:
            v = after[k] - before[k]
            if v < 0:
                return None
            d[k.split(":", 1)[1]] = round(v, 3)
    gen_s = d.get("tokens_predicted_seconds_total")
    d["gen_tokens_per_s"] = (round(d["tokens_predicted_total"] / gen_s, 2)
                             if gen_s else None)
    return d


def parse_pmset(text: str) -> dict:
    src = ("battery" if "'Battery Power'" in text else
           "ac" if ("'AC Power'" in text or "'UPS Power'" in text) else "unknown")
    m = re.search(r"(\d{1,3})%", text)
    return {"source": src, "percent": int(m.group(1)) if m else None}


def parse_ioreg_energy_wh(text: str) -> float | None:
    """Remaining battery energy: AppleRawCurrentCapacity (mAh) x Voltage (mV)."""
    cap = re.search(r'"AppleRawCurrentCapacity"\s*=\s*(\d+)', text or "")
    volt = re.search(r'^\s*"Voltage"\s*=\s*(\d+)', text or "", re.M)
    if not cap or not volt:
        return None
    return round(int(cap.group(1)) * int(volt.group(1)) / 1e6, 3)


def power_snapshot(run=subprocess.run) -> dict:
    snap = {"source": "unknown", "percent": None, "energy_wh": None, "at": time.time()}
    if sys.platform != "darwin":
        return snap
    try:
        snap.update(parse_pmset(run(["pmset", "-g", "batt"], capture_output=True, text=True,
                                    timeout=10).stdout or ""))
        if snap["source"] == "battery":
            snap["energy_wh"] = parse_ioreg_energy_wh(run(
                ["ioreg", "-rn", "AppleSmartBattery"], capture_output=True, text=True,
                timeout=10).stdout or "")
    except Exception:  # noqa: BLE001 — power telemetry never stops the run
        pass
    return snap


def energy_used(before: dict, after: dict) -> float | None:
    """Wh drawn from the battery over a job; None unless on battery the whole time."""
    if before.get("source") != "battery" or after.get("source") != "battery":
        return None
    if before.get("energy_wh") is None or after.get("energy_wh") is None:
        return None
    return round(before["energy_wh"] - after["energy_wh"], 3)


def server_cmd_line() -> str | None:
    port = os.environ.get("AWOS_LOCAL_PORT", "8080")
    models = Path(os.environ.get("AWOS_MODELS_DIR", str(Path.home() / ".cache" / "awos-models")))
    p = models / f"llama-server.{port}.cmd"
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def slots_from_cmd(cmd: str | None) -> int | None:
    m = re.search(r"(?:^|\s)-np\s+(\d+)", cmd or "")
    return int(m.group(1)) if m else None


def git_sha() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# ── run ───────────────────────────────────────────────────────────────────────


def preflight_cache_for(a: argparse.Namespace, root: Path) -> Path | None:
    if getattr(a, "preflight_cache", None):
        return Path(a.preflight_cache).expanduser().resolve()
    default = root / "_tools" / "validate.json"
    return default.resolve() if default.is_file() else None


def job_cmd(root: Path, out: Path, issue: str, arm: str, cache: Path | None,
            dry_run: bool) -> list[str]:
    cmd = [PY, str(JOB_SERIES), "run", "--series", issue, "--series-root", str(root),
           "--arms", arm, "--out-root", str(arm_dir(out, issue, arm))]
    if cache:
        cmd += ["--preflight-cache", str(cache)]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def child_env() -> dict:
    """The job_series env: every arm on the local server, $0."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "AWOS_PROVIDER": "local"}
    env.setdefault("AWOS_LOCAL_MODEL", DEFAULT_LOCAL_MODEL)
    return env


def mini_actions(arm_root: Path) -> int | None:
    """[mini-step] commands the mini harness ran (one per valid reply), from run.log."""
    logs = sorted(arm_root.glob("job_series/*/*/run.log"))
    if not logs:
        return None
    try:
        text = logs[-1].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return len(re.findall(r"\[mini-step\] \$ ", text))


def wait_for_ac(policy: str, power=power_snapshot, sleep=time.sleep, log=print,
                poll_s: float = 60.0, max_wait_s: float = 12 * 3600) -> tuple[bool, dict]:
    """(go, snapshot). policy wait: poll while on battery (up to max_wait_s);
    continue: go anyway (recorded); stop: don't go."""
    snap = power()
    if snap.get("source") != "battery" or policy == "continue":
        return True, snap
    if policy == "stop":
        return False, snap
    waited = 0.0
    log(f"[local_ladder] on battery ({snap.get('percent')}%): waiting for AC power "
        f"(--on-battery wait)", flush=True)
    while snap.get("source") == "battery":
        if waited >= max_wait_s:
            return False, snap
        sleep(poll_s)
        waited += poll_s
        snap = power()
    log("[local_ladder] AC power back; continuing", flush=True)
    return True, snap


class Hooks:
    """Side effects, swappable in tests (no model, no server, no power reads)."""

    def __init__(self, run=subprocess.run, get=http_get, sleep=time.sleep,
                 power=power_snapshot, log=print):
        self.run, self.get, self.sleep, self.power, self.log = run, get, sleep, power, log


def run_one(a: argparse.Namespace, root: Path, out: Path, issue: str, arm: str, index: int,
            position: int, cache: Path | None, hooks: Hooks) -> str:
    """One issue x arm (job_series call), with telemetry. Returns its status after."""
    d = arm_dir(out, issue, arm)
    d.mkdir(parents=True, exist_ok=True)
    side = read_sidecar(out, issue, arm)
    m_before = None if a.dry_run else server_metrics(hooks.get)
    p_before = hooks.power() if not a.dry_run else {"source": "skipped (dry-run)"}
    started = time.time()
    cmd = job_cmd(root, out, issue, arm, cache, a.dry_run)
    with open(d / "driver.log", "a", encoding="utf-8") as log:
        log.write(f"\n===== {time.strftime('%Y-%m-%dT%H:%M:%S')} {' '.join(cmd)}\n")
        log.flush()
        proc = hooks.run(cmd, cwd=REPO, env=child_env(), stdout=log, stderr=subprocess.STDOUT,
                         check=False)
    wall_min = round((time.time() - started) / 60, 2)
    m_after = None if a.dry_run else server_metrics(hooks.get)
    p_after = hooks.power() if not a.dry_run else {"source": "skipped (dry-run)"}
    st, row = status(out, issue, arm)
    side["attempts"].append({
        "issue": issue, "arm": arm, "index": index, "position": position,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "wall_minutes": wall_min, "exit_code": getattr(proc, "returncode", None),
        "status": st, "power_before": p_before, "power_after": p_after,
        "energy_wh": energy_used(p_before, p_after),
        "server": metrics_delta(m_before, m_after),
        "mini_actions": mini_actions(d) if arm.startswith("mini") else None,
        "dry_run": bool(a.dry_run),
    })
    write_json_atomic(d / SIDECAR, side)
    verdict = ("-" if row is None else "INVALID" if row.get("invalid") else
               "SOLVED" if row.get("solved") else "not solved")
    hooks.log(f"[local_ladder] {issue} {arm} -> {st} ({verdict}) {wall_min} min", flush=True)
    return st


def eta_line(out: Path, issues: list[str], arms: list[str]) -> str:
    mins, left = [], 0
    for issue in issues:
        for arm in arms:
            st, _ = status(out, issue, arm)
            if st == "done":
                att = read_sidecar(out, issue, arm).get("attempts") or []
                if att:
                    mins.append(float(att[-1].get("wall_minutes") or 0))
            else:
                left += 1
    if not mins:
        return f"{left} job(s) left"
    mean = sum(mins) / len(mins)
    return (f"{left} job(s) left, mean {mean:.1f} min/job over {len(mins)} -> "
            f"ETA ~{left * mean / 60:.1f} h")


def cmd_run(a: argparse.Namespace, hooks: Hooks | None = None) -> int:
    hooks = hooks or Hooks()
    root, out = Path(a.series_root).expanduser().resolve(), Path(a.out).expanduser().resolve()
    arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    if not root.is_dir():
        hooks.log(f"[local_ladder] BLOCKED: series root {root} does not exist", flush=True)
        return 4
    if not a.no_ready_check and not (root / "READY").is_file():
        hooks.log(f"[local_ladder] BLOCKED: no READY file in {root}", flush=True)
        return 4
    picked = single_file_issues(root, a.n_issues)
    existing = read_issues(out)
    if existing is not None and existing != picked:
        hooks.log(f"[local_ladder] BLOCKED: {out / ISSUES_FILE} differs from the selector's "
                  f"list now ({existing} vs {picked}); refusing to mix issue sets", flush=True)
        return 4
    if len(picked) < a.n_issues:
        hooks.log(f"[local_ladder] BLOCKED: only {len(picked)} single-file issue(s) "
                  f"< {a.n_issues}", flush=True)
        return 4
    if existing is None:
        write_issues(out, picked, root)
    issues = picked if a.limit is None else picked[: a.limit]
    cache = preflight_cache_for(a, root)
    srv_cmd = server_cmd_line()
    slots = slots_from_cmd(srv_cmd)
    if not a.dry_run and slots not in (None, 1):
        hooks.log(f"[local_ladder] BLOCKED: llama-server runs {slots} slots; the run is "
                  f"pre-registered on AWOS_LOCAL_SLOTS=1 (restart it with "
                  f"AWOS_LOCAL_SLOTS=1 scripts/local_model.sh start)", flush=True)
        return 4
    meta_path = out / "ladder_meta.json"
    meta = load_json(meta_path) or {"amendment": 6, "arms": arms, "pairs": PAIRS,
                                    "issues": picked, "sessions": []}
    if meta.get("arms") != arms:
        hooks.log(f"[local_ladder] BLOCKED: {meta_path} was started with arms "
                  f"{meta.get('arms')}, not {arms}", flush=True)
        return 4
    meta["sessions"].append({
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "git_sha": git_sha(),
        "dry_run": bool(a.dry_run), "server_cmd": srv_cmd, "server_slots": slots,
        "local_model": child_env().get("AWOS_LOCAL_MODEL"), "base_url": base_url(),
        "limit": a.limit, "on_battery": a.on_battery,
        "power": None if a.dry_run else hooks.power(),
    })
    write_json_atomic(meta_path, meta)
    hooks.log(f"[local_ladder] {len(issues)} issue(s) x {len(arms)} arms -> {out}; "
              f"{eta_line(out, issues, arms)}", flush=True)
    for i, issue in enumerate(issues):
        order = arm_order(i, tuple(arms))
        for pos, arm in enumerate(order):
            tries = 0
            while True:
                st, _ = status(out, issue, arm)
                if st == "done":
                    break
                if tries >= a.max_attempts:
                    hooks.log(f"[local_ladder] {issue} {arm}: still {st} after {tries} "
                              f"attempt(s) this session; moving on (rerun to retry)", flush=True)
                    break
                if not a.dry_run:
                    go, _snap = wait_for_ac(a.on_battery, hooks.power, hooks.sleep, hooks.log)
                    if not go:
                        hooks.log("[local_ladder] stopping: not on AC power "
                                  f"(--on-battery {a.on_battery}); rerun to resume", flush=True)
                        return 5
                    ok, restarts = ensure_server(a.health_wait, a.max_restarts, hooks.get,
                                                 hooks.run, hooks.sleep, hooks.log)
                    if not ok:
                        hooks.log(f"[local_ladder] stopping: llama-server down after "
                                  f"{restarts} restart(s); rerun to resume", flush=True)
                        return 5
                tries += 1
                hooks.log(f"[local_ladder] {i + 1}/{len(issues)} {issue} arm {pos + 1}/"
                          f"{len(order)} {arm} (order {order}) attempt {tries}", flush=True)
                run_one(a, root, out, issue, arm, i, pos, cache, hooks)
        hooks.log(f"[local_ladder] issue {i + 1}/{len(issues)} done; "
                  f"{eta_line(out, issues, arms)}", flush=True)
    hooks.log("[local_ladder] finished; next: local_ladder.py combine --out " + str(out),
              flush=True)
    return 0


# ── combine ───────────────────────────────────────────────────────────────────


def _js():
    sys.path.insert(0, str(REPO / "scripts"))
    import job_series  # noqa: WPS433
    return job_series


def finished_properly(row: dict) -> bool | None:
    """Did the arm end on its own terms (submitted) rather than on a limit or bad replies?"""
    comps = row.get("components") or {}
    loop = comps.get("loop") if isinstance(comps, dict) else None
    if isinstance(loop, dict) and loop.get("stop_reason"):
        return loop["stop_reason"] in ("submitted", "gate_red")
    if row.get("mini_exit_status") is not None:
        return row["mini_exit_status"] == "Submitted"
    if row.get("arm", "").startswith("pipeline") or row.get("plan_source") is not None:
        return not row.get("timed_out") and row.get("orchestrator_success") is not None
    return None


def valid_reply_rate(row: dict, attempt: dict) -> float | None:
    """Replies that carried a usable action: loop 1 - format_errors/steps; mini
    executed commands / model turns; the pipeline (tool calls) has none."""
    comps = row.get("components") or {}
    loop = comps.get("loop") if isinstance(comps, dict) else None
    if isinstance(loop, dict) and loop.get("steps"):
        return round(max(0.0, 1 - (loop.get("format_errors") or 0) / loop["steps"]), 4)
    acts, turns = attempt.get("mini_actions"), row.get("turns")
    if acts is not None and turns:
        return round(min(1.0, acts / turns), 4)
    return None


def enrich(row: dict, side: dict) -> dict:
    att = (side.get("attempts") or [{}])[-1]
    comps = row.get("components") or {}
    loop = comps.get("loop") if isinstance(comps, dict) else None
    srv = att.get("server") or {}
    inp = row.get("input_tokens") or 0
    srv_cached = (round(max(0.0, 1 - srv["prompt_tokens_total"] / inp), 4)
                  if inp and srv.get("prompt_tokens_total") is not None else None)
    return {**row, "ladder": {
        "position": att.get("position"), "wall_minutes": att.get("wall_minutes"),
        "power_before": (att.get("power_before") or {}).get("source"),
        "power_after": (att.get("power_after") or {}).get("source"),
        "energy_wh": att.get("energy_wh"), "server": att.get("server"),
        "finished_properly": finished_properly(row),
        "valid_reply_rate": valid_reply_rate(row, att),
        "tokens_per_s": srv.get("gen_tokens_per_s") or (loop or {}).get("tokens_per_s"),
        "cached_fraction": ((loop or {}).get("cached_fraction")
                            if (loop or {}).get("cached_fraction") is not None else srv_cached),
        "attempts": len(side.get("attempts") or []),
    }}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def secondary(rows: list[dict], arms: list[str]) -> dict:
    out = {}
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        valid = [r for r in rs if not r.get("invalid")]
        solved = [r for r in valid if r.get("solved")]
        lad = [r["ladder"] for r in valid]
        mins = sum(float(x.get("wall_minutes") or 0) for x in lad)
        energies = [x.get("energy_wh") for x in lad]
        wh = sum(e for e in energies if e is not None) if all(e is not None for e in energies) \
            and energies else None
        fin = [x.get("finished_properly") for x in lad if x.get("finished_properly") is not None]
        out[arm] = {
            "runs": len(rs), "valid": len(valid), "solved": len(solved),
            "solve_rate": round(len(solved) / len(valid), 4) if valid else None,
            "valid_reply_rate": _mean([x.get("valid_reply_rate") for x in lad]),
            "finished_properly_rate": round(sum(fin) / len(fin), 4) if fin else None,
            "stop_reasons": _count([((r.get("components") or {}).get("loop") or {}).get(
                "stop_reason") or r.get("mini_exit_status")
                or ("timed_out" if r.get("timed_out") else None) for r in valid]),
            "minutes_total": round(mins, 2),
            "minutes_per_solve": round(mins / len(solved), 2) if solved else None,
            "wh_per_solve": (round(wh / len(solved), 3) if wh is not None and solved else None),
            "energy_note": (None if wh is not None else
                            "unavailable: on AC (battery energy delta only on battery)"),
            "tokens_per_s": _mean([x.get("tokens_per_s") for x in lad]),
            "cached_fraction": _mean([x.get("cached_fraction") for x in lad]),
            "server_prompt_tokens": sum(int((x.get("server") or {}).get("prompt_tokens_total")
                                            or 0) for x in lad),
            "server_generated_tokens": sum(int((x.get("server") or {}).get(
                "tokens_predicted_total") or 0) for x in lad),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rs), 6),
        }
    return out


def _count(xs) -> dict:
    out: dict = {}
    for x in xs:
        out[str(x)] = out.get(str(x), 0) + 1
    return out


def combine(out: Path) -> dict:
    meta = load_json(out / "ladder_meta.json")
    issues = read_issues(out) or meta.get("issues") or []
    arms = list(meta.get("arms") or ARMS)
    rows, missing = [], []
    for idx, issue in enumerate(issues, start=1):
        for arm in arms:
            st, row = status(out, issue, arm)
            if st != "done" or row is None:
                missing.append({"issue": issue, "arm": arm, "status": st})
                continue
            rows.append(enrich({**row, "job": idx, "issue": issue, "repeat": 1},
                               read_sidecar(out, issue, arm)))
    violations = _js().manifest_violations(rows)
    base = {"series": "local_ladder_amendment6", "repeat": 1, "issues": issues,
            "dry_run": any(_dry(out, r) for r in rows)}
    written = {}
    all_data = {**base, "arms": arms, "results": rows, "missing": missing,
                "violations": violations}
    write_json_atomic(out / "combined_all.json", all_data)
    written["all"] = str(out / "combined_all.json")
    for name, (a, b) in PAIRS.items():
        if a in arms and b in arms:
            data = {**base, "arms": [a, b], "pair": name,
                    "results": [r for r in rows if r["arm"] in (a, b)]}
            p = out / f"combined_{name}.json"
            write_json_atomic(p, data)
            written[name] = str(p)
    sec = secondary(rows, arms)
    write_json_atomic(out / "secondary.json", sec)
    written["secondary"] = str(out / "secondary.json")
    return {"written": written, "rows": len(rows), "missing": missing,
            "violations": violations, "secondary": sec}


def _dry(out: Path, r: dict) -> bool:
    side = read_sidecar(out, r["issue"], r["arm"])
    return bool((side.get("attempts") or [{}])[-1].get("dry_run"))


def cmd_combine(a: argparse.Namespace) -> int:
    out = Path(a.out).expanduser().resolve()
    res = combine(out)
    print(f"[local_ladder] combined {res['rows']} row(s); missing {len(res['missing'])}; "
          f"violations {len(res['violations'])}")
    for k, v in res["written"].items():
        print(f"  {k}: {v}")
    for arm, s in res["secondary"].items():
        print(f"  {arm:22s} solved {s['solved']}/{s['valid']}  valid-reply "
              f"{s['valid_reply_rate']}  finished {s['finished_properly_rate']}  "
              f"min/solve {s['minutes_per_solve']}  tok/s {s['tokens_per_s']}  "
              f"cached {s['cached_fraction']}")
    for name, (x, y) in PAIRS.items():
        print(f"  {name}: python3 scripts/stats_audit.py {out / f'combined_{name}.json'} "
              f"--a {x} --b {y} --cost-budget 0.5")
    return 1 if any(v.get("severity") == "fatal" for v in res["violations"]) else 0


def cmd_select(a: argparse.Namespace) -> int:
    root = Path(a.series_root).expanduser().resolve()
    for issue in single_file_issues(root, a.n_issues):
        print(f"{issue}\t{issue_source_files(root, issue)[0]}")
    return 0


def main(argv: list[str] | None = None, hooks: Hooks | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--series-root", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--arms", default=",".join(ARMS))
    r.add_argument("--n-issues", type=int, default=N_ISSUES)
    r.add_argument("--limit", type=int, default=None, help="only the first K issues (smoke)")
    r.add_argument("--dry-run", action="store_true", help="job_series' no-op child; no server")
    r.add_argument("--no-ready-check", action="store_true")
    r.add_argument("--preflight-cache", default=None)
    r.add_argument("--health-wait", type=float, default=120.0)
    r.add_argument("--max-restarts", type=int, default=3)
    r.add_argument("--max-attempts", type=int, default=3,
                   help="job_series calls per issue x arm per session (stopped/killed ones)")
    r.add_argument("--on-battery", choices=("wait", "continue", "stop"), default="wait")
    c = sub.add_parser("combine")
    c.add_argument("--out", required=True)
    s = sub.add_parser("select")
    s.add_argument("--series-root", required=True)
    s.add_argument("--n-issues", type=int, default=N_ISSUES)
    a = p.parse_args(argv)
    if a.command == "run":
        bad = [x for x in a.arms.split(",") if x.strip() and x.strip() not in _js().ARM_CHILDREN]
        if bad:
            p.error(f"unknown arm(s) {bad}")
        return cmd_run(a, hooks)
    if a.command == "combine":
        return cmd_combine(a)
    return cmd_select(a)


if __name__ == "__main__":
    sys.exit(main())
