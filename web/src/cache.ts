// IndexedDB cache: per-session turns + lastSeq.
//
// Opening the app restores history instantly from local storage and asks the
// wrapper only for the delta (seq > lastSeq) instead of replaying the whole
// buffer — that's what makes other apps feel instant vs. our "一段段补发".
//
// Writes are coalesced (turns change every frame during streaming).

import {
  isSessionControl, sessionControlTargetsSid, type SessionControl,
} from "./protocol.ts";

const DB_NAME = "cc_remote_cache";
const STORE = "sessions";
const SCHEMA = 1;

// Bump when the cached turn shape changes in a way old entries can't be
// trusted (e.g. a past bug left tool-only turns without text). Old entries are
// ignored -> client falls back to a full buffer replay (text + tools restored).
// v6 adds assistant channels and structured process blocks. v7 binds cached
// turns to the backend's authoritative history revision so a destructive
// rewind or wrapper restart can never merge removed completed turns back in.
// v8 persists the revisioned v15 SessionControl snapshot so instant hydration
// cannot fall back to an unrevisioned terminal lock. v9 discards projections
// written by the old open-turn History merge, which could persist duplicate
// assistant blocks after switching away from and back to a running session.
const CACHE_VER = 9;
const MAX_CACHE_SESSIONS = 64;
const MAX_CACHE_TURNS = 100;
const MAX_CACHE_BYTES = 2 * 1024 * 1024;

interface ReplayRecord {
  sid: string;
  lastSeq: number;
  generation?: string;
  control?: SessionControl;
  savedAt: number;
}

function retainNewest(records: ReplayRecord[], record: ReplayRecord): void {
  records.push(record);
  records.sort((a, b) => b.savedAt - a.savedAt);
  if (records.length > MAX_CACHE_SESSIONS) records.length = MAX_CACHE_SESSIONS;
}

/** Bound the structured clone written for one session. Large image turns are
 * intentionally omitted from the instant-paint cache; authoritative history is
 * fetched on focus, while the replay cursor remains stored separately. */
export function boundCachedTurns(turns: unknown[]): unknown[] {
  const candidates = turns.slice(-MAX_CACHE_TURNS).map((turn) => {
    if (!turn || typeof turn !== "object" || Array.isArray(turn)) return turn;
    const record = turn as Record<string, unknown>;
    if (!Array.isArray(record.files)) return turn;
    // QueryFile.data is needed only until the command is put on the wire.  The
    // transcript protocol intentionally echoes metadata only, so make the
    // best-effort local cache obey the same rule even if an optimistic turn from
    // an older caller still contains the original body.
    return {
      ...record,
      files: record.files.map((file) => {
        const filename = file && typeof file === "object"
          && typeof (file as Record<string, unknown>).filename === "string"
          ? (file as Record<string, unknown>).filename as string
          : "";
        return { filename, data: "" };
      }),
    };
  });
  const kept: unknown[] = [];
  let bytes = 2; // []
  for (let i = candidates.length - 1; i >= 0; i--) {
    let encoded: string | undefined;
    try { encoded = JSON.stringify(candidates[i]); } catch { continue; }
    if (encoded === undefined) continue;
    const size = new TextEncoder().encode(encoded).byteLength + 1;
    if (size > MAX_CACHE_BYTES || bytes + size > MAX_CACHE_BYTES) continue;
    kept.unshift(candidates[i]);
    bytes += size;
  }
  return kept;
}

async function pruneCacheStore(d: IDBDatabase): Promise<void> {
  await new Promise<void>((resolve) => {
    const tx = d.transaction(STORE, "readwrite");
    const store = tx.objectStore(STORE);
    const newest: Array<{ key: IDBValidKey; savedAt: number }> = [];
    const req = store.openCursor();
    req.onsuccess = () => {
      const cur = req.result;
      if (!cur) return;
      const value = cur.value as (CachedSession & { v?: number }) | undefined;
      if (!value || value.v !== CACHE_VER) {
        cur.delete();
        cur.continue();
        return;
      }
      newest.push({
        key: cur.key,
        savedAt: typeof value.savedAt === "number" ? value.savedAt : 0,
      });
      newest.sort((a, b) => b.savedAt - a.savedAt);
      if (newest.length > MAX_CACHE_SESSIONS) {
        const evicted = newest.pop();
        if (evicted) {
          if (evicted.key === cur.key) cur.delete();
          else store.delete(evicted.key);
        }
      }
      cur.continue();
    };
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
    tx.onabort = () => resolve();
  });
}

export interface CachedSession {
  turns: unknown[];
  lastSeq: number;
  revision: string;
  generation?: string;
  control?: SessionControl | null;
  savedAt: number;
}

/** Validate and bind a cache control snapshot to its IndexedDB row key. */
export function controlForCachedSession(
  sessionId: string, value: unknown,
): SessionControl | undefined {
  return isSessionControl(value) && sessionControlTargetsSid(value, sessionId)
    ? value : undefined;
}

let dbPromise: Promise<IDBDatabase> | null = null;
function db(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, SCHEMA);
    req.onupgradeneeded = () => {
      const d = req.result;
      if (!d.objectStoreNames.contains(STORE)) d.createObjectStore(STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

export async function loadSession(sessionId: string): Promise<CachedSession | null> {
  if (typeof indexedDB === "undefined" || invalidatedSessions.has(sessionId)) {
    return null;
  }
  try {
    const d = await db();
    return await new Promise<CachedSession | null>((resolve) => {
      const tx = d.transaction(STORE, "readonly");
      const req = tx.objectStore(STORE).get(sessionId);
      req.onsuccess = () => {
        const r = req.result as (CachedSession & { v?: number }) | undefined;
        // Ignore stale caches from before the current shape (v must match).
        if (invalidatedSessions.has(sessionId) || !r || r.v !== CACHE_VER
            || typeof r.revision !== "string" || !r.revision
            || (r.control != null && !isSessionControl(r.control))) {
          resolve(null);
          return;
        }
        resolve({
          ...r,
          control: controlForCachedSession(sessionId, r.control),
        });
      };
      req.onerror = () => resolve(null);
    });
  } catch {
    return null;
  }
}

/** All cached sessions' last-seen seq, for seeding reconnect cursors so the
 *  wrapper replays only the DELTA (seq > lastSeq) instead of flooding the full
 *  history of every resident session on every reconnect. */
export async function loadAllReplayState(): Promise<{
  cursors: Record<string, number>;
  generations: Record<string, string>;
  controls: Record<string, SessionControl>;
}> {
  if (typeof indexedDB === "undefined") {
    return { cursors: {}, generations: {}, controls: {} };
  }
  try {
    const d = await db();
    const replay = await new Promise<{
      cursors: Record<string, number>;
      generations: Record<string, string>;
      controls: Record<string, SessionControl>;
    }>((resolve) => {
      const records: ReplayRecord[] = [];
      const req = d.transaction(STORE, "readonly").objectStore(STORE).openCursor();
      req.onsuccess = () => {
        const cur = req.result;
        if (!cur) {
          const cursors: Record<string, number> = {};
          const generations: Record<string, string> = {};
          const controls: Record<string, SessionControl> = {};
          for (const record of records) {
            if (record.lastSeq > 0) {
              cursors[record.sid] = record.lastSeq;
            }
            if (record.generation) generations[record.sid] = record.generation;
            if (record.control) controls[record.sid] = record.control;
          }
          resolve({ cursors, generations, controls });
          return;
        }
        const r = cur.value as (CachedSession & { v?: number }) | undefined;
        if (r && r.v === CACHE_VER
            && typeof r.revision === "string" && r.revision
            && typeof r.lastSeq === "number"
            && (r.lastSeq > 0 || isSessionControl(r.control))) {
          const sid = String(cur.key);
          const control = controlForCachedSession(sid, r.control);
          retainNewest(records, {
            sid,
            lastSeq: r.lastSeq,
            generation: typeof r.generation === "string" && r.generation
              ? r.generation
              : (control?.generation ?? undefined),
            control,
            savedAt: typeof r.savedAt === "number" ? r.savedAt : 0,
          });
        }
        cur.continue();
      };
      req.onerror = () => resolve({ cursors: {}, generations: {}, controls: {} });
    });
    // Queue a bounded cleanup after the readonly cursor transaction. This also
    // repairs databases created by older builds that never evicted sessions.
    void pruneCacheStore(d);
    return replay;
  } catch {
    return { cursors: {}, generations: {}, controls: {} };
  }
}

const pending = new Map<string, {
  sid: string; turns: unknown[]; lastSeq: number; revision: string;
  generation?: string;
  control?: SessionControl | null;
  epoch: number;
}>();
// A destructive history mutation must invalidate both the committed IDB row
// and any debounce/in-flight write that captured the pre-mutation turns.
const sessionEpochs = new Map<string, number>();
const invalidatedSessions = new Set<string>();
const invalidationTasks = new Map<string, {
  epoch: number;
  task: Promise<void>;
}>();
let saveTimer: ReturnType<typeof setTimeout> | null = null;

function sessionEpoch(sessionId: string): number {
  return sessionEpochs.get(sessionId) ?? 0;
}

/** Coalesced write — call freely on every turns change; actual IDB write is
 *  debounced 400ms so streaming doesn't hammer IndexedDB. */
export function saveSession(
  sessionId: string, turns: unknown[], lastSeq: number, revision: string,
  generation?: string, control?: SessionControl | null,
): void {
  if (typeof indexedDB === "undefined" || !sessionId || !revision) return;
  if (invalidatedSessions.has(sessionId)) return;
  if (!pending.has(sessionId) && pending.size >= MAX_CACHE_SESSIONS) {
    const oldest = pending.keys().next().value as string | undefined;
    if (oldest) pending.delete(oldest);
  }
  pending.set(sessionId, {
    sid: sessionId,
    turns,
    lastSeq,
    revision,
    generation,
    control: controlForCachedSession(sessionId, control),
    epoch: sessionEpoch(sessionId),
  });
  if (saveTimer) return;
  saveTimer = setTimeout(flush, 400);
}

async function flush(): Promise<void> {
  saveTimer = null;
  const jobs = Array.from(pending.values());
  pending.clear();
  if (!jobs.length) return;
  try {
    const d = await db();
    await new Promise<void>((resolve) => {
      const tx = d.transaction(STORE, "readwrite");
      const store = tx.objectStore(STORE);
      for (const job of jobs) {
        if (invalidatedSessions.has(job.sid)
            || job.epoch !== sessionEpoch(job.sid)) continue;
        const turns = boundCachedTurns(job.turns);
        store.put(
          { v: CACHE_VER, turns, lastSeq: job.lastSeq,
            revision: job.revision, generation: job.generation,
            control: job.control,
            savedAt: Date.now() }, job.sid);
      }
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
    });
    await pruneCacheStore(d);
  } catch { /* ignore — cache is best-effort */ }
  if (pending.size && !saveTimer) saveTimer = setTimeout(flush, 400);
}

/** Remove one session after a destructive transcript mutation.

    The epoch also makes a write already copied out of `pending` harmless. Keep
    reads/writes blocked until App observes a subsequent authoritative History. */
export async function invalidateSessionCache(sessionId: string): Promise<void> {
  if (!sessionId) return;
  const epoch = sessionEpoch(sessionId) + 1;
  sessionEpochs.set(sessionId, epoch);
  invalidatedSessions.add(sessionId);
  pending.delete(sessionId);
  const task = (async () => {
    if (typeof indexedDB === "undefined") return;
    try {
      const d = await db();
      await new Promise<void>((resolve) => {
        const tx = d.transaction(STORE, "readwrite");
        tx.objectStore(STORE).delete(sessionId);
        tx.oncomplete = () => resolve();
        tx.onerror = () => resolve();
        tx.onabort = () => resolve();
      });
    } catch { /* best-effort cache invalidation */ }
  })();
  invalidationTasks.set(sessionId, { epoch, task });
  await task;
  if (invalidationTasks.get(sessionId)?.task === task) {
    invalidationTasks.delete(sessionId);
  }
}

/** Re-enable future cache writes after an authoritative History replacement. */
export async function allowSessionCache(sessionId: string): Promise<void> {
  const epoch = sessionEpoch(sessionId);
  await invalidationTasks.get(sessionId)?.task;
  if (sessionEpoch(sessionId) === epoch) invalidatedSessions.delete(sessionId);
}

/** Explicit logout removes locally cached prompts and base64 images. */
export async function clearCache(): Promise<void> {
  pending.clear();
  invalidatedSessions.clear();
  sessionEpochs.clear();
  invalidationTasks.clear();
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
  }
  if (typeof indexedDB === "undefined") return;
  try {
    const d = await db();
    await new Promise<void>((resolve) => {
      const tx = d.transaction(STORE, "readwrite");
      tx.objectStore(STORE).clear();
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
      tx.onabort = () => resolve();
    });
  } catch { /* best-effort local cleanup */ }
}
