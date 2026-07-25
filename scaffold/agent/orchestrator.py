"""
Orchestrator Module: Coordinates Planner → Worker → Verifier loop.

The main execution engine: takes a goal and delivers a feature by orchestrating
expensive planning (Sonnet) and cheap execution (DeepSeek) with verification.
"""

import ast
import hashlib
import logging
import os
import re
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

try:
    from .agent_state_manager import AgentStateManager
    from .cheap_planner import CheapPlanner
    from .confidence_calibrator import ConfidenceCalibrator
    from .core.observability import ObservabilityStore, TaskSpan, new_span
    from .core.performance_tracker import ToolPerformanceTracker
    from .core.reasoning import ReasoningSession, ReasoningTrace, ReasoningTraceStore
    from .critic_engine import CriticEngine, max_critic_rounds
    from .dag_executor import DAGExecutor
    from .error_pattern_store import ErrorPatternStore, make_error_pattern
    from .escalation_engine import EscalationEngine, is_cheap_only
    from .example_store import ExampleStore
    from .exploration_phase import enrich_codebase_context, exploration_enabled, run_exploration
    from .git_manager import GitManager
    from .integration_reviewer import IntegrationReviewer
    from .live_renderer import LiveRenderer
    from .live_tool_synth import LiveToolSynthesizer
    from .mcts_search import MCTSSearchEngine, write_and_run_tests
    from .ml_router import TaskFeatureExtractor, build_ml_router
    from .planner import Planner
    from .post_mortem import FailureType, PostMortemEngine
    from .project_planner import ProjectPlanner
    from .prompt_evolver import PromptEvolver
    from .react_worker import ReActWorker, react_worker_enabled
    from .reward_store import RewardStore, compute_reward
    from .scaffold_evolver import ScaffoldEvolver
    from .self_correction import ErrorClass, SelfCorrectionEngine
    from .skill_library import SkillLibrary
    from .stability_gate import StabilityGate
    from .strategy_config import StrategyRouter
    from .symbol_index import SymbolIndex
    from .task_decomposer import TaskDecomposer
    from .test_runner import TestRunner
    from .vector_memory import VectorMemory, VectorMemoryUnavailableError
    from .verifier import Verifier
    from .worker import Worker
except ImportError:
    from agent_state_manager import AgentStateManager
    from cheap_planner import CheapPlanner
    from confidence_calibrator import ConfidenceCalibrator
    from core.observability import ObservabilityStore, new_span
    from core.performance_tracker import ToolPerformanceTracker
    from core.reasoning import ReasoningSession, ReasoningTrace, ReasoningTraceStore
    from critic_engine import CriticEngine
    from dag_executor import DAGExecutor
    from error_pattern_store import ErrorPatternStore, make_error_pattern
    from escalation_engine import EscalationEngine, is_cheap_only
    from example_store import ExampleStore
    from exploration_phase import enrich_codebase_context, exploration_enabled, run_exploration
    from git_manager import GitManager
    from integration_reviewer import IntegrationReviewer
    from live_renderer import LiveRenderer
    from live_tool_synth import LiveToolSynthesizer
    from mcts_search import MCTSSearchEngine, write_and_run_tests
    from ml_router import TaskFeatureExtractor, build_ml_router
    from post_mortem import FailureType, PostMortemEngine
    from project_planner import ProjectPlanner
    from prompt_evolver import PromptEvolver
    from react_worker import ReActWorker, react_worker_enabled
    from reward_store import RewardStore, compute_reward
    from scaffold_evolver import ScaffoldEvolver
    from self_correction import ErrorClass, SelfCorrectionEngine
    from skill_library import SkillLibrary
    from stability_gate import StabilityGate
    from strategy_config import StrategyRouter
    from symbol_index import SymbolIndex
    from task_decomposer import TaskDecomposer
    from test_runner import TestRunner
    from verifier import Verifier
    from worker import Worker
    try:
        from vector_memory import VectorMemory, VectorMemoryUnavailableError
    except ImportError:
        VectorMemory = None  # type: ignore
        VectorMemoryUnavailableError = Exception  # type: ignore

    try:
        from multi_resolution_context import suggest_level  # noqa: F401
    except ImportError:
        suggest_level = None  # type: ignore


class Orchestrator:
    """Orchestrates the full Planner-Worker-Verifier pipeline."""

    def __init__(self, tracker=None):
        """
        Initialize Orchestrator.
        
        Args:
            tracker: TokenTracker instance for cost monitoring (optional)
        """
        self.planner = CheapPlanner()
        self.worker = Worker()
        self.verifier = Verifier()
        self.tracker = tracker
        self.performance = ToolPerformanceTracker(persist_dir=".awos")
        self.trace_store = ReasoningTraceStore(persist_dir=".awos/traces")
        self.integration_reviewer = IntegrationReviewer()
        self.reward_store = RewardStore()
        self.ml_router = build_ml_router(
            min_samples=20,
            weights_path=Path(".awos") / "linucb_weights.pkl",
            gp_path=Path(".awos") / "gp_model.pkl",
        )
        self._gp_last_fit_at = 0  # episode count at last GP fit
        self._feature_extractor = TaskFeatureExtractor()

        # ── Warm-start LinUCB from historical RewardStore data ───────────
        replayed = self.ml_router.warm_start(self.reward_store, max_episodes=50)
        logger.info(
            "[orchestrator] warm-started LinUCB with %d episodes (ready=%s, updates=%d)",
            replayed, self.ml_router.is_ready(), self.ml_router.total_updates(),
        )

        self.escalation = EscalationEngine(
            monthly_budget=getattr(tracker, 'monthly_budget', 20.0) if tracker else 20.0,
            performance_tracker=self.performance,
            ml_router=self.ml_router,
        )
        self.decomposer = TaskDecomposer()
        self.example_store = ExampleStore()
        self.state_manager = AgentStateManager()
        self._current_state = None
        self.execution_log = []
        self.strategy_router = StrategyRouter()
        self.self_correction = SelfCorrectionEngine()
        self.skill_library = SkillLibrary()
        self.test_runner = None
        self.error_store = ErrorPatternStore()
        self.obs_store = ObservabilityStore(persist_dir=".awos")
        if os.getenv("AWOS_E2E", "").lower() in ("1", "true", "yes"):
            self.vector_memory = None
            logger.warning(
                "[VectorMemory] unavailable (E2E mode — vector memory disabled) — semantic retrieval disabled"
            )
        else:
            try:
                self.vector_memory = VectorMemory()
                logger.info("[VectorMemory] initialised")
            except Exception as exc:
                self.vector_memory = None
                logger.warning("[VectorMemory] unavailable (%s) — semantic retrieval disabled", exc)
        self.project_planner = ProjectPlanner(vector_memory=self.vector_memory)
        self.critic = CriticEngine()
        self.post_mortem = PostMortemEngine(self.error_store, model_caller=self._cheap_call)
        self.calibrator = ConfidenceCalibrator()
        self._prm = None
        self._prm_last_fit_at = 0
        self._session_count = 0
        self.prompt_evolver = PromptEvolver(
            store_path=".awos",
            cheap_call=self._cheap_call,
        )
        self.live_tool_synth = LiveToolSynthesizer(
            tools_dir=".awos/tools",
            cheap_call=self._cheap_call,
        )
        self.scaffold_evolver = ScaffoldEvolver(
            scaffold_root=".",
            cheap_call=self._cheap_call,
        )
        self.stability_gate = StabilityGate(
            project_root=".",
            env_file=".env",
        )
        self._pause_requested = False
        self._runtime_session = None
        self._runtime_store = None
        self._virtual_runtime = None
        self._gui_bus = None  # GuiEventBus — set during execute_feature()
        # Stagnation breaker
        self._failure_history: deque = deque(maxlen=int(os.getenv("AWOS_STAGNATION_WINDOW", "10")))
        self._stagnation_threshold = int(os.getenv("AWOS_STAGNATION_THRESHOLD", "3"))
        self.react_worker = None
        if react_worker_enabled():
            try:
                self.react_worker = ReActWorker()
                logger.info("[ReActWorker] enabled (AWOS_REACT_WORKER=1)")
            except ValueError as exc:
                logger.warning("[ReActWorker] unavailable (%s) — patch worker fallback", exc)

        # Multi-Resolution Context Pipeline
        self.context_pipeline = None
        self.semantic_retriever = None
        if self.vector_memory is not None:
            try:
                from .multi_resolution_context import MultiResolutionContextPipeline
                from .semantic_retriever import SemanticRetriever
                self.semantic_retriever = SemanticRetriever(
                    vector_memory=self.vector_memory,
                )
                self.context_pipeline = MultiResolutionContextPipeline(
                    semantic_retriever=self.semantic_retriever,
                )
                self._suggest_context_level = suggest_level  # noqa: F821
                logger.info("[MultiResContext] pipeline initialised")
            except Exception as exc:
                logger.warning("[MultiResContext] pipeline unavailable (%s)", exc)

    def _runtime_session_enabled(self) -> bool:
        return os.getenv("AWOS_RUNTIME_SESSION", "").lower() in ("1", "true", "yes")

    def _cheap_call(self, prompt: str) -> str:
        """
        Fire a cheap LLM call for critique generation (Phase 5B ReflexionMemory).
        Uses the worker's existing model client infrastructure with max_tokens=120.
        Retries up to 3 times with exponential backoff (0.5s, 1s, 2s).
        Returns empty string on any error — never raises.
        """
        delays = [0.5, 1.0, 2.0]
        last_exc = None
        for attempt in range(3):
            try:
                worker = self.worker
                # Try OpenCode Go FIRST (has quota, cheapest)
                if hasattr(worker, "opencode_client") and worker.opencode_client is not None:
                    response = worker.opencode_client.chat.completions.create(
                        model="qwen3.7-plus",  # Qwen for reasoning quality on aux calls
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=4096,  # Enough for reasoning + content
                        temperature=0.3,
                    )
                    return response.choices[0].message.content or ""
                # Try DeepSeek / OpenAI-compatible
                if hasattr(worker, "client") and worker.client is not None:
                    response = worker.client.chat.completions.create(
                        model="deepseek-chat",
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=120,
                        temperature=0.3,
                    )
                    return response.choices[0].message.content or ""
                # Fallback: OpenRouter (cheap, diverse models)
                if hasattr(worker, "openrouter_client") and worker.openrouter_client is not None:
                    response = worker.openrouter_client.chat.completions.create(
                        model="openrouter/auto",
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=120,
                        temperature=0.3,
                        extra_headers={
                            "HTTP-Referer": "https://github.com/999-sbpatel/AWOS_coding_agent",
                            "X-OpenRouter-Title": "AWOS",
                        },
                    )
                    return response.choices[0].message.content or ""
                # Fallback: OpenAI mini (cheap-only — no Haiku)
                if hasattr(worker, "openai_client") and worker.openai_client is not None:
                    response = worker.openai_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=120,
                        temperature=0.3,
                    )
                    return response.choices[0].message.content or ""
                if not is_cheap_only() and hasattr(worker, "anthropic_client") and worker.anthropic_client is not None:
                    response = worker.anthropic_client.messages.create(
                        model="claude-haiku-4-5",
                        max_tokens=120,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    return response.content[0].text or ""
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(delays[attempt])
        logger.error("[Orchestrator] _cheap_call failed after 3 retries: %s", last_exc)
        return ""

    def _update_task_node(self, task_id: Any, status: str, session_id: str) -> None:
        """Update the GoalNode containing *task_id* in the active GoalGraph."""
        if not hasattr(self, "_active_goal_graph") or self._active_goal_graph is None:
            return
        node = self.project_planner.find_node_for_task(
            self._active_goal_graph, task_id,
        )
        if node is not None:
            self.project_planner.update_node_status(
                self._active_goal_graph, node.goal_id, status,
                session_id=session_id, task_id=str(task_id),
            )

    def _maybe_fit_gp(self) -> None:
        """Fit GPWorldModel once 50+ episodes exist and refits every 25 new episodes."""
        total = self.reward_store.total_episodes()
        gp = getattr(self.ml_router, '_gp', None)
        if gp is None:
            return
        if total >= gp.MIN_EPISODES_TO_FIT and (total - self._gp_last_fit_at) >= 25:
            episodes = self.reward_store.get_recent(200)
            gp.fit(episodes)
            self._gp_last_fit_at = total
            logger.info("[gp_world_model] fitted on %d episodes", total)

    def _maybe_train_prm(self) -> None:
        """Trigger PRM training when 500+ MCTS traces have accumulated."""
        try:
            from .process_reward_model import MCTSTraceStore
        except ImportError:
            try:
                from process_reward_model import MCTSTraceStore
            except ImportError:
                return
        trace_store = MCTSTraceStore()
        total_traces = trace_store.count()
        if total_traces < 500:
            return
        if total_traces - self._prm_last_fit_at < 50:
            return
        try:
            from .prm import ProcessRewardModel
        except ImportError:
            try:
                from prm import ProcessRewardModel
            except ImportError:
                return
        if self._prm is None:
            self._prm = ProcessRewardModel()
        traces = trace_store.get_recent(500)
        self._prm.train(traces)
        self._prm_last_fit_at = total_traces
        logger.info("[prm] trained on %d MCTS traces", total_traces)

    def _persist_worker_failure_pattern(self, task: dict, task_id: Any, error: str) -> None:
        try:
            err_cls = self.self_correction.classify(error or "")
            if err_cls == ErrorClass.UNKNOWN:
                err_cls = ErrorClass.WORKER_FAIL
            critique = self.self_correction.generate_critique(
                task=task,
                error=error or "worker failed",
                error_class=err_cls,
                model_caller=self._cheap_call,
            ) or f"Worker failed: {error or 'unknown'}"
            pattern = make_error_pattern(
                task_id=str(task_id),
                file_path=task.get("file", "unknown"),
                error_type=err_cls.name,
                error_msg=error or "worker failed",
                critique=critique,
            )
            self.error_store.save(pattern)
            logger.info("[TASK %s] Failure pattern saved: %s", task_id, err_cls.name)
        except Exception as exc:
            logger.debug("[ErrorPattern] persist failed: %s", exc)

    def _replan_after_verify_fail(
        self,
        failed_task: dict,
        verify_error: str,
        codebase_context: dict,
    ) -> list[dict]:
        """Return revised task(s) after verifier rejection."""
        try:
            from .plan_actions import replan_task_after_verify_fail
        except ImportError:
            from plan_actions import replan_task_after_verify_fail
        return replan_task_after_verify_fail(failed_task, verify_error)

    def _failure_signature(self, failure_kind: str, error: str, file_path: str) -> str:
        """
        Hash (failure_kind, normalized_error, file) → 16-char hex.
        
        Normalization: digits → N, collapse whitespace, lowercase.
        Used for stagnation detection.
        """
        norm = re.sub(r'\d+', 'N', error[:200].lower())
        norm = re.sub(r'\s+', ' ', norm).strip()
        payload = f"{failure_kind}:{norm}:{file_path}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _check_stagnation(self, result: dict) -> bool:
        """
        Check if same failure signature repeating >= threshold.
        Returns True if stagnation detected (should pause).
        """
        if result.get("success"):
            return False

        failure_kind = result.get("failure_kind", "unknown")
        error = result.get("verify_error") or result.get("worker_error", "")
        file_path = result["task"].get("path") or result["task"].get("file", "")

        sig = self._failure_signature(failure_kind, error[:200], file_path)
        self._failure_history.append((sig, result["task_id"]))

        sig_count = sum(1 for s, _ in self._failure_history if s == sig)

        if sig_count >= self._stagnation_threshold:
            logger.warning(
                "[BREAKER] Stagnation detected: signature %s repeated %d times",
                sig[:8], sig_count
            )
            return True

        return False

    def _log_mcts_trace(self, task: dict, task_id: Any, result, features: list) -> None:
        try:
            from .process_reward_model import MCTSTrace, MCTSTraceStore
        except ImportError:
            from process_reward_model import MCTSTrace, MCTSTraceStore
        trace = MCTSTrace(
            task_id=str(task_id),
            task_action=(task.get("action") or "")[:200],
            task_features=(list(features)[:10] if features is not None and len(features) else [0.0] * 10),
            nodes=[
                {
                    "depth": 0,
                    "state": "root",
                    "reward": result.pass_rate,
                    "is_terminal": bool(result.success),
                    "static_ok": bool(result.search),
                }
            ],
            winning_path=[0] if result.search else [],
            total_rollouts=int(result.rollouts_used),
            final_reward=float(result.pass_rate),
        )
        MCTSTraceStore().log_mcts_trace(trace)

    def _critic_refine(self, task: dict, patch: dict, file_content: str,
                       tracker, esc_decision, attempt: int, codebase_context: dict,
                       sym_index) -> dict:
        """P7: Run CriticEngine on the worker patch; return revised patch or original."""
        if not self.critic.should_run(task=task, attempt=attempt):
            return patch
        try:
            verdict = self.critic.critique(
                task=task,
                patch=patch,
                file_content=file_content,
                codebase_context=codebase_context,
            )
            logger.info(
                "[critic] task=%s verdict=%s conf=%.2f hints=%d",
                task.get("task_id", "?"), verdict.verdict,
                verdict.confidence, len(verdict.hints),
            )
            if verdict.should_revise and verdict.hints:
                revised = {
                    **patch,
                    "critic_hints": verdict.hints,
                    "critic_confidence": verdict.confidence,
                }
                return revised
        except Exception as _ce:
            logger.warning("[critic] critique failed: %s", _ce)
        return patch

    def _compute_confidence(self, task: dict, patch: dict, esc_decision) -> object:
        """P9: Fuse critic+GP+LinUCB+PRM signals; return ConfidenceReport or None."""
        if self.calibrator is None:
            return None
        try:
            return self._compute_confidence_impl(task, patch, esc_decision)
        except Exception as _ce:
            logger.debug("[confidence] failed: %s", _ce)
            return None

    def _compute_confidence_impl(self, task: dict, patch: dict, esc_decision) -> object:
        critic_score = patch.get("critic_confidence") if patch else None
        prm_raw = None
        gp_score = None
        linucb_score = None
        try:
            import numpy as np
            features = self._feature_extractor.extract(
                task, self.escalation.failure_count(str(task.get("task_id", "")))
            )
            linucb_score = float(self.ml_router.predict_success(np.array(features)))
        except Exception:
            pass
        try:
            gp = getattr(self.ml_router, '_gp', None)
            if gp is not None and gp.is_fitted():
                import numpy as np
                features = self._feature_extractor.extract(
                    task, self.escalation.failure_count(str(task.get("task_id", "")))
                )
                gp_score = float(gp.predict_proba(np.array(features).reshape(1, -1))[0])
        except Exception:
            pass
        report = self.calibrator.fuse(
            critic_score=critic_score,
            prm_raw=prm_raw,
            gp_score=gp_score,
            linucb_score=linucb_score,
        )
        logger.info(
            "[confidence] task=%s score=%.2f verdict=%s",
            task.get("task_id", "?"), report.fused_score, report.verdict,
        )
        return report

    def _pick_best_candidate(self, candidates: list, task: dict) -> dict:
        """
        P7: Select the best patch from a list of candidates.
        Uses PRM scoring when ready, otherwise falls back to highest critic_confidence.
        Returns the single candidate unchanged if only one is provided.
        """
        if len(candidates) <= 1:
            return candidates[0]
        if self._prm is not None and getattr(self._prm, "ready", False):
            best = candidates[0]
            best_score = -1.0
            for c in candidates:
                try:
                    score = self._prm.predict(task, c)
                    if score > best_score:
                        best_score = score
                        best = c
                except Exception:
                    pass
            return best
        return max(candidates, key=lambda c: c.get("_critic_confidence", 0.0))

    def execute_feature(
        self,
        goal: str,
        codebase_root: str = ".",
        codebase_context: Optional[dict] = None,
        pre_planned_tasks: Optional[List] = None,
        use_parallel: bool = False,
        resume: bool = False,
        session_id: Optional[str] = None,
        auto_approve_plan: bool = False,
    ) -> dict:
        """
        Execute a complete feature request end-to-end.
        
        Args:
            goal: User's feature request (e.g., "add user authentication")
            codebase_root: Root directory of the codebase
            codebase_context: Optional context dict (auto-discovered if None)
        
        Returns:
            {
                "success": bool,
                "goal": "...",
                "tasks_completed": 5,
                "tasks_failed": 0,
                "total_cost": 0.15,
                "execution_log": [...],
                "errors": [],
                "time_elapsed": 12.5
            }
        """

        start_time = time.time()
        self.execution_log = []  # Reset log for each run

        # ── ReAct Trace Session ────────────────────────────────────────────────
        session = ReasoningSession(
            session_id=f"orch_{uuid.uuid4().hex[:8]}",
            goal=goal,
        )

        # ── Phase 6: VectorMemory — index codebase for semantic retrieval ──
        _vm_start = time.time()
        if self.vector_memory is not None:
            try:
                _n = self.vector_memory.index_codebase(codebase_root)
                logger.info("[VectorMemory] indexed %d chunks from %s", _n, codebase_root)
                if self._gui_bus:
                    self._gui_bus.emit_codebase_indexed(_n, time.time() - _vm_start)
            except Exception as _vm_exc:
                logger.warning("[VectorMemory] index_codebase failed: %s", _vm_exc)

        # ── Phase 5C: ProjectPlanner — load or create persistent Goal DAG ──
        self._active_goal_graph = self.project_planner.load_or_create_goal(
            goal or "unnamed_goal",
            session_id=session.session_id,
        )
        logger.info(
            "[ProjectPlanner] goal_id=%s nodes=%d",
            self._active_goal_graph.root_id,
            len(self._active_goal_graph.nodes),
        )

        repo_root = str(Path(codebase_root).resolve())

        # ── RuntimeSession (early — before worktree / index / plan) ─────
        if self._runtime_session_enabled():
            try:
                from .runtime_session import RuntimeSessionStore, SessionStatus
            except ImportError:
                from runtime_session import RuntimeSessionStore, SessionStatus
            self._runtime_store = RuntimeSessionStore()
            if session_id:
                self._runtime_session = self._runtime_store.begin_resume(session_id)
            elif resume:
                latest = self._runtime_store.find_latest_for_goal(goal)
                self._runtime_session = (
                    self._runtime_store.begin_resume(latest.session_id)
                    if latest
                    else self._runtime_store.create(goal, repo_root)
                )
            else:
                self._runtime_session = self._runtime_store.create(goal, repo_root)
            self._runtime_session.status = SessionStatus.RUNNING
            self._runtime_store.save(self._runtime_session)
            print(
                f"[SESSION] Runtime session {self._runtime_session.session_id} "
                f"({self._runtime_session.status.value})"
            )

            # ── GuiEventBus: structured event streaming ──────────────────
            try:
                from .gui_events import GuiEventBus
            except ImportError:
                from gui_events import GuiEventBus
            _session_id = self._runtime_session.session_id
            self._gui_bus = GuiEventBus(_session_id, goal)
            self._gui_bus.emit_session_start(goal, repo_root)
            print(f"[EVENTS] GuiEventBus started for session {_session_id}")

        # ── Worktree sandbox (optional) ─────────────────────────────────
        self._virtual_runtime = None
        if self._runtime_session and self._runtime_store:
            try:
                from .virtual_execution_runtime import VirtualExecutionRuntime
            except ImportError:
                from virtual_execution_runtime import VirtualExecutionRuntime
            self._virtual_runtime = VirtualExecutionRuntime(
                repo_root, self._runtime_session, self._runtime_store
            )
            effective_root = self._virtual_runtime.enter()
            if effective_root != repo_root:
                print(f"[WORKTREE] Isolated sandbox: {effective_root}")
                codebase_root = effective_root
                self._runtime_session.codebase_root = effective_root
                self._runtime_store.save(self._runtime_session)

        # Set up git safety — branch or file backups
        git = GitManager(codebase_root)
        branch = git.setup(goal)
        if branch:
            print(f"[GIT] Working on branch '{branch}'")
        else:
            print("[GIT] No git repo — using in-memory file backups")

        # Auto-discover codebase context if not provided
        if codebase_context is None:
            codebase_context = self._discover_codebase_context(codebase_root)

        # Build/Load symbol index for cross-file awareness (Phase 3)
        _index_path = str(Path(".awos") / "symbol_index.json")
        sym_index = SymbolIndex.load(_index_path)
        if sym_index is not None:
            # Loaded from cache — only reparse changed files
            _changed = sym_index.rebuild_changed()
            logger.info(
                "[SymbolIndex] loaded from cache (%d changed, %d files)",
                _changed, len(sym_index._cache),
            )
        else:
            # Full build from scratch
            sym_index = SymbolIndex(codebase_root)
            sym_index.build()
            sym_index.save(_index_path)
            logger.info("[SymbolIndex] built from scratch — %s", sym_index.summary())
        print(f"[SYMBOLS] {sym_index.summary()}")

        # ── Goal Clarification (Phase 1 & 3) — NEW ──────────────────────────
        # Feature flag: AWOS_ENABLE_CLARIFICATION
        if os.getenv("AWOS_ENABLE_CLARIFICATION", "").lower() in ("1", "true", "yes"):
            try:
                from .goal_clarifier import clarify_goal
                from .plan_reviewer import review_plan

                print("\n[CLARIFICATION] Analyzing goal...")
                clarified = clarify_goal(goal, interactive=True)

                if clarified.is_ambiguous and not clarified.clarifying_questions:
                    # Questions were answered, use enriched goal
                    goal = clarified.to_enriched_prompt()
                    print("[CLARIFICATION] Goal clarified")
                elif clarified.is_ambiguous:
                    # Non-interactive or failed, proceed with original
                    print("[CLARIFICATION] Warning: Goal may be ambiguous")

                # Store clarified goal for plan review
                _clarified_goal = clarified
            except Exception as exc:
                logger.warning("[CLARIFICATION] Failed (%s), proceeding with original goal", exc)
                _clarified_goal = None
        else:
            _clarified_goal = None

        # ── LiveRenderer: real-time terminal UI ────────────────────────────
        _live = LiveRenderer()
        _live.session_start(goal, n_files=getattr(sym_index, '_file_count', 0))

        # ── Exploration phase (pre-plan grep/find) ─────────────────────────
        _exploration_data: dict = {}
        if exploration_enabled() and pre_planned_tasks is None:
            print("\n[EXPLORE] Pre-plan repository scan...")
            _exploration_data = run_exploration(goal, codebase_root)
            codebase_context = enrich_codebase_context(codebase_context, _exploration_data)
            n_hits = len(_exploration_data.get("grep_hits", []))
            n_files = len(_exploration_data.get("hit_files", []))
            print(f"[EXPLORE] {n_hits} grep hit(s) across {n_files} file(s)")

        # Phase 1: Planning — skip if caller already built a cheap plan
        if pre_planned_tasks is not None:
            tasks = pre_planned_tasks
            print(f"\n[PLANNER] Using pre-built plan: {len(tasks)} tasks")
            for task in tasks:
                print(f"  Task {task['task_id']}: {task['action']} (complexity: {task['complexity']})")
        else:
            print(f"\n[PLANNER] Breaking down goal: {goal}")
            try:
                with _live.spinner("Planning…"):
                    if is_cheap_only():
                        plan = CheapPlanner().plan(
                            goal, codebase_context, tracker=self.tracker,
                        )
                        print("[PLANNER] Cheap-only mode — using OpenCode Go (DeepSeek V4 Flash)")
                    else:
                        plan = self.planner.plan(
                            goal, codebase_context,
                            tracker=self.tracker,
                            existing_goal=self._active_goal_graph,
                        )
            except Exception as _primary_err:
                logger.warning("[planner] primary planner failed (%s) — trying CheapPlanner", _primary_err)
                try:
                    with _live.spinner("Planning (fallback)…"):
                        plan = CheapPlanner().plan(
                            goal, codebase_context, tracker=self.tracker,
                        )
                    print("[PLANNER] Fell back to CheapPlanner (OpenCode Go / DeepSeek V4 Flash)")
                except Exception as e:
                    return {
                        "success": False,
                        "goal": goal,
                        "tasks_completed": 0,
                        "tasks_failed": 0,
                        "total_tasks": 0,
                        "total_cost": 0,
                        "execution_log": self.execution_log,
                        "errors": [f"Planning failed (primary: {_primary_err}; fallback: {str(e)})"],
                        "time_elapsed": time.time() - start_time
                    }
            tasks = plan.get("plan", [])
            print(f"[PLANNER] Generated {len(tasks)} tasks:")
            for task in tasks:
                print(f"  Task {task['task_id']}: {task['action']} (complexity: {task['complexity']})")

        # ── GuiEventBus: plan_generated ─────────────────────────────────────
        if self._gui_bus:
            self._gui_bus.emit_plan_generated(len(tasks), [
                {"task_id": t["task_id"], "action": t["action"], "complexity": t.get("complexity", 0)}
                for t in tasks
            ])

        # ── Plan Review Checkpoint (Phase 2) — NEW ───────────────────────────
        # Feature flag: AWOS_ENABLE_PLAN_REVIEW
        if os.getenv("AWOS_ENABLE_PLAN_REVIEW", "").lower() in ("1", "true", "yes"):
            try:
                from .plan_reviewer import review_plan

                # Extract files and acceptance from clarified goal if available
                files_to_modify = []
                acceptance = None
                if _clarified_goal:
                    acceptance = _clarified_goal.acceptance_criteria
                    # Try to extract files from tasks
                    for task in tasks:
                        file = task.get('file', task.get('files'))
                        if file:
                            if isinstance(file, list):
                                files_to_modify.extend(file)
                            else:
                                files_to_modify.append(file)
                    files_to_modify = list(set(files_to_modify)) if files_to_modify else None

                # Review plan with user
                review_result = review_plan(
                    goal=goal,
                    plan=tasks,
                    files_to_modify=files_to_modify,
                    acceptance_criteria=acceptance,
                    interactive=True,
                    auto_approve=auto_approve_plan,
                )

                if not review_result.approved:
                    # Plan rejected or needs refinement
                    if review_result.feedback:
                        print(f"\n[PLAN REVIEW] Plan needs revision: {review_result.feedback}")
                        print("[PLAN REVIEW] Please rerun with refined goal")
                    elif review_result.rejection_reason:
                        print(f"\n[PLAN REVIEW] Plan rejected: {review_result.rejection_reason}")

                    return {
                        "success": False,
                        "goal": goal,
                        "tasks_completed": 0,
                        "tasks_failed": 0,
                        "total_tasks": len(tasks),
                        "total_cost": 0,
                        "execution_log": self.execution_log,
                        "errors": ["Plan not approved by user"],
                        "plan_review_feedback": review_result.feedback or review_result.rejection_reason,
                        "time_elapsed": time.time() - start_time
                    }

                print("\n[PLAN REVIEW] ✓ Plan approved, proceeding with execution")

            except Exception as exc:
                logger.warning("[PLAN REVIEW] Failed (%s), proceeding without review", exc)

        _live.planning_done(
            tasks,
            model="CheapPlanner" if is_cheap_only() else self.planner.__class__.__name__,
        )

        if self._runtime_session and self._runtime_store:
            self._runtime_session.progress.total_tasks = len(tasks)
            self._runtime_store.save(self._runtime_session)

        # ── Phase 5C: Ensure every task has a GoalNode in the graph ──
        if hasattr(self, "_active_goal_graph") and self._active_goal_graph is not None:
            self.project_planner.ensure_task_nodes(
                self._active_goal_graph, tasks, session_id=session.session_id,
            )

        # Phase 2: Execution with Retry + Simplification
        decomposition_depth = 0
        max_decomposition = 2
        replan_depth = 0
        max_replan = 1
        tasks_to_run = list(tasks)
        total_tasks_all_cycles = len(tasks)

        # ── Multi-session resume ────────────────────────────────────────
        self._current_state = self.state_manager.load(goal)
        self.state_manager.add_session(self._current_state, session.session_id)

        if self._runtime_session and self._runtime_session.progress.completed_task_ids:
            already_done = set(self._runtime_session.progress.completed_task_ids)
            tasks_to_run = [t for t in tasks if t["task_id"] not in already_done]
            print(
                f"[RESUME] Skipping {len(tasks) - len(tasks_to_run)} already-completed task(s): "
                f"{sorted(already_done)}"
            )
            total_tasks_all_cycles = len(tasks_to_run)
        elif resume and self._current_state["completed_task_ids"]:
            already_done = set(self._current_state["completed_task_ids"])
            tasks_to_run = [t for t in tasks if t["task_id"] not in already_done]
            print(
                f"[RESUME] Skipping {len(tasks) - len(tasks_to_run)} already-completed task(s): "
                f"{sorted(already_done)}"
            )
            total_tasks_all_cycles = len(tasks_to_run)

        # ── Budget hard stop before execution ───────────────────────────
        from scaffold.agent.budget_ledger import get_ledger
        _ledger = get_ledger()
        _allowed, _reason = _ledger.check_budget(estimated_cost=0.01)
        if not _allowed:
            print(f"[BUDGET] ⛔ Cannot start execution — {_reason}")
            return {
                "success": False,
                "goal": goal,
                "tasks_completed": 0,
                "tasks_failed": len(tasks),
                "total_tasks": len(tasks),
                "execution_log": [{"task_id": "all", "status": "failed", "reason": _reason}],
                "errors": [_reason],
                "time_elapsed": time.time() - start_time,
            }
        elif _reason:
            print(f"[BUDGET] {_reason}")

        # Bind context needed by _execute_single_task into a closure
        _ctx = dict(
            codebase_root=codebase_root,
            codebase_context=codebase_context,
            sym_index=sym_index,
            git=git,
            session=session,
            _live=_live,
            _total_tasks=len(tasks),
            exploration=_exploration_data,
        )

        while True:
            results = self._run_task_batch(tasks_to_run, _ctx, use_parallel)

            # Update persistent state for each task
            for r in results:
                if r["success"]:
                    self.state_manager.mark_complete(self._current_state, r["task_id"])
                    if self._runtime_session and self._runtime_store:
                        self._runtime_store.checkpoint(
                            self._runtime_session,
                            completed_task_id=r["task_id"],
                            total_tasks=len(tasks),
                        )
                else:
                    self.state_manager.mark_failed(self._current_state, r["task_id"])
                    if self._runtime_session and self._runtime_store:
                        self._runtime_store.checkpoint(
                            self._runtime_session,
                            failed_task_id=r["task_id"],
                            total_tasks=len(tasks),
                        )

                    # ── Stagnation breaker ──────────────────────────────────
                    if self._check_stagnation(r):
                        from .runtime_session import SessionStatus
                        self._runtime_session.pause_reason = "STAGNATION"
                        print("\n[BREAKER] Stagnation detected: same error pattern repeating")
                        print(f"[BREAKER] Task {r['task_id']} stuck — auto-pausing session")
                        print(f"[BREAKER] Review: awos sessions show {self._runtime_session.session_id}")
                        self._runtime_store.finalize(self._runtime_session, SessionStatus.PAUSED)
                        self._pause_requested = True
                        tasks_completed = sum(1 for res in results if res["success"])
                        tasks_failed = sum(1 for res in results if not res["success"])
                        break

            if self._pause_requested and self._runtime_session and self._runtime_store:
                from .runtime_session import SessionStatus
                print("[SESSION] Pause requested — stopping after last checkpoint")
                self._runtime_store.finalize(self._runtime_session, SessionStatus.PAUSED)
                tasks_completed = sum(1 for r in results if r["success"])
                tasks_failed = sum(1 for r in results if not r["success"])
                break

            cycle_completed = sum(1 for r in results if r["success"])
            cycle_failed = sum(1 for r in results if not r["success"])
            failed_tasks = [r["task"] for r in results if not r["success"]]

            # ── Verify-fail → replan (before decomposition) ─────────────────
            if replan_depth < max_replan and os.getenv("AWOS_REPLAN_DISABLE", "").lower() not in (
                "1", "true", "yes",
            ):
                replanned: list[dict] = []
                for r in results:
                    if r.get("success") or r.get("failure_kind") != "verify_fail":
                        continue
                    task = r["task"]
                    if task.get("_replan_attempted"):
                        continue
                    replanned.extend(
                        self._replan_after_verify_fail(
                            task, r.get("verify_error", ""), codebase_context,
                        )
                    )
                if replanned:
                    print(
                        f"\n[REPLAN] Verify-fail → {len(replanned)} revised task(s) "
                        f"(depth={replan_depth + 1}/{max_replan})"
                    )
                    tasks_to_run = replanned
                    total_tasks_all_cycles += len(replanned)
                    replan_depth += 1
                    continue

            # ── Retry with Simplification ────────────────────────────────────
            if failed_tasks and decomposition_depth < max_decomposition:
                new_tasks = []
                for task in failed_tasks:
                    new_tasks.extend(self.decomposer.decompose(task))

                if new_tasks:
                    print(
                        f"\n[DECOMP] Decomposing {len(failed_tasks)} failed task(s) "
                        f"into {len(new_tasks)} sub-task(s)  (depth={decomposition_depth + 1}/{max_decomposition})"
                    )
                    tasks_to_run = new_tasks
                    total_tasks_all_cycles += len(new_tasks)
                    decomposition_depth += 1
                    continue

            tasks_completed = cycle_completed
            tasks_failed = cycle_failed
            break

        elapsed = time.time() - start_time
        overall_success = tasks_failed == 0

        # ── Phase 1: Observability — print metrics summary after execution ──
        print()
        print(self.obs_store.metrics(window=50).display())

        # ── Phase 2: Queue model + regret report ─────────────────────────
        try:
            from .core.queue_model import RegretTracker
        except ImportError:
            from core.queue_model import RegretTracker

        _qs = self.obs_store.queue_state(window=50)
        if _qs is not None:
            print(_qs.display())

        _episodes = self.reward_store.get_recent(200)
        _rs = RegretTracker.from_episodes(_episodes)
        if _rs is not None:
            print(_rs.display())

        # ── Phase 3: Inference visibility ─────────────────────────────────
        _ir = self.obs_store.inference_report(window=50)
        if _ir is not None:
            print(_ir.display())

        # ── Phase 4: Async projection ─────────────────────────────────────
        _ap = self.obs_store.async_projection(n_workers=4, window=50)
        if _ap is not None:
            print(_ap.display())

        # ── Phase 5: Tool Reflection — success matrix ──────────────────────
        _mx = self.performance.matrix_display()
        if _mx is not None:
            print(_mx)

        # Finalize git state — keep branch on success, rollback only on total failure
        if tasks_completed == 0 and tasks_failed > 0:
            print("[GIT] All tasks failed — performing full rollback")
            git.finalize(False, force_full_rollback=True)
        else:
            git.finalize(overall_success)

        # ── LiveRenderer: session done ──────────────────────────────────
        _total_cost = self.tracker.get_budget_status().get("total_cost", 0.0) if self.tracker else 0.0
        _live.session_done(
            completed=tasks_completed,
            failed=tasks_failed,
            total_cost=_total_cost,
            elapsed=elapsed,
        )

        # ── EvalReport: auto-run health dashboard after every session ──
        try:
            from .eval_report import (
                _analyse,
                _feature_status,
                _load_mcts_traces,
                _load_skills,
                _load_spans,
                _render,
            )
        except ImportError:
            try:
                from eval_report import (
                    _analyse,
                    _feature_status,
                    _load_mcts_traces,
                    _load_skills,
                    _load_spans,
                    _render,
                )
            except ImportError:
                _render = None
        if _render is not None:
            try:
                _spans = _load_spans(".awos", 100)
                _metrics = _analyse(_spans)
                _features = _feature_status()
                _skills_n = _load_skills(".awos")
                _mcts_n = _load_mcts_traces(".awos")
                _render(_metrics, _features, _skills_n, _mcts_n)
            except Exception as _er:
                logger.debug("[eval_report] skipped: %s", _er)

        # Phase 3: Summary
        print(f"\n{'='*60}")
        print("EXECUTION SUMMARY")
        print(f"{'='*60}")
        print(f"Goal:            {goal}")
        print(f"Tasks Completed: {tasks_completed}/{total_tasks_all_cycles}")
        print(f"Tasks Failed:    {tasks_failed}/{total_tasks_all_cycles}")
        if decomposition_depth > 0:
            print(f"Decomposition:   {decomposition_depth} cycle(s)")
        print(f"Time Elapsed:    {elapsed:.1f}s")

        if self.tracker:
            status = self.tracker.get_budget_status()
            print(f"Total Cost:      ${status.get('total_cost', 0):.4f}")
            print(f"Remaining Budget:{status.get('remaining_budget', 0):.2f}")

        print(f"{'='*60}\n")

        # ── Integration Review (T4 model checks cross-task coherence) ────
        review = {"passed": True, "issues": [], "suggestions": []}
        if overall_success:
            try:
                review = self.integration_reviewer.review(codebase_root, self.execution_log)
                if review.get("issues"):
                    print(f"[INTEGRATION] {len(review['issues'])} issue(s) found")
                    for issue in review["issues"][:3]:
                        print(f"  - {issue}")
            except Exception as e:
                print(f"[INTEGRATION] Review skipped: {e}")

        # ── Persist ReAct Trace ──────────────────────────────────────────
        session.final_success = overall_success
        self.trace_store.save(session)

        # ── Finalize goal state ─────────────────────────────────────────
        if self._runtime_session and self._runtime_store:
            try:
                from .runtime_session import SessionStatus
            except ImportError:
                from runtime_session import SessionStatus
            if self._virtual_runtime:
                _term = self._runtime_session.status
                if _term != SessionStatus.PAUSED:
                    _term = SessionStatus.COMPLETED if overall_success else SessionStatus.FAILED
                self._virtual_runtime.finalize(_term)
            if self._runtime_session.status != SessionStatus.PAUSED:
                self._runtime_store.finalize(
                    self._runtime_session,
                    SessionStatus.COMPLETED if overall_success else SessionStatus.FAILED,
                )

        self.state_manager.finalize(self._current_state, overall_success)

        # ── StabilityGate — auto-detect test suite health + update .env ──
        self._session_count += 1
        if self.stability_gate.should_check(self._session_count):
            try:
                _stable, _reason = self.stability_gate.run(verbose=False)
                logger.info("[StabilityGate] %s — %s", "STABLE" if _stable else "UNSTABLE", _reason)
            except Exception as _sg_exc:
                logger.debug("[StabilityGate] probe skipped: %s", _sg_exc)

        # ── Feature 1A: PromptEvolver — evolve worker guidelines ────────
        if self.prompt_evolver.should_evolve(self._session_count):
            try:
                guidelines = self.prompt_evolver.evolve()
                if guidelines:
                    self.prompt_evolver.persist(guidelines, self._session_count)
                    logger.info("[PromptEvolver] evolved guidelines persisted (session %d)", self._session_count)
            except Exception as _pe:
                logger.debug("[PromptEvolver] evolution skipped: %s", _pe)

        # ── Feature 1C: ScaffoldEvolver — self-patch scaffold on high failure rate ──
        _failure_rate = tasks_failed / max(tasks_completed + tasks_failed, 1)
        if self.scaffold_evolver.should_evolve(_failure_rate):
            try:
                _mut = self.scaffold_evolver.evolve_once(self.error_store)
                if _mut.accepted:
                    logger.info("[ScaffoldEvolver] scaffold mutation ACCEPTED: %s", _mut.description)
                else:
                    logger.debug("[ScaffoldEvolver] mutation rejected: %s", _mut.rejection_reason)
            except Exception as _se:
                logger.debug("[ScaffoldEvolver] evolution skipped: %s", _se)

        # ── Self-Learning Observability ─────────────────────────────────
        try:
            from scaffold.agent.self_learning_metrics import SelfLearningMetrics
        except ImportError:
            try:
                from self_learning_metrics import SelfLearningMetrics
            except ImportError:
                SelfLearningMetrics = None
        if SelfLearningMetrics is not None:
            try:
                SelfLearningMetrics().print_report()
            except Exception as _slm_exc:
                logger.debug("[SelfLearningMetrics] report skipped: %s", _slm_exc)

        # ── GuiEventBus: session_done ────────────────────────────────────────
        if self._gui_bus:
            self._gui_bus.emit_session_done(
                completed=tasks_completed,
                failed=tasks_failed,
                total_cost=sum(
                    log.get("cost_usd", 0) for log in self.execution_log
                ) if self.execution_log else 0.0,
                elapsed=elapsed,
            )

        return {
            "success": overall_success,
            "goal": goal,
            "git": git.status(),
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
            "total_tasks": len(tasks),
            "execution_log": self.execution_log,
            "errors": [log["reason"] for log in self.execution_log if log["status"] == "failed"],
            "time_elapsed": elapsed,
            "integration_review": review,
            "runtime_session_id": (
                self._runtime_session.session_id if self._runtime_session else None
            ),
            "worktree_path": (
                self._runtime_session.sandbox.worktree_path
                if self._runtime_session and self._runtime_session.sandbox.enabled
                else None
            ),
        }

    # ── Batch / Parallel Execution ─────────────────────────────────────────────

    def _run_task_batch(
        self,
        tasks: List[dict],
        ctx: dict,
        use_parallel: bool,
    ) -> List[dict]:
        """
        Execute a list of tasks, either sequentially or in parallel waves.

        Args:
            tasks:        List of task dicts from the Planner.
            ctx:          Shared execution context (codebase_root, git, …).
            use_parallel: If True, use DAGExecutor for concurrent waves.

        Returns:
            List of result dicts: {task_id, success, task}.
        """
        def run_one(task):
            return self._execute_single_task(task, ctx)

        if use_parallel:
            dag = DAGExecutor(tasks)
            print(
                f"[DAG] {len(tasks)} task(s) → {dag.wave_count} wave(s), "
                f"parallelism_score={dag.parallelism_score:.2f}"
            )
            return dag.execute(run_one)

        # Sequential with context chaining (Ralph Loop pattern):
        # pass each task's applied change into the next task's context
        results = []
        prev_task_context: str = ""
        for task in tasks:
            if prev_task_context:
                task = {**task, "prev_task_context": prev_task_context}
            r = run_one(task)
            results.append(r)
            if r.get("success") and r.get("_applied_context"):
                prev_task_context = r["_applied_context"]
                logger.debug("[CHAIN] Passing context to next task: %s chars", len(prev_task_context))
            if self._pause_requested:
                break
        return results

    def _execute_task_via_react(
        self,
        task: dict,
        ctx: dict,
        *,
        task_id,
        file_path: str,
        file_content: str,
        esc_decision,
        _span,
        _strategy,
        _task_ts: float,
        _live_t,
        _task_usage: dict,
        budget_left: float,
    ) -> dict:
        """Execute one task via ReAct tool loop (grep → read → edit → test)."""
        import time as _time

        codebase_root = ctx["codebase_root"]
        codebase_context = ctx["codebase_context"]
        git = ctx["git"]
        session = ctx["session"]
        exploration = (ctx.get("exploration") or {}).get("exploration_summary", "")

        print(f"[TASK {task_id}] ReAct worker ({esc_decision.spec.name})")
        if _live_t:
            _live_t.worker_start(1, esc_decision.spec.name)

        # ── ReAct step callback: emit agent thinking + tool calls live ─────
        _react_turn = 0

        def _on_react_step(thought: str, action: str, action_input: dict,
                           observation: str, success: bool, latency_ms: float) -> None:
            nonlocal _react_turn
            _react_turn += 1
            if not self._gui_bus:
                return
            # Emit thinking for non-trivial thoughts
            if thought and len(thought) > 5:
                self._gui_bus.emit_agent_thinking(thought, _react_turn)
            # Emit tool call
            self._gui_bus.emit_agent_tool_call(
                action=action,
                action_input=action_input,
                observation=observation,
                success=success,
                latency_ms=latency_ms,
                turn=_react_turn,
            )

        react_result = self.react_worker.execute_task(
            task=task,
            file_content=file_content,
            codebase_context=codebase_context,
            project_root=codebase_root,
            tracker=self.tracker,
            model_spec=esc_decision.spec,
            exploration_context=exploration,
            step_callback=_on_react_step,
        )
        try:
            from .usage_record import merge_result_usage
        except ImportError:
            from usage_record import merge_result_usage
        merge_result_usage(_task_usage, react_result)

        steps = react_result.get("steps") or []
        for step in steps:
            trace = ReasoningTrace(
                thought=getattr(step, "thought", ""),
                action=getattr(step, "action", ""),
                action_input=getattr(step, "action_input", {}),
                observation=getattr(step, "observation", "")[:500],
                success=getattr(step, "success", False),
                latency_ms=getattr(step, "latency_ms", 0.0),
                model=react_result.get("model_used", esc_decision.spec.name),
            )
            session.add_trace(trace)

        success = bool(react_result.get("success"))
        test_result = react_result.get("test_result")
        files_changed = react_result.get("files_changed") or []

        # ── GuiEventBus: file_edit for each changed file ──────────────────
        if self._gui_bus:
            for rel in files_changed:
                self._gui_bus.emit_file_edit(
                    path=rel,
                    status="modified",
                    lines_added=0,  # Will be detailed in follow-up
                    lines_removed=0,
                )

        if _live_t:
            _live_t.worker_done(success, n_edits=len(files_changed))

        if success:
            print(f"[TASK {task_id}] REACT OK: {react_result.get('summary', 'done')[:200]}")
            for rel in files_changed:
                abs_path = os.path.join(codebase_root, rel)
                git.record_modified(abs_path)
            self._update_task_node(task_id, "completed", session.session_id)
            self.execution_log.append({
                "task_id": task_id,
                "status": "completed",
                "reason": f"ReAct: {react_result.get('summary', 'completed')[:120]}",
            })
            self.performance.record(
                tool="ReActWorker",
                model=esc_decision.spec.name,
                task_type=self.performance._classify_task_type(task.get("action", "")),
                success=True,
                latency_ms=(_time.time() - _task_ts) * 1000,
                cost=float(_task_usage.get("cost_usd", 0)) or esc_decision.spec.cost_per_req,
            )
        else:
            err = react_result.get("error", "ReAct worker failed")
            print(f"[TASK {task_id}] REACT FAILED: {err}")
            for rel in files_changed:
                git.rollback_file(os.path.join(codebase_root, rel))
            self._update_task_node(task_id, "failed", session.session_id)
            self.execution_log.append({
                "task_id": task_id,
                "status": "failed",
                "reason": err,
            })
            self._persist_worker_failure_pattern(task, task_id, err)
            self.performance.record(
                tool="ReActWorker",
                model=esc_decision.spec.name,
                task_type=self.performance._classify_task_type(task.get("action", "")),
                success=False,
                latency_ms=(_time.time() - _task_ts) * 1000,
                cost=float(_task_usage.get("cost_usd", 0)) or esc_decision.spec.cost_per_req,
                error_type="react_fail",
            )

        if test_result is not None and not getattr(test_result, "no_tests_found", True):
            print(
                f"[TASK {task_id}] Tests: {test_result.passed} passed, "
                f"{test_result.failed} failed (pass_rate={test_result.pass_rate:.2%})"
            )
            if _live_t:
                _live_t.test_result(test_result.passed, test_result.passed + test_result.failed)
            # ── GuiEventBus: test_result ────────────────────────────────
            if self._gui_bus:
                self._gui_bus.emit_test_result(
                    test_name=task.get("action", f"task_{task_id}"),
                    passed=test_result.failed == 0,
                    duration=test_result.duration_sec if hasattr(test_result, 'duration_sec') else 0.0,
                    output=test_result.output if hasattr(test_result, 'output') else "",
                )
            task["test_result"] = test_result
            if test_result.failed > 0:
                success = False

        _cost = float(_task_usage.get("cost_usd", 0)) or esc_decision.spec.cost_per_req
        _aid = esc_decision.spec.level.value
        if success and test_result is not None and not test_result.no_tests_found and not test_result.timed_out:
            _reward = compute_reward(test_result.pass_rate, _aid, _cost)
            _success = test_result.pass_rate >= 1.0
        else:
            _reward = compute_reward(success, _aid, _cost)
            _success = success

        _features = self._feature_extractor.extract(task, self.escalation.failure_count(str(task_id)))
        self.reward_store.store(
            task=task, action_id=_aid, features=_features, success=_success,
            cost_usd=_cost, model_name=esc_decision.spec.name,
            input_tokens=int(_task_usage.get("input_tokens", 0)),
            output_tokens=int(_task_usage.get("output_tokens", 0)),
        )
        self.escalation.record_outcome(str(task_id), esc_decision.spec.level, _success, task=task, reward=_reward)
        self.strategy_router.update(task, _strategy.name, _reward)

        _span.complete_ts = _time.time()
        _span.success = _success
        _span.attempt_count = len(steps) or 1
        _span.input_tokens = int(_task_usage.get("input_tokens", 0))
        _span.context_level = int(_task_usage.get("context_level", 0))
        _span.context_tokens = int(_task_usage.get("context_tokens", 0))
        _span.output_tokens = int(_task_usage.get("output_tokens", 0))
        _span.cost_usd = _cost
        _span.strategy = _strategy.name
        self.obs_store.record(_span)
        print(_span.one_liner())

        if _live_t:
            _live_t.task_done(_success, elapsed=_time.time() - _task_ts)

        # ── GuiEventBus: task_complete ──────────────────────────────────────
        if self._gui_bus:
            self._gui_bus.emit_task_complete(
                task_id=task_id,
                success=_success,
                cost_usd=_cost,
                n_edits=len(files_changed),
                error="" if _success else (react_result.get("error", "")),
            )

        out = {"task_id": task_id, "success": _success, "task": task, "react": True}
        if not _success:
            out["failure_kind"] = "react_fail"
            out["verify_error"] = react_result.get("error", "")
        return out

    def _execute_single_task(self, task: dict, ctx: dict) -> dict:
        """
        Execute one task through the Worker → Verifier pipeline.
        Logs retry attempts when task execution fails and is retried.

        Side effects: updates self.execution_log, git backups, example_store,
        performance tracker, and ReAct session traces.

        Returns:
            {task_id, success, task}
        """
        codebase_root = ctx["codebase_root"]
        codebase_context = ctx["codebase_context"]
        sym_index = ctx["sym_index"]
        git = ctx["git"]
        session = ctx["session"]

        task_id = task["task_id"]

        # Resolve short filenames to full paths intelligently
        from .file_resolver import resolve_file
        try:
            resolved_file = resolve_file(task["file"], project_root=codebase_root)
            task = {**task, "file": resolved_file}  # Update task with resolved path
        except FileNotFoundError:
            # If resolution fails, try original path (might be already full)
            pass

        file_path = os.path.join(codebase_root, task["file"])

        _live_t = ctx.get("_live")           # LiveRenderer (optional)
        _total_t = ctx.get("_total_tasks", 1)
        _task_ts = time.time()

        # ── Phase 1: Observability — stamp arrival time ─────────────────────
        _span = new_span(
            task_id=task_id,
            goal=ctx.get("session", session).goal if hasattr(ctx.get("session", session), "goal") else "",
            action=task.get("action", ""),
            file=task.get("file", ""),
        )

        print(f"\n[TASK {task_id}] Executing: {task['action']}")
        print(f"           File: {task['file']}")
        if _live_t:
            _live_t.task_start(task_id, _total_t, task)

        # Detect if this is a file creation task
        action_lower = task['action'].lower()
        is_create_task = any(keyword in action_lower for keyword in [
            'initialize', 'create', 'add new', 'generate new', 'write new'
        ])

        # Load or initialize file content
        if is_create_task and not Path(file_path).exists():
            # Creating new file - use empty content and ensure parent dir exists
            print(f"[TASK {task_id}] Creating new file")
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            file_content = ""
            # Mark task as new file creation for worker
            task = {**task, "_is_new_file": True}
            # No need to backup non-existent file
        else:
            task = {**task, "_is_new_file": False}
            # Modifying existing file - read current content
            try:
                with open(file_path, "r") as f:
                    file_content = f.read()
                git.backup_file(file_path)
            except FileNotFoundError:
                print(f"[TASK {task_id}] ERROR: File not found")
                self.execution_log.append({
                    "task_id": task_id,
                    "status": "failed",
                    "reason": f"File not found: {file_path}",
                })
                return {
                    "task_id": task_id,
                    "success": False,
                    "task": task,
                    "failure_kind": "file_not_found",
                }

        # Auto-fit GPWorldModel if enough episodes have accumulated
        self._maybe_fit_gp()

        # Decide model tier
        budget_left = (
            self.tracker.monthly_budget - self.tracker.total_cost
            if self.tracker else 20.0
        )
        esc_decision = self.escalation.decide(
            task=task,
            failure_count=self.escalation.failure_count(str(task_id)),
            budget_remaining=budget_left,
            dead_providers=self.worker._dead_providers,
        )
        print(f"[TASK {task_id}] {self.escalation.summary(esc_decision)}")

        # ── GuiEventBus: model_routed + task_start ──────────────────────────
        if self._gui_bus:
            _spec = esc_decision.spec
            self._gui_bus.emit_model_routed(
                model_name=_spec.name,
                tier=f"T{_spec.level.value}",
                reason=esc_decision.reason,
                cost_estimate=_spec.cost_per_req,
                provider=_spec.provider,
            )
            self._gui_bus.emit_task_start(
                task_id=task_id,
                action=task.get("action", ""),
                file=task.get("file", ""),
                model_name=_spec.name,
                model_reason=esc_decision.reason,
            )

        # ── Phase 1: Observability — stamp routing decision ─────────────────
        import time as _time
        _span.routing_ts = _time.time()
        _span.model_chosen = esc_decision.spec.name
        _span.escalation_level = esc_decision.spec.level.value

        # ── Budget guard per task ──────────────────────────────────────
        from scaffold.agent.budget_ledger import get_ledger
        _ledger = get_ledger()
        _allowed, _reason = _ledger.check_budget(estimated_cost=0.05)
        if not _allowed:
            print(f"[BUDGET] ⛔ Skipping task {task_id} — {_reason}")
            self.execution_log.append({
                "task_id": task_id,
                "status": "failed",
                "reason": f"Budget block: {_reason}",
            })
            return {
                "task_id": task_id,
                "success": False,
                "task": task,
                "failure_kind": "budget_block",
            }
        elif _reason:
            print(f"[BUDGET] {_reason}")

        worker_success = False
        max_attempts = 3
        search_replace = None
        _task_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0, "context_level": 0, "context_tokens": 0}

        _strategy = self.strategy_router.select(task)
        logger.debug("[strategy] task=%s → strategy=%s", task_id, _strategy.name)

        _task = task          # working copy; correction hints injected on each failure
        _last_error = ""      # error text from previous attempt
        _last_error_class = None  # ErrorClass from previous failure (for store retrieval)

        # ── Multi-Resolution Context: build tiered assembly ──────────────
        _context_assembly = None
        if self.context_pipeline is not None:
            try:
                _level = (
                    self._suggest_context_level(_task)
                    if hasattr(self, "_suggest_context_level")
                    else 2
                )
                _context_assembly = self.context_pipeline.build(
                    task=_task,
                    level=_level,
                    file_content=file_content,
                )
                logger.info(
                    "[MultiResContext] level=%d tokens=%d",
                    _context_assembly.level,
                    _context_assembly.estimated_tokens,
                )
                _task_usage["context_level"] = _context_assembly.level
                _task_usage["context_tokens"] = _context_assembly.estimated_tokens
            except Exception as _ctx_exc:
                logger.debug("[MultiResContext] build skipped: %s", _ctx_exc)

        # ── Phase 6: VectorMemory — retrieve relevant code chunks ──────────
        _vector_chunks = []
        if self.vector_memory is not None:
            try:
                _vector_chunks = self.vector_memory.query_code(task.get("action", ""))
            except Exception as _vm_exc:
                logger.warning("[VectorMemory] query_code failed: %s", _vm_exc)

        # ── Phase 1: Observability — stamp worker start ───────────────────
        _span.worker_start_ts = _time.time()
        _span.strategy = _strategy.name

        # ── ReAct worker path (default ON via AWOS_REACT_WORKER=1) ───────
        if self.react_worker is not None and react_worker_enabled():
            return self._execute_task_via_react(
                task,
                ctx,
                task_id=task_id,
                file_path=file_path,
                file_content=file_content,
                esc_decision=esc_decision,
                _span=_span,
                _strategy=_strategy,
                _task_ts=_task_ts,
                _live_t=_live_t,
                _task_usage=_task_usage,
                budget_left=budget_left,
            )

        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                esc_decision = self.escalation.decide(
                    task=task,
                    failure_count=attempt - 1,
                    budget_remaining=budget_left,
                )
                # Generate self-correction hint from previous failure
                _hint = self.self_correction.suggest(
                    task=task, error=_last_error,
                    file_content=file_content, attempt=attempt,
                )
                _last_error_class = _hint.error_class

                # ── Phase 5B: generate verbal critique + save to ErrorPatternStore ──
                _critique = self.self_correction.generate_critique(
                    task=task,
                    error=_last_error,
                    error_class=_hint.error_class,
                    model_caller=self._cheap_call,
                )
                if _critique:
                    _pattern = make_error_pattern(
                        task_id    = str(task_id),
                        file_path  = task.get("file", "unknown"),
                        error_type = _hint.error_class.name,
                        error_msg  = _last_error,
                        critique   = _critique,
                    )
                    self.error_store.save(_pattern)
                    logger.info("[TASK %s] Critique saved: %s", task_id, _hint.error_class.name)

                # ── Phase 5B: retrieve past critiques for this file+error_type ──
                _past = self.error_store.retrieve(
                    file_path  = task.get("file", "unknown"),
                    error_type = _hint.error_class.name,
                    top_n      = 3,
                )

                # ── Feature 1B: LiveToolSynthesizer — synthesize helper tool on failure ──
                _tool_output = ""
                try:
                    _synth_tool = self.live_tool_synth.reflect(
                        task=task, error=_last_error, attempt=attempt,
                    )
                    if _synth_tool:
                        logger.info("[LiveToolSynth] new tool synthesized: %s", _synth_tool.name)
                        _tool_output = self.live_tool_synth.run_tool(_synth_tool, task)
                    else:
                        _existing = self.live_tool_synth.find_relevant_tool(task)
                        if _existing:
                            _tool_output = self.live_tool_synth.run_tool(_existing, task)
                except Exception as _lts_exc:
                    logger.debug("[LiveToolSynth] skipped: %s", _lts_exc)

                _task = {
                    **task,
                    "error_context":  _hint.format_for_prompt(),
                    "past_critiques": _past,
                    "tool_output":    _tool_output,
                }
                print(
                    f"[TASK {task_id}] Retry {attempt}: "
                    f"{esc_decision.spec.name} + correction [{_hint.approach_name}]"
                    + (f" + {len(_past)} past critique(s)" if _past else "")
                    + (" + tool output" if _tool_output else "")
                )
                if _live_t:
                    _live_t.retry_notice(attempt, _last_error, _hint.approach_name)

            print(f"[TASK {task_id}] Worker attempt {attempt}/{max_attempts} via {esc_decision.spec.name}")
            if _live_t:
                _live_t.worker_start(attempt, esc_decision.spec.name)

            try:
                search_replace = self.worker.execute_task(
                    task=_task,
                    file_content=file_content,
                    codebase_context=codebase_context,
                    tracker=self.tracker,
                    attempt=attempt,
                    symbol_index=sym_index,
                    model_spec=esc_decision.spec,
                    example_store=self.example_store,
                    strategy=_strategy,
                    skill_library=self.skill_library,
                    vector_chunks=_vector_chunks,
                    prompt_evolver=self.prompt_evolver,
                    context_assembly=_context_assembly,
                )
                try:
                    from .usage_record import merge_result_usage
                except ImportError:
                    from usage_record import merge_result_usage
                merge_result_usage(_task_usage, search_replace)
                if search_replace.get("success"):
                    # ── P7: Critic self-play ────────────────────────────
                    try:
                        search_replace = self._critic_refine(
                            task=_task,
                            patch=search_replace,
                            file_content=file_content,
                            tracker=self.tracker,
                            esc_decision=esc_decision,
                            attempt=attempt,
                            codebase_context=codebase_context,
                            sym_index=sym_index,
                        )
                    except Exception as _ce:
                        logger.warning("[critic_refine] skipped: %s", _ce)
                    # ── P9: Confidence gating ──────────────────────────
                    _conf = self._compute_confidence(_task, search_replace, esc_decision)
                    if _conf is not None and _conf.should_abort:
                        print(f"[TASK {task_id}] CONF-ABORT attempt {attempt}: {_conf.explanation}")
                        _last_error = f"Low confidence ({_conf.fused_score:.0%})"
                        self.escalation.record_outcome(str(task_id), esc_decision.spec.level, False)
                        if _live_t:
                            _live_t.worker_done(False, error=_last_error)
                        continue
                    worker_success = True
                    self.escalation.record_outcome(str(task_id), esc_decision.spec.level, True)
                    _conf_str = f"[conf={_conf.fused_score:.0%}]" if _conf is not None else ""
                    print(f"[TASK {task_id}] Worker generated patch {_conf_str}")
                    if _live_t:
                        _live_t.worker_done(True, n_edits=len(search_replace.get("extra_edits", [])) + 1)
                    break
                else:
                    _last_error = search_replace.get("error", "unknown output format")
                    self.escalation.record_outcome(str(task_id), esc_decision.spec.level, False)
                    print(f"[TASK {task_id}] Worker failed: {_last_error}")
                    if _live_t:
                        _live_t.worker_done(False, error=_last_error)
            except Exception as exc:
                _last_error = str(exc)
                self.escalation.record_outcome(str(task_id), esc_decision.spec.level, False)
                print(f"[TASK {task_id}] Worker error: {exc}")
                if _live_t:
                    _live_t.worker_done(False, error=str(exc))

        # ── MCTS fallback — search + verify after worker failure (default on) ──
        if not worker_success:
            try:
                from .mcts_policy import mcts_rollout_budget, should_run_mcts_fallback
            except ImportError:
                from mcts_policy import mcts_rollout_budget, should_run_mcts_fallback
            if should_run_mcts_fallback(task, worker_failed=True):
                self._persist_worker_failure_pattern(task, task_id, _last_error)
                try:
                    logger.info("[MCTS] Activating search fallback for task %s", task_id)
                    _mcts = MCTSSearchEngine(
                        generate_fn=self.worker._generate_n_patches,
                        evaluate_fn=write_and_run_tests,
                        max_rollouts=mcts_rollout_budget(task),
                        n_branches=3,
                        project_root=codebase_root,
                    )
                    _mcts_result = _mcts.search(_task, file_content, codebase_context)
                    _mcts_features = self._feature_extractor.extract(
                        task, self.escalation.failure_count(str(task_id))
                    )
                    self._log_mcts_trace(task, task_id, _mcts_result, _mcts_features)
                    if _mcts_result.search:
                        search_replace = {
                            "success": True,
                            "search": _mcts_result.search,
                            "replace": _mcts_result.replace,
                            "reasoning": _mcts_result.reasoning,
                            "model_used": "mcts",
                        }
                        worker_success = True
                        print(
                            f"[TASK {task_id}] MCTS recovered patch "
                            f"(pass_rate={_mcts_result.pass_rate:.0%}, "
                            f"{_mcts_result.rollouts_used} rollouts)"
                        )
                    self._maybe_train_prm()
                except Exception as _mcts_exc:
                    logger.warning("[MCTS] fallback failed: %s", _mcts_exc)

        if not worker_success:
            print(f"[TASK {task_id}] Worker failed after {max_attempts} attempts")
            self.execution_log.append({
                "task_id": task_id,
                "status": "failed",
                "reason": "Worker could not generate valid SEARCH/REPLACE",
            })
            self._update_task_node(task_id, "failed", session.session_id)
            # ── Log to RewardStore & update ML router ─────────────────────
            _cost = float(_task_usage.get("cost_usd", 0)) or esc_decision.spec.cost_per_req
            _aid = esc_decision.spec.level.value
            _reward = compute_reward(False, _aid, _cost)
            _features = self._feature_extractor.extract(task, self.escalation.failure_count(str(task_id)))
            self.reward_store.store(
                task=task, action_id=_aid, features=_features, success=False,
                cost_usd=_cost, model_name=esc_decision.spec.name,
                input_tokens=int(_task_usage.get("input_tokens", 0)),
                output_tokens=int(_task_usage.get("output_tokens", 0)),
            )
            self.escalation.record_outcome(str(task_id), esc_decision.spec.level, False, task=task, reward=_reward)
            self._maybe_fit_gp()
            self._maybe_train_prm()
            # ── P8: final WORKER_FAIL reflection ───────────────────────
            try:
                self.post_mortem.reflect(task, _last_error, FailureType.WORKER_FAIL)
            except Exception as _pme:
                logger.debug("[post_mortem] final worker_fail reflect: %s", _pme)
            # ── Phase 1: Observability — record failed span (worker gave up) ──
            _span.complete_ts = _time.time()
            _span.success = False
            _span.attempt_count = max_attempts
            _span.input_tokens = int(_task_usage.get("input_tokens", 0))
            _span.context_level = int(_task_usage.get("context_level", 0))
            _span.context_tokens = int(_task_usage.get("context_tokens", 0))
            _span.output_tokens = int(_task_usage.get("output_tokens", 0))
            _span.cost_usd = _cost
            self.obs_store.record(_span)
            print(_span.one_liner())
            if _live_t:
                _live_t.task_done(False, elapsed=time.time() - _task_ts)
            return {
                "task_id": task_id,
                "success": False,
                "task": task,
                "failure_kind": "worker_fail",
            }

        # Verifier attempts
        verify_success = False
        max_verify_attempts = 2
        _verify_error = ""

        for verify_attempt in range(1, max_verify_attempts + 1):
            print(f"[TASK {task_id}] Verifying (attempt {verify_attempt}/{max_verify_attempts})")

            search_replace["task_spec"] = task
            result = self.verifier.verify_and_apply(
                search_replace=search_replace,
                file_path=file_path,
                check_type="syntax",
            )

            if result["success"] and result["applied"]:
                print(f"[TASK {task_id}] VERIFIED: Change applied successfully")
                verify_success = True
                if _live_t:
                    _live_t.verify_result(True)
                break
            elif result.get("needs_retry") and verify_attempt < max_verify_attempts:
                print(f"[TASK {task_id}] Verification failed, retrying worker with error context")
                enhanced_task = {**task, "error_context": result.get("error_context", "")}
                try:
                    search_replace = self.worker.execute_task(
                        task=enhanced_task,
                        file_content=file_content,
                        codebase_context=codebase_context,
                        tracker=self.tracker,
                        attempt=verify_attempt + 1,
                        symbol_index=sym_index,
                        context_assembly=_context_assembly,
                    )
                    if search_replace.get("success"):
                        continue
                except Exception as exc:
                    print(f"[TASK {task_id}] Worker retry failed: {exc}")
                    break
            else:
                _verify_err = str(result.get("errors", ["unknown"])[0])
                if _verify_err == "unknown" and result.get("error_context"):
                    _verify_err = str(result["error_context"])[:400]
                _verify_error = _verify_err
                print(f"[TASK {task_id}] VERIFICATION FAILED: {_verify_err}")
                if _live_t:
                    _live_t.verify_result(False, issues=[_verify_err])
                # ── P8: verify failure reflection ──────────────────────
                try:
                    self.post_mortem.reflect(task, _verify_err, FailureType.VERIFY_FAIL)
                except Exception as _pme:
                    logger.debug("[post_mortem] verify_fail reflect: %s", _pme)
                break

        # ── Phase 6: VectorMemory — store task outcome ─────────────────────
        if self.vector_memory is not None:
            try:
                self.vector_memory.store_outcome(
                    task=task,
                    success=verify_success,
                    session_id=session.session_id,
                    critique=_critique if "_critique" in dir() else "",
                )
            except Exception as _vm_exc:
                logger.warning("[VectorMemory] store_outcome failed: %s", _vm_exc)

        if verify_success:
            self._update_task_node(task_id, "completed", session.session_id)
            git.record_modified(file_path)
            if search_replace and search_replace.get("success"):
                self.example_store.record_success(
                    task=task,
                    search=search_replace["search"],
                    replace=search_replace["replace"],
                )
                self.skill_library.record(
                    task=task,
                    model_name=esc_decision.spec.name,
                    strategy_name=_strategy.name,
                    attempts=max_attempts,
                )
            self.execution_log.append({
                "task_id": task_id,
                "status": "completed",
                "reason": "Task completed and verified",
            })
            _latency_ms = (_time.time() - _span.worker_start_ts) * 1000 if _span.worker_start_ts else None
            self.performance.record(
                tool="Worker",
                model=esc_decision.spec.name,
                task_type=self.performance._classify_task_type(task.get("action", "")),
                success=True,
                latency_ms=_latency_ms,
                cost=float(_task_usage.get("cost_usd", 0)) or esc_decision.spec.cost_per_req,
            )
        else:
            self._update_task_node(task_id, "failed", session.session_id)
            print(f"[TASK {task_id}] Rolling back '{task['file']}'")
            git.rollback_file(file_path)
            self.execution_log.append({
                "task_id": task_id,
                "status": "failed",
                "reason": "Verification failed — file restored",
            })
            _latency_ms = (_time.time() - _span.worker_start_ts) * 1000 if _span.worker_start_ts else None
            self.performance.record(
                tool="Worker",
                model=esc_decision.spec.name,
                task_type=self.performance._classify_task_type(task.get("action", "")),
                success=False,
                latency_ms=_latency_ms,
                cost=float(_task_usage.get("cost_usd", 0)) or esc_decision.spec.cost_per_req,
                error_type="verification_failed",
            )

        # ── TestRunner: run tests after successful verification ────────────
        test_result = None
        if verify_success:
            try:
                self.test_runner = TestRunner(project_root=codebase_root)
                test_result = self.test_runner.run(changed_files=[task["file"]])
                task["test_result"] = test_result
                if not test_result.no_tests_found:
                    print(
                        f"[TASK {task_id}] Tests: {test_result.passed} passed, "
                        f"{test_result.failed} failed (pass_rate={test_result.pass_rate:.2%})"
                    )
                    if _live_t:
                        _live_t.test_result(test_result.passed, test_result.passed + test_result.failed)
                    # ── P8: test failure reflection ─────────────────────
                    if test_result.failed > 0:
                        try:
                            self.post_mortem.reflect(
                                task,
                                f"{test_result.failed} test(s) failed (pass_rate={test_result.pass_rate:.0%})",
                                FailureType.TEST_FAIL,
                            )
                        except Exception as _pme:
                            logger.debug("[post_mortem] test_fail reflect: %s", _pme)
            except Exception as exc:
                logger.warning("[TASK %s] TestRunner error: %s", task_id, exc)
                task["test_result"] = None

        # ── Log to RewardStore & update ML router ──────────────────────────
        _cost = float(_task_usage.get("cost_usd", 0)) or esc_decision.spec.cost_per_req
        _aid = esc_decision.spec.level.value

        # Use test pass rate as reward signal when available, else fall back to syntax-only
        if verify_success and test_result is not None and not test_result.no_tests_found and not test_result.timed_out:
            _reward = compute_reward(test_result.pass_rate, _aid, _cost)
            _success = test_result.pass_rate >= 1.0
        else:
            _reward = compute_reward(verify_success, _aid, _cost)
            _success = verify_success

        _features = self._feature_extractor.extract(task, self.escalation.failure_count(str(task_id)))
        self.reward_store.store(
            task=task, action_id=_aid, features=_features, success=_success,
            cost_usd=_cost, model_name=esc_decision.spec.name,
            input_tokens=int(_task_usage.get("input_tokens", 0)),
            output_tokens=int(_task_usage.get("output_tokens", 0)),
        )
        self.escalation.record_outcome(str(task_id), esc_decision.spec.level, _success, task=task, reward=_reward)
        self._maybe_fit_gp()
        self._maybe_train_prm()

        # ── Update StrategyRouter with outcome ──────────────────────────────
        self.strategy_router.update(task, _strategy.name, _reward)

        # ── Phase 1: Observability — record completed span ──────────────────
        _span.complete_ts = _time.time()
        _span.success = _success
        _span.attempt_count = attempt  # loop var holds the attempt number where worker succeeded
        _span.input_tokens = int(_task_usage.get("input_tokens", 0))
        _span.context_level = int(_task_usage.get("context_level", 0))
        _span.context_tokens = int(_task_usage.get("context_tokens", 0))
        _span.output_tokens = int(_task_usage.get("output_tokens", 0))
        _span.cost_usd = _cost
        self.obs_store.record(_span)
        print(_span.one_liner())

        # ReAct Trace
        trace = ReasoningTrace(
            thought=f"Task {task_id}: {task['action']} on {task['file']}",
            action="Worker.execute_task",
            action_input={"file": task["file"], "complexity": task.get("complexity")},
            observation=(
                "Verified and applied" if verify_success
                else (search_replace or {}).get("error", "Worker/Verifier failed")
            ),
            success=verify_success,
            model=esc_decision.spec.name,
        )
        session.add_trace(trace)

        _applied_context = ""
        if verify_success and search_replace and search_replace.get("success"):
            _applied_context = (
                f"PREVIOUS TASK COMPLETED:\n"
                f"  Action: {task['action']}\n"
                f"  File: {task['file']}\n"
                f"  Code change (REPLACE block):\n"
                f"{search_replace.get('replace', '')[:500]}"
            )

        if _live_t:
            _live_t.task_done(verify_success, elapsed=time.time() - _task_ts)
        out = {
            "task_id": task_id,
            "success": verify_success,
            "task": task,
            "_applied_context": _applied_context,
        }
        if not verify_success:
            out["failure_kind"] = "verify_fail"
            out["verify_error"] = _verify_error
        return out

    def _discover_codebase_context(self, codebase_root: str) -> dict:
        """Auto-discover codebase structure with actual class/function symbols."""
        modules = []
        files = []
        symbols = []  # class/function names for richer planning context

        root = Path(codebase_root)

        for py_file in root.rglob("*.py"):
            if "__pycache__" in str(py_file) or ".git" in str(py_file):
                continue

            rel_path = str(py_file.relative_to(codebase_root))
            files.append(rel_path)

            if "__init__.py" not in str(py_file):
                module_name = rel_path.replace(".py", "").replace("/", ".")
                modules.append(module_name)

            # Extract top-level class and function names via AST
            try:
                source = py_file.read_text(errors="ignore")
                tree = ast.parse(source, filename=str(py_file))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        if isinstance(node, ast.ClassDef):
                            symbols.append(f"{rel_path}::class {node.name}")
                        elif node.col_offset == 0:  # top-level functions only
                            symbols.append(f"{rel_path}::def {node.name}")
            except (SyntaxError, Exception):
                pass

        architecture = "Python project with modular structure"
        if (root / "scaffold").exists():
            architecture = "AWOS-based Python agent framework (planner/worker/verifier pattern)"

        return {
            "modules": ", ".join(modules[:8]) + ("..." if len(modules) > 8 else ""),
            "files": files[:30],
            "architecture": architecture,
            "symbols": symbols[:40],  # Top-level symbols for planning context
        }
