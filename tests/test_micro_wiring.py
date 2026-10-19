"""micro_wiring.py: the WP8 micro-agent on the real WP6 grounding, WP7 secrets
broker, WP7 effects (write_field / FieldEquals) and the run_once key ledger.
Scripted fake model only: no model calls, network only on 127.0.0.1."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from scaffold.agent.desktop import micro_agent as ma
from scaffold.agent.desktop import micro_wiring as mw
from scaffold.agent.desktop import web_tools as wt
from scaffold.agent.desktop.effects import OperationLedger, OpState
from scaffold.agent.desktop.secrets import KeyringProvider, MemoryProvider, SecretBroker

REPO = Path(__file__).resolve().parents[1]
TB = REPO / "testbeds" / "dev_dashboard"
if str(TB) not in sys.path:
    sys.path.insert(0, str(TB))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, TB / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dd_seed = _load("dd_seed")
dd_server = _load("dd_server")
dd_verify = _load("verify")
dd_run = _load("run")

KEY = re.compile(r"fk_live_[A-Za-z0-9]{32}")
SIGN_IN = ['fill "Email" {{secret:fakeservice_username}}', 'fill "Password" {{secret:fakeservice_password}}',
           'click "Sign in"', 'fill "Verification code" {{secret:otp_code}}', 'click "Verify"',
           'done "signed in"']


def create_steps(project, scope_label, confirm):
    s = [f'click "{project}"', 'click "API keys"', 'click "Create key"', f'fill "Key name" "{project}-awos"',
         f'select "Scope" "{scope_label}"', 'click "Continue"']
    if confirm:
        s.append('click "Create key@dialog"')
    return s + ['copy "New API key value" fakeservice_key', 'done "key created and copied"']


SCRIPTS = {
    "a": {"sign_in": SIGN_IN, "create_key": create_steps("storefront-web", "Read-only", False)},
    "b": {"sign_in": SIGN_IN, "create_key": create_steps("ml-pipeline", "Read & write", True)},
    "c": {"sign_in": SIGN_IN, "create_key": create_steps("storefront-web", "Read-only", False)},
}


class Recorder(ma._MultiScript):
    def prompts(self):
        return [p for m in self.models.values() for p in m.prompts]


class SpyGrounder(mw.WebAXGrounder):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def resolve(self, page, n):
        self.calls += 1
        return super().resolve(page, n)


def _acts(root):
    return [json.loads(x) for x in (root / "harness/actions.jsonl").read_text().splitlines()]


def _created(root):
    return [a["key"] for a in _acts(root) if a["action"] == "create_key"]


def _run(root, variant, script=None, lines=None, **kw):
    lines = lines if lines is not None else []
    model = Recorder(script or SCRIPTS[variant])
    rep = ma.run_dev_dashboard(root / "sandbox", model, out=lines.append, **kw)
    return rep, model, lines


def _verify(root, lines):
    tr = root / "harness/transcript.txt"
    tr.write_text("\n".join(lines) + "\n")
    return dd_verify.verify(root / "harness", transcript=tr)


@pytest.fixture
def world(tmp_path, request):
    variant = getattr(request, "param", "a")
    root = tmp_path / f"w_{variant}"
    truth, srv = dd_run.setup(root, variant, 7)
    yield root, variant, srv
    srv.stop()


def _leak_free(root, model, lines, rep_path=None):
    keys = _created(root)
    blobs = {"prompts": "\n".join(model.prompts()), "transcript": "\n".join(lines),
             "report": (root / "sandbox/outbox/report.json").read_text()}
    ledger = root / "sandbox/.awos/micro_operations.jsonl"
    if ledger.exists():
        blobs["ledger"] = ledger.read_text()
    for name, blob in blobs.items():
        assert not KEY.search(blob), name
        for k in keys:
            assert k not in blob, name


# --- acceptance: variants a/b/c on the real pieces --------------------------------------------

@pytest.mark.parametrize("world", ["a", "b", "c"], indirect=True)
def test_real_wiring_variants_verified(world):
    root, variant, srv = world
    spy = SpyGrounder()
    store = mw.BrokerSecretStore()
    rep, model, lines = _run(root, variant, grounder=spy, store=store, wiring="real")
    assert rep["status"] == "done", rep
    assert rep["wiring"] == "real" and rep["key_plan"] == "create"
    assert rep["operation"]["outcome"] == "verified" and rep["operation"]["executed"]
    assert [s["outcome"] for s in rep["subtasks"]] == ["verified", "verified"]
    assert spy.calls >= 8 and spy.last is not None and spy.last.ok
    res = _verify(root, lines)
    assert res["pass"], res["reasons"]
    assert len(_created(root)) == 1
    _leak_free(root, model, lines)
    led = (root / "sandbox/.awos/micro_operations.jsonl").read_text()
    assert '"state": "succeeded"' in led and "{{secret:" not in led     # payload hashed, not stored
    kc = json.loads((root / "sandbox/Keychain/fakeservice.json").read_text())
    assert kc["password"] not in "\n".join(model.prompts())
    assert "{{secret:otp_code}}" in "\n".join(model.prompts())
    assert any(line.startswith("KEY plan=create op=op_") for line in lines)


def test_default_wiring_is_real(world, monkeypatch):
    root, variant, srv = world
    monkeypatch.delenv(mw.WIRING_ENV, raising=False)
    rep, model, lines = _run(root, variant)
    assert rep["status"] == "done" and rep["wiring"] == "real" and rep["key_plan"] == "create"
    agent = ma.MicroAgent(wt.LoopbackBrowser(srv.dashboard_url), model)
    assert isinstance(agent.grounder, mw.WebAXGrounder) and isinstance(agent.store, mw.BrokerSecretStore)


@pytest.mark.parametrize("world", ["a", "b"], indirect=True)
def test_stand_ins_still_available(world, monkeypatch):
    root, variant, srv = world
    rep, model, lines = _run(root, variant, wiring="local")
    assert rep["status"] == "done" and rep["wiring"] == "local" and "key_plan" not in rep
    assert _verify(root, lines)["pass"]
    assert not (root / "sandbox/.awos/micro_operations.jsonl").exists()
    monkeypatch.setenv(mw.WIRING_ENV, "local")
    agent = ma.MicroAgent(wt.LoopbackBrowser(srv.dashboard_url), model)
    assert isinstance(agent.grounder, ma.IndexGrounder) and isinstance(agent.store, ma.LocalSecretStore)


def test_keyring_provider_backend(world):
    """KeyringProvider path (an injected in-memory keyring backend; the real Keychain is not touched)."""
    class FakeKeyring:
        def __init__(self):
            self.d = {}

        def get_password(self, s, u):
            return self.d.get((s, u))

        def set_password(self, s, u, v):
            self.d[(s, u)] = v

        def delete_password(self, s, u):
            self.d.pop((s, u), None)

    root, variant, srv = world
    kr = FakeKeyring()
    store = mw.BrokerSecretStore(provider=KeyringProvider(service=mw.KEYRING_SERVICE, backend=kr))
    rep, model, lines = _run(root, variant, store=store)
    assert rep["status"] == "done"
    assert kr.d[(mw.KEYRING_SERVICE, "fakeservice_key")] == _created(root)[0]


# --- approval, retry, injection, MFA ---------------------------------------------------------

@pytest.mark.parametrize("world", ["b"], indirect=True)
def test_approval_before_create(world):
    root, variant, srv = world
    rep, model, lines = _run(root, variant)
    create = next(a for a in _acts(root) if a["action"] == "create_key")
    aps = [json.loads(x) for x in (root / "harness/approvals.jsonl").read_text().splitlines()]
    assert aps and aps[0]["decision"] == "approved" and aps[0]["scope"] == "write"
    assert aps[0]["seq"] < create["seq"]


def test_denied_approval_creates_nothing_and_ledger_allows_later_run(world):
    root, variant, srv = world
    ledger = OperationLedger(root / "ops.jsonl")
    asked = []
    rep, model, lines = _run(root, variant, ledger=ledger, approver=lambda req: asked.append(req) or False)
    assert rep["status"] == "needs_user" and rep["subtasks"][1]["outcome"] == "denied"
    assert asked and asked[0].action == "create_key" and _created(root) == []
    assert rep["operation"]["outcome"] == "halted_before_effect"
    op_id = rep["operation"]["op_id"]
    assert ledger.get(op_id) is OpState.HALTED
    rep2, _, lines2 = _run(root, variant, ledger=ledger)                  # nothing happened -> may run again
    assert rep2["status"] == "done" and len(_created(root)) == 1


def test_retry_after_success_does_not_create_second_key(world):
    root, variant, srv = world
    provider = MemoryProvider()                     # the vault survives the "crash" (as the Keychain would)
    ledger = OperationLedger(root / "ops.jsonl")
    lines = []
    rep1, m1, _ = _run(root, variant, store=mw.BrokerSecretStore(provider=provider), ledger=ledger, lines=lines)
    assert rep1["status"] == "done"
    rep2, m2, _ = _run(root, variant, store=mw.BrokerSecretStore(provider=provider),
                       ledger=OperationLedger(root / "ops.jsonl"), lines=lines)
    assert rep2["status"] == "done" and rep2["key_plan"] == "reuse"
    assert len(_created(root)) == 1
    assert m2.models["create_key"].prompts == []                          # the create subtask never ran
    res = _verify(root, lines)
    assert res["pass"], res["reasons"]


def test_retry_after_crash_before_save_reuses_key(world):
    """Key created and captured, then the sink fails (a crash before saving): the retry saves the
    same key and creates nothing."""
    root, variant, srv = world
    provider = MemoryProvider()
    lines = []

    class BrokenSink:
        def write_field(self, *a):
            return ma.EffectResult(False, "disk went away")

    rep1, _, _ = _run(root, variant, store=mw.BrokerSecretStore(provider=provider), sink=BrokenSink(), lines=lines)
    assert rep1["status"] == "failed"
    rep2, _, _ = _run(root, variant, store=mw.BrokerSecretStore(provider=provider), lines=lines)
    assert rep2["status"] == "done" and rep2["key_plan"] == "reuse"
    assert len(_created(root)) == 1
    res = _verify(root, lines)
    assert res["pass"], res["reasons"]


def test_retry_with_lost_vault_needs_user_not_duplicate(world):
    """The key exists on the dashboard but the vault lost it: never a silent second key."""
    root, variant, srv = world
    rep1, _, _ = _run(root, variant)
    assert rep1["status"] == "done"
    rep2, _, _ = _run(root, variant, store=mw.BrokerSecretStore())       # fresh, empty vault
    assert rep2["status"] == "needs_user" and "duplicate" in rep2["subtasks"][1]["reason"]
    assert len(_created(root)) == 1


def test_uncertain_ledger_entry_is_verified_before_retry(world):
    """A crash mid-create (ledger STARTED) with the key provably absent -> safe to run once."""
    root, variant, srv = world
    from scaffold.agent.desktop.effects import payload_hash
    ledger = OperationLedger(root / "ops.jsonl")
    payload = {"service": "FakeService", "project": "storefront-web", "scope": "read",
               "label": "storefront-web-awos", "secret": "{{secret:fakeservice_key}}"}
    op_id = payload_hash("create_credential", payload)
    ledger.record(op_id, "create_credential", OpState.STARTED)
    rep, _, _ = _run(root, variant, ledger=ledger)
    assert rep["status"] == "done" and rep["operation"]["op_id"] == op_id
    assert len(_created(root)) == 1 and ledger.get(op_id) is OpState.SUCCEEDED


def test_ledger_done_but_key_gone_needs_user(world):
    root, variant, srv = world
    rep1, _, _ = _run(root, variant, ledger=OperationLedger(root / "ops.jsonl"))
    assert rep1["status"] == "done"
    for p in srv.world.projects.values():
        for k in p["keys"]:
            if k["name"] == "storefront-web-awos":
                k["status"] = "revoked"
    rep2, _, _ = _run(root, variant, ledger=OperationLedger(root / "ops.jsonl"))
    assert rep2["status"] == "needs_user" and "reconcile" in rep2["subtasks"][1]["reason"]
    assert len(_created(root)) == 1


@pytest.mark.parametrize("world", ["c"], indirect=True)
def test_injection_flagged_and_admin_pick_blocked(world):
    root, variant, srv = world
    steps = create_steps("storefront-web", "Admin (full access)", False)
    rep, model, lines = _run(root, variant, {"sign_in": SIGN_IN, "create_key": steps})
    assert rep["status"] == "needs_user" and rep["flags"][0]["type"] == "prompt_injection"
    assert not any(a["action"] == "create_key" for a in _acts(root))
    assert rep["operation"]["outcome"] == "halted_before_effect"


def test_mfa_without_user_channel_needs_user(world):
    root, variant, srv = world
    rep, model, lines = _run(root, variant, user_channel=None)
    assert rep["status"] == "needs_user" and rep["subtasks"][0]["outcome"] == "needs_user"
    assert "verification code" in rep["subtasks"][0]["reason"]
    assert "login_2fa_ok" not in [a["action"] for a in _acts(root)]


def test_existing_awos_key_needs_user(world):
    root, variant, srv = world
    srv.world.new_key("storefront-web", "storefront-web-awos", "read")
    rep, model, lines = _run(root, variant)
    assert rep["status"] == "needs_user" and "duplicate" in rep["subtasks"][1]["reason"]
    assert _created(root) == [] and model.models["create_key"].prompts == []


# --- WP6 grounder on web pages ----------------------------------------------------------------

def _page(body):
    return wt.parse_page("http://127.0.0.1:1/x", 200, f"<title>t</title>{body}")


def test_grounder_ambiguous_and_unnamed_refuse():
    p = _page('<form method="post" action="/x"><button type="submit">Create key</button></form>'
              '<form method="post" action="/y"><button type="submit">Create key</button></form>')
    g = mw.WebAXGrounder().resolve(p, 1)
    assert g.control is None and g.refusal.startswith("ambiguous control")
    assert mw.WebAXGrounder().resolve(p, 9).refusal == "control 9 is not on this page"


def test_grounder_resolves_same_name_in_different_containers():
    p = _page('<form method="post" action="/r1" aria-label="Revoke key a"><button type="submit">Revoke</button></form>'
              '<form method="post" action="/r2" aria-label="Revoke key b"><button type="submit">Revoke</button></form>')
    gr = mw.WebAXGrounder()
    assert [gr.resolve(p, n).control.n for n in (1, 2)] == [1, 2]
    assert gr.last.match.tier == "role_name"


def test_grounder_refuses_click_under_open_dialog():
    p = _page('<a href="/projects">Projects</a>'
              '<div role="alertdialog" aria-label="Confirm"><p>Sure?</p>'
              '<form method="post" action="/c"><button type="submit">Create key</button></form>'
              '<form method="post" action="/n"><button type="submit">Cancel</button></form></div>')
    names = {c.name: c.n for c in p.controls}
    gr = mw.WebAXGrounder()
    assert gr.resolve(p, names["Create key"]).control is not None
    assert gr.resolve(p, names["Cancel"]).control is not None
    out = gr.resolve(p, names["Projects"])
    assert out.control is None and "blocked_by_modal" in out.refusal


# --- secrets store, sink, effect check ---------------------------------------------------------

def test_broker_store_redacts_and_masks():
    st = mw.BrokerSecretStore()
    v = "fk_live_" + "A1b2C3d4" * 4
    st.put("fakeservice_key", v)
    assert st.has("fakeservice_key") and st.get("fakeservice_key") == v
    assert v not in st.redact(f"saved {v}") and "{{secret:fakeservice_key}}" in st.redact(f"saved {v}")
    assert v not in st.mask_observation(f"Key created. Copy this key now: {v}")
    with pytest.raises(ValueError):
        st.put("Bad Name", "x")
    with pytest.raises(KeyError):
        mw.BrokerSecretStore().get("nope")


def test_effects_sink_writes_verifies_and_refuses(tmp_path):
    st = mw.BrokerSecretStore()
    v = "fk_live_" + "Z9y8X7w6" * 4
    st.put("k", v)
    env = tmp_path / ".env"
    env.write_text("# FakeService\nOTHER=1\nFAKESERVICE_API_KEY=\n")
    env.chmod(0o600)
    check = mw.FieldEqualsCheck(env, "dotenv", "FAKESERVICE_API_KEY", st, "k")
    sink = mw.EffectsSecretSink(st)
    r = sink.write_field(env, "dotenv", "FAKESERVICE_API_KEY", "k")
    assert r.ok and check().ok
    assert env.read_text() == f"# FakeService\nOTHER=1\nFAKESERVICE_API_KEY={v}\n"
    assert (env.stat().st_mode & 0o777) == 0o600
    assert v not in json.dumps(sink.last)
    again = sink.write_field(env, "dotenv", "FAKESERVICE_API_KEY", "k")     # idempotent
    assert again.ok and "unchanged" in again.detail
    dup = tmp_path / "dup.env"
    dup.write_text("FAKESERVICE_API_KEY=a\nFAKESERVICE_API_KEY=b\n")
    r = sink.write_field(dup, "dotenv", "FAKESERVICE_API_KEY", "k")
    assert not r.ok and "halted" in r.detail and dup.read_text() == "FAKESERVICE_API_KEY=a\nFAKESERVICE_API_KEY=b\n"
    assert not sink.write_field(env, "dotenv", "FAKESERVICE_API_KEY", "missing").ok
    toml = tmp_path / "c.toml"
    toml.write_text('[fakeservice]\napi_key = "old"   # expires\nbase_url = "https://x"\n')
    check = mw.FieldEqualsCheck(toml, "toml", "fakeservice.api_key", st, "k")
    assert sink.write_field(toml, "toml", "fakeservice.api_key", "k").ok and check().ok


def test_effects_sink_through_enforcer(tmp_path):
    from scaffold.agent.desktop.enforce import Enforcer, Mode
    from scaffold.agent.desktop.policy import SinkPolicy
    st = mw.BrokerSecretStore()
    st.put("k", "fk_live_" + "Q1w2E3r4" * 4)
    env = tmp_path / ".env"
    env.write_text("FAKESERVICE_API_KEY=\n")
    enf = Enforcer(SinkPolicy(tmp_path), Mode.ENFORCE, broker=st.broker)
    r = mw.EffectsSecretSink(st, enforcer=enf).write_field(env, "dotenv", "FAKESERVICE_API_KEY", "k")
    assert r.ok, r.detail
    assert "fk_live_" in env.read_text()


# --- configurable time cap ------------------------------------------------------------------------

def test_time_cap_env(monkeypatch):
    monkeypatch.delenv(ma.MAX_SECONDS_ENV, raising=False)
    assert ma.max_seconds_cap() == 120.0
    monkeypatch.setenv(ma.MAX_SECONDS_ENV, "600")
    assert ma.max_seconds_cap() == 600.0
    monkeypatch.setenv(ma.MAX_SECONDS_ENV, "nonsense")
    assert ma.max_seconds_cap() == 120.0
    monkeypatch.setenv(ma.MAX_SECONDS_ENV, "99999")
    assert ma.max_seconds_cap() == ma.MAX_SECONDS_CEILING


def test_time_cap_env_applies_to_subtasks(monkeypatch):
    monkeypatch.setenv(ma.MAX_SECONDS_ENV, "300")
    t = [0.0]

    def clock():
        t[0] += 50.0
        return t[0]

    class Br:
        page = None

        def open(self, path):
            return _page('<h1>Home</h1><a href="/n">Next</a>')

        def click(self, page, n):
            return page

    model = ma.ScriptedModel(['click "Next"'] * 20)
    agent = ma.MicroAgent(Br(), model, clock=clock)
    r = agent.run(ma.Subtask("t", "loop", start="/"))
    assert r.outcome is ma.Outcome.HALTED and "time cap 300s" in r.reason
    assert len(model.prompts) > 2                         # 120 s would have stopped after ~2 calls
