"""local_runtime: GGUF arch, policy merge, admission, prefix-KV save/restore, idle watch,
doctor row. All fakes: fake pmset text, a fake llama-server HTTP API, a fake process."""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scaffold.agent import doctor  # noqa: E402
from scaffold.agent import local_runtime as lr  # noqa: E402

PMSET_BATT = ("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=7340131)\t64%; "
              "discharging; 9:42 remaining present: true\n")
PMSET_LOW = PMSET_BATT.replace("64%", "12%")
PMSET_AC = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged; 0:00\n"


# ── GGUF ─────────────────────────────────────────────────────────────────────
def _s(x: str) -> bytes:
    b = x.encode()
    return struct.pack("<Q", len(b)) + b


def write_gguf(path: Path, kv: dict) -> Path:
    out = b"GGUF" + struct.pack("<IQQ", 3, 0, len(kv))
    for k, v in kv.items():
        out += _s(k)
        if isinstance(v, str):
            out += struct.pack("<I", 8) + _s(v)
        elif isinstance(v, float):
            out += struct.pack("<If", 6, v)
        elif isinstance(v, tuple):          # ("strs", n)  -> string array
            out += struct.pack("<IIQ", 9, 8, v[1]) + b"".join(_s(f"t{i}") for i in range(v[1]))
        else:
            out += struct.pack("<II", 4, v)
    path.write_bytes(out)
    return path


def qwen35_kv(vocab=40):
    return {"general.architecture": "qwen35", "qwen35.block_count": 32,
            "qwen35.embedding_length": 4096, "qwen35.attention.head_count": 16,
            "qwen35.attention.head_count_kv": 4, "qwen35.attention.key_length": 256,
            "qwen35.attention.value_length": 256, "qwen35.full_attention_interval": 4,
            "qwen35.ssm.state_size": 128, "qwen35.ssm.inner_size": 4096,
            "qwen35.ssm.group_count": 16, "qwen35.ssm.conv_kernel": 4,
            "tokenizer.ggml.pre": "qwen35", "tokenizer.ggml.tokens": ("strs", vocab)}


def test_gguf_arch_hybrid_qwen35(tmp_path):
    p = write_gguf(tmp_path / "m.gguf", qwen35_kv())
    info = lr.arch_info(lr.gguf_metadata(p))
    assert info.arch == "qwen35" and info.hybrid and info.attn_layers == 8
    assert info.kv_bytes_per_token == 8 * 4 * 512 * 2          # 32 KiB / token at f16
    assert 50e6 < info.fixed_state_bytes < 55e6                # ~53 MB recurrent state
    assert info.vocab == 40


def test_draft_tokenizer_must_match(tmp_path):
    t = lr.arch_info(lr.gguf_metadata(write_gguf(tmp_path / "t.gguf", qwen35_kv(40))))
    d = lr.arch_info(lr.gguf_metadata(write_gguf(
        tmp_path / "d.gguf", {"general.architecture": "qwen2", "qwen2.block_count": 4,
                              "tokenizer.ggml.pre": "qwen2", "tokenizer.ggml.tokens": ("strs", 30)})))
    ok, why = lr.draft_compatible(t, d)
    assert not ok and "qwen2/30" in why
    assert lr.draft_compatible(t, t)[0]


# ── power policy ─────────────────────────────────────────────────────────────
def test_parse_pmset():
    assert lr.parse_pmset(PMSET_BATT) == lr.PowerState("battery", 64)
    assert lr.parse_pmset(PMSET_AC).source == "ac"
    assert lr.parse_pmset("garbage").source == "unknown"


def test_power_state_uses_pmset():
    fake = lambda *a, **k: subprocess.CompletedProcess(a, 0, PMSET_BATT, "")  # noqa: E731
    if sys.platform == "darwin":
        assert lr.power_state(run=fake).source == "battery"


def test_policy_merge_is_most_restrictive():
    m = lr.Policy.merge(lr.Policy("user", None, True, 32, 300),
                        lr.Policy("battery", 16384, False, 16, 120),
                        lr.Policy("other", 8192, True, 64, 0))
    assert (m.ctx_max, m.allow_draft, m.spec_draft_max, m.idle_s) == (8192, False, 16, 120)


def _env(tmp_path, **kw):
    env = {"AWOS_MODELS_DIR": str(tmp_path), "AWOS_LOCAL_GGUF_FILE": "Qwen3.5-9B-Q4_K_M.gguf"}
    env.update(kw)
    return env


def test_plan_battery_keeps_ctx_but_blocks_draft(tmp_path):
    draft = write_gguf(tmp_path / "d.gguf", qwen35_kv())
    write_gguf(tmp_path / "Qwen3.5-9B-Q4_K_M.gguf", qwen35_kv())
    env = _env(tmp_path, AWOS_LOCAL_SPEC="draft", AWOS_LOCAL_DRAFT_GGUF=str(draft))
    p = lr.plan(env, lr.parse_pmset(PMSET_BATT), ram_mb=16384)
    # battery idle is 120 s, lifted to the loop command timeout (120) + margin (60)
    assert p.ctx == 24576 and p.spec == "ngram" and p.idle_s == 180
    assert any("idle 120s -> 180s" in n for n in p.notes)
    assert "--spec-type" in p.spec_args and "ngram-simple" in p.spec_args
    assert any("draft model off" in n for n in p.notes)
    ac = lr.plan(env, lr.parse_pmset(PMSET_AC), ram_mb=16384)
    assert ac.spec == "draft" and "-md" in ac.spec_args and ac.idle_s == 300


def test_plan_low_battery_caps_ctx(tmp_path):
    p = lr.plan(_env(tmp_path), lr.parse_pmset(PMSET_LOW), ram_mb=16384)
    assert p.ctx == 16384 and p.policy.name.endswith("battery-low")
    p2 = lr.plan(_env(tmp_path, AWOS_LOCAL_BATTERY_CTX_MAX="12288"),
                 lr.parse_pmset(PMSET_BATT), ram_mb=16384)
    assert p2.ctx == 12288


def test_plan_rejects_bad_spec_and_missing_or_mismatched_draft(tmp_path):
    ac = lr.parse_pmset(PMSET_AC)
    with pytest.raises(lr.PlanError, match="use one of"):
        lr.plan(_env(tmp_path, AWOS_LOCAL_SPEC="eagle"), ac, ram_mb=16384)
    with pytest.raises(lr.PlanError, match="AWOS_LOCAL_DRAFT_GGUF"):
        lr.plan(_env(tmp_path, AWOS_LOCAL_SPEC="draft"), ac, ram_mb=16384)
    write_gguf(tmp_path / "Qwen3.5-9B-Q4_K_M.gguf", qwen35_kv(40))
    bad = write_gguf(tmp_path / "bad.gguf", qwen35_kv(41))
    with pytest.raises(lr.PlanError, match="tokenizer"):
        lr.plan(_env(tmp_path, AWOS_LOCAL_SPEC="draft", AWOS_LOCAL_DRAFT_GGUF=str(bad)), ac,
                ram_mb=16384)


def test_spec_draft_max_is_bounded(tmp_path):
    p = lr.plan(_env(tmp_path, AWOS_LOCAL_SPEC="ngram", AWOS_LOCAL_SPEC_DRAFT_MAX="999"),
                lr.parse_pmset(PMSET_AC), ram_mb=16384)
    assert "64" in p.spec_args and "999" not in p.spec_args


# ── memory admission ─────────────────────────────────────────────────────────
def test_admission_fits_and_refuses(tmp_path):
    ok = lr.admit(tmp_path / "Qwen3.5-9B-Q4_K_M.gguf", 24576, ram_mb=16384, env={})
    assert ok.ok and ok.budget_mb == 12288
    big = lr.admit(tmp_path / "Qwen3.5-9B-Q4_K_M.gguf", 262144, ram_mb=16384, env={})
    assert not big.ok and "would swap" in big.message and "AWOS_LOCAL_CONTEXT=" in big.message
    with pytest.raises(lr.PlanError, match="would swap"):
        lr.plan(_env(tmp_path, AWOS_LOCAL_CONTEXT="262144"), lr.parse_pmset(PMSET_AC),
                ram_mb=16384)


def test_admission_counts_draft_and_host_cache(tmp_path):
    draft = write_gguf(tmp_path / "d.gguf", qwen35_kv())
    a = lr.admit(tmp_path / "Qwen3.5-9B-Q4_K_M.gguf", 8192, draft_path=draft, draft_ctx=8192,
                 cache_ram_mb=1000, ram_mb=16384, env={})
    assert "draft" in a.breakdown and a.breakdown["host_cache"]["cache_ram_mb"] == 1000
    assert a.need_mb > lr.CATALOG["Qwen3.5-9B-Q4_K_M.gguf"]["rss_mb"] + 1000


# ── fake llama-server ────────────────────────────────────────────────────────
class FakeServer:
    """Enough of llama-server's /health /props /slots /completion /apply-template."""

    def __init__(self, sdir: Path, model: Path, save_supported=True):
        self.sdir, self.model, self.save_supported = sdir, model, save_supported
        self.sleeping = False
        self.processing = False
        self.down = False
        self.slot_tokens = 0
        self.calls = []
        self.bodies = []
        self.total_slots = 1
        self.chat_template = "{{chatml}}"
        self.stay_asleep = False
        self.tokenizer = None          # callable(text) -> list, or None for no /tokenize

    def __call__(self, method, url, body, timeout):
        path = url.split("127.0.0.1:8080", 1)[1]
        self.calls.append((method, path))
        self.bodies.append((path, body))
        if self.down:
            raise ConnectionRefusedError()
        if path == "/health":
            return 200, {"status": "ok"}
        if path == "/props":
            props = {"model_path": str(self.model), "build_info": "b1-test",
                     "chat_template": self.chat_template, "is_sleeping": self.sleeping,
                     "total_slots": self.total_slots,
                     "default_generation_settings": {"n_ctx": 8192}}
            return 200, {k: v for k, v in props.items() if v is not None}
        if path == "/tokenize":
            if self.tokenizer is None:
                return 404, {}
            return 200, {"tokens": self.tokenizer(body["content"])}
        if path == "/slots":
            return 200, [{"id": 0, "is_processing": self.processing, "speculative": True}]
        if path == "/apply-template":
            msgs = body["messages"]
            text = "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in msgs)
            return 200, {"prompt": text + "<|im_start|>assistant\n"}
        if path == "/completion":
            if self.sleeping and not self.stay_asleep:
                self.sleeping = False      # any task wakes a sleeping llama-server
            n = len(body["prompt"]) // 4
            self.slot_tokens = n
            return 200, {"timings": {"prompt_n": n}, "tokens_evaluated": n}
        if path.startswith("/slots/0?action="):
            if not self.save_supported:
                return 501, {"error": {"message": "This server does not support slots action."}}
            f = self.sdir / body["filename"]
            if path.endswith("save"):
                self.sdir.mkdir(parents=True, exist_ok=True)
                f.write_bytes(json.dumps({"n": self.slot_tokens}).encode())
                return 200, {"n_saved": self.slot_tokens}
            if not f.exists():
                return 400, {"error": "missing"}
            self.slot_tokens = json.loads(f.read_bytes())["n"]
            return 200, {"n_restored": self.slot_tokens}
        return 404, {}


@pytest.fixture
def fake(tmp_path):
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF" + b"\0" * 64)
    return FakeServer(tmp_path / "slots", model)


BASE = "http://127.0.0.1:8080"
CMD = "llama-server -m m.gguf -c 8192 -np 1 -fa on --temp 0.7 --spec-type ngram-simple"


def test_render_prefix_cuts_before_user_turn(fake):
    pre = lr.render_prefix([{"role": "system", "content": "SYS"}], base=BASE, http=fake)
    assert pre == "<|im_start|>system\nSYS<|im_end|>\n"


def test_prefix_saved_then_restored(fake):
    first = lr.ensure_prefix_cached("P" * 400, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD)
    assert first["status"] == "saved" and first["n_tokens"] == 100
    files = list(fake.sdir.glob("awos-prefix-*.bin"))
    assert len(files) == 1 and files[0].with_suffix(".json").exists()
    fake.slot_tokens = 0                               # server restarted / slot evicted
    second = lr.ensure_prefix_cached("P" * 400, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD)
    assert second["status"] == "restored" and fake.slot_tokens == 100
    assert ("POST", "/completion") not in fake.calls[len(fake.calls) - 3:]


def test_prefix_key_invalidates_on_any_change(fake):
    props = fake("GET", BASE + "/props", None, 1)[1]
    k = lr.prefix_key(props, "P", CMD)
    assert lr.prefix_key(props, "Q", CMD) != k                                   # prefix text
    assert lr.prefix_key({**props, "build_info": "b2"}, "P", CMD) != k           # llama.cpp build
    assert lr.prefix_key({**props, "chat_template": "x"}, "P", CMD) != k         # template
    assert lr.prefix_key(props, "P", CMD.replace("-c 8192", "-c 4096")) != k     # KV flags
    assert lr.prefix_key(props, "P", CMD.replace("-fa on", "-fa off")) != k
    fake.model.write_bytes(b"GGUF" + b"\1" * 64)                                 # model file
    assert lr.prefix_key(props, "P", CMD) != k


def test_prefix_key_ignores_sampling_and_spec_flags(fake):
    props = fake("GET", BASE + "/props", None, 1)[1]
    other = "llama-server -m m.gguf -c 8192 -np 1 -fa on --temp 0.2"
    assert lr.prefix_key(props, "P", CMD) == lr.prefix_key(props, "P", other)


def test_prefix_unsupported_without_slot_save_path(fake):
    fake.save_supported = False
    r = lr.ensure_prefix_cached("P" * 40, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD)
    assert r["status"] == "unsupported" and "--slot-save-path" in r["detail"]


def test_prefix_gc_keeps_newest(fake):
    for i in range(6):
        lr.ensure_prefix_cached(f"prefix {i} " * 20, base=BASE, http=fake, sdir=fake.sdir,
                                cmd=CMD, keep=3)
    assert len(list(fake.sdir.glob("awos-prefix-*.bin"))) == 3


def test_prepare_first_step_gating(fake, tmp_path):
    msgs = [{"role": "system", "content": "SYS " * 50}]
    env = {"AWOS_LOCAL_SLOT_DIR": str(fake.sdir), "AWOS_MODELS_DIR": str(tmp_path)}
    assert lr.prepare_first_step(msgs, env=env, http=fake)["status"] == "skipped"
    env["AWOS_PROVIDER"] = "local"
    assert lr.prepare_first_step(msgs, env={**env, "AWOS_LOCAL_PREFIX_CACHE": "0"},
                                 http=fake)["status"] == "skipped"
    assert lr.prepare_first_step(msgs, env=env, http=fake)["status"] == "saved"
    assert lr.prepare_first_step(msgs, env=env, http=fake)["status"] == "restored"
    fake.down = True
    r = lr.prepare_first_step(msgs, env=env, http=fake)           # never raises
    assert r["status"] == "error"
    started = []
    r = lr.prepare_first_step(msgs, env={**env, "AWOS_LOCAL_AUTOSTART": "1"}, http=fake,
                              starter=lambda: started.append(1) or False)
    assert started and r["status"] == "error"


# ── idle watchdog ────────────────────────────────────────────────────────────
def test_watch_step_never_stops_a_busy_or_awake_server():
    assert lr.watch_step({"state": "up"}, 10.0, 1000.0, 300) == (None, False)
    busy = {"state": "sleeping", "slots": [{"is_processing": True}]}
    assert lr.watch_step(busy, 10.0, 1000.0, 300) == (None, False)
    since, stop = lr.watch_step({"state": "sleeping"}, None, 50.0, 300)
    assert since == 50.0 and not stop
    assert lr.watch_step({"state": "sleeping"}, 50.0, 351.0, 300) == (50.0, True)


def test_watch_stops_after_sleep_window(fake):
    fake.sleeping = True
    t = [0.0]
    stopped = []

    def clock():
        return t[0]

    def sleep(s):
        t[0] += s

    rc = lr.watch(env={"AWOS_LOCAL_EXIT_AFTER_SLEEP_S": "60"}, http=fake,
                  stopper=lambda: stopped.append(1), clock=clock, sleep=sleep, interval=15,
                  max_ticks=20)
    assert rc == 0 and stopped and t[0] >= 60


def test_watch_does_not_stop_awake_server(fake):
    stopped = []
    rc = lr.watch(env={"AWOS_LOCAL_EXIT_AFTER_SLEEP_S": "0"}, http=fake,
                  stopper=lambda: stopped.append(1), sleep=lambda s: None, max_ticks=5)
    assert rc == 2 and not stopped
    fake.down = True
    assert lr.watch(env={}, http=fake, stopper=lambda: stopped.append(1),
                    sleep=lambda s: None, max_ticks=5) == 1 and not stopped


def test_ensure_server_starts_only_when_down(fake):
    runs = []
    fake_run = lambda *a, **k: runs.append(a) or subprocess.CompletedProcess(a, 0, "", "")  # noqa
    assert lr.ensure_server(env={}, http=fake, run=fake_run) and not runs
    fake.down = True
    assert lr.ensure_server(env={}, http=fake, run=fake_run) and runs
    assert runs[0][0][-1] == "start"


# ── status + doctor ──────────────────────────────────────────────────────────
def test_status_and_doctor_row(fake, tmp_path):
    env = _env(tmp_path, AWOS_PROVIDER="local", AWOS_LOCAL_SLOT_DIR=str(fake.sdir),
               AWOS_LOCAL_SPEC="ngram")
    lr.ensure_prefix_cached("P" * 40, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD)
    s = lr.status(env, http=fake, power=lr.parse_pmset(PMSET_BATT))
    assert s["server"] == "up" and s["spec_live"] is True and s["prefix_cache_files"] == 1
    line = lr.status_line(s)
    assert "server up" in line and "battery 64%" in line and "spec ngram (live)" in line
    fake.sleeping = True
    assert lr.status(env, http=fake, power=lr.parse_pmset(PMSET_AC))["server"] == "sleeping"

    c = doctor.check_local_runtime(env, status_fn=lambda e: s)
    assert c.ok and not c.required and "prefix cache 1 file(s)" in c.detail
    down = {**s, "server": "down"}
    c = doctor.check_local_runtime(env, status_fn=lambda e: down)
    assert not c.ok and "local_model.sh start" in c.fix and not c.required
    c = doctor.check_local_runtime({}, status_fn=lambda e: {**down, "local_mode": False})
    assert c.ok and "not in use" in c.detail
    bad = {**s, "plan": {"ok": False, "error": "refused: would swap", "policy": "ac"}}
    c = doctor.check_local_runtime(env, status_fn=lambda e: bad)
    assert not c.ok and "would swap" in c.detail


def test_shell_cmd_reflects_plan(tmp_path):
    env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "HOME": str(tmp_path),
           "AWOS_MODELS_DIR": str(tmp_path), "AWOS_LOCAL_SPEC": "ngram",
           "AWOS_LOCAL_IDLE_S": "300", "AWOS_LOCAL_BATTERY_POLICY": "ignore",
           "LLAMA_SERVER": "/bin/echo"}
    # /bin/echo stands in for a llama-server build without speculative decoding
    r = subprocess.run(["bash", str(REPO / "scripts" / "local_model.sh"), "cmd"], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 3 and "has no --spec-type" in r.stderr
    bad =subprocess.run(["bash", str(REPO / "scripts" / "local_model.sh"), "cmd"],
                         env={**env, "AWOS_LOCAL_SPEC": "nope"}, capture_output=True,
                         text=True, timeout=30)
    assert bad.returncode == 3 and "use one of" in bad.stderr
    off = subprocess.run(["bash", str(REPO / "scripts" / "local_model.sh"), "cmd"],
                         env={**env, "AWOS_LOCAL_SPEC": "off"}, capture_output=True,
                         text=True, timeout=30)
    assert off.returncode == 0, off.stderr
    out = off.stdout
    assert "--sleep-idle-seconds 300" in out and "--slot-save-path" in out
    assert "--cache-ram 2048" in out and "--spec-type" not in out


# ── B4 hardening: degraded keys, wake, token alignment, KV flags, id_slot ────
def test_degraded_props_are_never_keyed(fake, tmp_path):
    fake.chat_template = None
    r = lr.ensure_prefix_cached("P" * 40, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD)
    assert r["status"] == "skipped" and "chat_template" in r["detail"]
    fake.chat_template = "{{chatml}}"
    fake.model = tmp_path / "missing.gguf"
    r = lr.ensure_prefix_cached("P" * 40, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD)
    assert r["status"] == "skipped" and "unreadable" in r["detail"]
    assert not list(fake.sdir.glob("awos-prefix-*")) if fake.sdir.exists() else True
    with pytest.raises(ValueError):
        lr.prefix_key({"model_path": "", "chat_template": "x"}, "P")
    with pytest.raises(ValueError):
        lr.prefix_key({"model_path": str(tmp_path / "nope"), "chat_template": "x"}, "P")


def test_sleeping_server_is_woken_or_skipped(fake):
    fake.sleeping = True
    r = lr.ensure_prefix_cached("P" * 40, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD,
                                wake=False)
    assert r == {"status": "skipped", "detail": "server sleeping"}
    fake.stay_asleep = True
    r = lr.ensure_prefix_cached("P" * 40, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD)
    assert r["status"] == "skipped" and r["detail"] == "server sleeping"
    fake.stay_asleep = False
    r = lr.ensure_prefix_cached("P" * 40, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD)
    assert r["status"] == "saved" and not fake.sleeping
    wake = [b for p, b in fake.bodies if p == "/completion"][-2]
    assert wake["cache_prompt"] is False and wake["n_predict"] == 0


SPECIALS = re.compile(r"(<\|im_start\|>|<\|im_end\|>)")


def toy_tokenizer(text):
    """Specials are single tokens; "hello" merges into one token; else one per char."""
    out = []
    for part in SPECIALS.split(text):
        if SPECIALS.fullmatch(part):
            out.append(part)
            continue
        i = 0
        while i < len(part):
            if part.startswith("hello", i):
                out.append("hello")
                i += 5
            else:
                out.append(part[i])
                i += 1
    return out


def test_token_alignment_backs_off_to_turn_boundary(fake):
    fake.tokenizer = toy_tokenizer
    head = "<|im_start|>system\nSYS<|im_end|>\n"
    probe = head + "<|im_start|>user\nhello<|im_end|>\n"
    bad = head + "<|im_start|>user\nhel"     # text prefix, but "hel" != the token "hello"
    r = lr.ensure_prefix_cached(bad, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD,
                                probe_text=probe)
    assert r["status"] == "saved" and r["token_check"] == "backoff:1"
    saved = [b for p, b in fake.bodies if p == "/completion"][-1]["prompt"]
    assert saved == head
    ok = lr.ensure_prefix_cached(head, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD,
                                 probe_text=probe)
    assert ok["token_check"] == "ok"
    r = lr.ensure_prefix_cached("hel", base=BASE, http=fake, sdir=fake.sdir, cmd=CMD,
                                probe_text="hello")
    assert r["status"] == "skipped" and r["token_check"] == "misaligned"
    fake.tokenizer = None                     # no /tokenize: proceed, marked unchecked
    r = lr.ensure_prefix_cached(head, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD,
                                probe_text=probe)
    assert r["status"] in ("saved", "restored") and r["token_check"] == "unavailable"


def test_prepare_first_step_passes_probe(fake, tmp_path):
    fake.tokenizer = toy_tokenizer
    env = {"AWOS_LOCAL_SLOT_DIR": str(fake.sdir), "AWOS_MODELS_DIR": str(tmp_path),
           "AWOS_PROVIDER": "local"}
    r = lr.prepare_first_step([{"role": "system", "content": "SYS"}], env=env, http=fake)
    assert r["status"] == "saved" and r["token_check"] == "ok"


def test_id_slot_only_with_multiple_slots(fake):
    lr.ensure_prefix_cached("P" * 40, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD)
    assert "id_slot" not in [b for p, b in fake.bodies if p == "/completion"][-1]
    fake.total_slots = 2
    lr.ensure_prefix_cached("Q" * 40, slot=1, base=BASE, http=fake, sdir=fake.sdir, cmd=CMD)
    assert [b for p, b in fake.bodies if p == "/completion"][-1]["id_slot"] == 1


def test_kv_flags_forms_and_new_flags(fake, tmp_path):
    sig = lr.kv_flags_signature
    assert sig("llama-server -c 8192 -fa on") == sig("llama-server --ctx-size=8192 --flash-attn=on")
    assert sig("x --rope-freq-scale -1.5") == "--rope-freq-scale=-1.5"
    assert sig("x --swa-full -c 4096") == "--ctx-size=4096 --swa-full="
    props = fake("GET", BASE + "/props", None, 1)[1]
    k = lr.prefix_key(props, "P", CMD)
    lora = tmp_path / "a.gguf"
    lora.write_bytes(b"lora-1")
    for extra in (f"--lora {lora}", f"--lora-scaled {lora} 0.5", "--control-vector cv.gguf",
                  "--control-vector-layer-range 2 10", "--rope-scaling yarn",
                  "--rope-freq-base 1e6", "--yarn-orig-ctx 32768", "--yarn-ext-factor=1.0",
                  "--override-kv a=int:1"):
        assert lr.prefix_key(props, "P", f"{CMD} {extra}") != k, extra
    with_lora = lr.prefix_key(props, "P", f"{CMD} --lora={lora}")
    assert with_lora == lr.prefix_key(props, "P", f"{CMD} --lora {lora}")
    lora.write_bytes(b"lora-2")                                   # same path, new weights
    assert lr.prefix_key(props, "P", f"{CMD} --lora {lora}") != with_lora
    # scaled: both values kept
    assert sig(f"x --lora-scaled {lora} 0.5") != sig(f"x --lora-scaled {lora} 0.7")


# ── B5: idle floor + task lease ──────────────────────────────────────────────
def test_cmd_timeout_mirrors_loop_executor():
    from scaffold.agent import loop_executor as lx
    assert lr.DEFAULT_LOOP_CMD_TIMEOUT_S == lx.DEFAULT_CMD_TIMEOUT_S
    assert lr.MAX_LOOP_CMD_TIMEOUT_S == lx.MAX_CMD_TIMEOUT_S


def test_idle_floor_follows_command_timeout(tmp_path):
    batt = lr.parse_pmset(PMSET_LOW)                       # battery-low policy: idle 60
    p = lr.plan(_env(tmp_path, AWOS_LOOP_CMD_TIMEOUT_S="300"), batt, ram_mb=16384)
    assert p.idle_s == 360 and p.policy.idle_s == 360
    p = lr.plan(_env(tmp_path, AWOS_LOOP_CMD_TIMEOUT_S="9999", AWOS_LOCAL_IDLE_MARGIN_S="0"),
                batt, ram_mb=16384)
    assert p.idle_s == 600                                 # capped like the loop's max
    p = lr.plan(_env(tmp_path, AWOS_LOCAL_IDLE_S="0", AWOS_LOCAL_BATTERY_POLICY="ignore"),
                batt, ram_mb=16384)
    assert p.idle_s == 0                                   # 0 = never sleep, untouched
    p = lr.plan(_env(tmp_path, AWOS_LOCAL_IDLE_S="900"), lr.parse_pmset(PMSET_AC), ram_mb=16384)
    assert p.idle_s == 900


def test_task_lease_heartbeat_and_release(tmp_path):
    env = {"AWOS_MODELS_DIR": str(tmp_path), "AWOS_LOCAL_LEASE_TTL_S": "30"}
    assert lr.live_leases(env) == []
    with lr.task_lease("job-1", env=env, interval=0.02) as lease:
        assert lease.path.is_file()
        live = lr.live_leases(env)
        assert len(live) == 1 and live[0]["task"] == "job-1" and live[0]["pid"] == os.getpid()
        first = json.loads(lease.path.read_text())["heartbeat"]
        deadline = time.time() + 3
        while lease.beats < 3 and time.time() < deadline:
            time.sleep(0.02)
        assert lease.beats >= 3
        assert json.loads(lease.path.read_text())["heartbeat"] >= first
    assert not lease.path.exists() and lr.live_leases(env) == []


def test_stale_and_dead_leases_are_ignored_and_cleaned(tmp_path):
    env = {"AWOS_MODELS_DIR": str(tmp_path)}
    d = lr.lease_dir(env)
    d.mkdir(parents=True)
    import socket
    host = socket.gethostname()
    old = d / "task-1-old.json"
    old.write_text(json.dumps({"pid": os.getpid(), "host": host, "heartbeat": time.time() - 999,
                               "ttl": 90}))
    dead = d / "task-2-dead.json"
    dead.write_text(json.dumps({"pid": 2 ** 22 + 12345, "host": host,
                                "heartbeat": time.time(), "ttl": 90}))
    assert lr.live_leases(env) == [] and not old.exists() and not dead.exists()


def test_task_lease_never_raises_on_unwritable_dir(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")                          # models dir is a file: mkdir fails
    with lr.task_lease("t", env={"AWOS_MODELS_DIR": str(f)}) as lease:
        assert lease.path is None


def _clock():
    t = [0.0]
    return t, (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + s))


def test_watch_never_stops_while_leased(fake, tmp_path):
    fake.sleeping = True
    t, clock, sleep = _clock()
    stopped = []
    leases = [["lease"]]
    env = {"AWOS_LOCAL_EXIT_AFTER_SLEEP_S": "30", "AWOS_MODELS_DIR": str(tmp_path)}
    rc = lr.watch(env=env, http=fake, stopper=lambda: stopped.append(1), clock=clock,
                  sleep=sleep, interval=15, max_ticks=10, lease_fn=lambda: leases[0])
    assert rc == 2 and not stopped
    leases[0] = []
    rc = lr.watch(env=env, http=fake, stopper=lambda: stopped.append(1), clock=clock,
                  sleep=sleep, interval=15, max_ticks=10, lease_fn=lambda: leases[0])
    assert rc == 0 and stopped


def test_watch_with_real_lease_file(fake, tmp_path):
    fake.sleeping = True
    t, clock, sleep = _clock()
    stopped = []
    env = {"AWOS_LOCAL_EXIT_AFTER_SLEEP_S": "0", "AWOS_MODELS_DIR": str(tmp_path)}
    with lr.task_lease("t", env=env):
        assert lr.watch(env=env, http=fake, stopper=lambda: stopped.append(1), clock=clock,
                        sleep=sleep, max_ticks=3) == 2 and not stopped


def _plan(idle, spec_args=(), ctx=8192, cache=2048):
    return lr.Plan(ctx, "off", list(spec_args), idle, cache, lr.Policy("x"),
                   lr.PowerState("battery"), lr.Admission(True, 0, 0, {}, ""), [])


def test_cmd_matches_plan():
    cmd = ("llama-server -m m.gguf -c 8192 --cache-ram 2048 --sleep-idle-seconds 300 "
           "--spec-type ngram-simple --spec-draft-n-max 16")
    assert lr.cmd_matches_plan(cmd, _plan(300, ["--spec-type", "ngram-simple",
                                                "--spec-draft-n-max", "16"]))
    assert not lr.cmd_matches_plan(cmd, _plan(180, ["--spec-type", "ngram-simple",
                                                    "--spec-draft-n-max", "16"]))
    assert not lr.cmd_matches_plan(cmd, _plan(300))                 # spec now off
    assert not lr.cmd_matches_plan(cmd.replace("-c 8192", "-c 4096"),
                                   _plan(300, ["--spec-type", "ngram-simple",
                                               "--spec-draft-n-max", "16"]))
    assert lr.cmd_matches_plan("", _plan(1))                        # unknown: no restart


def test_watch_replans_on_power_change_and_restarts_only_idle_unleased(fake, tmp_path):
    cmd = "llama-server -c 8192 --cache-ram 2048 --sleep-idle-seconds 300"
    sources = iter(["ac", "battery"] + ["battery"] * 20)
    leases = [["busy"]]
    restarts, planned = [], []
    t, clock, sleep = _clock()

    def plan_fn(ps):
        planned.append(ps.source)
        return _plan(180 if ps.source == "battery" else 300)

    kw = dict(env={"AWOS_LOCAL_EXIT_AFTER_SLEEP_S": "100000", "AWOS_MODELS_DIR": str(tmp_path)},
              http=fake, stopper=lambda: None, clock=clock, sleep=sleep,
              power_fn=lambda: lr.PowerState(next(sources)), restarter=lambda: restarts.append(1),
              plan_fn=plan_fn, cmd_fn=lambda: cmd, lease_fn=lambda: leases[0])
    fake.processing = False
    # A lease is held: the plan changed (idle 300 -> 180) but no restart.
    lr.watch(max_ticks=4, **kw)
    assert planned == ["battery"] and not restarts
    # Same watcher state can't be resumed across calls, so check the idle/lease gate
    # in one run: lease released after tick 3, server busy until tick 5.
    sources = iter(["ac", "battery"] + ["battery"] * 20)
    kw["power_fn"] = lambda: lr.PowerState(next(sources))
    ticks = [0]

    def lease_fn():
        ticks[0] += 1
        return ["busy"] if ticks[0] <= 3 else []

    fake.processing = True
    real_sleep = kw["sleep"]

    def sleep2(s):
        real_sleep(s)
        if ticks[0] >= 6:
            fake.processing = False

    kw.update(lease_fn=lease_fn, sleep=sleep2)
    lr.watch(max_ticks=10, **kw)
    assert restarts == [1]
    # No change in flags -> never a restart.
    restarts.clear()
    sources = iter(["ac", "battery"] + ["battery"] * 20)
    kw.update(power_fn=lambda: lr.PowerState(next(sources)), lease_fn=lambda: [],
              plan_fn=lambda ps: _plan(300))
    lr.watch(max_ticks=5, **kw)
    assert not restarts
