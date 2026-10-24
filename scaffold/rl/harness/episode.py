"""
episode.py — one rollout: TaskSpec -> MicroAgent on ChromiumBackend -> (Trajectory, Score).

    traj, score = run_episode(source, task, model, model_label="qwen3.5-4b", chromium=ch)
    admitted, rejected = admit(source, tasks, chromium=ch)

The Subtask is built from the TaskSpec exactly like a product subtask:
    name = task.id, goal = task.goal, start = source.reset(task) (absolute loopback URL),
    secrets_in / secrets_out from the task (values from task.meta["secrets"], put in a fresh
    in-memory BrokerSecretStore), allow_writes = task.allow_writes, max_steps = min(task, 12),
    ack = task.meta["ack"] (list of regexes) or GENERIC_ACK,
    approver = approves writes (create / submit), NEVER a destructive control.
MicroAgent runs with the product wiring (WebAXGrounder, BrokerSecretStore), so prompts,
grammars and code guards are the product's.

guards:
    "product"  MicroAgent as shipped: destructive controls are refused in code
               (event destructive_blocked), MFA / CAPTCHA / passkey / password walls and HTTP
               errors end the run with needs_user before the model is asked.
    "raw"      those two code guards are switched off for the episode (micro_agent.guard_page
               -> None, micro_agent.is_destructive -> False) so what the model CHOOSES takes
               effect: a trap click reaches the app (and its event log), and at a checkpoint the
               model itself must answer needs_user. Prompt-level product behaviour (injection
               warning, masking, grammar) is unchanged.

Every Trajectory gets one extra event {"event": "harness", ...}: guards, stop_by (who ended the
run: model | guard:<kind> | grounding | invalid | duplicates | model_error | infra | cap |
denied | done), model-chosen needs_user / destructive attempts, usage, estimated tokens,
per-op browser timings. Infra failures (browser, source, server) are caught and recorded in
traj.error; they never raise.
"""
from __future__ import annotations

import contextlib
import time
import traceback
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional, Tuple

from scaffold.agent.desktop import micro_agent as MA
from scaffold.rl.contract import Score, TaskSpec, Trajectory

from .models import OracleModel, RecordingModel, parse_reply

GENERIC_ACK = [r"\b(saved|created|updated|added|sent|submitted|scheduled|booked|confirmed|completed|done|"
               r"success(ful(ly)?)?|thank you|changes applied|archived|moved|renamed|uploaded|approved)\b"]

_GUARD_REASONS = {
    "captcha": "page asks for a CAPTCHA", "passkey": "page asks for a passkey",
    "mfa": "page asks for a multi-factor", "signin": "page asks for a password this subtask",
    "http": "unexpected page (HTTP",
}


def auto_approver(req: MA.ApprovalRequest) -> bool:
    """Approves writes; never a destructive control (MicroAgent never asks for one it was not
    told to allow, but this keeps the rule explicit)."""
    return req.action != "destructive"


@contextlib.contextmanager
def guard_mode(guards: str):
    if guards == "product":
        yield
        return
    if guards != "raw":
        raise ValueError(f"guards must be 'product' or 'raw', not {guards!r}")
    orig = (MA.guard_page, MA.is_destructive)
    MA.guard_page = lambda p, s: None  # type: ignore[assignment]
    MA.is_destructive = lambda c: False  # type: ignore[assignment]
    try:
        yield
    finally:
        MA.guard_page, MA.is_destructive = orig  # type: ignore[assignment]


def build_subtask(task: TaskSpec, start_url: str) -> MA.Subtask:
    ack = task.meta.get("ack")
    if isinstance(ack, str):
        ack = [ack]
    return MA.Subtask(name=task.id, goal=task.goal, start=start_url, secrets_in=list(task.secrets_in),
                      secrets_out=list(task.secrets_out), allow_writes=bool(task.allow_writes),
                      approval=None, ack=list(ack or GENERIC_ACK),
                      max_steps=max(1, min(int(task.max_steps or MA.MAX_STEPS), MA.MAX_STEPS)))


def stop_by(res: MA.MicroResult, rec: RecordingModel) -> str:
    """Who ended the run."""
    o, reason = res.outcome, res.reason or ""
    last = rec.steps[-1] if rec.steps else None
    if o is MA.Outcome.NEEDS_USER:
        if last is not None and last.action.startswith("needs_user(") and res.steps == last.i:
            ev = next((e for e in reversed(res.events) if e.get("step") == last.i and "action" in e), None)
            if ev is not None and ev["action"].startswith("needs_user("):
                return "model"
        for kind, prefix in _GUARD_REASONS.items():
            if reason.startswith(prefix):
                return f"guard:{kind}"
        if reason.startswith("model call failed"):
            return "model_error"
        if reason.startswith("cannot open the start page"):
            return "infra"
        if "ambiguous control" in reason or "has no accessible name" in reason:
            return "grounding"
        if "kept repeating" in reason:
            return "duplicates"
        if "invalid actions" in reason:
            return "invalid"
        if "already shows what this subtask would create" in reason:
            return "guard:forbid_before_write"
        return "guard:other"
    if o is MA.Outcome.HALTED:
        return "cap" if "cap" in reason else "halted"
    if o is MA.Outcome.DENIED:
        return "denied"
    return "done"


def _est_tokens(steps) -> int:
    return sum((len(s.system) + len(s.user) + len(s.reply)) // 4 for s in steps)


def _usage_delta(before: Optional[dict], after: Optional[dict]) -> Optional[dict]:
    if not after:
        return None
    b = before or {}
    return {k: (after.get(k, 0) - b.get(k, 0)) for k in after if isinstance(after.get(k), (int, float))}


def run_episode(source: Any, task: TaskSpec, model: Any, *, model_label: str, guards: str = "product",
                chromium: Any = None, backend_kw: Optional[dict] = None,
                ) -> Tuple[Trajectory, Score]:
    """Reset ``source`` for ``task``, run MicroAgent through headless Chromium, score with
    ``source.outcome``. Never raises for infra failures (see traj.error)."""
    from scaffold.agent.desktop import micro_wiring as mw
    from .chromium import ChromiumBackend
    traj = Trajectory(task_id=task.id, model=model_label)
    t0 = time.monotonic()
    backend = None
    rec = RecordingModel(model)
    harness: Dict[str, Any] = {"event": "harness", "guards": guards}
    usage0 = dict(getattr(model, "usage", None) or {}) or None
    res: Optional[MA.MicroResult] = None
    try:
        start_url = source.reset(task)
        u = urllib.parse.urlsplit(start_url)
        base = f"{u.scheme}://{u.netloc}"
        backend = ChromiumBackend(base, chromium=chromium, **(backend_kw or {}))
        rec.url_of = lambda: backend.page.url if backend.page is not None else ""
        store = mw.make_store("memory")
        for name, value in (task.meta.get("secrets") or {}).items():
            store.put(name, str(value))
        agent = MA.MicroAgent(backend, rec, store=store, grounder=mw.default_grounder("real"),
                              approver=auto_approver)
        st = build_subtask(task, start_url)
        with guard_mode(guards):
            res = agent.run(st)
        traj.outcome, traj.reason = res.outcome.value, res.reason
        traj.events, traj.flags = list(res.events), list(res.flags)
    except Exception as e:                                 # browser / source / server failure
        traj.error = f"{type(e).__name__}: {e}"[:500]
        harness["traceback"] = traceback.format_exc()[-2000:]
    finally:
        traj.steps = rec.steps
        if backend is not None:
            harness["browser_log"] = list(backend.log)
            harness["interruptions"] = list(backend.interruptions)
            harness["timings"] = list(backend.timings)
            try:
                backend.close()
            except Exception:
                pass
    # accepted = parsed AND not rejected by MicroAgent at that step
    rejected = {e.get("step") for e in traj.events if "rejected" in e and e.get("step") is not None}
    for s in traj.steps:
        s.valid = bool(s.valid and s.i not in rejected)
    if res is not None:
        harness["stop_by"] = stop_by(res, rec)
    if rec.errors and not traj.error and harness.get("stop_by") == "model_error":
        harness["model_error"] = rec.errors[-1]
        # our cloud spend cap, or the API account's own usage limit / rate limit: not a result, retry on resume
        if any(s in str(rec.errors[-1]) for s in ("budget reached", "usage limits", "RateLimitError", "overloaded")):
            traj.error = f"cloud budget reached: {rec.errors[-1]}"[:500]
    harness["model_needs_user"] = any(s.action.startswith("needs_user(") for s in traj.steps)
    harness["destructive_attempts"] = [e.get("control") for e in traj.events if e.get("event") == "destructive_blocked"]
    harness["usage"] = _usage_delta(usage0, getattr(model, "usage", None))
    harness["est_tokens"] = _est_tokens(traj.steps)
    if isinstance(model, OracleModel) and model.failures:
        harness["oracle_failures"] = list(model.failures)
    traj.events.append(harness)
    traj.seconds = round(time.monotonic() - t0, 3)
    try:
        score = source.outcome(task, traj)
    except Exception as e:
        if not traj.error:
            traj.error = f"outcome: {type(e).__name__}: {e}"[:500]
        score = Score(success=False, notes="verifier failed")
    return traj, score


def harness_event(traj: Trajectory) -> dict:
    return next((e for e in reversed(traj.events) if e.get("event") == "harness"), {})


def admit(source: Any, tasks: Iterable[TaskSpec], *, chromium: Any = None, guards: str = "product",
          ) -> Tuple[List[TaskSpec], List[Tuple[TaskSpec, str]]]:
    """Run each task's oracle through the full harness. Admitted iff the verifier says success
    with no unsafe codes and no infra error."""
    admitted: List[TaskSpec] = []
    rejected: List[Tuple[TaskSpec, str]] = []
    for task in tasks:
        try:
            steps = source.oracle(task)
        except Exception as e:
            rejected.append((task, f"oracle unavailable: {type(e).__name__}: {e}"))
            continue
        traj, score = run_episode(source, task, OracleModel(steps), model_label="oracle", guards=guards,
                                  chromium=chromium)
        h = harness_event(traj)
        if traj.error:
            rejected.append((task, f"infra: {traj.error}"))
        elif score.unsafe:
            rejected.append((task, f"oracle unsafe: {','.join(score.unsafe)}"))
        elif not score.success:
            why = "; ".join(h.get("oracle_failures") or []) or f"{traj.outcome}: {traj.reason}"
            rejected.append((task, f"oracle failed: {why}" + (f" ({score.notes})" if score.notes else "")))
        else:
            admitted.append(task)
    return admitted, rejected
