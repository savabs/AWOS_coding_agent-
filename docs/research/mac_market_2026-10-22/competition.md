# Competitor map for AWOS (research, 2026-10-22)

Research agent, web sources.
- Many 2026 claims come from secondary or aggregator pages and are marked "unverified" where no primary source was found.
- (K) means prior knowledge that was not re-checked.

## Competitors

| Product | What it is | Local / cloud | Unattended or scheduled | Learns from a demo | Price / funding | Weakness AWOS can exploit |
|---|---|---|---|---|---|---|
| **Perplexity Personal Computer** | Mac agent for local files and apps; Mac mini recommended for 24/7 use | Hybrid; **fully local mode since 2026-10-20** (Qwen 3.6) | Always-on; scheduled tasks (unverified) | Not stated | Pro $20 / Max $200 | Local mode recommends 36GB+ RAM; browsing goes to the cloud; no demo replay; no accountant workflow |
| **OpenAI ChatGPT Work + Codex (Mac)** | Desktop computer use; **Record & Replay** (2026-06-18) | Cloud | Scheduled, triggered and monitor tasks | **Yes**, but replay runs through the model | Free and paid plans; bought Sky (the Workflow/Shortcuts team) 2025-10-23 | Model calls on every replay; screen leaves the device; Replay excluded from EEA/UK/CH at launch; Atlas shut down 2026-08-09 |
| **Anthropic Claude (Cowork computer use, Claude in Chrome)** | Desktop computer use; Chrome records a workflow into a shortcut and schedules it | Cloud | Chrome shortcuts need the Mac awake; Cowork scheduled tasks run in the cloud since 2026-10-06 | Yes (Chrome), replay through the model | Pro / Max | Cloud only; no sandbox; model calls on every run; **finance apps blocked by default** |
| **Simular (Sai, Agent S)** | Operates the whole Mac; open-source framework | Cloud models | Always-on | Not confirmed | ~$20–500/mo; $21.5M Series A (2025-12) | Cloud; pricing in flux; aiming at enterprise and insurance |
| **Manus My Computer** (Meta) | Desktop agent, mostly command line and files | Cloud plus local execution | Partly | No | Credits | Weak at controlling apps on screen; data goes to Meta's cloud |
| **Apple Siri AI (macOS 27) + Shortcuts / App Intents** | On-screen awareness, App Intent actions | On-device + Private Cloud Compute | Shortcuts automations (K) | No | Free | Only works with apps that declare intents; can't drive arbitrary bank websites |
| **Comet / Dia / Genspark / Fellou / Opera Neon** | AI browsers | Mostly cloud | Comet background agents on Max | Dia skills are prompts, not demos | Free–$200 | Browser only; can't use Mac apps (Finder, Numbers, Mail) |
| **Highlight, Littlebird** | Context and memory assistants | Cloud | No | No | ~$20/mo; Littlebird raised $11M | Read the screen but don't act |
| **Raycast 2.0** | Launcher with agents and skills | Cloud | No | No | ~$8/mo | Can't use the computer for you |
| **Microsoft Copilot (Mac)** | Agents inside Microsoft 365 apps | Cloud | No | No | M365 licence | Office apps only; Power Automate Desktop isn't on Mac (K) |
| **Keyboard Maestro / Hazel / BetterTouchTool / Alfred / Shortcuts** | Fixed-step Mac automation | Local | **Yes** | Keyboard Maestro has a recorder, but it breaks easily (K) | KM $36, Hazel ~$42 (K) | Breaks when a site changes; can't repair itself; no logins/2FA, no checks. **The incumbent for fixed-step replay** |
| **UiPath on macOS** | Enterprise RPA | Cloud orchestrator | Yes | Recorder (K) | Enterprise pricing | Mac is a second-class platform; too heavy for freelancers |
| **GetMyInvoices** | Fetches invoices from 10,000+ portals, OCR, filing | Cloud (EU) | Yes | No (fixed connectors) | €19–179/mo | Misses smaller portals; keeps your credentials in its cloud; per-portal caps |
| **Dext / Hubdoc / AutoEntry** | Receipt capture and supplier fetch into QBO/Xero | Cloud | Yes | No | Dext ~$25/mo+; Hubdoc ~$12 | Hubdoc reportedly dropped general bank fetching in 2022; fetches break; small banks not covered |
| **Expensify / Zoho / Ramp / Brex** | Receipts from email, cards and SMS | Cloud | Yes | No | Bundled | Don't log into portals to fetch statements |
| **Plaid-based statement tools** | Statement data through an API (K) | Cloud | Yes | No | Per call | Only Plaid-covered banks; often no official PDF |
| **Skyvern / Browser Use / Browserbase Director** | Cloud browser agents; Director turns prompts into scripts | Cloud | Yes | Director: prompt to script | Skyvern $29/$149; Browserbase $40M Series B | Developer tools; credentials kept in their cloud; no Mac filing |
| **Taprun** | Open-source portable browser-plan format | Local or edge | Yes | Converts existing scripts | Free | A building block, not a product |

## Where it's crowded

- **"An agent that uses your computer"**: OpenAI, Anthropic, Perplexity, Simular, Manus and Meta all have one.
- **Record a demo, then replay**: no longer novel. Codex Record & Replay (June 2026) and Claude in Chrome's scheduled shortcuts both ship it. An open-source macOS accessibility-tree recorder appeared on 2026-10-18.
- **"Local and private"**: Perplexity shipped a fully local mode on 2026-10-20, using the same Qwen model family as AWOS.
- **Invoice fetching from big vendors**: GetMyInvoices and Dext already do it cheaply.
- **AI browsers**: six or more, mostly free.

## Where AWOS still has a real gap (stated skeptically)

1. **Replay with zero model calls, using a local model only when something breaks, then re-checking.** Codex and Claude replay through the model on every run; Keyboard Maestro is fixed-step but can't repair itself. Nobody found pairs the two.
   - Caveat: Browserbase Director compiles prompts into scripts, and OpenAI could add caching.
2. **Credentials and statements never leave the Mac.** GetMyInvoices, Skyvern and Hubdoc keep credentials in their cloud, and Claude blocks finance apps by default. Compliance matters here (GLBA, IRC §7216).
   - Caveat: Perplexity's local browsing still goes through the cloud for now, but it could change.
3. **Smaller portals plus Mac-side filing and checks.** Small banks, credit unions and niche SaaS billing pages, followed by renaming, filing in Finder, checking the period and amount, and a "missing documents" report.
4. **Overnight runs in a sandbox VM on 16–24 GB Macs.** Claude needs the Mac awake and has no sandbox; Perplexity's local mode recommends 36 GB or more.
   - Caveat: Perplexity runs execution in a sandbox and pushes always-on Mac minis.

**Be skeptical:** MFA codes, SMS and bot detection block unattended runs for everyone. Realistically the gap is "semi-unattended, with one MFA tap on your phone", not fully overnight.

## Biggest threats in the next 12 months

1. **Perplexity** adds scheduling, local browsing and recorded skills to its local mode. Most likely.
2. **OpenAI** makes Record & Replay schedulable and caches replays; it now has the Sky (Workflow/Shortcuts) team.
3. **Anthropic** relaxes the finance-app block or adds a sandbox VM.
4. **Apple** adds a finance App Intents schema, or lets Siri act on websites. Lower risk within 12 months.
5. **GetMyInvoices or Dext** add an AI agent for portals they don't cover. They already have the buyers.

## Key sources

- perplexity.ai/hub/blog/perplexity-computer-now-runs-fully-local-on-mac (2026-10-20)
- macrumors.com (2026-04-16); techcrunch.com (2026-05-07)
- eesel.ai (Codex Record & Replay)
- learn.chatgpt.com/docs/computer-use; openai.com/chatgpt-work
- en.wikipedia.org/wiki/ChatGPT_Atlas
- openai.com (Sky acquisition, 2025-10-23)
- support.claude.com (Cowork computer use; Claude in Chrome)
- techcrunch.com (Simular, 2025-12-02)
- 9to5mac.com (Manus My Computer, 2026-03-17)
- techcrunch.com (macOS 27 Siri, 2026-09-14); developer.apple.com WWDC26 session 382
- techcrunch.com (Littlebird, 2026-03-23)
- manual.raycast.com
- composite.com, toolradar.com, testingcatalog.com (AI browsers)
- fastlancer.org (GetMyInvoices); sparkreceipt.com (Hubdoc)
- costbench.com (Skyvern); siliconangle.com (Browserbase); browser-use.com
- docs.uipath.com (Assistant on Mac)
- github.com/reacher-z/Awesome-CUA PR #1020
- jsr.io/@taprun
