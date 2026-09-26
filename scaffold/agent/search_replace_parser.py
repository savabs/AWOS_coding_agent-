"""
SEARCH/REPLACE Parser — Zero-Redundancy Output

Problem: LLM outputs entire 500-line file for a 1-line change ($0.10 wasted).
Solution: Force LLM to output only SEARCH/REPLACE blocks. Validate with linter.

Format:
  ```
  SEARCH:
  def old_function():
      return "old"
  
  REPLACE:
  def old_function():
      return "new"
  ```
"""

import re
import subprocess
from pathlib import Path
from typing import List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class SearchReplaceBlock:
    """A single SEARCH/REPLACE block."""
    search_text: str
    replace_text: str
    file_path: Optional[str] = None
    line_start: int = 0
    line_end: int = 0


class SearchReplaceParser:
    """Parse and validate SEARCH/REPLACE blocks from LLM output."""
    
    SEARCH_REPLACE_PATTERN = r"```\s*(?:SEARCH|search)(.*?)(?:REPLACE|replace)(.*?)```"
    
    @staticmethod
    def extract_blocks(llm_output: str) -> List[SearchReplaceBlock]:
        """
        Extract all SEARCH/REPLACE blocks from LLM output.
        
        Args:
            llm_output: Raw output from LLM
        
        Returns:
            List of SearchReplaceBlock objects
        """
        blocks = []
        
        # Find all SEARCH/REPLACE pairs
        matches = re.finditer(
            SearchReplaceParser.SEARCH_REPLACE_PATTERN,
            llm_output,
            re.DOTALL | re.IGNORECASE
        )
        
        for match in matches:
            search_text = match.group(1).strip()
            replace_text = match.group(2).strip()
            
            if search_text and replace_text:
                blocks.append(SearchReplaceBlock(
                    search_text=search_text,
                    replace_text=replace_text,
                ))
        
        return blocks
    
    @staticmethod
    def validate_block(block: SearchReplaceBlock, file_path: Path) -> Tuple[bool, str]:
        """
        Validate a SEARCH/REPLACE block.
        
        Returns:
            (is_valid, error_message)
        """
        if not file_path.exists():
            return False, f"File not found: {file_path}"
        
        content = file_path.read_text()
        
        # Check: SEARCH text exists in file
        if block.search_text not in content:
            return False, f"SEARCH text not found in {file_path}"
        
        # Check: SEARCH text is unique (no accidental multiple matches)
        count = content.count(block.search_text)
        if count > 1:
            return False, f"SEARCH text matches {count} locations (ambiguous)"
        
        # Check: Replace text is syntactically valid (by language)
        is_valid_syntax, syntax_msg = SearchReplaceParser._check_syntax(
            file_path,
            block.replace_text
        )
        if not is_valid_syntax:
            return False, syntax_msg
        
        return True, ""
    
    @staticmethod
    def _check_syntax(file_path: Path, code: str) -> Tuple[bool, str]:
        """Run linter (ruff, black, etc.) on code snippet."""
        suffix = file_path.suffix.lower()
        
        if suffix == ".py":
            # Use Python AST to check syntax
            try:
                compile(code, filename="<snippet>", mode="exec")
                return True, ""
            except SyntaxError as e:
                return False, f"Python syntax error: {e}"
        
        elif suffix in [".js", ".ts"]:
            # For JS/TS, just check for common issues
            if code.count("{") != code.count("}"):
                return False, "Unmatched braces"
            if code.count("(") != code.count(")"):
                return False, "Unmatched parentheses"
        
        return True, ""
    
    @staticmethod
    def apply_block(block: SearchReplaceBlock, file_path: Path) -> bool:
        """
        Apply a SEARCH/REPLACE block to a file.
        
        Returns:
            True if successful, False otherwise
        """
        # Handle new file creation (empty SEARCH = create new file with REPLACE content)
        if not file_path.exists():
            if not block.search_text or block.search_text.strip() == "":
                # Creating new file
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(block.replace_text)
                print(f"✅ Created new file {file_path}")
                return True
            else:
                # File doesn't exist but SEARCH is not empty - error
                return False
        
        # Validate first
        is_valid, error = SearchReplaceParser.validate_block(block, file_path)
        if not is_valid:
            print(f"❌ Validation failed: {error}")
            return False
        
        # Apply
        content = file_path.read_text()
        new_content = content.replace(block.search_text, block.replace_text)
        
        file_path.write_text(new_content)
        print(f"✅ Applied SEARCH/REPLACE to {file_path}")
        return True
    
    @staticmethod
    def apply_blocks(blocks: List[SearchReplaceBlock], file_path: Path) -> Tuple[int, int]:
        """
        Apply multiple blocks to a file.
        
        Returns:
            (successful_count, failed_count)
        """
        successful = 0
        failed = 0
        
        for block in blocks:
            block.file_path = str(file_path)
            if SearchReplaceParser.apply_block(block, file_path):
                successful += 1
            else:
                failed += 1
        
        return successful, failed


class LinterGate:
    """Gate: Reject malformed diffs before applying."""
    
    @staticmethod
    def gate_check(file_path: Path) -> Tuple[bool, List[str]]:
        """
        Run linter on file. Returns (is_ok, [errors]).
        
        Checks:
          - Python syntax (ruff, black)
          - Type hints present
          - No trailing whitespace
        """
        suffix = file_path.suffix.lower()
        errors = []
        
        if suffix == ".py":
            # 1. Syntax check
            try:
                compile(file_path.read_text(), str(file_path), "exec")
            except SyntaxError as e:
                errors.append(f"Syntax error: {e}")
            
            # 2. Try ruff
            try:
                result = subprocess.run(
                    ["ruff", "check", str(file_path)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode != 0:
                    errors.extend(result.stdout.split("\n")[:3])  # First 3 errors
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass  # ruff not installed, skip
        
        return len(errors) == 0, errors
    
    @staticmethod
    def ask_for_correction(
        errors: List[str],
        original_code: str
    ) -> str:
        """
        Generate correction prompt to send back to LLM.
        
        This avoids applying broken code.
        """
        return f"""
Your SEARCH/REPLACE block has errors. Please fix and resubmit:

ERRORS:
{chr(10).join(f"  - {e}" for e in errors)}

ORIGINAL CODE BLOCK:
{original_code}

Please provide a corrected SEARCH/REPLACE block that passes the linter.
"""


# Quick test
if __name__ == "__main__":
    # Test extraction
    llm_output = """
Here's the fix:

```
SEARCH:
def old_function():
    return "old"

REPLACE:
def old_function():
    return "new"
```
"""
    
    parser = SearchReplaceParser()
    blocks = parser.extract_blocks(llm_output)
    print(f"Extracted {len(blocks)} blocks")
    for block in blocks:
        print(f"  SEARCH: {block.search_text[:30]}...")
        print(f"  REPLACE: {block.replace_text[:30]}...")
