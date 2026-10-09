"""T9 idle-time practice: mutation operators, mutant validation, series format,
capability posterior (docs/specs/idle_practice.md)."""
from __future__ import annotations

import ast
import importlib.util
import json
import math
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PRACTICE = REPO / "scripts" / "practice"
sys.path.insert(0, str(PRACTICE))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load("mutators", PRACTICE / "mutators.py")
MT = _load("practice_make_tasks", PRACTICE / "make_tasks.py")
CM = _load("practice_capability_map", PRACTICE / "capability_map.py")
JS = _load("job_series_for_practice", REPO / "scripts" / "job_series.py")

SAMPLE = textwrap.dedent('''\
    """Sample module."""
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        LIMIT: int = 3


    def clamp(x: int, lo: int = 0, hi: int = 10) -> int:
        if x < lo:
            return lo
        if x > hi and hi is not None:
            return hi
        return x


    def total(items, start=0):
        acc = start
        for it in items:
            acc += it * 2
        log("total", acc)
        return acc if acc >= 0 else -acc


    def pick(a, b):
        return max(a, b) - min(a, b) % 3


    def log(*args):
        print(*args)
''')


# ── operators ─────────────────────────────────────────────────────────────────


def test_every_operator_has_candidates_and_all_compile():
    muts = M.enumerate_mutations(SAMPLE)
    ops = {m.op for m in muts}
    assert ops == set(M.OPERATORS)
    applied = 0
    for m in muts:
        out = M.apply(SAMPLE, m)
        if out is None:
            continue
        applied += 1
        compile(out, "<t>", "exec")  # compilable
        assert ast.dump(ast.parse(out)) != ast.dump(ast.parse(SAMPLE))  # really changed
    assert applied >= len(muts) - 2


def test_mutations_are_minimal_single_hunk_edits():
    import difflib
    for m in M.enumerate_mutations(SAMPLE):
        out = M.apply(SAMPLE, m)
        if out is None:
            continue
        changed = [ln for ln in difflib.unified_diff(SAMPLE.splitlines(), out.splitlines(), n=0, lineterm="")
                   if ln.startswith("@@")]
        assert len(changed) == 1, (m, out)


def test_annotations_and_type_checking_are_never_mutated():
    muts = M.enumerate_mutations(SAMPLE)
    # LIMIT: int = 3 sits under `if TYPE_CHECKING:` on line 5
    assert not [m for m in muts if m.lineno in (4, 5)]


def _one(op: str, src: str, variant: int = 0) -> str:
    m = next(m for m in M.enumerate_mutations(src) if m.op == op and m.variant == variant)
    out = M.apply(src, m)
    assert out is not None
    return out


@pytest.mark.parametrize("op,src,expect", [
    ("flip_compare", "y = a < b\n", "y = a >= b\n"),
    ("flip_compare", "y = a is not None\n", "y = a is None\n"),
    ("flip_compare", "y = a not in b\n", "y = a in b\n"),
    ("flip_binop", "y = a + b\n", "y = a - b\n"),
    ("flip_boolop", "y = a and b\n", "y = a or b\n"),
    ("off_by_one", "y = x[2]\n", "y = x[3]\n"),
    ("negate_if", "if ok:\n    f()\n", "if not ok:\n    f()\n"),
    ("negate_if", "if not ok:\n    f()\n", "if ok:\n    f()\n"),
    ("remove_conditional", "def f(x):\n    if x:\n        return 1\n    return 2\n",
     "def f(x):\n    return 2\n"),
    ("swap_args", 'y = f(a, "b")\n', 'y = f("b", a)\n'),
    ("change_return", "def f():\n    return 5\n", "def f():\n    return None\n"),
    ("remove_call", "def f(x):\n    x.append(1)\n", "def f(x):\n    pass\n"),
    ("swap_ifexp", "y = a if c else b\n", "y = b if c else a\n"),
])
def test_operator_examples(op, src, expect):
    assert _one(op, src) == expect


def test_string_concatenation_and_percent_format_not_flipped():
    src = 'y = "a" + b\nz = "%s" % b\n'
    assert not [m for m in M.enumerate_mutations(src) if m.op == "flip_binop"]


def test_mutant_key_ignores_formatting():
    assert M.mutant_key("f.py", "y = a+b\n") == M.mutant_key("f.py", "y = a + b\n")
    assert M.mutant_key("f.py", "y = a+b\n") != M.mutant_key("g.py", "y = a+b\n")


# ── generator on a tiny synthetic repo ───────────────────────────────────────


def _toy_repo(root: Path) -> Path:
    repo = root / "toy"
    (repo / "toy").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "toy" / "__init__.py").write_text("")
    (repo / "toy" / "core.py").write_text(textwrap.dedent('''\
        def add(a, b):
            return a + b


        def is_small(x):
            if x < 10:
                return True
            return False


        def unused(x):
            return x * 7
    '''))
    (repo / "tests" / "test_add.py").write_text(
        "from toy.core import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    (repo / "tests" / "test_small.py").write_text(
        "from toy.core import is_small\n\ndef test_small():\n    assert is_small(3)\n"
        "    assert not is_small(30)\n")
    (repo / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n')
    return repo


def test_classify_keeps_only_test_flipping_collecting_mutants():
    base = {"total": 3, "passed": {"a", "b", "c"}, "failed": {}}
    mk = lambda **kw: {"timed_out": False, "exit": 1, "total": 3, "failed": {}, **kw}  # noqa: E731
    assert MT.classify(mk(exit=0), base, 0.9) == "no_test_flipped"
    assert MT.classify(mk(exit=2), base, 0.9) == "collection_or_internal_error"
    assert MT.classify(mk(total=2, failed={"a": ""}), base, 0.9) == "collection_changed"
    assert MT.classify(mk(timed_out=True), base, 0.9) == "timeout"
    assert MT.classify(mk(failed={"zzz": ""}), base, 0.9) == "no_test_flipped"
    assert MT.classify(mk(failed={"a": "", "b": "", "c": ""}), base, 0.5) == "too_many_failures"
    assert MT.classify(mk(failed={"a": "boom"}), base, 0.5) == "keep"


def test_generate_writes_valid_real_series_format(tmp_path):
    repo = _toy_repo(tmp_path)
    before = {p: p.read_text() for p in repo.rglob("*.py")}
    out = tmp_path / "series"
    stats = MT.generate(repo, out, n=3, name="toy", budget_s=120, per_file=3, seed=1,
                        max_fail_frac=1.0, max_tries=60, log=lambda *a: None)
    # never touches the original repo
    assert {p: p.read_text() for p in repo.rglob("*.py")} == before
    assert stats["kept"] >= 1 and stats["series"]
    for name in stats["series"]:
        sdir = out / name
        job = sdir / "jobs" / "01_regression"
        task = json.loads((job / "task.json").read_text())
        assert set(JS.TASK_KEYS) <= set(task)
        meta = json.loads((sdir / "practice.json").read_text())
        assert meta["op"] in M.OPERATORS and meta["file"] == "toy/core.py"
        assert "Mutation type" in (sdir / "SOURCE.md").read_text()
        # no fix hint: the goal never names the mutated file or the operator
        assert "core.py" not in task["goal"] and meta["op"] not in task["goal"]
        # reference = original file; base = mutated; hidden tests removed from base
        assert (job / "reference" / "toy" / "core.py").read_text() == before[repo / "toy" / "core.py"]
        assert (sdir / "base" / "toy" / "core.py").read_text() != before[repo / "toy" / "core.py"]
        hidden = sorted(p.name for p in (job / "hidden_tests").glob("test_hidden_*.py"))
        assert hidden
        for h in hidden:
            assert not (sdir / "base" / "tests" / h.replace("test_hidden_", "test_")).exists()
        # the harness's own check: hidden fail at start, pass with reference, visible ok
        c = JS.check_job(sdir, JS.discover_jobs(sdir), 1, job)
        assert c["ok"], c["problems"]
    # `unused` is never reached by a test: no kept mutant may be on its line
    assert all(json.loads((out / s / "practice.json").read_text())["line"] != 12
               for s in stats["series"])
    assert list((out / "_practice_runs").glob("toy_*.json"))


def test_generate_refuses_red_baseline(tmp_path):
    repo = _toy_repo(tmp_path)
    (repo / "tests" / "test_red.py").write_text("def test_red():\n    assert False\n")
    with pytest.raises(SystemExit):
        MT.generate(repo, tmp_path / "s", n=1, name="toy", budget_s=60, log=lambda *a: None)


def test_goal_lists_symptoms_and_caps_length():
    failing = {f"tests.test_x::test_{i}": f"AssertionError: assert {i} == 0" for i in range(12)}
    g = MT.goal_text("toy", failing, {t: "tests/test_x.py" for t in failing})
    assert "12 previously passing tests" in g
    assert "and 4 more" in g and g.count("\n- `") == 8


# ── capability posterior ─────────────────────────────────────────────────────


def test_beta_cdf_matches_closed_forms():
    # Beta(1,1) is uniform; Beta(2,1) cdf = x^2; Beta(1,3) cdf = 1-(1-x)^3
    for x in (0.1, 0.37, 0.5, 0.9):
        assert CM.beta_cdf(x, 1, 1) == pytest.approx(x, abs=1e-10)
        assert CM.beta_cdf(x, 2, 1) == pytest.approx(x * x, abs=1e-10)
        assert CM.beta_cdf(x, 1, 3) == pytest.approx(1 - (1 - x) ** 3, abs=1e-10)
    # symmetric Beta(a,a) has median 0.5
    assert CM.beta_ppf(0.5, 7, 7) == pytest.approx(0.5, abs=1e-9)
    # binomial identity: I_x(k, n-k+1) = P(Bin(n, x) >= k)
    n, k, x = 10, 4, 0.3
    tail = sum(math.comb(n, j) * x**j * (1 - x) ** (n - j) for j in range(k, n + 1))
    assert CM.beta_cdf(x, k, n - k + 1) == pytest.approx(tail, abs=1e-10)


def test_posterior_math():
    p = CM.posterior(7, 3, threshold=0.5)
    assert (p["alpha"], p["beta"]) == (8, 4)
    assert p["mean"] == pytest.approx(8 / 12, abs=1e-4)
    assert p["lo"] < p["mean"] < p["hi"]
    assert p["p_ge_threshold"] == pytest.approx(1 - CM.beta_cdf(0.5, 8, 4), abs=1e-4)
    empty = CM.posterior(0, 0)
    assert empty["mean"] == 0.5 and empty["p_ge_threshold"] == pytest.approx(0.5)


def test_capability_map_from_synthetic_results(tmp_path):
    root = tmp_path / "series"
    for name, repo, op in [("prac_a_001", "a", "flip_compare"), ("prac_a_002", "a", "swap_args"),
                           ("prac_b_001", "b", "flip_compare")]:
        (root / name).mkdir(parents=True)
        (root / name / "practice.json").write_text(json.dumps({"repo": repo, "op": op}))

    def doc(series, rows):
        return {"series": series, "results": [{"arm": arm, "job": 1, "solved": s, "invalid": inv}
                                              for arm, s, inv in rows]}
    docs = [
        doc("prac_a_001", [("off", True, False), ("off", True, False), ("on", False, False)]),
        doc("prac_a_002", [("off", False, False), ("off", False, True)]),   # invalid row dropped
        doc("prac_b_001", [("off", False, False)]),
        doc("prac_zz_007", [("off", True, False)]),                         # no practice.json
    ]
    cmap = CM.build_map(CM.collect(docs, root, "off"), threshold=0.5)
    assert cmap["a"]["flip_compare"]["solved"] == 2 and cmap["a"]["flip_compare"]["n"] == 2
    assert cmap["a"]["swap_args"]["n"] == 1 and cmap["a"]["swap_args"]["failed"] == 1
    assert cmap["a"]["*"]["n"] == 3 and cmap["a"]["*"]["solved"] == 2
    assert cmap["b"]["*"]["mean"] == pytest.approx(1 / 3, abs=1e-4)
    assert cmap["zz"]["unknown"]["solved"] == 1
    # the router signal orders repos by evidence
    assert cmap["a"]["flip_compare"]["p_ge_threshold"] > cmap["b"]["flip_compare"]["p_ge_threshold"]
