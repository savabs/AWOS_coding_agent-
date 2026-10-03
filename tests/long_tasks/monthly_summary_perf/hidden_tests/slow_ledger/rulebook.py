"""The default categorisation rulebook.

The rulebook has grown over the years: named merchants, a large block of
local independent merchants (one rule each, maintained by the support team)
and a handful of generic rules.  Priorities: generic rules first, then named
merchants, then local merchants.
"""

from decimal import Decimal

from .rules import Categoriser, Rule

# (keyword as it appears in descriptions, category, home currency)
NAMED_MERCHANTS = (
    ("TESCO", "Groceries", "GBP"), ("SAINSBURYS", "Groceries", "GBP"),
    ("WAITROSE", "Groceries", "GBP"), ("ALDI", "Groceries", "GBP"),
    ("LIDL", "Groceries", "GBP"), ("MORRISONS", "Groceries", "GBP"),
    ("ASDA", "Groceries", "GBP"), ("CO-OP", "Groceries", "GBP"),
    ("PRET A MANGER", "Eating out", "GBP"), ("COSTA", "Eating out", "GBP"),
    ("STARBUCKS", "Eating out", "GBP"), ("GREGGS", "Eating out", "GBP"),
    ("NANDOS", "Eating out", "GBP"), ("WAGAMAMA", "Eating out", "GBP"),
    ("PIZZA EXPRESS", "Eating out", "GBP"), ("DELIVEROO", "Eating out", "GBP"),
    ("SHELL", "Fuel", "GBP"), ("ESSO", "Fuel", "GBP"), ("TEXACO", "Fuel", "GBP"),
    ("TFL TRAVEL", "Travel", "GBP"), ("TRAINLINE", "Travel", "GBP"),
    ("UBER", "Travel", "GBP"), ("EASYJET", "Travel", "GBP"),
    ("RYANAIR", "Travel", "EUR"), ("NETFLIX", "Subscriptions", "GBP"),
    ("SPOTIFY", "Subscriptions", "SEK"), ("DISNEY PLUS", "Subscriptions", "GBP"),
    ("AMAZON", "Shopping", "GBP"), ("ARGOS", "Shopping", "GBP"),
    ("JOHN LEWIS", "Shopping", "GBP"), ("IKEA", "Home", "SEK"),
    ("BOOTS", "Health", "GBP"), ("SUPERDRUG", "Health", "GBP"),
    ("THAMES WATER", "Bills", "GBP"), ("BRITISH GAS", "Bills", "GBP"),
    ("OCTOPUS ENERGY", "Bills", "GBP"), ("VODAFONE", "Bills", "GBP"),
    ("CARREFOUR", "Groceries", "EUR"), ("MONOPRIX", "Groceries", "EUR"),
    ("EDEKA", "Groceries", "EUR"), ("REWE", "Groceries", "EUR"),
    ("MIGROS", "Groceries", "CHF"), ("COOP PRONTO", "Groceries", "CHF"),
    ("SBB CFF", "Travel", "CHF"), ("ICA NARA", "Groceries", "SEK"),
    ("SL ACCESS", "Travel", "SEK"), ("WHOLE FOODS", "Groceries", "USD"),
    ("TARGET", "Shopping", "USD"), ("WALGREENS", "Health", "USD"),
    ("MTA NYCT", "Travel", "USD"), ("SHAKE SHACK", "Eating out", "USD"),
    ("FNAC", "Shopping", "EUR"), ("DB BAHN", "Travel", "EUR"),
)

LOCAL_PREFIXES = (
    "NORTH", "GREEN", "ROYAL", "CITY", "BRIGHT", "OLD", "GRAND", "STAR",
    "BLUE", "RIVER", "OAK", "HIGH", "SOUTH", "EAST", "WEST", "MILL",
    "BRIDGE", "MARKET", "STATION", "ABBEY", "CASTLE", "PARK",
)
LOCAL_NOUNS = (
    ("BAKERY", "Eating out"), ("GARAGE", "Car"), ("PHARMACY", "Health"),
    ("BISTRO", "Eating out"), ("HARDWARE", "Home"), ("FLORIST", "Gifts"),
    ("CINEMA", "Entertainment"), ("GYM", "Fitness"), ("BOOKS", "Shopping"),
    ("DELI", "Groceries"), ("TAVERN", "Eating out"), ("VETS", "Pets"),
)


def _slug(text):
    return text.lower().replace(" ", "-")


def generic_rules():
    return [
        Rule("refund", r"\bREFUND\b", "Refunds", 5, "credit"),
        Rule("salary", r"\b(SALARY|PAYROLL|WAGES)\b", "Income:Salary", 10, "credit"),
        Rule("interest", r"\bINTEREST\b", "Income:Interest", 10, "credit"),
        Rule("rent", r"\bRENT\b", "Housing", 15, "debit", Decimal("500")),
        Rule("savings", r"^TFR\b.*\bSAVINGS\b", "Savings", 18),
        Rule("transfer-in", r"^(TFR|FPI)\b", "Transfers", 20, "credit"),
        Rule("transfer-out", r"^(TFR|FPO)\b", "Transfers", 20, "debit"),
        Rule("atm", r"\bATM\b|CASH WITHDRAWAL", "Cash", 25, "debit"),
        Rule("council-tax", r"\bCOUNCIL\s+TAX\b", "Bills", 25, "debit"),
        Rule("amazon-large", r"\b(AMAZON|AMZN)\b", "Shopping:Large", 40, "debit", Decimal("250")),
        Rule("fuel-tesco", r"\bTESCO\s+(PETROL|FUEL)\b", "Fuel", 50, "debit"),
        Rule("fuel-sainsburys", r"\bSAINSBURYS\s+(PETROL|FUEL)\b", "Fuel", 50, "debit"),
        Rule("travel-bigspend", r"\b(EASYJET|RYANAIR|TRAINLINE)\b", "Travel:Holiday", 45, "debit", Decimal("300")),
    ]


def merchant_rules():
    rules = []
    for keyword, category, _currency in NAMED_MERCHANTS:
        pattern = r"\b" + r"\s+".join(keyword.split()) + r"\b"
        rules.append(Rule("m-" + _slug(keyword), pattern, category, 50))
    for prefix in LOCAL_PREFIXES:
        for noun, category in LOCAL_NOUNS:
            name = f"local-{_slug(prefix)}-{_slug(noun)}"
            rules.append(Rule(name, rf"\b{prefix}\s+{noun}\b", category, 100))
    return rules


def default_rules():
    return generic_rules() + merchant_rules()


def default_categoriser():
    return Categoriser(default_rules())
