"""data.py — deterministic data pools for factory apps (all fictional)."""
from __future__ import annotations

import random
from typing import List, Sequence

FIRST = ["Dana", "Ravi", "Mei", "Lucas", "Amara", "Jonas", "Priya", "Tomás", "Hana", "Owen", "Zara", "Felix",
         "Ines", "Kofi", "Lena", "Marco", "Nadia", "Oscar", "Ruth", "Sami", "Tara", "Victor", "Wen", "Yusuf"]
LAST = ["Lee", "Patel", "Chen", "Moreau", "Okafor", "Berg", "Nair", "García", "Sato", "Walsh", "Haddad", "Novak",
        "Silva", "Mensah", "Fischer", "Rossi", "Karimi", "Lindqvist", "Adler", "Rahman", "Quinn", "Dubois", "Ito", "Kaya"]
COMPANIES = ["Acme Corp", "Brightline Ltd", "Copperleaf", "Dunmore & Co", "Evergreen Labs", "Foxglove Media",
             "Granite Works", "Harbor Freight Co", "Ironbark Systems", "Juniper Health", "Keystone Foods",
             "Lumen Analytics", "Mistral Textiles", "Northwind Traders", "Orchid Design", "Pinecrest Bank"]
CITIES = ["Lisbon", "Pune", "Osaka", "Leeds", "Accra", "Bergen", "Austin", "Lyon", "Porto", "Nairobi", "Kraków", "Cebu"]
DEPARTMENTS = ["Engineering", "Finance", "Sales", "Support", "Marketing", "Operations", "Legal", "People"]
PRODUCTS = [("USB-C cable", 12.99), ("Wireless mouse", 24.50), ("Laptop stand", 39.00), ("Desk lamp", 31.25),
            ("Notebook set", 8.75), ("Webcam HD", 54.00), ("Headphones", 89.99), ("Phone charger", 19.95),
            ("Keyboard", 49.00), ("Monitor arm", 72.40), ("Coffee mug", 11.00), ("Backpack", 64.90)]
TICKET_SUBJECTS = ["Cannot reset password", "Invoice shows wrong VAT", "Export to CSV fails", "App crashes on login",
                   "Refund not received", "Need more seats", "Sync stuck at 99%", "Typo on pricing page",
                   "Two-factor code never arrives", "Calendar invites duplicated", "Slow dashboard", "Change billing address"]
MAIL_SUBJECTS = ["Quarterly report draft", "Lunch on Friday?", "Contract renewal", "Updated floor plan",
                 "Your order has shipped", "Team offsite agenda", "Invoice overdue reminder", "Welcome aboard",
                 "Design review notes", "Travel booking confirmed", "Budget questions", "Parking permits"]
FILE_NAMES = ["budget_2026.xlsx", "roadmap.pdf", "team_photo.jpg", "minutes_march.docx", "logo_final.svg",
              "contract_signed.pdf", "notes.txt", "pitch_deck.key", "inventory.csv", "handbook.pdf",
              "invoice_template.docx", "sprint_plan.md"]
STATUSES = {"invoices": ["Draft", "Sent", "Paid", "Overdue"], "tickets": ["Open", "Pending", "Solved"],
            "crm": ["Lead", "Qualified", "Customer", "Churned"], "hr": ["Active", "On leave", "Probation"]}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def person(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def people(rng: random.Random, n: int) -> List[str]:
    out: List[str] = []
    while len(out) < n:
        p = person(rng)
        if p not in out and p.split()[0] not in [x.split()[0] for x in out]:
            out.append(p)
    return out


def email_of(name: str, domain: str = "example.com") -> str:
    a, b = name.lower().split()[:2]
    tr = str.maketrans("áàâãéèêíóôõúçñ", "aaaaeeeioooucn")
    return f"{a.translate(tr)}.{b.translate(tr)}@{domain}"


def phone(rng: random.Random) -> str:
    return f"({rng.randint(201, 989)}) {rng.randint(200, 989)}-{rng.randint(1000, 9999)}"


def date(rng: random.Random, year: int = 2026) -> str:
    return f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


def money(x: float) -> str:
    return f"${x:,.2f}"


def money_alts(x: float) -> List[str]:
    return [f"${x:,.2f}", f"{x:,.2f}", f"{x:.2f}"]


def pick(rng: random.Random, pool: Sequence, k: int) -> list:
    return rng.sample(list(pool), k)
