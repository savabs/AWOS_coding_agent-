# Work miner (v1)

**Purpose.** Derive how the owner actually works from local signals, so AWOS
automates what recurs instead of what we guess. Output: a ranked list of task
families, babysitting metrics for agent sessions, a weekly supervision series,
and the top automation candidates.

```
python -m scaffold.agent.work_miner --start-server   # start llama-server (4 slots), label, stop it
python -m scaffold.agent.work_miner                  # local model if up, else cache + rules
python -m scaffold.agent.work_miner --label rules    # deterministic only
```

Writes `.awos/jobs/work_profile.md`, `.awos/jobs/work_profile.json` and the
label cache `.awos/jobs/work_miner_labels.json` (all git-ignored).

## Privacy (hard rules)

- Prompts and shell history never leave the machine. The labeller talks only
  to a llama-server on localhost; `assert_local` refuses any other host.
- Secrets are redacted before a prompt is sent to the local model and before
  anything is stored.
- The stored profile and the label cache hold **no prompt text**: the cache is
  `taxonomy_version:sha256(prompt)[:20] -> {label, new_family, model}`; the
  profile has counts only for prompts (shell examples are redacted and short).

## Modules

| File | Role |
|------|------|
| `scaffold/agent/work_miner.py` | rules fallback, profile/scoring, candidates, report, CLI |
| `scaffold/agent/work_miner_label.py` | taxonomy, local-model labeller, hash cache, server start/stop |
| `scaffold/agent/work_miner_sessions.py` | owner-prompt filter, session timelines, babysitting metrics, weekly series |
| `scaffold/agent/work_miner_shell.py` | shell history rejoin + command-family normalisation |

## Owner prompts

From top-level transcripts `~/.claude/projects/*/*.jsonl` (subagents live
deeper and are skipped). A record is an owner prompt when it is a `user`
record that is not a sidechain, not `isMeta`, not a tool result, has
`origin.kind` of `human` (or none), `promptSource != system`, and does not
start with harness noise (`<system-reminder>`, `<task-notification>`, command
caveats, …). v0 also counted peer (agent-to-agent) messages; v1 excludes them.
Records are deduped by `uuid` across files (resumed/relocated sessions).

## Labelling

Taxonomy `v1.2` (in `work_miner_label.TAXONOMY`, each with an automatability
prior): continue / approve, status / progress check, keys / credentials /
quota (these three are **supervision**), steer / redirect agent, run benchmark / experiment, debug /
fix, build feature / implement, review / verify / test, git / commit / PR /
merge, research / explore, plan / strategy / discuss, explain / understand,
docs / writing, environment setup / install, machine / system admin, agent
orchestration / delegation, rules / memory / preferences, other.

The local model (Qwen3.5-9B via `scripts/local_model.sh`, up to 4 slots) gets
a system prompt with one-line definitions and must answer JSON constrained by
a schema: `label` from the enum + `new_family` (≤40 chars, only for `other`).
Temperature 0, thinking off, prompt truncated to 1500 chars. Off-schema or
unparseable replies fall back to the keyword rules. Proposals are aggregated
in the report so the taxonomy can be revised: bump `TAXONOMY_VERSION` and list
the families the new ones are carved from in `RELABEL_ON_BUMP`; cached labels
outside that set carry over, so a revision only re-asks the affected prompts.

History: v1.1 left 4% "other" but put ~30% of prompts in plan/strategy, and
the model's proposals for the rest were constraint / clarification / budget
items. v1.2 added "steer / redirect agent" (corrections, added constraints,
answers to the agent's questions) and widened keys to billing/budget.

## Shell history

zsh writes each embedded newline as a trailing backslash, so one pasted script
became dozens of v0 "commands" (`\`, `echo\`, `fi\`). v1 rejoins: trailing
backslash markers (a typed `\` continuation shows up as two), heredoc bodies
up to the delimiter, and open quotes (bash-style histories) — the latter two
only if they close within a bounded window, so a stray quote cannot swallow
the file. Families: git subcommand groups (inspect, commit/push, sync/branch),
python script / module / inline / repl, test runs across runners (pytest,
`python -m pytest`, npm/yarn test, cargo/go test, …), compound scripts, macOS
system tools, typo-tolerant matching (`clea` → clear).

## Session metrics (babysitting)

Per owner prompt: gap since the previous owner prompt; **agent run** (previous
prompt → last agent activity); agent **active** time (agent events closer than
10 min); idle before the prompt; and what preceded it, first match of:
key/auth error (tool output, API error, or agent text matching key/quota
patterns) · background task pending (Agent / `run_in_background` launch with
no completion notification yet, expiring after 12 h) · interrupted mid-run ·
agent asked a question · long silence (≥15 min idle) · agent finished turn.

Aggregates: prompts per session, median/p90 gaps and run lengths, supervision
share, supervision prompts per active agent-hour, preceder distributions for
supervision vs other prompts, and a weekly (ISO week) series. **The weekly
supervision share and supervision per agent-hour are the metrics the
supervision autopilot should push down.**

## Scoring and candidates

`score = share × automatability × recurrence × 100`, recurrence = fraction of
observed days the family appears on (a neutral 0.5 for undated shell history). Families
map to candidates through `PLAYBOOK` (e.g. git prompts + git shell commands →
"Git steward"); scores pool per candidate; the top 5 are reported with one
line on how AWOS takes them over.

## Tests

`tests/test_work_miner.py`: redaction, rules, labeller against a fake local
llama-server (schema-constrained request, redaction before send, fallback on
bad output, hash cache without text, incremental reruns, non-local hosts
refused), shell rejoin and normalisation, owner-prompt filter, session timing
and preceders, weekly series, dedupe, end-to-end report without prompt text.
