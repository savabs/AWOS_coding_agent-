#!/usr/bin/env python3
"""
Headless browser E2E test for AWOS GUI.
Uses Playwright to click, type, and capture console errors.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GUI_URL = "http://127.0.0.1:8765"


def run_gui_e2e() -> dict:
    from playwright.sync_api import sync_playwright

    results: dict = {
        "url": GUI_URL,
        "passed": [],
        "failed": [],
        "console_errors": [],
        "network_failures": [],
        "screenshots": {},
    }

    with sync_playwright() as p:
        # Prefer system Chrome — avoids long chromium download
        try:
            browser = p.chromium.launch(headless=True, channel="chrome")
        except Exception:
            browser = p.chromium.launch(headless=True)

        page = browser.new_page()

        page.on("console", lambda msg: (
            results["console_errors"].append(f"[{msg.type}] {msg.text}")
            if msg.type in ("error", "warning")
            else None
        ))
        page.on("pageerror", lambda err: results["console_errors"].append(f"[pageerror] {err}"))

        def on_response(resp):
            if resp.status >= 400 and GUI_URL in resp.url:
                results["network_failures"].append(f"{resp.status} {resp.url}")

        page.on("response", on_response)

        def check(name: str, ok: bool, detail: str = ""):
            entry = {"name": name, "detail": detail}
            (results["passed"] if ok else results["failed"]).append(entry)

        # 1. Page load
        try:
            resp = page.goto(GUI_URL, wait_until="networkidle", timeout=15000)
            check("page_load", resp is not None and resp.ok, f"status={resp.status if resp else None}")
        except Exception as exc:
            check("page_load", False, str(exc))
            browser.close()
            return results

        # 2. Branding visible
        brand = page.locator(".brand")
        check("brand_visible", brand.is_visible(), brand.inner_text())

        # 3. Tabs exist
        chat_tab = page.locator("#tab-chat")
        runs_tab = page.locator("#tab-runs")
        check("chat_tab", chat_tab.is_visible())
        check("runs_tab", runs_tab.is_visible())

        # 4. Mode cards / picker loaded (JS initChat)
        page.wait_for_timeout(1500)
        mode_picker = page.locator("#mode-picker .mode-option")
        mode_cards = page.locator("#mode-cards .mode-card")
        picker_count = mode_picker.count()
        cards_count = mode_cards.count()
        check(
            "modes_rendered",
            picker_count >= 3 or cards_count >= 3,
            f"picker={picker_count} cards={cards_count}",
        )

        # 5. Chat input + send button
        chat_input = page.locator("#chat-input")
        send_btn = page.locator("#btn-send")
        check("chat_input", chat_input.is_visible())
        check("send_button", send_btn.is_visible())

        # 6. Send Q/A message with streaming (cached short reply)
        chat_input.fill("say pong only")
        send_btn.click()
        try:
            page.wait_for_selector("#stream-body", timeout=5000)
            page.wait_for_function(
                "() => (document.querySelector('#stream-body')?.textContent || '').length > 0",
                timeout=60000,
            )
            page.wait_for_selector(".chat-msg.assistant .msg-body:not(.streaming)", timeout=60000)
            msgs = page.locator(".chat-msg").count()
            check("chat_stream_reply", msgs >= 2, f"messages={msgs}")
        except Exception as exc:
            check("chat_stream_reply", False, str(exc))
            err_el = page.locator(".chat-msg.error")
            if err_el.count():
                check("chat_error_detail", False, err_el.first.inner_text())

        # Screenshot chat
        shot_dir = ROOT / ".awos" / "gui_test"
        shot_dir.mkdir(parents=True, exist_ok=True)
        chat_shot = shot_dir / "chat.png"
        page.screenshot(path=str(chat_shot), full_page=True)
        results["screenshots"]["chat"] = str(chat_shot)

        # 7. Runs tab
        runs_tab.click()
        page.wait_for_timeout(1000)
        view_runs = page.locator("#view-runs.active")
        check("runs_view_active", view_runs.count() > 0)

        sessions = page.locator("#sessions .session-item")
        session_count = sessions.count()
        check("runs_list", session_count > 0, f"sessions={session_count}")

        if session_count > 0:
            sessions.first.click()
            page.wait_for_timeout(1500)
            files = page.locator("#files .file-item")
            check("file_list", files.count() > 0, f"files={files.count()}")
            if files.count() > 0:
                files.first.click()
                page.wait_for_timeout(500)
                diff_body = page.locator("#diff .diff-body, #diff .diff-line")
                check("diff_panel", diff_body.count() > 0, f"diff_lines={diff_body.count()}")

        runs_shot = shot_dir / "runs.png"
        page.screenshot(path=str(runs_shot), full_page=True)
        results["screenshots"]["runs"] = str(runs_shot)

        browser.close()

    return results


def main() -> int:
    print(f"Testing AWOS GUI at {GUI_URL} ...\n")
    try:
        results = run_gui_e2e()
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
        return 1
    except Exception as exc:
        print(f"FATAL: {exc}")
        return 1

    for item in results["passed"]:
        print(f"  PASS  {item['name']}" + (f" — {item['detail']}" if item.get("detail") else ""))
    for item in results["failed"]:
        print(f"  FAIL  {item['name']}" + (f" — {item['detail']}" if item.get("detail") else ""))

    if results["console_errors"]:
        print("\nConsole errors/warnings:")
        for e in results["console_errors"][:20]:
            print(f"  {e}")

    if results["network_failures"]:
        print("\nNetwork failures:")
        for e in results["network_failures"]:
            print(f"  {e}")

    if results.get("screenshots"):
        print("\nScreenshots:")
        for k, v in results["screenshots"].items():
            print(f"  {k}: {v}")

    out = ROOT / ".awos" / "gui_test" / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nFull results: {out}")

    return 1 if results["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
