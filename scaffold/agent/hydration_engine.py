"""
HydrationEngine: Full hydration pipeline.
Orchestrates: dispatcher → extract symbols → hydrate → cache → soul update.
"""

from pathlib import Path
from dataclasses import dataclass
from datetime import datetime

try:
    from .dispatcher import Dispatcher
    from .symbol_extractor import SymbolExtractor
    from .prompt_hydrator import PromptHydrator
    from .prompt_cache import PromptCache
    from .soul_xml import SoulXML, Learning
except ImportError:
    from dispatcher import Dispatcher
    from symbol_extractor import SymbolExtractor
    from prompt_hydrator import PromptHydrator
    from prompt_cache import PromptCache
    from soul_xml import SoulXML, Learning


@dataclass
class HydrationResult:
    """Result of full hydration pipeline."""

    task: str
    complexity_score: int
    model_tier: str
    symbols_extracted: int
    hydrated_prompt: str
    token_estimate: int
    cache_hit: bool
    cost_estimate: float
    cost_savings: float
    soul_updated: bool


class HydrationEngine:
    """Full hydration pipeline."""

    def __init__(self, repo_path: Path = None):
        self.repo_path = Path(repo_path) if repo_path else Path.cwd()
        self.dispatcher = Dispatcher(repo_path=self.repo_path)
        self.extractor = SymbolExtractor(repo_path=self.repo_path)
        self.hydrator = PromptHydrator(repo_path=self.repo_path)
        self.cache = PromptCache(cache_dir=self.repo_path / ".awos" / "cache")
        self.soul = SoulXML(soul_path=self.repo_path / ".awos" / "SOUL.xml")

    def think(self, task: str, ask_user: bool = True) -> HydrationResult:
        """
        Full hydration pipeline: dispatch → extract → hydrate → cache → soul.
        
        Args:
            task: The task description
            ask_user: If True, show manifest and ask for approval
        
        Returns:
            HydrationResult with full context
        """
        # Step 1: Dispatch (from Sprint 2)
        dispatch_decision = self.dispatcher.dispatch(
            task_description=task,
            ask_user=ask_user,
        )

        if not dispatch_decision.approved:
            # User denied, return empty result
            return HydrationResult(
                task=task,
                complexity_score=dispatch_decision.complexity_score,
                model_tier=dispatch_decision.model_config["model"],
                symbols_extracted=0,
                hydrated_prompt="[Task denied by user]",
                token_estimate=0,
                cache_hit=False,
                cost_estimate=0.0,
                cost_savings=0.0,
                soul_updated=False,
            )

        # Step 2: Extract symbols
        symbols = self.extractor.extract_symbols(task, limit=5)

        # Step 3: Hydrate prompt
        hydrated = self.hydrator.hydrate(
            task=task,
            symbols=symbols,
            model_config=dispatch_decision.model_config,
        )

        # Step 4: Check cache
        cache_hit = self.cache.check_cache(hydrated, dispatch_decision.model_config)

        # Calculate final cost
        final_cost = dispatch_decision.cost_estimate
        if cache_hit.hit:
            final_cost = cache_hit.cost_savings

        # Step 5: Update SOUL
        self.soul.init_soul()
        self.soul.add_cost_record(
            task=task,
            model=dispatch_decision.model_config["model"],
            cost=final_cost,
            tokens=hydrated.token_estimate,
        )

        # Return full result
        return HydrationResult(
            task=task,
            complexity_score=dispatch_decision.complexity_score,
            model_tier=dispatch_decision.model_config["model"],
            symbols_extracted=len(symbols),
            hydrated_prompt=hydrated.full_prompt,
            token_estimate=hydrated.token_estimate,
            cache_hit=cache_hit.hit,
            cost_estimate=dispatch_decision.cost_estimate,
            cost_savings=cache_hit.cost_savings,
            soul_updated=True,
        )


# Quick test
if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Initialize .awos directory
        awos_dir = tmpdir / ".awos"
        awos_dir.mkdir()

        # Create minimal STRUCT.xml
        struct_xml = awos_dir / "STRUCT.xml"
        struct_xml.write_text("""<?xml version="1.0"?>
<struct>
  <symbol name="authenticate" type="function" file="src/auth.py" line="10" size="20">
    <snippet>def authenticate(): ...</snippet>
  </symbol>
</struct>
""")

        engine = HydrationEngine(repo_path=tmpdir)

        # Test hydration (no user approval in test)
        result = engine.think("Fix authentication", ask_user=False)

        print("=" * 60)
        print("HYDRATION RESULT")
        print("=" * 60)
        print(f"Task: {result.task}")
        print(f"Complexity: {result.complexity_score}/10")
        print(f"Model: {result.model_tier}")
        print(f"Symbols: {result.symbols_extracted}")
        print(f"Token estimate: {result.token_estimate}")
        print(f"Cache hit: {result.cache_hit}")
        print(f"Cost: ${result.cost_estimate:.6f}")
        print(f"Savings: ${result.cost_savings:.6f}")
