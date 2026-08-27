"""First-run configuration for the packaged Windows distribution.

Pure, importable logic so the interactive PowerShell first-run wizard and the
zero-token smoke tests share one source of truth. Never writes to disk and
never touches Claude/Codex credentials: it only detects their executables on
PATH and validates/assembles the relay+wrapper environment file.

No machine-specific value is hardcoded here. The install root and the current
user's account are always supplied by the caller (from ``$PSScriptRoot`` /
known folders), never baked into the distribution.
"""
from __future__ import annotations

import ntpath
import os
import posixpath
import re
import secrets
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

# Prefixes that the Linux installers and the runtime config module also treat
# as placeholders. A packaged install must refuse them so an unconfigured
# release can never run with a guessable credential.
_PLACEHOLDER_PREFIXES = (
    "change-me",
    "changeme",
    "replace_with",
    "replace-with",
    "your-",
    "your_",
    "xxx",
    "<",
    "REPLACE_WITH_",
)
_PLACEHOLDER_SUBSTRINGS = ("REPLACE_WITH_", "placeholder")

_MACHINE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}")
_PRIVATE_IP_CIDRS = (
    ("10", None),        # 10/8
    ("172", range(16, 32)),  # 172.16/12
    ("192", (168,)),     # 192.168/16
    ("127", None),       # loopback 127/8
)


class ConfigValidationError(ValueError):
    """Raised when a first-run answer cannot be used as-is."""


@dataclass(frozen=True)
class FirstRunAnswers:
    login_password: str
    machine_name: str
    workspace: str
    public_origin: str
    relay_port: int
    allow_insecure_http: bool


def generate_secret(hex_bytes: int = 32) -> str:
    """Cryptographically strong hex secret (64 hex chars by default)."""
    if not (16 <= hex_bytes <= 128):
        raise ValueError("hex_bytes must be between 16 and 128")
    return secrets.token_hex(hex_bytes)


def is_placeholder(value: str) -> bool:
    normalized = (value or "").strip().lower()
    if not normalized:
        return True
    if any(normalized.startswith(prefix.lower()) for prefix in _PLACEHOLDER_PREFIXES):
        return True
    if any(marker in normalized for marker in _PLACEHOLDER_SUBSTRINGS):
        return True
    return normalized in {"secret", "password", "changeme", "qwerty"}


def validate_login_password(value: str) -> list[str]:
    errors: list[str] = []
    if is_placeholder(value):
        errors.append("login password must not be a placeholder")
    if len(value) < 16:
        errors.append("login password must be at least 16 characters")
    if len(value) > 1024:
        errors.append("login password must be at most 1024 characters")
    if any(ord(char) < 32 for char in value):
        errors.append("login password cannot contain control characters")
    if "'" in value or "\\" in value:
        errors.append("login password cannot contain a single quote or backslash")
    return errors


def validate_machine_name(value: str) -> list[str]:
    if _MACHINE_ID_RE.fullmatch(value) is None:
        return ["machine name must be 1-128 chars of letters, digits, . _ : @ or -"]
    if value.strip().lower() in {"default", "localhost"}:
        return ["machine name must be unique on the relay (not 'default')"]
    return []


def validate_workspace(value: str) -> list[str]:
    if not value:
        return ["workspace must not be empty"]
    if "\x00" in value or len(value.encode("utf-8", errors="surrogatepass")) > 4096:
        return ["workspace must be a path of at most 4096 UTF-8 bytes"]
    # The packaged distribution only ever runs on Windows, but the zero-token
    # test suite runs on any host (Windows CI, Ubuntu CI). Accept an absolute
    # path under either rule set: ntpath covers ``C:\...`` and ``\\server\share``;
    # posixpath covers ``/...``. ntpath.isabs alone is not enough — it treats a
    # leading ``/`` as relative, so POSIX-style workspaces must be checked too.
    if not (ntpath.isabs(value) or posixpath.isabs(value)):
        return ["workspace must be an absolute path"]
    return []


def is_private_or_local_ip(host: str | None) -> bool:
    """RFC1918 + loopback IPv4 literals only (no hostnames, no IPv6)."""
    if not host:
        return False
    parts = host.split(".")
    if len(parts) != 4:
        return False
    try:
        octets = [int(part) for part in parts]
    except ValueError:
        return False
    if any(octet < 0 or octet > 255 for octet in octets):
        return False
    first, second = octets[0], octets[1]
    for network, second_octets in _PRIVATE_IP_CIDRS:
        if first != int(network):
            continue
        if second_octets is None:
            return True
        if second in second_octets:
            return True
    return False


def validate_public_origin(value: str, *, allow_insecure_http: bool) -> list[str]:
    errors: list[str] = []
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ["public origin is not a valid URL"]
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        errors.append("public origin must be an http(s) URL without a path")
        return errors
    if not parsed.netloc or parsed.username is not None or parsed.password is not None:
        errors.append("public origin must not include userinfo")
    if parsed.path not in ("", "/"):
        errors.append("public origin must not include a path")
    if parsed.query or parsed.fragment:
        errors.append("public origin must not include a query or fragment")
    host = parsed.hostname
    if not host:
        errors.append("public origin must include a host")
        return errors
    if scheme == "http":
        if not (is_private_or_local_ip(host) or host == "localhost"):
            errors.append(
                "plain http origin must be a private/local IP literal or localhost"
            )
        elif not allow_insecure_http:
            errors.append(
                "plain http origin requires ALLOW_INSECURE_HTTP=1 (LAN only, "
                "traffic is unencrypted)"
            )
    return errors


def detect_claude() -> str | None:
    return shutil.which("claude")


def detect_codex() -> str | None:
    return shutil.which("codex")


def validate_answers(answers: FirstRunAnswers) -> list[str]:
    errors: list[str] = []
    errors.extend(validate_login_password(answers.login_password))
    errors.extend(validate_machine_name(answers.machine_name))
    errors.extend(validate_workspace(answers.workspace))
    errors.extend(
        validate_public_origin(
            answers.public_origin,
            allow_insecure_http=answers.allow_insecure_http,
        )
    )
    if not (1 <= answers.relay_port <= 65535):
        errors.append("relay port must be between 1 and 65535")
    return errors


_SAFE_UNQUOTED = re.compile(r"[A-Za-z0-9_./:@%+,-]+")


def _dotenv_value(value: str) -> str:
    """Quote a value for the python-dotenv loader.

    Safe values are emitted bare. Everything else is double-quoted with the
    escapes python-dotenv understands (``\\\\``, ``\\"``, ``\\$``), so a
    Windows path like ``C:\\Users\\alice\\projects`` round-trips exactly.
    """
    if _SAFE_UNQUOTED.fullmatch(value):
        return value
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
    )
    return f'"{escaped}"'


def build_env_content(
    *,
    answers: FirstRunAnswers,
    session_secret: str,
    wrapper_token: str,
    claude_bin: str | None,
    codex_bin: str | None,
    state_dir: str,
    work_root: str,
    static_dir: str | None = None,
) -> str:
    """Assemble the combined relay+wrapper ``.env`` for a LAN Windows machine.

    The relay and wrapper share one machine here, so a single environment file
    configures both. Secrets are passed in, never generated inside this
    function, so the caller controls when they are created.

    ``static_dir`` is the web UI directory. The installer points it at the
    ``current`` release junction so the value survives upgrades unchanged while
    ``current`` follows the active release.
    """
    machine_name = answers.machine_name
    origin = answers.public_origin.rstrip("/")
    relay_url = origin.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/ws"
    insecure = "1" if answers.allow_insecure_http else "0"
    claude_value = _dotenv_value(claude_bin) if claude_bin else ""
    codex_value = _dotenv_value(codex_bin) if codex_bin else ""
    static_value = _dotenv_value(static_dir) if static_dir else ""
    lines = [
        "# cc-remote packaged Windows distribution (generated by config-first-run.ps1)",
        "# Relay + wrapper share this machine, so one .env configures both.",
        "RELAY_HOST=0.0.0.0",
        f"RELAY_PORT={answers.relay_port}",
        f"PUBLIC_ORIGIN={_dotenv_value(origin)}",
        f"ALLOW_INSECURE_HTTP={insecure}",
        f"LOGIN_PASSWORD={_dotenv_value(answers.login_password)}",
        f"SESSION_SECRET={_dotenv_value(session_secret)}",
        f"WRAPPER_TOKEN={_dotenv_value(wrapper_token)}",
        f"CC_REMOTE_MACHINE_ID={_dotenv_value(machine_name)}",
        f"RELAY_URL={_dotenv_value(relay_url)}",
        f"CC_CWD={_dotenv_value(answers.workspace)}",
        f"CLAUDE_BIN={claude_value}",
        f"CC_REMOTE_CODEX_BIN={codex_value}",
        f"CC_REMOTE_STATE_DIR={_dotenv_value(state_dir)}",
        f"CLAUDE_WORK_ROOT={_dotenv_value(work_root)}",
        f"CODEX_WORK_ROOT={_dotenv_value(work_root)}",
        f"WEB_STATIC_DIR={static_value}",
        "SESSION_TTL_SECONDS=604800",
        "DEVICE_PAIRING_TTL_SECONDS=600",
        "MAX_CLIENTS=8",
        "MAX_CONCURRENT_SESSIONS=20",
        "LOG_LEVEL=INFO",
        "",
    ]
    return "\n".join(line for line in lines if line)


_DOUBLE_QUOTE_ESCAPES = {
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "\\": "\\",
    '"': '"',
    "$": "$",
}


def _unescape_double_quoted(value: str) -> str:
    """Undo python-dotenv's double-quote escape processing."""
    result: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            result.append(_DOUBLE_QUOTE_ESCAPES.get(value[index + 1], value[index + 1]))
            index += 2
        else:
            result.append(char)
            index += 1
    return "".join(result)


def parse_env_file(content: str) -> dict[str, str]:
    """Parse a dotenv-ish file into a dict.

    Mirrors python-dotenv's quoting semantics closely enough for the
    preserved-config gate: single-quoted values are literal, double-quoted
    values are unescaped.
    """
    result: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1]:
            if value[0] == "'":
                value = value[1:-1]
            elif value[0] == '"':
                value = _unescape_double_quoted(value[1:-1])
        result[key] = value
    return result


def validate_preserved_config(content: str) -> list[str]:
    """Reject a preserved config whose secrets drifted back to placeholders."""
    env = parse_env_file(content)
    errors: list[str] = []
    for key in ("LOGIN_PASSWORD", "SESSION_SECRET", "WRAPPER_TOKEN"):
        value = env.get(key, "")
        if is_placeholder(value):
            errors.append(f"{key} is a placeholder in the preserved config")
    if len(env.get("SESSION_SECRET", "")) < 32:
        errors.append("SESSION_SECRET must be at least 32 characters")
    if len(env.get("WRAPPER_TOKEN", "")) < 32:
        errors.append("WRAPPER_TOKEN must be at least 32 characters")
    return errors


def default_workspace_candidates() -> list[str]:
    """Candidate default workspaces, from most to least specific."""
    home = Path.home()
    candidates = [
        home / "cc-remote-workspace",
        home / "projects",
        home / "Documents",
        home / "Desktop",
    ]
    return [str(path) for path in candidates if path.exists()]


def local_lan_ip_candidates() -> list[str]:
    """Best-effort private IPv4 addresses of this host (empty when unknown)."""
    ip = _best_effort_local_ip()
    return [ip] if ip else []


def _best_effort_local_ip() -> str | None:
    try:
        import socket

        socket.setdefaulttimeout(1.0)
        # connect() to a public DNS name does not send traffic; it just selects
        # the outbound interface, whose address is the LAN IP of this host.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        address = probe.getsockname()[0]
        probe.close()
        return address if is_private_or_local_ip(address) else None
    except Exception:
        return None


def _env_arg(name: str, required: bool = False) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        raise ConfigValidationError(f"missing required value: {name}")
    return value


def _cli_validate_answers(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--login-password", default="")
    parser.add_argument("--machine-name", default="")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--public-origin", default="")
    parser.add_argument("--relay-port", type=int, default=8765)
    parser.add_argument("--insecure", action="store_true")
    args = parser.parse_args(argv)
    errors = validate_answers(
        FirstRunAnswers(
            login_password=args.login_password,
            machine_name=args.machine_name,
            workspace=args.workspace,
            public_origin=args.public_origin,
            relay_port=args.relay_port,
            allow_insecure_http=args.insecure,
        )
    )
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    return 0


def _cli_validate_preserved(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    args = parser.parse_args(argv)
    content = Path(args.file).read_text(encoding="utf-8")
    errors = validate_preserved_config(content)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    return 0


def _cli_render_env(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--login-password", default="")
    parser.add_argument("--machine-name", default="")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--public-origin", default="")
    parser.add_argument("--relay-port", type=int, default=8765)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--static-dir", default="")
    parser.add_argument("--claude-bin", default="")
    parser.add_argument("--codex-bin", default="")
    args = parser.parse_args(argv)
    answers = FirstRunAnswers(
        login_password=args.login_password,
        machine_name=args.machine_name,
        workspace=args.workspace,
        public_origin=args.public_origin,
        relay_port=args.relay_port,
        allow_insecure_http=args.insecure,
    )
    errors = validate_answers(answers)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    # Secrets cross the process boundary through the environment, never argv.
    content = build_env_content(
        answers=answers,
        session_secret=_env_arg("CCW_SESSION_SECRET", required=True),
        wrapper_token=_env_arg("CCW_WRAPPER_TOKEN", required=True),
        claude_bin=args.claude_bin or None,
        codex_bin=args.codex_bin or None,
        state_dir=args.state_dir,
        work_root=args.work_root,
        static_dir=args.static_dir or None,
    )
    sys.stdout.write(content)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: win_config.py validate-answers|render-env ...", file=sys.stderr)
        return 2
    command = argv[0]
    if command == "validate-answers":
        return _cli_validate_answers(argv[1:])
    if command == "render-env":
        return _cli_render_env(argv[1:])
    if command == "validate-preserved":
        return _cli_validate_preserved(argv[1:])
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
