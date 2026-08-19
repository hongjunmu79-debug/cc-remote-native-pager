import type { HistoryImage } from "./protocol";

export type HistoryImageVariant = "thumbnail" | "full";

export interface HistoryImageAsset {
  status: "loading" | "ready" | "error";
  mediaType?: string;
  data?: string;
  width?: number;
  height?: number;
}

interface AssetEntry extends HistoryImageAsset {
  sid: string;
  turnId: string;
  imageId: string;
  variant: HistoryImageVariant;
  lastUsed: number;
}

interface PendingAsset {
  key: string;
  sid: string;
  turnId: string;
  imageId: string;
  variant: HistoryImageVariant;
  requestId: string;
  revision?: string | null;
}

export interface HistoryImageRequest extends Omit<PendingAsset, "key"> {}

export const MAX_HISTORY_IMAGE_ASSETS = 48;

export function historyImageAssetKey(
  turnId: string,
  imageId: string,
  variant: HistoryImageVariant,
): string {
  return `${turnId}\u0000${imageId}\u0000${variant}`;
}

/** Bounded in-memory cache for summary-page images. Summary history carries
 * metadata only; thumbnails enter this cache near the viewport and originals
 * only after an explicit preview gesture. */
export class HistoryImageAssetCache {
  private readonly entries = new Map<string, AssetEntry>();
  private readonly pending = new Map<string, PendingAsset>();
  private readonly limit: number;
  private tick = 0;

  constructor(limit = MAX_HISTORY_IMAGE_ASSETS) {
    this.limit = limit;
  }

  private key(
    sid: string,
    turnId: string,
    imageId: string,
    variant: HistoryImageVariant,
  ): string {
    return `${sid}\u0000${historyImageAssetKey(turnId, imageId, variant)}`;
  }

  begin(request: HistoryImageRequest): boolean {
    const key = this.key(
      request.sid, request.turnId, request.imageId, request.variant,
    );
    if (this.entries.has(key)) return false;
    while (this.entries.size >= this.limit) {
      let oldestKey: string | null = null;
      let oldestTick = Number.POSITIVE_INFINITY;
      for (const [candidateKey, entry] of this.entries) {
        if (entry.status === "loading" || entry.lastUsed >= oldestTick) continue;
        oldestKey = candidateKey;
        oldestTick = entry.lastUsed;
      }
      if (!oldestKey) return false;
      this.entries.delete(oldestKey);
    }
    this.entries.set(key, {
      sid: request.sid,
      turnId: request.turnId,
      imageId: request.imageId,
      variant: request.variant,
      status: "loading",
      lastUsed: ++this.tick,
    });
    this.pending.set(request.requestId, { ...request, key });
    return true;
  }

  has(
    sid: string,
    turnId: string,
    imageId: string,
    variant: HistoryImageVariant,
  ): boolean {
    return this.entries.has(this.key(sid, turnId, imageId, variant));
  }

  cancel(requestId: string): void {
    const request = this.pending.get(requestId);
    if (!request) return;
    this.pending.delete(requestId);
    const entry = this.entries.get(request.key);
    if (entry?.status === "loading") this.entries.delete(request.key);
  }

  accept(event: HistoryImage): boolean {
    const request = this.pending.get(event.request_id);
    if (!request
        || event.session_id !== request.sid
        || event.turn_id !== request.turnId
        || event.image_id !== request.imageId
        || event.variant !== request.variant
        || (request.revision != null && event.revision !== request.revision)) {
      return false;
    }
    this.pending.delete(event.request_id);
    const ready = !!event.data && !!event.media_type && !event.error;
    this.entries.set(request.key, {
      sid: request.sid,
      turnId: request.turnId,
      imageId: request.imageId,
      variant: request.variant,
      status: ready ? "ready" : "error",
      mediaType: ready ? event.media_type ?? undefined : undefined,
      data: ready ? event.data ?? undefined : undefined,
      width: event.width ?? undefined,
      height: event.height ?? undefined,
      lastUsed: ++this.tick,
    });
    return true;
  }

  forSession(sid: string): Record<string, HistoryImageAsset> {
    const assets: Record<string, HistoryImageAsset> = {};
    for (const entry of this.entries.values()) {
      if (entry.sid !== sid) continue;
      entry.lastUsed = ++this.tick;
      assets[historyImageAssetKey(entry.turnId, entry.imageId, entry.variant)] = {
        status: entry.status,
        mediaType: entry.mediaType,
        data: entry.data,
        width: entry.width,
        height: entry.height,
      };
    }
    return assets;
  }

  dropSession(sid: string): boolean {
    let changed = false;
    for (const [key, entry] of this.entries) {
      if (entry.sid !== sid) continue;
      this.entries.delete(key);
      changed = true;
    }
    for (const [requestId, request] of this.pending) {
      if (request.sid === sid) this.pending.delete(requestId);
    }
    return changed;
  }

  clear(): void {
    this.entries.clear();
    this.pending.clear();
  }
}
