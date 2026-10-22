"""Browser tests for the MiniWoB++ shim (real Chromium via Playwright, real upstream HTML).

Run:  AWOS_CHROMIUM=<chromium or headless-shell path, optional> \
      ~/.awos/rl-venv/bin/python -m pytest -m integration tests/rl/test_miniwob_integration.py
Needs: python playwright + a Chromium, and the pinned MiniWoB cache
(scripts/rl/fetch_miniwob.sh). Skips cleanly when either is missing."""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(180)]

pw = pytest.importorskip("playwright.sync_api")

from scaffold.rl.miniwob import catalog  # noqa: E402
from scaffold.rl.miniwob.source import default_html_root, load_utterances  # noqa: E402

if not (default_html_root() / "core" / "core.js").is_file():
    pytest.skip("MiniWoB cache missing: run scripts/rl/fetch_miniwob.sh", allow_module_level=True)


@pytest.mark.parametrize("type_name", ["click-button", "click-link", "enter-text", "click-checkboxes"])
def test_oracle_solves_through_real_page(type_name):
    from scaffold.rl.miniwob.render import run_oracle
    for seed in (0, 1):
        r = run_oracle(type_name, seed)
        assert r["success"], (type_name, seed, r)
        assert r["unsafe"] == []


def test_utterances_match_frozen_file():
    from scaffold.rl.miniwob.render import compute_utterances
    types = ["click-button", "book-flight", "email-inbox-nl-turk", "login-user"]
    live = compute_utterances(types, [0, 3])
    frozen = load_utterances()
    for k, v in live.items():
        assert v == frozen[k], k


def test_wrong_action_scores_failure():
    from playwright.sync_api import sync_playwright
    from scaffold.rl.miniwob.render import _launch, _wait_started
    from scaffold.rl.miniwob.source import MiniWobSource
    src = MiniWobSource(seeds=[0], types=["click-button"])
    spec = src.task("click-button", 0)
    src.start()
    try:
        with sync_playwright() as p:
            b = _launch(p)
            page = b.new_page()
            page.goto(src.reset(spec), wait_until="load")
            _wait_started(page)
            page.get_by_role("button", name="next").first.click()
            sc = src.outcome(spec)
            # after the first end, the page is covered: a second (right) click cannot re-score
            assert page.locator("#sync-task-cover").inner_text() == "Episode finished."
            b.close()
    finally:
        src.stop()
    assert not sc.success and "raw_reward=-1" in sc.notes


def test_icon_controls_exposed_by_aria_patch():
    from scaffold.rl.miniwob.render import aria_snapshot
    snap = aria_snapshot("social-media", 0)
    for name in ("Reply", "Retweet", "Like", "More"):
        assert f'button "{name}"' in snap
    assert 'link "' in aria_snapshot("click-link", 0)
    assert catalog.PINNED_SHA in str(default_html_root())
