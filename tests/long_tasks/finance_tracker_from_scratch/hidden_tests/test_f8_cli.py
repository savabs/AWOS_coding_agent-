"""Feature 8: the `python -m fintrack` CLI, exit codes and error handling."""
import csv
import io
import json
import os
import subprocess
import sys

import pytest

from ft_helpers import NORTH, ROOT, SOUTH

RULES = {"default": "Other", "rules": [
    {"category": "Groceries", "contains": "tesco"},
    {"category": "Salary", "contains": "salary", "direction": "in"},
    {"category": "Transport", "regex": "^uber\\b"},
]}


@pytest.fixture
def cli(ft, tmp_path, capsys):
    db = str(tmp_path / "cli.db")

    def run(*args):
        code = ft.cli.main(["--db", db, *[str(a) for a in args]])
        out, err = capsys.readouterr()
        return code, out, err
    run.db = db
    return run


def test_import_reports_counts_and_duplicates(cli, write):
    n, s = write("north.csv", NORTH), write("south.csv", SOUTH)
    code, out, err = cli("import", n, s)
    assert code == 0, err
    assert f"Imported {n}: 3 added, 0 duplicates, 0 errors" in out.splitlines()
    assert f"Imported {s}: 3 added, 0 duplicates, 0 errors" in out.splitlines()
    code, out, _ = cli("import", n)
    assert code == 0
    assert out.strip() == f"Imported {n}: 0 added, 3 duplicates, 0 errors"


def test_import_with_bad_rows_exits_3(cli, write):
    bad = write("bad.csv", NORTH + "2026-03-04,BROKEN,xyz\n")
    code, out, err = cli("import", bad, "--account", "card")
    assert code == 3
    assert f"Imported {bad}: 3 added, 0 duplicates, 1 errors" in out
    assert f"{bad}:5:" in err
    assert "Traceback" not in err


def test_import_is_all_or_nothing_on_file_errors(ft, cli, write, tmp_path):
    good = write("north.csv", NORTH)
    code, out, err = cli("import", good, tmp_path / "nope.csv")
    assert code == 1
    assert err.startswith("error: ") and "Traceback" not in err
    code, _, err = cli("import", write("weird.csv", "a,b,c\n1,2,3\n"))
    assert code == 1 and err.startswith("error: ")
    with ft.storage.Ledger(cli.db) as led:
        assert led.transactions() == []


def test_categorize_and_report(cli, write):
    cli("import", write("north.csv", NORTH))
    code, out, _ = cli("categorize", "--rules", write("rules.json", json.dumps(RULES)))
    assert code == 0 and out.strip() == "Categorized 3 transactions"
    code, out, _ = cli("categorize", "--rules", write("rules2.json", json.dumps(RULES)), "--all")
    assert code == 0 and out.strip() == "Categorized 3 transactions"
    code, out, _ = cli("report", "--month", "2026-03")
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "Report for 2026-03"
    assert f"{'Groceries':<20} {1:>5} {'-45.20':>12}" in lines
    assert "Net: 2442.40" in lines
    code, out, _ = cli("report", "--month", "2026-03", "--json")
    assert code == 0
    assert json.loads(out)["income"] == "2500.00"
    code, out, _ = cli("report", "--month", "2026-07")
    assert code == 0 and out == "Report for 2026-07\nNo transactions.\n"


def test_budget_commands(cli, write):
    cli("import", write("north.csv", NORTH))
    cli("categorize", "--rules", write("rules.json", json.dumps(RULES)))
    code, out, _ = cli("budget", "list")
    assert code == 0 and out.strip() == "No budgets set."
    code, out, _ = cli("budget", "set", "Transport", "15")
    assert code == 0 and out.strip() == "Budget for Transport set to 15.00"
    cli("budget", "set", "Groceries", "40")
    cli("budget", "set", "Fun", "25.5")
    code, out, _ = cli("budget", "list")
    assert out.splitlines() == ["Fun: 25.50", "Groceries: 40.00", "Transport: 15.00"]
    code, out, _ = cli("budget", "check", "--month", "2026-03")
    assert code == 0
    assert out.splitlines() == [
        "Fun: spent 0.00 of 25.50 (OK)",
        "Groceries: spent 45.20 of 40.00 (OVER)",
        "Transport: spent 12.40 of 15.00 (NEAR)",
    ]


def test_export_command(cli, write, tmp_path):
    cli("import", write("north.csv", NORTH), write("south.csv", SOUTH))
    code, out, _ = cli("export", "--format", "csv", "--from", "2026-03-02", "--to", "2026-03-05")
    assert code == 0
    rows = list(csv.DictReader(io.StringIO(out)))
    assert [r["date"] for r in rows] == ["2026-03-02", "2026-03-03", "2026-03-05"]
    target = tmp_path / "out.json"
    code, out, _ = cli("export", "--format", "json", "--output", target)
    assert code == 0
    assert out.strip() == f"Exported 6 transactions to {target}"
    assert len(json.loads(target.read_text(encoding="utf-8"))) == 6


@pytest.mark.parametrize("args", [
    ("report", "--month", "2026-13"),
    ("report", "--month", "March"),
    ("budget", "set", "Food", "-10"),
    ("budget", "set", "Food", "ten"),
    ("budget", "check", "--month", "2026/03"),
    ("export", "--format", "csv", "--from", "2026-02-30"),
    ("export", "--format", "csv", "--from", "2026-04-01", "--to", "2026-03-01"),
])
def test_bad_input_gives_clean_error(cli, args):
    code, out, err = cli(*args)
    assert code == 1
    assert err.startswith("error: ")
    assert "Traceback" not in err


def test_categorize_bad_rules_and_usage_error(cli, write):
    code, _, err = cli("categorize", "--rules", write("rules.json", "{oops"))
    assert code == 1 and err.startswith("error: ")
    try:  # argparse's default is SystemExit(2); returning 2 is fine too
        code = cli("frobnicate")[0]
    except SystemExit as exc:
        code = exc.code
    assert code == 2


def test_python_dash_m_end_to_end(write, tmp_path):
    db = tmp_path / "env.db"
    env = {**os.environ, "FINTRACK_DB": str(db), "PYTHONPATH": ROOT}
    proc = subprocess.run([sys.executable, "-m", "fintrack", "import", str(write("s.csv", SOUTH))],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "3 added" in proc.stdout
    assert db.exists()
    proc = subprocess.run([sys.executable, "-m", "fintrack", "report", "--month", "2026-13"],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1
    assert proc.stderr.startswith("error: ") and "Traceback" not in proc.stderr
