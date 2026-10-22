"""
dp_common.py — small shared helpers for the doc_pack testbed (stdlib only).

    inr(paise)            12345678 -> "1,23,456.78"   (Indian digit grouping)
    usd(cents)            123400   -> "1,234.00"
    gstin(state, pan)     valid GSTIN with the official mod-36 checksum
    gstin_ok(s)           checksum check
    totp(secret_b32, t)   RFC 6238 (SHA1, 30 s, 6 digits) — CloudCo's optional 2FA
    month helpers         "2026-09" <-> dates, DD/MM/YYYY parsing
"""
from __future__ import annotations

import base64
import calendar
import hashlib
import hmac
import struct
import time
from datetime import date, timedelta

GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December"]


# ---------------------------------------------------------------- money --

def inr(paise: int) -> str:
    """Indian grouping: 12345678 paise -> '1,23,456.78' (sign kept)."""
    neg = paise < 0
    paise = abs(int(paise))
    rupees, p = divmod(paise, 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if neg else "") + f"{s}.{p:02d}"


def usd(cents: int) -> str:
    return f"{int(cents) // 100:,}.{int(cents) % 100:02d}"


def parse_inr(s: str) -> int:
    """'1,23,456.78' (or '-1,234.00') -> paise."""
    s = s.strip().replace(",", "").replace("₹", "").replace("Rs.", "").strip()
    neg = s.startswith("-")
    s = s.lstrip("-")
    r, _, p = s.partition(".")
    v = int(r or 0) * 100 + int((p + "00")[:2])
    return -v if neg else v


# ---------------------------------------------------------------- GSTIN --

def gst_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14.upper()):
        v = GST_CHARS.index(ch) * (2 if i % 2 else 1)
        total += v // 36 + v % 36
    return GST_CHARS[(36 - total % 36) % 36]


def gstin(state: int, pan: str, entity: str = "1") -> str:
    body = f"{state:02d}{pan.upper()}{entity}Z"
    return body + gst_check_char(body)


def gstin_ok(s: str) -> bool:
    s = (s or "").strip().upper()
    if len(s) != 15 or any(c not in GST_CHARS for c in s):
        return False
    return gst_check_char(s[:14]) == s[14]


# ----------------------------------------------------------------- TOTP --

def totp(secret_b32: str, t: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = int((time.time() if t is None else t) // step)
    d = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = d[-1] & 0x0F
    code = (struct.unpack(">I", d[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return f"{code:0{digits}d}"


def totp_ok(secret_b32: str, code: str, t: float | None = None, window: int = 1) -> bool:
    now = time.time() if t is None else t
    return any(hmac.compare_digest(totp(secret_b32, now + k * 30), (code or "").strip())
               for k in range(-window, window + 1))


# --------------------------------------------------------------- months --

def month_bounds(m: str) -> tuple[date, date]:
    y, mo = int(m[:4]), int(m[5:7])
    return date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1])


def month_label(m: str) -> str:
    """'2026-09' -> 'September 2026'."""
    return f"{MONTH_NAMES[int(m[5:7]) - 1]} {m[:4]}"


def month_short(m: str) -> str:
    """'2026-09' -> 'Sep 2026'."""
    return f"{MONTH_NAMES[int(m[5:7]) - 1][:3]} {m[:4]}"


def next_month(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y + (mo == 12)}-{(mo % 12) + 1:02d}"


def prev_month(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y - (mo == 1)}-{12 if mo == 1 else mo - 1:02d}"


def dmy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def parse_dmy(s: str) -> date | None:
    try:
        dd, mm, yy = (int(x) for x in (s or "").strip().split("/"))
        return date(yy, mm, dd)
    except (ValueError, TypeError):
        return None


def long_date(d: date) -> str:
    """date -> '01 Oct 2026'."""
    return d.strftime("%d %b %Y")


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def days(d0: date, d1: date):
    d = d0
    while d <= d1:
        yield d
        d += timedelta(days=1)
