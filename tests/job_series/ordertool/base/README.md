# ordertool

A small order-management command-line tool. Orders are stored in a CSV file.

    python -m ordertool --db orders.csv add --customer "Ada" --email ada@example.com --item WIDGET:2:9.99
    python -m ordertool --db orders.csv list --status paid
    python -m ordertool --db orders.csv set-status ORD-0001 paid
    python -m ordertool --db orders.csv export --output orders_export.csv
    python -m ordertool --db orders.csv export --from 2024-03-01 --to 2024-03-31 --status shipped
    python -m ordertool --db orders.csv report

Only the Python standard library is required. Run the tests with `python -m pytest`.
