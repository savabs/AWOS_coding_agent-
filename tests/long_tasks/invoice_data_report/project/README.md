# Invoice exports (June-August 2026)

`data/` holds one CSV export per month from our billing system: `2026-06.csv`, `2026-07.csv`, `2026-08.csv`.
Each row is one invoice (or credit note):

| column | meaning |
|--------|---------|
| `invoice_id` | invoice number, e.g. `INV-2026-00123`; credit notes start with `CN-` |
| `invoice_date` | ISO date `YYYY-MM-DD` |
| `customer_id` | stable customer id, e.g. `C101` — the name is typed by hand, trust the id |
| `customer_name` | customer name |
| `amount` | invoice amount in the invoice currency; credit notes are negative |
| `currency` | `EUR`, `USD`, `GBP` or `CHF` |

The billing system was upgraded during the quarter, so not every export looks exactly like this.

`data/fx_rates.csv` has monthly rates: `month,currency,rate_to_eur`. An amount in EUR is
`amount * rate_to_eur`, using the rate for the month of the invoice's `invoice_date`. EUR amounts need no conversion.

## What I need

**`reports/top_customers.csv`** — our 10 biggest customers by total billed in EUR.
- Columns, in this order: `rank,customer_id,customer_name,total_eur`
- `total_eur` = sum of the customer's invoices converted to EUR, credit notes included (they reduce the total).
  An invoice that appears more than once in the exports counts **once**. Round only the final total, to 2 decimals.
- Sorted by `total_eur` descending, `rank` 1..10. Any clean spelling of the customer's name is fine.

**`reports/duplicate_invoices.csv`** — invoices that were billed more than once.
- A duplicate is the same `invoice_id` appearing more than once anywhere in the exports.
  Different invoice numbers are different invoices, even if customer and amount match.
- Columns, in this order: `invoice_id,customer_id,invoice_date,currency,amount,times_billed`
- `amount` in the original currency with 2 decimals; `currency` upper case; `times_billed` = number of occurrences.
- Sorted by `invoice_id` ascending.

Both files: plain CSV with a header row, no thousands separators.
