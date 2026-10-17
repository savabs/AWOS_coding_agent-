"""Bank CSV importers for the two supported statement formats."""
from __future__ import annotations

import csv
import datetime
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Optional

from fintrack.errors import ParseError, ValidationError
from fintrack.models import Transaction, quantize

SUPPORTED_FORMATS = ("northbank", "southbank")

_HEADERS = {
    "northbank": (",", ["date", "description", "amount"]),
    "southbank": (";", ["booking date", "payee", "memo", "debit", "credit"]),
}


@dataclass
class RowError:
    line: int
    message: str


@dataclass
class ImportResult:
    format: str
    transactions: List[Transaction] = field(default_factory=list)
    errors: List[RowError] = field(default_factory=list)


class _RowProblem(Exception):
    pass


def _split_header(header_line: str, delimiter: str) -> list:
    row = next(csv.reader([header_line.lstrip("﻿")], delimiter=delimiter), [])
    return [c.strip().lower() for c in row]


def detect_format(header_line: str) -> str:
    for name, (delim, cols) in _HEADERS.items():
        if _split_header(header_line, delim) == cols:
            return name
    raise ParseError(f"unrecognized header: {header_line.strip()!r}")


def _clean_description(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parse_north_date(text: str) -> datetime.date:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?: (\d{2}):(\d{2})(?::(\d{2}))?)?", text.strip())
    if not m:
        raise _RowProblem(f"invalid date {text!r}")
    try:
        day = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if m.group(4) is not None:
            datetime.time(int(m.group(4)), int(m.group(5)), int(m.group(6) or 0))
    except ValueError:
        raise _RowProblem(f"invalid date {text!r}") from None
    return day


def _parse_south_date(text: str) -> datetime.date:
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", text.strip())
    if not m:
        raise _RowProblem(f"invalid date {text!r}")
    try:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        raise _RowProblem(f"invalid date {text!r}") from None


def _parse_north_amount(text: str) -> Decimal:
    raw = text.strip()
    if not re.fullmatch(r"[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?", raw):
        raise _RowProblem(f"invalid amount {text!r}")
    try:
        return quantize(Decimal(raw.replace(",", "")))
    except InvalidOperation:
        raise _RowProblem(f"invalid amount {text!r}") from None


def _parse_south_amount(text: str, column: str) -> Decimal:
    raw = text.strip()
    if raw.startswith("-"):
        raise _RowProblem(f"negative value in {column}: {text!r}")
    if not re.fullmatch(r"(\d{1,3}(\.\d{3})+|\d+)(,\d+)?", raw):
        raise _RowProblem(f"invalid amount in {column}: {text!r}")
    return quantize(Decimal(raw.replace(".", "").replace(",", ".")))


def _north_row(cells: list, account: str) -> Transaction:
    date_s, desc, amount_s = cells
    description = _clean_description(desc)
    if not description:
        raise _RowProblem("empty description")
    return Transaction(_parse_north_date(date_s), description, _parse_north_amount(amount_s), account)


def _south_row(cells: list, account: str) -> Transaction:
    date_s, payee, memo, debit_s, credit_s = cells
    day = _parse_south_date(date_s)
    payee, memo = _clean_description(payee), _clean_description(memo)
    if not payee:
        raise _RowProblem("empty description")
    description = f"{payee} - {memo}" if memo else payee
    has_debit, has_credit = bool(debit_s.strip()), bool(credit_s.strip())
    if has_debit == has_credit:
        raise _RowProblem("exactly one of Debit/Credit must be filled")
    if has_debit:
        amount = -_parse_south_amount(debit_s, "Debit")
    else:
        amount = _parse_south_amount(credit_s, "Credit")
    return Transaction(day, description, quantize(amount), account)


def parse_csv(path, fmt: Optional[str] = None, account: str = "default") -> ImportResult:
    if fmt is not None and fmt not in SUPPORTED_FORMATS:
        raise ValidationError(f"unknown format {fmt!r} (expected one of {', '.join(SUPPORTED_FORMATS)})")
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ParseError(f"file not found: {path}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ParseError(f"cannot read {path}: {exc}") from None

    lines = text.splitlines()
    if not lines or not lines[0].strip():
        raise ParseError(f"{path}: empty file or missing header")
    detected = detect_format(lines[0])
    if fmt is not None and detected != fmt:
        raise ParseError(f"{path}: header does not match format {fmt!r}")

    delimiter, columns = _HEADERS[detected]
    build = _north_row if detected == "northbank" else _south_row
    result = ImportResult(format=detected)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    next(reader)  # header
    for cells in reader:
        line = reader.line_num
        if not any(c.strip() for c in cells):
            continue
        try:
            if len(cells) != len(columns):
                raise _RowProblem(f"expected {len(columns)} columns, got {len(cells)}")
            result.transactions.append(build(cells, account))
        except _RowProblem as exc:
            result.errors.append(RowError(line, str(exc)))
    return result
