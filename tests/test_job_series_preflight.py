"""Hermetic tests for the evaluation instrument in scripts/job_series.py:
preflight, per-arm input manifests, provenance, and the health/report hooks.
No network, no model calls (children are tiny fake scripts or the dry child)."""
from __future__ import annotations

import json
import sys
import textwrap
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_job_series_runner import js, make_series  # noqa: E402  (same module instance)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(js, "key_usage", lambda state: None)
    monkeypatch.setattr(js, "backend_ok", lambda: True)
    # Hooks absent unless a test installs fakes (B/C's real modules must not leak in).
    monkeypatch.setitem(sys.modules, "eval_health", None)
    monkeypatch.setitem(sys.modules, "eval_report", None)


def _fake_arm(tmp_path, monkeypatch, name="fake"):
    """A non-dry arm whose child leaves a marker and reports usage (no model)."""
    script = tmp_path / f"{name}_child.py"
    script.write_text(textwrap.dedent("""
        import json, pathlib
        pathlib.Path("ran.marker").write_text("child ran")
        pathlib.Path("report.json").write_text(json.dumps({"success": True, "usage": {
            "cost_usd": 0.0, "turns": 2, "input_tokens": 1, "output_tokens": 1, "models": ["m"]}}))
    """))
    monkeypatch.setitem(js.ARM_CHILDREN, name, {
        "cmd": [sys.executable, str(script), "{job_dir}", "{project}", "--api-key", "sk-secret-123456789"],
        "env": {"AWOS_NOTEBOOK": "0"}})
    return name


def _run(tmp_path, series, *extra):
    out_root = tmp_path / "out"
    rc = js.main(["run", "--series", series, "--series-root", str(tmp_path),
                  "--out-root", str(out_root), *extra])
    files = sorted(out_root.glob("job_series_*.json"))
    return rc, (json.loads(files[0].read_text()) if files else None), out_root


# ── preflight ─────────────────────────────────────────────────────────────────


def test_preflight_aborts_on_invalid_job_before_any_child(tmp_path, monkeypatch, capsys):
    make_series(tmp_path, "bad", broken=True)          # references don't solve the jobs
    arm = _fake_arm(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(js, "stream_child", lambda *a, **k: calls.append(a) or False)
    rc, data, out_root = _run(tmp_path, "bad", "--arms", arm)
    assert rc == 3 and calls == []                      # no arm ever started
    out = capsys.readouterr().out
    assert "PREFLIGHT FAILED" in out and "no model calls were made" in out
    assert data["preflight"]["status"] == "failed" and data["preflight"]["invalid_jobs"] == [1, 2]
    assert data["valid"] is False and data["results"] == []
    assert data["violations"][0]["check"] == "preflight"
    assert "hidden tests fail with the reference" in data["preflight"]["jobs"]["1"][0]
    assert not list((out_root / "job_series").glob("*/fake/state/ran.marker"))


def test_preflight_ok_is_recorded(tmp_path, monkeypatch):
    make_series(tmp_path, "mini")
    arm = _fake_arm(tmp_path, monkeypatch)
    rc, data, _ = _run(tmp_path, "mini", "--arms", arm, "--jobs", "1")
    assert rc == 0
    assert data["preflight"]["status"] == "ok" and data["preflight"]["jobs"] == {"1": []}
    assert data["valid"] is True


def test_skip_preflight_and_dry_run_are_recorded(tmp_path, monkeypatch):
    make_series(tmp_path, "bad", broken=True)
    arm = _fake_arm(tmp_path, monkeypatch)
    rc, data, _ = _run(tmp_path, "bad", "--arms", arm, "--jobs", "1", "--skip-preflight")
    assert rc == 0 and len(data["results"]) == 1
    assert data["preflight"] == {"status": "skipped", "reason": "--skip-preflight"}
    assert any(v["check"] == "preflight_skipped" for v in data["violations"])
    make_series(tmp_path / "d", "bad", broken=True)
    rc, data, _ = _run(tmp_path / "d", "bad", "--dry-run", "--jobs", "1")
    assert data["preflight"] == {"status": "skipped", "reason": "dry-run"}
    assert not any(v["check"] == "preflight_skipped" for v in data["violations"])


# ── input manifests ───────────────────────────────────────────────────────────


def test_manifest_hashes_equal_across_arms(tmp_path):
    make_series(tmp_path, "mini")
    rc, data, _ = _run(tmp_path, "mini", "--dry-run")
    assert rc == 0
    by = {(r["arm"], r["job"]): r["inputs"] for r in data["results"]}
    for job in (1, 2):
        off, on = by[("off", job)], by[("on", job)]
        assert off["project_sha256"] == on["project_sha256"]
        assert off["task_sha256"] == on["task_sha256"]
        assert off["project_files"] == on["project_files"] > 0
        assert off["env"]["AWOS_NOTEBOOK"] == "0" and on["env"]["AWOS_NOTEBOOK"] == "1"
        assert off["env"]["AWOS_AGENT_MODEL"] == js.PINNED_MODEL
        assert "_dry_child" in off["cmd"]
    assert by[("off", 1)]["project_sha256"] != by[("off", 2)]["project_sha256"]
    assert not any(v["check"] == "input_mismatch" for v in data["violations"])


def test_routing_pin_and_session_are_recorded(tmp_path, monkeypatch):
    # A provider-pin ablation must say how it was routed, in every row and the run.
    monkeypatch.setenv("AWOS_OPENROUTER_PROVIDER", "deepinfra")
    make_series(tmp_path, "mini")
    rc, data, _ = _run(tmp_path, "mini", "--dry-run")
    assert rc == 0
    assert data["model_pin"]["routing"] == {"AWOS_OPENROUTER_PROVIDER": "deepinfra"}
    sessions = set()
    for r in data["results"]:
        assert r["inputs"]["env"]["AWOS_OPENROUTER_PROVIDER"] == "deepinfra"
        sessions.add(r["inputs"]["env"]["AWOS_SESSION_ID"])
    assert len(sessions) == len(data["results"])  # one session per (arm, job)


def test_manifest_mismatch_is_fatal(tmp_path, monkeypatch, capsys):
    make_series(tmp_path, "mini")
    real = js.materialize

    def skewed(sdir, jobs, number, dest, with_own_reference=False):
        out = real(sdir, jobs, number, dest, with_own_reference)
        if dest.parent.name == "on" and number == 2:      # the "on" arm gets an extra file
            (dest / "extra.py").write_text("x = 1\n")
        return out

    monkeypatch.setattr(js, "materialize", skewed)
    rc, data, _ = _run(tmp_path, "mini", "--dry-run")
    [v] = [v for v in data["violations"] if v["check"] == "input_mismatch"]
    assert v["severity"] == "fatal" and v["job"] == 2 and "project tree" in v["detail"]
    assert data["valid"] is False
    assert "INVALID RUN" in capsys.readouterr().out
    health = json.loads((Path(data["run_dir"]) / "health.json").read_text())
    assert health["ok"] is False and health["violations"][0]["check"] == "input_mismatch"


def test_manifest_masks_secrets_in_cmd(tmp_path, monkeypatch):
    make_series(tmp_path, "mini")
    arm = _fake_arm(tmp_path, monkeypatch)
    rc, data, _ = _run(tmp_path, "mini", "--arms", arm, "--jobs", "1")
    cmd = data["results"][0]["inputs"]["cmd"]
    assert "sk-secret-123456789" not in json.dumps(data)
    assert cmd[cmd.index("--api-key") + 1] == "***"
    assert js.mask_cmd(["x", "OPENROUTER_API_KEY=abc", "--token=zz", "Bearer sk-abcdefghijk"]) == \
        ["x", "OPENROUTER_API_KEY=***", "--token=***", "***"]


def test_tree_hash_skips_runtime_dirs(tmp_path):
    (tmp_path / "a.py").write_text("a")
    h1 = js.tree_hash(tmp_path)
    for d in (".git", ".awos", "__pycache__"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "junk").write_text("j")
    assert js.tree_hash(tmp_path) == h1
    (tmp_path / "b.py").write_text("b")
    assert js.tree_hash(tmp_path) != h1 and js.tree_hash(tmp_path)[1] == 2


def test_aider_files_from_report_and_log_and_empty_chat_flagged():
    rep = {"harness": "aider", "files_selection": "auto:src+tests", "files_added": ["a.py"],
           "files_read": ["t.py"], "files_in_chat": ["a.py"]}
    assert js.aider_files(rep, "")["added"] == ["a.py"]
    banner = ("+00:01 [harness_aider] aider 0.86.2 model=x key=set price=known max_reflections=10 "
              "add_files=none(0) cost_cap=$0.3\n")
    assert js.aider_files({}, banner) == {"source": "log", "selection": "none",
                                          "added_count": 0, "read_only_count": 0}
    assert js.aider_files({}, "no harness here") is None
    rows = [{"arm": "aider", "job": 3, "inputs": {"project_sha256": "p", "task_sha256": "t",
                                                  "aider_files": js.aider_files({}, banner)}},
            {"arm": "off", "job": 3, "inputs": {"project_sha256": "p", "task_sha256": "t"}}]
    [v] = js.manifest_violations(rows)
    assert v["check"] == "aider_no_files" and v["arm"] == "aider" and v["severity"] == "fatal"
    rows[1]["inputs"]["task_sha256"] = "other"
    assert {v["check"] for v in js.manifest_violations(rows)} == {"aider_no_files", "input_mismatch"}


# ── provenance ────────────────────────────────────────────────────────────────


def test_provenance_fields(tmp_path):
    make_series(tmp_path, "mini")
    rc, data, _ = _run(tmp_path, "mini", "--dry-run", "--jobs", "1")
    p = data["provenance"]
    assert len(p["git"]["sha"]) == 40 and isinstance(p["git"]["dirty"], bool)
    assert "branch" in p["git"] and isinstance(p["git"]["dirty_paths"], list)
    assert len(p["config"]["sha256"]) == 64 and len(p["config"]["series_sha256"]) == 64
    assert p["started_at"].endswith("Z") and p["finished_at"] >= p["started_at"]
    # The config hash moves with the config, not with time.
    sdir = tmp_path / "mini"
    a = js.config_hash("mini", sdir, ["off", "on"], [1], 1)
    assert a == js.config_hash("mini", sdir, ["off", "on"], [1], 1)
    assert a != js.config_hash("mini", sdir, ["off", "on"], [1], 2)
    assert a != js.config_hash("mini", sdir, ["off"], [1], 1)


# ── end-of-run hooks ──────────────────────────────────────────────────────────


def _install_hooks(monkeypatch, health: dict | None = None):
    calls: dict = {}

    def check_run(run_root: Path, results_path: Path) -> dict:
        calls["health"] = (run_root, results_path, json.loads(results_path.read_text()))
        return health if health is not None else {"ok": True, "violations": [
            {"severity": "warn", "check": "w", "arm": "off", "job": 1, "repeat": 1, "detail": "d"}]}

    def write_report(results_path: Path, out_dir: Path) -> Path:
        calls["report"] = (results_path, out_dir, json.loads(results_path.read_text()))
        p = out_dir / "report.md"
        p.write_text("# report\n")
        return p

    monkeypatch.setitem(sys.modules, "eval_health", types.SimpleNamespace(check_run=check_run))
    monkeypatch.setitem(sys.modules, "eval_report", types.SimpleNamespace(write_report=write_report))
    return calls


def test_hooks_called_with_contract_signatures(tmp_path, monkeypatch, capsys):
    calls = _install_hooks(monkeypatch)
    make_series(tmp_path, "mini")
    rc, data, out_root = _run(tmp_path, "mini", "--dry-run")
    run_root = Path(data["run_dir"])
    [results_path] = out_root.glob("job_series_*.json")
    assert calls["health"][:2] == (run_root, results_path)
    assert calls["health"][2]["results"]                   # results were on disk first
    assert calls["report"][:2] == (results_path, run_root)
    assert calls["report"][2]["valid"] is True             # report sees the health verdict
    health = json.loads((run_root / "health.json").read_text())
    assert health["ok"] is True and health["violations"][0]["check"] == "w"
    assert data["valid"] is True and data["report"] == str(run_root / "report.md")
    assert data["health"]["warn"] == 1
    assert f"report: {run_root / 'report.md'}" in capsys.readouterr().out


def test_health_failure_marks_run_invalid(tmp_path, monkeypatch, capsys):
    _install_hooks(monkeypatch, {"ok": False, "violations": [
        {"severity": "fatal", "check": "model_allowlist", "arm": "on", "job": 2, "repeat": 1,
         "detail": "call to openai/gpt-x"}]})
    make_series(tmp_path, "mini")
    rc, data, _ = _run(tmp_path, "mini", "--dry-run")
    assert data["valid"] is False and data["health"]["fatal"] == 1
    out = capsys.readouterr().out
    assert "INVALID RUN" in out and "model_allowlist" in out


def test_missing_hooks_warn_but_run_completes(tmp_path, capsys):
    make_series(tmp_path, "mini")
    rc, data, _ = _run(tmp_path, "mini", "--dry-run", "--jobs", "1")
    assert rc == 0 and data["valid"] is True and "report" not in data
    assert any(v["check"] == "health_check_unavailable" for v in data["violations"])
    out = capsys.readouterr().out
    assert "eval_health.check_run unavailable" in out and "eval_report.write_report unavailable" in out


def test_revalidate_runs_hooks_without_touching_the_run(tmp_path, monkeypatch):
    make_series(tmp_path, "mini")
    rc, data, out_root = _run(tmp_path, "mini", "--dry-run", "--jobs", "1")
    ts = data["timestamp"]
    run_root = Path(data["run_dir"])
    before = (run_root / "health.json").read_text()
    calls = _install_hooks(monkeypatch)
    out2 = tmp_path / "mine"
    assert js.main(["revalidate", ts, "--data-root", str(out_root), "--out-root", str(out2)]) == 0
    rv = out2 / f"job_series_{ts}_revalidated.json"
    assert calls["health"][:2] == (run_root, rv)
    assert calls["report"][:2] == (rv, out2 / "job_series" / f"{ts}_revalidated")
    assert (run_root / "health.json").read_text() == before
    reval = json.loads(rv.read_text())
    assert reval["valid"] is True and reval["valid_before"] is True and "report" in reval
