# invoice_data_report (kind: data)

Tests a data job, not a coding job: turn three messy monthly invoice exports into two exact reports
(top-10 customers by EUR total, duplicate invoices). The mess is what real billing exports look like:
UTF-8 BOM, a renamed/reordered header after a system upgrade, thousands separators, lower-case currency codes,
inconsistent customer names, credit notes, true duplicates (one re-exported in the next month) and
look-alike invoices that are NOT duplicates. `generate_data.py` (seed 20260923) regenerates data + reference.
