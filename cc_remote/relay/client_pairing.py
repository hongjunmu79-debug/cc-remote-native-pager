"""Short-lived, single-use grants for pairing browser clients.

The QR payload contains a high-entropy bearer token, but the relay keeps only
its SHA-256 digest. Grants are process-local: restarting the relay invalidates
every unredeemed QR instead of making a login credential durable.
"""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


@dataclass(frozen=True)
class ClientPairingGrant:
    token: str
    machine_id: str
    client_id: str
    subject: str | None
    expires_at: int


@dataclass(frozen=True)
class _StoredGrant:
    machine_id: str
    client_id: str
    subject: str | None
    expires_at: int


class ClientPairingStore:
    """Bounded in-memory registry of one-time client login grants."""

    def __init__(self, cap: int = 256) -> None:
        if cap <= 0:
            raise ValueError("client pairing capacity must be positive")
        self.cap = cap
        self._grants: dict[bytes, _StoredGrant] = {}
        self._lock = asyncio.Lock()

    def _prune_locked(self, now: int) -> None:
        for token_hash, grant in list(self._grants.items()):
            if grant.expires_at <= now:
                del self._grants[token_hash]

    async def create(
        self,
        machine_id: str,
        *,
        subject: str | None,
        ttl: int,
        now: int | None = None,
    ) -> ClientPairingGrant | None:
        current = int(time.time() if now is None else now)
        token = secrets.token_urlsafe(32)
        client_id = f"paired-{secrets.token_hex(16)}"
        stored = _StoredGrant(
            machine_id=machine_id,
            client_id=client_id,
            subject=subject,
            expires_at=current + ttl,
        )
        async with self._lock:
            self._prune_locked(current)
            if len(self._grants) >= self.cap:
                return None
            self._grants[_digest(token)] = stored
        return ClientPairingGrant(
            token=token,
            machine_id=stored.machine_id,
            client_id=stored.client_id,
            subject=stored.subject,
            expires_at=stored.expires_at,
        )

    async def redeem(
        self,
        token: str,
        *,
        machine_id: str,
        client_id: str,
        now: int | None = None,
    ) -> ClientPairingGrant | None:
        current = int(time.time() if now is None else now)
        token_hash = _digest(token)
        async with self._lock:
            self._prune_locked(current)
            stored = self._grants.pop(token_hash, None)
        # Consume on every redemption attempt. A captured or mistyped token
        # never gets repeated guesses at its machine/client scope.
        if (
            stored is None
            or stored.expires_at <= current
            or stored.machine_id != machine_id
            or stored.client_id != client_id
        ):
            return None
        return ClientPairingGrant(
            token=token,
            machine_id=stored.machine_id,
            client_id=stored.client_id,
            subject=stored.subject,
            expires_at=stored.expires_at,
        )
