"""Tests for the R40 routine-task builder (scripts/routines/build_r40.py).

Checks the set's shape and labels, that the committed manifest matches the
builder, that built series pass job_series validation, and that every near-miss
is NOT solved by applying its family's routine. No model calls."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "routines"))

import build_r40
import r40_families_a

_spec = importlib.util.spec_from_file_location("job_series_r40", REPO / "scripts" / "job_series.py")
js = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(js)


@pytest.fixture(scope="module")
def tasks():
    return build_r40.all_tasks()


@pytest.fixture(scope="module")
def by_series(tasks):
    return {t.series: t for t in tasks}


def test_shape_and_labels(tasks):
    assert len(tasks) == 48
    fams = {t.family for t in tasks}
    assert len(fams) == 10 and fams == set(build_r40.FAMILY_KIND)
    for fam in fams:
        inst = sorted(t.instance for t in tasks if t.family == fam and not t.near_miss)
        assert inst == [1, 2, 3, 4], fam
    nms = [t for t in tasks if t.near_miss]
    assert len(nms) == 8
    assert all(t.near_miss_reason and t.instance is None for t in nms)
    assert len({t.series for t in tasks}) == 48
    for t in tasks:
        assert t.series.startswith("r40_") and t.goal and t.hidden and t.reference and t.base
        assert all(name.startswith("test_hidden_") for name in t.hidden)
        assert not set(t.hidden) & {Path(p).name for p in t.base}


def test_committed_manifest_matches_builder(tasks):
    committed = json.loads(build_r40.BRANCH_MANIFEST.read_text(encoding="utf-8"))
    assert committed == build_r40.build_manifest(tasks), \
        "run: python scripts/routines/build_r40.py --out <dir> to refresh r40_manifest.json"
    assert committed["counts"] == {"tasks": 48, "families": 10, "instances": 40, "near_misses": 8}


def _check(task, out: Path) -> dict:
    build_r40.write_series(task, out)
    sdir = out / task.series
    jobs = js.discover_jobs(sdir)
    assert [n for n, _ in jobs] == [1]
    return js.check_job(sdir, jobs, 1, jobs[0][1])


@pytest.mark.parametrize("series", ["r40_f02_i1_to_0_9_3", "r40_f06_nm_json_from_table",
                                    "r40_f10_i3_one_shot_default"])
def test_built_series_validate(series, by_series, tmp_path):
    c = _check(by_series[series], tmp_path)
    assert c["ok"], c["problems"]


def _misapplied(series: str, by_series: dict) -> dict[str, str]:
    """Files the *family* routine would produce on a near-miss's start state."""
    t = by_series[series]
    if series == "r40_f01_nm_bench_not_tests":          # writes a pytest report instead
        return by_series["r40_f01_i1_report_stats"].reference
    if series == "r40_f02_nm_bump_requests_dep":        # cuts a release instead
        return by_series["r40_f02_i1_to_0_9_3"].reference
    if series == "r40_f03_nm_limit_ignored":            # adds the flag (again)
        tool = t.base["scripts/series_tool.py"]
        return {"scripts/series_tool.py": r40_families_a.F03_CASES["limit"]["ref"](tool)}
    if series == "r40_f05_nm_logic_not_lint":           # lint fix: nothing to change
        return {}
    if series == "r40_f06_nm_json_from_table":          # regenerates the table from stale JSON
        doc = t.base["docs/RESULTS.md"]
        data = json.loads(t.base["reports/e1_summary.json"])
        for arm, v in data["arms"].items():
            row = next(ln for ln in doc.splitlines() if ln.startswith(f"| {arm} |"))
            doc = doc.replace(row, f"| {arm} | {v['solved']} | {v['runs']} |")
        return {"docs/RESULTS.md": doc}
    if series == "r40_f07_nm_alias_bucket_of":          # plain rename
        return by_series["r40_f07_i2_bucket_of_to_component_bucket"].reference
    if series == "r40_f08_nm_notebook_default_off":     # adds a new env var
        return by_series["r40_f08_i1_max_retries"].reference
    if series == "r40_f09_nm_percentile_empty_bug":     # writes the test only
        return {"tests/test_percentile.py": t.reference["tests/test_percentile.py"]}
    raise AssertionError(f"no misapplication for {series}")


NEAR_MISSES = ["r40_f01_nm_bench_not_tests", "r40_f02_nm_bump_requests_dep",
               "r40_f03_nm_limit_ignored", "r40_f05_nm_logic_not_lint",
               "r40_f06_nm_json_from_table", "r40_f07_nm_alias_bucket_of",
               "r40_f08_nm_notebook_default_off", "r40_f09_nm_percentile_empty_bug"]


def test_near_miss_list_complete(tasks):
    assert sorted(t.series for t in tasks if t.near_miss) == sorted(NEAR_MISSES)


@pytest.mark.parametrize("series", NEAR_MISSES)
def test_family_routine_does_not_solve_near_miss(series, by_series, tmp_path):
    t = by_series[series]
    build_r40.write_series(t, tmp_path)
    sdir = tmp_path / series
    jobs = js.discover_jobs(sdir)
    project = js.materialize(sdir, jobs, 1, tmp_path / "proj")
    for rel, text in _misapplied(series, by_series).items():
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    hidden, _visible = js.judge(jobs[0][1], project)
    assert not hidden["ok"], f"family routine solved near-miss {series}: {hidden['summary']}"
