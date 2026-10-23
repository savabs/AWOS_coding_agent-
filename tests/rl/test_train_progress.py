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
    (str(time.time() + 60), True), (str(time.time() - 3600), False),
])
def test_stop_requested_only_after_run_start(body, expect):
    assert T.stop_requested(body, time.time()) is expect


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


def test_defaults_are_one_epoch_with_stop_switch():
    assert T.DEFAULTS["epochs"] == 1.0
    assert T.DEFAULTS["stop_url"].endswith("/rl-control/kaggle/STOP")
    assert T.DEFAULTS["stop_check_steps"] >= 1 and T.DEFAULTS["eval_gen_rows"] > 0
