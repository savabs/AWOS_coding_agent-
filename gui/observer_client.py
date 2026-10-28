"""Client for the authenticated local Electron observer-control API."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_TOKEN_FILE = Path(__file__).resolve().parents[1] / ".awos" / "observer_token"


class ControlClientError(RuntimeError):
    """A local control request failed or returned an invalid result."""


def load_control_token(token_file: str | None = None) -> str:
    """Load the token value without ever printing it."""
    path = Path(token_file or os.environ.get("AWOS_CONTROL_TOKEN_FILE", str(DEFAULT_TOKEN_FILE)))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        token = payload.get("token")
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlClientError(f"cannot read control token file: {path}") from exc
    if not isinstance(token, str) or not token:
        raise ControlClientError(f"control token file has no valid token: {path}")
    return token


def _read_response(response: Any) -> dict[str, Any]:
    try:
        body = response.read().decode("utf-8")
        payload = json.loads(body)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlClientError("control server returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ControlClientError("control server returned a non-object response")
    return payload


def send_control(
    action: str,
    *,
    target: str | None = None,
    value: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8766,
    token_file: str | None = None,
    token: str | None = None,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """Send one bounded semantic control request to Electron."""
    payload: dict[str, Any] = {"action": action}
    if target is not None:
        payload["target"] = target
    if value is not None:
        payload["value"] = value
    body = json.dumps(payload).encode("utf-8")
    if len(body) > 16 * 1024:
        raise ControlClientError("control request exceeds 16 KiB")

    request = Request(
        f"http://{host}:{port}/api/control",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-AWOS-CONTROL-TOKEN": token or load_control_token(token_file),
        },
    )
    try:
        response = opener(request, timeout=5)
        result = _read_response(response)
    except HTTPError as exc:
        try:
            result = _read_response(exc)
        except ControlClientError:
            result = {"status": "error", "error": f"HTTP {exc.code}"}
    except (OSError, URLError) as exc:
        raise ControlClientError(f"cannot reach Electron control server: {exc}") from exc

    if result.get("status") != "ok":
        error = result.get("error", "control request failed")
        raise ControlClientError(str(error))
    return result


def verify_control_result(
    response: dict[str, Any],
    action: str,
    *,
    target: str | None = None,
    value: str | None = None,
) -> dict[str, Any]:
    """Verify the response proves the requested semantic action was accepted."""
    if response.get("status") != "ok" or response.get("action") != action:
        raise ControlClientError("control response does not match the requested action")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ControlClientError("control response has no result object")
    if action in {"show", "hide", "focus", "status"} and action != "status":
        if not isinstance(result.get("visible"), bool) or not isinstance(result.get("focused"), bool):
            raise ControlClientError("window action returned no verified window state")
    if action == "navigate" and result.get("route") != target:
        raise ControlClientError("navigation response does not match the requested route")
    if action == "click" and result.get("target") != target:
        raise ControlClientError("click response does not match the requested target")
    if action == "fill" and result.get("value_chars") != len(value or ""):
        raise ControlClientError("fill response length does not match the requested value")
    return response
