"""
Edit Models — P0 Structured Edit Format.

Defines the JSON-first edit contract between the LLM and the file system.
Primary format: {"edits": [{"old_string": "...", "new_string": "..."}], "reasoning": "..."}
Fallback: legacy SEARCH/REPLACE text blocks (handled in worker._parse_search_replace).
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EditStatus(Enum):
    OK          = "ok"           # applied cleanly
    NOT_FOUND   = "not_found"    # old_string not in file
    MULTI_MATCH = "multi_match"  # old_string appears >1 times — ambiguous
    FUZZY_MATCH = "fuzzy_match"  # exact not found but fuzzy fallback succeeded
    SYNTAX_ERR  = "syntax_err"   # new content fails AST parse
    EMPTY       = "empty"        # old_string or new_string is blank


@dataclass
class EditInstruction:
    """A single old_string → new_string substitution."""
    old_string: str
    new_string: str
    description: str = ""


@dataclass
class EditRequest:
    """Parsed output from the LLM — one or more edit instructions."""
    edits: list[EditInstruction]
    reasoning: str = ""
    raw_response: str = ""


@dataclass
class SingleEditResult:
    """Result of applying one EditInstruction to file content."""
    instruction: EditInstruction
    status: EditStatus
    new_content: str = ""   # populated on OK or FUZZY_MATCH
    match_count: int = 0    # how many times old_string was found
    error: str = ""         # human-readable error on failure
    similarity: float = 0.0 # populated on FUZZY_MATCH


@dataclass
class EditResult:
    """Result of applying an entire EditRequest to file content."""
    request: EditRequest
    results: list[SingleEditResult] = field(default_factory=list)
    final_content: str = ""    # file content after all applied edits
    applied: int = 0           # number of edits that succeeded
    failed: int = 0            # number of edits that failed

    @property
    def success(self) -> bool:
        return self.applied > 0 and self.failed == 0

    @property
    def partial(self) -> bool:
        return self.applied > 0 and self.failed > 0

    def first_error(self) -> Optional[str]:
        for r in self.results:
            if r.error:
                return r.error
        return None

    def to_legacy_dict(self) -> dict:
        """
        Convert to the existing worker result format for backward compatibility.
        Maps the first (or only) edit to search/replace keys.
        """
        if not self.results:
            return {"success": False, "search": "", "replace": "", "reasoning": self.request.reasoning}

        first = self.results[0]
        extra = []
        if len(self.results) > 1:
            for r in self.results[1:]:
                if r.status in (EditStatus.OK, EditStatus.FUZZY_MATCH):
                    extra.append({
                        "old_string": r.instruction.old_string,
                        "new_string": r.instruction.new_string,
                    })

        return {
            "success": self.success or self.partial,
            "search": first.instruction.old_string,
            "replace": first.instruction.new_string,
            "reasoning": self.request.reasoning,
            "edit_status": first.status.value,
            "extra_edits": extra,
            "edits_applied": self.applied,
            "edits_failed": self.failed,
        }
