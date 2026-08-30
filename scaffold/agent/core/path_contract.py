"""Provider-free, fail-closed workspace path contract for planner evidence."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class PathContractError(ValueError):
    """Raised when a planner path is invalid or not grounded in evidence."""


def _root_path(project_root: str | os.PathLike[str]) -> Path:
    root = Path(project_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise PathContractError(f"workspace root is not a directory: {project_root!r}")
    return root


def canonical_workspace_path(
    path: str | os.PathLike[str], project_root: str | os.PathLike[str]
) -> str:
    """Return a safe root-relative POSIX path, rejecting workspace escapes."""
    if isinstance(path, bytes):
        raise PathContractError("workspace path must be a non-empty text value")
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw.strip():
        raise PathContractError("workspace path must be non-empty")
    if "\x00" in raw:
        raise PathContractError("workspace path is invalid: NUL byte")

    root = _root_path(project_root)
    normalized = raw.replace("\\", "/")
    candidate = Path(normalized)
    joined = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = joined.resolve(strict=False)
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise PathContractError(f"workspace path is outside workspace: {raw!r}") from exc
    return relative.as_posix()


def _snapshot_paths(snapshot: dict[str, Any] | None, project_root: Path) -> set[str]:
    if not isinstance(snapshot, dict):
        return set()
    grounded: set[str] = set()
    for field in ("candidate_files", "hit_files", "test_files"):
        values = snapshot.get(field, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                grounded.add(canonical_workspace_path(value, project_root))
            except (PathContractError, OSError):
                continue
    return grounded


def require_grounded_path(
    path: str | os.PathLike[str],
    project_root: str | os.PathLike[str],
    snapshot: dict[str, Any] | None,
    *,
    allow_create: bool = False,
) -> str:
    """Normalize a task path and require matching exploration evidence."""
    root = _root_path(project_root)
    canonical = canonical_workspace_path(path, root)
    resolved = root / canonical
    if not resolved.is_file() and not allow_create:
        raise PathContractError(f"workspace file does not exist: {canonical}")
    if allow_create and resolved.exists() and not resolved.is_file():
        raise PathContractError(f"workspace target is not a file: {canonical}")
    grounded = _snapshot_paths(snapshot, root)
    if canonical not in grounded and not allow_create:
        raise PathContractError(f"workspace path is not grounded in exploration evidence: {canonical}")
    return canonical
