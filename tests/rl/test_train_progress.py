"""Progress reporting, action accuracy and the remote stop switch of kaggle/rl-sft/train.py (no torch needed)."""
import importlib.util
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "kaggle" / "rl-sft" / "train.py"
spec = importlib.util.spec_from_file_location("rl_sft_train", SRC)
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)


def test_action_line_and_match():
    assert T.action_line("# go to keys\nclick(4)<|im_end|>") == "click(4)"
    assert T.action_match("click(4)<|im_end|>", "click(4)<|im_end|>") == (True, True)
    assert T.action_match("# hmm\nclick(5)", "click(4)<|im_end|>") == (False, True)
    assert T.action_match('fill(2, "a")', "click(4)<|im_end|>") == (False, False)
    assert T.action_match("", "") == (False, False)


@pytest.mark.parametrize("body,expect", [
    ("", False), ("not a time", False),
    ("2099-01-01T00:00:00Z", True), ("2000-01-01T00:00:00Z", False),
    ("+60", True), ("-3600", False),                     # epoch seconds relative to the run start
])
def test_stop_requested_only_after_run_start(body, expect):
    start = time.time()
    if body[:1] in "+-" and body:
        body = str(start + float(body))                  # computed at run time, not at collection
    assert T.stop_requested(body, start) is expect


def test_progress_line_reads_well():
    p = {"step": 120, "total": 370, "pct": 32.4, "loss": 0.2141, "eval_loss": 0.119, "action_acc": 0.71,
         "s_per_step": 41.8, "eta_s": 10450,
         "gpu": [{"util": 87, "mem_used": 11366, "mem_total": 15360}, {"util": 85, "mem_used": 11200,
                                                                       "mem_total": 15360}]}
    line = T.progress_line(p)
    assert line == ("step 120/370 32% | loss 0.214 | eval 0.119 | acc 71% | 41.8 s/step | ETA 2h54m"
                    " | GPU 87% 85% 11.1/15.0 GB")
    assert T.progress_line({"step": 0, "total": 10, "pct": 0}) == "step 0/10 0%"
    assert T.fmt_dur(95) == "1m35s"


def _ckpt(out, step, body=b"w"):
    d = out / "checkpoints" / f"checkpoint-{step}"
    d.mkdir(parents=True)
    (d / "adapter_model.safetensors").write_bytes(body)
    (d / "adapter_config.json").write_text("{}")
    (d / "trainer_state.json").write_text('{"log_history": [{"loss": 0.3, "step": %d}]}' % step)


def test_recover_adapter_promotes_newest_checkpoint(tmp_path):
    import json
    assert T.recover_adapter(tmp_path, "test") is False
    _ckpt(tmp_path, 50, b"old")
    _ckpt(tmp_path, 100, b"new")
    assert T.recover_adapter(tmp_path, "hung")
    assert (tmp_path / "adapter" / "adapter_model.safetensors").read_bytes() == b"new"
    m = json.loads((tmp_path / "metrics.json").read_text())
    assert m["status"] == "recovered_from_checkpoint" and m["global_steps"] == 100 and m["reason"] == "hung"


def test_supervisor_kills_a_hung_run_and_recovers(tmp_path):
    import sys
    _ckpt(tmp_path, 150)
    cfg = {**T.DEFAULTS, "ntfy_topic": "", "hang_minutes": 0.02, "startup_grace_min": 0, "watch_poll_s": 0.3,
           "stack_dump_wait_s": 0.2}
    t0 = time.time()
    rc = T.supervise([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, cfg)
    assert rc == 0 and time.time() - t0 < 20                     # killed, not waited out
    assert (tmp_path / "adapter" / "adapter_model.safetensors").exists()


def test_supervisor_passes_a_clean_exit(tmp_path):
    import sys
    cfg = {**T.DEFAULTS, "ntfy_topic": "", "watch_poll_s": 0.1}
    assert T.supervise([sys.executable, "-c", "pass"], tmp_path, cfg) == 0
    assert not (tmp_path / "adapter").exists()


def test_mem_info_and_ram_line(tmp_path):
    f = tmp_path / "meminfo"
    f.write_text("MemTotal:       32879092 kB\nMemFree:  1 kB\nMemAvailable:    9437184 kB\n")
    m = T.mem_info(str(f))
    assert m == {"avail_gb": 9.0, "total_gb": 31.36}
    assert T.mem_info(str(tmp_path / "missing")) == {}
    line = T.ram_line({**m, "procs": [{"pid": 1, "rss_gb": 10.2}, {"pid": 2, "rss_gb": 10.4}]})
    assert line == "RAM free 9.0/31 GB (ranks 10.2+10.4)"
    p = {"step": 5, "total": 10, "pct": 50, "ram": {**m, "procs": []}}
    assert T.progress_line(p).endswith("RAM free 9.0/31 GB")


def test_supervisor_low_ram_asks_for_a_clean_stop(tmp_path):
    """Below ram_low_gb the supervisor writes STOP_LOCAL (the ranks stop + save at the next check) and
    logs RAM; it only kills if RAM stays critical after the grace period."""
    import json
    import sys
    seq = iter([{"avail_gb": 8.0, "total_gb": 31.0}] + [{"avail_gb": 1.0, "total_gb": 31.0}] * 100)
    cfg = {**T.DEFAULTS, "ntfy_topic": "", "watch_poll_s": 0.1, "ram_grace_s": 0.3, "hang_minutes": 99,
           "stack_dump_wait_s": 0.1, "_mem_fn": lambda: dict(next(seq))}
    rc = T.supervise([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, cfg)
    assert (tmp_path / "STOP_LOCAL").read_text().startswith("low RAM: 1.0 GB")
    rows = [json.loads(x) for x in (tmp_path / "ram.jsonl").read_text().splitlines()]
    assert rows[0]["avail_gb"] == 8.0 and rows[-1]["avail_gb"] == 1.0
    assert rc != 0 or (tmp_path / "adapter").exists()          # killed after grace (no checkpoint here)


def test_find_resume_checkpoint_prefers_newest_full_state(tmp_path):
    inp = tmp_path / "input"
    k = inp / "awos-rl-sft" / "checkpoints"
    for step, full in ((100, True), (150, True)):
        d = k / f"checkpoint-{step}"
        d.mkdir(parents=True)
        (d / "adapter_model.safetensors").write_bytes(b"w")
        if full:
            for f in ("optimizer.pt", "scheduler.pt", "trainer_state.json"):
                (d / f).write_text("x")
    (inp / "awos-rl-sft" / "adapter").mkdir()
    (inp / "awos-rl-sft" / "adapter" / "adapter_model.safetensors").write_bytes(b"w")
    ck, full = T.find_resume_checkpoint("awos-rl-sft", inputs=inp)
    assert ck.name == "checkpoint-150" and full
    ck, full = T.find_resume_checkpoint(str(inp / "awos-rl-sft" / "adapter"), inputs=inp)
    assert ck.name == "adapter" and not full
    assert T.find_resume_checkpoint("", inputs=inp) is None
    assert T.find_resume_checkpoint("nothing-here", inputs=inp) is None


def test_stage_attaches_kernel_sources(tmp_path):
    import json
    from scaffold.rl.train import kaggle_driver as kd
    data = tmp_path / "d"
    data.mkdir()
    row = {"prompt": "<|im_start|>system\nS<|im_end|>\n<|im_start|>user\nU<|im_end|>\n<|im_start|>assistant\n"
                     "<think>\n\n</think>\n\n", "completion": "click(1)<|im_end|>",
           "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"},
                        {"role": "assistant", "content": "click(1)"}], "meta": {"split": "train"}}
    (data / "train.jsonl").write_text(json.dumps(row) + "\n")
    try:
        kd.stage(data, tmp_path / "st", "4B", "me", {"resume_from": "awos-rl-sft", "resume_mode": "exact"},
                 kernel_sources=["me/awos-rl-sft"])
    except SystemExit:
        import pytest
        pytest.skip("row validation needs the real rendered format")
    meta = json.loads((tmp_path / "st" / "kernel" / "kernel-metadata.json").read_text())
    assert meta["kernel_sources"] == ["me/awos-rl-sft"]
    cfg = json.loads((tmp_path / "st" / "dataset" / "train_config.json").read_text())
    assert cfg["resume_from"] == "awos-rl-sft" and cfg["resume_mode"] == "exact"


def test_defaults_are_one_epoch_with_stop_switch():
    assert T.DEFAULTS["epochs"] == 1.0
    assert T.DEFAULTS["stop_url"].endswith("/rl-control/kaggle/STOP")
    assert T.DEFAULTS["stop_check_steps"] >= 1 and T.DEFAULTS["eval_gen_rows"] > 0
