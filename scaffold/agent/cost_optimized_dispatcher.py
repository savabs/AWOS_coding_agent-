#!/usr/bin/env python3
"""
AWOS Cost-Optimized Dispatcher — Orchestrates all 6 cost-optimization modules.

Integrates:
  Phase 1: ProjectSoul (rules/architecture/patterns cached)
  Phase 2: ContextManager (budget tracking + auto-dehydration)
  Phase 3: TaskState (flat state persistence)
  Phase 4: StructGenerator (symbol injection)
  Phase 5: SearchReplaceParser (output validation)
  Phase 6: PreFlightManifest (cost approval gate)

Usage:
    dispatcher = CostOptimizedDispatcher()
    response = dispatcher.dispatch(
        task_prompt="Fix bug in dispatcher.py",
        context_files=["scaffold/agent/dispatcher.py"],
        output_tokens_estimate=600
    )
"""

import os
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass

from project_soul import ProjectSoul
from context_manager import ContextManager
from task_state import TaskState
from struct_generator import StructGenerator
from search_replace_parser import SearchReplaceParser, SearchReplaceBlock
from preflight_manifest_v2 import CostEstimator, PreFlightManifest
from coding_model_router import CodingModelRouter


@dataclass
class DispatcherConfig:
    """Configuration for cost-optimized dispatcher."""
    
    budget_dollars: float = 15.0
    max_context_tokens: int = 10000
    max_output_tokens: int = 2000
    project_root: Path = None
    struct_pattern: str = "scaffold/agent/*.py"
    struct_cache_hours: int = 1
    
    def __post_init__(self):
        if self.project_root is None:
            self.project_root = Path.cwd()


class CostOptimizedDispatcher:
    """
    Orchestrates all 6 cost-optimization modules into a unified workflow.
    
    Workflow:
    1. Initialize soul (rules/arch/patterns cached)
    2. Load context with budget tracking
    3. Inject symbol index (prevent hallucination)
    4. Build system prompt with cache_control
    5. Show pre-flight cost estimate
    6. Send to API (if approved)
    7. Validate output (SEARCH/REPLACE blocks)
    8. Apply changes to files
    9. Update task state
    """
    
    def __init__(self, config: Optional[DispatcherConfig] = None):
        """
        Initialize all 6 modules.
        
        Parameters
        ----------
        config : DispatcherConfig, optional
            Configuration. Defaults to sensible values.
        """
        self.config = config or DispatcherConfig()
        self.awos_dir = self.config.project_root / ".awos"
        self.awos_dir.mkdir(exist_ok=True)
        
        # Phase 1: ProjectSoul
        self.soul = ProjectSoul(str(self.awos_dir / "soul"))
        self.soul_prompt = self.soul.get_all_soul()
        
        # Phase 2: ContextManager
        self.context = ContextManager(
            max_tokens=self.config.max_context_tokens,
            budget_dollars=self.config.budget_dollars
        )
        
        # Phase 3: TaskState (initialized per-task)
        self.current_task: Optional[TaskState] = None
        
        # Phase 4: StructGenerator
        self.struct_gen = StructGenerator(self.config.project_root)
        self.symbols_prompt = ""
        self._load_or_generate_symbols()
        
        # Phase 5: SearchReplaceParser
        self.parser = SearchReplaceParser()
        
        # Phase 6: PreFlightManifest
        self.cost_estimator = CostEstimator()
        
        # Routing
        self.router = CodingModelRouter()
    
    def _load_or_generate_symbols(self) -> None:
        """Load STRUCT.xml or generate if missing."""
        struct_file = self.awos_dir / "STRUCT.xml"
        
        if struct_file.exists():
            self.struct_gen = StructGenerator(
                self.config.project_root,
                struct_file=struct_file
            )
        else:
            self.struct_gen.scan_project(self.config.struct_pattern)
            self.struct_gen.generate_struct_xml()
        
        self.symbols_prompt = self.struct_gen.get_symbols_as_prompt()
    
    def dispatch(
        self,
        task_id: str,
        task_prompt: str,
        context_files: Optional[List[str]] = None,
        output_tokens_estimate: int = 600,
        task_type: str = "code_review",
        require_approval: bool = True,
    ) -> Dict:
        """
        Full dispatch workflow: estimate → approve → execute → validate → update.
        
        Parameters
        ----------
        task_id : str
            Unique task identifier (for state persistence)
        task_prompt : str
            The user's request or instruction
        context_files : list of str, optional
            Files to hydrate into context
        output_tokens_estimate : int
            Expected output tokens for cost estimation
        task_type : str
            Task type for model routing (see CodingModelRouter)
        require_approval : bool
            Whether to show pre-flight manifest and wait for approval
            
        Returns
        -------
        dict
            {
                'success': bool,
                'task_id': str,
                'model': str,
                'tokens_used': int,
                'cost': float,
                'changes_applied': int,
                'files_modified': list,
                'errors': list
            }
        """
        result = {
            'success': False,
            'task_id': task_id,
            'model': None,
            'tokens_used': 0,
            'cost': 0.0,
            'changes_applied': 0,
            'files_modified': [],
            'errors': []
        }
        
        try:
            # Initialize task state (Phase 3)
            self.current_task = TaskState(task_id, str(self.awos_dir / "state"))
            self.current_task.update(
                status="in_progress",
                step=1,
                current_focus=task_prompt[:50]
            )
            
            # Build system prompt with cache control (Phase 1 + 4)
            system_prompt = self._build_system_prompt(task_type)
            
            # Load context (Phase 2)
            if context_files:
                for file_path in context_files:
                    self._hydrate_file(file_path)
            
            context_prompt = self.context.get_context_prompt()
            
            # Estimate cost (Phase 6)
            estimate = self._estimate_cost(
                system_prompt,
                context_prompt,
                task_prompt,
                output_tokens_estimate
            )
            
            # Check approval (Phase 6)
            if require_approval:
                monthly_spent = self._get_monthly_spent()
                manifest = PreFlightManifest(
                    estimate=estimate,
                    budget=self.config.budget_dollars,
                    monthly_spent=monthly_spent
                )
                
                print("\n" + "="*75)
                approved = manifest.ask_approval()
                print("="*75 + "\n")
                
                if not approved:
                    self.current_task.update(status="cancelled")
                    result['errors'].append("User cancelled dispatch")
                    return result
            
            # Route to model
            model_config = self.router.route(task_type)
            result['model'] = model_config.model
            
            print(f"🚀 Dispatching to {model_config.model}...")
            print(f"   Task: {task_id}")
            print(f"   Type: {task_type}")
            
            # Simulate API call (real implementation would use Anthropic client)
            # For now, demonstrate the flow
            response_text = self._simulate_api_call(
                system_prompt,
                context_prompt,
                task_prompt,
                model_config.model
            )
            
            # Validate output (Phase 5)
            blocks = self.parser.extract_blocks(response_text)
            result['changes_applied'] = len(blocks)
            
            if blocks:
                print(f"✅ Extracted {len(blocks)} SEARCH/REPLACE blocks")
                
                for i, block in enumerate(blocks, 1):
                    try:
                        file_path = Path(block.file_path) if block.file_path else None
                        if not file_path:
                            print(f"   [{i}] ⚠️  Skipped (no file specified in block)")
                            continue
                        if not file_path.exists():
                            print(f"   [{i}] ⚠️  File not found: {file_path}")
                            continue
                        self.parser.apply_block(block, file_path)
                        result['files_modified'].append(str(file_path))
                        self.current_task.add_file_modified(str(file_path), "modified")
                        print(f"   [{i}] Applied to {file_path}")
                    except Exception as e:
                        result['errors'].append(f"Block {i}: {str(e)}")
                        print(f"   ❌ Block {i} failed: {str(e)}")
            
            # Update task state (Phase 3)
            result['tokens_used'] = estimate.total_input_tokens + output_tokens_estimate
            result['cost'] = estimate.total_cost
            
            self.current_task.update_tracking(
                tokens_used=result['tokens_used'],
                cost=result['cost'],
                estimated_total=self.config.budget_dollars * 0.5  # Placeholder
            )
            
            self.current_task.add_decision(
                decision=f"Routed to {model_config.model}",
                rationale=f"Task type {task_type} → {model_config.model}"
            )
            
            result['success'] = len(result['errors']) == 0
            self.current_task.update(status="completed" if result['success'] else "failed")
            
            print(f"\n✅ Task {task_id} completed")
            print(f"   Files modified: {len(result['files_modified'])}")
            print(f"   Cost: ${result['cost']:.6f}")
            print(f"   Tokens: {result['tokens_used']}")
            
        except Exception as e:
            result['errors'].append(str(e))
            if self.current_task:
                self.current_task.update(status="error")
            print(f"❌ Dispatch failed: {str(e)}")
        
        return result
    
    def _build_system_prompt(self, task_type: str) -> str:
        """Build system prompt with soul + symbols (cached)."""
        model_config = self.router.route(task_type)
        model_instructions = self.router.get_system_prompt(task_type) or ""
        
        return f"""{self.soul_prompt}

SYMBOL INDEX:
{self.symbols_prompt}

TASK TYPE: {task_type}
MODEL: {model_config.model}

{model_instructions}

OUTPUT FORMAT:
Only output SEARCH/REPLACE blocks. Do NOT output full files.
Each block must have exact match text in SEARCH.

```
SEARCH:
[exact text to find]

REPLACE:
[exact replacement]
```

Rules:
- Match whitespace exactly
- Include surrounding context for unique matching
- Multiple blocks are OK
- Invalid blocks will be rejected
"""
    
    def _hydrate_file(self, file_path: str) -> None:
        """Load a file into context with budget tracking."""
        try:
            path = Path(file_path)
            if not path.exists():
                raise FileNotFoundError(f"{file_path} not found")
            
            content = path.read_text()
            tokens = len(content.split()) * 1.3  # Rough estimate
            
            priority = 10 if "dispatcher" in file_path else 5
            self.context.add_block(
                block_id=file_path,
                name=path.name,
                content=content,
                priority=priority
            )
            
            # Auto-dehydrate if needed
            self.context.emergency_dehydrate(threshold_percent=80)
            
        except Exception as e:
            print(f"⚠️  Failed to hydrate {file_path}: {str(e)}")
    
    def _estimate_cost(
        self,
        system_prompt: str,
        context_prompt: str,
        task_prompt: str,
        output_tokens_estimate: int
    ) -> object:
        """Estimate cost of request."""
        return self.cost_estimator.estimate(
            task_prompt=task_prompt,
            context=context_prompt,
            system_prompt=system_prompt,
            output_tokens_estimate=output_tokens_estimate
        )
    
    def _get_monthly_spent(self) -> float:
        """Get monthly spending (placeholder)."""
        # In real implementation, query inference_engine_monitor
        return 2.50
    
    def _simulate_api_call(
        self,
        system_prompt: str,
        context_prompt: str,
        task_prompt: str,
        model_name: str
    ) -> str:
        """Simulate API call for demo (real implementation uses Anthropic client)."""
        print(f"   System prompt: {len(system_prompt)} chars")
        print(f"   Context: {len(context_prompt)} chars")
        print(f"   Task: {len(task_prompt)} chars")
        
        # Return mock response with SEARCH/REPLACE block
        return f"""
Looking at the code, I can help fix the dispatcher.

```
SEARCH:
def dispatch(self, task_prompt: str):
    return "mock"

REPLACE:
def dispatch(self, task_prompt: str):
    # Fixed version
    return self._run_task(task_prompt)
```

The fix addresses the issue by properly routing to _run_task().
"""
    
    def get_budget_status(self) -> Dict:
        """Get current budget status."""
        return self.context.get_budget_status()
    
    def print_status(self) -> None:
        """Print dispatcher status."""
        status = self.get_budget_status()
        print("\n" + "="*75)
        print("DISPATCHER STATUS")
        print("="*75)
        print(f"Budget: ${self.config.budget_dollars:.2f}")
        print(f"Used: {status['token_percent']:.1f}% ({status['tokens_used']} tokens)")
        print(f"Remaining: ${status['remaining_budget']:.2f}")
        print(f"Current Task: {self.current_task.load()['description'] if self.current_task else 'None'}")
        print("="*75 + "\n")


def main():
    """Example usage of cost-optimized dispatcher."""
    print("\n" + "="*75)
    print("COST-OPTIMIZED DISPATCHER — FULL INTEGRATION")
    print("="*75 + "\n")
    
    # Initialize dispatcher
    config = DispatcherConfig(
        budget_dollars=15.0,
        max_context_tokens=10000,
        project_root=Path.cwd()
    )
    
    dispatcher = CostOptimizedDispatcher(config)
    print("✅ Initialized dispatcher (all 6 modules)")
    dispatcher.print_status()
    
    # Example dispatch
    result = dispatcher.dispatch(
        task_id="fix_dispatcher_v1",
        task_prompt="Fix the dispatch method to handle errors properly",
        context_files=["scaffold/agent/dispatcher.py"],
        output_tokens_estimate=600,
        task_type="code_review",
        require_approval=False  # Skip approval for demo
    )
    
    # Print result
    print("\n" + "="*75)
    print("DISPATCH RESULT")
    print("="*75)
    print(f"Success: {result['success']}")
    print(f"Task ID: {result['task_id']}")
    print(f"Model: {result['model']}")
    print(f"Cost: ${result['cost']:.6f}")
    print(f"Tokens: {result['tokens_used']}")
    print(f"Files modified: {result['files_modified']}")
    if result['errors']:
        print(f"Errors: {result['errors']}")
    print("="*75 + "\n")


if __name__ == "__main__":
    main()
