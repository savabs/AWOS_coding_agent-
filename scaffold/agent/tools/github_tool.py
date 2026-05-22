"""
tools/github_tool.py — GitHub integration tools.

Requires: GITHUB_TOKEN env var for authenticated requests (higher rate limits).
Uses PyGithub when available; falls back to raw REST API via requests.

Tools provided:
  - GitHubSearchTool        search repos, code, issues
  - GitHubReadFileTool      read a file from any public/private repo
  - GitHubListIssuesTool    list open issues for a repo
  - GitHubCreateIssueTool   create a new issue (write access required)
  - GitHubSearchCodeTool    search code across GitHub
"""

from __future__ import annotations

import base64
import os
from typing import Any

from .base import Tool, ToolResult


# ── shared helper ─────────────────────────────────────────────────────────────

def _gh_request(path: str, params: dict | None = None) -> dict:
    """Make an authenticated GitHub REST API request."""
    import requests  # type: ignore

    token = os.getenv("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = requests.get(
        f"https://api.github.com{path}",
        headers=headers,
        params=params or {},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


# ── tools ─────────────────────────────────────────────────────────────────────

class GitHubSearchReposTool(Tool):
    """Search GitHub repositories by keywords."""

    @property
    def name(self) -> str:
        return "github_search_repos"

    @property
    def description(self) -> str:
        return "Search GitHub for repositories matching a query. Returns repo names, descriptions, stars, and URLs."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "query": "Search query (e.g. 'agentic AI coding assistant language:python')",
            "max_results": "Max repos to return (default: 5)",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        if "query" not in args or not args["query"].strip():
            return ["Missing required parameter: 'query'"]
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        query = args["query"].strip()
        max_results = int(args.get("max_results", 5))
        try:
            data = _gh_request("/search/repositories", {"q": query, "per_page": max_results, "sort": "stars"})
        except Exception as e:
            return ToolResult.fail(f"GitHub API error: {e}")

        repos = data.get("items", [])
        lines = [f"GitHub repo search: {query!r} — {data.get('total_count', 0)} total\n"]
        results = []
        for r in repos:
            lines.append(f"★ {r.get('stargazers_count', 0):>6}  {r['full_name']}")
            lines.append(f"          {r.get('description') or '(no description)'}")
            lines.append(f"          {r['html_url']}")
            lines.append("")
            results.append({
                "full_name": r["full_name"],
                "description": r.get("description"),
                "stars": r.get("stargazers_count", 0),
                "url": r["html_url"],
                "language": r.get("language"),
            })

        return ToolResult.ok(text="\n".join(lines), data={"query": query, "repos": results})


class GitHubReadFileTool(Tool):
    """Read a file from a GitHub repository."""

    @property
    def name(self) -> str:
        return "github_read_file"

    @property
    def description(self) -> str:
        return "Read the contents of a file in a GitHub repository."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "repo": "Repository in owner/repo format (e.g. 'anthropics/anthropic-sdk-python')",
            "path": "File path within the repo (e.g. 'README.md')",
            "ref": "Branch, tag, or commit SHA (default: repo default branch)",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        errors = []
        if "repo" not in args:
            errors.append("Missing required parameter: 'repo'")
        if "path" not in args:
            errors.append("Missing required parameter: 'path'")
        return errors

    def execute(self, args: dict[str, Any]) -> ToolResult:
        repo = args["repo"].strip()
        path = args["path"].strip()
        ref = args.get("ref", "")
        params: dict = {}
        if ref:
            params["ref"] = ref
        try:
            data = _gh_request(f"/repos/{repo}/contents/{path}", params)
        except Exception as e:
            return ToolResult.fail(f"GitHub API error: {e}")

        if data.get("encoding") == "base64":
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        else:
            content = data.get("content", "")

        return ToolResult.ok(
            text=f"File: {repo}/{path}\n\n{content}",
            data={"repo": repo, "path": path, "content": content, "sha": data.get("sha", "")},
        )


class GitHubListIssuesTool(Tool):
    """List open issues for a GitHub repository."""

    @property
    def name(self) -> str:
        return "github_list_issues"

    @property
    def description(self) -> str:
        return "List open issues (and PRs) for a GitHub repository."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "repo": "Repository in owner/repo format",
            "state": "Issue state: 'open', 'closed', or 'all' (default: 'open')",
            "max_results": "Max issues to return (default: 10)",
            "label": "Filter by label name (optional)",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        if "repo" not in args:
            return ["Missing required parameter: 'repo'"]
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        repo = args["repo"].strip()
        state = args.get("state", "open")
        max_results = int(args.get("max_results", 10))
        params: dict = {"state": state, "per_page": max_results}
        if args.get("label"):
            params["labels"] = args["label"]
        try:
            issues = _gh_request(f"/repos/{repo}/issues", params)
        except Exception as e:
            return ToolResult.fail(f"GitHub API error: {e}")

        lines = [f"Issues for {repo} ({state}):\n"]
        results = []
        for issue in issues:
            is_pr = "pull_request" in issue
            kind = "PR" if is_pr else "Issue"
            lines.append(f"#{issue['number']} [{kind}] {issue['title']}")
            lines.append(f"  {issue['html_url']}")
            lines.append(f"  Labels: {', '.join(l['name'] for l in issue.get('labels', [])) or 'none'}")
            lines.append("")
            results.append({
                "number": issue["number"],
                "title": issue["title"],
                "url": issue["html_url"],
                "is_pr": is_pr,
                "labels": [l["name"] for l in issue.get("labels", [])],
            })

        return ToolResult.ok(text="\n".join(lines), data={"repo": repo, "issues": results})


class GitHubSearchCodeTool(Tool):
    """Search code across GitHub repositories."""

    @property
    def name(self) -> str:
        return "github_search_code"

    @property
    def description(self) -> str:
        return "Search code on GitHub. Useful for finding examples and patterns in open source."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "query": "Code search query (e.g. 'ToolRegistry register language:python')",
            "max_results": "Max results (default: 5)",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        if "query" not in args or not args["query"].strip():
            return ["Missing required parameter: 'query'"]
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        query = args["query"].strip()
        max_results = int(args.get("max_results", 5))
        try:
            data = _gh_request("/search/code", {"q": query, "per_page": max_results})
        except Exception as e:
            return ToolResult.fail(f"GitHub API error: {e}")

        items = data.get("items", [])
        lines = [f"Code search: {query!r} — {data.get('total_count', 0)} total\n"]
        results = []
        for item in items:
            repo = item.get("repository", {})
            lines.append(f"{repo.get('full_name', '?')}/{item['path']}")
            lines.append(f"  {item['html_url']}")
            lines.append("")
            results.append({
                "repo": repo.get("full_name"),
                "path": item["path"],
                "url": item["html_url"],
            })

        return ToolResult.ok(text="\n".join(lines), data={"query": query, "results": results})


class GitHubCreateIssueTool(Tool):
    """Create a new issue on a GitHub repository (requires GITHUB_TOKEN with write access)."""

    @property
    def name(self) -> str:
        return "github_create_issue"

    @property
    def description(self) -> str:
        return "Create a GitHub issue. Requires GITHUB_TOKEN with write access to the repo."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "repo": "Repository in owner/repo format",
            "title": "Issue title",
            "body": "Issue body (Markdown)",
            "labels": "Comma-separated label names (optional)",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        errors = []
        if "repo" not in args:
            errors.append("Missing required parameter: 'repo'")
        if "title" not in args or not args["title"].strip():
            errors.append("Missing required parameter: 'title'")
        return errors

    def execute(self, args: dict[str, Any]) -> ToolResult:
        import requests  # type: ignore

        token = os.getenv("GITHUB_TOKEN", "")
        if not token:
            return ToolResult.fail("GITHUB_TOKEN env var is required to create issues.")

        repo = args["repo"].strip()
        payload: dict = {"title": args["title"].strip(), "body": args.get("body", "")}
        if args.get("labels"):
            payload["labels"] = [l.strip() for l in args["labels"].split(",")]

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            resp = requests.post(
                f"https://api.github.com/repos/{repo}/issues",
                headers=headers,
                json=payload,
                timeout=20,
            )
            resp.raise_for_status()
            issue = resp.json()
        except Exception as e:
            return ToolResult.fail(f"GitHub API error: {e}")

        return ToolResult.ok(
            text=f"Issue created: #{issue['number']} — {issue['title']}\n{issue['html_url']}",
            data={"number": issue["number"], "url": issue["html_url"]},
        )
