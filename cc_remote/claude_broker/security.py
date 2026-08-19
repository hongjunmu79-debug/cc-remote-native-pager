"""Platform-neutral Unix peer credential checks for broker clients/servers."""

from __future__ import annotations

import ctypes
import socket
import struct
from typing import Any


class BrokerSecurityError(RuntimeError):
    """The local socket cannot be created or authenticated safely."""


def peer_uid(sock: Any) -> int:
    """Return an AF_UNIX peer uid on Linux or macOS, failing closed elsewhere."""
    if hasattr(socket, "SO_PEERCRED"):
        try:
            credentials = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, uid, _gid = struct.unpack("3i", credentials)
            return int(uid)
        except (AttributeError, OSError, struct.error) as exc:
            raise BrokerSecurityError(
                "cannot read Unix peer credentials"
            ) from exc

    # macOS and the BSDs expose getpeereid(3), though Python does not wrap it.
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError as exc:
        raise BrokerSecurityError(
            "Unix peer credentials are unsupported"
        ) from exc
    getpeereid = getattr(libc, "getpeereid", None)
    if getpeereid is None:
        raise BrokerSecurityError("Unix peer credentials are unsupported")
    uid = ctypes.c_uint()
    gid = ctypes.c_uint()
    getpeereid.argtypes = (
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
    )
    getpeereid.restype = ctypes.c_int
    if getpeereid(sock.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        error = ctypes.get_errno()
        raise BrokerSecurityError(f"getpeereid failed: errno {error}")
    return int(uid.value)


# Backward-compatible private name used by existing callers/tests.
_peer_uid = peer_uid
