"""Report tests. All merchants here are on UTC."""

import io
from datetime import date, datetime, timezone

from salesdesk.cli import main
from salesdesk.clock import FixedClock
from salesdesk.models import Order
from salesdesk.render import render_breakdown_csv, render_breakdown_text, render_daily_text
from salesdesk.service import ReportService

UTC = timezone.utc


def _seed(storage, settings):
    settings.register("acme", "Acme Ltd", "UTC")
    storage.add_orders([
        Order("o1", "acme", 1000, datetime(2026, 3, 9, 23, 59, tzinfo=UTC)),
        Order("o2", "acme", 2500, datetime(2026, 3, 10, 0, 1, tzinfo=UTC)),
        Order("o3", "acme", 1500, datetime(2026, 3, 10, 12, 0, tzinfo=UTC)),
        Order("o4", "acme", 700, datetime(2026, 3, 10, 13, 0, tzinfo=UTC), status="refunded"),
        Order("o5", "acme", 900, datetime(2026, 3, 10, 14, 0, tzinfo=UTC), status="cancelled"),
        Order("o6", "acme", 4000, datetime(2026, 3, 11, 0, 0, tzinfo=UTC)),
    ])


def test_daily_report_explicit_day(storage, settings):
    _seed(storage, settings)
    service = ReportService(storage, settings, FixedClock(datetime(2026, 3, 12, 9, 0, tzinfo=UTC)))
    report = service.daily_report("acme", date(2026, 3, 10))
    assert report.total_cents == 4000
    assert report.order_count == 2
    assert report.refunded_cents == 700
    assert report.order_ids == ["o2", "o3"]
    assert report.average_order_cents == 2000


def test_daily_report_defaults_to_today(storage, settings):
    _seed(storage, settings)
    service = ReportService(storage, settings, FixedClock(datetime(2026, 3, 10, 18, 0, tzinfo=UTC)))
    report = service.daily_report("acme")
    assert report.day == date(2026, 3, 10)
    assert report.total_cents == 4000


def test_yesterday_report(storage, settings):
    _seed(storage, settings)
    service = ReportService(storage, settings, FixedClock(datetime(2026, 3, 11, 6, 0, tzinfo=UTC)))
    report = service.yesterday_report("acme")
    assert report.day == date(2026, 3, 10)
    assert report.order_count == 2


def test_breakdown(storage, settings):
    _seed(storage, settings)
    service = ReportService(storage, settings, FixedClock(datetime(2026, 3, 12, tzinfo=UTC)))
    rows = service.breakdown("acme", date(2026, 3, 9), date(2026, 3, 11))
    assert [(r.day.day, r.order_count, r.total_cents) for r in rows] == [
        (9, 1, 1000), (10, 2, 4000), (11, 1, 4000)]
    csv_text = render_breakdown_csv(rows)
    assert csv_text.splitlines()[2] == "2026-03-10,2,4000,700"
    text = render_breakdown_text(rows)
    assert "TOTAL" in text and "$90.00" in text


def test_week_to_date(storage, settings):
    _seed(storage, settings)
    # 2026-03-11 is a Wednesday
    service = ReportService(storage, settings, FixedClock(datetime(2026, 3, 11, 20, 0, tzinfo=UTC)))
    rows = service.week_to_date("acme")
    assert [r.day for r in rows] == [date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11)]


def test_render_daily_text(storage, settings):
    _seed(storage, settings)
    service = ReportService(storage, settings, FixedClock(datetime(2026, 3, 10, 18, 0, tzinfo=UTC)))
    text = render_daily_text(service.daily_report("acme"))
    assert "Day:       2026-03-10 (UTC)" in text
    assert "Total:     $40.00" in text
    assert "Refunded:  $7.00" in text


def test_cli_end_to_end(tmp_path):
    db = str(tmp_path / "s.db")
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text(
        "order_id,customer_id,amount,created_at\n"
        "a,acme,10.00,2026-03-10T01:00:00Z\n"
        "b,acme,20.00,2026-03-10T22:00:00Z\n"
        "c,acme,30.00,2026-03-11T02:00:00Z\n"
    )
    out = io.StringIO()
    assert main(["--db", db, "add-customer", "acme", "Acme"], out) == 0
    assert main(["--db", db, "import", str(csv_path)], out) == 0
    out = io.StringIO()
    assert main(["--db", db, "--now", "2026-03-10T23:00:00Z", "report", "acme"], out) == 0
    assert "Total:     $30.00" in out.getvalue()
    out = io.StringIO()
    assert main(["--db", db, "report", "nobody"], out) == 2
