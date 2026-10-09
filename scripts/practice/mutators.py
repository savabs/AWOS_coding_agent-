"""mutators.py — LLM-free procedural AST mutations for idle-time practice (T9).

Spec: docs/specs/idle_practice.md. SWE-smith's "procedural modification"
strategy (arXiv 2504.21798) without the LM: each operator finds candidate
nodes in one module and produces a *minimal* source edit (a splice of the
node's own source span), so the mutated file keeps its formatting and the
diff against the original is one small hunk.

Every mutation is verified: the spliced source must compile and must parse to
exactly the AST the operator intended (ast.dump equality). Splices that would
change precedence are retried in parentheses, then dropped.

    enumerate_mutations(source) -> [Mutation]
    apply(source, mutation)     -> mutated source (str) or None
"""
from __future__ import annotations

import ast
import copy
import hashlib
import re
from dataclasses import dataclass, field

# ── operator tables ───────────────────────────────────────────────────────────

CMP_FLIP = {
    ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.Gt: ast.LtE, ast.LtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
}
BIN_FLIP = {
    ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv, ast.Div: ast.Mult,
    ast.FloorDiv: ast.Div, ast.Mod: ast.FloorDiv, ast.BitAnd: ast.BitOr,
    ast.BitOr: ast.BitAnd, ast.LShift: ast.RShift, ast.RShift: ast.LShift,
}

OPERATORS = (
    "flip_compare",       # a < b  -> a >= b ; == -> != ; in -> not in ; is -> is not
    "flip_binop",         # + <-> - ; * -> // ; / -> * ; // -> / ; % -> //
    "flip_boolop",        # and <-> or
    "off_by_one",         # int literal n -> n+1 / n-1 (not bools, not 0 -> -1)
    "negate_if",          # if c: -> if not c:
    "remove_conditional", # drop an `if` block that has no else
    "swap_args",          # f(a, b, ...) -> f(b, a, ...)
    "change_return",      # return <expr> -> return None
    "remove_call",        # drop a bare call statement: obj.method(...)
    "swap_ifexp",         # a if c else b -> b if c else a
)


@dataclass(frozen=True)
class Mutation:
    op: str
    index: int            # position of the target node in ast.walk order
    variant: int = 0      # e.g. off_by_one +1 (0) / -1 (1)
    lineno: int = 0
    desc: str = field(default="", compare=False)


# ── helpers ───────────────────────────────────────────────────────────────────


def _nodes(tree: ast.AST) -> list[ast.AST]:
    return list(ast.walk(tree))


def _annotation_ids(tree: ast.AST) -> set[int]:
    """ids of nodes inside annotations, docstrings or `if TYPE_CHECKING:` (never mutated)."""
    skip: set[int] = set()

    def mark(n: ast.AST | None) -> None:
        if n is not None:
            for sub in ast.walk(n):
                skip.add(id(sub))

    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mark(n.returns)
            for a in n.args.args + n.args.posonlyargs + n.args.kwonlyargs:
                mark(a.annotation)
            mark(n.args.vararg and n.args.vararg.annotation)
            mark(n.args.kwarg and n.args.kwarg.annotation)
            # default values are evaluated once at def time; still fine to mutate
        elif isinstance(n, ast.AnnAssign):
            mark(n.annotation)
        elif isinstance(n, ast.If):
            t = n.test
            name = t.id if isinstance(t, ast.Name) else t.attr if isinstance(t, ast.Attribute) else ""
            if name == "TYPE_CHECKING":
                mark(n)
            # if __name__ == "__main__": blocks are not exercised by tests
            if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                    and t.left.id == "__name__"):
                mark(n)
    return skip


def _parent_map(tree: ast.AST) -> dict[int, tuple[ast.AST, str]]:
    out: dict[int, tuple[ast.AST, str]] = {}
    for p in ast.walk(tree):
        for fname, value in ast.iter_fields(p):
            if isinstance(value, list):
                for v in value:
                    if isinstance(v, ast.AST):
                        out[id(v)] = (p, fname)
            elif isinstance(value, ast.AST):
                out[id(value)] = (p, fname)
    return out


def _has_pos(n: ast.AST) -> bool:
    return all(getattr(n, a, None) is not None
               for a in ("lineno", "col_offset", "end_lineno", "end_col_offset"))


def _offsets(source: str) -> list[int]:
    """Byte-free char offset of each line start (1-based lines -> index lineno-1)."""
    starts, pos = [], 0
    for line in source.splitlines(keepends=True):
        starts.append(pos)
        pos += len(line)
    starts.append(pos)
    return starts


def _char_index(source_lines: list[str], starts: list[int], lineno: int, col_bytes: int) -> int:
    # ast col offsets are UTF-8 byte offsets
    line = source_lines[lineno - 1]
    prefix = line.encode("utf-8")[:col_bytes].decode("utf-8", errors="ignore")
    return starts[lineno - 1] + len(prefix)


def _span(source: str, node: ast.AST) -> tuple[int, int]:
    lines = source.splitlines(keepends=True)
    starts = _offsets(source)
    return (_char_index(lines, starts, node.lineno, node.col_offset),
            _char_index(lines, starts, node.end_lineno, node.end_col_offset))


def _is_full_line_stmt(source: str, node: ast.stmt) -> bool:
    lines = source.splitlines(keepends=True)
    start_line = lines[node.lineno - 1]
    end_line = lines[node.end_lineno - 1]
    before = start_line.encode("utf-8")[:node.col_offset].decode("utf-8", errors="ignore")
    after = end_line.encode("utf-8")[node.end_col_offset:].decode("utf-8", errors="ignore")
    after = after.split("#", 1)[0]
    return before.strip() == "" and after.strip() in ("",)


# ── candidate enumeration ─────────────────────────────────────────────────────


def enumerate_mutations(source: str) -> list[Mutation]:
    """Every candidate mutation of `source` (not yet verified)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    skip = _annotation_ids(tree)
    parents = _parent_map(tree)
    out: list[Mutation] = []
    for i, n in enumerate(_nodes(tree)):
        if id(n) in skip or not _has_pos(n):
            continue
        ln = n.lineno
        if isinstance(n, ast.Compare):
            for k, o in enumerate(n.ops):
                if type(o) in CMP_FLIP:
                    out.append(Mutation("flip_compare", i, k, ln, type(o).__name__))
        elif isinstance(n, ast.BinOp) and type(n.op) in BIN_FLIP:
            # skip string formatting `"..." % x` and string concatenation
            if isinstance(n.op, ast.Mod) and isinstance(n.left, (ast.Constant, ast.JoinedStr)):
                continue
            if any(isinstance(side, ast.JoinedStr) or (isinstance(side, ast.Constant)
                   and isinstance(side.value, (str, bytes))) for side in (n.left, n.right)):
                continue
            out.append(Mutation("flip_binop", i, 0, ln, type(n.op).__name__))
        elif isinstance(n, ast.BoolOp):
            out.append(Mutation("flip_boolop", i, 0, ln, type(n.op).__name__))
        elif (isinstance(n, ast.Constant) and type(n.value) is int):
            out.append(Mutation("off_by_one", i, 0, ln, f"{n.value}+1"))
            if n.value != 0:
                out.append(Mutation("off_by_one", i, 1, ln, f"{n.value}-1"))
        elif isinstance(n, ast.If):
            out.append(Mutation("negate_if", i, 0, ln))
            if not n.orelse and _is_full_line_stmt(source, n):
                out.append(Mutation("remove_conditional", i, 0, ln))
        elif isinstance(n, ast.Call):
            pos = [a for a in n.args if not isinstance(a, ast.Starred)]
            if len(pos) >= 2 and len(pos) == len(n.args) and ast.dump(n.args[0]) != ast.dump(n.args[1]):
                out.append(Mutation("swap_args", i, 0, ln))
        elif isinstance(n, ast.Return):
            v = n.value
            if v is not None and not (isinstance(v, ast.Constant) and v.value is None):
                out.append(Mutation("change_return", i, 0, ln))
        elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and _is_full_line_stmt(source, n):
            out.append(Mutation("remove_call", i, 0, ln))
        elif isinstance(n, ast.IfExp):
            if ast.dump(n.body) != ast.dump(n.orelse):
                out.append(Mutation("swap_ifexp", i, 0, ln))
    return out


# ── application ───────────────────────────────────────────────────────────────


def _mutated_node(n: ast.AST, m: Mutation) -> ast.AST | None:
    """The replacement node (a deep copy), or None to delete a statement."""
    new = copy.deepcopy(n)
    if m.op == "flip_compare":
        new.ops[m.variant] = CMP_FLIP[type(n.ops[m.variant])]()
    elif m.op == "flip_binop":
        new.op = BIN_FLIP[type(n.op)]()
    elif m.op == "flip_boolop":
        new.op = ast.Or() if isinstance(n.op, ast.And) else ast.And()
    elif m.op == "off_by_one":
        new.value = n.value + (1 if m.variant == 0 else -1)
    elif m.op == "negate_if":  # replaces the test; `if not x` -> `if x`
        if isinstance(n.test, ast.UnaryOp) and isinstance(n.test.op, ast.Not):
            return copy.deepcopy(n.test.operand)
        return ast.UnaryOp(op=ast.Not(), operand=copy.deepcopy(n.test))
    elif m.op == "swap_args":
        new.args[0], new.args[1] = new.args[1], new.args[0]
    elif m.op == "change_return":
        new.value = ast.Constant(value=None)
    elif m.op == "swap_ifexp":
        new.body, new.orelse = new.orelse, new.body
    elif m.op in ("remove_conditional", "remove_call"):
        return None
    return new


def _expected_tree(tree: ast.Module, m: Mutation) -> ast.Module:
    """The whole-module AST the mutation should produce (for verification)."""
    t2 = copy.deepcopy(tree)
    target = _nodes(t2)[m.index]
    parents = _parent_map(t2)
    parent, fname = parents[id(target)]
    repl = _mutated_node(target, m)
    if m.op == "negate_if":
        target.test = repl
        return t2
    seq = getattr(parent, fname)
    if isinstance(seq, list):
        k = next(j for j, x in enumerate(seq) if x is target)
        if repl is None:
            del seq[k]
            if not seq:
                seq.append(ast.Pass())
        else:
            seq[k] = repl
    else:
        setattr(parent, fname, repl)
    return t2


def _same(a: ast.AST, b: ast.AST) -> bool:
    return ast.dump(a, include_attributes=False) == ast.dump(b, include_attributes=False)


_OP_TOKENS = {
    ast.Lt: r"<(?!=)", ast.GtE: r">=", ast.Gt: r">(?!=)", ast.LtE: r"<=",
    ast.Eq: r"==", ast.NotEq: r"!=", ast.Is: r"\bis\b", ast.IsNot: r"\bis\s+not\b",
    ast.In: r"\bin\b", ast.NotIn: r"\bnot\s+in\b",
    ast.Add: r"\+", ast.Sub: r"-", ast.Mult: r"\*(?!\*)", ast.Div: r"/(?!/)",
    ast.FloorDiv: r"//", ast.Mod: r"%", ast.BitAnd: r"&", ast.BitOr: r"\|",
    ast.LShift: r"<<", ast.RShift: r">>", ast.And: r"\band\b", ast.Or: r"\bor\b",
}
_OP_TEXT = {
    ast.Lt: "<", ast.GtE: ">=", ast.Gt: ">", ast.LtE: "<=", ast.Eq: "==", ast.NotEq: "!=",
    ast.Is: "is", ast.IsNot: "is not", ast.In: "in", ast.NotIn: "not in", ast.Add: "+",
    ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//", ast.Mod: "%",
    ast.BitAnd: "&", ast.BitOr: "|", ast.LShift: "<<", ast.RShift: ">>",
    ast.And: "and", ast.Or: "or",
}


def _replace_in_gap(source: str, a: int, b: int, old_op: type, new_op: type) -> str | None:
    gap = source[a:b]
    pat = _OP_TOKENS[old_op]
    if old_op is ast.Is:   # `is` must not match the `is` of `is not`
        pat = r"\bis\b(?!\s+not\b)"
    if old_op is ast.In:
        pat = r"(?<!not )\bin\b"
    new_gap, k = re.subn(pat, _OP_TEXT[new_op], gap, count=1)
    return source[:a] + new_gap + source[b:] if k else None


def _token_splice(source: str, node: ast.AST, m: Mutation) -> str | None:
    """Formatting-preserving edit: touch only the operator token or the swapped
    operand spans. None when not applicable (the unparse splice is the fallback)."""
    try:
        if m.op == "flip_compare":
            k = m.variant
            left = node.left if k == 0 else node.comparators[k - 1]
            right = node.comparators[k]
            old = type(node.ops[k])
            return _replace_in_gap(source, _span(source, left)[1], _span(source, right)[0],
                                   old, CMP_FLIP[old])
        if m.op == "flip_binop":
            old = type(node.op)
            return _replace_in_gap(source, _span(source, node.left)[1],
                                   _span(source, node.right)[0], old, BIN_FLIP[old])
        if m.op == "flip_boolop":
            old = type(node.op)
            new = ast.Or if old is ast.And else ast.And
            out = source
            for left, right in reversed(list(zip(node.values, node.values[1:]))):
                out = _replace_in_gap(out, _span(out, left)[1], _span(out, right)[0], old, new)
                if out is None:
                    return None
            return out
        if m.op == "off_by_one":
            a, b = _span(source, node)
            return source[:a] + repr(node.value + (1 if m.variant == 0 else -1)) + source[b:]
        if m.op == "change_return":
            a, b = _span(source, node.value)
            return source[:a] + "None" + source[b:]
        if m.op == "negate_if":
            t = node.test
            a, b = _span(source, t)
            if isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not):
                oa, ob = _span(source, t.operand)
                return source[:a] + source[oa:ob] + source[b:]
            inner = source[a:b]
            simple = isinstance(t, (ast.Name, ast.Attribute, ast.Call, ast.Subscript))
            return source[:a] + ("not " + inner if simple else "not (" + inner + ")") + source[b:]
        if m.op in ("swap_args", "swap_ifexp"):
            x, y = ((node.args[0], node.args[1]) if m.op == "swap_args"
                    else (node.body, node.orelse))
            xa, xb = _span(source, x)
            ya, yb = _span(source, y)
            if not xb <= ya:
                return None
            return source[:xa] + source[ya:yb] + source[xb:ya] + source[xa:xb] + source[yb:]
    except (AttributeError, IndexError, KeyError):
        return None
    return None


def apply(source: str, m: Mutation) -> str | None:
    """Mutated source for `m`, verified to compile and to match the intended AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    nodes = _nodes(tree)
    if m.index >= len(nodes):
        return None
    node = nodes[m.index]
    expected = _expected_tree(tree, m)
    candidates: list[str] = []
    if m.op in ("remove_conditional", "remove_call"):
        parents = _parent_map(tree)
        parent, fname = parents[id(node)]
        siblings = getattr(parent, fname)
        lines = source.splitlines(keepends=True)
        indent = lines[node.lineno - 1][: len(lines[node.lineno - 1]) - len(lines[node.lineno - 1].lstrip())]
        nl = "\n" if not lines[node.end_lineno - 1].endswith("\r\n") else "\r\n"
        filler = f"{indent}pass{nl}" if len(siblings) == 1 else ""
        candidates.append("".join(lines[: node.lineno - 1]) + filler + "".join(lines[node.end_lineno:]))
    else:
        tok = _token_splice(source, node, m)
        if tok is not None:
            candidates.append(tok)
        target = node.test if m.op == "negate_if" else node
        repl = _mutated_node(node, m)
        text = ast.unparse(repl)
        a, b = _span(source, target)
        candidates.append(source[:a] + text + source[b:])
        candidates.append(source[:a] + "(" + text + ")" + source[b:])
    for cand in candidates:
        try:
            compile(cand, "<mutant>", "exec")
            got = ast.parse(cand)
        except (SyntaxError, ValueError):
            continue
        if _same(got, expected) and not _same(got, tree):
            return cand
    return None


def mutant_key(path: str, mutated: str) -> str:
    """Dedupe key: file + normalized mutated AST (formatting-insensitive)."""
    try:
        norm = ast.dump(ast.parse(mutated), include_attributes=False)
    except SyntaxError:
        norm = mutated
    return hashlib.sha256(f"{path}\0{norm}".encode()).hexdigest()[:16]
