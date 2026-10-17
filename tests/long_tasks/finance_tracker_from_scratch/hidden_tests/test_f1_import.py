"""Feature 1: importing the two bank CSV formats (Decimal money)."""
from decimal import Decimal

import pytest

from ft_helpers import NORTH, SOUTH, day


def test_northbank_basic_parse(ft, write):
    res = ft.importers.parse_csv(write("n.csv", NORTH))
    assert res.format == "northbank"
    assert res.errors == []
    assert [t.date for t in res.transactions] == [day(2026, 3, 1), day(2026, 3, 2), day(2026, 3, 3)]
    assert [t.amount for t in res.transactions] == [Decimal("-45.20"), Decimal("2500.00"), Decimal("-12.40")]
    assert all(isinstance(t.amount, Decimal) for t in res.transactions)
    assert res.transactions[0].description == "TESCO STORES 2231"
    assert all(t.account == "default" and t.category is None and t.id is None for t in res.transactions)


def test_southbank_signs_decimal_comma_and_memo(ft, write):
    res = ft.importers.parse_csv(write("s.csv", SOUTH), account="joint")
    assert res.format == "southbank"
    assert res.errors == []
    got = [(t.date, t.description, t.amount, t.account) for t in res.transactions]
    assert got == [
        (day(2026, 3, 5), "Tesco - Weekly shop", Decimal("-45.20"), "joint"),
        (day(2026, 3, 6), "ACME Corp - Salary March", Decimal("2500.00"), "joint"),
        (day(2026, 3, 7), "Shell", Decimal("-60.00"), "joint"),
    ]


def test_detect_format(ft):
    assert ft.importers.detect_format("Date,Description,Amount") == "northbank"
    assert ft.importers.detect_format(" booking date ; PAYEE;Memo;Debit;Credit\n") == "southbank"
    with pytest.raises(ft.errors.ParseError):
        ft.importers.detect_format("When,What,HowMuch")


def test_bom_time_suffix_plus_sign_and_whitespace(ft, write):
    text = ("﻿Date,Description,Amount\n"
            "2026-03-31 23:59:59,  CORNER   SHOP \t LTD ,+3.10\n"
            "2026-04-01 00:05,REFUND,0.1\n")
    res = ft.importers.parse_csv(write("n.csv", text))
    assert res.errors == []
    a, b = res.transactions
    assert a.date == day(2026, 3, 31) and a.description == "CORNER SHOP LTD" and a.amount == Decimal("3.10")
    assert b.date == day(2026, 4, 1) and b.amount == Decimal("0.10")
    assert str(b.amount) == "0.10"


def test_malformed_northbank_rows_are_skipped_with_line_numbers(ft, write):
    text = ("Date,Description,Amount\n"          # 1
            "2026-03-01,GOOD ONE,-1.00\n"        # 2
            "2026-02-30,BAD DATE,-1.00\n"        # 3
            "\n"                                 # 4 (ignored silently)
            "2026-03-02,BAD AMOUNT,abc\n"        # 5
            "2026-03-03,TOO,MANY,COLS\n"         # 6
            "03/04/2026,US DATE,-1.00\n"         # 7
            "2026-03-04,,-1.00\n"                # 8 empty description
            '2026-03-05,"-1,234.56 OK","-1,234.56"\n')  # 9
    res = ft.importers.parse_csv(write("n.csv", text))
    assert [t.description for t in res.transactions] == ["GOOD ONE", "-1,234.56 OK"]
    assert res.transactions[1].amount == Decimal("-1234.56")
    assert [e.line for e in res.errors] == [3, 5, 6, 7, 8]
    assert all(e.message for e in res.errors)


def test_malformed_southbank_rows(ft, write):
    text = ("Booking Date;Payee;Memo;Debit;Credit\n"   # 1
            "01.03.2026;Both;x;1,00;2,00\n"            # 2 both filled
            "02.03.2026;Neither;x;;\n"                 # 3 neither
            "03.03.2026;Negative;x;-5,00;\n"           # 4 negative debit
            "2026-03-04;WrongDate;x;5,00;\n"           # 5 wrong date format
            "31.04.2026;Impossible;x;5,00;\n"          # 6 April 31st
            "05.03.2026;Fine;;1.234,56;\n")            # 7
    res = ft.importers.parse_csv(write("s.csv", text))
    assert [e.line for e in res.errors] == [2, 3, 4, 5, 6]
    assert len(res.transactions) == 1
    assert res.transactions[0].amount == Decimal("-1234.56")


def test_file_level_errors(ft, write, tmp_path):
    with pytest.raises(ft.errors.ParseError):
        ft.importers.parse_csv(tmp_path / "missing.csv")
    with pytest.raises(ft.errors.ParseError):
        ft.importers.parse_csv(write("weird.csv", "foo,bar\n1,2\n"))
    with pytest.raises(ft.errors.ParseError):
        ft.importers.parse_csv(write("empty.csv", ""))
    with pytest.raises(ft.errors.ParseError):  # header does not match the forced format
        ft.importers.parse_csv(write("n.csv", NORTH), fmt="southbank")
    with pytest.raises(ft.errors.ValidationError):
        ft.importers.parse_csv(write("n2.csv", NORTH), fmt="westbank")
    assert issubclass(ft.errors.ParseError, ft.errors.FintrackError)
    assert issubclass(ft.errors.ValidationError, ft.errors.FintrackError)
