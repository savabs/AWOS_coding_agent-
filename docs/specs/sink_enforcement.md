# Sink policy hardening + enforcement hook

VISION stage: 1 (one excellent worker: verification and safety of the
execution layer). Builds on `docs/specs/computer_use_foundations.md` (T10).

## Problem

`classify_command` looked only at `argv[0]`, so `bash -c "rm -rf ~"`,
`env X=1 rm`, `xargs rm`, `sudo`, `ls; rm x`, `$(...)`, backticks and
interpreter inline code (`python -c`, `osascript -e`) slipped past as a
harmless "unknown program". And nothing made a verdict bind: the policy
decided, but no executor asked it.

## Classifier (scaffold/agent/desktop/policy.py)

- Tokenize like a POSIX shell (`shlex`, punctuation mode); split on `| || && ; & ( )`
  and newlines; drop redirections (out targets become WRITE_SANDBOX targets,
  `/dev/null` excepted); skip heredoc bodies.
- `$(...)`, backticks, `<(...)`, `>(...)` outside single quotes: the inner
  command is classified recursively.
- Wrappers (`env`, `sudo`, `doas`, `xargs`, `nohup`, `time`, `nice`, `timeout`,
  `stdbuf`, `caffeinate`, `command`, `exec`, `builtin`) unwrap to the wrapped
  command; `sudo`/`doas` also add WRITE_OUTSIDE.
- `sh/bash/zsh/... -c` and `eval` recurse into the script; `find -exec` recurses;
  `find -delete` is DELETE; `git -C dir push` is GIT_PUSH.
- New class `OPAQUE` ("opaque_code"): interpreter inline code, a shell or
  interpreter reading stdin (`curl | sh`), a dynamic program name, unparseable
  input, nesting > 6. OPAQUE is irreversible and always-ask: agent/owner -> ask
  (a grant cannot silence it), untrusted initiator -> deny.

## Enforcement (scaffold/agent/desktop/enforce.py)

`Enforcer.check_command / check_action / run_command / run_action`:
allow -> run; ask -> run only if the approver callback returns True (none,
no, or exception -> blocked); deny -> blocked, approver never consulted.
One JSONL audit line per check (`kind=enforce`, verdict, rule, outcome).
Modes via `AWOS_SINK_POLICY`: `off` (default, `enforcer_from_env` returns
None), `audit` (shadow: log, always run), `enforce`. Audit path:
`AWOS_SINK_AUDIT`, default `.awos/sink_audit.jsonl`.

## Opt-in integration point (not wired yet)

`scaffold/agent/tools/run_command.py`, `RunCommandTool.execute()`, directly
before `self.sandbox.run(args["command"], ...)`:

```python
enf = enforcer_from_env(getattr(self.sandbox, "workspace", "."))
if enf is not None:
    res = enf.check_command(args["command"], initiator=self.initiator)
    if not res.allowed:
        return ToolResult.fail(res.message)
```

`self.initiator` defaults to `Provenance.AGENT`; `agent_loop.py` should set it to
`Provenance.UNTRUSTED` for a call made after untrusted content (fetch_url output,
screen/AX text) entered the turn. The `shell` tool (`agent_loop.COMMAND_TOOLS`)
takes the same lines. Wiring is a separate step with its own live proof.

## Live proof

`python -m scaffold.agent.desktop.enforce --demo` prints 20 tricky commands
with agent/untrusted verdicts, runs six through the Enforcer with a dry-run
executor, and prints the audit log. `--classify "<cmd>"` shows one command's
(class, target) pairs.

## Known gaps

Static analysis only: aliases, shell functions, `$PATH` tricks, scripts run
from files (`bash script.sh`, `python x.py` stay "unknown program" =
WRITE_SANDBOX) and network use by ordinary programs (`pip install`,
`npm i`) are not seen. Quoted `;` and `$(` inside double quotes
over-approximate (stricter, never looser).
