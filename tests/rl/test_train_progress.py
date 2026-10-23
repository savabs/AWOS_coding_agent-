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


def test_defaults_are_one_epoch_with_stop_switch():
    assert T.DEFAULTS["epochs"] == 1.0
    assert T.DEFAULTS["stop_url"].endswith("/rl-control/kaggle/STOP")
    assert T.DEFAULTS["stop_check_steps"] >= 1 and T.DEFAULTS["eval_gen_rows"] > 0
