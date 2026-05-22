"""
tools/web_search.py — Internet search tool.

Backends (in priority order):
  1. Tavily      — purpose-built for AI agents, needs TAVILY_API_KEY  ← primary
  2. DuckDuckGo  — no API key, always available  (duckduckgo-search)  ← fallback
  3. SerpAPI     — richer results, needs SERP_API_KEY env var
  4. Google CSE  — needs GOOGLE_API_KEY + GOOGLE_CSE_ID env vars

Usage:
    result = WebSearchTool()(query="AWOS coding agent patterns", max_results=5)
"""

from __future__ import annotations

import os
from typing import Any

from .base import Tool, ToolResult


class WebSearchTool(Tool):
    """Search the internet and return a list of results."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the internet for information. Returns titles, URLs, and snippets."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "query": "Search query string",
            "max_results": "Maximum number of results to return (default: 5)",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        errors = []
        if "query" not in args or not args["query"].strip():
            errors.append("Missing required parameter: 'query'")
        return errors

    def execute(self, args: dict[str, Any]) -> ToolResult:
        query = args["query"].strip()
        max_results = int(args.get("max_results", 5))

        # Try backends in priority order
        for backend in (self._tavily, self._ddg, self._serpapi, self._google_cse):
            try:
                results = backend(query, max_results)
                if results:
                    return self._format(results, query)
            except Exception:
                continue

        return ToolResult.fail(
            f"All search backends failed for query: '{query}'. "
            "Set TAVILY_API_KEY, or install duckduckgo-search, or set SERP_API_KEY."
        )

    # ── backends ──────────────────────────────────────────────────────────────

    def _tavily(self, query: str, max_results: int) -> list[dict]:
        from tavily import TavilyClient  # type: ignore

        key = os.getenv("TAVILY_API_KEY", "")
        if not key:
            raise RuntimeError("TAVILY_API_KEY not set")
        client = TavilyClient(api_key=key)
        response = client.search(query, max_results=max_results)
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
            for r in response.get("results", [])
        ]

    def _ddg(self, query: str, max_results: int) -> list[dict]:
        from duckduckgo_search import DDGS  # type: ignore

        with DDGS() as ddgs:
            return [
                {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
                for r in ddgs.text(query, max_results=max_results)
            ]

    def _serpapi(self, query: str, max_results: int) -> list[dict]:
        import requests  # type: ignore

        key = os.getenv("SERP_API_KEY", "")
        if not key:
            raise RuntimeError("SERP_API_KEY not set")
        resp = requests.get(
            "https://serpapi.com/search",
            params={"q": query, "api_key": key, "num": max_results},
            timeout=15,
        )
        resp.raise_for_status()
        organic = resp.json().get("organic_results", [])
        return [{"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")} for r in organic[:max_results]]

    def _google_cse(self, query: str, max_results: int) -> list[dict]:
        import requests  # type: ignore

        api_key = os.getenv("GOOGLE_API_KEY", "")
        cse_id = os.getenv("GOOGLE_CSE_ID", "")
        if not api_key or not cse_id:
            raise RuntimeError("GOOGLE_API_KEY or GOOGLE_CSE_ID not set")
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"q": query, "key": api_key, "cx": cse_id, "num": min(max_results, 10)},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return [{"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")} for r in items]

    # ── formatting ────────────────────────────────────────────────────────────

    @staticmethod
    def _format(results: list[dict], query: str) -> ToolResult:
        lines = [f"Search results for: {query!r}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   {r['url']}")
            if r.get("snippet"):
                lines.append(f"   {r['snippet'][:200]}")
            lines.append("")
        return ToolResult.ok(
            text="\n".join(lines),
            data={"query": query, "results": results},
        )
