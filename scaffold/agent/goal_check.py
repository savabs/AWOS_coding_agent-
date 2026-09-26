"""
goal_check.py — Is the user's goal FULLY done, not just each task?

The orchestrator judges every planner task alone: a task passes when its own
edit verifies. That lets a run "succeed" on a partial change — a settings
migration rewrote config.py and missed the seven other modules that read the
ini file directly. Nothing looked at the goal as a whole.

GoalChecker closes that gap. After the tasks finish it runs a short AgentLoop,
hands it the original goal verbatim plus what changed, and asks it to search
for every place the goal applies instead of trusting the diff's scope. What it
finds missing comes back as follow-up tasks, one per file, that the
orchestrator runs through its normal path.

Reading code was not enough. On the long-task benchmark a checker read an
export filter, declared it "complete", and missed that an invalid date raised
a traceback and that `--to` excluded orders placed late on the last day. So
the checker now TRIES the goal's behaviour: it works on a throwaway copy of
the workspace with a sandboxed run_command, runs the program with the inputs,
error cases and boundaries the goal states, and only then decides. The copy
is what makes execution safe here: a checker that could write the real tree
could finish the goal itself, unverified.

Its verdict arrives through a submit_verdict tool call, not free text: two of
three checks in that run ended with no parseable reply (out of turns, or an
empty final message) and so said nothing. Text JSON is still accepted as a
fallback, and a loop that ends without either gets one "call submit_verdict
now" turn before it is given up on.

A check that still ends without a verdict (out of turns, a repeated tool
call, a final reply with no verdict even after the nudge) is run once more
from scratch, on a fresh copy with a fresh loop: on the long-task benchmark
the one no-verdict check let a migration through at 18 of 22 hidden tests.

The checker runs on its own model, not the worker's (goal_check_model): the
cheapest worker model said "All requirements satisfied" at 11 of 14.

The checker is advisory plumbing, not a gate that can wedge a run: no model,
a crash, or no verdict at all count as "complete", and say so. Such a verdict
is marked unverified. The orchestrator never lets one excuse failed follow-up
work: once the checker has found gaps, only a real "complete" answer can
declare them closed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: Follow-up tasks per round. More than this is a re-plan, not a gap.
MAX_MISSING = 5
#: Trying the behaviour (write a probe, run it, read the traceback) costs
#: turns that reading alone did not. 40 turns on Haiku cost $0.58 a check
#: (~500k input tokens: 8 kept turns of 8k-char tool output, resent every turn)
#: and still ended on max_turns; with the tighter context below, 24 is enough.
DEFAULT_MAX_TURNS = 24
#: History the checker keeps verbatim, and how much of each tool result it
#: sees. It judges; it does not need a long transcript or whole files resent.
CHECK_KEEP_TURNS = 3
CHECK_TOOL_RESULT_CHARS = 3000
#: Default cap on one check (AWOS_GOAL_CHECK_MAX_COST overrides; the goal's
#: remaining budget still applies on top).
DEFAULT_CHECK_MAX_COST = 0.20
#: When a check is this close to its cost cap or turn limit, it is told to
#: decide and submit its verdict instead of being cut off without one.
DECIDE_AT_FRACTION = 0.75
DECIDE_TURNS_LEFT = 2
DECIDE_NOW = (
    "\n\nBUDGET: this review is nearly out of budget. Do not investigate "
    "further. Decide now on the evidence you have and call submit_verdict in "
    "this reply; list anything you could not verify as missing work."
)
#: The checker needs the shape of the change, not all of it.
MAX_DIFF_CHARS = 12000
VERDICT_TOOL = "submit_verdict"
#: The checker's model when OpenRouter is configured and AWOS_GOAL_CHECK_MODEL
#: is not set: stronger than the cheap worker models it judges.
DEFAULT_OPENROUTER_CHECK_MODEL = "anthropic/claude-haiku-4.5"
#: Checks per goal-check call: the first, and one fresh retry on no verdict.
CHECK_ATTEMPTS = 2
NUDGE_MESSAGE = f"Call {VERDICT_TOOL} now."
#: Stop reasons after which one more turn can still produce a verdict. After a
#: cost cap, a budget block or a provider error, another call is the problem.
NUDGEABLE_STOPS = ("max_turns", "completed", "repeated_tool_call")
#: Not copied into the checker's workspace: large, rebuilt on demand, runtime
#: state (.awos holds thousands of ledgers), or secrets (.env) that neither the
#: sandbox nor the confined host tools will read.
COPY_IGNORE = (".git", ".venv", "venv", "node_modules", "__pycache__", ".env", ".env.*",
               ".awos", "build", "dist", ".mypy_cache", ".pytest_cache", ".ruff_cache",
               ".tox")
#: Past this many MB the copy is abandoned and the review runs without
#: executing anything (AWOS_GOAL_CHECK_MAX_COPY_MB): a large repo copied up to
#: three times per goal could fill $TMPDIR or stall the run.
DEFAULT_MAX_COPY_MB = 500

SYSTEM_PROMPT = f"""You review whether a coding goal has been FULLY carried out
in a real project. You work on a disposable COPY of the project: you can read
and search the code, run the tests, and run shell commands in a sandbox
(run_command). Nothing you do can change the real project, and fixing things
is not your job: you judge.

Reading the code is not enough; code that looks right is often wrong at the
edges. TRY the goal's behaviour before you decide:
- Run the program or CLI the goal names with the inputs it mentions: each new
  flag or option alone and combined, and the plain path with none of them.
- Exercise every error case and boundary the goal states: invalid or unknown
  values, both ends of a range (is the end inclusive, as the goal says?),
  writing to a file as well as to stdout.
- Write small probe scripts or data files (in the project copy or $TMPDIR)
  when the program needs input you do not have.
- A traceback where the goal asked for a clean error message means the goal
  is NOT done. So does any stated behaviour you could not see working.

Be sceptical of the change's own scope: a partial change is the usual failure.
Search for every place the goal applies before you decide.

Work economically — every turn resends the conversation, and older tool output
is cut short. Aim for about 12 tool calls: search with grep and read targeted
line ranges rather than whole files, and put several behaviour checks into one
probe script instead of one command per check.

When you have decided, call {VERDICT_TOOL} exactly once. That call ends the
review — call it as soon as the evidence is clear.
"""

INSTRUCTIONS = (
    "Decide whether the goal is FULLY done. Search the codebase for every place "
    "the goal applies (e.g. every read of a setting, every caller, every "
    "command) — do not trust the diff's scope — and run the behaviour the goal "
    "describes, including the error cases and boundaries it states. Then call "
    f"{VERDICT_TOOL}. Each missing item must be concrete and verifiable: the "
    "observable behaviour that is wrong, what you saw, and where to fix it, e.g. "
    '{"action": "--to must include orders placed at 23:59:59 on that day; they '
    'are currently excluded by the date comparison in filter_orders", "file": '
    '"ordertool/export.py"}. At most '
    f"{MAX_MISSING} items, one per file, paths relative to the project root. "
    f"Only if you cannot call {VERDICT_TOOL}, reply with ONLY the same JSON "
    'object {"complete": bool, "missing": [{"action": str, "file": str}], '
    '"reasoning": str}.'
)

NO_SANDBOX_NOTE = (
    "(run_command is not available: this machine has no sandbox, so judge from "
    "the code and run_tests.)"
)
NO_EXEC_NOTE = (
    "(Nothing can be run in this review: no copy of the project could be made, "
    "so judge from reading the code alone.)"
)
INCOMPLETE_NEEDS_ITEMS = (
    "complete=false needs at least one missing item with a non-empty action; "
    "name what is wrong and where, or set complete=true."
)


@dataclass
class GoalVerdict:
    complete: bool
    missing: list[dict] = field(default_factory=list)
    reasoning: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    #: True only for a verdict the checker actually gave (the tool call, or
    #: JSON in its reply). The fail-open "complete" fallbacks are unverified.
    verified: bool = False
    #: How the verdict arrived: "tool", "text", "nudge_tool", "nudge_text", or
    #: "" for a fallback. The first two are the healthy paths.
    source: str = ""


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _float_env(name: str, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(os.getenv(name, "").strip() or "x")
    except ValueError:
        return default


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    try:
        from .agent_loop import estimate_cost
    except ImportError:
        from agent_loop import estimate_cost
    return estimate_cost(model, input_tokens, output_tokens)


def _tool_classes() -> tuple:
    try:
        from .tools.base import Tool, ToolRegistry, ToolResult
        from .tools.filesystem import ReadFileTool, ListDirTool, FindFilesTool, GrepTool
        from .tools.code_edit import RunTestsTool
        from .tools.run_command import RunCommandTool
    except ImportError:
        from tools.base import Tool, ToolRegistry, ToolResult
        from tools.filesystem import ReadFileTool, ListDirTool, FindFilesTool, GrepTool
        from tools.code_edit import RunTestsTool
        from tools.run_command import RunCommandTool
    return (Tool, ToolRegistry, ToolResult, ReadFileTool, ListDirTool, FindFilesTool,
            GrepTool, RunTestsTool, RunCommandTool)


def build_readonly_registry(project_root: str = ".", sandbox: Any = None,
                            run_tests: bool = True) -> Any:
    """The coding registry minus edit_file: a checker that can fix is a worker.

    run_command is offered only with a sandbox, and GoalChecker only passes
    one built over a throwaway COPY of the workspace. The sandbox confines
    writes to its workspace; were that the real tree, `sed -i` or a script
    would let the checker finish the goal itself, unverified. run_tests runs
    in the same sandbox, over the same copy. run_tests=False when there is no
    copy: pytest imports the model's conftest.py, and on the real tree with
    no sandbox that is the model's code running with the owner's rights.
    """
    (_, ToolRegistry, _, ReadFileTool, ListDirTool, FindFilesTool, GrepTool,
     RunTestsTool, RunCommandTool) = _tool_classes()

    registry = ToolRegistry()
    registry.project_root = project_root
    # The read tools run on the host, outside the sandbox: confined to the
    # project so a symlink or an absolute path cannot read host secrets.
    tools = [
        ReadFileTool(project_root=project_root, confine=True),
        ListDirTool(project_root=project_root, confine=True),
        FindFilesTool(project_root=project_root, confine=True),
        GrepTool(project_root=project_root, confine=True),
    ]
    if run_tests:
        tools.append(RunTestsTool(project_root=project_root, sandbox=sandbox))
    for tool in tools:
        registry.register(tool)
    if sandbox is not None:
        registry.register(RunCommandTool(sandbox))
    return registry


def goal_check_model(worker_model: Optional[str] = None) -> Optional[str]:
    """
    The checker's model: AWOS_GOAL_CHECK_MODEL, else Claude Haiku when the
    worker runs through OpenRouter on a cheaper model than Haiku, else the
    model the worker used. A local endpoint or a pinned provider is not
    OpenRouter even with the key set (Haiku's id would go to a server that
    does not serve it), a replayed or free worker keeps a $0 run at $0, and a
    worker priced at Haiku or above is not downgraded.
    """
    try:
        from .agent_loop import _price_for
        from .providers import openrouter_key
    except ImportError:
        from agent_loop import _price_for
        from providers import openrouter_key

    explicit = os.getenv("AWOS_GOAL_CHECK_MODEL", "").strip()
    if explicit:
        return explicit
    if not (openrouter_key() and _routes_via_openrouter()):
        return worker_model
    if worker_model is None:
        return DEFAULT_OPENROUTER_CHECK_MODEL
    worker_price = _price_for(worker_model)
    if worker_price == (0.0, 0.0):
        return worker_model
    haiku_price = _price_for(DEFAULT_OPENROUTER_CHECK_MODEL) or (0.0, 0.0)
    if worker_price is not None and worker_price[1] >= haiku_price[1]:
        return worker_model
    return DEFAULT_OPENROUTER_CHECK_MODEL


def _routes_via_openrouter() -> bool:
    """build_client_from_env's precedence: a local base URL wins, then OpenRouter."""
    provider = os.getenv("AWOS_PROVIDER", "").strip().lower()
    if os.getenv("AWOS_BASE_URL") and provider in ("", "local"):
        return False
    return provider in ("", "openrouter")


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


def build_prompt(goal: str, changed_files: list[str], diff: str,
                 can_run: bool = True, can_test: bool = True) -> str:
    files = "\n".join(f"- {f}" for f in changed_files) or "(none)"
    note = "" if can_run else f"\n\n{NO_SANDBOX_NOTE if can_test else NO_EXEC_NOTE}"
    return (
        f"The user's goal, verbatim:\n{goal}\n\n"
        f"Files changed so far:\n{files}\n\n"
        f"Diff of the changes:\n{_truncate(diff) or NO_DIFF_NOTE}\n\n"
        f"{INSTRUCTIONS}"
        + note
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


def verdict_from_data(data: Any) -> Optional[GoalVerdict]:
    """A verdict from the tool's arguments or a parsed JSON reply, or None."""
    if not isinstance(data, dict) or "complete" not in data:
        return None
    raw_missing = data.get("missing") or []
    if isinstance(raw_missing, str):
        # Some providers hand nested arrays over as a JSON string.
        try:
            raw_missing = json.loads(raw_missing)
        except ValueError:
            raw_missing = []
    missing = []
    for item in raw_missing if isinstance(raw_missing, list) else []:
        if isinstance(item, dict) and str(item.get("action", "")).strip():
            missing.append({"action": str(item["action"]).strip(),
                            "file": str(item.get("file") or "").strip()})
    complete = _as_bool(data["complete"])
    if not complete and not missing:
        # "Incomplete, nothing to do" ended the goal with zero follow-up
        # rounds: a finished goal reported failed, or a gap never attempted.
        # As no verdict, the tool refuses it and the text path nudges.
        return None
    return GoalVerdict(
        # A verdict of "complete" that still lists work is not complete.
        complete=complete and not missing,
        missing=missing[:MAX_MISSING],
        reasoning=str(data.get("reasoning", "")),
        verified=True,
    )


def parse_verdict(text: str) -> Optional[GoalVerdict]:
    """The checker's reply as a verdict, or None when no usable JSON is in it."""
    for candidate in _json_candidates(text or ""):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        verdict = verdict_from_data(data)
        if verdict is not None:
            return verdict
    return None


def _submit_verdict_tool() -> Any:
    Tool, _, ToolResult = _tool_classes()[:3]

    class SubmitVerdictTool(Tool):
        """The checker's answer. Recording it is the whole effect."""

        def __init__(self) -> None:
            self.verdict: Optional[GoalVerdict] = None

        @property
        def name(self) -> str:
            return VERDICT_TOOL

        @property
        def description(self) -> str:
            return (
                "Submit your final verdict on whether the goal is fully done. "
                "Call it once, after you have tried the goal's behaviour; it "
                "ends the review."
            )

        @property
        def parameters(self) -> dict[str, str]:
            return {
                "complete": "true only if every part of the goal works as stated",
                "missing": "remaining work, one concrete verifiable item per file",
                "reasoning": "what you ran and saw that decided the verdict",
            }

        @property
        def input_schema(self) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {
                    "complete": {"type": "boolean",
                                 "description": self.parameters["complete"]},
                    "missing": {
                        "type": "array",
                        "maxItems": MAX_MISSING,
                        "description": self.parameters["missing"],
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "description": (
                                    "the wrong behaviour, what you observed, and "
                                    "where to fix it")},
                                "file": {"type": "string", "description": (
                                    "path relative to the project root")},
                            },
                            "required": ["action", "file"],
                        },
                    },
                    "reasoning": {"type": "string",
                                  "description": self.parameters["reasoning"]},
                },
                "required": ["complete", "missing", "reasoning"],
            }

        def validate(self, args: dict[str, Any]) -> list[str]:
            return [] if "complete" in args else ["Missing required parameter: 'complete'"]

        def execute(self, args: dict[str, Any]) -> Any:
            verdict = verdict_from_data(args)
            if verdict is None and "complete" in args and not _as_bool(args["complete"]):
                return ToolResult.fail(INCOMPLETE_NEEDS_ITEMS)
            if verdict is None:
                return ToolResult.fail("Could not read the verdict; pass complete, missing, reasoning.")
            self.verdict = verdict
            return ToolResult.ok("Verdict recorded. The review is over; reply with nothing more.")

    return SubmitVerdictTool()


class _EndOnVerdict:
    """
    Wraps the model client so the loop ends once submit_verdict is recorded:
    the next turn is answered locally with an empty, tool-free reply, which
    AgentLoop takes as "finished" without another paid call. It also keeps the
    loop's message list, which the one nudge turn continues from.
    """

    def __init__(self, inner: Any, verdict_tool: Any, model: str = "",
                 cap: Optional[float] = None, max_turns: Optional[int] = None) -> None:
        self._inner = inner
        self._tool = verdict_tool
        self.messages: Optional[list] = None
        self._model, self._cap, self._max_turns = model, cap, max_turns
        self._calls = self._tokens_in = self._tokens_out = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _nearly_out(self) -> bool:
        # With the $0.20 cap, checks were stopped mid-investigation with no
        # verdict at all ("stop=cost_cap", UNVERIFIED). Deciding on what has
        # been seen beats no decision.
        if self._max_turns is not None and self._calls >= self._max_turns - DECIDE_TURNS_LEFT:
            return True
        if self._cap:
            spent = _estimate_cost(self._model, self._tokens_in, self._tokens_out)
            return spent >= DECIDE_AT_FRACTION * self._cap
        return False

    def complete(self, system: str, messages: list, registry: Any) -> Any:
        try:
            from .agent_loop import ModelReply
        except ImportError:
            from agent_loop import ModelReply
        self.messages = messages
        if self._tool.verdict is not None:
            return ModelReply(text="")
        if self._nearly_out():
            # In the system prompt, not a message: it must not break the
            # conversation's tool-call pairing in either dialect. The prompt
            # alone was ignored (3 of 4 checks ended UNVERIFIED), so the reply
            # is also forced to be the verdict call where the client allows it.
            reply = _complete_forcing(self._inner, system + DECIDE_NOW, messages,
                                      registry, VERDICT_TOOL)
        else:
            reply = self._inner.complete(system, messages, registry)
        self._calls += 1
        self._tokens_in += getattr(reply, "input_tokens", 0) or 0
        self._tokens_out += getattr(reply, "output_tokens", 0) or 0
        return reply


def _complete_forcing(client: Any, system: str, messages: list, registry: Any,
                      tool: str) -> Any:
    """
    One model call whose reply must call `tool`, when the client supports a
    forced tool choice; otherwise a plain call (replay cassettes, test doubles).
    """
    import inspect

    try:
        accepts = "tool_choice" in inspect.signature(client.complete).parameters
    except (TypeError, ValueError):
        accepts = False
    if accepts:
        return client.complete(system, messages, registry, tool_choice=tool)
    return client.complete(system, messages, registry)


class CopyTooLarge(Exception):
    """The workspace is over the copy cap. Not an OSError: copytree collects
    those per file and carries on copying."""


def _copy_workspace(root: str, max_bytes: Optional[int] = None) -> tuple[str, str]:
    """(temp dir to remove, the copy inside it) of `root`."""
    if max_bytes is None:
        max_bytes = _int_env("AWOS_GOAL_CHECK_MAX_COPY_MB", DEFAULT_MAX_COPY_MB) * 1024 * 1024
    copied = 0
    base_ignore = shutil.ignore_patterns(*COPY_IGNORE)

    def _ignore(directory: str, names: list[str]) -> set[str]:
        skipped = set(base_ignore(directory, names))
        for name in names:
            path = os.path.join(directory, name)
            # An unreadable directory would fail the whole copy.
            if (name not in skipped and os.path.isdir(path) and not os.path.islink(path)
                    and not os.access(path, os.R_OK | os.X_OK)):
                skipped.add(name)
        return skipped

    def _copy_file(src: str, dst: str) -> None:
        # A FIFO, a socket or a chmod-000 file the worker left behind made
        # copytree raise, and the checker then fell back to the real tree.
        # None of them is source the check needs, so they are skipped.
        nonlocal copied
        try:
            st = os.lstat(src)
        except OSError:
            return
        if not stat.S_ISREG(st.st_mode):
            return
        copied += st.st_size
        if copied > max_bytes:
            raise CopyTooLarge(f"workspace is over {max_bytes // (1024 * 1024)} MB")
        try:
            shutil.copy2(src, dst)
        except PermissionError:
            return

    tmp = tempfile.mkdtemp(prefix="awos-goalcheck-")
    try:
        name = os.path.basename(os.path.abspath(root)) or "workspace"
        copy = os.path.join(tmp, name)
        # symlinks=True: a link is copied as a link, never followed out of
        # the project; where it points is the sandbox's business.
        shutil.copytree(root, copy, symlinks=True, ignore=_ignore,
                        copy_function=_copy_file)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tmp, copy


def _default_sandbox(workspace: str) -> Any:
    try:
        from .sandbox import make_sandbox
    except ImportError:
        from sandbox import make_sandbox
    return make_sandbox(workspace)


class GoalChecker:
    """Runs one review of the whole goal, trying it out on a copy of the codebase."""

    def __init__(self, client: Any = None, model: Optional[str] = None,
                 tracker: Any = None, max_turns: Optional[int] = None,
                 sandbox_factory: Optional[Callable[[str], Any]] = None,
                 can_continue: Optional[Callable[[], bool]] = None,
                 remaining_budget: Optional[Callable[[], Optional[float]]] = None,
                 max_cost_usd: Optional[float] = None) -> None:
        self.client = client
        self.model = model
        self.tracker = tracker
        self.max_turns = max_turns or _int_env("AWOS_GOAL_CHECK_MAX_TURNS", DEFAULT_MAX_TURNS)
        #: workspace path -> Sandbox or None. Injectable for tests.
        self.sandbox_factory = sandbox_factory or _default_sandbox
        #: Asked before the retry check; False (the goal's budget is spent,
        #: say) gives up on a verdict instead of paying for another check.
        self.can_continue = can_continue
        #: USD the goal has left (None: no goal budget). Each check is capped
        #: at it, since a check's spend reaches the ledger only once it ends.
        self.remaining_budget = remaining_budget
        #: A cap on one check alone: AWOS_GOAL_CHECK_MAX_COST, unset = none.
        self.max_cost_usd = (max_cost_usd if max_cost_usd is not None
                             else _float_env("AWOS_GOAL_CHECK_MAX_COST", DEFAULT_CHECK_MAX_COST))

    def _cost_cap(self) -> Optional[float]:
        """The tighter of the per-check cap and what the goal has left."""
        caps = [self.max_cost_usd]
        if self.remaining_budget is not None:
            caps.append(self.remaining_budget())
        caps = [c for c in caps if c is not None]
        return max(0.0, min(caps)) if caps else None

    def check(self, goal: str, codebase_root: str, changed_files: list[str],
              diff: str) -> GoalVerdict:
        client, model = self.client, self.model or ""
        if client is None:
            try:
                client, model = client_for_model(self.model)
            except Exception as exc:
                logger.warning("[GOAL CHECK] no model client (%s); skipped", exc)
                return GoalVerdict(True, reasoning=f"goal check skipped: no model client ({exc})")

        verdict, stop, tokens_in, tokens_out = None, "", 0, 0
        for attempt in range(1, CHECK_ATTEMPTS + 1):
            cap = self._cost_cap()
            if cap is not None and cap <= 0:
                stop = "cost_cap"
                break
            try:
                verdict, stop, final, n_in, n_out = self._check_once(
                    client, model, goal, codebase_root, changed_files, diff,
                    max_cost_usd=cap)
            except Exception as exc:
                # A crash is not retried: the same crash would come back.
                logger.warning("[GOAL CHECK] checker crashed (%s); treated as complete", exc)
                return GoalVerdict(True, reasoning=f"goal check failed to run: {exc}",
                                   input_tokens=tokens_in, output_tokens=tokens_out)
            tokens_in, tokens_out = tokens_in + n_in, tokens_out + n_out
            if verdict is not None:
                break
            logger.warning("[GOAL CHECK] no verdict (stop=%s): %.300s", stop, final)
            # After a cost cap, a budget block or a provider error another
            # check is the problem, not the fix.
            if (attempt == CHECK_ATTEMPTS or stop not in NUDGEABLE_STOPS
                    or (self.can_continue is not None and not self.can_continue())):
                break
            # A retry costs about what the first check did; one the goal
            # cannot afford would only stop at the cap without a verdict.
            cost = _estimate_cost(model or getattr(client, "model", "") or "", n_in, n_out)
            cap = self._cost_cap()
            if cap is not None and cap < cost:
                logger.warning("[GOAL CHECK] no retry: $%.4f left, the check cost $%.4f",
                               cap, cost)
                break
            logger.warning("[GOAL CHECK] checking again from scratch (check %d of %d)",
                           attempt + 1, CHECK_ATTEMPTS)

        if verdict is None:
            verdict = GoalVerdict(
                True,
                reasoning=(f"goal check reply could not be parsed "
                           f"(stop={stop}, no {VERDICT_TOOL} call even after a nudge, "
                           f"{attempt} check(s)); treated as complete"),
            )
        verdict.input_tokens = tokens_in
        verdict.output_tokens = tokens_out
        return verdict

    def _check_once(self, client: Any, model: str, goal: str, codebase_root: str,
                    changed_files: list[str], diff: str,
                    max_cost_usd: Optional[float] = None) -> tuple:
        """One review on a fresh copy. (verdict or None, stop, final message, in, out)."""
        tmp, sandbox, copied = None, None, True
        try:
            try:
                tmp, workspace = _copy_workspace(codebase_root)
            except Exception as exc:
                # Without a copy there is nowhere safe to run code: review the
                # real tree by reading only, with no run_command or run_tests.
                logger.warning("[GOAL CHECK] could not copy the workspace (%s); "
                               "reviewing read-only", exc)
                workspace, copied = codebase_root, False
            else:
                try:
                    sandbox = self.sandbox_factory(workspace)
                except Exception as exc:
                    logger.warning("[GOAL CHECK] no sandbox (%s); run_command not offered", exc)
            return self._review(client, model, goal, workspace, sandbox,
                                changed_files, diff, can_test=copied,
                                max_cost_usd=max_cost_usd)
        finally:
            if sandbox is not None:
                try:
                    sandbox.close()
                except Exception as exc:
                    logger.warning("[GOAL CHECK] sandbox close failed: %s", exc)
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

    def _review(self, client: Any, model: str, goal: str, workspace: str,
                sandbox: Any, changed_files: list[str], diff: str,
                can_test: bool = True, max_cost_usd: Optional[float] = None) -> tuple:
        """(verdict or None, stop reason, final message, input tokens, output tokens)."""
        try:
            from .agent_loop import AgentLoop
        except ImportError:
            from agent_loop import AgentLoop

        verdict_tool = _submit_verdict_tool()
        registry = build_readonly_registry(workspace, sandbox=sandbox, run_tests=can_test)
        registry.register(verdict_tool)
        gated = _EndOnVerdict(
            client, verdict_tool,
            model=model or getattr(client, "model", "") or "",
            cap=max_cost_usd, max_turns=self.max_turns,
        )
        outcome = AgentLoop(
            registry=registry,
            client=gated,
            max_turns=self.max_turns,
            system_prompt=SYSTEM_PROMPT,
            # None falls back to AWOS_MAX_RUN_COST, as for any loop.
            max_cost_usd=max_cost_usd,
            keep_turns=CHECK_KEEP_TURNS,
            tool_result_chars=CHECK_TOOL_RESULT_CHARS,
        ).run(build_prompt(goal, changed_files, diff, can_run=sandbox is not None,
                           can_test=can_test))
        tokens_in, tokens_out = outcome.input_tokens, outcome.output_tokens

        verdict, source = verdict_tool.verdict, "tool"
        if verdict is None:
            verdict, source = parse_verdict(outcome.final_message), "text"
        # Not after a cost cap: the cap is a promise, even for one short call.
        # The verdict is forced at DECIDE_AT_FRACTION of it instead, before it trips.
        if verdict is None and outcome.stop_reason in NUDGEABLE_STOPS:
            verdict, source, n_in, n_out = self._nudge(
                client, registry, verdict_tool, gated.messages, outcome)
            tokens_in, tokens_out = tokens_in + n_in, tokens_out + n_out

        self._record_usage(model or getattr(client, "model", "") or "", tokens_in, tokens_out)
        if verdict is not None:
            verdict.source = source
            for item in verdict.missing:
                item["file"] = _relative_to(item["file"], workspace)
        return verdict, outcome.stop_reason, outcome.final_message, tokens_in, tokens_out

    def _nudge(self, client: Any, registry: Any, verdict_tool: Any,
               messages: Optional[list], outcome: Any) -> tuple:
        """One more turn asking for the verdict. (verdict, source, in, out)."""
        try:
            from .agent_loop import ModelReply
        except ImportError:
            from agent_loop import ModelReply

        history = list(messages or [])
        if not history:
            return None, "", 0, 0
        if history[-1].get("role") == "assistant":
            # Tool calls the loop stopped before answering; the API rejects
            # them unanswered, and the nudge does not need them.
            history.pop()
        if outcome.stop_reason == "completed" and outcome.final_message:
            history.append(client.format_assistant_turn(ModelReply(text=outcome.final_message)))
        last = history[-1] if history else None
        if last and last.get("role") == "user" and isinstance(last.get("content"), list):
            # Anthropic tool results: add the nudge to that user turn rather
            # than sending two user turns in a row.
            history[-1] = {**last, "content": [*last["content"],
                                               {"type": "text", "text": NUDGE_MESSAGE}]}
        else:
            history.append({"role": "user", "content": NUDGE_MESSAGE})

        try:
            # The nudge exists to get the verdict, so its reply must be one.
            reply = _complete_forcing(client, SYSTEM_PROMPT, history, registry, VERDICT_TOOL)
        except Exception as exc:
            logger.warning("[GOAL CHECK] nudge turn failed: %s", exc)
            return None, "", 0, 0
        for call in reply.tool_calls:
            if call.name == VERDICT_TOOL:
                registry.execute(call.name, call.arguments)
        if verdict_tool.verdict is not None:
            return verdict_tool.verdict, "nudge_tool", reply.input_tokens, reply.output_tokens
        verdict = parse_verdict(reply.text)
        return verdict, "nudge_text" if verdict else "", reply.input_tokens, reply.output_tokens

    def _record_usage(self, model: str, input_tokens: int, output_tokens: int) -> None:
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
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_price=price_in,
            output_price=price_out,
            tracker=self.tracker,
        )


def _relative_to(path: str, workspace: str) -> str:
    """A path the checker gave inside its copy, as the real project knows it."""
    if not path:
        return path
    for base in (os.path.realpath(workspace), os.path.abspath(workspace), "/workspace"):
        prefix = base.rstrip("/") + "/"
        if path.startswith(prefix):
            return path[len(prefix):]
    return path
