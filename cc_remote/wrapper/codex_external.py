"""Detect Codex sessions owned by another local process.

Codex rollout files are written asynchronously, so file growth alone cannot tell
whether a write came from this wrapper or from a native terminal.  The primary
signal here is stronger: another process has the same rollout inode open for
writing.  Turn markers provide a fallback for short-lived writers and tell the
wrapper when an externally-produced transcript must be reloaded.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import glob
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from cc_remote.wrapper.process_scan import (
    MAX_PROC_SCAN,
    ProcessIdentity,
    _darwin_process_info,
    _process_cmdline,
    _process_start_ticks,
    _process_stat,
)

MAX_FDS_PER_PROCESS = 8192
MAX_PARTIAL_RECORD_BYTES = 16 * 1024 * 1024
_TUI_SNAPSHOT_START_SLOP_NS = 2_000_000_000
_TUI_SNAPSHOT_READY_WINDOW_NS = 20_000_000_000
_CODEX_TUI_CLIENT = 'app_server.client_name="codex-tui"'
_CONNECTION_ID_RE = re.compile(
    r"(?:app_server\.connection_id=|connection_id=ConnectionId\()(\d+)"
)
_THREAD_ID_RE = re.compile(
    r"ThreadId \{ uuid: ([0-9a-fA-F-]{36}) \}"
)
_ROLLOUT_SID_RE = re.compile(
    r"rollout-[^\s\"']*-([0-9a-fA-F-]{36})\.jsonl"
)
_TERMINAL_EVENTS = frozenset({
    "task_complete", "turn_aborted", "task_failed", "task_cancelled",
})
_RESUME_OPTIONS_WITH_VALUE = frozenset({
    b"-c", b"--config", b"--enable", b"--disable", b"--remote",
    b"--remote-auth-token-env", b"-m", b"--model", b"--local-provider",
    b"-p", b"--profile", b"-s", b"--sandbox", b"-C", b"--cd",
    b"--add-dir", b"-a", b"--ask-for-approval", b"-i", b"--image",
})


@dataclass(frozen=True)
class TurnMarkers:
    started: frozenset[str]
    finished: frozenset[str]
    partial: bytes
    ordered: tuple[tuple[str, str], ...] = ()
    has_visible_user_message: bool = False


@dataclass(frozen=True)
class HolderScan:
    holders: dict[str, set[ProcessIdentity]]
    complete: bool
    # Headless app-server processes keep rollout FDs open while idle. They are
    # writers for turn attribution, but are not by themselves an interactive
    # terminal owner; their task markers drive the active lock instead.
    passive_holders: dict[str, set[ProcessIdentity]] = field(default_factory=dict)
    # A remote TUI reaches the shared daemon through a headless proxy which
    # never opens the rollout itself. Its exact thread is resolved separately
    # from the app-server's structured connection log.
    client_proxies: dict[ProcessIdentity, int] = field(default_factory=dict)
    # A private Codex App app-server is distinct from cc-remote's managed shared
    # daemon.  Keeping this identity separate lets the machine attribute a real
    # foreign active turn to App; merely opening/subscribing to a rollout is not
    # ownership and must not make an idle Remote session read-only.
    private_holders: dict[str, set[ProcessIdentity]] = field(default_factory=dict)


class CodexTuiLogTracker:
    """Bind live remote Codex proxies to the exact subscribed thread."""

    MAX_ROWS = 20_000

    def __init__(self, log_path: str | None = None) -> None:
        self.log_path = log_path
        self._process_uuid: str | None = None
        self._last_id = 0
        self._proxy_epoch: frozenset[ProcessIdentity] = frozenset()
        self._connections: dict[int, tuple[str, int]] = {}

    def _path(self) -> str:
        if self.log_path is not None:
            return self.log_path
        codex_home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
        return os.path.join(codex_home, "logs_2.sqlite")

    @staticmethod
    def _connection_id(body: str) -> int | None:
        match = _CONNECTION_ID_RE.search(body)
        return int(match.group(1)) if match is not None else None

    @staticmethod
    def _thread_id(body: str) -> str | None:
        match = _THREAD_ID_RE.search(body) or _ROLLOUT_SID_RE.search(body)
        return match.group(1).lower() if match is not None else None

    def bindings(
        self,
        watched_sids: Iterable[str],
        proxies: Mapping[ProcessIdentity, int],
    ) -> tuple[dict[str, set[ProcessIdentity]], bool]:
        """Return exact terminal holders and whether the log scan completed."""
        watched = set(watched_sids)
        result = {sid: set() for sid in watched}
        proxy_epoch = frozenset(proxies)
        if not proxy_epoch:
            self._proxy_epoch = proxy_epoch
            self._connections.clear()
            return result, True

        try:
            db = sqlite3.connect(
                f"file:{Path(self._path()).as_posix()}?mode=ro",
                uri=True,
                timeout=0.2,
            )
        except sqlite3.Error:
            return result, False
        try:
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT process_uuid FROM logs "
                "WHERE thread_id IS NULL AND process_uuid IS NOT NULL "
                "AND feedback_log_body LIKE '%rpc.transport=\"unix_socket\"%' "
                "AND feedback_log_body LIKE '%app_server.connection_id=%' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            process_uuid = row[0] if row and isinstance(row[0], str) else None
            if process_uuid is None:
                return result, False

            if (process_uuid != self._process_uuid
                    or proxy_epoch != self._proxy_epoch):
                self._process_uuid = process_uuid
                self._last_id = 0
                self._connections.clear()
                self._proxy_epoch = proxy_epoch

            ceiling_row = db.execute(
                "SELECT COALESCE(MAX(id), 0) FROM logs "
                "WHERE process_uuid=? AND thread_id IS NULL",
                (process_uuid,),
            ).fetchone()
            ceiling = int(ceiling_row[0]) if ceiling_row else 0
            rows = [] if ceiling <= self._last_id else db.execute(
                "SELECT id, target, feedback_log_body FROM logs "
                "WHERE process_uuid=? AND thread_id IS NULL "
                "AND id>? AND id<=? "
                "AND (feedback_log_body LIKE ? OR "
                "(target='codex_app_server::message_processor' "
                "AND feedback_log_body LIKE '%thread/unsubscribe%')) "
                "ORDER BY id LIMIT ?",
                (
                    process_uuid,
                    self._last_id,
                    ceiling,
                    f'%{_CODEX_TUI_CLIENT}%',
                    self.MAX_ROWS + 1,
                ),
            ).fetchall()
            if len(rows) > self.MAX_ROWS:
                return result, False
            for row_id, target, body in rows:
                if not isinstance(body, str):
                    continue
                connection_id = self._connection_id(body)
                if connection_id is None:
                    continue
                if (target == "codex_app_server::message_processor"
                        and "thread/unsubscribe" in body):
                    self._connections.pop(connection_id, None)
                    continue
                if _CODEX_TUI_CLIENT not in body or "thread/resume" not in body:
                    continue
                sid = self._thread_id(body)
                if sid is not None:
                    self._connections[connection_id] = (sid, int(row_id))
            self._last_id = ceiling
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return result, False
        finally:
            db.close()

        # One stdio proxy carries one app-server connection. A client may die
        # before thread/unsubscribe; the live process count is therefore the
        # authoritative cap and newer subscriptions win over stale rows.
        active = sorted(
            self._connections.values(), key=lambda item: item[1], reverse=True
        )[:len(proxy_epoch)]
        for identity, (sid, _row_id) in zip(sorted(proxy_epoch), active):
            if sid in result:
                result[sid].add(identity)
        return result, True


def _is_passive_app_server(
    proc_dir: Path, tty_nr: int, args: tuple[bytes, ...] | None = None,
) -> bool:
    """True for a headless Codex app-server, not an interactive TUI process."""
    if tty_nr != 0:
        return False
    if args is None:
        args = _process_cmdline(proc_dir)
    return bool(args and b"app-server" in args)


def _is_managed_shared_app_server(
    args: tuple[bytes, ...] | None,
) -> bool:
    """Recognize cc-remote's persistent remote-control daemon."""
    return bool(
        args
        and b"app-server" in args
        and b"--remote-control" in args
        and b"--listen" in args
    )


def _is_app_server_proxy(
    args: tuple[bytes, ...] | None, tty_nr: int,
) -> bool:
    return bool(
        tty_nr == 0 and args
        and b"app-server" in args and b"proxy" in args
    )


def _codex_resume_sids(
    args: tuple[bytes, ...] | None,
    sid_by_arg: Mapping[bytes, str],
) -> set[str]:
    """Map an interactive ``codex resume SID`` TUI to its logical session.

    Modern Codex TUIs can talk to a persistent app-server and never open the
    rollout themselves. Their explicit resume command is therefore the only
    exact idle-session ownership signal available in /proc.
    """
    if not args or b"resume" not in args:
        return set()
    command_names = {arg.rsplit(b"/", 1)[-1] for arg in args[:2]}
    if not command_names.intersection({b"codex", b"codex.exe", b"codex.js"}):
        return set()
    resume_at = args.index(b"resume")
    # `--last` resolves the target inside Codex. Its first positional is PROMPT,
    # so /proc contains no exact session id that Remote can safely claim.
    if b"--last" in args[resume_at + 1:]:
        return set()
    index = resume_at + 1
    while index < len(args):
        arg = args[index]
        if arg == b"--":
            index += 1
            if index >= len(args):
                return set()
            arg = args[index]
        elif arg in _RESUME_OPTIONS_WITH_VALUE:
            # Missing option values are left to Codex to reject. Conservatively
            # stop when no value is present rather than mistaking later prompt
            # text for a session owner.
            index += 2
            continue
        elif arg.startswith(b"-"):
            index += 1
            continue
        sid = sid_by_arg.get(arg)
        # SESSION_ID is the first positional after `resume`; the following
        # positional is PROMPT and may itself happen to contain another UUID.
        return {sid} if sid is not None else set()
    return set()


def _is_interactive_codex_tui(
    args: tuple[bytes, ...] | None, tty_nr: int,
) -> bool:
    """Recognize a native Codex TUI without treating helper commands as one."""
    if tty_nr == 0 or not args:
        return False
    command_names = {arg.rsplit(b"/", 1)[-1] for arg in args[:2]}
    if not command_names.intersection({b"codex", b"codex.exe", b"codex.js"}):
        return False
    return not any(
        helper in args for helper in (
            b"app-server", b"mcp-server", b"exec", b"execpolicy",
        )
    )


def _rollout_cwd(path: str) -> str | None:
    """Read only the bounded session_meta cwd needed for TUI attribution."""
    try:
        with open(path, "rb") as stream:
            line = stream.readline(1024 * 1024 + 1)
        if len(line) > 1024 * 1024:
            return None
        record = json.loads(line)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    payload = record.get("payload") if isinstance(record, dict) else None
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if not isinstance(cwd, str) or not cwd or "\x00" in cwd:
        return None
    return os.path.realpath(cwd)


def _shell_snapshot_rows(
    paths: Mapping[str, str], root: str,
) -> tuple[tuple[str, int, str | None], ...]:
    """Return exact watched session snapshots without scanning unrelated files."""
    rows: list[tuple[str, int, str | None]] = []
    for sid, rollout in paths.items():
        latest = -1
        try:
            matches = glob.iglob(os.path.join(root, f"{glob.escape(sid)}.*.sh"))
            for match in matches:
                try:
                    latest = max(latest, os.stat(match).st_mtime_ns)
                except OSError:
                    continue
        except OSError:
            continue
        if latest >= 0:
            rows.append((sid, latest, _rollout_cwd(rollout)))
    return tuple(rows)


def _snapshot_tui_bindings(
    processes: list[tuple[ProcessIdentity, int, str]],
    snapshots: tuple[tuple[str, int, str | None], ...],
) -> dict[ProcessIdentity, str]:
    """Uniquely bind plain `codex` TUIs to their startup shell snapshot.

    A shared app-server TUI does not keep the rollout open and a fresh `codex`
    command has no session id in argv. Codex does, however, create one shell
    snapshot named with the exact thread id immediately after that TUI starts.
    Bind only a one-to-one process/snapshot match in the same cwd and short
    startup window; ambiguous simultaneous launches deliberately stay unknown.
    """
    candidates: dict[ProcessIdentity, set[str]] = {}
    for identity, started_ns, cwd in processes:
        matches = {
            sid for sid, snapshot_ns, session_cwd in snapshots
            if session_cwd == cwd
            and started_ns - _TUI_SNAPSHOT_START_SLOP_NS <= snapshot_ns
            <= started_ns + _TUI_SNAPSHOT_READY_WINDOW_NS
        }
        if matches:
            candidates[identity] = matches
    counts: dict[str, int] = {}
    for matches in candidates.values():
        for sid in matches:
            counts[sid] = counts.get(sid, 0) + 1
    return {
        identity: next(iter(matches))
        for identity, matches in candidates.items()
        if len(matches) == 1 and counts.get(next(iter(matches))) == 1
    }


def _fd_is_writable(proc_dir: Path, fd_name: str) -> bool | None:
    try:
        with (proc_dir / "fdinfo" / fd_name).open() as stream:
            for line in stream:
                if not line.startswith("flags:"):
                    continue
                flags = int(line.split(":", 1)[1].strip(), 8)
                return (flags & os.O_ACCMODE) in (os.O_WRONLY, os.O_RDWR)
    except (OSError, ValueError):
        return None
    return None


def _darwin_writable_rollout_holders(
    paths: Mapping[str, str],
    own_processes: Iterable[ProcessIdentity],
) -> HolderScan:
    """Use lsof to detect exact rollout writers on macOS.

    Codex App and cc-remote can otherwise keep independent app-servers open on
    the same inode.  This scan is intentionally inode-based and fail-closed.
    """
    by_inode: dict[tuple[int, int], set[str]] = {}
    result = {sid: set() for sid in paths}
    passive = {sid: set() for sid in paths}
    private = {sid: set() for sid in paths}
    client_proxies: dict[ProcessIdentity, int] = {}
    filenames: list[str] = []
    for sid, path in paths.items():
        try:
            stat = os.stat(path)
        except OSError:
            continue
        by_inode.setdefault((stat.st_dev, stat.st_ino), set()).add(sid)
        filenames.append(path)
    if not filenames:
        return HolderScan(result, True, passive, client_proxies, private)
    try:
        completed = subprocess.run(
            [
                "/usr/sbin/lsof", "-n", "-P", "-w", "-FpcfnaDi",
                "--", *filenames,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.SubprocessError):
        return HolderScan(result, False, passive, client_proxies, private)
    # lsof returns 1 when no matching descriptor exists.
    if completed.returncode not in (0, 1):
        return HolderScan(result, False, passive, client_proxies, private)

    # Native Codex TUIs connect through a short headless proxy which does not
    # open the rollout. Preserve the existing log-based exact thread binding on
    # macOS instead of regressing the terminal-attached indicator.
    try:
        proxies = subprocess.run(
            ["/usr/bin/pgrep", "-f", "codex.*app-server.*proxy"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        proxies = None
        complete = False
    else:
        complete = proxies.returncode in (0, 1)
    if proxies is not None and proxies.returncode == 0:
        for value in proxies.stdout.split():
            try:
                proxy_pid = int(value)
            except ValueError:
                complete = False
                continue
            info = _darwin_process_info(proxy_pid)
            if info is None:
                complete = False
                continue
            identity, _ppid, tty_nr, args = info
            if _is_app_server_proxy(args, tty_nr):
                client_proxies[identity] = identity.start_ticks * 1_000_000_000

    pid: int | None = None
    access = ""
    device: int | None = None
    inode: int | None = None
    matches: dict[int, set[str]] = {}
    for line in completed.stdout.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            try:
                pid = int(value)
            except ValueError:
                pid = None
        elif tag == "f":
            access = ""
            device = None
            inode = None
        elif tag == "a":
            access = value
        elif tag == "D":
            try:
                device = int(value, 0)
            except ValueError:
                device = None
        elif tag == "i":
            try:
                inode = int(value)
            except ValueError:
                inode = None
        elif tag == "n" and pid is not None and access in {"w", "u"}:
            if device is not None and inode is not None:
                matches.setdefault(pid, set()).update(
                    by_inode.get((device, inode), ()))

    own = set(own_processes)
    for holder_pid, sids in matches.items():
        before = _darwin_process_info(holder_pid)
        if before is None:
            complete = False
            continue
        identity, _ppid, tty_nr, args = before
        if identity in own:
            continue
        after = _darwin_process_info(holder_pid)
        if after is None or after[0] != identity:
            complete = False
            continue
        for sid in sids:
            result[sid].add(identity)
            if _is_passive_app_server(Path(), tty_nr, args):
                passive[sid].add(identity)
                if not _is_managed_shared_app_server(args):
                    private[sid].add(identity)
    return HolderScan(result, complete, passive, client_proxies, private)


def writable_rollout_holders(
    paths: Mapping[str, str],
    own_processes: Iterable[ProcessIdentity] = (),
    *,
    proc_root: str = "/proc",
    shell_snapshot_root: str | None = None,
) -> HolderScan:
    """Return writable holders of each rollout, excluding exact wrapper children.

    Matching uses ``(st_dev, st_ino)`` rather than path text, so symlinks and
    renamed paths cannot create false negatives.  PID start ticks are checked
    before and after the FD walk to reject PID/FD reuse races.
    """
    if sys.platform == "darwin" and proc_root == "/proc":
        return _darwin_writable_rollout_holders(paths, own_processes)

    by_inode: dict[tuple[int, int], set[str]] = {}
    result = {sid: set() for sid in paths}
    passive = {sid: set() for sid in paths}
    private = {sid: set() for sid in paths}
    client_proxies: dict[ProcessIdentity, int] = {}
    sid_by_arg = {sid.encode(): sid for sid in paths}
    for sid, path in paths.items():
        try:
            st = os.stat(path)
        except OSError:
            continue
        by_inode.setdefault((st.st_dev, st.st_ino), set()).add(sid)
    if not by_inode:
        return HolderScan(result, True, passive, client_proxies, private)

    own = set(own_processes)
    root = Path(proc_root)
    if shell_snapshot_root is None:
        codex_home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
        shell_snapshot_root = os.path.join(codex_home, "shell_snapshots")
    snapshots = _shell_snapshot_rows(paths, shell_snapshot_root)
    unresolved_tuis: list[tuple[ProcessIdentity, int, str]] = []
    if own:
        own_fd_visible = False
        for identity in own:
            try:
                # Opening the directory is sufficient; it may legitimately be empty
                # during reconnect. This detects hidepid/ProtectProc-style setups
                # without treating ordinary process-exit races as scan failures.
                with os.scandir(root / str(identity.pid) / "fd"):
                    own_fd_visible = True
                    break
            except OSError:
                continue
        if not own_fd_visible:
            return HolderScan(result, False, passive, client_proxies, private)
    complete = True
    try:
        processes = (entry for entry in root.iterdir() if entry.name.isdigit())
        for index, proc_dir in enumerate(processes):
            if index >= MAX_PROC_SCAN:
                complete = False
                break
            pid = int(proc_dir.name)
            proc_stat = _process_stat(proc_dir)
            if proc_stat is None:
                continue
            _, start, tty_nr = proc_stat
            identity = ProcessIdentity(pid, start)
            if identity in own:
                continue
            args = _process_cmdline(proc_dir)
            if _is_app_server_proxy(args, tty_nr):
                try:
                    client_proxies[identity] = proc_dir.stat().st_ctime_ns
                except OSError:
                    complete = False
            logical_sids = (
                _codex_resume_sids(args, sid_by_arg) if tty_nr != 0 else set())
            interactive_tui = _is_interactive_codex_tui(args, tty_nr)
            matched: set[str] = set()
            try:
                fds = proc_dir.joinpath("fd").iterdir()
                for fd_index, fd_path in enumerate(fds):
                    if fd_index >= MAX_FDS_PER_PROCESS:
                        complete = False
                        break
                    try:
                        st = fd_path.stat()
                    except OSError:
                        continue
                    sids = by_inode.get((st.st_dev, st.st_ino))
                    if not sids:
                        continue
                    writable = _fd_is_writable(proc_dir, fd_path.name)
                    if writable is None:
                        complete = False
                        continue
                    if not writable:
                        continue
                    # Recheck the exact descriptor after fdinfo: the process may
                    # have closed and reused the same number during our scan.
                    try:
                        current = fd_path.stat()
                    except OSError:
                        continue
                    if (current.st_dev, current.st_ino) != (st.st_dev, st.st_ino):
                        continue
                    matched.update(sids)
            except OSError:
                pass
            if not matched and not logical_sids and not interactive_tui:
                continue
            # The process may have exited or the PID may have been reused while
            # its descriptors/cmdline were scanned. Only accept a stable identity.
            if _process_start_ticks(proc_dir) != start:
                continue
            if not logical_sids and interactive_tui:
                try:
                    cwd = os.path.realpath(os.readlink(proc_dir / "cwd"))
                    started_ns = proc_dir.stat().st_ctime_ns
                except OSError:
                    cwd = ""
                if cwd:
                    unresolved_tuis.append((identity, started_ns, cwd))
            for sid in logical_sids:
                result[sid].add(identity)
            for sid in matched:
                result[sid].add(identity)
                if _is_passive_app_server(proc_dir, tty_nr, args):
                    passive[sid].add(identity)
                    if not _is_managed_shared_app_server(args):
                        private[sid].add(identity)
    except OSError:
        return HolderScan(result, False, passive, client_proxies, private)
    for identity, sid in _snapshot_tui_bindings(
        unresolved_tuis, snapshots,
    ).items():
        result[sid].add(identity)
    return HolderScan(result, complete, passive, client_proxies, private)


def parse_turn_markers(data: bytes, partial: bytes = b"") -> TurnMarkers:
    """Parse lifecycle/user records and preserve one incomplete trailing record.

    ``task_started`` is commonly flushed a few milliseconds before the CLI's
    visible ``user_message``.  Keeping that second signal lets the watcher
    refresh the browser again when the prompt actually exists in history,
    without treating ordinary rollout growth as an ownership transition.
    """
    combined = partial + data
    lines = combined.splitlines(keepends=True)
    carry = b""
    if lines and not lines[-1].endswith((b"\n", b"\r")):
        carry = lines.pop()
        if len(carry) > MAX_PARTIAL_RECORD_BYTES:
            carry = b""

    started: set[str] = set()
    finished: set[str] = set()
    ordered: list[tuple[str, str]] = []
    has_visible_user_message = False
    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(record, dict) or record.get("type") != "event_msg":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if kind == "user_message":
            message = payload.get("message")
            if visible_codex_user_message(message):
                has_visible_user_message = True
        turn_id = payload.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 128:
            continue
        if kind == "task_started":
            started.add(turn_id)
            ordered.append((kind, turn_id))
        elif kind in _TERMINAL_EVENTS:
            finished.add(turn_id)
            ordered.append((kind, turn_id))
    return TurnMarkers(
        frozenset(started), frozenset(finished), carry, tuple(ordered),
        has_visible_user_message,
    )


_CODEX_REQUEST_MARKER = "## My request for Codex:"
_INTERNAL_CODEX_USER_PREFIXES = (
    "<app-context",
    "<collaboration_mode",
    "<environment_context",
    "<in-app-browser-context",
    "<permissions",
    "<turn_aborted",
)


def visible_codex_user_message(message: object) -> str | None:
    """Return the human prompt from a Codex App ``user_message`` record.

    Desktop can prepend ambient UI state and attachment metadata before the
    explicit request marker.  Treating every XML-looking row as internal drops
    the real prompt as well, which leaves interrupted turns with no user anchor.
    Unknown XML remains visible: a user is allowed to ask about literal markup.
    """
    if not isinstance(message, str):
        return None
    text = message.strip()
    if not text:
        return None
    marker = text.rfind(_CODEX_REQUEST_MARKER)
    if marker >= 0:
        request = text[marker + len(_CODEX_REQUEST_MARKER):].strip()
        return request or None
    lowered = text.lower()
    if lowered.startswith(_INTERNAL_CODEX_USER_PREFIXES):
        return None
    return text
