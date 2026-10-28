"""
dp_docs.py — render the portals' documents from build_world() data.

Every field the verifier checks is printed on its own line as "Label: value" so any text
extractor (PDFKit, pdftotext, the plain fallback parser) can find it. Rupee amounts use
"₹" and Indian grouping (1,23,456.78); CloudCo prints USD.

    cloudco_invoice(w, m) / payflow_invoice(w, m) / telconet_bill(w, m) -> PDF bytes
    bank_statement(w, d0, d1, password) -> PDF bytes (any range inside the ledger window)
    payflow_csv(w, m) -> str
"""
from __future__ import annotations

import csv
import io
from datetime import date

import dp_common as C
from dp_pdf import Doc

GREY = (0.35, 0.35, 0.4)


def _r(p: int) -> str:
    return "₹ " + C.inr(p)


def _rows(d: Doc, y: float, cols: list[float], cells: list[str], size: float = 9, bold: bool = False) -> None:
    for x, c in zip(cols, cells):
        d.text(x, y, c, size=size, bold=bold)


# ------------------------------------------------------------------ CloudCo --

def cloudco_invoice(w: dict, m: str) -> bytes:
    inv, prof = w["cloudco"][m], w["profile"]
    d = Doc(title=f"CloudCo invoice {inv['number']}", author="CloudCo, Inc.")
    d.box(0, 770, 595, 72, color=(0.13, 0.2, 0.42))
    d.text(40, 800, "CloudCo", size=22, bold=True, color=(1, 1, 1))
    d.text(420, 800, "INVOICE", size=20, bold=True, color=(1, 1, 1))
    y = 740
    for ln in ("CloudCo, Inc.", "548 Market Street, Suite 300, San Francisco, CA 94104, USA",
               "billing@cloudco.example  ·  US EIN 84-2219034"):
        d.text(40, y, ln, size=9, color=GREY)
        y -= 13
    y -= 10
    for ln in (f"Invoice number: {inv['number']}", f"Invoice date: {inv['date']}",
               f"Billing period: {inv['period'][0]} to {inv['period'][1]}", "Currency: USD"):
        d.text(40, y, ln, size=10, bold=ln.startswith("Invoice number"))
        y -= 15
    y -= 8
    d.text(40, y, "Bill to:", size=10, bold=True)
    y -= 14
    for ln in (prof["business"], prof["address"], "India", "Customer tax ID: Not provided"):
        d.text(40, y, ln, size=10)
        y -= 14
    y -= 14
    cols = [40, 330, 400, 480]
    d.rule(40, y + 12, 555)
    _rows(d, y, cols, ["Description", "Qty", "Unit price", "Amount"], bold=True)
    y -= 18
    _rows(d, y, cols, ["Team plan — seat (monthly)", str(inv["seats"]), "$" + C.usd(inv["seat_price"]),
                       "$" + C.usd(inv["seats"] * inv["seat_price"])])
    y -= 15
    _rows(d, y, cols, ["API usage (metered)", "1", "$" + C.usd(inv["usage"]), "$" + C.usd(inv["usage"])])
    y -= 10
    d.rule(40, y, 555)
    y -= 18
    for ln in (f"Subtotal: ${C.usd(inv['total'])}", "Tax (0%): $0.00", f"Total (USD): ${C.usd(inv['total'])}",
               f"Amount paid: ${C.usd(inv['total'])}", "Amount due: $0.00"):
        d.text(360, y, ln, size=10, bold=ln.startswith("Total"))
        y -= 15
    y -= 12
    d.text(40, y, "Status: Paid — charged to card ending 4417", size=9)
    y -= 13
    d.text(40, y, "CloudCo does not collect Indian GST. Reverse charge may apply for the recipient.", size=8,
           color=GREY)
    return d.render()


# ------------------------------------------------------------------ PayFlow --

def payflow_invoice(w: dict, m: str) -> bytes:
    inv, prof = w["payflow"][m]["invoice"], w["profile"]
    d = Doc(title=f"PayFlow tax invoice {inv['number']}", author="PayFlow Payments Pvt Ltd")
    d.text(40, 800, "PayFlow", size=22, bold=True, color=(0.0, 0.45, 0.35))
    d.text(400, 800, "TAX INVOICE", size=16, bold=True)
    y = 770
    for ln in ("PayFlow Payments Private Limited", "7th Floor, Kamala Mills, Lower Parel, Mumbai 400013, Maharashtra",
               f"Supplier GSTIN: {inv['supplier_gstin']}", "PAN: AAKCP4417M  ·  CIN: U67190MH2019PTC331207"):
        d.text(40, y, ln, size=9, bold=ln.startswith("Supplier GSTIN"))
        y -= 13
    y -= 10
    for ln in (f"Invoice number: {inv['number']}", f"Invoice date: {inv['date']}",
               f"Billing period: {inv['period'][0]} to {inv['period'][1]}",
               f"Place of supply: {prof['state']} ({prof['state_code']:02d})"):
        d.text(40, y, ln, size=10, bold=ln.startswith("Invoice number"))
        y -= 15
    y -= 8
    d.text(40, y, "Recipient:", size=10, bold=True)
    y -= 14
    for ln in (prof["business"], prof["address"], f"Recipient GSTIN: {inv['recipient_gstin']}"):
        d.text(40, y, ln, size=10)
        y -= 14
    y -= 14
    cols = [40, 330, 450]
    d.rule(40, y + 12, 555)
    _rows(d, y, cols, ["Description", "SAC", "Taxable value"], bold=True)
    y -= 18
    _rows(d, y, cols, [f"Payment gateway fees — {C.month_label(m)}", inv["sac"], _r(inv["taxable"])])
    y -= 10
    d.rule(40, y, 555)
    y -= 18
    d.text(330, y, f"Taxable value: {_r(inv['taxable'])}", size=10)
    y -= 15
    for k, v in inv["tax"].items():
        d.text(330, y, f"{k}: {_r(v)}", size=10)
        y -= 15
    d.text(330, y, f"Invoice total: {_r(inv['total'])}", size=11, bold=True)
    y -= 26
    d.text(40, y, "Fees were deducted from settlements; no payment is due. Tax is not payable on reverse charge.",
           size=8, color=GREY)
    y -= 12
    d.text(40, y, "This is a computer generated invoice and does not need a signature.", size=8, color=GREY)
    return d.render()


def payflow_csv(w: dict, m: str) -> str:
    f = io.StringIO()
    wr = csv.writer(f, lineterminator="\n")
    wr.writerow(["settlement_id", "settlement_date", "utr", "transactions", "gross_amount", "fees",
                 "gst_on_fees", "net_amount", "status"])
    for r in w["payflow"][m]["rows"]:
        wr.writerow([r["id"], r["date"], r["utr"], r["count"], f"{r['gross'] / 100:.2f}", f"{r['fee'] / 100:.2f}",
                     f"{r['gst'] / 100:.2f}", f"{r['net'] / 100:.2f}", "settled"])
    return f.getvalue()


# ----------------------------------------------------------------- TelcoNet --

def telconet_bill(w: dict, m: str) -> bytes:
    b, prof = w["telconet"][m], w["profile"]
    d = Doc(title=f"TelcoNet bill {b['number']}", author="TelcoNet Limited")
    d.box(0, 780, 595, 62, color=(0.85, 0.1, 0.25))
    d.text(40, 802, "TelcoNet", size=22, bold=True, color=(1, 1, 1))
    d.text(330, 802, "Tax Invoice — Monthly Bill", size=13, bold=True, color=(1, 1, 1))
    y = 755
    for ln in ("TelcoNet Limited", f"Circle office: {prof['city']}, {prof['state']}",
               f"Supplier GSTIN: {b['supplier_gstin']}"):
        d.text(40, y, ln, size=9, bold=ln.startswith("Supplier"))
        y -= 13
    y -= 10
    for ln in (f"Bill number: {b['number']}", f"Account number: {b['account']}", f"Bill date: {b['date']}",
               f"Bill period: {b['period'][0]} to {b['period'][1]}", f"Due date: {b['due']}"):
        d.text(40, y, ln, size=10, bold=ln.startswith("Bill number"))
        y -= 15
    y -= 8
    d.text(40, y, "Billed to:", size=10, bold=True)
    y -= 14
    for ln in (prof["business"], prof["address"], f"Customer GSTIN: {b['buyer_gstin']}"):
        d.text(40, y, ln, size=10)
        y -= 14
    y -= 14
    d.rule(40, y + 12, 555)
    _rows(d, y, [40, 450], ["Charges for the period", "Amount"], bold=True)
    y -= 18
    for desc, amt in b["lines"]:
        _rows(d, y, [40, 450], [desc, _r(amt)])
        y -= 15
    d.rule(40, y + 4, 555)
    y -= 14
    for ln in (f"Subtotal: {_r(b['subtotal'])}", f"CGST @9%: {_r(b['cgst'])}", f"SGST @9%: {_r(b['sgst'])}"):
        d.text(330, y, ln, size=10)
        y -= 15
    d.text(330, y, f"Total amount: {_r(b['total'])}", size=11, bold=True)
    y -= 26
    d.text(40, y, "Paid by AutoPay mandate on the due date. Late payment fee ₹ 100.00 applies after the due date.",
           size=8, color=GREY)
    return d.render()


# -------------------------------------------------------------- Bharat Bank --

def bank_statement(w: dict, d0: date, d1: date, password: str = None) -> bytes:
    bk, prof = w["bank"], w["profile"]
    tx = [t for t in bk["txns"] if d0.isoformat() <= t["date"] <= d1.isoformat()]
    before = [t for t in bk["txns"] if t["date"] < d0.isoformat()]
    opening = before[-1]["balance"] if before else bk["opening"]
    closing = tx[-1]["balance"] if tx else opening
    cr = sum(t["amount"] for t in tx if t["amount"] > 0)
    dr = -sum(t["amount"] for t in tx if t["amount"] < 0)
    d = Doc(title="Bharat Bank account statement", author="Bharat Bank")

    def head(page_no: int) -> float:
        d.box(0, 790, 595, 52, color=(0.05, 0.25, 0.55))
        d.text(40, 808, "Bharat Bank", size=20, bold=True, color=(1, 1, 1))
        d.text(400, 808, f"Statement of Account · page {page_no}", size=9, color=(1, 1, 1))
        return 765

    y = head(1)
    for ln in (f"Account holder: {prof['proprietor'].upper()} (proprietor, {prof['business']})",
               prof["address"], f"Account number: XXXXXXXX{bk['account_no'][-4:]}", "Account type: Current Account",
               f"IFSC: {bk['ifsc']}  ·  Branch: {bk['branch']}",
               f"Statement period: {C.dmy(d0)} to {C.dmy(d1)}",
               f"Opening balance: {_r(opening)}", f"Closing balance: {_r(closing)}",
               f"Total credits: {_r(cr)}", f"Total debits: {_r(dr)}"):
        d.text(40, y, ln, size=10, bold=ln.startswith(("Statement period", "Opening", "Closing")))
        y -= 14
    y -= 12
    cols = [40, 100, 330, 410, 490]
    page = 1
    hdr = ["Date", "Narration", "Debit", "Credit", "Balance"]
    d.rule(40, y + 12, 555)
    _rows(d, y, cols, hdr, bold=True, size=8)
    y -= 14
    for t in tx:
        if y < 60:
            d.new_page()
            page += 1
            y = head(page) - 10
            _rows(d, y, cols, hdr, bold=True, size=8)
            y -= 14
        amt = t["amount"]
        _rows(d, y, cols, [date.fromisoformat(t["date"]).strftime("%d/%m/%Y"), t["narration"][:44],
                           C.inr(-amt) if amt < 0 else "", C.inr(amt) if amt > 0 else "", C.inr(t["balance"])],
              size=7.5)
        y -= 12
    y -= 10
    d.text(40, max(y, 40), "This is a computer generated statement and does not require a signature.", size=8,
           color=GREY)
    return d.render(password=password)
