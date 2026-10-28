# WP7 third-party code and designs (effect verifier + secrets broker)

Paths: `scaffold/agent/desktop/effects/`, `scaffold/agent/desktop/secrets.py`,
`scaffold/agent/desktop/enforce.py`, `scaffold/agent/secret_scan.py`.

## Code reused

| Source | Licence | Where in AWOS | How |
|---|---|---|---|
| OpenAdapt Flow `openadapt_flow/runtime/effects/readback_values.py` (openadapt-flow 2.2.0, repo commit `b07ba6c94cc6b0a18fbf08b0772f5c525c156211`) | MIT, Copyright (c) 2026 OpenAdapt.AI (MLDSAI Inc.) | `effects/readback_values.py` | Vendored unchanged, with a provenance header |
| OpenAdapt Flow `runtime/effects/effect.py`, `_common.py`, `verification.py`, `check_level.py` (via `openadapt_types/check_level.py`), `openadapt_types/execute.py` | MIT (same holder) | `effects/outcome.py`, `effects/file_field.py` | Ported and rewritten without pydantic: verdict triple (CONFIRMED / REFUTED / INDETERMINATE), check-level ladder, the closed outcome enum (VERIFIED / HALTED_BEFORE_EFFECT / RECONCILIATION_REQUIRED / COMPLETED_UNVERIFIED), field_equals "exactly once" + collateral-loss judging, "unreadable means halt" |
| OpenAdapt Flow `runtime/effects/file_arrival.py` | MIT (same holder) | `effects/file_arrival.py` | Ported for local folders only (the SFTP transport was dropped) |
| OpenAdapt Flow `runtime/effects/compensation.py` | MIT (same holder) | `effects/compensation.py` | Ported: reconcile-or-escalate, ReconciliationTask with digests only, "only a duplicate has a safe automatic undo". `FileRestore` (exact pre-image undo) is AWOS's own |
| OpenAdapt Flow `runtime/effects/onscreen.py`, `ui_readback.py`, `fresh_session.py` | MIT (same holder) | `effects/readback.py` | Design only: same-screen vs re-navigated strength, OCR-ambiguous reads never pass. No code copied |

The full MIT licence text for OpenAdapt is in
`scaffold/agent/desktop/effects/LICENSE-OpenAdapt`. Every adapted file names
its source and the licence in its header.

## Runtime dependency (optional, not vendored)

| Package | Licence | Use |
|---|---|---|
| jaraco `keyring` | MIT (SPDX in pyproject; the repo has no LICENSE file, so the notice comes from the built wheel: Copyright Jason R. Coombs et al.) | `secrets.KeyringProvider` imports it lazily as the macOS Keychain layer. It is not installed by default and not required: the provider takes any injected backend, and the tests use an in-memory one so the real Keychain is never touched. |

## Designs learned (no code)

These come from the REA product-2 reports (`~/Projects/REA/product2/*/report.md`)
and BLUEPRINT.md, not from the products' source:

- 1Password `op://` references resolved at execution time, and Inbox Zero's
  "credentials never enter the browsing context": the handle-at-the-sink model
  (`{{secret:name}}`).
- macOS Keychain report: avoid `security find-generic-password -w` at runtime
  (it prints the secret to stdout); the policy already classes it as CREDENTIAL.
- The new `secret_scan` key-format rules (Stripe test, Groq, xAI, Google OAuth,
  1Password service account, Infisical service token, Vault, Doppler, Linear,
  Shopify, DigitalOcean, PyPI, Postman, Notion, Supabase, Replicate, Databricks,
  and the testbed `fk_live_`) were written from the vendors' public key prefixes.
  No scanner rule set was copied.

## Not used

- `bitwarden-clients` (Bitwarden licence + GPL), `bitwarden-mcp-server` and
  `bitwarden-map-the-web` (GPL-3.0), `bitwarden-sdk-sm` (proprietary). Nothing
  from these was read for code or copied.
