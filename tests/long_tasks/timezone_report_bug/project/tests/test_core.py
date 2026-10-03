from datetime import date, datetime, timedelta, timezone
import io

import pytest

from salesdesk.clock import FixedClock, parse_instant
from salesdesk.currency import format_cents, parse_amount
from salesdesk.importer import import_orders
from salesdesk.models import Order
from salesdesk.settings import UnknownCustomer, normalise_timezone
from salesdesk.timeutil import from_storage, iter_days, to_storage, to_utc_naive

UTC = timezone.utc


def test_parse_amount_rounds_half_up():
    assert parse_amount("12.50") == 1250
    assert parse_amount("1,234.56") == 123456
    assert parse_amount("0.005") == 1
    with pytest.raises(ValueError):
        parse_amount("abc")


def test_format_cents():
    assert format_cents(1234567) == "$12,345.67"
    assert format_cents(5, "EUR") == "€0.05"
    assert format_cents(150000, "JPY") == "¥1,500"


def test_storage_roundtrip_is_naive_utc():
    aware = datetime(2026, 3, 10, 12, 30, 45, 999, tzinfo=timezone(timedelta(hours=2)))
    text = to_storage(aware)
    assert text == "2026-03-10T10:30:45"
    assert from_storage(text) == datetime(2026, 3, 10, 10, 30, 45)
    assert to_utc_naive(datetime(2026, 1, 1)) == datetime(2026, 1, 1)


def test_iter_days_inclusive():
    days = list(iter_days(date(2026, 2, 27), date(2026, 3, 2)))
    assert days == [date(2026, 2, 27), date(2026, 2, 28), date(2026, 3, 1), date(2026, 3, 2)]


def test_fixed_clock_normalises_to_utc():
    clock = FixedClock(datetime(2026, 5, 1, 9, 0, tzinfo=timezone(timedelta(hours=-4))))
    assert clock.now() == datetime(2026, 5, 1, 13, 0, tzinfo=UTC)
    assert parse_instant("2026-05-01T13:00:00Z") == clock.now()


def test_settings_validation(settings):
    c = settings.register("c1", "Shop", "pst")
    assert c.timezone == "America/Los_Angeles"
    assert normalise_timezone("") == "UTC"
    with pytest.raises(ValueError):
        settings.register("c2", "Bad", "Mars/Olympus")
    with pytest.raises(ValueError):
        settings.register("c3", "Bad", "UTC", "XYZ")
    with pytest.raises(UnknownCustomer):
        settings.get("nope")


def test_order_validation():
    with pytest.raises(ValueError):
        Order("o1", "c1", 100, datetime(2026, 1, 1), status="lost")
    with pytest.raises(ValueError):
        Order("o1", "c1", -1, datetime(2026, 1, 1))


def test_orders_between_half_open(storage, settings):
    settings.register("c1", "Shop")
    storage.add_orders([
        Order("a", "c1", 100, datetime(2026, 3, 10, 0, 0, tzinfo=UTC)),
        Order("b", "c1", 200, datetime(2026, 3, 10, 23, 59, 59, tzinfo=UTC)),
        Order("c", "c1", 300, datetime(2026, 3, 11, 0, 0, tzinfo=UTC)),
    ])
    got = storage.orders_between("c1", datetime(2026, 3, 10), datetime(2026, 3, 11))
    assert [o.order_id for o in got] == ["a", "b"]


def test_import_csv(storage, settings):
    settings.register("c1", "Shop")
    data = io.StringIO(
        "order_id,customer_id,amount,created_at,status\n"
        "o1,c1,10.00,2026-03-10T08:00:00Z,paid\n"
        "o2,c1,oops,2026-03-10T09:00:00Z,paid\n"
        "o3,c1,5.25,2026-03-10T10:00:00+01:00,refunded\n"
    )
    result = import_orders(storage, data)
    assert result.imported == 2
    assert len(result.errors) == 1 and "line 3" in result.errors[0]
    orders = storage.all_orders("c1")
    assert orders[1].created_at == datetime(2026, 3, 10, 9, 0)
