"""config.py — the pack's sources file (``~/.awos/pack/sources.toml``).

    [pack]
    root      = "~/.awos/pack/Pack"      # Pack/<YYYY-MM>/<source-id>/<file>
    downloads = "~/Downloads"            # the sandbox VM's browser download folder
    settle_s  = 2.0                      # how long a download must keep its size to count as done
    wait_s    = 120                      # how long to wait for partial downloads to finish

    [[source]]
    id              = "hdfc-current"                 # [a-z0-9][a-z0-9_-]*, unique
    name            = "HDFC current account"
    kind            = "statement"                    # invoice | statement | gateway_report | tax_doc
    start_url       = "https://netbanking.hdfcbank.com/netbanking/"
    allowed_domains = ["netbanking.hdfcbank.com"]    # exact hosts; "*.example.com" for subdomains
    cadence         = "monthly"                      # monthly | quarterly
    expected        = { count = 1, types = ["pdf"] }
    naming          = "{source}_{period}{n}.{ext}"   # see files.render_name
    password_hint   = "hdfc_statement_pdf"           # keychain ITEM NAME, never the password
    skill           = "hdfc_statement_download"      # the taught recording to replay
    notes           = "Statement PDF is locked with customer id"
    enabled         = true

URLs must be https; plain http is accepted only for 127.0.0.1 / localhost (tests and
the local testbed). Every problem is collected and reported at once (ConfigError.problems).
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

DEFAULT_SOURCES_FILE = "~/.awos/pack/sources.toml"
DEFAULT_ROOT = "~/.awos/pack/Pack"
DEFAULT_DOWNLOADS = "~/Downloads"
ENV_SOURCES = "AWOS_PACK_SOURCES"

KINDS = ("invoice", "statement", "gateway_report", "tax_doc")
CADENCES = ("monthly", "quarterly")
QUARTER_END_MONTHS = (3, 6, 9, 12)          # Indian FY quarters (Apr-Jun, ...) end on the same months
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
ITEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")
HOST_RE = re.compile(r"^(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
BROAD_SUFFIXES = ("co.in", "gov.in", "co.uk", "com.au", "net.in", "org.in")
TYPE_RE = re.compile(r"^[a-z0-9]{1,8}$")
TEMPLATE_FIELDS = ("source", "kind", "period", "yyyy", "mm", "name", "ext", "n", "sha8")
DEFAULT_NAMING = "{source}_{period}{n}.{ext}"

SOURCE_KEYS = {"id", "name", "kind", "start_url", "allowed_domains", "cadence", "expected", "naming",
               "password_hint", "skill", "notes", "enabled"}
PACK_KEYS = {"root", "downloads", "settle_s", "wait_s"}
# Keys that would mean a secret was written into the file. Refused outright.
SECRET_KEYS = {"password", "passwd", "pass", "pin", "otp", "secret", "token", "api_key", "apikey"}


class ConfigError(ValueError):
    """sources.toml problems, all of them, each one actionable."""

    def __init__(self, problems: List[str], path: Optional[Path] = None):
        self.problems = list(problems)
        self.path = path
        where = f"{path}: " if path else ""
        super().__init__(where + ("; ".join(self.problems) if len(self.problems) < 4
                                  else f"{len(self.problems)} problems:\n  - " + "\n  - ".join(self.problems)))


@dataclass(frozen=True)
class Expected:
    count: int = 1
    types: Tuple[str, ...] = ("pdf",)


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    kind: str
    start_url: str
    allowed_domains: Tuple[str, ...]
    cadence: str = "monthly"
    expected: Expected = Expected()
    naming: str = DEFAULT_NAMING
    password_hint: Optional[str] = None
    skill: Optional[str] = None
    notes: str = ""
    enabled: bool = True

    def summary(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "kind": self.kind, "cadence": self.cadence,
                "start_url": self.start_url, "allowed_domains": list(self.allowed_domains),
                "expected": {"count": self.expected.count, "types": list(self.expected.types)},
                "naming": self.naming, "password_hint": self.password_hint, "skill": self.skill,
                "enabled": self.enabled, "notes": self.notes}


@dataclass
class PackConfig:
    root: Path
    downloads: Path
    sources: List[Source]
    settle_s: float = 2.0
    wait_s: float = 120.0
    path: Optional[Path] = None
    warnings: List[str] = field(default_factory=list)

    def source(self, sid: str) -> Source:
        for s in self.sources:
            if s.id == sid:
                return s
        raise ConfigError([f"no source with id {sid!r} (have: {', '.join(s.id for s in self.sources) or 'none'})"],
                          self.path)


# ── URL / host rules (shared with run.domain_policy) ────────────────────────
def host_matches(host: str, allowed: Tuple[str, ...]) -> bool:
    host = (host or "").lower().rstrip(".")
    for a in allowed:
        if a.startswith("*."):
            base = a[2:]
            if host == base or host.endswith("." + base):
                return True
        elif host == a:
            return True
    return False


def url_problem(url: str, allowed: Tuple[str, ...]) -> Optional[str]:
    """None if ``url`` may be visited under ``allowed``; else why not."""
    try:
        u = urlsplit(str(url))
        host = (u.hostname or "").lower()
    except ValueError:
        return f"{url!r} is not a URL"
    if not host:
        return f"{url!r} has no host"
    if u.username or u.password:
        return f"{url!r} carries credentials in the URL; remove them"
    if u.scheme == "https":
        pass
    elif u.scheme == "http" and host in LOOPBACK_HOSTS:
        pass
    else:
        return f"{url!r} must be https (plain http only for 127.0.0.1/localhost)"
    if not host_matches(host, allowed):
        return f"host {host!r} of {url!r} is not in allowed_domains {list(allowed)}"
    return None


# ── parsing ─────────────────────────────────────────────────────────────────
def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(p))))


def check_template(tpl: str) -> Optional[str]:
    if not isinstance(tpl, str) or not tpl.strip():
        return "naming must be a non-empty string"
    if "/" in tpl or "\\" in tpl or ".." in tpl:
        return f"naming {tpl!r} must be a file name (no '/', '\\' or '..')"
    for m in re.finditer(r"\{([^{}]*)\}", tpl):
        if m.group(1) not in TEMPLATE_FIELDS:
            return f"naming {tpl!r}: unknown field {{{m.group(1)}}} (use {', '.join(TEMPLATE_FIELDS)})"
    if "{ext}" not in tpl:
        return f"naming {tpl!r} must contain {{ext}} so the file keeps its type"
    return None


def _source(raw: Any, i: int, problems: List[str], warnings: List[str]) -> Optional[Source]:
    where = f"source #{i + 1}"
    if not isinstance(raw, dict):
        problems.append(f"{where}: must be a table ([[source]])")
        return None
    sid = raw.get("id")
    if isinstance(sid, str):
        where = f"source {sid!r}"
    n0 = len(problems)
    secret = sorted(k for k in raw if str(k).lower() in SECRET_KEYS)
    if secret:
        problems.append(f"{where}: {', '.join(secret)} looks like a secret -- never put passwords/OTPs in "
                        "this file; store them in the keychain and set password_hint to the item name")
    unknown = sorted(set(raw) - SOURCE_KEYS - set(secret))
    if unknown:
        problems.append(f"{where}: unknown key(s) {', '.join(unknown)} (allowed: {', '.join(sorted(SOURCE_KEYS))})")
    if not isinstance(sid, str) or not ID_RE.match(sid):
        problems.append(f"{where}: id must match {ID_RE.pattern} (lowercase letters, digits, '-', '_'), got {sid!r}")
    name = raw.get("name", sid)
    if not isinstance(name, str) or not name.strip():
        problems.append(f"{where}: name must be a non-empty string")
    kind = raw.get("kind")
    if kind not in KINDS:
        problems.append(f"{where}: kind must be one of {', '.join(KINDS)}, got {kind!r}")
    cadence = raw.get("cadence", "monthly")
    if cadence not in CADENCES:
        problems.append(f"{where}: cadence must be one of {', '.join(CADENCES)}, got {cadence!r}")

    domains = raw.get("allowed_domains")
    doms: Tuple[str, ...] = ()
    if not isinstance(domains, list) or not domains or not all(isinstance(d, str) for d in domains):
        problems.append(f"{where}: allowed_domains must be a non-empty list of host names "
                        "(e.g. [\"billing.example.com\"])")
    else:
        doms = tuple(d.strip().lower().rstrip(".") for d in domains)
        for d in doms:
            if "://" in d or "/" in d or ":" in d:
                problems.append(f"{where}: allowed_domains entry {d!r} must be a bare host (no scheme, path or port)")
            elif not HOST_RE.match(d):
                problems.append(f"{where}: allowed_domains entry {d!r} is not a host name")
            elif d.startswith("*.") and ("." not in d[2:] or d[2:] in BROAD_SUFFIXES):
                problems.append(f"{where}: allowed_domains entry {d!r} is too broad")
    url = raw.get("start_url")
    if not isinstance(url, str) or not url:
        problems.append(f"{where}: start_url is required")
    elif doms:
        why = url_problem(url, doms)
        if why:
            problems.append(f"{where}: start_url {why}")

    exp_raw = raw.get("expected", {})
    expected = Expected()
    if not isinstance(exp_raw, dict):
        problems.append(f"{where}: expected must be a table like {{ count = 1, types = [\"pdf\"] }}")
    else:
        bad = set(exp_raw) - {"count", "types"}
        if bad:
            problems.append(f"{where}: expected has unknown key(s) {', '.join(sorted(bad))}")
        count = exp_raw.get("count", 1)
        types = exp_raw.get("types", ["pdf"])
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 50:
            problems.append(f"{where}: expected.count must be a whole number 1..50, got {count!r}")
            count = 1
        if (not isinstance(types, list) or not types
                or not all(isinstance(t, str) and TYPE_RE.match(t.lower().lstrip(".")) for t in types)):
            problems.append(f"{where}: expected.types must be a list of file extensions like [\"pdf\", \"csv\"]")
            types = ["pdf"]
        expected = Expected(count, tuple(t.lower().lstrip(".") for t in types))

    naming = raw.get("naming", DEFAULT_NAMING)
    why = check_template(naming)
    if why:
        problems.append(f"{where}: {why}")
    elif expected.count > 1 and "{n}" not in naming and "{name}" not in naming and "{sha8}" not in naming:
        warnings.append(f"{where}: expects {expected.count} files but naming has no {{n}}/{{name}}/{{sha8}}; "
                        "later files get ' (2)' suffixes")

    hint = raw.get("password_hint")
    if hint is not None and (not isinstance(hint, str) or not ITEM_RE.match(hint)):
        problems.append(f"{where}: password_hint must be a keychain item NAME (letters, digits, _.@-; "
                        "no spaces), never the password itself")
    skill = raw.get("skill")
    if skill is not None and (not isinstance(skill, str) or not skill.strip()):
        problems.append(f"{where}: skill must be the id of a taught recording")
    if skill is None:
        warnings.append(f"{where}: no skill taught yet -- runs will report it as missing until you teach one "
                        "or drop the file into the pack folder yourself")
    notes = raw.get("notes", "")
    if not isinstance(notes, str):
        problems.append(f"{where}: notes must be a string")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        problems.append(f"{where}: enabled must be true or false")
    if len(problems) > n0:
        return None
    return Source(id=sid, name=name.strip(), kind=kind, start_url=url, allowed_domains=doms, cadence=cadence,
                  expected=expected, naming=naming, password_hint=hint, skill=skill, notes=notes, enabled=enabled)


def parse_config(data: Dict[str, Any], path: Optional[Path] = None, *,
                 root: Optional[str] = None, downloads: Optional[str] = None) -> PackConfig:
    problems: List[str] = []
    warnings: List[str] = []
    top_unknown = sorted(set(data) - {"pack", "source"})
    if top_unknown:
        problems.append(f"unknown top-level key(s) {', '.join(top_unknown)} (use [pack] and [[source]])")
    pack = data.get("pack", {})
    if not isinstance(pack, dict):
        problems.append("[pack] must be a table")
        pack = {}
    bad = sorted(set(pack) - PACK_KEYS)
    if bad:
        problems.append(f"[pack]: unknown key(s) {', '.join(bad)} (allowed: {', '.join(sorted(PACK_KEYS))})")
    settle_s, wait_s = pack.get("settle_s", 2.0), pack.get("wait_s", 120.0)
    for k, v in (("settle_s", settle_s), ("wait_s", wait_s)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v > 3600:
            problems.append(f"[pack]: {k} must be a number of seconds 0..3600, got {v!r}")
    raw_sources = data.get("source", [])
    if not isinstance(raw_sources, list):
        problems.append("source must be an array of tables ([[source]])")
        raw_sources = []
    sources: List[Source] = []
    seen: Dict[str, int] = {}
    for i, raw in enumerate(raw_sources):
        s = _source(raw, i, problems, warnings)
        if s is None:
            continue
        if s.id in seen:
            problems.append(f"source {s.id!r}: duplicate id (also source #{seen[s.id] + 1})")
            continue
        seen[s.id] = i
        sources.append(s)
    if not raw_sources:
        warnings.append("no [[source]] entries yet")
    if problems:
        raise ConfigError(problems, path)
    return PackConfig(root=_expand(root or pack.get("root", DEFAULT_ROOT)),
                      downloads=_expand(downloads or pack.get("downloads", DEFAULT_DOWNLOADS)),
                      sources=sources, settle_s=float(settle_s), wait_s=float(wait_s), path=path, warnings=warnings)


def sources_path(path: Optional[str] = None) -> Path:
    return _expand(path or os.environ.get(ENV_SOURCES) or DEFAULT_SOURCES_FILE)


def load_config(path: Optional[str] = None, *, root: Optional[str] = None,
                downloads: Optional[str] = None) -> PackConfig:
    p = sources_path(path)
    if not p.is_file():
        raise ConfigError(["sources file not found; create it (see docs/specs/doc_pack_v0.md) or pass --sources"], p)
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError([f"not valid TOML: {e}"], p) from None
    return parse_config(data, p, root=root, downloads=downloads)
