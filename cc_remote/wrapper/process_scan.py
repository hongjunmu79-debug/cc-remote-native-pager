"""Cross-platform process identity helpers shared by engine ownership scans."""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


MAX_PROC_SCAN = 8192
MAX_CMDLINE_BYTES = 64 * 1024
MAX_PS_OUTPUT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, order=True)
class ProcessIdentity:
    pid: int
    start_ticks: int


@dataclass(frozen=True)
class WindowsProcessInfo:
    """Stable metadata for one Windows process-table row."""

    identity: ProcessIdentity
    parent_pid: int
    args: tuple[bytes, ...]


_WINDOWS_EPOCH_FILETIME = 116_444_736_000_000_000
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_VM_READ = 0x0010
_PROCESS_TERMINATE = 0x0001
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _FileTime(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _ProcessBasicInformation(ctypes.Structure):
    _fields_ = [
        ("Reserved1", wintypes.LPVOID),
        ("PebBaseAddress", wintypes.LPVOID),
        ("Reserved2", wintypes.LPVOID * 2),
        ("UniqueProcessId", ctypes.c_size_t),
        ("Reserved3", wintypes.LPVOID),
    ]


def _windows_libraries():
    if sys.platform != "win32":
        raise OSError("Windows process APIs are unavailable")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.LPVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]
    ntdll.NtQueryInformationProcess.restype = wintypes.LONG
    return kernel32, ntdll


def _windows_identity_from_handle(handle) -> ProcessIdentity | None:
    kernel32, _ = _windows_libraries()
    created = _FileTime()
    exited = _FileTime()
    kernel = _FileTime()
    user = _FileTime()
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        return None
    started = (created.dwHighDateTime << 32) | created.dwLowDateTime
    return ProcessIdentity(0, started)


def _windows_process_rows() -> tuple[list[tuple[int, int, str]], bool]:
    """Read one bounded ToolHelp process snapshot without spawning a shell."""
    kernel32, _ = _windows_libraries()
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, 0, _INVALID_HANDLE_VALUE):
        return [], False
    rows: list[tuple[int, int, str]] = []
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return [], False
        while True:
            rows.append((
                int(entry.th32ProcessID),
                int(entry.th32ParentProcessID),
                str(entry.szExeFile),
            ))
            if len(rows) > MAX_PROC_SCAN:
                return [], False
            entry.dwSize = ctypes.sizeof(entry)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return rows, True


def _read_windows_memory(kernel32, handle, address: int, size: int) -> bytes | None:
    if address <= 0 or size <= 0 or size > MAX_CMDLINE_BYTES:
        return None
    buffer = (ctypes.c_ubyte * size)()
    read = ctypes.c_size_t()
    if not kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        buffer,
        size,
        ctypes.byref(read),
    ) or read.value != size:
        return None
    return bytes(buffer)


def _windows_process_command_line(
    kernel32, ntdll, handle,
) -> tuple[bytes, ...] | None:
    """Read a same-bitness process command line from its PEB.

    Claude and Node ship as x64 on supported Windows installations. A WOW64
    candidate is deliberately reported as unreadable instead of being guessed.
    """
    basic = _ProcessBasicInformation()
    returned = wintypes.ULONG()
    status = ntdll.NtQueryInformationProcess(
        handle,
        0,
        ctypes.byref(basic),
        ctypes.sizeof(basic),
        ctypes.byref(returned),
    )
    if status != 0 or not basic.PebBaseAddress:
        return None
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    peb = int(ctypes.cast(basic.PebBaseAddress, ctypes.c_void_p).value or 0)
    parameters_offset = 0x20 if pointer_size == 8 else 0x10
    raw_pointer = _read_windows_memory(
        kernel32, handle, peb + parameters_offset, pointer_size)
    if raw_pointer is None:
        return None
    parameters = int.from_bytes(raw_pointer, "little")
    command_offset = 0x70 if pointer_size == 8 else 0x40
    unicode_size = 16 if pointer_size == 8 else 8
    raw_unicode = _read_windows_memory(
        kernel32, handle, parameters + command_offset, unicode_size)
    if raw_unicode is None:
        return None
    length = int.from_bytes(raw_unicode[0:2], "little")
    pointer_offset = 8 if pointer_size == 8 else 4
    address = int.from_bytes(
        raw_unicode[pointer_offset:pointer_offset + pointer_size], "little")
    if length <= 0 or length > MAX_CMDLINE_BYTES or length % 2:
        return None
    raw_command = _read_windows_memory(kernel32, handle, address, length)
    if raw_command is None:
        return None
    try:
        command = raw_command.decode("utf-16-le")
    except UnicodeDecodeError:
        return None

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    count = ctypes.c_int()
    argv = shell32.CommandLineToArgvW(command, ctypes.byref(count))
    if not argv:
        return None
    try:
        if count.value <= 0 or count.value > 4096:
            return None
        return tuple(os.fsencode(argv[index]) for index in range(count.value))
    finally:
        kernel32.LocalFree(argv)


def windows_process_snapshot() -> tuple[
    list[WindowsProcessInfo], dict[int, int], bool,
]:
    """Return Claude/Node candidates plus a complete Windows ancestry map."""
    rows, complete = _windows_process_rows()
    if not complete:
        return [], {}, False
    parent_by_pid = {pid: parent for pid, parent, _name in rows}
    candidate_names = {"claude", "claude.exe", "node", "node.exe"}
    candidates: list[WindowsProcessInfo] = []
    kernel32, ntdll = _windows_libraries()
    for pid, parent_pid, name in rows:
        if pid <= 0 or name.lower() not in candidate_names:
            continue
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid)
        if not handle:
            # Candidate may have disappeared between the table and open.
            if process_identity(pid) is not None:
                complete = False
            continue
        try:
            partial_identity = _windows_identity_from_handle(handle)
            args = _windows_process_command_line(kernel32, ntdll, handle)
        finally:
            kernel32.CloseHandle(handle)
        if partial_identity is None or args is None:
            if process_identity(pid) is not None:
                complete = False
            continue
        candidates.append(WindowsProcessInfo(
            ProcessIdentity(pid, partial_identity.start_ticks),
            parent_pid,
            args,
        ))
    return candidates, parent_by_pid, complete


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
    if sys.platform == "win32" and proc_root == "/proc":
        if parent_pid is not None:
            rows, complete = _windows_process_rows()
            if not complete or next(
                (parent for candidate, parent, _name in rows if candidate == pid),
                None,
            ) != parent_pid:
                return None
        kernel32, _ = _windows_libraries()
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            partial = _windows_identity_from_handle(handle)
        finally:
            kernel32.CloseHandle(handle)
        if partial is None:
            return None
        return ProcessIdentity(pid, partial.start_ticks)
    return None


def process_owner_uid(pid: int, *, proc_root: str = "/proc") -> int | str | None:
    """Return the process owner without weakening the caller's identity check."""
    try:
        return os.stat(str(Path(proc_root) / str(pid))).st_uid
    except OSError:
        pass
    if sys.platform == "win32" and proc_root == "/proc":
        try:
            import win32api
            import win32security

            process = win32api.OpenProcess(
                _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            try:
                token = win32security.OpenProcessToken(
                    process, win32security.TOKEN_QUERY)
                try:
                    sid = win32security.GetTokenInformation(
                        token, win32security.TokenUser)[0]
                    return win32security.ConvertSidToStringSid(sid)
                finally:
                    token.Close()
            finally:
                process.Close()
        except (ImportError, OSError):
            return None
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


def terminate_exact_process(identity: ProcessIdentity) -> bool:
    """Terminate exactly one Windows process handle after identity validation.

    Windows has no safe way to send Ctrl+C to only a child that shares the
    terminal shell's process group. Explicit takeover therefore terminates the
    verified Claude executable handle and never its parent shell or descendants.
    Returns ``False`` when the PID disappeared or was reused.
    """
    if sys.platform != "win32":
        raise OSError("exact handle termination is Windows-only")
    kernel32, _ = _windows_libraries()
    handle = kernel32.OpenProcess(
        _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        identity.pid,
    )
    if not handle:
        return False
    try:
        partial = _windows_identity_from_handle(handle)
        if partial is None or partial.start_ticks != identity.start_ticks:
            return False
        if not kernel32.TerminateProcess(handle, 1):
            error = ctypes.get_last_error()
            if error in {5}:
                raise PermissionError(error, "TerminateProcess denied")
            raise OSError(error, "TerminateProcess failed")
        return True
    finally:
        kernel32.CloseHandle(handle)


def windows_filetime_to_unix_ms(start_ticks: int) -> int:
    """Convert the stable Windows creation FILETIME used by ProcessIdentity."""
    return (start_ticks - _WINDOWS_EPOCH_FILETIME) // 10_000
