"""Every number must match what the original implementation produced."""

import json
import os

import pytest

from ledger.fixtures import generate_ledger
from ledger.report import render_text
from ledger.summary import build_monthly_summary

with open(os.path.join(os.path.dirname(__file__), "goldens.json")) as handle:
    GOLDENS = json.load(handle)

_BUNDLES = {}


def _bundle(seed, size):
    key = (seed, size)
    if key not in _BUNDLES:
        _BUNDLES[key] = generate_ledger(seed, size)
    return _BUNDLES[key]


def _summary(case):
    bundle = _bundle(case["seed"], case["size"])
    account = bundle.account_id if case["account"] == "primary" else bundle.joint_account_id
    return build_monthly_summary(bundle.store, bundle.fx, bundle.categoriser,
                                 account, case["year"], case["month"])


@pytest.mark.parametrize("name", sorted(GOLDENS["cases"]))
def test_summary_matches_golden(name):
    case = GOLDENS["cases"][name]
    got = _summary(case).to_dict()
    expected = case["summary"]
    # Compare field by field first for a readable failure, then the whole dict.
    for key in expected:
        assert got.get(key) == expected[key], f"{name}: field {key!r} differs"
    assert got == expected


@pytest.mark.parametrize("name", sorted(GOLDENS["reports"]))
def test_text_report_matches_golden(name):
    case = GOLDENS["cases"][name]
    assert render_text(_summary(case), show_daily=True) == GOLDENS["reports"][name]
