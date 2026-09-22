"""
Intelligent file path resolver - converts short filenames to full paths.

Deterministic: same filename → same full path (caches results)
Intelligent: searches codebase, handles common patterns
"""

import os
from pathlib import Path
from typing import Optional, List, Tuple
import fnmatch


class FileResolver:
    """
    Resolve short filenames to full paths intelligently and deterministically.
    
    Examples:
        "orchestrator.py" → "scaffold/agent/orchestrator.py"
        "verifier.py" → "scaffold/agent/verifier.py"
        "awos.py" → "awos.py"
    
    Rules:
    1. Exact match in root → return immediately
    2. Search common directories first (scaffold/, tests/, docs/)
    3. Cache results for determinism
    4. If multiple matches, pick most relevant (shortest path, or in scaffold/)
    """
    
    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root).resolve()
        self._cache = {}  # filename → full_path cache for determinism
        
        # Common directories to search (in priority order)
        self.search_dirs = [
            "scaffold/agent",
            "scaffold",
            "tests",
            "docs",
            "scripts",
            "memory",
            "tools",
        ]
    
    def resolve(self, filename: str) -> str:
        """
        Resolve short filename to full path.
        
        Returns:
            Full relative path from project root (e.g., "scaffold/agent/orchestrator.py")
            
        Raises:
            FileNotFoundError: If file cannot be found
            ValueError: If multiple ambiguous matches found
        """
        # Check cache first (determinism)
        if filename in self._cache:
            return self._cache[filename]
        
        # If already a full path that exists, return as-is
        full_path = self.project_root / filename
        if full_path.exists():
            relative = str(Path(filename))
            self._cache[filename] = relative
            return relative
        
        # Search for the file
        matches = self._find_matches(filename)
        
        if not matches:
            raise FileNotFoundError(
                f"Cannot resolve '{filename}' - file not found in project.\n"
                f"Searched: {self.project_root} and subdirectories {self.search_dirs}"
            )
        
        if len(matches) == 1:
            result = matches[0]
            self._cache[filename] = result
            return result
        
        # Multiple matches - pick most relevant deterministically
        result = self._pick_best_match(filename, matches)
        self._cache[filename] = result
        return result
    
    def _find_matches(self, filename: str) -> List[str]:
        """Find all files matching the filename."""
        matches = []
        
        # Search in priority directories first
        for search_dir in self.search_dirs:
            dir_path = self.project_root / search_dir
            if not dir_path.exists():
                continue
            
            # Walk directory tree
            for root, dirs, files in os.walk(dir_path):
                # Skip hidden dirs and common ignore patterns
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', '.git']]
                
                if filename in files:
                    full_path = Path(root) / filename
                    relative = full_path.relative_to(self.project_root)
                    matches.append(str(relative))
        
        # Also check root directory
        root_file = self.project_root / filename
        if root_file.exists():
            matches.append(filename)
        
        return matches
    
    def _pick_best_match(self, filename: str, matches: List[str]) -> str:
        """
        Pick the best match when multiple files exist.
        
        Priority:
        1. Shortest path (fewer directories = more likely to be main file)
        2. In scaffold/ (core code)
        3. Not in tests/ (prefer source over test)
        """
        def score_match(path: str) -> Tuple[int, int, int]:
            """Return (priority, depth, in_tests) for sorting."""
            parts = Path(path).parts
            depth = len(parts)
            
            # Priority 1: Root files (depth 1)
            if depth == 1:
                return (0, depth, 0)
            
            # Priority 2: scaffold/ files
            if parts[0] == 'scaffold':
                return (1, depth, 0)
            
            # Priority 3: Other non-test files
            in_tests = 1 if 'tests' in parts or 'test' in parts else 0
            return (2, depth, in_tests)
        
        # Sort by score (lower is better)
        sorted_matches = sorted(matches, key=score_match)
        return sorted_matches[0]
    
    def clear_cache(self):
        """Clear the resolution cache (useful for testing)."""
        self._cache.clear()


# Global instance for convenience
_default_resolver = None


def get_resolver(project_root: str = ".") -> FileResolver:
    """Get the default file resolver instance."""
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = FileResolver(project_root)
    return _default_resolver


def resolve_file(filename: str, project_root: str = ".") -> str:
    """Convenience function to resolve a single file."""
    resolver = get_resolver(project_root)
    return resolver.resolve(filename)


# Quick test
if __name__ == "__main__":
    resolver = FileResolver(".")
    
    test_cases = [
        "orchestrator.py",
        "verifier.py",
        "worker.py",
        "awos.py",
        "planner.py",
    ]
    
    print("File Resolution Test")
    print("=" * 60)
    
    for filename in test_cases:
        try:
            resolved = resolver.resolve(filename)
            print(f"✓ {filename:20s} → {resolved}")
        except FileNotFoundError as e:
            print(f"✗ {filename:20s} → NOT FOUND")
        except ValueError as e:
            print(f"⚠ {filename:20s} → AMBIGUOUS")
    
    print("\nCache (determinism check):")
    print(resolver._cache)
