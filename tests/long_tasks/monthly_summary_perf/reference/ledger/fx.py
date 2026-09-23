"""FX rate table.

Rates are quoted as *units of base currency per one unit of foreign
currency* and are published on business days only.  A transaction uses the
most recent rate published on or before its posting date.  The provider
sometimes re-publishes a corrected rate for a day it already sent; the rate
received last for a given day wins.

Lookups use a per-currency index sorted by (date, arrival order), built
lazily and discarded whenever a rate is added.
"""

from bisect import bisect_right
from datetime import date

from .money import convert, parse_rate, to_cents

_ONE = parse_rate("1")


class MissingRateError(KeyError):
    pass


class FxTable:
    def __init__(self, base_currency):
        self.base_currency = base_currency.upper()
        self._rates = []  # (iso date string, currency, Decimal rate), in arrival order
        self._index = None  # currency -> (sorted dates, rates)
        self._memo = {}

    def add_rate(self, on, currency, rate):
        on_text = on.isoformat() if isinstance(on, date) else str(on).strip()
        date.fromisoformat(on_text)  # validate
        self._rates.append((on_text, currency.strip().upper(), parse_rate(rate)))
        self._index = None
        self._memo = {}

    def __len__(self):
        return len(self._rates)

    def currencies(self):
        return sorted({cur for _, cur, _ in self._rates})

    def _build_index(self):
        grouped = {}
        for seq, (on_text, cur, rate) in enumerate(self._rates):
            grouped.setdefault(cur, []).append((date.fromisoformat(on_text), seq, rate))
        index = {}
        for cur, entries in grouped.items():
            entries.sort(key=lambda e: (e[0], e[1]))
            index[cur] = ([e[0] for e in entries], [e[2] for e in entries])
        self._index = index

    def rate(self, currency, on_date):
        """Rate for ``currency`` in force on ``on_date``."""
        currency = currency.upper()
        if currency == self.base_currency:
            return _ONE
        key = (currency, on_date)
        cached = self._memo.get(key)
        if cached is not None:
            return cached
        if self._index is None:
            self._build_index()
        dates, rates = self._index.get(currency, ((), ()))
        pos = bisect_right(dates, on_date)
        if pos == 0:
            raise MissingRateError(f"no {currency} rate on or before {on_date}")
        result = rates[pos - 1]
        self._memo[key] = result
        return result

    def to_base(self, amount, currency, on_date):
        """Convert an amount in ``currency`` into the base currency."""
        if currency.upper() == self.base_currency:
            return to_cents(amount)
        return convert(amount, self.rate(currency, on_date))
