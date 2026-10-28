# AWOS Compute Engine (`compute/`)

Bare-metal inference runtime for localized intelligence.

**Status:** Scaffold placeholder — implementation begins Stage 2 per [[VISION]] v2.3.

## Purpose

- Direct CPU (AVX2/AVX-512) and GPU (CUDA/HIP) execution paths
- Zero-copy shared KV cache between kernel and inference engine
- Hardware-aware scheduling and token-level preemption
- FFI bridge from Python orchestration (`scaffold/agent/`)

## Planned layout

```
compute/
├── README.md           (this file)
├── CMakeLists.txt
├── include/
│   └── awos/
│       ├── compute_device.hpp
│       ├── context_cache.hpp
│       └── hardware_profile.hpp
├── src/
│   ├── cpu_avx2.cpp
│   ├── cuda_device.cu
│   └── shared_kv.cpp
├── kernels/
│   └── fused_attention.cu
└── bindings/
    └── python/         (pybind11 or ctypes FFI)
```

## Dev hardware target

- Primary: NVIDIA CUDA (GTX 1650 4GB — dev constraint)
- Fallback: CPU AVX2 (Ryzen 5500U)

## Research

See `docs/research/bare_metal_compute_engine.md`.
