# Would finance people let an AI agent use their logins? (2024–2026)

Date: 2026-10-23. Question: would finance teams, bookkeepers, CA firms and
small-business owners let an agent drive their own browser, using their own
logins, to download invoices and statements?

## Read this first: what the evidence covers

- **Reddit could not be reached.** reddit.com, old.reddit.com and its JSON
  endpoints all redirected to a block page. pullpush.io refused with "no free
  scraping for agents". Web searches with `site:reddit.com` returned zero Reddit
  threads across 12 queries. So this note has **no Reddit quotes**. The
  practitioner voice from r/Accounting, r/Bookkeeping and r/IndiaTax is still
  missing.
- The direct quotes below come from **Hacker News comments**, read through the
  Algolia API. Hacker News readers are technical, so the quotes are probably
  more fearful and more in favour of running locally than accountants would be.
- The other sources are security write-ups, bank and analyst statements, and
  guidance from accounting bodies and trade press. Vendor blogs were used only
  to show what the market looks like, never as evidence of what people think.
- "Sources" counts independent authors or outlets. Several comments in one
  Hacker News thread count as one source per commenter.
- Strength ratings: **STRONG** means 5 or more independent sources, including
  at least one primary source. **MEDIUM** means 3–4 sources. **WEAK** means 2
  or fewer, or only secondary sources.

---

## 1. What people fear, most common first

| # | Fear | Sources | Strength |
|---|------|---------|----------|
| 1 | Prompt injection: a web page tricks the agent into leaking data or acting | 11+ | STRONG |
| 2 | "AI touching the bank account": money moving without the owner | 8+ | STRONG |
| 3 | Credentials or page content going to a cloud vendor (leaks, training, a single point of failure) | 8 | STRONG |
| 4 | Terms of service, firm policy, audit trail and client confidentiality | 6 | MEDIUM-STRONG |
| 5 | Liability: who pays when the agent gets it wrong | 4 | MEDIUM |
| 6 | Reliability: hallucinations and wrong clicks | 3 | WEAK-MEDIUM |

**1. Prompt injection (STRONG).**
- Brave showed a hidden Reddit comment making Comet read a one-time code from
  the user's logged-in Gmail and post it publicly
  (brave.com/blog/comet-prompt-injection, 2025-08-20). LayerX followed with
  "CometJacking", and Brave published more Comet flaws (2025-10-21).
- Anthropic's own red-team tests of Claude for Chrome found a 23.6% attack
  success rate, falling to 11.2% with mitigations
  (anthropic.com/news/claude-for-chrome, 2025-08). Its support page says to use
  a browser profile with no banking logins and "strongly advises against" using
  it to manage financial accounts (support.claude.com/en/articles/12902428).
- Gartner (2025-12-01) told CISOs to "block all AI browsers for the
  foreseeable future". OpenAI's CISO has called prompt injection a "frontier,
  unsolved security problem" (The Register, 2025-10-23).
- Hacker News comments:
  - "you basically have to think of an agent as malicious ... drain your bank
    accounts" (news.ycombinator.com/item?id=45076859, 2025-08-30)
  - "Even if we reach 0.01% of attack success rate that could still nuke your
    bank account." (item?id=45036929, 2025-08-27)
- **Most relevant to statements:** a thread on "a €0.01 bank transfer could
  compromise a banking AI agent". The free-text description on a transaction
  becomes an instruction to the agent (item?id=48478233, 2026-06-10). **Statement
  pages themselves contain text an attacker controls**, so a read-only agent is
  still exposed.

**2. AI and the bank account (STRONG).**
- "I can't imagine accessing my bank account from Comet AI browser ... 'AI'
  and 'bank accounts' just don't go together in my view." (item?id=45005570,
  2025-08-24)
- "Who would accept a non-deterministic entity onto your computer to do said
  banking?" (item?id=45007679, 2025-08-24)
- "Even the most zealot openclaw users are not hooking their bank accounts
  into" agents (item?id=48668564, 2026-06-25)
- "in theory, clawdbot could drain my bank account. this makes a lot of people
  uncomfortable (me included, even now)" (item?id=47481305, 2026-03-22)
- The banks share this fear; see section 4.

**3. Credentials or data leaving the machine (STRONG).**
- "the only way I'm going to give an LLM access to a browser is if I'm running
  inference locally" (item?id=45031022, 2025-08-26)
- "if I log into my online banking, it could capture my transaction details
  ... and potentially use or train on that data" (item?id=45659236,
  2025-10-21, on ChatGPT Atlas)
- "You give your banking username and password directly to Plaid, and it
  keeps it ... I don't understand how anyone is OK with this."
  (item?id=47896037, 2026-04-24)
- "there's no world in which I just hand over my computer and browser with all
  my logins to an agent - just like I'd never do that with a human."
  (item?id=45685396, 2025-10-23)
- Gartner's main point is that AI sidebars send the content of open tabs to
  cloud back ends.

**4. Terms of service, firm policy, audit and confidentiality (MEDIUM-STRONG).**
- *The Firm* (2026-09-04, thefirm.media/articles/computer-use-ai-agents-accounting-firms):
  Xero's terms say not to let "any other person" use your credentials, and its
  developer terms bar bots that simulate users. An agent driving a browser
  "authenticates as a person", so its actions show up under a staff member's
  name.
- ICAEW (2024-02) said that putting client data into tools such as ChatGPT
  "is a potential breach" of confidentiality. In January 2026 the seven bodies
  behind the UK tax conduct guidance (PCRT) published guidance on AI tools
  that covers client confidentiality (icaew.com, Jan 2026).
- AICPA: secondary sources say its confidentiality rule (ET 1.700.001) treats
  sending client data to an AI platform as a disclosure that needs consent. I
  could not verify this against the AICPA text, so it is **unverified**.
- ICAI has no AI-specific circular that I could find. Commentators fall back on
  the ICAI Code of Ethics and the DPDP Act (suvit.io, Jan 2026).
- Indian taxpayer advice already says not to hand your income-tax portal
  password to your CA, and to register them as an intermediary instead
  (arthgyaan.com).

**5. Liability (MEDIUM).**
- Jamie Dimon (JPMorgan Q3 2026 call, ~2026-10-13): the bank won't let "just
  any outside agents into its systems", citing liability to clients.
- Bank of America has reportedly limited agent access while it works out who
  is liable for payments an AI authorises (FT, via aggregators; secondary).
- *The Firm*: "Responsibility does not move to the vendor because a model did
  the typing."

**6. Reliability (WEAK-MEDIUM).**
- 86% of CFOs report at least one case of inaccurate or hallucinated agent
  output (CFO Dive / Wakefield, 2026).
- *The Firm* notes that the newest computer-use model still fails about 27% of
  OSWorld tasks.

## 2. Does "local, read-only, asks first, verifiable log" answer these fears?

**Partly. It answers fears 3 and 4–5 well, and fear 1 only partly.**

Evidence that people value local or self-hosted (MEDIUM, 7 sources, all
technical users):
- 45031022, quoted above: local inference is the only way they would give an
  LLM browser access.
- "a local LLM would be the only way." (item?id=47896732, 2026-04-24)
- "probably the only option ... where you wouldn't need to provide your
  credentials to a third party" (item?id=47902120, 2026-04-25)
- "I don't trust 3rd party services with access to financial data ... I wish
  this was open source and self-hostable." (item?id=47899035, 2026-04-25)
- Requests for on-device LLMs in the browser, and for a small, open-source
  trusted base (item?id=45658708 and item?id=45659437, 2025-10-21).
- A local-first trading tool built because the author found it "hard to trust
  hosted ... platforms with ... API keys" (item?id=46756719, 2026-01-25).
- An incumbent is moving the same way: GetMyInvoices markets a desktop app
  that logs in through the user's own browser without saving passwords
  (vendor claim, not independently verified).

Approval before risky steps and a read-only design are valued (MEDIUM):
- Deloitte (2025-07): 59.7% of finance professionals trust agents only
  "within a defined framework", 2.7% trust them fully, and 19.9% trust them
  with nothing.
- An open-source "Permission Slip" auto-approves reads and requires a phone
  tap for "moving money" (item?id=47874972, 2026-04-23).
- Wish for a 1Password vault where the agent "asks to use" a password and the
  user approves (item?id=47505502, 2026-03-24).
- A finance-agent founder leads with "There's no transfer, pay, or send tool
  in the product" (item?id=47896530, 2026-04-24).

What local does **not** fix:
- **Prompt injection.** "A VM or traditional permissions doesn't really buy
  you anything" (item?id=45076859). Statement and invoice pages carry text an
  attacker can control (the €0.01 thread). Local inference with a weak small
  model may make injection *worse*.
- **Bank and portal terms of service.** A login is a login, wherever the model
  runs.
- **The CA or bookkeeper's duty to the client.** Running locally helps (no
  third-party processor), but the firm still needs client consent and a record
  of what happened.

## 3. What they would need to see first

| Requirement | Evidence | Strength |
|---|---|---|
| No ability to move money (read-only by construction) | Banks allow read-only only; Hubdoc says "no one ... can ... access money"; founder pitch 47896530; Deloitte | STRONG |
| Human approval on anything that writes, pays or sends | Deloitte 59.7%; Permission Slip; Anthropic support page; *The Firm* | MEDIUM-STRONG |
| Audit trail showing the agent separately from the human (a named agent identity) | *The Firm*; UK practice-software pitch for logging into government services without seeing the password | MEDIUM |
| Logins never handed to a third party / local | Section 2 | MEDIUM |
| Open source / self-hostable | 47899035, 45659437, 45658708 | WEAK-MEDIUM |
| SOC 2 | Incumbents carry it (Hubdoc is in scope of Xero's SOC 2 Type II); nobody asked for it in comments | WEAK (buyer-side assumption) |
| Insurance or a vendor guarantee | Nothing found | NONE |
| References from peer firms | Nothing found (this is the Reddit gap) | NONE |

## 4. Are banks and portals blocking agents?

**Yes for US big banks, for moving money (STRONG). For document portals there
are terms-of-service barriers but few reports of enforcement (MEDIUM).**
- **JPMorgan** (Oct 2026): blocks outside agents for retail customers, and
  third parties can't obtain login credentials. Read-only balances through
  Plaid-style connectors are still allowed. Source: earnings call plus
  aggregators (letsdatascience.com, kucoin.com).
- **Schwab** (CEO, ~2026-10-16): blocks unauthorised agents, including Meta's
  Muse, from moving client money, and sees attempts "tens or twenties of times
  a week". It plans read-only MCP access instead.
- **Citi and Bank of America**: reportedly bar third-party agents from logging
  in or transacting, while allowing read-only open-banking access (FT via
  PYMNTS and digg; secondary).
- **Amazon v. Perplexity** (filed ~2025-11-04, N.D. Cal.): Amazon alleges
  computer fraud and terms-of-service breach because Comet shopped without
  disclosing it was an agent and disguised itself as Chrome (Bloomberg).
- **Xero**: developer terms bar bots and browser extensions that simulate
  users, and Xero may suspend accounts it suspects (*The Firm*). No reports of
  actual suspensions found.
- **Bot detection:**
  - Cloudflare's "signed agents" category (2025-08) lets sites recognise
    declared agents and block undeclared ones.
  - A user worries banks will "force me to stop and use a 'normal' browser"
    (item?id=45242504, 2025-09-14).
  - "My bank assumes private browsing = hack attempt" (item?id=45005107).
  - ChatGPT agent clicked through "I am not a robot"
    (item?id=44716653, 2025-07-28), so detection by CAPTCHA alone is weak.
- **Precedent:** TD Bank sued Plaid in 2020 over credential screen-scraping
  and settled in 2021 (item?id=47897838).
- **India:** no reports found of GST or income-tax portal enforcement against
  agents. The DPDP Rules were notified in Nov 2025, and core fiduciary duties
  apply from ~13 May 2027 (Bar & Bench). Under Rule 8(3) the fiduciary must
  keep processing logs for at least one year, including logs for processing
  done through a processor, which supports the case for a verifiable log.
  Penalty figures of up to ₹250 cr are cited, but I have not checked them
  against the final text.

## 5. Are people already using browser agents for finance chores?

**General-purpose browser agents: WEAK. Dedicated fetchers that hold
credentials: STRONG.**
- **Habit already exists (STRONG).** Millions already hand bank and portal
  credentials to Plaid, Yodlee/Tiller, Hubdoc and GetMyInvoices, despite
  complaints (47896037, 47897440). Bookkeepers routinely take on clients'
  shared logins: "Most bookkeepers inherit shared credentials" (relayfi.com,
  vendor). The barrier to giving logins to software is already crossed. The
  open question is whether people will cross it for an *LLM agent*.
- **Agentic tools in finance teams (MEDIUM):**
  - 88% of CFOs say they use at least one agentic tool, but only 40% have fully
    integrated AI (CFO Dive, 2026).
  - Deloitte puts adoption at 13.5% of organisations (2025-07).
  - Neither survey is specific to browsers.
- **Hobbyists (WEAK):**
  - "Could a Claude Code routine watch my finances?" first used Chrome
    DevTools MCP against a bank (story 47894690, 2026-04-24). Replies were
    split between enthusiasm and alarm.
  - A Show HN for "Gabriel Operator" lists "invoice/status checks" across
    portals (item?id=45499577, 2025-10-07).
- **Comet in practice (WEAK):** users report it mainly for research ("I use
  Comet for specific financial research", item?id=45663309), not for
  transactions. Another user asks "What do real people use Comet for?"
  (item?id=45487463).
- **Unverified:** one Wikipedia claim says ChatGPT agent was removed in early
  Aug 2026, and one site says Atlas shut down on 2026-08-09. If either is
  true, consumer browser agents have weak traction.

## Evidence against adoption

1. Gartner tells enterprises to block AI browsers, so firm IT and MSPs have
   cover to say no.
2. The four largest US banks and Schwab restrict agents in public; read-only
   is the most they allow.
3. The vendors themselves (Anthropic, OpenAI) say not to use their agents on
   financial accounts and that prompt injection is unsolved.
4. Statement and invoice content is a channel for injection, so "read-only"
   does not mean "safe".
5. Professional bodies (ICAEW, PCRT, AICPA as reported) treat AI processing of
   client data as a confidentiality issue that needs consent.
6. Platform terms (Xero; Amazon's lawsuit) forbid credential-driven automation
   that poses as a human.
7. Deloitte: about 20% of finance professionals trust agents with no
   decisions at all, and "trust" is the top barrier at 21.3%.
8. Little organic evidence of people happily using general browser agents for
   finance chores.

## What this means for the wedge (company invoice collection)

- Pitch **"software that physically cannot pay or send money, running on your
  Mac, that only downloads PDFs"**. Do not pitch "an AI agent with your
  logins".
- Start with **SaaS billing portals** (Stripe-hosted invoices, AWS, Google,
  OpenAI), not banks. They carry less terms-of-service risk, and the banks are
  actively hostile.
- For bank statements, prefer the bank's own read-only user role or export.
- Use per-portal scripts with a narrow "find invoices, download" loop, not
  free-form browsing. Treat all page text as data. Keep a separate browser
  profile.
- Keep a signed local log per run with the portal, the files downloaded and
  their hashes. This meets the audit-trail need and DPDP Rule 8(3).
- **Next step: interviews.** The Reddit gap means the 5–10 user interviews
  must test fears 4 and 5 (firm policy, liability) directly. Ask: "Would your
  firm or client allow a tool that uses your existing browser session?"
