#!/usr/bin/env python3
"""
Unified Agent — Coding App entry point on the AWOS kernel.

Routes requests to handlers, executes via Orchestrator, persists to .awos/.
See VISION.md for AWOS identity. The kernel optimizes quality × speed ÷ cost.

Usage:
    awos chat                  # Interactive menu
    awos "your question"       # Single query, auto-route
"""

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
import uuid

logger = logging.getLogger(__name__)

from anthropic import Anthropic
from openai import OpenAI

from chat_orchestrator import ChatOrchestrator, IntentParser
from session_memory import SessionMemory, ChatSessionManager, MemoryStrategy
from model_selector import ModelSelector, CheapModel, ModelCost
from token_tracker import TokenTracker
from orchestrator import Orchestrator
from self_improver import SelfImprover
from coding_model_router import CodingModelRouter, CodingTask
from response_cache import ResponseCache
from memory.vector_memory import VectorMemory
from memory.codebase_index import CodebaseIndex
from budget_ledger import BudgetLedger, get_ledger
from core.performance_tracker import ToolPerformanceTracker


class HandlerType(Enum):
    """Handler types by cost efficiency"""
    WORKFLOW = "workflow"           # Lightweight status/workflow (cheapest)
    REASONING = "reasoning"         # Architecture/complex (moderate, deep thinking)
    CODING = "coding"               # Code changes (moderate cost, high value)
    REVIEW = "review"               # Code review/analysis (cheaper than coding)
    PLANNER_WORKER = "planner_worker"  # Hierarchical planning + execution
    SELF_IMPROVEMENT = "self_improvement"  # Self-improvement on this project
    UNKNOWN = "unknown"             # Fallback


@dataclass
class RoutingDecision:
    """Routing result"""
    handler: HandlerType
    confidence: float  # 0-1
    reason: str
    tokens_estimate: int
    cost_estimate: float


class RequestRouter:
    """Intelligently route requests to optimal handler using semantic understanding"""

    # Fallback regex patterns (used when API unavailable)
    FALLBACK_PATTERNS = {
        HandlerType.REASONING: [
            r'(?:explain|describe|tell).*(?:this|the|me|about)',
            r'(?:how|what|best).*(?:design|architect|scale|approach)',
            r'(?:why|pros|cons).*(?:approach|method)',
        ],
        HandlerType.CODING: [
            r'(?:implement|fix|build|create|write|code).*(?:login|auth|feature|bug|function)',
            r'(?:refactor|optimize|improve).*code',
        ],
        HandlerType.REVIEW: [
            r'(?:review|analyze|check|audit).*(?:code|security)',
            r'(?:is|looks).*(?:correct|right|good)',
        ],
        HandlerType.WORKFLOW: [
            r'(?:what|where).*(?:next|do|am)',
            r'(?:show|list).*(?:status|progress|plan)',
        ],
    }

    # Cost/token estimates for each handler type
    TOKENS_MAP = {
        HandlerType.WORKFLOW: 300,      # Cheap: just status
        HandlerType.REVIEW: 1500,       # Moderate: analyze code
        HandlerType.CODING: 2500,       # Higher: code generation
        HandlerType.REASONING: 4000,    # Expensive: deep reasoning
        HandlerType.PLANNER_WORKER: 3000,  # Planner ($0.03-0.05) + Workers ($0.001-0.002 each)
        HandlerType.SELF_IMPROVEMENT: 2000,  # Cheap planner + workers
    }

    COST_MAP = {
        HandlerType.WORKFLOW: 0.002,    # $0.002 (cached status)
        HandlerType.REVIEW: 0.008,      # $0.008 (code review)
        HandlerType.CODING: 0.015,      # $0.015 (code generation)
        HandlerType.REASONING: 0.050,   # $0.050 (reasoning model)
        HandlerType.PLANNER_WORKER: 0.10,  # $0.03 planning + $0.001-0.002 per task
        HandlerType.SELF_IMPROVEMENT: 0.02,  # $0.0005 (cheap plan) + $0.01-0.015 (workers)
    }

    def __init__(self):
        """Initialize semantic router"""
        # Try to initialize Anthropic client
        api_key = os.getenv("ANTHROPIC_API_KEY")
        self.client = Anthropic(api_key=api_key) if api_key else None
        self.routing_cache = {}
        
        if not self.client:
            print("⚠️  ANTHROPIC_API_KEY not set - using fallback regex routing")
            self.use_semantic = False
        else:
            self.use_semantic = True

    def route(self, user_input: str) -> RoutingDecision:
        """Route request using semantic understanding (fallback to regex if needed)"""
        
        # Check cache first
        cache_key = user_input.lower().strip()
        if cache_key in self.routing_cache:
            return self.routing_cache[cache_key]

        # Try semantic routing if available, fallback to regex
        if self.use_semantic:
            decision = self._route_semantic(user_input)
        else:
            decision = self._route_regex(user_input)
        
        # Cache for session
        self.routing_cache[cache_key] = decision
        return decision

    def _route_semantic(self, user_input: str) -> RoutingDecision:
        """Route using Claude Haiku semantic understanding"""
        routing_prompt = f"""Classify this user request into ONE category. Return ONLY the category name.

Request: "{user_input}"

Categories with examples:
- PLANNER_WORKER: "implement a new feature", "add authentication system", "refactor the database layer", "build a caching system" (multi-step, architectural)
- REASONING: "explain architecture", "how should I design auth", "what's best practice for scaling" (design questions, no code changes)
- CODING: "implement login", "fix this bug", "write a function", "add error handling" (single-file, straightforward)
- REVIEW: "review my code", "check for security issues", "analyze performance" (analysis only)
- WORKFLOW: "what's next", "show status", "where am I in the project" (project status)

Respond with ONLY the category name: PLANNER_WORKER | REASONING | CODING | REVIEW | WORKFLOW"""

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=10,
                messages=[{"role": "user", "content": routing_prompt}]
            )
            
            handler_text = response.content[0].text.strip().upper()
            # Handle both old and new routing categories
            if "PLANNER" in handler_text or "WORKER" in handler_text:
                handler = HandlerType.PLANNER_WORKER
            else:
                handler = self._parse_handler(handler_text)
            
            return RoutingDecision(
                handler=handler,
                confidence=0.95,  # Semantic models are very reliable
                reason=f"Semantic routing via Claude Haiku: {handler.value}",
                tokens_estimate=self.TOKENS_MAP.get(handler, 2000),
                cost_estimate=0.00005 + self.COST_MAP.get(handler, 0.01),  # Add routing cost
            )
            
        except Exception as e:
            # Fallback to regex
            print(f"⚠️  Semantic routing error: {e}, falling back to regex")
            return self._route_regex(user_input)

    def _route_regex(self, user_input: str) -> RoutingDecision:
        """Route using regex patterns (fast fallback)"""
        user_input_lower = user_input.lower().strip()
        best_match = None
        best_confidence = 0.0

        for handler_type, patterns in self.FALLBACK_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, user_input_lower)
                if match:
                    confidence = 0.75 + len(pattern) * 0.001
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_match = handler_type

        if best_match is None:
            # Smart default
            if any(word in user_input_lower for word in ['help', 'what can', 'how do']):
                best_match = HandlerType.WORKFLOW
                best_confidence = 0.6
            else:
                best_match = HandlerType.REASONING
                best_confidence = 0.5

        return RoutingDecision(
            handler=best_match,
            confidence=best_confidence,
            reason=f"Regex routing (fallback): {best_match.value}",
            tokens_estimate=self.TOKENS_MAP.get(best_match, 2000),
            cost_estimate=self.COST_MAP.get(best_match, 0.01),
        )

    @staticmethod
    def _parse_handler(text: str) -> HandlerType:
        """Parse handler type from semantic response"""
        text = text.upper().strip()
        
        if "SELF_IMPROVEMENT" in text or "IMPROVE" in text:
            return HandlerType.SELF_IMPROVEMENT
        elif "REASONING" in text:
            return HandlerType.REASONING
        elif "CODING" in text:
            return HandlerType.CODING
        elif "REVIEW" in text:
            return HandlerType.REVIEW
        elif "WORKFLOW" in text:
            return HandlerType.WORKFLOW
        else:
            return HandlerType.REASONING  # Safe default


class UnifiedAgent:
    """Single smart entry point for all requests"""

    def __init__(self):
        """Initialize agent"""
        self.project_root = self._detect_codebase_root()
        self.router = RequestRouter()
        self.chat_orchestrator = ChatOrchestrator()
        self.total_cost = 0.0
        self.total_tokens = 0

        # Guardrail: read budget cap from env (default $20/month)
        _budget = float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0"))

        # Debug mode: show model/cost/tricks info after each response
        self.debug_mode = os.getenv("AWOS_DEBUG", "").lower() in ("1", "true", "yes")
        self._last_model = ""      # set by _call_* helpers
        self._last_cost  = 0.0     # set by _call_* helpers

        # Cost optimization
        self.model_selector = ModelSelector()  # Choose cheap models (no Opus)
        self.token_tracker = TokenTracker(monthly_budget=_budget, monthly_token_target=50_000_000)
        self.response_cache = ResponseCache(ttl_seconds=900)  # 15-min cache

        # Planner-Worker orchestrator for hierarchical execution
        self.planner_worker = Orchestrator(tracker=self.token_tracker)

        # Self-Improver for automatic improvements to this project
        self.self_improver = SelfImprover(tracker=self.token_tracker)

        # ── NEW: Persistent Vector Memory ──────────────────────────────────────
        self.vector_memory = VectorMemory(persist_dir=".awos/memory")

        # ── NEW: Semantic Codebase Index ──────────────────────────────────────
        self.codebase_index = CodebaseIndex(project_root=self.project_root)
        # Lazy index: build on first use (expensive, ~2-5s for large projects)
        self._codebase_index_built = False

        # ── NEW: Persistent Budget Ledger ────────────────────────────────────
        self.budget_ledger = get_ledger()

        # ── NEW: Tool Performance Tracker ────────────────────────────────────
        self.performance = ToolPerformanceTracker(persist_dir=".awos")

        # Session memory
        self.session_manager = ChatSessionManager()
        self.current_session_id = str(uuid.uuid4())[:8]  # Short ID

    # ===== Internal Model Helpers =====

    def _read_relevant_context(self, user_input: str, max_files: int = 3, max_lines: int = 120) -> str:
        """
        Retrieve code context relevant to the user's request.

        Uses semantic search via CodebaseIndex (primary) with keyword
        fallback (secondary) if index is not yet built.
        """
        # ── PRIMARY: Semantic codebase search ──────────────────────────
        if not self._codebase_index_built:
            try:
                self.codebase_index.index_project()
                self._codebase_index_built = True
            except Exception as e:
                logger.warning("CodebaseIndex build failed: %s", e)

        if self._codebase_index_built:
            try:
                semantic_context = self.codebase_index.get_context_for_query(
                    user_input, max_chunks=max_files
                )
                if semantic_context and semantic_context != "(no relevant code found)":
                    return semantic_context
            except Exception as e:
                logger.warning("Semantic search failed: %s", e)

        # ── SECONDARY: Legacy keyword search (fallback) ────────────────
        keywords = [w for w in user_input.lower().split() if len(w) > 3][:6]
        py_files = [
            f for f in self.project_root.rglob("*.py")
            if "__pycache__" not in str(f) and ".git" not in str(f)
        ]

        scored = []
        for f in py_files:
            name_score = sum(1 for kw in keywords if kw in f.stem.lower())
            content_score = 0
            try:
                snippet = f.read_text(errors="ignore")[:2000].lower()
                content_score = sum(1 for kw in keywords if kw in snippet)
            except Exception:
                pass
            scored.append((name_score * 3 + content_score, f))

        scored.sort(reverse=True)
        top_files = [f for score, f in scored[:max_files] if score >= 1]
        if not top_files:
            top_files = [f for _score, f in scored[:max_files]]

        parts = []
        for f in top_files:
            try:
                content = f.read_text(errors="ignore")
                lines = content.split("\n")[:max_lines]
                rel = f.relative_to(self.project_root)
                parts.append(f"# {rel}\n" + "\n".join(lines))
            except Exception:
                pass
        return "\n\n".join(parts) if parts else "(no code context available)"

    def _call_deepseek(self, system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
        """Call DeepSeek API, return response text."""
        # Guardrail pre-check
        allowed, guard_msg = self.token_tracker.check_guard(estimated_cost=0.002)
        if not allowed:
            return guard_msg
        if guard_msg:
            print(guard_msg)

        # Budget hard stop
        if self.budget_ledger:
            allowed_bl, reason_bl = self.budget_ledger.check_budget(estimated_cost=0.01)
            if not allowed_bl:
                return f"Budget blocked: {reason_bl}"
            if reason_bl:
                logger.warning(reason_bl)

        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return self._call_anthropic(system_prompt, user_prompt, max_tokens)  # Fall back to Haiku
        try:
            client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=max_tokens,
                temperature=0.3
            )
            text = response.choices[0].message.content
            if self.token_tracker:
                inp = response.usage.prompt_tokens
                out = response.usage.completion_tokens
                cost = (inp / 1_000_000) * 0.14 + (out / 1_000_000) * 0.28
                self.token_tracker.record("coding", "DeepSeek V4 Flash", inp, out, cost)
                self.budget_ledger.record("coding", "DeepSeek V4 Flash", inp, out, cost)
                self._last_model, self._last_cost = "DeepSeek V4 Flash", cost
            return text
        except Exception as e:
            return f"DeepSeek error: {str(e)[:120]}"

    def _call_anthropic(self, system_prompt: str, user_prompt: str,
                        max_tokens: int = 2048,
                        model: str = "claude-haiku-4-5") -> str:
        """Call Anthropic API, return response text."""
        # Estimate cost for guardrail check
        rate_in  = 1.00 if "haiku" in model else (3.00 if "sonnet" in model else 5.00)
        rate_out = 5.00 if "haiku" in model else (15.0 if "sonnet" in model else 25.0)
        est_cost = (len(user_prompt) / 4 / 1_000_000) * rate_in + (500 / 1_000_000) * rate_out
        allowed, guard_msg = self.token_tracker.check_guard(estimated_cost=est_cost)
        if not allowed:
            return guard_msg
        if guard_msg:
            print(guard_msg)

        # Budget hard stop
        if self.budget_ledger:
            allowed_bl, reason_bl = self.budget_ledger.check_budget(estimated_cost=est_cost)
            if not allowed_bl:
                return f"Budget blocked: {reason_bl}"
            if reason_bl:
                logger.warning(reason_bl)

        try:
            response = self.router.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            text = response.content[0].text
            if self.token_tracker:
                inp = response.usage.input_tokens
                out = response.usage.output_tokens
                cost = (inp / 1_000_000) * rate_in + (out / 1_000_000) * rate_out
                model_label = ("Claude Haiku 4.5" if "haiku" in model
                               else ("Claude Sonnet 4.6" if "sonnet" in model else "Claude Opus 4.6"))
                self.token_tracker.record("coding", model_label, inp, out, cost)
                self.budget_ledger.record("coding", model_label, inp, out, cost)
                self._last_model, self._last_cost = model_label, cost
            return text
        except Exception as e:
            return f"Anthropic error: {str(e)[:120]}"

    def _detect_codebase_root(self) -> Path:
        """Auto-detect project root from git, pyproject.toml, etc."""
        markers = ['.git', 'pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt', 'Makefile']
        current = Path.cwd()
        for candidate in [current] + list(current.parents):
            for marker in markers:
                if (candidate / marker).exists():
                    return candidate
        return current

    def _is_complex_coding_task(self, user_input: str) -> bool:
        """Detect if a coding task needs planner-worker (multi-step, multi-file)."""
        text = user_input.lower()
        
        # Strong signals: clearly multi-step or architectural
        strong_complex = [
            'from scratch', 'entire', 'full system', 'new module', 'new service',
            'new feature', 'authentication', 'database layer', 'api layer',
            'pipeline', 'integrate', 'end-to-end', 'end to end',
        ]
        # Moderate signals: likely multi-file but could be simple
        moderate_complex = [
            'implement', 'build', 'create', 'add', 'system', 'module',
            'service', 'layer', 'feature', 'workflow', 'refactor',
        ]
        # Signals that strongly suggest a simple, focused change
        simple_signals = [
            'typo', 'one line', 'quick fix', 'small change',
            'rename', 'just change', 'just update',
        ]
        
        strong = sum(2 for s in strong_complex if s in text)
        moderate = sum(1 for s in moderate_complex if s in text)
        simple = sum(3 for s in simple_signals if s in text)  # Simple heavily vetoes
        
        score = strong + moderate - simple
        return score >= 2

    def show_menu(self):
        """Show interactive menu"""
        self.session_manager.create_session(
            session_id=self.current_session_id,
            strategy=MemoryStrategy.HYBRID
        )

        budget = self.token_tracker.monthly_budget
        bstat = self.budget_ledger.get_status(monthly_budget=budget)
        debug_hint = "  AWOS_DEBUG=1 for verbose mode" if not self.debug_mode else "  debug mode ON"

        # Vector memory stats
        try:
            vm_stat = self.vector_memory.stats()
            vm_line = f"  Memory  : {vm_stat['total_interactions']} interactions | {vm_stat['embed_model'].split('/')[-1]}"
        except Exception:
            vm_line = "  Memory  : initializing..."

        # Performance stats
        try:
            perf = self.performance.get_stats(tool="_handle_coding", window=50)
            if perf["count"]:
                perf_line = f"  Success : {perf['success_rate']*100:.0f}% ({perf['count']} recent tasks)"
            else:
                perf_line = "  Success : —"
        except Exception:
            perf_line = "  Success : —"

        print("\n" + "═" * 60)
        print("  🤖  AWOS Coding Agent")
        print("═" * 60)
        print(f"  Session : {self.current_session_id}")
        print(f"  Budget  : ${bstat['spent']:.4f} / ${budget:.2f} ({bstat['percent_used']:.1f}% used)")
        print(vm_line)
        print(perf_line)
        print(f"  Project : {self.project_root.name}/")
        print(f"  Models  : DeepSeek → Haiku → Sonnet → Opus (escalates on need)")
        print(f"  {debug_hint}")
        print("═" * 60)
        print("  Commands: budget · recall · debug · help · exit\n")

        while True:
            try:
                user_input = input("You: ").strip()

                if not user_input:
                    continue

                if user_input.lower() in ["exit", "quit", "bye", "done"]:
                    print("👋 Session saved.")
                    # Persist vector memory before exit
                    try:
                        vm_stats = self.vector_memory.stats()
                        print(f"  💾 VectorMemory: {vm_stats['total_interactions']} interactions persisted")
                    except Exception:
                        pass
                    self.session_manager.save_session()
                    break

                if user_input.lower() == "budget":
                    self.budget_ledger.show_status(monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0")))
                    continue

                if user_input.lower() == "recall":
                    query = input("  Recall query: ").strip()
                    if query:
                        results = self._recall_past_solutions(query, n_results=5)
                        if results:
                            print(f"\n{results}\n")
                        else:
                            print("  (no similar past interactions found)\n")
                    continue

                if user_input.lower() == "debug":
                    self.debug_mode = not self.debug_mode
                    print(f"  Debug mode {'ON ✅' if self.debug_mode else 'OFF'}")
                    continue

                if user_input.lower() in ["help", "?"]:
                    self.show_help()
                    continue

                self._last_model, self._last_cost = "", 0.0
                self.handle_request(user_input)

                # Per-request cost tag
                if self._last_model:
                    print(f"  [{self._last_model} | ${self._last_cost:.5f}]")
                print()

            except KeyboardInterrupt:
                print("\n\n👋 Session saved.")
                self.session_manager.save_session()
                break

    def handle_request(self, user_input: str):
        """Handle a request end-to-end"""
        # Phase 1: Check for self-improvement intent FIRST (highest priority)
        if self.self_improver.is_enabled():
            improvement_request = self.self_improver.detect_self_improvement(user_input)
            if improvement_request.is_self_improvement:
                response = self._handle_self_improvement(user_input)
                print(f"Assistant: {response}")
                return
        
        # Phase 2: Get context from session memory
        session = self.session_manager.current_session
        session_context = ""
        if session and len(session.messages) > 0:
            session_context, context_tokens = session.get_context_for_request()

        # Add user message to session
        if session:
            # Estimate tokens (rough: ~4 chars per token)
            user_tokens = len(user_input) // 4 + 50
            session.add_message("user", user_input, user_tokens)

        # Phase 2.5: Recall similar past solutions from vector memory
        past_solutions = self._recall_past_solutions(user_input)
        if past_solutions:
            user_input = f"[PAST CONTEXT]\n{past_solutions}\n\n[NEW REQUEST]\n{user_input}"

        # Phase 3: Check response cache before routing (free hits)
        cache_key = self.response_cache.make_key("unified", user_input)
        cached = self.response_cache.get(cache_key)
        if cached:
            self.token_tracker.record_cache_hit(estimated_cost_saved=0.005)
            self.budget_ledger.record_cache_hit(saved_cost=0.005)
            print(f"Assistant: {cached}")
            print("[cache hit - $0.00]")
            return

        # Phase 4: Route intelligently
        routing = self.router.route(user_input)

        # Execute based on handler — all routing is invisible to the user
        if routing.handler == HandlerType.WORKFLOW:
            response = self._handle_workflow(user_input)
        elif routing.handler == HandlerType.PLANNER_WORKER:
            response = self._handle_complex_coding(user_input)
        elif routing.handler == HandlerType.CODING:
            if self._is_complex_coding_task(user_input):
                response = self._handle_complex_coding(user_input)
            else:
                response = self._handle_coding(user_input)
        elif routing.handler == HandlerType.REVIEW:
            response = self._handle_review(user_input)
        elif routing.handler == HandlerType.REASONING:
            response = self._handle_reasoning(user_input)
        elif routing.handler == HandlerType.SELF_IMPROVEMENT:
            response = self._handle_self_improvement(user_input)
        else:
            response = self._handle_reasoning(user_input)  # Safe fallback for unknowns

        # Cache the response (skip ephemeral handlers — workflow/self-improvement state changes)
        if routing.handler in (HandlerType.CODING, HandlerType.REVIEW, HandlerType.REASONING):
            self.response_cache.set(cache_key, response, model=routing.handler.value)

        # Add assistant response to session
        if session:
            response_tokens = len(response) // 4 + 100
            session.add_message("assistant", response, response_tokens)
            
            # Periodically save and check if summarization needed
            if session.should_summarize() and not session.summary:
                session.summary = session.create_summary()
                print(f"📝 Session summary created ({len(session.summary)} chars)")

        print(f"Assistant: {response}")

        # Phase 5: Persist interaction to vector memory for future recall
        try:
            self.vector_memory.store(
                text=f"Q: {user_input}\nA: {response}",
                metadata={
                    "session_id": self.current_session_id,
                    "handler": routing.handler.value,
                    "success": True,
                    "cost": getattr(self, '_last_cost', 0.0),
                }
            )
        except Exception as e:
            logger.warning("VectorMemory store failed: %s", e)

    # ===== Recall Helper =====

    def _recall_past_solutions(self, user_input: str, n_results: int = 3) -> str:
        """Retrieve similar past interactions from vector memory."""
        try:
            results = self.vector_memory.retrieve(user_input, n_results=n_results)
            if not results:
                return ""

            parts = ["# Relevant past solutions:"]
            for r in results:
                text = r["text"]
                meta = r["metadata"]
                # Truncate long responses
                preview = text[:400] + "..." if len(text) > 400 else text
                parts.append(f"- {preview}")

            return "\n".join(parts)
        except Exception as e:
            logger.warning("VectorMemory recall failed: %s", e)
            return ""

    # ===== Handler Methods =====

    def _handle_workflow(self, user_input: str) -> str:
        """Handle workflow/orchestration requests (cheapest)"""
        # Use chat orchestrator
        response, _ = self.chat_orchestrator.process_input(user_input)
        if not response:
            response = "I'm ready to help with workflow tasks. What would you like to do?"
        self.total_cost += 0.002
        self.total_tokens += 300
        return response

    def _handle_coding(self, user_input: str) -> str:
        """Handle simple code tasks via DeepSeek (no planner-worker overhead)."""
        router = CodingModelRouter()
        text = user_input.lower()
        if any(w in text for w in ["bug", "fix", "error", "broken", "crash"]):
            task_type = "bug_analysis"
        elif any(w in text for w in ["test", "unittest", "pytest"]):
            task_type = "test_generation"
        elif any(w in text for w in ["doc", "comment", "readme"]):
            task_type = "documentation"
        else:
            task_type = "refactoring"
        
        system_prompt = router.get_system_prompt(task_type)
        code_context = self._read_relevant_context(user_input, max_files=2, max_lines=100)
        user_prompt = f"Codebase context:\n{code_context}\n\nTask: {user_input}"
        raw = self._call_deepseek(system_prompt, user_prompt)

        # Self-verification: compile-check any code blocks before returning
        blocks = self._extract_code_blocks(raw)
        for block in blocks:
            try:
                compile(block, "<agent_output>", "exec")
            except SyntaxError as e:
                self.performance.record(
                    tool="_handle_coding",
                    model=self._last_model or "unknown",
                    task_type=task_type,
                    success=False,
                    cost=self._last_cost,
                    error_type="syntax_error",
                )
                return (
                    f"[Self-Verification] Generated code has a syntax error: {e}\n\n"
                    f"Please rephrase or simplify your request. Raw output:\n{raw}"
                )
        self.performance.record(
            tool="_handle_coding",
            model=self._last_model or "unknown",
            task_type=task_type,
            success=True,
            cost=self._last_cost,
        )
        return raw

    def _extract_code_blocks(self, text: str) -> list:
        """Extract fenced Python code blocks for compile-checking."""
        import re
        pattern = re.compile(r"```python\n(.*?)\n```", re.DOTALL)
        return [m.group(1) for m in pattern.finditer(text)]

    def _handle_review(self, user_input: str) -> str:
        """Handle code review/analysis via DeepSeek with relevant context."""
        router = CodingModelRouter()
        text = user_input.lower()
        task_type = "bug_analysis" if any(w in text for w in ["bug", "error", "crash", "broken"]) else "code_review"
        
        system_prompt = router.get_system_prompt(task_type)
        code_context = self._read_relevant_context(user_input, max_files=3, max_lines=150)
        user_prompt = f"Code to review:\n{code_context}\n\nReview request: {user_input}"
        return self._call_deepseek(system_prompt, user_prompt)

    def _handle_reasoning(self, user_input: str) -> str:
        """Handle architecture/design questions via Haiku (smarter, still cheap)."""
        router = CodingModelRouter()
        task_type = "architecture_planning"
        
        system_prompt = router.get_system_prompt(task_type)
        code_context = self._read_relevant_context(user_input, max_files=2, max_lines=80)
        user_prompt = f"Project context:\n{code_context}\n\nQuestion: {user_input}"
        return self._call_anthropic(system_prompt, user_prompt, max_tokens=2048)
    
    def _handle_complex_coding(self, user_input: str) -> str:
        """Silently run planner-worker for complex coding tasks"""
        result = self.planner_worker.execute_feature(
            goal=user_input,
            codebase_root=str(self.project_root)
        )
        if result["success"]:
            msg = f"Done — {result['tasks_completed']} task(s) completed."
            if result.get("time_elapsed"):
                msg += f" ({result['time_elapsed']:.1f}s)"
        else:
            completed = result['tasks_completed']
            total = result['total_tasks']
            msg = f"Partial — {completed}/{total} tasks succeeded."
            if result.get("errors"):
                msg += f" Issues: {'; '.join(result['errors'][:2])}"
        self.total_cost += result.get("cost", 0.0)
        return msg

    def _handle_self_improvement(self, user_input: str) -> str:
        """Handle self-improvement requests using cheap planner + workers"""
        result = self.self_improver.handle_self_improvement(
            user_input=user_input,
            codebase_root=str(self.project_root)
        )
        
        if result["success"]:
            message = f"✅ Self-improvement complete!\n"
            message += f"   Tasks: {result['tasks_completed']} completed, {result['tasks_failed']} failed\n"
            message += f"   Cost: ${result['cost']:.4f}\n"
            message += f"   Details: {result['message']}"
        else:
            message = f"❌ Self-improvement failed: {result['message']}\n"
            message += f"   Cost: ${result['cost']:.4f}"
        
        self.total_cost += result["cost"]
        return message
    
    def _run_planner_worker(self, goal: str) -> dict:
        """Internal: run planner-worker pipeline silently"""
        return self.planner_worker.execute_feature(
            goal=goal,
            codebase_root=str(self.project_root)
        )

    # ===== Utility Methods =====

    def show_budget_status(self):
        """Show cost and token efficiency"""
        # Use token tracker for accurate budget monitoring
        self.token_tracker.show_status()

    def get_handler_name(self, handler: HandlerType) -> str:
        """Get handler display name"""
        names = {
            HandlerType.WORKFLOW: "Lightweight Workflow",
            HandlerType.CODING: "Code Generation",
            HandlerType.REVIEW: "Code Analysis",
            HandlerType.REASONING: "Deep Reasoning",
        }
        return names.get(handler, "Unknown")

    # ===== Session Management Methods =====

    def show_sessions_list(self):
        """List all saved sessions"""
        sessions = self.session_manager.list_sessions()
        
        if not sessions:
            print("\n📭 No saved sessions yet. Start a chat with: ai")
            return
        
        print("\n" + "=" * 70)
        print("📋 SAVED SESSIONS")
        print("=" * 70)
        
        for i, session_id in enumerate(sessions, 1):
            session = self.session_manager.get_session(session_id)
            if session:
                print(f"\n{i}. Session: {session_id}")
                print(f"   Messages: {len(session.messages)}")
                print(f"   Tokens: {session.total_tokens}")
                print(f"   Strategy: {session.strategy.value}")
                if session.summary:
                    summary_preview = session.summary[:60] + "..." if len(session.summary) > 60 else session.summary
                    print(f"   Summary: {summary_preview}")
        
        print("\n" + "=" * 70)
        print("Resume with: ai --resume <session_id>")
        print("=" * 70)

    def show_session_info(self, session_id: str):
        """Show details about a specific session"""
        session = self.session_manager.get_session(session_id)
        
        if not session:
            print(f"\n❌ Session '{session_id}' not found")
            print("Available sessions: ai --list")
            return
        
        print("\n" + "=" * 70)
        print(f"📄 SESSION: {session_id}")
        print("=" * 70)
        
        print(f"\nStrategy: {session.strategy.value}")
        print(f"Messages: {len(session.messages)}")
        print(f"Total tokens: {session.total_tokens}")
        
        if session.summary:
            print(f"\nSummary:\n{session.summary}")
        
        print(f"\nMessage history:")
        for i, msg in enumerate(session.messages[-5:], 1):  # Last 5 messages
            role = "👤 You" if msg.role == "user" else "🤖 AI"
            preview = msg.content[:60] + "..." if len(msg.content) > 60 else msg.content
            print(f"  {i}. {role}: {preview} ({msg.tokens} tokens)")
        
        if len(session.messages) > 5:
            print(f"  ... and {len(session.messages) - 5} earlier messages")
        
        print("\n" + "=" * 70)
        print("Resume this session: ai --resume " + session_id)
        print("=" * 70)

    def resume_session(self, session_id: str):
        """Resume a previous session"""
        session = self.session_manager.get_session(session_id)
        
        if not session:
            print(f"\n❌ Session '{session_id}' not found")
            print("Available sessions: ai --list")
            return
        
        # Load the session
        self.session_manager.current_session = session
        
        print("\n" + "=" * 70)
        print(f"✅ RESUMED: {session_id}")
        print("=" * 70)
        print(f"Messages: {len(session.messages)}")
        print(f"Tokens: {session.total_tokens}")
        print(f"Strategy: {session.strategy.value}")
        print("\nContinue your conversation. Type 'exit' to end.\n")
        
        # Resume interactive mode with this session
        while True:
            try:
                user_input = input("You: ").strip()

                if not user_input:
                    continue

                if user_input.lower() in ['exit', 'quit', 'bye', 'done']:
                    print("👋 Session saved")
                    self.session_manager.save_session()
                    break

                self.handle_request(user_input)
                print()

            except KeyboardInterrupt:
                print("\n\n👋 Session saved")
                self.session_manager.save_session()
                break

    def show_help(self):
        """Show help message"""
        print("""
AWOS Coding Agent — just talk naturally.

Things you can ask:
  "explain this project"
  "how should I design the auth system?"
  "implement a login feature"
  "review my code for bugs"
  "what should I do next?"
  "improve error handling"
  "make the code faster"
  "add better logging"

I figure out the best approach automatically.

Commands:
  budget  → show token usage and cost
  exit    → quit and save session
""")


def main():
    """Main entry point"""
    agent = UnifiedAgent()

    # Check for session management commands
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        # Session listing
        if command == "--list" or command == "--sessions":
            agent.show_sessions_list()
            return
        
        # Session info
        elif command == "--info" and len(sys.argv) > 2:
            session_id = sys.argv[2]
            agent.show_session_info(session_id)
            return
        
        # Resume session
        elif command == "--resume" and len(sys.argv) > 2:
            session_id = sys.argv[2]
            agent.resume_session(session_id)
            agent.show_budget_status()
            return
        
        # Help
        elif command in ["--help", "-h", "help"]:
            agent.show_help()
            return
        
        # Regular query
        else:
            # Single query mode - still use session memory
            agent.session_manager.create_session(
                session_id=agent.current_session_id,
                strategy=MemoryStrategy.HYBRID
            )
            
            query = " ".join(sys.argv[1:])
            agent.handle_request(query)
            agent.session_manager.save_session()
            agent.show_budget_status()
    else:
        # Interactive mode
        agent.show_menu()
        agent.show_budget_status()


if __name__ == "__main__":
    main()
