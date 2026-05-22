"""
Integration tests for Planner-Worker Orchestrator.

Tests the full pipeline: planning → execution → verification
"""

import pytest
import json
import os
import tempfile
from pathlib import Path

# Try to import modules
try:
    from scaffold.agent.planner import Planner
    from scaffold.agent.worker import Worker
    from scaffold.agent.verifier import Verifier
    from scaffold.agent.orchestrator import Orchestrator
    from scaffold.agent.token_tracker import TokenTracker
except ImportError:
    # Fallback for different import paths
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'agent'))
    from planner import Planner
    from worker import Worker
    from verifier import Verifier
    from orchestrator import Orchestrator
    from token_tracker import TokenTracker


class TestPlanner:
    """Test planning phase"""
    
    @pytest.fixture
    def planner(self):
        """Create planner instance"""
        # Check if API key is available
        if not os.getenv("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set")
        return Planner()
    
    @pytest.fixture
    def sample_context(self):
        """Sample codebase context"""
        return {
            "modules": "auth.py, user.py, database.py",
            "architecture": "Simple Flask MVC"
        }
    
    def test_planner_initialization(self, planner):
        """Test Planner can be initialized"""
        assert planner is not None
        assert planner.model == "claude-3-5-sonnet-20241022"
    
    def test_planner_generates_json(self, planner, sample_context):
        """Test Planner generates valid JSON blueprint"""
        goal = "Add a simple logging function"
        result = planner.plan(goal, sample_context)
        
        # Validate structure
        assert isinstance(result, dict)
        assert "plan" in result
        assert isinstance(result["plan"], list)
        assert len(result["plan"]) > 0
        
        # Validate each task
        for task in result["plan"]:
            assert "task_id" in task
            assert "file" in task
            assert "action" in task
            assert "complexity" in task
            assert task["complexity"] in ["low", "medium", "high"]


class TestWorker:
    """Test execution phase"""
    
    @pytest.fixture
    def worker(self):
        """Create worker instance"""
        # Note: Worker requires DEEPSEEK_API_KEY
        if not os.getenv("DEEPSEEK_API_KEY"):
            pytest.skip("DEEPSEEK_API_KEY not set")
        return Worker()
    
    @pytest.fixture
    def sample_task(self):
        """Sample micro-task"""
        return {
            "task_id": 1,
            "file": "test.py",
            "action": "Add a simple print function at the end of the file",
            "complexity": "low"
        }
    
    @pytest.fixture
    def sample_file_content(self):
        """Sample Python file content"""
        return """def hello():
    print("Hello, World!")

if __name__ == "__main__":
    hello()
"""
    
    def test_worker_initialization(self, worker):
        """Test Worker can be initialized"""
        assert worker is not None
        assert worker.model == "deepseek-chat"
    
    def test_worker_generates_search_replace(self, worker, sample_task, sample_file_content):
        """Test Worker generates valid SEARCH/REPLACE"""
        context = {"modules": "simple Python project"}
        
        result = worker.execute_task(sample_task, sample_file_content, context)
        
        # Validate structure
        assert isinstance(result, dict)
        if result.get("success"):
            assert "search" in result
            assert "replace" in result
            assert result["search"]  # Non-empty
            assert result["replace"]  # Non-empty


class TestVerifier:
    """Test verification phase"""
    
    @pytest.fixture
    def verifier(self):
        """Create verifier instance"""
        return Verifier()
    
    @pytest.fixture
    def temp_py_file(self):
        """Create temporary Python file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("""def add(a, b):
    return a + b

def subtract(a, b):
    return a - b
""")
            f.flush()
            yield f.name
        
        # Cleanup
        Path(f.name).unlink()
    
    def test_verifier_initialization(self, verifier):
        """Test Verifier can be initialized"""
        assert verifier is not None
    
    def test_verifier_applies_valid_change(self, verifier, temp_py_file):
        """Test Verifier applies valid SEARCH/REPLACE"""
        search_replace = {
            "search": """def subtract(a, b):
    return a - b""",
            "replace": """def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b"""
        }
        
        result = verifier.verify_and_apply(search_replace, temp_py_file)
        
        assert result["success"]
        assert result["applied"]
        assert "multiply" in result["file_content"]
    
    def test_verifier_rejects_invalid_change(self, verifier, temp_py_file):
        """Test Verifier rejects invalid syntax"""
        search_replace = {
            "search": "def add(a, b):",
            "replace": "def add(a, b)  # Missing colon"  # Syntax error
        }
        
        result = verifier.verify_and_apply(search_replace, temp_py_file)
        
        # Should detect the syntax error
        assert not result["success"] or result.get("needs_retry")


class TestOrchestrator:
    """Test full orchestration"""
    
    @pytest.fixture
    def orchestrator(self):
        """Create orchestrator instance"""
        tracker = TokenTracker(monthly_budget=20.0)
        if os.getenv("ANTHROPIC_API_KEY") and os.getenv("DEEPSEEK_API_KEY"):
            return Orchestrator(tracker=tracker)
        else:
            pytest.skip("API keys not configured for full orchestration test")
    
    @pytest.fixture
    def temp_project(self):
        """Create temporary project structure"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a simple Python project
            project_root = Path(tmpdir)
            
            # Create main.py
            (project_root / "main.py").write_text("""def main():
    print("Hello")

if __name__ == "__main__":
    main()
""")
            
            # Create utils.py
            (project_root / "utils.py").write_text("""def format_string(s):
    return s.upper()
""")
            
            yield str(project_root)
    
    def test_orchestrator_initialization(self, orchestrator):
        """Test Orchestrator can be initialized"""
        assert orchestrator is not None
        assert orchestrator.planner is not None
        assert orchestrator.worker is not None
        assert orchestrator.verifier is not None
    
    def test_orchestrator_discovers_codebase(self, orchestrator, temp_project):
        """Test Orchestrator auto-discovers codebase"""
        context = orchestrator._discover_codebase_context(temp_project)
        
        assert "modules" in context
        assert "files" in context
        assert "architecture" in context
        assert "symbols" in context  # Added by ast-based discovery
    
    def test_orchestrator_end_to_end(self, orchestrator, temp_project):
        """Test full end-to-end execution (integration test)"""
        goal = "Add a simple config module to read settings"
        
        result = orchestrator.execute_feature(
            goal=goal,
            codebase_root=temp_project
        )
        
        # Validate result structure
        assert isinstance(result, dict)
        assert "success" in result
        assert "goal" in result
        assert "tasks_completed" in result
        assert "tasks_failed" in result
        assert "total_tasks" in result
        assert "execution_log" in result
        assert "time_elapsed" in result
        
        # Check log entries
        if result["execution_log"]:
            for entry in result["execution_log"]:
                assert "task_id" in entry
                assert "status" in entry  # "completed" or "failed"


class TestTokenTracking:
    """Test cost tracking"""
    
    @pytest.fixture
    def tracker(self):
        """Create token tracker"""
        return TokenTracker(monthly_budget=20.0, monthly_token_target=50_000_000)
    
    def test_tracker_initialization(self, tracker):
        """Test TokenTracker initialization"""
        assert tracker.monthly_budget == 20.0
        assert tracker.monthly_token_target == 50_000_000
    
    def test_tracker_records_cost(self, tracker):
        """Test TokenTracker records API calls"""
        tracker.record(
            request_type="planning",
            model="Claude Sonnet 3.5",
            input_tokens=100,
            output_tokens=50,
            cost=0.05
        )
        
        status = tracker.get_budget_status()
        
        assert status["total_cost"] == 0.05
        assert status["total_tokens"] == 150
    
    def test_tracker_shows_status(self, tracker, capsys):
        """Test TokenTracker displays status"""
        tracker.record(
            request_type="execution",
            model="DeepSeek",
            input_tokens=100,
            output_tokens=50,
            cost=0.01
        )
        
        tracker.show_status()
        
        captured = capsys.readouterr()
        assert "spent" in captured.out.lower()
        assert "remaining" in captured.out.lower()


def test_imports():
    """Test that all modules can be imported"""
    assert Planner is not None
    assert Worker is not None
    assert Verifier is not None
    assert Orchestrator is not None
    assert TokenTracker is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
