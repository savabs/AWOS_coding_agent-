"""
One-shot context priority: relevant sources rank ahead of tests, and the
top-ranked source above the per-file cap goes whole up to a share of the
budget (AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION). Regression for real_parse_137/159,
where tests and a .pyi stub filled the budget and parse/__init__.py was cut
to keyword-hit sections that missed the fix region.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import one_shot
from scaffold.agent.one_shot import build_context, rank_files


def _source(n_funcs: int) -> str:
    out = ["import re", ""]
    for i in range(n_funcs):
        out += [f"def handler_{i}(value):", f"    return value * {i} + 1", ""]
    out += ["def needle_fix_here(x):", "    return x  # the line to change", ""]
    return "\n".join(out) + "\n"


def _test_file(i: int, n: int = 40) -> str:
    body = [f"from pkg.core import handler_{j}\n" for j in range(n)]
    body += [f"def test_{i}_{j}():\n    assert handler_{j}(1) == {j + 1}\n" for j in range(n)]
    return "".join(body)


def _repo(src_funcs: int, n_tests: int = 8) -> Path:
    """pkg/core.py (the source to fix, few grep hits), a .pyi stub, many tests with
    more keyword hits than the source."""
    root = Path(tempfile.mkdtemp())
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "core.py").write_text(_source(src_funcs), encoding="utf-8")
    (root / "pkg" / "core.pyi").write_text("def handler_0(value): ...\n", encoding="utf-8")
    (root / "tests").mkdir()
    for i in range(n_tests):
        (root / "tests" / f"test_t{i}.py").write_text(_test_file(i), encoding="utf-8")
    return root


def _exploration(root: Path, n_tests: int = 8) -> dict:
    hits = [{"file": str(root / "pkg" / "core.py"), "line": 3, "text": "handler"}]
    for i in range(n_tests):  # tests have more hits than the source
        hits += [{"file": str(root / "tests" / f"test_t{i}.py"), "line": k, "text": "handler"}
                 for k in (1, 2, 3)]
    hits.append({"file": str(root / "pkg" / "core.pyi"), "line": 1, "text": "handler"})
    hits += [{"file": str(root / "pkg" / "core.pyi"), "line": 1, "text": "x"}] * 5
    return {"grep_hits": hits}


TASK = "handler values are wrong"


# ── ranking ─────────────────────────────────────────────────────────────────

def test_ranking_keeps_the_original_order():
    code = ["tests/test_a.py", "pkg/core.py", "pkg/other.py", "tests/test_b.py", "pkg/core.pyi"]
    hits = {"tests/test_a.py": 9, "tests/test_b.py": 5, "pkg/core.py": 1, "pkg/core.pyi": 3}
    ranked = rank_files(code, "fix it", hits)
    assert ranked == ["tests/test_a.py", "tests/test_b.py", "pkg/core.pyi", "pkg/core.py",
                      "pkg/other.py"]                 # most hits first, as before
    assert one_shot._top_source(ranked, "fix it", hits) == "pkg/core.py"  # skips tests, .pyi


def test_no_top_source_among_unrelated_files():
    ranked = rank_files(["tests/test_a.py", "pkg/zzz.py"], "fix it", {"tests/test_a.py": 2})
    assert one_shot._top_source(ranked, "fix it", {"tests/test_a.py": 2}) is None


# ── whole-source rule ───────────────────────────────────────────────────────

def test_top_source_above_cap_goes_whole_before_tests(monkeypatch):
    monkeypatch.delenv("AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION", raising=False)
    monkeypatch.delenv("AWOS_ONE_SHOT_FILE_CAP_TOKENS", raising=False)
    root = _repo(src_funcs=900)                      # ~8k tokens: above 6k cap, under 12k
    cost = one_shot.estimate_tokens((root / "pkg" / "core.py").read_text())
    assert one_shot.file_cap_tokens() < cost <= 24000 // 2
    ctx = build_context(str(root), TASK, _exploration(root), 24000)
    assert "pkg/core.py" in ctx.files and "pkg/core.py" not in ctx.sections
    assert "def needle_fix_here(x):" in ctx.text      # far from every grep hit
    assert ctx.files.index("pkg/core.py") < min(
        ctx.files.index(f) for f in ctx.files if f.startswith("tests/"))
    assert ctx.tokens <= 24000 + 50


def test_source_above_fraction_stays_sectioned(monkeypatch):
    monkeypatch.delenv("AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION", raising=False)
    root = _repo(src_funcs=1500)                     # ~14k tokens: above 50% of 24k
    assert one_shot.estimate_tokens((root / "pkg" / "core.py").read_text()) > 12000
    ctx = build_context(str(root), TASK, _exploration(root), 24000)
    assert "pkg/core.py" in ctx.sections and "pkg/core.py" not in ctx.files


def test_fraction_zero_turns_the_rule_off(monkeypatch):
    monkeypatch.setenv("AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION", "0")
    root = _repo(src_funcs=900)
    ctx = build_context(str(root), TASK, _exploration(root), 24000)
    assert "pkg/core.py" in ctx.sections and "pkg/core.py" not in ctx.files


def test_only_the_top_source_gets_the_raised_cap(monkeypatch):
    monkeypatch.delenv("AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION", raising=False)
    root = _repo(src_funcs=900, n_tests=0)
    (root / "pkg" / "second.py").write_text(_source(900), encoding="utf-8")
    exp = {"grep_hits": [{"file": str(root / "pkg" / "core.py"), "line": 3, "text": "h"}] * 3
           + [{"file": str(root / "pkg" / "second.py"), "line": 3, "text": "h"}]}
    ctx = build_context(str(root), TASK, exp, 40000)
    assert "pkg/core.py" in ctx.files
    assert "pkg/second.py" in ctx.sections            # over the cap, not the top source


def test_small_source_unchanged(monkeypatch):
    monkeypatch.delenv("AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION", raising=False)
    root = _repo(src_funcs=50)                        # under the cap anyway
    ctx = build_context(str(root), TASK, _exploration(root), 24000)
    monkeypatch.setenv("AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION", "0")
    off = build_context(str(root), TASK, _exploration(root), 24000)
    assert ctx.text == off.text                        # rule does not apply
    assert ctx.sections == []


def test_fraction_env_parsing(monkeypatch):
    monkeypatch.delenv("AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION", raising=False)
    assert one_shot.whole_source_fraction() == 0.5
    monkeypatch.setenv("AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION", "0.3")
    assert one_shot.whole_source_fraction() == 0.3
    monkeypatch.setenv("AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION", "junk")
    assert one_shot.whole_source_fraction() == 0.5
    monkeypatch.setenv("AWOS_ONE_SHOT_WHOLE_SOURCE_FRACTION", "7")
    assert one_shot.whole_source_fraction() == 1.0
