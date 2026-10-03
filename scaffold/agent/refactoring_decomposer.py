"""
Intelligent Refactoring Task Decomposer

Decomposes complex refactoring tasks into atomic single-edit sub-tasks.

Intelligence: Pattern recognition for common refactoring operations
Deterministic: Same pattern always decomposes the same way
"""

import re
from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class AtomicTask:
    """
    A single atomic edit task.
    
    Atomic = can be done with ONE SEARCH/REPLACE block.
    """
    action: str
    file: str
    complexity: str = "low"
    depends_on: Optional[List[int]] = None  # Task IDs this depends on


class RefactoringDecomposer:
    """
    Decompose complex refactoring tasks into atomic sub-tasks.
    
    Example:
        Input: "Add import X, instantiate X, replace calls to Y with X"
        Output: 
          - Task 1: "Add import statement for X"
          - Task 2: "Instantiate X in __init__" (depends on 1)
          - Task 3: "Replace first call to Y with X" (depends on 2)
          - Task 4: "Replace remaining calls to Y with X" (depends on 3)
    """
    
    def __init__(self):
        # Refactoring patterns (compiled for performance)
        self.patterns = [
            # Pattern 1: Import + Use
            {
                "match": re.compile(r"(add|import).*import.*and.*(instantiate|use|replace)", re.IGNORECASE),
                "decompose": self._decompose_import_and_use,
            },
            # Pattern 2: Replace all occurrences
            {
                "match": re.compile(r"replace.*(all|every|multiple)", re.IGNORECASE),
                "decompose": self._decompose_replace_all,
            },
            # Pattern 3: Extract + Integrate
            {
                "match": re.compile(r"extract.*and.*(update|integrate|use)", re.IGNORECASE),
                "decompose": self._decompose_extract_integrate,
            },
        ]
    
    def should_decompose(self, task: Dict) -> bool:
        """Check if task should be decomposed into atomic sub-tasks."""
        action = task.get("action", "")
        
        # Check for multiple operations indicated by "and"
        if " and " in action.lower():
            return True
        
        # Check for refactoring patterns
        for pattern_def in self.patterns:
            if pattern_def["match"].search(action):
                return True
        
        return False
    
    def decompose(self, task: Dict) -> List[AtomicTask]:
        """
        Decompose a complex task into atomic sub-tasks.
        
        Returns:
            List of atomic tasks, or [original task] if no decomposition needed.
        """
        action = task.get("action", "")
        file_path = task.get("file", "")
        
        # Try each pattern
        for pattern_def in self.patterns:
            if pattern_def["match"].search(action):
                return pattern_def["decompose"](task)
        
        # No pattern matched - return original task wrapped
        return [AtomicTask(action=action, file=file_path, complexity=task.get("complexity", "medium"))]
    
    def _decompose_import_and_use(self, task: Dict) -> List[AtomicTask]:
        """
        Decompose "Add import X and instantiate/use X" pattern.
        
        Example:
            "Add import ModelRouter from model_router and instantiate it"
        Becomes:
            1. Add import statement for ModelRouter
            2. Instantiate ModelRouter in appropriate location
        """
        action = task.get("action", "")
        file_path = task.get("file", "")
        
        # Extract class name (first CamelCase word)
        class_match = re.search(r"\b([A-Z][a-zA-Z0-9_]+)\b", action)
        class_name = class_match.group(1) if class_match else "NewClass"
        
        # Extract module name if present
        module_match = re.search(r"from\s+([a-z_][a-z0-9_]*)", action, re.IGNORECASE)
        module_name = module_match.group(1) if module_match else f"{class_name.lower()}_module"
        
        sub_tasks = []
        
        # Sub-task 1: Add import
        sub_tasks.append(AtomicTask(
            action=f"Add import statement: 'from {module_name} import {class_name}' at the top of the file after existing imports",
            file=file_path,
            complexity="low",
        ))
        
        # Sub-task 2: Instantiate/use (depends on import)
        if "instantiat" in action.lower():
            sub_tasks.append(AtomicTask(
                action=f"Instantiate {class_name}: create an instance '{class_name.lower()} = {class_name}()' where needed",
                file=file_path,
                complexity="low",
                depends_on=[1],
            ))
        
        # Sub-task 3: Replace calls if mentioned
        if "replace" in action.lower():
            sub_tasks.append(AtomicTask(
                action=f"Replace original logic with calls to {class_name.lower()} instance methods",
                file=file_path,
                complexity="medium",
                depends_on=[2],
            ))
        
        return sub_tasks
    
    def _decompose_replace_all(self, task: Dict) -> List[AtomicTask]:
        """
        Decompose "Replace all X" into incremental replacements.
        
        Replaces are done incrementally to avoid large diffs:
            1. Replace first occurrence
            2. Replace remaining occurrences
        """
        action = task.get("action", "")
        file_path = task.get("file", "")
        
        # Extract what's being replaced
        replace_match = re.search(r"replace\s+(?:all\s+)?(?:the\s+)?([a-zA-Z_][a-zA-Z0-9_\s]*(?:calls?|usage|logic))", action, re.IGNORECASE)
        target = replace_match.group(1) if replace_match else "target"
        
        sub_tasks = []
        
        # Sub-task 1: Replace first
        sub_tasks.append(AtomicTask(
            action=f"Replace the first {target.strip()} in {file_path}",
            file=file_path,
            complexity="low",
        ))
        
        # Sub-task 2: Replace remaining
        sub_tasks.append(AtomicTask(
            action=f"Replace remaining {target.strip()} instances in {file_path}",
            file=file_path,
            complexity="medium",
            depends_on=[1],
        ))
        
        return sub_tasks
    
    def _decompose_extract_integrate(self, task: Dict) -> List[AtomicTask]:
        """
        Decompose "Extract X and integrate into Y" pattern.
        
        This has already been partially done (extraction to new file),
        so focus on integration steps.
        """
        action = task.get("action", "")
        file_path = task.get("file", "")
        
        # This pattern assumes extraction is done, focus on integration
        sub_tasks = []
        
        # Import the extracted module
        sub_tasks.append(AtomicTask(
            action=f"Add import statement for the extracted module",
            file=file_path,
            complexity="low",
        ))
        
        # Remove old code
        sub_tasks.append(AtomicTask(
            action=f"Remove the original (now extracted) logic from {file_path}",
            file=file_path,
            complexity="medium",
            depends_on=[1],
        ))
        
        # Add calls to new module
        sub_tasks.append(AtomicTask(
            action=f"Add calls to the new extracted module where the old logic was",
            file=file_path,
            complexity="medium",
            depends_on=[2],
        ))
        
        return sub_tasks


# Quick test
if __name__ == "__main__":
    decomposer = RefactoringDecomposer()
    
    test_tasks = [
        {
            "action": "Add import statement 'from model_router import ModelRouter' and instantiate ModelRouter and replace calls",
            "file": "orchestrator.py",
            "complexity": "medium",
        },
        {
            "action": "Replace all calls to old routing logic with ModelRouter methods",
            "file": "orchestrator.py",
            "complexity": "medium",
        },
    ]
    
    print("Refactoring Task Decomposer Test")
    print("=" * 70)
    
    for i, task in enumerate(test_tasks, 1):
        print(f"\nTask {i}: {task['action'][:60]}...")
        
        if decomposer.should_decompose(task):
            print("  → Should decompose: YES")
            sub_tasks = decomposer.decompose(task)
            print(f"  → Generated {len(sub_tasks)} atomic sub-tasks:")
            for j, sub in enumerate(sub_tasks, 1):
                deps = f" (depends on {sub.depends_on})" if sub.depends_on else ""
                print(f"     {j}. [{sub.complexity}] {sub.action[:55]}...{deps}")
        else:
            print("  → Should decompose: NO (already atomic)")
