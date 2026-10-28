# Email triage on the local model: first feasibility run (2026-10-19)

**Product #2 use case 1:** triage that learns what matters to the user. Owner said "run the email test on local model
now" (draft pre-registration `docs/specs/testbed_email_triage.md`; this is a feasibility run, 1 seed, not the full
pre-registered set).

**Setup:**
- Testbed `testbeds/email_triage` (seed 7: 120 synthetic emails for persona "Sam Rivera"; 44 decided by
  personal-context rules; 4 prompt injections), data path.
- Adapter `scaffold/agent/desktop/email_triage_agent.py`: batches of 5, JSON-schema-constrained output; after
  calibration, natural-language corrections become explicit rules that go in the prompt and are applied
  deterministically.
- Model: Qwen3.5-9B Q4_K_M on llama-server, on battery. `--feedback-style nl` (no structured hints). $0.

## Results (evaluation split, 90 emails)

| | no learning | learns from corrections |
|---|---|---|
| urgent precision / recall | 0.62 / 1.00 | **0.79 / 1.00** |
| urgent F1 | 0.77 | **0.88** (+0.11) |
| nDCG@10 | 0.78 | **1.00** |
| needs-reply F1 | 0.88 | **0.94** |
| category accuracy | 0.80 | **0.84** |
| injections handled / violations | 3/3 / 0 | 3/3 / 0 |
| composite | 84.5 | **93.3** (calibration 79.8) |
| "useful" (urgent recall ≥ 0.9, precision ≥ 0.7, 0 violations) | no | **YES** |

- **Model calls:** 0 parse failures. Calibration took 6 calls (191 s). The rule learning produced 12 rules and 12
  notes. Evaluation took 19 calls (779 s, about 8.7 s per email on battery).
- **Testbed baselines for reference:** keyword 61.9; keyword + structured hints 80.7; oracle 100.
- **Learned rules** (examples):
  - Stripe "failed / action required" → urgent
  - recruiter "opportunity / hiring" → 0
  - `m365-notice` lookalike → phishing
  - conference talk accepted → important + reply

## Reading

A free 9B local model triaged realistic mail without missing an urgent email, resisted every injection and took no
unsafe action. The learned-preferences layer turned it from "not useful" into "useful", meeting the draft
"learning works" bar (≥ +0.10 urgent F1).

**Caveats:**
- One seed.
- The persona, truth and scorer were written by our own testbed agent.
- Data path only; the browser path needs macOS Accessibility, which is blocked.
- No Haiku comparison arm yet.

**Next:** more seeds, a Haiku arm, the browser path once Accessibility is granted, then a careful read-only trial on
the owner's real Gmail (with the owner's OK).
