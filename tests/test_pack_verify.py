"""Month-end Document Pack v0.1: deterministic document verification.

All documents are synthetic: PDFs are drawn with CoreGraphics/CoreText via
PyObjC (skipped where Quartz is unavailable), plus a hand-written minimal PDF;
CSV statements live in tests/fixtures/pack/. No network, no model calls.
"""
from __future__ import annotations

import datetime as dt
import sys
import types
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from scaffold.agent.desktop.pack import pdftext
from scaffold.agent.desktop.pack.verify import (
    FIELD_NAMES,
    SourceSpec,
    check,
    extract_fields,
    find_dates,
    gstin_check_char,
    gstin_valid,
    month_bounds,
    parse_amount,
    parse_date,
)

FIX = Path(__file__).parent / "fixtures" / "pack"

try:
    import CoreText  # noqa: F401
    import Quartz
    from CoreFoundation import CFAttributedStringCreate
    from Foundation import NSURL

    HAVE_QUARTZ = hasattr(Quartz, "PDFDocument")
except Exception:  # pragma: no cover - non-macOS
    HAVE_QUARTZ = False

needs_quartz = pytest.mark.skipif(not HAVE_QUARTZ, reason="PyObjC Quartz/PDFKit not available")


def _g(first14: str) -> str:
    return first14 + gstin_check_char(first14)


OWNER_GSTIN = _g("29ABCDE1234F1Z")  # synthetic, valid checksum
VENDOR_GSTIN = _g("33AAACR5055K1Z")
OTHER_GSTIN = _g("07AAACZ9999Q1Z")


def make_pdf(path: Path, lines, *, password: str | None = None, image_only: bool = False) -> Path:
    """Draw text lines onto a one-page PDF with CoreText (optionally encrypted)."""
    aux = None
    if password:
        aux = {Quartz.kCGPDFContextUserPassword: password,
               Quartz.kCGPDFContextOwnerPassword: password + "-owner"}
    ctx = Quartz.CGPDFContextCreateWithURL(
        NSURL.fileURLWithPath_(str(path)), Quartz.CGRectMake(0, 0, 612, 792), aux)
    Quartz.CGPDFContextBeginPage(ctx, None)
    if image_only:  # a "scan": shapes only, no text layer
        Quartz.CGContextSetRGBFillColor(ctx, 0.2, 0.2, 0.2, 1)
        for i in range(20):
            Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(40, 700 - i * 20, 300, 8))
    else:
        font = CoreText.CTFontCreateWithName("Helvetica", 10, None)
        y = 760
        for ln in lines:
            attr = CFAttributedStringCreate(None, ln, {CoreText.kCTFontAttributeName: font})
            Quartz.CGContextSetTextPosition(ctx, 40, y)
            CoreText.CTLineDraw(CoreText.CTLineCreateWithAttributedString(attr), ctx)
            y -= 14
    Quartz.CGPDFContextEndPage(ctx)
    Quartz.CGPDFContextClose(ctx)
    return path


def minimal_pdf(lines) -> bytes:
    """Hand-written single-page PDF with a Helvetica text stream (no generator needed)."""
    esc = lambda s: s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    body = "BT /F1 11 Tf 40 760 Td 14 TL " + " ".join(f"({esc(l)}) Tj T*" for l in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(body)} >>\nstream\n{body}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def gst_invoice_lines(date="15/10/2026", inv="SYN/26-27/0042", buyer=OWNER_GSTIN, extra=()):
    return [
        "Coolcloud Hosting Pvt Ltd",
        f"Supplier GSTIN: {VENDOR_GSTIN}",
        "TAX INVOICE",
        f"Invoice No: {inv}",
        f"Invoice Date: {date}",
        f"Bill To: Owner Studio LLP   GSTIN: {buyer}",
        "Description: Cloud hosting, October 2026",
        "Taxable Value ₹1,04,624.39",
        "CGST @ 9%  ₹9,416.20",
        "SGST @ 9%  ₹9,416.19",
        "Grand Total ₹1,23,456.78",
        *extra,
    ]


HOSTING = SourceSpec("coolcloud", doc_type="invoice", vendor_keywords=("Coolcloud",),
                     currency="INR", owner_gstin=OWNER_GSTIN)


# ── unit: parsers ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("s,exp", [
    ("₹1,23,456.78", Decimal("123456.78")),
    ("INR 1,23,456.78", Decimal("123456.78")),
    ("Rs. 12,34,56,789.00", Decimal("123456789.00")),
    ("$1,234.56", Decimal("1234.56")),
    ("USD 20.00", Decimal("20.00")),
    ("1,000.00 Dr", Decimal("-1000.00")),
    ("1,000.00 Cr", Decimal("1000.00")),
    ("(500.00)", Decimal("-500.00")),
    ("-75.5", Decimal("-75.5")),
    ("1,2345.00", None),
    ("abc", None),
])
def test_parse_amount(s, exp):
    assert parse_amount(s) == exp


@pytest.mark.parametrize("s,exp", [
    ("15/10/2026", dt.date(2026, 10, 15)),
    ("03/10/2026", dt.date(2026, 10, 3)),  # day-first (India)
    ("10/15/2026", dt.date(2026, 10, 15)),  # impossible day-first -> month-first
    ("15-10-26", dt.date(2026, 10, 15)),
    ("2026-10-15", dt.date(2026, 10, 15)),
    ("15-Oct-2026", dt.date(2026, 10, 15)),
    ("15 October 2026", dt.date(2026, 10, 15)),
    ("October 15, 2026", dt.date(2026, 10, 15)),
    ("Sept 3, 2026", dt.date(2026, 9, 3)),
    ("1st Nov 2026", dt.date(2026, 11, 1)),
])
def test_parse_date(s, exp):
    assert parse_date(s) == exp


def test_month_bounds_forms():
    exp = (dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    assert month_bounds("2026-10") == exp
    assert month_bounds("Oct 2026") == exp
    assert month_bounds("10/2026") == exp
    assert month_bounds((2026, 10)) == exp
    assert month_bounds(dt.date(2026, 10, 9)) == exp
    assert month_bounds("2028-02")[1] == dt.date(2028, 2, 29)


def test_gstin_checksum():
    assert gstin_valid("27AAPFU0939F1ZV")  # widely published example GSTIN
    assert not gstin_valid("27AAPFU0939F1ZA")
    assert gstin_valid(OWNER_GSTIN) and gstin_valid(VENDOR_GSTIN)
    assert not gstin_valid("27AAPFU0939F0ZV")  # entity code 0 is not allowed
    assert not gstin_valid("27aapfu0939f1zv".upper()[:-1])


def test_find_dates_does_not_double_count():
    ds = find_dates("Period: 01/10/2026 to 31/10/2026, printed 2026-11-02")
    assert [d for d, *_ in ds] == [dt.date(2026, 10, 1), dt.date(2026, 10, 31), dt.date(2026, 11, 2)]


def test_extract_fields_usd_saas_invoice():
    text = "\n".join([
        "Anthropic, PBC", "Invoice number ABCD1234-0007", "Date of issue October 3, 2026",
        "Billing period Oct 1 – Oct 31, 2026", "Subtotal $200.00", "Tax $0.00",
        "Total $200.00", "Amount due $200.00 USD",
    ])
    f = extract_fields(text, SourceSpec("anthropic", vendor_keywords=("Anthropic",)))
    assert f["doc_type"] == "invoice"
    assert f["vendor"] == "Anthropic"
    assert f["invoice_no"] == "ABCD1234-0007"
    assert f["doc_date"] == dt.date(2026, 10, 3)
    assert (f["period_start"], f["period_end"]) == (dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    assert f["currency"] == "USD"
    assert f["amount_total"] == Decimal("200.00")
    assert f["tax_amount"] == Decimal("0.00")


def test_extract_fields_statement_text():
    text = "\n".join([
        "Synthetic Bank Ltd", "Statement of Account",
        "Statement Period: 01-Oct-2026 to 31-Oct-2026",
        "Opening Balance: ₹1,02,345.00 Cr", "Closing Balance: ₹1,19,999.50 Cr",
    ])
    f = extract_fields(text)
    assert f["doc_type"] == "statement"
    assert f["period_start"] == dt.date(2026, 10, 1) and f["period_end"] == dt.date(2026, 10, 31)
    assert f["opening_balance"] == Decimal("102345.00")
    assert f["closing_balance"] == Decimal("119999.50")
    assert f["currency"] == "INR"


def test_statement_for_month_phrase():
    f = extract_fields("Synthetic Bank\nAccount statement for Oct 2026\nClosing Balance 10.00")
    assert (f["period_start"], f["period_end"]) == (dt.date(2026, 10, 1), dt.date(2026, 10, 31))


def test_template_expansion_mechanism():
    v = pdftext.derive_vars(pan="ABCDE1234F", dob=dt.date(1990, 1, 31), mobile="+91 98765 43210")
    got = pdftext.expand_templates(
        ["<pan_lower><dob_ddmmyyyy>", "<name_first4_lower><dob_ddmm>", "<mobile_last4>"], v)
    assert got == [("template:<pan_lower><dob_ddmmyyyy>", "abcde1234f31011990"),
                   ("template:<mobile_last4>", "3210")]  # unknown placeholder -> skipped


# ── PDFs (PDFKit) ───────────────────────────────────────────────────────────

@needs_quartz
def test_gst_invoice_verified(tmp_path):
    p = make_pdf(tmp_path / "inv.pdf", gst_invoice_lines())
    v = check(p, HOSTING, "2026-10")
    assert v.status == "verified", v.reasons
    f = v.fields
    assert set(FIELD_NAMES) <= set(f)
    assert f["invoice_no"] == "SYN/26-27/0042"
    assert f["doc_date"] == dt.date(2026, 10, 15)
    assert f["amount_total"] == Decimal("123456.78")
    assert f["tax_amount"] == Decimal("18832.39")  # CGST + SGST
    assert f["gstin_supplier"] == VENDOR_GSTIN and f["gstin_buyer"] == OWNER_GSTIN
    assert f["currency"] == "INR" and f["vendor"] == "Coolcloud"
    assert v.backend == "pdfkit" and v.sha256 and v.dedupe_key == "coolcloud|syn/26-27/0042"
    assert v.to_dict()["fields"]["amount_total"] == "123456.78"


@needs_quartz
def test_invoice_wrong_period(tmp_path):
    p = make_pdf(tmp_path / "inv.pdf", gst_invoice_lines(date="15/08/2026"))
    v = check(p, HOSTING, "2026-10")
    assert v.status == "wrong_period" and "date_outside_month" in v.codes


@needs_quartz
def test_invoice_dated_early_next_month_is_flag_not_fail(tmp_path):
    p = make_pdf(tmp_path / "inv.pdf", gst_invoice_lines(date="03/11/2026"))
    v = check(p, HOSTING, "2026-10")
    assert v.status == "verified" and "dated_next_month" in v.flags
    assert check(p, HOSTING, "2026-10", tolerance_days=1).status == "wrong_period"


@needs_quartz
def test_saas_invoice_dated_next_month_but_billing_period_in_month(tmp_path):
    p = make_pdf(tmp_path / "saas.pdf", [
        "Anthropic, PBC", "Invoice number ABCD1234-0008", "Date of issue November 1, 2026",
        "Billing period Oct 1 – Oct 31, 2026", "Total $200.00", "Amount due $200.00 USD"])
    v = check(p, SourceSpec("anthropic", vendor_keywords=("Anthropic",), currency="USD"), "Oct 2026")
    assert v.status == "verified", v.reasons
    assert v.fields["currency"] == "USD" and v.fields["amount_total"] == Decimal("200.00")


@needs_quartz
@pytest.mark.parametrize("word", ["SAMPLE", "DRAFT", "Test invoice", "PROFORMA"])
def test_suspicious_words(tmp_path, word):
    p = make_pdf(tmp_path / "inv.pdf", gst_invoice_lines(extra=[word]))
    v = check(p, HOSTING, "2026-10")
    assert v.status == "suspicious" and "suspicious_text" in v.codes


@needs_quartz
def test_vendor_mismatch_is_suspicious(tmp_path):
    p = make_pdf(tmp_path / "inv.pdf", gst_invoice_lines())
    src = SourceSpec("other", doc_type="invoice", vendor_keywords=("Razorpay",))
    v = check(p, src, "2026-10")
    assert v.status == "suspicious" and "vendor_mismatch" in v.codes


@needs_quartz
def test_buyer_gstin_mismatch(tmp_path):
    p = make_pdf(tmp_path / "inv.pdf", gst_invoice_lines(buyer=OTHER_GSTIN))
    v = check(p, HOSTING, "2026-10")
    assert v.status == "suspicious" and "buyer_gstin_mismatch" in v.codes
    assert v.fields["gstin_buyer"] == OTHER_GSTIN


@needs_quartz
def test_bad_gstin_checksum(tmp_path):
    bad = VENDOR_GSTIN[:-1] + ("A" if VENDOR_GSTIN[-1] != "A" else "B")
    lines = [l.replace(VENDOR_GSTIN, bad) for l in gst_invoice_lines()]
    v = check(make_pdf(tmp_path / "inv.pdf", lines), HOSTING, "2026-10")
    assert v.status == "suspicious" and "gstin_checksum_fail" in v.codes


@needs_quartz
def test_duplicate_by_hash_and_invoice_no(tmp_path):
    p = make_pdf(tmp_path / "inv.pdf", gst_invoice_lines())
    first = check(p, HOSTING, "2026-10")
    assert check(p, HOSTING, "2026-10", seen={first.sha256}).status == "duplicate"
    p2 = make_pdf(tmp_path / "inv2.pdf", gst_invoice_lines(extra=["Reprinted copy"]))
    v2 = check(p2, HOSTING, "2026-10", seen=[first.dedupe_key])
    assert v2.status == "duplicate" and "same_invoice_no" in v2.codes


@needs_quartz
def test_encrypted_pdf_locked_then_unlocked_by_template(tmp_path):
    pw = "abcde1234f31011990"
    p = make_pdf(tmp_path / "stmt.pdf", [
        "Synthetic Bank Ltd", "Statement of Account",
        "Statement Period: 01/10/2026 to 31/10/2026",
        "Opening Balance: 1,02,345.00", "Closing Balance: 1,19,999.50"], password=pw)
    src = SourceSpec("bank", doc_type="statement", vendor_keywords=("Synthetic Bank",),
                     password_templates=("<pan_lower><dob_ddmmyyyy>",))
    locked = check(p, src, "2026-10")
    assert locked.status == "locked" and "encrypted" in locked.codes
    wrong = check(p, src, "2026-10", template_vars=pdftext.derive_vars(pan="ZZZZZ9999Z", dob="1990-01-31"))
    assert wrong.status == "locked"
    v = check(p, src, "2026-10", prev_closing="1,02,345.00",
              template_vars=pdftext.derive_vars(pan="ABCDE1234F", dob="31/01/1990"))
    assert v.status == "verified", v.reasons
    assert v.unlocked_by == "template:<pan_lower><dob_ddmmyyyy>"
    assert v.fields["closing_balance"] == Decimal("119999.50")
    # the password value never leaks into the verdict
    assert pw not in repr(v) and pw not in str(v.to_dict())
    # a caller-supplied (keychain) password is labelled, not echoed
    v2 = check(p, src, "2026-10", password=pw)
    assert v2.status == "verified" and v2.unlocked_by == "password" and pw not in str(v2.to_dict())


@needs_quartz
def test_statement_pdf_continuity_break(tmp_path):
    p = make_pdf(tmp_path / "stmt.pdf", [
        "Synthetic Bank Ltd", "Statement of Account",
        "Statement Period: 01-Nov-2026 to 30-Nov-2026",
        "Opening Balance: ₹1,29,999.50 Cr", "Closing Balance: ₹1,14,999.50 Cr"])
    src = SourceSpec("bank", vendor_keywords=("Synthetic Bank",))
    ok = check(p, src, "2026-11", prev_closing=Decimal("129999.50"))
    assert ok.status == "verified", ok.reasons
    bad = check(p, src, "2026-11", prev_closing="1,19,999.50")
    assert bad.status == "unverified" and "continuity_break" in bad.codes


@needs_quartz
def test_scanned_pdf_unverified_no_ocr(tmp_path):
    p = make_pdf(tmp_path / "scan.pdf", [], image_only=True)
    v = check(p, HOSTING, "2026-10")
    assert v.status == "unverified" and "no_text_layer" in v.codes


@needs_quartz
def test_hand_written_minimal_pdf(tmp_path):
    p = tmp_path / "min.pdf"
    p.write_bytes(minimal_pdf(["Coolcloud Hosting", "Invoice No: MIN-77", "Invoice Date: 2026-10-05",
                               "Total INR 4,999.00"]))
    v = check(p, SourceSpec("coolcloud", vendor_keywords=("Coolcloud",)), "2026-10")
    assert v.status == "verified", v.reasons
    assert v.fields["invoice_no"] == "MIN-77" and v.fields["amount_total"] == Decimal("4999.00")


@needs_quartz
def test_corrupt_pdf_body(tmp_path):
    p = tmp_path / "broken.pdf"
    p.write_bytes(b"%PDF-1.4\n" + b"\x00garbage" * 50)
    assert check(p, HOSTING, "2026-10").status == "corrupt"


# ── file sanity (no PDF backend needed) ─────────────────────────────────────

def test_html_saved_as_pdf(tmp_path):
    p = tmp_path / "statement.pdf"
    p.write_text("<!DOCTYPE html><html><body><h1>Session expired</h1>Please log in again.</body></html>")
    v = check(p, HOSTING, "2026-10")
    assert v.status == "corrupt" and "html_not_pdf" in v.codes


def test_empty_and_bad_magic(tmp_path):
    e = tmp_path / "e.pdf"
    e.write_bytes(b"")
    assert check(e, HOSTING, "2026-10").codes == ["empty_file"]
    b = tmp_path / "b.pdf"
    b.write_bytes(b"PK\x03\x04 not a pdf")
    v = check(b, HOSTING, "2026-10")
    assert v.status == "corrupt" and "bad_magic" in v.codes
    assert check(tmp_path / "missing.pdf", HOSTING, "2026-10").status == "corrupt"


def test_html_saved_as_csv(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("<html><head><title>500 Internal Server Error</title></head><body>oops</body></html>")
    assert check(p, "bank", "2026-10").status == "corrupt"


# ── tabular statements ──────────────────────────────────────────────────────

BANK = SourceSpec("bank", vendor_keywords=("Synthetic Bank",), currency="INR")


def test_csv_statement_verified_with_indian_numbers():
    v = check(FIX / "bank_statement_oct2026.csv", BANK, "2026-10", prev_closing="1,02,345.00")
    assert v.status == "verified", v.reasons
    f = v.fields
    assert f["doc_type"] == "statement"
    assert (f["period_start"], f["period_end"]) == (dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    assert f["opening_balance"] == Decimal("102345.00")
    assert f["closing_balance"] == Decimal("119999.50")
    # "DEMAND DRAFT" in a narration is not a draft watermark
    assert "suspicious_text" not in v.codes


def test_csv_statement_continuity_break_and_partial_period():
    v = check(FIX / "bank_statement_nov2026_break.csv", BANK, "2026-11", prev_closing="1,19,999.50")
    assert v.status == "unverified"
    assert "continuity_break" in v.codes and "partial_period" in v.flags


def test_csv_statement_wrong_period():
    v = check(FIX / "bank_statement_oct2026.csv", BANK, "2026-09")
    assert v.status == "wrong_period" and "period_outside_month" in v.codes


def test_csv_running_balance_tamper(tmp_path):
    src = (FIX / "bank_statement_oct2026.csv").read_text()
    p = tmp_path / "t.csv"
    p.write_text(src.replace('"1,27,345.00"', '"1,37,345.00"'))
    v = check(p, BANK, "2026-10")
    assert v.status == "suspicious" and "running_balance_break" in v.codes


def test_gateway_settlement_csv():
    v = check(FIX / "gateway_settlement_oct2026.csv",
              SourceSpec("razorpay", vendor_keywords=("setl_",)), "2026-10")
    assert v.status == "verified", v.reasons
    assert v.fields["doc_type"] == "gateway_report"
    assert v.fields["amount_total"] == Decimal("22000.00")
    assert v.fields["tax_amount"] == Decimal("93.42")


def _xlsx(path: Path, rows):
    def cell(ref, val):
        if isinstance(val, (int, float)):
            return f'<c r="{ref}"><v>{val}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{val}</t></is></c>'
    xml_rows = "".join(
        f'<row r="{i}">' + "".join(cell(f"{chr(65 + j)}{i}", v) for j, v in enumerate(r) if v != "") + "</row>"
        for i, r in enumerate(rows, 1))
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}"/>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData>{xml_rows}</sheetData></worksheet>')
    return path


def test_xlsx_statement(tmp_path):
    serial = (dt.date(2026, 10, 2) - dt.date(1899, 12, 30)).days
    p = _xlsx(tmp_path / "s.xlsx", [
        ["Synthetic Bank Ltd"],
        ["Txn Date", "Description", "Debit", "Credit", "Balance"],
        [serial, "Opening transfer", "", 1000.5, 51000.5],
        ["30/10/2026", "Fee", 0.5, "", 51000.0],
    ])
    v = check(p, BANK, "2026-10", prev_closing="50,000.00")
    assert v.status in ("verified",), v.reasons
    assert v.fields["period_start"] == dt.date(2026, 10, 2)
    assert v.fields["opening_balance"] == Decimal("50000.0")
    assert v.fields["closing_balance"] == Decimal("51000.0")


def test_html_table_xls_bank_export(tmp_path):
    p = tmp_path / "stmt.xls"
    p.write_text("<html><body><p>Synthetic Bank Ltd</p><table>"
                 "<tr><th>Date</th><th>Narration</th><th>Withdrawal</th><th>Deposit</th><th>Balance</th></tr>"
                 "<tr><td>03/10/2026</td><td>NEFT</td><td></td><td>1,000.00</td><td>11,000.00</td></tr>"
                 "<tr><td>29/10/2026</td><td>UPI</td><td>500.00</td><td></td><td>10,500.00</td></tr>"
                 "</table></body></html>")
    v = check(p, BANK, "2026-10", prev_closing="10,000.00")
    assert v.status == "verified", v.reasons
    assert v.fields["closing_balance"] == Decimal("10500.00")


def test_bad_xlsx_is_corrupt(tmp_path):
    p = tmp_path / "s.xlsx"
    p.write_bytes(b"PK\x03\x04truncated")
    assert check(p, BANK, "2026-10").status == "corrupt"


# ── backend fallbacks ───────────────────────────────────────────────────────

def test_no_backend_degrades_gracefully(tmp_path, monkeypatch):
    monkeypatch.setattr(pdftext, "available_backends", lambda: [])
    p = tmp_path / "a.pdf"
    p.write_bytes(minimal_pdf(["Invoice No: X-1"]))
    v = check(p, HOSTING, "2026-10")
    assert v.status == "unverified" and "no_pdf_backend" in v.codes
    enc = tmp_path / "enc.pdf"
    enc.write_bytes(minimal_pdf(["x"]).replace(b"/Root 1 0 R", b"/Root 1 0 R /Encrypt 9 0 R"))
    v = check(enc, HOSTING, "2026-10")
    assert v.status == "locked" and "encrypted_no_backend" in v.codes


def test_pypdf_fallback_glue(tmp_path, monkeypatch):
    """Exercise the pypdf path with a stub module (real pypdf may be absent)."""
    pw = "s3cret-value"

    class Page:
        def extract_text(self):
            return "Coolcloud Hosting\nInvoice No: PY-9\nInvoice Date: 09/10/2026\nTotal ₹2,500.00"

    class Reader:
        def __init__(self, path):
            self.is_encrypted, self._ok, self.pages = True, False, [Page()]

        def decrypt(self, value):
            return 1 if value == pw else 0

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=Reader))
    monkeypatch.setattr(pdftext, "available_backends", lambda: ["pypdf"])
    p = tmp_path / "a.pdf"
    p.write_bytes(minimal_pdf(["placeholder"]))
    src = SourceSpec("coolcloud", vendor_keywords=("Coolcloud",))
    assert check(p, src, "2026-10").status == "locked"
    v = check(p, src, "2026-10", passwords=[("keychain:coolcloud", pw)])
    assert v.status == "verified", v.reasons
    assert v.backend == "pypdf" and v.unlocked_by == "keychain:coolcloud"
    assert v.fields["amount_total"] == Decimal("2500.00")
    assert pw not in str(v.to_dict())
