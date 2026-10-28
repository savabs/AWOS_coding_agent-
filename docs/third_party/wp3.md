# Third-party code in WP3 (mail adapter)

## Ported (code derived from a third-party source)

| AWOS file | Source | Licence | What was ported |
|---|---|---|---|
| `scaffold/agent/desktop/mail_text.py` | Zero (Mail-0/Zero), commit 64c5480: `apps/server/src/thread-workflow-utils/workflow-utils.ts` | MIT, Copyright (c) 2025 Zero Email | `htmlToText`, `escapeXml`, `messageToXML`, `getParticipants`, `threadToXML`, rewritten in Python (stdlib `html.parser` in place of cheerio) and extended |
| `scaffold/agent/desktop/mail_text.py` | Zero, same commit: `apps/server/src/lib/email-processor.ts` | MIT, Copyright (c) 2025 Zero Email | tracking-pixel rule (img width/height 0 or 1), blocking remote images except `cid:`, hiding preheaders, the quoted-text selectors |

The MIT notice is reproduced in full in the `mail_text.py` module docstring.

Design reference only, no code copied: Zero's `apps/server/src/lib/driver/types.ts` (the `MailManager` interface) informed the method set of the `MailAdapter` protocol in `mail_adapter.py`.

None of the 7 Zero files with the restricted header was opened for porting or copied: `workflow-engine.ts`, `workflow-functions.ts`, `lib/analyze/interests.ts`, `routes/agent/index.ts`, `routes/agent/mcp.ts`, `workflows/sync-threads-workflow.ts`, `workflows/sync-threads-coordinator-workflow.ts`. No AGPL code was used (no inbox-zero code).

## Optional runtime dependency (not vendored)

| Package | Licence | Use |
|---|---|---|
| `keyring` (jaraco) | MIT | `mail_imap.keychain_password()` reads the Gmail app password from the macOS Keychain. It is imported only when it is needed. Without it, AWOS raises `MailCredentialError` with install instructions. |

Everything else in WP3 uses only the Python standard library (`imaplib`, `ssl`, `email`, `html.parser`, `socketserver` for the test server).
