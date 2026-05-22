"""
Tests for ProjectPlanner — Persistent Goal DAG (Phase 5C).

All tests use tmp_path to avoid polluting the real .awos/goals/ directory.
"""

import json
import uuid
from pathlib import Path

import pytest

from scaffold.agent.project_planner import (
    GoalGraphFullError,
    GoalGraph,
    GoalNode,
    ProjectPlanner,
    _jaccard,
)


# ── TestGoalNode ──────────────────────────────────────────────────────────


class TestGoalNode:

    def test_new_id_prefix(self):
        gid = GoalNode.new_id()
        assert gid.startswith("g_")
        assert len(gid) == 14  # g_ + 12 hex chars

    def test_new_id_unique(self):
        ids = {GoalNode.new_id() for _ in range(100)}
        assert len(ids) == 100

    def test_default_status_pending(self):
        node = GoalNode(goal_id="g_1", description="test")
        assert node.status == "pending"
        assert node.failure_count == 0
        assert node.completed_at is None

    def test_touch_updates_timestamps(self):
        node = GoalNode(goal_id="g_1", description="test")
        old_updated = node.updated_at
        node.touch("sess_1")
        assert node.updated_at >= old_updated
        assert "sess_1" in node.session_ids

    def test_touch_dedupes_session_ids(self):
        node = GoalNode(goal_id="g_1", description="test")
        node.touch("sess_1")
        node.touch("sess_1")
        assert node.session_ids == ["sess_1"]

    def test_to_dict_roundtrip(self):
        node = GoalNode(
            goal_id="g_1",
            description="desc",
            parent_ids=["g_root"],
            child_ids=["g_2"],
            tasks=[{"task_id": 1, "action": "add foo"}],
            status="in_progress",
            failure_count=2,
            critique_ids=["c1", "c2"],
        )
        data = node.to_dict()
        restored = GoalNode.from_dict(data)
        assert restored.goal_id == "g_1"
        assert restored.description == "desc"
        assert restored.parent_ids == ["g_root"]
        assert restored.child_ids == ["g_2"]
        assert restored.tasks == [{"task_id": 1, "action": "add foo"}]
        assert restored.status == "in_progress"
        assert restored.failure_count == 2
        assert restored.critique_ids == ["c1", "c2"]

    def test_from_dict_ignores_unknown_fields(self):
        data = GoalNode(goal_id="g_1", description="test").to_dict()
        data["unknown_field"] = "should_be_ignored"
        node = GoalNode.from_dict(data)
        assert node.goal_id == "g_1"
        assert not hasattr(node, "unknown_field")


# ── TestGoalGraph ─────────────────────────────────────────────────────────


class TestGoalGraph:

    def test_add_and_get(self):
        g = GoalGraph("g_root")
        n = GoalNode("g_1", "node 1")
        g.add_node(n)
        assert g.get_node("g_1") is n
        assert g.get_node("missing") is None

    def test_pending_nodes_root_only(self):
        root = GoalNode("g_root", "root", status="in_progress")
        g = GoalGraph("g_root", {"g_root": root})
        # Root has no parents but status is in_progress, so it should appear
        pending = g.pending_nodes()
        assert pending == [root]

    def test_pending_nodes_blocked_by_parent(self):
        root = GoalNode("g_root", "root", status="in_progress")
        child1 = GoalNode("g_1", "child 1", parent_ids=["g_root"], status="pending")
        child2 = GoalNode("g_2", "child 2", parent_ids=["g_1"], status="pending")
        g = GoalGraph("g_root", {
            "g_root": root, "g_1": child1, "g_2": child2,
        })
        pending = g.pending_nodes()
        # Only child1 is pending with all parents (root) completed-ish...
        # Wait, root is in_progress, not completed. So child1's parent is NOT completed.
        # Let me fix: root should be completed for child1 to be ready.
        root.status = "completed"
        pending = g.pending_nodes()
        assert child1 in pending
        assert child2 not in pending  # blocked by child1

    def test_pending_nodes_topological_order(self):
        root = GoalNode("g_root", "root", status="completed")
        c1 = GoalNode("g_1", "c1", parent_ids=["g_root"], status="pending", created_at="2026-01-01T00:00:00Z")
        c2 = GoalNode("g_2", "c2", parent_ids=["g_root"], status="pending", created_at="2026-01-02T00:00:00Z")
        g = GoalGraph("g_root", {"g_root": root, "g_1": c1, "g_2": c2})
        pending = g.pending_nodes()
        assert pending == [c1, c2]

    def test_all_complete(self):
        root = GoalNode("g_root", "root")
        c1 = GoalNode("g_1", "c1", parent_ids=["g_root"], status="completed")
        g = GoalGraph("g_root", {"g_root": root, "g_1": c1})
        assert g.all_complete()

    def test_all_complete_not_done(self):
        root = GoalNode("g_root", "root")
        c1 = GoalNode("g_1", "c1", parent_ids=["g_root"], status="pending")
        g = GoalGraph("g_root", {"g_root": root, "g_1": c1})
        assert not g.all_complete()

    def test_to_dict_from_dict_roundtrip(self):
        root = GoalNode("g_root", "root", status="in_progress")
        c1 = GoalNode("g_1", "c1", parent_ids=["g_root"], tasks=[{"task_id": 42}], status="pending")
        g = GoalGraph("g_root", {"g_root": root, "g_1": c1})
        data = g.to_dict()
        restored = GoalGraph.from_dict(data)
        assert restored.root_id == "g_root"
        assert "g_1" in restored.nodes
        assert restored.nodes["g_1"].tasks == [{"task_id": 42}]

    def test_from_dict_skips_corrupt_nodes(self):
        data = {
            "root_id": "g_root",
            "nodes": {
                "g_root": {"goal_id": "g_root", "description": "ok", "status": "pending"},
                "g_bad": "not_a_dict",
            },
        }
        g = GoalGraph.from_dict(data)
        assert g.get_node("g_root") is not None
        assert g.get_node("g_bad") is None


# ── TestProjectPlannerSaveLoad ────────────────────────────────────────────


class TestProjectPlannerSaveLoad:

    def test_init_creates_dir(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        assert (tmp_path / "goals").exists()

    def test_create_goal_creates_file(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Add OAuth auth", "sess_1")
        assert (tmp_path / "goals" / f"{g.root_id}.json").exists()

    def test_create_goal_creates_index(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Add OAuth auth", "sess_1")
        assert (tmp_path / "goals" / "INDEX.json").exists()
        index = json.loads((tmp_path / "goals" / "INDEX.json").read_text())
        assert g.root_id in index
        assert index[g.root_id]["status"] == "in_progress"

    def test_load_returns_same_goal_id(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g1 = p.load_or_create_goal("Add OAuth2 authentication system", "sess_1")
        g2 = p.load_or_create_goal("Add OAuth2 authentication system", "sess_2")
        assert g1.root_id == g2.root_id

    def test_reload_persists_nodes(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Add OAuth auth", "sess_1")
        child = GoalNode(GoalNode.new_id(), "child task", parent_ids=[g.root_id])
        p.append_nodes(g, [child], g.root_id, "sess_1")
        # Reload via new planner instance
        p2 = ProjectPlanner(str(tmp_path / "goals"))
        g2 = p2.load_or_create_goal("Add OAuth auth", "sess_2")
        assert child.goal_id in g2.nodes
        assert g2.nodes[child.goal_id].description == "child task"

    def test_summary_counts(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g1 = p.load_or_create_goal("Goal one", "s1")
        g2 = p.load_or_create_goal("Goal two", "s2")
        # Mark one completed
        root1 = g1.get_node(g1.root_id)
        p.update_node_status(g1, g1.root_id, "completed", "s1")
        s = p.summary()
        assert s["total"] == 2
        assert s["by_status"].get("completed", 0) == 1
        assert s["by_status"].get("in_progress", 0) == 1


# ── TestGoalMatching ──────────────────────────────────────────────────────


class TestGoalMatching:

    def test_exact_match_same_goal(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g1 = p.load_or_create_goal("Add user authentication", "sess_1")
        g2 = p.load_or_create_goal("Add user authentication", "sess_2")
        assert g1.root_id == g2.root_id

    def test_similar_match_same_goal(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g1 = p.load_or_create_goal("Add OAuth2 authentication", "sess_1")
        g2 = p.load_or_create_goal("Add OAuth2 authentication system", "sess_2")
        assert g1.root_id == g2.root_id

    def test_different_goal_creates_new(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g1 = p.load_or_create_goal("Add OAuth2 auth", "sess_1")
        g2 = p.load_or_create_goal("Add rate limiting middleware", "sess_2")
        assert g1.root_id != g2.root_id

    def test_short_description_always_new(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g1 = p.load_or_create_goal("Fix bug", "sess_1")
        g2 = p.load_or_create_goal("Fix bug", "sess_2")
        # "fix bug" has < 3 meaningful words after stop-word removal
        assert g1.root_id != g2.root_id

    def test_completed_goal_not_matched(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g1 = p.load_or_create_goal("Add OAuth2 auth", "sess_1")
        p.update_node_status(g1, g1.root_id, "completed", "sess_1")
        g2 = p.load_or_create_goal("Add OAuth2 auth", "sess_2")
        # Completed goals are excluded from matching
        assert g1.root_id != g2.root_id


# ── TestStatusPropagation ───────────────────────────────────────────────


class TestStatusPropagation:

    def test_update_completed_sets_completed_at(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        p.update_node_status(g, g.root_id, "completed", "s1")
        assert g.get_node(g.root_id).status == "completed"
        assert g.get_node(g.root_id).completed_at is not None

    def test_failure_increments_count(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        p.update_node_status(g, g.root_id, "failed", "s1")
        assert g.get_node(g.root_id).failure_count == 1
        p.update_node_status(g, g.root_id, "failed", "s1")
        assert g.get_node(g.root_id).failure_count == 2

    def test_twice_failed_blocks_children(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        child = GoalNode(GoalNode.new_id(), "child", parent_ids=[g.root_id], status="pending")
        p.append_nodes(g, [child], g.root_id, "s1")
        p.update_node_status(g, g.root_id, "failed", "s1")
        p.update_node_status(g, g.root_id, "failed", "s1")
        assert g.get_node(child.goal_id).status == "blocked"

    def test_root_completes_when_all_children_done(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        c1 = GoalNode(GoalNode.new_id(), "c1", parent_ids=[g.root_id])
        c2 = GoalNode(GoalNode.new_id(), "c2", parent_ids=[g.root_id])
        p.append_nodes(g, [c1, c2], g.root_id, "s1")
        p.update_node_status(g, c1.goal_id, "completed", "s1")
        assert g.get_node(g.root_id).status == "in_progress"
        p.update_node_status(g, c2.goal_id, "completed", "s1")
        assert g.get_node(g.root_id).status == "completed"

    def test_completed_unlocks_blocked_children(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        parent_task = GoalNode(GoalNode.new_id(), "parent")
        child = GoalNode(GoalNode.new_id(), "child", parent_ids=[parent_task.goal_id], status="blocked")
        p.append_nodes(g, [parent_task], g.root_id, "s1")
        p.append_nodes(g, [child], parent_task.goal_id, "s1")
        p.update_node_status(g, parent_task.goal_id, "completed", "s1")
        assert g.get_node(child.goal_id).status == "pending"

    def test_update_with_task_and_critique(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        p.update_node_status(g, g.root_id, "failed", "s1", task_id="t1", critique_id="c1")
        node = g.get_node(g.root_id)
        assert "c1" in node.critique_ids


# ── TestAppendNodes ───────────────────────────────────────────────────────


class TestAppendNodes:

    def test_append_links_parent_child(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        child = GoalNode(GoalNode.new_id(), "child")
        p.append_nodes(g, [child], g.root_id, "s1")
        root = g.get_node(g.root_id)
        assert child.goal_id in root.child_ids
        assert child.parent_ids == [g.root_id]

    def test_append_persists(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Add OAuth authentication system", "s1")
        child = GoalNode(GoalNode.new_id(), "child")
        p.append_nodes(g, [child], g.root_id, "s1")
        p2 = ProjectPlanner(str(tmp_path / "goals"))
        g2 = p2.load_or_create_goal("Add OAuth authentication system", "s2")
        assert child.goal_id in g2.get_node(g2.root_id).child_ids

    def test_graph_full_raises(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        # Fill to exactly 200 nodes (1 root + 199 children)
        for i in range(199):
            child = GoalNode(GoalNode.new_id(), f"c{i}")
            p.append_nodes(g, [child], g.root_id, "s1")
        assert len(g.nodes) == 200
        with pytest.raises(GoalGraphFullError):
            p.append_nodes(g, [GoalNode(GoalNode.new_id(), "overflow")], g.root_id, "s1")


# ── TestEdgeCases ───────────────────────────────────────────────────────────


class TestEdgeCases:

    def test_corrupt_file_handled(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        # Corrupt the JSON file
        path = tmp_path / "goals" / f"{g.root_id}.json"
        path.write_text("not json at all {[[")
        # Loading should not crash; creates a new goal instead
        g2 = p.load_or_create_goal("Test", "s2")
        # Because the file is corrupt, it can't load the existing one
        # However, INDEX.json still points to it, so load_graph returns None
        # and a new goal is created
        assert g2 is not None

    def test_load_graph_returns_none_for_missing(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        assert p._load_graph("g_nonexistent") is None

    def test_get_pending_graphs_excludes_completed(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g1 = p.load_or_create_goal("Open goal", "s1")
        g2 = p.load_or_create_goal("Completed goal", "s2")
        p.update_node_status(g2, g2.root_id, "completed", "s2")
        pending = p.get_pending_graphs()
        ids = {pg.root_id for pg in pending}
        assert g1.root_id in ids
        assert g2.root_id not in ids

    def test_find_node_for_task_found(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        task = {"task_id": 42, "action": "add foo"}
        p.ensure_task_nodes(g, [task], "s1")
        node = p.find_node_for_task(g, 42)
        assert node is not None
        assert node.description == "add foo"

    def test_find_node_for_task_not_found(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        node = p.find_node_for_task(g, 999)
        assert node is None

    def test_summary_oldest_open(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        p.load_or_create_goal("Old goal", "s1")
        p.load_or_create_goal("Newer goal", "s2")
        s = p.summary()
        assert s["oldest_open"] is not None


# ── TestPlannerSkipping ───────────────────────────────────────────────────


class TestPlannerSkipping:

    def test_filter_completed_tasks_skips_match(self):
        from scaffold.agent.planner import Planner
        mock_goal = type("MockGraph", (), {})()
        completed_node = GoalNode("g_1", "Add login function")
        completed_node.status = "completed"
        mock_goal.nodes = {"g_1": completed_node}
        tasks = [
            {"task_id": 1, "action": "Add login function"},
            {"task_id": 2, "action": "Add logout route"},
        ]
        filtered = Planner._filter_completed_tasks(tasks, mock_goal)
        assert len(filtered) == 1
        assert filtered[0]["task_id"] == 2

    def test_filter_completed_tasks_no_match(self):
        from scaffold.agent.planner import Planner
        mock_goal = type("MockGraph", (), {})()
        mock_goal.nodes = {}
        tasks = [
            {"task_id": 1, "action": "Add login function"},
        ]
        filtered = Planner._filter_completed_tasks(tasks, mock_goal)
        assert len(filtered) == 1

    def test_filter_completed_tasks_none_goal(self):
        from scaffold.agent.planner import Planner
        tasks = [
            {"task_id": 1, "action": "Add login function"},
        ]
        filtered = Planner._filter_completed_tasks(tasks, None)
        assert len(filtered) == 1


# ── TestJaccardHelper ────────────────────────────────────────────────────


class TestJaccard:

    def test_exact_match(self):
        assert _jaccard("Add OAuth2 authentication", "Add OAuth2 authentication") == 1.0

    def test_partial_overlap(self):
        score = _jaccard("Add OAuth2 auth", "Add OAuth2 authentication system")
        assert 0.0 < score < 1.0

    def test_no_overlap(self):
        assert _jaccard("Add OAuth2", "Deploy to Kubernetes") == 0.0

    def test_short_descriptions_zero(self):
        assert _jaccard("Fix bug", "Fix bug") == 0.0  # fewer than 3 meaningful words

    def test_empty_string(self):
        assert _jaccard("", "Add OAuth2") == 0.0
        assert _jaccard("Add OAuth2", "") == 0.0

    def test_case_insensitive(self):
        assert _jaccard("Add OAuth2 authentication", "add oauth2 AUTHENTICATION") == 1.0


# ── TestEnsureTaskNodes ────────────────────────────────────────────────────


class TestEnsureTaskNodes:

    def test_ensure_task_nodes_creates_children(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        tasks = [
            {"task_id": 1, "action": "add foo"},
            {"task_id": 2, "action": "add bar"},
        ]
        p.ensure_task_nodes(g, tasks, "s1")
        root = g.get_node(g.root_id)
        assert len(root.child_ids) == 2

    def test_ensure_task_nodes_idempotent(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        tasks = [
            {"task_id": 1, "action": "add foo"},
        ]
        p.ensure_task_nodes(g, tasks, "s1")
        p.ensure_task_nodes(g, tasks, "s1")  # second call
        root = g.get_node(g.root_id)
        assert len(root.child_ids) == 1  # not duplicated

    def test_ensure_task_nodes_appends_new_only(self, tmp_path):
        p = ProjectPlanner(str(tmp_path / "goals"))
        g = p.load_or_create_goal("Test", "s1")
        p.ensure_task_nodes(g, [{"task_id": 1, "action": "add foo"}], "s1")
        p.ensure_task_nodes(g, [{"task_id": 1, "action": "add foo"}, {"task_id": 2, "action": "add bar"}], "s1")
        root = g.get_node(g.root_id)
        assert len(root.child_ids) == 2
