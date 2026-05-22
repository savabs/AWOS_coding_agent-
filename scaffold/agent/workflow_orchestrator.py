#!/usr/bin/env python3
"""
Project Workflow Orchestrator — Understands repo state and executes next logical steps.

Replaces manual tracking with automatic "what's next?" suggestions.

Usage:
    orchestrate status              # Show current project state
    orchestrate next                # Execute next logical step
    orchestrate auto [count]        # Auto-run N next steps
    orchestrate plan                # Show full workflow plan
    orchestrate reset               # Reset project state tracking
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class ProjectStage(Enum):
    """Project lifecycle stages."""
    INIT = "init"                      # Project just created
    SETUP = "setup"                    # Setting up dependencies
    STRUCTURE = "structure"            # Building core architecture
    FEATURES = "features"              # Implementing features
    TESTING = "testing"                # Writing tests
    OPTIMIZATION = "optimization"      # Performance tuning
    SECURITY = "security"              # Security hardening
    DOCUMENTATION = "documentation"    # Writing docs
    DEPLOYMENT = "deployment"          # Deployment ready
    MAINTENANCE = "maintenance"        # Live project


class TaskStatus(Enum):
    """Task execution status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class ProjectTask:
    """A single task in the workflow."""
    id: str
    title: str
    description: str
    stage: ProjectStage
    status: TaskStatus = TaskStatus.PENDING
    depends_on: List[str] = field(default_factory=list)  # Task IDs
    files_to_modify: List[str] = field(default_factory=list)
    aider_prompt: str = ""
    completed_at: Optional[str] = None
    cost: float = 0.0
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'stage': self.stage.value,
            'status': self.status.value,
            'depends_on': self.depends_on,
            'files_to_modify': self.files_to_modify,
            'aider_prompt': self.aider_prompt,
            'completed_at': self.completed_at,
            'cost': self.cost,
        }


class ProjectOrchestrator:
    """Understands project state and executes next steps automatically."""
    
    def __init__(self, project_root: Path = None):
        """Initialize orchestrator."""
        self.project_root = project_root or Path.cwd()
        self.state_file = self.project_root / ".awos" / "workflow_state.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.tasks: Dict[str, ProjectTask] = {}
        self.current_stage = ProjectStage.INIT
        self.load_state()
        self._build_default_workflow()
    
    def load_state(self):
        """Load project state from disk."""
        if self.state_file.exists():
            with open(self.state_file) as f:
                data = json.load(f)
                self.current_stage = ProjectStage(data.get('stage', 'init'))
                
                # Reconstruct tasks
                for task_dict in data.get('tasks', []):
                    task = ProjectTask(
                        id=task_dict['id'],
                        title=task_dict['title'],
                        description=task_dict['description'],
                        stage=ProjectStage(task_dict['stage']),
                        status=TaskStatus(task_dict['status']),
                        depends_on=task_dict.get('depends_on', []),
                        files_to_modify=task_dict.get('files_to_modify', []),
                        aider_prompt=task_dict.get('aider_prompt', ''),
                        completed_at=task_dict.get('completed_at'),
                        cost=task_dict.get('cost', 0.0),
                    )
                    self.tasks[task.id] = task
    
    def save_state(self):
        """Save project state to disk."""
        data = {
            'stage': self.current_stage.value,
            'last_updated': datetime.now().isoformat(),
            'tasks': [task.to_dict() for task in self.tasks.values()]
        }
        with open(self.state_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def _build_default_workflow(self):
        """Build default workflow for a typical project."""
        if self.tasks:
            return  # Already has tasks
        
        workflow = [
            # SETUP stage
            ProjectTask(
                id="setup_1",
                title="Initialize Project Structure",
                description="Create core directory structure and config files",
                stage=ProjectStage.SETUP,
                files_to_modify=["pyproject.toml", "README.md"],
                aider_prompt="Create a basic Python project structure with pyproject.toml, README.md, .gitignore, and src/ folder"
            ),
            
            # STRUCTURE stage
            ProjectTask(
                id="struct_1",
                title="Define Core Architecture",
                description="Create main application skeleton",
                stage=ProjectStage.STRUCTURE,
                depends_on=["setup_1"],
                aider_prompt="Create main.py with basic CLI interface that prints 'Hello, World!'"
            ),
            
            ProjectTask(
                id="struct_2",
                title="Add Configuration Module",
                description="Create config management system",
                stage=ProjectStage.STRUCTURE,
                depends_on=["struct_1"],
                aider_prompt="Create a config.py module that loads settings from environment variables and a config file"
            ),
            
            # FEATURES stage
            ProjectTask(
                id="feat_1",
                title="Implement Core Features",
                description="Build main feature set",
                stage=ProjectStage.FEATURES,
                depends_on=["struct_2"],
                aider_prompt="Add 3-5 core functions that implement the main business logic"
            ),
            
            # TESTING stage
            ProjectTask(
                id="test_1",
                title="Add Unit Tests",
                description="Write tests for core functions",
                stage=ProjectStage.TESTING,
                depends_on=["feat_1"],
                aider_prompt="Create tests/ folder with pytest tests covering all main functions with >80% coverage"
            ),
            
            ProjectTask(
                id="test_2",
                title="Add Integration Tests",
                description="Test component interactions",
                stage=ProjectStage.TESTING,
                depends_on=["test_1"],
                aider_prompt="Create integration tests that test multiple components working together"
            ),
            
            # SECURITY stage
            ProjectTask(
                id="sec_1",
                title="Security Audit",
                description="Review code for security issues",
                stage=ProjectStage.SECURITY,
                depends_on=["test_2"],
                aider_prompt="Audit the code for security vulnerabilities: SQL injection, hardcoded secrets, input validation, error handling"
            ),
            
            # OPTIMIZATION stage
            ProjectTask(
                id="opt_1",
                title="Performance Optimization",
                description="Optimize critical paths",
                stage=ProjectStage.OPTIMIZATION,
                depends_on=["sec_1"],
                aider_prompt="Profile the code and optimize the 3 slowest functions"
            ),
            
            # DOCUMENTATION stage
            ProjectTask(
                id="doc_1",
                title="Write Documentation",
                description="Document code and APIs",
                stage=ProjectStage.DOCUMENTATION,
                depends_on=["opt_1"],
                aider_prompt="Create comprehensive docstrings using NumPy style for all public functions and classes"
            ),
            
            ProjectTask(
                id="doc_2",
                title="Create Usage Guide",
                description="Write user-facing documentation",
                stage=ProjectStage.DOCUMENTATION,
                depends_on=["doc_1"],
                aider_prompt="Create USAGE.md with examples of how to use the main features"
            ),
            
            # DEPLOYMENT stage
            ProjectTask(
                id="deploy_1",
                title="Prepare for Deployment",
                description="Add Docker and CI/CD",
                stage=ProjectStage.DEPLOYMENT,
                depends_on=["doc_2"],
                aider_prompt="Create Dockerfile and GitHub Actions workflow for CI/CD testing and deployment"
            ),
        ]
        
        for task in workflow:
            self.tasks[task.id] = task
        
        self.save_state()
    
    def get_dependencies_satisfied(self, task: ProjectTask) -> bool:
        """Check if all dependencies for a task are completed."""
        for dep_id in task.depends_on:
            if dep_id not in self.tasks:
                return False
            if self.tasks[dep_id].status != TaskStatus.COMPLETED:
                return False
        return True
    
    def get_next_task(self) -> Optional[ProjectTask]:
        """Find the next logical task to execute."""
        # Find pending tasks with satisfied dependencies
        candidates = [
            task for task in self.tasks.values()
            if task.status == TaskStatus.PENDING and self.get_dependencies_satisfied(task)
        ]
        
        if not candidates:
            return None
        
        # Prefer tasks in earlier stages
        candidates.sort(key=lambda t: list(ProjectStage).index(t.stage))
        
        return candidates[0]
    
    def get_next_tasks(self, count: int = 5) -> List[ProjectTask]:
        """Get next N tasks that could be executed."""
        tasks = []
        temp_state = {k: v.status for k, v in self.tasks.items()}
        
        for _ in range(count):
            # Find next task based on current temp state
            candidates = [
                task for task in self.tasks.values()
                if temp_state[task.id] == TaskStatus.PENDING and 
                all(temp_state[dep_id] == TaskStatus.COMPLETED for dep_id in task.depends_on)
            ]
            
            if not candidates:
                break
            
            candidates.sort(key=lambda t: list(ProjectStage).index(t.stage))
            task = candidates[0]
            tasks.append(task)
            temp_state[task.id] = TaskStatus.COMPLETED
        
        return tasks
    
    def get_project_status(self) -> Dict:
        """Get comprehensive project status."""
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.COMPLETED)
        pending = sum(1 for t in self.tasks.values() if t.status == TaskStatus.PENDING)
        in_progress = sum(1 for t in self.tasks.values() if t.status == TaskStatus.IN_PROGRESS)
        failed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.FAILED)
        
        return {
            'stage': self.current_stage.value,
            'total_tasks': total,
            'completed': completed,
            'pending': pending,
            'in_progress': in_progress,
            'failed': failed,
            'progress_percent': (completed / total * 100) if total > 0 else 0,
            'total_cost': sum(t.cost for t in self.tasks.values()),
        }
    
    def print_status(self):
        """Print comprehensive project status."""
        status = self.get_project_status()
        
        print("\n" + "="*70)
        print("📊 PROJECT WORKFLOW STATUS")
        print("="*70)
        print(f"Stage:     {status['stage'].upper()}")
        print(f"Progress:  {status['completed']}/{status['total_tasks']} completed ({status['progress_percent']:.0f}%)")
        print(f"Status:    {status['in_progress']} in progress, {status['pending']} pending")
        if status['failed'] > 0:
            print(f"Failed:    {status['failed']} ⚠️")
        print(f"Total Cost: ${status['total_cost']:.4f}")
        print("="*70)
        
        # Show next task
        next_task = self.get_next_task()
        if next_task:
            print(f"\n🎯 NEXT STEP: {next_task.title}")
            print(f"   Description: {next_task.description}")
            print(f"   Stage: {next_task.stage.value}")
            if next_task.files_to_modify:
                print(f"   Files: {', '.join(next_task.files_to_modify)}")
        else:
            print("\n✅ All tasks completed!")
        
        print()
    
    def print_plan(self):
        """Print full workflow plan."""
        print("\n" + "="*70)
        print("📋 PROJECT WORKFLOW PLAN")
        print("="*70)
        
        # Group by stage
        by_stage = {}
        for task in self.tasks.values():
            if task.stage not in by_stage:
                by_stage[task.stage] = []
            by_stage[task.stage].append(task)
        
        for stage in ProjectStage:
            if stage not in by_stage:
                continue
            
            print(f"\n▶ {stage.value.upper()}")
            for task in by_stage[stage]:
                status_icon = {
                    TaskStatus.COMPLETED: "✅",
                    TaskStatus.IN_PROGRESS: "🔄",
                    TaskStatus.PENDING: "⏳",
                    TaskStatus.FAILED: "❌",
                    TaskStatus.BLOCKED: "🚫",
                }[task.status]
                
                print(f"  {status_icon} {task.title}")
                print(f"     {task.description}")
                if task.depends_on:
                    deps = ", ".join(task.depends_on)
                    print(f"     Depends on: {deps}")
        
        print("\n" + "="*70 + "\n")
    
    def execute_task(self, task: ProjectTask) -> bool:
        """Execute a single task using Aider."""
        print(f"\n{'='*70}")
        print(f"▶ EXECUTING: {task.title}")
        print(f"{'='*70}")
        print(f"Description: {task.description}")
        print(f"Files: {', '.join(task.files_to_modify)}")
        
        # Launch Aider
        cmd = ["aider-cost"] + task.files_to_modify
        
        print(f"\n🚀 Launching Aider with prompt:\n  \"{task.aider_prompt}\"\n")
        
        # Note: In real scenario, we'd interact with Aider here
        # For now, just mark as in progress and completed
        task.status = TaskStatus.IN_PROGRESS
        self.save_state()
        
        print(f"Running: {' '.join(cmd)}")
        print("\n[Aider would open here for manual interaction]")
        print("Press Enter to mark as complete...")
        input()
        
        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.now().isoformat()
        task.cost = 0.015  # Placeholder
        self.current_stage = task.stage
        self.save_state()
        
        print(f"✅ Task completed: {task.title}")
        return True
    
    def execute_next(self) -> bool:
        """Execute the next logical task."""
        task = self.get_next_task()
        if not task:
            print("✅ No more tasks to execute!")
            return False
        
        return self.execute_task(task)
    
    def auto_execute(self, count: int = 5):
        """Automatically execute next N tasks."""
        tasks_to_run = self.get_next_tasks(count)
        
        if not tasks_to_run:
            print("✅ No more tasks to execute!")
            return
        
        print(f"\n📋 Auto-executing {len(tasks_to_run)} tasks...\n")
        
        for i, task in enumerate(tasks_to_run, 1):
            print(f"\n[{i}/{len(tasks_to_run)}] {task.title}")
            response = input("Execute? (y/n/skip): ").strip().lower()
            
            if response == 'y':
                self.execute_task(task)
            elif response == 'skip':
                print(f"⏭️  Skipped: {task.title}")
                continue
            else:
                print("Stopping auto-execute")
                break
        
        print("\n✅ Auto-execute session complete!")
        self.print_status()


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    command = sys.argv[1]
    orchestrator = ProjectOrchestrator()
    
    if command == "status":
        orchestrator.print_status()
    
    elif command == "next":
        orchestrator.execute_next()
    
    elif command == "plan":
        orchestrator.print_plan()
    
    elif command == "auto":
        count = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        orchestrator.auto_execute(count)
    
    elif command == "reset":
        if input("⚠️  Reset all progress? (y/n): ").lower() == 'y':
            orchestrator.state_file.unlink(missing_ok=True)
            print("✅ State reset")
    
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
