# mw_repair: repair layer for taught skills, plus a wrong-repair benchmark

Date: 2026-10-24. Branch `claude/mw-repair`. Research: `docs/research/rl_cua_2026-10-24/repair_verify.md` (sections 0, 1 and 4).

## What it does

`scaffold/agent/desktop/repair/` runs when a replayed step's control is **not found** (or is ambiguous) by the strict role+name grounder:

1. **Rule tier, no model** (`similo.py`). This is a Similo-style fingerprint: role, accessible name, visible text, nearby label text (row or block), region/dialog context, ancestor path, position among same-role controls, neighbouring control names, and href. Each feature is scored and the scores are combined as a weighted mean. A candidate is accepted only when all of these hold:
   - score >= `ACCEPT` (0.72);
   - margin over the next name-alike candidate >= `MARGIN` (0.10);
   - the **name anchor** holds: token similarity >= 0.66. If the name was not unique when recorded, the label must also agree.

   Two more guards:
   - **Identity tokens.** A token with a digit (`D-305`, `INV-0912`) caps name similarity at 0.25 when it is missing, so the rule tier never picks the neighbouring row.
   - **No character-level credit across different words.** "Victor Kaya" vs "Victor Lee" scores 1/3.

   If the best score is below `FLOOR` (0.45), the answer is NOT_FOUND. Anything in between is AMBIGUOUS.
2. **Model tier** (`model.py`). Runs only on AMBIGUOUS results. The model sees a shortlist of at most 5 candidates and replies `PICK n: reason` or `NOT_FOUND: reason`; a GBNF grammar constrains the reply. An unparseable reply, an out-of-range pick, or a dead server all count as an abstain. The model is any injectable callable. The default, `LocalPicker`, is an OpenAI-compatible llama-server on 127.0.0.1 and refuses non-loopback URLs, so there is no paid path.
3. **Safety gate** (`core.py`). Applied after either tier. A candidate whose name or context matches `micro_agent._DESTRUCTIVE_RE` (imported as is), or the local `SEND_RE` (send / submit / confirm / place order / checkout / sign / approve ...), is never repaired onto unless `approve(request)` returns True. With no approver the result is `needs_approval`, which halts as `risk_escalation`.
4. **Recording** (`SkillRepair`). The first clean strict grounding of a target with no fingerprint stores its fingerprint (enrolment). For a repair:
   - the new name goes into `target.aliases`, so the next replay is strict and model-free;
   - a role drift goes into `target.repairs` and is tried first next time (rung `repair_cache`);
   - each record carries `{from, to, tier, score, margin, reason, at}`.

   Records are written as a **new skill version only after a RUN_OK**. A failed run drops them. Steps with a period `name_template` (pack documents for one month) are never repaired.
5. **Absence-tolerant targets** (review fix). `SkillRepair.begin(program)` collects the targets of `optional` ops and the handlers' guard controls. These may legitimately be absent: the runner skips a missing optional step, and a missing guard means the handler does not fire. They get the rule tier only, with no model call. Anything short of a confident rule repair returns the strict grounder's own `missing`. Before this fix, an unsure rule tier turned a plain `missing` into `ambiguous`, so an optional "Close what's new" step halted as `ambiguous_match` instead of being skipped, and with a picker the model could click an unrelated header button.
6. **No typed values in stored labels** (review fix). The nearby label is built from the parent/grandparent AX text. It no longer reads the `value` of editable controls (text fields, combo boxes, pop-ups, search fields, ...), only their title/description, and it is passed through `web_tools.mask_secrets`. Before, a member number typed into a login form ended up in the enrolled fingerprint of the "Sign in" button and so in `skills/<id>/vN.json`.

Hook into `pack/runner.py` (opt-in, about 15 lines): `LiveRunner(repair=SkillRepair(Repairer(picker=LocalPicker())))`, or `SkillRepair()` for rule-only. `detail["repairs"]` reports the events, the model calls and what was written. Without `repair=`, behaviour is unchanged.

## How to run

```
PYTHONPATH=.:tests ~/.awos/rl-venv/bin/python -m pytest tests/test_repair.py tests/test_repair_runner.py -q
~/.awos/rl-venv/bin/python scripts/repair_bench.py --split test [--out r.json]
~/.awos/rl-venv/bin/python scripts/repair_bench.py --split test --model local --dry-run   # counts only
~/.awos/rl-venv/bin/python scripts/repair_bench.py --split test --model local --max-calls 300   # needs llama-server
```

How the benchmark builds its cases:
- Pages come from every oracle step of the **train-split** factory families, rendered through `scaffold/rl/factory` (import only).
- `repair/mutate.py` tags the target, mutates the parsed tree, serialises it back and re-parses it, so the ground truth is exact.
- Each step runs strict-first, as the product does.

How the split works:
- Thresholds were set on `--split dev` (contacts, prefs, records, mail): I swept `ANCHOR_NAME` over {0.5, 0.6, 0.66, 0.75} and `ACCEPT` over {0.65, 0.72, 0.8}.
- The headline numbers below are on `--split test` (docs_hub, assign, shop, checkpoints), which was not used for tuning.

## Measured (test split, 505 cases × 7 mutations, 3447 mutated pages; about 45 s on a laptop)

Re-find accuracy (target still present):

| arm | rename | move | wrap | role | decoy | ALL |
|---|---|---|---|---|---|---|
| strict (no repair) | 0.0% | 100% | 99.4% | 0.0% | 49.7% | 51.6% |
| rule | 68.5% | 100% | 100% | 94.7% | 79.6% | 88.3% |
| rule + oracle model | 96.0% | 100% | 100% | 94.7% | 97.2% | 97.7% |
| rule + noisy model (10% wrong) | 92.5% | 100% | 100% | 94.7% | 96.6% | 96.8% |
| rule + greedy model (never abstains) | 96.0% | 100% | 100% | 94.7% | 92.1% | 96.6% |

Wrong-repair rate (wrong control / cases; target <= 2%) and abstain correctness on deletions:

| arm | wrong ALL | wrong delete | wrong delete_decoy | abstain-correct delete | abstain-correct delete_decoy |
|---|---|---|---|---|---|
| strict | 0.0% | 0.0% | 0.0% | 100% | 100% |
| rule | **0.0%** | 0.0% | 0.0% | 100% | 100% |
| rule + oracle | 0.0% | 0.0% | 0.0% | 100% | 100% |
| rule + noisy10 | 3.1% | 7.5% | 12.3% | 92.5% | 87.7% |
| rule + greedy | 23.7% | 61.0% | 95.6% | 39.0% | 4.4% |

On the dev split the rule tier has 0.1% wrong (one delete_decoy case: "Your order has shipped" was repaired onto "Follow order has shipped"). The model tier is called 1058 times on test (about 31% of mutated pages), almost all on renames and deletions. Prompts average about 650 characters.

How to read this:
- **Rule-only meets the 2% target** with no model, but it recovers only 68% of renames.
- **Most of the remaining gain needs the model** (oracle: 96% of renames).
- **The model must be good at NOT_FOUND.** A model that is wrong 10% of the time pushes the total wrong rate to 3.1% and fails the target. A confident healer that never abstains reproduces the literature's false-heal failure (24% wrong; 96% wrong on delete_decoy).
- So the local model needs a measured abstain/pick error of about 5% or less on these shortlists before it is turned on. This benchmark is the gate for that, and its cases (train families only) are the natural SFT data for the repair specialist.

## Limits, and what is untested or stubbed

- **No real model was run.** `rule+local` exists, but no llama-server was up. The model rows come from mock pickers (oracle / noisy / greedy).
- **Synthetic mutations on factory HTML, through a pure-Python AX reader.** These are not real site version pairs (Wayback-style), and not Chromium AX trees. The rename word table differs from the matcher's synonym table, but the two overlap partly.
- **The decoy design is a judgement call.** A decoy is a *different* thing (another id, another entity's word, another action verb), never the same words plus a qualifier, because that is indistinguishable from a rename. Those semantic cases are left to the model tier.
- **Live fingerprints are enrolled at replay time.** `teach.py`, which is not my path, records role+name only, so a skill gets its full fingerprint on its first clean replay with the hook. Before that, repairs are name-only and conservative. The live label/ancestor extraction (`page_candidates`) is tested on the fake Safari AX runtime only, not on real Safari.
- **Commit happens on RUN_OK from the runner**, not after the pack core's file verification (that is outside runner.py). There are no tier-2 overlay rules and no `PRE_STEP` (open menu) answers.
- **A repair approved once becomes an alias.** Later replays then match it strictly, with no new approval.
- **Taught password fields are not repairable.** `target_role` maps AXSecureTextField to `password`, while teach pages label it `textbox`, so those steps abstain. This is safe.
- **Absence-tolerant targets are keyed by `id()`** of the program's target dicts, so the hook works only when `begin(program)` gets the same program object the runner grounds. `LiveRunner` does this. A caller that rebuilds the target dicts gets the old behaviour, where repair applies to every step.
- **Review fixes are tested at the hook level** (`tests/test_repair_review.py`: an optional step, a handler guard, a confident repair still allowed, label privacy, and a saved skill). There is no end-to-end optional-step run through the fake Safari portal. Bench numbers are unchanged after the fixes (rerun on the test split: rule 88.3% with 0.0% wrong; noisy10 3.1% wrong).
- **Exam integrity.** `collect` refuses the ood families (wizard, files_tree, calendar) and asserts split == train; MiniWoB is not touched. Both are tested in `tests/test_repair.py`.
