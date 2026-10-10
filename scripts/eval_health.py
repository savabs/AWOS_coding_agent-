#!/usr/bin/env python3
"""
eval_health.py — is this job-series run fit to be measured at all?

Rule 2 of docs/research/evaluation_first_principles_2026-10.md: before any
solve rate or $/solve is read, the run must pass a health check. The
learning-phase run (docs/memory/learning_phase_2026-09-30.md) failed silently
in ways no solve rate shows: truncated/empty replies, a notebook overwritten
2732 -> 364 chars, an unpinned Sonnet reviewer, Aider with no files in chat.

    check_run(run_root, results_path, allowed_models=None) -> {
        "ok": bool,                     # no fatal violation
        "violations": [{"severity": "fatal"|"warn", "check", "arm", "job",
                        "repeat", "detail"}, ...],
        "stats": {...},
    }

Inputs: the job-series results JSON (rows per (repeat, arm, job)) and the run
dir `<run_root>/[r<k>/]<arm>/{state,project,run.log}`. AWOS arms log every
model call to `<arm>/state/.awos/llm_calls.jsonl` (scaffold/agent/llm_call_log.py).

CLI:
    python scripts/eval_health.py <run_root> <results_json> [--allow MODEL ...] [--out PATH]
Prints a verdict and writes health.json next to the results (or --out).
Exit code 0 when ok, 1 on a fatal violation.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

#: Components whose reply is written into persistent state: a bad reply there
#: corrupts what every later job sees.
STATE_WRITERS = frozenset({"notebook", "critique"})
TRUNCATED = frozenset({"length", "max_tokens"})
AGENT_TRUNCATION_WARN = 0.05
NOTEBOOK_MAX_DROP = 0.40
BILLING_MAX_GAP = 0.50
INVALID_MAX_SHARE = 0.25
#: A provider-pinned run exists to let the prompt cache hit; below this share
#: of agent input tokens read from cache, the pin is not doing its job.
PINNED_MIN_CACHE_SHARE = 0.20
#: aider_no_edit reasons that mean the harness left Aider without the code.
AIDER_SETUP_REASONS = ("no_files_in_chat", "asked_to_add_files")

_SECTION = re.compile(r"^===== \[(\S+) j(\d+)\] (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[^\n]*=====$", re.M)
_TS_PREFIX = re.compile(r"^\+\d+:\d\d ", re.M)
_BANNER_MODEL = re.compile(r"\[harness_aider\] aider \S+ model=(\S+)")
_PRELOADED = re.compile(r"\[harness_aider\] aider .*? add_files=\S*?\((\d+)")
_NO_EDIT_LINE = re.compile(r"\[harness_aider\] aider_no_edit reason=(\w+)")
_FINAL_LINE = re.compile(r"\[aider-final\] (\{.*\})")
_APPLIED_LINE = re.compile(r"^Applied edit to (\S+)", re.M)
_ASK_TO_ADD = re.compile(r"\badd\b(?:[^.?!]|[.?!](?=\S)){0,160}?\bto\s+(?:the|this)\s+chat\b", re.I)


# ── inputs ────────────────────────────────────────────────────────────────────

def _load_results(results_path: Path) -> tuple[dict, list[dict]]:
    data = json.loads(Path(results_path).read_text())
    if isinstance(data, list):
        return {}, data
    return data, list(data.get("results") or data.get("rows") or [])


def _is_aider(arm: str) -> bool:
    return str(arm).startswith("aider")


def _arm_dir(run_root: Path, arm: str, repeat: Any) -> Path:
    if repeat not in (None, "") and (run_root / f"r{repeat}").is_dir():
        return run_root / f"r{repeat}" / arm
    return run_root / arm


def _sections(log_text: str) -> list[tuple[int, float, str]]:
    """[(job, start_epoch, text)] in log order (one per attempt)."""
    heads = list(_SECTION.finditer(log_text))
    out = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(log_text)
        try:  # the runner stamps local time (time.strftime)
            start = time.mktime(time.strptime(h.group(3), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            start = 0.0
        out.append((int(h.group(2)), start, log_text[h.start():end]))
    return out


def _read_calls(path: Path) -> list[dict]:
    calls = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            calls.append(item)
    return calls


def _attribute(calls: list[dict], sections: list[tuple[int, float, str]]) -> dict[int, list[dict]]:
    """Calls per job: the line's own `job` tag, else the run.log section it falls in."""
    starts = sorted((start, job) for job, start, _ in sections if start)
    out: dict[int, list[dict]] = defaultdict(list)
    for call in calls:
        job = None
        if call.get("job") not in (None, ""):
            try:
                job = int(call["job"])
            except (TypeError, ValueError):
                job = None
        if job is None and starts:
            ts = float(call.get("ts") or 0)
            for start, j in starts:
                if start <= ts:
                    job = j
        out[job if job is not None else -1].append(call)
    return out


# ── models ────────────────────────────────────────────────────────────────────

def _norm(model: str) -> str:
    m = str(model).strip().lower()
    return m[len("openrouter/"):] if m.startswith("openrouter/") else m


def _model_ok(model: Optional[str], allowed: set[str]) -> bool:
    if not model:
        return True  # no model reported (failed call): nothing to check
    m = _norm(model)
    for a in allowed:
        if m == a or m == a.split("/")[-1]:
            return True
        # Dated/variant ids the provider answers with: "<pinned>-20260415".
        if re.fullmatch(re.escape(a) + r"[-:@][\w.]*\d[\w.]*", m):
            return True
    return False


def default_allowed_models(meta: dict, aider_models: set[str]) -> set[str]:
    """The run's pin (results JSON model_pin, else job_series.PIN_ENV) plus Aider's model."""
    allowed: set[str] = set()
    pin = meta.get("model_pin") or {}
    env = dict(pin.get("env") or {})
    if pin.get("model"):
        allowed.add(pin["model"])
    if not pin:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import job_series  # noqa: WPS433
            env = dict(getattr(job_series, "PIN_ENV", {}) or {})
        except Exception:
            env = {}
    allowed.update(v for k, v in env.items() if k.endswith("_MODEL") and v)
    allowed.update(aider_models)
    routing = dict(pin.get("routing") or {})
    if str(routing.get("AWOS_PROVIDER", "")).strip().lower() == "local":
        # Every call was served by the local model (docs/specs/local_provider_spec.md).
        name = str(routing.get("AWOS_LOCAL_MODEL") or "").strip().removeprefix("local/")
        allowed.add("local/" + (name or _default_local_model()))
    return {_norm(m) for m in allowed}


def _default_local_model() -> str:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scaffold"))
        from agent.providers import LOCAL_DEFAULT_MODEL  # noqa: WPS433
        return LOCAL_DEFAULT_MODEL
    except Exception:
        return "qwen3.5-9b"


# ── aider ─────────────────────────────────────────────────────────────────────

def aider_section_facts(text: str) -> dict:
    """Files preloaded, final in-chat files, edits applied and no-edit reason of one Aider job log."""
    plain = _TS_PREFIX.sub("", text)
    final: dict = {}
    for fm in _FINAL_LINE.finditer(plain):
        try:
            final = json.loads(fm.group(1))
        except ValueError:
            pass
    pre = _PRELOADED.search(plain)
    preloaded = int(pre.group(1)) if pre else None
    applied = _APPLIED_LINE.findall(plain)
    m = _NO_EDIT_LINE.search(plain)
    if m:
        reason = m.group(1)
    elif final.get("edited") or applied:
        reason = None
    elif final and not final.get("in_chat"):
        reason = "no_files_in_chat"
    elif _ASK_TO_ADD.search(" ".join(plain.split())):
        reason = "asked_for_files_it_had" if preloaded else "asked_to_add_files"
    else:
        reason = "no_edits"
    model = _BANNER_MODEL.search(plain)
    return {"preloaded": preloaded, "in_chat": final.get("in_chat"), "applied": len(applied),
            "reason": reason, "model": model.group(1) if model else None}


# ── the check ────────────────────────────────────────────────────────────────

def check_run(run_root: Path, results_path: Path,
              allowed_models: Optional[set[str]] = None) -> dict:
    run_root, results_path = Path(run_root), Path(results_path)
    meta, rows = _load_results(results_path)
    dry_run = bool(meta.get("dry_run"))
    violations: list[dict] = []

    def flag(severity: str, check: str, arm: Any, job: Any, repeat: Any, detail: str) -> None:
        violations.append({"severity": severity, "check": check, "arm": arm, "job": job,
                           "repeat": repeat, "detail": detail})

    # Group rows per (repeat, arm), each sorted by job.
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r.get("repeat"), r.get("arm"))].append(r)
    for g in groups.values():
        g.sort(key=lambda r: int(r.get("job") or 0))

    # Logs first: the aider banner model feeds the default allowed set.
    logs: dict[tuple, list] = {}
    aider_models: set[str] = set()
    for (rep, arm) in groups:
        log = _arm_dir(run_root, arm, rep) / "run.log"
        logs[(rep, arm)] = _sections(log.read_text(errors="replace")) if log.is_file() else []
        if _is_aider(arm):
            for _, _, text in logs[(rep, arm)]:
                m = _BANNER_MODEL.search(_TS_PREFIX.sub("", text))
                if m:
                    aider_models.add(m.group(1))
    if allowed_models is None:
        allowed = default_allowed_models(meta, aider_models)
    else:
        allowed = {_norm(m) for m in allowed_models}

    stats: dict = {"rows": len(rows), "allowed_models": sorted(allowed), "arms": {}}
    dropped_jobs: set[tuple] = set()

    for (rep, arm), arm_rows in groups.items():
        key = f"r{rep}/{arm}" if rep not in (None, "") else str(arm)
        sections = logs[(rep, arm)]
        st: dict = {"jobs": len(arm_rows)}
        stats["arms"][key] = st

        # Silent zero-work jobs and invalid share.
        invalid = [r for r in arm_rows if r.get("invalid")]
        for r in invalid:
            dropped_jobs.add((rep, r.get("job")))
        st["invalid"] = len(invalid)
        st["invalid_share"] = round(len(invalid) / len(arm_rows), 4) if arm_rows else 0.0
        st["invalid_reasons"] = dict(Counter(str(r.get("invalid_reason")) for r in invalid))
        if st["invalid_share"] > INVALID_MAX_SHARE:
            flag("warn", "invalid_share", arm, None, rep,
                 f"{len(invalid)}/{len(arm_rows)} jobs invalid ({st['invalid_share']:.0%}) "
                 f"— measurement drops them")
        for r in arm_rows:
            # Aider counts 0 turns when it answers without an edit; with its files
            # in chat (aider_no_edit set, row not invalid) that is the model's fair
            # failure, the same call the runner makes — not silent zero work.
            if r.get("aider_no_edit") and not r.get("invalid"):
                continue
            if not dry_run and not r.get("invalid") and not (r.get("turns") or 0) > 0:
                flag("fatal", "zero_work", arm, r.get("job"), rep,
                     f"turns={r.get('turns')!r} and not marked invalid: silent zero-work job")

        # Models the runner recorded per row (covers runs without a call log).
        for r in arm_rows:
            bad = [m for m in (r.get("models") or []) if not _model_ok(m, allowed)]
            if bad:
                flag("fatal", "unpinned_model", arm, r.get("job"), rep,
                     f"row models {bad} not in allowed {sorted(allowed)}")

        # Billing presence.
        billed = [r.get("billed_usd") for r in arm_rows]
        for r in arm_rows:
            if not r.get("invalid") and r.get("billed_usd") is None:
                flag("warn", "missing_billing", arm, r.get("job"), rep,
                     "billed_usd is None: real spend unknown for this job")
        st["billed_usd"] = round(sum(b for b in billed if isinstance(b, (int, float))), 6)

        # Notebook must not shrink sharply between consecutive jobs.
        chars = [(r.get("job"), r.get("notebook_chars")) for r in arm_rows]
        if any(isinstance(c, (int, float)) and c > 0 for _, c in chars):
            st["notebook_chars"] = [c for _, c in chars]
            prev_job, prev = None, None
            for job, c in chars:
                if not isinstance(c, (int, float)):
                    continue
                if prev and c < prev * (1 - NOTEBOOK_MAX_DROP):
                    flag("fatal", "notebook_drop", arm, job, rep,
                         f"notebook {prev} -> {c} chars ({(c - prev) / prev:+.0%}) "
                         f"between job {prev_job} and job {job}")
                prev_job, prev = job, c

        if _is_aider(arm):
            _check_aider(arm, rep, arm_rows, sections, st, flag)
        else:
            _check_calls(arm, rep, arm_rows, sections, st, allowed, flag,
                         _arm_dir(run_root, arm, rep) / "state" / ".awos" / "llm_calls.jsonl")

    all_jobs = {(r.get("repeat"), r.get("job")) for r in rows}
    stats["jobs_dropped_any_arm"] = len(dropped_jobs)
    stats["jobs_total"] = len(all_jobs)
    stats["dropped_share"] = round(len(dropped_jobs) / len(all_jobs), 4) if all_jobs else 0.0
    if stats["dropped_share"] > INVALID_MAX_SHARE:
        flag("warn", "invalid_share", None, None, None,
             f"{len(dropped_jobs)}/{len(all_jobs)} jobs invalid in some arm "
             f"({stats['dropped_share']:.0%}) — head-to-head drops them from every arm")

    fatal = sum(v["severity"] == "fatal" for v in violations)
    stats["fatal"], stats["warn"] = fatal, len(violations) - fatal
    return {"ok": fatal == 0, "violations": violations, "stats": stats}


def _check_aider(arm, rep, arm_rows, sections, st, flag) -> None:
    last: dict[int, str] = {}
    for job, _, text in sections:
        last[job] = text  # the final attempt is the one scored
    reasons: Counter = Counter()
    for r in arm_rows:
        job = int(r.get("job") or 0)
        facts = aider_section_facts(last[job]) if job in last else {}
        reason = r.get("aider_no_edit") or facts.get("reason")
        if reason:
            reasons[reason] += 1
        if reason in AIDER_SETUP_REASONS:
            flag("fatal", "aider_no_files", arm, job, rep,
                 f"Aider had no files in chat ({reason}; preloaded={facts.get('preloaded')}, "
                 f"edits applied={facts.get('applied')}, marked invalid={bool(r.get('invalid'))})")
    st["aider_no_edit"] = dict(reasons)
    st["aider_no_edit_invalid"] = sum(n for k, n in reasons.items() if k in AIDER_SETUP_REASONS)


def _check_calls(arm, rep, arm_rows, sections, st, allowed, flag, log_path: Path) -> None:
    if not log_path.is_file():
        st["call_log"] = None
        flag("warn", "no_call_log", arm, None, rep,
             f"{log_path} missing (run predates llm_call_log): per-call checks skipped")
        return
    calls = _read_calls(log_path)
    st["call_log"] = str(log_path)
    st["calls"] = len(calls)
    st["calls_by_component"] = dict(Counter(c.get("component") for c in calls))
    st["call_errors"] = sum(1 for c in calls if c.get("error"))

    # ≥1 logged call per non-invalid job.
    per_job = _attribute(calls, sections)
    for r in arm_rows:
        job = int(r.get("job") or 0)
        if not r.get("invalid") and not per_job.get(job):
            flag("fatal", "no_calls_for_job", arm, job, rep,
                 "no model call logged for this job — the log missed it or the job did nothing")

    # State-writing components: no truncated or empty reply may be kept.
    for c in calls:
        comp = c.get("component")
        if comp not in STATE_WRITERS or c.get("error"):
            continue
        truncated = c.get("finish_reason") in TRUNCATED
        empty = c.get("visible_chars") == 0
        if not (truncated or empty):
            continue
        what = "truncated (finish_reason=%s)" % c.get("finish_reason") if truncated else "empty"
        job = _job_of(c, per_job)
        if c.get("final", True):
            flag("fatal", "state_write_bad_reply", arm, job, rep,
                 f"{comp} reply {what}, final attempt, model {c.get('response_model') or c.get('requested_model')}")
        else:
            flag("warn", "state_write_retry", arm, job, rep, f"{comp} reply {what}, retried")

    # Agent-loop truncation rate.
    agent = [c for c in calls if c.get("component") == "agent" and not c.get("error")]
    trunc = sum(1 for c in agent if c.get("finish_reason") in TRUNCATED)
    st["agent_replies"] = len(agent)
    st["agent_truncated"] = trunc
    st["agent_empty_replies"] = sum(1 for c in agent if c.get("visible_chars") == 0
                                    and not c.get("tool_calls"))
    rate = trunc / len(agent) if agent else 0.0
    st["agent_truncation_rate"] = round(rate, 4)
    if rate > AGENT_TRUNCATION_WARN:
        flag("warn", "agent_truncation_rate", arm, None, rep,
             f"{trunc}/{len(agent)} agent replies truncated ({rate:.1%} > {AGENT_TRUNCATION_WARN:.0%})")

    # Every requested and answering model is pinned.
    seen: Counter = Counter()
    bad: Counter = Counter()
    for c in calls:
        for field in ("requested_model", "response_model"):
            m = c.get(field)
            if not m:
                continue
            seen[m] += 1
            if not _model_ok(m, allowed):
                bad[(m, c.get("component"), field)] += 1
    st["models_seen"] = dict(seen)
    for (m, comp, field), n in sorted(bad.items(), key=str):
        flag("fatal", "unpinned_model", arm, None, rep,
             f"{comp} {field} {m!r} x{n} not in allowed {sorted(allowed)}")

    _check_provider(arm, rep, calls, agent, st, flag)

    # Billing vs logged cost.
    logged = sum(c["cost_usd"] for c in calls if isinstance(c.get("cost_usd"), (int, float)))
    st["logged_cost_usd"] = round(logged, 6)
    billed = st.get("billed_usd") or 0.0
    if billed > 0 and abs(billed - logged) / billed > BILLING_MAX_GAP:
        flag("warn", "billing_mismatch", arm, None, rep,
             f"billed ${billed:.4f} vs logged ${logged:.4f} "
             f"({abs(billed - logged) / billed:.0%} apart)")


def _provider_key(name: Any) -> str:
    """"DeepInfra" / "deepinfra" / "deepinfra/fp8" -> "deepinfra"."""
    return re.sub(r"[^a-z0-9]", "", str(name).strip().lower().split("/")[0])


def _check_provider(arm, rep, calls: list[dict], agent: list[dict], st: dict, flag) -> None:
    """Provider pin (AWOS_OPENROUTER_PROVIDER) honoured, and prompt-cache use."""
    # Cache stats for every arm: Σcached / Σinput over agent calls, and the
    # share of agent turns ≥2 (prefix already sent once) that hit the cache.
    total_in = sum(c.get("input_tokens") or 0 for c in agent)
    total_cached = sum(c.get("cached_tokens") or 0 for c in agent)
    share = total_cached / total_in if total_in else 0.0
    later = [c for c in agent if isinstance(c.get("turn"), int) and c["turn"] >= 2]
    hits = sum(1 for c in later if (c.get("cached_tokens") or 0) > 0)
    st["agent_cache_share"] = round(share, 4)
    st["agent_later_turns"] = len(later)
    st["agent_later_turns_cached_share"] = round(hits / len(later), 4) if later else 0.0
    st["providers_seen"] = dict(Counter(c.get("provider") for c in calls
                                        if c.get("provider") and not c.get("error")))

    requested = next((c.get("requested_provider") for c in calls
                      if c.get("requested_provider")), None)
    st["requested_provider"] = requested
    if not requested:
        return
    allowed = {_provider_key(p) for p in str(requested).split(",") if p.strip()}
    wrong: Counter = Counter()
    for c in calls:
        served = c.get("provider")
        if served and not c.get("error") and _provider_key(served) not in allowed:
            wrong[(served, c.get("component"))] += 1
    # With fallbacks allowed the pin is a preference: another provider serving a
    # call (the preferred one overloaded) is a cache miss to report, not a fault.
    fallbacks = any(c.get("fallbacks_allowed") for c in calls)
    served_ok = [c for c in calls if c.get("provider") and not c.get("error")]
    st["preferred_provider_share"] = (
        round(1 - sum(wrong.values()) / len(served_ok), 4) if served_ok else None)
    for (served, comp), n in sorted(wrong.items(), key=str):
        flag("warn" if fallbacks else "fatal",
             "provider_fallback" if fallbacks else "provider_mismatch", arm, None, rep,
             f"{comp} served by {served!r} x{n}; run "
             f"{'preferred' if fallbacks else 'pinned'} provider {requested!r}")
    if agent and share < PINNED_MIN_CACHE_SHARE:
        flag("warn", "low_cache_share", arm, None, rep,
             f"provider pinned to {requested!r} but agent cache share {share:.1%} "
             f"< {PINNED_MIN_CACHE_SHARE:.0%} ({total_cached}/{total_in} input tokens cached)")


def _job_of(call: dict, per_job: dict[int, list[dict]]) -> Optional[int]:
    for job, items in per_job.items():
        if any(item is call for item in items):
            return None if job == -1 else job
    return None


# ── CLI ───────────────────────────────────────────────────────────────────────

def format_report(health: dict) -> str:
    st = health["stats"]
    lines = [f"RUN HEALTH: {'OK' if health['ok'] else 'FAIL'} "
             f"({st['fatal']} fatal, {st['warn']} warn; {st['rows']} rows; "
             f"allowed models {', '.join(st['allowed_models']) or '-'})"]
    for sev in ("fatal", "warn"):
        items = [v for v in health["violations"] if v["severity"] == sev]
        if not items:
            continue
        lines.append(f"\n{sev.upper()}:")
        for v in items:
            where = "/".join(str(x) for x in (
                f"r{v['repeat']}" if v["repeat"] not in (None, "") else None,
                v["arm"], f"j{v['job']:02d}" if isinstance(v["job"], int) else None) if x)
            lines.append(f"  [{v['check']}] {where or 'run'}: {v['detail']}")
    lines.append("\nARMS:")
    for arm, a in st["arms"].items():
        bits = [f"jobs={a['jobs']}", f"invalid={a['invalid']} ({a['invalid_share']:.0%})",
                f"billed=${a.get('billed_usd', 0):.4f}"]
        if "calls" in a:
            bits += [f"calls={a['calls']}", f"logged=${a['logged_cost_usd']:.4f}",
                     f"agent_trunc={a['agent_truncation_rate']:.1%}",
                     f"cache={a.get('agent_cache_share', 0):.1%}"]
            if a.get("requested_provider"):
                bits.append(f"provider_pin={a['requested_provider']} "
                            f"seen={a.get('providers_seen')}")
        elif not _is_aider(arm.split("/")[-1]):
            bits.append("calls=n/a (no call log)")
        if "aider_no_edit" in a:
            bits.append(f"aider_no_edit={a['aider_no_edit']}")
        if "notebook_chars" in a:
            bits.append(f"notebook={a['notebook_chars']}")
        lines.append(f"  {arm}: " + " ".join(bits))
    lines.append(f"\nJobs invalid in some arm: {st['jobs_dropped_any_arm']}/{st['jobs_total']} "
                 f"({st['dropped_share']:.0%})")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_root", type=Path)
    ap.add_argument("results_json", type=Path)
    ap.add_argument("--allow", action="append", default=None,
                    help="allowed model id (repeatable); default: the run's pin + Aider's model")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to write the JSON (default: health.json next to the results)")
    args = ap.parse_args(argv)
    health = check_run(args.run_root, args.results_json,
                       set(args.allow) if args.allow else None)
    print(format_report(health))
    out = args.out or args.results_json.parent / "health.json"
    out.write_text(json.dumps(health, indent=2) + "\n")
    print(f"\nwrote {out}")
    return 0 if health["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
