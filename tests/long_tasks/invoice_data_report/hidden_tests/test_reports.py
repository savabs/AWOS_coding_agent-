"""Hidden tests for invoice_data_report. Run with cwd = project root.

They compare the agent's reports/*.csv with the expected outputs in
hidden_tests/expected/. Trivial formatting (whitespace, header case, BOM,
trailing newline, name spelling/case) is tolerated; wrong numbers, customers,
order, or duplicate sets are not.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

REPORTS = Path("reports")
EXPECTED = Path(__file__).resolve().parent / "expected"
TOP_COLS = ["rank", "customer_id", "customer_name", "total_eur"]
DUP_COLS = ["invoice_id", "customer_id", "invoice_date", "currency", "amount", "times_billed"]
TOL = 0.05  # EUR; wrong handling of FX / credit notes / duplicates is off by far more


def _read(path: Path):
    assert path.exists(), f"missing {path}"
    text = path.read_text(encoding="utf-8-sig")
    rows = [r for r in csv.reader(text.splitlines()) if any(c.strip() for c in r)]
    assert rows, f"{path} is empty"
    header = [h.strip().lower() for h in rows[0]]
    body = [[c.strip() for c in r] for r in rows[1:]]
    return header, [dict(zip(header, r)) for r in body]


def _num(s: str) -> float:
    return float(s.replace(",", "").replace(" ", ""))


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


@pytest.fixture(scope="module")
def top():
    return _read(REPORTS / "top_customers.csv")


@pytest.fixture(scope="module")
def top_exp():
    return _read(EXPECTED / "top_customers.csv")[1]


@pytest.fixture(scope="module")
def dups():
    return _read(REPORTS / "duplicate_invoices.csv")


@pytest.fixture(scope="module")
def dups_exp():
    return _read(EXPECTED / "duplicate_invoices.csv")[1]


def test_top_customers_header(top):
    assert top[0] == TOP_COLS


def test_top_customers_has_ten_ranked_rows(top):
    rows = top[1]
    assert len(rows) == 10
    assert [int(_num(r["rank"])) for r in rows] == list(range(1, 11))


def test_top_customers_ids_in_order(top, top_exp):
    assert [r["customer_id"].upper() for r in top[1]] == [r["customer_id"] for r in top_exp]


def test_top_customers_totals(top, top_exp):
    for got, exp in zip(top[1], top_exp):
        assert abs(_num(got["total_eur"]) - _num(exp["total_eur"])) <= TOL, (got, exp)


def test_top_customers_sorted_and_named(top, top_exp):
    totals = [_num(r["total_eur"]) for r in top[1]]
    assert totals == sorted(totals, reverse=True)
    for got, exp in zip(top[1], top_exp):
        assert _norm_name(got["customer_name"]) == _norm_name(exp["customer_name"]), (got, exp)


def test_duplicates_header(dups):
    assert dups[0] == DUP_COLS


def test_duplicates_exact_set(dups, dups_exp):
    got = {r["invoice_id"].upper() for r in dups[1]}
    exp = {r["invoice_id"] for r in dups_exp}
    assert not (got - exp), f"falsely flagged: {sorted(got - exp)}"
    assert not (exp - got), f"missed: {sorted(exp - got)}"
    assert len(dups[1]) == len(dups_exp), "each duplicate invoice should be listed once"


def test_duplicates_sorted_with_counts(dups, dups_exp):
    assert [r["invoice_id"].upper() for r in dups[1]] == [r["invoice_id"] for r in dups_exp]
    assert [int(_num(r["times_billed"])) for r in dups[1]] == [int(r["times_billed"]) for r in dups_exp]


def test_duplicates_details(dups, dups_exp):
    for got, exp in zip(dups[1], dups_exp):
        assert got["customer_id"].upper() == exp["customer_id"], (got, exp)
        assert got["invoice_date"] == exp["invoice_date"], (got, exp)
        assert got["currency"].upper() == exp["currency"], (got, exp)
        assert abs(_num(got["amount"]) - _num(exp["amount"])) <= 0.005, (got, exp)
