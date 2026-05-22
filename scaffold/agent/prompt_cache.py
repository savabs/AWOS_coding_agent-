"""
PromptCache: Cache prompts and reuse for 90% cost reduction.
Hashes context, stores tokens, detects hits.
"""

import hashlib
import pickle
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime

try:
    from .prompt_hydrator import HydratedPrompt
except ImportError:
    from prompt_hydrator import HydratedPrompt


@dataclass
class CacheHit:
    """Result of cache lookup."""

    hit: bool
    cache_key: str
    new_tokens: int
    cached_tokens: int
    cost_savings: float  # USD
    timestamp: str


class PromptCache:
    """Cache hydrated prompts to save 90% on repeated contexts."""

    def __init__(self, cache_dir: Path = None):
        self.cache_dir = Path(cache_dir) if cache_dir else Path(".awos/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_cache_key(self, hydrated: HydratedPrompt) -> str:
        """
        Hash context (non-task parts) to get cache key.
        Tasks change, but system + context usually repeats.
        """
        # Hash: system_prompt + all symbol names (not full code)
        content = f"{hydrated.system_prompt}:{':'.join(s.name for s in hydrated.symbols_used)}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def check_cache(
        self,
        hydrated: HydratedPrompt,
        model_config: dict = None,
    ) -> CacheHit:
        """
        Check if context is in cache.
        If yes: return cached token count (cost savings: 90%).
        If no: cache it for next time.
        """
        if model_config is None:
            model_config = {"cost_per_mtok": 0.14}  # Default: DeepSeek

        cache_key = self.get_cache_key(hydrated)
        cache_file = self.cache_dir / f"{cache_key}.pkl"

        if cache_file.exists():
            # CACHE HIT
            try:
                with open(cache_file, "rb") as f:
                    cached_data = pickle.load(f)
                    cached_tokens = cached_data.get("tokens", hydrated.token_estimate)

                # Cost calculation
                full_cost = (hydrated.token_estimate / 1_000_000) * model_config.get("cost_per_mtok", 0.14)
                # With caching: only pay 10% of token cost (Anthropic prompt caching policy)
                cached_cost = (cached_tokens * 0.1 / 1_000_000) * model_config.get("cost_per_mtok", 0.14)
                savings = full_cost - cached_cost

                return CacheHit(
                    hit=True,
                    cache_key=cache_key,
                    new_tokens=hydrated.token_estimate,
                    cached_tokens=cached_tokens,
                    cost_savings=savings,
                    timestamp=datetime.utcnow().isoformat(),
                )
            except Exception:
                # Cache corrupted, treat as miss
                pass

        # CACHE MISS — store for next time
        try:
            cache_data = {
                "tokens": hydrated.token_estimate,
                "system_prompt": hydrated.system_prompt,
                "symbols": [s.name for s in hydrated.symbols_used],
                "created": datetime.utcnow().isoformat(),
            }
            with open(cache_file, "wb") as f:
                pickle.dump(cache_data, f)
        except Exception:
            pass  # Cache write failed, continue anyway

        # Cost: pay full price (but caching for next call)
        cost = (hydrated.token_estimate / 1_000_000) * model_config.get("cost_per_mtok", 0.14)

        return CacheHit(
            hit=False,
            cache_key=cache_key,
            new_tokens=hydrated.token_estimate,
            cached_tokens=0,
            cost_savings=0.0,  # Will save on next call
            timestamp=datetime.utcnow().isoformat(),
        )

    def clear_cache(self) -> None:
        """Clear all cached prompts."""
        for f in self.cache_dir.glob("*.pkl"):
            try:
                f.unlink()
            except Exception:
                pass

    def cache_stats(self) -> dict:
        """Get cache statistics."""
        files = list(self.cache_dir.glob("*.pkl"))
        total_cached = 0
        for f in files:
            try:
                with open(f, "rb") as fp:
                    data = pickle.load(fp)
                    total_cached += data.get("tokens", 0)
            except Exception:
                pass

        return {
            "cache_files": len(files),
            "total_cached_tokens": total_cached,
            "estimated_savings": (total_cached * 0.9 / 1_000_000) * 0.14,  # Assuming DeepSeek
        }


# Quick test
if __name__ == "__main__":
    import tempfile
    from prompt_hydrator import HydratedPrompt
    from symbol_extractor import Symbol

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        cache = PromptCache(cache_dir=tmpdir)

        # Create sample hydrated prompt
        symbols = [
            Symbol("auth", "function", "src/auth.py", 10, 20, "def auth()..."),
        ]
        hydrated = HydratedPrompt(
            task="Fix auth bug",
            system_prompt="You are an expert.",
            context_section="## Code\n...",
            full_prompt="Full prompt here",
            token_estimate=1000,
            symbols_used=symbols,
        )

        # First call: cache miss
        hit1 = cache.check_cache(hydrated)
        print(f"Call 1: {'HIT' if hit1.hit else 'MISS'}")
        print(f"  Cost: ${hit1.cost_savings:.6f}")

        # Second call: cache hit
        hit2 = cache.check_cache(hydrated)
        print(f"Call 2: {'HIT' if hit2.hit else 'MISS'}")
        print(f"  Cost savings: ${hit2.cost_savings:.6f}")
        print(f"  Reduction: {hit2.cost_savings/1000*100:.1f}% (90% expected)")

        print(f"\nCache stats: {cache.cache_stats()}")
