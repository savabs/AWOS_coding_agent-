# fintrack — personal finance tracker (SPEC)

`fintrack` is a small command-line tool that imports bank statement CSVs into a
local SQLite database, categorizes transactions with rules, and produces monthly
reports, budget alerts and exports.

This file is the **complete specification**. Everything below that names a
module, class, function, field, command, exit code or output format is part of
the public interface and must be implemented exactly as written. Anything not
specified (internal helpers, the SQLite schema, extra modules) is up to you.

Constraints:

- Python 3.9+, **standard library only** (`sqlite3`, `csv`, `argparse`,
  `decimal`, `datetime`, `json`, `re`, ...). No third-party packages.
- Money is always `decimal.Decimal`, never `float`, quantized to 2 decimal
  places (`Decimal("0.01")`, `ROUND_HALF_UP`). Money stored in the database must
  come back exactly (e.g. `0.10 + 0.20 == 0.30`).
- Dates are plain `datetime.date` values (no time, no timezone).
- Run the tests with `python -m pytest`.

---

## Package layout

```
fintrack/
    __init__.py     # exposes __version__ = "1.0.0"
    __main__.py     # `python -m fintrack` -> fintrack.cli.main()
    errors.py
    models.py
    importers.py
    storage.py
    rules.py
    reports.py
    budgets.py
    export.py
    cli.py
```

---

## 1. `fintrack.errors`

```python
class FintrackError(Exception): ...          # base class for all fintrack errors
class ParseError(FintrackError): ...         # a whole file cannot be imported
class RulesError(FintrackError): ...         # the rules file is invalid
class ValidationError(FintrackError): ...    # a bad argument (month, amount, date range, format, ...)
```

---

## 2. `fintrack.models`

```python
@dataclass
class Transaction:
    date: datetime.date
    description: str
    amount: Decimal            # negative = money out, positive = money in
    account: str = "default"
    category: Optional[str] = None   # None = not categorized yet
    id: Optional[int] = None         # set by the Ledger once stored
```

---

## 3. Importing bank CSVs — `fintrack.importers`

Two bank formats are supported. Files are UTF-8 and may start with a BOM.

### Format `"northbank"`

- Comma-separated. Header (exactly these columns, in this order; compare
  case-insensitively after stripping whitespace): `Date,Description,Amount`
- `Date`: `YYYY-MM-DD`, optionally followed by a space and a time
  (`HH:MM` or `HH:MM:SS`) which is ignored. `2026-03-31 23:59:59` is March 31.
- `Amount`: signed decimal with a `.` decimal point; negative = money out.
  May carry thousands separators inside quotes (`"-1,234.56"`) and an optional
  leading `+`.

Example:

```
Date,Description,Amount
2026-03-01,TESCO STORES 2231,-45.20
2026-03-02,ACME CORP SALARY,"2,500.00"
```

### Format `"southbank"`

- Semicolon-separated. Header (case-insensitive, whitespace-stripped):
  `Booking Date;Payee;Memo;Debit;Credit`
- `Booking Date`: `DD.MM.YYYY`.
- `Debit` = money out, `Credit` = money in. Both are **unsigned** numbers in
  European notation: `,` is the decimal separator and `.` the thousands
  separator (`1.234,56`). Exactly one of the two is non-empty in each row.
  `amount = Credit` or `amount = -Debit`.
- `description = Payee`, or `Payee + " - " + Memo` when Memo is non-empty.

Example:

```
Booking Date;Payee;Memo;Debit;Credit
01.03.2026;Tesco;Weekly shop;45,20;
02.03.2026;ACME Corp;Salary March;;2.500,00
```

### Rules common to both formats

- Descriptions are stripped and runs of internal whitespace collapsed to a
  single space.
- Completely empty lines are ignored silently.
- A **malformed row** is skipped and reported, without aborting the import.
  Malformed means any of: wrong number of columns, empty description,
  unparseable or impossible date (`2026-02-30`), unparseable amount, (southbank)
  both or neither of Debit/Credit filled, or a negative Debit/Credit value.
- Row errors carry the **1-based physical line number** in the file (the
  header is line 1).
- A file whose header matches neither format, or that is empty, or that does
  not exist, raises `ParseError` (nothing is imported from it).

### API

```python
SUPPORTED_FORMATS = ("northbank", "southbank")

@dataclass
class RowError:
    line: int        # 1-based line number in the file
    message: str     # human-readable reason

@dataclass
class ImportResult:
    format: str                       # "northbank" or "southbank"
    transactions: list[Transaction]   # parsed rows, in file order, id=None, category=None
    errors: list[RowError]            # malformed rows, in file order

def detect_format(header_line: str) -> str
    # Return "northbank" or "southbank" for the given first line of a file.
    # Raise ParseError if it matches neither.

def parse_csv(path, fmt: Optional[str] = None, account: str = "default") -> ImportResult
    # Parse a bank file. fmt=None auto-detects from the header. Every
    # transaction gets the given account. An fmt not in SUPPORTED_FORMATS
    # raises ValidationError; a header that does not match fmt raises ParseError.
```

---

## 4. Persistence and duplicate detection — `fintrack.storage`

```python
@dataclass
class AddResult:
    added: int
    duplicates: int

class Ledger:
    def __init__(self, path)                 # path to an SQLite file; created if missing
    def close(self) -> None
    def __enter__(self) / __exit__(...)      # context manager; closes on exit

    def add_transactions(self, txns) -> AddResult
    def transactions(self, start=None, end=None, category=None, account=None) -> list[Transaction]
    def set_category(self, txn_id: int, category: Optional[str]) -> None   # KeyError for unknown id
    def set_budget(self, category: str, amount) -> None
    def get_budgets(self) -> dict[str, Decimal]
    def remove_budget(self, category: str) -> bool    # True if a budget was removed
```

- Data written through one `Ledger` is visible after closing it and opening a
  new `Ledger` on the same path.
- `transactions()` returns stored transactions with `id` set, ordered by date
  and then by insertion order. `start`/`end` are inclusive `date` bounds;
  `category` is an exact match, except that `category="Uncategorized"` also
  matches transactions whose category is `None`; `account` is an exact match.
- `set_budget` accepts a `Decimal`, `int` or numeric string; the amount must be
  > 0, otherwise `ValidationError`. Setting a budget again replaces it.

### Duplicate detection

Re-importing a statement, or importing two overlapping statements, must not
create duplicates — but genuinely repeated transactions (two identical coffees
on the same day) must be kept.

The **fingerprint** of a transaction is
`(date, amount, description.casefold(), account)`.

When `add_transactions` receives a batch in which a fingerprint occurs `k`
times, and the ledger already holds `m` transactions with that fingerprint,
it inserts `max(0, k - m)` of them and counts the rest as duplicates.
`added + duplicates == len(batch)`.

---

## 5. Rule-based categorization — `fintrack.rules`

A rules file is JSON:

```json
{
  "default": "Other",
  "rules": [
    {"category": "Groceries", "contains": "tesco", "priority": 10},
    {"category": "Transport", "regex": "^(uber|lyft)\\b", "priority": 5},
    {"category": "Salary",    "contains": "salary", "direction": "in"}
  ]
}
```

- Each rule has a non-empty string `category` and **exactly one** of
  `contains` (case-insensitive substring of the description) or `regex`
  (Python `re.search` with `re.IGNORECASE` on the description).
- `priority`: integer, default 0. Among all matching rules the one with the
  **highest priority** wins; on a tie the rule that appears **first** in the
  file wins.
- `direction` (optional): `"out"` matches only amounts < 0, `"in"` only
  amounts > 0.
- `default`: category for transactions no rule matches; defaults to
  `"Uncategorized"` when omitted.
- An invalid file raises `RulesError`: unreadable file, invalid JSON, top-level
  not an object, `rules` not a list, missing/empty category, neither or both of
  `contains`/`regex`, an invalid regex, a non-integer priority, or a bad
  direction.

```python
@dataclass
class Rule:
    category: str
    pattern: str
    kind: str              # "contains" or "regex"
    priority: int = 0
    direction: Optional[str] = None
    def matches(self, txn: Transaction) -> bool

class RuleSet:
    rules: list[Rule]
    default: str
    @classmethod
    def from_dict(cls, data: dict) -> "RuleSet"     # same validation as load_rules
    def categorize(self, txn: Transaction) -> str

def load_rules(path) -> RuleSet

def categorize_ledger(ledger: Ledger, ruleset: RuleSet, recategorize: bool = False) -> int
    # Assign a category to every uncategorized transaction (or to every
    # transaction when recategorize=True) and return how many were assigned.
```

---

## 6. Monthly reports — `fintrack.reports`

```python
@dataclass
class CategoryRow:
    category: str
    count: int
    total: Decimal

@dataclass
class MonthlyReport:
    year: int
    month: int
    rows: list[CategoryRow]
    income: Decimal      # sum of positive amounts in the month
    expenses: Decimal    # sum of negative amounts in the month (<= 0)
    net: Decimal         # income + expenses

def monthly_report(ledger, year: int, month: int) -> MonthlyReport
def render_text(report: MonthlyReport) -> str
def render_json(report: MonthlyReport) -> str
```

- One row per category that has transactions in that calendar month;
  transactions with category `None` are reported as `"Uncategorized"`.
- Rows are ordered by `total` ascending (biggest spending first); ties by
  category name ascending.
- A month with no transactions has no rows and all three sums `Decimal("0.00")`.
- An invalid month (outside 1..12) raises `ValidationError`.
- Amounts render as plain 2-decimal strings: `-123.45`, `2500.00`, `0.00`
  (never `-0.00`).

`render_text` output (lines joined with `"\n"`, ending with a final `"\n"`):

```
Report for 2026-03
Category             Count        Total
Groceries                3      -123.45
Salary                   1      2500.00
Income: 2500.00
Expenses: -123.45
Net: 2376.55
```

The header and each row are `f"{category:<20} {count:>5} {total:>12}"` (the
header uses the words `Category`, `Count`, `Total`). For an empty month the
output is exactly:

```
Report for 2026-04
No transactions.
```

`render_json` returns a JSON object:

```json
{"month": "2026-03",
 "categories": [{"category": "Groceries", "count": 3, "total": "-123.45"}, ...],
 "income": "2500.00", "expenses": "-123.45", "net": "2376.55"}
```

(amounts are strings; `categories` in the same order as `rows`).

---

## 7. Budgets — `fintrack.budgets`

Budgets are monthly spending limits per category, stored in the ledger
(`Ledger.set_budget`).

```python
@dataclass
class BudgetStatus:
    category: str
    budget: Decimal
    spent: Decimal       # money spent in that category that month (>= 0)
    remaining: Decimal   # budget - spent (negative when over)
    status: str          # "ok", "near" or "over"

def check_budgets(ledger, year: int, month: int,
                  near_threshold: Decimal = Decimal("0.8")) -> list[BudgetStatus]
```

- `spent = max(0, -(sum of the category's amounts in that month))` — refunds
  (positive amounts) in a category reduce its spending.
- `status` is `"over"` when `spent > budget`, else `"near"` when
  `spent >= near_threshold * budget`, else `"ok"`. Spending exactly the budget
  is `"near"`, not `"over"`.
- One status per budget (categories without transactions have `spent = 0.00`),
  ordered by category name. No budgets -> empty list.
- An invalid month raises `ValidationError`.

---

## 8. Export — `fintrack.export`

```python
EXPORT_COLUMNS = ["date", "description", "amount", "category", "account"]

def export_transactions(ledger, out, fmt: str = "csv",
                        start=None, end=None, category=None) -> int
    # Write matching transactions to the text stream `out`; return how many.
```

- Filters behave like `Ledger.transactions` (inclusive dates; `"Uncategorized"`
  matches uncategorized). Order: by date, then insertion order.
- `csv`: header row `date,description,amount,category,account`, `\n` line
  endings, date ISO `YYYY-MM-DD`, amount as a 2-decimal string, empty category
  for uncategorized.
- `json`: a JSON array of objects with the same keys; amount as a 2-decimal
  string, category `null` for uncategorized.
- An unknown `fmt`, or `start > end`, raises `ValidationError`.

---

## 9. Command line — `python -m fintrack`

```
python -m fintrack [--db PATH] COMMAND ...
```

`fintrack.cli.main(argv: Optional[list[str]] = None) -> int` implements the CLI
and returns the exit code; `fintrack/__main__.py` calls
`sys.exit(main())`. The database is `--db PATH`, else the `FINTRACK_DB`
environment variable, else `fintrack.db` in the current directory.

| Command | Behaviour / stdout |
|---|---|
| `import FILE [FILE ...] [--format northbank\|southbank] [--account NAME]` | For each file: `Imported FILE: A added, D duplicates, E errors`. Each malformed row goes to **stderr** as `FILE:LINE: MESSAGE`. FILE is printed as given on the command line. |
| `categorize --rules RULES.json [--all]` | `Categorized N transactions` (`--all` = recategorize everything). |
| `report --month YYYY-MM [--json]` | `render_text` (or `render_json` with `--json`) of that month. |
| `budget set CATEGORY AMOUNT` | `Budget for CATEGORY set to AMOUNT` (AMOUNT with 2 decimals). |
| `budget list` | One line per budget, ordered by category: `CATEGORY: AMOUNT`; or `No budgets set.` |
| `budget check --month YYYY-MM` | One line per budget, ordered by category: `CATEGORY: spent SPENT of BUDGET (STATUS)` with STATUS = `OK`, `NEAR` or `OVER`; or `No budgets set.` |
| `export --format csv\|json [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--category NAME] [--output FILE]` | Export to stdout, or to FILE (then stdout says `Exported N transactions to FILE`). |

`import` is all-or-nothing at file level: all files are parsed first, and if
any file raises `ParseError` (missing file, unknown header, ...) nothing at all
is imported.

### Exit codes and errors

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | error: bad input such as a missing/unrecognized file, invalid rules file, bad month/date/amount/range |
| 2 | command-line usage error (argparse's default) |
| 3 | `import` finished but skipped one or more malformed rows |

For exit code 1, print a one-line message starting with `error: ` to
**stderr** — never a Python traceback.
