"""Explicit ``claude-remote`` command line; never shadows official ``claude``."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import signal
import stat
import struct
import subprocess
import sys
import termios
import tty
from typing import Any

from .client import BrokerClient, BrokerClientError
from .paths import default_socket_path
from .protocol import FrameType
from .server import BrokerConfig, BrokerSecurityError, BrokerServer
from .session import DEFAULT_HISTORY_BYTES, DEFAULT_MAX_SESSIONS, SessionError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-remote",
        description="Attach to official Claude Code PTYs through a local same-user broker.",
    )
    parser.add_argument("--socket", default=default_socket_path(), help="broker Unix socket path")
    parser.add_argument("--json", action="store_true", help="print non-interactive results as JSON")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the local PTY broker in the foreground")
    serve.add_argument(
        "--claude-bin",
        default=os.environ.get("CLAUDE_REMOTE_CLAUDE_BIN", "claude"),
        help="path to the real official Claude Code executable",
    )
    serve.add_argument("--max-sessions", type=int, default=DEFAULT_MAX_SESSIONS)
    serve.add_argument("--history-bytes", type=int, default=DEFAULT_HISTORY_BYTES)

    status = commands.add_parser("status", help="show broker or session status")
    status.add_argument("session_id", nargs="?")
    commands.add_parser("list", help="list broker-owned Claude sessions")

    new = commands.add_parser("new", help="start a fresh official Claude Code session")
    new.add_argument("--cwd", default=None)
    new.add_argument("--no-attach", action="store_true")
    new.add_argument("claude_args", nargs="*", help="arguments after -- go to Claude")

    resume = commands.add_parser("resume", help="resume an official Claude session UUID")
    resume.add_argument("session_id")
    resume.add_argument("--cwd", default=None)
    resume.add_argument("--no-attach", action="store_true")
    resume.add_argument("claude_args", nargs="*", help="arguments after -- go to Claude")

    attach = commands.add_parser("attach", help="attach this terminal to a broker session")
    attach.add_argument("session_id")
    attach.add_argument("--read-only", action="store_true", help="mirror output without keyboard rights")

    send = commands.add_parser("send", help="atomically submit UTF-8 text plus Enter")
    send.add_argument("session_id")
    send.add_argument("text", help="text to submit, or - to read it from stdin")

    interrupt = commands.add_parser("interrupt", help="send a real Ctrl-C to a session PTY")
    interrupt.add_argument("session_id")

    stop = commands.add_parser("stop", help="terminate a broker-owned Claude process group")
    stop.add_argument("session_id")
    return parser


def _claude_args(values: list[str]) -> list[str]:
    return values[1:] if values[:1] == ["--"] else values


def _launch_cwd(value: str | None) -> str:
    """Resolve a launch directory in the invoking CLI process.

    The broker may outlive this command and have been started from a different
    directory, so sending ``None`` would incorrectly make the broker's cwd the
    default.  Freeze both implicit and explicit paths before crossing the
    process boundary.
    """
    return os.path.realpath(os.path.expanduser(value if value is not None else os.getcwd()))


def _print_result(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return
    if "sessions" in result:
        sessions = result["sessions"]
        if not sessions:
            print("No broker sessions.")
            return
        for session in sessions:
            state = "running" if session["running"] else f"exited({session['returncode']})"
            keyboard = " keyboard" if session.get("keyboard_attached") else ""
            print(
                f"{session['id']}  {state}  pid={session['pid']}  "
                f"attached={session['attached_count']}{keyboard}  {session['cwd']}"
            )
        return
    if "session" in result:
        session = result["session"]
        state = "running" if session["running"] else f"exited({session['returncode']})"
        print(
            f"{session['id']}  {state}  pid={session['pid']} "
            f"attached={session['attached_count']} input_busy={session['input_busy']} "
            f"cwd={session['cwd']}"
        )
        return
    if "status" in result:
        status = result["status"]
        print(
            f"broker {result['generation']} revision={result['revision']} "
            f"sessions={status['running_count']}/{status['session_count']} "
            f"socket={status['socket_path']}"
        )
        return
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def _terminal_size(fd: int) -> tuple[int, int, int, int]:
    try:
        raw = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
        rows, cols, xpixel, ypixel = struct.unpack("HHHH", raw)
        if rows and cols:
            return rows, cols, xpixel, ypixel
    except OSError:
        pass
    return 24, 80, 0, 0


async def _read_fd(fd: int) -> bytes:
    mode = os.fstat(fd).st_mode
    if stat.S_ISREG(mode):
        return os.read(fd, 64 * 1024)
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[None] = loop.create_future()

    def mark_ready() -> None:
        if not ready.done():
            ready.set_result(None)

    loop.add_reader(fd, mark_ready)
    try:
        await ready
        return os.read(fd, 64 * 1024)
    finally:
        loop.remove_reader(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


async def _interactive_attach(client: BrokerClient, session_id: str, *, keyboard: bool) -> int:
    attachment = await client.attach(session_id, keyboard=keyboard)
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    original_termios: list[Any] | None = None
    resize_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    signal_installed = False

    if attachment.keyboard and os.isatty(stdin_fd):
        original_termios = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)
    elif keyboard and not attachment.keyboard:
        os.write(sys.stderr.fileno(), b"claude-remote: attached as read-only mirror (keyboard is in use)\n")

    async def output_loop() -> int:
        while True:
            frame_type, payload = await attachment.read()
            if frame_type is FrameType.OUTPUT:
                assert isinstance(payload, bytes)
                _write_all(stdout_fd, payload)
            elif frame_type is FrameType.EXIT:
                assert isinstance(payload, dict)
                return int(payload.get("returncode", 0))
            elif frame_type is FrameType.ERROR:
                assert isinstance(payload, dict)
                error = payload.get("error", {})
                # Older brokers reject a terminal focus/key frame with
                # input_busy while Remote is durably confirming a model,
                # effort, or permission change. That control window is
                # recoverable and the server keeps the attachment open; do not
                # turn the advisory frame into a terminal disconnect.
                if str(error.get("code", "")) == "input_busy":
                    continue
                raise BrokerClientError(
                    str(error.get("code", "broker_error")),
                    str(error.get("message", "attachment failed")),
                )

    async def input_loop() -> None:
        if not attachment.keyboard:
            await asyncio.Future()
        while True:
            data = await _read_fd(stdin_fd)
            if not data:
                return
            await attachment.write(data)

    async def resize_loop() -> None:
        while True:
            await resize_event.wait()
            resize_event.clear()
            await attachment.resize(*_terminal_size(stdin_fd))

    try:
        if os.isatty(stdin_fd):
            await attachment.resize(*_terminal_size(stdin_fd))
            try:
                loop.add_signal_handler(signal.SIGWINCH, resize_event.set)
                signal_installed = True
            except (NotImplementedError, RuntimeError):
                pass
        output_task = asyncio.create_task(output_loop())
        input_task = asyncio.create_task(input_loop())
        resize_task = asyncio.create_task(resize_loop())
        done, pending = await asyncio.wait(
            {output_task, input_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        resize_task.cancel()
        await asyncio.gather(*pending, resize_task, return_exceptions=True)
        if output_task in done:
            return await output_task
        return 0  # stdin disconnected: detach, but leave Claude running
    finally:
        if signal_installed:
            loop.remove_signal_handler(signal.SIGWINCH)
        if original_termios is not None:
            termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, original_termios)
        await attachment.close()


async def _serve(args: argparse.Namespace) -> int:
    server = BrokerServer(BrokerConfig(
        socket_path=args.socket,
        claude_binary=args.claude_bin,
        max_sessions=args.max_sessions,
        history_bytes=args.history_bytes,
    ))
    await server.start()
    print(f"claude-remote broker listening on {server.socket_path}", file=sys.stderr)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopped.set)
            installed.append(sig)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await stopped.wait()
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)
        await server.close()
    return 0


async def _ensure_broker(client: BrokerClient, socket_path: str) -> None:
    """Start one detached same-user broker on first explicit launch.

    No shell, alias, PATH rewrite or official `claude` interception is used.
    Concurrent launchers may race; all of them simply wait for the one socket
    that wins the server's owned-socket check.
    """
    try:
        await client.status()
        return
    except BrokerClientError as exc:
        if exc.code != "broker_unavailable":
            raise

    target = os.path.abspath(os.path.expanduser(socket_path))
    parent = os.path.dirname(target)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    parent_info = os.lstat(parent)
    if (parent_info.st_uid != os.getuid()
            or not stat.S_ISDIR(parent_info.st_mode)
            or stat.S_IMODE(parent_info.st_mode) & 0o077):
        raise BrokerSecurityError(
            "broker socket directory must be private and owned by the current uid")
    log_path = os.path.join(parent, "claude-broker.log")
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "cc_remote.claude_remote",
                "--socket",
                target,
                "serve",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=log_fd,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        os.close(log_fd)

    deadline = asyncio.get_running_loop().time() + 3.0
    last_error: BrokerClientError | None = None
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)
        try:
            await client.status()
            return
        except BrokerClientError as exc:
            last_error = exc
            if exc.code not in {"broker_unavailable", "broker_disconnected"}:
                raise
    raise BrokerClientError(
        "broker_start_failed",
        f"broker did not become ready; see {log_path}: {last_error or 'timeout'}",
    )


async def async_main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    # The common path stays as small as official Claude: an unadorned
    # `claude-remote` starts and attaches a fresh official TUI.
    if not effective_argv:
        effective_argv = ["new"]
    args = _parser().parse_args(effective_argv)
    if args.command == "serve":
        return await _serve(args)
    client = BrokerClient(args.socket)
    launch_cwd = _launch_cwd(args.cwd) if args.command in {"new", "resume"} else None
    if args.command in {"new", "resume"}:
        await _ensure_broker(client, args.socket)
    if args.command == "status":
        _print_result(await client.status(args.session_id), as_json=args.json)
    elif args.command == "list":
        _print_result(await client.list(), as_json=args.json)
    elif args.command == "new":
        result = await client.new(cwd=launch_cwd, args=_claude_args(args.claude_args))
        if args.no_attach:
            _print_result(result, as_json=args.json)
        else:
            return await _interactive_attach(client, result["session"]["id"], keyboard=True)
    elif args.command == "resume":
        try:
            result = await client.resume(
                args.session_id, cwd=launch_cwd, args=_claude_args(args.claude_args),
            )
        except BrokerClientError as exc:
            if exc.code != "session_exists":
                raise
            # A detached broker TUI is already the requested resumed session.
            # Make `resume` idempotent for humans: reattach to that exact live
            # process instead of requiring them to discover a second command.
            result = await client.status(args.session_id)
            session = result.get("session")
            if (not isinstance(session, dict)
                    or session.get("id") != args.session_id
                    or session.get("running") is not True):
                raise exc
        if args.no_attach:
            _print_result(result, as_json=args.json)
        else:
            return await _interactive_attach(client, result["session"]["id"], keyboard=True)
    elif args.command == "attach":
        return await _interactive_attach(
            client, args.session_id, keyboard=not args.read_only,
        )
    elif args.command == "send":
        text = sys.stdin.read() if args.text == "-" else args.text
        _print_result(await client.send(args.session_id, text), as_json=args.json)
    elif args.command == "interrupt":
        _print_result(await client.interrupt(args.session_id), as_json=args.json)
    elif args.command == "stop":
        _print_result(await client.stop(args.session_id), as_json=args.json)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except (BrokerClientError, BrokerSecurityError, SessionError, ValueError) as exc:
        code = getattr(exc, "code", "error")
        print(f"claude-remote: {code}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
