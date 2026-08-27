# Security guidance

cc-remote lets a remote person drive a local Claude Code / Codex CLI. Treat any
account that can log in and open a Code session as holding a remote
agent/shell on the wrapper machine. This document collects the security model,
the release-hardening controls, and operational guidance. The two user journeys
and all configuration are in the [`README`](../../README.md).

## Model and control links

Two independent links exist and must never be conflated:

- **Model link** — the local `claude` / `codex` CLI → its own settings and
  authentication. cc-remote never touches model credentials or the model API.
- **Control link** — client ⇄ relay ⇄ wrapper ⇄ SDK / app-server ⇄ local CLI.
  All security boundaries in this document are about the control link.

## Authentication boundaries

| Boundary | Material | Rules |
| --- | --- | --- |
| Web login | `LOGIN_PASSWORD` (or `LOGIN_USERS_JSON`) | `POST /api/login` issues a short-lived HttpOnly/SameSite cookie. Never in URLs. |
| Session signing | `SESSION_SECRET` | HMAC key; strong random (`openssl rand -hex 32`). |
| Wrapper ⇄ relay | `WRAPPER_TOKEN` / `WRAPPER_TOKENS_JSON`, or a device-center-issued machine credential | WS-upgrade Bearer token. Machine-bound; one credential must never claim another device's `machine_id`. Relay stores only a hash. |
| Origin | exact `PUBLIC_ORIGIN` | `/ws` enforces the exact allowed browser origin. |

Logging redacts token/password fields. Never put tokens in URLs or wire-protocol
message bodies.

## Threat model

- **Compromise of the relay** (public VPS) leaks no session history, artifacts,
  or model credentials: the relay is a stateless WebSocket forwarder that never
  imports the SDK and never persists sessions.
- **Compromise of a logged-in browser** = control of the wrapper machine's
  Code sessions. Use strong passwords, rotate secrets, and revoke devices when
  a machine is lost.
- **LAN sniffing**: cleartext HTTP on a trusted LAN is a convenience, not a
  security boundary. Keep the firewall scoped to `LocalSubnet` and never expose
  the LAN relay beyond the local subnet.

## Android endpoint rules

The pager has **no built-in default server endpoint**. First launch shows a
server-entry screen:

- **HTTPS:** any valid root origin is accepted (no userinfo, query, fragment, or
  non-root path).
- **Cleartext HTTP:** only explicit private/local IP literals are allowed —
  RFC1918 `10/8`, `172.16/12`, `192.168/16`, and loopback — and only after a
  visible warning/confirmation. Public HTTP, userinfo, query, fragment, and
  non-root paths are rejected.
- The `network_security_config.xml` allows cleartext only to private/local
  ranges; the WebView enforces the selected origin for HTTP/HTTPS resource
  requests and main-frame navigation, so a page cannot escape its server origin.
- External links open in the system browser and never gain bridge access.

## Bridge and WebView enforcement

- One WebView owns authentication, WebSocket transport, and chat; the Compose
  dashboard is a bounded projection. There is no second session state machine.
- The native bridge is restricted to the exact configured origin via
  `WebViewCompat.addWebMessageListener`, with monotonic snapshot sequence,
  freshness timeouts, and a bounded frame size.
- Fail-closed behavior is preserved: if the bridge origin or a snapshot
  sequence is invalid, native state is not trusted.

## Windows distribution hardening

- First-run configuration generates cryptographically strong `SESSION_SECRET`
  and `WRAPPER_TOKEN`, validates the login password/machine name/default
  workspace, detects `claude`/`codex` without copying credentials, and **refuses
  placeholder values**.
- `config\` gets `/inheritance:r` + `/grant:r <user>` — no deny on
  `BUILTIN\Users` (a deny entry would lock the principal out of their own
  files).
- The firewall rule is scoped to **LocalSubnet** on Private/Domain profiles,
  bound to the relay port only.
- Scheduled tasks supervise the real long-lived processes (`supervise.ps1`,
  bounded restart-on-failure); no untracked children are spawned.
- A machine-created `.venv` is never shipped; runtime inputs are pinned by
  checksum; local builds are unsigned by default and signing is a CI-secret
  optional step.
- Uninstall/rollback never deletes Claude/Codex sessions or credentials.

## Operational guidance

- Use strong random values for `LOGIN_PASSWORD`, `SESSION_SECRET`, and wrapper
  tokens; rotate them periodically; never commit them or paste them into chat.
- Prefer TLS (`wss://`) everywhere. `ALLOW_INSECURE_HTTP` is a last-resort
  escape hatch for ephemeral bare-IP deployments — credentials and traffic go
  in cleartext.
- Limit relay exposure by IP when possible; login is rate-limited by default.
- Keep the protocol stack uniform: a mixed relay/web/wrapper version fails
  closed on the protocol-v19 gate. Upgrade all tiers in one maintenance window.
- Preserve config on upgrade and verify health (`/healthz`) after any upgrade or
  rollback.

## Reporting a vulnerability

File an issue in the repository. Do not include live `.env` values, cookies,
keystores, or signing material in any report.
