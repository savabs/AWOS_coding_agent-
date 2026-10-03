from datetime import date, datetime
from decimal import Decimal

import pytest

from ordertool.errors import OrderToolError, ValidationError
from ordertool.export import filter_orders
from ordertool.utils.dates import parse_date_arg
from ordertool.utils.money import format_money, parse_money, round_money


def test_round_money_is_half_up():
    assert round_money(Decimal("0.125")) == Decimal("0.13")
    assert round_money(Decimal("2.675")) == Decimal("2.68")
    assert format_money(Decimal("1.005")) == "1.01"


def test_parse_money_rounds_to_cents():
    assert parse_money("9.999") == Decimal("10.00")
    with pytest.raises(ValidationError):
        parse_money("abc")


def test_validation_error_is_user_facing():
    assert issubclass(ValidationError, OrderToolError)


def test_parse_date_arg():
    assert parse_date_arg("2024-03-10") == date(2024, 3, 10)
    with pytest.raises(ValidationError):
        parse_date_arg("2024-02-30")


def test_filter_orders_end_date_is_inclusive(seeded):
    seeded.create_order("Late", "late@example.com", ["WIDGET:1:1.00"],
                        created_at=datetime(2024, 3, 9, 23, 59, 59))
    ids = [o.id for o in filter_orders(seeded.list_orders(),
                                       date_from=date(2024, 3, 5), date_to=date(2024, 3, 9))]
    assert ids == ["ORD-0002", "ORD-0003", "ORD-0004"]


def test_cli_export_filters(seeded, db_path, capsys):
    from ordertool.cli import main

    assert main(["--db", db_path, "export", "--from", "2024-03-05"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(",")[0] for line in lines[1:]] == ["ORD-0002", "ORD-0003"]
    assert main(["--db", db_path, "export", "--to", "2024-31-01"]) == 1
    assert capsys.readouterr().err.startswith("error: invalid date")
