"""Issue B: small local models repeat a step that already worked.

Live evidence (VM, real Safari, local Qwen3.5-9B): after the new key was
captured the model sent copy_to_secret(4, fakeservice_key) five times and
only said done at step 12/12 (the cap). These tests reproduce that with fake
models and check the micro-agent loop now:
  * states in the history/observation that the secret is captured and that
    done is the next verb, and stops offering copy_to_secret in the grammar;
  * short-circuits an identical action that already succeeded (no second
    read of the hidden value), with a clear message;
  * auto-completes when the model repeats a finished step and the success
    condition already holds -- through the same ack + effect check as done();
  * tells the model which page it is on and what the page is for.
Scripted / fake models only: no model calls, network only on 127.0.0.1."""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scaffold.agent.desktop import micro_agent as ma
from scaffold.agent.desktop import web_tools as wt

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_micro_agent as tma  # noqa: E402  (testbed loaders, scripts, verifier)

KEY = re.compile(r"fk_live_[A-Za-z0-9]{32}")


class RepeatingModel(ma.ScriptedModel):
    """Behaves like the live Qwen run: follows the plan, and once the key is
    on screen keeps answering copy_to_secret for ``repeat`` replies before it
    says done. A grammar-constrained (gbnf) model can only emit what the
    grammar allows, so when copy_to_secret is no longer in the grammar it
    falls through to the next script step (done). A JSON model is not
    constrained and repeats regardless."""

    def __init__(self, steps, repeat=5, fmt="gbnf"):
        super().__init__(steps, fmt)
        self.repeat = repeat
        self.repeats_sent = 0

    def decide(self, system, user, grammar=None):
        if self.i < len(self.steps) and self.i > 0 and self.steps[self.i - 1].startswith("copy ") \
                and self.repeats_sent < self.repeat:
            allowed = grammar is None or "copy_to_secret(" in grammar
            if allowed:
                self.repeats_sent += 1
                self.prompts.append(user)
                self.grammars.append(grammar)
                out = self._render(self.steps[self.i - 1], user)
                return out if self.output_format == "gbnf" else self._to_json(out)
        return super().decide(system, user, grammar)


class Multi:
    """Per-subtask models (by the Goal line), like micro_agent._MultiScript."""

    def __init__(self, sign_in, create, fmt="gbnf"):
        self.output_format = fmt
        self.models = {"sign_in": sign_in, "create_key": create}

    def decide(self, system, user, grammar=None):
        key = "sign_in" if user.startswith("Goal: Sign in") else "create_key"
        return self.models[key].decide(system, user, grammar)


def _run(tmp_path, variant, create_model, fmt="gbnf"):
    root, truth, srv = tma._world(tmp_path, variant)
    lines = []
    sign_in = ma.ScriptedModel(tma.SIGN_IN, fmt)
    model = Multi(sign_in, create_model, fmt)
    try:
        rep = ma.run_dev_dashboard(root / "sandbox", model, out=lines.append)
    finally:
        srv.stop()
    tr = root / "harness/transcript.txt"
    tr.write_text("\n".join(lines) + "\n")
    res = tma.dd_verify.verify(root / "harness", transcript=tr)
    return root, rep, res, lines


def _create_steps(variant):
    return tma.SCRIPTS[variant]["create_key"]


# --- the live failure, reproduced -----------------------------------------------------------

@pytest.mark.parametrize("variant", ["a", "b", "c"])
def test_gbnf_repeater_cannot_repeat_copy_and_finishes_early(tmp_path, variant):
    m = RepeatingModel(_create_steps(variant), repeat=5)
    root, rep, res, lines = _run(tmp_path, variant, m)
    assert rep["status"] == "done", (rep, lines[-20:])
    assert res["pass"], res["reasons"]
    ck = rep["subtasks"][1]
    n_plan = len(_create_steps(variant))
    assert ck["outcome"] == "verified" and ck["steps"] == n_plan < 12     # not 12/12
    assert m.repeats_sent == 0                    # grammar no longer offered the repeat
    copies = [ln for ln in lines if "copy_to_secret(" in ln and "step" in ln]
    assert len(copies) == 1
    # the reply after the capture: grammar without copy, prompt says captured + done next
    after = m.prompts[-1]
    assert "copy_to_secret(" not in m.grammars[-1]
    assert "Already captured" in after and "{{secret:fakeservice_key}}" in after
    assert "CAPTURED: the hidden value is now saved as {{secret:fakeservice_key}}" in after
    assert "Your next reply must be done(" in after
    assert "Names you may copy_to_secret into" not in after
    assert "already saved as {{secret:fakeservice_key}}; do not copy again" in after
    assert "hidden; use copy_to_secret" not in after
    created = [json.loads(x)["key"] for x in (root / "harness/actions.jsonl").read_text().splitlines()
               if json.loads(x)["action"] == "create_key"]
    assert len(created) == 1
    blob = "\n".join(m.prompts) + "\n".join(lines)
    assert created[0] not in blob and not KEY.search(blob)


@pytest.mark.parametrize("variant", ["a", "c"])
def test_json_repeater_is_auto_completed_through_ack_and_effect(tmp_path, variant):
    """An unconstrained model repeats copy_to_secret anyway: the repeat is not
    executed, and since ack + capture already hold, the subtask closes VERIFIED
    via the same effect check (never on the model's word)."""
    m = RepeatingModel(_create_steps(variant), repeat=5, fmt="json")
    root, rep, res, lines = _run(tmp_path, variant, m, fmt="json")
    assert rep["status"] == "done", (rep, lines[-20:])
    assert res["pass"], res["reasons"]
    ck = rep["subtasks"][1]
    assert ck["outcome"] == "verified"
    assert ck["steps"] == len(_create_steps(variant))          # copy, then ONE repeat -> auto done
    assert m.repeats_sent == 1
    assert any("success condition met -> auto done" in ln for ln in lines)
    assert sum("step" in ln and "copy_to_secret(" in ln for ln in lines) == 2


def test_original_scripted_plan_unchanged(tmp_path):
    """The scripted happy path (copy then done) still takes exactly the same steps."""
    m = ma.ScriptedModel(_create_steps("b"))
    root, rep, res, lines = _run(tmp_path, "b", m)
    assert rep["status"] == "done" and res["pass"]
    assert rep["subtasks"][1]["steps"] == len(_create_steps("b"))


def test_prompt_states_current_page_purpose(tmp_path):
    m = ma.ScriptedModel(_create_steps("a"))
    root, rep, res, lines = _run(tmp_path, "a", m)
    assert rep["status"] == "done"
    p = m.prompts
    assert all("Current page: \"" in x for x in p)
    assert "navigation page" in p[0]                               # the projects list
    form = next(x for x in p if "form to fill" in x)
    assert "Key name" in form.split("form to fill", 1)[1].split("\n", 1)[0]
    shown = next(x for x in p if "to save with copy_to_secret" in x)
    assert "it shows a new hidden value [" in shown
    assert "Pages so far:" in p[-1]


# --- loop rules on small fixture pages -----------------------------------------------------------

SECRET = "fk_live_aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dF"
PAGES = {
    # hidden value, but no acknowledgement text the subtask asks for
    "/noack": ('<h1>Key</h1><div role="region" aria-label="Token">'
               f'<code id="newkey" aria-label="New API key value">{SECRET}</code></div>'),
    "/acked": ('<h1>Key created</h1><div role="region" aria-live="polite" aria-label="New API key">'
               '<p>Copy this key now.</p>'
               f'<code id="newkey" aria-label="New API key value">{SECRET}</code></div>'),
    "/form": ('<h1>New thing</h1><form method="get" action="/next" aria-label="Thing">'
              '<input name="nm" aria-label="Thing name"><button type="submit">Next</button></form>'),
    "/next": '<h1>Next</h1><a href="/form">Back</a>',
}


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = PAGES.get(self.path.split("?")[0], "<h1>Not found</h1>")
        code = 200 if self.path.split("?")[0] in PAGES else 404
        b = f"<!doctype html><html><head><title>t</title></head><body>{body}</body></html>".encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


class CountingBrowser(wt.LoopbackBrowser):
    reads = 0

    def read_raw(self, page, n):
        self.reads += 1
        return super().read_raw(page, n)


def _agent(site, model):
    br = CountingBrowser(site)
    store = ma.LocalSecretStore()
    return ma.MicroAgent(br, model, store=store, grounder=ma.IndexGrounder()), br, store


def test_duplicate_copy_without_ack_is_skipped_not_reread_and_never_verified(site):
    model = ma.ScriptedModel(['copy "New API key value" k'] * 4, fmt="json")
    agent, br, store = _agent(site, model)
    st = ma.Subtask("t", "save the key into k", start="/noack", secrets_out=["k"],
                    ack=[r"copy this key now"], effect=ma.SecretCaptured(store, "k"))
    r = agent.run(st)
    assert br.reads == 1 and store.get("k") == SECRET
    assert r.outcome is ma.Outcome.NEEDS_USER and "repeating" in r.reason   # no ack -> no auto success
    skipped = [e for e in r.events if e.get("duplicate_of")]
    assert len(skipped) == 3 and all(e["duplicate_of"] == 1 for e in skipped)
    assert "already done at step 1; not repeated" in model.prompts[2]
    assert "SKIPPED" in model.prompts[3]
    assert SECRET not in "\n".join(model.prompts)


def test_duplicate_copy_with_ack_auto_completes_via_effect(site):
    model = ma.ScriptedModel(['copy "New API key value" k'] * 6, fmt="json")
    agent, br, store = _agent(site, model)
    st = ma.Subtask("t", "save the key into k", start="/acked", secrets_out=["k"],
                    ack=[r"copy this key now"], effect=ma.SecretCaptured(store, "k"))
    r = agent.run(st)
    assert r.outcome is ma.Outcome.VERIFIED and r.steps == 2 and br.reads == 1
    assert r.events[-1].get("auto_done") and r.events[-1]["effect"] == "secret k captured"
    assert "Your next reply must be done(" in model.prompts[1]


def test_auto_complete_still_requires_the_effect_check(site):
    model = ma.ScriptedModel(['copy "New API key value" k'] * 3, fmt="json")
    agent, br, store = _agent(site, model)
    st = ma.Subtask("t", "save the key into k", start="/acked", secrets_out=["k"],
                    ack=[r"copy this key now"], effect=lambda: ma.EffectResult(False, "not in the vault"))
    r = agent.run(st)
    assert r.outcome is ma.Outcome.UNVERIFIED and "effect check failed" in r.reason


def test_gbnf_grammar_drops_copy_after_capture(site):
    model = ma.ScriptedModel(['copy "New API key value" k', 'done "saved"'])
    agent, br, store = _agent(site, model)
    st = ma.Subtask("t", "save the key into k", start="/acked", secrets_out=["k"],
                    ack=[r"copy this key now"], effect=ma.SecretCaptured(store, "k"))
    r = agent.run(st)
    assert r.outcome is ma.Outcome.VERIFIED and r.steps == 2
    assert "copy_to_secret(" in model.grammars[0] and "copy_to_secret(" not in model.grammars[1]


def test_duplicate_fill_on_same_page_skipped_but_allowed_after_page_change(site):
    model = ma.ScriptedModel(['fill "Thing name" "alpha"', 'fill "Thing name" "alpha"',
                              'fill "Thing name" "beta"', 'click "Next"', 'click "Back"',
                              'fill "Thing name" "beta"', 'needs_user "stop"'])
    agent, br, store = _agent(site, model)
    r = agent.run(ma.Subtask("t", "fill the form", start="/form"))
    dups = [e for e in r.events if e.get("duplicate_of")]
    assert len(dups) == 1 and dups[0]["step"] == 2 and dups[0]["duplicate_of"] == 1
    assert "already done at step 1; not repeated" in model.prompts[2]
    assert r.outcome is ma.Outcome.NEEDS_USER and r.reason == "stop"       # the model's own stop


# --- the same loop over the fake Safari (live driver on fakes) --------------------------------

@pytest.mark.parametrize("variant", ["a", "b"])
def test_fake_safari_local_repeater_finishes_before_cap(tmp_path, variant, monkeypatch):
    import test_live_driver as tld
    from scaffold.agent.desktop.live import probe as P

    rep_models = []

    def fake_make_model(provider, script=None):
        m = RepeatingModel(P.DD_SCRIPTS[variant]["create_key"], repeat=5)
        rep_models.append(m)
        return Multi(ma.ScriptedModel(P.DD_SCRIPTS[variant]["sign_in"]), m)

    monkeypatch.setattr(ma, "make_model", fake_make_model)
    rt, saf = tld._fake_world()
    lines = []
    res = P.demo_dd(variant, 7, tmp_path / "dd", runtime=rt, vm_check=tld.VM, out=lines.append, settle=1.0,
                    provider="local")
    assert res["pass"], (res.get("reasons"), res.get("error"), lines[-30:])
    ck = res["subtasks"][1]
    assert ck["outcome"] == "verified" and ck["steps"] == len(P.DD_SCRIPTS[variant]["create_key"]) < 12
    assert rep_models and rep_models[0].repeats_sent == 0
    assert sum("copy_to_secret(" in ln for ln in lines if "] step " in ln) == 1
