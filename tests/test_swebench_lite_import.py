from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import import_swebench_lite_dev as importer


def _row() -> dict[str, str]:
    return {
        "repo": "sqlfluff/sqlfluff",
        "instance_id": "sqlfluff__sqlfluff-1625",
        "base_commit": "14e1a23a3166b9a645a16de96f694c77a5d4abb7",
        "problem_statement": "Fix the issue.",
        "test_patch": "diff --git a/test.py b/test.py",
        "FAIL_TO_PASS": "[\"test.py::test_issue\"]",
        "PASS_TO_PASS": "[]",
        "environment_setup_commit": "67023b85c41a1b207c4f8a9331f8a6d6c6d69812",
        "patch": "secret gold patch",
    }


def test_validate_row_requires_core_fields() -> None:
    row = _row()
    del row["FAIL_TO_PASS"]

    with pytest.raises(importer.ImportErrorContract, match="FAIL_TO_PASS"):
        importer.validate_row(row)


def test_validate_row_rejects_path_escape_instance_id() -> None:
    row = _row()
    row["instance_id"] = "../outside"

    with pytest.raises(importer.ImportErrorContract, match="unsafe path"):
        importer.validate_row(row)


def test_write_private_row_is_deterministic_and_contained(tmp_path: Path) -> None:
    output = importer.write_private_row(tmp_path, _row())

    assert output == tmp_path / "tasks" / "swebench_lite_dev" / "sqlfluff__sqlfluff-1625.json"
    assert json.loads(output.read_text(encoding="utf-8"))["instance_id"] == "sqlfluff__sqlfluff-1625"

    with pytest.raises(importer.ImportErrorContract, match="overwrite"):
        importer.write_private_row(tmp_path, _row())


def test_write_private_row_does_not_follow_unsafe_instance_id(tmp_path: Path) -> None:
    row = _row()
    row["instance_id"] = "bad/id"
    with pytest.raises(importer.ImportErrorContract, match="unsafe path"):
        importer.validate_row(row)


def test_fetch_one_row_rejects_non_dev_split() -> None:
    with pytest.raises(importer.ImportErrorContract, match="dev split"):
        importer.fetch_one_row(split="test")


def test_fetch_one_row_uses_one_row_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return json.dumps({"rows": [{"row": _row()}]}).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = str(timeout)
        return Response()

    monkeypatch.setattr(importer, "urlopen", fake_urlopen)
    result = importer.fetch_one_row(offset=3)

    assert result["instance_id"] == "sqlfluff__sqlfluff-1625"
    assert "split=dev" in captured["url"]
    assert "length=1" in captured["url"]
    assert captured["timeout"] == "20"
