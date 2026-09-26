"""Job 03: --output failures are clean user errors and never clobber the target."""

import os
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

import ordertool.export as export_mod  # noqa: E402
from ordertool.cli import main  # noqa: E402
from ordertool.models import LineItem, Order, OrderStatus  # noqa: E402
from ordertool.storage import OrderStore  # noqa: E402


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "orders.csv")
    store = OrderStore(path)
    for n, day in enumerate([1, 5, 9], start=1):
        store.add(Order(id=f"ORD-{n:04d}", customer=f"Customer {n}", email=f"c{n}@example.com",
                        created_at=datetime(2024, 3, day, 12, 0), status=OrderStatus.PAID,
                        items=[LineItem.from_spec(f"SKU{n}:1:{n}.00")]))
    return path


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("command", [["export"], ["export", "--line-items"], ["customers"]])
def test_missing_directory_is_clean_error(db, capsys, tmp_path, command):
    target = tmp_path / "no_such_dir" / "out.csv"
    code, out, err = run(capsys, "--db", db, *command, "--output", str(target))
    assert code == 1
    assert err.startswith("error: ")
    assert str(target) in err
    assert len(err.strip().splitlines()) == 1
    assert not target.exists()


@pytest.mark.parametrize("command", ["export", "customers"])
def test_directory_as_output_is_clean_error(db, capsys, tmp_path, command):
    target = tmp_path / "a_folder"
    target.mkdir()
    code, _, err = run(capsys, "--db", db, command, "--output", str(target))
    assert code == 1
    assert err.startswith("error: ")
    assert str(target) in err


def test_no_traceback_from_the_real_cli(db, tmp_path):
    env = dict(os.environ)
    env.pop("ORDERTOOL_DB", None)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "ordertool", "--db", db, "export", "--output",
         str(tmp_path / "missing" / "x.csv")],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("error: ")


def test_failure_mid_write_keeps_previous_file(db, capsys, tmp_path, monkeypatch):
    target = tmp_path / "exports" / "orders.csv"
    target.parent.mkdir()
    target.write_text("previous good export\n", encoding="utf-8")

    real = export_mod.order_to_row
    calls = {"n": 0}

    def flaky(order):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(28, "No space left on device")
        return real(order)

    monkeypatch.setattr(export_mod, "order_to_row", flaky)
    code, _, err = run(capsys, "--db", db, "export", "--output", str(target))
    assert calls["n"] == 2
    assert code == 1
    assert err.startswith("error: ")
    assert target.read_text(encoding="utf-8") == "previous good export\n"
    assert sorted(os.listdir(target.parent)) == ["orders.csv"]


def test_successful_output_unchanged(db, capsys, tmp_path):
    target = tmp_path / "ok.csv"
    code, out, err = run(capsys, "--db", db, "export", "--output", str(target))
    assert code == 0, err
    assert out == ""
    assert err.strip() == f"exported 3 rows to {target}"
    text = target.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "id,created_at,customer,email,status,items,total"
    assert text.endswith("ORD-0003,2024-03-09T12:00:00,Customer 3,c3@example.com,paid,1,3.00\n")
    assert sorted(os.listdir(tmp_path)) == ["ok.csv", "orders.csv"]


def test_overwrites_existing_file_on_success(db, capsys, tmp_path):
    target = tmp_path / "cust.csv"
    target.write_text("old\n", encoding="utf-8")
    code, _, err = run(capsys, "--db", db, "customers", "--output", str(target))
    assert code == 0, err
    assert target.read_text(encoding="utf-8").startswith("customer,email,orders,total_spent\n")
