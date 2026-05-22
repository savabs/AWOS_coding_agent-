"""
Dispatcher: Orchestrate the full pipeline.
STRUCT.xml → complexity → routing → manifest → consent → decision.
"""

from pathlib import Path
from dataclasses import dataclass
from typing import Optional

try:
    from .complexity_scorer import ComplexityScorer
    from .model_router import ModelRouter
    from .preflight_manifest import PreflightManifest
    from .hitl_consent import HITLConsent
except ImportError:
    from complexity_scorer import ComplexityScorer
    from model_router import ModelRouter
    from preflight_manifest import PreflightManifest
    from hitl_consent import HITLConsent


@dataclass
class DispatchDecision:
    """Result of dispatch pipeline."""

    approved: bool
    model_config: dict
    manifest_path: Path
    complexity_score: int
    cost_estimate: float
    reason: str


class Dispatcher:
    """Main orchestrator: task → decision."""

    def __init__(self, repo_path: Path = None, awos_dir: Path = None):
        self.repo_path = Path(repo_path) if repo_path else Path.cwd()
        self.awos_dir = Path(awos_dir) if awos_dir else self.repo_path / ".awos"

        self.scorer = ComplexityScorer()
        self.router = ModelRouter()
        self.manifest_gen = PreflightManifest(self.awos_dir)
        self.consent = HITLConsent(self.awos_dir / "COST_LOG.xml")

    def dispatch(
        self,
        task_description: str,
        target_files: list[str] = None,
        ask_user: bool = True,
    ) -> DispatchDecision:
        """
        Full pipeline: score → route → manifest → consent.
        
        Args:
            task_description: What the agent should do
            target_files: Files involved (for scoring)
            ask_user: If True, show manifest and ask for approval (default)
        
        Returns:
            DispatchDecision with approval status and routing info
        """
        if target_files is None:
            target_files = []

        # Step 1: Calculate complexity
        if target_files:
            # Average complexity across files
            scores = [self.scorer.score(self.repo_path / f) for f in target_files]
            complexity_score = int(sum(scores) / len(scores))
        else:
            # Default to moderate complexity
            complexity_score = 5

        # Step 2: Route to model
        config = self.router.route(complexity_score)
        tier_name = self.router.get_tier_name(complexity_score)

        # Step 3: Generate preflight manifest
        manifest_path = self.manifest_gen.generate(
            task_description=task_description,
            complexity_score=complexity_score,
            repo_path=str(self.repo_path),
            files_involved=target_files,
        )

        # Step 4: Read manifest for display
        manifest_data = self.manifest_gen.read_manifest(manifest_path)

        # Step 5: Request HITL approval (default: deny)
        if ask_user:
            self.consent.display_manifest(manifest_data)
            approved = self.consent.request_approval()
        else:
            # Auto-deny if not asking (fail closed)
            approved = False

        # Step 6: Log decision
        self.consent.log_decision(
            task=task_description,
            model=config.model,
            complexity=complexity_score,
            cost_estimate=manifest_data["cost_estimate"],
            approved=approved,
        )

        # Build decision
        decision = DispatchDecision(
            approved=approved,
            model_config={
                "provider": config.provider,
                "model": config.model,
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
                "cost_per_mtok": config.cost_per_mtok,
            },
            manifest_path=manifest_path,
            complexity_score=complexity_score,
            cost_estimate=manifest_data["cost_estimate"],
            reason=f"Task '{task_description}' → Complexity {complexity_score}/10 → {tier_name}",
        )

        return decision


# Quick test
if __name__ == "__main__":
    from pathlib import Path
    import tempfile

    # Create temp repo with a test file
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        test_file = tmpdir / "test.py"
        test_file.write_text("""
def simple():
    return 42

def complex_fn(x):
    if x > 10:
        for i in range(x):
            while i > 0:
                if i % 2 == 0:
                    try:
                        result = complex_fn(i - 1)
                    except:
                        pass
                i -= 1
    return 0
""")

        # Run dispatcher
        dispatcher = Dispatcher(repo_path=tmpdir)
        decision = dispatcher.dispatch(
            task_description="Add type hints to functions",
            target_files=["test.py"],
            ask_user=False,  # Skip interactive for test
        )

        print(f"\nDispatch Decision:")
        print(f"  Approved: {decision.approved}")
        print(f"  Complexity: {decision.complexity_score}/10")
        print(f"  Model: {decision.model_config['model']}")
        print(f"  Cost: ${decision.cost_estimate:.6f}")
        print(f"  Manifest: {decision.manifest_path}")
