"""Hidden acceptance tests: reports must follow the merchant's own calendar day."""

import io
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from salesdesk.cli import main
from salesdesk.clock import FixedClock
from salesdesk.models import Order
from salesdesk.render import render_daily_text
from salesdesk.service import ReportService
from salesdesk.settings import SettingsStore
from salesdesk.storage import Storage

UTC = timezone.utc
LA = "America/Los_Angeles"
KOLKATA = "Asia/Kolkata"
AUCKLAND = "Pacific/Auckland"


def local(tz, *args):
    return datetime(*args, tzinfo=ZoneInfo(tz))


@pytest.fixture
def env():
    storage = Storage(":memory:")
    settings = SettingsStore(storage)
    yield storage, settings
    storage.close()


def service_at(env, instant):
    storage, settings = env
    return ReportService(storage, settings, FixedClock(instant))


def test_la_evening_order_counts_on_local_day(env):
    storage, settings = env
    settings.register("shop", "LA Shop", LA)
    storage.add_orders([
        Order("morning", "shop", 1000, local(LA, 2026, 3, 10, 9, 0)),
        # 21:30 PDT == 04:30 UTC the next day
        Order("late", "shop", 2500, local(LA, 2026, 3, 10, 21, 30)),
        Order("next", "shop", 400, local(LA, 2026, 3, 11, 8, 0)),
    ])
    svc = service_at(env, datetime(2026, 3, 12, 12, 0, tzinfo=UTC))
    r10 = svc.daily_report("shop", date(2026, 3, 10))
    assert sorted(r10.order_ids) == ["late", "morning"]
    assert r10.total_cents == 3500
    r11 = svc.daily_report("shop", date(2026, 3, 11))
    assert r11.order_ids == ["next"]
    assert r11.total_cents == 400


def test_kolkata_order_just_after_local_midnight(env):
    storage, settings = env
    settings.register("mumbai", "Mumbai Store", KOLKATA, "INR")
    storage.add_orders([
        Order("before", "mumbai", 700, local(KOLKATA, 2026, 5, 3, 23, 50)),
        # 00:15 IST == 18:45 UTC on the previous day
        Order("after", "mumbai", 1200, local(KOLKATA, 2026, 5, 4, 0, 15)),
        Order("noon", "mumbai", 300, local(KOLKATA, 2026, 5, 4, 12, 0)),
    ])
    svc = service_at(env, datetime(2026, 5, 6, tzinfo=UTC))
    r4 = svc.daily_report("mumbai", date(2026, 5, 4))
    assert sorted(r4.order_ids) == ["after", "noon"]
    assert r4.total_cents == 1500
    r3 = svc.daily_report("mumbai", date(2026, 5, 3))
    assert r3.order_ids == ["before"]


def test_auckland_default_report_is_local_today_not_yesterday(env):
    storage, settings = env
    settings.register("akl", "Auckland Cafe", AUCKLAND, "NZD")
    storage.add_orders([
        Order("yday", "akl", 5000, local(AUCKLAND, 2026, 6, 14, 15, 0)),
        Order("today1", "akl", 1100, local(AUCKLAND, 2026, 6, 15, 7, 30)),
        Order("today2", "akl", 900, local(AUCKLAND, 2026, 6, 15, 8, 45)),
    ])
    # 09:00 NZST on 15 June == 21:00 UTC on 14 June
    svc = service_at(env, local(AUCKLAND, 2026, 6, 15, 9, 0))
    report = svc.daily_report("akl")
    assert report.day == date(2026, 6, 15)
    assert report.total_cents == 2000
    assert sorted(report.order_ids) == ["today1", "today2"]
    assert svc.yesterday_report("akl").order_ids == ["yday"]


def test_la_default_report_in_the_evening(env):
    storage, settings = env
    settings.register("shop", "LA Shop", LA)
    storage.add_orders([
        Order("a", "shop", 1500, local(LA, 2026, 7, 1, 10, 0)),
        Order("b", "shop", 500, local(LA, 2026, 7, 1, 17, 30)),
    ])
    # 18:00 PDT on 1 July == 01:00 UTC on 2 July
    svc = service_at(env, local(LA, 2026, 7, 1, 18, 0))
    report = svc.daily_report("shop")
    assert report.day == date(2026, 7, 1)
    assert report.total_cents == 2000
    assert report.order_count == 2


def test_breakdown_buckets_by_local_day(env):
    storage, settings = env
    settings.register("mumbai", "Mumbai Store", KOLKATA, "INR")
    storage.add_orders([
        Order("d1-early", "mumbai", 100, local(KOLKATA, 2026, 5, 1, 0, 5)),
        Order("d1-late", "mumbai", 200, local(KOLKATA, 2026, 5, 1, 23, 55)),
        Order("d2-early", "mumbai", 400, local(KOLKATA, 2026, 5, 2, 3, 0)),
        Order("d3-late", "mumbai", 800, local(KOLKATA, 2026, 5, 3, 23, 59)),
        Order("outside", "mumbai", 1600, local(KOLKATA, 2026, 5, 4, 0, 1)),
    ])
    svc = service_at(env, datetime(2026, 5, 10, tzinfo=UTC))
    rows = svc.breakdown("mumbai", date(2026, 5, 1), date(2026, 5, 3))
    assert [(r.day, r.order_count, r.total_cents) for r in rows] == [
        (date(2026, 5, 1), 2, 300),
        (date(2026, 5, 2), 1, 400),
        (date(2026, 5, 3), 1, 800),
    ]


def test_auckland_week_to_date_uses_local_week(env):
    storage, settings = env
    settings.register("akl", "Auckland Cafe", AUCKLAND, "NZD")
    storage.add_orders([
        # Monday 8 June 2026, 00:30 NZST == Sunday 7 June 12:30 UTC
        Order("mon", "akl", 300, local(AUCKLAND, 2026, 6, 8, 0, 30)),
        Order("tue", "akl", 700, local(AUCKLAND, 2026, 6, 9, 23, 30)),
        Order("sun-before", "akl", 999, local(AUCKLAND, 2026, 6, 7, 22, 0)),
    ])
    # Tuesday 9 June 23:45 NZST == 11:45 UTC Tuesday
    svc = service_at(env, local(AUCKLAND, 2026, 6, 9, 23, 45))
    rows = svc.week_to_date("akl")
    assert [(r.day, r.total_cents) for r in rows] == [
        (date(2026, 6, 8), 300),
        (date(2026, 6, 9), 700),
    ]


def test_dst_spring_forward_day_is_23_hours(env):
    storage, settings = env
    settings.register("shop", "LA Shop", LA)
    # US DST starts 2026-03-08 at 02:00 PST -> 03:00 PDT.
    storage.add_orders([
        Order("prev-day", "shop", 1, local(LA, 2026, 3, 7, 23, 50)),     # 07:50 UTC Mar 8
        Order("first", "shop", 10, local(LA, 2026, 3, 8, 0, 30)),        # 08:30 UTC Mar 8
        Order("last", "shop", 100, local(LA, 2026, 3, 8, 23, 30)),       # 06:30 UTC Mar 9
        Order("next-day", "shop", 1000, local(LA, 2026, 3, 9, 0, 10)),   # 07:10 UTC Mar 9
    ])
    svc = service_at(env, datetime(2026, 3, 20, tzinfo=UTC))
    report = svc.daily_report("shop", date(2026, 3, 8))
    assert sorted(report.order_ids) == ["first", "last"]
    assert report.total_cents == 110
    rows = svc.breakdown("shop", date(2026, 3, 7), date(2026, 3, 9))
    assert [r.total_cents for r in rows] == [1, 110, 1000]


def test_utc_customer_unchanged(env):
    storage, settings = env
    settings.register("ldn", "UTC Shop", "UTC")
    storage.add_orders([
        Order("a", "ldn", 100, datetime(2026, 3, 9, 23, 59, 59, tzinfo=UTC)),
        Order("b", "ldn", 200, datetime(2026, 3, 10, 0, 0, tzinfo=UTC)),
        Order("c", "ldn", 400, datetime(2026, 3, 10, 23, 59, 59, tzinfo=UTC)),
        Order("d", "ldn", 800, datetime(2026, 3, 11, 0, 0, tzinfo=UTC)),
    ])
    svc = service_at(env, datetime(2026, 3, 10, 23, 30, tzinfo=UTC))
    report = svc.daily_report("ldn")
    assert report.day == date(2026, 3, 10)
    assert sorted(report.order_ids) == ["b", "c"]
    assert report.total_cents == 600
    rows = svc.breakdown("ldn", date(2026, 3, 9), date(2026, 3, 11))
    assert [r.total_cents for r in rows] == [100, 600, 800]


def test_rendered_report_shows_local_day(env):
    storage, settings = env
    settings.register("akl", "Auckland Cafe", AUCKLAND, "NZD")
    storage.add_order(Order("x", "akl", 4250, local(AUCKLAND, 2026, 6, 15, 6, 0)))
    svc = service_at(env, local(AUCKLAND, 2026, 6, 15, 10, 0))
    text = render_daily_text(svc.daily_report("akl"))
    assert "2026-06-15 (Pacific/Auckland)" in text
    assert "NZ$42.50" in text


def test_cli_report_uses_customer_timezone(tmp_path):
    db = str(tmp_path / "s.db")
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text(
        "order_id,customer_id,amount,created_at\n"
        "a,shop,10.00,2026-03-10T09:00:00-07:00\n"
        "b,shop,20.00,2026-03-10T22:30:00-07:00\n"
        "c,shop,40.00,2026-03-11T09:00:00-07:00\n"
    )
    out = io.StringIO()
    assert main(["--db", db, "add-customer", "shop", "LA Shop", "--timezone", LA], out) == 0
    assert main(["--db", db, "import", str(csv_path)], out) == 0
    out = io.StringIO()
    # 23:00 PDT on 10 March == 06:00 UTC on 11 March
    assert main(["--db", db, "--now", "2026-03-11T06:00:00Z", "report", "shop"], out) == 0
    text = out.getvalue()
    assert "2026-03-10 (America/Los_Angeles)" in text
    assert "Total:     $30.00" in text
