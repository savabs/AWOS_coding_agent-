"""WebJudge port (scaffold/rl/web_judge) with fake backends: no network, no paid call, no Pillow needed.

    PYTHONPATH=.:tests ~/.awos/rl-venv/bin/python -m pytest tests/rl/test_web_judge.py -q
"""
from __future__ import annotations

import base64
import hashlib
import json
import struct
import sys
import threading
import zlib
from pathlib import Path

import pytest

from scaffold.rl.web_judge import cost as C
from scaffold.rl.web_judge import judge as J
from scaffold.rl.web_judge import prompts as P
from scaffold.rl.web_judge import stats as S
from scaffold.rl.web_judge.backends import BackendError, HTTPStatusError, OpenAICompatBackend
from scaffold.rl.web_judge.layout import LayoutError, find_episodes, image_size, load_episode, load_levels

REPO = Path(__file__).resolve().parents[2]

# sha256 of the upstream strings (src/methods/webjudge_online_mind2web.py @ f0d805ee)
UPSTREAM_SHA256 = {
    "KEY_POINTS_SYSTEM": "f59402219cc586b00584fea7263a4fa93f71bf3e4d6fd43ae66f5430844cd51d",
    "KEY_POINTS_PROMPT": "0a0cf959a858a9eb295675230f7826d4c759d28b40b9d7ddc3776c7c936057ab",
    "IMAGE_SYSTEM": "0c15bc23bcadf384e37763aaccf601291df6c0cdab8ec763b023b7c34470dda4",
    "IMAGE_PROMPT": "75cc58c21896a74c2a26a72792ea68e91e2df31239d8e02c79b3977c5f773c9e",
    "OUTCOME_SYSTEM": "ff67fd2ed338ab1aa571ec13efaa17ce2a459857e92a11f04b4217979a5484af",
    "OUTCOME_PROMPT": "0002edd5e7fffc9313703e3def89401534fc77ccc82700a6866c25ccb2425171",
    "SCORE_PATTERN": "adee09506978a60965fc667cf5c1ffc4134d3502bca19fe02206449423b7d2fb",
    "OUTCOME_PROMPT_NO_IMAGES": "7ab5f230283267b7426b9bf8880aa5be6536cb192856d8eff72b005b4ca5bb65",
}


def png(w=1280, h=720) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def fake_encode(path: Path) -> str:
    """Stands in for Pillow's JPEG re-encode: base64 of the file bytes, which keeps the image size readable."""
    return base64.b64encode(path.read_bytes()).decode()


def v1_task(root: Path, tid="t1", task="Find a red shirt under $20", n=3, level=None, status=None,
            names=None, extra=None) -> Path:
    d = root / tid
    (d / "trajectory").mkdir(parents=True)
    names = names or [f"{i}_full_screenshot.png" for i in range(n)]
    for nm in names:
        (d / "trajectory" / nm).write_bytes(png(64, 48))
    r = {"task_id": tid, "task": task, "final_result_response": "done",
         "action_history": [f"<button> -> CLICK {i}" for i in range(len(names))], "thoughts": ["x"] * len(names)}
    if level:
        r["level"] = level
    if status:
        r["harness_status"] = status
    r.update(extra or {})
    (d / "result.json").write_text(json.dumps(r))
    return d


class FakeBackend:
    """Answers per purpose; image scores come from a list cycled per call (thread-safe)."""

    def __init__(self, scores=(4, 1, 5), outcome="Thoughts: ok\nStatus: \"success\"", kp="**Key Points**:\n1. a\n\n2. b",
                 fail_on=None):
        self.scores, self.outcome, self.kp, self.fail_on = list(scores), outcome, kp, fail_on
        self.calls, self.lock, self.seen = [], threading.Lock(), []
        self.name = "fake"

    def generate(self, messages, purpose=""):
        with self.lock:
            self.calls.append(purpose)
            self.seen.append(messages)
            if self.fail_on == purpose:
                raise BackendError("boom")
            if purpose == "key_points":
                return self.kp
            if purpose == "image":
                text = messages[1]["content"][0]["text"]
                i = sum(1 for p in self.calls if p == "image") - 1
                s = self.scores[i % len(self.scores)]
                return f"1. **Reasoning**: shot {i} for {text[:10]}\nmore\n\n2. **Score**: {s}"
            return self.outcome


# ---------------------------------------------------------------- prompts & parsing (upstream rules)

def test_prompts_are_upstream_verbatim():
    for name, sha in UPSTREAM_SHA256.items():
        assert hashlib.sha256(getattr(P, name).encode()).hexdigest() == sha, name


def test_parse_key_points_both_markers():
    assert J.parse_key_points("**Key Points**:\n1. Filter by cheapest\n\n   2. Size M") == "\n1. Filter by cheapest\n2. Size M"
    assert J.parse_key_points("intro Key Points: 1. a\n  2. b") == "1. a\n2. b"
    assert J.parse_key_points("no marker\n  x") == "no marker\nx"      # split()[-1] = whole text


def test_parse_image_response_rules():
    assert J.parse_image_response("1. **Reasoning**: sees filter\nline2\n\nrest\n2. **Score**: 4") == (4, "sees filter line2")
    # o4-mini style without bold: reasoning = first paragraph of the whole response (upstream behaviour)
    s, t = J.parse_image_response("1. Reasoning: nothing here.\n\n2. Score: 1")
    assert s == 1 and t == "1. Reasoning: nothing here."
    assert J.parse_image_response("no score at all") == (0, None)
    assert J.parse_image_response("Score: 9 then 7") == (0, None)        # no [1-5] digit -> 0
    assert J.parse_image_response("Score: 0, really 3")[0] == 3           # first [1-5] after "Score"


def test_extract_predication():
    assert J.extract_predication('Thoughts: fine\nStatus: "success"') == 1
    assert J.extract_predication('Thoughts: no\nStatus: "failure"') == 0
    assert J.extract_predication("Thoughts: success everywhere but no status line") == 0
    assert J.extract_predication("STATUS: Success") == 1
    assert J.extract_predication("") == 0


def test_select_key_screenshots_threshold_cap_and_empty_reason():
    resp = ["**Reasoning**: a\n**Score**: 3", "**Score**: 2", "garbage", "**Score**: 5 **Reasoning**:"]
    record, keys, reasons = J.select_key_screenshots(resp, 3)
    assert [r["Score"] for r in record] == [3, 2, 0, 5]
    assert keys == [0, 3]
    assert reasons == ["a **Score**: 3"]              # the empty reason of #3 is dropped, its image kept
    many = ["**Reasoning**: r\n\n**Score**: 4"] * 60
    _, keys, reasons = J.select_key_screenshots(many, 3)
    assert len(keys) == 50 and len(reasons) == 50


def test_outcome_prompt_with_and_without_images():
    m, text = J.outcome_messages("T", ["a1", "a2"], "\n1. k", ["QUJD"], ["why"])
    assert text == P.OUTCOME_PROMPT.format(task="T", key_points="\n1. k", last_actions="1. a1\n2. a2", thoughts="1. why")
    assert m[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")   # upstream label
    m, text = J.outcome_messages("T", ["a1"], "k", [], ["ignored"])
    assert "potentially important" not in text and len(m[1]["content"]) == 1


# ---------------------------------------------------------------- layout

def test_layout_v1_order_and_fields(tmp_path):
    v1_task(tmp_path, names=["10_x.png", "2_x.png", "1_x.png", "notes.txt"], level="Hard")
    (tmp_path / "t1" / "trajectory" / ".DS_Store").write_bytes(b"x")
    [ep] = find_episodes(tmp_path)
    assert [p.name for p in ep.screenshots] == ["1_x.png", "2_x.png", "10_x.png"]
    assert ep.schema == "v1" and ep.level == "hard" and ep.harness_status == "ok" and ep.judgeable
    assert ep.final_result_response == "done" and len(ep.action_history) == 4


def test_layout_v1_unnumbered_screenshot_is_error(tmp_path):
    v1_task(tmp_path, names=["a.png"])
    with pytest.raises(LayoutError):
        find_episodes(tmp_path)


def test_layout_v2_uses_step_screenshots(tmp_path):
    d = tmp_path / "art" / "abc"
    (d / "trajectory").mkdir(parents=True)
    for n in ("0000.png", "0001.png", "9999.png"):
        (d / "trajectory" / n).write_bytes(png(10, 10))
    steps = [{"step": 0, "screenshot": "0001.png", "action": "page -> NAVIGATE -> x | SUCCESS", "thought": None},
             {"step": 1, "screenshot": "0000.png", "action": "CLICK coords(1, 2) -> y | SUCCESS", "thought": "t"},
             {"step": 2, "screenshot": None, "action": "TASK_COMPLETE -> ANSWER: z", "thought": None}]
    (d / "result.json").write_text(json.dumps({"schema_version": "online-mind2web-v2", "task": "T", "task_id": "abc",
                                               "agent_final_answer": "z", "action_history": steps}))
    [ep] = find_episodes(tmp_path)
    assert ep.schema == "v2" and [p.name for p in ep.screenshots] == ["0001.png", "0000.png"]
    assert ep.action_history[-1] == "TASK_COMPLETE -> ANSWER: z" and ep.final_result_response == "z"


def test_layout_status_levels_duplicates(tmp_path):
    v1_task(tmp_path / "r", "a", status="captcha")
    v1_task(tmp_path / "r", "b", status="infra_failed", n=0)
    eps = {e.task_id: e for e in find_episodes(tmp_path / "r", levels={"a": "Easy"})}
    assert eps["a"].harness_status == "blocked" and not eps["a"].judgeable and eps["a"].level == "easy"
    assert eps["b"].harness_status == "infra_error" and eps["b"].screenshots == []
    v1_task(tmp_path / "bad", "c", status="weird")
    with pytest.raises(LayoutError):
        find_episodes(tmp_path / "bad")
    v1_task(tmp_path / "dup" / "s1", "x")
    v1_task(tmp_path / "dup" / "s2", "x")
    with pytest.raises(LayoutError, match="twice"):
        find_episodes(tmp_path / "dup")


def test_layout_missing_listed_screenshot(tmp_path):
    d = tmp_path / "q"
    (d / "trajectory").mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"schema_version": "online-mind2web-v2", "task": "T",
                                               "action_history": [{"step": 0, "screenshot": "0000.png", "action": "a"}]}))
    with pytest.raises(LayoutError, match="missing"):
        load_episode(d)


def test_load_levels_formats(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps([{"task_id": "x", "level": "Medium"}, {"task_id": "y"}]))
    (tmp_path / "b.json").write_text(json.dumps({"x": "HARD"}))
    assert load_levels(tmp_path / "a.json") == {"x": "medium"}
    assert load_levels(tmp_path / "b.json") == {"x": "hard"}


def test_image_size_png_and_jpeg(tmp_path):
    (tmp_path / "a.png").write_bytes(png(33, 17))
    assert image_size(tmp_path / "a.png") == (33, 17)
    # minimal JPEG header: SOI, APP0 (len 16), SOF0 with h=300, w=500
    jpg = (b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
           + b"\xff\xc0" + struct.pack(">HBHH", 11, 8, 300, 500) + b"\x03")
    (tmp_path / "b.jpg").write_bytes(jpg)
    assert image_size(tmp_path / "b.jpg") == (500, 300)


# ---------------------------------------------------------------- one episode / a run

def test_judge_episode_pipeline(tmp_path):
    v1_task(tmp_path, n=3)
    [ep] = find_episodes(tmp_path)
    fb = FakeBackend(scores=(4, 1, 5))
    out = J.judge_episode(ep, fb, encode=fake_encode, image_workers=1)
    assert fb.calls == ["key_points", "image", "image", "image", "outcome"]
    assert out["predicted_label"] == 1 and out["key_points"] == "\n1. a\n2. b"
    assert [r["Score"] for r in out["image_judge_record"]] == [4, 1, 5]
    assert out["key_screenshots"] == ["0_full_screenshot.png", "2_full_screenshot.png"]
    final = fb.seen[-1]
    assert len(final[1]["content"]) == 3                      # text + 2 key screenshots
    assert "1. <button> -> CLICK 0" in out["input_text"] and "Key Points: \n1. a\n2. b" in out["input_text"]
    img_msg = fb.seen[1]
    assert img_msg[1]["content"][1]["image_url"]["detail"] == "high"
    assert img_msg[1]["content"][0]["text"] == P.IMAGE_PROMPT.format(task=ep.task, key_points="\n1. a\n2. b")


def test_judge_run_statuses_and_resume(tmp_path):
    v1_task(tmp_path, "ok1", level="easy")
    v1_task(tmp_path, "blk", status="blocked", level="hard")
    v1_task(tmp_path, "inf", status="infra_error", n=0)
    eps = find_episodes(tmp_path)
    saved = []
    fb = FakeBackend(outcome="Thoughts: no\nStatus: failure")
    vs, info = J.judge_run(eps, fb, encode=fake_encode, workers=2, on_verdict=saved.append, log=lambda *a: None)
    st = {v["task_id"]: (v["status"], v["predicted_label"]) for v in vs}
    assert st == {"blk": ("blocked", 0), "inf": ("infra_error", 0), "ok1": ("judged", 0)}
    assert fb.calls.count("key_points") == 1 and info["stopped"] is None and len(saved) == 3
    fb2 = FakeBackend()
    vs2, _ = J.judge_run(eps, fb2, encode=fake_encode, done={v["task_id"]: v for v in saved}, log=lambda *a: None)
    assert fb2.calls == [] and [v["status"] for v in vs2] == [v["status"] for v in vs]
    fb3 = FakeBackend()
    vs3, _ = J.judge_run(eps, fb3, encode=fake_encode, judge_blocked=True, log=lambda *a: None)
    assert fb3.calls.count("key_points") == 3 and all(v["status"] == "judged" for v in vs3)


def test_judge_error_is_recorded_and_run_continues(tmp_path):
    v1_task(tmp_path, "a")
    v1_task(tmp_path, "b")
    vs, info = J.judge_run(find_episodes(tmp_path), FakeBackend(fail_on="outcome"), encode=fake_encode,
                           log=lambda *a: None)
    assert [v["status"] for v in vs] == ["judge_error", "judge_error"] and info["stopped"] is None


# ---------------------------------------------------------------- o4-mini backend: cost + cap (fake transport)

class FakeTransport:
    def __init__(self, usage=(2000, 300), fail=None):
        self.usage, self.fail, self.bodies, self.headers = usage, fail, [], []

    def __call__(self, url, body, headers, timeout):
        self.bodies.append(body)
        self.headers.append(headers)
        if self.fail:
            e = self.fail.pop(0) if isinstance(self.fail, list) else self.fail
            if e is not None:
                raise e
        system = body["messages"][0]["content"]
        content = {P.KEY_POINTS_SYSTEM: "**Key Points**:\n1. x",
                   P.IMAGE_SYSTEM: "**Reasoning**: r\n\n**Score**: 4"}.get(system, "Thoughts: t\nStatus: success")
        return {"choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": self.usage[0], "completion_tokens": self.usage[1],
                          "prompt_tokens_details": {"cached_tokens": 0}}}


def o4(monkeypatch, budget, transport, **kw):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    return OpenAICompatBackend("o4mini", budget=budget, transport=transport, sleep=lambda s: None, **kw)


def test_o4mini_request_shape_and_billing(monkeypatch, tmp_path):
    v1_task(tmp_path, n=2)
    [ep] = find_episodes(tmp_path)
    b, t = C.Budget(5.0), FakeTransport(usage=(2000, 300))
    out = J.judge_episode(ep, o4(monkeypatch, b, t, service_tier="flex"), encode=fake_encode)
    assert out["predicted_label"] == 1
    body = t.bodies[0]
    assert body["model"] == "o4-mini" and body["max_completion_tokens"] == 8192 and body["service_tier"] == "flex"
    assert "temperature" not in body and "max_tokens" not in body
    assert t.headers[0]["Authorization"] == "Bearer sk-test-not-real"
    assert b.calls == 4 and b.reserved == pytest.approx(0)
    assert b.spent == pytest.approx(4 * (2000 * 0.55 + 300 * 2.20) / 1e6)


def test_webjudge7b_request_is_upstream(monkeypatch):
    t = FakeTransport()
    be = OpenAICompatBackend("webjudge7b", base_url="http://h:1/v1", transport=t)
    be.generate(J.key_points_messages("T"), "key_points")
    assert t.bodies[0] == {"model": "osunlp/WebJudge-7B", "messages": J.key_points_messages("T"),
                           "max_tokens": 512, "temperature": 0}
    assert "Authorization" not in t.headers[0] or monkeypatch is None


def test_cap_stops_before_overspend_and_reports_partial(monkeypatch, tmp_path):
    for i in range(6):
        v1_task(tmp_path, f"t{i}", n=2)
    eps = find_episodes(tmp_path)
    t = FakeTransport(usage=(3000, 1500))
    be = o4(monkeypatch, None or C.Budget(0.08), t, max_output_tokens=2000)
    vs, info = J.judge_run(eps, be, encode=fake_encode, workers=1, log=lambda *a: None)
    b = be.budget
    assert b.spent <= 0.08 and info["stopped"].startswith("cap")
    st = [v["status"] for v in vs]
    assert "judged" in st and st[-1] == "not_judged" and "judge_error" not in st
    summ = S.summarize(vs)
    assert not summ["complete"] and summ["n_not_judged"] >= 1
    assert summ["official"]["n"] == summ["n_judged"]


def test_cap_reservation_is_worst_case():
    b = C.Budget(1.0)
    b.reserve(0.6)
    with pytest.raises(C.CapReached):
        b.reserve(0.5)
    b.settle(0.6, 0.1)
    b.reserve(0.5)
    assert b.spent == pytest.approx(0.1) and b.reserved == pytest.approx(0.5)


def test_fatal_and_retry_classification(monkeypatch):
    msgs = J.key_points_messages("T")
    t = FakeTransport(fail=HTTPStatusError(404, '{"error":{"code":"model_not_found"}}'))
    with pytest.raises(BackendError) as e:
        o4(monkeypatch, C.Budget(1.0), t).generate(msgs)
    assert e.value.fatal and "sk-test" not in str(e.value)
    t = FakeTransport(fail=[HTTPStatusError(503, "busy"), HTTPStatusError(429, "rate_limit"), None])
    assert o4(monkeypatch, C.Budget(1.0), t).generate(msgs).startswith("**Key Points**")
    assert len(t.bodies) == 3
    t = FakeTransport(fail=HTTPStatusError(500, "x"))
    with pytest.raises(BackendError) as e:
        o4(monkeypatch, C.Budget(1.0), t).generate(msgs)
    assert not e.value.fatal and len(t.bodies) == 3


def test_missing_key_is_fatal(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(BackendError) as e:
        OpenAICompatBackend("o4mini", budget=C.Budget(1.0))
    assert e.value.fatal


def test_fatal_stops_whole_run(monkeypatch, tmp_path):
    for i in range(3):
        v1_task(tmp_path, f"t{i}")
    t = FakeTransport(fail=HTTPStatusError(404, "model_not_found"))
    vs, info = J.judge_run(find_episodes(tmp_path), o4(monkeypatch, C.Budget(1.0), t), encode=fake_encode,
                           workers=1, log=lambda *a: None)
    assert info["fatal"] and all(v["status"] == "not_judged" for v in vs) and len(t.bodies) == 1


# ---------------------------------------------------------------- cost estimate

def test_image_tokens_patch_method():
    assert C.image_tokens(1280, 720) == 1583            # 40 x 23 = 920 patches x 1.72 -> ceil
    assert C.image_tokens(32, 32) == 2
    big = C.image_tokens(1280, 8000)                     # full-page shot shrinks to the 1536-patch budget
    assert big <= 1536 * 1.72 + 1 and big > 1400 * 1.72
    assert C.image_tokens(1280, 8000, patch_budget=0) == math_ceil(40 * 250 * 1.72)


def math_ceil(x):
    import math
    return math.ceil(x)


def test_usd_and_estimate():
    assert C.usd(C.prices_for("o4-mini"), 1_000_000, 1_000_000) == pytest.approx(5.50)
    assert C.usd(C.prices_for("o4-mini"), 1_000_000, 0, cached_tokens=1_000_000) == pytest.approx(0.275)
    e = C.estimate_episode("task", ["a", "b"], [(1280, 720)] * 10, max_output_tokens=8192)
    assert e.calls == 12 and e.images == 10 and e.worst_output_tokens == 12 * 8192
    assert e.expected_output_tokens == 500 + 10 * 700 + 1500
    assert 10 * 1583 < e.prompt_tokens < 10 * 1583 + 4 * 1583 + 10 * 700
    w = C.estimate_episode("task", ["a"], [(1280, 720)] * 10, worst_key_images=True)
    assert w.prompt_tokens > e.prompt_tokens


def test_message_tokens_reads_data_url_size():
    from scaffold.rl.web_judge.backends import data_url_tokens
    part = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(png(640, 480)).decode()}}
    assert data_url_tokens(part, 1536) == C.image_tokens(640, 480)


# ---------------------------------------------------------------- stats

def test_wilson_known_values():
    w = S.wilson(57, 100)
    assert w["rate"] == 0.57 and w["lo"] == pytest.approx(0.4722, abs=1e-4) and w["hi"] == pytest.approx(0.6627, abs=1e-4)
    assert S.wilson(0, 10)["lo"] == 0 and S.wilson(10, 10)["hi"] == pytest.approx(1) and S.wilson(0, 0) is None


def test_mcnemar_exact():
    assert S.mcnemar_exact(0, 0) == 1.0
    assert S.mcnemar_exact(0, 6) == pytest.approx(2 / 64)
    assert S.mcnemar_exact(2, 8) == pytest.approx(2 * (1 + 10 + 45) / 1024)
    assert S.mcnemar_exact(5, 5) == 1.0


def test_summary_and_compare():
    def v(t, s, l, lv="easy", h="ok"):
        return {"task_id": t, "status": s, "predicted_label": l, "level": lv, "harness_status": h}
    a = [v("1", "judged", 1), v("2", "judged", 0, "hard"), v("3", "blocked", 0, "hard", "blocked"),
         v("4", "judge_error", None), v("5", "judged", 1, None)]
    s = S.summarize(a)
    assert (s["n_tasks"], s["n_judged"], s["n_blocked"], s["n_judge_error"], s["complete"]) == (5, 3, 1, 1, False)
    assert s["official"]["k"] == 2 and s["official"]["n"] == 4
    assert s["executable"]["k"] == 2 and s["executable"]["n"] == 3
    assert s["by_level"]["hard"]["official"]["n"] == 2 and s["by_level"]["unknown"]["n_tasks"] == 1
    b = [v("1", "judged", 0), v("2", "judged", 1), v("3", "judged", 1), v("4", "judged", 1), v("6", "judged", 1)]
    c = S.compare(a, b, "A", "B")
    assert c["n_paired"] == 3 and c["a_only_success"] == 1 and c["b_only_success"] == 2
    assert c["only_in_a"] == 1 and c["only_in_b"] == 2
    assert c["mcnemar_exact_p"] == pytest.approx(1.0)


# ---------------------------------------------------------------- CLI (dry run + fake-backed run)

def load_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("web_judge_cli", REPO / "scripts" / "web" / "judge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_dry_run(tmp_path, capsys):
    v1_task(tmp_path / "A", "x", n=4, level="easy")
    v1_task(tmp_path / "A", "y", status="blocked")
    cli = load_cli()
    rc = cli.main(["--runs", str(tmp_path / "A"), "--backend", "dry", "--out", str(tmp_path / "r.json")])
    assert rc == 0
    est = json.loads((tmp_path / "r.json").read_text())["estimate"]
    assert est["total"]["episodes"] == 1 and est["total"]["calls"] == 6
    assert "per 300-task run" in capsys.readouterr().out


def test_cli_o4mini_with_fake_transport(tmp_path, monkeypatch, capsys):
    v1_task(tmp_path / "A", "x", level="easy")
    v1_task(tmp_path / "A", "y", level="hard")
    v1_task(tmp_path / "B", "x", level="easy")
    v1_task(tmp_path / "B", "y", status="infra_error", level="hard")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    cli = load_cli()
    t = FakeTransport()
    real = cli.OpenAICompatBackend
    monkeypatch.setattr(cli, "OpenAICompatBackend", lambda *a, **k: real(*a, transport=t, **k))
    monkeypatch.setattr(J, "encode_image", fake_encode)
    out = tmp_path / "res.json"
    rc = cli.main(["--runs", f"A={tmp_path / 'A'}", f"B={tmp_path / 'B'}", "--backend", "o4mini", "--out", str(out),
                   "--max-usd", "5", "--quiet"])
    assert rc == 0
    doc = json.loads(out.read_text())
    assert doc["runs"]["A"]["summary"]["official"]["k"] == 2
    assert doc["runs"]["B"]["summary"]["n_infra_error"] == 1
    assert doc["comparisons"][0]["n_paired"] == 2 and doc["cost"]["calls"] == 15
    assert "sk-test" not in out.read_text() and "sk-test" not in capsys.readouterr().out
    n = len(t.bodies)
    assert cli.main(["--runs", f"A={tmp_path / 'A'}", f"B={tmp_path / 'B'}", "--backend", "o4mini", "--out", str(out),
                     "--quiet"]) == 0
    assert len(t.bodies) == n                           # resumed from res.json.verdicts.jsonl


# ---------------------------------------------------------------- Kaggle kernel plumbing (no Kaggle, no GPU)

def load_file(path: Path, name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_kaggle_stage_and_kernel_dry_run(tmp_path, capsys):
    v1_task(tmp_path / "A", "x")
    v1_task(tmp_path / "A", "y", status="blocked")
    stage = load_file(REPO / "kaggle" / "web-judge" / "stage.py", "wj_stage")
    info = stage.stage(tmp_path / "st", [("A", tmp_path / "A")], "alice", ["max_model_len=32768"])
    ds, kd = tmp_path / "st" / "dataset", tmp_path / "st" / "kernel"
    assert info["episodes"] == {"A": 2}
    assert json.loads((ds / "judge_config.json").read_text())["max_model_len"] == 32768
    meta = json.loads((kd / "kernel-metadata.json").read_text())
    assert meta["id"] == "alice/awos-web-judge" and meta["dataset_sources"] == ["alice/awos-web-judge-data"]
    assert meta["enable_gpu"] and meta["code_file"] == "run.py"
    run = load_file(kd / "run.py", "wj_run")
    src = run.extract(ds / "web_judge_src.tar.gz", tmp_path / "src")
    assert (src / "scaffold" / "rl" / "web_judge" / "judge.py").exists() and (src / "scripts" / "web" / "judge.py").exists()
    rc = run.main(["--repo", str(src), "--runs-dir", str(ds / "runs"), "--config", str(ds / "judge_config.json"),
                   "--out", str(tmp_path / "o"), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0 and "--backend webjudge7b" in out and "A=" in out
    assert '--limit-mm-per-prompt {"image": 30}' in out and "--revision bb550f8e" in out and "--dtype half" in out
    assert '--mm-processor-kwargs {"max_pixels": 1003520}' in out and "--enforce-eager" in out   # T4 sizing (NOTICE 7)
    runs = run.collect_runs(ds / "runs", tmp_path / "t")
    assert [lbl for lbl, _ in runs] == ["A"] and len(find_episodes(runs[0][1])) == 2
    with pytest.raises(SystemExit):
        stage.build_config(["nope=1"])


def test_fix_torch_stack_removes_torchaudio_and_falls_back_to_cu128(monkeypatch):
    """2026-10-26 smoke: vLLM's CUDA 13 torch vs Kaggle's cu12.8 torchaudio killed vLLM at import."""
    import subprocess as sp
    run = load_file(REPO / "kaggle" / "web-judge" / "run.py", "wj_run_fix")
    calls = []
    checks = iter(['{"torch": "2.11.0+cu130", "cuda": "13.0", "ok": false}',
                   '{"torch": "2.11.0+cu128", "cuda": "12.8", "ok": true}'])
    monkeypatch.setattr(run.subprocess, "call", lambda cmd, **kw: calls.append(cmd) or 0)
    monkeypatch.setattr(run.subprocess, "check_call", lambda cmd, **kw: calls.append(cmd) or 0)
    monkeypatch.setattr(run.subprocess, "run", lambda cmd, **kw: sp.CompletedProcess(cmd, 0, next(checks), ""))
    out = run.fix_torch_stack()
    assert "uninstall" in calls[0] and calls[0][-1] == "torchaudio"
    assert "https://download.pytorch.org/whl/cu128" in calls[1] and "torch==2.11.0" in calls[1]
    assert out["second"]["ok"] and out["first"]["cuda"] == "13.0"
    ok_checks = iter(['{"torch": "2.11.0+cu130", "cuda": "13.0", "ok": true}'])
    calls.clear()
    monkeypatch.setattr(run.subprocess, "run", lambda cmd, **kw: sp.CompletedProcess(cmd, 0, next(ok_checks), ""))
    assert run.fix_torch_stack() == {"first": {"torch": "2.11.0+cu130", "cuda": "13.0", "ok": True}}
    assert len(calls) == 1                                       # GPU usable: only torchaudio removed
