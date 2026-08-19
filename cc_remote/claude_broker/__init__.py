"""Local PTY broker for the official Claude Code terminal client.

This package is deliberately separate from :mod:`cc_remote.tui`.  It never
installs or impersonates a ``claude`` executable: the broker owns a PTY and
executes the real Claude Code binary inside it, while ``claude-remote`` clients
attach over a same-user Unix-domain socket.
"""

from typing import Any

from .client import BrokerClient, BrokerClientError
from .paths import SOCKET_ENV, default_socket_path

__all__ = [
    "BrokerClient",
    "BrokerClientError",
    "BrokerConfig",
    "BrokerServer",
    "SOCKET_ENV",
    "default_socket_path",
]


def __getattr__(name: str) -> Any:
    """Load the POSIX PTY server only when that experimental feature is used."""
    if name in {"BrokerConfig", "BrokerServer"}:
        from .server import BrokerConfig, BrokerServer

        return {"BrokerConfig": BrokerConfig, "BrokerServer": BrokerServer}[name]
    raise AttributeError(name)
