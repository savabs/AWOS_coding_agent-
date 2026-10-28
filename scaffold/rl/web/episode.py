"""
episode.py — one live-web rollout: TaskSpec -> MicroAgent (open-web verbs) on ChromiumBackend
(web=True) -> (Trajectory, Score) + the per-task folder WebJudge reads.

    traj, score = run_web_episode(task, model, model_label="qwen3.5-4b", chromium=ch, judge_root=out / "judge")

Same observation path as the RL harness (CDP AX -> axmap -> driver.to_micro_page -> web_tools
outline) and the same MicroAgent prompt / grammar / code guards, with:
  * web_verbs=True (fill_enter / go_back / goto), max_steps_cap = task.max_steps (25),
    max_seconds_cap = the per-task wall clock (default 600 s);
  * a page hook on every observation: clip_page (prompt budget) then detect_block (CAPTCHA,
    Cloudflare, bot wall, 403/429, a start page that failed). A blocked page stops the episode
    (UserNeeded -> needs_user) and is recorded in the harness event as ``blocked``;
  * every observation waits for the page within ChromiumBackend's open-web read budget (load event,
    one re-read of a near-empty page; AWOS_WEB_SETTLE_S, default 8 s); the cost is in the harness
    event as ``timings`` (per op) and ``settle`` (summary);
  * a viewport screenshot before every model call (= the state after the previous action) and
    one at the end.

Judge layout (Online-Mind2Web submission schema v2, ``online-mind2web-v2``; the official
src/run.py reads it with --trajectories_dir <judge_root>):

    <judge_root>/<task_id>/result.json
    <judge_root>/<task_id>/trajectory/0000.png 0001.png ...

    result.json = {schema_version, task, task_id, agent_final_answer, reference_length,
                   action_history: [{step, screenshot, url, action, action_status, thought}],
                   + AWOS keys level, harness_status (ok | blocked | infra_error), harness_note}
                  (the AWOS keys are what scaffold/rl/web_judge uses to skip blocked episodes and count
                  them as failures; the official src/run.py ignores extra keys)
    step 0         "page -> NAVIGATE -> open the task's start page | SUCCESS|FAILED" (start URL)
    step j         one model step, Grammar A: '<target> -> <VERB> -> <our action> | SUCCESS|FAILED',
                   target = the control ('link "Sign in"') or page; screenshot = the page after it
    last step      'TASK_COMPLETE -> ANSWER: <done text>' (agent_final_answer = that text), or
                   'TASK_COMPLETE -> STOPPED: <why>' with agent_final_answer = null
"""
from __future__ import annotations

import json
import re
import shutil
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from scaffold.agent.desktop import micro_agent as MA
from scaffold.agent.desktop.web_tools import Page, UserNeeded, mask_secrets
from scaffold.rl.contract import Score, TaskSpec, Trajectory
from scaffold.rl.harness.episode import _est_tokens, _usage_delta, auto_approver, guard_mode, stop_by
from scaffold.rl.harness.models import RecordingModel

from .blocked import DOM_PROBE_JS, detect_block

SCHEMA_VERSION = "online-mind2web-v2"
AWOS_KEYS = ("level", "harness_status", "harness_note")
MAX_SECONDS = 600.0
#: prompt budget on large real pages (the outline already keeps 3000 chars of text, 40 controls)
NAME_CHARS, LIVE_CHARS, MAX_REGIONS = 150, 600, 6


def _png_1x1() -> bytes:
    """A 1x1 white PNG, for a step whose screenshot failed (the schema needs a file per step)."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b""))


_BLANK_PNG = _png_1x1()
_VERBS = {"click": "CLICK", "fill": "TYPE", "fill_enter": "TYPE", "select": "SELECT", "scroll": "SCROLL",
          "go_back": "GO_BACK", "goto": "NAVIGATE", "copy_to_secret": "READ"}


def clip_page(p: Page) -> Page:
    """Keep one observation of a huge real page under ~6k tokens: control names <= 150 chars,
    each alert / status / region / dialog text <= 600 chars, at most 6 regions."""
    def cut(s: str, n: int) -> str:
        return s if len(s) <= n else s[: n - 1] + "…"
    for c in p.controls:
        c.name = cut(c.name, NAME_CHARS)
        c.context = cut(c.context, 80)
        if len(c.options) > 30:
            c.options = c.options[:30]
    p.alerts = [cut(x, LIVE_CHARS) for x in p.alerts[:MAX_REGIONS]]
    p.statuses = [cut(x, LIVE_CHARS) for x in p.statuses[:MAX_REGIONS]]
    p.regions = [cut(x, LIVE_CHARS) for x in p.regions[:MAX_REGIONS]]
    p.dialog = cut(p.dialog, LIVE_CHARS)
    p.headings = [cut(h, 200) for h in p.headings[:20]]
    p.title = cut(p.title, 200)
    return p


def settle_summary(timings: List[dict]) -> Dict[str, Any]:
    """What waiting for pages cost this episode (the per-op detail stays in ``timings``): total and
    worst settle ms, how many reads waited for ``load`` past the settle and how many near-empty pages
    were read twice (ChromiumBackend web_settle_cap)."""
    ms = [float(t.get("settle_ms") or 0.0) for t in timings]
    return {"ops": len(ms), "total_ms": round(sum(ms), 1), "max_ms": round(max(ms or [0.0]), 1),
            "load_waits": sum(1 for t in timings if t.get("load_wait_ms")),
            "rereads": sum(int(t.get("reread") or 0) for t in timings)}


class ShotModel:
    """ActionModel wrapper: a viewport screenshot before each decide() (outside the timed call)."""

    def __init__(self, inner: RecordingModel, shoot):
        self.inner, self.shoot = inner, shoot
        self.output_format = inner.output_format
        self.calls = 0

    @property
    def usage(self):
        return self.inner.usage

    def decide(self, system: str, user: str, grammar: Optional[str] = None) -> str:
        self.shoot(self.calls)
        self.calls += 1
        return self.inner.decide(system, user, grammar)


def _thought(reply: str) -> Optional[str]:
    for ln in (reply or "").splitlines():
        if ln.strip().startswith("#"):
            t = ln.strip().lstrip("#").strip()
            return t or None
    return None


def build_subtask(task: TaskSpec, max_steps: int, max_seconds: float) -> MA.Subtask:
    return MA.Subtask(name=task.id, goal=task.goal, start=task.start, allow_writes=True, ack=[],
                      max_steps=max_steps, max_seconds=max_seconds)


def run_web_episode(task: TaskSpec, model: Any, *, model_label: str, chromium: Any, judge_root: Path,
                    max_steps: Optional[int] = None, max_seconds: float = MAX_SECONDS, guards: str = "product",
                    allow_private: bool = False, backend_kw: Optional[dict] = None,
                    ) -> Tuple[Trajectory, Score]:
    """Never raises for infra failures (traj.error). Writes <judge_root>/<task.id>/."""
    from scaffold.agent.desktop import micro_wiring as mw
    from scaffold.rl.harness.chromium import ChromiumBackend
    max_steps = int(max_steps or task.max_steps or 25)
    traj = Trajectory(task_id=task.id, model=model_label)
    t0 = time.monotonic()
    tdir = Path(judge_root) / task.id
    shots_dir = tdir / "trajectory"
    if tdir.exists():
        shutil.rmtree(tdir)                       # a resumed task is re-run from scratch
    raw_dir = tdir / ".raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    harness: Dict[str, Any] = {"event": "harness", "guards": guards, "web": True, "blocked": None}
    state: Dict[str, Any] = {"backend": None, "decides": 0}
    rec = RecordingModel(model)
    shots: Dict[str, Optional[Path]] = {}

    def shoot(key: str) -> None:
        b = state["backend"]
        p = raw_dir / f"{key}.png"
        shots[key] = p if (b is not None and b.screenshot(str(p))) else None

    def hook(page: Page) -> None:
        clip_page(page)
        b = state["backend"]
        marks: List[str] = []
        try:
            marks = list(b.pg.evaluate(DOM_PROBE_JS) or []) if b is not None else []
        except Exception:
            pass
        blk = detect_block(page, marks, start=shot_model.calls == 0)
        if blk is not None:
            harness["blocked"] = blk
            raise UserNeeded(f"blocked page ({blk['kind']}): {blk['evidence'][:80]}")

    shot_model = ShotModel(rec, lambda k: shoot(f"s{k}"))
    usage0 = dict(getattr(model, "usage", None) or {}) or None
    res: Optional[MA.MicroResult] = None
    backend = None
    try:
        kw = {"load_timeout": 30.0, "settle_timeout": 4.0, **(backend_kw or {})}
        backend = ChromiumBackend(task.start, chromium=chromium, web=True, allow_private=allow_private,
                                  page_hook=hook, **kw)
        state["backend"] = backend
        rec.url_of = lambda: backend.pg.url
        agent = MA.MicroAgent(backend, shot_model, store=mw.make_store("memory"),
                              grounder=mw.default_grounder("real"), approver=auto_approver, web_verbs=True,
                              max_steps_cap=max_steps, max_seconds_cap=max_seconds)
        with guard_mode(guards):
            res = agent.run(build_subtask(task, max_steps, max_seconds))
        traj.outcome, traj.reason = res.outcome.value, res.reason
        traj.events, traj.flags = list(res.events), list(res.flags)
    except Exception as e:                                  # browser / launch failure
        traj.error = f"{type(e).__name__}: {e}"[:500]
        harness["traceback"] = traceback.format_exc()[-2000:]
    finally:
        traj.steps = rec.steps
        if backend is not None:
            shoot("final")
            try:
                harness["final_url"] = backend.pg.url
            except Exception:
                pass
            harness["browser_log"] = list(backend.log)
            harness["interruptions"] = list(backend.interruptions)
            harness["timings"] = list(backend.timings)
            harness["settle"] = settle_summary(backend.timings)
            try:
                backend.close()
            except Exception:
                pass
    rejected = {e.get("step") for e in traj.events if "rejected" in e and e.get("step") is not None}
    for s in traj.steps:
        s.valid = bool(s.valid and s.i not in rejected)
    if res is not None:
        harness["stop_by"] = stop_by(res, rec)
        if harness["blocked"] is None and res.reason.startswith("cannot open the start page"):
            harness["blocked"] = {"kind": "load_failed", "evidence": res.reason[:160], "url": task.start,
                                  "status": 0}
        if harness["blocked"] is not None:
            harness["stop_by"] = f"blocked:{harness['blocked']['kind']}"
    if rec.errors and not traj.error and harness.get("stop_by") == "model_error":
        harness["model_error"] = rec.errors[-1]
        if any(s in str(rec.errors[-1]) for s in ("budget reached", "usage limits", "RateLimitError", "overloaded")):
            traj.error = f"cloud budget reached: {rec.errors[-1]}"[:500]
    last = traj.steps[-1].action if traj.steps else ""
    harness["answer"] = res.summary if (res is not None and last.startswith("done(")) else None
    harness["usage"] = _usage_delta(usage0, getattr(model, "usage", None))
    harness["est_tokens"] = _est_tokens(traj.steps)
    harness["max_prompt_tokens_est"] = max([(len(s.system) + len(s.user)) // 4 for s in traj.steps] or [0])
    harness["screenshots_missing"] = sorted(k for k, v in shots.items() if v is None)
    traj.events.append(harness)
    traj.seconds = round(time.monotonic() - t0, 3)
    try:
        write_judge_task(tdir, task, traj, shots)
    except Exception as e:
        harness["judge_write_error"] = f"{type(e).__name__}: {e}"[:300]
    shutil.rmtree(raw_dir, ignore_errors=True)
    b = harness["blocked"]
    return traj, Score(success=False, notes=f"blocked:{b['kind']}" if b else "unjudged (WebJudge scores offline)")


# ------------------------------------------------------------------ judge layout --

def _event_for(traj: Trajectory, i: int) -> dict:
    return next((e for e in traj.events if e.get("step") == i and ("action" in e or "rejected" in e)), {})


def step_action(step, ev: dict) -> Tuple[str, str]:
    """-> (Grammar A action string, SUCCESS|FAILED) for one non-terminal model step."""
    act = step.action or ""
    verb = _VERBS.get(act.split("(", 1)[0], "WAIT") if act else "WAIT"
    target = ev.get("control") or "page"
    failed = (not step.valid) or bool(ev.get("rejected"))
    desc = act or f"invalid reply: {mask_secrets((step.reply or '').strip().splitlines()[-1] if step.reply else '')[:120]}"
    if act.startswith("fill_enter("):
        desc += " then press Enter"
    if ev.get("rejected"):
        desc += f" (rejected: {str(ev['rejected'])[:120]})"
    status = "FAILED" if failed else "SUCCESS"
    return f"{target} -> {verb} -> {' '.join(desc.split())} | {status}", status


def write_judge_task(tdir: Path, task: TaskSpec, traj: Trajectory, shots: Dict[str, Optional[Path]]) -> Path:
    """<tdir>/result.json + <tdir>/trajectory/NNNN.png (see the module docstring)."""
    out = Path(tdir) / "trajectory"
    out.mkdir(parents=True, exist_ok=True)
    h = next((e for e in reversed(traj.events) if e.get("event") == "harness"), {})
    steps: List[dict] = []

    def add(action: str, status: Optional[str], url: str, thought: Optional[str], shot_key: str) -> None:
        k = len(steps)
        name = f"{k:04d}.png"
        src = shots.get(shot_key) or shots.get("final")
        if src is not None and Path(src).is_file():
            shutil.copyfile(src, out / name)
        else:
            (out / name).write_bytes(_BLANK_PNG)
        steps.append({"step": k, "screenshot": name, "url": url or None, "action": action,
                      "action_status": status, "thought": thought})

    n = len(traj.steps)
    first_url = (traj.steps[0].url if traj.steps else "") or h.get("final_url") or task.start
    opened = n > 0 and not (h.get("blocked") or {}).get("kind") == "load_failed"
    add(f"page -> NAVIGATE -> open the task's start page {task.start} | {'SUCCESS' if opened else 'FAILED'}",
        "SUCCESS" if opened else "FAILED", first_url, None, "s0")
    answer = h.get("answer")
    for idx, s in enumerate(traj.steps, 1):
        terminal = idx == n and (s.action.startswith("done(") or s.action.startswith("needs_user("))
        if terminal:
            break
        action, status = step_action(s, _event_for(traj, s.i))
        add(action, status, s.url, _thought(s.reply), f"s{idx}")
    last = traj.steps[-1] if traj.steps else None
    thought = _thought(last.reply) if last is not None and (last.action.startswith(("done(", "needs_user("))) else None
    if answer is not None:
        final = f"TASK_COMPLETE -> ANSWER: {' '.join(str(answer).split())}"
    else:
        why = (f"blocked page ({h['blocked']['kind']})" if h.get("blocked")
               else f"{traj.outcome or 'error'}: {traj.reason or traj.error}")
        final = f"TASK_COMPLETE -> STOPPED: {' '.join(mask_secrets(why).split())[:300]}"
    add(final, None, h.get("final_url") or (last.url if last else task.start), thought, "final")
    rl = task.meta.get("reference_length")
    blk = h.get("blocked")
    status = "blocked" if blk else ("infra_error" if traj.error else "ok")
    note = (f"{blk['kind']}: {blk.get('evidence') or ''}".strip() if blk
            else (traj.error[:200] if traj.error else f"stop_by={h.get('stop_by', '?')}"))
    doc = {"schema_version": SCHEMA_VERSION, "task": task.goal, "task_id": task.id,
           "agent_final_answer": " ".join(str(answer).split()) if answer is not None else None,
           "reference_length": int(rl) if isinstance(rl, int) and rl >= 1 else 1,
           "action_history": steps,
           # AWOS additions (scaffold/rl/web_judge reads them; the official src/run.py ignores extra keys)
           "level": task.meta.get("level") or None, "harness_status": status, "harness_note": note}
    (Path(tdir) / "result.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return Path(tdir) / "result.json"


def validate_judge_task(tdir: Path) -> List[str]:
    """The v2 rules we can check without jsonschema (schema_v2 README §6). [] = valid. The three AWOS
    keys (level, harness_status, harness_note) are allowed on top of the strict schema."""
    errs: List[str] = []
    d = json.loads((Path(tdir) / "result.json").read_text())
    allowed = {"schema_version", "task", "task_id", "agent_final_answer", "reference_length", "action_history",
               *AWOS_KEYS}
    if d.get("harness_status", "ok") not in ("ok", "blocked", "infra_error"):
        errs.append("harness_status")
    if set(d) - allowed:
        errs.append(f"extra keys {sorted(set(d) - allowed)}")
    for k in ("schema_version", "task", "task_id", "action_history", "reference_length"):
        if k not in d:
            errs.append(f"missing {k}")
    if d.get("schema_version") != SCHEMA_VERSION:
        errs.append("schema_version")
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", str(d.get("task_id", ""))):
        errs.append("task_id pattern")
    if not isinstance(d.get("reference_length"), int) or d["reference_length"] < 1:
        errs.append("reference_length")
    hist = d.get("action_history") or []
    if not hist:
        errs.append("empty action_history")
    names = []
    for i, s in enumerate(hist):
        if set(s) - {"step", "screenshot", "url", "action", "action_status", "thought"}:
            errs.append(f"step {i}: extra keys")
        if s.get("step") != i:
            errs.append(f"step {i}: index")
        if "thought" not in s:
            errs.append(f"step {i}: thought key")
        if not re.fullmatch(r"(\d{4}|\d+_full_screenshot_\d+)\.(png|jpg|jpeg|webp)", str(s.get("screenshot", ""))):
            errs.append(f"step {i}: screenshot name")
        elif not (Path(tdir) / "trajectory" / s["screenshot"]).is_file():
            errs.append(f"step {i}: screenshot file missing")
        names.append(str(s.get("screenshot")))
        st = s.get("action_status")
        if st is not None:
            if st not in ("SUCCESS", "FAILED") or not str(s.get("action", "")).endswith(f"| {st}"):
                errs.append(f"step {i}: action_status disagrees with the action suffix")
    if names != sorted(names):
        errs.append("screenshots do not sort into step order")
    if hist:
        fin = str(hist[-1].get("action", ""))
        if not fin.startswith("TASK_COMPLETE"):
            errs.append("last step is not TASK_COMPLETE")
        ans = d.get("agent_final_answer")
        if ans is not None and " ".join(fin.split("TASK_COMPLETE -> ANSWER:", 1)[-1].split()) != " ".join(ans.split()):
            errs.append("agent_final_answer differs from the TASK_COMPLETE answer")
    return errs
