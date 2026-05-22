"""
tools/huggingface_tool.py — HuggingFace Hub integration tools.

Uses HF_TOKEN env var for authenticated requests (private models, higher rate limits).
Falls back to unauthenticated API for public resources.

Tools provided:
  - HFSearchModelsTool     search models by task / keyword
  - HFSearchDatasetsTool   search datasets
  - HFModelInfoTool        get model card + metadata
  - HFDownloadFileTool     download a specific file from a model repo
"""

from __future__ import annotations

import os
from typing import Any

from .base import Tool, ToolResult


def _hf_headers() -> dict:
    token = os.getenv("HF_TOKEN", "")
    h = {"Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _hf_get(path: str, params: dict | None = None) -> dict | list:
    import requests  # type: ignore

    resp = requests.get(
        f"https://huggingface.co/api{path}",
        headers=_hf_headers(),
        params=params or {},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


# ── tools ─────────────────────────────────────────────────────────────────────

class HFSearchModelsTool(Tool):
    """Search HuggingFace Hub for models."""

    @property
    def name(self) -> str:
        return "hf_search_models"

    @property
    def description(self) -> str:
        return "Search HuggingFace Hub for models by keyword or task (e.g. 'code generation', 'text-to-sql')."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "query": "Search query or model name fragment",
            "task": "Filter by pipeline task (e.g. 'text-generation', 'summarization') — optional",
            "max_results": "Maximum results to return (default: 8)",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        if "query" not in args or not args["query"].strip():
            return ["Missing required parameter: 'query'"]
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        query = args["query"].strip()
        max_results = int(args.get("max_results", 8))
        params: dict = {"search": query, "limit": max_results, "sort": "downloads", "direction": -1}
        if args.get("task"):
            params["pipeline_tag"] = args["task"].strip()

        try:
            data = _hf_get("/models", params)
        except Exception as e:
            return ToolResult.fail(f"HuggingFace API error: {e}")

        models = data if isinstance(data, list) else data.get("models", [])
        lines = [f"HF Models: {query!r}\n"]
        results = []
        for m in models[:max_results]:
            mid = m.get("modelId") or m.get("id", "")
            downloads = m.get("downloads", 0)
            task = m.get("pipeline_tag", "")
            lines.append(f"{'↓ ' + str(downloads):>12}  {mid}  [{task}]")
            lines.append(f"              https://huggingface.co/{mid}")
            lines.append("")
            results.append({"id": mid, "task": task, "downloads": downloads})

        if not results:
            lines.append("(no results)")

        return ToolResult.ok(text="\n".join(lines), data={"query": query, "models": results})


class HFSearchDatasetsTool(Tool):
    """Search HuggingFace Hub for datasets."""

    @property
    def name(self) -> str:
        return "hf_search_datasets"

    @property
    def description(self) -> str:
        return "Search HuggingFace Hub for datasets by keyword."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "query": "Search query",
            "max_results": "Maximum results to return (default: 8)",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        if "query" not in args or not args["query"].strip():
            return ["Missing required parameter: 'query'"]
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        query = args["query"].strip()
        max_results = int(args.get("max_results", 8))
        try:
            data = _hf_get("/datasets", {"search": query, "limit": max_results, "sort": "downloads", "direction": -1})
        except Exception as e:
            return ToolResult.fail(f"HuggingFace API error: {e}")

        datasets = data if isinstance(data, list) else []
        lines = [f"HF Datasets: {query!r}\n"]
        results = []
        for d in datasets[:max_results]:
            did = d.get("id", "")
            downloads = d.get("downloads", 0)
            lines.append(f"{'↓ ' + str(downloads):>12}  {did}")
            lines.append(f"              https://huggingface.co/datasets/{did}")
            lines.append("")
            results.append({"id": did, "downloads": downloads})

        if not results:
            lines.append("(no results)")

        return ToolResult.ok(text="\n".join(lines), data={"query": query, "datasets": results})


class HFModelInfoTool(Tool):
    """Get metadata and model card for a HuggingFace model."""

    @property
    def name(self) -> str:
        return "hf_model_info"

    @property
    def description(self) -> str:
        return "Fetch metadata, tags, and model card text for a specific HuggingFace model ID."

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "model_id": "Model ID (e.g. 'mistralai/Mistral-7B-Instruct-v0.3')",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        if "model_id" not in args or not args["model_id"].strip():
            return ["Missing required parameter: 'model_id'"]
        return []

    def execute(self, args: dict[str, Any]) -> ToolResult:
        model_id = args["model_id"].strip()
        try:
            meta = _hf_get(f"/models/{model_id}")
        except Exception as e:
            return ToolResult.fail(f"HuggingFace API error: {e}")

        lines = [
            f"Model: {model_id}",
            f"Task: {meta.get('pipeline_tag', 'n/a')}",
            f"Downloads: {meta.get('downloads', 0):,}",
            f"Likes: {meta.get('likes', 0):,}",
            f"Tags: {', '.join(meta.get('tags', [])[:10])}",
            f"URL: https://huggingface.co/{model_id}",
        ]

        card = meta.get("cardData", {})
        if card:
            lines.append(f"\nCard data: {str(card)[:400]}")

        return ToolResult.ok(
            text="\n".join(lines),
            data={"model_id": model_id, "meta": meta},
        )


class HFDownloadFileTool(Tool):
    """Download a specific file from a HuggingFace model or dataset repo."""

    @property
    def name(self) -> str:
        return "hf_download_file"

    @property
    def description(self) -> str:
        return (
            "Download a file from a HuggingFace repo (model or dataset) and save it locally. "
            "Uses HF_TOKEN for private repos."
        )

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "repo_id": "HuggingFace repo ID (e.g. 'mistralai/Mistral-7B-Instruct-v0.3')",
            "filename": "File path within the repo (e.g. 'config.json', 'tokenizer.json')",
            "save_to": "Local path to save the file to",
            "repo_type": "Repo type: 'model' or 'dataset' (default: 'model')",
        }

    def validate(self, args: dict[str, Any]) -> list[str]:
        errors = []
        for k in ("repo_id", "filename", "save_to"):
            if k not in args or not str(args[k]).strip():
                errors.append(f"Missing required parameter: '{k}'")
        return errors

    def execute(self, args: dict[str, Any]) -> ToolResult:
        import requests  # type: ignore
        from pathlib import Path

        repo_id = args["repo_id"].strip()
        filename = args["filename"].strip()
        save_to = Path(args["save_to"].strip()).expanduser().resolve()
        repo_type = args.get("repo_type", "model")

        if repo_type == "dataset":
            url = f"https://huggingface.co/datasets/{repo_id}/resolve/main/{filename}"
        else:
            url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"

        try:
            resp = requests.get(url, headers=_hf_headers(), timeout=60, stream=True)
            resp.raise_for_status()
            save_to.parent.mkdir(parents=True, exist_ok=True)
            with open(save_to, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
        except Exception as e:
            return ToolResult.fail(f"Download failed: {e}")

        size = save_to.stat().st_size
        return ToolResult.ok(
            text=f"Downloaded {repo_id}/{filename} → {save_to} ({size:,} bytes)",
            data={"repo_id": repo_id, "filename": filename, "save_to": str(save_to), "size": size},
        )
