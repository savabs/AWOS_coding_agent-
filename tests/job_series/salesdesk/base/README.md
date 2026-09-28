# salesdesk

A small sales reporting service. Merchants record orders; salesdesk produces
daily sales reports and multi-day breakdowns for them. Data lives in one sqlite
database (`--db`, default `salesdesk.db`).

    python -m salesdesk.cli --db sales.db add-customer acme "Acme" --timezone Europe/Berlin --currency EUR
    python -m salesdesk.cli --db sales.db import orders.csv
    python -m salesdesk.cli --db sales.db report acme
    python -m salesdesk.cli --db sales.db --now 2026-03-10T09:00:00Z report acme --yesterday
    python -m salesdesk.cli --db sales.db breakdown acme 2026-03-01 2026-03-07 --csv

Only the Python standard library is required. Run the tests with `python -m pytest`.
