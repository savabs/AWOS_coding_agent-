"""Secret scan and redaction: the ONE rule set AWOS uses for credentials.

Used as a fail-closed gate before AWOS hands off a review branch
(``host/handoff.py``, which imports ``RULES`` and the env-secret logic from
here) and before ``awos fix`` pushes one (``app/fix.py``), and by
``redact()`` wherever model text is persisted (LLM call log, experience and
skill stores, hand-over notes, checkpoints).

Pure: no I/O, no git, no network. ``scan_diff(diff_text)`` returns
``Finding(path, line, rule, preview)`` records. A finding NEVER carries the
secret value: ``preview`` is at most the first 4 characters plus an ellipsis.

Key-format rules (``RULES``; specific first, one finding per span):
    private_key, putty_private_key, age_secret_key, aws_access_key,
    anthropic_api_key, openai_api_key, github_pat, github_token, gitlab_token,
    slack_token, stripe_live_key, google_api_key, npm_token,
    huggingface_token, sendgrid_api_key, twilio_key, jwt, discord_bot_token,
    telegram_bot_token, azure_storage_key, basic_auth_header,
    url_credentials.
Plus ``generic_secret_assignment`` (``password|secret|token|api_key =
"<value>"`` with an entropy threshold), which is context-dependent and so is
kept out of ``RULES``.

Suppression (``scan_diff`` / ``scan_line`` only):
    * obvious placeholders ("xxxx", "<your-key>", "example", "dummy", ...)
    * the generic rule is skipped for files under a ``tests/`` directory
    * an inline ``awos:allow-secret`` pragma on the same line waives the
      GENERIC rule only. The pragma is written by whoever authored the diff
      (possibly the model), so it never waives a known key format (AWS,
      Anthropic, GitHub, ...): those block whatever the line says. Callers
      should surface waived findings to the reviewer (``include_allowed``).
    * ``data:...;base64,`` payloads are blanked before a line is scanned
      (embedded images/fonts are base64 noise, e.g. ``eyJ...`` JSON).
    * binary, media and lock files are skipped (``skip_path``).
  The hand-off's whole-blob guard and ``redact()`` use NONE of these
  suppressions (``key_spans(text, placeholders=False)``): they are the
  backstop, so a key dressed up as a placeholder (``sk-<real>-example``),
  hidden in a data: URI or in a lock file is still caught / redacted there.

Emergency escape hatch: ``AWOS_SECRET_SCAN=0`` (see ``scan_enabled``) turns
the pattern gate off. It is for emergencies only (a false positive blocking an
urgent hand-off); the bypass is recorded in the job's warnings. It never
turns ``redact()`` off.
"""
from __future__ import annotations

import base64
import binascii
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import (Any, Callable, Dict, Iterable, List, Mapping, Match, Optional, Pattern,
                    Tuple)

PRAGMA = "awos:allow-secret"
ENV_SWITCH = "AWOS_SECRET_SCAN"
PREVIEW_CHARS = 4

GENERIC_RULE = "generic_secret_assignment"
GENERIC_MIN_LEN = 16
GENERIC_MIN_ENTROPY = 3.5   # bits per char; random base62 is ~5.9 max, ~4+ typical at 20+ chars

#: Env var names whose values are the owner's secrets (blocked / redacted
#: wherever they appear, in every encoding the hand-off checks).
SECRET_ENV_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.I)
MIN_ENV_SECRET_LEN = 8

REDACT_ERROR = "[REDACTED:redact_error]"


@dataclass(frozen=True)
class Finding:
    path: str
    line: int          # line number in the NEW file
    rule: str
    preview: str       # <= 4 chars of the match + "…"; never the value
    allowed: bool = False   # suppressed by the inline pragma (reported, not blocking)

    def __str__(self) -> str:
        tag = " [allowed by pragma]" if self.allowed else ""
        return f"{self.path}:{self.line}: {self.rule} ({self.preview}){tag}"


_B64URL = r"[A-Za-z0-9_\-]"
_END = r"(?![A-Za-z0-9_\-])"

# (rule, pattern). Order matters: earlier rules claim a span first, so
# ``sk-ant-...`` is reported as Anthropic, not OpenAI. A pattern with a
# ``secret`` group holds the secret in that group only (the rest is context:
# a URL's scheme/user/host, a header name); previews and redaction use it.
RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    # Superset of both former rule sets: handoff allowed any [A-Z ] run
    # (double spaces), secret_scan allowed digits and "BLOCK" (PGP).
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")),
    ("putty_private_key", re.compile(r"PuTTY-User-Key-File-\d+:")),
    ("age_secret_key", re.compile(r"\bAGE-SECRET-KEY-1[QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L]{58}\b")),
    ("aws_access_key", re.compile(
        r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ABIA|ACCA|A3T[A-Z0-9])[0-9A-Z]{16}\b")),
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_api_key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("gitlab_token", re.compile(r"\bgl(?:pat|dt|ptt|rt|cbt|soat)-[A-Za-z0-9_\-]{20,}")),
    ("slack_token", re.compile(r"\bxox[abposre]-[A-Za-z0-9\-]{10,}")),
    ("stripe_live_key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    ("npm_token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{34,}\b")),
    ("sendgrid_api_key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}" + _END)),
    ("twilio_key", re.compile(r"\b(?:AC|SK)[0-9a-f]{32}\b")),
    ("jwt", re.compile(r"\beyJ" + _B64URL + r"{10,}\." + _B64URL + r"{10,}\." + _B64URL
                       + r"{16,}" + _END)),
    ("discord_bot_token", re.compile(r"\b[MNO]" + _B64URL + r"{23,27}\." + _B64URL + r"{6}\."
                                     + _B64URL + r"{27,40}" + _END)),
    ("telegram_bot_token", re.compile(r"(?<![0-9])[0-9]{8,10}:" + _B64URL + r"{35}" + _END)),
    ("azure_storage_key", re.compile(r"AccountKey=(?P<secret>[A-Za-z0-9+/]{40,}={0,2})")),
    ("basic_auth_header", re.compile(
        r"(?i)\bAuthorization[\"']?\s*[:=]\s*[\"']?Basic\s+(?P<secret>[A-Za-z0-9+/]{8,}={0,2})")),
    ("url_credentials", re.compile(
        r"\b[A-Za-z][A-Za-z0-9+.\-]{1,20}://(?P<user>[^\s:/@'\"<>`]*):"
        r"(?P<secret>[^\s/@'\"<>`]+)@(?P<host>[A-Za-z0-9.\-_\[\]]+)")),
)
RULE_NAMES: Tuple[str, ...] = tuple(r for r, _ in RULES)

# Rules whose match is a header, not a value: no placeholder check applies.
_HEADER_RULES = frozenset({"private_key", "putty_private_key"})

# For redact(): the whole key block, not only its header line.
_REDACT_BLOCKS: Tuple[Tuple[str, Pattern[str]], ...] = (
    ("private_key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?"
        r"(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)")),
    ("putty_private_key", re.compile(
        r"PuTTY-User-Key-File-\d+:[\s\S]*?(?:Private-MAC:[ \t]*[0-9A-Fa-f]*|\Z)")),
)

# The name may only start where a run of name characters starts: the same
# matches as an unanchored name, without quadratic backtracking on long runs.
_GENERIC = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9_.\-])
    (?P<name>[A-Za-z0-9_.\-]*
        (?:password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key|auth[_\-]?key
          |private[_\-]?key|client[_\-]?secret|credential)
     [A-Za-z0-9_.\-]*)
    ["']?\s*(?::=|=>|==?|:)\s*
    (?P<q>["'`])(?P<val>[^"'`\s]+)(?P=q)
    """
)

# Lower-cased substrings that mark a value as a placeholder for every rule.
_PLACEHOLDERS = ("example", "xxxx", "dummy", "placeholder", "your_", "your-", "yourkey",
                 "<", ">", "redacted", "changeme", "insert", "replace_me", "replaceme")
# Extra substrings only for the generic rule (values there are free text).
_GENERIC_PLACEHOLDERS = ("fake", "test", "sample", "${", "{{", "%(", "...", "****",
                         "none", "null", "secret", "password", "token")
# Passwords in URLs / basic auth that are obviously not real.
_PLACEHOLDER_PASSWORDS = frozenset({
    "pass", "passwd", "password", "pwd", "secret", "pw", "changeme", "xxx", "***", "user",
    "username", "token", "apikey", "api_key", "key", "foo", "bar", "baz", "test", "none",
    "null", "redacted", "hunter2"})
_EXAMPLE_HOSTS = ("example.com", "example.org", "example.net", "example.invalid", "example")

# data: URIs with a base64 payload: the payload is blanked (same length, so
# offsets hold) before a diff line is scanned.
_DATA_URI = re.compile(r"(?i)\bdata:[a-z0-9.+/\-]*(?:;[a-z0-9.+=\-]+)*;base64,"
                       r"(?P<payload>[A-Za-z0-9+/_\-]+=*)")

# Paths the diff scan skips: binary/media/font/archive files (git shows them
# as "Binary files differ" anyway) and lock files (integrity hashes are
# high-entropy noise). The hand-off's whole-blob guard still scans them.
_SKIP_EXTS = frozenset("""
png jpg jpeg gif bmp ico webp tif tiff psd heic avif
mp3 mp4 m4a m4v wav ogg oga flac aac mov avi mkv webm wmv
woff woff2 ttf otf eot
zip gz tgz bz2 xz 7z rar tar jar war whl egg zst
pdf exe dll so dylib bin o a lib class pyc pyo wasm
sqlite sqlite3 db npy npz pkl pt pth onnx safetensors gguf
""".split())
_LOCK_FILES = frozenset({
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "poetry.lock", "pipfile.lock", "pdm.lock", "uv.lock", "cargo.lock", "gemfile.lock",
    "composer.lock", "go.sum", "flake.lock", "podfile.lock", "pubspec.lock", "mix.lock",
    "packages.lock.json", "gradle.lockfile", "conda-lock.yml"})


def scan_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """False only when ``AWOS_SECRET_SCAN=0`` (emergencies only)."""
    src = os.environ if env is None else env
    return str(src.get(ENV_SWITCH, "")).strip() != "0"


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def redact_preview(value: str) -> str:
    return value[:PREVIEW_CHARS] + "…"


def skip_path(path: str) -> bool:
    """True for binary/media/lock files the diff scan does not read."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name in _LOCK_FILES:
        return True
    return "." in name and name.rsplit(".", 1)[-1] in _SKIP_EXTS


def blank_data_uris(text: str) -> str:
    """``text`` with every base64 data: URI payload replaced by spaces (same length)."""
    if "base64," not in text.lower():
        return text
    return _DATA_URI.sub(lambda m: m.group(0)[:m.start("payload") - m.start()]
                         + " " * len(m.group("payload")), text)


def _is_placeholder(token: str, generic: bool = False) -> bool:
    low = token.lower()
    if any(p in low for p in _PLACEHOLDERS):
        return True
    if generic and any(p in low for p in _GENERIC_PLACEHOLDERS):
        return True
    # "sk-aaaaaaaaaaaaaaaaaaaaaaaa", "AKIA0000000000000000": not a real key.
    body = token[4:] if len(token) > 8 else token
    return len(set(body.lower())) < 5


def _placeholder_password(user: str, pw: str, host: str = "") -> bool:
    low = pw.lower()
    if not pw or low in _PLACEHOLDER_PASSWORDS or pw == user:
        return True
    if pw[0] in "$%{<*[(" or any(p in low for p in ("${", "{{", "%(", "%s", "{}")):
        return True
    if any(p in low for p in _PLACEHOLDERS):
        return True
    if set(low) <= set("x*.-_#?"):
        return True
    h = host.lower().split(":", 1)[0]
    return any(h == d or h.endswith("." + d) for d in _EXAMPLE_HOSTS)


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _check_jwt(m: Match[str]) -> bool:
    header, payload, sig = m.group(0).split(".")
    try:
        if not _b64url_decode(header).lstrip().startswith(b"{"):
            return False
    except (binascii.Error, ValueError):
        return False
    # A real signature is random; "xxxxxxxx..." / "signature" is a doc sample.
    return shannon_entropy(sig) >= 3.5 and shannon_entropy(payload) >= 3.0


def _check_url(m: Match[str]) -> bool:
    return not _placeholder_password(m.group("user"), m.group("secret"), m.group("host"))


def _check_basic(m: Match[str]) -> bool:
    try:
        raw = base64.b64decode(m.group("secret") + "=" * (-len(m.group("secret")) % 4),
                               validate=False).decode("utf-8", "strict")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return True   # undecodable: cannot prove it is fake, so it counts
    if ":" not in raw:
        return True
    user, pw = raw.split(":", 1)
    return not _placeholder_password(user, pw)


def _check_telegram(m: Match[str]) -> bool:
    tail = m.group(0).split(":", 1)[1]
    return bool(re.search(r"[A-Z]", tail) and re.search(r"[a-z]", tail))


# Structural checks run in EVERY mode (scan, blob guard, redact): they decide
# whether a match is the format at all, not whether it looks like a demo.
_CHECKS: Dict[str, Callable[[Match[str]], bool]] = {
    "jwt": _check_jwt,
    "url_credentials": _check_url,
    "basic_auth_header": _check_basic,
    "telegram_bot_token": _check_telegram,
}


def _secret_span(m: Match[str]) -> Tuple[int, int]:
    if "secret" in m.re.groupindex and m.group("secret") is not None:
        return m.span("secret")
    return m.span()


def key_spans(text: str, placeholders: bool = True) -> List[Tuple[str, int, int]]:
    """``(rule, start, end)`` of every key-format match in ``text``, one per
    span (earlier rules win), sorted by position. ``start:end`` is the secret
    itself (the ``secret`` group when the rule has one).

    ``placeholders=False`` skips the placeholder heuristics (the fail-closed
    callers: the hand-off's whole-blob guard and ``redact()``)."""
    claimed: List[Tuple[int, int]] = []
    out: List[Tuple[str, int, int]] = []
    for rule, pat in RULES:
        for m in pat.finditer(text):
            a, b = m.span()
            if any(not (b <= s or a >= e) for s, e in claimed):
                continue
            check = _CHECKS.get(rule)
            if check is not None and not check(m):
                continue
            sa, sb = _secret_span(m)
            if placeholders and rule not in _HEADER_RULES and _is_placeholder(text[sa:sb]):
                continue
            claimed.append((a, b))
            out.append((rule, sa, sb))
    out.sort(key=lambda t: t[1])
    return out


def _generic_spans(text: str, claimed: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for m in _GENERIC.finditer(text):
        val = m.group("val")
        a, b = m.start("val"), m.end("val")
        if any(not (b <= s or a >= e) for s, e in claimed):
            continue
        if len(val) < GENERIC_MIN_LEN or shannon_entropy(val) < GENERIC_MIN_ENTROPY:
            continue
        if _is_placeholder(val, generic=True):
            continue
        # Must look random: letters and digits mixed (not a sentence or path).
        if not (re.search(r"[A-Za-z]", val) and re.search(r"\d", val)):
            continue
        if "/" in val and not re.search(r"[+=]", val):
            continue  # URL / file path, not a key
        out.append((a, b))
    return out


def _in_tests_dir(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return "tests" in parts[:-1]


def scan_line_tokens(path: str, lineno: int, text: str,
                     include_allowed: bool = False) -> List[Tuple[Finding, str]]:
    """Findings for one added line, each with the matched token. The token is
    for in-process checks only (e.g. the hand-off's base-tree dedupe): never
    log, store or print it."""
    # The pragma waives only the generic rule (see the module docstring).
    allowed = PRAGMA in text.lower()
    scan = blank_data_uris(text)
    found: List[Tuple[Finding, str]] = []
    claimed: List[Tuple[int, int]] = []
    for rule, a, b in key_spans(scan, placeholders=True):
        tok = text[a:b]
        claimed.append((a, b))
        found.append((Finding(path, lineno, rule, redact_preview(tok)), tok))
    if not _in_tests_dir(path):
        for a, b in _generic_spans(scan, claimed):
            if allowed and not include_allowed:
                continue
            val = text[a:b]
            found.append((Finding(path, lineno, GENERIC_RULE, redact_preview(val), allowed), val))
    found.sort(key=lambda fv: fv[0].rule)
    return found


def scan_line(path: str, lineno: int, text: str,
              include_allowed: bool = False) -> List[Finding]:
    """Findings for one added line."""
    return [f for f, _ in scan_line_tokens(path, lineno, text, include_allowed)]


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _diff_path(header: str) -> Optional[str]:
    """Path from a ``+++ b/path`` line; None for /dev/null."""
    p = header[4:].rstrip("\n").split("\t", 1)[0]
    if p.startswith('"') and p.endswith('"') and len(p) >= 2:
        p = p[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape", "replace")
        try:
            p = p.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    if p == "/dev/null":
        return None
    return p[2:] if p.startswith(("b/", "w/", "i/")) else p


def added_lines(diff_text: str) -> Iterable[Tuple[str, int, str]]:
    """Yield ``(path, new_line_number, text)`` for every added line.

    Hunk line counts are tracked, so an added line whose content starts with
    ``++`` is never mistaken for a ``+++`` file header."""
    path: Optional[str] = None
    old_rem = new_rem = 0
    new_line = 0
    for raw in diff_text.splitlines():
        if old_rem > 0 or new_rem > 0:
            tag, body = raw[:1], raw[1:]
            if tag == "+":
                new_rem -= 1
                if path is not None:
                    yield path, new_line, body
                new_line += 1
            elif tag == "-":
                old_rem -= 1
            elif tag == "\\":
                pass  # "\ No newline at end of file"
            else:  # context (" " or an empty line from a stripped trailing space)
                old_rem -= 1
                new_rem -= 1
                new_line += 1
            continue
        if raw.startswith("+++ "):
            path = _diff_path(raw)
        elif raw.startswith("diff --git "):
            path = None
        else:
            m = _HUNK.match(raw)
            if m:
                old_rem = int(m.group(2)) if m.group(2) is not None else 1
                new_rem = int(m.group(4)) if m.group(4) is not None else 1
                new_line = int(m.group(3))


def scan_diff_tokens(diff_text: str,
                     include_allowed: bool = False) -> List[Tuple[Finding, str]]:
    """``scan_diff`` with each finding's matched token (in-process use only)."""
    out: List[Tuple[Finding, str]] = []
    for path, lineno, text in added_lines(diff_text):
        if skip_path(path):
            continue
        out.extend(scan_line_tokens(path, lineno, text, include_allowed))
    return out


def scan_diff(diff_text: str, include_allowed: bool = False) -> List[Finding]:
    """Scan the added lines of a unified diff. Blocking findings only, unless
    ``include_allowed`` (then pragma-suppressed ones come back with
    ``allowed=True``)."""
    return [f for f, _ in scan_diff_tokens(diff_text, include_allowed)]


def blocking(findings: Iterable[Finding]) -> List[Finding]:
    return [f for f in findings if not f.allowed]


def describe(findings: List[Finding], limit: int = 10) -> str:
    """One line for a job report: ``path:line rule (prev…)``, capped."""
    shown = "; ".join(f"{f.path}:{f.line} {f.rule} ({f.preview})" for f in findings[:limit])
    more = len(findings) - limit
    return shown + (f"; +{more} more" if more > 0 else "")


# --------------------------------------------------------------------------- #
# Owner env secrets and redaction
# --------------------------------------------------------------------------- #
def env_secret_values(env: Optional[Mapping[str, str]] = None) -> List[Tuple[str, str]]:
    """``(name, value)`` of the owner's secret env vars: a name matching
    ``SECRET_ENV_NAME`` and a value of at least ``MIN_ENV_SECRET_LEN`` chars.
    ``env=None`` reads ``os.environ``."""
    src = os.environ if env is None else env
    out = []
    for name, val in list(src.items()):
        if (isinstance(val, str) and len(val) >= MIN_ENV_SECRET_LEN
                and SECRET_ENV_NAME.search(str(name))):
            out.append((str(name), val))
    return out


def _env_pairs(env: Optional[Mapping[str, str]]) -> List[Tuple[str, str]]:
    # Longest value first: a value that is a prefix of another must not leave
    # the longer value's suffix behind.
    return sorted(env_secret_values(env), key=lambda kv: -len(kv[1]))


def _redact_with(text: str, pairs: List[Tuple[str, str]]) -> str:
    for var, val in pairs:
        if val in text:
            text = text.replace(val, f"[REDACTED:{var}]")
    spans: List[Tuple[int, int, str]] = []
    for rule, pat in _REDACT_BLOCKS:
        for m in pat.finditer(text):
            if all(m.end() <= s or m.start() >= e for s, e, _ in spans):
                spans.append((m.start(), m.end(), rule))
    keys = [(a, b, r) for r, a, b in key_spans(text, placeholders=False)
            if all(b <= s or a >= e for s, e, _ in spans)]
    spans += keys
    spans += [(a, b, GENERIC_RULE) for a, b in _generic_spans(text, [(s, e) for s, e, _ in spans])]
    if not spans:
        return text
    spans.sort()
    parts: List[str] = []
    pos = 0
    for a, b, rule in spans:
        if a < pos:
            continue
        parts.append(text[pos:a])
        parts.append(f"[REDACTED:{rule}]")
        pos = b
    parts.append(text[pos:])
    return "".join(parts)


def redact(text: Any, env: Optional[Mapping[str, str]] = None) -> str:
    """``text`` with every credential replaced: the owner's secret env values
    by ``[REDACTED:<VAR_NAME>]``, key formats (``RULES``, whole private-key
    blocks) and high-entropy ``password = "..."`` assignments by
    ``[REDACTED:<rule>]``.

    ``env=None`` uses ``os.environ``; pass ``{}`` to skip env values. No
    placeholder heuristics (over-redacting a demo key is harmless) and no
    ``AWOS_SECRET_SCAN`` bypass. Non-str input is ``str()``-ed, ``None``
    becomes ``""``. Never raises: on an internal error the whole text is
    replaced by ``[REDACTED:redact_error]`` (fail closed)."""
    try:
        if text is None:
            return ""
        if not isinstance(text, str):
            text = str(text)
        if not text:
            return text
        return _redact_with(text, _env_pairs(env))
    except Exception:
        return REDACT_ERROR


def redact_obj(obj: Any, env: Optional[Mapping[str, str]] = None) -> Any:
    """``redact`` applied to every string in a JSON-able structure (dict
    values, list/tuple items; keys are kept), returning a new structure.
    Redact *before* serialising, so a value with quotes or backslashes
    matches in its raw form. Never raises (fail closed per string)."""
    try:
        pairs = _env_pairs(env)
    except Exception:
        return REDACT_ERROR

    def walk(o: Any, depth: int = 0) -> Any:
        if depth > 64:
            return REDACT_ERROR
        if isinstance(o, str):
            try:
                return _redact_with(o, pairs) if o else o
            except Exception:
                return REDACT_ERROR
        if isinstance(o, dict):
            return {k: walk(v, depth + 1) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [walk(v, depth + 1) for v in o]
        return o

    try:
        return walk(obj)
    except Exception:
        return REDACT_ERROR
