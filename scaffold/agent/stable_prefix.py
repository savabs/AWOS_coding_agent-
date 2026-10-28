"""
stable_prefix.py — T7b: byte-stable prompt prefixes (AWOS_STABLE_PREFIX).

Providers cache the longest common prefix of consecutive requests (OpenRouter's
pinned V4 Flash providers bill a cached token at ~0.2x; llama-server skips its
prefill). A prefix only survives if every byte before the first difference is
identical, so the order is: stable first, volatile last.

  system → tool specs → repository section → goal (verbatim) → feedback

With AWOS_STABLE_PREFIX=1:

  one_shot / acceptance generator
      both build the repository section with repository_context() (one
      builder: same ranking text = the verbatim goal, same budget, a repo map
      without "(shown below)" marks), and send it right after a shared system
      line, before the role's instructions and the task. The acceptance call
      runs first on a task, so the one_shot call reads the repository from
      cache. The one_shot repair call continues the conversation (original
      messages + the reply + a new user message) instead of a fresh prompt.
  agent loop
      messages[0] puts goal-independent context (exploration notes, notebook)
      ahead of the task; feedback (one-shot note, resume reason, acceptance
      repair) goes in NEW trailing user messages, never edited into earlier
      ones; old tool results are masked in blocks (condense_blocks) so the
      history is byte-stable between condensation points, and the last read
      of a file later edited plus the last failing test output stay pinned.

Off (default): every prompt is byte-identical to before (tests check it).

Env:
  AWOS_STABLE_PREFIX          1/on | 0/off (default off)
  AWOS_STABLE_PREFIX_BLOCK    condensation block, in assistant turns (default 4)
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Optional

ENV = "AWOS_STABLE_PREFIX"
BLOCK_ENV = "AWOS_STABLE_PREFIX_BLOCK"
DEFAULT_BLOCK = 4

#: The one system message one_shot and the acceptance generator share, so the
#: repository section right after it is a common cached prefix.
SHARED_SYSTEM = (
    "You are an expert software engineer. The repository comes first; the "
    "instructions for this request and the task follow it. Follow those "
    "instructions exactly."
)


def enabled() -> bool:
    return os.getenv(ENV, "0").strip().lower() in ("1", "on", "true", "yes")


def block_turns() -> int:
    try:
        return max(1, int(os.getenv(BLOCK_ENV, str(DEFAULT_BLOCK))))
    except ValueError:
        return DEFAULT_BLOCK


# ── one shared repository section ────────────────────────────────────────────

def repository_context(project_root: str, goal: str, exploration: Optional[dict] = None,
                       budget: Optional[int] = None, extra_context: Optional[str] = None):
    """
    The repository section one_shot and the acceptance generator both send:
    ranked on the verbatim goal, one budget (one_shot's), a map that does not
    depend on the file choice. Deterministic: same workspace + same goal +
    same exploration → the same bytes.
    """
    try:
        from . import one_shot
    except ImportError:
        import one_shot
    return one_shot.build_context(project_root, goal, exploration,
                                  budget or one_shot.budget_tokens(),
                                  extra_context=extra_context, stable_map=True)


def repository_block(context_text: str) -> str:
    return f"# Repository\n\n{context_text}\n\n"


def one_shot_messages(task: str, context: Any, instructions: str) -> list:
    """system(shared) → user: repository → instructions → extra → task → ask."""
    extra = (getattr(context, "extra", "") or "").strip()
    user = (
        repository_block(context.text)
        + f"# Instructions\n\n{instructions}\n\n"
        + (f"{extra}\n\n" if extra else "")
        + f"# Task\n\n{task.strip()}\n\n"
        "Reply with the SEARCH/REPLACE blocks that complete the task."
    )
    return [{"role": "system", "content": SHARED_SYSTEM}, {"role": "user", "content": user}]


def acceptance_messages(goal_text: str, context_text: str, instructions: str) -> list:
    """system(shared) → user: repository → instructions → issue → ask."""
    user = (
        repository_block(context_text)
        + f"# Instructions\n\n{instructions}\n\n"
        + f"# Issue\n\n{goal_text.strip()}\n\n"
        "The repository above is the current, unfixed code. Write the pytest "
        "acceptance tests for this issue. They must fail on the current code and "
        "pass once the issue is fixed."
    )
    return [{"role": "system", "content": SHARED_SYSTEM}, {"role": "user", "content": user}]


def repair_followup(messages: list, reply_text: str, repair_user: str) -> list:
    """The first call's messages + its reply + the repair request, appended."""
    return [*messages, {"role": "assistant", "content": reply_text or "(no text)"},
            {"role": "user", "content": repair_user}]


# ── agent-loop feedback as new messages ──────────────────────────────────────

def append_feedback(prompt: Any, text: str) -> Any:
    """str prompt (flag off): text appended to it, as before. list prompt
    (flag on): a new trailing message; earlier ones are never edited."""
    if isinstance(prompt, list):
        return [*prompt, text]
    return prompt + "\n\n" + text


# ── block masking ────────────────────────────────────────────────────────────

def pinned_ids(events: Iterable[tuple]) -> set:
    """
    Tool-call ids whose results are never masked: for each file later edited,
    its last read before an edit; and the last failing test run.
    events: (call_id, kind, path, failing) in call order, kind in
    {"read", "edit", "test"}.
    """
    last_read: dict = {}
    pinned_by_path: dict = {}
    last_fail = None
    for call_id, kind, path, failing in events:
        if kind == "read" and path:
            last_read[path] = call_id
        elif kind == "edit" and path and path in last_read:
            pinned_by_path[path] = last_read[path]
        elif kind == "test" and failing:
            last_fail = call_id
    pins = set(pinned_by_path.values())
    if last_fail is not None:
        pins.add(last_fail)
    return pins


def condense_blocks(messages: list, keep_turns: int, block: int,
                    pins: Optional[set] = None) -> int:
    """
    Stub tool results of the oldest assistant turns, in whole blocks: with A
    assistant turns, the first floor((A - keep_turns) / block) * block turns
    are masked. Between block boundaries nothing is rewritten, so every call
    extends the previous one's bytes. Results whose tool-call id is in `pins`
    stay. Only content shrinks (ids and pairing stay). Returns how many
    results were newly stubbed.
    """
    try:
        from .agent_loop import _elided_stub, _should_stub
    except ImportError:
        from agent_loop import _elided_stub, _should_stub
    pins = pins or set()
    assistant_idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    over = len(assistant_idx) - keep_turns
    masked = (over // block) * block if over > 0 else 0
    if masked <= 0:
        return 0
    cutoff = assistant_idx[masked]  # messages from here on stay verbatim
    stubbed = 0
    for i in range(1, cutoff):
        message = messages[i]
        role = message.get("role")
        if role == "tool":
            content = message.get("content")
            if (message.get("tool_call_id") not in pins and isinstance(content, str)
                    and _should_stub(content)):
                messages[i] = {**message, "content": _elided_stub(len(content))}
                stubbed += 1
        elif role == "user" and isinstance(message.get("content"), list):
            new_blocks, changed = [], False
            for b in message["content"]:
                content = b.get("content") if isinstance(b, dict) else None
                if (isinstance(b, dict) and b.get("type") == "tool_result"
                        and b.get("tool_use_id") not in pins
                        and isinstance(content, str) and _should_stub(content)):
                    new_blocks.append({**b, "content": _elided_stub(len(content))})
                    changed = True
                    stubbed += 1
                else:
                    new_blocks.append(b)
            if changed:
                messages[i] = {**message, "content": new_blocks}
    return stubbed


def norm_path(path: Any, root: Optional[str]) -> Optional[str]:
    if not isinstance(path, str) or not path.strip():
        return None
    full = path if os.path.isabs(path) or not root else os.path.join(root, path)
    return os.path.normpath(full)


# ── measurement ──────────────────────────────────────────────────────────────

def serialize(messages: list, system: str = "", tools: Any = None) -> str:
    """A deterministic rendering of a request, for prefix comparisons."""
    import json
    head = json.dumps({"system": system, "tools": tools}, sort_keys=True, default=str)
    return head + "".join(json.dumps(m, sort_keys=True, default=str) for m in messages)


def common_prefix_fraction(a: str, b: str) -> float:
    """Share of `b` (the later request) covered by its common prefix with `a`."""
    if not b:
        return 1.0
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i / len(b)
