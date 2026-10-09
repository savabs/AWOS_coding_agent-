"""
edit_grammar.py — T4 copy-constrained SEARCH: a GBNF grammar for llama-server.

Spec: docs/specs/edit_robustness.md (trick T4, docs/research/trick_book_2026-10.md).
Off unless AWOS_EDIT_GRAMMAR=1, and only for a local (llama-server) client:
cloud APIs (OpenRouter Flash) take no grammar — the fuzzy ladder
(fuzzy_apply.py) is the only lever there.

CRANE-style: the reply stays free text everywhere except inside an edit
block. Between "<<<<<<< SEARCH" and "=======" every line must be a verbatim
line of one of the target files; REPLACE lines are free (any line not
starting with ">>>>>>>").

Any-order line set, not in-order contiguity: the SEARCH lines are drawn from
a radix trie of the files' *distinct* lines. The trie is deterministic, so
the grammar engine keeps one parse stack per position. An in-order
constraint (each line followed only by its successor) needs one alternative
per file *position*; every repeated line (blank lines, "return", ")") then
keeps one live stack per occurrence, which is the case GBNF handles worst.
Contiguity is left to the apply path: exact match, then the fuzzy ladder.
An empty SEARCH (new file / append) stays legal.

Size: the grammar is roughly the size of the distinct text; above
AWOS_EDIT_GRAMMAR_MAX_BYTES (default 200000) it is not sent (logged).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

ENV_VAR = "AWOS_EDIT_GRAMMAR"
MAX_BYTES_ENV = "AWOS_EDIT_GRAMMAR_MAX_BYTES"
DEFAULT_MAX_BYTES = 200_000
HEAD = "<<<<<<< SEARCH"
DIV = "======="
TAIL = ">>>>>>> REPLACE"


def enabled() -> bool:
    return os.getenv(ENV_VAR, "").strip().lower() in ("1", "true", "yes", "on")


def max_bytes() -> int:
    try:
        return int(os.getenv(MAX_BYTES_ENV, "") or DEFAULT_MAX_BYTES)
    except ValueError:
        return DEFAULT_MAX_BYTES


def _lit_char(c: str) -> str:
    if c == "\\":
        return "\\\\"
    if c == '"':
        return '\\"'
    if c == "\n":
        return "\\n"
    if c == "\r":
        return "\\r"
    if c == "\t":
        return "\\t"
    o = ord(c)
    if o < 0x20 or o == 0x7F:
        return f"\\x{o:02X}"
    if o > 0x7E:
        return f"\\u{o:04X}" if o <= 0xFFFF else f"\\U{o:08X}"
    return c


def literal(s: str) -> str:
    """A GBNF string literal for `s`."""
    return '"' + "".join(_lit_char(c) for c in s) + '"'


def _class_char(c: str) -> str:
    if c in "]\\^-[":
        return "\\" + c
    if c == "\n":
        return "\\n"
    return _lit_char(c) if c != '"' else '"'


def not_prefixed(prefix: str) -> str:
    """GBNF alternatives for one line (no newline) NOT starting with `prefix`."""
    alts = ['""']
    for k in range(len(prefix)):
        head = literal(prefix[:k]) + " " if k else ""
        alts.append(f'{head}[^{_class_char(prefix[k])}\\n] [^\\n]*')
        if k:
            alts.append(literal(prefix[:k]))
    return " | ".join(f"( {a} )" for a in alts)


class _Node:
    __slots__ = ("children", "terminal")

    def __init__(self) -> None:
        self.children: dict[str, "_Node"] = {}
        self.terminal = False


def _build_trie(lines: Iterable[str]) -> _Node:
    root = _Node()
    for line in lines:
        node = root
        for c in line:
            node = node.children.setdefault(c, _Node())
        node.terminal = True
    return root


def file_lines(paths: Iterable[Path]) -> list[str]:
    """Distinct lines (\\r stripped) of the readable files, in first-seen order."""
    seen: dict[str, None] = {}
    for p in paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.split("\n"):
            seen.setdefault(line.rstrip("\r"), None)
    return list(seen)


def _trie_rules(root: _Node) -> list[str]:
    """Radix-compressed trie as GBNF rules; rule `sl` is one SEARCH line + newline."""
    rules: list[str] = []
    counter = [0]

    def name_for(node: _Node) -> str:
        counter[0] += 1
        return f"t{counter[0]}"

    def emit(node: _Node, name: str) -> None:
        alts = []
        if node.terminal:
            alts.append('"\\n"')
        for c, child in node.children.items():
            label = c
            while not child.terminal and len(child.children) == 1:
                (c2, child), = child.children.items()
                label += c2
            if not child.children:
                alts.append(literal(label + "\n"))
            else:
                sub = name_for(child)
                alts.append(f"{literal(label)} {sub}")
                emit(child, sub)
        rules.append(f"{name} ::= " + (" | ".join(alts) if alts else '"\\n"'))

    emit(root, "sl")
    return rules


def build_grammar(lines: list[str]) -> str:
    """Full GBNF for a reply whose SEARCH lines are drawn from `lines`."""
    rules = [
        "root ::= ( fl \"\\n\" | blk \"\\n\" )* ( fl | blk )?",
        f"fl ::= {not_prefixed(HEAD[:7])}",
        f"blk ::= {literal(HEAD + chr(10))} sl* {literal(DIV + chr(10))} rl* {literal(TAIL)}",
        f"rl ::= ( {not_prefixed(TAIL[:7])} ) \"\\n\"",
    ]
    rules += _trie_rules(_build_trie(lines))
    return "\n".join(rules) + "\n"


def grammar_for_files(project_root: str, rels: Iterable[str]) -> tuple[Optional[str], str]:
    """(grammar or None, note). None when there are no readable targets or the
    grammar exceeds max_bytes(); `note` says why, for the log."""
    root = Path(project_root)
    rels = [r for r in dict.fromkeys(rels) if r]
    paths = [root / r for r in rels if (root / r).is_file()]
    if not paths:
        return None, "no readable target files"
    lines = file_lines(paths)
    grammar = build_grammar(lines)
    size = len(grammar.encode("utf-8"))
    cap = max_bytes()
    if size > cap:
        return None, (f"grammar {size} bytes > cap {cap} ({len(paths)} files, "
                      f"{len(lines)} distinct lines)")
    return grammar, f"{size} bytes, {len(paths)} files, {len(lines)} distinct lines"
