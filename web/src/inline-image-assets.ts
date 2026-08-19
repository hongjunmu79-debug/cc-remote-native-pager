import type { PreviewAsset } from "./protocol.ts";
import { parseLocalFileTarget } from "./file-link.ts";
import { imageDimensionsFromBase64 } from "./img.ts";

export type MessageImageTarget =
  | { kind: "local"; value: string }
  | { kind: "external"; value: string }
  | { kind: "blocked"; value: "" };

const IMAGE_SUFFIX = /\.(?:png|jpe?g|gif|webp|avif)$/i;

/** Classify an image emitted inside a chat message. Local filesystem paths are
 * never assigned to an HTML src; the caller must materialize them over the
 * authenticated preview-asset channel. */
export function classifyMessageImageTarget(rawTarget: string): MessageImageTarget {
  const target = rawTarget.trim();
  if (!target || target.startsWith("#") || target.startsWith("//")) {
    return { kind: "blocked", value: "" };
  }
  if (/^https?:\/\//i.test(target)) {
    return { kind: "external", value: target };
  }
  const local = parseLocalFileTarget(target);
  if (!local || !IMAGE_SUFFIX.test(local.path)) {
    return { kind: "blocked", value: "" };
  }
  return { kind: "local", value: local.path };
}

export interface InlineImageAsset {
  status: "loading" | "ready" | "error";
  mediaType?: string;
  data?: string;
  width?: number;
  height?: number;
}

interface AssetEntry extends InlineImageAsset {
  sid: string;
  path: string;
  lastUsed: number;
}

interface PendingAsset {
  key: string;
  sid: string;
  path: string;
  previewId: string;
  requestId: string;
}

export interface InlineImageRequest {
  sid: string;
  path: string;
  previewId: string;
  requestId: string;
}

export const MAX_INLINE_IMAGE_ASSETS = 24;

/** Small in-memory, cross-session LRU for images visible in chat. It validates
 * every response against the exact sid/path/preview/request tuple so a delayed
 * background frame cannot fill another session's image. */
export class InlineImageAssetCache {
  private readonly entries = new Map<string, AssetEntry>();
  private readonly pending = new Map<string, PendingAsset>();
  private readonly limit: number;
  private tick = 0;

  constructor(limit = MAX_INLINE_IMAGE_ASSETS) {
    this.limit = limit;
  }

  private key(sid: string, path: string): string {
    return `${sid}\u0000${path}`;
  }

  private evictOneSettled(): boolean {
    let oldestKey: string | null = null;
    let oldestTick = Number.POSITIVE_INFINITY;
    for (const [key, entry] of this.entries) {
      if (entry.status === "loading" || entry.lastUsed >= oldestTick) continue;
      oldestKey = key;
      oldestTick = entry.lastUsed;
    }
    if (!oldestKey) return false;
    this.entries.delete(oldestKey);
    return true;
  }

  begin(request: InlineImageRequest): boolean {
    const key = this.key(request.sid, request.path);
    if (this.entries.has(key)) return false;
    while (this.entries.size >= this.limit) {
      if (!this.evictOneSettled()) return false;
    }
    this.entries.set(key, {
      sid: request.sid,
      path: request.path,
      status: "loading",
      lastUsed: ++this.tick,
    });
    this.pending.set(request.requestId, { ...request, key });
    return true;
  }

  has(sid: string, path: string): boolean {
    return this.entries.has(this.key(sid, path));
  }

  cancel(requestId: string): void {
    const request = this.pending.get(requestId);
    if (!request) return;
    this.pending.delete(requestId);
    const entry = this.entries.get(request.key);
    if (entry?.status === "loading") this.entries.delete(request.key);
  }

  accept(event: PreviewAsset): boolean {
    const request = this.pending.get(event.request_id);
    if (!request || event.sid !== request.sid || event.path !== request.path
        || event.preview_id !== request.previewId) return false;
    this.pending.delete(event.request_id);
    const ready = !!event.data && !!event.media_type && !event.error;
    const dimensions = ready
      ? imageDimensionsFromBase64(event.data ?? "", event.media_type ?? "")
      : null;
    this.entries.set(request.key, {
      sid: request.sid,
      path: request.path,
      status: ready ? "ready" : "error",
      mediaType: ready ? event.media_type ?? undefined : undefined,
      data: ready ? event.data ?? undefined : undefined,
      ...(dimensions ? { width: dimensions[0], height: dimensions[1] } : {}),
      lastUsed: ++this.tick,
    });
    return true;
  }

  forSession(sid: string): Record<string, InlineImageAsset> {
    const assets: Record<string, InlineImageAsset> = {};
    for (const entry of this.entries.values()) {
      if (entry.sid !== sid) continue;
      entry.lastUsed = ++this.tick;
      assets[entry.path] = {
        status: entry.status,
        mediaType: entry.mediaType,
        data: entry.data,
        ...(entry.width && entry.height
          ? { width: entry.width, height: entry.height }
          : {}),
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
