"""Monthly account summary.

For one account and one calendar month the summary reports, in the
account's base currency:

* opening balance (account opening balance + everything posted before the
  month) and closing balance;
* the closing balance of every day of the month;
* income, spending, net movement and the number of transactions;
* foreign-transaction fees (charged per foreign transaction, informational);
* average spend per debit and the largest single expense;
* totals per category for the month and year-to-date;
* the top five merchants by spend.

Every transaction is converted to the base currency individually (rate of
its posting date) before being added up.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

from .money import ZERO, average, fx_fee
from .rules import merchant_name

TOP_MERCHANTS = 5


@dataclass
class MonthlySummary:
    account_id: str
    currency: str
    year: int
    month: int
    opening_balance: object = ZERO
    closing_balance: object = ZERO
    total_income: object = ZERO
    total_spending: object = ZERO
    net_movement: object = ZERO
    fx_fees: object = ZERO
    txn_count: int = 0
    debit_count: int = 0
    average_spend: object = ZERO
    largest_expense: tuple = None  # (txn_id, amount) or None
    categories: dict = field(default_factory=dict)
    ytd_categories: dict = field(default_factory=dict)
    top_merchants: list = field(default_factory=list)  # [(merchant, spend)]
    daily_balances: list = field(default_factory=list)  # [(date, balance)]

    def to_dict(self):
        return {
            "account_id": self.account_id,
            "currency": self.currency,
            "period": f"{self.year:04d}-{self.month:02d}",
            "opening_balance": str(self.opening_balance),
            "closing_balance": str(self.closing_balance),
            "total_income": str(self.total_income),
            "total_spending": str(self.total_spending),
            "net_movement": str(self.net_movement),
            "fx_fees": str(self.fx_fees),
            "txn_count": self.txn_count,
            "debit_count": self.debit_count,
            "average_spend": str(self.average_spend),
            "largest_expense": (
                None if self.largest_expense is None
                else [self.largest_expense[0], str(self.largest_expense[1])]
            ),
            "categories": {k: str(v) for k, v in sorted(self.categories.items())},
            "ytd_categories": {k: str(v) for k, v in sorted(self.ytd_categories.items())},
            "top_merchants": [[name, str(v)] for name, v in self.top_merchants],
            "daily_balances": [[d.isoformat(), str(v)] for d, v in self.daily_balances],
        }


def month_bounds(year, month):
    if not 1 <= month <= 12:
        raise ValueError(f"bad month {month}")
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _settled(fx, txn, base_currency):
    """Transaction amount in the account's base currency."""
    return fx.to_base(txn.amount, txn.currency, txn.posted_on)


def _balance_on(store, fx, account, opening, first, day):
    """Balance at the end of ``day``, starting from the month's opening."""
    balance = opening
    for txn in store.transactions(account.account_id, start=first, end=day):
        balance += _settled(fx, txn, account.base_currency)
    return balance


def build_monthly_summary(store, fx, categoriser, account_id, year, month):
    account = store.account(account_id)
    base = account.base_currency
    if fx.base_currency != base:
        raise ValueError(f"FX table is in {fx.base_currency}, account is in {base}")
    first, last = month_bounds(year, month)
    summary = MonthlySummary(account_id=account_id, currency=base, year=year, month=month)

    # Opening balance: everything posted before the month.
    opening = account.opening_balance
    for txn in store.transactions(account_id, end=first - timedelta(days=1)):
        opening += _settled(fx, txn, base)
    summary.opening_balance = opening

    # The month itself.
    month_txns = store.transactions(account_id, start=first, end=last)
    income = ZERO
    spending = ZERO
    fees = ZERO
    merchant_spend = {}
    largest = None
    for txn in month_txns:
        settled = _settled(fx, txn, base)
        if settled >= 0:
            income += settled
        else:
            spending += -settled
            summary.debit_count += 1
            name = merchant_name(txn.description)
            merchant_spend[name] = merchant_spend.get(name, ZERO) + (-settled)
            if largest is None or settled < largest[1]:
                largest = (txn.txn_id, settled)
        if txn.currency != base:
            fees += fx_fee(settled)
        category = categoriser.categorise(txn)
        summary.categories[category] = summary.categories.get(category, ZERO) + settled
    summary.txn_count = len(month_txns)
    summary.total_income = income
    summary.total_spending = spending
    summary.net_movement = income - spending
    summary.fx_fees = fees
    summary.average_spend = average(spending, summary.debit_count)
    summary.largest_expense = None if largest is None else (largest[0], -largest[1])

    ranked = sorted(merchant_spend.items(), key=lambda item: (-item[1], item[0]))
    summary.top_merchants = ranked[:TOP_MERCHANTS]

    # Year to date, per category.
    for txn in store.transactions(account_id, start=date(year, 1, 1), end=last):
        category = categoriser.categorise(txn)
        settled = _settled(fx, txn, base)
        summary.ytd_categories[category] = summary.ytd_categories.get(category, ZERO) + settled

    # End-of-day balances.
    day = first
    while day <= last:
        summary.daily_balances.append((day, _balance_on(store, fx, account, opening, first, day)))
        day += timedelta(days=1)
    summary.closing_balance = summary.daily_balances[-1][1]
    return summary
