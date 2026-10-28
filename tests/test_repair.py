"""repair/: tier-1 similarity re-find, model tier (mock + local picker without network), safety gate,
page mutations with ground truth, and the benchmark's exam-integrity guard. No model, no network."""
import io
import json
import random
import sys
from pathlib import Path

import pytest

from scaffold.agent.desktop.repair import (Fingerprint, LocalPicker, Pick, Repairer, greedy_picker, oracle_picker,
                                           risk_of)
from scaffold.agent.desktop.repair import mutate as M
from scaffold.agent.desktop.repair import similo as S
from scaffold.agent.desktop.repair.model import parse_reply, prompt
from scaffold.rl.factory.htmldriver import parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import repair_bench as RB  # noqa: E402

ROWS = "".join(
    f'<tr><td>D-30{i}</td><td>{co}</td><td><button type="submit" name="__ctl" value="done_30{i}" title="Mark D-30{i} '
    f'as won"><span aria-hidden="true">v</span></button><button type="submit" name="__ctl" value="arch_30{i}">'
    f'Archive D-30{i}</button></td></tr>' for i, co in ((1, "Lumen"), (2, "Ironbark"), (3, "Northwind")))
PAGE = ('<html><head><title>Deals</title></head><body><form id="appform" method="post" action="/act"><nav '
        'aria-label="Main"><a href="/home">Deals</a> <a href="/help">Help</a></nav><main><section role="region" '
        'aria-label="Deals"><h1>Deals</h1><div class="field"><label for="q">Search deals</label><input id="q" '
        f'name="q"></div><button type="submit" name="__ctl" value="go">Search</button><table>{ROWS}</table>'
        '</section></main></form></body></html>')


def idx(view, name):
    return next(i for i, c in enumerate(view.controls) if c.name == name)


def fp_of(name, html=PAGE):
    v = parse(html)
    return M.ctl_fingerprint(v, idx(v, name))


# ------------------------------------------------------------------ similo --

def test_identity_tokens_cap_a_neighbouring_row():
    assert S.name_sim("Mark D-302 as won", "Mark D-303 as won") <= S.ID_CAP
    assert S.name_sim("Mark D-302 as won", "Mark D-302 as won now") > 0.7
    assert S.token_sim("Victor Kaya", "Victor Lee") < 0.5          # another person, not a 60% match


def test_unchanged_page_accepts_the_same_control():
    fp = fp_of("Mark D-302 as won")
    v = parse(PAGE)
    d = S.decide(S.rank(fp, M.view_candidates(v)), fp)
    assert d.kind == "accept" and d.best.name == "Mark D-302 as won" and d.margin >= S.MARGIN


def test_deleted_row_is_not_found_not_the_neighbour():
    fp = fp_of("Mark D-302 as won")
    gone = PAGE.replace('title="Mark D-302 as won"', 'title="x"').replace("<td>D-302</td>", "<td></td>")
    gone = parse(gone.replace('title="x"', 'hidden title="x"'))
    res = Repairer().refind(fp, M.view_candidates(gone))
    assert res.status in ("not_found", "ambiguous") and not res.ok


def test_name_only_fingerprint_from_an_old_skill():
    fp = Fingerprint(role="button", name="Search")
    v = parse(PAGE.replace(">Search</button>", ">Search ›</button>"))
    res = Repairer().refind(fp, M.view_candidates(v))
    assert res.ok and res.candidate.name == "Search ›" and res.tier == "rule"


def test_roles_are_gated():
    fp = Fingerprint(role="textbox", name="Search deals")
    v = parse(PAGE.replace('<label for="q">Search deals</label><input id="q" name="q">', ""))
    res = Repairer().refind(fp, M.view_candidates(v))          # the "Search" button is not a text field
    assert res.status == "not_found"


def test_fingerprint_round_trips():
    fp = fp_of("Archive D-301")
    back = Fingerprint.from_dict(json.loads(json.dumps(fp.to_dict())))
    assert back == fp and back.ancestors == fp.ancestors


# ------------------------------------------------------------ model tier --

def test_ambiguous_goes_to_the_picker_with_at_most_5():
    fp = Fingerprint(role="button", name="Open")
    v = parse("<body>" + "".join(f'<p>Row {i} <button>Open</button></p>' for i in range(8)) + "</body>")
    seen = {}

    def picker(f, short):
        seen["n"] = len(short)
        return Pick(None, "none of these", calls=1)
    rp = Repairer(picker)
    res = rp.refind(fp, M.view_candidates(v))
    assert seen["n"] <= 5 and res.status == "not_found" and res.tier == "model" and rp.model_calls == 1


def test_model_pick_is_validated_and_recorded():
    fp = Fingerprint(role="button", name="Open")
    v = parse("<body><p>a <button>Open</button></p><p>b <button>Open</button></p></body>")
    cands = M.view_candidates(v)
    res = Repairer(oracle_picker(lambda f: cands[1])).refind(fp, cands)
    assert res.ok and res.tier == "model" and res.candidate is cands[1]
    bad = Repairer(lambda f, s: Pick(7, "out of range", calls=1)).refind(fp, cands)
    assert bad.status == "not_found"
    boom = Repairer(lambda f, s: 1 / 0).refind(fp, cands)
    assert boom.status == "not_found"


def test_parse_reply_grammar():
    assert parse_reply("PICK 2: same row", 3).choice == 1
    assert parse_reply("NOT_FOUND: deleted", 3).choice is None
    assert parse_reply("PICK 4: x", 3).choice is None
    assert parse_reply("I think the second one", 3).choice is None
    assert parse_reply("", 3).choice is None


def test_local_picker_is_loopback_only_and_mockable():
    with pytest.raises(ValueError):
        LocalPicker(url="https://api.example.com/v1/chat/completions")
    sent = {}

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        sent["body"] = json.loads(req.data)
        return R(json.dumps({"choices": [{"message": {"content": "PICK 1: same control"}}]}).encode())
    lp = LocalPicker(opener=opener)
    fp = Fingerprint(role="button", name="Open")
    p = lp(fp, [Fingerprint(role="button", name="View"), Fingerprint(role="button", name="Open all")])
    assert p.choice == 0 and p.calls == 1 and "grammar" in sent["body"] and sent["body"]["temperature"] == 0
    dead = LocalPicker(opener=lambda req, timeout: (_ for _ in ()).throw(OSError("refused")))
    assert dead(fp, [fp]).choice is None                        # no server = abstain, never a guess


def test_prompt_lists_numbered_candidates():
    txt = prompt(Fingerprint(role="button", name="Open", context="Deals"),
                 [Fingerprint(role="button", name="View"), Fingerprint(role="link", name="Open file")])
    assert "1. button named 'View'" in txt and "2. link named 'Open file'" in txt and "NOT_FOUND" in txt


# ------------------------------------------------------------- safety gate --

@pytest.mark.parametrize("name", ["Delete invoice", "Pay now", "Send message", "Submit payment", "Purchase",
                                  "Transfer funds", "Sign out", "Place order"])
def test_never_repairs_onto_a_risky_control_without_approval(name):
    fp = Fingerprint(role="button", name="Invoice")
    v = parse(f"<body><button>Invoice {name}</button></body>")
    cands = M.view_candidates(v)
    res = Repairer(greedy_picker()).refind(fp, cands)
    assert risk_of(cands[0]) and res.status == "needs_approval" and not res.ok
    asked = []
    ok = Repairer(greedy_picker(), approve=lambda r: asked.append(r) or True).refind(fp, cands)
    assert ok.ok and asked and asked[0]["action"] == "repair_onto_risky_control"


def test_risk_reads_context_too():
    assert risk_of(Fingerprint(role="button", name="OK", context="Delete account"))
    assert not risk_of(Fingerprint(role="button", name="Download PDF", context="Invoices"))


# --------------------------------------------------------------- mutations --

@pytest.mark.parametrize("kind", M.MUTATIONS)
def test_mutations_keep_exact_ground_truth(kind):
    v = parse(PAGE)
    i = idx(v, "Mark D-302 as won")
    got = None
    for seed in range(6):
        got = M.mutate(PAGE, i, kind, random.Random(seed))
        if got is not None:
            break
    assert got is not None, kind
    if kind.startswith("delete"):
        assert got.gt is None and "Mark D-302 as won" not in [c.name for c in got.view.controls]
    else:
        assert got.gt is not None
        c = got.view.controls[got.gt]
        if kind == "rename":
            assert c.name != "Mark D-302 as won" and "D-302" in c.name
        if kind == "role":
            assert c.role == "link"
        if kind == "decoy":
            assert len(got.view.controls) == len(v.controls) + 1
    assert M.GT not in json.dumps(M.ctl_fingerprint(got.view, 0).to_dict())     # the tag is no feature


def test_role_mutation_does_not_apply_to_fields():
    v = parse(PAGE)
    assert M.mutate(PAGE, idx(v, "Search deals"), "role", random.Random(0)) is None


def test_decoy_names_are_another_thing_not_a_qualifier():
    rng = random.Random(1)
    assert M.decoy_name("Mark D-305 as won", rng) != "Mark D-305 as won"
    assert "D-305" not in M.decoy_name("Mark D-305 as won", random.Random(2))
    d = M.decoy_name("Victor Kaya", random.Random(0), ["Victor Kaya", "Jonas Lee"])
    assert d != "Victor Kaya" and not d.startswith("Victor Kaya ")


# ------------------------------------------------------- bench + integrity --

def test_bench_refuses_exam_families_and_never_renders_them():
    exam = RB.ood_families()
    assert exam == set(RB.EXAM_FAMILIES)
    assert not (set(RB.DEV) | set(RB.TEST)) & exam
    for fam in exam:
        with pytest.raises(SystemExit):
            RB.collect([fam])
    cases = RB.collect(["records"], max_tasks=6)
    assert cases and all(c["split"] == "train" and c["family"] not in exam for c in cases)


def test_bench_does_not_touch_miniwob():
    src = (ROOT / "scripts" / "repair_bench.py").read_text()
    assert "miniwob" not in src.split('"""', 2)[2].lower()      # only the docstring mentions it


def test_bench_small_run_meets_the_wrong_repair_target():
    cases = RB.collect(["records"], max_tasks=8)
    summ = RB.summarize(RB.run(cases))
    assert summ["rule"]["ALL"]["wrong"] <= 0.02
    assert summ["rule"]["delete"]["abstain_correct"] >= 0.9
    assert summ["rule"]["move"]["accuracy"] >= 0.9
    assert summ["rule+greedy"]["ALL"]["wrong"] > summ["rule"]["ALL"]["wrong"]     # the bench sees false heals
    assert "| rule |" in RB.table(summ)


def test_bench_dry_run_costs_nothing():
    out = RB.dry_run(RB.collect(["records"], max_tasks=3), max_calls=10)
    assert out["cost_usd"] == 0.0 and out["would_call"] <= 10
