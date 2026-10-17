"""salesdesk — a small sales reporting service.

Merchants (customers of salesdesk) record orders; salesdesk produces daily
sales reports and multi-day breakdowns for them.

Module map:
    models       plain dataclasses (Customer, Order, DayTotal, DailyReport)
    clock        injectable clocks (SystemClock, FixedClock)
    timeutil     timestamp conversion and calendar-day helpers
    currency     integer-cents money helpers and formatting
    storage      sqlite persistence for orders and customers
    settings     customer settings (timezone, currency) with validation
    importer     CSV order import
    aggregation  per-day totals and breakdowns
    service      ReportService — the public entry point used by the CLI
    render       text / CSV rendering of reports
    cli          command line interface
"""

__version__ = "1.4.2"
