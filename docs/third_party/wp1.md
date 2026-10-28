# WP1 (triage cascade): third-party code and designs

## Code vendored (ported)

| What | From | Licence | Where in AWOS |
|---|---|---|---|
| Automated-sender regexes: sender address `(no-?reply\|donotreply\|do-not-reply)`, subject `(newsletter\|unsubscribe\|notification)`, body `(do not reply\|this is an automated)`, and the `dontReplyTo` address set | Zero (Mail-0/Zero), `apps/server/src/thread-workflow-utils/index.ts` (`shouldGenerateDraft`), commit `64c5480c341750578da0746f2db9ad84da686334` (local clone `~/Projects/REA/product2/_src/mail0-zero`) | MIT, Copyright (c) 2025 Zero Email | `scaffold/agent/desktop/triage_cascade.py`, section "L0" (`ZERO_*` constants). The full MIT notice is reproduced in the source next to the port. |

That is the only third-party code. We did not use any of the Zero files that carry restricted headers (`workflow-functions.ts` and the others listed in BLUEPRINT §2). We also did not port Zero's `analyzeEmailIntent`. AWOS wrote everything else in the module: the injection detector, the static L2 matchers, the precedence, the abstain logic and the logprob extraction.

## Design only (no code, no prompts, no fixtures copied)

| Product | Licence | What we took as an idea |
|---|---|---|
| Inbox Zero | AGPL-3.0 with extra commercial terms; `ee/` is proprietary | A cascade that stops at the first confident layer. Calibrated choice with a NONE option, a minimum confidence and a dead-heat margin. Bulk classes summed before comparing. Starting thresholds 0.3 / 0.1 / 0.75 (to be re-tuned with `scripts/triage_calibration_probe.py`). Precedence that puts the user first. |
| Superhuman | proprietary | Never auto-archive anyone the user has written to; the same-domain exception; archived mail must stay findable (our `review_queue_<phase>.json`). |
| SaneBox | proprietary | Precedence: explicit subject rule > domain rule > learned training > model default. Sent-folder contacts as a strong signal of importance. When unsure, fail open (mail stays in the inbox). |

Sources: the reports in `~/Projects/REA/product2/{inbox-zero,superhuman-ai,sanebox,mail0-zero}/report.md` and `~/Projects/REA/product2/BLUEPRINT.md` (§1 UC1, §2, §3, §4 WP1, Critic items 1 and 9).
