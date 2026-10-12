"""
paths.py — where AWOS code lives vs. where AWOS state lives.

A source checkout keeps its state where it always has: <repo>/.awos and
<repo>/.env. An installed copy (pip / pipx: the code sits in site-packages)
must never write inside site-packages, so its state lives under AWOS_HOME
(default ~/.awos):

    ~/.awos/.env         optional key file (exported env vars always win)
    ~/.awos/host/        host queue (AWOS_HOST_DIR; same default in both modes)
    ~/.awos/runtime/     the job child's working dir; its relative .awos/
                         (spend ledger budget.json, memory, ...) lands here

Overrides: AWOS_HOME moves all of it; AWOS_STATE_DIR moves only the runtime dir.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The directory that contains the `scaffold` package (repo root in a checkout,
#: site-packages when installed).
CODE_ROOT = Path(__file__).resolve().parents[2]

#: Models a fresh user gets when only ANTHROPIC_API_KEY is configured.
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
MODEL_ENV_VARS = ("AWOS_AGENT_MODEL", "AWOS_PLANNER_MODEL", "AWOS_NOTEBOOK_MODEL", "AWOS_LADDER")


def is_installed() -> bool:
    """True when running from an installed wheel (code in site-/dist-packages)."""
    return CODE_ROOT.name in ("site-packages", "dist-packages")


def awos_home() -> Path:
    raw = os.getenv("AWOS_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".awos"


def state_root() -> Path:
    """
    The directory whose `.awos/` holds runtime state, and the job child's cwd.
    Checkout: the repo root (unchanged behaviour). Installed: ~/.awos/runtime.
    """
    raw = os.getenv("AWOS_STATE_DIR", "").strip()
    if raw:
        root = Path(raw).expanduser()
    elif is_installed():
        root = awos_home() / "runtime"
    else:
        return CODE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def dotenv_path() -> Path:
    """The .env AWOS loads (never overriding exported vars)."""
    return awos_home() / ".env" if is_installed() else CODE_ROOT / ".env"


def apply_model_defaults(env: "os._Environ | dict | None" = None) -> bool:
    """
    Fresh-user default: with ANTHROPIC_API_KEY set, no AWOS_PROVIDER pin and no
    AWOS_* model variable set, run everything on claude-haiku-5-5.
    Returns True when the defaults were applied.
    """
    env = os.environ if env is None else env
    if not (env.get("ANTHROPIC_API_KEY") or "").strip():
        return False
    if (env.get("AWOS_PROVIDER") or "").strip():
        return False
    if any((env.get(k) or "").strip() for k in MODEL_ENV_VARS):
        return False
    for k in MODEL_ENV_VARS:
        env[k] = DEFAULT_ANTHROPIC_MODEL
    return True


def no_provider_message() -> str:
    where = dotenv_path()
    return (
        "No model provider is configured.\n"
        "  Cloud (recommended):  export ANTHROPIC_API_KEY=sk-ant-...\n"
        f"                        (or put ANTHROPIC_API_KEY=... in {where})\n"
        "  Local model:          export AWOS_PROVIDER=local  "
        "(OpenAI-compatible server at AWOS_LOCAL_BASE_URL, default http://127.0.0.1:8080/v1)\n"
        "Then run `awos keys` to check."
    )


def provider_configured(env: "os._Environ | dict | None" = None) -> bool:
    """Any credential/pin a job could run on (cheap, offline check)."""
    env = os.environ if env is None else env
    if (env.get("AWOS_PROVIDER") or "").strip().lower() == "local":
        return True
    if (env.get("AWOS_AGENT_MODEL") or "").strip().lower().startswith("local/"):
        return True
    keys = ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
            "OPENAI_API_KEY", "OPENCODE_GO_API_KEY")
    return any((env.get(k) or "").strip() for k in keys)
