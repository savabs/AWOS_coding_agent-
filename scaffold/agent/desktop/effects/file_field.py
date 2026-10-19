# The FIELD_EQUALS + collateral-loss contract is adapted from OpenAdapt Flow
# (openadapt_flow/runtime/effects/effect.py and _common.py: field_equals,
# "exactly once", collateral loss -> REFUTED, unreadable -> INDETERMINATE),
# Copyright (c) 2026 OpenAdapt.AI (MLDSAI Inc.), MIT License -- see
# LICENSE-OpenAdapt in this directory. The .env / TOML substrate is AWOS's own.
"""Config-file field effect: "FIELD holds VALUE exactly once, nothing else changed".

Two formats:
    dotenv   field = variable name                 FAKESERVICE_API_KEY
    toml     field = "section.key" or "key"        fakeservice.api_key

``FieldEquals`` is the verifier. It never stores the expected value: it keeps
a sha256 digest and compares the digest of what it reads back, so the
contract (and every verdict/receipt built from it) is safe to log.

    eff = FieldEquals.for_value(path, "dotenv", "FAKESERVICE_API_KEY", value)
    before = eff.capture()
    write_field(path, "dotenv", "FAKESERVICE_API_KEY", value)
    v = eff.verify(before)        # CONFIRMED | REFUTED | INDETERMINATE

CONFIRMED needs all of:
  * the field is present exactly once and its value's digest matches;
  * collateral check: every other line is the pre-state's, in order. In a
    dotenv file the only extra lines allowed are blank or ``#`` comments; in
    TOML every other parsed value is unchanged and the only physical lines
    that changed are that key's assignment (plus its section header and a
    blank line when the section had to be created);
  * the trailing newline is kept and the file mode is not loosened.
REFUTED when the file is readable and any of those fails. ``absent=True`` on
a REFUTED verdict means the field is still missing and the file is byte-for-
byte the pre-state: the only case in which retrying the write is safe.
INDETERMINATE when the file cannot be read (or the pre-state could not be).

``write_field`` is the minimal-edit writer the enforcer uses at the sink:
replace the one existing assignment, or add one; refuse when the field is
ambiguous (two assignments, a multi-line value); atomic replace that keeps the
file mode.
"""
from __future__ import annotations

import difflib
import os
import re
import stat
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .outcome import CheckLevel, EffectVerdict, confirmed, digest, indeterminate, refuted

SUBSTRATE = "file_field"
FORMATS = ("dotenv", "toml")


class FieldWriteRefused(ValueError):
    """The writer will not guess (ambiguous field, unsupported shape, no file)."""


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------

def _dotenv_re(name: str) -> re.Pattern:
    return re.compile(r"^(?P<lead>\s*(?:export\s+)?)" + re.escape(name) + r"(?P<eq>\s*=\s*)(?P<val>.*?)\s*$")


def dotenv_value(line: str, name: str) -> Optional[str]:
    """The value of ``name`` on this line (quotes stripped), or None."""
    m = _dotenv_re(name).match(line.rstrip("\r\n"))
    if not m:
        return None
    v = m.group("val")
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def _split_field(fmt: str, name: str) -> Tuple[Optional[str], str]:
    if fmt == "toml" and "." in name:
        sect, key = name.rsplit(".", 1)
        return sect, key
    return None, name


_SECTION = re.compile(r"^\s*\[(?!\[)\s*([^\]]+?)\s*\]\s*(?:#.*)?$")


def _toml_key_re(key: str) -> re.Pattern:
    k = re.escape(key)
    return re.compile(r"^(?P<lead>\s*)(?P<key>" + k + r"|\"" + k + r"\"|'" + k + r"')(?P<eq>\s*=\s*)(?P<rest>.*)$")


def _toml_assign_lines(lines: List[str], sect: Optional[str], key: str) -> List[int]:
    """Indexes of physical lines assigning ``key`` inside ``sect`` (None = top level)."""
    cur: Optional[str] = None
    rx = _toml_key_re(key)
    out = []
    for i, ln in enumerate(lines):
        m = _SECTION.match(ln)
        if m:
            cur = m.group(1).strip()
            continue
        if ln.lstrip().startswith("[["):
            cur = "\x00array-table"
            continue
        if cur == sect and rx.match(ln.rstrip("\r\n")):
            out.append(i)
    return out


def toml_get(data: dict, sect: Optional[str], key: str):
    node = data
    if sect is not None:
        for part in sect.split("."):
            node = node.get(part.strip().strip('"').strip("'"), {}) if isinstance(node, dict) else {}
    return node.get(key) if isinstance(node, dict) else None


def _toml_pop(data: dict, sect: Optional[str], key: str) -> None:
    node = data
    if sect is not None:
        for part in sect.split("."):
            node = node.get(part.strip().strip('"').strip("'"), {}) if isinstance(node, dict) else {}
    if isinstance(node, dict):
        node.pop(key, None)


def _toml_string(value: str) -> str:
    out = ['"']
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _dotenv_quote(value: str) -> str:
    if value and re.fullmatch(r"[A-Za-z0-9_\-./:@+=,]+", value):
        return value
    if "\n" in value or "\r" in value:
        raise FieldWriteRefused("multi-line value refused for a .env field")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------
# the verifier
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FileSnapshot:
    readable: bool
    text: str = ""
    mode: int = 0
    exists: bool = False
    sha256: str = ""


def snapshot(path: Path) -> FileSnapshot:
    p = Path(path)
    if not p.exists():
        return FileSnapshot(readable=True, exists=False)
    try:
        b = p.read_bytes()
        mode = stat.S_IMODE(p.stat().st_mode)
        return FileSnapshot(readable=True, text=b.decode("utf-8"), mode=mode, exists=True,
                            sha256=digest(b))
    except (OSError, UnicodeDecodeError):
        return FileSnapshot(readable=False)


@dataclass(frozen=True)
class FieldEquals:
    path: Path
    fmt: str
    field: str
    expected_sha256: str
    #: allow extra blank / comment lines in a dotenv file (a "# FakeService" header)
    allow_comment_inserts: bool = True
    substrate: str = SUBSTRATE

    def __post_init__(self):
        if self.fmt not in FORMATS:
            raise ValueError(f"fmt must be one of {FORMATS}")

    @classmethod
    def for_value(cls, path, fmt: str, field: str, value: str, **kw) -> "FieldEquals":
        """Build the contract from a value; only its digest is kept."""
        return cls(Path(path), fmt, field, digest(value), **kw)

    def capture(self) -> FileSnapshot:
        return snapshot(self.path)

    # .......................................................................
    def verify(self, before: FileSnapshot) -> EffectVerdict:
        lvl = CheckLevel.SYSTEM_OF_RECORD
        if not before.readable:
            return indeterminate(self.substrate, "pre-state of the target file was unreadable", lvl)
        now = snapshot(self.path)
        if not now.readable:
            return indeterminate(self.substrate, "target file unreadable at verify time", lvl)
        if not now.exists:
            return refuted(self.substrate, "target file missing", lvl, observed_count=0, expected_count=1,
                           absent=not before.exists, evidence={"exists": False})
        if self.fmt == "dotenv":
            res = self._dotenv(before, now)
        else:
            res = self._toml(before, now)
        count, value_ok, collateral, unchanged = res
        reasons = list(collateral)
        if before.exists and before.text.endswith("\n") and not now.text.endswith("\n"):
            reasons.append("trailing newline lost")
        if before.exists and now.mode & ~before.mode & 0o777:
            reasons.append(f"file mode loosened {oct(before.mode)} -> {oct(now.mode)}")
        ev = {"field_count": count, "value_matches": bool(value_ok), "pre_sha256": before.sha256,
              "post_sha256": now.sha256, "collateral": len(collateral)}
        if count != 1:
            reasons.insert(0, f"{self.field} present {count} times (expected exactly once)")
        elif not value_ok:
            reasons.insert(0, f"{self.field} does not hold the expected value")
        if reasons:
            return refuted(self.substrate, "; ".join(reasons), lvl, observed_count=count, expected_count=1,
                           absent=(count == 0 and unchanged), evidence=ev)
        return confirmed(self.substrate, f"{self.field} set exactly once; rest of file unchanged", lvl,
                         observed_count=1, expected_count=1, evidence=ev)

    # .......................................................................
    def _dotenv(self, before: FileSnapshot, now: FileSnapshot):
        vals, rest = [], []
        for ln in now.text.splitlines():
            v = dotenv_value(ln, self.field)
            (rest.append(ln) if v is None else vals.append(v))
        value_ok = len(vals) == 1 and digest(vals[0]) == self.expected_sha256
        seed_rest = [ln for ln in before.text.splitlines() if dotenv_value(ln, self.field) is None]
        bad = []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=seed_rest, b=rest, autojunk=False).get_opcodes():
            if op == "equal":
                continue
            if (op == "insert" and self.allow_comment_inserts
                    and all(x.strip() == "" or x.lstrip().startswith("#") for x in rest[j1:j2])):
                continue
            bad.append(f"collateral: {op} of pre-existing line(s) {i1 + 1}-{i2}")
        unchanged = now.text == before.text
        return len(vals), value_ok, bad, unchanged

    def _toml(self, before: FileSnapshot, now: FileSnapshot):
        sect, key = _split_field("toml", self.field)
        try:
            new = tomllib.loads(now.text)
        except tomllib.TOMLDecodeError as ex:
            return 0, False, [f"target is no longer valid TOML ({type(ex).__name__})"], False
        try:
            old = tomllib.loads(before.text) if before.exists else {}
        except tomllib.TOMLDecodeError:
            old = None
        a, b = before.text.splitlines(), now.text.splitlines()
        count = len(_toml_assign_lines(b, sect, key))
        val = toml_get(new, sect, key)
        value_ok = isinstance(val, str) and digest(val) == self.expected_sha256
        bad = []
        if old is not None:
            o2, n2 = _deep(old), _deep(new)
            _toml_pop(o2, sect, key)
            _toml_pop(n2, sect, key)
            _prune_empty(o2), _prune_empty(n2)
            if o2 != n2:
                bad.append("collateral: other TOML values changed")
        rx = _toml_key_re(key)
        sect_existed = sect is None or any(
            (m := _SECTION.match(x)) and m.group(1).strip() == sect for x in a)
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
            if op == "equal":
                continue
            removed = a[i1:i2]
            added = b[j1:j2]
            ok_removed = all(rx.match(x) for x in removed)
            ok_added = all(rx.match(x) or (not sect_existed and (x.strip() == "" or _SECTION.match(x)))
                           for x in added)
            if not (ok_removed and ok_added):
                bad.append(f"collateral: {op} of line(s) {i1 + 1}-{i2} beyond the {key} assignment")
        return count, value_ok, bad, now.text == before.text


def _deep(x):
    if isinstance(x, dict):
        return {k: _deep(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_deep(v) for v in x]
    return x


def _prune_empty(d: dict) -> None:
    for k in list(d):
        if isinstance(d[k], dict):
            _prune_empty(d[k])
            if not d[k]:
                del d[k]


# --------------------------------------------------------------------------
# the minimal-edit writer (a sink: only the enforcer should call it with a
# resolved secret)
# --------------------------------------------------------------------------

@dataclass
class WriteResult:
    path: Path
    changed: bool
    action: str               # replaced | appended | unchanged
    pre_sha256: str = ""
    post_sha256: str = ""
    pre_mode: int = 0
    notes: List[str] = field(default_factory=list)


def render_field(text: str, fmt: str, name: str, value: str, *,
                 drop_inline_comment: bool = True) -> Tuple[str, str]:
    """-> (new_text, action). Pure; refuses rather than guesses."""
    if fmt not in FORMATS:
        raise FieldWriteRefused(f"unsupported format {fmt!r}")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    if fmt == "dotenv":
        rx = _dotenv_re(name)
        hits = [i for i, ln in enumerate(lines) if rx.match(ln.rstrip("\r\n"))]
        if len(hits) > 1:
            raise FieldWriteRefused(f"{name} is assigned {len(hits)} times; refusing to guess which one")
        q = _dotenv_quote(value)
        if hits:
            i = hits[0]
            m = rx.match(lines[i].rstrip("\r\n"))
            ending = lines[i][len(lines[i].rstrip("\r\n")):] or nl
            new_line = f"{m.group('lead')}{name}{m.group('eq')}{q}"
            if lines[i].rstrip("\r\n") == new_line:
                return text, "unchanged"
            lines[i] = new_line + ending
            return "".join(lines), "replaced"
        if text and not text.endswith(("\n", "\r")):
            text += nl
        return text + f"{name}={q}{nl}", "appended"

    sect, key = _split_field("toml", name)
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as ex:
        raise FieldWriteRefused(f"target is not valid TOML ({ex})") from None
    plain = [ln.rstrip("\r\n") for ln in lines]
    hits = _toml_assign_lines(plain, sect, key)
    if len(hits) > 1:
        raise FieldWriteRefused(f"{name} assigned {len(hits)} times")
    sval = _toml_string(value)
    if hits:
        i = hits[0]
        m = _toml_key_re(key).match(plain[i])
        rest = m.group("rest")
        # only a single-line basic/literal string value is replaced in place
        sm = re.match(r'^("(?:[^"\\]|\\.)*"|\'[^\']*\')(?P<tail>\s*(?:#.*)?)$', rest)
        if not sm:
            raise FieldWriteRefused(f"{name} is not a single-line string value; refusing to rewrite it")
        tail = "" if drop_inline_comment else sm.group("tail")
        new_line = f"{m.group('lead')}{m.group('key')}{m.group('eq')}{sval}{tail}"
        if new_line == plain[i]:
            return text, "unchanged"
        ending = lines[i][len(plain[i]):] or nl
        lines[i] = new_line + ending
        return "".join(lines), "replaced"
    entry = f"{key} = {sval}{nl}"
    if sect is None:
        first = next((i for i, ln in enumerate(plain) if _SECTION.match(ln) or ln.lstrip().startswith("[[")),
                     len(plain))
        lines.insert(first, entry)
        return "".join(lines), "appended"
    # find the section; insert after its last non-blank line
    start = next((i for i, ln in enumerate(plain)
                  if (m := _SECTION.match(ln)) and m.group(1).strip() == sect), None)
    if start is None:
        if text and not text.endswith(("\n", "\r")):
            text += nl
        sep = nl if text.strip() else ""
        return text + f"{sep}[{sect}]{nl}{entry}", "appended"
    end = len(plain)
    for j in range(start + 1, len(plain)):
        if _SECTION.match(plain[j]) or plain[j].lstrip().startswith("[["):
            end = j
            break
    last = start
    for j in range(start + 1, end):
        if plain[j].strip():
            last = j
    if lines and not lines[last].endswith(("\n", "\r")):
        lines[last] += nl
    lines.insert(last + 1, entry)
    return "".join(lines), "appended"


def write_field(path, fmt: str, name: str, value: str, *, create: bool = False,
                drop_inline_comment: bool = True) -> WriteResult:
    """Set one field with a minimal edit; atomic replace; keeps the file mode.

    ``create=False`` (default) refuses a missing file: a secret landing in a
    NEW location is an owner decision, never a side effect.
    """
    p = Path(path)
    if p.is_symlink():
        raise FieldWriteRefused("target is a symlink; refusing to write through it")
    if not p.exists():
        if not create:
            raise FieldWriteRefused(f"{p.name} does not exist (create=False)")
        text, pre_mode, pre_sha = "", 0o600, ""
    else:
        raw = p.read_bytes()
        text = raw.decode("utf-8")
        pre_mode = stat.S_IMODE(p.stat().st_mode)
        pre_sha = digest(raw)
    new_text, action = render_field(text, fmt, name, value, drop_inline_comment=drop_inline_comment)
    if action == "unchanged":
        return WriteResult(p, False, action, pre_sha, pre_sha, pre_mode)
    data = new_text.encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".awos-", suffix=".tmp", dir=str(p.parent))
    try:
        os.fchmod(fd, pre_mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return WriteResult(p, True, action, pre_sha, digest(data), pre_mode)
