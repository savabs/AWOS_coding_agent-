"""
Language detection for repositories

Step 1.7: Add language detection logic
"""

from pathlib import Path
from collections import defaultdict
from typing import Dict


def detect_languages(repo_path: str) -> Dict[str, float]:
    """Detect primary languages in repository by file count."""
    counts = defaultdict(int)
    total = 0
    
    lang_ext = {
        '.py': 'python',
        '.cpp': 'cpp', '.cc': 'cpp', '.cxx': 'cpp', '.h': 'cpp',
        '.rs': 'rust',
        '.go': 'go',
        '.js': 'javascript', '.jsx': 'javascript',
        '.ts': 'typescript', '.tsx': 'typescript',
        '.java': 'java',
    }
    
    for root, dirs, files in Path(repo_path).walk(on_error=lambda e: None):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for file in files:
            ext = Path(file).suffix
            if ext in lang_ext:
                counts[lang_ext[ext]] += 1
                total += 1
    
    if total == 0:
        return {}
    
    return {lang: count / total for lang, count in counts.items()}
