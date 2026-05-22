"""
CodingModelRouter: Route tasks to coding-specific models with caching.

PHILOSOPHY:
- Models: DeepSeek Coder (specialized), Claude Sonnet (with cache), not general models
- Caching: Leverage 1hr Claude caching + local prompt cache (90% savings)
- Tasks: Code review, refactoring, testing, architecture, planning
- Metrics: Track cache hits, cost savings, turnaround time

Why coding-specific?
- DeepSeek Coder: Fine-tuned for code (better than general models for programming)
- Claude Sonnet (cached): Output tokens 90% cheaper with prompt caching
- Specialized models work better on code tasks than general intelligence models
"""

from dataclasses import dataclass
from typing import Literal
from enum import Enum


class CodingTask(Enum):
    """Types of coding tasks we handle."""
    CODE_REVIEW = "code_review"
    BUG_ANALYSIS = "bug_analysis"
    REFACTORING = "refactoring"
    TEST_GENERATION = "test_generation"
    ARCHITECTURE_PLANNING = "architecture_planning"
    ENGINEERING_PLANNING = "engineering_planning"
    DOCUMENTATION = "documentation"
    OPTIMIZATION = "optimization"


@dataclass
class CodingModelConfig:
    """Configuration for coding-specific model."""
    
    provider: str
    model: str
    task_types: list[str]  # Which tasks this model is best for
    max_tokens: int
    temperature: float
    use_cache: bool  # Enable prompt caching?
    cache_ttl_hours: int  # Cache time-to-live (Claude supports 1 hour minimum)
    input_cost_per_mtok: float
    output_cost_per_mtok: float
    input_cost_with_cache_per_mtok: float  # Usually same as input
    output_cost_with_cache_per_mtok: float  # 90% savings on cached tokens
    reasoning_effort: str  # "low", "medium", "high" for extended thinking


class CodingModelRouter:
    """
    Route coding tasks to specialized models with caching.
    
    Model Selection:
    - DeepSeek Coder: Best for general code tasks (specialized, cheaper)
    - DeepSeek-R1: Complex architecture/engineering planning (reasoning)
    - Claude Sonnet (cached): High-quality reviews, when cache works
    
    Why NOT general models?
    - Opus: Too slow, too expensive for routine coding
    - Haiku: Insufficient for complex code tasks
    - GPT-4: Not specialized for code
    
    Caching Strategy:
    - System prompt + codebase structure cached (1 hour)
    - Project context reused across multiple requests
    - 90% cost savings on cached token sections
    """
    
    def __init__(self):
        """Initialize coding-specific model tiers."""
        
        self.models = {
            "deepseek_coder": CodingModelConfig(
                provider="deepseek",
                model="deepseek-chat",
                task_types=[
                    "code_review",
                    "bug_analysis",
                    "optimization",
                    "refactoring",
                    "test_generation",
                ],
                max_tokens=8000,
                temperature=0.3,
                use_cache=False,
                cache_ttl_hours=0,
                input_cost_per_mtok=0.14,
                output_cost_per_mtok=0.28,   # DeepSeek output is $0.28, not $0.14
                input_cost_with_cache_per_mtok=0.14,
                output_cost_with_cache_per_mtok=0.28,
                reasoning_effort="low",
            ),
            
            "deepseek_r1": CodingModelConfig(
                provider="deepseek",
                model="deepseek-reasoner",
                task_types=[
                    "architecture_planning",
                    "engineering_planning",
                    "optimization",
                ],
                max_tokens=16000,
                temperature=0.5,
                use_cache=False,
                cache_ttl_hours=0,
                input_cost_per_mtok=0.50,
                output_cost_per_mtok=2.00,  # Reasoning models cost more
                input_cost_with_cache_per_mtok=0.50,
                output_cost_with_cache_per_mtok=2.00,
                reasoning_effort="high",
            ),
            
            "claude_sonnet_cached": CodingModelConfig(
                provider="anthropic",
                model="claude-sonnet-4-6",
                task_types=[
                    "code_review",
                    "architecture_planning",
                    "documentation",
                    "refactoring",
                ],
                max_tokens=8000,
                temperature=0.3,
                use_cache=True,
                cache_ttl_hours=1,
                input_cost_per_mtok=3.00,          # Correct: $3/MTok input
                output_cost_per_mtok=15.00,         # Correct: $15/MTok output
                input_cost_with_cache_per_mtok=0.30,  # Cache READ = 10% of input price
                output_cost_with_cache_per_mtok=15.00, # Output is NEVER cached
                reasoning_effort="low",
            ),
        }
        
        # Task preferences (which model is best for each task)
        self.task_preferences = {
            CodingTask.CODE_REVIEW: "claude_sonnet_cached",  # High quality with cache
            CodingTask.BUG_ANALYSIS: "deepseek_coder",  # Fast, good enough
            CodingTask.REFACTORING: "claude_sonnet_cached",  # Needs precision
            CodingTask.TEST_GENERATION: "deepseek_coder",  # Fast generation
            CodingTask.ARCHITECTURE_PLANNING: "deepseek_r1",  # Needs reasoning
            CodingTask.ENGINEERING_PLANNING: "deepseek_r1",  # Complex planning
            CodingTask.DOCUMENTATION: "deepseek_coder",  # Adequate for docs
            CodingTask.OPTIMIZATION: "deepseek_r1",  # Needs deep analysis
        }
    
    def route(self, task_type: str, complexity: int = 5) -> CodingModelConfig:
        """
        Route to best coding model.
        
        Args:
            task_type: CodingTask enum or string
            complexity: 1-10 scale (used as tiebreaker)
        
        Returns:
            CodingModelConfig for this task
        """
        
        # Parse task type
        if isinstance(task_type, str):
            try:
                task = CodingTask[task_type.upper()]
            except KeyError:
                # Default to code review for unknown tasks
                task = CodingTask.CODE_REVIEW
        else:
            task = task_type
        
        # Get preferred model
        preferred_model_key = self.task_preferences.get(
            task,
            "deepseek_coder"  # Default fallback
        )
        
        # Override for very complex tasks: use reasoning model
        if complexity >= 8 and task in [
            CodingTask.ARCHITECTURE_PLANNING,
            CodingTask.OPTIMIZATION,
            CodingTask.ENGINEERING_PLANNING,
        ]:
            return self.models["deepseek_r1"]
        
        return self.models[preferred_model_key]
    
    def estimate_cost_with_cache(
        self,
        task_type: str,
        input_tokens: int,
        output_tokens: int,
        cache_hit: bool = False,
    ) -> dict:
        """
        Estimate cost considering caching.
        
        Returns:
            {
                "model": "...",
                "cost_without_cache": float,
                "cost_with_cache": float,
                "savings": float,
                "cache_effective": bool,
            }
        """
        
        model_config = self.route(task_type)
        
        # Cost without cache
        cost_no_cache = (
            (input_tokens / 1_000_000) * model_config.input_cost_per_mtok +
            (output_tokens / 1_000_000) * model_config.output_cost_per_mtok
        )
        
        # Cache saves on INPUT only (cache read price replaces normal input price).
        # Output tokens are always billed at full price — they are never cached.
        if cache_hit and model_config.use_cache:
            cost_with_cache = (
                (input_tokens / 1_000_000) * model_config.input_cost_with_cache_per_mtok +
                (output_tokens / 1_000_000) * model_config.output_cost_with_cache_per_mtok
            )
            savings = cost_no_cache - cost_with_cache
        else:
            cost_with_cache = cost_no_cache
            savings = 0.0
        
        return {
            "model": model_config.model,
            "provider": model_config.provider,
            "cost_without_cache": cost_no_cache,
            "cost_with_cache": cost_with_cache,
            "savings": savings,
            "cache_effective": cache_hit and model_config.use_cache,
        }
    
    def get_system_prompt(self, task_type: str) -> str:
        """Get optimized system prompt for coding task."""
        
        if isinstance(task_type, str):
            task_type = task_type.lower()
        
        base = """You are an expert software engineer specializing in coding tasks.
Focus on practical solutions, code quality, and maintainability.
Output code examples when relevant. Be concise but thorough."""
        
        prompts = {
            "code_review": f"""{base}
            
TASK: Code Review
- Identify bugs, performance issues, security risks
- Suggest improvements with specific examples
- Rate code quality 1-10 with reasoning
- Format: Issue → Impact → Suggestion → Example""",
            
            "bug_analysis": f"""{base}
            
TASK: Bug Analysis
- Reproduce the issue step-by-step
- Identify root cause with evidence
- Propose minimal fix with test case
- Explain why this fixes it""",
            
            "refactoring": f"""{base}
            
TASK: Refactoring
- Preserve functionality completely
- Improve readability and maintainability
- Reduce complexity or duplication
- Provide before/after with explanation""",
            
            "test_generation": f"""{base}
            
TASK: Test Generation
- Create comprehensive test cases
- Cover happy path, edge cases, errors
- Use clear, descriptive test names
- Format: unit tests (pytest, unittest, or equivalent)""",
            
            "architecture_planning": f"""{base}
            
TASK: Architecture Planning
- Design system components and interactions
- Consider scalability and maintainability
- Document trade-offs and rationale
- Include diagrams or pseudocode""",
            
            "engineering_planning": f"""{base}
            
TASK: Engineering Planning
- Break down work into milestones
- Identify risks and dependencies
- Estimate effort and timeline
- Define success criteria""",
            
            "documentation": f"""{base}
            
TASK: Documentation
- Clear, accurate technical documentation
- Include examples and use cases
- Structure: Overview → Details → Examples
- Use proper formatting (markdown/rst)""",
            
            "optimization": f"""{base}
            
TASK: Optimization
- Identify performance bottlenecks with evidence
- Provide optimized solution with benchmarks
- Consider time/space tradeoffs
- Include before/after metrics""",
        }
        
        return prompts.get(task_type, base)
    
    def get_models_for_task(self, task_type: str) -> list[str]:
        """Get all models that support this task."""
        
        models_for_task = []
        for model_key, config in self.models.items():
            if task_type.lower() in config.task_types:
                models_for_task.append(model_key)
        
        return models_for_task if models_for_task else ["deepseek_coder"]  # Fallback
    
    def print_routing_table(self):
        """Print human-readable routing table."""
        print("\n" + "=" * 80)
        print("CODING-SPECIFIC MODEL ROUTER")
        print("=" * 80)
        
        print("\nTASK → MODEL ROUTING:")
        print("-" * 80)
        for task, model_key in self.task_preferences.items():
            config = self.models[model_key]
            cache_str = "📦 cached" if config.use_cache else "no cache"
            print(f"  {task.value:25} → {config.model:25} ({cache_str})")
        
        print("\nMODEL CAPABILITIES:")
        print("-" * 80)
        for model_key, config in self.models.items():
            print(f"\n  {config.model}")
            print(f"    Provider: {config.provider}")
            print(f"    Tasks: {', '.join(config.task_types)}")
            print(f"    Caching: {'✓ Yes (1 hour)' if config.use_cache else '✗ No'}")
            print(f"    Cost: ${config.input_cost_per_mtok}/MTok input, "
                  f"${config.output_cost_per_mtok}/MTok output")
            if config.use_cache:
                print(f"    With cache: ${config.output_cost_with_cache_per_mtok}/MTok output "
                      f"(90% savings!)")
        
        print("\n" + "=" * 80)
        print("STRATEGY: Coding-specific models + prompt caching for cost reduction")
        print("=" * 80 + "\n")


# Quick test
if __name__ == "__main__":
    router = CodingModelRouter()
    router.print_routing_table()
    
    # Test routing
    print("\nROUTING EXAMPLES:")
    print("-" * 80)
    
    tasks = [
        "code_review",
        "bug_analysis",
        "architecture_planning",
        "test_generation",
    ]
    
    for task in tasks:
        config = router.route(task)
        cost_estimate = router.estimate_cost_with_cache(
            task,
            input_tokens=2000,
            output_tokens=800,
            cache_hit=True,
        )
        print(f"\n  Task: {task}")
        print(f"    Model: {config.model}")
        print(f"    Cost (no cache): ${cost_estimate['cost_without_cache']:.6f}")
        print(f"    Cost (cached): ${cost_estimate['cost_with_cache']:.6f}")
        print(f"    Savings: ${cost_estimate['savings']:.6f}")
