"""
A small GBNF recogniser for tests (llama.cpp's validator binary is not
installed here). Covers the dialect AWOS emits: string literals with
escapes, character classes ([...] / [^...], ranges, escapes), rule
references, groups, alternation, and the postfix operators * + ? {m} {m,}
{m,n}. One rule per line (continuation lines starting with whitespace or
'|' are joined). `matches(grammar, text)` is True when the whole text is
derivable from `root`.
"""
from __future__ import annotations

import re
import sys
from functools import lru_cache

_ESC = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"', "[": "[", "]": "]",
        "-": "-", "^": "^", "/": "/"}


class GBNFError(ValueError):
    pass


def _read_escape(src: str, i: int) -> tuple[str, int]:
    """src[i] is the char after a backslash; return (char, next index)."""
    c = src[i]
    if c in _ESC:
        return _ESC[c], i + 1
    for prefix, width in (("x", 2), ("u", 4), ("U", 8)):
        if c == prefix:
            return chr(int(src[i + 1:i + 1 + width], 16)), i + 1 + width
    raise GBNFError(f"bad escape \\{c}")


class _Parser:
    def __init__(self, src: str) -> None:
        self.s = src
        self.i = 0

    def ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t\n":
            self.i += 1

    def alt(self):
        seqs = [self.seq()]
        self.ws()
        while self.i < len(self.s) and self.s[self.i] == "|":
            self.i += 1
            seqs.append(self.seq())
            self.ws()
        return ("alt", tuple(seqs)) if len(seqs) > 1 else seqs[0]

    def seq(self):
        items = []
        while True:
            self.ws()
            if self.i >= len(self.s) or self.s[self.i] in "|)":
                break
            items.append(self.postfix(self.atom()))
        return ("seq", tuple(items))

    def atom(self):
        c = self.s[self.i]
        if c == '"':
            self.i += 1
            out = []
            while self.s[self.i] != '"':
                if self.s[self.i] == "\\":
                    ch, self.i = _read_escape(self.s, self.i + 1)
                else:
                    ch, self.i = self.s[self.i], self.i + 1
                out.append(ch)
            self.i += 1
            return ("lit", "".join(out))
        if c == "[":
            self.i += 1
            neg = self.s[self.i] == "^"
            if neg:
                self.i += 1
            ranges = []
            while self.s[self.i] != "]":
                lo, self.i = self._class_char()
                hi = lo
                if self.s[self.i] == "-" and self.s[self.i + 1] != "]":
                    self.i += 1
                    hi, self.i = self._class_char()
                ranges.append((lo, hi))
            self.i += 1
            return ("cls", neg, tuple(ranges))
        if c == "(":
            self.i += 1
            inner = self.alt()
            self.ws()
            if self.s[self.i] != ")":
                raise GBNFError("missing )")
            self.i += 1
            return inner
        m = re.match(r"[A-Za-z0-9_-]+", self.s[self.i:])
        if not m:
            raise GBNFError(f"unexpected {self.s[self.i:self.i + 20]!r}")
        self.i += m.end()
        return ("ref", m.group(0))

    def _class_char(self) -> tuple[str, int]:
        if self.s[self.i] == "\\":
            return _read_escape(self.s, self.i + 1)
        return self.s[self.i], self.i + 1

    def postfix(self, node):
        if self.i >= len(self.s):
            return node
        c = self.s[self.i]
        if c == "*":
            self.i += 1
            return ("rep", node, 0, None)
        if c == "+":
            self.i += 1
            return ("rep", node, 1, None)
        if c == "?":
            self.i += 1
            return ("rep", node, 0, 1)
        if c == "{":
            m = re.match(r"\{(\d+)(,(\d*))?\}", self.s[self.i:])
            if not m:
                raise GBNFError("bad {m,n}")
            self.i += m.end()
            lo = int(m.group(1))
            hi = lo if m.group(2) is None else (int(m.group(3)) if m.group(3) else None)
            return ("rep", node, lo, hi)
        return node


def parse_grammar(text: str) -> dict:
    rules: dict = {}
    logical: list[str] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if logical and (line[0] in " \t" or line.lstrip().startswith("|")):
            logical[-1] += " " + line.strip()
        else:
            logical.append(line)
    for line in logical:
        name, sep, body = line.partition("::=")
        if not sep:
            raise GBNFError(f"no ::= in {line[:40]!r}")
        p = _Parser(body)
        rules[name.strip()] = p.alt()
        p.ws()
        if p.i != len(p.s):
            raise GBNFError(f"trailing text in rule {name.strip()}: {p.s[p.i:p.i + 30]!r}")
    _check_refs(rules)
    return rules


def _check_refs(rules: dict) -> None:
    def walk(node):
        kind = node[0]
        if kind == "ref" and node[1] not in rules:
            raise GBNFError(f"undefined rule {node[1]}")
        if kind in ("alt", "seq"):
            for child in node[1]:
                walk(child)
        if kind == "rep":
            walk(node[1])
    if "root" not in rules:
        raise GBNFError("no root rule")
    for body in rules.values():
        walk(body)


def matches(grammar: str, text: str) -> bool:
    rules = parse_grammar(grammar)
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

    @lru_cache(maxsize=None)
    def ends(node, pos: int) -> frozenset:
        kind = node[0]
        if kind == "lit":
            s = node[1]
            return frozenset([pos + len(s)]) if text.startswith(s, pos) else frozenset()
        if kind == "cls":
            if pos >= len(text):
                return frozenset()
            ch = text[pos]
            inside = any(lo <= ch <= hi for lo, hi in node[2])
            return frozenset([pos + 1]) if inside != node[1] else frozenset()
        if kind == "ref":
            return ends(rules[node[1]], pos)
        if kind == "alt":
            out = set()
            for child in node[1]:
                out |= ends(child, pos)
            return frozenset(out)
        if kind == "seq":
            cur = {pos}
            for child in node[1]:
                nxt = set()
                for p in cur:
                    nxt |= ends(child, p)
                cur = nxt
                if not cur:
                    break
            return frozenset(cur)
        if kind == "rep":
            child, lo, hi = node[1], node[2], node[3]
            out = {pos} if lo == 0 else set()
            frontier, seen, count = {pos}, {pos}, 0
            while frontier and (hi is None or count < hi):
                count += 1
                nxt = set()
                for p in frontier:
                    nxt |= ends(child, p)
                if count >= lo:
                    out |= nxt
                # Unbounded: stop once no new positions appear.
                frontier = (nxt - seen) if hi is None else nxt
                seen |= nxt
            return frozenset(out)
        raise GBNFError(kind)

    return len(text) in ends(rules["root"], 0)
