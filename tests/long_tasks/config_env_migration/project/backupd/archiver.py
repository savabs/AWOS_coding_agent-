"""Build backup archives (zip when compressing, plain tar otherwise)."""

import tarfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"


def use_compression(cfg) -> bool:
    # getboolean on the parser understands yes/no/on/off as well.
    return cfg.parser.getboolean("backup", "compress", fallback=True)


def archive_name(cfg, when: datetime) -> str:
    ext = "zip" if use_compression(cfg) else "tar"
    return f"backup-{when.strftime(TIMESTAMP_FORMAT)}.{ext}"


def create_archive(cfg, files: Iterable[Path], when: datetime, dest_dir: Path) -> Path:
    """Write *files* (relative to cfg.source_dir) into an archive in *dest_dir*."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / archive_name(cfg, when)
    source = Path(cfg.source_dir)
    files = list(files)
    if use_compression(cfg):
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for rel in files:
                zf.write(source / rel, arcname=Path(rel).as_posix())
    else:
        with tarfile.open(target, "w") as tf:
            for rel in files:
                tf.add(source / rel, arcname=Path(rel).as_posix())
    return target


def archive_members(path: Path) -> list:
    """Names stored in an archive written by :func:`create_archive`."""
    path = Path(path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            return sorted(zf.namelist())
    with tarfile.open(path) as tf:
        return sorted(m.name for m in tf.getmembers() if m.isfile())
