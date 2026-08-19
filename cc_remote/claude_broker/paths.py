"""Canonical local endpoint configuration shared by broker callers."""

from __future__ import annotations

import os
from pathlib import Path


SOCKET_ENV = "CC_REMOTE_CLAUDE_BROKER_SOCKET"
SOCKET_BASENAME = "claude-broker.sock"


def default_socket_path() -> str:
    """Return the sole supported local broker endpoint.

    An explicit environment override is useful for tests and managed wrapper
    deployments.  Otherwise prefer the per-user XDG runtime directory and fall
    back to a private directory in the user's home.
    """
    configured = os.environ.get(SOCKET_ENV, "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime_dir:
        return str(Path(runtime_dir).expanduser().absolute() / "cc-remote" / SOCKET_BASENAME)
    return str(Path.home() / ".cc-remote" / SOCKET_BASENAME)
