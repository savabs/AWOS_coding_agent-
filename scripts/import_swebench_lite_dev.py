#!/usr/bin/env python3
"""Fetch exactly one SWE-bench Lite development row into private AWOS state."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEFAULT_ROOT = REPO / ".awos" / "holdout_private"
DEFAULT_DATASET = "princeton-nlp/SWE-bench_Lite"
DEFAULT_SPLIT = "dev"
ROWS_ENDPOINT = "https://datasets-server.huggingface.co/rows"
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_INSTANCE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_REQUIRED_FIELDS = (
    "repo",
    "instance_id",
    "base_commit",
    "problem_statement",
    "test_patch",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "environment_setup_commit",
)


class ImportErrorContract(ValueError):
    """Raised when a public benchmark row is unsafe or incomplete."""


def fetch_one_row(*, dataset: str = DEFAULT_DATASET, split: str = DEFAULT_SPLIT, offset: int = 0) -> Mapping[str, Any]:
    if split != "dev":
        raise ImportErrorContract("this importer only accepts the SWE-bench Lite dev split")
    if offset < 0:
        raise ImportErrorContract("offset must not be negative")
    query = urlencode({"dataset": dataset, "config": "default", "split": split, "offset": offset, "length": 1})
    request = Request(f"{ROWS_ENDPOINT}?{query}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed HTTPS endpoint
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
    except OSError as exc:
        raise ImportErrorContract("SWE-bench row request failed") from exc
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise ImportErrorContract("SWE-bench response exceeds the local metadata limit")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportErrorContract("SWE-bench row response is not valid JSON") from exc
    rows = document.get("rows") if isinstance(document, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ImportErrorContract("SWE-bench endpoint did not return exactly one row")
    row = rows[0].get("row")
    if not isinstance(row, dict):
        raise ImportErrorContract("SWE-bench response row is malformed")
    return validate_row(row)


def validate_row(row: Mapping[str, Any]) -> Mapping[str, Any]:
    missing = [field for field in _REQUIRED_FIELDS if not isinstance(row.get(field), str) or not row[field].strip()]
    if missing:
        raise ImportErrorContract(f"SWE-bench row is missing fields: {', '.join(missing)}")
    instance_id = str(row["instance_id"])
    if not _INSTANCE_ID.fullmatch(instance_id):
        raise ImportErrorContract("SWE-bench instance_id contains unsafe path characters")
    if len(str(row["base_commit"])) != 40 or len(str(row["environment_setup_commit"])) != 40:
        raise ImportErrorContract("SWE-bench commit identifiers must be 40-character values")
    return dict(row)


def write_private_row(root: Path, row: Mapping[str, Any]) -> Path:
    root = root.expanduser().resolve()
    output = (root / "tasks" / "swebench_lite_dev" / f"{row['instance_id']}.json").resolve()
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise ImportErrorContract("metadata output escaped the private root") from exc
    if output.exists():
        raise ImportErrorContract(f"refusing to overwrite existing metadata: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(row), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import one SWE-bench Lite dev row into the private AWOS holdout")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--offset", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        row = fetch_one_row(dataset=args.dataset, split=args.split, offset=args.offset)
        output = write_private_row(args.root, row)
    except (ImportErrorContract, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"dataset": args.dataset, "split": args.split, "instance_id": row["instance_id"], "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
