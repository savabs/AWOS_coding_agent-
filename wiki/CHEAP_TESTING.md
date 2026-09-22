---
title: "Testing the Agent Without Paying For It"
tags:
  - doc/wiki
  - topic/testing
---

# Testing the Agent Without Paying For It

Iterating on the agent should not mean juggling API keys or watching a meter.
There are four tiers here, and only the last one costs anything. Most work
happens in tiers 0–2, where no credential exists at all.

| Tier | What it exercises | Cost | Needs a key |
|---|---|---|---|
| 0 Scripted stub | loop mechanics, every stop condition | free | no |
| 1 Cassette replay | a real model's actual behaviour, deterministically | free | no |
| 2 Local model | genuinely new behaviour, unlimited attempts | free | no |
| 3 Paid API | frontier capability, recording new cassettes | cents | yes |

---

## Tier 0 — scripted stub

`tests/test_agent_loop.py` drives the loop with hand-written replies. Instant,
offline, runs in CI. It proves the loop terminates, that edits are refused when
they would break a file, and that the model can recover from a rejection.

What it cannot prove is that a *real* model behaves this way. That is tier 1.

```bash
python3 -m pytest tests/test_agent_loop.py -q
```

---

## Tier 1 — cassettes (the one that removes the key problem)

Record a run once; replay those exact replies forever, offline and free.

```bash
# once — against anything: a local model, a cheap API, a frontier model
awos agent "add retry to fetch()" --cassette .awos/cassettes/retry.json --record

# thereafter — free, deterministic, no credential present
awos agent "add retry to fetch()" --cassette .awos/cassettes/retry.json
```

A cassette is plain JSON holding the model's replies and token counts. It is
readable, diffable, and safe to commit — it contains no credentials (there is a
test asserting exactly that).

This is what CI should run. It catches real regressions: change the system
prompt, or break a tool, and the replayed run diverges.

**On divergence.** Replay is keyed on the conversation so far. When a run stops
matching the recording, the cassette says so rather than quietly serving the
wrong turn. Lenient mode (default) then falls back to the next recorded reply
in order and logs a warning; `--strict` / `AWOS_CASSETTE_STRICT=1` refuses
outright. Prefer strict in CI, where silent drift is what you are trying to
catch.

**Cassettes are portable across directories.** Tool output embeds absolute
paths, so a naive recording only replays in the directory it was made in. The
project root is normalised out of the key, which is what lets one cassette
drive a benchmark case in a fresh checkout each run.

---

## Tier 2 — a local model

Free and unlimited, for exploring behaviour no cassette covers yet. Any
OpenAI-compatible server works — Ollama, LM Studio, vLLM, llama.cpp:

```bash
ollama serve
ollama pull qwen2.5-coder

export AWOS_BASE_URL=http://localhost:11434/v1
export AWOS_AGENT_MODEL=qwen2.5-coder
awos agent "add retry to fetch()"
```

No key is needed; the SDK's mandatory credential is filled with a placeholder.
Pick a model that supports tool calling — the loop is driven by tool calls, and
a model without them will simply talk instead of acting.

A local model is weaker than a frontier one, so treat a failure here as a
question ("did the loop handle that badly?") rather than a verdict on the
architecture.

---

## Tier 3 — a paid API, capped

For recording cassettes and for measuring real capability. Always with a
ceiling on a single run:

```bash
awos agent "add retry to fetch()" --max-cost 0.25
```

The run aborts with `stop_reason="cost_cap"` once it has spent that much.
Distinct from `BudgetLedger`'s monthly cap: this bounds *one* run, which is
what makes it safe to point a benchmark at a paid model and walk away.

DeepSeek is the cheap tier at $0.14/$0.28 per MTok — a fifteen-case benchmark
costs cents, not dollars. Prices live in `PRICES` in
`scaffold/agent/agent_loop.py`; an unpriced model is costed at zero rather than
guessed at, so add new ones there before relying on a cap.

---

## Which to use

- **Changing the loop, prompts, or tools** → tiers 0 and 1. No key, instant.
- **CI** → tiers 0 and 1, strict mode.
- **"Would a real model do this?"** → tier 2 first, tier 3 if it matters.
- **Benchmarking executors against each other** → tier 3 once to record, then
  tier 1 forever, so the comparison is reproducible and free to repeat.

The point of recording is that a paid run should happen once and then keep
paying you back.

## Related

- [[tool_using_agent_loop_spec]]
