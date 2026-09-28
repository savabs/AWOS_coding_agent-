"""Build backup archives (zip when compressing, plain tar otherwise)."""

import os
import tarfile
import zipfile
import zlib
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .errors import ArchiveError

TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"


def use_compression(cfg) -> bool:
    return bool(cfg.compress)


def archive_name(cfg, when: datetime) -> str:
    ext = "zip" if use_compression(cfg) else "tar"
    return f"backup-{when.strftime(TIMESTAMP_FORMAT)}.{ext}"


def create_archive(cfg, files: Iterable[Path], when: datetime, dest_dir: Path) -> Path:
    """Write *files* (relative to cfg.source_dir) into an archive in *dest_dir*.

    The archive is written under a temporary name that does not look like a
    backup and renamed into place only when complete, so a failed or
    interrupted run never leaves a half-written ``backup-*`` file behind.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / archive_name(cfg, when)
    partial = dest_dir / f".{target.name}.partial"
    source = Path(cfg.source_dir)
    files = list(files)
    try:
        if use_compression(cfg):
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for rel in files:
                    zf.write(source / rel, arcname=Path(rel).as_posix())
        else:
            with tarfile.open(partial, "w") as tf:
                for rel in files:
                    tf.add(source / rel, arcname=Path(rel).as_posix())
        os.replace(partial, target)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        partial.unlink(missing_ok=True)
        raise ArchiveError(f"could not write archive {target}: {exc}") from exc
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return target


def verify_archive(path: Path) -> int:
    """Read every file stored in *path* back; return how many there are.

    Raises ArchiveError when the archive is unreadable or damaged.
    """
    path = Path(path)
    try:
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as zf:
                bad = zf.testzip()
                if bad is not None:
                    raise ArchiveError(f"{path.name} is damaged: bad data in {bad}")
                return sum(1 for info in zf.infolist() if not info.is_dir())
        count = 0
        with tarfile.open(path) as tf:
            for member in tf:
                if member.isfile():
                    stream = tf.extractfile(member)
                    while stream.read(1 << 20):
                        pass
                    count += 1
        return count
    except ArchiveError:
        raise
    except (OSError, EOFError, zlib.error, zipfile.BadZipFile, tarfile.TarError) as exc:
        raise ArchiveError(f"{path.name} is damaged: {exc}") from exc


def archive_members(path: Path) -> list:
    """Names stored in an archive written by :func:`create_archive`."""
    path = Path(path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            return sorted(zf.namelist())
    with tarfile.open(path) as tf:
        return sorted(m.name for m in tf.getmembers() if m.isfile())
