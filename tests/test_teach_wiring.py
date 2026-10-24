"""Product #2 teach-once wiring: the skill runner on the WP6 grounding ladder, WP7 secrets broker
and WP7 effect outcomes, end to end from a native-recorder-parity trace. No model calls,
127.0.0.1 only."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from scaffold.agent.desktop.effects.outcome import Outcome  # noqa: E402
from scaffold.agent.desktop.grounding import build_index  # noqa: E402
from scaffold.agent.desktop.secrets import MemoryProvider, SecretBroker  # noqa: E402
from testbeds.teach_once import DEMO_INSTANCE, TASKS  # noqa: E402
from testbeds.teach_once.browser import Page  # noqa: E402
from testbeds.teach_once.induction import induce  # noqa: E402
from testbeds.teach_once.seed import PORTAL_PASSWORD  # noqa: E402
from testbeds.teach_once.skill_runner import (DialogInjectingEnv, SkillRunner, evaluate, ground_role_name,  # noqa: E402
                                              run_instance)
from testbeds.teach_once.trace import validate_trace  # noqa: E402
from testbeds.teach_once.wiring import (GROUNDERS, BrokerEnv, page_to_ax_tree, run_outcome, wp6_ground,  # noqa: E402
                                        wp6_strict)

pytestmark = pytest.mark.timeout(300)


def _page(html: str, title: str = "t") -> Page:
    return Page("http://127.0.0.1:1/x", 200, f"<html><head><title>{title}</title></head><body><main>{html}"
                                             "</main></body></html>")


# ============================================================ AX adapter ==

def test_page_to_ax_tree_is_a_wp6_tree_without_secure_values():
    pg = _page('<form aria-label="Sign in"><label for="u">Username</label><input id="u" name="u" value="sam">'
               '<label for="p">Password</label><input id="p" name="p" type="password" value="hunter2-secret">'
               '<button>Sign in</button></form><a href="/help">Help</a>', title="Orders")
    tree, by_path = page_to_ax_tree(pg, "orders")
    assert "hunter2-secret" not in json.dumps(tree)
    idx = build_index(tree)
    assert not idx.truncated
    ctrls = {n.name: n for n in idx.nodes if n.path in by_path}
    assert set(ctrls) == {"Username", "Password", "Sign in", "Help"}
    assert ctrls["Password"].role == "securetextfield" and ctrls["Username"].value == "sam"
    assert ctrls["Sign in"].containers() == [("webarea", "orders"), ("form", "Sign in")]
    assert all(n.center is not None for n in ctrls.values())                # visible: WP6 gates can pass
    assert by_path[ctrls["Help"].path]["href"] == "/help"


def test_wp6_ground_maps_matches_and_refusals():
    pg = _page('<form><label for="a">Invoice number</label><input id="a" name="a">'
               '<label for="b">Bill date</label><input id="b" name="b"></form>'
               '<form><button>Cancel</button><button>Save</button></form>'
               '<a href="/e">Download CSV</a><a href="/f">CSV help</a>')
    g = wp6_ground(pg, {"role": "AXTextField", "name": "Invoice number"})
    assert g.status == "ok" and g.rung == "exact" and g.element["name"] == "Invoice number"
    g = wp6_ground(pg, {"role": "AXTextField", "name": "invoice number:"})        # WP6 normalised name
    assert g.status == "ok" and g.rung == "normalized"
    g = wp6_ground(pg, {"role": "AXLink", "name": "Export CSV", "aliases": ["Download CSV"]}, fuzzy=False)
    assert g.status == "ok" and g.rung == "alias"
    # rename: a tie is never picked; excluding another step's target makes it unique
    tgt = {"role": "AXTextField", "name": "Invoice date"}
    assert wp6_ground(pg, tgt).status == "ambiguous"
    g = wp6_ground(pg, tgt, exclude=["Invoice number"])
    assert g.status == "ok" and g.rung == "wp6:rename" and g.element["name"] == "Bill date"
    assert wp6_strict(pg, tgt, exclude=["Invoice number"]).status == "missing"   # strict: halt -> teach
    assert wp6_ground(pg, tgt, exclude=["Invoice number"], fuzzy=False).status == "missing"
    # WP6 ambiguity refuses (and the runner halts on it)
    two = _page('<form><button>Save</button></form><form><button>Save</button></form>')
    g = wp6_ground(two, {"role": "AXButton", "name": "Save"})
    assert g.status == "ambiguous" and g.refusal == "ambiguous" and g.candidates == ["Save", "Save"]
    # a normal step never resolves to a destructive control
    dz = _page('<form><button>Delete bill PS-1</button><button>Help</button></form>')
    assert wp6_ground(dz, {"role": "AXButton", "name": "Save bill PS-1"}).status == "missing"
    g = wp6_ground(dz, {"role": "AXButton", "name": "Save bill PS-1", "aliases": ["Delete bill PS-1"]})
    assert g.status == "ambiguous" and g.refusal == "risk_escalation"
    assert wp6_ground(None, {"role": "AXButton", "name": "x"}).status == "missing"
    assert GROUNDERS["heuristic"] is ground_role_name and GROUNDERS["wp6"] is wp6_ground


class _OnePageEnv:
    """Minimal run env: one web app showing one fixed page."""

    def __init__(self, home: Path, page: Page):
        self.home, self.context, self._page, self.clicked = home, {"today": "2026-03-02"}, page, []

    def is_web(self, app):
        return True

    def page(self, app):
        return self._page

    def click(self, app, el):
        self.clicked.append(el.get("name"))
        return self._page

    def actions(self):
        return len(self.clicked)


def _program(op: dict) -> dict:
    return {"task": "t_unit", "version": 1, "params": [], "globals": [], "setup": [op], "loop": None,
            "finally": [], "check": [], "handlers": [], "seen_text": {}}


@pytest.mark.parametrize("optional", [False, True])
def test_runner_halts_on_risk_escalation_even_for_optional_steps(tmp_path, optional):
    dz = _page('<form><button>Delete bill PS-1</button></form>')
    op = {"id": "s1", "action": "click", "app": "accounting", "optional": optional,
          "target": {"role": "AXButton", "name": "Save bill PS-1", "aliases": ["Delete bill PS-1"]}}
    env = _OnePageEnv(tmp_path, dz)
    res = SkillRunner(_program(op), env, ground="wp6").run()
    assert res["status"] == "HALTED" and res["halt"]["reason"] == "risk_escalation"
    assert res["halt"]["grounding"]["refusal"] == "risk_escalation"
    assert env.clicked == [] and run_outcome(res) is Outcome.HALTED_BEFORE_EFFECT
    two = _page('<form><button>Save</button></form><form><button>Save</button></form>')
    op2 = dict(op, target={"role": "AXButton", "name": "Save"})
    res = SkillRunner(_program(op2), _OnePageEnv(tmp_path, two), ground="wp6").run()
    assert res["status"] == "HALTED" and res["halt"]["reason"] == "ambiguous_match"


# =============================================================== secrets ==

@pytest.fixture(scope="module")
def progs(tmp_path_factory):
    from testbeds.teach_once.demonstrate import record_demo
    run = tmp_path_factory.mktemp("wiring_demos")
    return {t: induce([record_demo(run, t)]).program for t in TASKS}


def _leaky_dialog(env):
    """After sign-in the portal shows a modal that echoes the password (a leaky app)."""
    return DialogInjectingEnv(env, "orders", after_actions=4, title="Confirm your password",
                              lines=(f"You signed in with password {PORTAL_PASSWORD}.",),
                              buttons=("Not now", "Change password"))


def test_secrets_via_broker_are_substituted_at_typing_and_redacted_from_reports(progs, tmp_path):
    p2 = progs["t2_orders"]
    assert '"secret": "Password"' in json.dumps(p2)                      # the program holds no value
    ok = run_instance(p2, tmp_path / "ok", "t2_orders", "i03", ground="wp6", broker=True)
    assert ok["success"] and ok["run"]["status"] == "VERIFIED" and ok["run"]["model_calls"] == 0
    assert PORTAL_PASSWORD not in json.dumps(ok["run"])
    # a halt whose screen shows the secret: the report carries the handle, never the value
    leak = run_instance(p2, tmp_path / "leak", "t2_orders", "i03", ground="wp6", broker=True, env_wrap=_leaky_dialog)
    h = leak["run"]["halt"]
    assert leak["run"]["status"] == "HALTED" and h["reason"] == "unknown_dialog"
    blob = json.dumps(leak["run"])
    assert PORTAL_PASSWORD not in blob and "{{secret:Password}}" in blob
    # control: without the broker the same halt report would have leaked it
    raw = run_instance(p2, tmp_path / "raw", "t2_orders", "i03", ground="wp6", env_wrap=_leaky_dialog)
    assert PORTAL_PASSWORD in json.dumps(raw["run"]["halt"])


def test_broker_env_handles_and_sink_audit(tmp_path):
    class Base:
        typed = []

        def secret(self, name):
            return {"Password": "pw-value-123"}.get(name)

        def type(self, app, el, value):
            self.typed.append(value)

    env = BrokerEnv(Base())
    hdl = env.secret("Password")
    assert hdl == "{{secret:Password}}" and env.secret("nope") is None
    env.type("orders", {"name": "Password"}, hdl)
    assert Base.typed == ["pw-value-123"]
    assert env.broker.uses == [{"handle": "{{secret:Password}}", "target": "ui:orders/Password"}]
    assert env.redact_obj({"m": ["typed pw-value-123"]}) == {"m": ["typed {{secret:Password}}"]}
    # an explicit broker (e.g. the Keychain one) can be injected
    b = SecretBroker(MemoryProvider({"portal_user": "sam-x"}))
    assert BrokerEnv(Base(), b).secret("portal user") == "{{secret:portal_user}}"


# =============================================================== effects ==

class _NoDownload:
    """The export link stops producing a file (it returns a page instead)."""

    def __init__(self, base):
        self.base = base

    def __getattr__(self, k):
        return getattr(self.base, k)

    def click(self, app, el):
        if "CSV" in (el.get("name") or ""):
            return self.base.page(app)
        return self.base.click(app, el)


def test_effect_records_and_outcomes(progs, tmp_path):
    r = run_instance(progs["t2_orders"], tmp_path / "ok", "t2_orders", "i03", ground="wp6")
    run = r["run"]
    assert run["outcome"] == "verified" and run_outcome(run) is Outcome.VERIFIED
    dl = [e for e in run["effects"] if e["substrate"] == "file_arrival"]
    assert len(dl) == 1 and dl[0]["verdict"] == "confirmed" and dl[0]["check_level"] == "system_of_record"
    assert {e["substrate"] for e in run["effects"]} >= {"file_arrival", "csv_append", "file_write"}
    assert all(e["verdict"] == "confirmed" for e in run["effects"])
    # the download does not arrive: FileArrival refutes it, the run HALTS (no wrong effect downstream)
    bad = run_instance(progs["t2_orders"], tmp_path / "bad", "t2_orders", "i03", ground="wp6", env_wrap=_NoDownload)
    h = bad["run"]["halt"]
    assert bad["run"]["status"] == "HALTED" and h["reason"] == "postcondition_failed"
    assert h["effect"]["substrate"] == "file_arrival" and h["effect"]["verdict"] == "refuted" and h["effect"]["absent"]
    assert bad["run"]["outcome"] == "reconciliation_required"         # it had already signed in / filtered
    assert bad["verify"]["violations"] == []
    # file task: every move is a system-of-record confirmed effect
    r1 = run_instance(progs["t1_statements"], tmp_path / "t1", "t1_statements", "i04", ground="wp6")
    assert r1["run"]["outcome"] == "verified"
    assert any(e["substrate"] == "file_move" for e in r1["run"]["effects"])
    # nothing to do on the finished state: no effect, not halted
    again = run_instance(progs["t1_statements"], tmp_path / "t1", "t1_statements", "i04", ground="wp6")
    assert again["run"]["status"] != "HALTED"


# ============================================================ end to end ==

def _summary(out: dict) -> dict:
    return {k: out[k][x] for k in ("v1", "vN") for x in ("held_out_total",)} | {
        "model_calls": out["v1"]["model_calls"] + out["vN"]["model_calls"],
        "wrong_effects": out["v1"]["wrong_effects"] + out["vN"]["wrong_effects"]}


def test_native_trace_to_held_out_with_wp6_grounding(monkeypatch, tmp_path):
    """Acceptance: native-recorder-parity t3 trace -> induce -> held-out runs on WP6 grounding with
    the broker: v1 >= the heuristic default, 8/8 after teach, 0 model calls, 0 wrong effects, every
    failure a HALT; and the 3-task table (heuristic vs WP6) meets 19/24 -> 24/24."""
    from test_capture import run_parity
    _a, native, _log, _ad = run_parity(monkeypatch, tmp_path / "rec", "t3_invoices")
    assert native.recorder["kind"] == "macos_ax_fsevents" and validate_trace(native, (PORTAL_PASSWORD,)) == []
    table = {}
    for g in ("heuristic", "wp6", "wp6_strict"):
        out = evaluate(tmp_path / g, TASKS, ground=g, broker=True, traces={"t3_invoices": native}, quiet=True,
                       inject=(g == "wp6"))
        table[g] = out
        s = _summary(out)
        assert s["model_calls"] == 0 and s["wrong_effects"] == [], (g, s)
        assert out["vN"]["held_out"] == {t: "8/8" for t in TASKS}, (g, out["vN"])
        for k in ("v1", "vN"):
            n_halted = len([1 for t, r in out[k]["halted"].items() if not t.endswith("/" + DEMO_INSTANCE)])
            ok = int(out[k]["held_out_total"].split("/")[0])
            assert ok + n_halted == 24, (g, k, out[k])                        # every failure is a HALT
        assert PORTAL_PASSWORD not in json.dumps({k: v for k, v in out.items() if k != "programs"})
    h1 = int(table["heuristic"]["v1"]["held_out_total"].split("/")[0])
    w1 = int(table["wp6"]["v1"]["held_out_total"].split("/")[0])
    assert h1 >= 19 and w1 >= h1, (h1, w1)
    assert int(table["wp6"]["v1"]["held_out"]["t3_invoices"].split("/")[0]) >= 4
    assert all(r["resume"]["success"] and r["resume"]["outcome"] == "verified"
               for r in table["wp6"]["injected_dialog"])
    print("\n" + json.dumps({g: {"v1": o["v1"]["held_out"], "v1_total": o["v1"]["held_out_total"],
                                 "vN_total": o["vN"]["held_out_total"], "teach_rounds": len(o["teach_rounds"]),
                                 "v1_outcomes": o["v1"]["outcomes"]} for g, o in table.items()}, indent=1))
