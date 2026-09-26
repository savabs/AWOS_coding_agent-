"""Ledgerly: a small ledger service that produces monthly account summaries.

Modules:
    models    -- Account / Transaction records and row (CSV) conversion
    money     -- Decimal helpers: parsing, rounding, currency conversion, fees
    fx        -- FX rate table (base currency per unit of foreign currency)
    storage   -- LedgerStore: in-memory store of raw ledger rows + accounts
    rules     -- categorisation rules and merchant-name normalisation
    summary   -- the monthly summary builder
    report    -- text / JSON rendering of a summary
    fixtures  -- deterministic data generator for demos and tests
    cli       -- command line entry point (python -m ledger)
"""

__version__ = "1.4.2"
