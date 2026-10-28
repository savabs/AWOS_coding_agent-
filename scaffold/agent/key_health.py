"""
key_health.py — check every configured model provider without spending.

Each check is a free, read-only request:

    anthropic    GET {ANTHROPIC_BASE_URL or https://api.anthropic.com}/v1/models
    local        GET {AWOS_LOCAL_BASE_URL minus /v1}/health   (llama-server)
    openrouter   GET https://openrouter.ai/api/v1/key          (only when a key is set)

and is classified as one of

    ok              the provider answered and accepted the credential
    unauthorized    401/403: the key is wrong, revoked or out of credit
    unreachable     no answer (offline, DNS, timeout, 5xx)
    not-configured  no key (or, for local, no server and no URL set)

Key values are never printed, logged or returned: a check carries only the
env var's name and a short fingerprint-free hint ("set", "empty").

`awos keys` prints the table. The host worker (scaffold/agent/host/worker.py)
calls `credentials_problem()` before it takes a job and after an auth error,
and parks the job in "blocked" (reason: credentials) instead of failing it.

Spec: docs/specs/supervision_autopilot.md.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

try:
    from scaffold.agent import paths as _paths
except ImportError:
    import paths as _paths  # type: ignore

REPO = _paths.CODE_ROOT

OK, UNAUTHORIZED, UNREACHABLE, NOT_CONFIGURED = "ok", "unauthorized", "unreachable", "not-configured"

DEFAULT_LOCAL_BASE = "http://127.0.0.1:8080/v1"
ANTHROPIC_VERSION = "2023-06-01"

#: Text in a child's error or log that means "the provider rejected the key".
AUTH_ERROR_RE = re.compile(
    r"invalid x-api-key|authentication_error|AuthenticationError|PermissionDeniedError"
    r"|permission_error|invalid api key|incorrect api key|api key not valid"
    r"|no auth credentials|user not found"
    r"|\b(?:error code|status(?: code)?|http)[:= ]*(?:401|403)\b"
    r"|\b(?:401|403)\b[^\n]{0,40}(?:unauthori[sz]ed|forbidden|auth|api key)",
    re.I)


@dataclass
class KeyCheck:
    provider: str
    status: str
    env_var: str          # which variable holds the credential (name only)
    detail: str           # human hint; never contains a key
    action: str = ""      # the one thing the owner must do, when not ok
    http_status: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── env: what a job child would see ───────────────────────────────────────────


def _dotenv_values(path: Optional[Path] = None) -> dict:
    path = path or _paths.dotenv_path()
    out: dict = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not m:
            continue
        val = m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        out[m.group(1)] = val
    return out


def effective_env(env: Optional[dict] = None, dotenv_path: Optional[Path] = None) -> dict:
    """
    The env a job child ends up with: `env` (default os.environ) wins, and the
    repo .env fills only names that are absent (dotenv override=False). A name
    present but empty stays empty (that is how a local_only job blanks keys).
    """
    base = dict(os.environ if env is None else env)
    for k, v in _dotenv_values(dotenv_path).items():
        base.setdefault(k, v)
    return base


# ── HTTP (one place to mock) ──────────────────────────────────────────────────


def http_get(url: str, headers: dict, timeout: float) -> tuple[Optional[int], str]:
    """(status, short body) — status None when the server could not be reached."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(2000).decode("utf-8", "replace")
        except Exception:
            body = ""
        return exc.code, body
    except Exception as exc:  # URLError, timeout, connection reset, ...
        return None, type(exc).__name__


def _timeout() -> float:
    try:
        return float(os.getenv("AWOS_KEY_CHECK_TIMEOUT_SEC", "6"))
    except ValueError:
        return 6.0


def _classify(provider: str, env_var: str, status: Optional[int], body: str,
              fix: str) -> KeyCheck:
    if status is None:
        return KeyCheck(provider, UNREACHABLE, env_var, f"no answer ({body})",
                        "check the network (or that the provider is up)")
    if status in (401, 403):
        return KeyCheck(provider, UNAUTHORIZED, env_var, f"HTTP {status}: key rejected",
                        fix, status)
    if status < 400 or status == 429:  # 429: rate limited, but the key is accepted
        return KeyCheck(provider, OK, env_var,
                        f"HTTP {status}" + (" (rate limited)" if status == 429 else ""),
                        "", status)
    return KeyCheck(provider, UNREACHABLE, env_var, f"HTTP {status}",
                    "provider error; retry later", status)


# ── provider checks ──────────────────────────────────────────────────────────


def check_anthropic(env: dict, get=http_get) -> KeyCheck:
    var = "ANTHROPIC_API_KEY"
    key = (env.get(var) or "").strip()
    if not key:
        return KeyCheck("anthropic", NOT_CONFIGURED, var,
                        "empty" if var in env else "not set",
                        f"set {var} in .env")
    base = (env.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    status, body = get(f"{base}/v1/models?limit=1",
                       {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
                       _timeout())
    return _classify("anthropic", var, status, body,
                     f"replace {var} in .env (console.anthropic.com > API keys)")


def check_openrouter(env: dict, get=http_get) -> KeyCheck:
    var = "OPENROUTER_API_KEY"
    key = (env.get(var) or "").strip()
    if not key:
        return KeyCheck("openrouter", NOT_CONFIGURED, var,
                        "empty" if var in env else "not set (off)", "")
    status, body = get("https://openrouter.ai/api/v1/key",
                       {"Authorization": f"Bearer {key}"}, _timeout())
    return _classify("openrouter", var, status, body,
                     f"replace {var} in .env (openrouter.ai/keys), or remove it")


def local_base(env: dict) -> tuple[str, bool]:
    """(base url, explicitly configured?) — same precedence as providers.local_base_url."""
    for var in ("AWOS_LOCAL_BASE_URL", "AWOS_BASE_URL"):
        val = (env.get(var) or "").strip()
        if val:
            return val, True
    return DEFAULT_LOCAL_BASE, False


def check_local(env: dict, get=http_get) -> KeyCheck:
    base, configured = local_base(env)
    root = re.sub(r"/v1/?$", "", base.rstrip("/"))
    headers = {}
    local_key = (env.get("AWOS_LOCAL_API_KEY") or env.get("LOCAL_API_KEY") or "").strip()
    if local_key:
        headers["Authorization"] = f"Bearer {local_key}"
    status, body = get(f"{root}/health", headers, min(_timeout(), 3.0))
    if status is None and not configured:
        return KeyCheck("local", NOT_CONFIGURED, "AWOS_LOCAL_BASE_URL",
                        f"no server at {root}", "")
    check = _classify("local", "AWOS_LOCAL_API_KEY", status, body,
                      "fix AWOS_LOCAL_API_KEY (the llama-server --api-key)")
    if check.status == UNREACHABLE:
        check.env_var = "AWOS_LOCAL_BASE_URL"
        check.detail = f"{root}: {check.detail}"
        check.action = "start the local model server (scripts/local_model.sh)"
    elif status == 503:  # llama-server: model still loading
        check.detail = "loading"
    return check


CHECKS: dict[str, Callable[..., KeyCheck]] = {
    "anthropic": check_anthropic,
    "local": check_local,
    "openrouter": check_openrouter,
}


def check_all(env: Optional[dict] = None, get=http_get,
              providers: Optional[list] = None) -> list[KeyCheck]:
    env = effective_env(env)
    names = providers or list(CHECKS)
    return [CHECKS[n](env, get=get) for n in names]


# ── what the host worker asks ─────────────────────────────────────────────────


def providers_for(privacy: str) -> list[str]:
    """A local_only job may only use the local server; a cloud_ok job uses the clouds."""
    return ["local"] if privacy == "local_only" else ["anthropic", "openrouter", "local"]


def credentials_problem(checks: list[KeyCheck], privacy: str = "cloud_ok") -> Optional[dict]:
    """
    None when the job can authenticate, else {"provider", "status", "action",
    "summary"}. Blocking rules (credentials only; a network outage is not a
    credentials block):
      - any configured provider the job may use answers 401/403, or
      - a cloud_ok job has no usable provider at all (every one not-configured).
    """
    allowed = set(providers_for(privacy))
    usable = [c for c in checks if c.provider in allowed]
    for c in usable:
        if c.status == UNAUTHORIZED:
            return {"provider": c.provider, "status": c.status, "action": c.action,
                    "summary": f"{c.provider} key rejected ({c.detail}); {c.action}"}
    if privacy != "local_only" and usable and all(c.status == NOT_CONFIGURED for c in usable):
        return {"provider": "any", "status": NOT_CONFIGURED,
                "action": "export ANTHROPIC_API_KEY (or put it in .env), or set AWOS_PROVIDER=local",
                "summary": "no model provider is configured; export ANTHROPIC_API_KEY "
                           "or set AWOS_PROVIDER=local"}
    return None


def looks_like_auth_error(text: str) -> bool:
    return bool(text and AUTH_ERROR_RE.search(text))


# ── CLI ───────────────────────────────────────────────────────────────────────


def format_table(checks: list[KeyCheck]) -> str:
    lines = [f"  {'provider':<11} {'status':<15} {'credential':<20} detail"]
    for c in checks:
        lines.append(f"  {c.provider:<11} {c.status:<15} {c.env_var:<20} {c.detail}")
        if c.status not in (OK, NOT_CONFIGURED) and c.action:
            lines.append(f"  {'':<11} -> {c.action}")
    return "\n".join(lines)


def main(as_json: bool = False) -> int:
    checks = check_all()
    if as_json:
        print(json.dumps([c.to_dict() for c in checks], indent=2))
    else:
        print(format_table(checks))
    bad = credentials_problem(checks)
    if bad and not as_json:
        if bad.get("status") == NOT_CONFIGURED:
            print("\n" + _paths.no_provider_message())
        else:
            print(f"\n{bad['summary']}")
    return 1 if bad else 0
