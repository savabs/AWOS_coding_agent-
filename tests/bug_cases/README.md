---
title: "Benchmark Cases"
tags:
  - doc/wiki
  - topic/testing
---

# Benchmark Cases

Twelve cases used to measure one thing: does letting the model drive its own
turns through tools beat emitting a patch from a single blind look?

These are fixtures, not tests. `pytest.ini` excludes this directory — every
case has a `test_case.py`, so collecting them collides on the duplicate
basename and aborts the whole run. They are executed deliberately, each staged
in its own throwaway directory, by `tests/test_bench_cases.py` and
`scripts/bench_executors.py`.

## Layout

```
<case_name>/
    task.json        {"action": "...", "file": "buggy.py"}
    buggy.py         the file to fix
    test_case.py     fails before the fix, passes after
    fix.py           ground truth — validation and fault localisation
    context/         optional; other project files, staged alongside
```

## Tiers

The leading letter groups cases by what they demand.

| Tier | Premise | Cases |
|---|---|---|
| **A** | Self-contained. Everything needed is in `buggy.py`. | 4 |
| **B** | The cause lives in a file `buggy.py` does not contain. | 4 |
| **C** | The codebase already has the right answer; reuse beats reinvent. | 2 |
| **D** | The task description is vague; the contract is in the tests. | 2 |

Tier A is the baseline — both executors should manage these, and a failure
here is a problem with the executor, not the case.

Tiers B and C carry a `context/` directory, and that is what makes the
benchmark discriminating. A single-shot worker is handed only the *text* of
`buggy.py`, so a fix depending on a constant, field name or signature defined
in `context/` is unreachable for it by construction. An agent that can open
files finds it by looking. This is not a trick — it is the shape of most real
changes.

What each multi-file case requires from its context:

| Case | Needs |
|---|---|
| `b1_constant_mismatch` | `MAX_RETRIES` from `limits.py` |
| `b2_schema_drift` | the renamed field `display_name` |
| `b3_caller_contract` | the `strict` keyword its caller passes |
| `b4_enum_extension` | `SUCCEEDED`, `FAILED`, `CANCELLED` |
| `c1_reuse_existing_helper` | `normalize_path` |
| `c2_use_project_error_type` | `ValidationError` and its `field` |

Tier D withholds the contract from the task description, so the exact output
format or the true location of the defect is discoverable only by reading and
running the tests.

## Adding a case

Write the four files, then validate before trusting anything it reports:

```bash
python3 scripts/validate_bug_cases.py
```

Validation is not a formality. It fails a case whose tests pass on `buggy.py`
(it measures nothing), one whose tests fail even on `fix.py` (it is
unsolvable), and a multi-file case whose `fix.py` uses nothing from `context/`
(it does not discriminate, so both executors score alike). A case that is wrong
in any of these ways is worse than no case at all, because it still produces a
number.

Keep cases deterministic: no network, no clock, no randomness, no sleeping.
Every case must be solvable by a competent engineer from the repository alone.

## Running the benchmark

```bash
# record once against a real model, then replay free forever
python3 scripts/bench_executors.py --record --cassette-dir .awos/cassettes
python3 scripts/bench_executors.py --cassette-dir .awos/cassettes
python3 scripts/bench_executors.py --arms both --cassette-dir .awos/cassettes
```

See [[CHEAP_TESTING]] for the recording workflow and the cost controls.

## Related

- [[CHEAP_TESTING]]
- [[tool_using_agent_loop_spec]]
