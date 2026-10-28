"""Shared data and helpers for the hidden tests (not a test module)."""
import datetime
import importlib
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class Lazy:
    """Lazy.importers -> fintrack.importers, imported on first attribute access."""

    def __getattr__(self, name):
        return importlib.import_module(f"fintrack.{name}")


def day(y, m, d):
    return datetime.date(y, m, d)


def txn(ft, date, desc, amount, category=None, account="default"):
    return ft.models.Transaction(date=date, description=desc, amount=Decimal(amount),
                                 account=account, category=category)


NORTH = (
    "Date,Description,Amount\n"
    "2026-03-01,TESCO STORES 2231,-45.20\n"
    '2026-03-02,ACME CORP SALARY,"2,500.00"\n'
    "2026-03-03,UBER TRIP 8812,-12.40\n"
)

SOUTH = (
    "Booking Date;Payee;Memo;Debit;Credit\n"
    "05.03.2026;Tesco;Weekly shop;45,20;\n"
    "06.03.2026;ACME Corp;Salary March;;2.500,00\n"
    "07.03.2026;Shell;;60,00;\n"
)
