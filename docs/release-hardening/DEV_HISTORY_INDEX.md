# Development history and handoff index

This index maps the repository's dated design docs and the release-hardening
deliverables. It is the sanitized handoff: it contains no raw ADB dumps, user
conversations, credentials, cookies, keystores, live `.env` files, or the full
local handoff archive. Anything that could not be published is intentionally
excluded and only referenced generically.

## Dated design docs

| Doc | Date | Topic |
| --- | --- | --- |
| [`docs/2026-08-16-dual-engine-handoff-hardening.md`](../2026-08-16-dual-engine-handoff-hardening.md) | 2026-08-16 | Dual-engine (Claude + Codex) native pager projection, Android IME handling on legacy WebView, Codex active-writer handoff, reconnect/durability, and the signed-pager release gate at that time. |
| [`docs/2026-08-16-windows-takeover-engine-filter.md`](../2026-08-16-windows-takeover-engine-filter.md) | 2026-08-16 | Windows Claude ownership discovery and exact-handle takeover, plus the `ALL / CLAUDE / CODEX` Agent Deck engine filter. |

These are design/verification records, not runbooks. For current behavior see
`AGENTS.md` (maintainer invariants), the `README` (user journeys), and the
native-pager docs below.

## Native-pager architecture docs

| Doc | Topic |
| --- | --- |
| [`docs/native-pager/ARCHITECTURE.md`](../native-pager/ARCHITECTURE.md) | Accepted architecture: one WebView owns auth/WebSocket/chat; Compose is the bounded projection; exact-origin bridge; licensing note. |
| [`docs/native-pager/BRIDGE_PROTOCOL.md`](../native-pager/BRIDGE_PROTOCOL.md) | Versioned WebMessage bridge wire contract between the web reducer and the Kotlin native host. |
| [`docs/native-pager/DEPLOYMENT.md`](../native-pager/DEPLOYMENT.md) | Release inputs, build steps, web deployment, APK upgrade verification, and rollback. |
| [`docs/native-pager/TEST_STRATEGY.md`](../native-pager/TEST_STRATEGY.md) | Test strategy for the native pager and bridge. |

## Release-hardening deliverables

| Doc | Topic |
| --- | --- |
| [`RELEASE_MAINTAINER.md`](RELEASE_MAINTAINER.md) | Release tag contract, workflows, building/signing assets, checksums/attestations, rollback, pre-flight checklist. |
| [`SECURITY.md`](SECURITY.md) | Threat model, authentication boundaries, Android endpoint and WebView enforcement, Windows hardening, operational guidance. |
| [`IMPLEMENTATION_REPORT.md`](IMPLEMENTATION_REPORT.md) | Final implementation report for the production-release hardening work: files changed, tests, limitations, pushed commit SHA. |

## Provenance

`docs/UPSTREAM.md` records the upstream `muggle-stack/cc-remote` project, the
MIT license, and what the pager adaptation adds (native Android pager, Windows
packaging, canonical release metadata).

## Canonical version source

All release versions/identity live in `deploy/release-metadata.json` and are
validated by `deploy/validate_release_metadata.py` (see
[`RELEASE_MAINTAINER.md`](RELEASE_MAINTAINER.md)).
