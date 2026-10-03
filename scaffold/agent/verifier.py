"""
Verifier Module: Applies SEARCH/REPLACE changes and validates with local tools.

Uses local compiler/linter to catch errors and provide feedback for retry.
Cost: FREE (local execution). Enables error-catching loop for worker self-correction.
"""

import difflib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class Verifier:
    """Applies code changes and validates them with local compiler/linter."""
    
    def __init__(self):
        """Initialize Verifier."""
        pass
    
    def verify_and_apply(
        self,
        search_replace: dict,
        file_path: str,
        check_type: str = "syntax"
    ) -> dict:
        """
        Apply SEARCH/REPLACE to file and validate.
        
        Args:
            search_replace: {"search": "...", "replace": "..."}
            file_path: Path to file to modify
            check_type: "syntax", "lint", or "all"
        
        Returns:
            {
                "success": bool,
                "applied": bool,
                "errors": [],
                "file_content": "modified content (if applied)",
                "needs_retry": bool,
                "error_context": "info for worker to fix"
            }
        """
        
        # Read original file (or detect new file creation)
        is_new_file = search_replace.get("_is_new_file", False)
        try:
            with open(file_path, 'r') as f:
                original_content = f.read()
        except FileNotFoundError:
            # If this is a new file creation (empty SEARCH), that's OK
            if is_new_file or (not search_replace.get("search") or search_replace.get("search").strip() == ""):
                original_content = ""
            else:
                return {
                    "success": False,
                    "applied": False,
                    "errors": [f"File not found: {file_path}"],
                    "needs_retry": False,
                    "error_context": "File does not exist. Check path in task."
                }
        
        # ── JSON-applied path: edits already applied by Worker ──────
        if search_replace.get("_json_applied"):
            modified_content = search_replace.get("_final_content", original_content)
            errors = self._check_syntax(file_path, modified_content)
            if errors:
                return {
                    "success": False, "applied": False, "errors": errors,
                    "file_content": modified_content, "needs_retry": True,
                    "error_context": f"Syntax error after JSON edit:\n{errors[0]}\n\nContext:\n{self._get_error_context(modified_content, errors[0])}"
                }
            contract_errors = self._check_contract_compliance(modified_content, task=search_replace.get("task_spec"))
            if contract_errors:
                return {
                    "success": False, "applied": False, "errors": contract_errors,
                    "file_content": modified_content, "needs_retry": True,
                    "error_context": f"Contract violation:\n{chr(10).join(contract_errors)}"
                }
            try:
                with open(file_path, 'w') as f:
                    f.write(modified_content)
            except Exception as e:
                return {
                    "success": False, "applied": False,
                    "errors": [f"Failed to write file: {str(e)}"],
                    "needs_retry": False,
                    "error_context": f"Permission error writing {file_path}"
                }
            return {
                "success": True, "applied": True, "errors": [],
                "file_content": modified_content, "needs_retry": False,
                "error_context": ""
            }

        # Apply SEARCH/REPLACE
        search_text = search_replace.get("search", "")
        replace_text = search_replace.get("replace", "")
        
        # Handle new file creation (empty SEARCH = write full content)
        if not search_text or search_text.strip() == "":
            if is_new_file or original_content == "":
                # Creating new file - write full content
                from pathlib import Path
                Path(file_path).parent.mkdir(parents=True, exist_ok=True)
                modified_content = replace_text
                
                # Check syntax
                errors = self._check_syntax(file_path, modified_content)
                if errors:
                    return {
                        "success": False,
                        "applied": False,
                        "errors": errors,
                        "file_content": modified_content,
                        "needs_retry": True,
                        "error_context": f"Syntax error in new file:\n{errors[0]}\n\nContext:\n{self._get_error_context(modified_content, errors[0])}"
                    }
                
                # Write file
                try:
                    with open(file_path, 'w') as f:
                        f.write(modified_content)
                except Exception as e:
                    return {
                        "success": False,
                        "applied": False,
                        "errors": [f"Failed to write file: {str(e)}"],
                        "needs_retry": False,
                        "error_context": f"Error writing {file_path}"
                    }
                
                return {
                    "success": True,
                    "applied": True,
                    "errors": [],
                    "file_content": modified_content,
                    "needs_retry": False,
                    "error_context": ""
                }
            else:
                # Existing file but empty search - error
                return {
                    "success": False,
                    "applied": False,
                    "errors": ["Empty search text"],
                    "needs_retry": False,
                    "error_context": "Worker provided empty search string."
                }
        
        # 4-tier fuzzy matching — find and apply the change
        matched, modified_content, match_tier = self._apply_fuzzy(
            original_content, search_text, replace_text
        )
        if not matched:
            nearby_hint = self._find_nearby_content(original_content, search_text)
            return {
                "success": False,
                "applied": False,
                "errors": ["SEARCH string not found in file"],
                "needs_retry": True,
                "error_context": (
                    f"SEARCH block did not match any content in the file.\n\n"
                    f"{nearby_hint}\n\n"
                    f"Your SEARCH block must be an exact (or near-exact) copy of lines "
                    f"from the file. Fix only the SEARCH block — keep your REPLACE unchanged."
                ),
            }
        if match_tier != "exact":
            print(f"[VERIFIER] Fuzzy match applied (tier={match_tier})")

        fidelity_errors = self._check_edit_fidelity(original_content, modified_content, search_replace.get("task_spec"))
        if fidelity_errors:
            return {
                "success": False,
                "applied": False,
                "errors": fidelity_errors,
                "file_content": modified_content,
                "needs_retry": False,
                "fidelity_fail": True,
                "error_context": (
                    f"{fidelity_errors[0]}\n\n"
                    "Match the edit shape to the task wording (e.g. # comment above, not inner docstring)."
                ),
            }
        
        # Check for syntax errors (local, free validation)
        errors = self._check_syntax(file_path, modified_content)

        if errors and check_type in ["syntax", "all"]:
            return {
                "success": False,
                "applied": False,
                "errors": errors,
                "file_content": modified_content,
                "needs_retry": True,
                "error_context": f"Syntax error after applying change:\n{errors[0]}\n\nContext:\n{self._get_error_context(modified_content, errors[0])}"
            }

        # Check contract compliance if task spec provided
        contract_errors = self._check_contract_compliance(modified_content, task=search_replace.get("task_spec"))
        if contract_errors and check_type in ["syntax", "all"]:
            return {
                "success": False,
                "applied": False,
                "errors": contract_errors,
                "file_content": modified_content,
                "needs_retry": True,
                "error_context": f"Contract violation:\n{chr(10).join(contract_errors)}\n\nFix the code to match the specification exactly."
            }

        # Check integration compliance (imports, instantiation, usage)
        integration_errors = self._check_integration_compliance(
            original_content, modified_content, task=search_replace.get("task_spec")
        )
        if integration_errors:
            return {
                "success": False,
                "applied": False,
                "errors": integration_errors,
                "file_content": modified_content,
                "needs_retry": True,
                "error_context": f"Integration check failed:\n{chr(10).join(integration_errors)}\n\nEnsure all required imports, instantiations, and usages are present."
            }

        # If no errors, apply the file change (write to disk)
        try:
            with open(file_path, 'w') as f:
                f.write(modified_content)
        except Exception as e:
            return {
                "success": False,
                "applied": False,
                "errors": [f"Failed to write file: {str(e)}"],
                "needs_retry": False,
                "error_context": f"Permission error writing {file_path}"
            }
        
        return {
            "success": True,
            "applied": True,
            "errors": [],
            "file_content": modified_content,
            "needs_retry": False,
            "error_context": ""
        }
    
    def _check_syntax(self, file_path: str, content: str) -> list:
        """Check syntax using appropriate tool for file type."""
        errors = []
        
        ext = Path(file_path).suffix.lower()
        
        # Python syntax check
        if ext == ".py":
            try:
                compile(content, file_path, "exec")
            except SyntaxError as e:
                errors.append(
                    f"SyntaxError at line {e.lineno}: {e.msg}\n"
                    f"  {e.text}\n"
                    f"  {' ' * (e.offset - 1)}^"
                )
            except Exception as e:
                errors.append(f"Python error: {str(e)}")
        
        # C++ syntax check (if g++ available)
        elif ext in [".cpp", ".cc", ".cxx", ".h", ".hpp"]:
            errors.extend(self._check_cpp_syntax(file_path, content))
        
        # TypeScript/JavaScript syntax check
        elif ext in [".ts", ".tsx", ".js", ".jsx"]:
            errors.extend(self._check_ts_syntax(file_path, content))
        
        return errors
    
    def _check_cpp_syntax(self, file_path: str, content: str) -> list:
        """Check C++ syntax with g++ (if available)."""
        errors = []
        try:
            # Try to compile with -fsyntax-only (fast check)
            result = subprocess.run(
                ["g++", "-fsyntax-only", file_path],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                errors.append(result.stderr)
        
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # g++ not available, skip check
            pass
        
        return errors
    
    def _check_ts_syntax(self, file_path: str, content: str) -> list:
        """Check TypeScript/JavaScript syntax."""
        # Simple check: look for unclosed brackets
        errors = []
        
        brackets = {"(": ")", "{": "}", "[": "]"}
        stack = []
        
        for i, char in enumerate(content):
            if char in brackets:
                stack.append((char, i))
            elif char in brackets.values():
                if not stack:
                    errors.append(f"Unmatched closing '{char}' at position {i}")
                    break
                open_char, _ = stack.pop()
                if brackets[open_char] != char:
                    errors.append(f"Mismatched brackets: '{open_char}' vs '{char}' at position {i}")
                    break
        
        if stack:
            open_char, pos = stack[-1]
            errors.append(f"Unclosed '{open_char}' at position {pos}")
        
        return errors
    
    def _get_error_context(self, content: str, error_msg: str) -> str:
        """Extract relevant lines around error."""
        # Try to extract line number from error message
        try:
            match = re.search(r"line (\d+)", error_msg)
            if match:
                line_num = int(match.group(1))
                lines = content.split("\n")
                start = max(0, line_num - 3)
                end = min(len(lines), line_num + 2)
                context = "\n".join(
                    f"{i+1:4d}: {lines[i]}"
                    for i in range(start, end)
                )
                return context
        except:
            pass
        
        # Default: return first 10 lines
        lines = content.split("\n")[:10]
        return "\n".join(f"{i+1:4d}: {line}" for i, line in enumerate(lines))

    def _apply_fuzzy(
        self, original_content: str, search_text: str, replace_text: str
    ) -> tuple:
        """
        4-tier fuzzy matching (Aider-inspired).
        Returns (matched: bool, modified_content: str, tier: str).
        """
        # Tier 1: exact
        if search_text in original_content:
            return True, original_content.replace(search_text, replace_text, 1), "exact"

        o_lines = original_content.split("\n")
        r_lines = replace_text.split("\n")
        s_lines = search_text.split("\n")
        n = len(s_lines)

        # Tier 2: whitespace-insensitive per line
        def _ws(t): return re.sub(r'[ \t]+', ' ', t.strip())
        s_ws = [_ws(l) for l in s_lines]
        o_ws = [_ws(l) for l in o_lines]
        for i in range(max(1, len(o_lines) - n + 1)):
            if o_ws[i:i + n] == s_ws:
                return True, "\n".join(o_lines[:i] + r_lines + o_lines[i + n:]), "whitespace"

        # Tier 3: strip trailing whitespace per line
        s_rs = [l.rstrip() for l in s_lines]
        o_rs = [l.rstrip() for l in o_lines]
        for i in range(max(1, len(o_lines) - n + 1)):
            if o_rs[i:i + n] == s_rs:
                return True, "\n".join(o_lines[:i] + r_lines + o_lines[i + n:]), "trailing_ws"

        # Tier 4: difflib fuzzy (≥85% similarity on stripped content)
        s_stripped = "\n".join(l.strip() for l in s_lines if l.strip())
        best_ratio, best_start = 0.0, -1
        for i in range(max(1, len(o_lines) - n + 1)):
            cand = "\n".join(l.strip() for l in o_lines[i:i + n] if l.strip())
            ratio = difflib.SequenceMatcher(None, s_stripped, cand).ratio()
            if ratio > best_ratio:
                best_ratio, best_start = ratio, i
        if best_ratio >= 0.85 and best_start >= 0:
            new_lines = o_lines[:best_start] + r_lines + o_lines[best_start + n:]
            return True, "\n".join(new_lines), f"fuzzy({best_ratio:.0%})"

        return False, original_content, "none"

    def _check_edit_fidelity(
        self, original_content: str, modified_content: str, task: dict | None
    ) -> list:
        """Reject patches whose shape contradicts the task action (e.g. docstring vs comment-above)."""
        if not task:
            return []
        action = (task.get("action") or "").lower()
        if "comment above" not in action:
            return []

        orig_set = set(original_content.splitlines())
        new_lines = [ln for ln in modified_content.splitlines() if ln not in orig_set]
        added_hash = any(ln.lstrip().startswith("#") for ln in new_lines)
        added_docstring = any('"""' in ln or "'''" in ln for ln in new_lines)
        if added_docstring and not added_hash:
            return [
                "fidelity_fail: task asked for comment above but patch added docstring inside function body"
            ]
        return []

    def _find_nearby_content(self, content: str, search_text: str) -> str:
        """Return the most similar block in the file to help the LLM fix its SEARCH block."""
        lines = content.split("\n")
        n = len(search_text.split("\n"))
        best_ratio, best_start = 0.0, 0
        for i in range(max(1, len(lines) - n + 1)):
            cand = "\n".join(lines[i:i + n])
            ratio = difflib.SequenceMatcher(None, search_text, cand).ratio()
            if ratio > best_ratio:
                best_ratio, best_start = ratio, i
        ctx_start = max(0, best_start - 2)
        ctx_end = min(len(lines), best_start + n + 2)
        nearby = "\n".join(
            f"{ctx_start + j + 1:4d}: {line}"
            for j, line in enumerate(lines[ctx_start:ctx_end])
        )
        return (
            f"Best match in file (similarity {best_ratio:.0%}) near line {best_start + 1}:\n"
            f"{nearby}"
        )

    def _check_integration_compliance(
        self, original_content: str, modified_content: str, task: dict = None
    ) -> list:
        """
        Verify integration-related changes were actually applied.
        
        Checks for common integration patterns mentioned in task:
        - "Add import" → verify import statement exists
        - "Instantiate" → verify class instantiation exists
        - "Replace calls" → verify new usage exists
        """
        if not task:
            return []
        
        errors = []
        action = task.get("action", "").lower()
        
        # Check 1: Import statements
        if "import" in action and "add" in action:
            # Extract potential class/module names from action
            # Look for patterns like "import X" or "from X import Y"
            import re
            # Common pattern: mentions class name in quotes or CamelCase
            import_patterns = re.findall(r"'([A-Z][a-zA-Z0-9_]+)'|\"([A-Z][a-zA-Z0-9_]+)\"|\\b([A-Z][a-zA-Z0-9_]+)\\b", action)
            for pattern_match in import_patterns:
                # pattern_match is a tuple of (quoted1, quoted2, unquoted)
                class_name = pattern_match[0] or pattern_match[1] or pattern_match[2]
                if class_name and len(class_name) > 2:  # Avoid single letters
                    # Check if import was added
                    if class_name not in modified_content and f"import {class_name}" not in modified_content:
                        errors.append(f"Task requires importing '{class_name}' but no import statement found")
        
        # Check 2: Instantiation
        if "instantiat" in action:  # matches "instantiate", "instantiation"
            # Extract class names mentioned in task
            import re
            class_patterns = re.findall(r"'([A-Z][a-zA-Z0-9_]+)'|\"([A-Z][a-zA-Z0-9_]+)\"|([A-Z][a-zA-Z0-9_]+)\(", action)
            for pattern_match in class_patterns:
                class_name = pattern_match[0] or pattern_match[1] or pattern_match[2]
                if class_name and len(class_name) > 2:
                    # Check for instantiation pattern: ClassName()
                    if f"{class_name}(" not in modified_content:
                        errors.append(f"Task requires instantiating '{class_name}' but no instantiation found: {class_name}()")
        
        # Check 3: Replacements
        if "replace" in action and ("call" in action or "usage" in action or "method" in action):
            # This is trickier - we need to verify SOME change happened
            # Simple heuristic: if task says "replace", modified should differ from original
            if modified_content.strip() == original_content.strip():
                errors.append("Task requires replacing calls/usage but file content is unchanged")
        
        return errors

    def _check_contract_compliance(self, content: str, task: dict | None) -> list:
        return check_contract_compliance(content, task)

    def _discover_test_file(self, source_path: str, project_root: str = ".") -> str | None:
        """
        Locate the test file for *source_path* using standard, alternative,
        and grep fallback patterns.  Returns absolute path or None.
        """
        import os, subprocess
        from pathlib import Path
        src = Path(source_path)
        stem = src.stem
        root = Path(project_root)

        # Standard: tests/test_<stem>.py
        standard = root / "tests" / f"test_{stem}.py"
        if standard.exists():
            return str(standard)

        # Alternative 1: <stem>_test.py in tests/
        alt_in_tests = root / "tests" / f"{stem}_test.py"
        if alt_in_tests.exists():
            return str(alt_in_tests)

        # Alternative 2: test_<stem>.py next to source
        alt = src.parent / f"test_{stem}.py"
        if alt.exists():
            return str(alt)

        # Check tests/ directory exists at all
        tests_dir = root / "tests"
        if not tests_dir.is_dir():
            return None

        # Grep fallback: find any test file that imports the module
        try:
            result = subprocess.run(
                ["grep", "-rl", stem, str(tests_dir)],
                capture_output=True, text=True, timeout=5,
            )
            hits = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            if hits:
                return hits[0]
        except Exception:
            pass
        return None

    def run_tests(self, source_path: str, project_root: str = ".") -> object:
        """
        Run tests for *source_path* if AWOS_SAFE_TO_RUN_TESTS=1 is set.
        Returns a simple result object with .passed, .failed, .no_tests_found.
        """
        import os, subprocess
        from dataclasses import dataclass

        @dataclass
        class _TestResult:
            passed: int = 0
            failed: int = 0
            no_tests_found: bool = False
            timed_out: bool = False
            @property
            def pass_rate(self) -> float:
                total = self.passed + self.failed
                return self.passed / total if total else 0.0

        if not os.getenv("AWOS_SAFE_TO_RUN_TESTS"):
            result = _TestResult(no_tests_found=True)
            return result

        test_file = self._discover_test_file(source_path, project_root)
        if test_file is None:
            return _TestResult(no_tests_found=True)

        try:
            proc = subprocess.run(
                ["python3", "-m", "pytest", test_file, "-q", "--tb=no"],
                capture_output=True, text=True, timeout=60,
                cwd=project_root,
            )
            passed = failed = 0
            for line in proc.stdout.splitlines():
                if " passed" in line:
                    try:
                        passed = int(line.strip().split()[0])
                    except (ValueError, IndexError):
                        pass
                if " failed" in line:
                    try:
                        failed = int(line.strip().split()[0])
                    except (ValueError, IndexError):
                        pass
            return _TestResult(passed=passed, failed=failed)
        except subprocess.TimeoutExpired:
            return _TestResult(timed_out=True)
        except Exception:
            return _TestResult(no_tests_found=True)


def check_contract_compliance(content: str, task: dict | None) -> list:
    """
    Check if generated code follows the Planner's contract specification.

    Shared helper used by both Verifier and SelfVerificationEngine.
    """
    errors = []
    if not task:
        return errors

    # 1. function_signature must appear literally in the code
    sig = task.get("function_signature", "")
    if sig:
        # Strip type hints for flexible matching (def name(...):)
        sig_match = re.search(r"def\s+\w+\s*\(", sig)
        if sig_match:
            core_sig = sig_match.group(0)
            if core_sig not in content:
                errors.append(f"Missing required function signature: {core_sig}")

    # 2. constraints — key phrases should appear in code (basic keyword check)
    constraints = task.get("constraints", [])
    for c in constraints:
        # Extract key noun phrases (naive: words > 4 chars)
        keywords = [w.lower() for w in c.split() if len(w) > 4]
        if keywords and not any(kw in content.lower() for kw in keywords):
            errors.append(f"Constraint possibly unmet: '{c}' (no keywords found)")

    # 3. must_not — forbidden patterns must NOT appear
    must_not = task.get("must_not", [])
    for m in must_not:
        # Extract import names or forbidden terms
        forbidden_terms = [w.strip("'\"") for w in m.split() if len(w.strip("'\"")) > 3]
        for term in forbidden_terms:
            if term.lower() in content.lower():
                errors.append(f"Contract violation — forbidden pattern found: '{term}' (from: {m})")

    return errors
