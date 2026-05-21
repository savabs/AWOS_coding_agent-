"""
Worker Module: Executes atomic micro-tasks using DeepSeek Coder.

Each task receives one specific action and narrow context window.
Cost: ~$0.001-0.002 per task. Accuracy improves due to micro-context.
"""

import json
import os
import re
from typing import Optional
from openai import OpenAI  # For DeepSeek API access
from anthropic import Anthropic  # For Haiku/Sonnet/Opus
try:
    from .budget_ledger import get_ledger
except ImportError:
    from budget_ledger import get_ledger

_MONTHLY_BUDGET = float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0"))


class Worker:
    """Uses DeepSeek Coder to generate SEARCH/REPLACE code changes."""
    
    def __init__(self, api_key: Optional[str] = None, model: str = "deepseek-chat"):
        """Initialize Worker with DeepSeek primary and Haiku fallback."""
        deepseek_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        
        if deepseek_key and anthropic_key:
            self.client = OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")
        else:
            self.client = None
        
        if anthropic_key and deepseek_key:
            self.anthropic_client = Anthropic(api_key=anthropic_key)
        else:
            self.anthropic_client = None
        
        if not self.client and not self.anthropic_client:
            raise ValueError("Set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY")
        
        self.model = model
        self.fallback_model = "claude-haiku-4-5"
    
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
        
        # Build micro-context: show only relevant lines
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

        prompt = f"""You are a senior software engineer executing a precise code change.
{vector_section}{critique_block}{strategy_preamble}{prev_context_section}{cross_file_section}
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

OUTPUT FORMAT (exact structure required):
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

Do not add markdown backticks inside the code blocks. The SEARCH text must be copy-pasteable from the file.{strategy_suffix}"""
        
        response_text = ""
        model_used = "unknown"
        
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
        if use_provider == "deepseek" and self.client:
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
                if tracker:
                    inp = response.usage.prompt_tokens
                    out = response.usage.completion_tokens
                    cost = (inp / 1_000_000) * inp_price + (out / 1_000_000) * out_price
                    tracker.record("execution", model_label, inp, out, cost)
            except Exception as e:
                print(f"[WORKER] {model_label} failed (attempt {attempt}): {str(e)[:80]}")
        
        elif use_provider == "anthropic" and self.anthropic_client:
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
                if tracker:
                    inp = response.usage.input_tokens
                    out = response.usage.output_tokens
                    cost = (inp / 1_000_000) * inp_price + (out / 1_000_000) * out_price
                    tracker.record("execution", model_label, inp, out, cost)
            except RuntimeError:
                raise
            except Exception as e:
                print(f"[WORKER] {model_label} failed (attempt {attempt}): {str(e)[:80]}")
        
        # Hard fallback: Haiku if primary failed
        if not response_text and self.anthropic_client and use_model != self.fallback_model:
            _allowed, _reason = get_ledger().check_budget(0.017, _MONTHLY_BUDGET)
            if not _allowed:
                raise RuntimeError(f"[BUDGET HARD STOP] {_reason}")
            try:
                response = self.anthropic_client.messages.create(
                    model=self.fallback_model,
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}],
                )
                response_text = response.content[0].text
                model_used = "Haiku (fallback)"
                if tracker:
                    inp = response.usage.input_tokens
                    out = response.usage.output_tokens
                    cost = (inp / 1_000_000) * 1.00 + (out / 1_000_000) * 5.00
                    tracker.record("execution", "Haiku fallback", inp, out, cost)
            except Exception as e:
                raise RuntimeError(f"All providers failed: {str(e)}")
        
        if not response_text:
            raise RuntimeError("No API key available or all providers failed")
        
        # Parse SEARCH/REPLACE from response
        result = self._parse_search_replace(response_text)
        
        # ── Phase 7: Self-Verification ─────────────────────────────────────
        if result["success"]:
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
                )
            else:
                return {
                    "success": False,
                    "error": f"Failed to parse SEARCH/REPLACE: {response_text[:200]}",
                    "model_used": model_used
                }
        
        result["model_used"] = model_used
        return result
    
    def _extract_context(self, file_content: str, action: str) -> str:
        """Extract relevant context: always include imports + class headers + action vicinity."""
        lines = file_content.split("\n")
        
        if len(lines) <= 120:
            return file_content
        
        # Part 1: Always include imports and top-level defs (first 25 lines)
        header_lines = set(range(min(25, len(lines))))
        
        # Part 2: Class/def lines (structural anchors)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(("class ", "def ", "async def ", "@")):
                for j in range(max(0, i - 1), min(len(lines), i + 3)):
                    header_lines.add(j)
        
        # Part 3: Action-keyword vicinity (50 lines around best match)
        relevant_keywords = [w for w in action.lower().split() if len(w) > 3][:4]
        best_start, best_score = 0, -1
        for i, line in enumerate(lines):
            score = sum(1 for kw in relevant_keywords if kw in line.lower())
            if score > best_score:
                best_score, best_start = score, i
        
        vicinity = set(range(max(0, best_start - 10), min(len(lines), best_start + 60)))
        
        # Merge and output in order
        selected = sorted(header_lines | vicinity)
        result = []
        prev = -2
        for i in selected:
            if i - prev > 1:
                result.append("# ...")
            result.append(lines[i])
            prev = i
        
        return "\n".join(result)
    
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
            if model_spec is not None and model_spec.provider == "anthropic" and self.anthropic_client:
                resp = self.anthropic_client.messages.create(
                    model=model_spec.model_id,
                    max_tokens=400,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.content[0].text.strip()
            elif self.client:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=400,
                    temperature=0.1,
                )
                return resp.choices[0].message.content.strip()
            elif self.anthropic_client:
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
        """
        action = task.get("action", "")
        complexity = task.get("complexity", "medium")
        context = self._extract_context(file_content, action)
        
        prompt = f"""Task: {action}
Complexity: {complexity}

File Context:
{context}

Generate a SEARCH/REPLACE block to complete this task."""
        return prompt

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
        
        prompt = (
            f"File: {task.get('file', 'unknown')}\n"
            f"Task: {action}\n"
            f"Complexity: {task.get('complexity', 'medium')}\n\n"
            f"Context:\n{context}\n\n"
            f"Generate SEARCH/REPLACE blocks to complete this task."
        )
        return prompt

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

        if len(lines) <= 120:
            return file_content

        # Part 1: Always include imports and module docstring (first 30 lines)
        header_lines = set(range(min(30, len(lines))))

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
