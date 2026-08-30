"""Build a bounded, deterministic context packet for an AWOS coding task."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable


def _safe_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"path outside project root: {relative}") from exc
    return candidate


def _read(root: Path, relative: str) -> str:
    path = _safe_path(root, relative)
    if not path.is_file():
        return f"[MISSING: {relative}]"
    return path.read_text(encoding="utf-8", errors="replace")


def _latest_checkpoint(root: Path) -> str | None:
    directory = root / "docs" / "memory"
    files = sorted(directory.glob("checkpoint_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(files[0].relative_to(root)) if files else None


def build_context_packet(
    root: str | Path,
    task: str,
    *,
    task_file: str | None = None,
    files: Iterable[str] = (),
    max_chars: int = 12000,
) -> str:
    """Return canonical facts plus selected task context within ``max_chars``."""
    if max_chars < 200:
        raise ValueError("max_chars must be at least 200")
    root_path = Path(root).resolve()
    sections: list[tuple[str, str]] = [("TASK", task)]
    for label, relative in (
        ("PROJECT FACTS", "memories/repo/project_structure.md"),
        ("IDENTITY", "VISION.md"),
    ):
        sections.append((label, _read(root_path, relative)))
    checkpoint = _latest_checkpoint(root_path)
    if checkpoint:
        sections.append(("LATEST CHECKPOINT", _read(root_path, checkpoint)))
    if task_file:
        sections.append(("TASK FILE", _read(root_path, task_file)))
    for relative in files:
        sections.append((f"FILE: {relative}", _read(root_path, relative)))

    rendered = "\n\n".join(f"## {label}\n{content}" for label, content in sections)
    if len(rendered) <= max_chars:
        return rendered

    # Preserve the task, canonical facts, and selected file identity.  Large
    # project summaries must not crowd out the file the worker is meant to edit.
    marker = "\n[TRUNCATED]\n"
    required = [f"## TASK\n{task}"]
    if task_file:
        required.append(f"## TASK FILE: {task_file}\n{_read(root_path, task_file)}")
    required.extend(
        f"## FILE: {relative}\n{_read(root_path, relative)}" for relative in files
    )
    required_text = "\n\n".join(required)
    separator = "\n\n"
    remaining = max_chars - len(marker) - len(required_text) - len(separator) - 1
    if remaining <= 0:
        return (required_text[: max_chars - len(marker)] + marker)[:max_chars]
    prefix = rendered[:remaining]
    return prefix + separator + required_text + marker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task")
    parser.add_argument("--root", default=".")
    parser.add_argument("--task-file")
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--max-chars", type=int, default=12000)
    args = parser.parse_args()
    print(build_context_packet(args.root, args.task, task_file=args.task_file, files=args.file, max_chars=args.max_chars))


if __name__ == "__main__":
    main()

__all__ = ["build_context_packet"]
