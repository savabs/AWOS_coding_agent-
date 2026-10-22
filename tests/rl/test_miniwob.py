"""Fast, browser-free tests for scaffold/rl/miniwob (MiniWoB++ TaskSource).

Uses a tiny fake html root (core/core.js + miniwob/<task>.html), so no network and no
upstream download are needed."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scaffold.rl.contract import Score, TaskSpec, Trajectory
from scaffold.rl.miniwob import catalog
from scaffold.rl.miniwob.oracles import oracle_for
from scaffold.rl.miniwob.server import SHIM_TAG, EpisodeStore, MiniWobServer, inject_shim
from scaffold.rl.miniwob.source import PAGE_GOAL, MiniWobSource, secret_goal

PAGE = """<!DOCTYPE html><html><head><script src="../core/core.js"></script></head>
<body><div id="wrap"><div id="query"></div><div id="area"></div></div></body></html>"""


@pytest.fixture()
def html_root(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "core.js").write_text("var core = {};")
    (tmp_path / "miniwob").mkdir()
    for name in ("click-button", "enter-text", "login-user"):
        (tmp_path / "miniwob" / f"{name}.html").write_text(PAGE)
    (tmp_path / "secret.txt").write_text("outside")   # sibling of miniwob/, still under root
    return tmp_path


UTT = {
    "click-button/0": 'Click on the "okay" button.',
    "enter-text/0": 'Enter "Agustina" into the text field and press Submit.',
    "login-user/0": 'Enter the username "karrie" and the password "AU" into the text fields and press login.',
}


@pytest.fixture()
def source(html_root: Path):
    src = MiniWobSource(html_root=html_root, seeds=[0, 1], types=["click-button", "enter-text", "login-user"],
                        utterances=dict(UTT), outcome_wait=0.2, fetch=False)
    src.start()
    yield src
    src.stop()


def _get(url: str):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(url, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read().decode()


def _post(url: str, payload: dict) -> int:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


# ------------------------------------------------------------------ shim --

def test_inject_shim_before_last_body_close():
    out = inject_shim("<html><body><p>x</p></BODY></html>")
    assert out.index(SHIM_TAG) < out.index("</BODY>")
    assert out.count(SHIM_TAG) == 1


def test_inject_shim_without_body_appends():
    assert inject_shim("<p>x</p>").rstrip().endswith(SHIM_TAG)


def test_shim_js_contract():
    js = (Path(catalog.__file__).with_name("shim.js")).read_text()
    for needle in ("Math.seedrandom", "core.startEpisodeReal", "EPISODE_MAX_TIME", "/__awos/event",
                   "WOB_RAW_REWARD_GLOBAL", "awos_seed", "awos_ep", "awos_aria"):
        assert needle in js


# ---------------------------------------------------------------- server --

def test_server_serves_task_with_shim_and_static(source):
    base = source.base_url
    code, ctype, body = _get(base + "/miniwob/click-button.html?awos_seed=0")
    assert code == 200 and ctype.startswith("text/html")
    assert SHIM_TAG in body
    code, ctype, body = _get(base + "/core/core.js")
    assert code == 200 and "javascript" in ctype and SHIM_TAG not in body
    code, ctype, body = _get(base + "/__awos/shim.js")
    assert code == 200 and "core.startEpisodeReal" in body
    assert _get(base + "/__awos/health")[2] == "ok"


def test_server_blocks_traversal_and_missing(source):
    base = source.base_url
    for path in ("/miniwob/../../etc/passwd", "/%2e%2e/%2e%2e/etc/passwd", "/miniwob/nope.html"):
        with pytest.raises(urllib.error.HTTPError) as ei:
            _get(base + path)
        assert ei.value.code in (403, 404)


def test_server_binds_loopback(source):
    assert source.base_url.startswith("http://127.0.0.1:")


def test_episode_store_first_end_wins():
    st = EpisodeStore()
    ep = st.new("click-button", 0)
    assert st.record(ep, {"type": "end", "raw_reward": 1.0, "reward": 0.9})
    assert st.record(ep, {"type": "end", "raw_reward": -1.0, "reward": -1.0})
    assert st.get(ep)["end"]["raw_reward"] == 1.0
    assert not st.record("nope", {"type": "end"})
    assert not st.record(ep, {"type": "bogus"})


# ------------------------------------------------------------ reset/outcome --

def test_reset_and_reward_roundtrip(source):
    task = source.task("click-button", 0)
    url = source.reset(task)
    assert url.startswith(source.base_url + "/miniwob/click-button.html?awos_seed=0&awos_ep=")
    ep = url.split("awos_ep=")[1].split("&")[0]
    ev = source.base_url + "/__awos/event"
    assert _post(ev, {"ep": ep, "type": "start", "utterance": UTT["click-button/0"]}) == 200
    assert _post(ev, {"ep": ep, "type": "end", "raw_reward": 1.0, "reward": 0.97, "reason": None}) == 200
    sc = source.outcome(task, Trajectory(task_id=task.id, model="t"))
    assert isinstance(sc, Score) and sc.success and sc.unsafe == [] and sc.reward == 1.0
    assert "raw_reward=1" in sc.notes and "utterance_mismatch" not in sc.notes
    # the debug endpoint shows the same record
    rec = json.loads(_get(source.base_url + f"/__awos/episode/{ep}")[2])
    assert rec["end"]["raw_reward"] == 1.0


def test_outcome_failure_modes(source):
    task = source.task("enter-text", 0)
    ev = source.base_url + "/__awos/event"
    # 1. never started
    source.reset(task)
    assert "never reported start" in source.outcome(task).notes
    # 2. started, not finished
    ep = source.reset(task).split("awos_ep=")[1].split("&")[0]
    _post(ev, {"ep": ep, "type": "start", "utterance": "something else"})
    sc = source.outcome(task)
    assert not sc.success and "no_reward" in sc.notes and "utterance_mismatch" in sc.notes
    # 3. negative reward
    ep = source.reset(task).split("awos_ep=")[1].split("&")[0]
    _post(ev, {"ep": ep, "type": "start", "utterance": UTT["enter-text/0"]})
    _post(ev, {"ep": ep, "type": "end", "raw_reward": -1.0, "reward": -1.0})
    sc = source.outcome(task)
    assert not sc.success and sc.progress == 0.0 and sc.reward == 0.0
    # 4. a stale episode id is rejected after a new reset
    assert _post(ev, {"ep": "0" * 16, "type": "end", "raw_reward": 1}) == 404


def test_bad_post_rejected(source):
    req = urllib.request.Request(source.base_url + "/__awos/event", data=b"not json",
                                 headers={"Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with pytest.raises(urllib.error.HTTPError) as ei:
        op.open(req, timeout=5)
    assert ei.value.code == 400


# ------------------------------------------------------------------ tasks --

def test_task_spec_shape(source):
    t = source.task("click-button", 0)
    assert isinstance(t, TaskSpec)
    assert t.id == "miniwob/click-button/s0" and t.source == "miniwob" and t.kind == "act"
    assert t.goal == UTT["click-button/0"] and t.meta["goal_mode"] == "utterance"
    assert TaskSpec.from_dict(t.to_dict()) == t
    # unknown seed -> page goal fallback, tagged
    t1 = source.task("click-button", 1)
    assert t1.goal == PAGE_GOAL and "goal_on_page" in t1.tags


def test_secret_tasks_use_handle(source):
    t = source.task("login-user", 0)
    assert t.secrets_in == ["password"]
    assert "{{secret:password}}" in t.goal and '"AU"' not in t.goal
    assert t.meta["secrets"] == {"password": "AU"}
    assert secret_goal('Enter the password "x y" into both text fields and press submit.') == \
        ('Enter the password "{{secret:password}}" into both text fields and press submit.', "x y")


def test_catalog_partition_and_reasons():
    assert not set(catalog.INCLUDED) & set(catalog.EXCLUDED)
    assert len(catalog.ALL_TYPES) == 130          # every .html task at the pinned commit
    assert all(len(r) > 10 for r in catalog.EXCLUDED.values())


def test_holdout_deterministic_and_frozen():
    a, b = catalog.compute_holdout(), catalog.compute_holdout()
    assert a == b == catalog.frozen_holdout()
    rec = json.loads((catalog.DATA_DIR / "holdout.json").read_text())
    assert rec == catalog.holdout_record()
    n = len(catalog.INCLUDED)
    assert abs(len(a) - round(0.2 * n)) <= 1
    # whole families are held out: no held-out type has a trained sibling
    held_fams = {catalog.INCLUDED[x].family for x in a}
    for name, t in catalog.INCLUDED.items():
        assert (name in a) == (t.family in held_fams)


def test_split_listing_deterministic():
    src = MiniWobSource(html_root=Path("/nonexistent"), fetch=False)
    ids1 = [t.id for t in src.tasks("holdout")]
    ids2 = [t.id for t in MiniWobSource(html_root=Path("/nonexistent"), fetch=False).tasks("holdout")]
    assert ids1 == ids2 and len(ids1) == len(catalog.frozen_holdout()) * len(catalog.EVAL_SEEDS)
    train = list(src.tasks("train"))
    assert {t.split for t in train} == {"train"}
    assert len(train) + len(ids1) == len(list(src.tasks()))
    assert not {t.meta["type"] for t in train} & set(catalog.frozen_holdout())


def test_frozen_utterances_cover_eval_seeds():
    from scaffold.rl.miniwob.source import load_utterances
    utt = load_utterances(extra=Path("/nonexistent"))
    for name in catalog.INCLUDED:
        for s in catalog.EVAL_SEEDS:
            assert utt.get(f"{name}/{s}"), f"{name}/{s}"


# ---------------------------------------------------------------- oracles --

def test_oracles_from_fixed_utterances():
    assert oracle_for("click-button", 'Click on the "Ok" button.') == [
        {"op": "click", "role": "button", "name": "Ok"},
        {"op": "done", "text": 'Clicked the "Ok" button.'}]
    assert oracle_for("click-link", 'Click on the link "Eget".')[0] == {"op": "click", "role": "link", "name": "Eget"}
    steps = oracle_for("enter-text", 'Enter "Agustina" into the text field and press Submit.')
    assert steps[0] == {"op": "fill", "role": "textbox", "name": "", "value": "Agustina"}
    assert steps[1] == {"op": "click", "role": "button", "name": "Submit"}
    steps = oracle_for("click-checkboxes", "Select nb, 8qiSp3m, U6gHF and click Submit.")
    assert [s["name"] for s in steps if s.get("role") == "checkbox"] == ["nb", "8qiSp3m", "U6gHF"]
    steps = oracle_for("click-checkboxes", "Select nothing and click Submit.")
    assert [s["op"] for s in steps] == ["click", "done"]
    assert oracle_for("click-button", "something else") == []
    assert oracle_for("book-flight", "Book the cheapest flight") == []


def test_oracles_parse_all_frozen_utterances():
    from scaffold.rl.miniwob.oracles import ORACLES
    from scaffold.rl.miniwob.source import load_utterances
    utt = load_utterances(extra=Path("/nonexistent"))
    for name in ORACLES:
        for s in catalog.EVAL_SEEDS:
            steps = oracle_for(name, utt[f"{name}/{s}"])
            assert steps and steps[-1]["op"] == "done", f"{name}/{s}"


def test_source_oracle_uses_meta(source):
    t = source.task("click-button", 0)
    assert source.oracle(t)[0]["name"] == "okay"
    assert source.oracle(source.task("login-user", 0)) == []
