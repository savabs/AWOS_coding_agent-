# WP2 (preference store v2 + implicit corrections): third-party code

**Vendored code: none.** Every line in these files was written for AWOS:
- `testbeds/email_triage/prefs.py`
- `testbeds/email_triage/feedback.py`
- `testbeds/email_triage/seed.py` (scoping case)
- `scaffold/agent/desktop/correction_capture.py`
- `tests/test_prefs_v2.py`

No file was copied or ported from `~/Projects/REA/product2/_src/`.

## Design-only references (concepts, no code)

These sources informed the design through the REA reports in `~/Projects/REA/product2/*/report.md` and
`BLUEPRINT.md`. No source file from them was opened while writing these modules.

| Source | Licence | What we took (concept only) |
|---|---|---|
| Inbox Zero | AGPL-3.0 + extra terms (**NO code**) | Implicit-correction capture that skips the agent's own actions; include/exclude learned patterns with provenance; a promotion gate (≥3 threads + model agreement); precedence (user first, then newest, exclude wins ties). Reimplemented clean-room with our own field names. |
| SaneBox (public help pages) | proprietary service | People keyed by address, services by sender + subject; rule precedence subject > domain > learned > default; moving mail back to the inbox is a strong signal. |
| Superhuman (public docs) | proprietary | Explicit correction scopes (thread / address / domain); domain scope only by explicit choice. |
| Shortwave (public docs) | proprietary | Deterministic rules first, model second (no code involved). |

If code from a permissive source (MIT, Apache-2.0 or BSD) is ever vendored into these paths, list it here with
its path, licence and upstream commit, and keep its original header.
