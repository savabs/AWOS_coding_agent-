# WP5 third-party notices: skill generaliser + halt → teach

Files covered: `testbeds/teach_once/induction.py`, `testbeds/teach_once/skill_runner.py`,
and the integration hook in `testbeds/teach_once/compiler.py`.

## OpenAdapt flow (MIT): algorithms ported, no code copied

Source read: `~/Projects/REA/product2/_src/openadapt/packages/flow/openadapt_flow/`.
The modules depend on flow's pydantic IR (`ir.py`: `Workflow`, `ProgramGraph`, `Step`,
`Anchor`, ...), so nothing could be vendored as-is. The algorithms below were rewritten
against this testbed's `awos.demo_trace/1` trace and `awos.skill/1` skill format. The
notice is kept here and in the `induction.py` module header.

| OpenAdapt source | What was ported | Where in AWOS |
|---|---|---|
| `compiler/induction.py` (`_align`, `_mark_order_splits`, optional/branch handling, `Uncertainty` + `RecordNext`) | Aligns several demos with `difflib.SequenceMatcher` over value-free step signatures. Equal blocks are merged. An inserted or deleted step that is non-consequential becomes optional. If that step is consequential, or a replace divergence appears, induction **refuses** and returns a scenario-level "record another example" prompt. A value that varies with nothing to explain it is also refused, never guessed. | `induction._merge_ops`, `_merge_programs`, `_unc`, `induce` |
| `compiler/param_confirm.py` | A single confirmation batch of field-label parameter proposals, with values masked except first and last character. Decisions are confirm / rename / secret / constant (+ `input`). Fail-closed: an unconfirmed constant stays constant. | `induction.propose_params`, `apply_param_decisions` |
| `learning/teach.py`, `learning/halt_loop.py` | Halt report → fix demonstration → splice. The guard keys on text that is **new** on the screen where the run stopped, plus the control the operator clicked. The fix is spliced as a guarded handler that runs before the blocked step is retried. Destructive fixes are refused. A fix that does not explain the situation is refused, and vN is left unchanged. | `induction.teach`, `_teach_ui`, `_teach_item` |
| `learning/gate.py`, `learning/loop.py` (gate + held-out canary before promotion) | A candidate is promoted only after the gate passes. The demo must still pass with 0 model calls, and the canary must not halt again at the taught point. | `skill_runner.testbed_gate`, `induction.teach(gate=...)` |
| `learning/library.py` (`SkillLibrary`, `Provenance`, `SkillVersion`) | Versioned skills with an active pointer. Each new version records its parent sha and provenance entries (halt, fix-trace sha, change, gate summary). | `induction.SkillLibrary`, `teach` provenance |
| `runtime/resolver.py` (ladder; ambiguity is a typed halt, candidate order is never used) | The role+name ladder rungs exact → alias → normalized → unique token overlap. Two equal candidates are AMBIGUOUS and the run halts. | `skill_runner.ground_role_name`, `induction.fuzzy_pick` |

Licence text (from `packages/flow/LICENSE`):

```
MIT License

Copyright (c) 2026 OpenAdapt.AI (MLDSAI Inc.)

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
```

## workflow-use (AGPL-3.0): design only

Nothing from workflow-use was copied or read into these files. The only input was the
REA report (`~/Projects/REA/product2/workflow-use/report.md`), which suggested two ideas:

- an explicit, bounded `agent` step only where content genuinely changes per run
  (`loop.agent_step`, called only when no learned layout matches and an agent callback
  is plugged in, counted in `model_calls`)
- confidence tiers for parameters, here derived (observation-explained) vs. constant-field
  proposals

## AWOS-original parts (no third-party lineage)

- Per-item **layout profiles**. Each line skeleton keeps the value as a capture group and
  wildcards other value-like spans and the other parameters' values. CSV documents use a
  table-cell anchor.
- Typed validation, including refusal on dates that could be read two ways.
- Value explanation from what the user saw (`compiler._derive`).
- Aggregates, skip rules, idempotent effects, pagination following, the dialog-injection
  harness, and the testbed gate.
