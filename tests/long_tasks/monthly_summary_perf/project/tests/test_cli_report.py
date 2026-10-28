import json

from ledger.cli import main
from ledger.fixtures import generate_ledger
from ledger.report import render_text
from ledger.summary import build_monthly_summary


def test_cli_summary_json(capsys):
    assert main(["summary", "--seed", "2", "--size", "tiny", "--year", "2024", "--month", "3", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["account_id"] == "ACC-0002"
    assert data["period"] == "2024-03"
    assert len(data["daily_balances"]) == 31


def test_text_report_mentions_key_figures():
    bundle = generate_ledger(2, "tiny")
    s = build_monthly_summary(bundle.store, bundle.fx, bundle.categoriser, bundle.account_id, 2024, 3)
    text = render_text(s, show_daily=True)
    assert text.startswith("Monthly summary ACC-0002 2024-03 (GBP)")
    assert "Closing balance" in text and "Top merchants" in text
    assert "2024-03-31" in text


def test_cli_generate(tmp_path, capsys):
    out = tmp_path / "rows.csv"
    assert main(["generate", "--seed", "2", "--size", "tiny", "--out", str(out)]) == 0
    assert out.read_text().startswith("id,account,date,desc,amount,currency")
