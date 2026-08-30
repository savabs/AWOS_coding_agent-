#!/usr/bin/env python3
"""Verify and compare two locally supplied AWOS holdout report bundles."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scaffold.agent.evaluation_corpus import (  # noqa: E402
    CorpusSchemaError,
    HoldoutCorpusManifest,
    compare_holdout_report_bundles,
)


def _write_atomic(path: Path, content: str, protected: set[Path]) -> None:
    resolved = path.resolve()
    if resolved in protected:
        raise CorpusSchemaError("output path must differ from manifest and report artifacts")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and temporary_name.exists():
            temporary_name.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify and compare local AWOS holdout report bundles"
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Optional comparison JSON output path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = HoldoutCorpusManifest.from_json(
            args.manifest.read_text(encoding="utf-8")
        )
        comparison = compare_holdout_report_bundles(
            manifest,
            str(args.baseline),
            str(args.candidate),
        )
        output = comparison.to_json()
        protected = {
            args.manifest.resolve(),
            args.baseline.resolve(),
            args.candidate.resolve(),
        }
        if args.output:
            _write_atomic(args.output, output + "\n", protected)
        print(output)
        return 1 if comparison.regressed else 0
    except (CorpusSchemaError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
