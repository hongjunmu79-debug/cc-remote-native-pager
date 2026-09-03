"""FastAPI relay: QR/password login -> session cookie, /ws (wrapper + web
clients), /healthz, optional static hosting of the web client from the same
origin.

Auth: wrapper authenticates with a Bearer WRAPPER_TOKEN header; web clients
authenticate with a Secure HttpOnly session cookie obtained from a login API.
Cookie-authenticated WebSockets must also match PUBLIC_ORIGIN when configured.

The relay never imports claude_agent_sdk and never touches the model API.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse

from cc_remote.config import (
    RelayConfig, relay_config, valid_machine_id, validate_relay_config,
)
from cc_remote.log import logger
from cc_remote.relay.auth import (
    SESSION_COOKIE_NAME, SessionClaims, authenticate_login,
    make_session_token, session_token_claims, wrapper_machine_scope,
)
from cc_remote.relay.client_pairing import ClientPairingStore
from cc_remote.relay.devices import DeviceStore
from cc_remote.relay.pairing import RelayHub
from cc_remote.relay.push import (
    PushDispatcher, PushOutcome, PushSubscription, PushSubscriptionStore,
)

log = logger("cc_remote.relay.server")

_LOGIN_WINDOW = 60.0
_LOGIN_MAX = 5
_LOGIN_MAX_IPS = 4096
_LOGIN_MAX_TOTAL_ATTEMPTS = 16384


def _turn_push_outcome(result: object | None) -> PushOutcome:
    subtype = str(getattr(result, "subtype", "") or "").lower()
    if subtype in {
        "error_during_execution", "interrupted", "cancelled", "canceled",
    }:
        return "interrupted"
    if bool(getattr(result, "is_error", False)):
        return "failed"
    return "success"


_LOGIN_CLEANUP_INTERVAL = 10.0
SESSION_EXPIRED_CLOSE_CODE = 1008
SESSION_EXPIRED_CLOSE_REASON = "session expired"
SESSION_REVOKED_CLOSE_CODE = 1008
SESSION_REVOKED_CLOSE_REASON = "session revoked"
_PUSH_BODY_MAX_BYTES = 16 * 1024
_DEVICE_BODY_MAX_BYTES = 8 * 1024
_PUSH_KEY_RE = re.compile(r"[A-Za-z0-9_-]{16,1024}")


def _lan_ipv4_addresses() -> tuple[str, ...]:
    """Return likely physical LAN IPv4 addresses, excluding tunnel space."""
    addresses: set[str] = set()
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        infos = ()
    for _family, _kind, _proto, _canonname, sockaddr in infos:
        host = str(sockaddr[0])
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            continue
        if (not isinstance(ip, ipaddress.IPv4Address) or ip.is_loopback
                or ip.is_link_local or not ip.is_private
                or ip in ipaddress.ip_network("198.18.0.0/15")):
            continue
        addresses.add(host)

    def rank(host: str) -> tuple[int, str]:
        ip = ipaddress.ip_address(host)
        if ip in ipaddress.ip_network("192.168.0.0/16"):
            return (0, host)
        if ip in ipaddress.ip_network("10.0.0.0/8"):
            return (1, host)
        return (2, host)

    return tuple(sorted(addresses, key=rank))


def _advertised_origin(cfg: RelayConfig) -> str:
    """Refresh the phone-facing origin for local/private HTTP deployments."""
    configured = cfg.public_origin.rstrip("/")
    try:
        parsed = urlsplit(configured)
        configured_ip = ipaddress.ip_address(parsed.hostname or "")
    except (ValueError, TypeError):
        return configured
    if parsed.scheme not in {"http", "https"} or not configured_ip.is_private:
        return configured
    candidates = _lan_ipv4_addresses()
    if not candidates:
        return configured
    netloc = candidates[0]
    if parsed.port is not None:
        netloc += f":{parsed.port}"
    return f"{parsed.scheme}://{netloc}"


def _origin_allowed(origin: str, cfg: RelayConfig) -> bool:
    if not origin:
        return True
    return origin.rstrip("/") in {
        cfg.public_origin.rstrip("/"), _advertised_origin(cfg).rstrip("/")
    }


class LoginRateLimiter:
    """Bounded per-IP login limiter with global stale-entry cleanup."""

    def __init__(
        self,
        *,
        window: float = _LOGIN_WINDOW,
        max_per_ip: int = _LOGIN_MAX,
        max_ips: int = _LOGIN_MAX_IPS,
        max_total_attempts: int = _LOGIN_MAX_TOTAL_ATTEMPTS,
        cleanup_interval: float = _LOGIN_CLEANUP_INTERVAL,
    ) -> None:
        self.window = window
        self.max_per_ip = max_per_ip
        self.max_ips = max_ips
        self.max_total_attempts = max_total_attempts
        self.cleanup_interval = cleanup_interval
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._total_attempts = 0
        self._last_cleanup = 0.0

    @property
    def key_count(self) -> int:
        return len(self._attempts)

    @property
    def total_attempts(self) -> int:
        return self._total_attempts

    def reset(self) -> None:
        self._attempts.clear()
        self._total_attempts = 0
        self._last_cleanup = 0.0

    def _cleanup(self, now: float) -> None:
        cutoff = now - self.window
        total = 0
        for ip in list(self._attempts):
            fresh = [attempt for attempt in self._attempts[ip] if attempt > cutoff]
            if fresh:
                self._attempts[ip] = fresh
                total += len(fresh)
            else:
                del self._attempts[ip]
        self._total_attempts = total
        self._last_cleanup = now

    def limited(self, ip: str, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        if current - self._last_cleanup >= self.cleanup_interval:
            self._cleanup(current)

        attempts = self._attempts.get(ip)
        if attempts is not None:
            cutoff = current - self.window
            fresh = [attempt for attempt in attempts if attempt > cutoff]
            self._total_attempts -= len(attempts) - len(fresh)
            if fresh:
                self._attempts[ip] = fresh
                attempts = fresh
            else:
                del self._attempts[ip]
                attempts = None

        if attempts is not None and len(attempts) >= self.max_per_ip:
            return True
        if attempts is None and len(self._attempts) >= self.max_ips:
            return True
        if self._total_attempts >= self.max_total_attempts:
            return True

        if attempts is None:
            attempts = []
            self._attempts[ip] = attempts
        attempts.append(current)
        self._total_attempts += 1
        return False


_login_limiter = LoginRateLimiter()
_pair_limiter = LoginRateLimiter(max_per_ip=30)


@dataclass
class _SessionEntry:
    expires_at: int
    revoked: asyncio.Event = field(default_factory=asyncio.Event)


class SessionRegistry:
    """Process-local revocation registry for signed browser sessions."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self._entries: dict[str, _SessionEntry] = {}
        self._lock = asyncio.Lock()

    def _prune_locked(self, now: float) -> None:
        for jti, entry in list(self._entries.items()):
            if entry.expires_at <= now:
                del self._entries[jti]

    async def register(self, claims: SessionClaims) -> bool:
        async with self._lock:
            self._prune_locked(time.time())
            if claims.jti in self._entries or len(self._entries) >= self.cap:
                return False
            self._entries[claims.jti] = _SessionEntry(claims.expires_at)
            return True

    async def active(self, claims: SessionClaims) -> bool:
        return await self.active_id(claims.jti, claims.expires_at)

    async def active_id(self, jti: str, expires_at: float) -> bool:
        async with self._lock:
            self._prune_locked(time.time())
            entry = self._entries.get(jti)
            return entry is not None and entry.expires_at == expires_at

    async def subscribe(self, claims: SessionClaims) -> Optional[asyncio.Event]:
        async with self._lock:
            self._prune_locked(time.time())
            entry = self._entries.get(claims.jti)
            if entry is None or entry.expires_at != claims.expires_at:
                return None
            return entry.revoked

    async def revoke(self, jti: str) -> bool:
        async with self._lock:
            entry = self._entries.pop(jti, None)
            if entry is None:
                return False
            entry.revoked.set()
            return True


class _BodyTooLarge(ValueError):
    pass


def _cookie_secure(cfg: RelayConfig) -> bool:
    """Allow an insecure cookie for the loopback quick-start, or when the
    operator has explicitly opted into ALLOW_INSECURE_HTTP for a plain-http
    public origin."""
    origin = urlsplit(cfg.public_origin.strip().rstrip("/"))
    if origin.scheme != "http":
        return True
    if origin.hostname in {"127.0.0.1", "::1", "localhost"}:
        return False
    return not cfg.allow_insecure_http


def _rate_limited(ip: str) -> bool:
    return _login_limiter.limited(ip)


def _request_origin_allowed(req: Request, cfg: RelayConfig) -> bool:
    """Reject browser cross-origin POSTs while retaining non-browser CLI use."""
    origin = req.headers.get("origin", "").strip()
    return _origin_allowed(origin, cfg)


def _local_pairing_request(req: Request) -> bool:
    """True only for a browser directly using a loopback relay origin.

    This is the password-free bootstrap path used by the console shortcut on
    the relay host. A request forwarded by Caddy retains the public client IP
    through ``_request_ip`` and therefore cannot use this exception.
    """
    if _request_ip(req) not in {"127.0.0.1", "::1", "localhost"}:
        return False
    origin = req.headers.get("origin", "").strip()
    if not origin:
        return False
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
    )


def _request_ip(req: Request) -> str:
    """Use Caddy's client address only when the direct peer is loopback.

    The bundled relay binds 127.0.0.1 behind Caddy. Without this trusted-proxy
    rule every public user shares the single 127.0.0.1 login bucket, so five bad
    attempts can continuously lock out the legitimate user. A directly exposed
    relay never trusts a caller-supplied forwarding header.
    """
    peer = req.client.host if req.client else "?"
    if peer not in {"127.0.0.1", "::1", "localhost"}:
        return peer[:128]
    forwarded = req.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if not forwarded:
        return peer
    try:
        return str(ipaddress.ip_address(forwarded))
    except ValueError:
        return peer


async def _read_json_limited(req: Request, max_bytes: int):
    content_length = req.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise ValueError("invalid content-length") from exc
        if declared_length < 0:
            raise ValueError("invalid content-length")
        if declared_length > max_bytes:
            raise _BodyTooLarge

    body = bytearray()
    async for chunk in req.stream():
        if len(body) + len(chunk) > max_bytes:
            raise _BodyTooLarge
        body.extend(chunk)
    return json.loads(body)


async def _active_claims(
    req: Request,
    cfg: RelayConfig,
    sessions: SessionRegistry,
) -> SessionClaims | None:
    token = req.cookies.get(SESSION_COOKIE_NAME, "")
    claims = session_token_claims(token, cfg.session_secret)
    if (
        claims is None
        or claims.expires_at <= time.time()
        or not await sessions.active(claims)
    ):
        return None
    return claims


def _push_subject(claims: SessionClaims) -> str:
    # Legacy password mode intentionally represents one shared user.
    return claims.subject or "legacy"


def _device_subject(claims: SessionClaims) -> str:
    # Legacy single-password mode is one owner, just like Push subscriptions.
    return claims.subject or "legacy"


async def _claims_allow_machine(
    claims: SessionClaims,
    machine_id: str,
    devices: DeviceStore,
) -> bool:
    if claims.client_id is not None:
        return claims.allows_machine(machine_id)
    return claims.allows_machine(machine_id) or await devices.owned_by(
        machine_id, _device_subject(claims))


def _clean_device_text(value: object, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (not normalized or len(normalized) > maximum
            or any(ord(char) < 32 for char in normalized)):
        return None
    return normalized


def _parse_push_subscription(
    body: object,
    claims: SessionClaims,
    *,
    enrolled_machine: bool = False,
) -> PushSubscription | None:
    if not isinstance(body, dict):
        return None
    machine_id = body.get("machine_id")
    endpoint = body.get("endpoint")
    keys = body.get("keys")
    if (
        not isinstance(machine_id, str)
        or not valid_machine_id(machine_id)
        or (not claims.allows_machine(machine_id) and not enrolled_machine)
        or not isinstance(endpoint, str)
        or len(endpoint) > 4096
        or not isinstance(keys, dict)
    ):
        return None
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return None
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if (
        not isinstance(p256dh, str)
        or not _PUSH_KEY_RE.fullmatch(p256dh)
        or not isinstance(auth, str)
        or not _PUSH_KEY_RE.fullmatch(auth)
    ):
        return None
    return PushSubscription(
        subject=_push_subject(claims),
        machine_id=machine_id,
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        session_jti=claims.jti,
        expires_at=claims.expires_at,
    )


async def _serve_client_until_expiry(
    websocket: WebSocket,
    hub: RelayHub,
    expires_at: int,
    revoked: asyncio.Event,
    machine_id: str = "default",
    expected_client_id: str | None = None,
) -> None:
    """Serve until disconnect, signed expiry, or server-side revocation."""
    remaining = max(0.0, expires_at - time.time())
    owner = asyncio.current_task()
    assert owner is not None
    close_signal: asyncio.Future[tuple[int, str]] = (
        asyncio.get_running_loop().create_future()
    )

    async def guard() -> None:
        try:
            await asyncio.wait_for(revoked.wait(), timeout=remaining)
            close = (SESSION_REVOKED_CLOSE_CODE, SESSION_REVOKED_CLOSE_REASON)
        # Python 3.10 raises asyncio.TimeoutError here; builtin TimeoutError is
        # only a reliable alias from Python 3.11 onward.
        except asyncio.TimeoutError:
            close = (SESSION_EXPIRED_CLOSE_CODE, SESSION_EXPIRED_CLOSE_REASON)
        if not close_signal.done():
            close_signal.set_result(close)
            owner.cancel()

    guard_task = asyncio.create_task(guard())
    try:
        if expected_client_id is not None:
            await hub.serve_client(
                websocket, machine_id, expected_client_id=expected_client_id)
        elif machine_id == "default":
            await hub.serve_client(websocket)
        else:
            await hub.serve_client(websocket, machine_id)
    except asyncio.CancelledError:
        if not close_signal.done():
            raise
        code, reason = close_signal.result()
        log.info("ws session closed", reason=reason)
        await websocket.close(code=code, reason=reason)
    finally:
        if not guard_task.done():
            guard_task.cancel()
        await asyncio.gather(guard_task, return_exceptions=True)


def create_app(
    cfg: Optional[RelayConfig] = None,
    *,
    device_store: DeviceStore | None = None,
) -> FastAPI:
    if cfg is None:
        cfg = relay_config()
    validate_relay_config(cfg)
    app = FastAPI(title="cc-remote relay")
    sessions = SessionRegistry(cfg.session_registry_cap)
    client_pairings = ClientPairingStore()
    devices = device_store or DeviceStore(cfg.device_db_path)
    push_store: PushSubscriptionStore | None = None
    push_dispatcher: PushDispatcher | None = None
    if cfg.push_vapid_public_key:
        push_store = PushSubscriptionStore(cfg.push_db_path)
        push_dispatcher = PushDispatcher(
            push_store,
            vapid_private_key=cfg.push_vapid_private_key,
            vapid_subject=cfg.push_vapid_subject,
            session_active=lambda subscription: sessions.active_id(
                subscription.session_jti, subscription.expires_at),
        )

    async def on_live_turn_end(machine_id: str, msg: object) -> None:
        if push_dispatcher is None:
            return
        result = getattr(msg, "result", None)
        await push_dispatcher.notify_turn_end(
            machine_id,
            outcome=_turn_push_outcome(result),
        )

    hub = RelayHub(cfg, on_live_turn_end=on_live_turn_end)
    login_slots = asyncio.Semaphore(cfg.login_inflight_cap)
    app.state.hub = hub
    app.state.sessions = sessions
    app.state.client_pairings = client_pairings
    app.state.login_slots = login_slots
    app.state.push_store = push_store
    app.state.push_dispatcher = push_dispatcher
    app.state.device_store = devices

    @app.get("/api/auth-config")
    async def auth_config() -> JSONResponse:
        return JSONResponse(
            {
                "multi_user": bool(cfg.login_users_json),
                "password_enabled": bool(
                    cfg.login_password or cfg.login_users_json),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/push-config")
    async def push_config(req: Request) -> JSONResponse:
        claims = await _active_claims(req, cfg, sessions)
        if claims is None:
            return JSONResponse(
                {"ok": False}, status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(
            {
                "enabled": push_dispatcher is not None,
                "public_key": cfg.push_vapid_public_key
                if push_dispatcher is not None else "",
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/push/subscribe")
    async def push_subscribe(req: Request) -> JSONResponse:
        if not _request_origin_allowed(req, cfg):
            return JSONResponse({"error": "origin_rejected"}, status_code=403)
        claims = await _active_claims(req, cfg, sessions)
        if claims is None:
            return JSONResponse({"ok": False}, status_code=401)
        if push_store is None:
            return JSONResponse({"error": "push_disabled"}, status_code=503)
        try:
            body = await _read_json_limited(req, _PUSH_BODY_MAX_BYTES)
        except _BodyTooLarge:
            return JSONResponse({"error": "too_large"}, status_code=413)
        except Exception:
            return JSONResponse({"error": "bad_request"}, status_code=400)
        requested_machine = body.get("machine_id") if isinstance(body, dict) else None
        if isinstance(requested_machine, str) and claims.client_id is not None:
            if not claims.allows_machine(requested_machine):
                return JSONResponse(
                    {"error": "invalid_subscription"}, status_code=400,
                )
        enrolled_machine = (
            isinstance(requested_machine, str)
            and claims.client_id is None
            and await devices.owned_by(
                requested_machine, _device_subject(claims))
        )
        subscription = _parse_push_subscription(
            body, claims, enrolled_machine=enrolled_machine)
        if subscription is None:
            return JSONResponse({"error": "invalid_subscription"}, status_code=400)
        await push_store.upsert(subscription)
        return JSONResponse(
            {"ok": True}, headers={"Cache-Control": "no-store"})

    @app.post("/api/push/unsubscribe")
    async def push_unsubscribe(req: Request) -> JSONResponse:
        if not _request_origin_allowed(req, cfg):
            return JSONResponse({"error": "origin_rejected"}, status_code=403)
        claims = await _active_claims(req, cfg, sessions)
        if claims is None:
            return JSONResponse({"ok": False}, status_code=401)
        if push_store is None:
            return JSONResponse({"ok": True})
        try:
            body = await _read_json_limited(req, _PUSH_BODY_MAX_BYTES)
        except _BodyTooLarge:
            return JSONResponse({"error": "too_large"}, status_code=413)
        except Exception:
            return JSONResponse({"error": "bad_request"}, status_code=400)
        endpoint = body.get("endpoint") if isinstance(body, dict) else None
        if not isinstance(endpoint, str) or not (1 <= len(endpoint) <= 4096):
            return JSONResponse({"error": "invalid_subscription"}, status_code=400)
        await push_store.remove_endpoint(_push_subject(claims), endpoint)
        return JSONResponse(
            {"ok": True}, headers={"Cache-Control": "no-store"})

    async def issue_browser_session(
        *,
        subject: str | None,
        machines: tuple[str, ...],
        client_id: str | None = None,
    ) -> JSONResponse:
        token, exp = make_session_token(
            cfg.session_secret,
            cfg.session_ttl_seconds,
            subject=subject,
            machines=machines,
            client_id=client_id,
        )
        claims = session_token_claims(token, cfg.session_secret)
        assert claims is not None
        if not await sessions.register(claims):
            return JSONResponse(
                {"error": "session_capacity"}, status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        response = JSONResponse(
            {"ok": True, "exp": exp}, headers={"Cache-Control": "no-store"}
        )
        response.set_cookie(
            SESSION_COOKIE_NAME,
            token,
            max_age=cfg.session_ttl_seconds,
            path="/",
            secure=_cookie_secure(cfg),
            httponly=True,
            samesite="strict",
        )
        return response

    @app.post("/api/client-pairing")
    async def client_pairing_start(req: Request) -> JSONResponse:
        """Issue a one-time QR grant from a trusted console.

        Existing browser sessions may pair another client from any allowed
        origin. With no session, only a browser directly connected over a
        loopback origin may bootstrap pairing.
        """
        claims = await _active_claims(req, cfg, sessions)
        if claims is None:
            if not _local_pairing_request(req):
                return JSONResponse(
                    {"error": "pairing_requires_local_or_authenticated_console"},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )
        elif not _request_origin_allowed(req, cfg):
            return JSONResponse(
                {"error": "origin_rejected"}, status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        try:
            body = await _read_json_limited(req, _DEVICE_BODY_MAX_BYTES)
        except _BodyTooLarge:
            return JSONResponse({"error": "too_large"}, status_code=413)
        except Exception:
            return JSONResponse({"error": "bad_request"}, status_code=400)
        requested = body.get("machine_id") if isinstance(body, dict) else None
        if requested is not None and (
                not isinstance(requested, str) or not valid_machine_id(requested)):
            return JSONResponse({"error": "invalid_machine"}, status_code=400)

        connected = hub.machine_ids
        machine_id = requested or (connected[0] if connected else None)
        if machine_id is None or machine_id not in connected:
            return JSONResponse(
                {"error": "wrapper_offline"}, status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        if claims is not None and not await _claims_allow_machine(
                claims, machine_id, devices):
            return JSONResponse({"error": "machine_not_authorized"}, status_code=403)

        grant = await client_pairings.create(
            machine_id,
            subject=claims.subject if claims is not None else None,
            ttl=cfg.device_pairing_ttl_seconds,
        )
        if grant is None:
            return JSONResponse(
                {"error": "pairing_capacity"}, status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        payload = json.dumps({
            "v": 1,
            "type": "cc_remote_client_pair",
            "relay": _advertised_origin(cfg),
            "token": grant.token,
            "machine_id": grant.machine_id,
            "client_id": grant.client_id,
        }, ensure_ascii=False, separators=(",", ":"))
        return JSONResponse({
            "ok": True,
            "payload": payload,
            "machine_id": grant.machine_id,
            "client_id": grant.client_id,
            "expires_at": grant.expires_at,
        }, headers={"Cache-Control": "no-store"})

    @app.post("/api/client-pairing/redeem")
    async def client_pairing_redeem(req: Request) -> JSONResponse:
        if not _request_origin_allowed(req, cfg):
            return JSONResponse({"error": "origin_rejected"}, status_code=403)
        if _pair_limiter.limited(_request_ip(req)):
            return JSONResponse(
                {"error": "rate_limited"}, status_code=429,
                headers={"Retry-After": "1", "Cache-Control": "no-store"},
            )
        try:
            body = await _read_json_limited(req, _DEVICE_BODY_MAX_BYTES)
        except _BodyTooLarge:
            return JSONResponse({"error": "too_large"}, status_code=413)
        except Exception:
            return JSONResponse({"error": "bad_request"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "bad_request"}, status_code=400)
        token = body.get("token")
        machine_id = body.get("machine_id")
        client_id = body.get("client_id")
        if (
            not isinstance(token, str) or not (32 <= len(token) <= 128)
            or not isinstance(machine_id, str) or not valid_machine_id(machine_id)
            or not isinstance(client_id, str) or not (1 <= len(client_id) <= 128)
            or any(ord(char) < 32 for char in client_id)
        ):
            return JSONResponse({"error": "invalid_pairing"}, status_code=400)
        grant = await client_pairings.redeem(
            token, machine_id=machine_id, client_id=client_id)
        if grant is None:
            return JSONResponse(
                {"error": "invalid_or_expired_pairing"}, status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        response = await issue_browser_session(
            subject=grant.subject,
            machines=(grant.machine_id,),
            client_id=grant.client_id,
        )
        if response.status_code == 200:
            log.info(
                "client pairing redeemed",
                machine_id=grant.machine_id,
                client_id=grant.client_id,
            )
        return response

    @app.post("/api/login")
    async def login(req: Request) -> JSONResponse:
        if not _request_origin_allowed(req, cfg):
            return JSONResponse(
                {"error": "origin_rejected"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        ip = _request_ip(req)
        if _rate_limited(ip):
            return JSONResponse(
                {"error": "rate_limited"},
                status_code=429,
                headers={"Cache-Control": "no-store", "Retry-After": "1"},
            )
        # Do not queue an unbounded number of slow bodies behind the gate.
        # There is no await between locked() and acquire(), so this check and
        # the immediate decrement are atomic with respect to the event loop.
        if login_slots.locked():
            return JSONResponse(
                {"error": "login_capacity"},
                status_code=503,
                headers={"Cache-Control": "no-store", "Retry-After": "1"},
            )
        await login_slots.acquire()
        try:
            try:
                body = await asyncio.wait_for(
                    _read_json_limited(req, cfg.login_body_max_bytes),
                    timeout=cfg.login_read_timeout,
                )
            except asyncio.TimeoutError:
                return JSONResponse(
                    {"error": "request_timeout"},
                    status_code=408,
                    headers={"Cache-Control": "no-store"},
                )
        except _BodyTooLarge:
            return JSONResponse(
                {"error": "too_large"},
                status_code=413,
                headers={"Cache-Control": "no-store"},
            )
        except Exception:
            return JSONResponse(
                {"error": "bad_request"},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        finally:
            login_slots.release()
        candidate = body.get("password", "") if isinstance(body, dict) else ""
        password = candidate if isinstance(candidate, str) else ""
        username_value = body.get("username", "") if isinstance(body, dict) else ""
        username = username_value if isinstance(username_value, str) else ""
        access = authenticate_login(username, password, cfg)
        if access is None:
            log.warning("login failed", ip=ip)
            return JSONResponse({"error": "invalid"}, status_code=401)
        subject, machines = access
        response = await issue_browser_session(
            subject=subject,
            machines=machines,
        )
        if response.status_code != 200:
            log.warning("session registry full", ip=ip)
            return response
        log.info("login ok", ip=ip)
        return response

    @app.get("/api/session")
    async def session_status(req: Request) -> JSONResponse:
        token = req.cookies.get(SESSION_COOKIE_NAME, "")
        claims = session_token_claims(token, cfg.session_secret)
        if (
            claims is None
            or claims.expires_at <= time.time()
            or not await sessions.active(claims)
        ):
            return JSONResponse(
                {"ok": False}, status_code=401, headers={"Cache-Control": "no-store"}
            )
        return JSONResponse(
            {"ok": True, "exp": claims.expires_at,
             "username": claims.subject,
             "client_id": claims.client_id},
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/logout")
    async def logout(req: Request) -> JSONResponse:
        if not _request_origin_allowed(req, cfg):
            return JSONResponse(
                {"error": "origin_rejected"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        token = req.cookies.get(SESSION_COOKIE_NAME, "")
        claims = session_token_claims(token, cfg.session_secret)
        if claims is not None:
            if push_store is not None:
                await push_store.remove_session(claims.jti)
            await sessions.revoke(claims.jti)
        response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        response.delete_cookie(
            SESSION_COOKIE_NAME,
            path="/",
            secure=_cookie_secure(cfg),
            httponly=True,
            samesite="strict",
        )
        return response

    @app.get("/api/devices")
    async def device_list(req: Request) -> JSONResponse:
        claims = await _active_claims(req, cfg, sessions)
        if claims is None:
            return JSONResponse({"ok": False}, status_code=401)
        subject = _device_subject(claims)
        records = await devices.list_for_subject(subject)
        if claims.client_id is not None:
            records = [
                record for record in records
                if await _claims_allow_machine(claims, record.machine_id, devices)
            ]
        connected = set(hub.machine_ids)
        if claims.client_id is not None:
            connected = {
                machine_id for machine_id in connected
                if await _claims_allow_machine(claims, machine_id, devices)
            }
        result = [
            {
                "machine_id": record.machine_id,
                "label": record.label,
                "platform": record.platform,
                "hostname": record.hostname,
                "created_at": record.created_at,
                "last_seen": record.last_seen,
                "online": record.machine_id in connected,
                "managed": True,
            }
            for record in records
        ]
        known = {record.machine_id for record in records}
        visible_legacy = {
            machine_id for machine_id in connected
            if claims.allows_machine(machine_id)
        }
        if claims.client_id is None and "*" not in claims.machines:
            visible_legacy.update(claims.machines)
        for machine_id in sorted(visible_legacy - known):
            result.append({
                "machine_id": machine_id,
                "label": machine_id,
                "platform": "",
                "hostname": "",
                "created_at": None,
                "last_seen": None,
                "online": machine_id in connected,
                "managed": False,
            })
        pairing_expires_at = await devices.pairing_expires_at(subject)
        return JSONResponse(
            {
                "ok": True,
                "devices": result,
                "pairing": {
                    "enabled": pairing_expires_at is not None,
                    "expires_at": pairing_expires_at,
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/devices/pairing")
    async def device_pairing_start(req: Request) -> JSONResponse:
        if not _request_origin_allowed(req, cfg):
            return JSONResponse({"error": "origin_rejected"}, status_code=403)
        claims = await _active_claims(req, cfg, sessions)
        if claims is None:
            return JSONResponse({"ok": False}, status_code=401)
        if claims.client_id is not None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        grant = await devices.create_pairing(
            _device_subject(claims), ttl=cfg.device_pairing_ttl_seconds)
        return JSONResponse(
            {"ok": True, "code": grant.code, "expires_at": grant.expires_at},
            headers={"Cache-Control": "no-store"},
        )

    @app.delete("/api/devices/pairing")
    async def device_pairing_close(req: Request) -> JSONResponse:
        if not _request_origin_allowed(req, cfg):
            return JSONResponse({"error": "origin_rejected"}, status_code=403)
        claims = await _active_claims(req, cfg, sessions)
        if claims is None:
            return JSONResponse({"ok": False}, status_code=401)
        if claims.client_id is not None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        await devices.close_pairing(_device_subject(claims))
        return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})

    @app.post("/api/devices/pair")
    async def device_pair(req: Request) -> JSONResponse:
        if _pair_limiter.limited(_request_ip(req)):
            return JSONResponse(
                {"error": "rate_limited"}, status_code=429,
                headers={"Retry-After": "1", "Cache-Control": "no-store"},
            )
        try:
            body = await _read_json_limited(req, _DEVICE_BODY_MAX_BYTES)
        except _BodyTooLarge:
            return JSONResponse({"error": "too_large"}, status_code=413)
        except Exception:
            return JSONResponse({"error": "bad_request"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "bad_request"}, status_code=400)
        code = _clean_device_text(body.get("code"), maximum=64)
        label = _clean_device_text(body.get("label"), maximum=64)
        platform = _clean_device_text(body.get("platform"), maximum=32)
        hostname = _clean_device_text(body.get("hostname"), maximum=255)
        if None in (code, label, platform, hostname):
            return JSONResponse({"error": "invalid_device"}, status_code=400)
        enrolled = await devices.redeem(
            code,
            label=label,
            platform=platform,
            hostname=hostname,
        )
        if enrolled is None:
            return JSONResponse(
                {"error": "invalid_or_expired_code"}, status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        origin = urlsplit(cfg.public_origin)
        relay_url = (
            f"{'wss' if origin.scheme == 'https' else 'ws'}://{origin.netloc}/ws"
        )
        return JSONResponse(
            {
                "ok": True,
                "machine_id": enrolled.machine_id,
                "label": enrolled.label,
                "token": enrolled.token,
                "relay_url": relay_url,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.patch("/api/devices/{machine_id}")
    async def device_rename(machine_id: str, req: Request) -> JSONResponse:
        if not valid_machine_id(machine_id):
            return JSONResponse({"error": "invalid_device"}, status_code=400)
        if not _request_origin_allowed(req, cfg):
            return JSONResponse({"error": "origin_rejected"}, status_code=403)
        claims = await _active_claims(req, cfg, sessions)
        if claims is None:
            return JSONResponse({"ok": False}, status_code=401)
        if not await _claims_allow_machine(claims, machine_id, devices):
            return JSONResponse({"error": "not_found"}, status_code=404)
        try:
            body = await _read_json_limited(req, _DEVICE_BODY_MAX_BYTES)
        except _BodyTooLarge:
            return JSONResponse({"error": "too_large"}, status_code=413)
        except Exception:
            return JSONResponse({"error": "bad_request"}, status_code=400)
        label = _clean_device_text(
            body.get("label") if isinstance(body, dict) else None, maximum=64)
        if label is None:
            return JSONResponse({"error": "invalid_label"}, status_code=400)
        changed = await devices.rename(
            machine_id, _device_subject(claims), label)
        if not changed:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})

    @app.delete("/api/devices/{machine_id}")
    async def device_revoke(machine_id: str, req: Request) -> JSONResponse:
        if not valid_machine_id(machine_id):
            return JSONResponse({"error": "invalid_device"}, status_code=400)
        if not _request_origin_allowed(req, cfg):
            return JSONResponse({"error": "origin_rejected"}, status_code=403)
        claims = await _active_claims(req, cfg, sessions)
        if claims is None:
            return JSONResponse({"ok": False}, status_code=401)
        if not await _claims_allow_machine(claims, machine_id, devices):
            return JSONResponse({"error": "not_found"}, status_code=404)
        revoked = await devices.revoke(machine_id, _device_subject(claims))
        if not revoked:
            return JSONResponse({"error": "not_found"}, status_code=404)
        await hub.disconnect_wrapper(machine_id, reason="device revoked")
        return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})

    @app.get("/api/machines")
    async def machine_list(req: Request) -> JSONResponse:
        claims = await _active_claims(req, cfg, sessions)
        if claims is None:
            return JSONResponse(
                {"ok": False}, status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        records = await devices.list_for_subject(_device_subject(claims))
        if claims.client_id is not None:
            records = [
                record for record in records
                if await _claims_allow_machine(claims, record.machine_id, devices)
            ]
        machines = {record.machine_id for record in records}
        machines.update(
            machine_id for machine_id in hub.machine_ids
            if claims.allows_machine(machine_id)
        )
        if claims.client_id is None and "*" not in claims.machines:
            machines.update(claims.machines)
        return JSONResponse(
            {"ok": True, "machines": sorted(machines)},
            headers={"Cache-Control": "no-store"},
        )

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        authorization = websocket.headers.get("authorization", "")
        role: str | None = None
        wrapper_scope: str | None = None
        dynamic_wrapper_token: str | None = None
        if authorization.lower().startswith("bearer "):
            wrapper_token = authorization[7:].strip()
            wrapper_scope = wrapper_machine_scope(wrapper_token, cfg)
            if wrapper_scope is None:
                wrapper_scope = await devices.machine_for_token(wrapper_token)
                if wrapper_scope is not None:
                    dynamic_wrapper_token = wrapper_token
            if wrapper_scope is not None:
                role = "wrapper"
        claims: Optional[SessionClaims] = None
        if role is None:
            token = websocket.cookies.get(SESSION_COOKIE_NAME, "")
            origin = websocket.headers.get("origin", "")
            origin_ok = _origin_allowed(origin, cfg)
            claims = session_token_claims(token, cfg.session_secret)
            if (
                claims is not None
                and claims.expires_at > time.time()
                and origin_ok
                and await sessions.active(claims)
            ):
                role = "client"
            elif token and not origin_ok:
                log.warning("ws origin rejected", origin=origin or "-")
        if role is None:
            await websocket.close(code=1008, reason="unauthorized")
            return
        await websocket.accept()
        log.info("ws accepted", role=role)
        if role == "wrapper":
            if wrapper_scope not in (None, "*"):
                await devices.touch_seen(wrapper_scope)
            async def dynamic_wrapper_authorized(machine_id: str) -> bool:
                if dynamic_wrapper_token is None:
                    return True
                return await devices.machine_for_token(
                    dynamic_wrapper_token) == machine_id
            await hub.serve_wrapper(
                websocket,
                None if wrapper_scope == "*" else wrapper_scope,
                dynamic_wrapper_authorized,
            )
        else:
            assert claims is not None
            revoked = await sessions.subscribe(claims)
            if revoked is None:
                await websocket.close(
                    code=SESSION_REVOKED_CLOSE_CODE,
                    reason=SESSION_REVOKED_CLOSE_REASON,
                )
                return
            requested_machine = websocket.query_params.get("machine", "").strip()
            if requested_machine and not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}", requested_machine
            ):
                await websocket.close(code=1008, reason="invalid machine")
                return
            if requested_machine and not await _claims_allow_machine(
                    claims, requested_machine, devices):
                await websocket.close(code=1008, reason="machine not authorized")
                return
            if requested_machine:
                machine_id = requested_machine
            else:
                connected = [
                    candidate for candidate in hub.machine_ids
                    if await _claims_allow_machine(claims, candidate, devices)
                ]
                if connected:
                    machine_id = connected[0]
                elif "*" not in claims.machines:
                    machine_id = claims.machines[0]
                else:
                    enrolled = await devices.list_for_subject(
                        _device_subject(claims))
                    machine_id = (
                        enrolled[0].machine_id if enrolled
                        else hub.default_machine_id()
                    )
            if machine_id == "default":
                # Keep the legacy call shape for embedded relays and tests that
                # replace the expiry guard. Named machines use the extended
                # route-aware form below.
                if claims.client_id is None:
                    await _serve_client_until_expiry(
                        websocket, hub, claims.expires_at, revoked)
                else:
                    await _serve_client_until_expiry(
                        websocket, hub, claims.expires_at, revoked,
                        expected_client_id=claims.client_id)
            else:
                await _serve_client_until_expiry(
                    websocket, hub, claims.expires_at, revoked, machine_id,
                    expected_client_id=claims.client_id)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "wrapper_connected": hub.wrapper_connected,
                "machines": hub.machine_ids, "clients": hub.client_count}

    # Static web client. Mounted last so /api/login and /ws and /healthz win.
    if cfg.static_dir and os.path.isdir(cfg.static_dir):
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=cfg.static_dir, html=True), name="static")
        log.info("serving static web client", dir=cfg.static_dir)

    return app
