# Local AI demand check: where money already flows (2026-10)

Date: 2026-10-12. Inputs: 5 evidence reports plus 5 adversarial skeptic reviews (one pair per opportunity). This note only uses evidence that held up after the skeptic review. Labels: **verified** = page fetched; **reported** = third-party or news; **self-reported** = founder or vendor claim; **unverifiable** = could not fetch.

---

## 1. Bottom line

**No opportunity has strong evidence that people pay for a *local-first* offering from a solo builder.** The skeptic marked all five as `money_weak`. The pattern is the same each time:

- Money clearly flows in every category, but it goes to **cloud incumbents** (Dext, AutoEntry, Wispr Flow, M365 Copilot with a BAA), to **funded hardware makers** (Olares, Tiiny), or to **specialists with special hardware** (Tenstorrent bounties).
- Where local or private work is paid for directly, the verified prices are **small and crowded**: EUR 462 average bid across 172 bidders, about USD 100 for an offline legal RAG job, GBP 129 across 77 bidders, about $20 one-time Mac apps.
- No surviving source shows anyone paying **extra for privacy or offline operation**. Every local-only product we found (Bookkeeping-OCR, ReceiptMatrix, Floxtop, DesktopBrain, BlindStatement, PHNTM One) has no public traction.

**Least-bad option, low confidence: a single-purpose, paid local-AI Mac app (opportunity 1).** It is the only category with verified evidence that *individuals pay for software that runs locally*: Whisper Transcription IAPs at $29.99–$99.99, VoiceInk at $25–$49, Superwhisper lifetime at $249.99, Private LLM at #14 Top Paid Utilities, Trace buyers in its HN thread, and one Stripe-verified solo app at $12k MRR (Voicy, which may be cloud). It also needs no sales cycle, no liability paperwork, and no money. **However, the specific wedge that was recommended (a local, content-reading file or receipt organizer) was refuted.** At least 4–6 such apps already exist at about $20 and drew 1–6 HN points each. A new wedge has to be chosen from documented complaints, not assumed.

**Second, as a cheap paid test rather than a business: private document conversion with verified tie-out (opportunity 2).** The category is paid, at $0.19–$0.78 per document from cloud vendors. AWOS's verification layer (totals tie out, balances roll forward) is a real point of difference from chatbots. But the privacy premium is unproven, and the best solo revenue example in the category is shrinking fast.

**Confidence: low.** This is a choice of where to run cheap kill-tests, not a call to commit months of work.

---

## 2. Ranking

Order: (1) surviving evidence of money, (2) low investment, (3) AWOS fit.

| # | Opportunity | Surviving payment evidence (short) | Typical price | Who pays | Investment | AWOS fit | Skeptic verdict | Confidence |
|---|---|---|---|---|---|---|---|---|
| 1 | Paid single-purpose local-AI Mac apps | Verified App Store and vendor prices; Trace buyers in HN thread; Voicy $12,195 MRR Stripe-verified (dictation, possibly cloud) | $5–$99 one-time; $8–$15/mo; lifetime $250–$350 | Individual Apple Silicon knowledge workers | $99/yr Apple dev + 4–8 wk build | Partial: AWOS strength is agentic work; this market rewards single-purpose polish | money_weak (wedge refuted) | Low–medium that money exists; low for a new entrant |
| 2 | Offline/private document processing (statements, receipts to CSV/QBO/Xero) | Verified cloud prices (AutoEntry, Dext add-ons); Bank Statement Converter ~USD 6.4k MRR in 2023, new subs 191 to 45/mo in 2026 (self-reported); offline products exist with no traction | Cloud $0.19–$0.78/doc, $13–$469/mo; offline $119 one-time | Bookkeepers and accounting firms (cloud); privacy segments unproven | ~$100–$300 + 4–8 wk; test corpus is the hard part | Good technically (verification, undo, on-device); extraction accuracy unmeasured | money_weak | Low |
| 3 | Edge-model optimization (porting, quantizing, kernels) | Tenstorrent: 135 bounty issues, 98 closed as completed, $200–$35k (payouts self-reported by program); 1 freelance ONNX job, 140 bids | Bounties $500–$3k for bring-ups; freelance ~$440 | Silicon vendors (Tenstorrent); app teams (unpriced) | $0 cash, but bring-ups need Tenstorrent hardware | Medium: test-gated work suits AWOS, but **AI agents may not claim bounties** and all 7 open bounties are assigned | money_weak | Low for this builder (money is real but locked) |
| 4 | Private AI installs for privacy-bound SMBs (law, clinics, CPAs) | Freelancer: EUR 250–750 job (172 bids, avg EUR 462); offline legal RAG job ~USD 18–150; high end is vendor list prices only | Commodity USD 100–750; vendor lists USD 1.5k–20k + $750/mo (unproven) | Technical buyers on freelance boards; regulated practices unobserved | Low cash, 1–3 months of sales, BAA/E&O/liability | Medium technically; weak commercially (install is commodity; M365 Copilot has a BAA) | money_weak | Low |
| 5 | Local AI box / appliance (setup + managed service) | Olares One $2.29M Kickstarter (hardware maker's money); 1 closed OpenClaw Mac-mini setup job (GBP 129 avg, 77 bids); HKBN HK$788 install; Lemony $499/mo list price | Setup ~$100–$170; managed (unproven) | Prosumers buy hardware; setup buyers appeared during the OpenClaw burst | ~$0–$50 for service model | Medium for service layer, weak for hardware; buyers ask for OpenClaw by name | money_weak | Low (demand looks like it is fading: 0 open jobs in category on 2026-10-12) |

Why edge optimization ranks above private AI despite the barriers: it has the largest *verified* stream of repeated payments (98 completed bounties). That money is mostly out of reach for a laptop-only builder who works through agents, and the fit column reflects this.

---

## 3. Per-opportunity detail

### 3.1 Paid single-purpose local-AI Mac apps

**What survived**
- Verified prices people pay for local Mac AI utilities: Whisper Transcription IAPs $29.99–$99.99 (rated 3.9 from 168 ratings); VoiceInk $25/$39/$49 one-time (6,750 GitHub stars); Superwhisper $8.49/mo, $84.99/yr, $249.99 lifetime; Private LLM $4.99, #14 Top Paid Utilities, 734 ratings; Msty $149/yr or $349 lifetime; Elephas $19–$49/mo.
- Trace (offline meeting transcripts, £9.99): 205 HN points, with several commenters saying they bought it. The thread includes complaints that MacWhisper is "a buggy mess" and that Pro is too expensive, plus requests for Teams/Exchange calendar support, speaker separation and crash recovery.
- Voicy: $12,195 MRR and $186,212 total, Stripe-verified on TrustMRR. This shows a solo founder can earn in this app pattern, but it is a dictation app and probably cloud-based.
- File-organizer price band confirmed by competitors: Floxtop $19.99 one-time, Sparkle from $9.25/mo.

**What was refuted**
- "On-device content-reading file organizing is underserved": refuted. Floxtop (local, OCR, review-before-apply, $19.99), DesktopBrain (100% on-device, revert ledger), Ocdify, PDF AI Renamer, NameQuick and RenameClick already exist. HN launches got 1–6 points and none shows revenue.
- The "Sparkle sends filenames to OpenAI" privacy angle rests on one older third-party review. Sparkle's homepage does not say where processing happens.
- The MacWhisper "$100k revenue" figure is a 2023 newsletter claim with no source and no time window.
- Dictation, local LLM chat and meeting notes are saturated with free open-source and VC-funded tools (8+ free dictation launches on HN in 2026).
- Apple Intelligence (Floxtop's Smart Rename already uses it on macOS 26) can commoditize basic organize and rename features for free.

**Fastest first-dollar path**
Pick the wedge from documented complaints, not from AWOS's existing demos. The best-documented complaint set so far is local meeting transcription: crash recovery, non-English support, speaker labels, Teams/Exchange calendar, disk use, MacWhisper reliability. That category is crowded, though (Trace, Anarlog/Hyprnote is free and MIT-licensed). Another candidate is agent- or CLI-callable local transcription (VoiceInk issue #801), which suits a developer audience. Put a Gumroad or Lemon Squeezy page live at $19–$29 with a 14-day refund and charge from day one. If there are fewer than about 10 sales in 2 weeks after a Show HN, stop.

**Biggest risk**
The market pays only small impulse prices, the competition is on polish and distribution, and AWOS's agentic advantage is mostly invisible in a single-purpose app. A realistic outcome is a few dozen $20 sales.

### 3.2 Offline/private document processing

**What survived**
- AutoEntry (Sage), verified: $13–$469/mo, $0.19–$0.26 per credit. Invoices cost 1 credit, line items 2, bank-statement pages 3. Incumbents charge 2–3x more for hard documents.
- Dext partner pricing, verified: per client per month, 10-client minimum, add-ons $7–$7.5 per client per month. The base price is not shown, and the $25.21 Business figure came from a different page that was not fetched.
- Bank Statement Converter (self-reported): about HKD 50k MRR (~USD 6.4k) in January 2023. In 2026, new subscribers fell from 191 to 45 a month; the founder blames AI chatbots and a better competitor.
- Offline and privacy products exist only as price points: Bookkeeping-OCR $119 one-time, BlindStatement $39–$149/mo beta, ReceiptMatrix $12.99/mo. None shows traction.
- Not re-verified by the skeptic, carried as unverified: Docparser $39–$159/mo, DocuClipper $29–$279/mo, Hubdoc $12/mo, Nanonets $0.30 per block, Rossum from $18k/yr.

**What was refuted**
- The privacy premium: no RFP, job post or buyer request asks for offline processing. BlindStatement's "Trusted by" names look like placeholders, and its HN launch got 2 points.
- "No Mac-native local player": wrong. ReceiptMatrix is a local-only Mac app (2 HN points).
- Freelancer data-entry figures are average bids competing against $3–$9/hr labor. Those buyers upload documents to strangers anyway, so privacy does not drive their purchase.

**Fastest first-dollar path**
Sell a service before building a product. Offer fixed-price batches of "private statement/receipt conversion with a balance tie-out report" at $50–$150 against posted Freelancer/Upwork conversion jobs. Before that, benchmark AWOS plus a local VLM on synthetic and own statements, at no cost. If no buyer pays more than the cloud price for the privacy guarantee after 3–5 batches, drop the privacy angle.

**Biggest risk**
Chatbots are commoditizing generic conversion, and the segments that might pay for privacy (forensic accounting, lending, legal) want accuracy guarantees, security reviews and liability cover. Local-model OCR and table accuracy is unmeasured; the 8/10 coding result says nothing about it.

### 3.3 Edge-model optimization

**What survived**
- Tenstorrent tt-metal pays bounties repeatedly: 135 bounty-labeled issues, 98 closed as completed, new ones posted as recently as 2026-10-07. Bring-ups are mostly $1,500–$3,000, with Gemma-2 at $7,500. Payouts themselves are self-reported by the program.
- About 50 distinct assignees, many of them non-employees, so outsiders can get in.
- One Core ML consultancy with named client testimonials. Rate unpublished.
- Freelance: 1 active ONNX job, $250–$750, 140 bids.

**What was refuted**
- The bounties are mostly **not edge or laptop work**. The largest are SFPU numerics kernels on Tenstorrent's own accelerators ($10k–$35k), and one specialist holds about 30 of roughly 133.
- Hardware is required. An assignee on #49307 admitted he could not verify his work without it.
- All 7 open bounties are assigned, with 4 or more people queueing on some.
- **tt-metal CONTRIBUTING.md bans using AI agents to claim or request assignment, on pain of a permanent ban.** An autonomous AWOS bounty farm is ruled out; a human must review and post.
- The tinygrad $500 "no hardware" bounty: an earlier PR (#18048) was closed without merging, and there was no maintainer reply to the claimant.
- Plain quantization is free (bartowski, mlx-community). No verified price exists for the "hard ANE/NPU surgery" tier.

**Fastest first-dollar path**
Watch new tt-metal bounties and pick only CPU/reference-checkable numerics fixes ($500–$1,500), human in the loop and claimed within hours of posting. Low odds. The durable alternative (fixed-price Core ML/MLX ports with parity reports) has no verified buyers or price.

**Biggest risk**
Weeks of unpaid competitive work for, at most, a few $500–$1,500 payouts, gated by hardware and anti-agent rules.

### 3.4 Private AI installs for privacy-bound SMBs

**What survived**
- Commodity demand is real but tiny and crowded. Freelancer.com on-prem LLM and RAG job: EUR 250–750, 172 proposals, average EUR 462. Freelancer.in offline legal assistant: INR 1,500–12,500 (~USD 18–150). That job is almost exactly AWOS's spec: 16 GB Mac, Ollama, about 40k legal PDFs, at least 8/10 queries correct.
- Vendor price anchors (self-reported, no proof of sales): Sand Labs from AUD 2,500; local-llm.net USD 1.5k / 8k / 20k plus $750/mo.
- ChatGPT Business and Claude Team are not BAA-eligible (reported).
- Individuals pay $19–$49/mo (Elephas) or $149/yr (Msty) for local or offline AI *software*.

**What was refuted**
- High-end prices are list prices only: local-llm.net shows zero customers, logos or testimonials, and Sand Labs names no clients.
- The regulatory gap is narrow. Microsoft 365 Copilot is covered by Microsoft's default BAA (about $30/user/mo on a qualifying license), Google Workspace Gemini is BAA-covered, and OpenAI offers an API BAA. Many practices already have the compliant cloud option.
- No end buyer (an identifiable firm, clinic or CPA) was observed posting a budget.
- Sand Labs itself offers "contractually closed enterprise model" cloud as the substitute for local.

**Fastest first-dollar path**
Bid on live "local LLM / private RAG / offline legal assistant" freelance jobs with a 2-minute AWOS offline demo (citations plus undo/audit log). Expect USD 100–750 per job. This gets a first dollar and testimonials, not a business.

**Biggest risk**
The money that is visible is a race to the bottom. The higher tier needs a long, trust-heavy sales cycle with BAA, insurance and liability exposure, against the client's MSP and against M365 Copilot.

### 3.5 Local AI box / appliance

**What survived**
- Olares One Kickstarter $2,287,742 from 798 backers (verified). Prosumers pay about $2.9k for a local AI box, but the money goes to a VC-funded hardware maker.
- One real closed "OpenClaw Setup on macOS" job (GBP 20–250, average GBP 129, 77 proposals).
- HKBN telco install at HK$788 (~USD 100), reported.
- Lemony at $499/mo for 5 users plus a $2M seed (reported). A price anchor with no customers named.

**What was refuted**
- OpenClawReady's "$997" appears only in the title; its FAQ says $200–$500, and it shows no customers. SetupClaw's $3k–$6k figures are unverifiable (429) and were discarded.
- Freelancer's open-claw category showed 0 jobs on 2026-10-12. The demand looks like a Feb–Apr 2026 burst that has faded.
- At least 6 free one-click OpenClaw installers launched on HN, pushing the value of an install toward $0.
- A telco bundling installs at about $100 caps what a freelancer can charge.
- Consumer appetite is weak (HN thread: 5 points, the one reply prefers $20/mo cloud).

**Fastest first-dollar path**
Bid on any remaining Mac mini or local-agent setup jobs and bundle one AWOS recurring workflow. Roughly $100–$250 per job.

**Biggest risk**
A fading, commoditized, one-time setup market where buyers ask for OpenClaw rather than AWOS. The managed office tier is unproven and carries liability.

---

## 4. Open questions for the top 2, and the cheapest ways to answer them

### 4.1 Local-AI Mac app

| Unknown | Cheapest way to find out (no outreach) |
|---|---|
| Do local-only competitors actually sell, or only list prices? | Record App Store rating counts and chart positions for Floxtop, DesktopBrain, PDF AI Renamer, Trace and Whisper Transcription, then re-check in 2 weeks. Rating growth is a rough proxy for sales. Search TrustMRR and IndieHackers for any local-AI Mac app with verified MRR. |
| Which wedge has paying pain, not just interest? | Mine GitHub issues on VoiceInk, Hex, Handy and Anarlog for top-voted feature requests (gh api, free), plus HN comments on Trace, FreeFlow and MacWhisper threads. Count "I would pay" or "I bought" statements per complaint type. |
| Will anyone pay us specifically? | Put a Gumroad page live at $19–$29 with refunds, plus one Show HN. A public post is not outreach. Kill rule: fewer than 10 sales in 2 weeks. |
| Is the 9B local model reliable enough for the chosen task? | Run a 30–50 item local benchmark on the M5 for the chosen wedge before building a UI. |

### 4.2 Private document processing

| Unknown | Cheapest way to find out (no outreach) |
|---|---|
| Is local extraction accurate enough to sell? | Build 50 synthetic plus own bank statements and receipts. Measure field accuracy and tie-out pass rate for Qwen-class local VLM against Haiku 5.5. Cost: $0 locally, a few cents in the cloud. |
| Does anyone pay a privacy premium? | Search freelance boards and HN Algolia for "offline", "no upload", "on-premise" combined with "bank statement" or "invoice" over the last 12 months, and count posts with budgets. Optionally, a landing page with two prices (cloud versus "verified offline") and a Stripe pre-order button. |
| Is the low end collapsing for everyone, or only for Bank Statement Converter? | Track public revenue posts from other converters (TrustMRR, IndieHackers) and Bank Statement Converter's own monthly posts. |
| Will posted conversion buyers pay for a tie-out report? | Bid on 3–5 existing posted conversion jobs with the tie-out report as the differentiator. This responds to posted demand rather than doing cold outreach. |

---

## 5. Sources

Labels as given in the evidence or skeptic reports. "Fetched" means fetched during this research cycle.

### Private AI for SMBs
- https://www.freelancer.com/projects/machine-learning/private-local-llm-developer-sql — verified
- https://www.freelancer.in/projects/ai-development/offline-legal-assistant-configuration — verified
- https://sandlabs.com.au/private-ai-setup — self-reported (page fetched)
- https://www.local-llm.net/ — self-reported (page fetched)
- https://apertus.ai/en/blog/local-ai-appliance-vs-api-tips-for-smes-2025/ — self-reported
- https://www.vyrox.com/ai/solutions — self-reported
- https://petronellatech.com/blog/private-ai-deployment-guide-enterprise/ — self-reported
- https://anythingllm.com/pricing — verified
- https://elephas.app/pricing — verified
- https://msty.ai/pricing — verified
- https://costbench.com/compare/harvey-ai-vs-luminance/ — reported (not fetched)
- https://ibl.ai/blog/on-premise-legal-ai-platform — unverifiable
- https://fiverr.com/achupradeesh/set-up-open-webui-with-ollama-for-your-business-team — unverifiable (403)
- https://intuitionlabs.ai/articles/chatgpt-enterprise-baa-cost-and-seats — reported
- https://news.ycombinator.com/item?id=45168074 — verified (skeptic thread)
- ABA Formal Opinion 512, Florida Bar Opinion 24-1 — could not fetch (403)

### Mac apps
- https://startupsemail.substack.com/p/100000-revenue-with-a-simple-mac — reported
- https://apps.apple.com/us/app/whisper-transcription/id1668083311 — verified
- https://superwhisper.com/docs/billing/plans.md — verified
- https://tryvoiceink.com/ — verified (user counts self-reported)
- https://apps.apple.com/us/app/private-llm-local-ai-chat/id6448106860 — verified
- https://wisprflow.ai/pricing — verified
- https://pulse2.com/wispr-raises-280-million-at-2-billion-valuation-as-revenue-grows-150-quarterly-and-voice-ai-push-accelerates — reported
- https://trustmrr.com/founder/buildingwaves — verified (Stripe-verified figures)
- https://makeitsparkle.co/ — verified (user count self-reported)
- https://matthewbecher.substack.com/p/mini-review-sparkle-file-organizer — reported
- https://news.ycombinator.com/item?id=48521236 — verified (Trace thread)
- https://news.ycombinator.com/item?id=47040375 — verified (FreeFlow thread)
- https://floxtop.com — verified
- https://desktopbrain.saposs.com/ — verified
- https://ocdify.vyanlabs.in — could not fetch (TLS error)
- https://apps.apple.com/app/id6746876116 — search snippet only
- https://peerpush.com/p/namequick — reported
- https://peerpush.com/p/renameclick-offline-ai-file-renamer — not fetched
- Setapp listings — could not fetch (404); Reddit — blocked (403)

### Offline document processing
- https://www.autoentry.com/pricing — verified
- https://docparser.com/pricing/ — verified by the evidence report, not re-fetched by the skeptic
- https://www.docuclipper.com/pricing/ — verified by the evidence report, not re-fetched by the skeptic
- https://dext.com/en/partner/pricing — verified (the $25.21 Business figure is not on this page)
- https://www.hubdoc.com/pricing — verified by the evidence report, not re-fetched by the skeptic
- https://nanonets.com/pricing — verified by the evidence report, not re-fetched by the skeptic
- https://rossum.ai/pricing/ — verified by the evidence report, not re-fetched by the skeptic
- https://bankstatementconverter.com/blog/posts/2023-02-01-revenue-numbers/ — self-reported (page fetched)
- https://bankstatementconverter.com/blog/posts/2026-08-28-business-is-dying/ — self-reported (page fetched)
- https://www.blindstatement.com/ — verified (vendor pricing only)
- https://news.ycombinator.com/item?id=46646023 — verified (BlindStatement launch, 2 points)
- https://bookkeeping-ocr.com/ — verified
- https://news.ycombinator.com/item?id=47502374 — self-reported (ReceiptMatrix, fetched via HN API)
- https://news.ycombinator.com/item?id=46859383 — verified (Local Bank Statement Converter, 1 point)
- https://www.freelancer.com/jobs/data-entry/?keyword=invoice — verified (average bids, not budgets)
- https://www.smartvault.com/resources/why-accountants-are-right-to-be-cautious-about-ai/ — reported (primary Accounting Today report not fetched)
- https://news.ycombinator.com/item?id=47645024 — self-reported (Vibooks founder)
- Upwork data-entry and Capterra pages — could not fetch (403)

### Edge optimization
- https://github.com/tenstorrent/tt-metal/issues?q=label%3Abounty — verified via API (payouts self-reported by program)
- https://github.com/tenstorrent/tt-metal/issues/51398 — verified
- https://github.com/tenstorrent/tt-metal/issues/49307 — verified
- https://github.com/tenstorrent/tt-metal/issues/50676 — verified
- https://github.com/tenstorrent/tt-metal/issues/56290 — verified
- https://github.com/tenstorrent/tt-metal/issues/18287 — verified
- https://github.com/tenstorrent/tt-metal/blob/main/CONTRIBUTING.md — verified (AI agent claim ban)
- https://github.com/tinygrad/tinygrad/issues/18310 — reported (claimant quoting the bounty sheet; sheet not fetchable)
- https://github.com/tinygrad/tinygrad/pull/18048 — verified (closed unmerged)
- https://www.freelancer.com/projects/machine-learning/Object-Detection-Model-Finetuning — verified
- https://argmaxinc.com/pricing — verified
- https://drobinin.com/consulting/coreml-on-device-ml/ — verified (self-reported clients)
- https://www.indeed.com/q-quantization-quantization-quantum-algorithm-quantization-vector-database-oracle-cloud-jobs.html — unverifiable
- https://www.indeed.com/q-quantization-quantum-processor-quantization-quantum-algorithm-quantum-algorithm-oracle-cloud-jobs.html — reported
- https://www.capterra.ca/software/1038536/picovoice — reported
- https://github.com/sponsors/ggerganov — verified
- https://edgeimpulse.com/pricing — verified
- https://news.ycombinator.com/item?id=49922569 — verified
- https://news.ycombinator.com/item?id=45217712 — verified (Core ML pain anecdote)

### Local AI box
- https://www.kicktraq.com/projects/167544890/olares-one-the-local-al-powerhouse-on-your-desk/ — verified
- https://shop.olares.com/products/olares-one — verified (list price)
- https://www.kicktraq.com/search/?find=tiiny — verified
- https://www.it-boltwise.de/stichwort/lares-os — reported (snippet only)
- https://techcrunch.com/?p=3017623 — reported (Lemony)
- https://www.thestandard.com.hk/finance/article/326611/HKBN-launches-OpenClaw-application-solution-for-individual-customers — reported
- https://www.freelancer.de/projects/open-claw/openclaw-setup-macos — verified
- https://www.freelancer.com/jobs/open-claw/ — verified (0 jobs on 2026-10-12)
- https://openclawready.com/blog/how-much-does-openclaw-setup-cost/ — self-reported (internally inconsistent)
- https://claudemarket.ai/blog/done-for-you-openclaw-setup-for-teams — unverifiable (429); figures discarded
- https://techcrunch.com/2026/04/24/mac-mini-price-expensive-ebay-shortage-ai-memory/ — reported
- https://www.phntmcore.com/ — verified
- https://wccftech.com/nvidia-dgx-spark-700-usd-price-hike-memory-shortages-4699-new-pricing/ — reported
- https://news.ycombinator.com/item?id=47546796 — verified
- One-click installers: https://news.ycombinator.com/item?id=47134606, https://news.ycombinator.com/item?id=47217358, https://news.ycombinator.com/item?id=47213733, https://news.ycombinator.com/item?id=46886535, https://news.ycombinator.com/item?id=46932625, https://news.ycombinator.com/item?id=47295889 — verified via skeptic
- Security concerns: https://news.ycombinator.com/item?id=46842884, https://news.ycombinator.com/item?id=46913871 — verified via skeptic
