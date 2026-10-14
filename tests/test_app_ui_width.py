"""The app's line editor counts display columns, not characters (CJK / emoji)."""
from __future__ import annotations

import io
import re

import pytest

from scaffold.agent.app.ui import Term, char_width, display_width, edit_window, fit_width


@pytest.mark.parametrize("ch, want", [
    ("a", 1), (" ", 1), ("é", 1),
    ("中", 2), ("あ", 2), ("한", 2), ("Ａ", 2),     # CJK, kana, hangul, fullwidth
    ("😀", 2), ("🚀", 2),                          # emoji
    ("́", 0), ("‍", 0), ("️", 0),   # combining acute, ZWJ, VS16
])
def test_char_width(ch, want):
    assert char_width(ch) == want


def test_display_width_mixed():
    assert display_width("ab中文😀") == 2 + 4 + 2
    assert display_width("é") == 1


def _term(cols: int = 40) -> Term:
    t = Term(out=io.StringIO(), inp=io.StringIO(), force_plain=True)
    t.tty = True                     # exercise the redraw path without a real terminal
    t.width = lambda: cols
    return t


def _redraw(t: Term, text: str, cur: int, prompt: str = "> ") -> tuple[str, int]:
    t.out.seek(0)
    t.out.truncate()
    t.prompt, t._buf, t._cur = prompt, list(text), cur
    t._redraw()
    out = t.out.getvalue()
    m = re.search(r"\x1b\[(\d+)D$", out)
    shown = out.split(prompt, 1)[1]
    shown = re.sub(r"\x1b\[\d+D$", "", shown)
    return shown, int(m.group(1)) if m else 0


def test_cursor_moves_back_by_columns_for_cjk():
    t = _term()
    shown, back = _redraw(t, "中文abc", cur=1)      # cursor before 文
    assert shown == "中文abc"
    assert back == 2 + 3                            # 文 is two columns, abc three
    _, back = _redraw(t, "中文abc", cur=2)
    assert back == 3


def test_cursor_moves_back_by_columns_for_emoji():
    t = _term()
    _, back = _redraw(t, "fix 🚀 bug", cur=4)       # cursor before the rocket
    assert back == 2 + 4


def test_long_wide_line_scrolls_by_columns_and_keeps_cursor_visible():
    t = _term(cols=13)                              # avail = max(10, 13 - 2 - 1) = 10
    text = "中" * 12                                 # 24 columns
    shown, back = _redraw(t, text, cur=len(text))
    assert display_width(shown) <= 10 and back == 0
    shown, back = _redraw(t, text, cur=6)
    assert display_width(shown) <= 10
    assert back <= display_width(shown) - 2         # the char under the cursor is on screen


@pytest.mark.parametrize("cur", range(0, 9))
def test_edit_window_invariants(cur):
    chars = list("a中b😀c文d")
    for avail in (3, 4, 5, 7, 20):
        start, end, back = edit_window(chars, min(cur, len(chars)), avail)
        c = min(cur, len(chars))
        assert start <= c <= end
        assert display_width("".join(chars[start:end])) <= avail
        assert back == display_width("".join(chars[c:end]))


def test_fit_width_cuts_on_columns():
    assert fit_width("中文中文", 5) == "中文…"
    assert fit_width("abc", 5) == "abc"
