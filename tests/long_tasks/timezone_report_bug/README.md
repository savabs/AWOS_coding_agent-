# timezone_report_bug

Tests cross-module debugging from a user symptom report: the wrong totals show up in the report, but the causes are in `timeutil` (UTC day boundaries and a UTC "today"), `aggregation` (buckets orders by UTC date) and `service` (never passes the customer's timezone).
It's realistic because timestamps are stored correctly as naive UTC and the visible tests cover only UTC customers, which is how bugs like this reach production. There are decoys too (currency rounding, zone aliases, storage string comparison) that look suspicious but are correct.
The hidden tests check LA, Kolkata (+05:30) and Auckland customers, orders near midnight, the default "today" report, week-to-date, a 23-hour US DST day (which also catches a "start + 24h" fix), UTC customers and the CLI.
