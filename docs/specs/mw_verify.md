# mw_verify: expected-effects verification and the false-VERIFIED benchmark

Product track. Background research: `docs/research/rl_cua_2026-10-24/repair_verify.md`, verification sections. The rule it sets: deterministic expected-effects checks decide PASS, and a model may only veto a PASS, never grant one.

## What it does

`scaffold/agent/desktop/effects/expect.py`

- **`induce(demo_before, demo_after, kind=...)`** turns one taught demo into an `Expectation`. The demo input is a readback of the system of record before and after, plus the final page and any files.
- **What an Expectation holds.** Each item gets its own verdict:
  - `atom_delta`: one item on a view changed by exactly N. This covers "value present", "row gone" and duplicates (+2).
  - `read_unchanged`: nothing else on that view changed, so nothing else was deleted.
  - `row_count`: the item count changed by N.
  - `no_error`: no role=alert banner on the final page.
  - `files_arrived`: the right count, extensions and size range.
  - `files_distinct`: no two arrived files share a sha256.
  - `answer`: the final answer contains the taught value.
  - `ended`: the run ended with needs_user at a checkpoint.
  - Advisory items, which never decide the verdict: `banner` (success banner), `url` (final URL pattern) and `final_text` (a value visible on the final page).
- **`check(exp, before, after, judge=None)`** gives each item CONFIRMED, REFUTED or INDETERMINATE, then a run verdict:
  - **VERIFIED** only if every required item is CONFIRMED and at least one required item exists. An act demo whose effect no readback could see is marked `observable=False`, and its runs are never VERIFIED.
  - **FAILED** if any required item is REFUTED. The reason starts with `FAILED:` and names each refuted item.
  - **UNVERIFIED** for anything else: missing or unreadable evidence, nothing to check, or a judge veto.
  - `RunCheck.outcome` maps these verdicts onto the closed `outcome.Outcome` enum.
- **Judge hook.** The judge is an injectable callable. It is called only when the deterministic verdict is VERIFIED, so it cannot grant a pass. If it returns `False` or a non-empty string, VERIFIED becomes UNVERIFIED. If it raises, the run is also downgraded.
- **No values in reasons.** Reasons and records never contain observed values. Items are named by label, and evidence holds counts and digests only. A test covers this.
- **Pack adapter.** `pack_hook({source_id: induce_files(taught_files)})` checks a source's filed documents. It counts files by the same rule as the pack's `_good()`: `type_ok` and not `flagged`. A stray download that the document verifier already set aside, such as last month's statement, therefore cannot refute a correct month. The pack accepts *at least* `expected.count` good files, so the hook relaxes `files_arrived` to "at least the taught count". A month with more good documents than the demo is not FAILED, and a double download is still FAILED through `files_distinct`. A direct `check()` keeps the exact count.

`scaffold/agent/desktop/pack/run.py` (minimal opt-in hook)

- **New parameter.** `run_month(..., expect=hook)` takes a hook. After each source is processed, `expect_gate` runs it on entries with a collected_* status.
- **It can only downgrade.**
  - `unverified` turns collected_verified into collected_unverified.
  - `failed` sets the status to `failed`, with the refutation as the reason.
  - If the hook returns `None`, the status is left alone.
  - If the hook raises, collected_verified becomes collected_unverified.
- **Off by default.** Without `expect=`, behaviour is unchanged.

## Benchmark: `scripts/verify_bench.py`

```
PYTHONPATH=. python3 scripts/verify_bench.py [--family F] [--limit N] [--crawl 0|1|2] [--judge banner] [--json out.json]
```

The benchmark runs in process with no network, no model calls and no cost. It takes about 30 to 50 seconds for all 460 train tasks.

**Demo.** For each factory task, the scripted oracle runs once and an Expectation is induced from it. The readback covers every nav view, every view the demo visited, and `<main>` links from those views `--crawl` levels deep. Each view is read in a fresh session over a copy of the backend state, so unsaved drafts, filters, tabs and popups from the acting session are never read back. The checker sees only rendered page atoms (row text, list items, paragraphs, field values, pressed or expanded buttons, banners). It never sees `app.state`.

**Cases.** Each case runs on a fresh instance:

| Case | What happens |
|---|---|
| `success` | The oracle is replayed unchanged |
| `success_detour` | The oracle runs, then visits /help and comes back before done |
| `partial` | The last state-changing step is skipped |
| `wrong_value` | The first fill gets a wrong value, or a select gets a different option |
| `duplicate` | The last state-changing step is done twice |
| `extra_deletion` | After the run, one unrelated item is removed from the backend |
| `banner_no_effect` | The success banner is on screen but the backend is reverted |
| `wrong_answer` | Answer tasks: done() is called without the value |
| `no_pause` | Pause tasks: done instead of needs_user |

**Ground truth.** Truth comes from `scaffold/rl/factory/verify.py` `score()`, the oracle verifier.

## Measured (2026-10-24, train split, 460 tasks, 2331 scored cases)

Settings: `--crawl 1`, which is the default.

| Metric | Value |
|---|---|
| **false-VERIFIED rate** (VERIFIED among the 1266 truth-fail cases) | **2.13%** (27) |
| false-VERIFIED on faults any page readback can see (1168 cases) | **0.0%** |
| true-VERIFIED rate (truth-success) | 91.8% (978 / 1065) |
| false-FAILED rate (truth-success) | 2.9% (31) |
| truth-fail cases caught as FAILED | 91.7% |

Confusion, truth by verdict:

| | verified | unverified | failed |
|---|---|---|---|
| success | 978 | 56 | 31 |
| fail | 27 | 78 | 1161 |

Results by case:

| Case | n | verified | unverified | failed | n/a |
|---|---|---|---|---|---|
| success | 460 | 432 | 28 | 0 | 0 |
| success_detour | 460 | 432 | 28 | 0 | 0 |
| partial | 314 | 0 | 28 | 286 | 146 |
| wrong_value | 117 (3 truth-ok) | 6 | 11 | 100 | 343 |
| duplicate | 179 (142 truth-ok) | 114 | 1 | 64 | 281 |
| extra_deletion | 341 | 21 | 10 | 310 | 119 |
| banner_no_effect | 314 | 0 | 28 | 286 | 146 |
| wrong_answer | 101 | 0 | 0 | 101 | 359 |
| no_pause | 45 | 0 | 0 | 45 | 415 |

**Where the 27 false VERIFIED come from.** All 27 are faults the readback cannot see. The run's pages are identical to the clean replay's (`fault_visible=false`):

- docs_hub articles that are linked only from search results, which need a POST (21);
- shop order promo and total, which no page shows (3);
- the prefs `pro` flag, set by a second Apply click and never rendered (3).

**Effect of readback depth:**
- `--crawl 0` (nav views and visited views only): 5.4% false-VERIFIED.
- `--crawl 2`: same as crawl 1.

**Judge veto.** With `--judge banner` (a toy deterministic judge that vetoes act runs with no confirmation banner), true-VERIFIED drops to 65.0%, because the detour runs lose the banner. False-VERIFIED stays at 2.13%. This shows the judge can only remove VERIFIED stamps, and a banner-based judge does not catch invisible faults.

**False FAILED.** The 31 false-FAILED runs are duplicates whose second click showed an error banner while the backend stayed correct. This is a conservative outcome.

**Unobservable demos.** 28 tasks are UNVERIFIED on every run: mail send, reply and draft write to stores that no view shows.

## Limits and what is stubbed

- **Readback coverage is the ceiling.** A fault outside everything the adapter reads back cannot be caught. To make that visible, the benchmark reports `fault_visible` and splits the false-VERIFIED rate on it. A product adapter has to choose its readback views: the demo's views plus list or pagination links. Crawling links is safe in the factory, where GET requests never write, but is not automatically safe on real sites.
- **The demo and the replay are the same task**, with the same seeded data, so expectations are literal values. Parameterising them for next month's data, with different values but the same shape, is not built. For pack files, count, type and size do generalise.
- **The factory adapter lives inside the benchmark script** (`atoms`, `Session.readback`). There is no live adapter yet for the product's AX tree or `web_tools.Page`. `expect.py` itself is substrate-agnostic.
- **The judge is a hook only.** No model judge is wired in. `banner_judge` is a deterministic stand-in.
- **The pack hook is tested with fake runners only,** not on a live portal.
- **Exam integrity.** Only the factory `train` split is used. The `ood` families (wizard, files_tree, calendar) and MiniWoB are never touched. `test_bench_exam_integrity` asserts this. The output JSON carries `"not_training_data": true`.

## Tests

```
PYTHONPATH=.:tests ~/.awos/rl-venv/bin/python -m pytest tests/test_effects_expect.py tests/rl -q
```

- `tests/test_effects_expect.py`: 21 passed.
- `tests/rl`: 179 passed, 7 skipped.
- The pack and effects suites still pass.
