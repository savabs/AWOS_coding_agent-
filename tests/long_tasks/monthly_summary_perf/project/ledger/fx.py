"""FX rate table.

Rates are quoted as *units of base currency per one unit of foreign
currency* and are published on business days only.  A transaction uses the
most recent rate published on or before its posting date.  The provider
sometimes re-publishes a corrected rate for a day it already sent; the rate
received last for a given day wins.
"""

from datetime import date

from .money import convert, parse_rate, to_cents


class MissingRateError(KeyError):
    pass


class FxTable:
    def __init__(self, base_currency):
        self.base_currency = base_currency.upper()
        self._rates = []  # (iso date string, currency, Decimal rate), in arrival order

    def add_rate(self, on, currency, rate):
        on_text = on.isoformat() if isinstance(on, date) else str(on).strip()
        date.fromisoformat(on_text)  # validate
        self._rates.append((on_text, currency.strip().upper(), parse_rate(rate)))

    def __len__(self):
        return len(self._rates)

    def currencies(self):
        return sorted({cur for _, cur, _ in self._rates})

    def rate(self, currency, on_date):
        """Rate for ``currency`` in force on ``on_date``."""
        currency = currency.upper()
        if currency == self.base_currency:
            return parse_rate("1")
        best_date = None
        best_rate = None
        for on_text, cur, rate in self._rates:
            published = date.fromisoformat(on_text)
            if cur != currency or published > on_date:
                continue
            if best_date is None or published >= best_date:
                best_date = published
                best_rate = rate
        if best_rate is None:
            raise MissingRateError(f"no {currency} rate on or before {on_date}")
        return best_rate

    def to_base(self, amount, currency, on_date):
        """Convert an amount in ``currency`` into the base currency."""
        if currency.upper() == self.base_currency:
            return to_cents(amount)
        return convert(amount, self.rate(currency, on_date))
