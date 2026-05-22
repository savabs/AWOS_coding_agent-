"""
SymbolExtractor: Extract relevant symbols from STRUCT.xml.
Matches task keywords to code symbols (classes, functions, modules).
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


@dataclass
class Symbol:
    """A code symbol (function, class, module)."""

    name: str
    type: str  # "function", "class", "module"
    file: str
    line: int
    size: int  # approximate lines of code
    snippet: str  # code preview


class SymbolExtractor:
    """Extract symbols from STRUCT.xml and match to task."""

    def __init__(self, repo_path: Path = None):
        self.repo_path = Path(repo_path) if repo_path else Path.cwd()
        self.struct_xml = self.repo_path / ".awos" / "STRUCT.xml"

    def extract_symbols(self, task_description: str, limit: int = 5) -> list[Symbol]:
        """
        Extract relevant symbols for a task.
        
        Args:
            task_description: What the agent needs to do
            limit: Max number of symbols to return
        
        Returns:
            List of relevant Symbol objects
        """
        if not self.struct_xml.exists():
            # No STRUCT.xml available, return empty
            return []

        try:
            tree = ET.parse(self.struct_xml)
            root = tree.getroot()
        except Exception:
            return []

        # Parse task keywords
        task_keywords = self._extract_keywords(task_description)

        # Find matching symbols
        symbols = []
        for symbol_elem in root.findall(".//symbol"):
            name = symbol_elem.get("name", "")
            sym_type = symbol_elem.get("type", "")
            file = symbol_elem.get("file", "")
            line = int(symbol_elem.get("line", "0"))
            size = int(symbol_elem.get("size", "0"))
            snippet = symbol_elem.findtext("snippet", "")

            # Score: how relevant is this symbol to the task?
            score = self._score_relevance(name, task_keywords)

            if score > 0:
                symbols.append(
                    (
                        score,
                        Symbol(
                            name=name,
                            type=sym_type,
                            file=file,
                            line=line,
                            size=size,
                            snippet=snippet,
                        ),
                    )
                )

        # Sort by relevance, return top N
        symbols.sort(key=lambda x: x[0], reverse=True)
        return [sym for _, sym in symbols[:limit]]

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords from text."""
        # Simple tokenization
        stop_words = {
            "the", "a", "an", "and", "or", "is", "are", "be", "to", "of",
            "for", "in", "on", "with", "this", "that", "what", "how", "why"
        }
        tokens = text.lower().split()
        return [t.strip(".,!?;:") for t in tokens if t and t.lower() not in stop_words]

    def _score_relevance(self, symbol_name: str, keywords: list[str]) -> float:
        """Score how relevant a symbol is to the task keywords."""
        symbol_lower = symbol_name.lower()
        score = 0.0

        for kw in keywords:
            # Exact substring match
            if kw in symbol_lower:
                score += 1.0
            # Partial match (first 3 chars)
            elif symbol_lower.startswith(kw[:3]):
                score += 0.3

        return score

    def get_symbol_code(self, symbol: Symbol) -> str:
        """Load actual code for a symbol."""
        try:
            file_path = self.repo_path / symbol.file
            if not file_path.exists():
                return ""

            lines = file_path.read_text(errors="ignore").split("\n")
            # Return snippet + context (lines around it)
            start = max(0, symbol.line - 2)
            end = min(len(lines), symbol.line + symbol.size + 2)
            return "\n".join(lines[start:end])
        except Exception:
            return symbol.snippet or ""


# Quick test
if __name__ == "__main__":
    import tempfile

    # Create a test STRUCT.xml
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        awos_dir = tmpdir / ".awos"
        awos_dir.mkdir()

        # Create sample STRUCT.xml
        struct_xml = awos_dir / "STRUCT.xml"
        struct_xml.write_text("""<?xml version="1.0"?>
<struct>
  <symbol name="authenticate" type="function" file="src/auth.py" line="10" size="20">
    <snippet>def authenticate(username, password):\n    ...</snippet>
  </symbol>
  <symbol name="UserSession" type="class" file="src/session.py" line="5" size="50">
    <snippet>class UserSession:\n    ...</snippet>
  </symbol>
  <symbol name="process_request" type="function" file="src/handler.py" line="1" size="30">
    <snippet>def process_request(req):\n    ...</snippet>
  </symbol>
</struct>
""")

        extractor = SymbolExtractor(repo_path=tmpdir)
        symbols = extractor.extract_symbols("Fix authentication bug", limit=3)

        print(f"Found {len(symbols)} relevant symbols:")
        for sym in symbols:
            print(f"  - {sym.name} ({sym.type}) in {sym.file}:{sym.line}")
