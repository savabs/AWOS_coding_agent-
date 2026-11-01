"""AWOS Replay 0.8 phone link + fewer pings + failure evidence (docs/specs/phone_link.md), against the fake relay in
conftest's server (in memory, same JSON as the real relay) and a fake ntfy (/ntfy/<topic>):

  - pairing: the register call carries sha256(smsToken); the pair link carries channel, key, token, topic
  - a 2FA handoff sends ONE encrypted `need` (decrypted here with the key from the link; the ciphertext does not
    contain the portal name) and ONE generic ntfy push; the phone's `code` is typed + submitted, the run VERIFIED,
    `resolved` and a `status` follow; the handled message is deleted; the code is in no log, state or export
  - a forwarded SMS "Your verification code is 482915" is used and deleted; an SMS older than the need never is
  - approve no -> the denied path (REJECTED); relay 503 -> the normal handoff still works and the popup says so
  - batching: two keep-alive sweeps with logouts -> no notice, no push; ONE at pre-flight
  - slow-run alert; a screenshot is stored (locally only) when a step fails
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q
"""
import hashlib
import json
import time
import urllib.parse

import pytest

from conftest import relay_box

pytestmark = pytest.mark.timeout(180)
CODE = "482915"


def _wait(fn, timeout=30, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("timed out waiting for " + what)


def _fast(x, **kw):
    s = dict(phonePollMs=300, phonePushGapMs=0, keepAliveSpacingMs=100, preflightSpacingMs=0, loginPollMs=300,
             loginProbeMs=800, collectionWaitMin=2)
    s.update(kw)
    x.set_settings(**s)


def _pair(x):
    st = x.call("phone_pair", base=x.server.base, ntfyBase=x.server.base + "/ntfy")
    frag = urllib.parse.parse_qs(st["link"].split("#", 1)[1])
    keys = {k: v[0] for k, v in frag.items()}
    assert st["link"].startswith(x.server.base + "/phone/#c="), st["link"]
    return st, keys


def _crypto_page(x):
    """an independent WebCrypto implementation of the envelope (not the extension's code)."""
    p = x.ctx.new_page()
    p.goto(x.server.base + "/help.html")
    p.evaluate("""() => {
      const u = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4)), c => c.charCodeAt(0));
      const b = a => btoa(String.fromCharCode(...new Uint8Array(a))).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
      const key = k => crypto.subtle.importKey('raw', u(k), 'AES-GCM', false, ['encrypt', 'decrypt']);
      window.__open = async (k, dir, env) => JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt(
        {name: 'AES-GCM', iv: u(env.iv), additionalData: new TextEncoder().encode(dir)}, await key(k), u(env.ct))));
      window.__seal = async (k, dir, obj) => { const iv = crypto.getRandomValues(new Uint8Array(12));
        const ct = await crypto.subtle.encrypt({name: 'AES-GCM', iv, additionalData: new TextEncoder().encode(dir)}, await key(k),
                                               new TextEncoder().encode(JSON.stringify(obj)));
        return {id: b(crypto.getRandomValues(new Uint8Array(16))), iv: b(iv), ct: b(ct)}; };
    }""")
    return p


def _opened(cp, keys, env, direction="to-phone"):
    return cp.evaluate("([k, d, e]) => window.__open(k, d, e)", [keys["k"], direction, env])


def _phone_says(x, cp, keys, obj):
    env = cp.evaluate("([k, o]) => window.__seal(k, 'to-mac', o)", [keys["k"], obj])
    relay_box(x.server, keys["c"], "to-mac").append(dict(env, at=int(time.time() * 1000)))
    return env


def _record_supplier(x, name="Acme Supplier Portal"):
    page = x.ctx.new_page()
    page.goto(x.server.base + "/portal/supplier.html")
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name=name)
    with page.expect_download():
        page.click("#sup")
    time.sleep(0.8)
    rec = x.call("stop_recording")
    x.call("save_recording", id=rec["id"], expect={"urlPattern": "", "texts": []},
           expectDownload={"nameRegex": r"supplier-statement-{{year}}-{{month}}\.pdf$"})
    return page, tab, rec


def _needs(x, cp, keys):
    return [o for o in (_opened(cp, keys, e) for e in relay_box(x.server, keys["c"], "to-phone")) if o["type"] == "need"]


def _everything(x):
    """every place a code could leak to: run state, storage (local + session), the shareable export."""
    blob = x.sw.evaluate("Promise.all([chrome.storage.local.get(null), chrome.storage.session.get(null)])")
    return json.dumps([x.state(), blob, x.call("export_log")])


def _start_otp_run(x, name="Acme Supplier Portal"):
    x.ctx.clear_cookies()
    page, tab, rec = _record_supplier(x, name)
    page.goto(x.server.base + "/help.html")
    x.server.auth["/portal/supplier.html"] = "/portal/otp_phone.html"     # the portal asks for a code before the files
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run(statuses=("needs_user", "done", "failed"))
    assert run["status"] == "needs_user" and run["pending"]["handoff"] == "otp", run["log"]
    return page, tab, rec, run


# ---- pairing ------------------------------------------------------------------------------------------------------

def test_pairing_registers_token_hash_and_unpair_wipes(x):
    st, keys = _pair(x)
    assert set(keys) == {"c", "k", "t", "n"} and keys["n"].startswith("awos-") and len(keys["n"]) == 21
    assert len(keys["c"]) == 22 and len(keys["k"]) == 43 and len(keys["t"]) == 22
    assert x.server.relay["registered"] == {keys["c"]: hashlib.sha256(keys["t"].encode()).hexdigest()}
    assert st["register"]["ok"] and x.state()["phone"]["paired"]
    # the Options page shows the link as a QR code (made in the extension) and a copy button
    opt = x.ctx.new_page()
    opt.goto(f"chrome-extension://{x.id}/options.html")
    opt.wait_for_selector("#phoneqr svg")
    assert opt.input_value("#phonelink") == st["link"]
    assert "<script" not in opt.inner_html("#phoneqr")
    assert x.call("phone_unpair") == {"paired": False}
    assert not x.state()["phone"]["paired"] and not x.sw.evaluate("chrome.storage.local.get('phone').then(o => o.phone || null)")


# ---- a code from the phone ----------------------------------------------------------------------------------------

def test_2fa_handoff_sends_encrypted_need_and_phone_code_finishes_the_run(x):
    _fast(x)
    st, keys = _pair(x)
    cp = _crypto_page(x)
    page, tab, rec, run = _start_otp_run(x)
    need = _wait(lambda: _needs(x, cp, keys), what="a need on the relay")
    assert len(need) == 1, need
    n = need[0]
    assert n["kind"] == "code" and n["portal"] == "Acme Supplier Portal" and n["req"] and n["at"] and "one-time code" in n["message"], n
    raw = json.dumps(relay_box(x.server, keys["c"], "to-phone"))
    assert "Acme" in json.dumps(n) and "Acme" not in raw and "Supplier" not in raw            # the relay sees ciphertext only
    pushes = _wait(lambda: x.server.ntfy, what="the push")
    assert len(pushes) == 1 and pushes[0]["topic"] == keys["n"], pushes
    assert pushes[0]["body"] == "AWOS needs you: open the AWOS phone page"
    assert pushes[0]["headers"]["click"] == x.server.base + "/phone/" and pushes[0]["headers"]["title"]
    # the phone: seen, then the code
    _phone_says(x, cp, keys, {"type": "seen", "req": n["req"]})
    env = _phone_says(x, cp, keys, {"type": "code", "req": n["req"], "code": CODE})
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    texts = [l["text"] for l in run["log"]]
    assert any("typed the code from your phone" in t and "not logged" in t for t in texts), texts
    assert any("opened this on your phone" in t for t in texts), texts
    assert ("to-mac", env["id"]) in x.server.relay["deleted"] and not relay_box(x.server, keys["c"], "to-mac")
    out = [_opened(cp, keys, e) for e in relay_box(x.server, keys["c"], "to-phone")]
    assert [o["type"] for o in out] == ["need", "resolved", "status"], out
    assert out[1]["req"] == n["req"]
    assert out[2]["items"] == [{"portal": "Acme Supplier Portal", "state": "VERIFIED", "detail": ""}] and out[2]["at"], out[2]
    assert len(x.server.ntfy) == 1                                     # a good result does not ping
    blob = _everything(x)
    assert CODE not in blob                                            # never in logs, state, storage or the export
    for p in x.server.ntfy:
        assert CODE not in json.dumps(p) and "Acme" not in json.dumps(p) and "Supplier" not in json.dumps(p)


def test_forwarded_sms_code_is_used_and_an_older_sms_never(x):
    _fast(x)
    st, keys = _pair(x)
    cp = _crypto_page(x)
    x.server.relay["ignore_since"] = True              # the relay returns everything: the extension itself must filter
    sms = relay_box(x.server, keys["c"], "sms")
    sms.append({"at": int(time.time() * 1000) - 120000, "id": "old1", "sms": "Your verification code is 111111"})
    page, tab, rec, run = _start_otp_run(x)
    n = _wait(lambda: _needs(x, cp, keys), what="a need")[0]
    time.sleep(1.5)                                    # several polls: the old SMS is not used
    assert x.state()["run"]["status"] == "needs_user" and page.evaluate("window.__tries") == 0
    sms.append({"at": int(time.time() * 1000), "id": "new1", "sms": "Acme: Your verification code is " + CODE + ". Do not share it."})
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert any("typed the code from a forwarded SMS" in l["text"] for l in run["log"]), run["log"]
    assert ("sms", "new1") in x.server.relay["deleted"] and ("sms", "old1") in x.server.relay["deleted"]   # read = deleted, used or not
    assert CODE not in _everything(x) and "111111" not in _everything(x)


def test_sms_code_extraction_rules(x):
    out = x.sw.evaluate("""() => [
      AWOSPhone.extractCode('Your verification code is 482915'),
      AWOSPhone.extractCode('G-482915 is your Google verification code.'),
      AWOSPhone.extractCode('Use 7731 as your one-time passcode'),
      AWOSPhone.extractCode('Your login code: AB12CD'),
      AWOSPhone.extractCode('Lunch at 1230 tomorrow?'),
      AWOSPhone.extractCode('Please confirm your account'),
      AWOSPhone.smsCode([{at: 5, id: 'a', sms: 'code 111111'}, {at: 20, id: 'b', sms: 'code 222222'}, {at: 30, id: 'c', sms: 'hi'}], 10),
      AWOSPhone.smsCode([{at: 5, id: 'a', sms: 'code 111111'}], 10),
    ]""")
    assert out[:6] == ["482915", "482915", "7731", "AB12CD", "", ""], out
    assert out[6] == {"code": "222222", "id": "b", "at": 20} and out[7] is None, out


# ---- approvals --------------------------------------------------------------------------------------------------

def test_phone_approve_no_takes_the_denied_path(x):
    _fast(x)
    st, keys = _pair(x)
    cp = _crypto_page(x)
    page = x.ctx.new_page()
    page.goto(x.server.base + "/cart.html")
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name="Pay the supplier")
    page.click("#pay")
    time.sleep(0.6)
    rec = x.call("stop_recording")
    page.reload()
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run(statuses=("awaiting_approval", "done", "failed"))
    assert run["status"] == "awaiting_approval", run["log"]
    n = _wait(lambda: _needs(x, cp, keys), what="an approval need")[0]
    assert n["kind"] == "approve" and "Pay now" in n["message"], n
    _phone_says(x, cp, keys, {"type": "approve", "req": n["req"], "yes": False})
    run = x.wait_run()
    assert run["result"]["code"] == "REJECTED" and page.inner_text("#status") == "Not paid", run["log"]
    assert any("declined this on your phone" in l["text"] for l in run["log"])
    out = [_opened(cp, keys, e)["type"] for e in relay_box(x.server, keys["c"], "to-phone")]
    assert out == ["need", "resolved", "status"], out
    assert len(x.server.ntfy) == 2                     # the need, and the run's failed status


def test_phone_approve_yes_clicks(x):
    _fast(x)
    st, keys = _pair(x)
    cp = _crypto_page(x)
    page = x.ctx.new_page()
    page.goto(x.server.base + "/cart.html")
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name="Pay the supplier")
    page.click("#pay")
    time.sleep(0.6)
    rec = x.call("stop_recording")
    page.reload()
    x.call("run", recId=rec["id"], tabId=tab)
    n = _wait(lambda: _needs(x, cp, keys), what="an approval need")[0]
    _phone_says(x, cp, keys, {"type": "approve", "req": n["req"], "yes": True})
    run = x.wait_run()
    assert run["status"] == "done" and page.inner_text("#status").startswith("Payment sent"), run["log"]


# ---- relay down -------------------------------------------------------------------------------------------------

def test_relay_503_keeps_the_normal_handoff(x):
    _fast(x)
    x.server.relay_status = 503
    st = x.call("phone_pair", base=x.server.base, ntfyBase=x.server.base + "/ntfy")
    assert st["paired"] and st["register"]["status"] == 503 and not st["register"]["ok"]
    page, tab, rec, run = _start_otp_run(x)
    run = _wait(lambda: (lambda r: r if any("could not send" in l["text"] for l in r["log"]) else None)(x.state()["run"]), what="the 503 log")
    assert any("503" in l["text"] for l in run["log"])
    assert not x.server.ntfy
    popup = x.popup(tab)
    popup.wait_for_selector("#phoneline:not([hidden])")
    assert "not configured" in popup.inner_text("#phoneline")
    page.fill("#code", CODE)                           # the user does it at the Mac, as before
    page.click("text=Verify")
    page.wait_for_url("**/portal/supplier.html")
    x.call("continue", runId=run["id"], nonce=run["pending"]["nonce"])
    assert x.wait_run()["result"]["verdict"] == "VERIFIED"


# ---- batching ---------------------------------------------------------------------------------------------------

def test_keep_alive_logouts_are_batched_into_one_preflight_notice(x):
    x.ctx.clear_cookies()
    _fast(x)
    st, keys = _pair(x)
    cp = _crypto_page(x)
    page, tab, rec = _record_supplier(x, "Acme Supplier Portal")
    col = x.call("coll_save", name="Month-end", recIds=[rec["id"]])
    x.server.auth["/portal/supplier.html"] = "/portal/login.html"
    for _ in range(2):
        out = x.sw.evaluate("(id) => keepAliveSweep(id)", col["id"])
        assert out["results"][0]["status"] == "login" and out["notified"] == [], out
    assert not [n for n in x.sw.evaluate("NOTICES") if n["id"].startswith("awos-keep-")]
    assert not x.server.ntfy and not relay_box(x.server, keys["c"], "to-phone")
    assert x.state()["portalStatus"][rec["id"]]["status"] == "login"          # shown in the checklist
    out = x.sw.evaluate("(id) => firePreflight(id)", col["id"])
    pre = [n for n in x.sw.evaluate("NOTICES") if n["id"].startswith("awos-pre-")]
    assert len(pre) == 1 and pre[0]["message"].startswith("1 portal needs a login") and len(x.server.ntfy) == 1, (pre, x.server.ntfy)
    msgs = [_opened(cp, keys, e) for e in relay_box(x.server, keys["c"], "to-phone")]
    assert len(msgs) == 1 and msgs[0]["type"] == "status" and msgs[0]["items"][0]["state"] == "login needed", msgs
    # the option restores immediate notices
    _fast(x, immediateNotices=True)
    x.sw.evaluate("chrome.storage.local.get('portalStatus').then(o => { for (const k in o.portalStatus) o.portalStatus[k].notified = false; return chrome.storage.local.set(o); })")
    x.sw.evaluate("(id) => keepAliveSweep(id)", col["id"])
    assert len([n for n in x.sw.evaluate("NOTICES") if n["id"].startswith("awos-keep-")]) == 1
    x.ctx.clear_cookies()


# ---- slow runs, screenshots -------------------------------------------------------------------------------------

def test_slow_run_is_flagged_in_verdict_and_phone_status(x):
    _fast(x, slowRunExtraMs=0)
    st, keys = _pair(x)
    cp = _crypto_page(x)
    x.ctx.clear_cookies()
    page, tab, rec = _record_supplier(x)
    x.sw.evaluate("(id) => chrome.storage.local.get('recordings').then(o => { o.recordings[id].durations = [40, 50, 60]; return chrome.storage.local.set(o); })", rec["id"])
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED" and run["result"]["slow"]["median"] == 50, run["result"]
    assert "slower than usual" in run["result"]["reason"] and any("slower than usual" in l["text"] for l in run["log"])
    status = [_opened(cp, keys, e) for e in relay_box(x.server, keys["c"], "to-phone")][-1]
    assert status["type"] == "status" and "slower than usual" in status["summary"] and "slower than usual" in status["items"][0]["detail"]
    durs = x.state()["recordings"][rec["id"]]["durations"]
    assert len(durs) == 4 and durs[-1] == run["result"]["slow"]["ms"]
    # an ordinary run is not flagged (the median is now the fast past runs plus this slow one)
    x.sw.evaluate("(id) => chrome.storage.local.get('recordings').then(o => { o.recordings[id].durations = [60000, 60000, 60000]; return chrome.storage.local.set(o); })", rec["id"])
    x.call("run", recId=rec["id"], tabId=tab)
    assert "slow" not in x.wait_run()["result"]


def test_screenshot_is_stored_locally_when_a_step_fails(x):
    _fast(x)
    st, keys = _pair(x)
    page = x.ctx.new_page()
    page.goto(x.server.base + "/form.html")
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name="Form")
    page.click("button")
    time.sleep(0.6)
    rec = x.call("stop_recording")
    x.server.overrides["/form.html"] = "form_deleted.html"
    x.set_settings(phonePollMs=300, enabled=False)
    page.bring_to_front()
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run()
    assert run["result"]["verdict"] == "FAILED", run["log"]
    shot = run.get("shot") or {}
    assert shot.get("id"), (shot, [l["text"] for l in run["log"]][-4:])
    s = x.call("get_shot", id=shot["id"])
    assert s["dataUrl"].startswith("data:image/jpeg;base64,") and s["runId"] == run["id"]
    assert [m["id"] for m in x.state()["shots"]] == [shot["id"]] and "dataUrl" not in x.state()["shots"][0]
    assert any("saved on this computer only" in l["text"] for l in run["log"])
    assert "data:image" not in json.dumps(x.server.relay)                    # never sent to the phone / relay
    popup = x.popup(tab)
    popup.wait_for_selector("#shotlink:not([hidden])")
    viewer = x.ctx.new_page()
    viewer.goto(f"chrome-extension://{x.id}/shot.html?id={shot['id']}")
    viewer.wait_for_selector("img#shot[src^='data:image/jpeg']")
