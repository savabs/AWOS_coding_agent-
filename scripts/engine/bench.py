#!/usr/bin/env python3
"""
bench.py — replay the EngineBench workload against an OpenAI-compatible endpoint (llama-server) and
report the four parameters: SPEED, QUALITY, MEMORY, COMPUTE (docs/specs/enginebench.md).

    python scripts/engine/bench.py --url http://127.0.0.1:8091 --budget agent=40 --label ext
    python scripts/engine/bench.py --url http://127.0.0.1:8091 --kinds repair verify --budget repair=25,verify=25
    python scripts/engine/bench.py ... --workload bench/engine/workload-v1.jsonl.gz \\
        --workload bench/engine/local/workload-v1-web.jsonl.gz        # + the local-only live-web episodes

Replay is sequential, one client, episodes in workload order and calls in step order (as one agent runs),
so llama-server's prompt cache can reuse the shared prefix between steps. Loopback URLs only (no paid
path). Per call it records wall time, llama-server's ``timings`` (prompt_n / prompt_ms = prefill,
predicted_n / predicted_ms = decode, cache_n = prompt tokens restored from the cache, i.e. n_past), the
longest token prefix shared with an earlier prompt (what a perfect cache could reuse; via /apply-template
+ /tokenize) and correctness against the workload's expected answer. A sampler thread follows the server
process: peak RSS (ps), peak phys_footprint (``footprint``, sudo-free), CPU seconds (ps cputime) and GPU
busy seconds (IOAccelerator "Device Utilization %" from ioreg, whole GPU, sudo-free). Energy is not
measured: powermetrics needs sudo.

Writes <out>/report.json and <out>/report.md; prints the one-line config fingerprint.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "scripts" / "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

import capture  # noqa: E402

DEFAULT_WORKLOAD = ROOT / "bench" / "engine" / "workload-v1.jsonl.gz"
KINDS = ("agent", "repair", "verify")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
SHA_CACHE = Path.home() / ".cache" / "awos-enginebench" / "sha256.json"
ENERGY_NOTE = "not measured: macOS powermetrics needs sudo (no sudo-free per-process energy source)"


# ------------------------------------------------------------------- http --

def check_loopback(url: str) -> None:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host not in LOOPBACK:
        raise ValueError(f"bench.py talks to a local server only, not {host!r}")


def http(base: str, path: str, body: Optional[dict] = None, timeout: float = 600.0) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")


def parse_prometheus(text: str) -> Dict[str, float]:
    out = {}
    for line in (text or "").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 2:
            try:
                out[parts[0]] = float(parts[1])
            except ValueError:
                pass
    return out


# ------------------------------------------------------------ the process --

def parse_cputime(s: str) -> float:
    """ps cputime ('1:02.50', '12:01:02.50', '1-02:03:04') -> seconds."""
    s = (s or "").strip()
    if not s:
        return 0.0
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [float(x) for x in s.split(":")]
    sec = 0.0
    for x in parts:
        sec = sec * 60 + x
    return days * 86400 + sec


def ps_sample(pid: int) -> Optional[Dict[str, float]]:
    try:
        out = subprocess.run(["ps", "-o", "rss=,time=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    if len(out) < 2:
        return None
    return {"rss_mb": int(out[0]) / 1024.0, "cpu_s": parse_cputime(out[1])}


_FOOT = re.compile(r"Footprint:\s*([\d.]+)\s*([KMG]B)")


def parse_footprint(text: str) -> Optional[float]:
    m = _FOOT.search(text or "")
    if not m:
        return None
    return float(m.group(1)) * {"KB": 1 / 1024, "MB": 1.0, "GB": 1024.0}[m.group(2)]


def footprint_mb(pid: int) -> Optional[float]:
    try:
        r = subprocess.run(["footprint", "-p", str(pid)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_footprint(r.stdout)


_GPU = re.compile(r'"Device Utilization %"\s*=\s*(\d+)')


def gpu_util() -> Optional[float]:
    try:
        r = subprocess.run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"], capture_output=True, text=True,
                           timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    vals = [float(x) for x in _GPU.findall(r.stdout)]
    return max(vals) if vals else None


def integrate(samples: Sequence[tuple], baseline: float = 0.0) -> float:
    """[(t, util%)] -> busy seconds above baseline (trapezoid)."""
    busy = 0.0
    for (t0, u0), (t1, u1) in zip(samples, samples[1:]):
        busy += (t1 - t0) * (max(u0 - baseline, 0.0) + max(u1 - baseline, 0.0)) / 200.0
    return busy


class Sampler:
    """Follows the server process while the replay runs (and the whole GPU, which is shared)."""

    def __init__(self, pid: Optional[int], every: float = 0.5, foot_every: float = 2.0,
                 ps: Callable = ps_sample, foot: Callable = footprint_mb, gpu: Callable = gpu_util):
        self.pid, self.every, self.foot_every = pid, every, foot_every
        self._ps, self._foot, self._gpu = ps, foot, gpu
        self.rss: List[float] = []
        self.foot: List[float] = []
        self.gpu: List[tuple] = []
        self.cpu0 = self.cpu1 = None
        self.gpu_baseline = 0.0
        self._stop = threading.Event()
        self._th: Optional[threading.Thread] = None
        self.t0 = self.t1 = 0.0

    def idle_baseline(self, seconds: float = 2.0) -> float:
        vals = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            u = self._gpu()
            if u is None:
                break
            vals.append(u)
            time.sleep(0.25)
        self.gpu_baseline = statistics.median(vals) if vals else 0.0
        return self.gpu_baseline

    def _loop(self) -> None:
        last_foot = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if self.pid:
                s = self._ps(self.pid)
                if s:
                    self.rss.append(s["rss_mb"])
                if now - last_foot >= self.foot_every:
                    f = self._foot(self.pid)
                    if f is not None:
                        self.foot.append(f)
                    last_foot = now
            u = self._gpu()
            if u is not None:
                self.gpu.append((now, u))
            self._stop.wait(self.every)

    def start(self) -> None:
        if self.pid:
            s = self._ps(self.pid)
            self.cpu0 = s["cpu_s"] if s else None
        self.t0 = time.monotonic()
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        if self._th:
            self._th.join(timeout=15)
        self.t1 = time.monotonic()
        if self.pid:
            s = self._ps(self.pid)
            self.cpu1 = s["cpu_s"] if s else None
            f = self._foot(self.pid)
            if f is not None:
                self.foot.append(f)
        wall = self.t1 - self.t0
        cpu = (self.cpu1 - self.cpu0) if (self.cpu0 is not None and self.cpu1 is not None) else None
        if cpu is not None and cpu < 0:          # the process died or the pid was reused: no number
            cpu = None
        return {"peak_rss_mb": round(max(self.rss), 1) if self.rss else None,
                "peak_footprint_mb": round(max(self.foot), 1) if self.foot else None,
                "cpu_s": round(cpu, 2) if cpu is not None else None,
                "cpu_util_cores": round(cpu / wall, 2) if cpu is not None and wall > 0 else None,
                "gpu_busy_s": round(integrate(self.gpu, self.gpu_baseline), 2) if len(self.gpu) > 1 else None,
                "gpu_util_mean": round(statistics.mean(u for _t, u in self.gpu), 1) if self.gpu else None,
                "gpu_idle_baseline": self.gpu_baseline, "samples": len(self.rss), "wall_s": round(wall, 2)}


def find_pid(port: int) -> Optional[int]:
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True,
                             timeout=10).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    return int(out[0]) if out else None


def cmdline(pid: Optional[int]) -> str:
    if not pid:
        return ""
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# ------------------------------------------------------------ fingerprint --

_QUANT = re.compile(r"(?i)(?<![A-Z0-9])(IQ\d_[A-Z0-9]+|Q\d_K(?:_[SML])?|Q\d_\d|TQ\d_\d|MXFP4|BF16|F16|F32)(?![A-Z0-9])")
_SHARD = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$")
_DROP_FLAGS = {"-m": 1, "--model": 1, "--host": 1, "--port": 1, "--alias": 1, "-a": 1, "--log-file": 1}


def quant_of(name: str) -> str:
    m = _QUANT.search(Path(name).name)
    return m.group(1).upper() if m else "?"


def shard_paths(path: str) -> List[Path]:
    p = Path(path)
    m = _SHARD.search(p.name)
    if not m:
        return [p]
    n = int(m.group(2))
    return [p.with_name(_SHARD.sub(f"-{i:05d}-of-{n:05d}.gguf", p.name)) for i in range(1, n + 1)]


def file_sha256(path: Path, cache: Path = SHA_CACHE) -> Optional[str]:
    try:
        st = path.stat()
    except OSError:
        return None
    key = f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    try:
        db = json.loads(cache.read_text())
    except (OSError, ValueError):
        db = {}
    if key in db:
        return db[key]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    db[key] = h.hexdigest()
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(db, indent=0))
    except OSError:
        pass
    return db[key]


def server_flags(cmd: str) -> List[str]:
    """llama-server command line -> its flags, minus model path / host / port / alias (not config)."""
    toks = cmd.split()
    if toks and "llama-server" in toks[0]:
        toks = toks[1:]
    out, skip = [], 0
    for t in toks:
        if skip:
            skip -= 1
            continue
        if t in _DROP_FLAGS:
            skip = _DROP_FLAGS[t]
            continue
        out.append(t)
    return out


def model_from_cmd(cmd: str) -> Optional[str]:
    toks = cmd.split()
    for i, t in enumerate(toks[:-1]):
        if t in ("-m", "--model"):
            return toks[i + 1]
    return None


def fingerprint(props: Optional[dict], cmd: str, model_path: Optional[str] = None,
                sha: Callable[[Path], Optional[str]] = file_sha256) -> Dict[str, Any]:
    props = props or {}
    build = str(props.get("build_info") or "?")
    mp = model_path or model_from_cmd(cmd) or props.get("model_path") or "?"
    shards = shard_paths(mp) if mp != "?" else []
    shas = [sha(p) for p in shards]
    sha_s = "+".join((s or "?")[:12] for s in shas) if shas else "?"
    size = 0
    for p in shards:
        try:
            size += p.stat().st_size
        except OSError:
            pass
    flags = server_flags(cmd)
    name = _SHARD.sub(".gguf", Path(mp).name)
    line = f"llama.cpp {build} | {name} sha256:{sha_s} | {quant_of(name)} | {' '.join(flags) or '(flags unknown)'}"
    return {"build": build, "model": name, "model_path": mp, "sha256": shas, "model_bytes": size or None,
            "quant": quant_of(name), "flags": flags, "line": line,
            "id": hashlib.sha256(line.encode()).hexdigest()[:10],
            "n_ctx": (props.get("default_generation_settings") or {}).get("n_ctx")}


# ---------------------------------------------------------------- scoring --

_REPAIR = re.compile(r"^\s*(?:PICK\s+([1-5])|(NOT_FOUND))\b")
_VERIFY = re.compile(r"^\s*(VERIFIED|NOT_DONE|UNSAFE)\b")


def score_call(kind: str, expected: Optional[str], reply: str, reference: Optional[str] = None) -> Dict[str, Any]:
    """-> {pred, correct (None = no expected answer), outcome, ref_agree}"""
    out: Dict[str, Any] = {"pred": None, "correct": None, "outcome": None, "ref_agree": None}
    if kind == "agent":
        pred = capture.norm_action(reply)
        out["pred"] = pred
        out["outcome"] = "invalid" if pred is None else "action"
        if expected is not None:
            out["correct"] = pred == expected
        if reference is not None:
            out["ref_agree"] = pred == reference
    elif kind == "repair":
        m = _REPAIR.match(reply or "")
        pred = (f"PICK {m.group(1)}" if m.group(1) else "NOT_FOUND") if m else None
        out["pred"] = pred
        if expected is not None:
            out["correct"] = pred == expected
            if pred is None or pred == "NOT_FOUND":
                out["outcome"] = "abstain_ok" if expected == "NOT_FOUND" else "abstain"
            else:
                out["outcome"] = "right" if pred == expected else "wrong"     # a repair onto the wrong control
    elif kind == "verify":
        m = _VERIFY.match(reply or "")
        pred = m.group(1) if m else None
        out["pred"] = pred
        if expected is not None:
            out["correct"] = pred == expected
            out["outcome"] = ("false_verified" if pred == "VERIFIED" and expected != "VERIFIED" else
                              "right" if pred == expected else "wrong")
    return out


# ------------------------------------------------------------------ select --

def parse_budget(spec: Optional[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for part in (spec or "").split(","):
        if part.strip():
            k, _, v = part.partition("=")
            if k.strip() not in KINDS:
                raise ValueError(f"budget kind {k!r} not in {KINDS}")
            out[k.strip()] = int(v)
    return out


def select(episodes: Sequence[dict], kinds: Sequence[str] = KINDS, budget: Optional[Dict[str, int]] = None,
           sources: Optional[Sequence[str]] = None) -> List[dict]:
    """Whole episodes, in workload order, per kind until its call budget is reached (None = all)."""
    budget = budget or {}
    used = {k: 0 for k in kinds}
    out = []
    for kind in kinds:
        for ep in episodes:
            if ep["kind"] != kind or (sources and ep.get("source") not in sources):
                continue
            cap = budget.get(kind)
            if cap is not None and used[kind] >= cap:
                break
            out.append(ep)
            used[kind] += len(ep["calls"])
    return out


# ------------------------------------------------------------------ replay --

def lcp(a: Sequence[int], b: Sequence[int]) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def tokens_of(base: str, messages: list) -> Optional[List[int]]:
    """The exact prompt tokens the server will see (chat template applied server-side)."""
    try:
        tpl = http(base, "/apply-template", {"messages": messages, "chat_template_kwargs": {"enable_thinking": False}},
                   timeout=60)
        prompt = tpl.get("prompt") if isinstance(tpl, dict) else None
        if prompt is None:
            return None
        tk = http(base, "/tokenize", {"content": prompt, "add_special": True, "parse_special": True}, timeout=60)
        return list(tk.get("tokens") or []) if isinstance(tk, dict) else None
    except Exception:  # noqa: BLE001 -- the LCP column is optional
        return None


def request_body(call: dict, model: str, grammar: bool = True) -> dict:
    body = {"model": model, "temperature": 0, "max_tokens": call["max_tokens"], "messages": call["messages"],
            "chat_template_kwargs": {"enable_thinking": False}, "cache_prompt": True}
    if grammar and call.get("grammar"):
        body["grammar"] = call["grammar"]
    return body


def replay(base: str, episodes: Sequence[dict], *, model: str = "local", measure_lcp: bool = True,
           timeout: float = 600.0, history: int = 64, progress: Optional[Callable[[str], None]] = None,
           grammar: bool = True) -> List[dict]:
    recs: List[dict] = []
    prev_tokens: List[List[int]] = []
    for ep in episodes:
        for call in ep["calls"]:
            toks = tokens_of(base, call["messages"]) if measure_lcp else None
            lcp_prev = lcp_best = None
            if toks is not None:
                lcp_prev = lcp(toks, prev_tokens[-1]) if prev_tokens else 0
                lcp_best = max((lcp(toks, t) for t in prev_tokens), default=0)
                prev_tokens = (prev_tokens + [toks])[-history:]
            t0 = time.monotonic()
            err = ""
            data: dict = {}
            try:
                data = http(base, "/v1/chat/completions", request_body(call, model, grammar), timeout=timeout)
            except Exception as e:  # noqa: BLE001 -- a failed call is recorded, the replay goes on
                err = f"{type(e).__name__}: {e}"[:300]
            wall = time.monotonic() - t0
            reply = ""
            if isinstance(data, dict) and data.get("choices"):
                reply = data["choices"][0].get("message", {}).get("content") or ""
            t = (data.get("timings") if isinstance(data, dict) else None) or {}
            u = (data.get("usage") if isinstance(data, dict) else None) or {}
            sc = (score_call(ep["kind"], call.get("expected"), reply, call.get("reference")) if not err else
                  {"pred": None, "correct": None, "outcome": "error", "ref_agree": None})   # not a quality miss
            rec = {"id": call["id"], "episode": ep["id"], "kind": ep["kind"], "source": ep.get("source"),
                   "step": call.get("step"), "wall_s": round(wall, 4), "error": err, "reply": reply[:300],
                   "prompt_n": int(t.get("prompt_n") or 0), "prompt_ms": float(t.get("prompt_ms") or 0.0),
                   "predicted_n": int(t.get("predicted_n") or 0), "predicted_ms": float(t.get("predicted_ms") or 0.0),
                   "cache_n": int(t.get("cache_n") or 0), "usage_prompt": int(u.get("prompt_tokens") or 0),
                   "usage_completion": int(u.get("completion_tokens") or 0),
                   "prompt_tokens": len(toks) if toks is not None else None,
                   "lcp_prev": lcp_prev, "lcp_best": lcp_best, "expected": call.get("expected"), **sc}
            recs.append(rec)
            if progress:
                progress(f"{len(recs):4d} {rec['kind']:6s} {wall:6.2f}s prefill {rec['prompt_n']:5d}t "
                         f"cache {rec['cache_n']:5d}t lcp {lcp_best} -> {(rec['pred'] or '?')[:40]} "
                         f"{'' if rec['correct'] is None else ('ok' if rec['correct'] else 'MISS')}{' ERR ' + err if err else ''}")
    return recs


# ----------------------------------------------------------------- summary --

def _q(xs: Sequence[float], q: float) -> Optional[float]:
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _r(x: Optional[float], n: int = 3) -> Optional[float]:
    return None if x is None else round(x, n)


def _rate(num: int, den: int) -> Optional[float]:
    return round(num / den, 4) if den else None


def speed(recs: Sequence[dict]) -> Dict[str, Any]:
    ok = [r for r in recs if not r["error"]]
    if not ok:
        return {"n": 0}
    wall = [r["wall_s"] for r in ok]
    pre = [r["prompt_ms"] / 1000 for r in ok]
    dec = [r["predicted_ms"] / 1000 for r in ok]
    sp, sd = sum(r["prompt_ms"] for r in ok), sum(r["predicted_ms"] for r in ok)
    return {"n": len(ok), "wall_s_mean": _r(statistics.mean(wall)), "wall_s_p50": _r(_q(wall, 0.5)),
            "wall_s_p90": _r(_q(wall, 0.9)), "prefill_s_mean": _r(statistics.mean(pre)),
            "decode_s_mean": _r(statistics.mean(dec)),
            "overhead_s_mean": _r(statistics.mean(w - p - d for w, p, d in zip(wall, pre, dec))),
            "prefill_share": _r(sp / 1000 / sum(wall)) if sum(wall) else None,
            "prefill_tok_s": _r(sum(r["prompt_n"] for r in ok) / (sp / 1000), 1) if sp else None,
            "decode_tok_s": _r(sum(r["predicted_n"] for r in ok) / (sd / 1000), 1) if sd else None}


def cache(recs: Sequence[dict]) -> Dict[str, Any]:
    ok = [r for r in recs if not r["error"]]
    tot = sum(r["prompt_n"] + r["cache_n"] for r in ok)
    out = {"calls_hit": sum(1 for r in ok if r["cache_n"] > 0), "hit_rate": _rate(sum(1 for r in ok if r["cache_n"] > 0), len(ok)),
           "cached_share": _rate(sum(r["cache_n"] for r in ok), tot)}
    lr = [r for r in ok if r.get("lcp_best") is not None]
    if lr:
        lt = sum(r["prompt_n"] + r["cache_n"] for r in lr)
        reusable = sum(r["lcp_best"] for r in lr)
        out.update({"reusable_share": _rate(reusable, lt), "reusable_prev_share": _rate(sum(r["lcp_prev"] for r in lr), lt),
                    "capture_of_reusable": _rate(sum(min(r["cache_n"], r["lcp_best"]) for r in lr), reusable)})
    return out


def quality(recs: Sequence[dict]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    ag = [r for r in recs if r["kind"] == "agent"]
    if ag:
        ex = [r for r in ag if r["correct"] is not None]
        ref = [r for r in ag if r["ref_agree"] is not None]
        eps: Dict[str, List[bool]] = {}
        for r in ex:
            eps.setdefault(r["episode"], []).append(bool(r["correct"]))
        out["agent"] = {"n": len(ag), "n_expected": len(ex), "step_match": _rate(sum(1 for r in ex if r["correct"]), len(ex)),
                        "episode_all_match": _rate(sum(1 for v in eps.values() if all(v)), len(eps)),
                        "episodes_scored": len(eps),
                        "ref_agree": _rate(sum(1 for r in ref if r["ref_agree"]), len(ref)),
                        "invalid": _rate(sum(1 for r in ag if r["outcome"] == "invalid"), len(ag))}
    rp = [r for r in recs if r["kind"] == "repair" and r["correct"] is not None]
    if rp:
        present = [r for r in rp if r["expected"] != "NOT_FOUND"]
        gone = [r for r in rp if r["expected"] == "NOT_FOUND"]
        out["repair"] = {"n": len(rp), "accuracy": _rate(sum(1 for r in rp if r["correct"]), len(rp)),
                         "refind_accuracy": _rate(sum(1 for r in present if r["correct"]), len(present)),
                         "wrong_repair_rate": _rate(sum(1 for r in rp if r["outcome"] == "wrong"), len(rp)),
                         "abstain_correct": _rate(sum(1 for r in gone if r["outcome"] == "abstain_ok"), len(gone)),
                         "n_present": len(present), "n_not_found": len(gone)}
    vf = [r for r in recs if r["kind"] == "verify" and r["correct"] is not None]
    if vf:
        neg = [r for r in vf if r["expected"] != "VERIFIED"]
        out["verify"] = {"n": len(vf), "accuracy": _rate(sum(1 for r in vf if r["correct"]), len(vf)),
                         "false_verified_rate": _rate(sum(1 for r in neg if r["pred"] == "VERIFIED"), len(neg)),
                         "n_negative": len(neg),
                         "verified_recall": _rate(sum(1 for r in vf if r["expected"] == "VERIFIED" and r["pred"] == "VERIFIED"),
                                                  sum(1 for r in vf if r["expected"] == "VERIFIED"))}
    return out


def compute(recs: Sequence[dict], proc: Dict[str, Any]) -> Dict[str, Any]:
    ok = [r for r in recs if not r["error"]]
    eps = {r["episode"] for r in ok}
    pt = sum(r["prompt_n"] + r["cache_n"] for r in ok)
    gen = sum(r["predicted_n"] for r in ok)
    n = max(len(ok), 1)
    return {"calls": len(ok), "episodes": len(eps), "prompt_tokens": pt, "prompt_tokens_evaluated": sum(r["prompt_n"] for r in ok),
            "cached_tokens": sum(r["cache_n"] for r in ok), "generated_tokens": gen,
            "prompt_tokens_per_call": round(pt / n, 1), "generated_tokens_per_call": round(gen / n, 2),
            "prompt_tokens_per_task": round(pt / max(len(eps), 1), 1), "generated_tokens_per_task": round(gen / max(len(eps), 1), 1),
            "cpu_s": proc.get("cpu_s"), "cpu_s_per_call": _r(proc["cpu_s"] / n) if proc.get("cpu_s") is not None else None,
            "gpu_busy_s": proc.get("gpu_busy_s"),
            "gpu_busy_s_per_call": _r(proc["gpu_busy_s"] / n) if proc.get("gpu_busy_s") is not None else None,
            "energy_j": None, "energy_note": ENERGY_NOTE}


def summarize(recs: Sequence[dict], proc: Dict[str, Any], fp: Dict[str, Any]) -> Dict[str, Any]:
    by_kind = {}
    for k in KINDS:
        rs = [r for r in recs if r["kind"] == k]
        if rs:
            eps: Dict[str, float] = {}
            for r in rs:
                eps[r["episode"]] = eps.get(r["episode"], 0.0) + r["wall_s"]
            by_kind[k] = {"speed": speed(rs), "cache": cache(rs),
                          "task_s_mean": _r(statistics.mean(eps.values())), "tasks": len(eps)}
    mb = (fp.get("model_bytes") or 0) / 2 ** 20
    mem = {"peak_rss_mb": proc.get("peak_rss_mb"), "peak_footprint_mb": proc.get("peak_footprint_mb"),
           "model_file_mb": round(mb, 1) if mb else None,
           "est_peak_unified_mb": round(proc["peak_footprint_mb"] + mb, 1) if proc.get("peak_footprint_mb") and mb else None,
           "note": "footprint = dirty memory (KV, compute buffers, Metal allocations); mmapped weights are clean pages "
                   "and not counted in it, so est_peak_unified = footprint + model file size (weights fully resident)"}
    return {"speed": {"all": speed(recs), **{k: v["speed"] for k, v in by_kind.items()}},
            "task_s": {k: {"mean": v["task_s_mean"], "tasks": v["tasks"]} for k, v in by_kind.items()},
            "cache": {"all": cache(recs), **{k: v["cache"] for k, v in by_kind.items()}},
            "quality": quality(recs), "memory": mem, "compute": compute(recs, proc),
            "errors": sum(1 for r in recs if r["error"])}


def _fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "-"
    if pct:
        return f"{100 * v:.1f}%"
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def markdown(rep: Dict[str, Any]) -> str:
    s, L = rep["summary"], []
    L.append(f"# EngineBench: {rep['label']}\n")
    L.append(f"`{rep['fingerprint']['line']}`  (fp {rep['fingerprint']['id']})\n")
    L.append(f"workload {rep['workload']['version']} digest {rep['workload']['digest'][:12]}, {rep['workload']['calls']} calls "
             f"in {rep['workload']['episodes']} episodes; budget {rep['workload']['budget'] or 'all'}; "
             f"{rep['started']}; wall {rep['wall_s']} s; errors {s['errors']}\n")
    L.append("## SPEED (seconds per call)\n")
    L.append("| kind | n | wall mean | p50 | p90 | prefill | decode | other | prefill share | prefill tok/s | decode tok/s | task s |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, v in s["speed"].items():
        if not v.get("n"):
            continue
        ts = (s["task_s"].get(k) or {}).get("mean")
        L.append(f"| {k} | {v['n']} | {_fmt(v['wall_s_mean'])} | {_fmt(v['wall_s_p50'])} | {_fmt(v['wall_s_p90'])} | "
                 f"{_fmt(v['prefill_s_mean'])} | {_fmt(v['decode_s_mean'])} | {_fmt(v['overhead_s_mean'])} | "
                 f"{_fmt(v['prefill_share'], True)} | {_fmt(v['prefill_tok_s'])} | {_fmt(v['decode_tok_s'])} | {_fmt(ts)} |")
    L.append("\n## Prefix cache\n")
    L.append("| kind | calls hit | hit rate | cached share | reusable (best LCP) | reusable (prev) | captured |")
    L.append("|---|---|---|---|---|---|---|")
    for k, v in s["cache"].items():
        L.append(f"| {k} | {v.get('calls_hit')} | {_fmt(v.get('hit_rate'), True)} | {_fmt(v.get('cached_share'), True)} | "
                 f"{_fmt(v.get('reusable_share'), True)} | {_fmt(v.get('reusable_prev_share'), True)} | "
                 f"{_fmt(v.get('capture_of_reusable'), True)} |")
    L.append("\n## QUALITY\n")
    q = s["quality"]
    if "agent" in q:
        a = q["agent"]
        L.append(f"- agent: exact action match {_fmt(a['step_match'], True)} of {a['n_expected']} steps with a known answer; "
                 f"episodes all-steps-right {_fmt(a['episode_all_match'], True)} of {a['episodes_scored']}; "
                 f"agrees with the CI reply {_fmt(a['ref_agree'], True)}; invalid {_fmt(a['invalid'], True)}")
    if "repair" in q:
        r = q["repair"]
        L.append(f"- repair: accuracy {_fmt(r['accuracy'], True)} (n {r['n']}); re-find {_fmt(r['refind_accuracy'], True)} "
                 f"of {r['n_present']}; wrong-repair rate {_fmt(r['wrong_repair_rate'], True)}; "
                 f"NOT_FOUND right {_fmt(r['abstain_correct'], True)} of {r['n_not_found']}")
    if "verify" in q:
        v = q["verify"]
        L.append(f"- verify: accuracy {_fmt(v['accuracy'], True)} (n {v['n']}); false-VERIFIED rate "
                 f"{_fmt(v['false_verified_rate'], True)} of {v['n_negative']} failures; VERIFIED recall {_fmt(v['verified_recall'], True)}")
    m, c = s["memory"], s["compute"]
    L.append("\n## MEMORY\n")
    L.append(f"- peak RSS {_fmt(m['peak_rss_mb'])} MB; peak footprint {_fmt(m['peak_footprint_mb'])} MB; model file "
             f"{_fmt(m['model_file_mb'])} MB; est. peak unified {_fmt(m['est_peak_unified_mb'])} MB")
    L.append("\n## COMPUTE\n")
    L.append(f"- prompt tokens {c['prompt_tokens']} ({c['prompt_tokens_evaluated']} evaluated, {c['cached_tokens']} from cache), "
             f"generated {c['generated_tokens']}; per call {c['prompt_tokens_per_call']} + {c['generated_tokens_per_call']}; "
             f"per task {c['prompt_tokens_per_task']} + {c['generated_tokens_per_task']}")
    L.append(f"- server CPU {_fmt(c['cpu_s'])} s ({_fmt(c['cpu_s_per_call'])} s/call); GPU busy {_fmt(c['gpu_busy_s'])} s "
             f"({_fmt(c['gpu_busy_s_per_call'])} s/call, whole GPU above idle); energy: {c['energy_note']}")
    return "\n".join(L) + "\n"


# -------------------------------------------------------------------- run --

def run(base: str, episodes: Sequence[dict], *, label: str, workload: Dict[str, Any], pid: Optional[int] = None,
        model_path: Optional[str] = None, model: str = "local", measure_lcp: bool = True, warmup: bool = True,
        sample: bool = True, out: Optional[Path] = None, progress: Optional[Callable[[str], None]] = None,
        timeout: float = 600.0, grammar: bool = True) -> Dict[str, Any]:
    check_loopback(base)
    try:
        props = http(base, "/props", timeout=30)
        props = props if isinstance(props, dict) else {}
    except Exception:  # noqa: BLE001
        props = {}
    fp = fingerprint(props, cmdline(pid), model_path)
    if warmup:              # load / first-graph costs stay out of the numbers
        try:
            http(base, "/v1/chat/completions", {"model": model, "max_tokens": 1, "temperature": 0,
                                                "messages": [{"role": "user", "content": "hi"}]}, timeout=timeout)
        except Exception:  # noqa: BLE001
            pass
    sampler = Sampler(pid) if sample else None
    if sampler:
        sampler.idle_baseline()
    try:
        m0 = parse_prometheus(http(base, "/metrics", timeout=10))
    except Exception:  # noqa: BLE001
        m0 = {}
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    t0 = time.monotonic()
    if sampler:
        sampler.start()
    recs = replay(base, episodes, model=model, measure_lcp=measure_lcp, progress=progress, timeout=timeout,
                  grammar=grammar)
    proc = sampler.stop() if sampler else {}
    wall = time.monotonic() - t0
    try:
        m1 = parse_prometheus(http(base, "/metrics", timeout=10))
    except Exception:  # noqa: BLE001
        m1 = {}
    rep = {"label": label, "started": started, "wall_s": round(wall, 1), "fingerprint": fp, "workload": workload,
           "process": proc, "server_metrics_delta": {k: round(m1[k] - m0.get(k, 0.0), 3) for k in m1
                                                     if k.endswith("_total")},
           "summary": summarize(recs, proc, fp), "calls": recs}
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(rep, indent=1))
        (out / "report.md").write_text(markdown(rep))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--url", default="http://127.0.0.1:8091", help="llama-server base URL (loopback only)")
    ap.add_argument("--workload", type=Path, action="append", help=f"workload file(s) (default {DEFAULT_WORKLOAD})")
    ap.add_argument("--kinds", nargs="*", default=list(KINDS), choices=KINDS)
    ap.add_argument("--sources", nargs="*", help="only these episode sources (e.g. factory-ood web-dev)")
    ap.add_argument("--budget", help="calls per kind, whole episodes, e.g. agent=40,repair=20,verify=20")
    ap.add_argument("--pid", default="auto", help="server pid to sample ('auto' = the listener on the port, 'none')")
    ap.add_argument("--model-path", help="GGUF path for the fingerprint (default: from the server command line)")
    ap.add_argument("--label", default="run")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--no-lcp", action="store_true", help="skip /tokenize (no reusable-prefix column)")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--no-grammar", action="store_true", help="drop the GBNF grammar (measures its decode cost; "
                    "quality numbers then are not the product's)")
    a = ap.parse_args(argv)
    check_loopback(a.url)
    headers, eps = capture.load(a.workload or [DEFAULT_WORKLOAD])
    budget = parse_budget(a.budget)
    sel = select(eps, a.kinds, budget, a.sources)
    port = urllib.parse.urlsplit(a.url).port or 80
    pid = None if a.pid == "none" else (find_pid(port) if a.pid == "auto" else int(a.pid))
    wl = {"version": capture.VERSION, "digest": "+".join(h["digest"][:12] for h in headers) or "?",
          "calls": sum(len(e["calls"]) for e in sel), "episodes": len(sel), "budget": a.budget, "kinds": a.kinds,
          "grammar": not a.no_grammar}
    out = a.out or ROOT / "bench" / "engine" / "runs" / f"{time.strftime('%Y%m%d-%H%M%S')}-{a.label}"
    rep = run(a.url, sel, label=a.label, workload=wl, pid=pid, model_path=a.model_path, measure_lcp=not a.no_lcp,
              out=out, progress=lambda s: print(s, flush=True), timeout=a.timeout,
              grammar=not a.no_grammar)
    print(markdown(rep))
    print(f"fingerprint: {rep['fingerprint']['line']}")
    print(f"wrote {out}/report.json, report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
