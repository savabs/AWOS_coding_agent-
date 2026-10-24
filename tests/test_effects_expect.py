"""Expected-effects verification (effects/expect.py), its pack/run.py hook, and the
false-VERIFIED benchmark (scripts/verify_bench.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from scaffold.agent.desktop.effects.expect import (RUN_FAILED, RUN_UNVERIFIED, RUN_VERIFIED, Expect, Expectation,
                                                   Observation, check, induce, induce_files, pack_hook)
from scaffold.agent.desktop.effects.outcome import Outcome, Verdict
from scaffold.agent.desktop.pack import RunnerResult, Verdict as PackVerdict, VERIFIED
from scaffold.agent.desktop.pack import run as RUN
from scaffold.agent.desktop.pack.config import parse_config

ROOT = Path(__file__).resolve().parents[1]

BEFORE = {"home": ["row:D-301 Proposal", "row:D-302 Proposal", "row:D-303 Lead", "text:Deals"],
          "detail": ["field:status=Proposal", "field:owner=Ravi"]}
AFTER = {"home": ["row:D-301 Won", "row:D-302 Proposal", "row:D-303 Lead", "text:Deals"],
         "detail": ["field:status=Won", "field:owner=Ravi"]}


def obs(reads, **kw):
    kw.setdefault("alerts", [])
    kw.setdefault("statuses", ["D-301 marked as won."])
    kw.setdefault("url", "http://h/t/i1/home")
    kw.setdefault("text", "Deals D-301 Won")
    return Observation(reads={k: (list(v) if v is not None else None) for k, v in reads.items()}, **kw)


@pytest.fixture
def exp():
    return induce(obs(BEFORE, statuses=[]), obs(AFTER), kind="act")


def test_induced_items(exp):
    kinds = [e.kind for e in exp.items]
    assert exp.observable
    assert kinds.count("atom_delta") == 4 and kinds.count("read_unchanged") == 2
    assert "no_error" in kinds and "banner" in kinds and "url" in kinds
    assert not next(e for e in exp.items if e.kind == "banner").required
    assert Expectation.from_dict(exp.to_dict()).to_dict() == exp.to_dict()


def test_success_verified(exp):
    r = check(exp, obs(BEFORE), obs(AFTER))
    assert r.verdict == RUN_VERIFIED and r.outcome is Outcome.VERIFIED
    assert all(x.verdict is Verdict.CONFIRMED for x in r.results if x.expect.required)


def test_partial_fails_loudly(exp):
    part = dict(AFTER, detail=BEFORE["detail"])          # list updated, detail not saved
    r = check(exp, obs(BEFORE), obs(part))
    assert r.verdict == RUN_FAILED and r.reason.startswith("FAILED:")
    assert r.outcome is Outcome.RECONCILIATION_REQUIRED and r.refuted()


def test_duplicate_and_wrong_value(exp):
    dup = dict(AFTER, home=AFTER["home"] + ["row:D-301 Won"])
    r = check(exp, obs(BEFORE), obs(dup))
    assert r.verdict == RUN_FAILED and ("duplicated" in r.reason or "collateral" in r.reason)
    wrong = dict(AFTER, detail=["field:status=Lost", "field:owner=Ravi"])
    assert check(exp, obs(BEFORE), obs(wrong)).verdict == RUN_FAILED


def test_extra_deletion_fails(exp):
    gone = dict(AFTER, home=[x for x in AFTER["home"] if "D-303" not in x])
    r = check(exp, obs(BEFORE), obs(gone))
    assert r.verdict == RUN_FAILED and "collateral" in r.reason


def test_banner_without_effect_fails(exp):
    r = check(exp, obs(BEFORE), obs(BEFORE, statuses=["D-301 marked as won."]))
    assert r.verdict == RUN_FAILED
    assert any(x.expect.kind == "banner" and x.verdict is Verdict.CONFIRMED for x in r.results)


def test_missing_banner_or_other_url_is_advisory(exp):
    r = check(exp, obs(BEFORE), obs(AFTER, statuses=[], url="http://h/t/i9/help"))
    assert r.verdict == RUN_VERIFIED
    assert sum(x.verdict is Verdict.REFUTED for x in r.results) >= 1


def test_error_banner_fails(exp):
    assert check(exp, obs(BEFORE), obs(AFTER, alerts=["Save failed"])).verdict == RUN_FAILED


def test_missing_evidence_never_verified(exp):
    r = check(exp, obs(BEFORE), obs(dict(AFTER, detail=None)))
    assert r.verdict == RUN_UNVERIFIED and "missing evidence" in r.reason
    r = check(exp, obs(BEFORE), Observation(reads=dict(AFTER)))    # alerts not observed
    assert r.verdict == RUN_UNVERIFIED
    assert check(exp, obs({}), obs({})).verdict == RUN_UNVERIFIED


def test_unobservable_demo_never_verified():
    e = induce(obs(BEFORE), obs(BEFORE), kind="act")
    assert not e.observable
    assert check(e, obs(BEFORE), obs(BEFORE)).verdict == RUN_UNVERIFIED
    assert check(Expectation("act", []), obs({}), obs({})).verdict == RUN_UNVERIFIED


def test_judge_can_only_downgrade(exp):
    calls = []

    def yes(e, after, res):
        calls.append(res.verdict)
        return True

    assert check(exp, obs(BEFORE), obs(AFTER), judge=yes).verdict == RUN_VERIFIED
    # never consulted unless the deterministic verdict is VERIFIED -> cannot grant
    assert check(exp, obs(BEFORE), obs(BEFORE), judge=yes).verdict == RUN_FAILED
    assert check(exp, obs(BEFORE), obs(dict(AFTER, detail=None)), judge=yes).verdict == RUN_UNVERIFIED
    assert calls == [RUN_VERIFIED]
    r = check(exp, obs(BEFORE), obs(AFTER), judge=lambda *a: "screenshot shows an error")
    assert r.verdict == RUN_UNVERIFIED and r.judge["veto"] and "screenshot" in r.reason
    assert check(exp, obs(BEFORE), obs(AFTER), judge=lambda *a: False).verdict == RUN_UNVERIFIED

    def boom(*a):
        raise RuntimeError("model down")
    assert check(exp, obs(BEFORE), obs(AFTER), judge=boom).verdict == RUN_UNVERIFIED
    # a judge trying to "grant" (returning a VERIFIED-ish object) on a refuted run changes nothing
    assert check(exp, obs(BEFORE), obs(BEFORE), judge=lambda *a: "verified").verdict == RUN_FAILED


def test_reasons_carry_no_values():
    secret = "field:api_key=sk-THISISSECRET123"
    e = induce(obs({"s": []}), obs({"s": [secret]}))
    r = check(e, obs({"s": []}), obs({"s": []}))
    assert r.verdict == RUN_FAILED
    assert "THISISSECRET" not in str(r.to_record())


def test_answer_and_pause():
    a = induce(obs(BEFORE), obs(BEFORE, answer="The amount is $954.00", ended="done"), kind="answer",
               answer="$954.00")
    assert check(a, obs(BEFORE), obs(BEFORE, answer="it is $954.00", ended="done")).verdict == RUN_VERIFIED
    assert check(a, obs(BEFORE), obs(BEFORE, answer="not found", ended="done")).verdict == RUN_FAILED
    assert check(a, obs(BEFORE), obs(AFTER, answer="The amount is $954.00")).verdict == RUN_FAILED  # it wrote
    p = induce(obs(BEFORE), obs(BEFORE, ended="needs_user"), kind="pause")
    assert check(p, obs(BEFORE), obs(BEFORE, ended="needs_user")).verdict == RUN_VERIFIED
    assert check(p, obs(BEFORE), obs(BEFORE, ended="done")).verdict == RUN_FAILED


def test_files():
    e = induce_files([{"name": "acme_2026-09.pdf", "size": 40_000, "sha256": "a"}])
    f = lambda *fs: Observation(files=list(fs))  # noqa: E731
    good = {"name": "acme_2026-10.pdf", "size": 38_000, "sha256": "b"}
    assert check(e, Observation(), f(good)).verdict == RUN_VERIFIED
    assert check(e, Observation(), f()).verdict == RUN_FAILED
    assert check(e, Observation(), f(good, dict(good, name="x (1).pdf"))).verdict == RUN_FAILED
    assert check(e, Observation(), f(dict(good, name="page.html"))).verdict == RUN_FAILED
    assert check(e, Observation(), f(dict(good, size=900))).verdict == RUN_FAILED     # error page, too small
    assert check(e, Observation(), Observation(files=None)).verdict == RUN_UNVERIFIED
    assert not induce_files([]).observable
    with pytest.raises(ValueError):
        Expect("vibes")


# ── pack/run.py hook ────────────────────────────────────────────────────────
PDF = b"%PDF-1.4\n" + b"x" * 4000


def _cfg(tmp_path):
    dl = tmp_path / "Downloads"
    dl.mkdir()
    return parse_config({"pack": {"root": str(tmp_path / "Pack"), "downloads": str(dl), "settle_s": 0, "wait_s": 0},
                         "source": [{"id": "acme", "name": "Acme SaaS", "kind": "invoice",
                                     "start_url": "https://billing.acme.com/invoices",
                                     "allowed_domains": ["billing.acme.com"], "skill": "acme_invoice"}]})


def _run(cfg, files, **kw):
    def runner(source, period, ctx):
        for name, data in files:
            (ctx.downloads / name).write_bytes(data)
        return RunnerResult()
    t = [0.0]
    return RUN.run_month(cfg, "2026-10", runner=runner, check=lambda *a: PackVerdict(VERIFIED),
                         sleep=lambda s: None, clock=lambda: t[0], **kw)["sources"]["acme"]


def test_pack_hook_confirms(tmp_path):
    hook = pack_hook({"acme": induce_files([{"name": "inv.pdf", "size": 4009, "sha256": "x"}])})
    e = _run(_cfg(tmp_path), [("inv.pdf", PDF)], expect=hook)
    assert e["status"] == RUN.COLLECTED_VERIFIED and e["expect"]["verdict"] == RUN_VERIFIED


def test_pack_hook_refutes_and_downgrades(tmp_path):
    hook = pack_hook({"acme": induce_files([{"name": "inv.pdf", "size": 400_000, "sha256": "x"}])})
    e = _run(_cfg(tmp_path), [("inv.pdf", PDF)], expect=hook)        # 4 KB vs taught 400 KB
    assert e["status"] == RUN.FAILED and "FAILED" in e["reason"]


def test_pack_hook_no_opinion_and_errors(tmp_path):
    e = _run(_cfg(tmp_path), [("inv.pdf", PDF)], expect=pack_hook({}))
    assert e["status"] == RUN.COLLECTED_VERIFIED and "expect" not in e
    (tmp_path / "b").mkdir()

    def broken(*a):
        raise RuntimeError("x")
    e = _run(_cfg(tmp_path / "b"), [("inv.pdf", PDF)], expect=broken)
    assert e["status"] == RUN.COLLECTED_UNVERIFIED


def test_pack_hook_never_upgrades(tmp_path):
    entry = {"status": RUN.COLLECTED_UNVERIFIED, "files": []}
    RUN.expect_gate(entry, None, None, lambda *a: check(Expectation("files", [Expect("files_distinct")]),
                                                        Observation(), Observation(files=[])))
    assert entry["status"] == RUN.COLLECTED_UNVERIFIED
    entry = {"status": RUN.MISSING, "files": []}
    RUN.expect_gate(entry, None, None, lambda *a: pytest.fail("not called for non-collected entries"))
    assert entry["status"] == RUN.MISSING


def test_pack_hook_ignores_flagged_files():
    """Review finding: a flagged extra file (e.g. last month's statement) must not refute a correct month."""
    hook = pack_hook({"acme": induce_files([{"name": "stmt.pdf", "size": 50_000, "sha256": "a"}])})
    good = {"path": "/p/stmt_2026-10.pdf", "size": 48_000, "sha256": "b", "type_ok": True}
    stray = dict(good, path="/p/stmt_2026-09.pdf", sha256="c", flagged=True)
    entry = {"status": RUN.COLLECTED_VERIFIED, "files": [good, stray]}
    RUN.expect_gate(entry, "acme", None, hook)
    assert entry["status"] == RUN.COLLECTED_VERIFIED and entry["expect"]["verdict"] == RUN_VERIFIED
    entry = {"status": RUN.COLLECTED_VERIFIED, "files": [dict(good, type_ok=False)]}   # no good file left
    RUN.expect_gate(entry, "acme", None, hook)
    assert entry["status"] == RUN.FAILED


def test_pack_hook_accepts_more_good_files_but_not_duplicates():
    """The pack accepts >= expected.count good files; the hook matches (duplicates still FAIL)."""
    hook = pack_hook({"acme": induce_files([{"name": "inv.pdf", "size": 40_000, "sha256": "a"}])})
    f = {"path": "/p/inv1.pdf", "size": 40_000, "sha256": "b", "type_ok": True}
    entry = {"status": RUN.COLLECTED_VERIFIED, "files": [f, dict(f, path="/p/inv2.pdf", sha256="c")]}
    RUN.expect_gate(entry, "acme", None, hook)
    assert entry["status"] == RUN.COLLECTED_VERIFIED
    entry = {"status": RUN.COLLECTED_VERIFIED, "files": [f, dict(f, path="/p/inv1 (1).pdf")]}
    RUN.expect_gate(entry, "acme", None, hook)
    assert entry["status"] == RUN.FAILED
    exact = induce_files([{"name": "inv.pdf", "size": 40_000, "sha256": "a"}])      # direct check stays exact
    two = Observation(files=[{"name": "a.pdf", "size": 40_000, "sha256": "b"}, {"name": "b.pdf", "size": 40_000,
                                                                                "sha256": "c"}])
    assert check(exact, Observation(), two).verdict == RUN_FAILED


def test_pack_without_hook_unchanged(tmp_path):
    e = _run(_cfg(tmp_path), [("inv.pdf", PDF)])
    assert e["status"] == RUN.COLLECTED_VERIFIED and "expect" not in e


# ── benchmark ───────────────────────────────────────────────────────────────
def _bench():
    spec = importlib.util.spec_from_file_location("verify_bench", ROOT / "scripts" / "verify_bench.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bench_exam_integrity():
    """The benchmark never touches the frozen factory ood split (or MiniWoB)."""
    vb = _bench()
    from scaffold.rl.factory.app import FAMILIES
    ood_fams = {n for n, f in FAMILIES.items() if f.ood}
    assert ood_fams                                          # wizard, files_tree, calendar
    cat = vb.Catalog(["records"] + sorted(ood_fams))
    assert cat.tasks("ood")
    sel = vb.select_entries(cat)
    assert sel and all(e.task.split == "train" for e in sel)
    assert not ({e.task.family for e in sel} & ood_fams)
    assert not [e for e in sel if e.task.id in {t.id for t in cat.tasks("ood")}]
    for fam in ood_fams:
        assert vb.select_entries(cat, family=fam) == []
    assert vb.bench(family="wizard")["tasks"] == 0
    assert "miniwob" not in (ROOT / "scripts" / "verify_bench.py").read_text().split('"""', 2)[2].lower()


def test_bench_small_run():
    vb = _bench()
    rep = vb.bench(family="records", limit=12)
    assert rep["not_training_data"] and rep["split"] == "train"
    assert rep["cases_scored"] > 30 and not rep["demo_errors"]
    assert rep["false_verified_rate"] == 0.0
    assert rep["true_verified_rate"] >= 0.8
    j = vb.bench(family="records", limit=12, judge=vb.banner_judge)
    # the judge can only take VERIFIED stamps away
    assert j["confusion"]["success"]["verified"] <= rep["confusion"]["success"]["verified"]
    assert j["confusion"]["fail"]["verified"] <= rep["confusion"]["fail"]["verified"]
