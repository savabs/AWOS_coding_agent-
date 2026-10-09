"""C10: every chore's probes FAIL on the start state and PASS after the
scripted reference solution; invariants hold on both."""
import shutil

import pytest

from scaffold.agent.desktop.chores import DEFAULT_CHORE_DIR, Chore, load_all, validate_chore

CHORES = load_all(DEFAULT_CHORE_DIR)


def test_c10_has_ten_chores_with_unique_ids():
    assert len(CHORES) == 10
    assert len({c.id for c in CHORES}) == 10
    for c in CHORES:
        assert (c.directory / "solve.py").exists()
        assert c.probes, c.id
        assert not any(p.is_live for p in c.probes), "C10 runs without a live desktop"


@pytest.mark.parametrize("chore", CHORES, ids=[c.id for c in CHORES])
def test_chore_valid(chore, tmp_path):
    if not chore.supported:
        pytest.skip(f"needs {chore.platform}")
    res = validate_chore(chore, tmp_path)
    assert res.valid, res.summary()


def test_unsolved_end_state_is_rejected(tmp_path):
    """A no-op 'solution' must not validate: probes would pass on neither state."""
    src = next(c for c in CHORES if c.id == "c04_ini_port").directory
    d = tmp_path / "chore"
    shutil.copytree(src, d)
    (d / "solve.py").write_text("pass\n")
    res = validate_chore(Chore.load(d), tmp_path / "work")
    assert not res.valid and not res.probe_checks[0].passes_on_end


def test_broken_solution_reports_exit(tmp_path):
    src = next(c for c in CHORES if c.id == "c03_csv_to_json").directory
    d = tmp_path / "chore"
    shutil.copytree(src, d)
    (d / "solve.py").write_text("raise SystemExit(3)\n")
    res = validate_chore(Chore.load(d), tmp_path / "work")
    assert not res.valid and "exit 3" in res.reason


def test_invariant_violation_rejected(tmp_path):
    """Solution that changes the port but also clobbers host -> invalid."""
    src = next(c for c in CHORES if c.id == "c04_ini_port").directory
    d = tmp_path / "chore"
    shutil.copytree(src, d)
    (d / "solve.py").write_text(
        "open('config/settings.ini','w').write('[server]\\nport = 9090\\n[log]\\nlevel = info\\n')\n")
    res = validate_chore(Chore.load(d), tmp_path / "work")
    assert all(v.valid for v in res.probe_checks)
    assert not res.valid
