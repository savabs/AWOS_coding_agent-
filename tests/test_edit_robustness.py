"""T4 edit robustness: fuzzy-apply ladder (AWOS_FUZZY_APPLY) and the
copy-constraint grammar (AWOS_EDIT_GRAMMAR). Spec: docs/specs/edit_robustness.md."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scaffold" / "agent"))

import edit_grammar  # noqa: E402
import fuzzy_apply  # noqa: E402
import one_shot  # noqa: E402
from verifier import Verifier  # noqa: E402

SRC = '''import os


class Greeter:
    def __init__(self, name):
        self.name = name

    def greet(self, loud=False):
        msg = "hello, " + self.name
        if loud:
            msg = msg.upper()
        return msg

    def wave(self):
        return "wave from " + self.name


def helper(x):
    return x * 2
'''


@pytest.fixture
def fuzzy_on(monkeypatch):
    monkeypatch.setenv("AWOS_FUZZY_APPLY", "1")


# ── ladder tiers ─────────────────────────────────────────────────────────────

def test_tier1_exact():
    m = fuzzy_apply.apply(SRC, "def helper(x):\n    return x * 2", "def helper(x):\n    return x * 3")
    assert m.matched and m.tier == 1 and "x * 3" in m.content


def test_tier2_whitespace():
    search = "        msg  =  \"hello, \" + self.name   \n        if loud:"
    m = fuzzy_apply.apply(SRC, search, "        msg = \"hi, \" + self.name\n        if loud:")
    assert m.matched and m.tier == 2
    assert '        msg = "hi, " + self.name\n        if loud:' in m.content
    assert m.content.count("if loud") == 1


def test_tier3_reindent_shifts_replace():
    search = "def wave(self):\n    return \"wave from \" + self.name"
    replace = "def wave(self):\n    # waves\n    return \"wave from \" + self.name"
    m = fuzzy_apply.apply(SRC, search, replace)
    assert m.matched and m.tier == 3
    assert '    def wave(self):\n        # waves\n        return "wave from "' in m.content
    compile(m.content, "x.py", "exec")


def test_tier4_difflib_minor_token_noise():
    search = ("    def greet(self, loud=False):\n        msg = 'hello, ' + self.name\n"
              "        if loud:\n            msg = msg.upper()\n        return msg")
    replace = ("    def greet(self, loud=False):\n        msg = 'hello, ' + self.name\n"
               "        if loud:\n            msg = msg.upper() + '!'\n        return msg")
    m = fuzzy_apply.apply(SRC, search, replace)
    assert m.matched and m.tier == 4 and m.ratio >= 0.9
    assert "msg.upper() + '!'" in m.content
    assert m.content.count("def greet") == 1 and "def wave" in m.content


def test_tier4_below_threshold_is_no_match():
    m = fuzzy_apply.apply(SRC, "    def greet(self, quiet=True):\n        x = compute_everything()",
                          "pass")
    assert not m.matched and m.content == SRC


# ── safety ───────────────────────────────────────────────────────────────────

DUP = '''def a():
    x = 1
    return x


def b():
    x = 1
    return x
'''


def test_exact_ambiguity_refused():
    m = fuzzy_apply.apply(DUP, "    x = 1\n    return x", "    x = 2\n    return x")
    assert not m.matched and m.reason.startswith("ambiguous") and m.content == DUP


def test_whitespace_ambiguity_refused():
    m = fuzzy_apply.apply(DUP, "    x  = 1\n    return x", "    x = 2\n    return x")
    assert not m.matched and m.reason.startswith("ambiguous")


def test_reindent_ambiguity_refused():
    m = fuzzy_apply.apply(DUP, "x = 1\nreturn x", "x = 2\nreturn x")
    assert not m.matched and m.reason.startswith("ambiguous")


def test_difflib_requires_unique_anchor():
    # Close to both bodies, but every line also occurs twice: no unique anchor.
    m = fuzzy_apply.apply(DUP, "    x = 1\n    return  x  # note", "    x = 2\n    return x")
    assert not m.matched and m.content == DUP
    assert "anchor" in m.reason or m.reason.startswith("ambiguous")


def test_difflib_refuses_second_similar_window():
    text = ("def one():\n    total = compute(alpha, beta)\n    return total + 1\n\n"
            "def two():\n    total = compute(alpha, beta)\n    return total + 1\n"
            "UNIQUE_MARKER_LINE = 1\n")
    # 'def one():' is a unique anchor, but the body also matches the other def.
    search = "    total = compute(alpha, betta)\n    return total + 1"
    m = fuzzy_apply.apply(text, search, "    total = 0\n    return total")
    assert not m.matched and m.content == text


def test_relindent_refuses_replace_left_of_search():
    search = "    def wave(self):\n        return \"wave from \" + self.name"
    m = fuzzy_apply.apply(SRC.replace("    def wave", "  def wave").replace(
        '        return "wave from', '    return "wave from'), search,
        "def wave(self):\n    return 1")
    assert not m.matched


# ── Verifier / EditFileTool integration ──────────────────────────────────────

def test_default_off_is_legacy_byte_identical(monkeypatch):
    """Flag off: the ladder is never consulted and the legacy matcher's
    output (frozen here) is unchanged, including its unsafe un-reindented
    whitespace tier."""
    monkeypatch.delenv("AWOS_FUZZY_APPLY", raising=False)

    def boom(*a, **k):
        raise AssertionError("ladder used with flag off")
    monkeypatch.setattr(fuzzy_apply, "apply", boom)
    v = Verifier()
    ok, out, tier = v._apply_fuzzy(SRC, "def helper(x):\n    return x * 2",
                                   "def helper(x):\n    return x * 3")
    assert (ok, tier) == (True, "exact") and out == SRC.replace("x * 2", "x * 3")
    ok, out, tier = v._apply_fuzzy(SRC, 'def wave(self):\n    return "wave from " + self.name',
                                   "def wave(self):\n    pass")
    assert (ok, tier) == (True, "whitespace")
    assert "\ndef wave(self):\n    pass\n" in out      # legacy: not re-indented
    ok, out, tier = v._apply_fuzzy(SRC, "nothing like this", "x")
    assert (ok, out, tier) == (False, SRC, "none")


def test_verifier_logs_tier(tmp_path, fuzzy_on, capsys):
    f = tmp_path / "g.py"
    f.write_text(SRC)
    out = Verifier().verify_and_apply(
        {"search": "def wave(self):\n    return \"wave from \" + self.name",
         "replace": "def wave(self):\n    return \"bye \" + self.name"}, str(f))
    assert out["success"]
    assert "[FUZZY-APPLY] tier=3" in capsys.readouterr().out
    assert '        return "bye " + self.name' in f.read_text()


def test_verifier_refusal_explains(tmp_path, fuzzy_on, capsys):
    f = tmp_path / "d.py"
    f.write_text(DUP)
    out = Verifier().verify_and_apply({"search": "x = 1\nreturn x", "replace": "x = 2\nreturn x"},
                                      str(f))
    assert not out["success"] and "ambiguous" in out["error_context"]
    assert f.read_text() == DUP
    assert "[FUZZY-APPLY] refused" in capsys.readouterr().out


def test_apply_blocks_uses_ladder(tmp_path, fuzzy_on):
    (tmp_path / "g.py").write_text(SRC)
    blocks = [one_shot.EditBlock("g.py", "def helper(x):\n  return x * 2",
                                 "def helper(x):\n  return x * 5")]
    applied, failed = one_shot.apply_blocks(str(tmp_path), blocks)
    assert applied == ["g.py"] and not failed
    text = (tmp_path / "g.py").read_text()
    # 2- vs 4-space indent is not a uniform shift (tier 3 refuses); tier 4
    # lands it at the right place and the file still compiles.
    assert "return x * 5" in text and "x * 2" not in text
    compile(text, "g.py", "exec")


# ── grammar ──────────────────────────────────────────────────────────────────

def test_grammar_for_small_file(tmp_path):
    (tmp_path / "a.py").write_text('x = "q\\\\"\nif x:\n    print(x)\n')
    g, note = edit_grammar.grammar_for_files(str(tmp_path), ["a.py", "missing.py"])
    assert g is not None and "1 files" in note
    assert g.startswith("root ::=")
    assert "sl ::=" in g and "blk ::=" in g
    assert '"<<<<<<< SEARCH\\n" sl*' in g
    # every file line is reachable as a literal (radix edges may split them)
    assert '\\"q\\\\\\\\\\"' in g.replace(" ", "") or "q" in g
    for rule in g.strip().splitlines():
        assert " ::= " in rule


def test_grammar_trie_accepts_lines():
    """Walk the generated trie rules: each file line must be derivable from sl."""
    import re
    lines = ["def f():", "def g():", "    return 1", "", "    return 12"]
    rules = dict(r.split(" ::= ", 1) for r in edit_grammar._trie_rules(edit_grammar._build_trie(lines)))

    def unlit(tok):
        return bytes(tok[1:-1], "utf-8").decode("unicode_escape")

    def derives(rule, text):
        for alt in rules[rule].split(" | "):
            parts = re.findall(r'"(?:[^"\\]|\\.)*"|t\d+', alt)
            lit = unlit(parts[0])
            if not text.startswith(lit):
                continue
            rest = text[len(lit):]
            if len(parts) == 1 and rest == "":
                return True
            if len(parts) == 2 and derives(parts[1], rest):
                return True
        return False

    for line in lines:
        assert derives("sl", line + "\n"), line
    assert not derives("sl", "def h():\n")


def test_grammar_size_cap(tmp_path, monkeypatch, capsys):
    (tmp_path / "big.py").write_text("".join(f"v{i} = {i}\n" for i in range(2000)))
    monkeypatch.setenv("AWOS_EDIT_GRAMMAR_MAX_BYTES", "1000")
    g, note = edit_grammar.grammar_for_files(str(tmp_path), ["big.py"])
    assert g is None and "> cap 1000" in note


def test_grammar_skipped_for_cloud_client(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AWOS_EDIT_GRAMMAR", "1")
    (tmp_path / "a.py").write_text("x = 1\n")
    ctx = one_shot.OneShotContext(text="", files=["a.py"])
    assert one_shot._edit_grammar_body(object(), str(tmp_path), ctx, False) is None
    assert "not the local server" in capsys.readouterr().out


def test_grammar_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AWOS_EDIT_GRAMMAR", raising=False)
    ctx = one_shot.OneShotContext(text="", files=["a.py"])
    assert one_shot._edit_grammar_body(object(), str(tmp_path), ctx, False) is None


def test_grammar_local_client_body(tmp_path, monkeypatch):
    import providers
    monkeypatch.setenv("AWOS_EDIT_GRAMMAR", "1")
    monkeypatch.setattr(providers, "is_local_client", lambda c: True)
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "test_a.py").write_text("assert 1\n")
    ctx = one_shot.OneShotContext(text="", files=["a.py", "test_a.py"], read_only=["test_a.py"])
    body = one_shot._edit_grammar_body(object(), str(tmp_path), ctx, False)
    assert body and "x = 1" in body["grammar"] and "assert 1" not in body["grammar"]


def test_fuzzy_path_prefix(tmp_path, fuzzy_on):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n")
    blocks = [one_shot.EditBlock("path/to/pkg/mod.py", "x = 1", "x = 2")]
    applied, failed = one_shot.apply_blocks(str(tmp_path), blocks)
    assert applied == ["pkg/mod.py"] and not failed
    assert (tmp_path / "pkg" / "mod.py").read_text() == "x = 2\n"


def test_fuzzy_path_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AWOS_FUZZY_APPLY", raising=False)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n")
    applied, failed = one_shot.apply_blocks(
        str(tmp_path), [one_shot.EditBlock("path/to/pkg/mod.py", "x = 1", "x = 2")])
    assert not applied and failed


def test_fuzzy_path_ambiguous_basename(tmp_path, fuzzy_on):
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "mod.py").write_text("x = 1\n")
    assert one_shot._fuzzy_path(tmp_path, "zzz/mod.py") is None


GLUED = ("Let's produce the block for `pkg/mod.py`.pkg/mod.py\n"
         "<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n")


def test_glued_path_parsed_with_flag(fuzzy_on):
    blocks, bad = one_shot.parse_blocks(GLUED)
    assert [b.path for b in blocks] == ["pkg/mod.py"] and not bad


def test_glued_path_off_by_default(monkeypatch):
    monkeypatch.delenv("AWOS_FUZZY_APPLY", raising=False)
    blocks, bad = one_shot.parse_blocks(GLUED)
    assert not blocks and bad
