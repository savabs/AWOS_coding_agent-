"""local_runtime: GGUF arch, policy merge, admission, prefix-KV save/restore, idle watch,
doctor row. All fakes: fake pmset text, a fake llama-server HTTP API, a fake process."""
from __future__ import annotations

import json
import struct
import subprocess
import sys
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
    assert p.ctx == 24576 and p.spec == "ngram" and p.idle_s == 120
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

    def __call__(self, method, url, body, timeout):
        path = url.split("127.0.0.1:8080", 1)[1]
        self.calls.append((method, path))
        if self.down:
            raise ConnectionRefusedError()
        if path == "/health":
            return 200, {"status": "ok"}
        if path == "/props":
            return 200, {"model_path": str(self.model), "build_info": "b1-test",
                         "chat_template": "{{chatml}}", "is_sleeping": self.sleeping,
                         "default_generation_settings": {"n_ctx": 8192}}
        if path == "/slots":
            return 200, [{"id": 0, "is_processing": self.processing, "speculative": True}]
        if path == "/apply-template":
            msgs = body["messages"]
            text = "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in msgs)
            return 200, {"prompt": text + "<|im_start|>assistant\n"}
        if path == "/completion":
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
