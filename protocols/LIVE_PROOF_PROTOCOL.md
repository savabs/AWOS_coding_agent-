# Live Proof Protocol

> **Purpose:** Every non-trivial feature must be *felt*, not only asserted by unit tests.
> Unit tests prove code paths. Live proof proves the system behaves as intended in a way a human can read and trust.

---

## The rule

**Do not mark a feature done until you can run something and watch the right thing happen.**

| Layer | What it proves | Example |
|-------|----------------|---------|
| **Unit tests** | Functions and branches | `test_check_stagnation_trips_at_threshold` |
| **Live proof** | End-to-end behavior humans can observe | `./scripts/demo_stagnation_breaker.sh` → `[BREAKER]` + `paused (STAGNATION)` |

Unit tests alone are necessary but not sufficient for kernel, orchestrator, CLI, session, or safety features.

---

## When live proof is required

**Required** for changes that affect:
- Orchestrator loop (plan → route → worker → verify → learn)
- Session lifecycle (pause, resume, checkpoint, cancel)
- Safety mechanisms (budget caps, circuit breakers, stagnation breaker)
- CLI commands users run (`awos run`, `awos mission`, `awos sessions`, etc.)
- Verification / reward / routing behavior visible in a run

**Optional (unit tests only)** for:
- Pure helpers with no user-visible behavior
- Typo / comment-only edits
- Internal refactors with unchanged external behavior (regression tests suffice)

When in doubt: **require live proof.**

---

## What a live proof artifact must include

Every live proof ships as a **pair**:

| Artifact | Location | Contents |
|----------|----------|----------|
| **Runnable demo** | `scripts/demo_<feature>.sh` or documented `awos` command | One command to run; no hidden setup |
| **Proof guide** | `docs/<feature>_proof.md` | What to watch for, success markers, how to verify |

### Proof guide template

```markdown
# <Feature> — Live Proof

## Quick start
<one command>

## What to watch for
- Marker 1: `[TAG] message` ← means X
- Marker 2: `Status: paused (REASON)` ← proof it worked

## Success criteria
- [ ] Observable marker A appears
- [ ] Session/CLI shows expected state
- [ ] No manual code edits needed to trigger behavior

## Config (if any)
| Env var | Demo value | Production default |
```

### Demo script conventions

- Print a short preamble: what will happen, what markers to watch
- Use low thresholds / small fixtures so proof finishes quickly
- End with: session ID or command to inspect result
- `|| true` where pause is expected success, not failure

---

## Spec and task file requirements

**Spec** (`docs/specs/<name>_spec.md`) must have a **Live proof** section:
- Scenario (what breaks or succeeds)
- Command to run
- Success markers (exact strings or CLI output)
- Expected session/artifact state after run

**Task file** (`tasks/active/<name>.md`) must include:
```markdown
- [ ] Live proof: run `<command>` — watch for `<marker>`
```

Mark `[x]` only after you have actually run it and seen the markers.

---

## Checkpoint requirements

Checkpoints for features with live proof must record:
1. Command run
2. Key output lines observed (copy 2–5 lines, not full log)
3. Pass/fail against success markers

Example:
```markdown
## Live proof (stagnation breaker)
- Ran: `./scripts/demo_stagnation_breaker.sh`
- Saw: `[BREAKER] Stagnation detected`, `Status: paused (STAGNATION)`
- Session: `rs_abc123`
```

---

## Anti-patterns (forbidden)

| Anti-pattern | Why it's wrong |
|--------------|----------------|
| "Tests pass, ship it" | No human verified behavior |
| Mock-only integration test with no runnable demo | Can't feel it; hard to debug |
| Proof guide without runnable command | Documentation theater |
| Marking live proof done without running | Violates Write-Gate |
| 50-line shell script with no printed markers | Operator can't tell success from noise |

---

## Reference implementation

Stagnation breaker (canonical example):
- Demo: `scripts/demo_stagnation_breaker.sh`
- Guide: `docs/stagnation_breaker_proof.md`
- Mission fixture: `docs/missions/stagnation_proof.json`

---

## Related

- [[AWOS]] — §2.6 Live Proof Protocol
- [[stagnation_breaker_proof]] — example proof guide
- Quality gate: `python scripts/quality_gate.py --task tasks/active/<name>.md`
