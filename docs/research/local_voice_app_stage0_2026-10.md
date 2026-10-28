# Local voice-AI Mac app: Stage 0 plan (opportunity #1)

Date: 2026-10-12. Author: product lead (agent synthesis of 5 research tracks plus a critic review).
Inputs: competitor, complaint-mining, underserved-segment, on-device feasibility, and business-mechanics tracks; critic review; `docs/research/local_ai_demand_check_2026-10.md`.

Evidence labels:
- **verified**: a source we fetched.
- **measured**: we ran it on this M5 16 GB Mac, or computed it from fetched data.
- **reported**: press or a third party.
- **self-reported**: a vendor's own claim, or a single user's comment or review.
- **judgement**: the author's opinion. These are marked so they cannot pass as evidence.

All fetched content was treated as untrusted data.

---

## 1. Bottom line

**What to build:** a local, bot-free meeting recorder for Mac that never loses audio and turns each meeting into a running Markdown document.
- It records the mic and system audio as two separate tracks.
- Audio is written to disk as segments while it records, so a crash does not lose it.
- Transcription runs in chunks that can resume, and it reports per-minute coverage, so we can show nothing was dropped.
- You and the other side are labelled from the two tracks, without needing diarization.
- A local LLM writes a block of decisions, actions and open questions. Each quote in it is checked against the transcript and linked to its timestamp.
- Everything goes into a plain Markdown folder the user picks. Recurring meetings append to one running file per series (for example, a 1:1 or a standup).

The pitch sells the outcome: "your recurring meetings as a running doc you own, and it never loses a recording." It does not sell "local Whisper."

**Build order:** the long-form reliability engine (critic's direction #2) comes first. Importing a recording into it is milestone 1, and live capture is milestone 2. That way the hardest unknown (system-audio capture) gets tested early, and something useful exists even if capture slips. Hinglish/Indic is a later language mode, not the product.

**Confidence: low to moderate (judgement).**

Why direction #1 still wins:
- The closest real purchases are for this shape of product. Trace sold at about £10 and then raised to £29-39 ([traceapp.info/buy](https://traceapp.info/buy), verified; [HN 48521236](https://news.ycombinator.com/item?id=48521236), at least 4 "I bought it" comments, verified).
- Granola charges $14/user/mo for the same outcome in the cloud ([pricing](https://www.granola.ai/pricing), verified).
- Every paid voice-app breakout on HN sold a specific workflow, not a model: Trace got 205 points and Scriber Pro 137 (verified via HN Algolia).

What argues against it:
- The space is crowded. Meetily has 31.6k stars, Anarlog 9.5k, and Ghost Pepper Meet, Biscotti and Trace also exist (verified).
- The specific gaps (appending recurring meetings, plain folder output, crash safety, Outlook calendar) come from about 2-3 HN commenters ([48539830](https://news.ycombinator.com/item?id=48539830), [48533220](https://news.ycombinator.com/item?id=48533220), [48545225](https://news.ycombinator.com/item?id=48545225); self-reported). They are hypotheses, not demand.
- Base rates are poor. TurboWhisper, a paid Mac voice app, has **$107** of verified lifetime revenue ([trustmrr](https://trustmrr.com/startup/turbowhisper), verified). Most local-voice Show HNs score 1-5 points (measured).

**My estimate of passing the kill rule (at least 10 sales in 14 days), as built today:** about 25-35% (judgement). It depends mostly on whether a Show HN reaches the front page.

**Do not write the app until two gates pass:**
1. A one-day capture prototype passes the kill-the-app recovery test (§8, Q1).
2. Pre-launch demand signals clear their thresholds (§7, gate G0).

---

## 2. Scoring table: top 5 directions

Scale: 1-5, where 5 is best for us. For Apple-giveaway risk, 5 means low risk. For investment, 5 means cheap.

**D1. Meeting recorder that writes a running Markdown doc, on a crash-safe recorder (recommended).** Overall: **3.4**, ranked first because the pitch is demo-able.
- **Evidence people pay: 3.** Trace buyers paid £10 and the price later rose to £29-39 (verified). Granola charges $14/mo (verified). One buyer chose Trace over a free app because "I just want markdown" (48545225, self-reported).
- **Gap size: 2.** VoiceInk's top request is system audio (#31, 16 upvotes; #199, 14) (verified). Meeting complaints appear in 15 of 153 negative reviews (measured). The specific wishlist comes from 2-3 people. Free clones exist.
- **Apple-giveaway risk: 3.** Notes records, transcribes and summarizes, but per Apple's guide it has no Zoom/Teams system audio, no speaker labels and no plain-file output (verified). A future macOS could add these.
- **Feasibility on M5: 3.** Measured: 10 minutes of audio transcribes in 47-49 s (whisper.cpp turbo q5/f16), and a Qwen summary takes 53 s. Not tested: system-audio capture, crash recovery, mic bleed, and Qwen's 6.3 GB running next to Zoom.
- **Fit with owner and AWOS: 4.** AWOS verified routines map directly onto checkpointed, resumable chunks, quote-to-timestamp checks and coverage reports. Local Qwen batch summarization is already running.
- **Investment: 2.** A native Swift app with ScreenCaptureKit or Core Audio taps, permissions, licensing and two builds. Roughly 5-7 weeks of solo work (judgement).

**D2. Long-form "never lose it" transcriber: 1-4 h files, chunked, resumable, coverage report.** Overall: **3.2**. Its machinery is strong but it is a weak headline; use it as D1's backbone and as milestone 1.
- **Evidence people pay: 2.** MacWhisper-family in-app purchases run $29.99-99.99 (verified). But the likely buyers already own MacWhisper, and journalists often use Colab instead of paying (49295241, self-reported).
- **Gap size: 3.** Long audio appears in 29 of 153 negatives and lost recordings in 18 of 153 (measured). Bug reports: VoiceInk #727 (middles dropped), Handy #1332 (7 reactions), and one Whisper Transcription review asking for files over 1 h (verified or self-reported). Some of these are bugs that maintainers could fix.
- **Apple-giveaway risk: 4.** Voice Memos has no verified multi-hour transcripts, diarization or exports.
- **Feasibility on M5: 4.** Measured RTF of 0.075 means about 4.5 min per hour of audio, on battery. Only tested up to 10.4 min. mlx-whisper hallucinated on trailing silence, so VAD is mandatory.
- **Fit with owner and AWOS: 4.** Same verified-routine fit as D1.
- **Investment: 4.** Files in, files out. No capture permissions. About 2-3 weeks (judgement).

**D3. Hinglish/Indic code-switch transcription, sold in USD to the diaspora.** Overall: **2.3**. Keep it as a later mode.
- **Evidence people pay: 1.** Measured mentions are low: 13 of 540 reviews mention India or Indic languages, and code-mix appears 3 times. Launches have flopped: Parrot got 1 point, OpenNotetaker 2, ListenIn 2 (verified).
- **Gap size: 2.** The quality gap is real: vanilla Whisper large-v3 scores about 30-34% WER (Trelis model card, self-reported). The demand gap has not been shown.
- **Apple-giveaway risk: 4.** Apple supports neither Hindi nor Indian English (verified). But Wispr Flow ships free Hinglish ([wisprflow.ai/india](https://wisprflow.ai/india), verified).
- **Feasibility on M5: 2.** Measured: vanilla turbo scored WER 0.21-0.58, the output script flips, and forcing English makes it translate. A Qwen rewrite fixed one clip in 3.1 s, but that is a paraphrase, not a verbatim transcript. The Tara fine-tune was not tested.
- **Fit with owner and AWOS: 4.** The owner is in India and can record real Hinglish test data, which no competitor in the corpus has.
- **Investment: 3.** Model conversion plus an evaluation set.

**D4. In-room diarized capture for interviews, with speaker enrollment.** Overall: **2.3**.
- **Evidence people pay: 2.** Diarization in paid apps disappoints users and leads to cancellations (Whisper Transcription GB review, self-reported). MacWhisper Pro already includes speaker recognition (verified).
- **Gap size: 2.** Diarization appears in only 4 of 153 negatives (measured). GitHub requests have 5-15 upvotes (verified). One HN commenter called in-room diarization "hardly touched" (50045136, self-reported).
- **Apple-giveaway risk: 4.** Apple has no speaker labels.
- **Feasibility on M5: 2.** Measured 12/12 turns correct, but only on two very different TTS voices with 0.5 s gaps. Real rooms were not tested.
- **Fit with owner and AWOS: 3.** Moderate.
- **Investment: 3.** sherpa-onnx and FluidAudio make it cheap to build.

**D5. Agent-callable transcription (CLI or MCP for Claude Code and local agents).** Overall: **2.2**.
- **Evidence people pay: 1.** The only demand found is one issue with 9 upvotes (VoiceInk #801, verified). Developers anchor at "free, or I'll ask Claude to build it" (48534192, verified).
- **Gap size: 1.** MacWhisper already has a CLI and Spokenly has MCP (verified).
- **Apple-giveaway risk: 5.** Low.
- **Feasibility on M5: 4.** Same pipeline as D2.
- **Fit with owner and AWOS: 5.** Native to AWOS.
- **Investment: 4.** Small.

Overall is a judgement-weighted score, not a plain average. Pay evidence and distribution reach weigh most because the kill rule counts sales in 14 days.

On raw averages D2 (3.5) beats D1 (2.8). D1 ranks first anyway because D2's selling point, reliability, can't be seen at purchase. D1's running doc, and a demo that kills the app mid-call and recovers the recording, can be shown in 30 seconds.

**Dropped directions (strong evidence against them):**
- **Generic or cheaper dictation:** Handy has 33.6k stars, BetterWispr got 194 points on 2026-10-11, Superwhisper launched a free tier in 2026-08, there are 178 dictation Show HNs since 2025, and Apple ships SpeechAnalyzer.
- **Generic file transcription:** MacWhisper and Buzz cover it.
- **Clinical scribe:** buyers expect HIPAA and SOC2, and the seller carries liability.
- **RSI dictation:** Talon and free tools cover it.
- **Voice-to-action:** launches score under 20 points with no visible revenue.
- **Selling to the domestic Indian market:** Wispr's India share is 14% of installs but about 2% of revenue ([TechCrunch](https://techcrunch.com/2026/05/09/voice-ai-in-india-is-hard-wispr-flow-is-betting-on-it-anyway/), reported).

---

## 3. Competitor map

**Meeting capture (D1's segment)**

| Product | Price | Local/cloud | Traction | Weaknesses (evidence) |
|---|---|---|---|---|
| **Trace** | £29 Solo / £39 2-Mac direct (Paddle); £39 on the Mac App Store; launched at £9.99 | Local | Show HN 205 pts, at least 4 buyers in the thread; 2nd Show HN 3 pts | English-only. HN asks for crash recovery, German, Outlook calendar, appending recurring meetings, and plain-folder or git output (thread comments, self-reported). [traceapp.info/buy](https://traceapp.info/buy) |
| **Granola** | Basic free (30-day history), Business $14/user/mo, Enterprise $35 | Cloud (pricing page doesn't say where processing runs) | 14,413 iOS ratings, 4.96 | Cloud; refused by privacy-sensitive users (HN 49244333). [pricing](https://www.granola.ai/pricing) |
| **Otter / Fireflies** | Subscription | Cloud | 84,433 / 5,252 iOS ratings | Bot joins calls; auto-join privacy complaints (Otter GB/IN reviews) |
| **Meetily** | Free (MIT); Pro $10/user/mo | Local | 31,637 stars | Top requests open: speaker ID (15), templates (11), recordings folder (12). [repo](https://github.com/Zackriya-Solutions/meetily) |
| **Anarlog (ex-Hyprnote)** | Free (MIT) | Local | 9,475 stars; Launch HN 270 pts | Open-source Granola clone; no specific weakness gathered |
| **Ghost Pepper Meet, Biscotti, Muesli (1.4k stars), HushScribe, OpenOats, localscribe** | Free | Local | Small | Users report Ghost Pepper's speaker recognition doesn't work |
| **Apple Notes / Voice Memos** | Free | On-device | Installed base | Per Apple's guide: no Zoom/Teams system audio, no speaker labels, limited languages and regions (no Hindi or Indian English). [guide](https://support.apple.com/guide/mac-help/mchl2102c2ae) |

**File and long-form transcription (D2's segment)**

| Product | Price | Local/cloud | Traction | Weaknesses (evidence) |
|---|---|---|---|---|
| **MacWhisper / Whisper Transcription (MAS)** | Pro about €44-64 one-time (sources conflict); MAS in-app purchases $29.99-99.99 | Local | 168 US MAS ratings, 3.89 | Closest to feature-complete: diarization, CLI, auto meeting recording, watched folders. But "a buggy mess now" (HN 48529911), crashes take audio with them (48533220), "awful" diarization leading to cancellations, 1,400 audio files lost (GB reviews; self-reported). [macwhisper.com](https://www.macwhisper.com/) |
| **Aiko** | $24 one-time (MAS) | Local | 326 ratings, 4.70 | Single language only; Gujarati output in Devanagari script (IN reviews) |
| **Buzz** | Free (MIT) | Local | 21,947 stars | Export and batch requests (#314) |

**Dictation (dropped segment)**

| Product | Price | Local/cloud | Traction | Weaknesses (evidence) |
|---|---|---|---|---|
| **Superwhisper** | Free local dictation; Pro $8.49/mo, $84.99/yr, $249.99 lifetime | Hybrid | 841 iOS ratings | Price complaints, forced punctuation, censorship, lost dictation (reviews). [plans](https://superwhisper.com/docs/billing/plans.md) |
| **Wispr Flow** | Free 2k words/wk; $12-15/mo; India Rs 320-400/mo | Cloud | 17,127 iOS ratings, 4.84; reported $2B valuation | Over-confident rewriting (HN 50044767); WER regressed from 9.0% to 11.2% (HN 48548002, self-reported). [pricing](https://wisprflow.ai/pricing) |
| **VoiceInk** | $25 / $39 / $49 one-time; GPL source | Local | 6,750 stars; claims 13,469+ users | No meeting capture or diarization. Middles dropped (#727). [site](https://tryvoiceink.com/) |
| **Spokenly** | Free local; Pro $9.99/mo | Hybrid | 61 iOS ratings | Already does meeting recording and MCP voice input. [site](https://spokenly.app/) |
| **Handy / FluidVoice / BetterWispr and similar** | Free | Local | 33.6k / 12k stars; 194 pts | Commodity dictation; reference only |

**Technical moat is close to zero.** whisper.cpp (MIT), WhisperKit (MIT), FluidAudio (Apache-2.0, includes diarization), parakeet-mlx and sherpa-onnx are all free to embed (verified via the GitHub API). Differentiation has to come from outcome and reliability.

---

## 4. Top complaints and gaps

**Corpus.** 540 App Store reviews across 14 apps and 3 storefronts (US, IN, GB), 153 of them rated 1-3 stars. Clusters come from a keyword regex, so they overlap and are noisy. The corpus leans iOS (measured).

GitHub counts are upvotes ("reactions") on issues.

| # | Complaint or gap | Count | Example (URL) | Relevance to D1 |
|---|---|---|---|---|
| 1 | Price, subscriptions, paywall | 48/153 negatives (31%) | "£7.99 a month is a ridiculous amount… go back to… one-off purchase" (Superwhisper GB 14047926845); [HN 45923353](https://news.ycombinator.com/item?id=45923353) | Sell one-time, with a trial before any paywall |
| 2 | Crashes and unreliability | 32/153 (21%) | "Crash recovery. Most of these apps are incredibly buggy… taking the recorded audio with them. Macwhisper is incredibly bad at this" ([HN 48533220](https://news.ycombinator.com/item?id=48533220), one person) | **Core promise** |
| 3 | Long audio breaks | 29/153 (19%); keyword hits in GitHub issues: VoiceInk 131/663, Handy 143/790 | "much of the middle of it is not transcribed" ([VoiceInk #727](https://github.com/Beingpax/VoiceInk/issues/727)) | **Core promise (coverage report)** |
| 4 | Lost recordings | 18/153 (12%) | "nothing can be found… the actual voice memo [isn't] saved… happened five" times (Wispr 14487745299, [reviews](https://apps.apple.com/us/app/id6497229487?see-all=reviews)) | **Core promise** |
| 5 | Meeting capture / system audio | 15/153; VoiceInk #31 (16), #199 (14) | [VoiceInk #31](https://github.com/Beingpax/VoiceInk/issues/31); Otter "automatically join every meeting" (GB 10260042029) | Headline feature |
| 6 | Language, accent, code-mix | 15/153 accent/language; code-mix 1/153 (3/540) | "please add Hinglish" (Whisper Transcription IN 12595356911); [VoiceInk #2](https://github.com/Beingpax/VoiceInk/issues/2) | Later mode (D3) |
| 7 | Paywall before any value / India pricing | 14/153 | "how can we PAY without testing anything?" (IN 12935795329, [reviews](https://apps.apple.com/in/app/id1668083311?see-all=reviews)) | Full 7-day trial |
| 8 | Unfaithful LLM rewriting | Not counted (qualitative) | "WAY to[o] confident in guessing what you meant" ([HN 50044767](https://news.ycombinator.com/item?id=50044767)) | Keep the verbatim transcript and put the LLM block in a separate section with checked quotes |
| 9 | Diarization fails on real meetings | 4/153; VoiceInk #529 (10), Meetily speaker ID (15) | "thrown completely if someone says yeah… cancelled my subscription" (GB 12978088290, [reviews](https://apps.apple.com/gb/app/id1668083311?see-all=reviews)) | Avoid by recording two tracks (you vs them) |
| 10 | Organizing and export | 4/153 export; Buzz #314 (6) | "1400 audio files that are nowhere to be found" (GB 14521070729); [Buzz #314](https://github.com/chidiwilliams/buzz/issues/314) | Plain folder, user-visible audio |
| 11 | Meeting wishlist: separate the two sides, local folder, Outlook calendar, append recurring meetings, remember speakers | 1 commenter (+ Trace thread asks) | [HN 48539830](https://news.ycombinator.com/item?id=48539830) | Defines the MVP, but it is **one person** |
| 12 | Wants Markdown, chose paid over free for it | 1 buyer | [HN 48545225](https://news.ycombinator.com/item?id=48545225) | Strongest single pay signal for D1 |

Honest read: rows 2-4 are the strongest pain signals, and they come from many apps. Rows 11-12 match D1 exactly, but there are only about 2 people behind them.

---

## 5. Feasibility results (measured on M5 16 GB, on battery 18%→6%)

**Caveats.** All audio was macOS `say` TTS, which is cleaner than real mic audio, so WER here is a best case. The longest file was 10.4 min. Data is in `.awos/jobs/voice_spike/results/` (`summary_table.txt`, `*.jsonl`).

**Transcription and memory**

| Component | Result | Notes |
|---|---|---|
| English WER, 10.4 min long-form | whisper.cpp turbo 1.2%, turbo q5 1.3%, small 1.2%; mlx-whisper turbo 1.1%; Parakeet v3 2.5% | Whisper is slightly better on long-form |
| English WER, dictation clips (US/UK and Indian-English voices) | 0.000-0.006 for all engines (whisper small on Indian English: 0.037) | TTS best case |
| Speed on 10.4 min | Parakeet 15.6 s (RTF 0.025); whisper.cpp small 28.2 s; mlx turbo 30.8 s; whisper.cpp turbo q5 46.9 s (RTF 0.075); turbo f16 48.9 s | About 5 min per hour worst case |
| Dictation latency (model loaded) | Parakeet 0.15-0.23 s; whisper small 0.40 s; turbo 1.24 s | Not needed for D1 |
| Cold model load | pywhispercpp turbo 12.7 s; others 0.3-2 s | Keep the model resident during a session |
| Peak RAM | turbo q5 0.86 GB; turbo f16 2.0 GB; mlx turbo 2.2-2.4 GB; Parakeet 2.8-3.1 GB; Qwen3.5-9B 6.3-6.4 GB RSS | turbo q5 plus Qwen ≈ 7.3 GB. **Not measured alongside Zoom.** |

**Diarization, languages and summarization**

| Component | Result | Notes |
|---|---|---|
| Diarization (sherpa-onnx + pyannote seg 3.0 ONNX) | 12/12 turns correct, 2 speakers found automatically, RTF 0.075, about 370 MB RSS, about 47 MB of models | Easy synthetic case only. Embedding-model licenses not checked. |
| Silence hallucination | mlx-whisper emitted garbage on trailing silence (WER inflated to 6.5%); whisper.cpp did not | Ship with whisper.cpp `--vad` (Silero) plus a repetition filter |
| Hindi | turbo CER 0.094, readable Devanagari; Parakeet CER 0.94 (no Hindi support) | Whisper is the only path for Hindi |
| Hinglish | Vanilla WER 0.21-0.58; script flips; forcing English translates or drops content | No engine is reliable out of the box |
| Qwen3.5-9B Q4_K_M post-processing | Dictation cleanup 3.6 s; 72 s dialogue summary 15.5 s; 10 min meeting summary 53 s (first token at 9.0 s, about 10 tok/s generation) | Fine as a batch step after the call. Owners attributed loosely without speaker labels. |
| Licenses | Whisper MIT, whisper.cpp MIT, Parakeet CC-BY-4.0, sherpa-onnx Apache-2.0, pyannote seg MIT, Qwen3.5-9B Apache-2.0 (verified, HF/GitHub APIs) | Needs a NOTICE file and Acknowledgements screen |

**Not tested yet:**
- ScreenCaptureKit or Core Audio process-tap system-audio capture on macOS 26
- crash recovery
- mic bleed and echo duplicates
- real Zoom, Meet or Teams calls
- files of 1-4 hours
- RAM pressure with Zoom, a browser and Qwen running together
- real human (including Indian) speakers

---

## 6. Business mechanics, costs and go-to-market

**Channels**
- **Direct, first:** a notarized Developer ID DMG sold through **Paddle** (5% + 50¢, merchant of record, India not on its excluded list; Trace uses it; [pricing](https://www.paddle.com/pricing), verified).
- **Payment fallback:** **Polar** (5% + 50¢, +1.5% on non-US cards, about 1.25% payout/FX; India explicitly supported via Stripe Connect Express; VoiceInk uses it; [docs](https://polar.sh/docs/merchant-of-record/supported-countries), verified).
- **Third option:** Gumroad (10% + 50¢).
- **Avoid:** Lemon Squeezy (Indian sellers without Stripe approval get PayPal-only payouts) and raw Stripe India (invite-only, and you handle tax yourself).
- **Mac App Store, second:** Small Business Program at 15% ([verified](https://developer.apple.com/app-store/small-business-program/)). The MAS build has to differ from the direct build:
  - sandboxed
  - no license keys and no license screen at launch (2.4.5(vi))
  - no Sparkle updater
  - downloading the model on first run is tolerated in practice (Whisper Transcription and Aiko do it; verified), though guideline 2.4.5(iv) could be read against it
  
  Some buyers can't use the MAS at all ("my work prohibits App Store apps", Trace thread). Submit the MAS build after the direct launch. It is not needed to pass the kill rule.
- **Setapp:** skip until after day 90. Revenue lags 2-2.5 months and Setapp wants mature apps (verified).

**Net revenue per $29 sale (computed):** Paddle about $27.05; Polar about $26.2 before payout costs; Gumroad about $25.60; MAS about $24.65. Fees won't decide the outcome; distribution will.

**Cash costs**

| Item | Cost | Status |
|---|---|---|
| Apple Developer Program | $99/yr ([verified](https://developer.apple.com/programs/enroll/)) | Required for Developer ID and notarization |
| Domain | about $12/yr, or $0 if the existing AWOS site domain is reused (`site/` already has a landing page and a KV early-access form) | Owner decision |
| Model hosting | $0 (Cloudflare R2 free tier: 10 GB-month, free egress, [verified](https://developers.cloudflare.com/r2/pricing/); Hugging Face mirror) | Resumable downloads with checksums |
| Payments | Per sale only | Apply to Paddle and Polar on day 1 |
| India tax (export-of-services income, GST/LUT, FIRA) | One chartered-accountant consult; fee unknown | **unverifiable**; needs a CA |
| **Total cash before launch** | **About $99-130** | |

The main cost is the owner's time: about 5-7 weeks to launch (judgement). The laptop-only, no-spend constraint is respected: no cloud inference, and the LLM runs locally.

**Go-to-market checklist**
- [ ] Day 1: apply to Paddle and Polar. Enroll in the Apple Developer Program.
- [ ] Week 0: landing page on the existing `site/` stack with a 30-second demo GIF (kill the app mid-call, show recovery, show a recurring 1:1 file), a waitlist, and "notify me / $19 founder price" intent capture.
- [ ] Week 0: personal outreach to named HN commenters and Trace buyers (48539830, 48533220, 48545225, 48539643, 48538789). Ask: "Would you pay $29 one-time for X?"
- [ ] Private beta with 10-20 users who have real meetings. Collect crash and data-loss reports and real-call WER.
- [ ] Notarized DMG, Sparkle updates, Paddle license key with offline activation, 7-day full trial with no card, 14-day no-questions refund (the Trace and VoiceInk pattern).
- [ ] Acknowledgements/NOTICE screen with CC-BY credits. No gated model fetched silently.
- [ ] Show HN, posted once. Tue-Thu, US morning (about 19:30-21:30 IST). An honest comparison table against Trace, MacWhisper and Meetily. Reply to every comment for 6 hours.
- [ ] Same week: an r/macapps post (for feedback). The following week: Product Hunt.
- [ ] UTM-coded checkout links per channel. Daily sales log. The kill-rule clock starts at the Show HN.
- [ ] After launch: submit the MAS build, do App Store search optimization, consider Setapp at day 90 or later.

**Realistic revenue ranges (base rates, judgement)**
- Median first-90-day outcome: about $100-1,000. TurboWhisper has $107 lifetime (verified); CompressX made about $600 from a #2 Product of the Day (self-reported).
- With an HN front page: about $1k-5k (inferred; Trace has not disclosed sales).
- More than $10k in 90 days: no verified local-only comparable.

---

## 7. MVP scope, pricing, kill rule

**Working name:** placeholder, e.g. "Ledger Notes". Do not use another company's name.

**Gate G0, before writing the full app (about 1 week; all must pass):**
- **(a)** The capture prototype records mic and system audio as separate tracks from a real Zoom or Meet call. After `kill -9` mid-call, at least 99% of the audio is recoverable on relaunch.
- **(b)** 3 real calls and one file of 2 hours or more go through whisper.cpp turbo q5 with VAD. Record real-speech WER spot checks, coverage, mic bleed, and peak RAM with Qwen loaded after the call. Pass if nothing is dropped and RAM stays under 12 GB total.
- **(c)** Demand: at least 3 "yes, I'd pay $29" replies from outreach, **or** at least 30 waitlist signups. Below that, either rethink the headline or stop (judgement threshold).

**Milestone 1: import (backbone, about 2-3 weeks)**

*Must:*
- Drop in an audio or video file of any length.
- Chunked, checkpointed transcription with whisper.cpp turbo q5, VAD and a repetition/garbage filter.
- Resumes after quit or crash.
- Per-minute coverage report, with gaps flagged.
- Verbatim transcript with timestamps.
- Markdown written to a user-chosen folder.
- A local LLM block (decisions, actions, open questions), produced after transcription. Every quoted line is verified as a substring of the transcript and linked to its timestamp. Unverifiable lines are dropped or flagged. This is the AWOS verified-routine applied.

**Milestone 2: live capture (headline, about 2-3 weeks)**

*Must:*
- Menu-bar app.
- Mic plus system audio as two tracks, so you/them labels come for free.
- Audio written to disk in segments while recording (for example, 30 s compressed segments), so a crash loses no more than a few seconds.
- Automatic recovery on relaunch.
- A "series" picker: append this meeting to a running file, e.g. `1on1-alex.md`, with dated sections and carried-over open actions.
- Audio files visible in the folder, with retention settings.
- Paddle licensing, 7-day trial, notarized DMG.

*Should:*
- Calendar title matching through EventKit. This covers Outlook or Exchange only if the account is added to macOS Calendar; to be verified.
- Mid-call flag hotkey (Trace has this; parity).
- Option for a smaller LLM (Qwen3-4B class) for 16 GB machines, and unloading the LLM after use.
- Obsidian-friendly YAML frontmatter.
- An optional git commit per meeting.
- sherpa-onnx diarization inside the "them" track when there are 3 or more people.
- Mic-bleed and echo deduplication.

*Later:*
- Hinglish/Indic language mode (Tara fine-tune, a choice of output script, evaluated on the owner's own recordings).
- German and other languages via Whisper.
- Speaker voices remembered across meetings.
- A sandboxed MAS build.
- CLI/MCP export for agents (D5 as a feature).
- Setapp.
- No iOS app: most dictation complaints come from iOS keyboards.

**Explicitly out of scope:**
- live dictation
- cloud models
- subscriptions
- bots that join calls
- summaries that replace the transcript

**Pricing hypothesis**
- One-time: **$29 Solo (1 Mac) / $39 Personal (2 Macs) / $69 (5 Macs)**.
- Launch-week **$19 founder price** on the direct channel only, to help the unit count for the kill rule. Trace's move from £9.99 to £29-39 suggests it is safe to raise the price later.
- 7-day full trial, 14-day refund. No paywall before value; every paywall-before-value review in the corpus was 1-star.
- Charge in USD. Add an INR price only if Indian demand shows up.
- Anchors: Trace £29-39, VoiceInk $25-49, Aiko $24, MacWhisper about €44-64 (verified).

**Kill rule (pre-registered)**
- Fewer than **10 paid sales** within **14 days** of the Show HN → stop feature work.
- Refunds within the window do not count as sales.
- The $19 founder price counts.
- At that point, publish a post-mortem with per-channel numbers in `docs/research/`, and either (a) open-source it as a portfolio piece, or (b) fold the reliability engine into AWOS as a verified-routine.
- **Early kill:** if G0(c) fails twice after reworking the headline, stop before milestone 2.
- **Continue signal:** 10 or more sales and a refund rate under 20% → raise to $29, submit the MAS build, start the Hinglish mode.

---

## 8. Open questions and the cheapest way to answer each

| # | Question | Cheapest test | Cost |
|---|---|---|---|
| Q1 | Can we capture system audio and mic reliably on macOS 26 (ScreenCaptureKit vs Core Audio process taps), with acceptable permission prompts? | Swift CLI prototype; 3 real calls; `kill -9` test | 1-2 days |
| Q2 | Does Qwen (6.3 GB) plus Zoom plus a browser fit in 16 GB without swapping? | Run the summary right after a real call; record `vm_stat` and memory pressure; compare with Qwen3-4B | 2 hours |
| Q3 | Real-mic WER, mic bleed and echo with laptop speakers vs headphones? | 3 real calls, both setups; hand-check 5 minutes of each | Half a day |
| Q4 | Does the pipeline hold up on files of 2-4 hours (memory growth, loops, coverage)? | Run a public 3-hour podcast or lecture through it | 1 hour of compute |
| Q5 | Will anyone pay $29 for "running Markdown doc plus never-lose"? | Personal outreach to about 8 named HN commenters and Trace buyers, plus a landing page waitlist | 1 day |
| Q6 | How many units did Trace actually sell? (calibrates the ceiling) | Ask the developer (AG342) politely on HN or by email | 10 min |
| Q7 | Does Paddle onboard an Indian individual seller, and with what payout method and fees? | Apply on day 1; Polar in parallel | 1 hour + wait |
| Q8 | How is the income taxed in India (export of services, GST threshold, LUT, FIRA)? | One chartered-accountant consult | One fee |
| Q9 | Does EventKit see Outlook or Exchange events when the account is added to macOS Calendar? | Add a test Exchange or Outlook.com account; read it with EventKit | 1 hour |
| Q10 | Licenses for the speaker-embedding models (3D-Speaker ERes2Net, NeMo TitaNet)? | Fetch the model cards and LICENSE files | 15 min |
| Q11 | Will Apple add system-audio meeting capture to Notes in macOS 27? | Can't be known before WWDC in June 2027. Kill rule and payback both fall before that. | $0 |
| Q12 | Does the Hinglish fine-tune (Tara) really reach about 8-14% WER on real speech? | Convert to ggml; test on 20 real clips the owner records | 1 day (later) |

---

## 9. Sources

**Competitors and pricing**
- Trace: https://traceapp.info/buy ; https://news.ycombinator.com/item?id=48521236 (comments 48545225, 48536438, 48539643, 48539830, 48538789, 48534192, 48534468, 48535261)
- Granola: https://www.granola.ai/pricing
- MacWhisper: https://www.macwhisper.com/ ; https://goodsnooze.gumroad.com/l/macwhisper
- Superwhisper: https://superwhisper.com/docs/billing/plans.md
- Wispr Flow: https://wisprflow.ai/pricing ; https://wisprflow.ai/india
- VoiceInk: https://tryvoiceink.com/ ; https://github.com/Beingpax/VoiceInk/issues/801 ; /issues/727 ; /issues/31 ; /issues/2
- Spokenly: https://spokenly.app/
- Meetily: https://github.com/Zackriya-Solutions/meetily ; https://meetily.ai/pricing
- Handy: https://api.github.com/repos/cjpais/Handy ; https://github.com/cjpais/Handy/issues/1283
- FluidAudio: https://github.com/FluidInference/FluidAudio
- Buzz: https://github.com/chidiwilliams/buzz/issues/314
- Aiko and App Store lookups: https://itunes.apple.com/search?term=aiko&entity=macSoftware ; https://itunes.apple.com/lookup?id=1668083311,1672085276,6751968220&country=us
- Apple Notes and dictation: https://support.apple.com/guide/mac-help/mchl2102c2ae ; https://support.apple.com/en-gw/guide/notes/apdb5106e334/mac

**HN threads**
- https://news.ycombinator.com/item?id=50042333 (BetterWispr; comments 50045136, 50044611, 50044767)
- https://news.ycombinator.com/item?id=48533220 (crash recovery)
- https://news.ycombinator.com/item?id=49244333
- https://news.ycombinator.com/item?id=45923353
- https://news.ycombinator.com/item?id=49295241
- https://news.ycombinator.com/item?id=47040375
- https://news.ycombinator.com/item?id=45591222
- https://news.ycombinator.com/item?id=47254300
- https://news.ycombinator.com/item?id=47013088

**App Store reviews**
- https://apps.apple.com/us/app/id6497229487?see-all=reviews
- https://apps.apple.com/in/app/id1668083311?see-all=reviews
- https://apps.apple.com/gb/app/id1668083311?see-all=reviews
- https://apps.apple.com/us/app/id6740315592?see-all=reviews
- https://apps.apple.com/us/app/id6471464415?see-all=reviews
- RSS feed: https://itunes.apple.com/us/rss/customerreviews/page=1/id=6497229487/sortby=mostrecent/json

**India and Hinglish**
- https://techcrunch.com/2026/05/09/voice-ai-in-india-is-hard-wispr-flow-is-betting-on-it-anyway/
- https://huggingface.co/Trelis/tara
- https://itunes.apple.com/search?term=dictation&entity=macSoftware&country=in

**Business mechanics**
- Apple: https://developer.apple.com/programs/enroll/ ; https://developer.apple.com/developer-id/ ; https://developer.apple.com/app-store/small-business-program/ ; https://developer.apple.com/app-store/review/guidelines/
- Payments: https://www.paddle.com/pricing ; https://polar.sh/docs/merchant-of-record/supported-countries ; https://docs.lemonsqueezy.com/help/getting-started/supported-countries ; https://gumroad.com/pricing ; https://stripe.com/in/pricing ; https://dodopayments.com/pricing
- Setapp: https://docs.setapp.com/docs/setapp-membership-revenue
- Hosting: https://developers.cloudflare.com/r2/pricing/
- Revenue base rates: https://trustmrr.com/startup/turbowhisper ; https://soff.es/blog/redacted-for-mac-launch

**Models and licenses**
- https://huggingface.co/api/models/openai/whisper-large-v3-turbo
- https://huggingface.co/api/models/nvidia/parakeet-tdt-0.6b-v3
- https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models

**Local measurements**
- `.awos/jobs/voice_spike/results/` (summary_table.txt, pywcpp_*.jsonl, parakeet_tdt_v3.jsonl, diar_sherpa.jsonl, llm_qwen.jsonl, whisper_turbo.jsonl)
- Scripts in `.awos/jobs/voice_spike/scripts/`

**Prior work:** `docs/research/local_ai_demand_check_2026-10.md`
