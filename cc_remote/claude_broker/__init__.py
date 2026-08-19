"""Local PTY broker for the official Claude Code terminal client.

This package is deliberately separate from :mod:`cc_remote.tui`.  It never
installs or impersonates a ``claude`` executable: the broker owns a PTY and
executes the real Claude Code binary inside it, while ``claude-remote`` clients
attach over a same-user Unix-domain socket.
"""

from .client import BrokerClient, BrokerClientError
from .paths import SOCKET_ENV, default_socket_path
from .server import BrokerConfig, BrokerServer

__all__ = [
    "BrokerClient",
    "BrokerClientError",
    "BrokerConfig",
    "BrokerServer",
    "SOCKET_ENV",
    "default_socket_path",
]
