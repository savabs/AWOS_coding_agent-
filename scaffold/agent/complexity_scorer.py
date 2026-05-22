"""
ComplexityScorer: Analyze code complexity from STRUCT.xml or file path.
Returns a score 1–10 where 1=trivial, 10=expert-required.
"""

import re
from pathlib import Path
from typing import Union


class ComplexityScorer:
    """Score code complexity based on simple heuristics."""

    def __init__(self):
        self.weights = {
            "lines_of_code": 0.3,
            "cyclomatic": 0.4,
            "import_depth": 0.2,
            "nesting": 0.1,
        }

    def score(self, source: Union[str, Path]) -> int:
        """
        Return complexity score 1–10.
        - source can be a file path or raw code string.
        """
        # Try to interpret as file path first
        if isinstance(source, Path):
            code = source.read_text(errors="ignore")
        elif isinstance(source, str):
            # Check if it looks like a file path
            try:
                path = Path(source)
                if path.exists() and path.is_file() and len(source) < 256:
                    code = path.read_text(errors="ignore")
                else:
                    # Treat as raw code string
                    code = source
            except (OSError, ValueError):
                # Invalid path, treat as code string
                code = source
        else:
            code = str(source)

        # Calculate component scores (0–1 scale)
        loc_score = self._score_lines_of_code(code)
        cyc_score = self._score_cyclomatic(code)
        imp_score = self._score_import_depth(code)
        nest_score = self._score_nesting(code)

        # Weighted average → 1–10
        weighted = (
            loc_score * self.weights["lines_of_code"]
            + cyc_score * self.weights["cyclomatic"]
            + imp_score * self.weights["import_depth"]
            + nest_score * self.weights["nesting"]
        )

        # Map [0, 1] → [1, 10]
        final_score = int(1 + weighted * 9)
        return max(1, min(10, final_score))

    def _score_lines_of_code(self, code: str) -> float:
        """Score based on line count. 0–1 scale."""
        lines = len(code.split("\n"))
        # 0 lines = 0, 1000 lines = 1.0
        return min(1.0, lines / 1000)

    def _score_cyclomatic(self, code: str) -> float:
        """
        Estimate cyclomatic complexity by counting control flow keywords.
        0–1 scale.
        """
        # Count: if, elif, else, for, while, except, and, or
        keywords = ["if ", "elif ", "for ", "while ", "except ", " and ", " or "]
        count = sum(code.count(kw) for kw in keywords)
        # 0 branches = 0, 50+ branches = 1.0
        return min(1.0, count / 50)

    def _score_import_depth(self, code: str) -> float:
        """Score based on import count and depth. 0–1 scale."""
        # Simple heuristic: count imports
        imports = len(re.findall(r"^(import|from)\s", code, re.MULTILINE))
        # 0 imports = 0, 50+ imports = 1.0
        return min(1.0, imports / 50)

    def _score_nesting(self, code: str) -> float:
        """Estimate nesting depth by counting indentation levels. 0–1 scale."""
        max_indent = 0
        for line in code.split("\n"):
            if line.strip():
                indent = len(line) - len(line.lstrip())
                max_indent = max(max_indent, indent)
        # 0 indent = 0, 32+ spaces = 1.0
        return min(1.0, max_indent / 32)


# Quick test
if __name__ == "__main__":
    scorer = ComplexityScorer()

    # Test 1: Simple code
    simple = "x = 1\ny = 2\nz = x + y"
    print(f"Simple code: {scorer.score(simple)}")  # Should be 1–2

    # Test 2: Complex code
    complex_code = """
import os
import sys
from pathlib import Path

def process_data(x):
    if x > 10:
        if x > 20:
            if x > 30:
                for i in range(x):
                    while i > 0:
                        if i % 2 == 0 and i % 3 == 0:
                            try:
                                result = process_data(i - 1)
                            except Exception as e:
                                pass
                        i -= 1
            else:
                return x * 2
        else:
            return x + 1
    return 0
"""
    print(f"Complex code: {scorer.score(complex_code)}")  # Should be 6–10
