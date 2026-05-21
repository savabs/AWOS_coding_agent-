"""
Orchestrator Module: Coordinates Planner → Worker → Verifier loop.

The main execution engine: takes a goal and delivers a feature by orchestrating
expensive planning (Sonnet) and cheap execution (DeepSeek) with verification.
"""

import ast
import logging
import os
import time
import uuid
from typing import Any, Optional, Dict, List
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

try:
    from .planner import Planner
    from .worker import Worker
    from .verifier import Verifier
    from .git_manager import GitManager
    from .symbol_index import SymbolIndex
    from .escalation_engine import EscalationEngine
    from .example_store import ExampleStore
    from .integration_reviewer import IntegrationReviewer
    from .core.performance_tracker import ToolPerformanceTracker
    from .core.reasoning import ReasoningTrace, ReasoningSession, ReasoningTraceStore
    from .core.observability import ObservabilityStore, new_span, TaskSpan
    from .task_decomposer import TaskDecomposer
    from .dag_executor import DAGExecutor
    from .agent_state_manager import AgentStateManager
    from .reward_store import RewardStore, compute_reward
    from .ml_router import build_ml_router, TaskFeatureExtractor
    from .strategy_config import StrategyRouter
    from .self_correction import SelfCorrectionEngine
    from .skill_library import SkillLibrary
    from .skill_library import SkillLibrary
    from .test_runner import TestRunner
    from .error_pattern_store import ErrorPatternStore, make_error_pattern
    from .project_planner import ProjectPlanner
    from .vector_memory import VectorMemory, VectorMemoryUnavailableError
except ImportError:
    from planner import Planner
    from worker import Worker
    from verifier import Verifier
    from git_manager import GitManager
    from symbol_index import SymbolIndex
    from escalation_engine import EscalationEngine
    from example_store import ExampleStore
    from integration_reviewer import IntegrationReviewer
    from core.performance_tracker import ToolPerformanceTracker
    from core.reasoning import ReasoningTrace, ReasoningSession, ReasoningTraceStore
    from core.observability import ObservabilityStore, new_span, TaskSpan
    from task_decomposer import TaskDecomposer
    from dag_executor import DAGExecutor
    from agent_state_manager import AgentStateManager
    from reward_store import RewardStore, compute_reward
    from ml_router import build_ml_router, TaskFeatureExtractor
    from strategy_config import StrategyRouter
    from self_correction import SelfCorrectionEngine
    from skill_library import SkillLibrary
    from test_runner import TestRunner
    from error_pattern_store import ErrorPatternStore, make_error_pattern
    from project_planner import ProjectPlanner
    try:
        from vector_memory import VectorMemory, VectorMemoryUnavailableError
    except ImportError:
        VectorMemory = None  # type: ignore
        VectorMemoryUnavailableError = Exception  # type: ignore


class Orchestrator:
    """Orchestrates the full Planner-Worker-Verifier pipeline."""
    
    def __init__(self, tracker=None):
        """
        Initialize Orchestrator.
        
        Args:
            tracker: TokenTracker instance for cost monitoring (optional)
        """
        self.planner = Planner()
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
        try:
            self.vector_memory = VectorMemory()
            logger.info("[VectorMemory] initialised")
        except Exception as exc:
            self.vector_memory = None
            logger.warning("[VectorMemory] unavailable (%s) — semantic retrieval disabled", exc)
        self.project_planner = ProjectPlanner(vector_memory=self.vector_memory)

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
                # Try DeepSeek / OpenAI-compatible first (cheapest)
                if hasattr(worker, "client") and worker.client is not None:
                    response = worker.client.chat.completions.create(
                        model="deepseek-chat",
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=120,
                        temperature=0.3,
                    )
                    return response.choices[0].message.content or ""
                # Fallback: Anthropic Haiku
                if hasattr(worker, "anthropic_client") and worker.anthropic_client is not None:
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
    
    def execute_feature(
        self,
        goal: str,
        codebase_root: str = ".",
        codebase_context: Optional[dict] = None,
        pre_planned_tasks: Optional[List] = None,
        use_parallel: bool = False,
        resume: bool = False,
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
        if self.vector_memory is not None:
            try:
                _n = self.vector_memory.index_codebase(codebase_root)
                logger.info("[VectorMemory] indexed %d chunks from %s", _n, codebase_root)
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

        # Set up git safety — branch or file backups
        git = GitManager(codebase_root)
        branch = git.setup(goal)
        if branch:
            print(f"[GIT] Working on branch '{branch}'")
        else:
            print(f"[GIT] No git repo — using in-memory file backups")
        
        # Auto-discover codebase context if not provided
        if codebase_context is None:
            codebase_context = self._discover_codebase_context(codebase_root)
        
        # Build symbol index for cross-file awareness (Phase 3)
        sym_index = SymbolIndex(codebase_root)
        sym_index.build()
        print(f"[SYMBOLS] {sym_index.summary()}")
        
        # Phase 1: Planning — skip if caller already built a cheap plan
        if pre_planned_tasks is not None:
            tasks = pre_planned_tasks
            print(f"\n[PLANNER] Using pre-built plan: {len(tasks)} tasks")
            for task in tasks:
                print(f"  Task {task['task_id']}: {task['action']} (complexity: {task['complexity']})")
        else:
            print(f"\n[PLANNER] Breaking down goal: {goal}")
            try:
                plan = self.planner.plan(
                    goal, codebase_context,
                    tracker=self.tracker,
                    existing_goal=self._active_goal_graph,
                )
            except Exception as e:
                return {
                    "success": False,
                    "goal": goal,
                    "tasks_completed": 0,
                    "tasks_failed": 0,
                    "total_cost": 0,
                    "execution_log": self.execution_log,
                    "errors": [f"Planning failed: {str(e)}"],
                    "time_elapsed": time.time() - start_time
                }
            tasks = plan.get("plan", [])
            print(f"[PLANNER] Generated {len(tasks)} tasks:")
            for task in tasks:
                print(f"  Task {task['task_id']}: {task['action']} (complexity: {task['complexity']})")
        
        # ── Phase 5C: Ensure every task has a GoalNode in the graph ──
        if hasattr(self, "_active_goal_graph") and self._active_goal_graph is not None:
            self.project_planner.ensure_task_nodes(
                self._active_goal_graph, tasks, session_id=session.session_id,
            )

        # Phase 2: Execution with Retry + Simplification
        decomposition_depth = 0
        max_decomposition = 2
        tasks_to_run = list(tasks)
        total_tasks_all_cycles = len(tasks)

        # ── Multi-session resume ────────────────────────────────────────
        self._current_state = self.state_manager.load(goal)
        self.state_manager.add_session(self._current_state, session.session_id)

        if resume and self._current_state["completed_task_ids"]:
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
        )

        while True:
            results = self._run_task_batch(tasks_to_run, _ctx, use_parallel)

            # Update persistent state for each task
            for r in results:
                if r["success"]:
                    self.state_manager.mark_complete(self._current_state, r["task_id"])
                else:
                    self.state_manager.mark_failed(self._current_state, r["task_id"])

            cycle_completed = sum(1 for r in results if r["success"])
            cycle_failed = sum(1 for r in results if not r["success"])
            failed_tasks = [r["task"] for r in results if not r["success"]]

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

        # Finalize git state — keep branch on success, rollback on total failure
        if tasks_completed == 0 and tasks_failed > 0:
            print("[GIT] All tasks failed — performing full rollback")
        git.finalize(overall_success)
        
        # Phase 3: Summary
        print(f"\n{'='*60}")
        print(f"EXECUTION SUMMARY")
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
        self.state_manager.finalize(self._current_state, overall_success)

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
        return results

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
        file_path = os.path.join(codebase_root, task["file"])

        # ── Phase 1: Observability — stamp arrival time ─────────────────────
        _span = new_span(
            task_id=task_id,
            goal=ctx.get("session", session).goal if hasattr(ctx.get("session", session), "goal") else "",
            action=task.get("action", ""),
            file=task.get("file", ""),
        )

        print(f"\n[TASK {task_id}] Executing: {task['action']}")
        print(f"           File: {task['file']}")

        # Load file content
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
            return {"task_id": task_id, "success": False, "task": task}

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
        )
        print(f"[TASK {task_id}] {self.escalation.summary(esc_decision)}")

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
            return {"task_id": task_id, "success": False, "task": task}
        elif _reason:
            print(f"[BUDGET] {_reason}")

        # Worker attempts
        worker_success = False
        max_attempts = 3
        search_replace = None

        _strategy = self.strategy_router.select(task)
        logger.debug("[strategy] task=%s → strategy=%s", task_id, _strategy.name)

        _task = task          # working copy; correction hints injected on each failure
        _last_error = ""      # error text from previous attempt
        _last_error_class = None  # ErrorClass from previous failure (for store retrieval)

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

                _task = {
                    **task,
                    "error_context":  _hint.format_for_prompt(),
                    "past_critiques": _past,
                }
                print(
                    f"[TASK {task_id}] Retry {attempt}: "
                    f"{esc_decision.spec.name} + correction [{_hint.approach_name}]"
                    + (f" + {len(_past)} past critique(s)" if _past else "")
                )

            print(f"[TASK {task_id}] Worker attempt {attempt}/{max_attempts} via {esc_decision.spec.name}")

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
                )
                if search_replace.get("success"):
                    worker_success = True
                    self.escalation.record_outcome(str(task_id), esc_decision.spec.level, True)
                    print(f"[TASK {task_id}] Worker generated SEARCH/REPLACE")
                    break
                else:
                    _last_error = search_replace.get("error", "unknown output format")
                    self.escalation.record_outcome(str(task_id), esc_decision.spec.level, False)
                    print(f"[TASK {task_id}] Worker failed: {_last_error}")
            except Exception as exc:
                _last_error = str(exc)
                self.escalation.record_outcome(str(task_id), esc_decision.spec.level, False)
                print(f"[TASK {task_id}] Worker error: {exc}")

        if not worker_success:
            print(f"[TASK {task_id}] Worker failed after {max_attempts} attempts")
            self.execution_log.append({
                "task_id": task_id,
                "status": "failed",
                "reason": "Worker could not generate valid SEARCH/REPLACE",
            })
            self._update_task_node(task_id, "failed", session.session_id)
            # ── Log to RewardStore & update ML router ─────────────────────
            _cost = esc_decision.spec.cost_per_req
            _aid = esc_decision.spec.level.value
            _reward = compute_reward(False, _aid, _cost)
            _features = self._feature_extractor.extract(task, self.escalation.failure_count(str(task_id)))
            self.reward_store.store(task=task, action_id=_aid, features=_features, success=False, cost_usd=_cost, model_name=esc_decision.spec.name)
            self.escalation.record_outcome(str(task_id), esc_decision.spec.level, False, task=task, reward=_reward)
            # ── Phase 1: Observability — record failed span (worker gave up) ──
            _span.complete_ts = _time.time()
            _span.success = False
            _span.attempt_count = max_attempts
            _span.cost_usd = _cost
            self.obs_store.record(_span)
            print(_span.one_liner())
            return {"task_id": task_id, "success": False, "task": task}

        # Verifier attempts
        verify_success = False
        max_verify_attempts = 2

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
                    )
                    if search_replace.get("success"):
                        continue
                except Exception as exc:
                    print(f"[TASK {task_id}] Worker retry failed: {exc}")
                    break
            else:
                print(f"[TASK {task_id}] VERIFICATION FAILED: {result.get('errors', ['unknown'])[0]}")
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
                cost=esc_decision.spec.cost_per_req,
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
                cost=esc_decision.spec.cost_per_req,
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
            except Exception as exc:
                logger.warning("[TASK %s] TestRunner error: %s", task_id, exc)
                task["test_result"] = None

        # ── Log to RewardStore & update ML router ──────────────────────────
        _cost = esc_decision.spec.cost_per_req
        _aid = esc_decision.spec.level.value

        # Use test pass rate as reward signal when available, else fall back to syntax-only
        if verify_success and test_result is not None and not test_result.no_tests_found and not test_result.timed_out:
            _reward = compute_reward(test_result.pass_rate, _aid, _cost)
            _success = test_result.pass_rate >= 1.0
        else:
            _reward = compute_reward(verify_success, _aid, _cost)
            _success = verify_success

        _features = self._feature_extractor.extract(task, self.escalation.failure_count(str(task_id)))
        self.reward_store.store(task=task, action_id=_aid, features=_features, success=_success, cost_usd=_cost, model_name=esc_decision.spec.name)
        self.escalation.record_outcome(str(task_id), esc_decision.spec.level, _success, task=task, reward=_reward)

        # ── Update StrategyRouter with outcome ──────────────────────────────
        self.strategy_router.update(task, _strategy.name, _reward)

        # ── Phase 1: Observability — record completed span ──────────────────
        _span.complete_ts = _time.time()
        _span.success = _success
        _span.attempt_count = attempt  # loop var holds the attempt number where worker succeeded
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

        return {"task_id": task_id, "success": verify_success, "task": task, "_applied_context": _applied_context}

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
