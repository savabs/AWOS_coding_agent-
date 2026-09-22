"""
create_file_executor.py — Cheap path for new-file deliverables (any extension).

One LLM call → WriteFileTool → local acceptance checks. No SEARCH/REPLACE loop.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from scaffold.agent.goal_acceptance import acceptance_for_create_goal, verify_deliverable

try:
    from scaffold.agent.tools.filesystem import WriteFileTool
    from scaffold.agent.usage_record import empty_usage, merge_usage, record_api_usage
except ImportError:
    from tools.filesystem import WriteFileTool
    from usage_record import empty_usage, merge_usage, record_api_usage


@dataclass
class CreateFileResult:
    success: bool
    paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    content_bytes: int = 0
    model_used: str = ""
    usage: dict[str, Any] = field(default_factory=empty_usage)


def _strip_fences(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```[\w.-]*\n(.*)\n```\s*$", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def _read_template_hint(codebase_root: Path, goal: str) -> str:
    g = goal.lower()
    candidates: list[Path] = []
    if "checkpoint" in g:
        candidates.extend([
            codebase_root / "docs/memory/CHECKPOINT_TEMPLATE.html",
            codebase_root / "docs/memory/CHECKPOINT_TEMPLATE.md",
        ])
    if "spec" in g:
        candidates.append(codebase_root / "docs/specs/SPEC_TEMPLATE.html")
    if "research" in g:
        candidates.append(codebase_root / "docs/research/RESEARCH_TEMPLATE.html")
    if "task" in g:
        candidates.append(codebase_root / "tasks/active/TASK_TEMPLATE.html")

    for p in candidates:
        if p.is_file():
            body = p.read_text(encoding="utf-8", errors="replace")
            return f"\nPROJECT TEMPLATE ({p.relative_to(codebase_root)}):\n```\n{body[:4000]}\n```\n"
    return ""


class CreateFileExecutor:
    """Generate and write a new file deliverable."""

    def __init__(
        self,
        llm_caller: Optional[Callable[[str, str], tuple[str, str, dict]]] = None,
    ):
        """
        llm_caller: (system, user) -> (content, model_name, usage_dict)
        """
        self._llm = llm_caller
        self._writer = WriteFileTool()

    def execute(
        self,
        goal: str,
        target_path: str,
        codebase_root: str | Path = ".",
        extra_context: str = "",
        tracker=None,
    ) -> CreateFileResult:
        root = Path(codebase_root).resolve()
        rel = target_path.lstrip("./")
        full = root / rel

        if full.exists() and full.stat().st_size > 0:
            # Allow overwrite only when goal explicitly says so
            if not re.search(r"\b(overwrite|replace|update)\b", goal, re.I):
                return CreateFileResult(
                    success=False,
                    paths=[rel],
                    errors=[f"Refusing to overwrite existing file: {rel} (use mutate path)"],
                )

        system = (
            "You produce complete file contents for a software project deliverable. "
            "Output ONLY the raw file body — no markdown fences, no explanation before or after. "
            "Match the project's tone and any template provided."
        )
        template_hint = _read_template_hint(root, goal)
        user = f"""GOAL: {goal}

OUTPUT FILE (relative path): {rel}
{template_hint}
{f"ADDITIONAL CONTEXT:{chr(10)}{extra_context}" if extra_context else ""}

Write the full contents for `{rel}` now."""

        try:
            content, model_used, usage = self._call_llm(system, user, tracker)
        except Exception as e:
            return CreateFileResult(success=False, errors=[f"Generation failed: {e}"])

        content = _strip_fences(content)
        if not content.strip():
            return CreateFileResult(
                success=False,
                errors=["Model returned empty content"],
                model_used=model_used,
                usage=usage,
            )

        write_result = self._writer.execute({
            "path": str(full),
            "content": content,
        })
        if not write_result.success:
            return CreateFileResult(
                success=False,
                errors=[write_result.error or "write failed"],
                model_used=model_used,
                usage=usage,
            )

        verify_errors = verify_deliverable(full)
        ok, accept_errors = acceptance_for_create_goal(goal, [rel], root)
        errors = verify_errors + accept_errors

        return CreateFileResult(
            success=ok and not verify_errors,
            paths=[rel],
            errors=errors,
            content_bytes=len(content.encode("utf-8")),
            model_used=model_used,
            usage=usage,
        )

    def _call_llm(self, system: str, user: str, tracker) -> tuple[str, str, dict]:
        if self._llm is not None:
            return self._llm(system, user)

        usage = empty_usage()
        # Prefer Gemini Flash (cheap), then DeepSeek, then OpenAI mini
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if gemini_key:
            from google import genai

            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"{system}\n\n{user}",
            )
            text = (resp.text or "").strip()
            meta = resp.usage_metadata
            inp = meta.prompt_token_count if meta else len(user) // 4
            out = meta.candidates_token_count if meta else len(text) // 4
            cost = (inp / 1_000_000) * 0.15 + (out / 1_000_000) * 0.60
            usage = merge_usage(
                usage,
                record_api_usage(
                    request_type="create_file",
                    model="Gemini 2.5 Flash",
                    input_tokens=inp,
                    output_tokens=out,
                    input_price=0.15,
                    output_price=0.60,
                    tracker=tracker,
                ),
            )
            return text, "gemini-2.5-flash", usage

        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        if deepseek_key:
            from openai import OpenAI

            client = OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=8192,
            )
            text = resp.choices[0].message.content or ""
            inp = resp.usage.prompt_tokens if resp.usage else 0
            out = resp.usage.completion_tokens if resp.usage else 0
            cost = (inp / 1_000_000) * 0.14 + (out / 1_000_000) * 0.28
            usage = merge_usage(
                usage,
                record_api_usage(
                    request_type="create_file",
                    model="DeepSeek Chat",
                    input_tokens=inp,
                    output_tokens=out,
                    input_price=0.14,
                    output_price=0.28,
                    tracker=tracker,
                ),
            )
            return text.strip(), "deepseek-chat", usage

        raise RuntimeError(
            "No API key for create path. Set GEMINI_API_KEY or DEEPSEEK_API_KEY."
        )
