"""
local_runtime.py — runtime policy for the local llama-server (REA roadmap B3-B5).

Stdlib only, so scripts/local_model.sh can run it as a plain script:

    python3 scaffold/agent/local_runtime.py plan --shell   # policy + admission -> shell vars
    python3 scaffold/agent/local_runtime.py status         # human summary (doctor uses status())
    python3 scaffold/agent/local_runtime.py watch          # stop a server that has slept too long
    python3 scaffold/agent/local_runtime.py gguf FILE      # architecture / KV cost of a GGUF

B3  speculative decoding: AWOS_LOCAL_SPEC=off|ngram|draft -> llama-server flags (spec_args).
B4  static-prefix KV persisted across tasks and server restarts through llama-server's
    slot save/restore API, keyed by hash(GGUF identity, llama.cpp build, chat template,
    KV-relevant flags, prefix text) (ensure_prefix_cached / prepare_first_step).
B5  power-aware residency: llama-server's own --sleep-idle-seconds unloads the model
    after AWOS_LOCAL_IDLE_S (default 300 s) without requests and reloads it on the next
    one; `watch` optionally exits a server that has stayed asleep. A battery policy
    (pmset) is min/max-merged with the user's settings, and memory admission refuses
    model + draft + context + host-cache combinations that would not fit in
    physical RAM minus an OS reserve.

Hook points for the agent loop (this module never edits the loop):

    from scaffold.agent import local_runtime
    # once per task, before the FIRST model call, with the messages that never change
    # (system prompt; tools if the request sends `tools`):
    info = local_runtime.prepare_first_step(static_messages, tools=tools)
    # -> {"status": "restored"|"saved"|"warm"|"skipped"|"unsupported"|"error", ...}
    # It never raises and returns "skipped" unless AWOS_PROVIDER=local (or a local/
    # agent model) and AWOS_LOCAL_PREFIX_CACHE is not 0. Optionally
    # local_runtime.ensure_server() first (AWOS_LOCAL_AUTOSTART=1 makes
    # prepare_first_step do that itself) to restart a server the watchdog stopped.

    # around the whole task (all steps, commands and the gate):
    with local_runtime.task_lease(task_id):
        run()
    # A heartbeat file under $AWOS_MODELS_DIR/leases/ is refreshed by a daemon thread;
    # `watch` never stops the server, and never restarts it for a power-source change,
    # while a live lease exists. plan() also keeps --sleep-idle-seconds at or above the
    # loop's command timeout + AWOS_LOCAL_IDLE_MARGIN_S, so llama-server's own idle
    # unload cannot fire while a single bash command runs.

Findings on this machine (2026-10-16, llama.cpp b11146, Qwen3.5-9B Q4_K_M):
Qwen3.5 is a hybrid architecture (`qwen35`): 32 blocks, full attention every 4th block
(8 attention layers, 4 KV heads x 256+256 dims), the other 24 are Gated-DeltaNet
recurrent layers with a fixed-size state. Consequences: llama-server disables
--cache-reuse (KV shifting) for it, and a cached prompt is only reusable when the new
prompt starts with *all* of the cached tokens (a recurrent state cannot be rolled
back to an arbitrary earlier position). So the saved prefix must be exactly the
static prefix (evaluated with n_predict=0), never prefix + generated tokens.
The GGUF has no MTP/nextn tensors (so no draft-mtp). Speculation still works:
measured on battery, ngram-simple drafting gave 26.6 tok/s vs 10.4 without on an
echo-heavy reply (47/48 drafts accepted) and no change on novel text. No same-tokenizer
draft model is on disk (the only other GGUF, Qwen2.5-3B, has a 151936-token qwen2
vocabulary vs 248320 qwen35), so `draft` is wired and validated but unmeasured.
Slot restore (1858-token prefix, 108 MB file, ~25 ms) cut the first step after a
server restart from 1880 prompt tokens / 8048 ms to 22 tokens / 428 ms.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shlex
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

MIB = 1024 * 1024

# ── GGUF metadata ────────────────────────────────────────────────────────────
_GGUF_SCALAR = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?",
                10: "Q", 11: "q", 12: "d"}


def _rd(fh, fmt):
    return struct.unpack("<" + fmt, fh.read(struct.calcsize(fmt)))[0]


def _rs(fh) -> str:
    return fh.read(_rd(fh, "Q")).decode("utf-8", "replace")


def _gguf_value(fh, typ):
    if typ == 8:
        return _rs(fh)
    if typ == 9:
        et, n = _rd(fh, "I"), _rd(fh, "Q")
        if et == 8:
            for _ in range(n):
                _rs(fh)
            return f"<str[{n}]>"
        if et == 9:
            raise ValueError("nested GGUF arrays unsupported")
        size = struct.calcsize(_GGUF_SCALAR[et])
        if n <= 64:
            return [_rd(fh, _GGUF_SCALAR[et]) for _ in range(n)]
        fh.seek(size * n, 1)
        return f"<arr[{n}]>"
    return _rd(fh, _GGUF_SCALAR[typ])


def gguf_metadata(path) -> dict:
    """GGUF header key/values (string arrays and arrays > 64 items summarised)."""
    out: dict = {}
    with open(Path(path).expanduser(), "rb") as fh:
        if fh.read(4) != b"GGUF":
            raise ValueError(f"{path}: not a GGUF file")
        out["gguf.version"] = _rd(fh, "I")
        out["gguf.tensor_count"] = _rd(fh, "Q")
        for _ in range(_rd(fh, "Q")):
            key = _rs(fh)
            out[key] = _gguf_value(fh, _rd(fh, "I"))
    return out


#: bytes per element for llama.cpp KV cache types (block-quant overhead included)
KV_TYPE_BYTES = {"f32": 4.0, "f16": 2.0, "bf16": 2.0, "q8_0": 34 / 32, "q5_1": 24 / 32,
                 "q5_0": 22 / 32, "q4_1": 20 / 32, "q4_0": 18 / 32, "iq4_nl": 18 / 32}


@dataclass
class ArchInfo:
    arch: str
    blocks: int
    attn_layers: int
    hybrid: bool              # recurrent/SSM layers present (no KV shifting, no partial rollback)
    vocab: int
    tokenizer_pre: str
    kv_bytes_per_token: float  # attention KV at the given cache type
    fixed_state_bytes: int     # recurrent state per sequence (independent of context)


def arch_info(meta: dict, kv_type: str = "f16") -> ArchInfo:
    a = meta.get("general.architecture", "?")
    g = lambda k, d=None: meta.get(f"{a}.{k}", d)  # noqa: E731
    blocks = int(g("block_count", 0) or 0)
    heads = g("attention.head_count", 1) or 1
    heads = max(heads) if isinstance(heads, list) else heads
    kv_heads = g("attention.head_count_kv", heads)
    emb = int(g("embedding_length", 0) or 0)
    k_len = int(g("attention.key_length", emb // heads if heads else 0) or 0)
    v_len = int(g("attention.value_length", k_len) or 0)
    interval = g("full_attention_interval")
    hybrid = g("ssm.state_size") is not None
    if isinstance(kv_heads, list):            # per-layer: 0 marks a non-attention layer
        attn_layers = sum(1 for h in kv_heads if h)
        kv_sum = sum(kv_heads)
    else:
        attn_layers = blocks // int(interval) if interval else (0 if hybrid else blocks)
        kv_sum = attn_layers * int(kv_heads)
    per_tok = kv_sum * (k_len + v_len) * KV_TYPE_BYTES.get(kv_type, 2.0)
    fixed = 0
    if hybrid:
        rec_layers = blocks - attn_layers
        inner = int(g("ssm.inner_size", 0) or 0)
        state = int(g("ssm.state_size", 0) or 0)
        groups = int(g("ssm.group_count", 0) or 0)
        conv_k = int(g("ssm.conv_kernel", 0) or 0)
        conv_ch = inner + 2 * groups * state
        fixed = rec_layers * (inner * state * 4 + max(conv_k - 1, 0) * conv_ch * 4)
    toks = meta.get("tokenizer.ggml.tokens", "")
    m = re.match(r"<str\[(\d+)\]>", toks) if isinstance(toks, str) else None
    return ArchInfo(a, blocks, attn_layers, hybrid, int(m.group(1)) if m else 0,
                    str(meta.get("tokenizer.ggml.pre", "")), per_tok, fixed)


def draft_compatible(target: ArchInfo, draft: ArchInfo) -> tuple[bool, str]:
    """A draft model must share the target's tokenizer (vocab size and pre-tokenizer)."""
    if target.vocab != draft.vocab or target.tokenizer_pre != draft.tokenizer_pre:
        return False, (f"draft tokenizer {draft.tokenizer_pre}/{draft.vocab} != target "
                       f"{target.tokenizer_pre}/{target.vocab}")
    return True, "same tokenizer"


# ── memory admission ─────────────────────────────────────────────────────────
#: Measured on this laptop (M5, 16 GB, llama.cpp b11146, -ngl 99 -fa on, f16 KV).
#: rss_mb = resident size of weights + compute buffers (measured 5897 MB RSS right after
#: load at -c 8192, minus that run's 256 MB KV and 53 MB state); kv_mb_per_1k / fixed_mb
#: are from arch_info and match a slot file (1871 tokens -> 114 MB = 1871 x 32 KiB +
#: ~53 MB recurrent state).
CATALOG = {
    "Qwen3.5-9B-Q4_K_M.gguf": {"rss_mb": 5600, "kv_mb_per_1k": 32.0, "fixed_mb": 53,
                               "note": "measured 2026-10-16, b11146, hybrid qwen35"},
}
DEFAULT_CACHE_RAM_MB = 2048      # llama-server's own default is 8192 MiB: too much at 16 GB


def physical_ram_mb(run=subprocess.run) -> int:
    try:
        if sys.platform == "darwin":
            out = run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            return int(out.stdout.strip()) // MIB
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // MIB
    except Exception:
        return 0


def os_reserve_mb(ram_mb: int, env: Optional[dict] = None) -> int:
    env = os.environ if env is None else env
    raw = (env.get("AWOS_LOCAL_OS_RESERVE_MB") or "").strip()
    if raw.isdigit():
        return int(raw)
    return max(4096, ram_mb // 4)


def model_cost(path, ctx: int, slots: int = 1, kv_type: str = "f16") -> dict:
    """{weights_mb, kv_mb, state_mb} for one model at ctx total tokens over `slots`."""
    p = Path(path).expanduser()
    cat = CATALOG.get(p.name)
    if cat and kv_type == "f16":
        return {"weights_mb": cat["rss_mb"], "kv_mb": int(cat["kv_mb_per_1k"] * ctx / 1024),
                "state_mb": cat["fixed_mb"] * slots, "source": "catalog"}
    size_mb = p.stat().st_size // MIB if p.exists() else 0
    info = arch_info(gguf_metadata(p), kv_type) if p.exists() else None
    kv = int(info.kv_bytes_per_token * ctx / MIB) if info else 0
    state = int(info.fixed_state_bytes * slots / MIB) if info else 0
    return {"weights_mb": size_mb + 600, "kv_mb": kv, "state_mb": state, "source": "estimate"}


@dataclass
class Admission:
    ok: bool
    need_mb: int
    budget_mb: int
    breakdown: dict
    message: str


def admit(model_path, ctx: int, slots: int = 1, draft_path=None, draft_ctx: int = 0,
          cache_ram_mb: int = DEFAULT_CACHE_RAM_MB, ram_mb: Optional[int] = None,
          env: Optional[dict] = None, kv_type: str = "f16") -> Admission:
    ram = physical_ram_mb() if ram_mb is None else ram_mb
    budget = ram - os_reserve_mb(ram, env) if ram else 0
    parts = {"model": model_cost(model_path, ctx, slots, kv_type)}
    if draft_path:
        parts["draft"] = model_cost(draft_path, draft_ctx or ctx, slots, kv_type)
    parts["host_cache"] = {"cache_ram_mb": max(cache_ram_mb, 0)}
    need = sum(v for part in parts.values() for k, v in part.items() if k.endswith("_mb"))
    if not ram:
        return Admission(True, need, 0, parts, "physical RAM unknown; admission skipped")
    if need <= budget:
        return Admission(True, need, budget, parts, f"fits: need {need} MB of {budget} MB budget")
    m = parts["model"]
    per_1k = max(m["kv_mb"] * 1024 / max(ctx, 1), 1e-9)
    spare = budget - (need - m["kv_mb"])
    fit_ctx = int(spare / per_1k * 1024) // 1024 * 1024 if spare > 0 else 0
    hint = (f"try AWOS_LOCAL_CONTEXT={fit_ctx}" if fit_ctx >= 4096 else
            "use a smaller model/quant")
    if draft_path:
        hint += ", drop the draft model (AWOS_LOCAL_SPEC=ngram)"
    hint += f", or lower AWOS_LOCAL_CACHE_RAM_MB (now {cache_ram_mb})"
    return Admission(False, need, budget, parts,
                     f"refused: needs ~{need} MB but the budget is {budget} MB "
                     f"({ram} MB RAM - {ram - budget} MB OS reserve); it would swap. {hint}")


# ── power + policy ───────────────────────────────────────────────────────────
@dataclass
class PowerState:
    source: str                  # "ac" | "battery" | "unknown"
    percent: Optional[int] = None


def parse_pmset(text: str) -> PowerState:
    src = "unknown"
    if "'Battery Power'" in text:
        src = "battery"
    elif "'AC Power'" in text or "'UPS Power'" in text:
        src = "ac"
    m = re.search(r"(\d{1,3})%", text)
    return PowerState(src, int(m.group(1)) if m else None)


def power_state(run=subprocess.run) -> PowerState:
    if sys.platform != "darwin":
        return PowerState("unknown")
    try:
        out = run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5)
        return parse_pmset(out.stdout or "")
    except Exception:
        return PowerState("unknown")


@dataclass
class Policy:
    name: str
    ctx_max: Optional[int] = None     # None = no cap
    allow_draft: bool = True
    spec_draft_max: int = 64
    idle_s: int = 300                 # <= 0 disables idle sleep

    @staticmethod
    def merge(*ps: "Policy") -> "Policy":
        """Most restrictive wins: min of caps, AND of permissions."""
        caps = [p.ctx_max for p in ps if p.ctx_max]
        idles = [p.idle_s for p in ps if p.idle_s > 0]
        return Policy("+".join(p.name for p in ps), min(caps) if caps else None,
                      all(p.allow_draft for p in ps), min(p.spec_draft_max for p in ps),
                      min(idles) if idles else 0)


def battery_policy(ps: PowerState, mode: str = "restrictive",
                   battery_ctx_max: Optional[int] = None) -> Policy:
    """On battery: no draft model, shorter idle. The context is only capped below 20 %
    (16384) or when AWOS_LOCAL_BATTERY_CTX_MAX is set, because a silent context change
    would make battery and AC runs of the same experiment incomparable."""
    if mode == "ignore" or ps.source != "battery":
        # idle_s=0: no opinion, so the user's AWOS_LOCAL_IDLE_S (incl. 0 = never) decides
        return Policy("ac" if ps.source == "ac" else "power-unknown", idle_s=0)
    if ps.percent is not None and ps.percent < 20:
        cap = min(16384, battery_ctx_max) if battery_ctx_max else 16384
        return Policy("battery-low", ctx_max=cap, allow_draft=False, spec_draft_max=8, idle_s=60)
    return Policy("battery", ctx_max=battery_ctx_max, allow_draft=False, spec_draft_max=16,
                  idle_s=120)


def _int_env(env, key, default, lo=None, hi=None) -> int:
    raw = (env.get(key) or "").strip()
    try:
        val = int(raw) if raw else default
    except ValueError:
        raise PlanError(f"{key}={raw!r} is not an integer")
    if lo is not None:
        val = max(lo, val)
    if hi is not None:
        val = min(hi, val)
    return val


class PlanError(Exception):
    pass


#: Mirrors scaffold/agent/loop_executor.py (DEFAULT_CMD_TIMEOUT_S / MAX_CMD_TIMEOUT_S);
#: this module stays stdlib-only and importable as a plain script, so it does not import
#: the loop. tests/test_local_runtime.py asserts the two stay equal.
DEFAULT_LOOP_CMD_TIMEOUT_S = 120
MAX_LOOP_CMD_TIMEOUT_S = 600
DEFAULT_IDLE_MARGIN_S = 60


def loop_cmd_timeout_s(env: Optional[dict] = None) -> int:
    """AWOS_LOOP_CMD_TIMEOUT_S as the loop reads it (default 120, at most 600)."""
    env = os.environ if env is None else env
    try:
        val = _int_env(env, "AWOS_LOOP_CMD_TIMEOUT_S", DEFAULT_LOOP_CMD_TIMEOUT_S, lo=1)
    except PlanError:
        val = DEFAULT_LOOP_CMD_TIMEOUT_S
    return min(MAX_LOOP_CMD_TIMEOUT_S, val)


def idle_floor_s(env: Optional[dict] = None) -> int:
    """Smallest allowed --sleep-idle-seconds: one loop command + a margin, so the model
    is never unloaded between the call that issued a command and the call after it."""
    env = os.environ if env is None else env
    return loop_cmd_timeout_s(env) + _int_env(env, "AWOS_LOCAL_IDLE_MARGIN_S",
                                              DEFAULT_IDLE_MARGIN_S, lo=0)


SPEC_MODES = ("off", "ngram", "draft")


@dataclass
class Plan:
    ctx: int
    spec: str
    spec_args: list
    idle_s: int
    cache_ram_mb: int
    policy: Policy
    power: PowerState
    admission: Admission
    notes: list = field(default_factory=list)


def models_dir(env: Optional[dict] = None) -> Path:
    env = os.environ if env is None else env
    raw = (env.get("AWOS_MODELS_DIR") or "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".cache" / "awos-models"


def model_path(env: Optional[dict] = None) -> Path:
    env = os.environ if env is None else env
    return models_dir(env) / (env.get("AWOS_LOCAL_GGUF_FILE") or "Qwen3.5-9B-Q4_K_M.gguf")


def spec_args(spec: str, draft_max: int, draft_path=None) -> list:
    if spec == "off":
        return []
    if spec == "ngram":
        # prompt-lookup style: draft the continuation of the last 12-gram if it occurred
        # earlier in the context (agents re-quote file text). Measured 26.6 vs 10.4 tok/s
        # on an echo-heavy reply; it does not fire on novel text.
        return ["--spec-type", "ngram-simple", "--spec-ngram-simple-size-n", "12",
                "--spec-ngram-simple-size-m", str(draft_max), "--spec-draft-n-max", str(draft_max)]
    return ["--spec-type", "draft-simple", "-md", str(draft_path), "-ngld", "99",
            "--spec-draft-n-max", str(draft_max), "--spec-draft-n-min", "0"]


def plan(env: Optional[dict] = None, power: Optional[PowerState] = None,
         ram_mb: Optional[int] = None) -> Plan:
    """Merge user settings with the power policy, pick spec flags, admit memory.
    Raises PlanError with a user-facing message on any refusal."""
    env = dict(os.environ if env is None else env)
    power = power or power_state()
    notes = []
    want_ctx = _int_env(env, "AWOS_LOCAL_CONTEXT", 24576, lo=512)
    slots = _int_env(env, "AWOS_LOCAL_SLOTS", 1, lo=1)
    spec = (env.get("AWOS_LOCAL_SPEC") or "off").strip().lower()
    if spec not in SPEC_MODES:
        raise PlanError(f"AWOS_LOCAL_SPEC={spec!r}: use one of {', '.join(SPEC_MODES)}")
    user = Policy("user", spec_draft_max=_int_env(env, "AWOS_LOCAL_SPEC_DRAFT_MAX", 16, 1, 64),
                  idle_s=_int_env(env, "AWOS_LOCAL_IDLE_S", 300))
    bmode = (env.get("AWOS_LOCAL_BATTERY_POLICY") or "restrictive").strip().lower()
    bctx = _int_env(env, "AWOS_LOCAL_BATTERY_CTX_MAX", 0, lo=0) or None
    pol = Policy.merge(user, battery_policy(power, bmode, bctx))
    floor = idle_floor_s(env)
    if 0 < pol.idle_s < floor:
        notes.append(f"idle {pol.idle_s}s -> {floor}s (loop command timeout "
                     f"{loop_cmd_timeout_s(env)}s + margin)")
        pol.idle_s = floor
    ctx = min(want_ctx, pol.ctx_max) if pol.ctx_max else want_ctx
    if ctx < want_ctx:
        notes.append(f"context {want_ctx} -> {ctx} ({pol.name} policy)")
    draft = None
    if spec == "draft":
        raw = (env.get("AWOS_LOCAL_DRAFT_GGUF") or "").strip()
        if not raw:
            raise PlanError("AWOS_LOCAL_SPEC=draft needs AWOS_LOCAL_DRAFT_GGUF=<path to a small "
                            "same-tokenizer GGUF>; none is configured")
        draft = Path(raw).expanduser()
        if not draft.is_file():
            raise PlanError(f"AWOS_LOCAL_DRAFT_GGUF={draft} does not exist")
        mp = model_path(env)
        if mp.is_file():
            ok, why = draft_compatible(arch_info(gguf_metadata(mp)), arch_info(gguf_metadata(draft)))
            if not ok:
                raise PlanError(f"AWOS_LOCAL_SPEC=draft refused: {why}")
        if not pol.allow_draft:
            notes.append(f"draft model off ({pol.name} policy); using ngram drafting instead")
            spec, draft = "ngram", None
    cache_ram = _int_env(env, "AWOS_LOCAL_CACHE_RAM_MB", DEFAULT_CACHE_RAM_MB, lo=0)
    adm = admit(model_path(env), ctx, slots, draft, ctx if draft else 0, cache_ram,
                ram_mb=ram_mb, env=env)
    if not adm.ok:
        raise PlanError(adm.message)
    return Plan(ctx, spec, spec_args(spec, pol.spec_draft_max, draft), pol.idle_s, cache_ram,
                pol, power, adm, notes)


def plan_shell(p: Plan) -> str:
    q = shlex.quote
    return "\n".join([
        f"AWOS_PLAN_CTX={p.ctx}",
        f"AWOS_PLAN_SPEC={q(p.spec)}",
        f"AWOS_PLAN_SPEC_ARGS={q(' '.join(p.spec_args))}",
        f"AWOS_PLAN_IDLE_S={p.idle_s}",
        f"AWOS_PLAN_CACHE_RAM_MB={p.cache_ram_mb}",
        f"AWOS_PLAN_POLICY={q(p.policy.name)}",
        f"AWOS_PLAN_NEED_MB={p.admission.need_mb}",
        f"AWOS_PLAN_BUDGET_MB={p.admission.budget_mb}",
        f"AWOS_PLAN_NOTES={q('; '.join(p.notes))}",
    ])


# ── HTTP to the local server ─────────────────────────────────────────────────
Http = Callable[[str, str, Optional[dict], float], tuple]   # (method, url, body, timeout) -> (status, json)


def http_json(method: str, url: str, body: Optional[dict] = None, timeout: float = 30.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    key = os.environ.get("AWOS_LOCAL_API_KEY", "").strip()
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def server_base(env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    raw = ((env.get("AWOS_LOCAL_BASE_URL") or env.get("AWOS_BASE_URL") or "").strip()
           or f"http://127.0.0.1:{env.get('AWOS_LOCAL_PORT') or 8080}/v1").rstrip("/")
    return raw[:-3] if raw.endswith("/v1") else raw


def server_state(base: Optional[str] = None, http: Http = http_json, timeout: float = 1.5) -> dict:
    """{"state": "up"|"sleeping"|"loading"|"down", "props": {...}, "slots": [...]}"""
    base = base or server_base()
    try:
        st, _ = http("GET", base + "/health", None, timeout)
    except Exception:
        return {"state": "down", "props": {}, "slots": []}
    if st == 503:
        return {"state": "loading", "props": {}, "slots": []}
    if st != 200:
        return {"state": "down", "props": {}, "slots": []}
    try:
        _, props = http("GET", base + "/props", None, timeout)
    except Exception:
        props = {}
    slots: list = []
    if not props.get("is_sleeping"):
        try:   # /slots on a sleeping server would not wake it, but keep the probe cheap
            st2, s = http("GET", base + "/slots", None, timeout)
            slots = s if st2 == 200 and isinstance(s, list) else []
        except Exception:
            slots = []
    return {"state": "sleeping" if props.get("is_sleeping") else "up", "props": props or {},
            "slots": slots}


# ── B4: static-prefix KV persistence ─────────────────────────────────────────
PREFIX_FILE_RE = re.compile(r"^awos-prefix-[0-9a-f]{24}\.bin$")


def slot_dir(env: Optional[dict] = None) -> Path:
    env = os.environ if env is None else env
    raw = (env.get("AWOS_LOCAL_SLOT_DIR") or "").strip()
    return Path(raw).expanduser() if raw else models_dir(env) / "slots"


def _file_identity(path: str) -> Optional[str]:
    """name:size:sha256(first+last 4 MiB)[:16], or None when the file is unreadable."""
    if not path:
        return None
    p = Path(path)
    try:
        st = p.stat()
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            h.update(fh.read(4 * MIB))
            fh.seek(max(st.st_size - 4 * MIB, 0))
            h.update(fh.read(4 * MIB))
        return f"{p.name}:{st.st_size}:{h.hexdigest()[:16]}"
    except OSError:
        return None


#: Flags that change the saved KV/state (layout or the values in it) -> number of values
#: they take (-1 = one optional value). Sampling, spec and idle flags do not matter.
#: Adapters / control vectors / RoPE+YaRN scaling change every cached K/V value.
KV_FLAG_ARITY = {
    "--ctx-size": 1, "--parallel": 1, "--flash-attn": -1, "--cache-type-k": 1,
    "--cache-type-v": 1, "--swa-full": 0, "--kv-unified": 0, "--no-kv-unified": 0,
    "--lora": 1, "--lora-scaled": 2, "--lora-init-without-apply": 0,
    "--control-vector": 1, "--control-vector-scaled": 2, "--control-vector-layer-range": 2,
    "--rope-scaling": 1, "--rope-scale": 1, "--rope-freq-base": 1, "--rope-freq-scale": 1,
    "--yarn-orig-ctx": 1, "--yarn-ext-factor": 1, "--yarn-attn-factor": 1,
    "--yarn-beta-slow": 1, "--yarn-beta-fast": 1, "--override-kv": 1,
}
KV_FLAG_ALIASES = {"-c": "--ctx-size", "-np": "--parallel", "-fa": "--flash-attn",
                   "-ctk": "--cache-type-k", "-ctv": "--cache-type-v", "-kvu": "--kv-unified",
                   "-no-kvu": "--no-kv-unified"}
KV_FLAGS = set(KV_FLAG_ARITY) | set(KV_FLAG_ALIASES)
_FILE_VALUED = {"--lora", "--lora-scaled", "--control-vector", "--control-vector-scaled"}
_NUM_RE = re.compile(r"^-?\d+(\.\d*)?([eE][-+]?\d+)?$")


def _is_value(tok: str) -> bool:
    return not tok.startswith("-") or bool(_NUM_RE.match(tok))


def kv_flags_signature(cmd: str) -> str:
    """Sorted `flag=values` for every KV-relevant flag in a llama-server command line.
    Accepts `--flag value` and `--flag=value`, short aliases, repeated flags; adapter /
    control-vector files are keyed by content identity as well as path."""
    try:
        toks = shlex.split(cmd or "")
    except ValueError:
        toks = (cmd or "").split()
    out = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        i += 1
        inline = None
        if tok.startswith("--") and "=" in tok:
            tok, inline = tok.split("=", 1)
        flag = KV_FLAG_ALIASES.get(tok, tok)
        if flag not in KV_FLAG_ARITY:
            continue
        arity = KV_FLAG_ARITY[flag]
        want = 1 if arity == -1 else arity
        values = [inline] if inline is not None else []
        while len(values) < want and i < len(toks) and _is_value(toks[i]):
            values.append(toks[i])
            i += 1
        if flag in _FILE_VALUED and values:
            ident = _file_identity(values[0])
            values[0] = f"{values[0]}@{ident or 'unreadable'}"
        out.append(f"{flag}={' '.join(values)}")
    return " ".join(sorted(out))


def server_cmd_file(env: Optional[dict] = None) -> Path:
    env = os.environ if env is None else env
    return models_dir(env) / f"llama-server.{env.get('AWOS_LOCAL_PORT') or 8080}.cmd"


def degraded_props(props: dict) -> Optional[str]:
    """Why /props cannot key a slot file (None when it can): a sleeping server, or no
    readable model file / chat template. A key over missing fields would collide
    across models or templates, so callers skip instead of keying on it."""
    if not isinstance(props, dict) or not props:
        return "no /props"
    if props.get("is_sleeping"):
        return "server sleeping"
    if not props.get("model_path"):
        return "/props has no model_path"
    if not props.get("chat_template"):
        return "/props has no chat_template"
    if _file_identity(str(props["model_path"])) is None:
        return f"model file unreadable: {props['model_path']}"
    return None


def prefix_key(props: dict, prefix_text: str, cmd: str = "") -> str:
    """hash(GGUF identity, llama.cpp build, chat template, KV flags, n_ctx, prefix text).
    Raises ValueError on degraded props (see degraded_props)."""
    why = degraded_props(props)
    if why:
        raise ValueError(f"cannot key prefix cache: {why}")
    gen = props.get("default_generation_settings") or {}
    parts = [
        "v2",
        str(_file_identity(props.get("model_path", ""))),
        str(props.get("build_info", "")),
        hashlib.sha256(str(props.get("chat_template", "")).encode()).hexdigest(),
        kv_flags_signature(cmd),
        str(gen.get("n_ctx", "")),
        hashlib.sha256(prefix_text.encode()).hexdigest(),
    ]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _gc(sdir: Path, keep: int, protect: str) -> None:
    files = sorted((p for p in sdir.glob("awos-prefix-*.bin") if PREFIX_FILE_RE.match(p.name)),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[keep:]:
        if p.name == protect:
            continue
        for q in (p, p.with_suffix(".json")):
            try:
                q.unlink()
            except OSError:
                pass


#: Turn-start markers (special tokens) a prefix may be cut before, per template family.
TURN_MARKERS = ("<|im_start|>", "<|start_header_id|>", "<start_of_turn>", "<|turn>",
                "[INST]", "<|user|>", "<|system|>")


def _tokens(base: str, http: Http, text: str) -> Optional[list]:
    """Token ids of `text` from llama-server's /tokenize (special tokens parsed, no BOS),
    None when the endpoint is unavailable."""
    try:
        st, r = http("POST", base + "/tokenize", {"content": text, "add_special": False}, 30.0)
    except Exception:
        return None
    toks = r.get("tokens") if st == 200 and isinstance(r, dict) else None
    if not isinstance(toks, list):
        return None
    return [t.get("id") if isinstance(t, dict) else t for t in toks]


def _previous_boundary(text: str, end: int) -> int:
    """Start of the last turn marker strictly before `end` (-1 when none)."""
    if end <= 0:
        return -1
    return max(text.rfind(m, 0, end - 1) for m in TURN_MARKERS)


def align_prefix(prefix_text: str, probe_text: str, base: str, http: Http,
                 max_backoffs: int = 4) -> tuple[Optional[str], str]:
    """(prefix, check) where tokens(prefix) is a prefix of tokens(probe_text).

    A text prefix is not always a token prefix (a merge can straddle the cut), and a
    slot holding tokens the next prompt does not start with is useless on a hybrid
    model. Backs off to the previous turn marker up to max_backoffs times.
    check: "ok" | "backoff:N" | "unavailable" (no /tokenize: unchecked) | "misaligned"
    (prefix None: nothing aligned, skip caching)."""
    if not probe_text.startswith(prefix_text):
        return None, "misaligned"
    full = _tokens(base, http, probe_text)
    if full is None:
        return prefix_text, "unavailable"
    cand = prefix_text
    for n in range(max_backoffs + 1):
        toks = _tokens(base, http, cand)
        if toks is None:
            return prefix_text, "unavailable"
        if toks and full[:len(toks)] == toks:
            return cand, "ok" if n == 0 else f"backoff:{n}"
        cut = _previous_boundary(cand, len(cand))
        if cut <= 0:
            break
        cand = cand[:cut]
    return None, "misaligned"


def _slot_count(props: dict, env: Optional[dict] = None) -> int:
    env = os.environ if env is None else env
    try:
        n = int((props or {}).get("total_slots") or 0)
    except (TypeError, ValueError, AttributeError):
        n = 0
    if n <= 0:
        try:
            n = int((env.get("AWOS_LOCAL_SLOTS") or "1").strip())
        except ValueError:
            n = 1
    return max(1, n)


def _wake(base: str, http: Http, slot: int, multi: bool) -> dict:
    """Wake a sleeping server with a 1-token, uncached request (it lands in the slot we
    are about to overwrite anyway) and return fresh /props ({} on failure)."""
    body = {"prompt": " ", "n_predict": 0, "cache_prompt": False}
    if multi:
        body["id_slot"] = slot
    try:
        http("POST", base + "/completion", body, 240.0)
        st, props = http("GET", base + "/props", None, 30.0)
        return props if st == 200 and isinstance(props, dict) else {}
    except Exception:
        return {}


def ensure_prefix_cached(prefix_text: str, slot: int = 0, base: Optional[str] = None,
                         http: Http = http_json, sdir: Optional[Path] = None,
                         cmd: Optional[str] = None, keep: int = 4,
                         probe_text: Optional[str] = None, wake: bool = True) -> dict:
    """Make slot `slot` hold exactly `prefix_text`'s KV/state.

    restored  a saved file for this exact key was loaded (fast first step)
    warm      the slot already holds this prefix and nothing longer (no-op)
    saved     evaluated now (n_predict=0) and saved for the next task / restart
    skipped   not safe to key or cut: server asleep (and wake=False or it stayed
              asleep), /props missing model_path / chat_template, model file
              unreadable, or the prefix is not a token prefix of `probe_text`
    unsupported  the server was started without --slot-save-path

    probe_text: a full rendered prompt that starts with prefix_text (render_prefix_probe);
    when given, /tokenize checks the cut and backs off to an earlier turn boundary.
    id_slot is sent only when the server has more than one slot.
    """
    base = base or server_base()
    sdir = Path(sdir) if sdir else slot_dir()
    t0 = time.monotonic()
    st, props = http("GET", base + "/props", None, 30.0)
    if st != 200:
        return {"status": "error", "detail": f"/props HTTP {st}"}
    multi = _slot_count(props) > 1
    if isinstance(props, dict) and props.get("is_sleeping"):
        if not wake:
            return {"status": "skipped", "detail": "server sleeping"}
        props = _wake(base, http, slot, multi)
    why = degraded_props(props)
    if why:
        return {"status": "skipped", "detail": why}
    check = "unchecked"
    if probe_text is not None:
        aligned, check = align_prefix(prefix_text, probe_text, base, http)
        if aligned is None:
            return {"status": "skipped", "detail": "prefix is not a token prefix of the "
                    "rendered prompt", "token_check": check}
        prefix_text = aligned
    if cmd is None:
        try:
            cmd = server_cmd_file().read_text()
        except OSError:
            cmd = ""
    key = prefix_key(props, prefix_text, cmd)
    name = f"awos-prefix-{key[:24]}.bin"
    meta_path = (sdir / name).with_suffix(".json")
    if (sdir / name).is_file():
        st, r = http("POST", f"{base}/slots/{slot}?action=restore", {"filename": name}, 120.0)
        if st == 200:
            try:
                want = json.loads(meta_path.read_text()).get("n_tokens")
            except (OSError, ValueError):
                want = None
            got = r.get("n_restored")
            if want is None or want == got:
                os.utime(sdir / name)
                return {"status": "restored", "key": key[:24], "n_tokens": got,
                        "token_check": check, "ms": round((time.monotonic() - t0) * 1000, 1)}
        if st == 501 or "not supported" in json.dumps(r):
            return {"status": "unsupported", "detail": "start llama-server with --slot-save-path"}
        # stale/corrupt file: fall through and rebuild it
    body = {"prompt": prefix_text, "n_predict": 0, "cache_prompt": True}
    if multi:
        body["id_slot"] = slot
    st, r = http("POST", base + "/completion", body, 600.0)
    if st != 200:
        return {"status": "error", "detail": f"/completion HTTP {st}"}
    n_tok = (r.get("timings") or {}).get("prompt_n")
    cached = r.get("tokens_evaluated") or r.get("tokens_cached")
    st, s = http("POST", f"{base}/slots/{slot}?action=save", {"filename": name}, 120.0)
    if st == 501 or (st != 200 and "not supported" in json.dumps(s)):
        return {"status": "unsupported", "detail": "start llama-server with --slot-save-path"}
    if st != 200:
        return {"status": "error", "detail": f"slot save HTTP {st}: {s}"}
    try:
        sdir.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps({
            "key": key, "n_tokens": s.get("n_saved"), "build": props.get("build_info"),
            "model": Path(props.get("model_path", "")).name, "prefix_chars": len(prefix_text),
            "saved_at": time.time()}, indent=1))
        _gc(sdir, keep, name)
    except OSError:
        pass
    return {"status": "saved" if n_tok else "warm", "key": key[:24], "n_tokens": s.get("n_saved"),
            "prompt_n": n_tok, "tokens": cached, "token_check": check,
            "ms": round((time.monotonic() - t0) * 1000, 1)}


SENTINELS = ("⁣AWOS-A⁣", "⁣AWOS-B⁣")


def render_prefix(static_messages: list, tools: Optional[list] = None,
                  base: Optional[str] = None, http: Http = http_json) -> str:
    """The server-rendered text of `static_messages`, cut at a turn boundary, so it is a
    byte-for-byte prefix of every later prompt that starts with those messages."""
    return render_prefix_probe(static_messages, tools, base, http)[0]


def render_prefix_probe(static_messages: list, tools: Optional[list] = None,
                        base: Optional[str] = None, http: Http = http_json) -> tuple:
    """(prefix, probe): render_prefix's text and one full sentinel render that starts
    with it (for the /tokenize alignment check in ensure_prefix_cached)."""
    base = base or server_base()
    renders = []
    for s in SENTINELS:
        body = {"messages": list(static_messages) + [{"role": "user", "content": s}]}
        if tools:
            body["tools"] = tools
        st, r = http("POST", base + "/apply-template", body, 30.0)
        if st != 200 or "prompt" not in r:
            raise RuntimeError(f"/apply-template HTTP {st}")
        renders.append(r["prompt"])
    common = os.path.commonprefix(renders)
    cut = common.rfind("<|im_start|>")          # ChatML (Qwen): cut before the user turn
    if cut <= 0:
        cut = common.rfind("\n") + 1             # generic: last full line
    return common[:cut], renders[0]


def local_enabled(env: Optional[dict] = None) -> bool:
    env = os.environ if env is None else env
    if (env.get("AWOS_PROVIDER") or "").strip().lower() == "local":
        return True
    return (env.get("AWOS_AGENT_MODEL") or "").strip().lower().startswith("local/")


def prepare_first_step(static_messages: list, tools: Optional[list] = None,
                       env: Optional[dict] = None, http: Http = http_json,
                       starter: Optional[Callable[[], bool]] = None) -> dict:
    """Loop hook: call once per task before the first model call. Never raises."""
    env = os.environ if env is None else env
    if not local_enabled(env) or (env.get("AWOS_LOCAL_PREFIX_CACHE") or "1").strip() == "0":
        return {"status": "skipped"}
    try:
        base = server_base(env)
        if (env.get("AWOS_LOCAL_AUTOSTART") or "").strip() == "1":
            if not (starter or (lambda: ensure_server(env=env, http=http)))():
                return {"status": "error", "detail": "local server not running"}
        prefix, probe = render_prefix_probe(static_messages, tools, base, http)
        if not prefix:
            return {"status": "skipped", "detail": "empty prefix"}
        slot = _int_env(env, "AWOS_LOCAL_SLOT_ID", 0, lo=0)
        return ensure_prefix_cached(prefix, slot=slot, base=base, http=http, sdir=slot_dir(env),
                                    probe_text=probe)
    except Exception as exc:  # the cache is an optimisation; never fail a run for it
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ensure_server(env: Optional[dict] = None, http: Http = http_json,
                  run=subprocess.run, timeout: float = 240.0) -> bool:
    """Start the server through scripts/local_model.sh if it is down (on-demand restart
    after the idle watchdog stopped it). A sleeping server wakes on its own."""
    env = dict(os.environ if env is None else env)
    if server_state(server_base(env), http)["state"] in ("up", "sleeping", "loading"):
        return True
    r = run(["bash", str(repo_root() / "scripts" / "local_model.sh"), "start"], env=env,
            capture_output=True, text=True, timeout=timeout)
    return r.returncode == 0


# ── B5: task lease ───────────────────────────────────────────────────────────
LEASE_PREFIX = "task-"
DEFAULT_LEASE_TTL_S = 90


def lease_dir(env: Optional[dict] = None) -> Path:
    return models_dir(env) / "leases"


def lease_ttl_s(env: Optional[dict] = None) -> int:
    env = os.environ if env is None else env
    try:
        return _int_env(env, "AWOS_LOCAL_LEASE_TTL_S", DEFAULT_LEASE_TTL_S, lo=3)
    except PlanError:
        return DEFAULT_LEASE_TTL_S


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


class TaskLease:
    """A heartbeat file that says "a task is using the local model". Refreshed by a
    daemon thread every ttl/3 seconds; removed on release. Never raises: when the
    directory cannot be written, `path` is None and the lease is a no-op."""

    def __init__(self, task_id: str = "", env: Optional[dict] = None,
                 interval: Optional[float] = None, clock=time.time) -> None:
        self.env = dict(os.environ if env is None else env)
        self.ttl = lease_ttl_s(self.env)
        self.interval = interval if interval is not None else max(1.0, self.ttl / 3)
        self.clock = clock
        self.task_id = str(task_id or "")
        self.started = clock()
        self.beats = 0
        self.path: Optional[Path] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        d = lease_dir(self.env)
        try:
            d.mkdir(parents=True, exist_ok=True)
            self.path = d / f"{LEASE_PREFIX}{os.getpid()}-{uuid.uuid4().hex[:12]}.json"
            self.refresh()
        except OSError:
            self.path = None

    def refresh(self) -> None:
        if self.path is None:
            return
        rec = {"pid": os.getpid(), "host": socket.gethostname(), "task": self.task_id,
               "started": self.started, "heartbeat": self.clock(), "ttl": self.ttl}
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(rec))
            os.replace(tmp, self.path)
            self.beats += 1
        except OSError:
            pass

    def start(self) -> "TaskLease":
        if self.path is not None and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="awos-task-lease",
                                            daemon=True)
            self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.refresh()

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self.path is not None:
            for q in (self.path, self.path.with_suffix(".tmp")):
                try:
                    q.unlink()
                except OSError:
                    pass


@contextlib.contextmanager
def task_lease(task_id: str = "", env: Optional[dict] = None,
               interval: Optional[float] = None) -> Iterator[TaskLease]:
    """Hold a live lease while a task runs (see TaskLease); always released."""
    lease = TaskLease(task_id, env, interval).start()
    try:
        yield lease
    finally:
        lease.release()


def live_leases(env: Optional[dict] = None, now: Optional[float] = None,
                clean: bool = True) -> list:
    """Lease records whose heartbeat is younger than their ttl and whose process (on
    this host) is alive. Stale files are removed when `clean`."""
    d = lease_dir(env)
    if not d.is_dir():
        return []
    now = time.time() if now is None else now
    host = socket.gethostname()
    out = []
    for p in d.glob(f"{LEASE_PREFIX}*.json"):
        try:
            rec = json.loads(p.read_text())
            age = now - float(rec.get("heartbeat", 0))
            ttl = float(rec.get("ttl") or DEFAULT_LEASE_TTL_S)
            alive = rec.get("host") != host or _pid_alive(int(rec.get("pid", 0)))
        except (OSError, ValueError, TypeError):
            continue   # half-written or unreadable: neither live nor safe to delete
        if age <= ttl and alive:
            out.append({**rec, "path": str(p), "age_s": round(age, 1)})
        elif clean:
            try:
                p.unlink()
            except OSError:
                pass
    return out


# ── B5: idle watchdog ────────────────────────────────────────────────────────
def watch_step(state: dict, asleep_since: Optional[float], now: float, exit_after_s: float,
               ) -> tuple[Optional[float], bool]:
    """One watchdog tick -> (asleep_since, stop_now). Stops only a server that reports
    is_sleeping (llama-server sleeps only when idle, so no request is in flight) and has
    stayed asleep for exit_after_s; any other state resets the timer."""
    if state.get("state") != "sleeping":
        return None, False
    if any(s.get("is_processing") for s in state.get("slots") or []):
        return None, False
    since = asleep_since if asleep_since is not None else now
    return since, (now - since) >= exit_after_s


def _flag_value(toks: list, *names: str) -> Optional[str]:
    for i, t in enumerate(toks):
        for n in names:
            if t == n and i + 1 < len(toks):
                return toks[i + 1]
            if t.startswith(n + "="):
                return t.split("=", 1)[1]
    return None


def cmd_matches_plan(cmd: str, p: Plan) -> bool:
    """Does a running server's command line already implement plan `p` (context,
    idle sleep, host cache, speculative flags)? An empty/unknown cmd counts as a match
    (nothing to compare, so no restart)."""
    if not (cmd or "").strip():
        return True
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return True
    if _flag_value(toks, "-c", "--ctx-size") not in (None, str(p.ctx)):
        return False
    if (_flag_value(toks, "--sleep-idle-seconds") or "0") != str(max(p.idle_s, 0)):
        return False
    if _flag_value(toks, "--cache-ram") not in (None, str(p.cache_ram_mb)):
        return False
    spec = " ".join(p.spec_args)
    if spec:
        return f" {spec} " in f" {' '.join(toks)} "
    return "--spec-type" not in toks


def _idle(state: dict) -> bool:
    if state.get("state") == "sleeping":
        return True
    return state.get("state") == "up" and not any(
        s.get("is_processing") for s in state.get("slots") or [])


def watch(env: Optional[dict] = None, http: Http = http_json, stopper=None,
          clock=time.monotonic, sleep=time.sleep, interval: float = 15.0,
          max_ticks: Optional[int] = None, power_fn: Optional[Callable] = None,
          restarter: Optional[Callable] = None, plan_fn: Callable = None,
          lease_fn: Optional[Callable] = None, cmd_fn: Optional[Callable] = None) -> int:
    """Loop until the server is stopped (returns 0) or is already down (returns 1).

    Never stops a server while a live task lease exists. When the power source changes
    it re-runs plan(); if the running server's flags no longer match, it restarts the
    server (local_model.sh stop + start) once the server is idle and no lease exists."""
    env = dict(os.environ if env is None else env)
    exit_after = float(_int_env(env, "AWOS_LOCAL_EXIT_AFTER_SLEEP_S", 300, lo=0))
    base = server_base(env)
    script = str(repo_root() / "scripts" / "local_model.sh")

    def _sh(action: str) -> bool:
        return subprocess.run(["bash", script, action], env=env, capture_output=True,
                              text=True).returncode == 0

    stopper = stopper or (lambda: _sh("stop"))
    restarter = restarter or (lambda: _sh("stop") and _sh("start"))
    power_fn = power_fn or power_state
    plan_fn = plan_fn or (lambda ps: plan(env, ps))
    lease_fn = lease_fn or (lambda: live_leases(env))

    def _cmd() -> str:
        if cmd_fn is not None:
            return cmd_fn()
        try:
            return server_cmd_file(env).read_text()
        except OSError:
            return ""

    since, ticks = None, 0
    last_source: Optional[str] = None
    pending_restart = False
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        st = server_state(base, http)
        if st["state"] == "down":
            return 1
        leased = bool(lease_fn())
        try:
            source = power_fn().source
        except Exception:
            source = last_source
        if last_source is not None and source != last_source:
            try:
                pending_restart = not cmd_matches_plan(_cmd(), plan_fn(PowerState(source or
                                                                                  "unknown")))
            except PlanError:
                pending_restart = False      # the new plan is refused: keep what runs
        last_source = source
        if pending_restart and not leased and _idle(st):
            # re-check: a task may have started since the probe
            if not lease_fn() and _idle(server_state(base, http)):
                restarter()
                pending_restart, since = False, None
                sleep(interval)
                continue
        since, stop = watch_step(st, since, clock(), exit_after)
        if leased:
            since, stop = None, False
        if stop:
            # re-check right before stopping: a request may have woken it meanwhile
            if server_state(base, http)["state"] == "sleeping" and not lease_fn():
                stopper()
                return 0
            since = None
        sleep(interval)
    return 2


# ── status (doctor) ──────────────────────────────────────────────────────────
def status(env: Optional[dict] = None, http: Http = http_json,
           power: Optional[PowerState] = None) -> dict:
    env = dict(os.environ if env is None else env)
    power = power or power_state()
    st = server_state(server_base(env), http)
    spec_live = None
    for s in st.get("slots") or []:
        if "speculative" in s:
            spec_live = bool(s["speculative"])
    sdir = slot_dir(env)
    files = [p for p in sdir.glob("awos-prefix-*.bin") if PREFIX_FILE_RE.match(p.name)] \
        if sdir.is_dir() else []
    try:
        p = plan(env, power)
        plan_d = {"ok": True, "ctx": p.ctx, "spec": p.spec, "policy": p.policy.name,
                  "idle_s": p.idle_s, "need_mb": p.admission.need_mb,
                  "budget_mb": p.admission.budget_mb, "notes": p.notes}
    except PlanError as exc:
        plan_d = {"ok": False, "error": str(exc),
                  "policy": battery_policy(power).name, "spec": env.get("AWOS_LOCAL_SPEC", "off")}
    except Exception as exc:
        plan_d = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "policy": "?",
                  "spec": env.get("AWOS_LOCAL_SPEC", "off")}
    live_ctx = (st.get("props", {}).get("default_generation_settings") or {}).get("n_ctx")
    return {"server": st["state"], "power": asdict(power), "plan": plan_d, "live_ctx": live_ctx,
            "spec_live": spec_live, "prefix_cache_files": len(files),
            "prefix_cache_mb": sum(f.stat().st_size for f in files) // MIB,
            "slot_dir": str(sdir), "local_mode": local_enabled(env)}


def status_line(s: dict) -> str:
    pw = s["power"]
    power = pw["source"] + (f" {pw['percent']}%" if pw.get("percent") is not None else "")
    pl = s["plan"]
    spec = pl.get("spec", "off") + ("" if s["spec_live"] is None
                                    else (" (live)" if s["spec_live"] else " (not live)"))
    if pl.get("ok"):
        pol = f"policy {pl['policy']} (ctx {pl['ctx']}, idle {pl['idle_s']}s, " \
              f"mem {pl['need_mb']}/{pl['budget_mb']} MB)"
    else:
        pol = f"policy {pl.get('policy')}: {pl.get('error')}"
    server = s["server"] + (f" (ctx {s['live_ctx']})" if s.get("live_ctx") else "")
    return (f"server {server} · {power} · {pol} · spec {spec} · prefix cache "
            f"{s['prefix_cache_files']} file(s), {s['prefix_cache_mb']} MB")


def _main(argv: list) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "plan":
        try:
            p = plan()
        except PlanError as exc:
            print(f"local_model: {exc}", file=sys.stderr)
            return 3
        if "--shell" in argv:
            print(plan_shell(p))
        else:
            print(json.dumps({**asdict(p)}, indent=1, default=str))
        return 0
    if cmd == "status":
        s = status()
        print(json.dumps(s, indent=1) if "--json" in argv else status_line(s))
        return 0
    if cmd == "watch":
        return watch()
    if cmd == "gguf" and len(argv) > 1:
        print(json.dumps(asdict(arch_info(gguf_metadata(argv[1]))), indent=1))
        return 0
    print("usage: local_runtime.py {plan [--shell]|status [--json]|watch|gguf FILE}",
          file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main(sys.argv[1:]))
