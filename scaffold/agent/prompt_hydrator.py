"""
PromptHydrator: Build hydrated prompts with code context.
Combines task + extracted symbols → full prompt with context.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from .symbol_extractor import Symbol, SymbolExtractor
except ImportError:
    from symbol_extractor import Symbol, SymbolExtractor


@dataclass
class HydratedPrompt:
    """A prompt with full context."""

    task: str
    system_prompt: str
    context_section: str
    full_prompt: str
    token_estimate: int
    symbols_used: list[Symbol]


class PromptHydrator:
    """Build hydrated prompts with code context."""

    def __init__(self, repo_path: Path = None):
        self.repo_path = Path(repo_path) if repo_path else Path.cwd()
        self.extractor = SymbolExtractor(repo_path)

    def hydrate(
        self,
        task: str,
        symbols: list[Symbol],
        model_config: dict = None,
        include_system_prompt: bool = True,
    ) -> HydratedPrompt:
        """
        Build hydrated prompt with symbols.
        
        Args:
            task: The task description
            symbols: Extracted symbols to include
            model_config: Model config (for token limits)
            include_system_prompt: Include system instructions
        
        Returns:
            HydratedPrompt with full context
        """
        if model_config is None:
            model_config = {"max_tokens": 8000}

        max_tokens = model_config.get("max_tokens", 8000)

        # Build sections
        system_prompt = self._build_system_prompt() if include_system_prompt else ""
        context_section = self._build_context(symbols, max_tokens)
        
        # Assemble full prompt
        full_prompt = self._assemble_prompt(system_prompt, task, context_section)
        
        # Estimate tokens (rough: 1 token per 4 chars)
        token_estimate = len(full_prompt) // 4

        return HydratedPrompt(
            task=task,
            system_prompt=system_prompt,
            context_section=context_section,
            full_prompt=full_prompt,
            token_estimate=token_estimate,
            symbols_used=symbols,
        )

    def _build_system_prompt(self) -> str:
        """Build system instructions."""
        return """You are an expert software engineer.
You analyze code, understand requirements, and provide precise solutions.
Focus on:
- Code clarity and correctness
- Following existing patterns in the codebase
- Minimal changes (refactor, don't rewrite)
- Testing edge cases
- Security and performance
"""

    def _build_context(self, symbols: list[Symbol], max_tokens: int) -> str:
        """Build context section from symbols."""
        if not symbols:
            return ""

        context_lines = ["## Relevant Code Context\n"]
        context_lines.append(f"Found {len(symbols)} relevant symbols:\n")

        token_budget = max_tokens // 2  # Reserve space for user input
        tokens_used = 0

        for sym in symbols:
            # Get actual code if possible
            try:
                code = self.extractor.get_symbol_code(sym)
            except Exception:
                code = sym.snippet

            # Build symbol block
            block = f"""
### {sym.name} ({sym.type})
File: {sym.file}:{sym.line}
```
{code}
```
"""
            block_tokens = len(block) // 4
            if tokens_used + block_tokens > token_budget:
                context_lines.append(f"... ({len(symbols) - len(context_lines)} more symbols omitted due to token limit)")
                break

            context_lines.append(block)
            tokens_used += block_tokens

        return "\n".join(context_lines)

    def _assemble_prompt(self, system: str, task: str, context: str) -> str:
        """Assemble full prompt."""
        parts = []

        if system:
            parts.append(f"# System Instructions\n{system}")

        if context:
            parts.append(context)

        parts.append(f"# Task\n{task}")

        return "\n\n".join(parts)

    def estimate_tokens(self, prompt: str) -> int:
        """Rough token count (1 token per 4 chars)."""
        return len(prompt) // 4


# Quick test
if __name__ == "__main__":
    import tempfile
    from symbol_extractor import Symbol

    hydrator = PromptHydrator()

    # Create sample symbols
    symbols = [
        Symbol(
            name="authenticate",
            type="function",
            file="src/auth.py",
            line=10,
            size=20,
            snippet="def authenticate(user, pwd):\n    return user == 'admin' and pwd == 'secret'",
        ),
        Symbol(
            name="UserSession",
            type="class",
            file="src/session.py",
            line=5,
            size=50,
            snippet="class UserSession:\n    def __init__(self, user):\n        self.user = user",
        ),
    ]

    task = "Add session timeout handling to prevent inactive sessions"

    hydrated = hydrator.hydrate(task, symbols)

    print("=" * 60)
    print("HYDRATED PROMPT")
    print("=" * 60)
    print(hydrated.full_prompt[:500] + "...")
    print("\n" + "=" * 60)
    print(f"Symbols used: {len(hydrated.symbols_used)}")
    print(f"Token estimate: {hydrated.token_estimate}")
