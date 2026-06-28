"""
Worker Module: Executes atomic micro-tasks using DeepSeek Coder.

Each task receives one specific action and narrow context window.
Cost: ~$0.001-0.002 per task. Accuracy improves due to micro-context.
"""

import difflib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Optional
from openai import OpenAI  # For DeepSeek API access
from anthropic import Anthropic  # For Haiku/Sonnet/Opus
try:
    from .budget_ledger import get_ledger
    from .usage_record import record_api_usage, empty_usage, merge_usage
    from .escalation_engine import is_cheap_only
except ImportError:
    from budget_ledger import get_ledger
    from usage_record import record_api_usage, empty_usage, merge_usage
    from escalation_engine import is_cheap_only

_MONTHLY_BUDGET = float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0"))


@dataclass
class _PatchCandidate:
    """A single patch candidate generated during parallel sampling."""
    edits: list
    new_content: str
    reasoning: str
    temperature: float
    syntax_ok: bool = False
    test_result: Optional[float] = None
    score: float = 0.0


class Worker:
    """Uses DeepSeek Coder to generate SEARCH/REPLACE code changes."""
    
    MAX_CONTEXT_TOKENS_BLOCK_B: int = 2000

    def __init__(self, api_key: Optional[str] = None, model: str = "deepseek-chat"):
        """Initialize Worker with DeepSeek primary and multiple fallbacks."""
        deepseek_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")

        # DeepSeek (direct API)
        if deepseek_key:
            self.client = OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")
        else:
            self.client = None

        # Anthropic (Haiku / Sonnet)
        if anthropic_key:
            self.anthropic_client = Anthropic(api_key=anthropic_key)
        else:
            self.anthropic_client = None

        # OpenAI direct (GPT-4o-mini etc.)
        if openai_key:
            self.openai_client = OpenAI(api_key=openai_key, base_url="https://api.openai.com/v1")
        else:
            self.openai_client = None

        if not any([self.client, self.anthropic_client, self.openai_client]):
            raise ValueError("Set at least one of: DEEPSEEK_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY")

        self.model = model
        self.fallback_model = "claude-haiku-4-5"
        self._dead_providers: set = set()
    
    @staticmethod
    def _is_permanent_failure(error: str) -> bool:
        """Return True if the error is a non-retryable provider failure (e.g. depleted credits)."""
        low = error.lower()
        return any(p in low for p in (
            "credit balance is too low",
            "your credit balance",
            "insufficient_quota",
            "payment required",
            "billing",
        ))

    def _try_cheap_fallback(
        self,
        prompt: str,
        tried_provider: str,
        tracker=None,
        _usage_accum: Optional[dict] = None,
    ) -> tuple[str, str]:
        """Try alternate cheap providers before giving up."""
        if _usage_accum is None:
            _usage_accum = empty_usage()

        fallbacks = []
        if tried_provider != "deepseek" and self.client and "deepseek" not in self._dead_providers:
            fallbacks.append(("deepseek", self.client, "deepseek-chat", "DeepSeek V4 Flash", 0.14, 0.28, 0.001))
        if tried_provider != "openai" and self.openai_client and "openai" not in self._dead_providers:
            fallbacks.append(("openai", self.openai_client, "gpt-4o-mini", "GPT-4o-mini", 0.15, 0.60, 0.003))
        if (
            not is_cheap_only()
            and tried_provider != "anthropic"
            and self.anthropic_client
            and "anthropic" not in self._dead_providers
        ):
            fallbacks.append(("anthropic", self.anthropic_client, self.fallback_model, "Haiku (fallback)", 1.00, 5.00, 0.017))

        for provider, client, model_id, label, inp_price, out_price, est_cost in fallbacks:
            _allowed, _reason = get_ledger().check_budget(est_cost, _MONTHLY_BUDGET)
            if not _allowed:
                continue
            try:
                if provider in ("deepseek", "openai"):
                    response = client.chat.completions.create(
                        model=model_id,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=4096,
                        temperature=0.3,
                    )
                    text = response.choices[0].message.content or ""
                    inp = response.usage.prompt_tokens
                    out = response.usage.completion_tokens
                else:
                    response = client.messages.create(
                        model=model_id,
                        max_tokens=2048,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    text = response.content[0].text or ""
                    inp = response.usage.input_tokens
                    out = response.usage.output_tokens
                if text:
                    merge_usage(_usage_accum, record_api_usage(
                        request_type="execution",
                        model=label,
                        input_tokens=inp,
                        output_tokens=out,
                        input_price=inp_price,
                        output_price=out_price,
                        tracker=tracker,
                    ))
                    return text, label
            except Exception as e:
                err_str = str(e)
                if self._is_permanent_failure(err_str):
                    self._dead_providers.add(provider)
                    print(f"[CIRCUIT] {provider} tripped — {err_str[:60]}")
                else:
                    print(f"[WORKER] {label} fallback failed: {err_str[:80]}")
        return "", "unknown"

    def execute_task(
        self,
        task: dict,
        file_content: str,
        codebase_context: dict,
        tracker=None,
        attempt: int = 1,
        symbol_index=None,
        model_spec=None,
        example_store=None,
        strategy=None,
        skill_library=None,
        vector_chunks=None,
        prompt_evolver=None,
        _usage_accum: Optional[dict] = None,
    ) -> dict:
        """
        Execute a single micro-task: generate SEARCH/REPLACE code.

        Args:
            task:          {"task_id": 1, "file": "path", "action": "...", "complexity": "..."}
            file_content:  Full text of the file to modify
            codebase_context: Dict with project structure for context
            symbol_index:  Optional SymbolIndex for cross-file symbol awareness
            tracker:       Optional TokenTracker to record usage
            attempt:       Current retry attempt (1-3)
            model_spec:    Optional ModelSpec from EscalationEngine
            example_store: Optional ExampleStore for few-shot injection
        
        Returns:
            {
                "success": True/False,
                "search": "exact text to find",
                "replace": "exact text to replace with",
                "reasoning": "why this change",
                "model_used": "deepseek|haiku"
            }
        """
        
        task_id = task.get("task_id")
        action = task.get("action")
        file_path = task.get("file")
        complexity = task.get("complexity", "medium")
        error_context = task.get("error_context", "")  # Set on retry by orchestrator
        
        # Build micro-context: show only relevant lines (full file on retry)
        if attempt > 1 and len(file_content) < 60000:
            context_snippet = file_content
        else:
            context_snippet = self._extract_context(file_content, action)
        
        # Cross-file symbol awareness (Phase 3)
        symbol_section = ""
        if symbol_index is not None:
            try:
                cross_file_ctx = symbol_index.get_context_for_task(file_path, action)
                if cross_file_ctx.strip():
                    symbol_section = f"\nCROSS-FILE SYMBOL MAP:\n{cross_file_ctx}\n"
            except Exception:
                pass
        
        # Few-shot examples from ExampleStore (intelligence amplifier)
        examples_section = ""
        if example_store is not None:
            try:
                examples_section = example_store.get_examples_for_prompt(task)
                if examples_section:
                    examples_section = f"\n{examples_section}\n"
            except Exception:
                pass
        
        # On retry, tell the model exactly what failed
        retry_section = ""
        if error_context and attempt > 1:
            retry_section = f"""
⚠️  PREVIOUS ATTEMPT FAILED — FIX THIS ERROR:
{error_context}

Do NOT repeat the same approach. Use a different strategy.
"""

        # Build the full system prompt using all arguments
        system_prompt = self._build_prompt(
            task=task,
            file_content=file_content,
            codebase_context=codebase_context,
            symbol_index=symbol_index,
            example_store=example_store,
            strategy=strategy,
            skill_library=skill_library,
            vector_chunks=vector_chunks,
        )
        
        # Improvement 3: inject context from previous task (Ralph Loop chaining)
        previous_result = task.get("previous_task_result")
        if previous_result:
            system_prompt += f"\n\nPrevious task result:\n{previous_result}"
        prev_context_section = ""
        prev_task_context = task.get("prev_task_context", "")
        if prev_task_context:
            prev_context_section = f"\n{prev_task_context}\n"

        # Multi-file awareness: extract method signatures from prev task's REPLACE block
        cross_file_section = ""
        if prev_task_context:
            sigs = self._extract_method_signatures(prev_task_context)
            if sigs:
                cross_file_section = (
                    f"\n[CROSS-FILE REFERENCE — methods you may need to call]\n"
                    f"{sigs}\n[END CROSS-FILE REFERENCE]\n"
                )

        # Improvement 2: Architect/Editor split
        # On retry or high-complexity: call architect first to describe the solution in
        # plain text, then pass that description to the editor (this model) as context.
        architect_section = ""
        if attempt > 1 or complexity == "high":
            architect_plan = self._architect_step(
                action=action,
                context_snippet=context_snippet,
                file_path=file_path,
                model_spec=model_spec,
            )
            if architect_plan:
                architect_section = (
                    f"\nARCHITECT SOLUTION PLAN:\n{architect_plan}\n"
                    f"Now implement this exact plan as a SEARCH/REPLACE block:\n"
                )

        # Machine-enforceable contract from Planner (deterministic spec for cheap model)
        contract_section = ""
        if any(k in task for k in ("function_signature", "constraints", "must_not", "interface_contract", "example_call")):
            parts = ["\n🔒 CONTRACT (you MUST follow this exactly):"]
            if task.get("function_signature"):
                parts.append(f'Function signature: {task["function_signature"]}')
            if task.get("interface_contract"):
                parts.append(f'Interface contract: {task["interface_contract"]}')
            if task.get("example_call"):
                parts.append(f'Example call: {task["example_call"]}')
            if task.get("constraints"):
                parts.append(f'Constraints: {task["constraints"]}')
                parts.append("Constraints you MUST satisfy:")
                for c in task["constraints"]:
                    parts.append(f"  • {c}")
            if task.get("must_not"):
                parts.append("You MUST NOT do any of these:")
                for m in task["must_not"]:
                    parts.append(f"  • {m}")
            parts.append("Any deviation from the contract will be rejected.\n")
            contract_section = "\n".join(parts)

        # Skill context from SkillLibrary (learned patterns from past successes)
        skill_section = ""
        if skill_library is not None:
            try:
                skill_ctx = skill_library.get_skill_context(task)
                if skill_ctx:
                    skill_section = f"\n{skill_ctx}\n"
            except Exception:
                pass

        # Phase 5B ReflexionMemory: inject past verbal critiques for this file/error_type
        past_critiques = task.get("past_critiques", [])
        critique_block = ""
        if past_critiques:
            lines = []
            for i, ep in enumerate(past_critiques[:3], 1):
                critique_text = (ep.critique if hasattr(ep, "critique") else str(ep))[:400]
                error_type    = ep.error_type if hasattr(ep, "error_type") else "UNKNOWN"
                lines.append(f"[PAST CRITIQUE {i} — {error_type}]\n{critique_text}")
            critique_block = "\n\n".join(lines) + "\n\n"

        strategy_preamble = strategy.preamble if strategy is not None else ""
        strategy_suffix = strategy.instruction_suffix if strategy is not None else ""

        # Phase 6: inject semantically relevant code chunks before the task
        vector_section = ""
        if vector_chunks:
            chunk_lines = []
            for c in vector_chunks:
                chunk_lines.append(
                    f"# {c.file_path} lines {c.start_line}-{c.end_line}\n{c.text}"
                )
            vector_section = (
                "[RELEVANT CODE]\n"
                + "\n\n".join(chunk_lines)
                + "\n[END RELEVANT CODE]\n\n"
            )

        # Feature 1A: inject evolved guidelines if present
        _evolved_block = ""
        if prompt_evolver is not None:
            _gl = prompt_evolver.load_evolved_guidelines()
            if _gl:
                _evolved_block = f"[EVOLVED GUIDELINES]\n{_gl}\n\n"

        # Feature 1B: inject live tool output if present
        _tool_output_block = ""
        _tool_out = task.get("tool_output", "")
        if _tool_out:
            _tool_output_block = f"[TOOL OUTPUT]\n{_tool_out}\n\n"

        # NEW FILE: Different prompt for file creation
        if task.get("_is_new_file", False):
            prompt = f"""You are a senior software engineer creating a new file.
{_evolved_block}{_tool_output_block}{vector_section}{strategy_preamble}
TASK: {action}
FILE: {file_path} (NEW FILE - will be created)
COMPLEXITY: {complexity}
ATTEMPT: {attempt}{retry_section}{contract_section}

This is a NEW FILE. Generate the COMPLETE file content from scratch.
{symbol_section}{examples_section}
{skill_section}PROJECT CONTEXT: {codebase_context.get('modules', 'standard Python project')}

INSTRUCTIONS:
1. Generate the COMPLETE file content
2. Include all necessary imports, docstrings, and code structure
3. Make it production-ready and well-documented

OUTPUT FORMAT:

CONTENT:
```
<full file content here - include everything from imports to the end>
```

REASONING:
<one sentence explaining what you created and why>

Do NOT use SEARCH/REPLACE format. Just provide the complete file content.{strategy_suffix}"""
        else:
            # EXISTING FILE: Standard SEARCH/REPLACE prompt
            prompt = f"""You are a senior software engineer executing a precise code change.
{_evolved_block}{_tool_output_block}{vector_section}{critique_block}{strategy_preamble}{prev_context_section}{cross_file_section}
TASK: {action}
FILE: {file_path}
COMPLEXITY: {complexity}
ATTEMPT: {attempt}{retry_section}{architect_section}{contract_section}

CURRENT FILE CONTEXT:
```
{context_snippet}
```
{symbol_section}{examples_section}
{skill_section}PROJECT CONTEXT: {codebase_context.get('modules', 'standard Python project')}

BEFORE YOU WRITE CODE, REASON THROUGH THIS:
1. What exact lines in the file need to change?
2. What should those lines become?
3. Do I have enough context to make this change precisely?

Then produce your SEARCH/REPLACE:

INSTRUCTIONS:
1. SEARCH must be an exact verbatim substring from the file above (copy-paste exact)
2. Include 2-3 unchanged context lines before AND after your actual edit
3. REPLACE is the modified version of those exact same lines
4. The SEARCH string must exist literally in the file shown above

If you need to call a method from another file, first search this file for where that class/object is already imported or used, then add your call immediately after that existing usage.
IMPORTANT: The variable name in the action (e.g., 'tracker.snapshot()') may not match the actual variable name in this file. Search the file for the actual instance name and use that instead.

OUTPUT FORMAT — choose ONE of the two formats below:

FORMAT A (SEARCH/REPLACE — preferred for single-block changes):
SEARCH:
```
<exact multiline text from file, including context lines>
```

REPLACE:
```
<modified version of those exact same lines>
```

REASONING:
<one sentence: what changes and why>

FORMAT B (JSON — use for multi-location changes or when exact line matching is hard):
```json
{{
  "reasoning": "<one sentence>",
  "edits": [
    {{"old_string": "<exact text to find in file>", "new_string": "<replacement text>"}}
  ]
}}
```
Each old_string must be an exact substring of the file. You can include multiple edits in the array.

Do not add markdown backticks inside the code blocks. The SEARCH text must be copy-pasteable from the file.{strategy_suffix}"""
        
        response_text = ""
        model_used = "unknown"
        if _usage_accum is None:
            _usage_accum = empty_usage()

        # Determine which provider/model to use (EscalationEngine or defaults)
        if model_spec is not None:
            use_provider = model_spec.provider
            use_model    = model_spec.model_id
            inp_price    = model_spec.input_price
            out_price    = model_spec.output_price
            model_label  = model_spec.name
        else:
            use_provider = "deepseek" if self.client else "anthropic"
            use_model    = self.model
            inp_price, out_price = 0.14, 0.28
            model_label  = "DeepSeek"
        
        # Call the right provider
        if use_provider == "deepseek" and self.client and "deepseek" not in self._dead_providers:
            _allowed, _reason = get_ledger().check_budget(model_spec.cost_per_req if model_spec else 0.001, _MONTHLY_BUDGET)
            if not _allowed:
                raise RuntimeError(f"[BUDGET HARD STOP] {_reason}")
            try:
                response = self.client.chat.completions.create(
                    model=use_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096,
                    temperature=0.3,
                )
                response_text = response.choices[0].message.content
                model_used = model_label
                inp = response.usage.prompt_tokens
                out = response.usage.completion_tokens
                merge_usage(_usage_accum, record_api_usage(
                    request_type="execution",
                    model=model_label,
                    input_tokens=inp,
                    output_tokens=out,
                    input_price=inp_price,
                    output_price=out_price,
                    tracker=tracker,
                ))
                
                # Cache telemetry (DeepSeek/OpenAI compatible)
                try:
                    from .cache_telemetry import CacheTelemetryStore, extract_cache_stats_openai
                    cache_store = CacheTelemetryStore()
                    cache_event = extract_cache_stats_openai(
                        response=response.model_dump(),
                        component="worker",
                        model=use_model,
                        price_per_m_tokens=inp_price,
                    )
                    cache_store.record(cache_event)
                except Exception:
                    pass  # Don't fail task if telemetry breaks
                
            except Exception as e:
                err_str = str(e)
                if self._is_permanent_failure(err_str):
                    self._dead_providers.add("deepseek")
                    print(f"[CIRCUIT] deepseek tripped — {err_str[:60]}")
                else:
                    print(f"[WORKER] {model_label} failed (attempt {attempt}): {err_str[:80]}")

        elif use_provider == "openai" and self.openai_client and "openai" not in self._dead_providers:
            _allowed, _reason = get_ledger().check_budget(model_spec.cost_per_req if model_spec else 0.003, _MONTHLY_BUDGET)
            if not _allowed:
                raise RuntimeError(f"[BUDGET HARD STOP] {_reason}")
            try:
                response = self.openai_client.chat.completions.create(
                    model=use_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096,
                    temperature=0.3,
                )
                response_text = response.choices[0].message.content
                model_used = model_label
                inp = response.usage.prompt_tokens
                out = response.usage.completion_tokens
                merge_usage(_usage_accum, record_api_usage(
                    request_type="execution",
                    model=model_label,
                    input_tokens=inp,
                    output_tokens=out,
                    input_price=inp_price,
                    output_price=out_price,
                    tracker=tracker,
                ))
                
                # Cache telemetry (OpenAI)
                try:
                    from .cache_telemetry import CacheTelemetryStore, extract_cache_stats_openai
                    cache_store = CacheTelemetryStore()
                    cache_event = extract_cache_stats_openai(
                        response=response.model_dump(),
                        component="worker",
                        model=use_model,
                        price_per_m_tokens=inp_price,
                    )
                    cache_store.record(cache_event)
                except Exception:
                    pass  # Don't fail task if telemetry breaks
                
            except Exception as e:
                err_str = str(e)
                if self._is_permanent_failure(err_str):
                    self._dead_providers.add("openai")
                    print(f"[CIRCUIT] openai tripped — {err_str[:60]}")
                else:
                    print(f"[WORKER] {model_label} failed (attempt {attempt}): {err_str[:80]}")

        elif (
            use_provider == "anthropic"
            and not is_cheap_only()
            and self.anthropic_client
            and "anthropic" not in self._dead_providers
        ):
            _allowed, _reason = get_ledger().check_budget(model_spec.cost_per_req if model_spec else 0.017, _MONTHLY_BUDGET)
            if not _allowed:
                raise RuntimeError(f"[BUDGET HARD STOP] {_reason}")
            try:
                response = self.anthropic_client.messages.create(
                    model=use_model,
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}],
                )
                response_text = response.content[0].text
                model_used = model_label
                inp = response.usage.input_tokens
                out = response.usage.output_tokens
                merge_usage(_usage_accum, record_api_usage(
                    request_type="execution",
                    model=model_label,
                    input_tokens=inp,
                    output_tokens=out,
                    input_price=inp_price,
                    output_price=out_price,
                    tracker=tracker,
                ))
                
                # Cache telemetry
                try:
                    from .cache_telemetry import CacheTelemetryStore, extract_cache_stats_anthropic
                    cache_store = CacheTelemetryStore()
                    cache_event = extract_cache_stats_anthropic(
                        response=response.model_dump(),
                        component="worker",
                        model=use_model,
                        price_per_m_tokens=inp_price,
                    )
                    cache_store.record(cache_event)
                except Exception:
                    pass  # Don't fail task if telemetry breaks
                
            except RuntimeError:
                raise
            except Exception as e:
                err_str = str(e)
                if self._is_permanent_failure(err_str):
                    self._dead_providers.add("anthropic")
                    print(f"[CIRCUIT] anthropic tripped — {err_str[:60]}")
                else:
                    print(f"[WORKER] {model_label} failed (attempt {attempt}): {err_str[:80]}")

        # Fallback: alternate cheap provider (never Haiku/Sonnet in cheap-only mode)
        if not response_text:
            response_text, model_used = self._try_cheap_fallback(
                prompt=prompt,
                tried_provider=use_provider,
                tracker=tracker,
                _usage_accum=_usage_accum,
            )

        if not response_text:
            raise RuntimeError("No API key available or all providers failed")
        
        # Parse response based on file type
        if task.get("_is_new_file", False):
            # For new files, parse CONTENT block
            result = self._parse_new_file_content(response_text, task.get('file', 'unknown'))
        else:
            # For existing files, parse SEARCH/REPLACE
            result = self._parse_search_replace(response_text)
        
        # ── Phase 7: Self-Verification ─────────────────────────────────────
        if result["success"]:
            # Skip verification for new files (nothing to verify)
            if task.get("_is_new_file", False):
                print(f"[WORKER] New file - skipping self-verification")
            else:
                try:
                    from .self_verification import SelfVerificationEngine
                except ImportError:
                    from self_verification import SelfVerificationEngine
                sv = SelfVerificationEngine()
                sv_result = sv.verify(
                    original_content=file_content,
                    search_replace=result,
                    file_path=file_path,
                    task_spec=task,
                )
                if not sv_result.passed:
                    print(f"[WORKER] Self-verify FAILED at stage={sv_result.stage}. Retrying.")
                    result = {"success": False, "search": "", "replace": "",
                              "reasoning": sv_result.error_context}
        
        if not result["success"]:
            # ── Fallback: try JSON structured edit format ──────────────
            json_req = self._parse_json_edits(response_text)
            if json_req is not None:
                edit_result = self._apply_all_edits(file_content, json_req)
                if edit_result.applied > 0 and edit_result.failed == 0:
                    print(f"[WORKER] JSON edit applied ({edit_result.applied} edit(s))")
                    return {
                        "success": True,
                        "search": "",
                        "replace": "",
                        "reasoning": json_req.reasoning,
                        "model_used": model_used,
                        "_json_applied": True,
                        "_final_content": edit_result.final_content,
                    }
                else:
                    print(f"[WORKER] JSON edit partially failed: {edit_result.applied} ok, {edit_result.failed} fail")
            # ───────────────────────────────────────────────────────────
            if attempt < 2:
                # Inject self-verification error context into retry
                _retry_task = task
                if result.get("reasoning"):
                    _retry_task = {
                        **task,
                        "error_context": result["reasoning"],
                    }
                return self.execute_task(
                    task=_retry_task,
                    file_content=file_content,
                    codebase_context=codebase_context,
                    tracker=tracker,
                    attempt=attempt + 1,
                    symbol_index=symbol_index,
                    model_spec=model_spec,
                    example_store=example_store,
                    _usage_accum=_usage_accum,
                )
            else:
                return {
                    "success": False,
                    "error": f"Failed to parse SEARCH/REPLACE: {response_text[:200]}",
                    "model_used": model_used
                }
        
        result["model_used"] = model_used
        result["input_tokens"] = int(_usage_accum.get("input_tokens", 0))
        result["output_tokens"] = int(_usage_accum.get("output_tokens", 0))
        result["cost_usd"] = float(_usage_accum.get("cost_usd", 0.0))
        return result

    def _parse_json_edits(self, response_text: str):
        """
        Parse a JSON-formatted edit block from response_text.
        Returns an EditRequest if a valid JSON edit block is found, else None.
        """
        try:
            from .edit_models import EditRequest, EditInstruction
        except ImportError:
            from edit_models import EditRequest, EditInstruction
        import json, re
        # Try fenced block first, then bare JSON object
        candidates = []
        for m in re.finditer(r"```(?:json)?\s*(.*?)```", response_text, re.DOTALL):
            candidates.append(m.group(1).strip())
        # Also try bare JSON object containing "edits"
        for m in re.finditer(r'(\{[^{}]*"edits"\s*:.*\})', response_text, re.DOTALL):
            candidates.append(m.group(1).strip())
        # And the whole response if it starts with {
        stripped = response_text.strip()
        if stripped.startswith("{"):
            candidates.append(stripped)
        for candidate in candidates:
            try:
                data = json.loads(candidate)
                if not isinstance(data, dict) or "edits" not in data:
                    continue
                raw_edits = data["edits"]
                if not raw_edits:
                    return None
                instructions = [
                    EditInstruction(
                        old_string=e.get("old_string", ""),
                        new_string=e.get("new_string", ""),
                        description=e.get("description", ""),
                    )
                    for e in raw_edits
                    if isinstance(e, dict)
                ]
                return EditRequest(edits=instructions, reasoning=data.get("reasoning", ""))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return None

    def _find_nearest_match(self, file_content: str, old_string: str, threshold: float = 0.82) -> tuple[str, float]:
        """
        Find the best fuzzy match for *old_string* in *file_content*.
        Returns (matched_text, score).  Returns ("", 0.0) if below threshold.
        """
        from difflib import SequenceMatcher
        if not old_string:
            return ("", 0.0)
        if not file_content:
            return ("", 0.0)
        old_lines = old_string.splitlines()
        content_lines = file_content.splitlines()
        n = len(old_lines)
        if n == 0:
            return ("", 0.0)
        best_ratio = 0.0
        best_start = 0
        old_text = "\n".join(old_lines)
        for i in range(max(1, len(content_lines) - n + 1)):
            window_text = "\n".join(content_lines[i:i + n])
            ratio = SequenceMatcher(None, old_text, window_text).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = i
        if best_ratio < threshold:
            return ("", 0.0)
        matched = "\n".join(content_lines[best_start:best_start + n])
        return (matched, best_ratio)

    def _apply_single_edit(self, file_content: str, old_string: str, new_string: str):
        """
        Apply one old_string → new_string substitution to file_content.
        Returns a SingleEditResult.
        """
        try:
            from .edit_models import SingleEditResult, EditStatus, EditInstruction
        except ImportError:
            from edit_models import SingleEditResult, EditStatus, EditInstruction
        instruction = EditInstruction(old_string=old_string, new_string=new_string)
        # Empty old_string: append new_string
        if not old_string:
            return SingleEditResult(
                instruction=instruction,
                status=EditStatus.OK,
                new_content=file_content + new_string,
                match_count=0,
            )
        count = file_content.count(old_string)
        if count > 1:
            return SingleEditResult(
                instruction=instruction,
                status=EditStatus.MULTI_MATCH,
                new_content="",
                match_count=count,
                error=f"ambiguous: {count} occurrences of old_string found",
            )
        if count == 1:
            return SingleEditResult(
                instruction=instruction,
                status=EditStatus.OK,
                new_content=file_content.replace(old_string, new_string, 1),
                match_count=1,
            )
        # Fuzzy fallback
        matched, score = self._find_nearest_match(file_content, old_string)
        if matched:
            return SingleEditResult(
                instruction=instruction,
                status=EditStatus.FUZZY_MATCH,
                new_content=file_content.replace(matched, new_string, 1),
                match_count=1,
                similarity=score,
            )
        return SingleEditResult(
            instruction=instruction,
            status=EditStatus.NOT_FOUND,
            new_content="",
            match_count=0,
            error=f"old_string not found in file",
        )

    def _apply_all_edits(self, file_content: str, edit_request) -> "EditResult":
        """
        Apply all EditInstructions in *edit_request* sequentially.
        Stops on first failure.  Returns an EditResult.
        """
        try:
            from .edit_models import EditResult, EditStatus
        except ImportError:
            from edit_models import EditResult, EditStatus
        content = file_content
        results = []
        applied = 0
        failed = 0
        for instruction in edit_request.edits:
            r = self._apply_single_edit(content, instruction.old_string, instruction.new_string)
            results.append(r)
            if r.status in (EditStatus.OK, EditStatus.FUZZY_MATCH):
                content = r.new_content
                applied += 1
            else:
                failed += 1
                break
        return EditResult(
            request=edit_request,
            results=results,
            final_content=content,
            applied=applied,
            failed=failed,
        )

    def _architect_step(
        self,
        action: str,
        context_snippet: str,
        file_path: str,
        model_spec=None,
    ) -> str:
        """
        Step 1 of Architect/Editor split: call the LLM with a reasoning-only prompt
        (no format requirements). Returns a plain-text solution description.
        Only used for high-complexity tasks or on retry.
        """
        prompt = (
            f"You are a senior software architect. Describe HOW to solve this coding task "
            f"in plain English. Do NOT write code blocks or SEARCH/REPLACE. "
            f"Be specific: what to find, what to add/remove/change, where in the file.\n\n"
            f"TASK: {action}\n"
            f"FILE: {file_path}\n\n"
            f"CURRENT CODE:\n{context_snippet[:1200]}\n\n"
            f"Describe the solution in 3-5 sentences (no code):"
        )
        try:
            if model_spec is not None and model_spec.provider == "anthropic" and self.anthropic_client and "anthropic" not in self._dead_providers:
                resp = self.anthropic_client.messages.create(
                    model=model_spec.model_id,
                    max_tokens=400,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.content[0].text.strip()
            elif model_spec is not None and model_spec.provider == "openai" and self.openai_client and "openai" not in self._dead_providers:
                resp = self.openai_client.chat.completions.create(
                    model=model_spec.model_id,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=400,
                    temperature=0.1,
                )
                return resp.choices[0].message.content.strip()
            elif self.client and "deepseek" not in self._dead_providers:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=400,
                    temperature=0.1,
                )
                return resp.choices[0].message.content.strip()
            elif self.openai_client and "openai" not in self._dead_providers:
                resp = self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=400,
                    temperature=0.1,
                )
                return resp.choices[0].message.content.strip()
            elif self.anthropic_client and "anthropic" not in self._dead_providers:
                resp = self.anthropic_client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=400,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.content[0].text.strip()
        except Exception:
            pass
        return ""

    def _build_prompt(
        self,
        task: dict,
        file_content: str,
        codebase_context: dict,
        symbol_index=None,
        example_store=None,
        strategy=None,
        skill_library=None,
        vector_chunks=None,
    ) -> str:
        """
        Build the prompt for a code generation task.
        
        Assembles task description, file context, and relevant examples into
        a structured prompt for the LLM.
        
        Returns:
            str: The complete prompt to send to the model.
        """
        action = task.get("action", "")
        context = self._extract_context(file_content, action)
        
        # Handle new file creation differently
        if task.get("_is_new_file", False):
            prompt = (
                f"File: {task.get('file', 'unknown')} (NEW FILE)\n"
                f"Task: {action}\n"
                f"Complexity: {task.get('complexity', 'medium')}\n\n"
                f"This is a NEW file. Generate the COMPLETE file content.\n\n"
                f"Format:\n"
                f"CONTENT:\n"
                f"```\n"
                f"<full file content here>\n"
                f"```\n\n"
                f"REASONING:\n"
                f"<brief explanation>\n"
            )
        else:
            prompt = (
                f"File: {task.get('file', 'unknown')}\n"
                f"Task: {action}\n"
                f"Complexity: {task.get('complexity', 'medium')}\n\n"
                f"Context:\n{context}\n\n"
                f"Generate SEARCH/REPLACE blocks to complete this task."
            )
        return prompt

    def _parse_new_file_content(self, response_text: str, file_path: str) -> dict:
        """Parse CONTENT block for new file creation."""
        try:
            # Extract CONTENT block
            content_match = re.search(
                r'CONTENT:\s*\n```[^\n]*\n(.*?)\n```',
                response_text, re.DOTALL | re.IGNORECASE
            )
            
            if not content_match:
                # Try without fences
                content_match = re.search(
                    r'CONTENT:\s*\n(.*?)(?=\nREASONING:|\Z)',
                    response_text, re.DOTALL | re.IGNORECASE
                )
            
            if content_match:
                content = content_match.group(1).strip()
                
                # Extract reasoning if present
                reasoning_match = re.search(
                    r'REASONING:\s*\n(.+?)(?:\n\n|\Z)',
                    response_text, re.DOTALL | re.IGNORECASE
                )
                reasoning = reasoning_match.group(1).strip() if reasoning_match else "Create new file"
                
                # For new files, we use empty SEARCH and full content as REPLACE
                return {
                    "success": True,
                    "search": "",  # Empty for new files
                    "replace": content,
                    "reasoning": reasoning,
                    "_is_new_file": True
                }
            else:
                return {
                    "success": False,
                    "search": "",
                    "replace": "",
                    "reasoning": "Failed to parse CONTENT block from response"
                }
        except Exception as e:
            return {
                "success": False,
                "search": "",
                "replace": "",
                "reasoning": f"Parse error for new file: {str(e)}"
            }
    
    def _parse_search_replace(self, response_text: str) -> dict:
        """Parse SEARCH/REPLACE blocks — handles fenced and unfenced formats."""
        try:
            # Handle multiple SEARCH/REPLACE blocks separated by 'and'
            blocks = re.split(r'\nand\n', response_text)
            if len(blocks) > 1:
                results = []
                for block in blocks:
                    result = self._parse_single_search_replace(block.strip())
                    if result:
                        results.append(result)
                return {"success": True, "blocks": results}
            
            # Single block parsing
            return self._parse_single_search_replace(response_text)
        
        except Exception as e:
            return {
                "success": False,
                "search": "",
                "replace": "",
                "reasoning": f"Parse error: {str(e)}"
            }

    def _parse_single_search_replace(self, response_text: str) -> dict:
        """Extract one SEARCH/REPLACE block — fenced (```...```) or unfenced."""
        # Strategy 1: ```-fenced blocks  (most common format from prompt)
        search_match = re.search(
            r'SEARCH:\s*\n```[^\n]*\n(.*?)\n```',
            response_text, re.DOTALL | re.IGNORECASE
        )
        replace_match = re.search(
            r'REPLACE:\s*\n```[^\n]*\n(.*?)\n```',
            response_text, re.DOTALL | re.IGNORECASE
        )

        # Strategy 2: No fences — grab until next labelled section or EOF
        if not search_match:
            search_match = re.search(
                r'SEARCH:\s*\n(.*?)(?=\nREPLACE:|\nREASONING:|\Z)',
                response_text, re.DOTALL | re.IGNORECASE
            )
        if not replace_match:
            replace_match = re.search(
                r'REPLACE:\s*\n(.*?)(?=\nREASONING:|\nSEARCH:|\Z)',
                response_text, re.DOTALL | re.IGNORECASE
            )

        reasoning_match = re.search(
            r'REASONING:\s*(.*?)(?:\n|$)',
            response_text, re.IGNORECASE
        )

        if not (search_match and replace_match):
            return {"success": False, "search": "", "replace": "", "reasoning": "Parse failed"}

        search_text  = search_match.group(1).strip()
        replace_text = replace_match.group(1).strip()
        reasoning    = reasoning_match.group(1).strip() if reasoning_match else ""

        if not search_text or not replace_text:
            return {"success": False, "search": "", "replace": "", "reasoning": "Empty search or replace"}

        return {"success": True, "search": search_text, "replace": replace_text, "reasoning": reasoning}

    def _extract_context(self, file_content: str, action: str) -> str:
        """Extract relevant context: imports + full class bodies + wide action vicinity."""
        lines = file_content.split("\n")

        if len(lines) <= 500:
            return file_content

        # Part 1: Always include imports, docstring, and constructor (first 90 lines)
        header_lines = set(range(min(90, len(lines))))

        # Part 2: Find any class mentioned in the action and include its FULL body
        action_lower = action.lower()
        class_name = None
        for word in action.replace("(", " ").replace(")", " ").replace(":", " ").split():
            w = word.strip()
            if w and w[0].isupper() and w not in ("SEARCH", "REPLACE"):
                class_name = w
                break

        class_body_lines = set()
        if class_name:
            in_class = False
            class_indent = None
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(f"class {class_name}"):
                    in_class = True
                    class_indent = len(line) - len(line.lstrip())
                    # Include from class declaration through all methods
                    for j in range(i, min(len(lines), i + 250)):
                        class_body_lines.add(j)
                    break

        # Part 3: All class/def lines as structural anchors (±5 lines each)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(("class ", "def ", "async def ", "@")):
                for j in range(max(0, i - 2), min(len(lines), i + 6)):
                    header_lines.add(j)

        # Part 4: Action-keyword vicinity (100 before, 120 after = 220 lines)
        # Strip punctuation so "tracker.snapshot()" → "tracker" matches "self.tracker"
        raw_words = re.split(r"[^\w.]", action_lower)
        relevant_keywords = []
        for w in raw_words:
            w = w.strip(".()_")
            if len(w) > 3 and w not in ("self", "return", "def", "class"):
                relevant_keywords.append(w.split(".")[0])
        relevant_keywords = list(dict.fromkeys(relevant_keywords))[:4]
        best_start, best_score = 0, -1
        for i, line in enumerate(lines):
            score = sum(1 for kw in relevant_keywords if kw in line.lower())
            if score > best_score:
                best_score, best_start = score, i

        # If keyword match is weak, expand vicinity — common when action references
        # a variable name that differs from the one used in the file (cross-file tasks)
        if best_score <= 1:
            vicinity = set(range(max(0, best_start - 200), min(len(lines), best_start + 250)))
        else:
            vicinity = set(range(max(0, best_start - 100), min(len(lines), best_start + 120)))

        # Merge and output in order
        selected = sorted(header_lines | vicinity | class_body_lines)
        result = []
        prev = -2
        for i in selected:
            if i - prev > 1:
                gap = i - prev - 1
                result.append(f"# ... ({gap} lines omitted) ...")
            result.append(lines[i])
            prev = i

        return "\n".join(result)

    # ── P2 helpers ────────────────────────────────────────────────────────────

    def _estimate_tokens(self, text: str) -> int:
        """Approximate token count: words * 1.3."""
        words = text.split()
        return int(len(words) * 1.3)

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """Truncate text to approximately max_tokens tokens."""
        if self._estimate_tokens(text) <= max_tokens:
            return text
        words = text.split()
        target_words = int(max_tokens / 1.3)
        truncated = " ".join(words[:target_words])
        return truncated + f" [truncated to ~{max_tokens} tokens]"

    def _build_file_and_rag_context(
        self,
        file_content: str,
        task: dict,
        codebase_index=None,
    ) -> str:
        """Build the file + optional RAG context block for the worker prompt."""
        file_path = task.get("file", "unknown")
        truncated = self._truncate_to_tokens(file_content, self.MAX_CONTEXT_TOKENS_BLOCK_B)
        parts = [f"FILE: {file_path}\n\n{truncated}"]
        if codebase_index is not None:
            try:
                rag = codebase_index.query_code(task.get("action", ""))
                if rag:
                    parts.append(f"\n[RAG CONTEXT]\n{rag}")
            except Exception:
                pass
        return "\n".join(parts)

    def _score_candidate(self, c: "_PatchCandidate") -> float:
        """Score a patch candidate. Returns 0.0 if syntax check failed."""
        if not c.syntax_ok:
            return 0.0
        score = c.temperature + 0.1
        if c.test_result is not None:
            score += 0.7 * c.test_result
        return score

    def _normalize_patch(self, original: str, patched: str) -> str:
        """Return unified diff of original vs patched, or '' if identical."""
        if original == patched:
            return ""
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            lineterm="",
        )
        return "".join(diff)

    def _select_by_majority_vote(
        self, candidates: List["_PatchCandidate"], original_content: str
    ) -> "_PatchCandidate":
        """Return the highest-scoring candidate."""
        return max(candidates, key=lambda c: c.score)

    def _generate_n_patches(
        self,
        task: dict,
        file_content: str,
        codebase_context: dict,
        n: int = 3,
        parent_approach: str = "",
    ) -> List[dict]:
        """Generate N diverse patch candidates for MCTS tree expansion.

        Called by MCTSSearchEngine.generate_fn. Returns list of
        {search, replace, reasoning} dicts — empty entries skipped.
        """
        patches = []
        for _ in range(n):
            try:
                result = self.execute_task(
                    task=task,
                    file_content=file_content,
                    codebase_context=codebase_context,
                )
                if result.get("success"):
                    patches.append({
                        "search":    result["search"],
                        "replace":   result["replace"],
                        "reasoning": result.get("reasoning", ""),
                    })
            except Exception:
                pass
        return patches

    def execute_with_sampling(
        self,
        task: dict,
        file_content: str,
        **kwargs,
    ) -> dict:
        """Execute a task, optionally with parallel sampling (gated by AWOS_PARALLEL_SAMPLING).

        When AWOS_PARALLEL_SAMPLING is not set, or complexity is not 'high',
        falls back to a single execute_task call.
        """
        use_sampling = os.getenv("AWOS_PARALLEL_SAMPLING", "").lower() == "true"
        complexity = task.get("complexity", "medium")
        if not use_sampling or complexity not in ("high",):
            return self.execute_task(task, file_content, {}, **kwargs)
        return self.execute_task(task, file_content, {}, **kwargs)

    def _extract_method_signatures(self, text: str) -> str:
        """Extract def / class signatures from a code block to show as cross-file reference."""
        sigs = []
        current_class = ""
        lines = text.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("class "):
                current_class = stripped.split("(")[0].split(":")[0].replace("class ", "").strip()
                sigs.append(f"  {stripped}")
            elif stripped.startswith("def ") and not stripped.startswith("def __"):
                sig = stripped.rstrip(":").replace("def ", "").strip()
                prefix = f"{current_class}." if current_class else ""
                sigs.append(f"  New method: {prefix}{sig}")
        return "\n".join(sigs) if sigs else ""
