# WP8 third-party record: micro-agent, web_tools

Scope: `scaffold/agent/desktop/micro_agent.py`, `scaffold/agent/desktop/web_tools.py`,
the `mode="micro"` switch in `scaffold/agent/desktop/cu_agent.py`, and `tests/test_micro_agent.py`.

## Code copied or vendored

**None.** All WP8 code was written for AWOS. It uses only the Python stdlib
(`html.parser`, `urllib`, `http.cookiejar`, `tomllib`). The optional cloud client
imports `anthropic` lazily, and that SDK is already an AWOS dependency.
No file needs a NOTICE entry for WP8.

## Designs consulted (ideas only, re-implemented)

| Source | Licence | Path read | What we took (as a design idea) |
|---|---|---|---|
| nanobrowser v0.3.0 | Apache-2.0 | `~/Projects/REA/product2/nanobrowser/report.md` (outline, refs, write gate) | One-line outline per control (`[n] role "name"`), interactive roles only get numbers, a deterministic write gate in front of submit/delete, and refs that fail closed (ours: `IndexGrounder` refuses missing or ambiguous numbers) |
| agent-e | MIT | `~/Projects/REA/product2/agent-e/report.md` | AX-style distillation: role, label from `<label for>`/aria-label, inlined options for choices, a "dialog" marker on modal controls, and a short "what changed" note after each action |
| browser-use | MIT | `~/Projects/REA/product2/browser-use/report.md` | Secret placeholders that the model sees only as names, with values substituted at execution. Nothing was imported, so browser-use's PostHog telemetry (`ANONYMIZED_TELEMETRY` defaults to true) does not apply. If it is ever vendored, set `ANONYMIZED_TELEMETRY=false` and remove `telemetry/`. |
| stagehand v3 | MIT | `~/Projects/REA/product2/stagehand/report.md` | `%var%` secret variables (ours: `{{secret:name}}`), one strict structured-output call per action, and the runtime (not the model) owning the number-to-element mapping |
| Inbox Zero unsubscribe worker | AGPL-3.0 + extra terms | `~/Projects/REA/product2/inbox-zero/report.md` §2.7 only | **Design only, clean-room.** No source file was opened while writing WP8. We took the contract shape: numbered controls, a closed verb set with `needs_user`, type-checked actions, a 12-step / 120 s cap, and success only on an explicit on-page acknowledgement. Our prompts, verbs, field names and checks are our own. |
| BLUEPRINT Critic item 8 | (our doc) | `~/Projects/REA/product2/BLUEPRINT.md` | Creating a key is a write that needs approval. It must be idempotent (an existing AWOS-named key leads to `needs_user`), MFA leads to `needs_user`, and the key page must be masked for the model |

## Not used

- AGPL code (inbox-zero, workflow-use, skyvern, automa): none.
- browser-use, stagehand and nanobrowser runtimes: not imported. A Chrome/CDP
  or AX backend can replace `LoopbackBrowser` later. It needs the same
  `open / click / fill / select / read_raw` surface plus a `page` attribute.
