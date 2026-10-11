"""Feature 2: duplicate detection across imports."""
from ft_helpers import NORTH, day, txn


def test_reimporting_same_file_adds_nothing(ft, write, ledger):
    path = write("n.csv", NORTH)
    first = ledger.add_transactions(ft.importers.parse_csv(path).transactions)
    second = ledger.add_transactions(ft.importers.parse_csv(path).transactions)
    assert (first.added, first.duplicates) == (3, 0)
    assert (second.added, second.duplicates) == (0, 3)
    assert len(ledger.transactions()) == 3


def test_identical_rows_within_one_file_are_kept(ft, write, ledger):
    text = ("Date,Description,Amount\n"
            "2026-03-10,COFFEE BAR,-3.50\n"
            "2026-03-10,COFFEE BAR,-3.50\n")
    path = write("n.csv", text)
    r1 = ledger.add_transactions(ft.importers.parse_csv(path).transactions)
    assert (r1.added, r1.duplicates) == (2, 0)
    r2 = ledger.add_transactions(ft.importers.parse_csv(path).transactions)
    assert (r2.added, r2.duplicates) == (0, 2)
    # a later statement showing three such coffees adds exactly one more
    three = write("n3.csv", text + "2026-03-10,COFFEE BAR,-3.50\n")
    r3 = ledger.add_transactions(ft.importers.parse_csv(three).transactions)
    assert (r3.added, r3.duplicates) == (1, 2)
    assert len(ledger.transactions()) == 3


def test_overlapping_statements(ft, write, ledger):
    feb_mar = write("a.csv", "Date,Description,Amount\n"
                             "2026-02-27,RENT,-900.00\n"
                             "2026-03-01,TESCO,-10.00\n")
    mar = write("b.csv", "Date,Description,Amount\n"
                         "2026-03-01,tesco,-10.00\n"      # same txn, different case
                         "2026-03-02,TESCO,-10.00\n")     # different day -> new
    ledger.add_transactions(ft.importers.parse_csv(feb_mar).transactions)
    res = ledger.add_transactions(ft.importers.parse_csv(mar).transactions)
    assert (res.added, res.duplicates) == (1, 1)
    assert [t.date for t in ledger.transactions()] == [day(2026, 2, 27), day(2026, 3, 1), day(2026, 3, 2)]


def test_fingerprint_includes_amount_and_account(ft, ledger):
    base = [txn(ft, day(2026, 3, 1), "SHOP", "-5.00")]
    ledger.add_transactions(base)
    res = ledger.add_transactions([
        txn(ft, day(2026, 3, 1), "SHOP", "-5.01"),
        txn(ft, day(2026, 3, 1), "SHOP", "-5.00", account="savings"),
        txn(ft, day(2026, 3, 1), "SHOP", "-5.00"),
    ])
    assert (res.added, res.duplicates) == (2, 1)
