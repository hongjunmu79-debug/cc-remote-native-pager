"""Cross-platform process identity helpers shared by engine ownership scans."""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


MAX_PROC_SCAN = 8192
MAX_CMDLINE_BYTES = 64 * 1024
MAX_PS_OUTPUT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, order=True)
class ProcessIdentity:
    pid: int
    start_ticks: int


def _process_stat(proc_dir: Path) -> tuple[int, int, int] | None:
    """Return (parent pid, start ticks, tty number) from /proc stat."""
    try:
        raw = (proc_dir / "stat").read_bytes()
        end = raw.rfind(b") ")
        if end < 0:
            return None
        fields = raw[end + 2:].split()  # starts at field 3 (state)
        return int(fields[1]), int(fields[19]), int(fields[4])
    except (OSError, ValueError, IndexError):
        return None


def _process_start_ticks(proc_dir: Path) -> int | None:
    stat = _process_stat(proc_dir)
    return stat[1] if stat is not None else None


def _process_cmdline(proc_dir: Path) -> tuple[bytes, ...] | None:
    try:
        raw = (proc_dir / "cmdline").read_bytes()
    except OSError:
        return None
    if not raw or len(raw) > MAX_CMDLINE_BYTES:
        return ()
    return tuple(arg for arg in raw.split(b"\0") if arg)


_DARWIN_PS_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+"
    r"([A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})"
    r"\s+(\S+)\s+(.*)$"
)


DarwinProcessInfo = tuple[
    ProcessIdentity, int, int, tuple[bytes, ...]
]


def _parse_darwin_process_line(line: str) -> DarwinProcessInfo | None:
    match = _DARWIN_PS_RE.match(line.strip())
    if match is None:
        return None
    try:
        pid = int(match.group(1))
        started = int(time.mktime(time.strptime(
            match.group(3), "%a %b %d %H:%M:%S %Y")))
        parsed = shlex.split(match.group(5))
        parent_pid = int(match.group(2))
    except (ValueError, OverflowError):
        return None
    args = tuple(arg.encode(errors="surrogateescape") for arg in parsed)
    tty_nr = 0 if match.group(4) in {"??", "?", "-"} else 1
    return ProcessIdentity(pid, started), parent_pid, tty_nr, args


def _darwin_process_info(pid: int) -> DarwinProcessInfo | None:
    """Return stable process metadata on macOS where procfs is unavailable."""
    try:
        completed = subprocess.run(
            [
                "/bin/ps", "-p", str(pid),
                "-o", "pid=", "-o", "ppid=", "-o", "lstart=",
                "-o", "tty=", "-o", "command=",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    info = _parse_darwin_process_line(completed.stdout)
    if info is None or info[0].pid != pid:
        return None
    return info


def darwin_process_snapshot() -> tuple[list[DarwinProcessInfo], bool]:
    """Return one bounded ps snapshot used for ancestry and CLI matching."""
    try:
        completed = subprocess.run(
            [
                "/bin/ps", "-axo", "pid=,ppid=,lstart=,tty=,command=",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return [], False
    if completed.returncode != 0:
        return [], False
    if len(completed.stdout.encode(errors="surrogateescape")) > MAX_PS_OUTPUT_BYTES:
        return [], False
    lines = completed.stdout.splitlines()
    if len(lines) > MAX_PROC_SCAN:
        return [], False
    result: list[DarwinProcessInfo] = []
    for line in lines:
        if not line.strip():
            continue
        info = _parse_darwin_process_line(line)
        if info is None:
            return [], False
        result.append(info)
    return result, True


def process_identity(pid: int, *, proc_root: str = "/proc",
                     parent_pid: int | None = None) -> ProcessIdentity | None:
    stat = _process_stat(Path(proc_root) / str(pid))
    if stat is not None:
        if parent_pid is not None and stat[0] != parent_pid:
            return None
        return ProcessIdentity(pid, stat[1])
    if sys.platform == "darwin" and proc_root == "/proc":
        info = _darwin_process_info(pid)
        if info is None or (parent_pid is not None and info[1] != parent_pid):
            return None
        return info[0]
    return None


def process_owner_uid(pid: int, *, proc_root: str = "/proc") -> int | None:
    """Return the process owner without weakening the caller's identity check."""
    try:
        return os.stat(str(Path(proc_root) / str(pid))).st_uid
    except OSError:
        pass
    if sys.platform != "darwin" or proc_root != "/proc":
        return None
    try:
        completed = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "uid="],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        return int(completed.stdout.strip())
    except ValueError:
        return None
