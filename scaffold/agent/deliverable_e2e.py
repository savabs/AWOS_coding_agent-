"""
deliverable_e2e.py — Automated online checks for the create-file deliverable path.

Runs without manual GUI clicks:
  1. Router regression cases
  2. Orchestrator create path (mock LLM — no API keys)
  3. GUI HTTP SSE /api/chat/stream in agent mode (mock LLM)
  4. Regression: deliverable lands in docs/, not run_awos_demo.py

Usage:
    python3 -m scaffold.agent.deliverable_e2e
    awos validate e2e
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    duration_ms: float = 0.0


@dataclass
class E2EReport:
    checks: list[CheckResult] = field(default_factory=list)
    workspace: str = ""
    port: int = 0
    artifacts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "passed": sum(1 for c in self.checks if c.passed),
            "failed": sum(1 for c in self.checks if not c.passed),
            "workspace": self.workspace,
            "port": self.port,
            "artifacts": self.artifacts,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail, "duration_ms": c.duration_ms}
                for c in self.checks
            ],
        }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _mock_create_content(goal: str, target_path: str) -> str:
    return (
        f"# E2E Deliverable\n\n"
        f"**Goal:** {goal}\n\n"
        f"**Path:** `{target_path}`\n\n"
        f"This file was written by the automated deliverable E2E harness (mock LLM).\n"
        f"Timestamp: {time.time():.0f}\n"
    )


def _bootstrap_e2e_env() -> None:
    """Dummy keys so Orchestrator/Worker init succeeds; create path uses mock LLM."""
    import os

    os.environ.setdefault("ANTHROPIC_API_KEY", "e2e-test-key")
    os.environ.setdefault("DEEPSEEK_API_KEY", "e2e-test-key")
    os.environ.setdefault("AWOS_E2E", "1")
    os.environ.setdefault("AWOS_CHEAP_ONLY", "true")
    os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")


def install_mock_planner(monkeypatch=None) -> None:
    """Patch planners to return structured create_file tasks (no API)."""
    from scaffold.agent.plan_actions import extract_path_from_goal

    def _mock_plan(self, goal, codebase_context, tracker=None, existing_goal=None, **kwargs):
        path = extract_path_from_goal(goal)
        if not path:
            path = "docs/deliverable.md"
        return {
            "plan": [{
                "task_id": 1,
                "task_type": "create_file",
                "path": path,
                "file": path,
                "action": goal,
                "content_hint": goal,
                "complexity": "low",
            }],
            "reasoning": "e2e mock planner",
            "total_tasks": 1,
        }

    targets = [
        "scaffold.agent.cheap_planner.CheapPlanner.plan",
        "scaffold.agent.planner.Planner.plan",
    ]
    if monkeypatch is not None:
        for t in targets:
            monkeypatch.setattr(t, _mock_plan)
    else:
        import importlib

        for mod_name, cls in (
            ("scaffold.agent.cheap_planner", "CheapPlanner"),
            ("scaffold.agent.planner", "Planner"),
        ):
            mod = importlib.import_module(mod_name)
            getattr(mod, cls).plan = _mock_plan  # type: ignore[method-assign]


def install_mock_create_executor(monkeypatch=None) -> None:
    """Patch CreateFileExecutor.execute to avoid real API calls."""

    def _patched_execute(
        self,
        goal: str,
        target_path: str,
        codebase_root: str | Path = ".",
        extra_context: str = "",
        tracker=None,
    ):
        from scaffold.agent.create_file_executor import CreateFileResult
        from scaffold.agent.goal_acceptance import acceptance_for_create_goal, verify_deliverable
        from scaffold.agent.tools.filesystem import WriteFileTool

        rel = target_path.lstrip("./")
        full = Path(codebase_root) / rel
        if full.exists() and full.stat().st_size > 0:
            if not __import__("re").search(r"\b(overwrite|replace|update)\b", goal, __import__("re").I):
                return CreateFileResult(
                    success=False,
                    paths=[rel],
                    errors=[f"Refusing to overwrite existing file: {rel}"],
                )

        content = _mock_create_content(goal, rel)
        write = WriteFileTool().execute({"path": str(full.resolve()), "content": content})
        if not write.success:
            return CreateFileResult(success=False, errors=[write.error or "write failed"])

        verify_errors = verify_deliverable(full)
        ok, accept_errors = acceptance_for_create_goal(goal, [rel], codebase_root)
        errors = verify_errors + accept_errors
        return CreateFileResult(
            success=ok and not verify_errors,
            paths=[rel],
            errors=errors,
            content_bytes=len(content.encode()),
            model_used="e2e-mock",
        )

    target = "scaffold.agent.create_file_executor.CreateFileExecutor.execute"
    if monkeypatch is not None:
        monkeypatch.setattr(target, _patched_execute)
    else:
        import importlib

        mod = importlib.import_module("scaffold.agent.create_file_executor")
        mod.CreateFileExecutor.execute = _patched_execute  # type: ignore[method-assign]


def parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_type = "message"
    data_buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_buf.append(line[5:].strip())
        elif line == "" and data_buf:
            try:
                events.append({"event": event_type, "data": json.loads("\n".join(data_buf))})
            except json.JSONDecodeError:
                events.append({"event": event_type, "data": {"raw": "\n".join(data_buf)}})
            data_buf = []
            event_type = "message"
    if data_buf:
        try:
            events.append({"event": event_type, "data": json.loads("\n".join(data_buf))})
        except json.JSONDecodeError:
            pass
    return events


def post_sse(url: str, payload: dict, timeout: float = 120.0) -> list[dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return parse_sse(body)


def _run_check(name: str, fn: Callable[[], None]) -> CheckResult:
    t0 = time.perf_counter()
    try:
        fn()
        ms = (time.perf_counter() - t0) * 1000
        return CheckResult(name=name, passed=True, duration_ms=ms)
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        return CheckResult(name=name, passed=False, detail=str(exc), duration_ms=ms)


def run_orchestrator_create_check(workspace: Path, goal: str, expected_suffix: str) -> None:
    from scaffold.agent.orchestrator import Orchestrator
    from scaffold.agent.token_tracker import TokenTracker

    orch = Orchestrator(tracker=TokenTracker(monthly_budget=20.0))
    result = orch.execute_feature(goal=goal, codebase_root=str(workspace))
    if not result.get("success"):
        raise AssertionError(f"orchestrator failed: {result.get('errors')}")
    created = list((workspace / "docs").rglob("*"))
    created = [p for p in created if p.is_file() and "E2E Deliverable" in p.read_text(encoding="utf-8")]
    if not created:
        raise AssertionError("no created deliverable under docs/")
    rel = str(created[0].relative_to(workspace))
    if not rel.endswith(expected_suffix):
        raise AssertionError(f"expected *{expected_suffix}, got {rel}")


def start_test_gui_server(workspace: Path, port: int) -> ThreadingHTTPServer:
    from scaffold.agent.gui_chat import GuiChatService

    service = GuiChatService(codebase_root=str(workspace))

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            pass

        def _send_json(self, data: object, status: int = 200) -> None:
            body = json.dumps(data, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_sse(self, events) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for item in events:
                    chunk = (
                        f"event: {item.get('event', 'message')}\n"
                        f"data: {json.dumps(item.get('data', {}), default=str)}\n\n"
                    ).encode("utf-8")
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != "/api/chat/stream":
                self._send_json({"error": "not found"}, 404)
                return
            try:
                body = self._read_json()
                events = service.handle_message_stream(
                    message=body.get("message", ""),
                    mode=body.get("mode", "qa"),
                    chat_id=body.get("chat_id"),
                )
                self._send_sse(events)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] == "/api/modes":
                from scaffold.agent.gui_chat import MODE_LABELS, VALID_MODES
                self._send_json({"modes": [{"id": m, "label": MODE_LABELS[m]} for m in VALID_MODES]})
            else:
                self._send_json({"error": "not found"}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def run_http_agent_create_check(base_url: str, workspace: Path, goal: str) -> dict[str, Any]:
    events = post_sse(
        f"{base_url}/api/chat/stream",
        {"message": goal, "mode": "agent"},
        timeout=180.0,
    )
    done = next((e for e in events if e.get("event") == "done"), None)
    if not done:
        raise AssertionError(f"no done event; got {[e.get('event') for e in events]}")
    meta = done.get("data", {}).get("meta", {})
    if not meta.get("success"):
        raise AssertionError(f"agent meta.success false: {meta}")
    reply = done.get("data", {}).get("reply", "")
    if "complete" not in reply.lower() and "created" not in reply.lower():
        raise AssertionError(f"unexpected reply: {reply[:200]}")
    # Find any new file under docs/
    docs = workspace / "docs"
    if not docs.is_dir():
        raise AssertionError("docs/ not created")
    files = list(docs.rglob("*"))
    created = [p for p in files if p.is_file() and "E2E Deliverable" in p.read_text(encoding="utf-8")]
    if not created:
        raise AssertionError("no mock deliverable file found under docs/")
    return {"meta": meta, "files": [str(p.relative_to(workspace)) for p in created]}


def run_all_checks(
    workspace: Optional[Path] = None,
    use_mock: bool = True,
    monkeypatch=None,
) -> E2EReport:
    """Run full deliverable E2E suite. Uses temp workspace by default."""
    import tempfile

    report = E2EReport()
    if workspace is None:
        workspace = Path(tempfile.mkdtemp(prefix="awos_deliverable_e2e_"))
    else:
        workspace = Path(workspace)
        workspace.mkdir(parents=True, exist_ok=True)

    report.workspace = str(workspace)
    (workspace / "docs").mkdir(exist_ok=True)

    _bootstrap_e2e_env()

    # Decoy: regression target from the original bug
    decoy = workspace / "run_awos_demo.py"
    decoy.write_text("# demo\n# TODO: implement\n", encoding="utf-8")
    decoy_before = decoy.read_text(encoding="utf-8")

    if use_mock:
        install_mock_create_executor(monkeypatch)
        install_mock_planner(monkeypatch)

    def _router_coarse_create():
        from scaffold.agent.deliverable_router import DeliverableRouter, TaskKind

        d = DeliverableRouter().classify(
            "you can create e .md file regarding making a uncensored agent"
        )
        assert d.kind == TaskKind.CREATE, d.reason

    report.checks.append(_run_check("router_coarse_create", _router_coarse_create))

    def _plan_schema():
        from scaffold.agent.plan_actions import is_create_file, normalize_task

        task = normalize_task({
            "task_id": 1,
            "task_type": "create_file",
            "path": "docs/guide.md",
            "complexity": "low",
        }, workspace)
        assert is_create_file(task)

    report.checks.append(_run_check("plan_create_file_schema", _plan_schema))

    goal_md = "create docs/e2e_uncensored_agent_guide.md about uncensored local agents"

    def _orch_create():
        run_orchestrator_create_check(workspace, goal_md, "e2e_uncensored_agent_guide.md")

    report.checks.append(_run_check("orchestrator_create_file", _orch_create))

    port = _free_port()
    report.port = port
    server = start_test_gui_server(workspace, port)
    base = f"http://127.0.0.1:{port}"

    def _http_modes():
        req = urllib.request.urlopen(f"{base}/api/modes", timeout=10)
        data = json.loads(req.read().decode())
        assert len(data.get("modes", [])) >= 3

    report.checks.append(_run_check("http_api_modes", _http_modes))

    def _http_agent():
        info = run_http_agent_create_check(
            base,
            workspace,
            "create docs/e2e_via_http_guide.md about deliverable routing",
        )
        report.artifacts.extend(info.get("files", []))

    report.checks.append(_run_check("http_agent_create_sse", _http_agent))

    server.shutdown()

    def _no_decoy_patch():
        after = decoy.read_text(encoding="utf-8")
        assert after == decoy_before, "run_awos_demo.py was modified (regression)"

    report.checks.append(_run_check("regression_no_decoy_patch", _no_decoy_patch))

    def _mutate_not_create():
        from scaffold.agent.deliverable_router import DeliverableRouter, TaskKind

        d = DeliverableRouter().classify("fix the bug in run_awos_demo.py add hello function")
        assert d.kind == TaskKind.MUTATE

    report.checks.append(_run_check("router_mutate_not_create", _mutate_not_create))

    return report


def print_report(report: E2EReport) -> None:
    print("\n═══ AWOS Deliverable E2E ═══")
    print(f"Workspace: {report.workspace}")
    if report.port:
        print(f"HTTP port:  {report.port}")
    print()
    for c in report.checks:
        icon = "PASS" if c.passed else "FAIL"
        line = f"  {icon}  {c.name} ({c.duration_ms:.0f}ms)"
        if c.detail:
            line += f"\n         {c.detail}"
        print(line)
    print()
    print(f"Result: {report.to_dict()['passed']}/{len(report.checks)} passed")
    if report.artifacts:
        print("Artifacts:", ", ".join(report.artifacts))


def main() -> int:
    report = run_all_checks(use_mock=True)
    out_dir = Path(".awos/e2e")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "deliverable_e2e_report.json"
    out_path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    print_report(report)
    print(f"\nReport: {out_path}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
