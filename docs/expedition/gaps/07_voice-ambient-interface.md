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
