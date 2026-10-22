"""Month-end document pack core: config, files, run (resume/idempotence), report, zip, CLI.

Everything runs on temp dirs with fake runners: no browser, no model, no network."""
from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from scaffold.agent.desktop.pack import (MISMATCH, SUSPICIOUS, UNVERIFIED, VERIFIED, Checkpoint, RunnerResult, Verdict)
from scaffold.agent.desktop.pack import __main__ as cli
from scaffold.agent.desktop.pack import files as F
from scaffold.agent.desktop.pack import report as R
from scaffold.agent.desktop.pack import run as RUN
from scaffold.agent.desktop.pack.config import ConfigError, load_config, parse_config, url_problem

PDF = b"%PDF-1.4\n%fake statement\n"


def src(**kw):
    d = {"id": "acme", "name": "Acme SaaS", "kind": "invoice", "start_url": "https://billing.acme.com/invoices",
         "allowed_domains": ["billing.acme.com"], "skill": "acme_invoice"}
    d.update(kw)
    return d


def make_cfg(tmp_path, sources, **pack):
    dl = tmp_path / "Downloads"
    dl.mkdir(parents=True, exist_ok=True)
    data = {"pack": {"root": str(tmp_path / "Pack"), "downloads": str(dl), "settle_s": 0, "wait_s": 0, **pack},
            "source": sources}
    return parse_config(data)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def dropper(files_by_source, *, then=None):
    """A fake skill runner: drops bytes into the downloads dir, like a browser would."""
    calls = []

    def runner(source, period, ctx):
        calls.append(source.id)
        assert ctx.allowed(source.start_url, "navigate")
        assert not ctx.allowed("https://evil.example.com/", "navigate")
        for name, data in files_by_source.get(source.id, []):
            (ctx.downloads / name).write_bytes(data)
        if then:
            return then(source, period, ctx)
        return RunnerResult()
    runner.calls = calls
    return runner


def verified(doc_path, source, period):
    return Verdict(VERIFIED, [], {"period": str(period)})


def run(cfg, month="2026-10", **kw):
    clk = FakeClock()
    kw.setdefault("check", verified)
    return RUN.run_month(cfg, month, sleep=clk.sleep, clock=clk, **kw)


# ── config ──────────────────────────────────────────────────────────────────
def test_config_valid_defaults(tmp_path):
    cfg = make_cfg(tmp_path, [src(), src(id="hdfc", kind="statement", cadence="quarterly",
                                      start_url="http://127.0.0.1:8123/login", allowed_domains=["127.0.0.1"],
                                      expected={"count": 2, "types": ["pdf", "csv"]},
                                      naming="{source}_{yyyy}{mm}{n}.{ext}", password_hint="hdfc_pdf")])
    a, h = cfg.sources
    assert a.cadence == "monthly" and a.expected.count == 1 and a.expected.types == ("pdf",)
    assert h.expected.types == ("pdf", "csv") and h.password_hint == "hdfc_pdf"


@pytest.mark.parametrize("bad,needle", [
    ({"kind": "receipt"}, "kind must be one of"),
    ({"start_url": "http://billing.acme.com/"}, "must be https"),
    ({"start_url": "https://other.com/"}, "not in allowed_domains"),
    ({"allowed_domains": []}, "allowed_domains must be a non-empty list"),
    ({"allowed_domains": ["https://billing.acme.com"]}, "bare host"),
    ({"allowed_domains": ["*.com"]}, "too broad"),
    ({"cadence": "weekly"}, "cadence must be"),
    ({"expected": {"count": 0}}, "expected.count"),
    ({"naming": "../{source}.{ext}"}, "must be a file name"),
    ({"naming": "{source}_{bogus}.{ext}"}, "unknown field"),
    ({"naming": "{source}.pdf"}, "must contain {ext}"),
    ({"password": "hunter2"}, "looks like a secret"),
    ({"password_hint": "my secret pw"}, "keychain item NAME"),
    ({"id": "Bad Id"}, "id must match"),
    ({"colour": "red"}, "unknown key"),
    ({"start_url": "https://u:p@billing.acme.com/"}, "credentials in the URL"),
])
def test_config_errors(tmp_path, bad, needle):
    with pytest.raises(ConfigError) as ei:
        make_cfg(tmp_path, [src(**bad)])
    assert any(needle in p for p in ei.value.problems), ei.value.problems


def test_config_duplicate_ids_and_all_problems_reported(tmp_path):
    with pytest.raises(ConfigError) as ei:
        make_cfg(tmp_path, [src(), src(), src(id="x", kind="nope", cadence="daily")])
    probs = ei.value.problems
    assert any("duplicate id" in p for p in probs)
    assert sum("source 'x'" in p for p in probs) == 2


def test_load_config_file_and_errors(tmp_path):
    p = tmp_path / "sources.toml"
    p.write_text('[pack]\nroot = "%s"\n\n[[source]]\nid = "acme"\nname = "Acme"\nkind = "invoice"\n'
                 'start_url = "https://billing.acme.com/"\nallowed_domains = ["billing.acme.com"]\n'
                 'skill = "acme"\nexpected = { count = 1, types = ["pdf"] }\n' % (tmp_path / "Pack"))
    cfg = load_config(str(p))
    assert cfg.source("acme").name == "Acme" and cfg.root == tmp_path / "Pack"
    with pytest.raises(ConfigError):
        cfg.source("nope")
    p.write_text("not = [valid")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(str(p))
    with pytest.raises(ConfigError, match="not found"):
        load_config(str(tmp_path / "missing.toml"))


def test_url_policy_rules():
    allowed = ("billing.acme.com", "*.bank.in")
    assert url_problem("https://billing.acme.com/x", allowed) is None
    assert url_problem("https://netbanking.bank.in/", allowed) is None
    assert url_problem("https://bank.in.evil.com/", allowed)
    assert url_problem("http://billing.acme.com/", allowed)
    assert url_problem("http://127.0.0.1:9/", ("127.0.0.1",)) is None
    assert url_problem("javascript:alert(1)", allowed)


# ── files ───────────────────────────────────────────────────────────────────
def test_partial_download_waits_until_finished(tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    before = F.snapshot(dl)
    part = dl / "statement.pdf.crdownload"
    part.write_bytes(b"%PDF-")
    clk = FakeClock()
    ticks = []

    def sleep(s):
        clk.sleep(s)
        ticks.append(s)
        if len(ticks) == 2:                      # the browser finishes and renames
            part.write_bytes(PDF)
            part.rename(dl / "statement.pdf")
    files, pending = F.wait_for_downloads(dl, before, settle_s=1.0, wait_s=30, sleep=sleep, clock=clk)
    assert [p.name for p in files] == ["statement.pdf"] and pending == []


def test_partial_that_never_finishes_is_pending(tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    before = F.snapshot(dl)
    (dl / "big.pdf.download").mkdir()            # Safari's bundle is a directory
    (dl / "done.pdf").write_bytes(PDF)
    clk = FakeClock()
    files, pending = F.wait_for_downloads(dl, before, settle_s=1.0, wait_s=5, sleep=clk.sleep, clock=clk)
    assert [p.name for p in files] == ["done.pdf"] and pending == ["big.pdf.download"]


def test_growing_file_not_taken_until_stable(tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    before = F.snapshot(dl)
    f = dl / "r.csv"
    f.write_bytes(b"a")
    clk = FakeClock()
    n = [0]

    def sleep(s):
        clk.sleep(s)
        n[0] += 1
        if n[0] < 3:
            with open(f, "ab") as fh:
                fh.write(b"more")
            os.utime(f, (clk.t + 1000, clk.t + 1000))
    files, pending = F.wait_for_downloads(dl, before, settle_s=1.0, wait_s=30, sleep=sleep, clock=clk)
    assert files == [f] and f.read_bytes() == b"amoremore" and n[0] >= 3


def test_render_name_and_never_overwrite(tmp_path):
    n = F.render_name("{source}_{period}{n}.{ext}", source_id="acme", kind="invoice", period="2026-10",
                      original="Invoice #42.PDF", index=1, expected_count=1, sha="ab" * 32)
    assert n == "acme_2026-10.pdf"
    n2 = F.render_name("{yyyy}{mm}_{name}{n}_{sha8}.{ext}", source_id="a", kind="k", period="2026-10",
                       original="../../etc/passwd.pdf", index=2, expected_count=2, sha="ab" * 32)
    assert "/" not in n2 and n2.startswith("202610_passwd_2_abababab") and n2.endswith(".pdf")
    d = tmp_path / "out"
    d.mkdir()
    (d / "x.pdf").write_bytes(b"keep")
    s = tmp_path / "s.pdf"
    s.write_bytes(b"new")
    dest = F.place(s, d, "x.pdf")
    assert dest.name == "x (2).pdf" and (d / "x.pdf").read_bytes() == b"keep" and not s.exists()


# ── run ─────────────────────────────────────────────────────────────────────
def test_happy_month_files_ledger_and_verdicts(tmp_path):
    cfg = make_cfg(tmp_path, [src(), src(id="hdfc", kind="statement", start_url="https://nb.hdfc.in/",
                                         allowed_domains=["nb.hdfc.in"], password_hint="hdfc_pdf",
                                         skill="hdfc_stmt")])
    runner = dropper({"acme": [("Invoice-1001.pdf", PDF + b"acme")], "hdfc": [("stmt.pdf", PDF + b"hdfc")]})
    st = run(cfg, runner=runner)
    assert st["sources"]["acme"]["status"] == RUN.COLLECTED_VERIFIED
    assert st["sources"]["hdfc"]["status"] == RUN.COLLECTED_VERIFIED
    assert (cfg.root / "2026-10" / "acme" / "acme_2026-10.pdf").read_bytes() == PDF + b"acme"
    assert not list(cfg.downloads.iterdir())                 # moved, not copied
    recs = list(F.Ledger(cfg.root).records())
    assert [r["event"] for r in recs] == ["filed", "filed"] and all(len(r["sha256"]) == 64 for r in recs)
    assert "hdfc_pdf" not in (cfg.root / "ledger.jsonl").read_text()


def test_default_verifier_stub_gives_unverified(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, [src()])
    monkeypatch.setattr(RUN, "default_check", lambda: __import__(
        "scaffold.agent.desktop.pack", fromlist=["stub_check"]).stub_check)
    clk = FakeClock()
    st = RUN.run_month(cfg, "2026-10", runner=dropper({"acme": [("i.pdf", PDF)]}), sleep=clk.sleep, clock=clk)
    e = st["sources"]["acme"]
    assert e["status"] == RUN.COLLECTED_UNVERIFIED and e["files"][0]["verdict"]["status"] == UNVERIFIED


def test_missing_source_and_rerun_retries_only_missing(tmp_path):
    cfg = make_cfg(tmp_path, [src(), src(id="gw", kind="gateway_report", start_url="https://dash.rzp.io/",
                                         allowed_domains=["dash.rzp.io"], skill="rzp")])
    r1 = dropper({"acme": [("a.pdf", PDF)]})                 # gateway produced nothing
    st = run(cfg, runner=r1)
    assert st["sources"]["gw"]["status"] == RUN.MISSING
    assert "no new file appeared" in st["sources"]["gw"]["reason"]
    r2 = dropper({"gw": [("settlements.pdf", PDF + b"gw")]})
    st = run(cfg, runner=r2)
    assert r2.calls == ["gw"]                               # acme not re-run
    assert st["sources"]["gw"]["status"] == RUN.COLLECTED_VERIFIED
    assert st["sources"]["gw"]["attempts"] == 2


def test_duplicate_of_earlier_month_is_not_filed(tmp_path):
    cfg = make_cfg(tmp_path, [src()])
    same = PDF + b"september invoice"
    run(cfg, "2026-09", runner=dropper({"acme": [("inv.pdf", same)]}))
    st = run(cfg, "2026-10", runner=dropper({"acme": [("inv (1).pdf", same)]}))
    e = st["sources"]["acme"]
    assert e["status"] == RUN.MISSING and e["files"] == []
    assert "already filed for 2026-09/acme" in e["reason"]
    assert (cfg.downloads / "inv (1).pdf").exists()          # never deletes the owner's file
    assert not (cfg.root / "2026-10" / "acme").exists() or not list((cfg.root / "2026-10" / "acme").iterdir())


def test_needs_you_checkpoint_then_resolved(tmp_path):
    cfg = make_cfg(tmp_path, [src()])

    def otp(source, period, ctx):
        ctx.checkpoint(Checkpoint("otp", "Enter the OTP sent to +91 98xxxx otp: 123456",
                                  "https://billing.acme.com/otp?session=SECRETTOKEN"))
    st = run(cfg, runner=otp)
    e = st["sources"]["acme"]
    assert e["status"] == RUN.NEEDS_YOU and e["checkpoint"]["kind"] == "otp"
    blob = json.dumps(st)
    assert "123456" not in blob and "SECRETTOKEN" not in blob
    rep = R.build(cfg, "2026-10")
    assert "enter the OTP" in rep["sources"][0]["todo"]
    st = run(cfg, runner=dropper({"acme": [("i.pdf", PDF)]}))
    assert st["sources"]["acme"]["status"] == RUN.COLLECTED_VERIFIED and st["sources"]["acme"]["checkpoint"] is None


def test_needs_you_via_result_and_runner_crash(tmp_path):
    cfg = make_cfg(tmp_path, [src(), src(id="b", start_url="https://b.example.com/",
                                         allowed_domains=["b.example.com"], skill="b")])

    def runner(source, period, ctx):
        if source.id == "acme":
            return RunnerResult("needs_you", checkpoint=Checkpoint("captcha", "Type the characters"))
        raise RuntimeError("element 'Download' not found password=hunter2")
    st = run(cfg, runner=runner)
    assert st["sources"]["acme"]["status"] == RUN.NEEDS_YOU
    assert st["sources"]["b"]["status"] == RUN.FAILED
    assert "hunter2" not in st["sources"]["b"]["reason"] and "not found" in st["sources"]["b"]["reason"]


def test_resume_after_interruption_collects_without_rerunning(tmp_path):
    cfg = make_cfg(tmp_path, [src()])

    class Crash(BaseException):
        pass

    def dies(source, period, ctx):
        (ctx.downloads / "inv.pdf").write_bytes(PDF)        # download landed, then power cut
        raise Crash()
    with pytest.raises(Crash):
        run(cfg, runner=dies)
    st = RUN.load_state(cfg.root, "2026-10")
    assert st["sources"]["acme"]["status"] == RUN.RUNNING and "before" in st["sources"]["acme"]
    (cfg.root / "2026-10" / RUN.LOCK_NAME).write_text("999999999")   # stale lock from the dead process
    r2 = dropper({})
    st = run(cfg, runner=r2)
    assert r2.calls == [] and st["sources"]["acme"]["status"] == RUN.COLLECTED_VERIFIED
    assert "before" not in st["sources"]["acme"]


def test_lock_blocks_concurrent_run(tmp_path):
    cfg = make_cfg(tmp_path, [src()])
    (cfg.root / "2026-10").mkdir(parents=True)
    (cfg.root / "2026-10" / RUN.LOCK_NAME).write_text(str(os.getppid()))
    with pytest.raises(RUN.PackBusy):
        run(cfg, runner=dropper({}))


def test_manual_drop_in_is_adopted(tmp_path):
    cfg = make_cfg(tmp_path, [src(skill=None)])
    st = run(cfg, runner=dropper({}))
    assert st["sources"]["acme"]["status"] == RUN.MISSING and "no skill taught" in st["sources"]["acme"]["reason"]
    d = cfg.root / "2026-10" / "acme"
    d.mkdir(parents=True, exist_ok=True)
    (d / "my download.pdf").write_bytes(PDF)
    st = run(cfg, runner=dropper({}))
    e = st["sources"]["acme"]
    assert e["status"] == RUN.COLLECTED_VERIFIED and e["files"][0]["event"] == "adopted"


def test_quarterly_not_due_and_policy_refusal(tmp_path):
    cfg = make_cfg(tmp_path, [src(cadence="quarterly")])
    st = run(cfg, runner=dropper({}))
    assert st["sources"]["acme"]["status"] == RUN.NOT_DUE
    st = run(cfg, "2026-09", runner=dropper({"acme": [("q.pdf", PDF)]}))
    assert st["sources"]["acme"]["status"] == RUN.COLLECTED_VERIFIED
    p = RUN.period_for(cfg.sources[0], "2026-09")
    assert str(p.start) == "2026-07-01" and str(p.end) == "2026-09-30"
    cfg2 = make_cfg(tmp_path / "b", [src()])
    st = run(cfg2, runner=dropper({}), policy_factory=lambda s: (lambda url, action: False))
    assert st["sources"]["acme"]["status"] == RUN.FAILED and "policy refused" in st["sources"]["acme"]["reason"]


def test_suspicious_and_wrong_type_set_aside_and_excluded_from_zip(tmp_path):
    cfg = make_cfg(tmp_path, [src(expected={"count": 2, "types": ["pdf"]}),
                              src(id="b", start_url="https://b.example.com/", allowed_domains=["b.example.com"],
                                  skill="b")])

    def check(doc_path, source, period):
        if b"<html" in Path(doc_path).read_bytes():
            return {"status": SUSPICIOUS, "reasons": ["an HTML page saved as .pdf"]}
        if b"august" in Path(doc_path).read_bytes():
            return Verdict(MISMATCH, ["statement period is 2026-08"])
        return Verdict(VERIFIED)
    runner = dropper({"acme": [("a.pdf", PDF + b"1"), ("b.pdf", b"<html>login</html>"), ("c.html", b"x")],
                      "b": [("s.pdf", PDF + b"august")]})
    st = run(cfg, runner=runner, check=check)
    assert st["sources"]["acme"]["status"] == RUN.MISSING and "set aside" in st["sources"]["acme"]["reason"]
    assert st["sources"]["b"]["status"] == RUN.MISSING
    res = R.make_zip(cfg, "2026-10")
    names = zipfile.ZipFile(res["path"]).namelist()
    assert "2026-10/report.html" in names and "2026-10/report.json" in names
    assert any(n.endswith("acme_2026-10_1.pdf") for n in names)
    assert len([n for n in names if n.startswith("2026-10/acme/")]) == 1
    assert not any(n.startswith("2026-10/b/") for n in names)
    assert len(res["excluded"]) == 3 and not any(".pack_state" in n or "ledger" in n for n in names)


def test_report_html_offline_text_json_no_secrets(tmp_path):
    cfg = make_cfg(tmp_path, [src(password_hint="acme_pdf_pw"),
                              src(id="never", start_url="https://n.example.com/", allowed_domains=["n.example.com"],
                                  skill="n")])
    run(cfg, runner=dropper({"acme": [("i.pdf", PDF)]}), only="acme")
    paths = R.write_report(cfg, "2026-10")
    h = paths["html"].read_text()
    assert "<script" not in h.lower() and "http-equiv=\"Content-Security-Policy\"" in h
    assert "src=" not in h and "<link" not in h
    assert "acme_pdf_pw" not in h and "acme_pdf_pw" not in paths["txt"].read_text()
    assert "password is not in this pack" in h
    rep = json.loads(paths["json"].read_text())
    by = {r["id"]: r for r in rep["sources"]}
    assert by["never"]["status"] == R.NOT_RUN and "run --month 2026-10 --source never" in by["never"]["todo"]
    assert rep["collected"] == 1 and rep["due"] == 2 and not rep["complete"]
    st = RUN.load_state(cfg.root, "2026-10")
    st["sources"]["acme"]["reason"] = "<img src=x onerror=alert(1)>"
    assert "<img" not in R.render_html(R.build(cfg, "2026-10", st))


def test_zip_excludes_tampered_file(tmp_path):
    cfg = make_cfg(tmp_path, [src()])
    run(cfg, runner=dropper({"acme": [("i.pdf", PDF)]}))
    (cfg.root / "2026-10" / "acme" / "acme_2026-10.pdf").write_bytes(b"changed")
    res = R.make_zip(cfg, "2026-10")
    assert res["included"] == [] and res["excluded"][0]["why"].startswith("file changed")


def test_deleted_file_reopens_source(tmp_path):
    cfg = make_cfg(tmp_path, [src()])
    run(cfg, runner=dropper({"acme": [("i.pdf", PDF)]}))
    (cfg.root / "2026-10" / "acme" / "acme_2026-10.pdf").unlink()
    r = dropper({"acme": [("i2.pdf", PDF + b"again")]})
    st = run(cfg, runner=r, force=True)
    assert r.calls == ["acme"] and st["sources"]["acme"]["status"] == RUN.COLLECTED_VERIFIED


# ── CLI ─────────────────────────────────────────────────────────────────────
FAKE_RUNNER_CALLS = []


def cli_runner(source, period, ctx):
    FAKE_RUNNER_CALLS.append(source.id)
    (ctx.downloads / f"{source.id}.pdf").write_bytes(PDF + source.id.encode())
    return RunnerResult()


def test_cli_end_to_end(tmp_path, capsys, monkeypatch):
    dl = tmp_path / "Downloads"
    dl.mkdir()
    p = tmp_path / "sources.toml"
    p.write_text(f'[pack]\nroot = "{tmp_path / "Pack"}"\ndownloads = "{dl}"\nsettle_s = 0\nwait_s = 0\n\n'
                 '[[source]]\nid = "acme"\nname = "Acme"\nkind = "invoice"\n'
                 'start_url = "https://billing.acme.com/"\nallowed_domains = ["billing.acme.com"]\nskill = "acme"\n')
    monkeypatch.setattr(RUN, "default_check", lambda: verified)
    assert cli.main(["--sources", str(p), "sources-check"]) == 0
    assert "1 source(s) OK" in capsys.readouterr().out
    assert cli.main(["--sources", str(p), "run", "--month", "2026-10", "--quiet",
                     "--runner", "tests.test_pack_core:cli_runner"]) == 0
    assert "1 of 1 due sources collected" in capsys.readouterr().out
    assert cli.main(["--sources", str(p), "report", "--month", "2026-10"]) == 0
    assert cli.main(["--sources", str(p), "zip", "--month", "2026-10"]) == 0
    assert "1 document(s)" in capsys.readouterr().out
    assert cli.main(["--sources", str(p), "status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["2026-10"]["collected_verified"] == 1
    p.write_text('[[source]]\nid = "x"\nkind = "nope"\n')
    assert cli.main(["--sources", str(p), "sources-check"]) == 1
    assert "kind must be one of" in capsys.readouterr().err
