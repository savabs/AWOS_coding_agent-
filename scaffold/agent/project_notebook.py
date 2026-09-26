"""
project_notebook.py — What the agent learned about one project, job to job.

docs/specs/compounding_proof_spec.md, piece A. On the agent_loop path nothing
learned used to reach the next job: every goal on a project started from the
task text and a fresh exploration, and re-discovered the test command, the
project's error type and the pitfall that cost the last job ten turns.

The notebook is the per-project memory an employee builds up. It lives at
`.awos/projects/<project_id>/notebook.md` (cwd-relative, like all state;
local only, never uploaded):

- Read: the orchestrator puts it in every agent-loop prompt of a goal, headed
  as possibly out of date, capped at READ_CAP chars.
- Write: once per goal, after the goal (and its goal check) finishes, one
  cheap LLM call rewrites it from the old notebook, the goal, the outcome and
  a compact action trace of the goal's agent-loop tasks. The model returns the
  whole notebook in four fixed sections; anything else is dropped, and a
  failed or unparseable reply keeps the old notebook.

AWOS_NOTEBOOK=0 turns off both. AWOS_NOTEBOOK_MODEL picks the update's model
(default: the cheap worker model through OpenRouter). AWOS_PROJECT_ID names
the project; else it is derived from the codebase root's path.

Stdout markers (live proof): `[notebook] read <n> chars from <path>` and
`[notebook] updated <path> (<n> chars, $<cost>)`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

STATE_DIR = ".awos"
#: Chars of the notebook a prompt carries.
READ_CAP = 4000
#: The notebook the update may write; a longer reply is trimmed to fit.
MAX_NOTEBOOK_CHARS = 3500
#: The whole goal's action trace given to the update.
MAX_TRACE_CHARS = 6000
#: Lines kept under "## Past jobs" (the newest).
MAX_PAST_JOBS = 15
#: The update's model when AWOS_NOTEBOOK_MODEL is not set: the cheap worker.
DEFAULT_NOTEBOOK_MODEL = "deepseek/deepseek-v4-flash"
#: Room for a 3500-char notebook plus a little reasoning chatter.
MAX_OUTPUT_TOKENS = 2000

SECTIONS = (
    "## How to work here",
    "## Layout and conventions",
    "## Pitfalls",
    "## Past jobs",
)

HEADER = (
    "What you learned about this project on earlier jobs (may be out of date; "
    "check before relying on it):"
)

SYSTEM_PROMPT = f"""You keep a coding agent's notebook about ONE software
project. After each job you rewrite the notebook so the next job on this
project goes faster: the commands that work, where things live, the project's
own rules, and what went wrong before and what fixed it.

Rules:
- Use only facts observed in the job's trace and outcome, or already in the
  notebook. No guesses, no generic advice.
- Keep what is still true; correct what the trace shows is wrong; drop what
  no longer matters.
- Return the WHOLE new notebook, at most {MAX_NOTEBOOK_CHARS} characters, in
  Markdown with exactly these sections, in this order, and nothing else:

{SECTIONS[0]}
(commands that worked: the test command, entry points, how to run the program)
{SECTIONS[1]}
(where things live; project rules: its error type, helpers to use, formats)
{SECTIONS[2]}
(what went wrong on a past job and what fixed it)
{SECTIONS[3]}
(one line per job, oldest first: goal -> outcome; keep the last {MAX_PAST_JOBS})

Short bullet points. No preamble, no closing remarks, no code fences.
"""


# ── Where it lives ───────────────────────────────────────────────────────────


def enabled() -> bool:
    """AWOS_NOTEBOOK=0 turns the notebook off: no read, no write."""
    return os.getenv("AWOS_NOTEBOOK", "1").strip() != "0"


def project_id(codebase_root: str) -> str:
    """AWOS_PROJECT_ID, else the first 12 hex chars of sha256(resolved root)."""
    explicit = os.getenv("AWOS_PROJECT_ID", "").strip()
    if explicit:
        # It becomes a directory name: nothing that could climb out of it.
        return re.sub(r"[^A-Za-z0-9._-]", "_", explicit).strip(".") or "project"
    resolved = str(Path(codebase_root).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]


def notebook_path(codebase_root: str, state_dir: str = STATE_DIR) -> Path:
    return Path(state_dir) / "projects" / project_id(codebase_root) / "notebook.md"


# ── Read ─────────────────────────────────────────────────────────────────────


def read_notebook(codebase_root: str, state_dir: str = STATE_DIR) -> str:
    """
    The notebook for a prompt, capped at READ_CAP; "" when off, missing or
    empty. Prints the read marker when there is something to read.
    """
    if not enabled():
        return ""
    path = notebook_path(codebase_root, state_dir)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not text:
        return ""
    text = text[:READ_CAP]
    print(f"[notebook] read {len(text)} chars from {path}")
    return text


def prompt_section(notebook: str) -> str:
    """The prompt block for a notebook; "" for none."""
    notebook = (notebook or "").strip()
    if not notebook:
        return ""
    return f"{HEADER}\n{notebook[:READ_CAP]}"


# ── The trace the update learns from ─────────────────────────────────────────


def task_trace(task: dict, outcomes: list, verdict: Optional[dict] = None) -> str:
    """
    One agent-loop task, compactly: its action, every tool call (name, short
    args, ok or the error, a command's output tail) and how it was judged.
    `outcomes` are the task's LoopOutcomes (one per attempt).
    """
    lines = [f"TASK: {' '.join(str(task.get('action', '')).split())[:200]}"]
    for n, outcome in enumerate(outcomes, 1):
        if len(outcomes) > 1:
            lines.append(f" attempt {n}:")
        for turn in getattr(outcome, "transcript", None) or []:
            for call in turn.get("calls", []):
                status = "ok" if call.get("ok") else "ERROR"
                line = f" - {call.get('name', '?')}({call.get('args', '')}) {status}"
                if call.get("detail"):
                    line += f": {call['detail']}"
                lines.append(line)
        lines.append(f" stop: {getattr(outcome, 'stop_reason', '?')}")
    if verdict:
        judged = "passed" if verdict.get("success") else f"failed ({verdict.get('error', '')})"
        lines.append(f" result: {judged}; tests: {verdict.get('test_status', '?')}")
    return "\n".join(lines)


def compact_trace(task_traces: list[str], limit: int = MAX_TRACE_CHARS) -> str:
    """
    The goal's task traces joined, at most `limit` chars. Each task gets an
    equal share, so a long first task cannot crowd out the rest; a task cut
    short keeps its head (what was tried) and its tail (how it ended).
    """
    traces = [t for t in task_traces if t]
    if not traces:
        return ""
    joined = "\n\n".join(traces)
    if len(joined) <= limit:
        return joined
    share = max(200, (limit - 2 * (len(traces) - 1)) // len(traces))
    marker = "\n ...\n"
    parts = []
    for trace in traces:
        if len(trace) > share:
            head = (share - len(marker)) // 2
            tail = share - len(marker) - head
            trace = trace[:head] + marker + trace[-tail:]
        parts.append(trace)
    return "\n\n".join(parts)[:limit]


# ── Write ────────────────────────────────────────────────────────────────────


def notebook_model() -> str:
    return os.getenv("AWOS_NOTEBOOK_MODEL", "").strip() or DEFAULT_NOTEBOOK_MODEL


def _default_client() -> Any:
    """OpenAI-shaped client through OpenRouter; None without its key."""
    try:
        from .providers import chat_client
    except ImportError:
        from providers import chat_client
    return chat_client()


def build_update_prompt(old: str, goal: str, outcome: dict, trace: str) -> str:
    parts = [
        "CURRENT NOTEBOOK:\n" + (old.strip() or "(empty: this is the first job on this project)"),
        "THIS JOB'S GOAL:\n" + goal.strip(),
        "OUTCOME:\n" + _describe_outcome(outcome),
        "ACTION TRACE (tool calls: name(args) ok/ERROR: detail):\n" + (trace or "(none)"),
        "Return the whole new notebook now.",
    ]
    return "\n\n".join(parts)


def _describe_outcome(outcome: dict) -> str:
    lines = [f"success: {bool(outcome.get('success'))}"]
    for key in ("tasks_completed", "tasks_failed", "tests"):
        if outcome.get(key) not in (None, ""):
            lines.append(f"{key}: {outcome[key]}")
    check = outcome.get("goal_check") or {}
    if check and check.get("source") != "not_run":
        lines.append(f"goal check: {'complete' if check.get('complete') else 'incomplete'}"
                     f" — {str(check.get('reasoning', ''))[:500]}")
    if outcome.get("incomplete_reason"):
        lines.append(f"incomplete: {str(outcome['incomplete_reason'])[:300]}")
    return "\n".join(lines)


def parse_notebook(reply: str) -> Optional[str]:
    """
    The reply as a notebook: the four sections only, in order, at most
    MAX_PAST_JOBS past jobs and MAX_NOTEBOOK_CHARS chars. None when it has
    none of the sections (a refusal, an apology, garbage).
    """
    text = (reply or "").strip()
    # A reasoning model may think out loud first, or fence the answer.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```[a-zA-Z]*\n|\n```\s*$", "", text).strip()

    bodies: dict[str, list[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        heading = _section_of(line)
        if heading is not None:
            current = heading
            bodies.setdefault(current, [])
            continue
        if line.startswith("#"):
            current = None  # a section the notebook does not have: dropped
            continue
        if current is not None:
            bodies[current].append(line.rstrip())
    if not bodies:
        return None

    sections = {name: _trim_blank(bodies.get(name, [])) for name in SECTIONS}
    jobs = [line for line in sections[SECTIONS[3]] if line.strip()]
    sections[SECTIONS[3]] = jobs[-MAX_PAST_JOBS:]

    notebook = _render(sections)
    while len(notebook) > MAX_NOTEBOOK_CHARS:
        # Shrink the longest section by one line: the oldest past job, or
        # the last line of any other section.
        name = max(SECTIONS, key=lambda n: sum(len(line) + 1 for line in sections[n]))
        if not sections[name]:
            return notebook[:MAX_NOTEBOOK_CHARS]
        if name == SECTIONS[3]:
            sections[name].pop(0)
        else:
            sections[name].pop()
        notebook = _render(sections)
    return notebook


def _section_of(line: str) -> Optional[str]:
    """The canonical heading a line opens, if it is one of the four."""
    if not line.lstrip().startswith("#"):
        return None
    words = re.sub(r"[^a-z ]", " ", line.lower()).split()
    for name in SECTIONS:
        if words == name.lower().lstrip("# ").split():
            return name
    return None


def _trim_blank(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return lines


def _render(sections: dict[str, list[str]]) -> str:
    return "\n\n".join(
        name + ("\n" + "\n".join(sections[name]) if sections[name] else "")
        for name in SECTIONS
    ) + "\n"


def update_notebook(
    codebase_root: str,
    goal: str,
    outcome: dict,
    trace: str,
    *,
    client: Any = None,
    model: Optional[str] = None,
    tracker: Any = None,
    state_dir: str = STATE_DIR,
) -> Optional[str]:
    """
    Rewrite the notebook after a goal; returns the new text, or None when it
    was left as it was (off, no trace, no client, a failed call, a reply that
    is not a notebook). Never raises: learning must not fail a finished goal.
    """
    if not enabled():
        return None
    if not trace:
        print("[notebook] update skipped: no agent-loop trace for this goal")
        return None
    path = notebook_path(codebase_root, state_dir)
    model = model or notebook_model()
    try:
        client = client if client is not None else _default_client()
    except Exception as exc:
        client = None
        logger.warning("[notebook] no client: %s", exc)
    if client is None:
        print("[notebook] update skipped: no model client (OPENROUTER_API_KEY not set)")
        return None

    try:
        old = path.read_text(encoding="utf-8")
    except OSError:
        old = ""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_update_prompt(old, goal, outcome, trace)},
            ],
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=0,
        )
    except Exception as exc:
        print(f"[notebook] update failed ({type(exc).__name__}); kept {path}")
        logger.warning("[notebook] update call failed: %s", exc)
        return None

    cost = _record_spend(model, response, tracker)
    try:
        reply = response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        reply = ""
    notebook = parse_notebook(reply)
    if notebook is None:
        print(f"[notebook] update unusable (no notebook sections, ${cost:.4f}); kept {path}")
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(notebook, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        print(f"[notebook] update not saved ({exc}); kept {path}")
        return None
    print(f"[notebook] updated {path} ({len(notebook)} chars, ${cost:.4f})")
    return notebook


def _record_spend(model: str, response: Any, tracker: Any) -> float:
    """The update's spend into TokenTracker + BudgetLedger (request_type="notebook")."""
    try:
        from .agent_loop import _price_for
        from . import usage_record
    except ImportError:
        from agent_loop import _price_for
        import usage_record
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    price_in, price_out = _price_for(model) or (0.0, 0.0)
    try:
        usage_record.record_api_usage(
            request_type="notebook",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_price=price_in,
            output_price=price_out,
            tracker=tracker,
        )
    except Exception as exc:
        logger.warning("[notebook] spend not recorded: %s", exc)
    return usage_record.cost_from_tokens(input_tokens, output_tokens, price_in, price_out)
