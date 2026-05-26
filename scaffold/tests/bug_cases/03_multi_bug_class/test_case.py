import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("buggy_03", Path(__file__).parent / "buggy.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
TaskQueue = _mod.TaskQueue


def test_push_priority_ordering():
    q = TaskQueue()
    q.push("low", priority=10)
    q.push("high", priority=1)
    q.push("medium", priority=5)
    assert q.peek()["name"] == "high"


def test_pop_returns_highest_urgency():
    q = TaskQueue()
    q.push("b", priority=5)
    q.push("a", priority=1)
    q.push("c", priority=9)
    first = q.pop()
    assert first["name"] == "a"
    second = q.pop()
    assert second["name"] == "b"


def test_pop_increments_processed():
    q = TaskQueue()
    q.push("t", priority=1)
    q.pop()
    assert q.processed() == 1


def test_pop_empty_returns_none():
    q = TaskQueue()
    assert q.pop() is None


def test_drain_returns_all_in_order():
    q = TaskQueue()
    q.push("c", priority=3)
    q.push("a", priority=1)
    q.push("b", priority=2)
    result = q.drain()
    assert [t["name"] for t in result] == ["a", "b", "c"]


def test_drain_clears_queue():
    q = TaskQueue()
    q.push("x", priority=1)
    q.drain()
    assert q.pending() == 0


def test_fifo_same_priority():
    q = TaskQueue()
    q.push("first", priority=5)
    q.push("second", priority=5)
    assert q.pop()["name"] == "first"
