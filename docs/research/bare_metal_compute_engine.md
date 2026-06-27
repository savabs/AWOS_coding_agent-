---
title: "Research: Bare-Metal Compute Engine"
tags:
  - doc/research
  - topic/compute
  - topic/local-inference
  - phase/2
---

# Research: Bare-Metal Compute Engine for AWOS

> **Status:** Initial research stub (2026-06-16)
> **Canonical identity:** [[VISION]] v2.3 — Bare-Metal Compute Engine section

## Dual compute (API + local)

AWOS supports **both**. Neither is an afterthought.

| Path | AWOS optimizes | Cannot control |
|------|----------------|----------------|
| **API** | Routing, cache, memory, verify, cost caps, orchestration | Provider GPU/CPU/KV |
| **Local** | All of the above + `compute/` hardware stack | — |

Hybrid deployments are the default. `escalation_engine.py` / `ml_router.py` already route API models today; `compute/` extends local path in Stage 2.

## Problem (local path specifically)

Python orchestration leaves performance on the table for **local** inference when we don't own the hardware path:

- Open-source models on consumer GPUs
- Enterprise on-premise private servers
- Organizations that cannot send data off-network

On the **API path**, AWOS cannot configure provider hardware — but still optimizes routing, cache, memory, and verification around API limits.

AWOS metric (useful work / dollar / second / watt) on local deployments requires owning the **hardware execution path**; on API deployments it requires minimizing tokens, retries, and wasted tier escalations.

## Thesis

The LLM is a statistical prediction engine. AWOS is the deterministic kernel + **bare-metal compute runtime** that harnesses it.

Value accrues to whoever prevents commodity intelligence from burning cash and latency on:

- IPC copies between orchestrator and inference process
- KV cache re-tokenization on every task switch
- Generic unfused GPU kernels with idle VRAM bandwidth

## Architecture (target)

```
AWOS Kernel (native) ←→ Shared KV Cache (shm/mmap) ←→ ComputeDevice backends
                              ↓
                    CPU_AVX2 | GPU_CUDA | GPU_HIP (later)
```

### ComputeDevice interface (C++)

- `load_model_weights(path)` — quantized weights into device-optimal layout
- `execute_inference_fused(tokens, kv_handle)` — fused attention + decode
- `preempt()` — token-level abort on early verify failure
- `profile()` — VRAM, SM count, AVX caps → hardware-aware routing

## Substrate strategy (not rewrite from scratch)

**Phase 1:** Fork/extend **ggml + llama.cpp** as inference substrate.

- Proven quantization, CUDA backends, model loading
- AWOS adds: shared KV segments, kernel fusion hooks, scheduler integration

**Phase 2:** Custom AWOS CUDA kernels for:

- Fused context-match + attention blocks
- Zero-copy handoff between agent memory tiers and KV cache

**Phase 3:** HIP/Vulkan for AMD enterprise GPUs.

## Zero-copy shared memory

- `shm_open` + `mmap` region for active KV blocks keyed by `session_id` + `context_id`
- Kernel repoints context pointer on task switch — no re-tokenize
- Python FFI: `awos_compute.switch_context(ctx_id)` from orchestrator

## Hardware-aware routing (extends ml_router)

Features beyond cost:

- `vram_free_mb`, `gpu_util`, `cpu_avx_level`
- Historical success rate per (task_type, device, model_quant)
- Preempt long generation when verifier signals early failure

## Dev hardware (this repo)

| Device | Spec | Implication |
|--------|------|-------------|
| GPU | NVIDIA GTX 1650, 4GB VRAM | Q4 7B max; forces KV discipline + fusion |
| CPU | AMD Ryzen 5 5500U, AVX2+FMA | No AVX-512; CPU path uses AVX2 intrinsics |
| OS | Linux 6.8 | `shm_open`, CUDA 560.x |

**Build order:** CUDA GPU inference path first → CPU AVX2 fallback → scale to enterprise multi-GPU.

## Open questions

1. Rust vs C++ for `compute/` core? (C++ aligns with CUDA/ggml ecosystem)
2. Embed llama.cpp as submodule vs dynamic link?
3. How does shared KV interact with RuntimeSession (planned `runtime_session.py`)?

## Related

- [[VISION]] — Bare-Metal Compute Engine
- `docs/HOW_TO_CODE_GPU_KERNEL.md` — CUDA fundamentals
- `scaffold/agent/escalation_engine.py` — routing to extend
- `scaffold/agent/ml_router.py` — bandit features to extend

## Next step

Spec: `docs/specs/compute_engine_spec.md` with atomic steps:

1. `compute/` directory scaffold + CMake
2. Hardware profiler CLI (`awos hardware`)
3. ggml bridge — load Q4 model, single inference call from Python
4. Shared KV segment POC
5. Integrate with escalation routing
