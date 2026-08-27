"""Install layout, ACL commands, and checksum helpers for the Windows package.

The install root is always supplied at runtime (defaults computed from known
folders), never hardcoded in the distribution. Path logic is pure so the
PowerShell installer and the zero-token tests share one definition.
"""
from __future__ import annotations

import getpass
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_PATH_BYTES = 4096


@dataclass(frozen=True)
class InstallLayout:
    """Fixed sub-layout under an install root. ``root`` is caller-supplied."""

    root: Path

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def config_file(self) -> Path:
        return self.config_dir / ".env"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def releases_dir(self) -> Path:
        return self.root / "releases"

    @property
    def runtime_dir(self) -> Path:
        return self.root / "runtime"

    @property
    def venv_dir(self) -> Path:
        return self.runtime_dir / ".venv"

    def create_all(self) -> None:
        for directory in (
            self.config_dir,
            self.logs_dir,
            self.state_dir,
            self.releases_dir,
            self.runtime_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def sorted_releases(self) -> list[Path]:
        if not self.releases_dir.is_dir():
            return []
        return sorted(
            (
                path
                for path in self.releases_dir.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            ),
            key=lambda path: path.name.lower(),
        )


def default_install_root() -> Path:
    """Default per-user install root from the LOCALAPPDATA known folder.

    The folder is resolved from the environment at runtime; its expansion
    result is never baked into the distribution or the scripts.
    """
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "cc-remote"
    return Path.home() / "AppData" / "Local" / "cc-remote"


def machine_install_root() -> Path:
    """All-users install root (requires elevation) from ProgramData."""
    program_data = os.environ.get("ProgramData")
    if program_data:
        return Path(program_data) / "cc-remote"
    return Path("C:/ProgramData/cc-remote")


def default_work_root() -> Path:
    return Path.home() / ".cc-remote" / "work"


def current_user() -> str:
    """Best-effort Windows account name for ACL grants (never hardcoded)."""
    for candidate in (getpass.getuser(), os.environ.get("USERNAME"), os.environ.get("USER")):
        if candidate:
            return candidate
    return "Everyone"


def acl_commands(target: Path, principal: str) -> list[list[str]]:
    """``icacls`` invocations that restrict [target] to [principal] only.

    The commands are built (not executed) so unit tests can assert the shape
    without touching the filesystem. The installer executes them.

    ``/inheritance:r`` removes inherited ACEs and ``/grant:r`` replaces the
    explicit grants with the named user only. There is deliberately no deny
    entry: every Windows account (including the principal) is a member of
    BUILTIN\\Users, and deny beats allow, so a deny on Users would lock the
    principal out of their own config.
    """
    target = str(target)
    return [
        ["icacls", target, "/inheritance:r"],
        ["icacls", target, "/grant:r", f"{principal}:(OI)(CI)F"],
    ]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_sha256sums(directory: Path, files: list[Path], *, relroot: Path) -> Path:
    """Write a ``SHA256SUMS`` file listing ``sha256  relpath`` lines."""
    sums = directory / "SHA256SUMS"
    lines = []
    for path in sorted(files):
        rel = path.relative_to(relroot).as_posix()
        lines.append(f"{sha256_of(path)}  {rel}")
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sums


_ABS_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def is_absolute_windows_path(value: str) -> bool:
    return bool(_ABS_WIN_DRIVE.match(value)) or value.startswith(("\\\\", "//"))


def validate_layout_leaf(path: Path, root: Path) -> list[str]:
    """Reject absolute-path segments and traversal that escape the install root.

    Only the path's parts *relative to the root* are scanned for traversal: an
    absolute Windows path legitimately begins with a drive root (``C:\\``) which
    is_absolute_windows_path would otherwise flag.
    """
    errors: list[str] = []
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        errors.append(f"path escapes the install root: {path}")
    try:
        relative = path.relative_to(root)
    except ValueError:
        return errors
    for part in relative.parts:
        if part in {"..", "."} or is_absolute_windows_path(part):
            errors.append(f"path contains a traversal segment: {path}")
    return errors
