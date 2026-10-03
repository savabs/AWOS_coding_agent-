#!/usr/bin/env python3
"""Deterministic generator for the invoice_data_report long task.

Writes:
  project/data/2026-06.csv, 2026-07.csv, 2026-08.csv, fx_rates.csv
  reference/reports/top_customers.csv, reference/reports/duplicate_invoices.csv
  hidden_tests/expected/  (copy of the reference outputs used by the hidden tests)

Messiness injected on purpose (see README.md):
  * 2026-06.csv starts with a UTF-8 BOM
  * 2026-07.csv has renamed headers and a different column order
  * 2026-08.csv writes amounts with thousands separators ("1,234.50", quoted)
  * customer names vary in case / whitespace for the same customer_id
  * some currency codes are lower case
  * exact duplicate rows (same invoice id billed twice; one three times;
    one re-exported in the next month's file)
  * near-duplicates: same customer + amount, different invoice id and date
    (these are real, separate invoices and must NOT be flagged)
  * credit notes (negative amounts) that reduce totals

The seed search below is deterministic: it picks the first seed for which the
common mistakes (no FX conversion, double-counting duplicates, dropping credit
notes) each change the top-10 ranking, and adjacent top-11 totals are well
separated. Run: python generate_data.py
"""
from __future__ import annotations

import csv
import datetime as dt
import random
import shutil
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "project" / "data"
REF = ROOT / "reference" / "reports"
EXPECTED = ROOT / "hidden_tests" / "expected"

MONTHS = ["2026-06", "2026-07", "2026-08"]
FX = {  # 1 unit of currency = rate EUR
    "2026-06": {"USD": 0.9213, "GBP": 1.1687, "CHF": 1.0412},
    "2026-07": {"USD": 0.9158, "GBP": 1.1742, "CHF": 1.0495},
    "2026-08": {"USD": 0.9271, "GBP": 1.1805, "CHF": 1.0448},
}

COMPANY_WORDS_A = ["Acme", "Nordwind", "Bluefin", "Helvetia", "Brightside", "Kestrel",
                   "Orion", "Sable", "Tamarack", "Vireo", "Juniper", "Lumen", "Marlow",
                   "Quarry", "Redcliff", "Silverline", "Thornbury", "Umbra", "Westgate",
                   "Zephyr"]
COMPANY_WORDS_B = ["Logistics", "Foods", "Robotics", "Textiles", "Analytics", "Pharma",
                   "Media", "Energy", "Retail", "Labs"]
SUFFIX = {"EUR": ["GmbH", "B.V.", "SAS", "S.p.A."], "USD": ["Inc.", "LLC"],
          "GBP": ["Ltd", "plc"], "CHF": ["AG", "SA"]}
N_CUSTOMERS = 40


def build(seed: int):
    rng = random.Random(seed)
    names_used = set()
    customers = []
    for i in range(N_CUSTOMERS):
        cur = rng.choices(["EUR", "USD", "GBP", "CHF"], weights=[50, 22, 16, 12])[0]
        while True:
            name = f"{rng.choice(COMPANY_WORDS_A)} {rng.choice(COMPANY_WORDS_B)} {rng.choice(SUFFIX[cur])}"
            if name not in names_used:
                names_used.add(name)
                break
        weight = 1.0 / (i + 1) ** 0.55
        customers.append({"id": f"C{101 + i:03d}", "name": name, "cur": cur, "w": weight})

    invoices = []  # dicts: id, date, cid, amount (float, 2dp), cur, month
    seq = 1
    for month in MONTHS:
        y, m = map(int, month.split("-"))
        n = rng.randint(380, 440)
        days = (dt.date(y, m % 12 + 1, 1) - dt.date(y, m, 1)).days if m < 12 else 31
        rows = []
        for _ in range(n):
            c = rng.choices(customers, weights=[c["w"] for c in customers])[0]
            cur = c["cur"] if rng.random() > 0.08 else "EUR"
            amount = round(min(max(rng.lognormvariate(7.4, 0.9), 45.0), 48000.0), 2)
            date = dt.date(y, m, rng.randint(1, days))
            rows.append({"date": date, "cid": c["id"], "amount": amount, "cur": cur, "month": month})
        rows.sort(key=lambda r: r["date"])
        for r in rows:
            r["id"] = f"INV-2026-{seq:05d}"
            seq += 1
        invoices.extend(rows)
    return rng, customers, invoices


def to_eur(amount, cur, month):
    return amount if cur == "EUR" else amount * FX[month][cur]


def totals(invoices, *, fx=True, skip_cn=False):
    t = defaultdict(float)
    for inv in invoices:
        if skip_cn and inv["amount"] < 0:
            continue
        t[inv["cid"]] += to_eur(inv["amount"], inv["cur"], inv["month"]) if fx else inv["amount"]
    return t


def top(t, n=10):
    return [c for c, _ in sorted(t.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def attempt(seed: int):
    rng, customers, invoices = build(seed)
    by_id = {c["id"]: c for c in customers}
    base = totals(invoices)
    ranked = top(base, 14)

    # Credit notes: a big one on the #9/#10 customer, smaller ones elsewhere.
    credit_notes = []
    cn_targets = [(ranked[8], 0.35), (ranked[2], 0.04), (ranked[12], 0.10)]
    for k, (cid, frac) in enumerate(cn_targets, 1):
        c = by_id[cid]
        month = MONTHS[1 + (k % 2)]
        y, m = map(int, month.split("-"))
        amt_eur = base[cid] * frac
        amt = round(-(amt_eur / (1 if c["cur"] == "EUR" else FX[month][c["cur"]])), 2)
        credit_notes.append({"id": f"CN-2026-{k:04d}", "date": dt.date(y, m, 10 + 5 * k), "cid": cid,
                             "amount": amt, "cur": c["cur"], "month": month})
    all_inv = invoices + credit_notes
    correct = totals(all_inv)
    ranked = top(correct, 14)

    # Duplicates: largest invoices of customers ranked #11 and #12 (double
    # counting pushes them up), plus a few random ones.
    dups = {}  # invoice id -> extra copies
    def largest(cid, exclude):
        cands = [i for i in invoices if i["cid"] == cid and i["id"] not in exclude]
        return max(cands, key=lambda i: to_eur(i["amount"], i["cur"], i["month"]))
    dups[largest(ranked[10], dups)["id"]] = 1
    dups[largest(ranked[10], dups)["id"]] = 2          # billed three times
    dups[largest(ranked[11], dups)["id"]] = 1
    pool = [i for i in invoices if i["id"] not in dups]
    for inv in rng.sample(pool, 4):
        dups[inv["id"]] = 1
    # One July invoice from the last days also re-exported in August's file.
    late_july = [i for i in invoices if i["month"] == "2026-07" and i["date"].day >= 29 and i["id"] not in dups]
    cross = rng.choice(late_july)
    dups[cross["id"]] = 1

    # Near-duplicates: same customer + amount, new id and a later date (new invoices).
    near = []
    nd_pool = [i for i in invoices if i["id"] not in dups and i["month"] != "2026-08"]
    for k, src in enumerate(rng.sample(nd_pool, 5), 1):
        nm = MONTHS[MONTHS.index(src["month"]) + 1]
        y, m = map(int, nm.split("-"))
        near.append({"id": f"INV-2026-{9000 + k:05d}", "date": dt.date(y, m, min(src["date"].day, 28)),
                     "cid": src["cid"], "amount": src["amount"], "cur": src["cur"], "month": nm})
    all_inv = invoices + credit_notes + near
    correct = totals(all_inv)
    top10 = top(correct)

    # Checks that the task discriminates.
    doubled = list(all_inv) + [i for i in invoices if i["id"] in dups for _ in range(dups[i["id"]])]
    ok = True
    ok &= top(totals(all_inv, fx=False)) != top10
    ok &= top(totals(doubled)) != top10
    ok &= top(totals(all_inv, skip_cn=True)) != top10
    vals = sorted(correct.values(), reverse=True)[:11]
    ok &= all(a - b > 50 for a, b in zip(vals, vals[1:]))
    return ok, dict(customers=customers, invoices=invoices, credit_notes=credit_notes, near=near,
                    dups=dups, cross=cross["id"], correct=correct, top10=top10, rng=rng)


def name_variant(rng, name):
    r = rng.random()
    if r < 0.70:
        return name
    if r < 0.78:
        return name.upper()
    if r < 0.85:
        return name.lower()
    if r < 0.92:
        return "  " + name + " "
    return name.replace(" ", "  ", 1)


def fmt_plain(a):
    return f"{a:.2f}"


def fmt_thousands(a):
    return f"{a:,.2f}"


def main():
    seed = 20260923
    while True:
        ok, s = attempt(seed)
        if ok:
            break
        seed += 1
    print(f"using seed {seed}")
    rng = s["rng"]
    by_id = {c["id"]: c for c in s["customers"]}
    everything = s["invoices"] + s["credit_notes"] + s["near"]

    # Rows per file, with duplicate copies placed right after the original,
    # except the cross-month one which reappears in August.
    per_file = {m: [] for m in MONTHS}
    for inv in sorted(everything, key=lambda i: (i["date"], i["id"])):
        per_file[inv["month"]].append(inv)
        extra = s["dups"].get(inv["id"], 0)
        if inv["id"] == s["cross"]:
            continue
        for _ in range(extra):
            per_file[inv["month"]].append(inv)
    cross_inv = next(i for i in s["invoices"] if i["id"] == s["cross"])
    per_file["2026-08"].insert(3, cross_inv)

    DATA.mkdir(parents=True, exist_ok=True)
    rendered = {}  # invoice id -> rendered name/currency, so duplicate copies are identical

    def render(inv):
        key = inv["id"]
        if key not in rendered:
            cur = inv["cur"].lower() if rng.random() < 0.04 else inv["cur"]
            rendered[key] = {"name": name_variant(rng, by_id[inv["cid"]]["name"]), "cur": cur}
        return rendered[key]

    # June: standard header, UTF-8 BOM
    with open(DATA / "2026-06.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["invoice_id", "invoice_date", "customer_id", "customer_name", "amount", "currency"])
        for inv in per_file["2026-06"]:
            r = render(inv)
            w.writerow([inv["id"], inv["date"].isoformat(), inv["cid"], r["name"], fmt_plain(inv["amount"]), r["cur"]])
    # July: renamed + reordered header
    with open(DATA / "2026-07.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Customer ID", "Customer", "Invoice No", "Date", "Currency", "Total"])
        for inv in per_file["2026-07"]:
            r = render(inv)
            w.writerow([inv["cid"], r["name"], inv["id"], inv["date"].isoformat(), r["cur"], fmt_plain(inv["amount"])])
    # August: standard header, thousands separators
    with open(DATA / "2026-08.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["invoice_id", "invoice_date", "customer_id", "customer_name", "amount", "currency"])
        for inv in per_file["2026-08"]:
            r = render(inv)
            w.writerow([inv["id"], inv["date"].isoformat(), inv["cid"], r["name"], fmt_thousands(inv["amount"]), r["cur"]])

    with open(DATA / "fx_rates.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["month", "currency", "rate_to_eur"])
        for m in MONTHS:
            for cur, rate in FX[m].items():
                w.writerow([m, cur, f"{rate:.4f}"])

    # Reference outputs
    REF.mkdir(parents=True, exist_ok=True)
    with open(REF / "top_customers.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "customer_id", "customer_name", "total_eur"])
        for k, cid in enumerate(s["top10"], 1):
            w.writerow([k, cid, by_id[cid]["name"], f"{s['correct'][cid]:.2f}"])
    all_by_id = {i["id"]: i for i in everything}
    with open(REF / "duplicate_invoices.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["invoice_id", "customer_id", "invoice_date", "currency", "amount", "times_billed"])
        for iid in sorted(s["dups"]):
            inv = all_by_id[iid]
            w.writerow([iid, inv["cid"], inv["date"].isoformat(), inv["cur"], fmt_plain(inv["amount"]),
                        s["dups"][iid] + 1])

    EXPECTED.mkdir(parents=True, exist_ok=True)
    for fn in ("top_customers.csv", "duplicate_invoices.csv"):
        shutil.copy(REF / fn, EXPECTED / fn)
    for m in MONTHS:
        print(m, len(per_file[m]), "rows")


if __name__ == "__main__":
    main()
