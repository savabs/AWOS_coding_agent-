"""
verify.py: deterministic document verification for the Month-end Document Pack.

    v = check("hdfc_oct.pdf",
              SourceSpec("hdfc_current", doc_type="statement",
                         vendor_keywords=("HDFC Bank",),
                         password_templates=("<pan_lower><dob_ddmmyyyy>",)),
              "2026-10",
              prev_closing="1,02,345.00",            # from the pack ledger
              template_vars=derive_vars(pan=..., dob=...))
    v.status   -> "verified" | "unverified" | "wrong_period" | "duplicate"
                  | "locked" | "corrupt" | "suspicious"
    v.fields   -> doc_type, vendor, invoice_no, doc_date, period_start,
                  period_end, currency, amount_total, tax_amount,
                  gstin_supplier, gstin_buyer, opening_balance, closing_balance
    v.reasons  -> ["code: detail", ...]

No model calls, no network. Regex and heuristics only.

Pipeline
  1. file sanity      size > 0, magic bytes, HTML error page saved as .pdf/.csv,
                      PDF opens, CSV / XLSX / HTML-table .xls parse.
  2. text             pdftext.extract (PDFKit, else pypdf, else unverified);
                      encrypted PDFs unlocked with caller passwords/templates.
  3. fields           dates (DD/MM/YYYY, ISO, 15-Oct-2026, October 15, 2026,
                      "Oct 2026"), amounts (₹ / INR / Rs / USD / $; Indian
                      grouping 1,23,456.78), invoice no, GSTIN (+checksum),
                      statement period, opening/closing balance.
  4. period           doc date or statement/billing period vs target month,
                      with a tolerance window for invoices dated early next
                      month (flag, not fail).
  5. continuity       statement opening balance == previous closing balance.
  6. suspicious       sample/test/specimen/DRAFT/proforma text, vendor not
                      matching the source's keywords, bad GSTIN checksum,
                      buyer GSTIN != owner GSTIN.

Status precedence (first match wins):
  corrupt > locked > duplicate > wrong_period > suspicious > unverified > verified
Informational flags (``severity == "flag"``) appear in reasons but leave the
status alone, e.g. ``dated_next_month``, ``partial_period``, ``currency_mismatch``.

OCR is not included: an image-only PDF is ``unverified`` (``no_text_layer``).
"""
from __future__ import annotations

import calendar
import csv
import datetime as _dt
import hashlib
import html
import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union
from xml.etree import ElementTree as ET

from . import pdftext
from .pdftext import derive_vars, normalize_text  # re-export for callers

__all__ = [
    "check",
    "Verdict",
    "SourceSpec",
    "STATUSES",
    "FIELD_NAMES",
    "gstin_valid",
    "gstin_check_char",
    "parse_amount",
    "parse_date",
    "find_dates",
    "extract_fields",
    "month_bounds",
    "derive_vars",
]

STATUSES = ("verified", "unverified", "wrong_period", "duplicate", "locked", "corrupt", "suspicious")
FIELD_NAMES = (
    "doc_type", "vendor", "invoice_no", "doc_date", "period_start", "period_end",
    "currency", "amount_total", "tax_amount", "gstin_supplier", "gstin_buyer",
    "opening_balance", "closing_balance",
)
_PRECEDENCE = ("corrupt", "locked", "duplicate", "wrong_period", "suspicious", "unverified")

DEFAULT_SUSPICIOUS_WORDS = (
    "sample", "specimen", "test", "dummy", "draft", "lorem ipsum",
    "proforma", "pro forma", "pro-forma", "not valid", "void",
)
# words that are legitimate inside these phrases (bank narrations)
_SUSPICIOUS_EXEMPT = {"draft": re.compile(r"demand\s+draft|\bdd\s+draft", re.I)}


# ── public types ────────────────────────────────────────────────────────────

@dataclass
class SourceSpec:
    """What the pack expects from one source (bank account, SaaS vendor, gateway)."""

    name: str
    doc_type: Optional[str] = None  # invoice | statement | gateway_report | None (auto)
    vendor: Optional[str] = None  # display name
    vendor_keywords: Tuple[str, ...] = ()  # any must appear (case-insensitive)
    currency: Optional[str] = None  # expected currency, e.g. "INR" / "USD"
    supplier_gstin: Optional[str] = None  # expected supplier GSTIN (domestic vendors)
    owner_gstin: Optional[str] = None  # our GSTIN: buyer GSTIN must match
    password_templates: Tuple[str, ...] = ()  # e.g. ("<pan_lower><dob_ddmmyyyy>",)
    suspicious_words: Tuple[str, ...] = DEFAULT_SUSPICIOUS_WORDS
    tolerance_days: int = 7  # invoices dated up to N days into next month: flag only
    dayfirst: bool = True  # 03/10/2026 = 3 Oct (India); auto-swaps when impossible


@dataclass
class _Reason:
    severity: str  # corrupt | locked | duplicate | wrong_period | suspicious | unverified | flag | info
    code: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code


@dataclass
class Verdict:
    status: str
    fields: Dict[str, Any]
    reasons: List[str]
    sha256: Optional[str] = None
    dedupe_key: Optional[str] = None  # "vendor|invoice_no" when both are known
    backend: Optional[str] = None
    unlocked_by: Optional[str] = None  # label only, never a password
    pages: int = 0
    _items: List[_Reason] = field(default_factory=list, repr=False)

    @property
    def codes(self) -> List[str]:
        return [r.code for r in self._items]

    @property
    def flags(self) -> List[str]:
        return [r.code for r in self._items if r.severity == "flag"]

    def to_dict(self) -> Dict[str, Any]:
        def ser(v):
            if isinstance(v, Decimal):
                return str(v)
            if isinstance(v, (_dt.date, _dt.datetime)):
                return v.isoformat()
            return v

        return {
            "status": self.status,
            "fields": {k: ser(v) for k, v in self.fields.items()},
            "reasons": list(self.reasons),
            "codes": self.codes,
            "sha256": self.sha256,
            "dedupe_key": self.dedupe_key,
            "backend": self.backend,
            "unlocked_by": self.unlocked_by,
            "pages": self.pages,
        }


# ── GSTIN ───────────────────────────────────────────────────────────────────

_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b")
_GSTIN_LOOSE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]{3})\b")


def gstin_check_char(first14: str) -> str:
    """GSTN mod-36 check character for the first 14 characters."""
    total = 0
    for i, ch in enumerate(first14.upper()):
        v = _GST_CHARS.index(ch) * (2 if i % 2 else 1)
        total += v // 36 + v % 36
    return _GST_CHARS[(36 - total % 36) % 36]


def gstin_valid(g: str) -> bool:
    g = (g or "").strip().upper()
    if not GSTIN_RE.fullmatch(g):
        return False
    if not (1 <= int(g[:2]) <= 38 or g[:2] == "97" or g[:2] == "99"):
        return False
    return gstin_check_char(g[:14]) == g[14]


# ── amounts ─────────────────────────────────────────────────────────────────

_NUM = r"\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
_CUR = r"₹|INR|Rs\.?|USD|US\$|\$|EUR|€|GBP|£"
AMOUNT_RE = re.compile(
    rf"(?P<neg>-)?\s*(?P<cur>{_CUR})?\s*(?P<neg2>-)?(?<![\d.,/])(?P<num>{_NUM})(?![\d/]|\.\d|-\d)"
    rf"(?:\s*(?P<cur2>INR|USD|EUR|GBP)\b)?(?:\s*(?P<drcr>Cr|Dr|CR|DR)\b\.?)?"
)
_CUR_MAP = {"₹": "INR", "INR": "INR", "RS": "INR", "RS.": "INR", "USD": "USD", "US$": "USD",
            "$": "USD", "EUR": "EUR", "€": "EUR", "GBP": "GBP", "£": "GBP"}
_INDIAN_GROUP = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}$")
_WESTERN_GROUP = re.compile(r"^\d{1,3}(?:,\d{3})+$")


def parse_amount(s: str) -> Optional[Decimal]:
    """'₹1,23,456.78' / 'INR 1,23,456.78' / '$1,234.56' / '(500.00)' / '1,000.00 Dr' -> Decimal.

    Rejects malformed digit grouping such as '1,2345'. Dr / parentheses -> negative."""
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1].strip()
    m = re.search(r"\b(Dr|DR)\b\.?\s*$", t)
    if m:
        neg, t = True, t[: m.start()].strip()
    t = re.sub(r"\b(Cr|CR)\b\.?\s*$", "", t).strip()
    t = re.sub(rf"^(?:{_CUR})\s*", "", t).strip()
    t = re.sub(r"\s*(?:INR|USD|EUR|GBP)$", "", t).strip()
    if t.startswith("-"):
        neg, t = True, t[1:].strip()
    t = re.sub(rf"^(?:{_CUR})\s*", "", t).strip()
    if not re.fullmatch(r"[\d,]+(?:\.\d+)?", t):
        return None
    intpart = t.split(".")[0]
    if "," in intpart and not (_INDIAN_GROUP.match(intpart) or _WESTERN_GROUP.match(intpart)):
        return None
    try:
        d = Decimal(t.replace(",", ""))
    except InvalidOperation:
        return None
    return -d if neg else d


def _amounts_in(line: str) -> List[Tuple[Decimal, Optional[str]]]:
    out = []
    for m in AMOUNT_RE.finditer(line):
        num = m.group("num")
        # skip things that are clearly dates / ids: a bare small integer with no decimals
        # and no currency is ambiguous (e.g. "Page 1"), keep only if it has decimals or currency
        cur = m.group("cur") or m.group("cur2")
        if not cur and "." not in num and "," not in num:
            continue
        v = parse_amount(num)
        if v is None:
            continue
        if m.group("neg") or m.group("neg2") or (m.group("drcr") or "").upper() == "DR":
            v = -v
        out.append((v, _CUR_MAP.get((cur or "").upper()) if cur else None))
    return out


def _detect_currency(text: str) -> Optional[str]:
    counts = {"INR": 0, "USD": 0, "EUR": 0, "GBP": 0}
    counts["INR"] += len(re.findall(r"₹|\bINR\b|\bRs\.?\s*\d|\bRupees\b", text))
    counts["USD"] += len(re.findall(r"\bUSD\b|US\$|\$\s?\d", text))
    counts["EUR"] += len(re.findall(r"\bEUR\b|€", text))
    counts["GBP"] += len(re.findall(r"\bGBP\b|£", text))
    best = max(counts.items(), key=lambda kv: kv[1])
    return best[0] if best[1] else None


# ── dates ───────────────────────────────────────────────────────────────────

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})
_MONTHS["sept"] = 9
_MON = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"

_D_ISO = re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_D_NUM = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4}|\d{2})\b")
_D_DMONY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-]*({_MON})\.?[\s\-,']*(\d{{4}}|\d{{2}})\b", re.I)
_D_MONDY = re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
_D_MONY = re.compile(rf"\b({_MON})\.?[\s\-',]+(\d{{4}})\b", re.I)


def _year(y: str) -> int:
    n = int(y)
    return n + 2000 if n < 100 else n


def _mk(y: int, m: int, d: int) -> Optional[_dt.date]:
    try:
        return _dt.date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str, dayfirst: bool = True) -> List[Tuple[_dt.date, int, int]]:
    """All full dates in ``text`` as (date, start, end), in text order, non-overlapping."""
    found: List[Tuple[_dt.date, int, int]] = []
    taken: List[Tuple[int, int]] = []

    def free(a, b):
        return all(b <= x or a >= y for x, y in taken)

    for m in _D_ISO.finditer(text):
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d and free(*m.span()):
            found.append((d, *m.span()))
            taken.append(m.span())
    for m in _D_MONDY.finditer(text):
        d = _mk(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
        if d and free(*m.span()):
            found.append((d, *m.span()))
            taken.append(m.span())
    for m in _D_DMONY.finditer(text):
        d = _mk(_year(m.group(3)), _MONTHS[m.group(2).lower()[:3]], int(m.group(1)))
        if d and free(*m.span()):
            found.append((d, *m.span()))
            taken.append(m.span())
    for m in _D_NUM.finditer(text):
        a, b, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        d = _mk(y, b, a) if dayfirst else _mk(y, a, b)
        if d is None:  # impossible as given: try the other order
            d = _mk(y, a, b) if dayfirst else _mk(y, b, a)
        if d and free(*m.span()):
            found.append((d, *m.span()))
            taken.append(m.span())
    found.sort(key=lambda t: t[1])
    return found


def parse_date(s: str, dayfirst: bool = True) -> Optional[_dt.date]:
    if s is None:
        return None
    s = str(s).strip()
    ds = find_dates(s, dayfirst)
    if ds:
        return ds[0][0]
    for fmt in ("%Y%m%d", "%d%m%Y"):
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def month_bounds(period) -> Tuple[_dt.date, _dt.date]:
    """'2026-10' | '10/2026' | 'Oct 2026' | (2026, 10) | date -> (first day, last day)."""
    if isinstance(period, tuple):
        y, m = int(period[0]), int(period[1])
    elif isinstance(period, (_dt.date, _dt.datetime)):
        y, m = period.year, period.month
    else:
        s = str(period).strip()
        mm = re.fullmatch(r"(\d{4})-(\d{1,2})", s) or None
        if mm:
            y, m = int(mm.group(1)), int(mm.group(2))
        elif re.fullmatch(r"(\d{1,2})/(\d{4})", s):
            a, b = s.split("/")
            y, m = int(b), int(a)
        else:
            mm = _D_MONY.search(s)
            if not mm:
                raise ValueError(f"unrecognised period: {period!r}")
            y, m = int(mm.group(2)), _MONTHS[mm.group(1).lower()[:3]]
    return _dt.date(y, m, 1), _dt.date(y, m, calendar.monthrange(y, m)[1])


# ── field extraction ────────────────────────────────────────────────────────

_LINES_AFTER = 1  # labels in PDF tables often have the value on the next line


def _label_lines(text: str, label_re: re.Pattern) -> List[Tuple[str, str]]:
    """For every line matching ``label_re``: (rest of line after the label, next line)."""
    lines = text.split("\n")
    out = []
    for i, ln in enumerate(lines):
        m = label_re.search(ln)
        if m:
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            out.append((ln[m.end():], nxt))
    return out


def _first_amount_after(label_re: re.Pattern, text: str, last: bool = False) -> Tuple[Optional[Decimal], Optional[str]]:
    hits = []
    for rest, nxt in _label_lines(text, label_re):
        am = _amounts_in(rest) or _amounts_in(nxt)
        if am:
            hits.append(am[0])
    if not hits:
        return None, None
    return hits[-1] if last else hits[0]


def _first_date_after(label_re: re.Pattern, text: str, dayfirst: bool) -> Optional[_dt.date]:
    for rest, nxt in _label_lines(text, label_re):
        ds = find_dates(rest, dayfirst) or find_dates(nxt, dayfirst)
        if ds:
            return ds[0][0]
    return None


_TOTAL_LABELS = [
    re.compile(r"\bgrand\s+total\b", re.I),
    re.compile(r"\btotal\s+amount\s+(?:payable|due)\b", re.I),
    re.compile(r"\b(?:net|total)\s+payable\b|\bamount\s+payable\b", re.I),
    re.compile(r"\binvoice\s+total\b|\btotal\s+invoice\s+(?:value|amount)\b", re.I),
    re.compile(r"\bamount\s+due\b|\bbalance\s+due\b|\btotal\s+due\b", re.I),
    re.compile(r"\btotal\s+(?:amount|value)\b|\bamount\s+paid\b", re.I),
    re.compile(r"(?<!sub)(?<!sub-)(?<!sub )\btotal\b(?!\s+(?:tax|gst|igst|cgst|sgst|vat))", re.I),
]
_TAX_TOTAL = re.compile(r"\btotal\s+(?:tax|gst)(?:\s+amount)?\b|\btax\s+amount\b|\bgst\s+amount\b|\bvat\b", re.I)
_IGST = re.compile(r"\bIGST\b", re.I)
_CGST = re.compile(r"\bCGST\b", re.I)
_SGST = re.compile(r"\b(?:SGST|UTGST)\b", re.I)
_GENERIC_TAX = re.compile(r"\b(?:GST|tax)\b(?:\s*@?\s*\d{1,2}(?:\.\d+)?\s*%)?", re.I)
_OPENING = re.compile(r"\bopening\s+(?:balance|bal\.?)\b|\bbalance\s+(?:b/f|brought\s+forward)\b|\bprevious\s+balance\b", re.I)
_CLOSING = re.compile(r"\bclosing\s+(?:balance|bal\.?)\b|\bbalance\s+(?:c/f|carried\s+forward)\b|\bavailable\s+balance\b", re.I)
_INVNO = re.compile(
    r"\b(?:tax\s+)?(?:invoice|inv|bill|receipt)\s*(?:no\.?|number|num|#|id)\s*[:#.\-]?\s*"
    r"(?:\n\s*)?([A-Z0-9][A-Z0-9\-/_.]{2,40})",
    re.I,
)
_DATE_LABEL = re.compile(
    r"\b(?:invoice\s+date|date\s+of\s+(?:issue|invoice)|issue\s+date|bill\s+date|"
    r"date\s+issued|statement\s+date|invoice\s+dt\.?|dated|date)\b\s*[:\-]?", re.I
)
_PERIOD_LABEL = re.compile(
    r"\b(?:statement\s+period|billing\s+period|service\s+period|for\s+the\s+period|"
    r"period|statement\s+(?:from|for)|from)\b", re.I
)
_RANGE_SEP = re.compile(r"^\s*(?:to|till|until|through|thru|-|–|—|and)\s*", re.I)
_MONDAY_RANGE = re.compile(
    rf"\b({_MON})\.?\s+(\d{{1,2}})\s*(?:-|–|—|to)\s*({_MON})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I
)
_STATEMENT_FOR_MONTH = re.compile(rf"\b(?:statement|report|summary|invoice)\b[^\n]{{0,30}}?\b(?:for|of)\b[^\n]{{0,15}}?\b({_MON})\.?[\s\-',]+(\d{{4}})\b", re.I)
_BUYER_CTX = re.compile(r"bill(?:ed)?\s+to|buyer|recipient|customer|client|ship(?:ped)?\s+to|your\s+gstin|purchaser|consignee", re.I)
_SUPPLIER_CTX = re.compile(r"supplier|seller|vendor|our\s+gstin|from\b|issued\s+by|sold\s+by", re.I)


def _classify(text: str, hint: Optional[str]) -> str:
    if hint:
        return hint
    t = text.lower()
    if re.search(r"settlement|payout|merchant\s+id|gateway", t):
        return "gateway_report"
    if re.search(r"statement\s+of\s+account|account\s+statement|opening\s+balance|closing\s+balance|bank\s+statement|card\s+statement", t):
        return "statement"
    if re.search(r"invoice|bill\s+no|receipt", t):
        return "invoice"
    return "unknown"


def _extract_period(text: str, dayfirst: bool) -> Tuple[Optional[_dt.date], Optional[_dt.date]]:
    # 1) "Oct 1 - Oct 31, 2026"
    m = _MONDAY_RANGE.search(text)
    if m:
        y = int(m.group(5))
        a = _mk(y, _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
        b = _mk(y, _MONTHS[m.group(3).lower()[:3]], int(m.group(4)))
        if a and b:
            if b < a:  # Dec 15 - Jan 14, 2027
                a = _mk(y - 1, a.month, a.day)
            return a, b
    # 2) "<period label> DATE to DATE" on one line (or label line + next)
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if not _PERIOD_LABEL.search(ln):
            continue
        seg = ln + " " + (lines[i + 1] if i + 1 < len(lines) else "")
        ds = find_dates(seg, dayfirst)
        for (d1, s1, e1), (d2, s2, e2) in zip(ds, ds[1:]):
            if _RANGE_SEP.match(seg[e1:s2]) and d1 <= d2:
                return d1, d2
    # 3) any "DATE to DATE" anywhere
    ds = find_dates(text, dayfirst)
    for (d1, s1, e1), (d2, s2, e2) in zip(ds, ds[1:]):
        gap = text[e1:s2]
        if len(gap) < 12 and re.fullmatch(r"\s*(?:to|till|until|through|-|–|—)\s*", gap, re.I) and d1 <= d2:
            return d1, d2
    # 4) "Statement for October 2026"
    m = _STATEMENT_FOR_MONTH.search(text)
    if m:
        a, b = month_bounds((int(m.group(2)), _MONTHS[m.group(1).lower()[:3]]))
        return a, b
    return None, None


def _extract_gstins(text: str, owner_gstin: Optional[str]) -> Tuple[List[str], List[str], Optional[str], Optional[str]]:
    """-> (valid, invalid_checksum, supplier, buyer)."""
    valid, invalid = [], []
    roles: Dict[str, str] = {}
    lines = text.split("\n")
    owner = (owner_gstin or "").upper() or None
    for i, ln in enumerate(lines):
        for m in _GSTIN_LOOSE.finditer(ln.upper()):
            g = m.group(1)
            if not gstin_valid(g):
                if GSTIN_RE.fullmatch(g) or g[13] == "Z":
                    if g not in invalid:
                        invalid.append(g)
                continue
            if g in valid:
                continue
            valid.append(g)
            ctx = ln[: m.start()] + " " + (lines[i - 1] if i else "")
            if owner and g == owner:
                roles[g] = "buyer"
            elif _BUYER_CTX.search(ln[: m.start()]) or (not _SUPPLIER_CTX.search(ln[: m.start()]) and _BUYER_CTX.search(lines[i - 1] if i else "")):
                roles[g] = "buyer"
            elif _SUPPLIER_CTX.search(ctx):
                roles[g] = "supplier"
    supplier = next((g for g in valid if roles.get(g) == "supplier"), None)
    buyer = next((g for g in valid if roles.get(g) == "buyer"), None)
    rest = [g for g in valid if g not in (supplier, buyer)]
    if supplier is None and rest:
        supplier = rest.pop(0)
    if buyer is None and rest:
        buyer = rest.pop(0)
    return valid, invalid, supplier, buyer


def extract_fields(text: str, source: Optional[SourceSpec] = None) -> Dict[str, Any]:
    """Deterministic field extraction from normalised document text."""
    src = source or SourceSpec(name="?")
    text = normalize_text(text)
    f: Dict[str, Any] = {k: None for k in FIELD_NAMES}
    f["doc_type"] = _classify(text, src.doc_type)
    low = text.lower()
    for kw in src.vendor_keywords:
        if kw.lower() in low:
            f["vendor"] = src.vendor or kw
            break
    m = _INVNO.search(text)
    if m:
        cand = m.group(1).strip(".-/")
        if re.search(r"\d", cand) and not re.fullmatch(r"(?i)date|dt", cand):
            f["invoice_no"] = cand
    f["doc_date"] = _first_date_after(_DATE_LABEL, text, src.dayfirst)
    f["period_start"], f["period_end"] = _extract_period(text, src.dayfirst)
    if f["doc_date"] is None and f["doc_type"] == "invoice":
        ds = find_dates(text, src.dayfirst)
        if ds:
            f["doc_date"] = ds[0][0]
    f["currency"] = _detect_currency(text)

    total = None
    for lab in _TOTAL_LABELS:
        v, cur = _first_amount_after(lab, text, last=True)
        if v is not None:
            total = v
            if cur and not f["currency"]:
                f["currency"] = cur
            break
    f["amount_total"] = total

    tax_total, _ = _first_amount_after(_TAX_TOTAL, text, last=True)
    if tax_total is None:
        ig, _ = _first_amount_after(_IGST, text)
        cg, _ = _first_amount_after(_CGST, text)
        sg, _ = _first_amount_after(_SGST, text)
        if cg is not None and sg is not None:
            tax_total = cg + sg
        elif ig is not None:
            tax_total = ig
        else:
            tax_total, _ = _first_amount_after(_GENERIC_TAX, text)
    f["tax_amount"] = tax_total

    f["opening_balance"], _ = _first_amount_after(_OPENING, text)
    f["closing_balance"], _ = _first_amount_after(_CLOSING, text, last=True)

    valid, invalid, sup, buy = _extract_gstins(text, src.owner_gstin)
    f["gstin_supplier"], f["gstin_buyer"] = sup, buy
    f["_gstins_invalid"] = invalid
    return f


# ── tabular (CSV / XLSX / HTML-table .xls) ──────────────────────────────────

_HDR_DATE = re.compile(r"\b(?:txn|transaction|value|posting|tran|settlement)?\s*date\b|^date$|^dt$", re.I)
_HDR_DEBIT = re.compile(r"debit|withdrawal|\bdr\b|paid\s+out", re.I)
_HDR_CREDIT = re.compile(r"credit|deposit|\bcr\b|paid\s+in", re.I)
_HDR_BAL = re.compile(r"balance|\bbal\b", re.I)
_HDR_AMOUNT = re.compile(r"^(?:net\s+|settled\s+|settlement\s+|gross\s+)?amount\b|^amount", re.I)
_HDR_TAX = re.compile(r"\b(?:tax|gst|igst)\b", re.I)
_HDR_DESC = re.compile(r"description|narration|particulars|remarks|details", re.I)


def _hnorm(c: str) -> str:
    return " ".join((c or "").replace("_", " ").split())


def _find_header(rows: List[List[str]]) -> Optional[int]:
    for i, r in enumerate(rows[:40]):
        cells = [_hnorm(c) for c in r]
        has_date = any(_HDR_DATE.search(c) for c in cells if c)
        has_money = any(_HDR_BAL.search(c) or _HDR_DEBIT.search(c) or _HDR_CREDIT.search(c) or _HDR_AMOUNT.search(c) for c in cells if c)
        if has_date and has_money and sum(1 for c in cells if c) >= 2:
            return i
    return None


def _col(hdr: List[str], rx: re.Pattern, exclude: Tuple[re.Pattern, ...] = ()) -> Optional[int]:
    for j, c in enumerate(hdr):
        if c and rx.search(c) and not any(e.search(c) for e in exclude):
            return j
    return None


def _tabular_fields(rows: List[List[str]], src: SourceSpec, reasons: List[_Reason],
                    extra_text: str = "") -> Tuple[Dict[str, Any], str]:
    text = "\n".join(" ".join(c.strip() for c in r if c.strip()) for r in rows)
    if extra_text:
        text = extra_text + "\n" + text
    hi = _find_header(rows)
    if hi is None:
        reasons.append(_Reason("unverified", "no_table_header", "no date + amount/balance header row found"))
        return extract_fields(text, src), text
    # labelled values (period, opening balance, account holder) live in the preamble;
    # the table body itself is read column-wise below
    preamble = "\n".join(" ".join(c.strip() for c in r if c.strip()) for r in rows[:hi])
    f = extract_fields(preamble, src)
    low = text.lower()
    f["vendor"] = next((src.vendor or kw for kw in src.vendor_keywords if kw.lower() in low), None)
    hdr = [_hnorm(c) for c in rows[hi]]
    ci_date = _col(hdr, _HDR_DATE)
    ci_bal = _col(hdr, _HDR_BAL, (re.compile(r"^opening", re.I),))
    ci_dr = _col(hdr, _HDR_DEBIT)
    ci_cr = _col(hdr, _HDR_CREDIT)
    ci_amt = _col(hdr, _HDR_AMOUNT, (_HDR_TAX,))
    ci_tax = _col(hdr, _HDR_TAX)
    is_gateway = bool(re.search(r"settlement|payout|utr|fee", " ".join(hdr), re.I))
    if not src.doc_type:
        f["doc_type"] = "gateway_report" if is_gateway and ci_bal is None else "statement"

    tx = []  # (date, debit, credit, balance, amount, tax)
    for r in rows[hi + 1:]:
        if not any(c.strip() for c in r):
            continue
        cell = lambda j: r[j].strip() if j is not None and j < len(r) else ""
        d = parse_date(cell(ci_date), src.dayfirst)
        if d is None:
            continue  # footer / totals / "Opening balance" pseudo-rows
        tx.append((d, parse_amount(cell(ci_dr)) if cell(ci_dr) else None,
                   parse_amount(cell(ci_cr)) if cell(ci_cr) else None,
                   parse_amount(cell(ci_bal)) if cell(ci_bal) else None,
                   parse_amount(cell(ci_amt)) if cell(ci_amt) else None,
                   parse_amount(cell(ci_tax)) if cell(ci_tax) else None))
    if not tx:
        reasons.append(_Reason("unverified", "no_rows", "table has a header but no dated rows"))
        return f, text
    dates = [t[0] for t in tx]
    if f["period_start"] is None:
        f["period_start"], f["period_end"] = min(dates), max(dates)
    if f["doc_date"] is None:
        f["doc_date"] = max(dates)

    if ci_bal is not None and tx[0][3] is not None:
        first = tx[0]
        if f["opening_balance"] is None:
            f["opening_balance"] = first[3] + (first[1] or 0) - (first[2] or 0)
        if f["closing_balance"] is None:
            last_bal = next((t[3] for t in reversed(tx) if t[3] is not None), None)
            f["closing_balance"] = last_bal
        # running-balance integrity within the file
        prev = None
        for i, t in enumerate(tx):
            if t[3] is None:
                continue
            if prev is not None and (ci_dr is not None or ci_cr is not None):
                exp = prev - (t[1] or 0) + (t[2] or 0)
                if abs(exp - t[3]) > Decimal("0.01"):
                    reasons.append(_Reason("suspicious", "running_balance_break",
                                           f"row {i + 1}: expected {exp}, file says {t[3]}"))
                    break
            prev = t[3]
    if ci_amt is not None and f["amount_total"] is None:
        vals = [t[4] for t in tx if t[4] is not None]
        if vals:
            f["amount_total"] = sum(vals, Decimal(0))
    if ci_tax is not None and f["tax_amount"] is None:
        vals = [t[5] for t in tx if t[5] is not None]
        if vals:
            f["tax_amount"] = sum(vals, Decimal(0))
    return f, text


def _read_csv_rows(data: bytes) -> List[List[str]]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            s = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    s = normalize_text(s)
    try:
        dialect = csv.Sniffer().sniff(s[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [r for r in csv.reader(io.StringIO(s), dialect)]


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _xlsx_col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _read_xlsx_rows(path: str) -> List[List[str]]:
    """Minimal stdlib XLSX reader: first worksheet, shared + inline strings, numbers.

    Date-formatted numeric cells come back as Excel serials and are converted
    when the column header says 'date'."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        sheets = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            raise ValueError("no worksheet")
        root = ET.fromstring(z.read(sheets[0]))
    rows: List[List[str]] = []
    for row in root.iter(f"{{{_NS['m']}}}row"):
        out: List[str] = []
        for c in row.findall("m:c", _NS):
            ref = c.get("r")
            idx = _xlsx_col(ref) if ref else len(out)
            while len(out) < idx:
                out.append("")
            t = c.get("t")
            if t == "s":
                v = c.find("m:v", _NS)
                val = shared[int(v.text)] if v is not None and v.text else ""
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
            else:
                v = c.find("m:v", _NS)
                val = v.text if v is not None and v.text else ""
            out.append(val)
        rows.append(out)
    # Excel serial dates in date-like header columns
    hi = _find_header(rows)
    if hi is not None:
        for j, h in enumerate(rows[hi]):
            if _HDR_DATE.search(_hnorm(h)):
                for r in rows[hi + 1:]:
                    if j < len(r) and re.fullmatch(r"\d{5}(?:\.\d+)?", r[j] or ""):
                        d = _dt.date(1899, 12, 30) + _dt.timedelta(days=int(float(r[j])))
                        r[j] = d.isoformat()
    return rows


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None
        self.text: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        self.text.append(data)
        if self._cell is not None:
            self._cell.append(data)


# ── sniffing ────────────────────────────────────────────────────────────────

_HTML_HEAD = re.compile(rb"^\s*(?:\xef\xbb\xbf)?\s*(?:<!doctype\s+html|<html|<head|<body|<\?xml[^>]*>\s*<html|<!--)", re.I)


def _looks_html(head: bytes) -> bool:
    return bool(_HTML_HEAD.match(head)) or (b"<html" in head[:2048].lower() and b"%PDF" not in head[:1024])


def _kind(path: str, head: bytes) -> str:
    ext = os.path.splitext(path)[1].lower()
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "xlsx" if ext in (".xlsx", ".xlsm", "") else "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole"  # legacy binary .xls / .doc
    if _looks_html(head):
        return "html"
    return "csv" if ext in (".csv", ".txt", ".tsv") else "unknown"


# ── main entry point ────────────────────────────────────────────────────────

def _decide(items: List[_Reason]) -> str:
    sev = {r.severity for r in items}
    for s in _PRECEDENCE:
        if s in sev:
            return s
    return "verified"


def _as_source(source) -> SourceSpec:
    if isinstance(source, SourceSpec):
        return source
    if isinstance(source, dict):
        d = dict(source)
        for k in ("vendor_keywords", "password_templates", "suspicious_words"):
            if k in d and d[k] is not None:
                d[k] = tuple(d[k])
        return SourceSpec(**d)
    return SourceSpec(name=str(source))


def _to_dec(v) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    return parse_amount(str(v))


def check(
    doc_path,
    source: Union[SourceSpec, dict, str],
    period,
    *,
    prev_closing=None,
    password: Optional[str] = None,
    passwords: Sequence[Tuple[str, str]] = (),
    template_vars: Optional[Dict[str, str]] = None,
    owner_gstin: Optional[str] = None,
    seen: Iterable[str] = (),
    tolerance_days: Optional[int] = None,
    pdf_backend: Optional[str] = None,
) -> Verdict:
    """Verify one collected document against its source and target month.

    ``password``     a single password resolved by the caller (keychain via the
                     secrets broker); recorded only as the label "password".
    ``passwords``    extra ``(label, value)`` pairs.
    ``template_vars`` variables for ``source.password_templates`` (see
                     ``pdftext.derive_vars``).
    ``prev_closing`` previous month's closing balance from the pack ledger.
    ``seen``         sha256 hex digests and/or "vendor|invoice_no" keys already
                     in the pack -> ``duplicate``.
    """
    src = _as_source(source)
    path = os.fspath(doc_path)
    items: List[_Reason] = []
    fields: Dict[str, Any] = {k: None for k in FIELD_NAMES}
    v = Verdict(status="corrupt", fields=fields, reasons=[], _items=items)
    tol = src.tolerance_days if tolerance_days is None else tolerance_days
    owner = (owner_gstin or src.owner_gstin or "").upper() or None
    if owner and src.owner_gstin is None:
        src = SourceSpec(**{**src.__dict__, "owner_gstin": owner})

    def done() -> Verdict:
        v.status = _decide(items)
        v.reasons = [str(r) for r in items]
        return v

    # 1. file sanity ────────────────────────────────────────────────────────
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as e:
        items.append(_Reason("corrupt", "unreadable", type(e).__name__))
        return done()
    if size == 0 or not data.strip():
        items.append(_Reason("corrupt", "empty_file", f"{size} bytes"))
        return done()
    v.sha256 = hashlib.sha256(data).hexdigest()
    head = data[:4096]
    ext = os.path.splitext(path)[1].lower()
    kind = _kind(path, head)

    text = ""
    if ext == ".pdf" and kind != "pdf":
        if kind == "html":
            items.append(_Reason("corrupt", "html_not_pdf", "an HTML page (likely an error/login page) saved as .pdf"))
        else:
            items.append(_Reason("corrupt", "bad_magic", "no %PDF- header"))
        return done()

    if kind == "pdf":
        if b"%%EOF" not in data[-2048:]:
            items.append(_Reason("flag", "pdf_no_eof", "missing %%EOF trailer (possibly truncated)"))
        cands: List[Tuple[str, str]] = []
        if password:
            cands.append(("password", password))
        cands += [(str(a), str(b)) for a, b in passwords]
        res = pdftext.extract(path, cands, templates=src.password_templates,
                              template_vars=template_vars, backend=pdf_backend)
        v.backend, v.unlocked_by, v.pages = res.backend, res.unlocked_by, res.pages
        if res.error == "no_backend":
            if res.encrypted:
                items.append(_Reason("locked", "encrypted_no_backend", "encrypted and no PDF backend available to unlock it"))
            else:
                items.append(_Reason("unverified", "no_pdf_backend", "neither PDFKit (Quartz) nor pypdf is available"))
            return done()
        if res.error:
            items.append(_Reason("corrupt", "pdf_open_failed", res.error))
            return done()
        if res.locked:
            detail = f"tried {res.attempts} candidate password(s)" if res.attempts else "no password or template available"
            items.append(_Reason("locked", "encrypted", detail))
            return done()
        if res.encrypted and res.unlocked_by:
            items.append(_Reason("info", "unlocked", res.unlocked_by))
        if res.pages == 0:
            items.append(_Reason("corrupt", "no_pages", "PDF has no pages"))
            return done()
        if not res.has_text:
            items.append(_Reason("unverified", "no_text_layer",
                                 "image-only/scanned PDF; OCR is not included in v0.1"))
            return done()
        text = res.text
        fields.update(extract_fields(text, src))
    elif kind in ("csv", "xlsx", "html", "unknown") or ext in (".csv", ".xlsx", ".xls"):
        extra_text = ""
        try:
            if kind == "xlsx":
                rows = _read_xlsx_rows(path)
            elif kind == "html":
                p = _TableParser()
                p.feed(data.decode("utf-8", "replace"))
                rows = [r for r in p.rows if any(c.strip() for c in r)]
                extra_text = normalize_text(" ".join(" ".join(p.text).split()))
                if ext not in (".xls", ".htm", ".html") or _find_header(rows) is None:
                    items.append(_Reason("corrupt", "html_not_document",
                                         "an HTML page saved under a document extension"))
                    return done()
            elif kind == "unknown" and ext not in (".csv", ".txt", ".tsv"):
                items.append(_Reason("corrupt", "unknown_format", f"unrecognised file type {ext or '(none)'}"))
                return done()
            else:
                if b"\x00" in data[:4096]:
                    items.append(_Reason("corrupt", "binary_not_csv", "NUL bytes in a text table"))
                    return done()
                rows = _read_csv_rows(data)
        except (zipfile.BadZipFile, ET.ParseError, ValueError, KeyError) as e:
            items.append(_Reason("corrupt", "parse_failed", type(e).__name__))
            return done()
        if len([r for r in rows if any(c.strip() for c in r)]) < 2:
            items.append(_Reason("corrupt", "too_few_rows", "fewer than 2 non-empty rows"))
            return done()
        tf, text = _tabular_fields(rows, src, items, extra_text)
        fields.update(tf)
        v.backend = kind
    elif kind == "ole":
        items.append(_Reason("unverified", "legacy_xls", "binary .xls is not parsed in v0.1; export as CSV/XLSX"))
        return done()
    else:
        items.append(_Reason("corrupt", "unknown_format", kind))
        return done()

    invalid = fields.pop("_gstins_invalid", []) or []
    if fields.get("currency") is None and src.currency:
        fields["currency"] = src.currency.upper()
    low = normalize_text(text).lower()

    # 3b. duplicates ──────────────────────────────────────────────────────────
    seen_set = {str(s).lower() for s in seen or ()}
    if fields.get("invoice_no"):
        v.dedupe_key = f"{(fields.get('vendor') or src.name).lower()}|{fields['invoice_no'].lower()}"
    if v.sha256 in seen_set:
        items.append(_Reason("duplicate", "same_file", v.sha256[:12]))
    elif v.dedupe_key and v.dedupe_key in seen_set:
        items.append(_Reason("duplicate", "same_invoice_no", v.dedupe_key))

    # 4. period ─────────────────────────────────────────────────────────────
    ms, me = month_bounds(period)
    ps, pe, dd = fields.get("period_start"), fields.get("period_end"), fields.get("doc_date")
    dtype = fields.get("doc_type")
    if ps and pe:
        if pe < ms or ps > me:
            items.append(_Reason("wrong_period", "period_outside_month",
                                 f"{ps.isoformat()}..{pe.isoformat()} vs {ms:%Y-%m}"))
        elif dtype in ("statement", "gateway_report") and (ps > ms or pe < me):
            # covers only part of the month: incomplete statement / mid-month export
            if (ps - ms).days > 3 or (me - pe).days > 3:
                items.append(_Reason("flag", "partial_period",
                                     f"covers {ps.isoformat()}..{pe.isoformat()} of {ms:%Y-%m}"))
    elif dd:
        if ms <= dd <= me:
            pass
        elif me < dd <= me + _dt.timedelta(days=tol):
            items.append(_Reason("flag", "dated_next_month",
                                 f"{dd.isoformat()} is within {tol} days after {ms:%Y-%m}; likely previous-month usage"))
        else:
            items.append(_Reason("wrong_period", "date_outside_month", f"{dd.isoformat()} vs {ms:%Y-%m}"))
    else:
        items.append(_Reason("unverified", "no_date", "no document date or statement period found"))

    # 5. continuity ─────────────────────────────────────────────────────────
    prev = _to_dec(prev_closing)
    ob = fields.get("opening_balance")
    if prev is not None and dtype == "statement":
        if ob is None:
            items.append(_Reason("flag", "opening_balance_missing", "cannot check continuity"))
        elif abs(ob - prev) > Decimal("0.01"):
            items.append(_Reason("unverified", "continuity_break",
                                 f"opening {ob} != previous closing {prev}"))

    # 6. suspicious ─────────────────────────────────────────────────────────
    for w in src.suspicious_words:
        scan = low
        ex = _SUSPICIOUS_EXEMPT.get(w.lower())
        if ex:
            scan = ex.sub(" ", scan)
        if re.search(rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])", scan):
            items.append(_Reason("suspicious", "suspicious_text", repr(w)))
    if src.vendor_keywords and not fields.get("vendor"):
        items.append(_Reason("suspicious", "vendor_mismatch",
                             f"none of {list(src.vendor_keywords)} found for source {src.name!r}"))
    for g in invalid:
        items.append(_Reason("suspicious", "gstin_checksum_fail", g))
    if src.supplier_gstin and fields.get("gstin_supplier") and fields["gstin_supplier"] != src.supplier_gstin.upper():
        items.append(_Reason("suspicious", "supplier_gstin_mismatch",
                             f"{fields['gstin_supplier']} != expected {src.supplier_gstin.upper()}"))
    if owner and dtype == "invoice":
        found_all = set(GSTIN_RE.findall(normalize_text(text).upper()))
        if fields.get("gstin_buyer") and fields["gstin_buyer"] != owner:
            items.append(_Reason("suspicious", "buyer_gstin_mismatch",
                                 f"{fields['gstin_buyer']} != owner {owner}"))
        elif fields.get("gstin_supplier") and owner not in found_all:
            items.append(_Reason("flag", "owner_gstin_absent", "domestic invoice without our GSTIN (ITC at risk)"))

    # misc flags / required fields ─────────────────────────────────────────
    if src.currency and fields.get("currency") and fields["currency"] != src.currency.upper():
        items.append(_Reason("flag", "currency_mismatch", f"{fields['currency']} != expected {src.currency.upper()}"))
    at, tx = fields.get("amount_total"), fields.get("tax_amount")
    if at is not None and tx is not None and abs(tx) > abs(at):
        items.append(_Reason("flag", "tax_exceeds_total", f"{tx} > {at}"))
    if dtype == "invoice" and at is None:
        items.append(_Reason("unverified", "missing_amount", "no invoice total found"))
    if dtype == "statement" and fields.get("closing_balance") is None and at is None:
        items.append(_Reason("unverified", "missing_balance", "no closing balance found"))
    if dtype == "unknown":
        items.append(_Reason("unverified", "unknown_doc_type", "not recognisable as invoice/statement/report"))
    return done()
