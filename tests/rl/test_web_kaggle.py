"""Online-Mind2Web on Kaggle without Kaggle, GPUs or network: settings parsing, staging (files + metadata),
the kernel's command lines, the harness's per-worker --base-url, and that the gated task file never reaches
the repo, the logs or the kernel output. Stdlib only.

    PYTHONPATH=.:tests ~/.awos/rl-venv/bin/python -m pytest tests/rl/test_web_kaggle.py -q
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
KDIR = ROOT / "kaggle" / "web-eval"
MARKER = "ZZ-GATED-TASK-TEXT-7f3a"            # stands in for the task file's content


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ST = _load("awos_web_eval_stage", KDIR / "stage.py")
RUN = _load("awos_web_eval_run", KDIR / "run.py")

SHA = "ab" * 32
SPECS = f'''LLAMA_TAG="b99999"
MODEL_SPECS="
m-hf      org/M-GGUF                    M-Q4_K_M.gguf                       {"1" * 40} 11 {SHA} 2
m-rel     gh-release:o/r               rel-Q4_K_M-00001-of-00002.gguf     rel-tag     7  {SHA} 2
other     org/O-GGUF                    O.gguf                              {"2" * 40} 5  {SHA} 2
"
'''


def envs(tmp: Path, body: str = "") -> tuple:
    k = tmp / "kaggle.env"
    k.write_text(body or 'MODELS="m-hf m-rel"\nSPLIT="dev"\nWORKERS=4\nGPUS=2\nMAX_STEPS=25\nMAX_SECONDS=900\n'
                         'KERNEL_SLUG="awos-web-eval"\nDATASET_SLUG="awos-web-eval-data"\nRUN_NAME="t"\n')
    w = tmp / "web.env"
    w.write_text("CTX_PER_SLOT=4096\nNP=2\n")
    e = tmp / "eval.env"
    e.write_text(SPECS)
    return k, w, e


def fixtures(tmp: Path) -> tuple:
    tj = tmp / "in" / "Online_Mind2Web.json"
    tj.parent.mkdir(parents=True)
    tj.write_text(json.dumps([{"task_id": "t1", "confirmed_task": MARKER, "website": "example.com", "level": "easy"},
                              {"task_id": "t2", "confirmed_task": MARKER, "website": "example.org", "level": "hard"}]))
    md = tmp / "models"
    md.mkdir()
    (md / "M-Q4_K_M.gguf").write_bytes(b"x" * 11)
    (md / "rel-Q4_K_M-00001-of-00002.gguf").write_bytes(b"y" * 7)
    (md / "rel-Q4_K_M-00002-of-00002.gguf").write_bytes(b"z" * 3)
    (md / "O.gguf").write_bytes(b"o" * 5)                  # not selected: must not be staged
    return tj, md


# ----------------------------------------------------------------- settings --

def test_repo_settings_parse_and_match_the_ci_files():
    s = ST.load_settings()
    assert s["MODELS"] == ["qwen3.5-4b", "a-final"] and s["SPLIT"] == "dev"
    assert s["MAX_STEPS"] == 25 and s["WORKERS"] >= s["GPUS"] == 2
    assert s["SLOTS_PER_SERVER"] == -(-s["WORKERS"] // s["GPUS"])
    ev = ST.parse_env(ROOT / "ci" / "rl_eval.env")
    web = ST.parse_env(ROOT / "ci" / "rl_web.env")
    assert s["LLAMA_TAG"] == ev["LLAMA_TAG"] == "b11146"
    assert s["CTX_PER_SLOT"] == int(web["CTX_PER_SLOT"]) and s["MAX_SECONDS"] == int(web["MAX_SECONDS"])
    assert s["KERNEL_SLUG"] and s["DATASET_SLUG"] and s["DEADLINE_HOURS"] < 12
    text = (ROOT / "ci" / "rl_web_kaggle.env").read_text()
    assert not re.search(r"hf_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{10,}|KGAT_|TOKEN=|KEY=", text)    # no secrets


def test_settings_from_files_and_bad_values(tmp_path):
    k, w, e = envs(tmp_path)
    s = ST.load_settings(k, w, e)
    assert s["MODELS"] == ["m-hf", "m-rel"] and s["CTX_PER_SLOT"] == 4096 and s["LLAMA_TAG"] == "b99999"
    assert s["SLOTS_PER_SERVER"] == 2 and s["RUN_NAME"] == "t" and s["LLAMA_BUILD_SOURCE"] == ""
    for bad in ('MODELS="nope"\n', 'MODELS="m-hf"\nSPLIT="train"\n', 'MODELS="m-hf"\nWORKERS=0\n',
                'MODELS="m-hf"\nDEADLINE_HOURS=12\n', 'MODELS="m-hf m-hf m-hf m-hf m-hf"\n', 'MODELS=""\n',
                'MODELS="m-hf"\nKERNEL_SLUG="a b"\n'):
        k.write_text(bad)
        with pytest.raises(SystemExit):
            ST.load_settings(k, w, e)
    k.write_text('MODELS="m-hf"\nWORKERS=3\nGPUS=2\n')
    assert ST.load_settings(k, w, e)["SLOTS_PER_SERVER"] == 2


def test_settings_cli_prints_env_and_specs(tmp_path, capsys):
    k, w, e = envs(tmp_path)
    assert ST.main(["settings", "--env", str(k), "--web-env", str(w), "--specs", str(e)]) == 0
    out = capsys.readouterr().out
    assert "MODELS=m-hf m-rel\n" in out and "KERNEL_SLUG=awos-web-eval\n" in out and "specs" not in out
    ST.main(["settings", "--format", "specs", "--env", str(k), "--web-env", str(w), "--specs", str(e)])
    lines = capsys.readouterr().out.split("\n")
    assert lines[1].split() == ["m-rel", "gh-release:o/r", "rel-Q4_K_M-00001-of-00002.gguf", "rel-tag", SHA]


# ------------------------------------------------------------------- staging --

def test_stage_builds_a_private_internet_gpu_dataset_and_kernel(tmp_path, capsys):
    k, w, e = envs(tmp_path, 'MODELS="m-hf m-rel"\nWORKERS=4\nLLAMA_BUILD_SOURCE="alice/awos-web-eval-build"\n'
                             'RUN_NAME="r1"\n')
    tj, md = fixtures(tmp_path)
    out = tmp_path / "st"
    assert ST.main(["stage", "--env", str(k), "--web-env", str(w), "--specs", str(e), "--tasks-json", str(tj),
                    "--models-dir", str(md), "--out", str(out), "--user", "alice"]) == 0
    assert MARKER not in capsys.readouterr().out                       # only the count is printed
    ds, kn = out / "dataset", out / "kernel"
    assert sorted(p.name for p in ds.iterdir()) == sorted([
        "web_eval_src.tar.gz", "Online_Mind2Web.json", "web_eval_config.json", "dataset-metadata.json",
        "M-Q4_K_M.gguf", "rel-Q4_K_M-00001-of-00002.gguf", "rel-Q4_K_M-00002-of-00002.gguf"])
    dmeta = json.loads((ds / "dataset-metadata.json").read_text())
    assert dmeta["id"] == "alice/awos-web-eval-data" and dmeta["isPrivate"] is True
    meta = json.loads((kn / "kernel-metadata.json").read_text())
    assert meta["id"] == "alice/awos-web-eval" and meta["title"] == "awos-web-eval"
    assert meta["enable_internet"] is True and meta["enable_gpu"] is True and meta["is_private"] is True
    assert meta["machine_shape"] == "NvidiaTeslaT4" and meta["code_file"] == "run.py"
    assert meta["dataset_sources"] == ["alice/awos-web-eval-data"]
    assert meta["kernel_sources"] == ["alice/awos-web-eval-build"]
    assert (kn / "run.py").read_text() == (KDIR / "run.py").read_text() and sorted(p.name for p in kn.iterdir()) == [
        "kernel-metadata.json", "run.py"]
    cfg = json.loads((ds / "web_eval_config.json").read_text())
    assert cfg["models"] == [{"label": "m-hf", "file": "M-Q4_K_M.gguf"},
                             {"label": "m-rel", "file": "rel-Q4_K_M-00001-of-00002.gguf"}]
    assert (cfg["workers"], cfg["gpus"], cfg["slots_per_server"], cfg["ctx_per_slot"]) == (4, 2, 2, 4096)
    assert (cfg["max_steps"], cfg["max_seconds"], cfg["llama_tag"], cfg["run_name"]) == (25, 900, "b99999", "r1")
    assert cfg["ntfy_topic"] == RUN.DEFAULTS["ntfy_topic"] and MARKER not in json.dumps(cfg)
    with tarfile.open(ds / "web_eval_src.tar.gz") as tf:
        names = tf.getnames()
    assert "scaffold/rl/web/runner.py" in names and "scaffold/rl/web/data/dev_ids.json" in names
    assert "scaffold/agent/desktop/micro_agent.py" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") or Path(n).name == "Online_Mind2Web.json" for n in names)
    info = json.loads((out / "stage.json").read_text())
    assert info["dataset"] == "alice/awos-web-eval-data" and info["kernel"] == "alice/awos-web-eval"
    assert info["tasks_in_file"] == 2 and MARKER not in json.dumps(info)


def test_stage_checks_model_files(tmp_path):
    k, w, e = envs(tmp_path)
    tj, md = fixtures(tmp_path)
    s = ST.load_settings(k, w, e)
    (md / "M-Q4_K_M.gguf").write_bytes(b"short")
    with pytest.raises(SystemExit, match="bytes"):
        ST.stage(tmp_path / "st", tj, md, s, "alice")
    (md / "M-Q4_K_M.gguf").unlink()
    with pytest.raises(SystemExit, match="not in"):
        ST.stage(tmp_path / "st", tj, md, s, "alice")


def test_staged_folder_is_what_kaggle_driver_pushes(tmp_path, capsys):
    from scaffold.rl.train import kaggle_driver as kd
    k, w, e = envs(tmp_path)
    tj, md = fixtures(tmp_path)
    ST.stage(tmp_path / "st", tj, md, ST.load_settings(k, w, e), "alice")
    res = kd.push(tmp_path / "st", dry=True)
    out = capsys.readouterr().out
    assert res == {"dataset": "alice/awos-web-eval-data", "kernel": "alice/awos-web-eval", "dry_run": True}
    assert f"kaggle datasets create -p {tmp_path / 'st' / 'dataset'}" in out
    assert f"kaggle kernels push -p {tmp_path / 'st' / 'kernel'}" in out and MARKER not in out


def test_collect_drops_any_task_file_and_lists_model_folders(tmp_path):
    out = tmp_path / "ko"
    (out / "m-hf__web-dev" / "judge" / "t1").mkdir(parents=True)
    (out / "m-hf__web-dev" / "judge" / "t1" / "result.json").write_text("{}")
    (out / "m-rel__web-dev" / "judge").mkdir(parents=True)
    (out / "llama-cuda-b1-sm75.tar.gz").write_bytes(b"")
    (out / "Online_Mind2Web.json").write_text(MARKER)
    dirs = ST.collect("alice/awos-web-eval", out, dry=True)
    assert [d.name for d in dirs] == ["m-hf__web-dev", "m-rel__web-dev"]
    assert not list(out.rglob("Online_Mind2Web.json"))


# ------------------------------------------------------------- kernel entry --

def test_one_llama_server_per_gpu_with_the_ci_flags():
    cfg = dict(RUN.DEFAULTS, workers=4, gpus=2, slots_per_server=2, ctx_per_slot=12288, base_port=8080)
    plan = RUN.server_plan(cfg, "/opt/llama/llama-server", "/in/M.gguf", "qwen3.5-4b")
    assert [(g, port, env["CUDA_VISIBLE_DEVICES"]) for g, port, _, env in plan] == [(0, 8080, "0"), (1, 8081, "1")]
    for g, port, cmd, env in plan:
        assert cmd[:3] == ["/opt/llama/llama-server", "-m", "/in/M.gguf"]
        s = " ".join(cmd)
        assert "--host 127.0.0.1" in s and f"--port {port}" in s and "--alias qwen3.5-4b" in s
        assert "--jinja --reasoning off" in s and "--temp 0" in s and "--cache-prompt" in s
        assert "-ngl 99" in s and "-c 24576 -np 2" in s
        assert env["LD_LIBRARY_PATH"].startswith("/opt/llama:")
    assert RUN.slots(dict(cfg, slots_per_server=0, workers=3)) == 2


def test_harness_invocation_spreads_workers_over_the_servers(tmp_path):
    cfg = dict(RUN.DEFAULTS, split="dev", workers=4, max_steps=25, max_seconds=900)
    cmd = RUN.harness_cmd(cfg, "a-final", "/in/Online_Mind2Web.json", tmp_path / "a-final__web-dev", [8080, 8081])
    assert cmd[1:4] == ["-m", "scaffold.rl.web", "run"]
    s = " ".join(cmd)
    assert "--model local --model-label a-final --split dev --max-steps 25 --max-seconds 900 --workers 4" in s
    assert "--tasks-json /in/Online_Mind2Web.json" in s and f"--out {tmp_path / 'a-final__web-dev'}/" in s
    assert s.endswith("--base-url http://127.0.0.1:8080/v1 --base-url http://127.0.0.1:8081/v1")
    assert "--n " not in s                                       # all tasks by default
    smoke = " ".join(RUN.harness_cmd(dict(cfg, n_tasks=8), "a-final", "/in/t.json", tmp_path / "x", [8080]))
    assert " --n 8 " in smoke                                    # N_TASKS: a smoke run before the full one
    from scaffold.rl.web.runner import main as harness_main   # the CLI accepts exactly these options
    with pytest.raises(SystemExit) as ex:
        harness_main(cmd[3:] + ["--help"])
    assert ex.value.code == 0


def test_llama_cuda_sources():
    cfg = dict(RUN.DEFAULTS, llama_tag="b11146")
    assert RUN.prebuilt_assets(cfg) == [
        "https://github.com/ggml-org/llama.cpp/releases/download/b11146/llama-b11146-bin-ubuntu-cuda-12.8-x64.tar.gz",
        "https://github.com/ggml-org/llama.cpp/releases/download/b11146/"
        "cudart-llama-b11146-bin-ubuntu-cuda-12.8-x64.tar.gz"]
    clone, conf, build = RUN.build_cmds(cfg, Path("/k/tmp"))
    assert clone[:5] == ["git", "clone", "--depth", "1", "--branch"] and clone[5] == "b11146"
    assert "-DGGML_CUDA=ON" in conf and "-DCMAKE_CUDA_ARCHITECTURES=75" in conf
    assert build[:2] == ["cmake", "--build"] and "llama-server" in build
    assert RUN.build_name(cfg) == "llama-cuda-b11146-sm75.tar.gz"


def test_ntfy_topic_is_the_rl_sft_one_and_pings_carry_counts_only():
    train = (ROOT / "kaggle" / "rl-sft" / "train.py").read_text()
    assert RUN.DEFAULTS["ntfy_topic"] == re.search(r'"ntfy_topic": "([^"]*)"', train).group(1)
    line = "[7/50] abc123 BLOCKED:cloudflare outcome=blocked stop_by=blocked steps=0 4.2s"
    assert RUN.parse_progress(line) == {"i": 7, "total": 50, "tag": "BLOCKED:cloudflare", "seconds": 4.2}
    assert RUN.parse_progress("[8/50] x ERR outcome=None stop_by=None steps=0 None error=boom 12s")["seconds"] == 0
    assert RUN.parse_progress("100 tasks: 100 to run") is None
    st = {"done": 10, "total": 100, "answered": 6, "blocked": 2, "errors": 0, "task_s": 900.0}
    assert RUN.progress_line("qwen3.5-4b", st, 0).startswith("qwen3.5-4b: 10/100 tasks | answered 6 blocked 2")


def test_kernel_dry_run_prints_commands_and_never_the_task_file(tmp_path, capsys):
    k, w, e = envs(tmp_path)
    tj, md = fixtures(tmp_path)
    ST.stage(tmp_path / "st", tj, md, ST.load_settings(k, w, e), "alice")
    out = tmp_path / "out"
    assert RUN.main(["--src", str(ROOT), "--inputs", str(tmp_path / "st" / "dataset"), "--out", str(out),
                     "--dry-run"]) == 0
    text = capsys.readouterr().out
    assert text.count("CUDA_VISIBLE_DEVICES=0") == 2 and text.count("CUDA_VISIBLE_DEVICES=1") == 2
    assert text.count("-m scaffold.rl.web run --model local") == 2 and "m-rel__web-dev/" in text
    assert MARKER not in text and not list(out.rglob("Online_Mind2Web.json"))


# ------------------------------------------------------- harness --base-url --

def test_worker_base_url_round_robin(monkeypatch):
    import multiprocessing as mp
    from scaffold.rl.harness import chromium
    from scaffold.rl.web import runner
    assert [runner.worker_base_url(["a", "b"], i) for i in range(5)] == ["a", "b", "a", "b", "a"]
    monkeypatch.setattr(chromium, "Chromium", lambda web=False: object())
    monkeypatch.setenv("AWOS_LOCAL_BASE_URL", "http://127.0.0.1:9999/v1")
    counter = mp.get_context("spawn").Value("i", 0)
    args = {"base_urls": ["http://127.0.0.1:8080/v1", "http://127.0.0.1:8081/v1"]}
    seen = []
    for _ in range(3):
        runner._worker_init(args, counter)
        seen.append(runner.os.environ["AWOS_LOCAL_BASE_URL"])
    assert seen == ["http://127.0.0.1:8080/v1", "http://127.0.0.1:8081/v1", "http://127.0.0.1:8080/v1"]
    runner._worker_init({"base_urls": []})                      # no --base-url: the env is left alone
    assert runner.os.environ["AWOS_LOCAL_BASE_URL"] == "http://127.0.0.1:8080/v1"
    runner._W.clear()


# --------------------------------------------------------- task file / CI --

def test_task_file_is_never_in_the_repo():
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git not available")
    assert not [p for p in tracked.splitlines() if Path(p).name.lower() == "online_mind2web.json"]
    assert "Online_Mind2Web.json" in ST.TASK_FILE == RUN.TASK_FILE


def test_workflow_wiring():
    wf = (ROOT / ".github" / "workflows" / "rl-web-kaggle.yml").read_text()
    assert '"bench-cloud/rl-web-kaggle-**"' in wf
    for s in ("secrets.HF_TOKEN", "secrets.KAGGLE_USERNAME", "secrets.KAGGLE_KEY", "secrets.KAGGLE_API_TOKEN"):
        assert s in wf
    assert "kaggle/web-eval/stage.py stage" in wf and "kaggle_driver import push" in wf
    assert "kaggle_driver wait" in wf and "kaggle/web-eval/stage.py collect" in wf
    assert "sha256sum -c -" in wf and "rl-web-merged-$model-$split" in wf
    assert not re.search(r"(cat|head|tail|less)\s+\S*Online_Mind2Web\.json", wf)     # never printed
    assert not re.search(r"echo\s+\S*\$\{?(HF_TOKEN|KAGGLE_KEY|KAGGLE_API_TOKEN)", wf)
    web = (ROOT / ".github" / "workflows" / "rl-web.yml").read_text()
    assert '"!bench-cloud/rl-web-kaggle-**"' in web and '"!bench-cloud/rl-web-judge-**"' in web
    ladder = (ROOT / ".github" / "workflows" / "ladder.yml").read_text()
    assert '"!bench-cloud/rl-**"' in ladder                  # Kaggle launches never start the paid ladder
    gi = subprocess.run(["git", "check-ignore", "-q", "Online_Mind2Web.json"], cwd=ROOT).returncode
    assert gi == 0, "Online_Mind2Web.json must be git-ignored"


# ---------------------------------------- two fake llama-servers, real CLI --

WH = _load("awos_test_web_harness_site", Path(__file__).resolve().parent / "test_web_harness.py")


def _serve(handler):
    import threading
    srv = WH.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _fake_llama(hits: list):
    class H(WH.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            time.sleep(0.5)                                   # slow enough that both workers get tasks
            hits.append(self.server.server_address[1])
            raw = json.dumps({"choices": [{"message": {"content": 'done "ok"'}}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 3}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass
    return _serve(H)


@pytest.mark.timeout(240)
@pytest.mark.skipif(not WH._chromium_ok(), reason="Playwright / Chromium build missing")
def test_two_workers_each_use_their_own_server(tmp_path):
    import os
    site_srv = _serve(WH._H)
    site = f"http://127.0.0.1:{site_srv.server_address[1]}"
    hits: list = []
    llamas = [_fake_llama(hits), _fake_llama(hits)]
    ports = [s.server_address[1] for s in llamas]
    tj = WH.write_tasks(tmp_path, [{"task_id": f"k{i}", "confirmed_task": "Find the shop name", "website": site + "/",
                                    "level": "easy"} for i in range(6)])
    out = tmp_path / "run"
    cmd = [sys.executable, "-m", "scaffold.rl.web", "run", "--model", "local", "--split", "all", "--tasks-json",
           str(tj), "--allow-private", "--workers", "2", "--max-steps", "3", "--out", str(out)]
    for p in ports:
        cmd += ["--base-url", f"http://127.0.0.1:{p}/v1"]
    env = {k: v for k, v in os.environ.items() if k != "AWOS_LOCAL_BASE_URL"}
    try:
        r = subprocess.run(cmd, cwd=ROOT, env=dict(env, PYTHONPATH=str(ROOT)), capture_output=True, text=True,
                           timeout=200)
        assert r.returncode == 0, r.stderr[-2000:]
        rows = [json.loads(x) for x in (out / "trajectories.jsonl").read_text().splitlines()]
        assert sorted(x["task"]["id"] for x in rows) == [f"k{i}" for i in range(6)]
        assert not [x for x in rows if x["trajectory"].get("error")], rows[0]["trajectory"].get("error")
        assert set(hits) == set(ports), hits                  # both servers served model steps
    finally:
        site_srv.shutdown()
        for s in llamas:
            s.shutdown()


def test_locate_src_accepts_the_archive_or_kaggles_unpacked_folder(tmp_path):
    """Kaggle unpacks web_eval_src.tar.gz into web_eval_src/ (2026-10-25 smoke): both forms must work."""
    import tarfile
    inp = tmp_path / "input" / "datasets" / "me" / "awos-web-eval-data"
    (inp / "web_eval_src" / "scaffold").mkdir(parents=True)          # what Kaggle mounts
    assert RUN.locate_src(tmp_path / "input", tmp_path / "tmp") == inp / "web_eval_src"
    inp2 = tmp_path / "input2" / "awos-web-eval-data"
    (tmp_path / "pkg" / "scaffold").mkdir(parents=True)
    (tmp_path / "pkg" / "scaffold" / "__init__.py").write_text("")
    inp2.mkdir(parents=True)
    with tarfile.open(inp2 / "web_eval_src.tar.gz", "w:gz") as tf:
        tf.add(tmp_path / "pkg" / "scaffold", arcname="scaffold")
    got = RUN.locate_src(tmp_path / "input2", tmp_path / "tmp2")
    assert (got / "scaffold" / "__init__.py").exists()
    with pytest.raises(SystemExit, match="not found"):
        RUN.locate_src(tmp_path / "nothing", tmp_path / "tmp3")


def test_servers_share_a_large_cuda_jit_cache_and_start_one_at_a_time(tmp_path, monkeypatch):
    """2026-10-25 M0: two servers JIT-compiling the prebuilt PTX at once were not healthy after 900 s."""
    plan = RUN.server_plan(dict(RUN.DEFAULTS, gpus=2), "/x/llama-server", "/m.gguf", "m")
    caches = {e["CUDA_CACHE_PATH"] for _g, _p, _c, e in plan}
    assert len(caches) == 1 and all(int(e["CUDA_CACHE_MAXSIZE"]) >= 2 ** 31 for _g, _p, _c, e in plan)
    order = []

    class P:
        def poll(self):
            return None

    def fake_popen(cmd, **kw):
        order.append(("start", kw["env"]["CUDA_VISIBLE_DEVICES"]))
        return P()
    monkeypatch.setattr(RUN.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(RUN, "wait_health", lambda port, timeout, p: order.append(("healthy", port, timeout)))
    monkeypatch.setattr(RUN, "smoke", lambda port, alias: 0.1)
    RUN.start_servers(dict(RUN.DEFAULTS, gpus=2), tmp_path / "llama-server", tmp_path / "m.gguf", "m", tmp_path)
    assert [o[0] for o in order] == ["start", "healthy", "start", "healthy"]
    assert order[1][2] == RUN.DEFAULTS["first_server_timeout_s"] > order[3][2] == RUN.DEFAULTS["server_start_timeout_s"]
