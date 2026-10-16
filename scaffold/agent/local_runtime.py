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

import hashlib
import json
import os
import re
import shlex
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

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
        return Policy("ac" if ps.source == "ac" else "power-unknown")
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


def _file_identity(path: str) -> str:
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
        return f"{path}:unreadable"


#: flags that change the saved KV/state layout (sampling, spec and idle flags do not)
KV_FLAGS = {"-c", "--ctx-size", "-np", "--parallel", "-fa", "--flash-attn", "-ctk", "-ctv",
            "--cache-type-k", "--cache-type-v", "--swa-full", "-kvu", "--kv-unified",
            "-no-kvu", "--no-kv-unified"}


def kv_flags_signature(cmd: str) -> str:
    toks = shlex.split(cmd or "")
    out = []
    for i, t in enumerate(toks):
        if t in KV_FLAGS:
            nxt = toks[i + 1] if i + 1 < len(toks) and not toks[i + 1].startswith("-") else ""
            out.append(f"{t}={nxt}")
    return " ".join(sorted(out))


def server_cmd_file(env: Optional[dict] = None) -> Path:
    env = os.environ if env is None else env
    return models_dir(env) / f"llama-server.{env.get('AWOS_LOCAL_PORT') or 8080}.cmd"


def prefix_key(props: dict, prefix_text: str, cmd: str = "") -> str:
    """hash(GGUF identity, llama.cpp build, chat template, KV flags, n_ctx, prefix text)."""
    gen = props.get("default_generation_settings") or {}
    parts = [
        "v1",
        _file_identity(props.get("model_path", "")),
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


def ensure_prefix_cached(prefix_text: str, slot: int = 0, base: Optional[str] = None,
                         http: Http = http_json, sdir: Optional[Path] = None,
                         cmd: Optional[str] = None, keep: int = 4) -> dict:
    """Make slot `slot` hold exactly `prefix_text`'s KV/state.

    restored  a saved file for this exact key was loaded (fast first step)
    warm      the slot already holds this prefix and nothing longer (no-op)
    saved     evaluated now (n_predict=0) and saved for the next task / restart
    unsupported  the server was started without --slot-save-path
    """
    base = base or server_base()
    sdir = Path(sdir) if sdir else slot_dir()
    t0 = time.monotonic()
    st, props = http("GET", base + "/props", None, 30.0)
    if st != 200:
        return {"status": "error", "detail": f"/props HTTP {st}"}
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
                        "ms": round((time.monotonic() - t0) * 1000, 1)}
        if st == 501 or "not supported" in json.dumps(r):
            return {"status": "unsupported", "detail": "start llama-server with --slot-save-path"}
        # stale/corrupt file: fall through and rebuild it
    st, r = http("POST", base + "/completion",
                 {"prompt": prefix_text, "n_predict": 0, "cache_prompt": True, "id_slot": slot},
                 600.0)
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
            "prompt_n": n_tok, "tokens": cached,
            "ms": round((time.monotonic() - t0) * 1000, 1)}


SENTINELS = ("⁣AWOS-A⁣", "⁣AWOS-B⁣")


def render_prefix(static_messages: list, tools: Optional[list] = None,
                  base: Optional[str] = None, http: Http = http_json) -> str:
    """The server-rendered text of `static_messages`, cut at a turn boundary, so it is a
    byte-for-byte prefix of every later prompt that starts with those messages."""
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
    return common[:cut]


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
        prefix = render_prefix(static_messages, tools, base, http)
        if not prefix:
            return {"status": "skipped", "detail": "empty prefix"}
        return ensure_prefix_cached(prefix, base=base, http=http, sdir=slot_dir(env))
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


def watch(env: Optional[dict] = None, http: Http = http_json, stopper=None,
          clock=time.monotonic, sleep=time.sleep, interval: float = 15.0,
          max_ticks: Optional[int] = None) -> int:
    """Loop until the server is stopped (returns 0) or is already down (returns 1)."""
    env = dict(os.environ if env is None else env)
    exit_after = float(_int_env(env, "AWOS_LOCAL_EXIT_AFTER_SLEEP_S", 300, lo=0))
    base = server_base(env)
    stopper = stopper or (lambda: subprocess.run(
        ["bash", str(repo_root() / "scripts" / "local_model.sh"), "stop"], env=env,
        capture_output=True, text=True).returncode == 0)
    since, ticks = None, 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        st = server_state(base, http)
        if st["state"] == "down":
            return 1
        since, stop = watch_step(st, since, clock(), exit_after)
        if stop:
            # re-check right before stopping: a request may have woken it meanwhile
            if server_state(base, http)["state"] == "sleeping":
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
