"""Shared stdlib helpers for the teach-once testbed (no AWOS imports)."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Optional


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    return sha256_bytes(Path(p).read_bytes())


def manifest(root: Path) -> Dict[str, str]:
    """{relative posix path: sha256} for every regular file under root."""
    root = Path(root)
    out: Dict[str, str] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = sha256_file(p)
    return out


def dirs(root: Path) -> List[str]:
    root = Path(root)
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_dir())


def write_json(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def slug(name: str) -> str:
    """'Northwind Credit Union' -> 'northwind-credit-union'."""
    return "-".join(re.findall(r"[a-z0-9]+", name.lower()))


# ---------------------------------------------------------------- money ----

def parse_money(text: str) -> Optional[Decimal]:
    """'$1,234.50' / '1234.5' / '€ 980,00' / 'EUR 1.250,00' -> Decimal (2 dp)."""
    s = re.sub(r"[^\d.,\-]", "", str(text))
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):          # 1.250,00
            s = s.replace(".", "").replace(",", ".")
        else:                                      # 1,250.00
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = (head.replace(",", "") + "." + tail) if len(tail) == 2 else s.replace(",", "")
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def fmt_money(d) -> str:
    return f"{Decimal(d).quantize(Decimal('0.01')):.2f}"


# ---------------------------------------------------------------- dates ----

DATE_FORMATS = ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %b %Y", "%d %B %Y",
                "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d", "%Y.%m.%d", "%d/%m/%Y")


def parse_date(text: str, formats: Iterable[str] = DATE_FORMATS) -> Optional[dt.date]:
    t = str(text).strip()
    for f in formats:
        try:
            return dt.datetime.strptime(t, f).date()
        except ValueError:
            continue
    return None


def find_dates(text: str) -> List[tuple]:
    """Every (raw, date) found in free text, using DATE_FORMATS."""
    pats = [r"\d{4}-\d{2}-\d{2}", r"\d{4}[/.]\d{2}[/.]\d{2}", r"\d{1,2}[/.]\d{1,2}[/.]\d{4}",
            r"[A-Z][a-z]+ \d{1,2}, \d{4}", r"\d{1,2} [A-Z][a-z]+ \d{4}"]
    out = []
    for pat in pats:
        for m in re.finditer(pat, text):
            d = parse_date(m.group(0))
            if d:
                out.append((m.group(0), d))
    return out


def week_bounds(day: dt.date) -> tuple:
    start = day - dt.timedelta(days=day.weekday())
    return start, start + dt.timedelta(days=6)


# ------------------------------------------------------------------ csv ----

def csv_rows(text: str) -> List[List[str]]:
    return [r for r in csv.reader(io.StringIO(text)) if r]


def csv_dicts(text: str) -> List[dict]:
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def csv_text(rows: List[List[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def append_csv_row(path: Path, row: List[str]) -> None:
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + csv_text([row]), encoding="utf-8")


# ------------------------------------------------------------------ pdf ----

def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(lines: List[str]) -> bytes:
    """A minimal valid one-page PDF with an uncompressed text stream."""
    body = ["BT", "/F1 10 Tf", "12 TL", "50 780 Td"]
    body += [f"({_pdf_escape(ln)}) Tj T*" for ln in lines]
    body.append("ET")
    stream = "\n".join(body).encode("latin-1", "replace")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def pdf_text(data: bytes) -> str:
    """Text lines of a make_pdf() PDF (what Preview would show)."""
    raw = data.decode("latin-1", "replace")
    lines = re.findall(r"\(((?:\\.|[^\\)])*)\) Tj", raw)
    return "\n".join(re.sub(r"\\(.)", r"\1", ln) for ln in lines)


def file_text(path: Path) -> str:
    """What a viewer app shows for a file: PDF text, else decoded text."""
    data = Path(path).read_bytes()
    if data.startswith(b"%PDF"):
        return pdf_text(data)
    return data.decode("utf-8", "replace")
