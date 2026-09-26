"""The monthly summary of a large account must be at least 5x faster than the
original implementation, measured in the same process on the same data.

The timed workload is what a real request does: load the account's ledger
rows and FX rates into fresh objects, then build the monthly summary.  Both
implementations get identical inputs (taken from the frozen original
generator) through the public API.
"""

import gc
import time

import slow_ledger.fixtures as slow_fixtures
import slow_ledger.fx as slow_fx
import slow_ledger.models as slow_models
import slow_ledger.rulebook as slow_rulebook
import slow_ledger.storage as slow_storage
import slow_ledger.summary as slow_summary

import ledger.fx as fx_mod
import ledger.models as models
import ledger.rulebook as rulebook
import ledger.storage as storage
import ledger.summary as summary

REQUIRED_SPEEDUP = 5.0
SEED, SIZE, YEAR, MONTH = 7, "large", 2025, 12

_source = slow_fixtures.generate_ledger(SEED, SIZE)
ROWS = [dict(row) for row in _source.store._rows]
RATES = list(_source.fx._rates)
ACCOUNTS = [
    dict(account_id=a.account_id, holder=a.holder, base_currency=a.base_currency,
         opening_balance=a.opening_balance, opened_on=a.opened_on)
    for a in _source.store.accounts()
]
ACCOUNT_ID = _source.account_id


def _run(storage_mod, fx_module, models_mod, rulebook_mod, summary_mod):
    store = storage_mod.LedgerStore()
    for fields in ACCOUNTS:
        store.add_account(models_mod.Account(**fields))
    for row in ROWS:
        store.add_row(dict(row))
    fx = fx_module.FxTable("GBP")
    for on_text, currency, rate in RATES:
        fx.add_rate(on_text, currency, rate)
    categoriser = rulebook_mod.default_categoriser()
    return summary_mod.build_monthly_summary(store, fx, categoriser, ACCOUNT_ID, YEAR, MONTH)


def _best_of(n, fn):
    best = None
    result = None
    for _ in range(n):
        gc.collect()
        started = time.perf_counter()
        result = fn()
        elapsed = time.perf_counter() - started
        best = elapsed if best is None else min(best, elapsed)
    return best, result


def test_large_account_summary_is_5x_faster():
    new_time, new_result = _best_of(
        3, lambda: _run(storage, fx_mod, models, rulebook, summary))
    old_time, old_result = _best_of(
        2, lambda: _run(slow_storage, slow_fx, slow_models, slow_rulebook, slow_summary))
    assert new_result.to_dict() == old_result.to_dict()
    speedup = old_time / new_time
    print(f"\noriginal {old_time:.3f}s, current {new_time:.3f}s, speedup {speedup:.1f}x")
    assert speedup >= REQUIRED_SPEEDUP, (
        f"summary is only {speedup:.2f}x faster than the original "
        f"({new_time:.3f}s vs {old_time:.3f}s); need >= {REQUIRED_SPEEDUP}x"
    )
