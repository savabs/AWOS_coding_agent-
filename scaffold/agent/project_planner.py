"""
ProjectPlanner — Persistent Goal DAG for cross-session project memory (Phase 5C).

Maintains a directed acyclic graph of GoalNodes at .awos/goals/{goal_id}.json,
enabling AWOS to:
  1. Resume work across sessions (skip completed tasks)
  2. Adapt to failures (decompose and replan within graph context)
  3. Extend existing goals (new requests appended to related open graphs)

Key design decisions:
  - One JSON file per goal (crash isolation)
  - Atomic write via .tmp + os.replace()
  - Goal matching via Jaccard keyword overlap (threshold 0.4) — zero LLM cost
  - GoalNode status lifecycle: pending → in_progress → completed | failed | stalled | blocked

References:
  - AFlow (ICLR 2025, arXiv:2410.10762) — workflow as search over task graphs
  - HiPlan (Aug 2025) — hierarchical planning with milestones + local steps
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_GOALS_DIR = ".awos/goals"
_MAX_OPEN_GOALS = 50
_MAX_NODES_PER_GRAPH = 200
_MATCH_THRESHOLD = 0.4
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "to", "in", "of", "for", "with",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "can", "must", "shall", "this", "that", "these", "those", "i", "you",
    "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their", "on", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "under", "again", "further", "then", "once", "here", "there",
    "when", "where", "why", "how", "all", "each", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "just", "also",
})


class GoalGraphFullError(Exception):
    """Raised when appending would exceed MAX_NODES_PER_GRAPH."""


@dataclass
class GoalNode:
    """A single node in the Goal DAG."""

    goal_id: str
    description: str
    parent_ids: List[str] = field(default_factory=list)
    child_ids: List[str] = field(default_factory=list)
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "pending"  # pending | in_progress | completed | failed | stalled | blocked
    created_at: str = field(default_factory=lambda: GoalNode._now())
    updated_at: str = field(default_factory=lambda: GoalNode._now())
    completed_at: Optional[str] = None
    session_ids: List[str] = field(default_factory=list)
    failure_count: int = 0
    critique_ids: List[str] = field(default_factory=list)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @classmethod
    def new_id(cls) -> str:
        return f"g_{uuid.uuid4().hex[:12]}"

    def touch(self, session_id: str) -> None:
        """Update timestamps and record session touch."""
        self.updated_at = self._now()
        if session_id not in self.session_ids:
            self.session_ids.append(session_id)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> GoalNode:
        # Strip GoalNode keys that are not in the dataclass (forward compat)
        field_names = {f.name for f in cls.__dataclass_fields__.values()}
        clean = {k: v for k, v in data.items() if k in field_names}
        return cls(**clean)


class GoalGraph:
    """Directed acyclic graph of GoalNodes."""

    def __init__(self, root_id: str, nodes: Optional[Dict[str, GoalNode]] = None):
        self.root_id = root_id
        self.nodes: Dict[str, GoalNode] = nodes or {}

    def add_node(self, node: GoalNode) -> None:
        if node.goal_id in self.nodes:
            logger.warning("[GoalGraph] overwriting node %s", node.goal_id)
        self.nodes[node.goal_id] = node

    def get_node(self, goal_id: str) -> Optional[GoalNode]:
        return self.nodes.get(goal_id)

    def pending_nodes(self) -> List[GoalNode]:
        """
        Return all nodes whose status is 'pending' and all parents are completed.
        Ordered topologically (parents before children).
        """
        # Kahn's algorithm: find all pending nodes with no pending/in_progress parents
        pending = [
            n for n in self.nodes.values()
            if n.status in ("pending", "in_progress", "stalled")
        ]
        # Filter to those whose real parents are all completed (root excluded)
        ready = []
        for node in pending:
            real_parents = [pid for pid in node.parent_ids if pid != self.root_id]
            parents_done = (
                all(
                    self.nodes.get(pid, GoalNode(goal_id=pid, description="")).status == "completed"
                    for pid in real_parents
                ) if real_parents else True
            )
            if parents_done:
                ready.append(node)

        # Topological sort by creation time (stable, deterministic)
        ready.sort(key=lambda n: n.created_at)
        return ready

    def all_complete(self) -> bool:
        """True when every non-root node is completed."""
        for node in self.nodes.values():
            if node.goal_id == self.root_id:
                continue
            if node.status != "completed":
                return False
        return True

    def to_dict(self) -> dict:
        return {
            "root_id": self.root_id,
            "nodes": {gid: n.to_dict() for gid, n in self.nodes.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> GoalGraph:
        root_id = data.get("root_id", "")
        nodes_raw = data.get("nodes", {})
        nodes = {}
        for gid, ndata in nodes_raw.items():
            try:
                nodes[gid] = GoalNode.from_dict(ndata)
            except Exception as exc:
                logger.warning("[GoalGraph] skipping corrupt node %s: %s", gid, exc)
        return cls(root_id=root_id, nodes=nodes)


class ProjectPlanner:
    """
    Persistent Goal DAG manager.
    Stores one JSON file per goal in .awos/goals/.
    """

    def __init__(self, goals_dir: str = _GOALS_DIR, vector_memory=None):
        self._dir = Path(goals_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "INDEX.json"
        self._vector_memory = vector_memory  # Optional VectorMemory for semantic goal matching

    # ── Public API ──────────────────────────────────────────────────────

    def load_or_create_goal(
        self,
        description: str,
        session_id: str,
    ) -> GoalGraph:
        """
        Find an open goal matching *description* (Jaccard >= MATCH_THRESHOLD),
        or create a new root GoalNode and persist it.
        """
        index = self._load_index()

        # Phase 6: Try VectorMemory semantic match first (higher precision than Jaccard)
        if self._vector_memory is not None:
            try:
                matched_text = self._vector_memory.find_similar_goal(description)
                if matched_text is not None:
                    for goal_id, meta in index.items():
                        if meta.get("status") == "completed":
                            continue
                        if meta.get("description", "") == matched_text:
                            graph = self._load_graph(goal_id)
                            if graph is not None:
                                logger.info(
                                    "[ProjectPlanner] vector-matched goal %s for '%s...'",
                                    goal_id, description[:40],
                                )
                                return graph
            except Exception as _vm_exc:
                logger.warning("[ProjectPlanner] vector goal match failed: %s", _vm_exc)

        # Fallback: Jaccard keyword overlap
        best_id: Optional[str] = None
        best_score = 0.0
        for goal_id, meta in index.items():
            if meta.get("status") == "completed":
                continue
            score = _jaccard(description, meta.get("description", ""))
            if score > best_score:
                best_score = score
                best_id = goal_id

        if best_id and best_score >= _MATCH_THRESHOLD:
            graph = self._load_graph(best_id)
            if graph is not None:
                logger.info(
                    "[ProjectPlanner] Jaccard-matched goal %s (score=%.2f) for '%s...'",
                    best_id, best_score, description[:40],
                )
                return graph

        # Create new root node
        root_id = GoalNode.new_id()
        root = GoalNode(
            goal_id=root_id,
            description=description,
            status="in_progress",
        )
        root.touch(session_id)
        graph = GoalGraph(root_id=root_id)
        graph.add_node(root)

        self._save_graph(graph)
        self._update_index_entry(root_id, root)
        logger.info(
            "[ProjectPlanner] created new goal %s for '%s...'",
            root_id, description[:40],
        )
        return graph

    def ensure_task_nodes(
        self,
        graph: GoalGraph,
        tasks: List[Dict[str, Any]],
        session_id: str,
    ) -> None:
        """
        Ensure every task in *tasks* has a corresponding GoalNode.
        Skips tasks already present in the graph.
        New tasks are appended as child nodes under the root.
        """
        # Collect existing task_ids to avoid duplication
        existing_task_ids = set()
        for node in graph.nodes.values():
            for t in node.tasks:
                tid = t.get("task_id")
                if tid is not None:
                    existing_task_ids.add(tid)

        for task in tasks:
            task_id = task.get("task_id")
            if task_id is not None and task_id in existing_task_ids:
                continue

            node_id = GoalNode.new_id()
            node = GoalNode(
                goal_id=node_id,
                description=task.get("action", ""),
                tasks=[task],
                status="pending",
            )
            node.touch(session_id)
            graph.add_node(node)

            # Link child to root
            root = graph.get_node(graph.root_id)
            if root is not None:
                if node_id not in root.child_ids:
                    root.child_ids.append(node_id)
                    root.touch(session_id)

        self._save_graph(graph)

    def update_node_status(
        self,
        graph: GoalGraph,
        goal_id: str,
        new_status: str,
        session_id: str,
        task_id: Optional[str] = None,
        critique_id: Optional[str] = None,
    ) -> None:
        """
        Update a node status, propagate effects, persist atomically.
        """
        node = graph.get_node(goal_id)
        if node is None:
            logger.warning(
                "[ProjectPlanner] update_node_status: node %s not found", goal_id,
            )
            return

        node.status = new_status
        node.touch(session_id)

        if new_status == "completed":
            node.completed_at = GoalNode._now()
            node.failure_count = 0
            # Unlock children whose other parents are also done
            for cid in node.child_ids:
                child = graph.get_node(cid)
                if child is None:
                    continue
                if child.status == "blocked":
                    real_parents = [pid for pid in child.parent_ids if pid != graph.root_id]
                    all_parents_done = all(
                        graph.get_node(pid) is not None
                        and graph.get_node(pid).status == "completed"
                        for pid in real_parents
                    ) if real_parents else True
                    if all_parents_done:
                        child.status = "pending"
                        child.touch(session_id)

        elif new_status == "failed":
            node.failure_count += 1
            if node.failure_count >= 2:
                # Block children if max failures reached
                for cid in node.child_ids:
                    child = graph.get_node(cid)
                    if child is not None and child.status in ("pending", "in_progress"):
                        child.status = "blocked"
                        child.touch(session_id)

        if critique_id and critique_id not in node.critique_ids:
            node.critique_ids.append(critique_id)

        # Propagate to root: if all non-root children completed → root completed
        if new_status == "completed":
            root = graph.get_node(graph.root_id)
            if root is not None:
                all_done = all(
                    graph.get_node(cid) is not None
                    and graph.get_node(cid).status == "completed"
                    for cid in root.child_ids
                )
                if all_done and root.child_ids:
                    root.status = "completed"
                    root.completed_at = GoalNode._now()
                    root.touch(session_id)

        self._save_graph(graph)
        self._update_index_entry(graph.root_id, graph.get_node(graph.root_id) or node)

    def append_nodes(
        self,
        graph: GoalGraph,
        new_nodes: List[GoalNode],
        parent_id: str,
        session_id: str,
    ) -> GoalGraph:
        """
        Append *new_nodes* under *parent_id*. Used for goal extension
        and for TaskDecomposer sub-task expansion.
        Raises GoalGraphFullError if limit would be exceeded.
        """
        if len(graph.nodes) + len(new_nodes) > _MAX_NODES_PER_GRAPH:
            raise GoalGraphFullError(
                f"Graph would exceed {_MAX_NODES_PER_GRAPH} nodes "
                f"(current {len(graph.nodes)}, adding {len(new_nodes)})"
            )

        parent = graph.get_node(parent_id)
        if parent is None:
            logger.warning(
                "[ProjectPlanner] append_nodes: parent %s not found", parent_id,
            )
            return graph

        for node in new_nodes:
            if parent_id not in node.parent_ids:
                node.parent_ids.append(parent_id)
            node.touch(session_id)
            graph.add_node(node)
            if node.goal_id not in parent.child_ids:
                parent.child_ids.append(node.goal_id)

        parent.touch(session_id)
        self._save_graph(graph)
        return graph

    def get_pending_graphs(self) -> List[GoalGraph]:
        """Return all non-completed GoalGraphs from INDEX."""
        index = self._load_index()
        graphs = []
        for goal_id, meta in index.items():
            if meta.get("status") != "completed":
                graph = self._load_graph(goal_id)
                if graph is not None:
                    graphs.append(graph)
        return graphs

    def summary(self) -> dict:
        """Return {total, by_status, oldest_open} from INDEX."""
        index = self._load_index()
        total = len(index)
        by_status: Dict[str, int] = {}
        oldest_open = None
        for meta in index.values():
            st = meta.get("status", "UNKNOWN")
            by_status[st] = by_status.get(st, 0) + 1
            if st != "completed":
                updated = meta.get("updated_at", "")
                if oldest_open is None or (updated and updated < oldest_open):
                    oldest_open = updated
        return {
            "total": total,
            "by_status": by_status,
            "oldest_open": oldest_open,
        }

    def find_node_for_task(
        self,
        graph: GoalGraph,
        task_id: Any,
    ) -> Optional[GoalNode]:
        """Find the GoalNode that contains a task with the given task_id."""
        for node in graph.nodes.values():
            for task in node.tasks:
                if task.get("task_id") == task_id:
                    return node
        return None

    # ── Persistence helpers ───────────────────────────────────────────────

    def _load_index(self) -> Dict[str, Dict[str, Any]]:
        if not self._index_path.exists():
            return {}
        try:
            with open(self._index_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[ProjectPlanner] INDEX load failed: %s", exc)
            return {}

    def _save_index(self, index: Dict[str, Dict[str, Any]]) -> None:
        self._atomic_write(self._index_path, index)

    def _update_index_entry(self, goal_id: str, node: GoalNode) -> None:
        index = self._load_index()
        index[goal_id] = {
            "description": node.description,
            "status": node.status,
            "updated_at": node.updated_at,
        }
        # Prune completed older than 30 days (lazy eviction)
        cutoff = _days_ago_iso(30)
        to_remove = [
            gid for gid, meta in index.items()
            if meta.get("status") == "completed"
            and meta.get("updated_at", "9999") < cutoff
        ]
        for gid in to_remove:
            index.pop(gid, None)
            # Optionally archive, but for now just remove from index
            # File stays on disk — harmless
        # Cap open goals
        open_goals = [
            (gid, meta) for gid, meta in index.items()
            if meta.get("status") != "completed"
        ]
        if len(open_goals) > _MAX_OPEN_GOALS:
            # Sort by updated_at ascending (oldest first), trim
            open_goals.sort(key=lambda x: x[1].get("updated_at", ""))
            for gid, _ in open_goals[:len(open_goals) - _MAX_OPEN_GOALS]:
                index.pop(gid, None)
        self._save_index(index)

    def _load_graph(self, goal_id: str) -> Optional[GoalGraph]:
        path = self._dir / f"{goal_id}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return GoalGraph.from_dict(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[ProjectPlanner] graph load failed for %s: %s", goal_id, exc)
            return None

    def _save_graph(self, graph: GoalGraph) -> None:
        path = self._dir / f"{graph.root_id}.json"
        self._atomic_write(path, graph.to_dict())

    def _atomic_write(self, path: Path, data: Any) -> None:
        """Write to .tmp then rename for crash safety."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(str(tmp), str(path))
        except OSError as exc:
            logger.warning("[ProjectPlanner] atomic write failed: %s", exc)


# ── Helpers ───────────────────────────────────────────────────────────


def _tokenise(text: str) -> set:
    """Lowercase, strip punctuation, split on non-alphanum, remove stop words."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = text.split()
    return {t for t in tokens if t not in _STOP_WORDS and len(t) > 1}


def _jaccard(a: str, b: str) -> float:
    """Jaccard similarity between two strings."""
    set_a = _tokenise(a)
    set_b = _tokenise(b)
    if not set_a or not set_b:
        return 0.0
    # Guard: very short descriptions (fewer than 3 meaningful words) always score 0
    if len(set_a) < 3 or len(set_b) < 3:
        return 0.0
    inter = set_a & set_b
    union = set_a | set_b
    return len(inter) / len(union) if union else 0.0


def _days_ago_iso(days: int) -> str:
    from datetime import timedelta
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
