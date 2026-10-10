# Gap 07: Owner interface (on-device speech, ambient/proactive interaction, I/O energy)

Explorer chart, 2026-10-10. Scope: how the owner commands, interrupts and supervises a local-first agent computer without an IDE. Covers ASR/TTS/duplex models, the always-listening energy budget, why classic voice assistants stalled, and proactive-agent research. Includes one small measurement on the owner's M5 laptop.

## Summary

The local speech stack is a solved commodity. It is not a research frontier. Streaming ASR (Parakeet TDT on the Neural Engine, Kyutai STT, Apple SpeechAnalyzer) runs 70-360x faster than real time on Apple Silicon. On clean speech its word error rate (WER) is about 2%, and on hard conversational audio about 12-14%. Silero VAD runs at about 1,200x real time, and a commercial wake word uses under 1% of a Raspberry Pi 5 CPU. So the always-on listening tax is negligible next to the LLM. Full-duplex speech-to-speech models (Moshi: 7B, about 200 ms latency) feel natural, but their reasoning sits at small-LM level (Helium 54.3 MMLU). That makes them the wrong core for an agent that must act correctly. The bottleneck is not hearing or speaking. It is two things: (a) grounding a spoken command in a verifiable task, and (b) deciding when to interrupt the owner. Both map onto machinery the Gatekeeper already has: the verification gate, and a calibrated cost threshold for escalating to a human.

**Recommendation:** use a cascaded, local, streaming stack: wake word or push-to-talk → VAD → streaming ASR → the existing router → streaming TTS. Speech is a thin I/O shell around the Gatekeeper. Proactivity goes through an explicit expected-utility gate, with notifications as the default output channel and voice reserved for urgent items.

## What matters

1. **A cascade beats end-to-end duplex for an agent.** Kyutai's own product (Unmute) wraps a text LLM with STT and TTS. Its targets are STT TTFT <50 ms, LLM TTFT <200 ms and TTS first audio <450 ms, for under 1 s in total. Semantic end-of-turn prediction comes from the STT model itself. A cascade keeps a text transcript, which is auditable, can be replayed as a routine, and can be verified. It also lets the Gatekeeper route to any model. Moshi folds ASR, LLM and TTS into one 7B model at 12.5 Hz with about 200 ms practical latency. The paper reports Helium at 54.3 MMLU against 62.5 for Mistral 7B, so you gain conversational fluidity and lose reasoning and routing.
2. **ASR on the Neural Engine.** FluidAudio (CoreML) measures Parakeet TDT v2 at 2.1% WER and 145.8x RTFx on LibriSpeech test-clean (M4 Pro). Parakeet EOU in streaming mode gets 4.87% WER at 12.5x real time with 320 ms chunks on a base M2, and 8.29% at 160 ms chunks. Argmax measured long-form earnings calls: Apple SpeechAnalyzer 14.0% WER at 70x, WhisperKit small.en 12.8% at 35x, Parakeet v2 11.7% at 359x. The ANE leaves the GPU free for the local LLM, which is the real contention point on a 16 GB machine.
3. **Whisper large-v3 on the GPU is the wrong default.** I measured whisper.cpp 1.x (ggml 0.25.1, Metal) on the owner's **Apple M5 with 16 GB**, using an 8.44 s synthetic command made with `say`. Model load took 3.14 s, encode 1.14 s and decode 1.72 s, for about 2.9 s warm. Peak RSS was 4.1 GB. The transcript was exactly right. That is about 3x real time, but it uses the GPU and about a quarter of the RAM. Whisper also pads every input to a 30 s window, which penalises short commands. Parakeet on the ANE is far cheaper for the same English accuracy.
4. **TTS is cheap if it streams.** Kokoro-82M runs at 23.2x RTFx in Swift/CoreML (1.5 GB peak RAM, per FluidAudio). Kyutai TTS takes streaming text input, so speech can start before the LLM finishes. In my measurement on the M5, macOS `say` took 2.53 s wall time to render a 5.34 s reply to file, which is a usable zero-install fallback.
5. **The listening tax is tiny.** Porcupine's vendor numbers: 0.6% CPU on a Raspberry Pi 5, 97.3% detection at 1 false alarm per 10 hours. Silero VAD processes a 30 ms chunk in about 1 ms on one CPU thread (about 3% duty on one core), or at about 1,230x real time on the ANE via CoreML. A secondary source gives 1-5 mW for KWS on a dedicated DSP. On a Mac with no DSP exposed, assume an always-on listener adds a fraction of a percent of one efficiency core. That is well below the idle floor of the box itself.
6. **Turn-taking is its own evaluation axis.** Full-Duplex-Bench (ASRU 2025) scores pause handling, backchannels, turn-taking and interruption with automatic metrics. For AWOS, barge-in ("stop, not that file") has to cancel the in-flight action and the TTS within one VAD frame. That is a host-loop requirement, not a model one.
7. **Proactivity is a decision-theory problem, and the cost of interrupting dominates.** Horvitz (CHI '99), whose Lumiere prototypes underpinned the Office Assistant, framed it this way: act, ask, or do nothing, depending on the expected utility under an inferred probability of the user's goal. PRISM (ICLR 2026) reuses the same rule: intervene only when calibrated P(accept) exceeds a threshold set by the asymmetric costs of a missed help and a false alarm. It reports 22.78% fewer false alarms and 20.14% higher F1 on ProactiveBench. The original Proactive Agent paper (arXiv 2410.12361) found false alarms to be the main failure. A fine-tuned model reached F1 0.73 and beat GPT-4o. Google's Magic Cue (Pixel 10, Gemini Nano, fully on-device) shows the shipping pattern: no wake word, the suggestion is a one-tap chip inside the current context, and nothing interrupts.

## What does not matter (yet)

- **Full-duplex speech-to-speech as the agent brain.** Watch it as a front-end option for chit-chat and backchannels. It is not where correctness comes from.
- **Voice cloning and expressive TTS.** These make no difference to work output. A clear neutral voice is enough.
- **Wake-word accuracy tuning beyond the vendor level.** At 1 false alarm per 10 hours, the cost is a stray transcript, and the gate catches it.
- **Dedicated DSP hardware for listening.** The energy is already a rounding error next to LLM inference.

## Lessons from voice assistants (synthesis)

Siri, Alexa, Google Assistant and Cortana plateaued on slot-filling grammars. A command either matched an intent schema or failed. Recovery was "Sorry, I didn't get that", with no shared state to repair. The LLM shift solves open-vocabulary understanding but adds latency and hallucinated actions. The durable lessons for AWOS:

1. **Ground every spoken command into an explicit task card.** The card holds goal, scope and acceptance check, and is read back by voice or on screen before anything irreversible runs. This is Horvitz's "maintain mutual understanding" principle, and it is also how the verification gate already works.
2. **Error recovery means repairing the plan, not repeating the speech.** Keep the transcript and let the owner correct one slot ("not payments, billing").
3. **Most voice use is short and routine.** That is exactly the verified-routine replay path. Voice should hit replay first.

*Caveat:* primary sources on Alexa/Siri internal completion metrics were not retrieved in this pass (search budget exhausted). This section is synthesis and should be cited before use.

## Implications for AWOS

**Recommended I/O stack (Apple Silicon host):**

| Stage | Choice | Runs on | Latency budget | Energy note |
|---|---|---|---|---|
| Activation | Push-to-talk hotkey (default); Porcupine/openWakeWord optional | CPU | <100 ms | <1% of one core (vendor Pi 5 figure) |
| VAD + endpointing | Silero VAD v6 (CoreML), plus semantic EOU from the ASR | ANE/CPU | 1 frame (32 ms) | about 0.1% at 1,230x RTFx |
| ASR | Parakeet TDT v2/EOU (FluidAudio) streaming; Apple SpeechAnalyzer fallback | ANE | partials every 160-320 ms; final <300 ms after EOU | GPU stays free for the LLM |
| Understanding | Existing Gatekeeper router: replay → local LLM → cloud | GPU/cloud | TTFT <200 ms for replay/local | dominant cost |
| Confirmation | Task card shown and spoken when the action is irreversible | UI | — | — |
| TTS | Kokoro-82M CoreML streaming; `say` fallback | ANE/CPU | first audio <300 ms | 23x RTFx |
| Proactive output | Notification or chip by default; voice only above an urgency threshold | UI | — | — |

**End-to-end target:** under 1 s from end of speech to first audio for replayed routines. The time to finish the task is whatever the Gatekeeper needs. Speak an acknowledgement right away and report the result when verification passes.

**Proactive gate:** surface a suggestion only when calibrated P(accept) × value exceeds the interrupt cost. The interrupt cost is set by channel (chip < notification < voice) and by the owner's state (in a call, typing, idle). Log every accept and dismiss as reward evidence. That gives the existing reward store a direct signal for tuning the threshold per owner. It also gives a cheap first target for the "gets better at the owner's work" claim.

**Measurement to add:** a `voice_probe` live proof that records per-stage latency, plus `powermetrics` ANE/GPU/CPU milliwatts (needs sudo) for idle-listening, ASR and TTS phases. The watts column above is still estimated, not measured.

## Open questions

1. Measured idle listening power on the M5 with VAD always on versus push-to-talk only. This needs `sudo powermetrics`, which was unavailable here.
2. Can the ANE hold Parakeet and a small local LLM at the same time, or do they contend for memory bandwidth on 16 GB?
3. Does semantic EOU (Kyutai, Parakeet EOU) actually reduce cut-offs on technical dictation full of file names and identifiers? Custom vocabulary may matter more than model choice.
4. What dismiss rate makes proactive suggestions net-negative for this owner? It needs an A/B test with logged accept and dismiss.

## Key resources

- https://arxiv.org/html/2410.00037v2: Moshi paper. Duplex architecture, 160/200 ms latency, Helium MMLU 54.3.
- https://kyutai.org/stt: Kyutai STT 1B/2.6B, 0.5 s/2.5 s delay, semantic VAD, MLX support.
- https://github.com/kyutai-labs/delayed-streams-modeling and https://arxiv.org/html/2509.08753v2: DSM streaming STT/TTS.
- https://kyutai.org/unmute/ and https://www.mintlify.com/kyutai-labs/unmute/architecture/overview: Cascaded voice wrapper with per-stage latency targets.
- https://docs.fluidinference.com/reference/benchmarks.md: Apple Silicon CoreML numbers for Parakeet, Silero VAD, Kokoro and diarization.
- https://www.argmaxinc.com/blog/apple-and-argmax: SpeechAnalyzer vs WhisperKit vs Parakeet on earnings22.
- https://developer.nvidia.com/blog/nvidia-speech-ai-models-deliver-industry-leading-accuracy-and-performance: Parakeet TDT 0.6B v2, Open ASR leaderboard 6.05% average WER.
- https://picovoice.ai/products/voice/wake-word/: Porcupine CPU and accuracy (vendor-reported).
- https://arxiv.org/abs/2503.04721: Full-Duplex-Bench turn-taking metrics.
- https://erichorvitz.com/uiact.htm and https://erichorvitz.com/lumiere.htm: Mixed-initiative principles; Lumiere as basis of the Office Assistant.
- https://iclr.cc/virtual/2026/poster/10007161: PRISM, a cost-derived acceptance threshold for proactive agents.
- https://arxiv.org/pdf/2410.12361: Proactive Agent / ProactiveBench, false alarms as the dominant failure.
- https://arxiv.org/html/2602.04482v1: ProAgentBench, proactive assistance from real work data.
- https://www.deeplearning.ai/the-batch/inside-magic-cue-googles-new-ai-assistant-for-pixel-10 and https://9to5google.com/2025/08/20/pixel-10-magic-cue-launch/: Magic Cue design.

## Sources

All URLs above were consulted. The secondary figure of 1-5 mW for KWS is from https://www.codesota.com/building-blocks/keyword-spotting. The Silero figure of about 1 ms per 30 ms chunk is from the Silero VAD project README (mirror: https://gitee.com/RapidAI/silero-vad). Local measurement: whisper-cli with ggml-large-v3 on an Apple M5 (16 GB), plus macOS `say`, run on 2026-10-10. Raw output was in /tmp/awos_voice_probe and is not committed.

## Freshness update (2026-10-10, via arXiv/GitHub/HN/HF APIs)

Method note: WebSearch was unavailable and the arXiv API returned HTTP 429 after the first few calls, so paper metadata and abstracts below come from the Hugging Face papers API (huggingface.co/api/papers/ID), plus GitHub releases, HN search and the FluidAudio docs page. Abstract text was read; full papers were not.

### New since the chart
1. **Voice agents are far less reliable at tasks than at talking (tau-Voice, 2026-03-14).** On 278 grounded tasks, text GPT-5 (reasoning) reaches 85% pass@1. Full-duplex voice agents reach 31-51% in clean audio and 26-38% with noise and accents, which is 30-45% of text capability. 79-90% of failures are agent behaviour, not audio. https://huggingface.co/papers/2603.13686. Why it matters: this is direct evidence for the chart's cascade recommendation. Keep a text brain behind the voice shell.
2. **APEX-Voice (2026-09-28).** 120 stateful professional workflows. Five frontier real-time voice agents (GPT-Live-1, Gemini-3.8-Live, Grok-Voice-Think-2.0, Step-Audio3, GPT-realtime-2.1) all score at most 25% Pass@1, and the best Reliable@3 is 10.8%. Stateful coordination is the dominant failure. https://huggingface.co/papers/2609.34973. Why it matters: it confirms that fluent duplex speech does not mean correct work. The task card and verification gate are where reliability comes from.
3. **Frontend/backend split for duplex models (2026-09-18).** A duplex speech frontend emits a delegation token, forwards streaming ASR to a text LLM backend for tool calls, and speaks the result back through streaming TTS. It reports 92-97% tool-call recall. https://huggingface.co/papers/2609.19334. Why it matters: research is converging on the chart's architecture. Treat a duplex model as an optional front-end and keep the Gatekeeper as the backend.
4. **Duplex models speak when addressed or after silence, not when needed (2026-09-17).** Across five model families, being addressed and silence are far more reliable triggers than false facts or hazards. https://huggingface.co/papers/2609.19596. Why it matters: duplex models do not do the proactive "should I interrupt" judgement. That stays with the explicit expected-utility gate.
5. **Interruption recovery benchmark, IHBench (2026-06-17).** It tests whether an agent resumes the correct workflow step after barge-in. Earlier benchmarks only measured barge-in timing. https://huggingface.co/papers/2606.19595. Why it matters: the chart treats barge-in as "cancel within one VAD frame". Recovery of plan state afterwards is a second requirement. It supports the chart's "repair the plan, not the speech" lesson.
6. **Proactive-agent research expanded.** PARE / Pare-Bench (143 tasks, state-machine apps with simulated active users, 2026-04-01): https://huggingface.co/papers/2604.00842. A temporal-graph trigger model that decides when to wake an agent without calling an LLM per event, with +16.7 mean F1 over 14 LLM backbones (2026-05-28): https://huggingface.co/papers/2605.30152. A "Foundations of Proactive Agents" paper (Task Capability, Temporal Allocation, Trust; 2026-09-29): https://huggingface.co/papers/2609.37267. Why it matters: the trigger can be a cheap non-LLM model over the OS event stream, which suits the idle-time and watt budget. It is not yet validated for AWOS.
7. **FluidAudio is still moving fast.** Latest releases v0.17.4 (2026-09-25, Nemotron 3 diarization running on the M3 Neural Engine) through v0.17.7 (2026-10-08, "Paradee-8M"). The v0.17.4 release notes also mention an app-logger option to keep recognised words off the console. https://github.com/FluidInference/FluidAudio/releases. Why it matters: the dependency is active, but the API is pre-1.0 and changes monthly, so pin a version.
8. **Open-source voice-agent stacks matured.** huggingface/speech-to-speech reached v1.0.0 on 2026-09-06 (13.4k stars, "voice agents with open-source models", pushed 2026-10-09): https://github.com/huggingface/speech-to-speech. moonshine-ai/moonshine (11.2k stars, low-latency STT, intent recognition and TTS; v0.1.5 on 2026-08-24): https://github.com/moonshine-ai/moonshine. Why it matters: these are cascade references and Moonshine is an alternative to Parakeet. Neither was benchmarked here.
9. **Kyutai released a small TTS and post-training notes.** kyutai/pocket-tts (CC-BY-4.0, updated 2026-10-01): https://huggingface.co/kyutai/pocket-tts. A Kyutai blog post on "Post-training speech models for better interactivity" (2026-06-10) exists at https://kyutai.org/blog/2026-06-10-interactivity, but its body did not render in a plain fetch, so I did not read it. Why it matters: another candidate for the TTS slot.
10. **Hobbyist voice-for-coding-agent tools appeared**, e.g. https://github.com/CakeCrusher/full_duplex_code (2026-09-29), https://github.com/dixonSolutions/AgentVoice, and https://tryluke.dev/ (voice planning for coding agents, 2026-10-07), found via HN. Why it matters: they show demand and the pattern (voice as a shell over an existing coding agent). They have no evidence on reliability.
11. **Gemini 3.8 Flash TTS (2026-09-23, HN 331 points)** is a cloud TTS release: https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-8-text-to-speech/. Why it matters: little for a local-first stack, but it sets the cloud quality bar for the TTS fallback.

### Corrections
- None found. No primary source contradicted a chart number. Two small precision notes: the FluidAudio page lists the 160 ms Parakeet EOU row at 4.78x RTFx (the chart gives no RTFx for it), and the chart's unsourced "base M2" hardware label for the EOU row was not re-checked.

### Confirmed claims
- Parakeet TDT v2: 2.1% WER, 145.8x RTFx on test-clean. Parakeet EOU streaming: 4.87% WER at 12.48x with 320 ms chunks, and 8.29% with 160 ms chunks. Silero VAD v6: 1,230.6x RTFx. Kokoro-82M Swift CoreML: 23.2x, 1.50 GB peak. All match https://docs.fluidinference.com/reference/benchmarks.md.
- Porcupine vendor figures: 0.6% CPU on Raspberry Pi 5, 97.3% accuracy at 1 false alarm per 10 hours (tested at 10 dB SNR). Confirmed at https://picovoice.ai/products/voice/wake-word/. They remain vendor-reported.
- Helium MMLU 54.3 appears in Table 7 of the Moshi paper, https://arxiv.org/html/2410.00037v2. The 160 and 200 ms latency figures also appear on the abstract page.
- PRISM (arXiv 2602.01532) and ProAgentBench (2602.04482) exist with the dates implied by the chart.

### Still unverified
- Mistral 7B at 62.5 MMLU, the Argmax earnings-call WERs, and the Moshi/Unmute latency budgets were not re-fetched.
- The 1-5 mW KWS figure (secondary source) and the Silero "about 1 ms per 30 ms chunk" claim.
- PRISM's 22.78% fewer false alarms and 20.14% F1 gain, and the 0.73 F1 in Proactive Agent, were not re-read against the papers.
- Idle-listening watts on the M5, ANE and local-LLM memory contention, EOU behaviour on identifier-heavy dictation, and the owner's dismiss-rate threshold are still open. None of the new items measures them.
- Alexa/Siri completion metrics are still uncited.
- The new benchmark scores are from abstracts only, and the voice agents tested are mostly cloud systems.
