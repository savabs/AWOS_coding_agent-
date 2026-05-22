"""
tools/fetch_url.py — HTTP fetch + text extraction tool.

Fetches a URL and returns clean text (HTML stripped via BeautifulSoup if available,
raw text otherwise). Useful for reading documentation, GitHub pages, and articles.

Usage:
    result = FetchURLTool()(url="https://docs.anthropic.com/en/api/getting-started")
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .base import Tool, ToolResult

_MAX_CHARS = 12_000  # Truncate long pages


class FetchURLTool(Tool):
    """Fetch a URL and return its text content."""

    @property
    def name(self) -> str:
        return "fetch_url"

    @property
    def description(self) -> str:
        return (
            "Fetch the content of a URL (HTTP/HTTPS) and return clean extracted text. "
            "Strips HTML tags when possible. Useful for reading docs, articles, GitHub pages."
        )

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "url": "The URL to fetch",
            "max_chars": f"Maximum characters to return (default: {_MAX_CHARS})",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        url = args.get("url", "").strip()
        if not url:
            return ["Missing required parameter: 'url'"]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return [f"URL must start with http:// or https://. Got: {url!r}"]
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        import requests  # type: ignore

        url = args["url"].strip()
        max_chars = int(args.get("max_chars", _MAX_CHARS))

        try:
            resp = requests.get(
                url,
                timeout=20,
                headers={"User-Agent": "AWOS-CodingAgent/1.0 (github.com/awos)"},
                allow_redirects=True,
            )
            resp.raise_for_status()
        except Exception as e:
            return ToolResult.fail(f"HTTP error fetching {url!r}: {e}")

        content_type = resp.headers.get("Content-Type", "").lower()
        raw = resp.text

        if "html" in content_type:
            text = self._strip_html(raw)
        else:
            text = raw

        text = self._clean(text)
        truncated = len(text) > max_chars
        text = text[:max_chars] + ("\n... [truncated]" if truncated else "")

        return ToolResult.ok(
            text=f"URL: {url}\n\n{text}",
            data={
                "url": url,
                "status_code": resp.status_code,
                "content_type": content_type,
                "truncated": truncated,
                "text": text,
            },
        )

    @staticmethod
    def _strip_html(html: str) -> str:
        try:
            from bs4 import BeautifulSoup  # type: ignore

            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            return soup.get_text(separator="\n")
        except ImportError:
            # Fallback: naive tag stripping
            text = re.sub(r"<[^>]+>", " ", html)
            return text

    @staticmethod
    def _clean(text: str) -> str:
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()
