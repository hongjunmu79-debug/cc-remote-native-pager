"""Small cross-platform primitives for durable state files.

The service primarily runs on POSIX, but the native Android deployment uses a
Windows wrapper as well.  Keep the platform branches here so persistence code
does not grow scattered ``os.name`` checks or import POSIX-only modules.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Optional, Union


PathLike = Union[str, bytes, os.PathLike[str], os.PathLike[bytes]]

try:  # POSIX
    import fcntl as _fcntl
except ImportError:  # Windows
    _fcntl = None
    import msvcrt as _msvcrt


def lock_exclusive(fd: int) -> None:
    """Block until byte zero of ``fd`` is exclusively locked."""
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
        return
    os.lseek(fd, 0, os.SEEK_SET)
    _msvcrt.locking(fd, _msvcrt.LK_LOCK, 1)


def unlock(fd: int) -> None:
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
        return
    os.lseek(fd, 0, os.SEEK_SET)
    _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)


def set_file_mode(fd: int, path: Optional[PathLike], mode: int) -> None:
    """Set a file mode through the strongest primitive on this platform."""
    fchmod = getattr(os, "fchmod", None)
    if fchmod is not None:
        fchmod(fd, mode)
        return
    # Windows has no fchmod.  chmod on the still-private temporary/lock path is
    # the closest equivalent (it mainly controls the read-only attribute).
    if path is not None:
        os.chmod(path, mode)


def set_path_mode(path: PathLike, mode: int) -> None:
    """Apply a private mode without treating Windows ACLs as POSIX modes.

    Windows ``chmod`` only controls the read-only attribute and can reject
    inherited directories even when the current user has full DACL access.
    LocalAppData already provides the per-user security boundary there.
    """
    try:
        os.chmod(path, mode)
    except OSError:
        if os.name != "nt":
            raise


def fsync_directory(path: PathLike) -> None:
    """Persist a directory rename where directory handles are supported."""
    if os.name == "nt":
        # CPython cannot open directories with os.open on Windows.  os.replace
        # still provides the required atomic visibility boundary there.
        return
    directory_fd = os.open(Path(path), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def has_private_owner_mode(info: os.stat_result) -> bool:
    """Validate POSIX ownership/mode when that metadata is meaningful."""
    if os.name == "nt":
        # Windows reports synthetic uid/rwx values; access is governed by the
        # DACL inherited from the per-user LocalAppData directory.
        return True
    return (
        info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) & 0o077 == 0
    )


def owned_by_current_user(info: os.stat_result) -> bool:
    """Compare file ownership only on platforms with real uid metadata."""
    return os.name == "nt" or info.st_uid == os.getuid()
