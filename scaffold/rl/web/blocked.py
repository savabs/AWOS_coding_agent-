"""
blocked.py — is this live page a bot wall / CAPTCHA / broken load? (infrastructure, not skill)

    b = detect_block(page, dom_markers=[...], start=True)  # -> None | {"kind", "evidence", "url", "status"}

Kinds (reported separately in the summary; they still count as failures in the score):
    cloudflare   Cloudflare / similar interstitial ("Just a moment...", challenge form)
    captcha      a CAPTCHA / human check fills the page (reCAPTCHA, hCaptcha, press-and-hold, ...)
    bot_wall     "Access denied" / "Pardon our interruption" / "you have been blocked" pages
    http_<code>  403 / 429 anywhere, any >= 400 on the start page (the site did not load)
    empty_page   the start page rendered nothing (no title, text or controls)
    load_failed  the start URL could not be opened at all (DNS, TLS, timeout); set by the episode

Never solved, never bypassed: the episode records it and stops. Text signals only count on
pages that look like an interstitial (title / main heading match, or a short page), so a
normal page that mentions "captcha" in its footer is not a block. DOM markers come from
DOM_PROBE_JS (challenge forms, challenge iframes).
"""
from __future__ import annotations

import re
from typing import List, Optional

_CLOUDFLARE = re.compile(r"(just a moment\.\.\.|checking (if the site connection is secure|your browser)|"
                         r"attention required!? \| cloudflare|performing security verification|"
                         r"enable javascript and cookies to continue|ddos protection by|"
                         r"verify you are human by completing)", re.I)
_CAPTCHA = re.compile(r"(\b(re)?captcha\b|\bhcaptcha\b|i'?m not a robot|are you a (human|robot)|"
                      r"verify (that )?you are (a )?human|press (and|&) hold|"
                      r"prove you('re| are) (not a robot|human)|unusual traffic from your (computer|network)|"
                      r"complete the security check)", re.I)
_BOT_WALL = re.compile(r"(^access denied\b|pardon our interruption|request (unsuccessful|blocked)|"
                       r"you have been blocked|you('ve| have) been (temporarily )?blocked|"
                       r"automated (access|queries|requests)|bot (detection|protection)|incapsula incident|"
                       r"perimeterx|datadome|are you a bot\b|access to this page has been denied)", re.I)

#: run in the page: challenge markers that only bot walls have (not the invisible reCAPTCHA badge)
DOM_PROBE_JS = r"""(() => {
  const q = s => { try { return !!document.querySelector(s); } catch (e) { return false; } };
  const out = [];
  if (q('#challenge-form, #challenge-running, #cf-challenge-running, #challenge-stage, .cf-turnstile-wrapper')) out.push('cloudflare');
  if (q('iframe[src*="challenges.cloudflare.com"]')) out.push('cloudflare');
  if (q('#px-captcha, #captcha-container, iframe[src*="geo.captcha-delivery.com"], iframe[src*="captcha-delivery"]')) out.push('captcha');
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 60 && r.height > 60; };
  for (const f of document.querySelectorAll('iframe[src*="recaptcha/api2/anchor"], iframe[src*="recaptcha/enterprise/anchor"], iframe[src*="hcaptcha.com"]')) {
    if (vis(f)) { out.push('captcha_widget'); break; }
  }
  return out;
})()"""

SHORT_PAGE = 1500          # chars of page text below which a text signal counts anywhere on the page


def _top(page) -> str:
    return "\n".join([page.title or ""] + list(page.headings[:2]))


def detect_block(page, dom_markers: Optional[List[str]] = None, start: bool = False) -> Optional[dict]:
    """None, or {"kind", "evidence", "url", "status"} for a page we must not act on."""
    status = int(getattr(page, "status", 0) or 0)
    top = _top(page)
    body = "\n".join([top, page.live_text(), (page.text or "")[:3000]])
    short = len(page.text or "") < SHORT_PAGE and len(page.controls) < 25
    marks = list(dom_markers or [])

    def hit(kind: str, ev: str) -> dict:
        return {"kind": kind, "evidence": " ".join(ev.split())[:160], "url": page.url, "status": status}

    m = _CLOUDFLARE.search(top) or (short and _CLOUDFLARE.search(body))
    if m:
        return hit("cloudflare", m.group(0))
    if "cloudflare" in marks:
        return hit("cloudflare", "challenge markup")
    # a long page whose title mentions CAPTCHA is an article about it, not a wall
    titled = len(page.text or "") < 5000
    m = (titled and _CAPTCHA.search(top)) or (short and _CAPTCHA.search(body))
    if m:
        return hit("captcha", m.group(0))
    if "captcha" in marks or ("captcha_widget" in marks and short):
        return hit("captcha", "captcha markup")
    m = (titled and _BOT_WALL.search(top)) or (short and _BOT_WALL.search(body))
    if m and (status >= 400 or short):
        return hit("bot_wall", m.group(0))
    if status in (403, 429) or (start and status >= 400):
        return hit(f"http_{status}", page.title or "")
    if start and not (page.title or "").strip() and not (page.text or "").strip() and not page.controls:
        return hit("empty_page", "nothing rendered")
    return None
