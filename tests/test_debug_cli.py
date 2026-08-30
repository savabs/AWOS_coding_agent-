"""Tests for the machine-readable debug-report CLI surface."""

import json
import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scaffold.agent.core.debug_journal import DebugJournal
from scaffold.agent.core.debug_schema import (
    ClaimType,
    DebugFinding,
    FindingStatus,
)


def test_debug_report_command_parser_has_read_only_inspection_options():
    from awos import build_parser

    args = build_parser().parse_args(
        ["debug-report", "session-1", "--root", ".awos/debug", "--pretty"]
    )

    assert args.command == "debug-report"
    assert args.session_id == "session-1"
    assert args.root == ".awos/debug"
    assert args.pretty is True


def test_debug_report_command_emits_json(tmp_path: Path, capsys):
    from awos import cmd_debug_report

    journal = DebugJournal(tmp_path / "debug", "session-2")
    journal.append_finding(
        DebugFinding(
            finding_id="finding-1",
            claim_type=ClaimType.OBSERVATION,
            subject="recorded failure",
            status=FindingStatus.UNKNOWN,
        )
    )

    cmd_debug_report(
        Namespace(root=str(tmp_path / "debug"), session_id="session-2", pretty=False)
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["session_id"] == "session-2"
    assert payload["findings"][0]["status"] == "UNKNOWN"


def test_debug_report_command_fails_for_missing_bundle(tmp_path: Path, capsys):
    from awos import cmd_debug_report

    try:
        cmd_debug_report(
            Namespace(root=str(tmp_path / "debug"), session_id="missing", pretty=False)
        )
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("missing debug bundle must fail")

    assert "not found" in capsys.readouterr().err
