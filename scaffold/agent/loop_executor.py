"""
loop_executor.py — the "loop" executor (AWOS_EXECUTOR=loop): a minimal open
loop in which the model runs shell commands until it says it is done.

Why: on Claude Haiku 5.5 the pre-registered ladder runs
(docs/memory/runs/ladder_h1*_haiku_2026-10-15.md) showed mini-swe-agent's
bash-only loop solving more issues than AWOS's pipeline (planner → acceptance
tests → one-shot edit → gates → capped repair) at about half the cost: the
pipeline stopped too early. This executor is that loop inside AWOS, so the
product layer (host queue, handoff branches, reports, `awos fix`, licence,
usage ping, sandbox, cost tracking) stays as it is.

The loop:
  * one tool, `bash` (a command string), run in the task workspace through
    AWOS's sandbox (sandbox.make_sandbox: Seatbelt / docker), never on the bare
    host; per-command timeout (AWOS_LOOP_CMD_TIMEOUT_S, default 120 s); output
    cut mini-style (head + tail with a notice) past OUTPUT_LIMIT characters;
  * the model works until it replies, without a tool call, starting with
    SUBMIT_MARKER (or runs `echo SUBMIT_MARKER`, mini-swe-agent's convention);
  * limits: AWOS_LOOP_MAX_STEPS model calls (default 60), the task cost cap,
    the goal budget and a wall clock;
  * after a submit the caller's success gate runs on the final tree (the
    orchestrator's judge + full-suite gate, new failures vs the baseline only).
    Red with new failures: the failing output goes back into the SAME
    conversation and the loop continues, up to AWOS_LOOP_GATE_RETRIES
    (default 2) times. The gate only adds work; it never stops the model early.

No planner, no acceptance tests, no one-shot, no arbiter, no notebook.

Local model (AWOS_PROVIDER=local): by default the replies are plain text
under a GBNF grammar instead of tool calls (AWOS_LOOP_GRAMMAR,
loop_grammar.py): a short preamble, then exactly one ```bash block or the
submit line. The Anthropic path is unchanged. The history is append-only and
the prompt bytes are fixed, so each request extends the previous one
(prefix-cache reuse); per-call cache and speed telemetry goes to
llm_call_log and the components.json `loop` entry (LoopRun.telemetry).

Reliability and cost layers (rea_synthesis A6, A7, C9, C10; pre-registered
in docs/specs/loop_reliability_ab.md), each its own module:
  * stuck detector (loop_stuck.py, AWOS_LOOP_STUCK, default "log": counts
    triggers, prompt unchanged; "nudge" appends a short note to the tool
    results). It never stops the run;
  * checkpoint (loop_checkpoint.py): when the caller passes one, every
    finished turn is saved and a killed run resumes the same conversation;
  * compaction (loop_compaction.py, AWOS_LOOP_COMPACT, default off): the
    only thing that ever rewrites the history (goal verbatim + summary +
    recent turns); with it off the history stays append-only;
  * cost routing (loop_route.py): when the caller passes `escalate`, a
    rung that is stuck, out of its step allowance, out of format or
    truncation retries, erroring, or red after the gate's rounds hands the
    task one way up the ladder. The next rung starts a fresh conversation
    (goal + handover note; the prompt cache does not carry across models
    anyway) on the same working tree.

Prompt text (SYSTEM_PROMPT, _INSTANCE_TEMPLATE, FORMAT_ERROR_MESSAGE,
TRUNCATED_MESSAGE) and the observation truncation rule are adapted from
mini-swe-agent 2.4.6 (minisweagent/config/mini.yaml), under its MIT licence:

    MIT License

    Copyright (c) 2025 Kilian A. Lieret and Carlos E. Jimenez

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""

from __future__ import annotations

import os
import platform
import shlex
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

try:
    from .agent_loop import (TRUNCATED_FINISH, WorkspaceWatch, _finish_reason, _int0,
                             estimate_cost)
    from .llm_call_log import record_error, record_response, timing_fields
    from .loop_compaction import Compactor, compaction_enabled, summary_request
    from .loop_grammar import wrap_for_loop
    from .loop_route import handover_note
    from .loop_stuck import StuckDetector, stuck_mode
    from .tools.base import Tool, ToolRegistry, ToolResult
except ImportError:  # flat sys.path import (scaffold/agent on the path)
    from agent_loop import (TRUNCATED_FINISH, WorkspaceWatch, _finish_reason, _int0,
                            estimate_cost)
    from llm_call_log import record_error, record_response, timing_fields
    from loop_compaction import Compactor, compaction_enabled, summary_request
    from loop_grammar import wrap_for_loop
    from loop_route import handover_note
    from loop_stuck import StuckDetector, stuck_mode
    from tools.base import Tool, ToolRegistry, ToolResult

#: The line that ends the loop (mini-swe-agent's own marker, kept so the two
#: arms of the A/B differ in harness, not in vocabulary).
SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
#: Component name in llm_call_log, the budget ledger and components.json.
COMPONENT = "loop"

DEFAULT_MAX_STEPS = 60
DEFAULT_GATE_RETRIES = 2
DEFAULT_CMD_TIMEOUT_S = 120
MAX_CMD_TIMEOUT_S = 600
#: Observation size past which the output is cut to head + tail (mini: 10000,
#: 5000 + 5000).
OUTPUT_LIMIT = 10_000
#: Replies in a row with neither a tool call nor the submit marker before the
#: loop gives up (mini: max_consecutive_format_errors = 3).
MAX_FORMAT_ERRORS = 3
#: Replies in a row cut off by the output-token limit before giving up.
MAX_TRUNCATIONS = 3


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, "").strip() or default))
    except ValueError:
        return default


def max_steps() -> int:
    """AWOS_LOOP_MAX_STEPS (default 60): model calls per task."""
    return _int_env("AWOS_LOOP_MAX_STEPS", DEFAULT_MAX_STEPS, minimum=1)


def gate_retries() -> int:
    """AWOS_LOOP_GATE_RETRIES (default 2): red-gate feedback rounds after a submit."""
    return _int_env("AWOS_LOOP_GATE_RETRIES", DEFAULT_GATE_RETRIES)


def command_timeout() -> int:
    """AWOS_LOOP_CMD_TIMEOUT_S (default 120, max 600): one bash command's limit."""
    return min(MAX_CMD_TIMEOUT_S, _int_env("AWOS_LOOP_CMD_TIMEOUT_S", DEFAULT_CMD_TIMEOUT_S,
                                           minimum=1))


# ── the bash tool ────────────────────────────────────────────────────────────


def truncate_output(text: str, limit: int = OUTPUT_LIMIT) -> str:
    """mini-style: whole output under `limit`, else its head and tail with a notice."""
    if len(text) <= limit:
        return text
    half = limit // 2
    elided = len(text) - 2 * half
    return (f"{text[:half]}\n\n<warning>Output too long: {elided} characters elided from "
            "the middle. Narrow the command (grep, head, tail, sed -n) to see the "
            f"rest.</warning>\n\n{text[-half:]}")


def render_observation(exit_code: int, output: str, timed_out: bool = False,
                       timeout_s: int = 0) -> str:
    head = f"<returncode>{exit_code}</returncode>"
    if timed_out:
        head += (f"\n<warning>The command timed out after {timeout_s}s and was killed. "
                 "Run something shorter, or put a long job in the background with a "
                 "timeout of its own.</warning>")
    return f"{head}\n<output>\n{truncate_output(output)}</output>"


def bash_command(command: str) -> str:
    """`command` for the sandbox's /bin/sh, run by bash when the sandbox has one
    (the tool is called bash; a docker image's sh may be dash)."""
    q = shlex.quote(command)
    return f"command -v bash >/dev/null 2>&1 && exec bash -c {q} || exec /bin/sh -c {q}"


class BashTool(Tool):
    """Run one shell command in the task workspace, through the sandbox."""

    def __init__(self, sandbox: Any, timeout_s: Optional[int] = None) -> None:
        self.sandbox = sandbox
        self.timeout_s = timeout_s or command_timeout()

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return ("Execute a bash command in the repository (cwd = the repository root). "
                "Each call runs in a new subshell: `cd` and exported variables do not "
                f"persist. Commands are killed after {self.timeout_s}s.")

    @property
    def parameters(self) -> dict[str, str]:
        return {"command": "The bash command to execute"}

    def validate(self, args: dict[str, Any]) -> list[str]:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return ["Missing required parameter: 'command' (a non-empty string)"]
        return []

    def close(self) -> None:
        close = getattr(self.sandbox, "close", None)
        if callable(close):
            close()

    def execute(self, args: dict[str, Any]) -> ToolResult:
        res = self.sandbox.run(bash_command(args["command"]), timeout_sec=self.timeout_s)
        output = (res.stdout or "")
        if res.stderr:
            output = f"{output}{'' if not output or output.endswith(chr(10)) else chr(10)}{res.stderr}"
        text = render_observation(res.exit_code, output, res.timed_out, self.timeout_s)
        ok = res.exit_code == 0 and not res.timed_out
        return ToolResult(success=ok, text=text, error="" if ok else text,
                          data={"exit_code": res.exit_code, "timed_out": res.timed_out})


def build_loop_registry(sandbox: Any, timeout_s: Optional[int] = None) -> ToolRegistry:
    """The loop's whole tool set: `bash`, bound to `sandbox` (never None)."""
    if sandbox is None:
        raise ValueError("the loop executor runs commands only through a sandbox")
    registry = ToolRegistry()
    registry.sandbox = sandbox
    registry.project_root = str(getattr(sandbox, "workspace", "") or "")
    registry.register(BashTool(sandbox, timeout_s))
    return registry


# ── prompts (adapted from mini-swe-agent 2.4.6 config/mini.yaml, MIT) ───────


SYSTEM_PROMPT = """You are a helpful assistant that can interact with a computer: \
you solve software tasks in a real repository by running bash commands."""

_INSTANCE_TEMPLATE = """Please solve this task in the repository at the current \
working directory.

<task>
{goal}
</task>

You can execute bash commands and edit files to implement the necessary changes.

## Recommended Workflow

This workflow should be done step-by-step so that you can iterate on your changes and any possible problems.

1. Analyze the codebase by finding and reading relevant files
2. Create a script to reproduce the issue
3. Edit the source code to resolve the issue
4. Verify your fix works by running your script again
5. Test edge cases to ensure your fix is robust
6. Run the project's existing tests that cover the code you changed
7. Remove any scratch scripts you created, unless the task asks for them
8. When the task is fully done and verified, reply WITHOUT a tool call, with \
`{marker}` as the first line, followed by a short summary of what you changed.
   <important>Only submit when you are done: submitting ends your work on this task.</important>

## Command Execution Rules

You are operating in an environment where

1. You issue at least one command
2. The system executes the command(s) in a subshell
3. You see the result(s)
4. You write your next command(s)

Each response should include:

1. **Reasoning text** where you explain your analysis and plan
2. At least one call to the `bash` tool with your command (until you submit)

**CRITICAL REQUIREMENTS:**

- Your response SHOULD include reasoning text explaining what you're doing
- Until you submit, your response MUST include AT LEAST ONE bash tool call
- Directory or environment variable changes are not persistent. Every action is executed in a new subshell.
- However, you can prefix any action with `MY_ENV_VAR=MY_VALUE cd /path/to/working/dir && ...` or write/load environment variables from files
- Commands run in a sandbox: writes are limited to the repository, and there is no network access except to localhost.
- To finish, reply with no tool call and `{marker}` as the first line of your reply.

<system_information>
{system} {release} {machine}
</system_information>

## Useful command examples

### Create a new file:

```bash
cat <<'EOF' > newfile.py
import numpy as np
hello = "world"
print(hello)
EOF
```

### Edit files with sed:
{sed_note}
```bash
# Replace all occurrences
sed -i{sed_arg} 's/old_string/new_string/g' filename.py

# Replace only first occurrence
sed -i{sed_arg} 's/old_string/new_string/' filename.py

# Replace first occurrence on line 1
sed -i{sed_arg} '1s/old_string/new_string/' filename.py

# Replace all occurrences in lines 1-10
sed -i{sed_arg} '1,10s/old_string/new_string/g' filename.py
```

### View file content:

```bash
# View specific lines with numbers
nl -ba filename.py | sed -n '10,20p'
```

### Any other command you want to run

```bash
anything
```
"""

FORMAT_ERROR_MESSAGE = (
    "Your reply had no bash tool call and did not submit.\n\n"
    "Every response needs to call the `bash` tool at least once, with your command as "
    'its argument: {{"command": "your_command_here"}}.\n\n'
    "If the task is fully done and verified, reply with no tool call and "
    "`{marker}` as the first line of your reply."
)

TRUNCATED_MESSAGE = (
    "Your previous response reached the output token limit before you produced a tool "
    "call, so it was cut off. Respond more concisely and finish with exactly one bash "
    "tool call. If you need to think more, do so briefly."
)

# Text protocol (AWOS_LOOP_GRAMMAR, loop_grammar.py: the local model under a
# GBNF grammar, no `tools`). Same workflow and examples as _INSTANCE_TEMPLATE;
# the reply format is the fenced envelope the grammar enforces. The task comes
# LAST: everything before it is the same bytes for every task, so the local
# server's prompt cache reuses the whole instruction block across tasks.
_INSTANCE_TEMPLATE_TEXT = """You will solve a task in the repository at the current \
working directory. The task is at the end of this message.

You can execute bash commands and edit files to implement the necessary changes.

## Recommended Workflow

This workflow should be done step-by-step so that you can iterate on your changes and any possible problems.

1. Analyze the codebase by finding and reading relevant files
2. Create a script to reproduce the issue
3. Edit the source code to resolve the issue
4. Verify your fix works by running your script again
5. Test edge cases to ensure your fix is robust
6. Run the project's existing tests that cover the code you changed
7. Remove any scratch scripts you created, unless the task asks for them
8. When the task is fully done and verified, reply with `{marker}` on a line of \
its own, followed by a short summary of what you changed.
   <important>Only submit when you are done: submitting ends your work on this task.</important>

## Command Execution Rules

You are operating in an environment where

1. You issue one command
2. The system executes the command in a subshell
3. You see the result
4. You write your next command

Each response has exactly this shape:

1. Optionally, a few short lines of reasoning: your analysis and plan
2. Then EXACTLY ONE bash code block with your command, and nothing after it:

```bash
your_command_here
```

**CRITICAL REQUIREMENTS:**

- Exactly one ```bash block per response (until you submit). Join several commands with `&&` or write a script.
- No line inside the block may start with three backticks.
- Directory or environment variable changes are not persistent. Every action is executed in a new subshell.
- However, you can prefix any action with `MY_ENV_VAR=MY_VALUE cd /path/to/working/dir && ...` or write/load environment variables from files
- Commands run in a sandbox: writes are limited to the repository, and there is no network access except to localhost.
- To finish, reply with no bash block: `{marker}` on its own line, then a short summary.

<system_information>
{system} {release} {machine}
</system_information>

## Useful command examples

### Create a new file:

```bash
cat <<'EOF' > newfile.py
import numpy as np
hello = "world"
print(hello)
EOF
```

### Edit files with sed:
{sed_note}
```bash
# Replace all occurrences
sed -i{sed_arg} 's/old_string/new_string/g' filename.py

# Replace only first occurrence
sed -i{sed_arg} 's/old_string/new_string/' filename.py

# Replace first occurrence on line 1
sed -i{sed_arg} '1s/old_string/new_string/' filename.py

# Replace all occurrences in lines 1-10
sed -i{sed_arg} '1,10s/old_string/new_string/g' filename.py
```

### View file content:

```bash
# View specific lines with numbers
nl -ba filename.py | sed -n '10,20p'
```

## The task

<task>
{goal}
</task>
"""

FORMAT_ERROR_MESSAGE_TEXT = (
    "Your reply had no bash block and did not submit.\n\n"
    "Every response needs a few lines of reasoning at most, then exactly one block:\n\n"
    "```bash\nyour_command_here\n```\n\n"
    "If the task is fully done and verified, reply with `{marker}` on a line of its own, "
    "then a short summary."
)

GATE_FEEDBACK_TEMPLATE = (
    "You submitted, but AWOS ran the project's tests on the final tree and these tests "
    "fail after your change (new failures; tests that already failed before your change "
    "are not counted):\n\n{failures}\n\n"
    "Fix them, or explain why they are unrelated to the task, then submit again "
    "(reply with no tool call and `{marker}` as the first line). "
    "Feedback round {round} of {max_rounds}."
)


def instance_prompt(goal: str, uname: Optional[Any] = None, text_protocol: bool = False) -> str:
    """The first user message: the goal verbatim plus mini's workflow and rules.

    text_protocol: the fenced-envelope variant for the grammar-constrained
    local loop (static instructions first, the goal last)."""
    u = uname or platform.uname()
    darwin = u.system == "Darwin"
    template = _INSTANCE_TEMPLATE_TEXT if text_protocol else _INSTANCE_TEMPLATE
    return template.format(
        goal=goal.strip(), marker=SUBMIT_MARKER, system=u.system, release=u.release,
        machine=u.machine,
        sed_note=("\n<important>\nYou are on MacOS. For all the below examples, you need to "
                  "use `sed -i ''` instead of `sed -i`.\n</important>\n" if darwin else ""),
        sed_arg=" ''" if darwin else "",
    )


def gate_feedback(failures: str, round_no: int, max_rounds: int) -> str:
    return GATE_FEEDBACK_TEMPLATE.format(failures=failures.strip() or "(no output)",
                                         marker=SUBMIT_MARKER, round=round_no,
                                         max_rounds=max_rounds)


# ── submit detection ────────────────────────────────────────────────────────


def submitted_text(text: str) -> Optional[str]:
    """The submission (text after the marker line) when `text` submits, else None.

    A reply submits when its first non-empty line starts with the marker; a
    reply that reasons first and puts the marker on a later line of its own
    counts too (models do both)."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip().strip("`*").strip()
        if stripped.startswith(SUBMIT_MARKER):
            rest = stripped[len(SUBMIT_MARKER):].strip()
            return "\n".join(([rest] if rest else []) + lines[i + 1:]).strip()
    return None


def is_submit_command(call: Any) -> bool:
    """`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` as a bash call (mini's convention)."""
    if getattr(call, "name", "") != "bash":
        return False
    cmd = str((getattr(call, "arguments", None) or {}).get("command", "")).strip()
    for quote in ("", "'", '"'):
        if cmd == f"echo {quote}{SUBMIT_MARKER}{quote}":
            return True
    return False


# ── the loop ────────────────────────────────────────────────────────────────


#: gate(files_changed, submission) -> {"green": bool, "retry": bool,
#:   "feedback": str, ...}. green: the tree passes; retry: red with NEW
#:   failures (worth sending back). Anything else is kept in LoopRun.gate.
GateFn = Callable[[list, str], dict]


@dataclass
class LoopRun:
    """What one loop-executor run did."""

    success: bool = False
    #: submitted | gate_red | max_steps | cost_cap | goal_budget | wall_clock |
    #: format_error | truncated | model_error
    stop_reason: str = "max_steps"
    steps: int = 0
    submits: int = 0
    gate_retries: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    #: Text protocol under a GBNF grammar (loop_grammar.py) instead of tool calls.
    grammar: bool = False
    #: Replies with neither an action nor a submit (each got the nudge), and of
    #: those, text-protocol replies the envelope parser could not read.
    format_errors: int = 0
    parse_failures: int = 0
    #: llama-server `timings`, summed over the calls that reported them.
    timed_calls: int = 0
    prefill_ms: float = 0.0
    prefill_tokens: int = 0
    gen_tokens: int = 0
    gen_ms: float = 0.0
    #: local_runtime.prepare_first_step result (status, tokens restored...), or None.
    prefix_cache: Optional[dict] = None
    final_message: str = ""
    files_changed: list = field(default_factory=list)
    gate: Optional[dict] = None
    transcript: list = field(default_factory=list)
    elapsed_sec: float = 0.0
    #: Continued from a checkpoint of a killed run (loop_checkpoint.py).
    resumed: bool = False
    #: Route rung the run ended on (0 = the first) and each escalation
    #: {"from", "to", "reason", "step"} (loop_route.py).
    rung: int = 0
    escalations: list = field(default_factory=list)
    #: StuckDetector.summary() / Compactor.summary(), when they ran.
    stuck: Optional[dict] = None
    compaction: Optional[dict] = None

    def telemetry(self) -> dict:
        """Prompt-cache and speed summary (rea_synthesis B2): cached_fraction =
        cached / prompt tokens over the run; mean_prefill_ms per timed call;
        tokens_per_s = generated tokens / generation seconds (llama-server)."""
        out = {"input_tokens": self.input_tokens, "cached_tokens": self.cached_tokens,
               "cache_write_tokens": self.cache_write_tokens,
               "cached_fraction": (round(self.cached_tokens / self.input_tokens, 4)
                                   if self.input_tokens > 0 else None),
               "grammar": self.grammar, "format_errors": self.format_errors,
               "parse_failures": self.parse_failures}
        if self.prefix_cache is not None:
            out["prefix_cache"] = self.prefix_cache.get("status")
        if self.resumed:
            out["resumed"] = True
        if self.escalations:
            out["rung"] = self.rung
            out["escalations"] = list(self.escalations)
        if self.stuck is not None:
            out["stuck"] = self.stuck
        if self.compaction is not None:
            out["compaction"] = self.compaction
        if self.timed_calls:
            out["mean_prefill_ms"] = round(self.prefill_ms / self.timed_calls, 1)
            out["mean_prefill_tokens"] = round(self.prefill_tokens / self.timed_calls, 1)
            out["tokens_per_s"] = (round(self.gen_tokens / (self.gen_ms / 1000.0), 2)
                                   if self.gen_ms > 0 else None)
        return out

    def component(self) -> dict:
        """The components.json entry for this run."""
        return {"status": "ran", "steps": self.steps, "submits": self.submits,
                "gate_retries": self.gate_retries, "cost_usd": round(self.cost_usd, 6),
                "stop_reason": self.stop_reason, "success": self.success,
                **self.telemetry()}


#: LoopRun counters a checkpoint carries (small; the transcript is not saved).
_CHECKPOINT_FIELDS = ("steps", "submits", "gate_retries", "tool_calls", "input_tokens",
                      "output_tokens", "cached_tokens", "cache_write_tokens", "cost_usd",
                      "format_errors", "parse_failures", "timed_calls", "prefill_ms",
                      "prefill_tokens", "gen_tokens", "gen_ms", "rung")


def _prepare_local_prefix(system_prompt: str) -> Optional[dict]:
    """Restore the saved KV of the static system prefix on the local
    llama-server before the first call (local_runtime.prepare_first_step,
    rea_synthesis B4). Returns its result dict, or None when the hook is
    unavailable. Never raises."""
    try:
        try:
            from scaffold.agent import local_runtime
        except ImportError:  # pragma: no cover - flat import
            import local_runtime  # type: ignore
        res = local_runtime.prepare_first_step(
            [{"role": "system", "content": system_prompt}])
        return res if isinstance(res, dict) else None
    except Exception:  # noqa: BLE001 - an optimisation only; a miss costs nothing
        return None


class LoopExecutor:
    """Drive `client` through bash calls in `registry` until it submits."""

    def __init__(
        self,
        client: Any,
        registry: Any,
        goal: str,
        *,
        workspace: Optional[str] = None,
        gate: Optional[GateFn] = None,
        max_steps_: Optional[int] = None,
        gate_retries_: Optional[int] = None,
        cost_cap: Optional[float] = None,
        wall_s: Optional[float] = None,
        budget_exhausted: Optional[Callable[[], bool]] = None,
        on_call: Optional[Callable[[Any, float], None]] = None,
        path_filter: Optional[Callable[[list], list]] = None,
        system_prompt: str = SYSTEM_PROMPT,
        clock: Callable[[], float] = time.monotonic,
        checkpoint: Any = None,
        stuck: Optional[StuckDetector] = None,
        escalate: Optional[Callable[[str], Any]] = None,
        rung_steps_: Optional[int] = None,
        compact: Optional[bool] = None,
    ) -> None:
        # AWOS_LOOP_GRAMMAR (default on for AWOS_PROVIDER=local): the local
        # OpenAI-compatible client becomes the grammar-constrained text
        # protocol (loop_grammar.py). Anthropic and every other client pass
        # through unchanged.
        self._set_client(client)
        self.registry = registry
        self.goal = goal
        self.workspace = workspace or getattr(registry, "project_root", "") or ""
        self.gate = gate
        self.max_steps = max_steps_ if max_steps_ is not None else max_steps()
        self.max_gate_retries = gate_retries_ if gate_retries_ is not None else gate_retries()
        self.cost_cap = cost_cap
        self.wall_s = wall_s
        self.budget_exhausted = budget_exhausted
        self.on_call = on_call
        self.path_filter = path_filter
        self.system_prompt = system_prompt
        self.clock = clock
        self.messages: list[dict[str, Any]] = []
        #: loop_checkpoint.LoopCheckpoint (or anything with save/load/finish).
        self.checkpoint = checkpoint
        self._stuck_given = stuck
        self.stuck: Optional[StuckDetector] = None
        #: escalate(reason) -> the next rung's client, or None at the top.
        self.escalate = escalate
        self.rung_steps = rung_steps_
        self.compact = compaction_enabled() if compact is None else bool(compact)
        self.compactor: Optional[Compactor] = None
        self._summary_replies: list = []
        self._summary_lock = threading.Lock()
        self._ladder_done = escalate is None
        self._rung_start = 0
        self._rung_gate_retries = 0
        self._format_errors = 0
        self._truncations = 0
        self._cost_at_start = 0.0
        self._files_before_resume: list = []
        self._stuck_earlier: list = []
        self._state_step = -1
        self._last_used_tokens = 0

    @property
    def model_name(self) -> str:
        return str(getattr(self.client, "model", "") or "")

    # -- helpers --

    def _set_client(self, client: Any) -> None:
        self.client = wrap_for_loop(client)
        self.text_protocol = bool(getattr(self.client, "text_protocol", False))

    def _new_rung_layers(self) -> None:
        """A fresh stuck detector and compactor for the client now in use."""
        if self._stuck_given is not None and self.stuck is None:
            self.stuck = self._stuck_given
        elif stuck_mode() != "off" or self._stuck_given is not None:
            mode = self._stuck_given.mode if self._stuck_given is not None else None
            self.stuck = StuckDetector(mode)
        else:
            self.stuck = None
        self.compactor = (Compactor(self.model_name, summarize=self._summarize)
                          if self.compact else None)

    def _changed(self, watch: Optional[WorkspaceWatch], before: Optional[dict]) -> list:
        if watch is None:
            return list(self._files_before_resume)
        rels = watch.changed(before, watch.snapshot()) or []
        if self._files_before_resume:
            rels = sorted(set(rels) | set(self._files_before_resume))
        if self.path_filter is not None:
            try:
                rels = list(self.path_filter(rels))
            except Exception:  # noqa: BLE001 - a filter failure keeps everything
                pass
        return rels

    def _stop_reason_before_call(self, run: LoopRun, started: float) -> Optional[str]:
        if self.wall_s and self.wall_s > 0 and self.clock() - started >= self.wall_s:
            return "wall_clock"
        if self.budget_exhausted is not None and self.budget_exhausted():
            return "goal_budget"
        if run.steps >= self.max_steps:
            return "max_steps"
        # A resumed run's earlier spend was already taken off the cap by the
        # caller (host SpendTracker), so only this attempt's spend counts here.
        if self.cost_cap is not None and run.cost_usd - self._cost_at_start >= self.cost_cap:
            return "cost_cap"
        return None

    def _account(self, run: LoopRun, reply: Any) -> None:
        cached, written = _int0(reply, "cached_tokens"), _int0(reply, "cache_write_tokens")
        cost = estimate_cost(self.model_name, reply.input_tokens, reply.output_tokens,
                             cached, written)
        record_response(COMPONENT, self.model_name, reply.raw,
                        visible_chars=len(reply.text or ""),
                        input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
                        cost_usd=cost, finish_reason=_finish_reason(reply.raw).strip("?") or None,
                        tool_calls=len(reply.tool_calls), turn=run.steps,
                        cached_tokens=cached or None,
                        **({"loop_grammar": True} if self.text_protocol else {}))
        timing = timing_fields(reply.raw)
        if "prompt_eval_ms" in timing or "gen_ms" in timing:
            run.timed_calls += 1
            run.prefill_ms += float(timing.get("prompt_eval_ms") or 0.0)
            run.prefill_tokens += int(timing.get("prompt_eval_tokens") or 0)
            run.gen_tokens += int(timing.get("gen_tokens") or 0)
            run.gen_ms += float(timing.get("gen_ms") or 0.0)
        run.input_tokens += reply.input_tokens
        run.output_tokens += reply.output_tokens
        run.cached_tokens += cached
        run.cache_write_tokens += written
        run.cost_usd += cost
        if self.on_call is not None:
            try:
                self.on_call(reply, cost)
            except Exception:  # noqa: BLE001 - bookkeeping never ends the loop
                pass

    def _submit(self, run: LoopRun, submission: str, files: list) -> tuple[bool, str]:
        """Gate a submit. (done, feedback): done ends the loop; else feedback goes back."""
        run.submits += 1
        run.final_message = submission
        run.files_changed = files
        g = self.gate(files, submission) if self.gate is not None else {"green": True}
        g = dict(g or {})
        run.gate = g
        if g.get("retry") and self._rung_gate_retries < self.max_gate_retries:
            self._rung_gate_retries += 1
            run.gate_retries += 1
            return False, gate_feedback(str(g.get("feedback", "")), self._rung_gate_retries,
                                        self.max_gate_retries)
        run.success = bool(g.get("green"))
        run.stop_reason = "submitted" if run.success else "gate_red"
        return True, ""

    def _append_note(self, text: str) -> None:
        """Add a harness note to the user turn just appended (tool results), so
        roles still alternate; OpenAI `tool` messages get a user message after."""
        last = self.messages[-1] if self.messages else None
        if isinstance(last, dict) and last.get("role") == "user":
            content = last.get("content")
            if isinstance(content, str):
                last["content"] = f"{content}\n\n{text}"
                return
            if isinstance(content, list):
                content.append({"type": "text", "text": text})
                return
        self.messages.append({"role": "user", "content": text})

    def _turn_state(self, watch: Optional[WorkspaceWatch], before: Optional[dict],
                    step: int) -> tuple[list, Any]:
        """(files changed so far, a progress key) once per step: the key moves
        whenever a changed file's size or mtime moves."""
        if step == self._state_step:
            return self._state
        files, key = [], None
        if watch is not None:
            snap = watch.snapshot()
            if snap is not None:
                files = watch.changed(before, snap) or []
                key = hash(tuple(sorted((f, snap.get(f)) for f in files)))
        if self._files_before_resume:
            files = sorted(set(files) | set(self._files_before_resume))
        self._state_step, self._state = step, (files, key)
        return self._state

    def _summarize(self, msgs: list) -> str:
        """Compaction's summary call: the plain tool client (never the bash
        grammar), a tool-free transcript request. Cost is accounted by the
        loop thread (_drain_summaries)."""
        inner = getattr(self.client, "inner", self.client)
        reply = inner.complete(self.system_prompt, summary_request(msgs), self.registry)
        with self._summary_lock:
            self._summary_replies.append(reply)
        return reply.text or ""

    def _drain_summaries(self, run: LoopRun) -> None:
        with self._summary_lock:
            replies, self._summary_replies = self._summary_replies, []
        for reply in replies:
            self._account(run, reply)

    def _counters(self, run: LoopRun, files: list) -> dict:
        out = {name: getattr(run, name) for name in _CHECKPOINT_FIELDS}
        out.update(rung_start=self._rung_start, rung_gate_retries=self._rung_gate_retries,
                   files_changed=list(files))
        return out

    def _resume(self, run: LoopRun) -> bool:
        """Continue a killed run from its checkpoint. False: start fresh."""
        if self.checkpoint is None:
            return False
        try:
            state = self.checkpoint.load()
        except Exception:  # noqa: BLE001 - a broken checkpoint means a fresh start
            state = None
        if not state:
            return False
        messages, counters = state
        rung = int(counters.get("rung") or 0)
        for _ in range(rung):  # back onto the rung the conversation belongs to
            nxt = None
            if self.escalate is not None:
                try:
                    nxt = self.escalate("resume")
                except Exception:  # noqa: BLE001
                    nxt = None
            if nxt is None:
                return False  # that rung is gone: its history can't be replayed here
            self._set_client(nxt)
        for name in _CHECKPOINT_FIELDS:
            if name in counters:
                setattr(run, name, type(getattr(run, name))(counters[name]))
        self._rung_start = int(counters.get("rung_start") or 0)
        self._rung_gate_retries = int(counters.get("rung_gate_retries") or 0)
        self._files_before_resume = list(counters.get("files_changed") or [])
        self._cost_at_start = run.cost_usd
        self.messages = list(messages)
        run.resumed = True
        return True

    def _escalate(self, run: LoopRun, reason: str, watch: Optional[WorkspaceWatch],
                  before: Optional[dict], note: str = "") -> bool:
        """Hand the task one way up the route ladder. True when a stronger rung
        took over (the loop continues); False at the top or without routing."""
        if self._ladder_done:
            return False
        try:
            nxt = self.escalate(reason) if self.escalate is not None else None
        except Exception:  # noqa: BLE001 - a rung that can't be built = no rung
            nxt = None
        if nxt is None:
            self._ladder_done = True
            return False
        self._drain_summaries(run)
        old = self.model_name
        files = self._changed(watch, before)
        if self.stuck is not None:
            self._stuck_earlier.append({"model": old, **self.stuck.summary()})
        self._set_client(nxt)
        run.rung += 1
        run.escalations.append({"from": old, "to": self.model_name, "reason": reason,
                                "step": run.steps})
        handover = handover_note(run.transcript, files, reason)
        if note:
            handover += f"\n\nThe last check reported:\n{note.strip()[:4000]}"
        # A fresh conversation in the new rung's protocol: the old one may be
        # in another message shape, and the cache is per model anyway.
        self.messages = [{"role": "user", "content": instance_prompt(
            self.goal, text_protocol=self.text_protocol) + "\n\n" + handover}]
        self._new_rung_layers()
        self._rung_start = run.steps
        self._rung_gate_retries = 0
        self._format_errors = self._truncations = 0
        run.stop_reason, run.success = "max_steps", False
        return True

    def _after_turn(self, run: LoopRun, watch: Optional[WorkspaceWatch],
                    before: Optional[dict]) -> None:
        """Between turns (every model reply that did not end the run): route,
        compact, checkpoint."""
        self._drain_summaries(run)
        if not self._ladder_done:
            reason = None
            if self.stuck is not None and self.stuck.escalate_recommended:
                reason = f"stuck: {self.stuck.summary().get('escalate_trigger') or 'signal'}"
            elif self.rung_steps and run.steps - self._rung_start >= self.rung_steps:
                reason = f"rung step allowance ({self.rung_steps}) spent"
            if reason and self._escalate(run, reason, watch, before):
                return
        if self.compactor is not None:
            files, _ = self._turn_state(watch, before, run.steps)
            self.compactor.note_files(files)
            self.compactor.maybe_start(self.messages, self._last_used_tokens)
            swapped = self.compactor.maybe_swap(self.messages, self._last_used_tokens)
            if swapped is not None:
                self.messages = swapped
            self._drain_summaries(run)
        if self.checkpoint is not None:
            files = (self._turn_state(watch, before, run.steps)[0]
                     if watch is not None else list(self._files_before_resume))
            try:
                self.checkpoint.save(self.messages, self._counters(run, files))
            except Exception:  # noqa: BLE001 - a checkpoint never ends the loop
                pass

    # -- the loop --

    def run(self) -> LoopRun:
        run = LoopRun(grammar=self.text_protocol)
        started = self.clock()
        watch = WorkspaceWatch(self.workspace) if self.workspace else None
        before = watch.snapshot() if watch is not None else None
        # History is append-only from here: no message is edited, re-rendered
        # or dropped mid-run, so every request is the previous request plus a
        # suffix (tests/test_prefix_stability.py). The exceptions are opt-in:
        # compaction (AWOS_LOOP_COMPACT) and an escalation to another rung.
        if not self._resume(run):
            run = LoopRun(grammar=self.text_protocol)
            self.messages = [{"role": "user", "content": instance_prompt(
                self.goal, text_protocol=self.text_protocol)}]
        self._new_rung_layers()
        if self.text_protocol and not run.resumed:
            # Local llama-server: restore (or build) the saved KV of the static
            # system prefix before the first call. The text protocol sends no
            # tools, so that prefix is exactly the start of every request.
            # No-op unless AWOS_PROVIDER=local; never raises.
            run.prefix_cache = _prepare_local_prefix(self.system_prompt)
        self._format_errors = self._truncations = 0
        try:
            while True:
                stop = self._stop_reason_before_call(run, started)
                if stop:
                    run.stop_reason = stop
                    break
                format_message = (FORMAT_ERROR_MESSAGE_TEXT if self.text_protocol
                                  else FORMAT_ERROR_MESSAGE).format(marker=SUBMIT_MARKER)
                run.steps += 1
                try:
                    reply = self.client.complete(self.system_prompt, self.messages,
                                                 self.registry)
                except Exception as exc:  # noqa: BLE001
                    record_error(COMPONENT, self.model_name, exc, turn=run.steps)
                    run.stop_reason = "model_error"
                    run.final_message = f"{type(exc).__name__}: {exc}"[:500]
                    if self._escalate(run, f"model error: {type(exc).__name__}", watch, before):
                        continue
                    break
                self._account(run, reply)
                self._last_used_tokens = int(reply.input_tokens or 0)
                calls = list(reply.tool_calls or [])
                run.transcript.append({"step": run.steps, "text": (reply.text or "")[:500],
                                       "commands": [str((c.arguments or {}).get("command", ""))[:300]
                                                    for c in calls]})

                if calls:
                    self._format_errors = self._truncations = 0
                    self.messages.append(self.client.format_assistant_turn(reply))
                    if any(is_submit_command(c) for c in calls):
                        done, feedback = self._submit(run, reply.text or "",
                                                      self._changed(watch, before))
                        if done:
                            if not run.success and self._escalate(
                                    run, "gate red after the feedback rounds", watch, before,
                                    note=str((run.gate or {}).get("feedback", ""))):
                                continue
                            break
                        results = [ToolResult(success=False, text=feedback, error=feedback)
                                   if is_submit_command(c) else
                                   ToolResult.fail("Not run: this reply also submitted.")
                                   for c in calls]
                        self.messages.extend(self.client.format_tool_results(calls, results))
                        self._after_turn(run, watch, before)
                        continue
                    results = []
                    for call in calls:
                        run.tool_calls += 1
                        results.append(self.registry.execute(call.name, call.arguments))
                    self.messages.extend(self.client.format_tool_results(calls, results))
                    run.transcript[-1]["ok"] = all(getattr(r, "success", False) for r in results)
                    if self.stuck is not None:
                        _, key = self._turn_state(watch, before, run.steps)
                        nudge = self.stuck.observe(run.steps, run.transcript[-1]["commands"],
                                                   results, key)
                        if nudge:
                            self._append_note(nudge)
                    self._after_turn(run, watch, before)
                    continue

                submission = submitted_text(reply.text or "")
                if submission is not None:
                    self._format_errors = self._truncations = 0
                    self.messages.append(self.client.format_assistant_turn(reply))
                    done, feedback = self._submit(run, submission,
                                                  self._changed(watch, before))
                    if done:
                        if not run.success and self._escalate(
                                run, "gate red after the feedback rounds", watch, before,
                                note=str((run.gate or {}).get("feedback", ""))):
                            continue
                        break
                    self.messages.append({"role": "user", "content": feedback})
                    self._after_turn(run, watch, before)
                    continue

                if _finish_reason(reply.raw) in TRUNCATED_FINISH:
                    self._truncations += 1
                    if self._truncations > MAX_TRUNCATIONS:
                        run.stop_reason = "truncated"
                        if self._escalate(run, "replies kept hitting the token limit",
                                          watch, before):
                            continue
                        break
                    if reply.text:
                        self.messages.append(self.client.format_assistant_turn(reply))
                    self.messages.append({"role": "user", "content": TRUNCATED_MESSAGE})
                    self._after_turn(run, watch, before)
                    continue

                self._format_errors += 1
                run.format_errors += 1
                if self.text_protocol:
                    run.parse_failures += 1  # the grammar should make this impossible
                if self._format_errors >= MAX_FORMAT_ERRORS:
                    run.stop_reason = "format_error"
                    run.final_message = (reply.text or "")[:500]
                    if self._escalate(run, "replies kept missing the action format",
                                      watch, before):
                        continue
                    break
                if reply.text:
                    self.messages.append(self.client.format_assistant_turn(reply))
                self.messages.append({"role": "user", "content": format_message})
                self._after_turn(run, watch, before)
        finally:
            run.elapsed_sec = self.clock() - started
            self._drain_summaries(run)
            if self.stuck is not None:
                run.stuck = self.stuck.summary()
                if self._stuck_earlier:
                    run.stuck["earlier_rungs"] = list(self._stuck_earlier)
            if self.compactor is not None:
                run.compaction = self.compactor.summary()
        if run.stop_reason not in ("submitted", "gate_red"):
            # Stopped by a limit, not by the model: what it changed is still reported.
            run.files_changed = self._changed(watch, before)
            run.success = False
        if self.checkpoint is not None and run.stop_reason != "model_error":
            # Ended on its own: not resumable. A model error (a crashed local
            # server) or a kill leaves the checkpoint for the next attempt.
            try:
                self.checkpoint.finish(run.stop_reason)
            except Exception:  # noqa: BLE001
                pass
        return run
