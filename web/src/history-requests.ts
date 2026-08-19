export interface HistoryRequestKey {
  sid: string;
  before?: string | null;
  limit: number;
  generation?: string | null;
  revision?: string | null;
}

interface PendingHistoryRequest extends HistoryRequestKey {
  connectionEpoch: number;
  startedAt: number;
}

/** One authority for every focus/reconnect/rebuild history trigger.
 *
 * App historically had four independent call sites.  Each generated a new
 * reliable command id, so wrapper-side command dedupe could not recognize that
 * they all requested the same page.  This coordinator merges those triggers
 * within a connection while still allowing a newer rollback revision, wrapper
 * generation, or pagination cursor to issue its own read.
 */
export class HistoryRequestCoordinator {
  private connectionEpoch = 0;
  private readonly pending = new Map<string, PendingHistoryRequest>();
  private readonly now: () => number;
  private readonly timeoutMs: number;

  constructor(
    now: () => number = () => Date.now(),
    timeoutMs = 15_000,
  ) {
    this.now = now;
    this.timeoutMs = timeoutMs;
  }

  beginConnection(): void {
    this.connectionEpoch += 1;
    this.pending.clear();
  }

  clear(): void {
    this.pending.clear();
  }

  private static key(request: HistoryRequestKey): string {
    return `${request.sid}\u0000${request.before ?? ""}\u0000${request.limit}`;
  }

  request(request: HistoryRequestKey, send: () => void): boolean {
    const key = HistoryRequestCoordinator.key(request);
    const existing = this.pending.get(key);
    const now = this.now();
    if (existing && existing.connectionEpoch === this.connectionEpoch
        && now - existing.startedAt < this.timeoutMs) {
      // A newly revealed destructive revision must issue a replacement even
      // when an ordinary focus read is already in flight.  The reverse is safe:
      // a later generic reconnect trigger can share the revision-bound read.
      const sameRevision = existing.revision
        ? !request.revision || existing.revision === request.revision
        : !request.revision;
      const sameGeneration = !existing.generation || !request.generation
        || existing.generation === request.generation;
      if (sameRevision && sameGeneration) {
        // Upgrade an early focus request once replay reveals the exact epochs.
        if (!existing.generation && request.generation) {
          existing.generation = request.generation;
        }
        return false;
      }
    }
    this.pending.set(key, {
      ...request,
      connectionEpoch: this.connectionEpoch,
      startedAt: now,
    });
    send();
    return true;
  }

  complete(response: {
    session_id: string;
    before?: string | null;
    generation?: string | null;
    revision?: string | null;
  }): void {
    for (const [key, pending] of this.pending) {
      if (pending.sid !== response.session_id
          || (pending.before ?? "") !== (response.before ?? "")) continue;
      if (pending.generation && response.generation
          && pending.generation !== response.generation) continue;
      if (pending.revision && response.revision
          && pending.revision !== response.revision) continue;
      this.pending.delete(key);
    }
  }

  size(): number {
    return this.pending.size;
  }
}
