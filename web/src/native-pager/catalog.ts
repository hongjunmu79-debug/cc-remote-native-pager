import type { Engine, SessionInfo, Space } from "../protocol.ts";
import { compareSessionsByActivity } from "../session-order.ts";

const NATIVE_ENGINES: readonly Engine[] = ["claude", "codex"];

export type SessionCatalogs = Readonly<Record<string, readonly SessionInfo[]>>;

function normalizeSession(
  session: SessionInfo,
  engine: Engine,
  space: Space,
): SessionInfo {
  if (session.engine === engine && (session.space ?? "code") === space) {
    return session;
  }
  return { ...session, engine, space };
}

/**
 * Build the native dashboard's engine-neutral Code catalog.
 *
 * The regular Web UI deliberately owns one engine/surface at a time. The native
 * pager is a monitoring surface, so it consumes both cached Code catalogs without
 * feeding foreign rows into the active chat reducer. This keeps background list
 * refreshes cheap and makes command routing explicit at the pager boundary.
 */
export function nativeCodeCatalog(catalogs: SessionCatalogs): SessionInfo[] {
  const unique = new Map<string, SessionInfo>();
  for (const engine of NATIVE_ENGINES) {
    const key = `code:${engine}`;
    for (const session of catalogs[key] ?? []) {
      const normalized = normalizeSession(session, engine, "code");
      const identity = `${engine}\u0000${normalized.session_id}`;
      unique.set(identity, normalized);
    }
  }
  return [...unique.values()].sort(compareSessionsByActivity);
}

export function findNativeSession(
  catalogs: SessionCatalogs,
  sessionId: string,
): SessionInfo | undefined {
  return nativeCodeCatalog(catalogs).find(
    (session) => session.session_id === sessionId,
  );
}
