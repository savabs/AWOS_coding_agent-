"""R40 families 6-10: regenerate a markdown table from JSON, rename a function
across a module, add a config env var, write a unit test for a pure function,
update a spec section from a JSON summary. 4 instances each (+ near-misses for
6, 7, 8, 9)."""
from __future__ import annotations

import json
import re

from r40_common import PYTEST_INI, D, T, Task, repo_text, sub_once

# ── F06 regenerate a markdown table from JSON ─────────────────────────────────

F06_HIDDEN = D('''
    import json
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]
    DOC = ROOT / "@@DOC@@"
    START, END = "<!-- table:@@ID@@ start -->", "<!-- table:@@ID@@ end -->"
    EXPECTED = json.loads(@@ROWS@@)        # header row first, then data rows
    OUTSIDE = json.loads(@@OUTSIDE@@)      # [text before START, text after END]


    def split(text):
        head, rest = text.split(START, 1)
        table, tail = rest.split(END, 1)
        return head, table, tail


    def cells(line):
        return [c.strip() for c in line.strip().strip("|").split("|")]


    def test_table_regenerated():
        _, table, _ = split(DOC.read_text(encoding="utf-8"))
        lines = [ln for ln in table.splitlines() if ln.strip()]
        assert set(lines[1].replace("|", "").replace(":", "").strip()) <= {"-", " "}, lines[1]
        rows = [cells(lines[0])] + [cells(ln) for ln in lines[2:]]
        assert rows == EXPECTED


    def test_rest_of_doc_untouched():
        head, _, tail = split(DOC.read_text(encoding="utf-8"))
        assert [head, tail] == OUTSIDE
''')


def _md_table(rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def _f06_doc(title: str, tid: str, intro: str, stale: list[list[str]], outro: str) -> tuple[str, str, str]:
    head = f"# {title}\n\n{intro}\n\n<!-- table:{tid} start -->"
    tail = f"<!-- table:{tid} end -->\n\n{outro}\n"
    return head, tail, f"{head}\n{_md_table(stale)}\n{tail}"


F06_CASES = [
    dict(slug="e1_arms", doc="docs/RESULTS.md", json_path="reports/e1_summary.json", tid="e1",
         title="Results", intro="Generated tables — regenerate from reports/, don't hand-edit.",
         outro="E1 is pre-registered in docs/specs/ablation_E1_edit_reliability.md.",
         data={"run": "20261006T0912", "arms": {
             "on": {"solved": 47, "runs": 146, "cost_usd": 4.9640},
             "off": {"solved": 41, "runs": 146, "cost_usd": 4.3800},
             "aider": {"solved": 38, "runs": 146, "cost_usd": 13.1400}}},
         spec=("header `| Arm | Solved | Runs | $/run |`, one row per arm sorted by arm name, "
               "$/run = cost_usd / runs with 3 decimals (no `$` sign)"),
         rows=lambda d: [["Arm", "Solved", "Runs", "$/run"]] + [
             [a, str(v["solved"]), str(v["runs"]), f"{v['cost_usd'] / v['runs']:.3f}"]
             for a, v in sorted(d["arms"].items())],
         stale=[["Arm", "Solved", "Runs", "$/run"], ["off", "39", "140", "0.031"],
                ["on", "44", "140", "0.035"]]),
    dict(slug="prices", doc="docs/MODELS.md", json_path="reports/prices.json", tid="prices",
         title="Models", intro="Prices are per million tokens, from the provider catalogue.",
         outro="Routing rules live in docs/specs/model_tiering_spec.md.",
         data={"models": [
             {"id": "deepseek/deepseek-v4-flash", "input": 0.14, "output": 0.28},
             {"id": "deepseek/deepseek-v4-pro", "input": 0.55, "output": 2.19},
             {"id": "qwen/qwen3.6-35b-a3b", "input": 0.0, "output": 0.0},
             {"id": "anthropic/claude-haiku-4.5", "input": 1.0, "output": 5.0},
             {"id": "google/gemini-2.5-flash", "input": 0.3, "output": 2.5}]},
         spec=("header `| Model | Input $/M | Output $/M |`, one row per model sorted by input "
               "price ascending (ties by id), prices with 2 decimals"),
         rows=lambda d: [["Model", "Input $/M", "Output $/M"]] + [
             [m["id"], f"{m['input']:.2f}", f"{m['output']:.2f}"]
             for m in sorted(d["models"], key=lambda m: (m["input"], m["id"]))],
         stale=[["Model", "Input $/M", "Output $/M"], ["deepseek/deepseek-chat", "0.27", "1.10"]]),
    dict(slug="cache_components", doc="docs/CACHE.md", json_path="reports/cache_summary.json",
         tid="cache", title="Prompt cache",
         intro="Hit ratio per component from `scripts/cache_report.py --json`.",
         outro="Token-weighted: cached / input tokens.",
         data={"by_component": {
             "one_shot": {"calls": 73, "input_tokens": 410000, "cached_tokens": 0, "cache_hit_ratio": 0.0},
             "agent": {"calls": 512, "input_tokens": 9200000, "cached_tokens": 7452000,
                       "cache_hit_ratio": 0.81},
             "acceptance": {"calls": 66, "input_tokens": 0, "cached_tokens": 0, "cache_hit_ratio": None},
             "arbiter": {"calls": 9, "input_tokens": 54000, "cached_tokens": 12420,
                         "cache_hit_ratio": 0.23}}},
         spec=("header `| Component | Calls | Hit % |`, one row per component in the JSON's order, "
               "Hit % = cache_hit_ratio x 100 with 1 decimal, or `-` when it is null"),
         rows=lambda d: [["Component", "Calls", "Hit %"]] + [
             [k, str(v["calls"]), "-" if v["cache_hit_ratio"] is None
              else f"{100 * v['cache_hit_ratio']:.1f}"] for k, v in d["by_component"].items()],
         stale=[["Component", "Calls", "Hit %"], ["agent", "400", "75.0"]]),
    dict(slug="series_sizes", doc="docs/SERIES.md", json_path="reports/series.json", tid="series",
         title="Benchmark series", intro="Series under ~/.awos-harness/real_series.",
         outro="See docs/specs/compounding_proof_spec.md for the series format.",
         data=[{"name": "real_click_3208", "jobs": 1, "difficulty": "easy"},
               {"name": "ordertool", "jobs": 12, "difficulty": "mixed"},
               {"name": "salesdesk", "jobs": 8, "difficulty": "mixed"},
               {"name": "backupd", "jobs": 8, "difficulty": "hard"},
               {"name": "real_attrs_1513", "jobs": 1, "difficulty": "easy"}],
         spec=("header `| Series | Jobs | Difficulty |`, one row per series sorted by jobs "
               "descending, ties by name ascending"),
         rows=lambda d: [["Series", "Jobs", "Difficulty"]] + [
             [s["name"], str(s["jobs"]), s["difficulty"]]
             for s in sorted(d, key=lambda s: (-s["jobs"], s["name"]))],
         stale=[["Series", "Jobs", "Difficulty"], ["ordertool", "12", "mixed"]]),
]


def f06() -> list[Task]:
    fam = "f06_md_table_from_json"
    tasks = []
    for i, c in enumerate(F06_CASES, 1):
        head, tail, doc = _f06_doc(c["title"], c["tid"], c["intro"], c["stale"], c["outro"])
        rows = c["rows"](c["data"])
        base = {"pytest.ini": PYTEST_INI, c["doc"]: doc,
                c["json_path"]: json.dumps(c["data"], indent=2) + "\n"}
        goal = (f"Regenerate the table between `<!-- table:{c['tid']} start -->` and "
                f"`<!-- table:{c['tid']} end -->` in {c['doc']} from {c['json_path']}: "
                f"{c['spec']}. Leave everything outside the markers unchanged.")
        tasks.append(Task(
            family=fam, slug=c["slug"], instance=i, goal=goal, base=base,
            hidden={f"test_hidden_table_{c['slug']}.py": T(
                F06_HIDDEN, DOC=c["doc"], ID=c["tid"], ROWS=repr(json.dumps(rows)),
                OUTSIDE=repr(json.dumps([doc.split(f"<!-- table:{c['tid']} start -->")[0],
                                         doc.split(f"<!-- table:{c['tid']} end -->")[1]])))},
            reference={c["doc"]: f"{head}\n{_md_table(rows)}\n{tail}"},
            check="table cells between the markers equal the recomputation; text outside unchanged",
            params={"doc": c["doc"], "json": c["json_path"], "table": c["tid"]}))
    # near-miss: the table is the source of truth, the JSON is stale (reverse direction)
    truth = [["Arm", "Solved", "Runs"], ["aider", "38", "146"], ["off", "41", "146"], ["on", "47", "146"]]
    head, tail, doc = _f06_doc("Results", "e1", "E1 numbers below were hand-checked against the logs.",
                               truth, "E1 is pre-registered in docs/specs/ablation_E1_edit_reliability.md.")
    stale = {"run": "20261006T0912", "arms": {"aider": {"solved": 36, "runs": 140},
                                               "off": {"solved": 40, "runs": 140},
                                               "on": {"solved": 45, "runs": 140}}}
    fixed = {"run": "20261006T0912", "arms": {r[0]: {"solved": int(r[1]), "runs": int(r[2])}
                                               for r in truth[1:]}}
    nm_hidden = D('''
        import json
        from pathlib import Path

        ROOT = Path(__file__).resolve().parents[1]
        DOC = @@DOC@@


        def test_json_matches_table():
            data = json.loads((ROOT / "reports" / "e1_summary.json").read_text(encoding="utf-8"))
            assert data["run"] == "20261006T0912"
            assert data["arms"] == @@ARMS@@


        def test_doc_untouched():
            assert (ROOT / "docs" / "RESULTS.md").read_text(encoding="utf-8") == DOC
    ''')
    tasks.append(Task(
        family=fam, slug="json_from_table", near_miss=True,
        goal=("The E1 table in docs/RESULTS.md (between the `table:e1` markers) was hand-checked "
              "against the logs and is correct; reports/e1_summary.json is out of date. Update the "
              "solved/runs numbers in reports/e1_summary.json to match the table. Don't touch the doc."),
        base={"pytest.ini": PYTEST_INI, "docs/RESULTS.md": doc,
              "reports/e1_summary.json": json.dumps(stale, indent=2) + "\n"},
        hidden={"test_hidden_json_from_table.py": T(nm_hidden, DOC=repr(doc),
                                                     ARMS=repr(fixed["arms"]))},
        reference={"reports/e1_summary.json": json.dumps(fixed, indent=2) + "\n"},
        check="JSON arms equal the table; the markdown doc is byte-identical",
        near_miss_reason="data flows table -> JSON; regenerating the table from JSON destroys the truth",
        params={"doc": "docs/RESULTS.md", "json": "reports/e1_summary.json"}))
    return tasks


# ── F07 rename a function across a module ─────────────────────────────────────

CACHE_COMPARE = D('''
    #!/usr/bin/env python3
    """cache_compare.py — compare per-component cache hit ratios of two runs (A vs B).

        python scripts/cache_compare.py --a RUN_A_DIR --b RUN_B_DIR
    """
    from __future__ import annotations

    import argparse
    import json
    import sys
    from collections import defaultdict
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cache_report  # noqa: E402
    from cache_report import _num, bucket_of, find_logs, iter_lines, summarise  # noqa: E402


    def compare(a_targets: list[str], b_targets: list[str]) -> dict:
        sa = summarise(iter_lines(find_logs(a_targets)))
        sb = summarise(iter_lines(find_logs(b_targets)))
        out = {}
        for bucket in cache_report.BUCKETS:
            ra, rb = sa["by_component"].get(bucket), sb["by_component"].get(bucket)
            if ra or rb:
                out[bucket] = {"a": ra and ra["cache_hit_ratio"], "b": rb and rb["cache_hit_ratio"]}
        return out


    def cost_by_bucket(targets: list[str]) -> dict:
        totals: dict = defaultdict(float)
        for line in iter_lines(find_logs(targets)):
            totals[bucket_of(line)] += _num(line.get("cost_usd"))
        return {k: round(v, 6) for k, v in sorted(totals.items())}


    def main(argv: list[str] | None = None) -> int:
        ap = argparse.ArgumentParser(description="compare cache hit ratios of two runs")
        ap.add_argument("--a", nargs="+", required=True)
        ap.add_argument("--b", nargs="+", required=True)
        args = ap.parse_args(argv)
        print(json.dumps({"hit_ratio": compare(args.a, args.b),
                          "cost_a": cost_by_bucket(args.a), "cost_b": cost_by_bucket(args.b)},
                         indent=2))
        return 0


    if __name__ == "__main__":
        raise SystemExit(main())
''')

RUN_A = [{"component": "agent", "input_tokens": 100, "cached_tokens": 50, "output_tokens": 10,
          "cost_usd": 0.002, "provider": "deepinfra"},
         {"component": "acceptance", "request_type": "acceptance_arbitration", "input_tokens": 40,
          "output_tokens": 4, "cost_usd": "0.001", "provider": "deepinfra"}]
RUN_B = [{"component": "agent", "input_tokens": 100, "cached_tokens": 80, "output_tokens": 10,
          "cost_usd": 0.001, "provider": "deepinfra"},
         {"component": "one_shot", "input_tokens": 200, "output_tokens": 30, "cost_usd": 0.004,
          "provider": "novita"},
         {"component": "planner", "input_tokens": 10, "output_tokens": 1, "cost_usd": None,
          "error": "timeout"}]

CACHE_VISIBLE = D('''
    from pathlib import Path

    import cache_report as cr

    FIX = Path(__file__).resolve().parents[1] / "fixtures"


    def test_bucket_of():
        assert cr.bucket_of({"component": "agent"}) == "agent"
        assert cr.bucket_of({"component": "acceptance", "request_type": "acceptance_arbitration"}) == "arbiter"


    def test_num():
        assert cr._num("2") == 2.0 and cr._num(None) == 0.0 and cr._num("x") == 0.0


    def test_summarise_and_find_logs():
        s = cr.summarise(cr.iter_lines(cr.find_logs([str(FIX / "run_a")])))
        assert s["total"]["calls"] == 2
        assert s["by_component"]["agent"]["cache_hit_ratio"] == 0.5
''')

F07_HIDDEN = D('''
    import ast
    import json
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT / "scripts"))
    OLD, NEW = @@OLD@@, @@NEW@@
    FIX = ROOT / "fixtures"


    def identifiers(path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                yield node.id
            elif isinstance(node, ast.Attribute):
                yield node.attr
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                yield node.name
            elif isinstance(node, ast.alias):
                yield node.name
                if node.asname:
                    yield node.asname


    def test_old_name_gone_everywhere():
        me = Path(__file__).resolve()
        files = list((ROOT / "scripts").glob("*.py")) + list((ROOT / "tests").rglob("*.py"))
        for f in files:
            if f.resolve() == me or f.name.startswith("test_hidden_"):
                continue
            assert OLD not in set(identifiers(f)), f"{OLD} still used in {f.relative_to(ROOT)}"


    def test_new_name_defined():
        import cache_report
        assert callable(getattr(cache_report, NEW, None))
        assert not hasattr(cache_report, OLD)


    def test_behaviour_preserved(capsys):
        import cache_compare
        assert cache_compare.main(["--a", str(FIX / "run_a"), "--b", str(FIX / "run_b")]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["hit_ratio"] == {"one_shot": {"a": None, "b": 0.0}, "agent": {"a": 0.5, "b": 0.8},
                                    "arbiter": {"a": 0.0, "b": None}, "other": {"a": None, "b": 0.0}}
        assert out["cost_a"] == {"agent": 0.002, "arbiter": 0.001}
        assert out["cost_b"] == {"agent": 0.001, "one_shot": 0.004, "other": 0.0}
''')


def _f07_base() -> dict[str, str]:
    jl = lambda rows: "".join(json.dumps(r) + "\n" for r in rows)
    return {"pytest.ini": PYTEST_INI,
            "scripts/cache_report.py": repo_text("scripts/cache_report.py"),
            "scripts/cache_compare.py": CACHE_COMPARE,
            "fixtures/run_a/llm_calls.jsonl": jl(RUN_A),
            "fixtures/run_b/llm_calls.jsonl": jl(RUN_B),
            "tests/test_cache_report.py": CACHE_VISIBLE}


def f07() -> list[Task]:
    fam = "f07_rename_function"
    base = _f07_base()
    renames = [("_num", "as_number"), ("bucket_of", "component_bucket"),
               ("summarise", "summarize"), ("find_logs", "discover_logs")]
    tasks = []
    for i, (old, new) in enumerate(renames, 1):
        pat = re.compile(rf"\b{re.escape(old)}\b")
        ref = {}
        for rel in ("scripts/cache_report.py", "scripts/cache_compare.py", "tests/test_cache_report.py"):
            new_text = pat.sub(new, base[rel])
            assert new_text != base[rel], (old, rel)
            ref[rel] = new_text
        goal = (f"Rename the function `{old}` in scripts/cache_report.py to `{new}` and update every "
                f"caller in the project (scripts and tests). Don't keep the old name around; "
                f"behaviour must not change.")
        tasks.append(Task(
            family=fam, slug=f"{old.strip('_')}_to_{new}", instance=i, goal=goal, base=base,
            hidden={f"test_hidden_rename_{new}.py": T(F07_HIDDEN, OLD=repr(old), NEW=repr(new))},
            reference=ref,
            check="AST: old identifier gone from scripts/ and tests/; new one defined; cache_compare output unchanged",
            params={"old": old, "new": new},
            sources=["scripts/cache_report.py"]))
    # near-miss: add the new name but keep the old one as a deprecated alias
    cr = base["scripts/cache_report.py"]
    ref_cr = re.sub(r"\bbucket_of\b", "component_bucket", cr)
    ref_cr = sub_once(ref_cr, "import argparse\nimport json\nimport sys\n",
                      "import argparse\nimport json\nimport sys\nimport warnings\n")
    ref_cr = sub_once(ref_cr, "\n\ndef _num(", "\n\n" + D('''


        def bucket_of(line: dict) -> str:
            """Deprecated alias of component_bucket (kept for external notebooks)."""
            warnings.warn("bucket_of is deprecated; use component_bucket", DeprecationWarning,
                          stacklevel=2)
            return component_bucket(line)
    ''').rstrip("\n") + "\n\n\ndef _num(")
    ref_cc = sub_once(CACHE_COMPARE, "from cache_report import _num, bucket_of, find_logs",
                      "from cache_report import _num, component_bucket, find_logs")
    ref_cc = sub_once(ref_cc, "totals[bucket_of(line)]", "totals[component_bucket(line)]")
    nm_hidden = D('''
        import sys
        import warnings
        from pathlib import Path

        import pytest

        ROOT = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(ROOT / "scripts"))
        LINES = [{"component": "agent"}, {"component": "arbiter"}, {"component": "zzz"},
                 {"component": "acceptance", "request_type": "acceptance_arbitration"}]


        def test_new_name_does_not_warn():
            import cache_report
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                assert [cache_report.component_bucket(x) for x in LINES] == ["agent", "arbiter", "other", "arbiter"]
                cache_report.summarise(LINES)


        def test_old_name_still_works_but_warns():
            import cache_report
            for line in LINES:
                with pytest.warns(DeprecationWarning):
                    got = cache_report.bucket_of(line)
                assert got == cache_report.component_bucket(line)


        def test_compare_uses_new_name():
            import cache_compare
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                cache_compare.cost_by_bucket([str(ROOT / "fixtures" / "run_a")])
    ''')
    tasks.append(Task(
        family=fam, slug="alias_bucket_of", near_miss=True,
        goal=("We want `bucket_of` in scripts/cache_report.py to be called `component_bucket`, but "
              "external notebooks still import `bucket_of`. Make `component_bucket` the real function "
              "(used everywhere inside the project) and keep `bucket_of` as a deprecated alias that "
              "emits a DeprecationWarning and returns the same result."),
        base=base, hidden={"test_hidden_alias.py": nm_hidden},
        reference={"scripts/cache_report.py": ref_cr, "scripts/cache_compare.py": ref_cc},
        check="component_bucket works without warnings; bucket_of still exists and warns DeprecationWarning",
        near_miss_reason="a plain rename deletes bucket_of, which the request says must keep working",
        params={"old": "bucket_of", "new": "component_bucket"},
        sources=["scripts/cache_report.py"]))
    return tasks


# ── F08 add a config env var with default + doc line ──────────────────────────

CONFIG = D('''
    """Runtime settings read from AWOS_* environment variables."""
    from __future__ import annotations

    import os
    from dataclasses import dataclass
    from typing import Mapping


    def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
        raw = env.get(name)
        if raw is None or not raw.strip():
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on")


    def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
        try:
            return int(env.get(name, ""))
        except ValueError:
            return default


    def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
        try:
            return float(env.get(name, ""))
        except ValueError:
            return default


    def _env_str(env: Mapping[str, str], name: str, default: str) -> str:
        raw = env.get(name)
        return raw if raw else default


    @dataclass(frozen=True)
    class Settings:
        notebook: bool = True
        one_shot: bool = True
        goal_check: bool = False
        max_turns: int = 150
        max_cost_usd: float = 1.0
        model: str = "deepseek/deepseek-v4-flash"


    def load_settings(env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return Settings(
            notebook=_env_bool(env, "AWOS_NOTEBOOK", True),
            one_shot=_env_bool(env, "AWOS_ONE_SHOT", True),
            goal_check=_env_bool(env, "AWOS_GOAL_CHECK", False),
            max_turns=_env_int(env, "AWOS_MAX_TURNS", 150),
            max_cost_usd=_env_float(env, "AWOS_MAX_COST_USD", 1.0),
            model=_env_str(env, "AWOS_AGENT_MODEL", "deepseek/deepseek-v4-flash"),
        )
''')

CONFIG_DOC = D('''
    # Configuration

    AWOS reads these environment variables at start-up (`awos_cfg.config.load_settings`).
    Booleans accept 1/true/yes/on (case-insensitive); anything else is false. A malformed
    number falls back to the default.

    | Variable | Default | Meaning |
    |---|---|---|
    | `AWOS_NOTEBOOK` | `1` | Keep the per-project notebook between jobs |
    | `AWOS_ONE_SHOT` | `1` | Try a one-shot edit before the agent loop |
    | `AWOS_GOAL_CHECK` | `0` | Run the goal check after the agent reports done |
    | `AWOS_MAX_TURNS` | `150` | Agent turn cap per job |
    | `AWOS_MAX_COST_USD` | `1.0` | Spend cap per job in dollars |
    | `AWOS_AGENT_MODEL` | `deepseek/deepseek-v4-flash` | Model id for the agent loop |

    Flags not listed here are experimental and may disappear.
''')

F08_HIDDEN = D('''
    import dataclasses
    from pathlib import Path

    from awos_cfg.config import Settings, load_settings

    ROOT = Path(__file__).resolve().parents[1]
    VAR, FIELD, DEFAULT = @@VAR@@, @@FIELD@@, @@DEFAULT@@
    CASES = @@CASES@@               # [(raw env value, expected)]
    DOC_DEFAULTS = @@DOC_DEFAULTS@@  # accepted spellings of the default in the doc table


    def test_default():
        assert getattr(load_settings({}), FIELD) == DEFAULT
        assert getattr(Settings(), FIELD) == DEFAULT


    def test_env_override():
        for raw, want in CASES:
            assert getattr(load_settings({VAR: raw}), FIELD) == want, raw


    def test_existing_settings_unchanged():
        s = load_settings({})
        assert (s.notebook, s.one_shot, s.goal_check, s.max_turns, s.max_cost_usd) == (True, True, False, 150, 1.0)
        assert load_settings({"AWOS_MAX_TURNS": "20"}).max_turns == 20
        assert FIELD in {f.name for f in dataclasses.fields(Settings)}


    def test_documented():
        rows = [ln for ln in (ROOT / "docs" / "CONFIG.md").read_text(encoding="utf-8").splitlines()
                if ln.startswith("|") and f"`{VAR}`" in ln]
        assert len(rows) == 1, rows
        cells = [c.strip() for c in rows[0].strip().strip("|").split("|")]
        assert cells[1].strip("`") in DOC_DEFAULTS, cells
        assert len(cells) == 3 and cells[2], "needs a meaning"
''')

F08_CASES = [
    dict(var="AWOS_MAX_RETRIES", field="max_retries", typ="int", default=3, default_src="3",
         helper="_env_int", doc_defaults=["3"], meaning="Retries per job after an infrastructure failure",
         cases=[("5", 5), ("0", 0), ("lots", 3)], words="an int, default 3"),
    dict(var="AWOS_STABLE_PREFIX", field="stable_prefix", typ="bool", default=False, default_src="False",
         helper="_env_bool", doc_defaults=["0"], meaning="Keep the prompt prefix byte-stable for caching",
         cases=[("1", True), ("yes", True), ("0", False), ("", False)], words="a bool, default off (`0`)"),
    dict(var="AWOS_ARBITER_TIMEOUT_S", field="arbiter_timeout_s", typ="float", default=45.0,
         default_src="45.0", helper="_env_float", doc_defaults=["45", "45.0"],
         meaning="Seconds before the acceptance arbiter call is abandoned",
         cases=[("12.5", 12.5), ("90", 90.0), ("soon", 45.0)], words="a float in seconds, default 45"),
    dict(var="AWOS_TRACE_DIR", field="trace_dir", typ="str", default=".awos/traces",
         default_src='".awos/traces"', helper="_env_str", doc_defaults=[".awos/traces"],
         meaning="Where span traces are written",
         cases=[("/tmp/t", "/tmp/t"), ("", ".awos/traces")], words="a path string, default `.awos/traces`"),
]


def f08() -> list[Task]:
    fam = "f08_config_env_var"
    base = {"pytest.ini": PYTEST_INI, "awos_cfg/__init__.py": "", "awos_cfg/config.py": CONFIG,
            "docs/CONFIG.md": CONFIG_DOC,
            "tests/test_config.py": D('''
                from awos_cfg.config import load_settings


                def test_defaults_and_overrides():
                    assert load_settings({}).max_turns == 150
                    assert load_settings({"AWOS_NOTEBOOK": "0"}).notebook is False
                    assert load_settings({"AWOS_MAX_COST_USD": "x"}).max_cost_usd == 1.0
            ''')}
    tasks = []
    for i, c in enumerate(F08_CASES, 1):
        ref = sub_once(CONFIG, '    model: str = "deepseek/deepseek-v4-flash"\n',
                       f'    model: str = "deepseek/deepseek-v4-flash"\n'
                       f'    {c["field"]}: {c["typ"]} = {c["default_src"]}\n')
        ref = sub_once(ref, '        model=_env_str(env, "AWOS_AGENT_MODEL", "deepseek/deepseek-v4-flash"),\n',
                       '        model=_env_str(env, "AWOS_AGENT_MODEL", "deepseek/deepseek-v4-flash"),\n'
                       f'        {c["field"]}={c["helper"]}(env, "{c["var"]}", {c["default_src"]}),\n')
        doc = sub_once(CONFIG_DOC, "| `AWOS_AGENT_MODEL` | `deepseek/deepseek-v4-flash` | Model id for the agent loop |\n",
                       "| `AWOS_AGENT_MODEL` | `deepseek/deepseek-v4-flash` | Model id for the agent loop |\n"
                       f"| `{c['var']}` | `{c['doc_defaults'][0]}` | {c['meaning']} |\n")
        goal = (f"Add a new setting `{c['var']}` ({c['words']}) exposed as `Settings.{c['field']}` in "
                f"awos_cfg/config.py and parsed by `load_settings` the same way as the other "
                f"{c['typ']} settings. Document it as a row in the table in docs/CONFIG.md "
                f"(variable, default, meaning).")
        tasks.append(Task(
            family=fam, slug=c["field"], instance=i, goal=goal, base=base,
            hidden={f"test_hidden_env_{c['field']}.py": T(
                F08_HIDDEN, VAR=repr(c["var"]), FIELD=repr(c["field"]), DEFAULT=repr(c["default"]),
                CASES=repr(c["cases"]), DOC_DEFAULTS=repr(c["doc_defaults"]))},
            reference={"awos_cfg/config.py": ref, "docs/CONFIG.md": doc},
            check="default, env override parsing, existing settings unchanged, one doc row with the default",
            params={"var": c["var"], "field": c["field"], "type": c["typ"], "default": c["default"]}))
    nm_hidden = D('''
        import dataclasses
        from pathlib import Path

        from awos_cfg.config import Settings, load_settings

        ROOT = Path(__file__).resolve().parents[1]


        def test_notebook_off_by_default():
            assert load_settings({}).notebook is False
            assert Settings().notebook is False
            assert load_settings({"AWOS_NOTEBOOK": "1"}).notebook is True


        def test_no_new_setting():
            assert [f.name for f in dataclasses.fields(Settings)] == [
                "notebook", "one_shot", "goal_check", "max_turns", "max_cost_usd", "model"]
            rows = [ln for ln in (ROOT / "docs" / "CONFIG.md").read_text(encoding="utf-8").splitlines()
                    if ln.startswith("| `AWOS_")]
            assert len(rows) == 6


        def test_doc_default_updated():
            row = next(ln for ln in (ROOT / "docs" / "CONFIG.md").read_text(encoding="utf-8").splitlines()
                       if "`AWOS_NOTEBOOK`" in ln)
            assert [c.strip() for c in row.strip("|").split("|")][1].strip("`") == "0"
    ''')
    tasks.append(Task(
        family=fam, slug="notebook_default_off", near_miss=True,
        goal=("Make the notebook opt-in: `AWOS_NOTEBOOK` should default to off (`0`) instead of on. "
              "Update awos_cfg/config.py and the default shown in docs/CONFIG.md."),
        base=base, hidden={"test_hidden_notebook_default.py": nm_hidden},
        reference={
            "awos_cfg/config.py": sub_once(sub_once(CONFIG, "    notebook: bool = True\n",
                                                    "    notebook: bool = False\n"),
                                           'notebook=_env_bool(env, "AWOS_NOTEBOOK", True)',
                                           'notebook=_env_bool(env, "AWOS_NOTEBOOK", False)'),
            "docs/CONFIG.md": sub_once(CONFIG_DOC, "| `AWOS_NOTEBOOK` | `1` |", "| `AWOS_NOTEBOOK` | `0` |"),
        },
        check="AWOS_NOTEBOOK defaults to False in code and doc; no new setting or doc row",
        near_miss_reason="changes an existing variable's default; adding a new env var is wrong",
        params={"var": "AWOS_NOTEBOOK", "new_default": False}))
    return tasks


# ── F09 write a unit test for an existing pure function ──────────────────────

PURE = D('''
    """Pure helpers shared by the eval scripts (snapshots of scripts/job_series.py and
    scripts/cache_report.py functions)."""
    from __future__ import annotations

    import math
    from typing import Any


    def parse_jobs(spec: str | None, available: list[int]) -> list[int]:
        """'1-6,9' -> [1..6, 9], restricted to jobs that exist."""
        if not spec:
            return list(available)
        wanted: set[int] = set()
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                lo, hi = part.split("-", 1)
                wanted.update(range(int(lo), int(hi) + 1))
            else:
                wanted.add(int(part))
        return [n for n in available if n in wanted]


    def spread(xs: list[float]) -> dict:
        """mean, sample sd, min, max of per-repeat values (None values skipped)."""
        xs = [float(x) for x in xs if x is not None]
        if not xs:
            return {"n": 0, "mean": None, "sd": None, "min": None, "max": None}
        m = sum(xs) / len(xs)
        sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5 if len(xs) > 1 else 0.0
        return {"n": len(xs), "mean": round(m, 4), "sd": round(sd, 4),
                "min": round(min(xs), 4), "max": round(max(xs), 4)}


    def num_or_zero(value: Any) -> float:
        try:
            if value is None or isinstance(value, bool):
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0


    def bucket_of(line: dict) -> str:
        """The report bucket for one log line."""
        component = str(line.get("component") or "")
        if line.get("request_type") == "acceptance_arbitration" or component == "arbiter":
            return "arbiter"
        if component in ("one_shot", "agent", "acceptance"):
            return component
        return "other"


    def percentile(xs: list[float], q: float) -> float:
        """q-th percentile (0-100) by nearest rank. Raises ValueError on empty input."""
        if not xs:
            return 0.0
        s = sorted(xs)
        k = max(0, min(len(s) - 1, math.ceil(q / 100 * len(s)) - 1))
        return s[k]
''')

F09_HIDDEN = D('''
    import hashlib
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    import pytest

    ROOT = Path(__file__).resolve().parents[1]
    TEST = @@TEST@@
    MODULE = "awos_util/pure.py"
    MODULE_SHA = @@SHA@@
    MUTANTS = @@MUTANTS@@      # [(original snippet, mutated snippet)]


    def run_test(project):
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", TEST],
                           cwd=project, capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout[-2000:]


    def test_test_file_exists_and_passes():
        path = ROOT / TEST
        assert path.is_file(), f"missing {TEST}"
        assert path.read_text(encoding="utf-8").count("def test_") >= 2
        code, out = run_test(ROOT)
        assert code == 0, out


    def test_function_not_modified():
        assert hashlib.sha256((ROOT / MODULE).read_bytes()).hexdigest() == MODULE_SHA


    @pytest.mark.parametrize("i", range(len(MUTANTS)))
    def test_kills_mutant(i, tmp_path):
        assert (ROOT / TEST).is_file(), f"missing {TEST}"
        proj = tmp_path / "p"
        shutil.copytree(ROOT, proj, ignore=shutil.ignore_patterns(".git", "__pycache__", "test_hidden_*"))
        src = (proj / MODULE).read_text(encoding="utf-8")
        old, new = MUTANTS[i]
        assert src.count(old) == 1
        (proj / MODULE).write_text(src.replace(old, new), encoding="utf-8")
        code, out = run_test(proj)
        assert code != 0, f"the test passes on mutant {i}: {new!r}"
''')

F09_CASES = [
    dict(fn="parse_jobs",
         cover=("a range like '1-3', a comma list like '1,4', numbers not in `available` being dropped, "
                "and an empty or None spec returning all of `available`"),
         mutants=[("        return list(available)\n", "        return []\n"),
                  ("range(int(lo), int(hi) + 1)", "range(int(lo), int(hi))"),
                  ("    return [n for n in available if n in wanted]", "    return sorted(wanted)")],
         ref=D('''
             from awos_util.pure import parse_jobs


             def test_range_is_inclusive():
                 assert parse_jobs("1-3", [1, 2, 3, 4]) == [1, 2, 3]


             def test_comma_list():
                 assert parse_jobs("1,4", [1, 2, 3, 4]) == [1, 4]


             def test_unknown_numbers_dropped():
                 assert parse_jobs("2,9", [1, 2, 3]) == [2]


             def test_empty_spec_returns_all():
                 assert parse_jobs("", [1, 2]) == [1, 2]
                 assert parse_jobs(None, [3]) == [3]
         ''')),
    dict(fn="spread",
         cover=("the sample standard deviation (n-1) on a small list like [1, 2, 3], None values being "
                "skipped, and an empty input giving n=0 with every other value None"),
         mutants=[("/ (len(xs) - 1)) ** 0.5", "/ len(xs)) ** 0.5"),
                  ("xs = [float(x) for x in xs if x is not None]", "xs = [float(x) for x in xs]"),
                  ('return {"n": 0, "mean": None,', 'return {"n": 0, "mean": 0.0,')],
         ref=D('''
             from awos_util.pure import spread


             def test_sample_sd():
                 s = spread([1, 2, 3])
                 assert s["n"] == 3 and s["mean"] == 2.0 and s["sd"] == 1.0
                 assert s["min"] == 1.0 and s["max"] == 3.0


             def test_none_skipped():
                 assert spread([None, 2, None, 4])["n"] == 2


             def test_empty():
                 assert spread([]) == {"n": 0, "mean": None, "sd": None, "min": None, "max": None}
         ''')),
    dict(fn="num_or_zero",
         cover=("numbers and numeric strings ('2.5' -> 2.5), None -> 0.0, booleans -> 0.0 "
                "(True is not 1 here), and junk like 'abc' -> 0.0"),
         mutants=[("if value is None or isinstance(value, bool):", "if value is None:"),
                  ("        return float(value)\n", "        return float(int(value))\n"),
                  ("    except (TypeError, ValueError):\n        return 0.0",
                   "    except (TypeError, ValueError):\n        return -1.0")],
         ref=D('''
             from awos_util.pure import num_or_zero


             def test_numbers_and_strings():
                 assert num_or_zero(3) == 3.0
                 assert num_or_zero("2.5") == 2.5


             def test_none_and_bools():
                 assert num_or_zero(None) == 0.0
                 assert num_or_zero(True) == 0.0 and num_or_zero(False) == 0.0


             def test_junk():
                 assert num_or_zero("abc") == 0.0
                 assert num_or_zero([1]) == 0.0
         ''')),
    dict(fn="bucket_of",
         cover=("known components mapping to themselves (one_shot, agent, acceptance), the arbiter "
                "(component 'arbiter', or request_type 'acceptance_arbitration' even when the component "
                "is 'acceptance'), and anything else mapping to 'other'"),
         mutants=[('if line.get("request_type") == "acceptance_arbitration" or component == "arbiter":',
                   'if component == "arbiter":'),
                  ('if component in ("one_shot", "agent", "acceptance"):',
                   'if component in ("one_shot", "agent"):'),
                  ('    return "other"\n\n\ndef percentile', '    return component or "other"\n\n\ndef percentile')],
         ref=D('''
             from awos_util.pure import bucket_of


             def test_known_components():
                 for c in ("one_shot", "agent", "acceptance"):
                     assert bucket_of({"component": c}) == c


             def test_arbiter():
                 assert bucket_of({"component": "arbiter"}) == "arbiter"
                 assert bucket_of({"component": "acceptance", "request_type": "acceptance_arbitration"}) == "arbiter"


             def test_other():
                 assert bucket_of({"component": "planner"}) == "other"
                 assert bucket_of({}) == "other"
         ''')),
]


def f09() -> list[Task]:
    import hashlib
    fam = "f09_unit_test_pure_fn"
    sha = hashlib.sha256(PURE.encode()).hexdigest()
    base = {"pytest.ini": PYTEST_INI, "awos_util/__init__.py": "", "awos_util/pure.py": PURE,
            "tests/test_pure_smoke.py": D('''
                from awos_util import pure


                def test_module_imports():
                    assert callable(pure.parse_jobs) and callable(pure.spread)
            ''')}
    tasks = []
    for i, c in enumerate(F09_CASES, 1):
        test_path = f"tests/test_{c['fn']}.py"
        for old, _new in c["mutants"]:
            assert PURE.count(old) == 1, (c["fn"], old)
        goal = (f"Write unit tests for `{c['fn']}` in awos_util/pure.py, in a new file `{test_path}`. "
                f"Cover {c['cover']}. Don't change the function itself.")
        tasks.append(Task(
            family=fam, slug=f"test_{c['fn']}", instance=i, goal=goal, base=base,
            hidden={f"test_hidden_tests_for_{c['fn']}.py": T(
                F09_HIDDEN, TEST=repr(test_path), SHA=repr(sha), MUTANTS=repr(c["mutants"]))},
            reference={test_path: c["ref"]},
            check="new test file passes on HEAD, function unchanged, and fails on each of 3 planted mutants",
            params={"function": c["fn"], "test_file": test_path, "mutants": len(c["mutants"])},
            sources=["scripts/job_series.py (parse_jobs, _spread)", "scripts/cache_report.py (_num, bucket_of)"]))
    fixed = sub_once(PURE, "    if not xs:\n        return 0.0\n    s = sorted(xs)\n    k = max(",
                     '    if not xs:\n        raise ValueError("percentile of an empty list")\n'
                     "    s = sorted(xs)\n    k = max(")
    nm_hidden = D('''
        import subprocess
        import sys
        from pathlib import Path

        import pytest

        from awos_util.pure import percentile

        ROOT = Path(__file__).resolve().parents[1]


        def test_empty_raises():
            with pytest.raises(ValueError):
                percentile([], 50)


        def test_nearest_rank_unchanged():
            assert percentile([1, 2, 3, 4], 50) == 2
            assert percentile([5, 1, 3], 100) == 5
            assert percentile([5, 1, 3], 0) == 1


        def test_regression_test_added_and_green():
            path = ROOT / "tests" / "test_percentile.py"
            assert path.is_file()
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                "tests/test_percentile.py"], cwd=ROOT, capture_output=True, text=True)
            assert r.returncode == 0, r.stdout[-1500:]
    ''')
    tasks.append(Task(
        family=fam, slug="percentile_empty_bug", near_miss=True,
        goal=("Add tests/test_percentile.py with a regression test that `percentile([], 50)` in "
              "awos_util/pure.py raises ValueError, as its docstring promises, plus a nearest-rank "
              "case. The new tests must pass."),
        base=base, hidden={"test_hidden_percentile.py": nm_hidden},
        reference={"awos_util/pure.py": fixed, "tests/test_percentile.py": D('''
            import pytest

            from awos_util.pure import percentile


            def test_empty_raises():
                with pytest.raises(ValueError):
                    percentile([], 50)


            def test_nearest_rank():
                assert percentile([1, 2, 3, 4], 50) == 2
        ''')},
        check="percentile([]) raises ValueError; nearest-rank behaviour unchanged; new test file green",
        near_miss_reason="the function violates its docstring; a test-only routine cannot make the test pass",
        params={"function": "percentile"}))
    return tasks


# ── F10 update a spec doc section from a JSON summary ─────────────────────────

F10_HIDDEN = D('''
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]
    DOC = ROOT / @@DOC@@
    EXPECTED = @@EXPECTED@@     # non-blank lines of the Results section, in order
    BEFORE, AFTER = @@BEFORE@@, @@AFTER@@


    def split(text):
        head, rest = text.split("## Results\\n", 1)
        body, sep, tail = rest.partition("\\n## ")
        return head, body, sep + tail


    def test_results_section():
        _, body, _ = split(DOC.read_text(encoding="utf-8"))
        assert [ln.rstrip() for ln in body.splitlines() if ln.strip()] == EXPECTED


    def test_other_sections_untouched():
        head, _, tail = split(DOC.read_text(encoding="utf-8"))
        assert head == BEFORE
        assert tail.strip() == AFTER.strip()
''')

F10_CASES = [
    dict(slug="e1_edit_reliability", title="E1 — edit-reliability package A/B",
         summary={"run": "20261006T0912", "label": "INCONCLUSIVE", "p_delta_positive": 0.81,
                  "arms": {"off": {"solved": 41, "runs": 146, "cost_usd": 4.38},
                           "on": {"solved": 47, "runs": 146, "cost_usd": 4.964}}},
         hypothesis="Fuzzy apply + edit grammar raise hidden-test solves at equal cost."),
    dict(slug="t7b_stable_prefix", title="T7b — stable prompt prefix",
         summary={"run": "20261003T2210", "label": "KEEP", "p_delta_positive": 0.97,
                  "arms": {"prefix_off": {"solved": 30, "runs": 56, "cost_usd": 2.24},
                           "prefix_on": {"solved": 31, "runs": 56, "cost_usd": 1.512}}},
         hypothesis="A byte-stable prefix raises cache hits and lowers $/run without hurting solves."),
    dict(slug="one_shot_default", title="One-shot default",
         summary={"run": "20261001T0805", "label": "KEEP", "p_delta_positive": 0.99,
                  "arms": {"agent": {"solved": 33, "runs": 66, "cost_usd": 6.6},
                           "one_shot": {"solved": 35, "runs": 66, "cost_usd": 1.98},
                           "aider": {"solved": 29, "runs": 66, "cost_usd": 5.28}}},
         hypothesis="Trying a one-shot edit first solves as much for a fraction of the cost."),
    dict(slug="loop_breaker", title="Loop breaker",
         summary={"run": "20260928T1530", "label": "REJECT", "p_delta_positive": 0.12,
                  "arms": {"off": {"solved": 20, "runs": 28, "cost_usd": 1.4},
                           "on": {"solved": 18, "runs": 28, "cost_usd": 1.26}}},
         hypothesis="Stopping no-progress loops early saves money without losing solves."),
]


def _f10_doc(c) -> tuple[str, str, str]:
    arms = ", ".join(c["summary"]["arms"])
    before = (f"# {c['title']}\n\nStatus: run complete.\n\n## Hypothesis\n\n{c['hypothesis']}\n\n"
              f"## Arms\n\n{arms} (paired, same tasks, same pinned model).\n\n")
    after = ("\n## Notes\n\nDecision rule: docs/specs/stats_decision_rule.md. "
             "Numbers come from the run's combined summary JSON.\n")
    stale = "_Pending: fill in from the run summary once the run finishes._\n"
    return before, after, f"{before}## Results\n\n{stale}{after}"


def _f10_lines(s: dict) -> list[str]:
    out = [f"Run `{s['run']}`."]
    for arm, v in s["arms"].items():
        out.append(f"- {arm}: {v['solved']}/{v['runs']} solved, ${v['cost_usd'] / v['runs']:.3f} per run")
    out.append(f"Decision: {s['label']} (P(delta>0) = {s['p_delta_positive']:.2f})")
    return out


def f10() -> list[Task]:
    fam = "f10_spec_section_from_json"
    tasks = []
    for i, c in enumerate(F10_CASES, 1):
        doc_path = f"docs/specs/{c['slug']}.md"
        json_path = f"reports/{c['slug']}_summary.json"
        before, after, doc = _f10_doc(c)
        lines = _f10_lines(c["summary"])
        base = {"pytest.ini": PYTEST_INI, doc_path: doc,
                json_path: json.dumps(c["summary"], indent=2) + "\n"}
        goal = (f"Fill in the `## Results` section of {doc_path} from {json_path}. The section should "
                f"read: a line ``Run `<run>`.``; then one bullet per arm in the JSON's order, "
                f"`- <arm>: <solved>/<runs> solved, $<cost_usd / runs, 3 decimals> per run`; then "
                f"`Decision: <label> (P(delta>0) = <p_delta_positive, 2 decimals>)`. Replace the "
                f"pending placeholder; leave every other section exactly as it is.")
        tasks.append(Task(
            family=fam, slug=c["slug"], instance=i, goal=goal, base=base,
            hidden={f"test_hidden_results_{c['slug']}.py": T(
                F10_HIDDEN, DOC=repr(doc_path), EXPECTED=repr(lines), BEFORE=repr(before),
                AFTER=repr(after))},
            reference={doc_path: before + "## Results\n\n" + "\n".join(lines) + "\n" + after},
            check="Results section lines equal the JSON-derived lines; all other sections untouched",
            params={"doc": doc_path, "summary": json_path, "arms": list(c["summary"]["arms"])}))
    return tasks


FAMILIES = [f06, f07, f08, f09, f10]
