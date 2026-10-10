"""Deterministic ledger generator for demos, tests and load testing.

``generate_ledger(seed, size)`` builds a store with a primary current account
(and a smaller joint account sharing the store), a two-year FX table and the
default rulebook.  The same seed always produces exactly the same data.
"""

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .fx import FxTable
from .models import Account
from .rulebook import LOCAL_NOUNS, LOCAL_PREFIXES, NAMED_MERCHANTS, default_categoriser
from .storage import LedgerStore

SIZES = {"tiny": 6, "small": 15, "medium": 100, "large": 500}
FIRST_MONTH = (2024, 1)
MONTHS = 24
BASE = "GBP"
START_RATES = {"EUR": 856_000, "USD": 787_000, "CHF": 893_000, "SEK": 74_500}  # micro-units
CITIES_BY_CURRENCY = {
    "GBP": ("LONDON", "LEEDS", "BRISTOL", "YORK", "BATH", "MANCHESTER", "ONLINE"),
    "EUR": ("PARIS", "BERLIN", "MADRID"),
    "CHF": ("ZURICH",),
    "SEK": ("STOCKHOLM",),
    "USD": ("NEW YORK", "BOSTON"),
}
SPEND_RANGES = {  # category -> (min pence, max pence)
    "Groceries": (150, 12000), "Eating out": (250, 6500), "Fuel": (2000, 9000),
    "Travel": (180, 42000), "Subscriptions": (499, 1799), "Shopping": (399, 45000),
    "Home": (500, 30000), "Health": (199, 4500), "Bills": (2500, 18000),
    "Car": (3000, 60000), "Gifts": (1500, 8000), "Entertainment": (800, 3500),
    "Fitness": (1200, 6000), "Pets": (2500, 22000),
}


@dataclass
class LedgerBundle:
    store: LedgerStore
    fx: FxTable
    categoriser: object
    account_id: str
    joint_account_id: str


def _months():
    year, month = FIRST_MONTH
    for _ in range(MONTHS):
        yield year, month
        month += 1
        if month == 13:
            year, month = year + 1, 1


def _business_days(start, end):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def build_fx(rng):
    fx = FxTable(BASE)
    levels = dict(START_RATES)
    corrections = []
    for day in _business_days(date(2023, 12, 27), date(2025, 12, 31)):
        for currency in sorted(levels):
            step = max(1, levels[currency] // 400)
            levels[currency] = max(step, levels[currency] + rng.randint(-step, step))
            fx.add_rate(day, currency, Decimal(levels[currency]).scaleb(-6))
            if rng.random() < 0.03:
                corrections.append((day, currency, levels[currency] + rng.randint(-step, step)))
    # The provider's corrections feed arrives after the daily files.
    for day, currency, micro in corrections:
        fx.add_rate(day, currency, Decimal(micro).scaleb(-6))
    return fx


def _merchant_catalogue():
    catalogue = []
    for keyword, category, currency in NAMED_MERCHANTS:
        catalogue.append((keyword, category, currency))
    for prefix in LOCAL_PREFIXES:
        for noun, category in LOCAL_NOUNS:
            catalogue.append((f"{prefix} {noun}", category, "GBP"))
    catalogue.append(("TESCO PETROL", "Fuel", "GBP"))
    catalogue.append(("SAINSBURYS FUEL", "Fuel", "GBP"))
    catalogue.append(("CORNER SHOP", "Groceries", "GBP"))  # matches no rule
    catalogue.append(("MARKET STALL", "Groceries", "EUR"))  # matches no rule
    return catalogue


def _pence(rng, lo, hi):
    return Decimal(rng.randint(lo, hi)).scaleb(-2)


def _card_purchase(rng, catalogue, card):
    if rng.random() < 0.2:
        pool = [m for m in catalogue if m[2] != "GBP"]
    else:
        pool = [m for m in catalogue if m[2] == "GBP"]
    keyword, category, currency = rng.choice(pool)
    if currency != "GBP" and rng.random() < 0.2:
        currency = "GBP"  # foreign merchant charged in sterling
    lo, hi = SPEND_RANGES[category]
    if rng.random() < 0.04:
        hi *= 3
    amount = -_pence(rng, lo, hi)
    store_no = 100 + (sum(map(ord, keyword)) * 7 + rng.randint(0, 3)) % 900
    cities = CITIES_BY_CURRENCY[currency]
    city = cities[store_no % len(cities)]  # each store is in one city
    channel = rng.choice((f"CARD {card}", f"CARD {card}", "POS", "CNP"))
    return f"{channel} {keyword} {store_no:04d} {city}", amount, currency


def _month_rows(rng, catalogue, account_id, card, year, month, per_month, salary):
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    rows = []

    def add(day, desc, amount, currency=BASE):
        rows.append((date(year, month, day), desc, amount, currency))

    add(1, "SO LANDLORD RENT FLAT 2", Decimal("-1250.00"))
    add(min(25, last), "FPI ACME LTD SALARY", salary)
    if month % 3 == 0:
        add(last, "INTEREST PAID", _pence(rng, 50, 900))
    add(rng.randint(1, 28), "DD COUNCIL TAX", Decimal("-162.40"))
    extras = max(0, per_month - len(rows))
    for _ in range(extras):
        day = rng.randint(1, last)
        roll = rng.random()
        if roll < 0.04:
            add(day, f"ATM CASH WITHDRAWAL {rng.choice(CITIES_BY_CURRENCY['GBP'])}",
                -Decimal(rng.choice((20, 40, 50, 100, 200))).quantize(Decimal("0.01")))
        elif roll < 0.07:
            add(day, f"TFR TO SAVINGS {rng.randint(1000, 9999)}", -_pence(rng, 5000, 50000))
        elif roll < 0.09:
            add(day, f"FPI {rng.choice(('J SMITH', 'A KHAN', 'M LEE'))} REF {rng.randint(10, 99)}",
                _pence(rng, 1000, 30000))
        elif roll < 0.11:
            keyword = rng.choice(("AMAZON", "ARGOS", "JOHN LEWIS", "EASYJET"))
            add(day, f"REFUND {keyword} {rng.randint(100, 999)}", _pence(rng, 500, 20000))
        else:
            desc, amount, currency = _card_purchase(rng, catalogue, card)
            add(day, desc, amount, currency)
    return rows


def _add_account_rows(store, rng, catalogue, account_id, per_month, salary):
    card = f"{rng.randint(1000, 9999)}"
    seq = 0
    for year, month in _months():
        rows = _month_rows(rng, catalogue, account_id, card, year, month, per_month, salary)
        # Postings are exported in arrival order, not date order.
        rng.shuffle(rows)
        for posted, desc, amount, currency in rows:
            seq += 1
            padded = desc if rng.random() < 0.9 else f"  {desc.replace(' ', '  ', 1)} "
            store.add_row({
                "id": f"{account_id}-{seq:06d}",
                "account": account_id,
                "date": posted.isoformat(),
                "desc": padded,
                "amount": str(amount),
                "currency": currency,
            })


def generate_ledger(seed, size="small"):
    if size not in SIZES:
        raise ValueError(f"unknown size {size!r}; choose from {sorted(SIZES)}")
    per_month = SIZES[size]
    rng = random.Random(seed)
    fx = build_fx(rng)
    store = LedgerStore()
    catalogue = _merchant_catalogue()
    primary = f"ACC-{seed:04d}"
    joint = f"JNT-{seed:04d}"
    opened = date(2023, 12, 1)
    store.add_account(Account(primary, "Primary holder", BASE, _pence(rng, 0, 500000), opened))
    store.add_account(Account(joint, "Joint holders", BASE, _pence(rng, 0, 100000), opened))
    # Bigger accounts are business-style accounts with proportionally bigger inflows.
    scale = max(Decimal(1), Decimal(per_month) / Decimal(40))
    salary = (_pence(rng, 250000, 480000) * scale).quantize(Decimal("0.01"))
    _add_account_rows(store, rng, catalogue, primary, per_month, salary)
    _add_account_rows(store, rng, catalogue, joint, max(6, per_month // 10), _pence(rng, 50000, 90000))
    return LedgerBundle(store, fx, default_categoriser(), primary, joint)
