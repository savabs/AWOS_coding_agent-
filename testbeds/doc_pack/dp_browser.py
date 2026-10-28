"""
dp_browser.py — the HTTP-level browser the oracle drives: AWOS's own
scaffold.agent.desktop.web_tools.LoopbackBrowser (numbered controls, forms, cookies,
loopback-only) plus file downloads, which LoopbackBrowser deliberately does not do.

    br = DocBrowser(url)
    page = br.open("/login"); page = br.fill(page, n, "x"); page = br.click(page, n)
    if page.download: name, data = page.download

A response that is not HTML (Content-Disposition: attachment, PDF, CSV) becomes a stub
page "Downloaded <file>" with ``page.download = (filename, bytes)``; the previous page
stays current so the agent can keep clicking on it (as in a real browser).
The same interface is implemented over live Safari in safari_oracle.py.
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scaffold.agent.desktop.web_tools import (
    MAX_BYTES,
    LoopbackBrowser,
    Page,
    WebError,
    check_loopback,
    parse_page,
)


class DocBrowser(LoopbackBrowser):
    def __init__(self, base_url: str, timeout: float = 20.0):
        super().__init__(base_url, timeout=timeout)
        self.downloads = []

    def _request(self, url: str, data: bytes | None = None) -> Page:
        check_loopback(url)
        method = "POST" if data is not None else "GET"
        headers = {}
        try:
            with self.op.open(url, data=data, timeout=self.timeout) as r:
                raw = r.read(MAX_BYTES + 1)
                status, final, headers = r.status, r.geturl(), r.headers
        except urllib.error.HTTPError as ex:
            raw, status, final, headers = ex.read(MAX_BYTES + 1), ex.code, url, ex.headers
        except (urllib.error.URLError, OSError) as ex:
            raise WebError(f"{method} failed: {type(ex).__name__}") from ex
        if len(raw) > MAX_BYTES:
            raise WebError("response too large")
        self.log.append(f"{method} {urllib.parse.urlparse(url).path} -> {status}")
        ctype = (headers.get("Content-Type") or "").lower() if headers else ""
        cd = (headers.get("Content-Disposition") or "") if headers else ""
        if status == 200 and ("attachment" in cd.lower() or not ctype.startswith("text/html")):
            fn = _filename(cd) or Path(urllib.parse.urlparse(final).path).name or "download.bin"
            stub = parse_page(check_loopback(final), status,
                              f"<html><head><title>Downloaded</title></head><body><p>Downloaded {fn}</p></body></html>")
            stub.download = (fn, raw)  # type: ignore[attr-defined]
            self.downloads.append((fn, len(raw)))
            return stub                                  # self.page stays the page the link was on
        self.page = parse_page(check_loopback(final), status, raw.decode("utf-8", "replace"))
        self.page.download = None  # type: ignore[attr-defined]
        return self.page

    def download(self, page: Page, n: int) -> tuple[str, bytes]:
        p = self.click(page, n)
        d = getattr(p, "download", None)
        if not d:
            raise WebError(f"control {n} did not download a file (got page {p.title!r})")
        return d


def _filename(cd: str) -> str:
    m = re.search(r'filename="?([^";]+)"?', cd or "")
    return m.group(1).strip() if m else ""
