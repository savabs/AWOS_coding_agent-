"""
render.py — offline helpers that need a real browser (Playwright Chromium):

  * precompute the exact utterance of every (type, seed) -> data/utterances.json
  * dump the accessibility snapshot of a task page (to audit the inclusion list)
  * a tiny self-check that drives the oracle types through the real page

Not used at episode time: the harness owns the browser. Playwright is imported lazily so
the package imports without it. AWOS_CHROMIUM may point at a Chromium executable.
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, Iterable, List, Optional, Sequence

from . import catalog
from .source import MiniWobSource


def _launch(pw):
    exe = os.environ.get("AWOS_CHROMIUM") or None
    return pw.chromium.launch(headless=True, executable_path=exe)


def _wait_started(page, timeout_s: float = 10.0) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st = page.evaluate("() => window.AWOS_MINIWOB ? window.AWOS_MINIWOB.utterance : null")
        if st is not None:
            return st
        time.sleep(0.05)
    raise TimeoutError("shim did not start the episode")


def compute_utterances(types: Iterable[str], seeds: Sequence[int],
                       progress: bool = False) -> Dict[str, str]:
    from playwright.sync_api import sync_playwright

    src = MiniWobSource(seeds=seeds, types=list(types), utterances={})
    src.start()
    out: Dict[str, str] = {}
    try:
        with sync_playwright() as pw:
            browser = _launch(pw)
            for spec in src.tasks():
                url = src.reset(spec)
                t0 = time.time()
                page = browser.new_page()      # fresh page: no state carried between tasks
                page.goto(url, wait_until="load")
                utt = _wait_started(page)
                page.close()
                rec = src.episode(spec) or {}
                live = ((rec.get("start") or {}).get("utterance") or "").strip()
                key = f"{spec.meta['type']}/{spec.meta['seed']}"
                out[key] = (utt or live).strip()
                if progress:
                    print(f"{time.time() - t0:5.2f}s {spec.id} | {out[key][:100]}", flush=True)
            browser.close()
    finally:
        src.stop()
    return out


def aria_snapshot(type_name: str, seed: int = 0, aria_patch: bool = True) -> str:
    from playwright.sync_api import sync_playwright

    src = MiniWobSource(seeds=[seed], types=[type_name], aria_patch=aria_patch, utterances={})
    src.start()
    try:
        with sync_playwright() as pw:
            browser = _launch(pw)
            page = browser.new_page()
            page.goto(src.reset(src.task(type_name, seed)), wait_until="load")
            _wait_started(page)
            time.sleep(0.3)
            snap = page.locator("body").aria_snapshot()
            browser.close()
            return snap
    finally:
        src.stop()


def run_oracle(type_name: str, seed: int) -> dict:
    """Drive the oracle with Playwright role locators (a stand-in for the AWOS harness)
    and return the Score as a dict. Used by the integration test."""
    from playwright.sync_api import sync_playwright

    src = MiniWobSource(seeds=[seed], types=[type_name])
    spec = src.task(type_name, seed)
    if not spec.meta["utterance"]:
        spec.meta["utterance"] = compute_utterances([type_name], [seed])[f"{type_name}/{seed}"]
    steps = src.oracle(spec)
    src.start()
    try:
        with sync_playwright() as pw:
            browser = _launch(pw)
            page = browser.new_page()
            page.goto(src.reset(spec), wait_until="load")
            _wait_started(page)
            for st in steps:
                if st["op"] == "done":
                    break
                if st.get("name"):
                    loc = page.get_by_role(st["role"], name=st["name"], exact=True).first
                else:
                    loc = page.get_by_role(st["role"]).first
                if st["op"] == "click":
                    loc.click()
                elif st["op"] == "fill":
                    loc.fill(st["value"])
            score = src.outcome(spec, None)
            browser.close()
    finally:
        src.stop()
    d = score.to_dict()
    d["steps"] = steps
    return d


def write_utterances(path, utterances: Dict[str, str]) -> None:
    payload = {"miniwob_sha": catalog.PINNED_SHA, "n": len(utterances),
               "utterances": dict(sorted(utterances.items()))}
    with open(path, "w") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
        f.write("\n")
