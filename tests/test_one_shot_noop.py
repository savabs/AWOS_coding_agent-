"""No-op SEARCH/REPLACE blocks must not count as an applied edit.

real_cachetools_423: the one-shot reply's only block had REPLACE == SEARCH,
EditFileTool accepted X -> X, the file landed in `applied`, the already-green
tests ran and the job was "solved in 1 call" with no change.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scaffold.agent.one_shot import (  # noqa: E402
    NO_NET_CHANGE_REASON, NO_OP_REASON, EditBlock, apply_blocks, is_no_op, run_one_shot,
)

CALC = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"


def _project() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("from .calc import add\n", encoding="utf-8")
    (root / "pkg" / "calc.py").write_text(CALC, encoding="utf-8")
    return root


class _SeqClient:
    """OpenAI-shaped; replies in order."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.replies.pop(0) if self.replies else ""
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100),
        )


def _block(search: str, replace: str, path: str = "pkg/calc.py") -> str:
    return f"{path}\n<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE\n"


NOOP = _block("def add(a, b):\n    return a - b", "def add(a, b):\n    return a - b")
FIX = _block("def add(a, b):\n    return a - b", "def add(a, b):\n    return a + b")
UNFIX = _block("def add(a, b):\n    return a + b", "def add(a, b):\n    return a - b")


def test_identical_block_fails_with_no_op_reason_and_file_untouched():
    root = _project()
    b = EditBlock("pkg/calc.py", "def add(a, b):\n    return a - b",
                  "def add(a, b):\n    return a - b")
    applied, failed = apply_blocks(str(root), [b])
    assert applied == []
    assert failed == [{"path": "pkg/calc.py", "reason": NO_OP_REASON,
                       "search": b.search, "index": 0}]
    assert (root / "pkg" / "calc.py").read_text() == CALC


def test_whitespace_only_no_op_caught_but_indent_change_is_not():
    s = "def add(a, b):\n    return a - b"
    assert is_no_op(EditBlock("x.py", s, "def add(a,  b):   \n    return a\t- b\n\n"))
    assert is_no_op(EditBlock("x.py", s, "\n" + s))
    assert not is_no_op(EditBlock("x.py", s, "def add(a, b):\n        return a - b"))
    assert not is_no_op(EditBlock("x.py", s, "def add(a, b):\n    return a + b"))
    assert not is_no_op(EditBlock("x.py", "", ""))  # create/append is never a no-op


def test_mixed_real_and_no_op_blocks():
    root = _project()
    blocks = [
        EditBlock("pkg/calc.py", "    return a - b", "    return a + b"),
        EditBlock("pkg/calc.py", "def mul(a, b):\n    return a * b",
                  "def mul(a, b):\n    return a  *  b"),
    ]
    applied, failed = apply_blocks(str(root), blocks)
    assert applied == ["pkg/calc.py"]
    assert len(failed) == 1
    assert failed[0]["reason"] == NO_OP_REASON and failed[0]["index"] == 1
    assert "return a + b" in (root / "pkg" / "calc.py").read_text()


def test_no_op_reply_and_no_op_repair_leave_nothing_applied():
    root = _project()
    client = _SeqClient(NOOP, NOOP)
    res = run_one_shot(client, "m", str(root), "fix add", None)
    assert len(client.calls) == 2 and res.repair_calls == 1  # no-op went to repair
    assert "no-op edit" in client.calls[1]["messages"][0]["content"]
    assert NO_OP_REASON in client.calls[1]["messages"][1]["content"]
    assert res.applied == []
    assert res.failed and all(NO_OP_REASON in f["reason"] for f in res.failed)
    assert (root / "pkg" / "calc.py").read_text() == CALC


def test_no_op_repaired_into_real_change_applies():
    root = _project()
    res = run_one_shot(_SeqClient(NOOP, FIX), "m", str(root), "fix add", None)
    assert res.applied == ["pkg/calc.py"] and res.failed == []
    assert "return a + b" in (root / "pkg" / "calc.py").read_text()


def test_block_and_reversing_block_is_no_net_change():
    root = _project()
    client = _SeqClient(FIX + "\n" + UNFIX)
    res = run_one_shot(client, "m", str(root), "fix add", None)
    assert len(client.calls) == 1
    assert res.applied == []
    assert any(f["reason"] == NO_NET_CHANGE_REASON for f in res.failed)
    assert (root / "pkg" / "calc.py").read_text() == CALC


def test_normal_edit_unchanged():
    root = _project()
    client = _SeqClient(FIX)
    res = run_one_shot(client, "m", str(root), "fix add", None)
    assert len(client.calls) == 1
    assert res.applied == ["pkg/calc.py"] and res.failed == []
    assert "return a + b" in (root / "pkg" / "calc.py").read_text()


def test_new_file_counts_as_change():
    root = _project()
    client = _SeqClient(_block("", "X = 1", path="pkg/new.py"))
    res = run_one_shot(client, "m", str(root), "add new", None)
    assert res.applied == ["pkg/new.py"]
    assert (root / "pkg" / "new.py").read_text() == "X = 1\n"
