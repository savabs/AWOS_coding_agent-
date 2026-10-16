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
  * limits: AWOS_LOOP_MAX_STEPS model calls (default 60) per route rung,
    counted from the rung's start (without routing: per task), and per task
    the cost cap, the goal budget and a wall clock;
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
    (goal + handover note with secrets redacted; the prompt cache does not
    carry across models anyway) on the same working tree. A non-final rung
    is bounded by its allowance (rung_steps_); the final rung gets the full
    max steps from its own start, so a late escalation does not starve the
    stronger model.

Model errors are classified (error_kind: context_overflow, auth, rate_limit,
timeout, connection, server, other; LoopRun.error_kind). Only the transient
kinds (rate_limit, timeout, connection, server) leave the checkpoint
resumable. A context overflow first forces one synchronous compaction and
retries (at most once per overflow streak), then tries the next rung, then
stops with stop_reason "context_overflow".

Command output that contains the harness's own note markers (the stuck
detector's "[harness note]", the handover and gate-feedback lead lines) is
quoted in render_observation, so a file the model prints cannot pose as the
harness.

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

import hashlib
import json
import os
import platform
import re
import shlex
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

try:
    from .agent_loop import (TRUNCATED_FINISH, WorkspaceWatch, _finish_reason, _int0,
                             estimate_cost)
    from .llm_call_log import record_error, record_response, timing_fields
    from .loop_checkpoint import redact_text
    from .loop_compaction import (CHARS_PER_TOKEN, SUMMARY_INSTRUCTION, Compactor,
                                  compaction_enabled, context_window, estimate_tokens,
                                  next_request_tokens, summary_request)
    from .loop_grammar import wrap_for_loop
    from .loop_route import HANDOVER_TEMPLATE, handover_note
    from .loop_stuck import NUDGE_PREFIX, StuckDetector, stuck_mode
    from .tools.base import Tool, ToolRegistry, ToolResult
except ImportError:  # flat sys.path import (scaffold/agent on the path)
    from agent_loop import (TRUNCATED_FINISH, WorkspaceWatch, _finish_reason, _int0,
                            estimate_cost)
    from llm_call_log import record_error, record_response, timing_fields
    from loop_checkpoint import redact_text
    from loop_compaction import (CHARS_PER_TOKEN, SUMMARY_INSTRUCTION, Compactor,
                                 compaction_enabled, context_window, estimate_tokens,
                                 next_request_tokens, summary_request)
    from loop_grammar import wrap_for_loop
    from loop_route import HANDOVER_TEMPLATE, handover_note
    from loop_stuck import NUDGE_PREFIX, StuckDetector, stuck_mode
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
#: Output cap assumed when the client does not say (compaction reserve).
DEFAULT_OUTPUT_CAP = 8192
#: Safety margin (tokens) under the window for compaction and summary requests.
CONTEXT_MARGIN_TOKENS = 1000
#: keep_recent of the temporary compactor a context overflow builds.
OVERFLOW_KEEP_RECENT = 2


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, "").strip() or default))
    except ValueError:
        return default


def max_steps() -> int:
    """AWOS_LOOP_MAX_STEPS (default 60): model calls per route rung, counted
    from the rung's start (per task when routing is off)."""
    return _int_env("AWOS_LOOP_MAX_STEPS", DEFAULT_MAX_STEPS, minimum=1)


def gate_retries() -> int:
    """AWOS_LOOP_GATE_RETRIES (default 2): red-gate feedback rounds after a submit."""
    return _int_env("AWOS_LOOP_GATE_RETRIES", DEFAULT_GATE_RETRIES)


def command_timeout() -> int:
    """AWOS_LOOP_CMD_TIMEOUT_S (default 120, max 600): one bash command's limit."""
    return min(MAX_CMD_TIMEOUT_S, _int_env("AWOS_LOOP_CMD_TIMEOUT_S", DEFAULT_CMD_TIMEOUT_S,
                                           minimum=1))


# ── model error classification ───────────────────────────────────────────────


ERROR_KINDS = ("context_overflow", "auth", "rate_limit", "timeout", "connection", "server",
               "other")
#: Kinds worth another attempt later: they leave the checkpoint resumable.
TRANSIENT_ERROR_KINDS = frozenset({"rate_limit", "timeout", "connection", "server"})

_OVERFLOW_TEXT = re.compile(
    r"prompt is too long|context_length_exceeded|maximum context length|"
    r"exceeds the available context size|context size|context window|"
    r"too many tokens|input is too long|reduce the length of the messages", re.I)
_AUTH_TEXT = re.compile(
    r"invalid x-api-key|invalid api key|incorrect api key|api key not valid|"
    r"authentication|unauthori[sz]ed|permission denied|api_key is not set|"
    r"api key is not set|no api key|missing api key", re.I)
_RATE_TEXT = re.compile(r"rate limit|rate_limit|too many requests", re.I)
_OVERLOAD_TEXT = re.compile(r"overloaded|internal server error|bad gateway|"
                            r"service unavailable|gateway timeout", re.I)
_TIMEOUT_TEXT = re.compile(r"timed out|timeout", re.I)
_CONNECTION_TEXT = re.compile(r"connection (refused|reset|error|aborted)|"
                              r"failed to establish|name or service not known|"
                              r"server disconnected|remote end closed", re.I)


def _status_code(exc: BaseException) -> Optional[int]:
    for obj in (exc, getattr(exc, "response", None)):
        code = getattr(obj, "status_code", None) if obj is not None else None
        if code is None and obj is not None:
            code = getattr(obj, "status", None)
        if isinstance(code, int) and not isinstance(code, bool):
            return code
    return None


def _error_text(exc: BaseException) -> str:
    parts = [str(exc)]
    for attr in ("message", "code", "type", "body"):
        val = getattr(exc, attr, None)
        if val is not None and not callable(val):
            parts.append(val if isinstance(val, str) else repr(val))
    return " ".join(parts)


def error_kind(exc: BaseException) -> str:
    """Classify a model-call exception: "context_overflow" | "auth" |
    "rate_limit" | "timeout" | "connection" | "server" | "other".

    Matches Anthropic / OpenAI SDK exception class names (any class in the
    MRO), HTTP status codes and message text (Anthropic "prompt is too long",
    OpenAI "context_length_exceeded" / "maximum context length", llama-server
    "exceeds the available context size")."""
    try:
        names = {c.__name__ for c in type(exc).__mro__}
        text = _error_text(exc)
        code = _status_code(exc)
        if _OVERFLOW_TEXT.search(text) or code == 413:
            return "context_overflow"
        if (names & {"AuthenticationError", "PermissionDeniedError"}
                or code in (401, 403) or _AUTH_TEXT.search(text)):
            return "auth"
        if "RateLimitError" in names or code == 429 or _RATE_TEXT.search(text):
            return "rate_limit"
        # Timeouts before connection errors: APITimeoutError subclasses
        # APIConnectionError in both SDKs.
        if (names & {"APITimeoutError", "Timeout", "TimeoutError", "TimeoutException",
                     "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout",
                     "timeout"} or code in (408, 504)):
            return "timeout"
        if (names & {"InternalServerError", "OverloadedError", "ServiceUnavailableError",
                     "APIStatusError"} and (code is None or code >= 500)) \
                or (code is not None and code >= 500) or _OVERLOAD_TEXT.search(text):
            return "server"
        if (names & {"APIConnectionError", "ConnectionError", "ConnectError",
                     "RemoteProtocolError", "NetworkError", "ConnectionRefusedError",
                     "ConnectionResetError", "BrokenPipeError"}
                or _CONNECTION_TEXT.search(text)):
            return "connection"
        if _TIMEOUT_TEXT.search(text):
            return "timeout"
    except Exception:  # noqa: BLE001 - classification never raises
        pass
    return "other"


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


#: Lead text of the harness's own notes (stuck nudges and resume / overflow
#: notes use NUDGE_PREFIX; the handover note and gate feedback use these).
HARNESS_LEADS = (HANDOVER_TEMPLATE.split("(", 1)[0].strip(),
                 "You submitted, but AWOS ran the project's tests",
                 "The last check reported:")
_FORGED_PREFIX = re.compile(r"\[\s*" + r"\s+".join(
    re.escape(w) for w in NUDGE_PREFIX.strip("[]").split()) + r"\s*\]", re.I)
_FORGED_LEADS = re.compile("|".join(r"\s+".join(re.escape(w) for w in lead.split())
                                    for lead in HARNESS_LEADS), re.I)


def neutralise_harness_text(output: str) -> str:
    """Quote anything in command output that looks like a harness note, so a
    file the model prints cannot speak as the harness. Output without such
    text comes back unchanged (same bytes)."""
    if not output:
        return output
    out = _FORGED_PREFIX.sub("[quoted text: harness note]", output)
    return _FORGED_LEADS.sub(lambda m: f"(quoted from command output) {m.group(0)}", out)


def render_observation(exit_code: int, output: str, timed_out: bool = False,
                       timeout_s: int = 0) -> str:
    output = neutralise_harness_text(output)
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
    #: format_error | truncated | model_error | context_overflow. "running"
    #: only while the loop runs: every way out of it sets a real reason.
    stop_reason: str = "running"
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
    #: Local prefix-cache drift (loop_grammar: tokens that should have been
    #: cached from the previous call but were re-read), over the calls the
    #: client could judge; speculative decoding drafted / accepted tokens.
    prefix_drift_checked: int = 0
    prefix_drift_calls: int = 0
    prefix_drift_tokens: int = 0
    draft_tokens: int = 0
    draft_accepted: int = 0
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
    #: {"from", "to", "reason", "step", "rung_steps", "rung_cost_usd",
    #: "stuck"} (loop_route.py; "stuck" only when the detector ran).
    rung: int = 0
    escalations: list = field(default_factory=list)
    #: StuckDetector.summary() / Compactor.summary(), when they ran.
    stuck: Optional[dict] = None
    compaction: Optional[dict] = None
    #: error_kind() of the model error the run stopped on (None otherwise),
    #: and the kind of every model error met on the way.
    error_kind: Optional[str] = None
    model_errors: list = field(default_factory=list)
    #: Compactions forced by a context overflow.
    forced_compactions: int = 0
    #: Checkpoint writes that failed (None: no checkpoint in use).
    checkpoint_failures: Optional[int] = None
    #: On resume: files that changed after the last saved step (the kill
    #: landed mid-command), or None when the workspace matched.
    resume_changed: Optional[list] = None

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
            if self.resume_changed is not None:
                out["resume_changed"] = list(self.resume_changed)
        if self.error_kind is not None:
            out["error_kind"] = self.error_kind
        if self.model_errors:
            out["model_errors"] = list(self.model_errors)
        if self.forced_compactions:
            out["forced_compactions"] = self.forced_compactions
        if self.checkpoint_failures is not None:
            out["checkpoint_failures"] = self.checkpoint_failures
        if self.escalations:
            out["rung"] = self.rung
            out["escalations"] = list(self.escalations)
        if self.stuck is not None:
            out["stuck"] = self.stuck
        if self.compaction is not None:
            out["compaction"] = self.compaction
        if self.prefix_drift_checked:
            out["prefix_drift_calls"] = self.prefix_drift_calls
            out["prefix_drift_tokens"] = self.prefix_drift_tokens
            out["prefix_drift_checked"] = self.prefix_drift_checked
        if self.draft_tokens:
            out["draft_tokens"] = self.draft_tokens
            out["draft_acceptance"] = round(self.draft_accepted / self.draft_tokens, 4)
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
                      "prefill_tokens", "gen_tokens", "gen_ms", "rung", "escalations",
                      "forced_compactions", "model_errors")


def workspace_fingerprint(snap: Optional[dict], files: list) -> Optional[dict]:
    """What a checkpoint remembers of the workspace: a stable hash of every
    file's (size, mtime), the newest mtime, and the changed files' stats.
    None when the tree was too big to watch."""
    if snap is None:
        return None
    items = sorted((k, list(v)) for k, v in snap.items())
    digest = hashlib.sha256(json.dumps(items, separators=(",", ":")).encode("utf-8",
                                                                          "replace"))
    return {"fp": digest.hexdigest()[:24],
            "max_mtime_ns": max((v[1] for v in snap.values()), default=0),
            "files": {f: list(snap[f]) for f in files if f in snap}}


def changed_since(saved: Any, snap: Optional[dict]) -> Optional[list]:
    """Files that changed after `saved` (a workspace_fingerprint) was taken:
    None when the workspace still matches; [] when it differs but no file can
    be named (e.g. only deletions)."""
    if not isinstance(saved, dict) or snap is None:
        return None
    current = workspace_fingerprint(snap, [])
    if current is None or current["fp"] == saved.get("fp"):
        return None
    try:
        newest = int(saved.get("max_mtime_ns") or 0)
    except (TypeError, ValueError):
        newest = 0
    names = {f for f, st in snap.items() if st[1] > newest}
    for f, st in (saved.get("files") or {}).items():
        if snap.get(f) is None or list(snap.get(f)) != list(st):
            names.add(f)
    return sorted(names)


def _local_task_lease(executor: Any) -> Any:
    """local_runtime.task_lease() for a run on the local model, else a no-op
    context. Never raises."""
    import contextlib

    local = executor.text_protocol or executor.model_name.lower().startswith("local/")
    if not local:
        return contextlib.nullcontext()
    try:
        try:
            from scaffold.agent import local_runtime
        except ImportError:  # pragma: no cover - flat import
            import local_runtime  # type: ignore
        return local_runtime.task_lease(executor.goal[:60])
    except Exception:  # noqa: BLE001 - the lease is an optimisation only
        return contextlib.nullcontext()


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
        self._state_snap: Optional[dict] = None
        #: Size of the last request and its reply, and len(messages) before it
        #: (compaction triggers on the estimated NEXT request).
        self._last_in = 0
        self._last_out = 0
        self._msgs_at_call: Optional[int] = None
        self._rung_cost_start = 0.0
        #: A forced compaction already answered the current overflow streak.
        self._overflow_forced = False
        #: Compactor a context overflow built when compaction is off.
        self._overflow_compactor: Optional[Compactor] = None
        self._resume_summaries = 0
        self._resume_rung = 0

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
        self.compactor = self._make_compactor() if self.compact else None
        self._overflow_compactor = None

    def _output_cap(self) -> int:
        """The client's output-token cap (max_tokens), or DEFAULT_OUTPUT_CAP."""
        for obj in (self.client, getattr(self.client, "inner", None)):
            cap = getattr(obj, "max_tokens", None) if obj is not None else None
            if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
                return cap
        return DEFAULT_OUTPUT_CAP

    def _make_compactor(self, keep_recent: Optional[int] = None) -> Compactor:
        """A compactor for the client in use: thresholds keep room for one
        reply plus one maximal observation; a failed summary falls back to a
        deterministic one."""
        reserve = (self._output_cap() + OUTPUT_LIMIT // CHARS_PER_TOKEN
                   + CONTEXT_MARGIN_TOKENS)
        kw = {"keep_recent": keep_recent} if keep_recent else {}
        return Compactor(self.model_name, summarize=self._summarize, reserve_tokens=reserve,
                         deterministic_fallback=True, **kw)

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
        # Steps are per rung, counted from the rung's start: the final rung
        # gets the full max_steps however late it took over (a non-final
        # rung hands over at its rung_steps allowance first). The cost cap,
        # goal budget and wall clock stay per task.
        if run.steps - self._rung_start >= self.max_steps:
            return "max_steps"
        # A resumed run's earlier spend was already taken off the cap by the
        # caller (host SpendTracker), so only this attempt's spend counts here.
        if self.cost_cap is not None and run.cost_usd - self._cost_at_start >= self.cost_cap:
            return "cost_cap"
        return None

    def _account(self, run: LoopRun, reply: Any) -> None:
        cached, written = _int0(reply, "cached_tokens"), _int0(reply, "cache_write_tokens")
        cost = estimate_cost(self.model_name, reply.input_tokens, reply.output_tokens,
                             cached, written, _int0(reply, "cache_write_1h_tokens"))
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
        drift = getattr(reply, "prefix_drift", None)
        if isinstance(drift, int):
            run.prefix_drift_checked += 1
            if drift > 0:
                run.prefix_drift_calls += 1
                run.prefix_drift_tokens += drift
        drafted = getattr(reply, "draft_tokens", None)
        if isinstance(drafted, int) and drafted > 0:
            run.draft_tokens += drafted
            run.draft_accepted += int(getattr(reply, "draft_accepted", 0) or 0)
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
        if self.gate is None:
            g = {"green": True}
        else:
            # The gate (the full suite) can outlast Anthropic's 5-minute cache
            # TTL; the client keeps the prefix warm meanwhile when a feedback
            # round could follow (AnthropicToolClient.keep_warm, C11).
            warm = getattr(self.client, "keep_warm", None)
            if callable(warm):
                with warm(self._rung_gate_retries < self.max_gate_retries) as kept:
                    g = self.gate(files, submission)
                for ping in list(getattr(kept, "replies", None) or []):
                    self._account(run, ping)
            else:
                g = self.gate(files, submission)
        g = dict(g or {})
        run.gate = g
        if g.get("retry") and self._rung_gate_retries < self.max_gate_retries:
            self._rung_gate_retries += 1
            run.gate_retries += 1
            feedback = gate_feedback(str(g.get("feedback", "")), self._rung_gate_retries,
                                     self.max_gate_retries)
            for comp in (self.compactor, self._overflow_compactor):
                if comp is not None:
                    comp.note_gate_feedback(feedback)
            return False, feedback
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
        files, key, snap = [], None, None
        if watch is not None:
            snap = watch.snapshot()
            if snap is not None:
                files = watch.changed(before, snap) or []
                key = hash(tuple(sorted((f, snap.get(f)) for f in files)))
        if self._files_before_resume:
            files = sorted(set(files) | set(self._files_before_resume))
        self._state_step, self._state, self._state_snap = step, (files, key), snap
        return self._state

    def _summary_budget_chars(self) -> int:
        """Characters the summary request's transcript may take: the window
        minus the summary's own output, the system prompt and a margin."""
        comp = self.compactor or self._overflow_compactor
        window = comp.window if comp is not None else context_window(self.model_name)
        tokens = (window - self._output_cap() - CONTEXT_MARGIN_TOKENS
                  - estimate_tokens([], self.system_prompt))
        return max(2000, tokens * CHARS_PER_TOKEN - len(SUMMARY_INSTRUCTION) - 200)

    def _summarize(self, msgs: list) -> str:
        """Compaction's summary call: the plain tool client (never the bash
        grammar), a tool-free transcript request sized to fit the window.
        Cost is accounted by the loop thread (_drain_summaries)."""
        inner = getattr(self.client, "inner", self.client)
        request = summary_request(msgs, budget_chars=self._summary_budget_chars())
        reply = inner.complete(self.system_prompt, request, self.registry)
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
        comp = self.compactor or self._overflow_compactor
        out.update(rung_start=self._rung_start, rung_gate_retries=self._rung_gate_retries,
                   rung_cost_start=self._rung_cost_start, files_changed=list(files),
                   compaction_summaries=comp.summaries if comp is not None else 0)
        fp = workspace_fingerprint(self._state_snap, files)
        if fp is not None:
            out["workspace"] = fp
        return out

    def _resume_note(self, run: LoopRun, saved: Any, snap: Optional[dict]) -> None:
        """After a resume: when the workspace moved after the last saved step
        (the kill may have landed mid-command), say so in the conversation."""
        names = changed_since(saved, snap)
        if names is None:
            return
        run.resume_changed = list(names)
        # The baseline snapshot of this attempt already holds these edits, so
        # report them with the files changed before the kill.
        self._files_before_resume = sorted(set(self._files_before_resume) | set(names))
        shown = ", ".join(names[:30]) + (f" (and {len(names) - 30} more)"
                                         if len(names) > 30 else "")
        self._append_note(
            f"{NUDGE_PREFIX} This run was interrupted and has been resumed. The workspace "
            "changed after the last step saved above, so the interruption may have landed "
            "in the middle of a command and its effect may be partial. Files changed since "
            f"then: {shown or '(could not tell which; possibly deletions)'}. Check them "
            "(for example with `git status` and `git diff`) before you continue.")

    def _resume(self, run: LoopRun, snap: Optional[dict] = None) -> bool:
        """Continue a killed run from its checkpoint. False: start fresh.
        `snap`: the workspace snapshot taken at this attempt's start."""
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
        try:
            self._rung_cost_start = float(counters.get("rung_cost_start") or 0.0)
        except (TypeError, ValueError):
            self._rung_cost_start = 0.0
        self._files_before_resume = list(counters.get("files_changed") or [])
        self._resume_summaries = int(counters.get("compaction_summaries") or 0)
        self._resume_rung = rung
        self._cost_at_start = run.cost_usd
        self.messages = list(messages)
        run.resumed = True
        self._resume_note(run, counters.get("workspace"), snap)
        self._operator_note()
        return True

    def _operator_note(self) -> None:
        """An auto-continue attempt's NOTE (host/worker.py writes it next to
        the checkpoint as `<checkpoint>.note`). A fresh start already has it in
        the goal; a resumed conversation gets it here, once."""
        path = getattr(self.checkpoint, "path", None)
        if path is None:
            return
        try:
            text = Path(f"{path}.note").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return
        if text:
            self._append_note(text)

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
        stuck_summary = self.stuck.summary() if self.stuck is not None else None
        if stuck_summary is not None:
            self._stuck_earlier.append({"model": old, **stuck_summary})
        self._set_client(nxt)
        run.rung += 1
        event = {"from": old, "to": self.model_name, "reason": reason, "step": run.steps,
                 "rung_steps": run.steps - self._rung_start,
                 "rung_cost_usd": round(run.cost_usd - self._rung_cost_start, 6)}
        if stuck_summary is not None:
            event["stuck"] = stuck_summary
        run.escalations.append(event)
        handover = handover_note(run.transcript, files, reason)
        if note:
            handover += f"\n\nThe last check reported:\n{note.strip()[:4000]}"
        # The note quotes commands and test output: keep secrets out of it.
        handover = redact_text(handover)
        # A fresh conversation in the new rung's protocol: the old one may be
        # in another message shape, and the cache is per model anyway.
        self.messages = [{"role": "user", "content": instance_prompt(
            self.goal, text_protocol=self.text_protocol) + "\n\n" + handover}]
        self._new_rung_layers()
        self._rung_start = run.steps
        self._rung_cost_start = run.cost_usd
        self._rung_gate_retries = 0
        self._format_errors = self._truncations = 0
        self._overflow_forced = False
        self._msgs_at_call = None
        run.stop_reason, run.success = "running", False
        return True

    def _recover_overflow(self, run: LoopRun) -> bool:
        """Context overflow: force one synchronous compaction (a temporary
        compactor with fewer recent turns when compaction is off) so the call
        can be retried. False when this overflow streak already had its
        forced compaction or nothing could be compacted."""
        if self._overflow_forced:
            return False
        self._overflow_forced = True
        comp = self.compactor
        if comp is None:
            if self._overflow_compactor is None:
                self._overflow_compactor = self._make_compactor(OVERFLOW_KEEP_RECENT)
                if self._resume_summaries and run.resumed and run.rung == self._resume_rung:
                    self._overflow_compactor.restore(self.messages, self._resume_summaries)
            comp = self._overflow_compactor
        swapped = comp.force(self.messages)
        self._drain_summaries(run)
        if swapped is None:
            return False
        self.messages = swapped
        self._msgs_at_call = None
        run.forced_compactions += 1
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
            used = self._next_request_tokens()
            self.compactor.maybe_start(self.messages, used)
            swapped = self.compactor.maybe_swap(self.messages, used)
            if swapped is not None:
                self.messages = swapped
                self._msgs_at_call = None
            self._drain_summaries(run)
        if self._overflow_compactor is not None:
            self._overflow_compactor.note_files(self._turn_state(watch, before, run.steps)[0])
        if self.checkpoint is not None:
            files = (self._turn_state(watch, before, run.steps)[0]
                     if watch is not None else list(self._files_before_resume))
            try:
                ok = self.checkpoint.save(self.messages, self._counters(run, files))
            except Exception:  # noqa: BLE001 - a checkpoint never ends the loop
                ok = False
            if ok is False:
                run.checkpoint_failures = (run.checkpoint_failures or 0) + 1

    def _next_request_tokens(self) -> int:
        """Estimated size of the next request (0: unknown, the compactor then
        estimates the whole list): the last request + its reply + what was
        appended since, without the reply's own assistant turn."""
        if self._msgs_at_call is None or self._msgs_at_call > len(self.messages):
            return 0
        appended = [m for m in self.messages[self._msgs_at_call:]
                    if not (isinstance(m, dict) and m.get("role") == "assistant")]
        return next_request_tokens(self._last_in, self._last_out, appended)

    # -- the loop --

    def run(self) -> LoopRun:
        # A local model holds a task lease while the run lasts, so the power
        # policy never unloads llama-server mid-task (local_runtime.task_lease).
        with _local_task_lease(self):
            return self._run()

    def _run(self) -> LoopRun:
        run = LoopRun(grammar=self.text_protocol)
        started = self.clock()
        watch = WorkspaceWatch(self.workspace) if self.workspace else None
        before = watch.snapshot() if watch is not None else None
        # History is append-only from here: no message is edited, re-rendered
        # or dropped mid-run, so every request is the previous request plus a
        # suffix (tests/test_prefix_stability.py). The exceptions are opt-in:
        # compaction (AWOS_LOOP_COMPACT) and an escalation to another rung.
        if not self._resume(run, before):
            run = LoopRun(grammar=self.text_protocol)
            self.messages = [{"role": "user", "content": instance_prompt(
                self.goal, text_protocol=self.text_protocol)}]
        self._new_rung_layers()
        if self.checkpoint is not None:
            run.checkpoint_failures = 0
        if run.resumed and self._resume_summaries and self.compactor is not None:
            self.compactor.restore(self.messages, self._resume_summaries)
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
                if stop == "max_steps" and self._escalate(
                        run, f"rung step budget ({self.max_steps}) spent", watch, before):
                    continue  # a non-final rung out of steps: the next rung takes over
                if stop:
                    run.stop_reason = stop
                    break
                format_message = (FORMAT_ERROR_MESSAGE_TEXT if self.text_protocol
                                  else FORMAT_ERROR_MESSAGE).format(marker=SUBMIT_MARKER)
                run.steps += 1
                msgs_before = len(self.messages)
                try:
                    reply = self.client.complete(self.system_prompt, self.messages,
                                                 self.registry)
                except Exception as exc:  # noqa: BLE001
                    record_error(COMPONENT, self.model_name, exc, turn=run.steps)
                    kind = error_kind(exc)
                    run.error_kind = kind
                    run.model_errors.append(kind)
                    run.final_message = f"{type(exc).__name__}: {exc}"[:500]
                    if kind == "context_overflow":
                        # Compact once and retry; then a rung with a fresh
                        # conversation; else stop.
                        if self._recover_overflow(run):
                            continue
                        run.stop_reason = "context_overflow"
                        if self._escalate(run, f"context overflow: {type(exc).__name__}",
                                          watch, before):
                            continue
                        break
                    run.stop_reason = "model_error"
                    # Auth / no key on a lower rung: the next rung is another
                    # provider or key, so moving up is still right. At the top: stop.
                    if self._escalate(run, f"model error ({kind}): {type(exc).__name__}",
                                      watch, before):
                        continue
                    break
                self._overflow_forced = False
                self._account(run, reply)
                self._last_in = int(reply.input_tokens or 0)
                self._last_out = int(reply.output_tokens or 0)
                self._msgs_at_call = msgs_before
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
            elif self._overflow_compactor is not None:
                run.compaction = {**self._overflow_compactor.summary(), "enabled": False}
        if run.stop_reason not in ("model_error", "context_overflow"):
            run.error_kind = None  # an error met on the way did not end the run
        if run.stop_reason not in ("submitted", "gate_red"):
            # Stopped by a limit, not by the model: what it changed is still reported.
            run.files_changed = self._changed(watch, before)
            run.success = False
        transient = (run.stop_reason == "model_error"
                     and run.error_kind in TRANSIENT_ERROR_KINDS)
        if self.checkpoint is not None and not transient:
            # Ended on its own (or on an error a retry will not fix: auth, an
            # overflow, a bad request): not resumable. A transient model error
            # (rate limit, timeout, a crashed local server) or a kill leaves
            # the checkpoint for the next attempt.
            try:
                self.checkpoint.finish(run.stop_reason)
            except Exception:  # noqa: BLE001
                pass
        return run
