"""
react_worker.py — ReAct-style coding worker (observe → tool → repeat).

Replaces single-shot SEARCH/REPLACE for tasks when AWOS_REACT_WORKER=1.
Uses workspace-scoped tools from coding_tools.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from anthropic import Anthropic
from openai import OpenAI

try:
    from google import genai
except ImportError:
    genai = None  # type: ignore

try:
    from .coding_tools import build_coding_tool_registry, format_tool_catalog
    from .repo_map import enrich_with_repo_map
    from .test_runner import TestResult, TestRunner
    from .usage_record import empty_usage, merge_usage, record_api_usage
except ImportError:
    from coding_tools import build_coding_tool_registry, format_tool_catalog
    from repo_map import enrich_with_repo_map
    from test_runner import TestResult, TestRunner
    from usage_record import empty_usage, merge_usage, record_api_usage

logger = logging.getLogger(__name__)

_MAX_TURNS = int(os.getenv("AWOS_REACT_MAX_TURNS", "12"))
_MAX_OBS_CHARS = int(os.getenv("AWOS_REACT_MAX_OBS_CHARS", "6000"))
_STUCK_THRESHOLD = int(os.getenv("AWOS_REACT_STUCK_THRESHOLD", "3"))
_STUCK_FORCE_EXIT = int(os.getenv("AWOS_REACT_STUCK_FORCE_EXIT", "5"))


def _error_signature(obs_text: str, action_name: str) -> str:
    """Create a short signature for an error to detect repeated failures."""
    key = f"{action_name}:{obs_text[:200]}"
    return str(hash(key))


def react_worker_enabled() -> bool:
    return os.getenv("AWOS_REACT_WORKER", "1").lower() in ("1", "true", "yes")


@dataclass
class ReActStep:
    thought: str
    action: str
    action_input: dict[str, Any]
    observation: str
    success: bool
    latency_ms: float = 0.0


@dataclass
class ReActResult:
    success: bool
    summary: str = ""
    files_changed: list[str] = field(default_factory=list)
    steps: list[ReActStep] = field(default_factory=list)
    test_result: Optional[TestResult] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model_used: str = ""
    error: str = ""


class ReActWorker:
    """Multi-turn tool-using worker for atomic coding tasks."""

    def __init__(self, api_key: Optional[str] = None, model: str = "deepseek-chat") -> None:
        # E2E test mode: skip real API setup
        self._e2e_mode = os.getenv("AWOS_E2E", "").lower() in ("1", "true", "yes")
        if self._e2e_mode:
            self.client = None
            self.anthropic_client = None
            self.openai_client = None
            self.gemini_client = None
            self.openrouter_client = None
            self.opencode_client = None  # added for OpenCode Go integration
            self.model = model
            self._dead_providers: set[str] = set()
            return

        deepseek_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        self.gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        opencode_key = os.getenv("OPENCODE_GO_API_KEY")
        opencode_base = os.getenv("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1")

        # DeepSeek via OpenCode Go (primary) with fallback to direct DeepSeek
        self.client = OpenAI(api_key=opencode_key, base_url="https://opencode.ai/zen/go/v1") if opencode_key else (
            OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com") if deepseek_key else None
        )
        self.anthropic_client = Anthropic(api_key=anthropic_key) if anthropic_key else None
        self.openai_client = OpenAI(api_key=openai_key, base_url="https://api.openai.com/v1") if openai_key else None
        self.openrouter_client = OpenAI(api_key=openrouter_key, base_url="https://openrouter.ai/api/v1") if openrouter_key else None
        self.opencode_client = OpenAI(api_key=opencode_key, base_url="https://opencode.ai/zen/go/v1") if opencode_key else None
        self.gemini_client = None
        if self.gemini_key and genai is not None:
            _shadow = os.environ.pop("GOOGLE_API_KEY", None)
            self.gemini_client = genai.Client(api_key=self.gemini_key)
            if _shadow is not None:
                os.environ["GOOGLE_API_KEY"] = _shadow

        if not any([self.client, self.anthropic_client, self.openai_client, self.openrouter_client, self.gemini_client, self.opencode_client]):
            raise ValueError("Set at least one API key for ReActWorker")

        self.model = model
        # Pre-populate dead providers from missing API keys so escalation engine skips them
        self._dead_providers: set[str] = set()
        if not self.client:
            self._dead_providers.add("deepseek")
        if not self.anthropic_client:
            self._dead_providers.add("anthropic")
        if not self.openai_client:
            self._dead_providers.add("openai")
        if not self.openrouter_client:
            self._dead_providers.add("openrouter")
        if not self.gemini_client:
            self._dead_providers.add("google")
        if not self.opencode_client:
            self._dead_providers.add("opencode")

    def execute_task(
        self,
        *,
        task: dict,
        file_content: str,
        codebase_context: dict,
        project_root: str,
        tracker=None,
        model_spec=None,
        exploration_context: str = "",
        step_callback: "Callable[[str, str, dict, str, bool, float], None] | None" = None,
    ) -> dict[str, Any]:
        """
        Run ReAct loop for one planner task.

        Args:
            task: Task dict from planner.
            file_content: Current file content.
            codebase_context: Context from codebase discovery.
            project_root: Root directory of project.
            tracker: Optional token tracker.
            model_spec: Optional model specification.
            exploration_context: Optional exploration context.
            step_callback: Optional callback(thought, action, action_input, observation, success, latency_ms)
                          called after each ReAct turn for live streaming.

        Returns dict compatible with orchestrator expectations (success, usage, traces).
        """

        # E2E test mode: return a fake response without any LLM calls
        if self._e2e_mode:
            return {
                "success": True,
                "reasoning": "E2E mode — no LLM call",
                "model_used": "e2e-mock",
                "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
                "steps": [],
                "files_changed": [],
            }

        files_changed: list[str] = []

        def _track(rel_path: str) -> None:
            if rel_path not in files_changed:
                files_changed.append(rel_path)

        registry = build_coding_tool_registry(project_root, on_file_change=_track)
        tool_catalog = format_tool_catalog(registry)

        action = task.get("action", "")
        file_path = task.get("file", "")

        codebase_context = enrich_with_repo_map(
            action,
            project_root,
            codebase_context,
            primary_file=file_path or None,
        )
        exploration = exploration_context or codebase_context.get("exploration", "")
        repo_map = codebase_context.get("repo_map", "")

        system_prompt = self._build_system_prompt(tool_catalog, project_root)
        user_prompt = self._build_task_prompt(
            action=action,
            file_path=file_path,
            file_content=file_content,
            codebase_context=codebase_context,
            exploration=exploration,
            repo_map=repo_map,
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        usage = empty_usage()
        steps: list[ReActStep] = []
        finished = False
        summary = ""
        last_test: Optional[TestResult] = None
        model_used = model_spec.name if model_spec else "deepseek"

        consecutive_failures = 0
        last_error_sig = ""
        stuck_warned = False

        for turn in range(1, _MAX_TURNS + 1):
            print(f"[REACT] Turn {turn}/{_MAX_TURNS}")
            t0 = time.time()
            try:
                raw, turn_usage, model_used = self._call_model(
                    messages, model_spec=model_spec, tracker=tracker
                )
            except Exception as exc:
                return self._fail_result(
                    f"LLM call failed: {exc}",
                    steps,
                    files_changed,
                    usage,
                    model_used,
                )
            merge_usage(usage, turn_usage)

            parsed = self._parse_action(raw)
            if parsed is None:
                obs = (
                    "Invalid response format. Reply with JSON only:\n"
                    '{"thought":"...","action":"tool_name","action_input":{...}}'
                )
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"OBSERVATION:\n{obs}"})
                steps.append(
                    ReActStep("", "parse_error", {}, obs, False, (time.time() - t0) * 1000)
                )
                continue

            thought = parsed.get("thought", "")
            action_name = str(parsed.get("action", "")).strip()
            action_input = parsed.get("action_input") or {}
            if not isinstance(action_input, dict):
                action_input = {}

            print(f"[REACT] {action_name}: {thought[:120]}")

            if action_name == "finish":
                summary = str(action_input.get("summary", thought or "Task complete"))
                finished = True
                steps.append(
                    ReActStep(
                        thought,
                        "finish",
                        action_input,
                        summary,
                        True,
                        (time.time() - t0) * 1000,
                    )
                )
                if step_callback:
                    step_callback(thought, "finish", action_input, summary, True, (time.time() - t0) * 1000)
                break

            tool_result = registry.execute(action_name, action_input)
            obs_text = tool_result.text or tool_result.error
            if len(obs_text) > _MAX_OBS_CHARS:
                obs_text = obs_text[:_MAX_OBS_CHARS] + "\n...(truncated)"

            if action_name == "run_tests" and tool_result.data:
                last_test = TestResult(
                    passed=int(tool_result.data.get("passed", 0)),
                    failed=int(tool_result.data.get("failed", 0)),
                    errors=int(tool_result.data.get("errors", 0)),
                    pass_rate=float(tool_result.data.get("pass_rate", 0.0)),
                    raw_output=str(tool_result.data.get("raw_output", "")),
                    no_tests_found=bool(tool_result.data.get("no_tests_found")),
                )

            steps.append(
                ReActStep(
                    thought,
                    action_name,
                    action_input,
                    obs_text[:500],
                    tool_result.success,
                    (time.time() - t0) * 1000,
                )
            )
            if step_callback:
                step_callback(thought, action_name, action_input, obs_text[:500],
                              tool_result.success, (time.time() - t0) * 1000)

            if tool_result.success:
                consecutive_failures = 0
                last_error_sig = ""
                stuck_warned = False
            else:
                sig = _error_signature(obs_text, action_name)
                if sig == last_error_sig:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 1
                    last_error_sig = sig
                    stuck_warned = False

                if consecutive_failures >= _STUCK_FORCE_EXIT:
                    print(f"[REACT] STUCK: {consecutive_failures} consecutive failures with same error — force exit")
                    return self._fail_result(
                        f"Stuck in loop: {consecutive_failures} consecutive failures (sig={sig[:8]})",
                        steps, files_changed, usage, model_used,
                    )

                if consecutive_failures >= _STUCK_THRESHOLD and not stuck_warned:
                    stuck_warned = True
                    print(f"[REACT] STUCK WARNING: {consecutive_failures} consecutive failures — injecting guidance")
                    obs_text = (
                        f"STUCK DETECTION: You have failed {consecutive_failures} times with the same error.\n"
                        f"Last error: {obs_text[:300]}\n"
                        "STOP repeating the same approach. Try a fundamentally different strategy:\n"
                        "- Read the file again to see its current state\n"
                        "- Use a different edit approach (smaller change, different location)\n"
                        "- If the task is impossible as stated, call finish with a failure summary\n"
                        "Do NOT retry the exact same edit."
                    )

            assistant_msg = json.dumps(
                {"thought": thought, "action": action_name, "action_input": action_input},
                indent=2,
            )
            messages.append({"role": "assistant", "content": assistant_msg})
            status = "OK" if tool_result.success else "ERROR"
            messages.append(
                {
                    "role": "user",
                    "content": f"OBSERVATION ({status}):\n{obs_text}",
                }
            )

        if not finished:
            return self._fail_result(
                f"Exceeded max turns ({_MAX_TURNS}) without finish",
                steps,
                files_changed,
                usage,
                model_used,
            )

        # Post-task test gate when tests weren't run in-loop
        test_pass = True
        if last_test is None and os.getenv("AWOS_SAFE_TO_RUN_TESTS", "") == "1":
            runner = TestRunner(project_root=project_root)
            last_test = runner.run(changed_files=files_changed or [file_path])
        if last_test is not None:
            if last_test.no_tests_found:
                test_pass = not files_changed  # edits without tests = inconclusive fail
            else:
                test_pass = (
                    last_test.pass_rate >= 1.0
                    and last_test.failed == 0
                    and last_test.errors == 0
                )

        needs_edits = self._task_needs_edits(task)
        has_required_edits = bool(files_changed) or not needs_edits
        success = bool(finished and test_pass and has_required_edits)
        if finished and files_changed and not test_pass:
            success = False
            summary = summary or "Edits applied but tests did not pass"

        return {
            "success": success,
            "react": True,
            "summary": summary,
            "files_changed": files_changed,
            "steps": steps,
            "test_result": last_test,
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
            "cost_usd": float(usage.get("cost_usd", 0.0)),
            "model_used": model_used,
            "error": "" if success else (summary or "ReAct task failed"),
        }

    @staticmethod
    def _task_needs_edits(task: dict) -> bool:
        """Heuristic: code-fix tasks must produce file changes."""
        action = str(task.get("action", "")).lower()
        return any(k in action for k in ("fix", "edit", "add", "implement", "update", "change", "correct"))

    def _fail_result(
        self,
        error: str,
        steps: list[ReActStep],
        files_changed: list[str],
        usage: dict,
        model_used: str,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "react": True,
            "summary": "",
            "files_changed": files_changed,
            "steps": steps,
            "test_result": None,
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
            "cost_usd": float(usage.get("cost_usd", 0.0)),
            "model_used": model_used,
            "error": error,
        }

    def _build_system_prompt(self, tool_catalog: str, project_root: str) -> str:
        return f"""You are an expert coding agent. Fix the issue by editing source files.

WORKSPACE: {project_root}

EDITING RULES (MUST FOLLOW):
1. ALWAYS read_file before editing — you need the exact text to match.
   Read the file, find the exact lines to change, then edit.
2. For ALL edits use shell: sed, python3 -c, or heredocs.
   Example: python3 -c \"c=open('f.py').read();c=c.replace('old','new');open('f.py','w').write(c)\"
3. After EVERY edit, run: shell \"git diff\" to confirm changes are correct.
4. Make MINIMAL changes — change only what's needed. One-line fixes preferred.
5. If edit fails (command error, no diff), READ the file again to see exact content.

TOOLS:
{tool_catalog}

WORKFLOW:
1. grep/find to locate the code → read_file to see exact lines
2. shell sed/python3-c to edit → shell \"git diff\" to verify
3. If tests available: install deps, run tests, fix if failing
4. finish(summary=...) when done

RESPONSE: {{\"thought\": \"...\", \"action\": \"tool\", \"action_input\": {{...}} }}"""

    def _build_task_prompt(
        self,
        *,
        action: str,
        file_path: str,
        file_content: str,
        codebase_context: dict,
        exploration: str,
        repo_map: str = "",
    ) -> str:
        arch = codebase_context.get("architecture", "")
        symbols = codebase_context.get("symbols", [])[:15]
        snippet = file_content[:2500] if file_content else "(empty / new file)"
        return f"""TASK: {action}
FILE: {file_path}

CODE (first 20K chars — read_file for more):
{snippet[:5000]}

ARCHITECTURE: {arch}
REPO MAP: {repo_map or '(none)'}
EXPLORATION: {exploration or '(none)'}

STEPS:
1. read_file to see exact code around the bug
2. shell sed/python3-c to make minimal edit
3. shell \"git diff\" to verify change is correct
4. If possible, run tests; if deps missing, install them
5. finish with summary when done"""

    def _parse_action(self, raw: str) -> Optional[dict[str, Any]]:
        raw = raw.strip()
        # Strip markdown fences
        fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
        if fence:
            raw = fence.group(1).strip()
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and "action" in data:
                return data
        except json.JSONDecodeError:
            pass
        # Fallback: extract JSON object with balanced braces (handles nested dicts)
        depth = 0
        start = -1
        for i, ch in enumerate(raw):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        data = json.loads(raw[start:i + 1])
                        if isinstance(data, dict) and "action" in data:
                            return data
                    except json.JSONDecodeError:
                        pass
                    start = -1
        return None

    def _default_model_spec(self):
        """Pick first available provider when orchestrator does not pass escalation spec."""
        try:
            from .escalation_engine import EscalationLevel, ModelSpec
        except ImportError:
            from escalation_engine import EscalationLevel, ModelSpec

        prefer = (os.getenv("AWOS_REACT_PROVIDER") or "").strip().lower()

        def _openai():
            if self.openai_client and "openai" not in self._dead_providers:
                return ModelSpec(
                    level=EscalationLevel.OPENAI,
                    name="GPT-4o-mini",
                    model_id="gpt-4o-mini",
                    provider="openai",
                    cost_per_req=0.003,
                    input_price=0.15,
                    output_price=0.60,
                    min_complexity=0,
                    min_budget_remaining=0.0,
                    min_failures=0,
                )
            return None

        def _gemini():
            if self.gemini_client and "google" not in self._dead_providers:
                return ModelSpec(
                    level=EscalationLevel.GEMINI_FLASH,
                    name="Gemini 2.5 Flash",
                    model_id=os.getenv("AWOS_GEMINI_MODEL", "gemini-2.5-flash"),
                    provider="google",
                    cost_per_req=0.001,
                    input_price=0.10,
                    output_price=0.40,
                    min_complexity=0,
                    min_budget_remaining=0.0,
                    min_failures=0,
                )
            return None

        def _deepseek():
            if self.client and "deepseek" not in self._dead_providers:
                return ModelSpec(
                    level=EscalationLevel.DEEPSEEK,
                    name="DeepSeek V4 Flash",
                    model_id=self.model,
                    provider="deepseek",
                    cost_per_req=0.001,
                    input_price=0.14,
                    output_price=0.28,
                    min_complexity=0,
                    min_budget_remaining=0.0,
                    min_failures=0,
                )
            return None

        def _opencode():
            # OpenCode Go — single provider, two models:
            #   default: deepseek-v4-flash (cheap, fast)
            #   escalate: deepseek-v4-pro (complex / failed tasks)
            if self.opencode_client and "opencode" not in self._dead_providers:
                opencode_model = os.getenv("AWOS_OPENCODE_MODEL", "deepseek-v4-flash")
                return ModelSpec(
                    level=EscalationLevel.DEEPSEEK,
                    name="DeepSeek V4 Flash",
                    model_id=opencode_model,
                    provider="opencode",
                    cost_per_req=0.001,
                    input_price=0.14,
                    output_price=0.28,
                    min_complexity=0,
                    min_budget_remaining=0.0,
                    min_failures=0,
                )
            return None

        def _openrouter():
            if self.openrouter_client and "openrouter" not in self._dead_providers:
                return ModelSpec(
                    level=EscalationLevel.OPENROUTER,
                    name="OpenRouter",
                    model_id="openrouter/auto",
                    provider="openrouter",
                    cost_per_req=0.002,
                    input_price=0.20,
                    output_price=0.80,
                    min_complexity=0,
                    min_budget_remaining=0.0,
                    min_failures=0,
                )
            return None

        def _anthropic():
            if self.anthropic_client and "anthropic" not in self._dead_providers:
                return ModelSpec(
                    level=EscalationLevel.HAIKU,
                    name="Claude Haiku 4.5",
                    model_id="claude-haiku-4-5",
                    provider="anthropic",
                    cost_per_req=0.017,
                    input_price=1.00,
                    output_price=5.00,
                    min_complexity=0,
                    min_budget_remaining=0.0,
                    min_failures=0,
                )
            return None

        order = {
            "opencode": (_opencode, _deepseek, _openrouter, _gemini, _anthropic, _openai),
            "gemini": (_gemini, _opencode, _deepseek, _openrouter, _anthropic, _openai),
            "google": (_gemini, _opencode, _deepseek, _openrouter, _anthropic, _openai),
            "deepseek": (_deepseek, _opencode, _openrouter, _gemini, _anthropic, _openai),
            "openrouter": (_openrouter, _opencode, _deepseek, _gemini, _anthropic, _openai),
            "anthropic": (_anthropic, _opencode, _openrouter, _gemini, _deepseek, _openai),
            "openai": (_openai, _opencode, _openrouter, _gemini, _deepseek, _anthropic),
        }
        # Default order: OpenCode Go first — it's the only provider with
        # working quota (see checkpoint_2026-07-24_master_session §3.4).
        # When Sprint 2 restores other providers, move them back to the front.
        builders = order.get(prefer, (_opencode, _deepseek, _openrouter, _gemini, _anthropic, _openai))
        for build in builders:
            spec = build()
            if spec is not None:
                return spec
        return None

    def _call_model(
        self,
        messages: list[dict[str, str]],
        *,
        model_spec=None,
        tracker=None,
    ) -> tuple[str, dict, str]:
        usage = empty_usage()
        spec = model_spec or self._default_model_spec()
        if spec is None:
            raise RuntimeError("No available model provider for ReAct worker")
        provider = spec.provider
        model_id = spec.model_id
        inp_price = spec.input_price
        out_price = spec.output_price
        label = spec.name

        if provider == "google" and self.gemini_client and "google" not in self._dead_providers:
            prompt = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
            resp = self.gemini_client.models.generate_content(model=model_id, contents=prompt)
            text = (resp.text or "").strip()
            # Gemini usage approximated
            recorded = record_api_usage(
                request_type="react_worker",
                model=model_id,
                input_tokens=max(len(prompt) // 4, 1),
                output_tokens=max(len(text) // 4, 1),
                input_price=inp_price,
                output_price=out_price,
                tracker=tracker,
            )
            merge_usage(usage, recorded)
            return text, usage, label

        if provider == "anthropic" and self.anthropic_client and "anthropic" not in self._dead_providers:
            resp = self.anthropic_client.messages.create(
                model=model_id,
                max_tokens=1500,
                messages=[{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"],
                system=next((m["content"] for m in messages if m["role"] == "system"), ""),
                temperature=0.1,
            )
            text = resp.content[0].text.strip()
            recorded = record_api_usage(
                request_type="react_worker",
                model=model_id,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                input_price=inp_price,
                output_price=out_price,
                tracker=tracker,
            )
            merge_usage(usage, recorded)
            return text, usage, label

        if provider == "openrouter" and self.openrouter_client and "openrouter" not in self._dead_providers:
            resp = self.openrouter_client.chat.completions.create(
                model=model_id,
                messages=messages,
                max_tokens=1500,
                temperature=0.1,
                extra_headers={
                    "HTTP-Referer": "https://github.com/999-sbpatel/AWOS_coding_agent",
                    "X-OpenRouter-Title": "AWOS",
                },
            )
            text = resp.choices[0].message.content.strip()
            u = resp.usage
            recorded = record_api_usage(
                request_type="react_worker",
                model=model_id,
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                input_price=inp_price,
                output_price=out_price,
                tracker=tracker,
            )
            merge_usage(usage, recorded)
            return text, usage, label

        if provider == "openai" and self.openai_client and "openai" not in self._dead_providers:
            resp = self.openai_client.chat.completions.create(
                model=model_id,
                messages=messages,
                max_tokens=1500,
                temperature=0.1,
            )
            text = resp.choices[0].message.content.strip()
            u = resp.usage
            recorded = record_api_usage(
                request_type="react_worker",
                model=model_id,
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                input_price=inp_price,
                output_price=out_price,
                tracker=tracker,
            )
            merge_usage(usage, recorded)
            return text, usage, label

        if provider == "opencode" and self.opencode_client and "opencode" not in self._dead_providers:
            resp = self.opencode_client.chat.completions.create(
                model=model_id,
                messages=messages,
                max_tokens=1500,
                temperature=0.1,
            )
            text = resp.choices[0].message.content.strip()
            u = resp.usage
            recorded = record_api_usage(
                request_type="react_worker",
                model=model_id,
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                input_price=inp_price,
                output_price=out_price,
                tracker=tracker,
            )
            merge_usage(usage, recorded)
            return text, usage, label

        if provider == "deepseek" and self.client and "deepseek" not in self._dead_providers:
            resp = self.client.chat.completions.create(
                model=model_id,
                messages=messages,
                max_tokens=4000,  # DeepSeek V4 needs room for thinking + response
                temperature=0.1,
            )
            text = resp.choices[0].message.content.strip()
            u = resp.usage
            recorded = record_api_usage(
                request_type="react_worker",
                model=model_id,
                input_tokens=u.prompt_tokens,
                output_tokens=u.completion_tokens,
                input_price=inp_price,
                output_price=out_price,
                tracker=tracker,
            )
            merge_usage(usage, recorded)
            return text, usage, label

        raise RuntimeError("No available model provider for ReAct worker")
