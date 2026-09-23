"""
goal_check.py — Is the user's goal FULLY done, not just each task?

The orchestrator judges every planner task alone: a task passes when its own
edit verifies. That lets a run "succeed" on a partial change — a settings
migration rewrote config.py and missed the seven other modules that read the
ini file directly. Nothing looked at the goal as a whole.

GoalChecker closes that gap. After the tasks finish it runs a short AgentLoop
with read-only tools, hands it the original goal verbatim plus what changed,
and asks it to search for every place the goal applies instead of trusting the
diff's scope. What it finds missing comes back as follow-up tasks, one per
file, that the orchestrator runs through its normal path.

The checker is advisory plumbing, not a gate that can wedge a run: no model,
a crash, or a reply that will not parse all count as "complete", and say so.
Such a verdict is marked unverified. The orchestrator never lets one excuse
failed follow-up work: once the checker has found gaps, only a real
"complete" answer can declare them closed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Follow-up tasks per round. More than this is a re-plan, not a gap.
MAX_MISSING = 5
DEFAULT_MAX_TURNS = 25
#: The checker needs the shape of the change, not all of it.
MAX_DIFF_CHARS = 12000

SYSTEM_PROMPT = """You review whether a coding goal has been FULLY carried out
in a real repository. You can read and search the code and run the tests, but
you cannot edit anything.

Be sceptical of the change's own scope: a partial change is the usual failure.
Search for every place the goal applies before you decide.
"""

INSTRUCTIONS = (
    "Decide whether the goal is FULLY done. Search the codebase for every place "
    "the goal applies (e.g. every read of a setting, every caller, every "
    "command) — do not trust the diff's scope. List concrete remaining work as "
    "follow-up tasks, one per file, each an actionable instruction. Reply with "
    'ONLY a JSON object {"complete": bool, "missing": [{"action": str, '
    '"file": str}], "reasoning": str}.'
)


@dataclass
class GoalVerdict:
    complete: bool
    missing: list[dict] = field(default_factory=list)
    reasoning: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    #: True only for a verdict the checker actually gave (parsed from its
    #: reply). The fail-open "complete" fallbacks are unverified.
    verified: bool = False


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def build_readonly_registry(project_root: str = ".") -> Any:
    """The coding registry minus edit_file: a checker that can fix is a worker.

    run_command is left out on purpose. The sandbox confines writes to the
    workspace, which is exactly where the checker must not write: `sed -i` or
    a script would let it finish the goal itself, unverified. run_tests stays;
    it is the one execution the checker needs, and it only reads the tree.
    """
    try:
        from .tools.base import ToolRegistry
        from .tools.filesystem import ReadFileTool, ListDirTool, FindFilesTool, GrepTool
        from .tools.code_edit import RunTestsTool
    except ImportError:
        from tools.base import ToolRegistry
        from tools.filesystem import ReadFileTool, ListDirTool, FindFilesTool, GrepTool
        from tools.code_edit import RunTestsTool

    registry = ToolRegistry()
    for tool in (
        ReadFileTool(project_root=project_root),
        ListDirTool(project_root=project_root),
        FindFilesTool(project_root=project_root),
        GrepTool(project_root=project_root),
        RunTestsTool(project_root=project_root),
    ):
        registry.register(tool)
    return registry


def client_for_model(model: Optional[str]) -> tuple[Any, str]:
    """
    The same construction the agent-loop executor uses: OpenRouter needs
    vendor-prefixed ids, everything else takes the id as is.
    """
    try:
        from .agent_loop import build_client_from_env
        from .providers import openrouter_key, openrouter_model_id
    except ImportError:
        from agent_loop import build_client_from_env
        from providers import openrouter_key, openrouter_model_id

    if model and openrouter_key():
        model = openrouter_model_id(model)
    client = build_client_from_env(model)
    return client, getattr(client, "model", None) or model or ""


def working_tree_changes(root: str) -> tuple[list[str], str]:
    """
    (changed files, diff) of `root`'s working tree against HEAD. The run
    never commits, so this is everything it did. Untracked files are listed
    by name only; their content is one read_file away for the checker.
    """
    try:
        from .sandbox import HOST_GIT_DIFF_OPTS, HOST_GIT_OPTS
    except ImportError:
        from sandbox import HOST_GIT_DIFF_OPTS, HOST_GIT_OPTS

    def _git(*args: str) -> str:
        # The tree was just written by sandboxed code; see HOST_GIT_OPTS.
        try:
            r = subprocess.run(["git", *HOST_GIT_OPTS, "-C", root, *args],
                               capture_output=True, text=True, timeout=30)
        except Exception as exc:
            logger.debug("[GOAL CHECK] git %s failed: %s", args[0], exc)
            return ""
        return r.stdout if r.returncode == 0 else ""

    # -z: NUL-separated and unquoted, so names with spaces or non-ASCII
    # characters come back whole.
    tracked = _git("diff", *HOST_GIT_DIFF_OPTS, "HEAD", "--name-only", "--relative", "-z").split("\0")
    untracked = _git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    tracked, untracked = [f for f in tracked if f], [f for f in untracked if f]
    diff = _git("diff", *HOST_GIT_DIFF_OPTS, "HEAD", "--relative")
    if untracked:
        diff += "\nNew untracked files:\n" + "\n".join(untracked)
    return sorted(set(tracked) | set(untracked)), diff


def _truncate(text: str, limit: int = MAX_DIFF_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [diff truncated, {len(text) - limit} more chars]"


#: Outside version control there is no diff, and a bare "(empty)" read as
#: "nothing was done": a finished fix came back as a follow-up task.
NO_DIFF_NOTE = (
    "(no diff available: this folder is not under version control. Read the "
    "changed files listed above to see their current state before judging.)"
)


def build_prompt(goal: str, changed_files: list[str], diff: str) -> str:
    files = "\n".join(f"- {f}" for f in changed_files) or "(none)"
    return (
        f"The user's goal, verbatim:\n{goal}\n\n"
        f"Files changed so far:\n{files}\n\n"
        f"Diff of the changes:\n{_truncate(diff) or NO_DIFF_NOTE}\n\n"
        f"{INSTRUCTIONS}"
    )


def _json_candidates(text: str):
    """Fenced blocks first, then every balanced {...} span, outermost first."""
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S):
        yield m.group(1).strip()
    for start in (i for i, ch in enumerate(text) if ch == "{"):
        depth, in_str, esc = 0, False, False
        for end in range(start, len(text)):
            ch = text[end]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield text[start:end + 1]
                    break


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def parse_verdict(text: str) -> Optional[GoalVerdict]:
    """The checker's reply as a verdict, or None when no usable JSON is in it."""
    for candidate in _json_candidates(text or ""):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if not isinstance(data, dict) or "complete" not in data:
            continue
        missing = []
        for item in data.get("missing") or []:
            if isinstance(item, dict) and str(item.get("action", "")).strip():
                missing.append({"action": str(item["action"]).strip(),
                                "file": str(item.get("file") or "").strip()})
        complete = _as_bool(data["complete"])
        return GoalVerdict(
            # A verdict of "complete" that still lists work is not complete.
            complete=complete and not missing,
            missing=missing[:MAX_MISSING],
            reasoning=str(data.get("reasoning", "")),
            verified=True,
        )
    return None


class GoalChecker:
    """Runs one read-only review of the whole goal against the codebase."""

    def __init__(self, client: Any = None, model: Optional[str] = None,
                 tracker: Any = None, max_turns: Optional[int] = None) -> None:
        self.client = client
        self.model = model
        self.tracker = tracker
        self.max_turns = max_turns or _int_env("AWOS_GOAL_CHECK_MAX_TURNS", DEFAULT_MAX_TURNS)

    def check(self, goal: str, codebase_root: str, changed_files: list[str],
              diff: str) -> GoalVerdict:
        try:
            from .agent_loop import AgentLoop
        except ImportError:
            from agent_loop import AgentLoop

        client, model = self.client, self.model or ""
        if client is None:
            try:
                client, model = client_for_model(self.model)
            except Exception as exc:
                logger.warning("[GOAL CHECK] no model client (%s); skipped", exc)
                return GoalVerdict(True, reasoning=f"goal check skipped: no model client ({exc})")

        try:
            outcome = AgentLoop(
                registry=build_readonly_registry(codebase_root),
                client=client,
                max_turns=self.max_turns,
                system_prompt=SYSTEM_PROMPT,
            ).run(build_prompt(goal, changed_files, diff))
        except Exception as exc:
            logger.warning("[GOAL CHECK] checker crashed (%s); treated as complete", exc)
            return GoalVerdict(True, reasoning=f"goal check failed to run: {exc}")

        self._record_usage(model or getattr(client, "model", "") or "", outcome)
        verdict = parse_verdict(outcome.final_message)
        if verdict is None:
            logger.warning("[GOAL CHECK] unparseable reply (stop=%s): %.300s",
                           outcome.stop_reason, outcome.final_message)
            verdict = GoalVerdict(
                True,
                reasoning=(f"goal check reply could not be parsed "
                           f"(stop={outcome.stop_reason}); treated as complete"),
            )
        verdict.input_tokens = outcome.input_tokens
        verdict.output_tokens = outcome.output_tokens
        return verdict

    def _record_usage(self, model: str, outcome: Any) -> None:
        # Same book-keeping as the agent-loop executor, or the budget hard-stop
        # never sees what the check costs.
        try:
            from .agent_loop import _price_for
            from .usage_record import record_api_usage
        except ImportError:
            from agent_loop import _price_for
            from usage_record import record_api_usage
        price_in, price_out = _price_for(model) or (0.0, 0.0)
        record_api_usage(
            request_type="goal_check",
            model=model,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            input_price=price_in,
            output_price=price_out,
            tracker=self.tracker,
        )
