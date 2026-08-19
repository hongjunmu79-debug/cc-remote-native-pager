import type { HistoryImageAsset } from "./history-image-assets";
import type { QueryImg } from "./protocol";

interface TurnImageSource {
  id: string;
  images?: QueryImg[];
}

/** Keeps the already-painted optimistic image alive while a summary History
 * swaps the turn to payload-free image references and fetches its thumbnail. */
export class TurnImagePreviewCache {
  private sid: string | null = null;
  private readonly previews = new Map<string, QueryImg[]>();

  update(sid: string | null, turns: readonly TurnImageSource[]): void {
    if (this.sid !== sid) {
      this.sid = sid;
      this.previews.clear();
    }
    const retained = new Set(turns.map((turn) => turn.id));
    for (const id of this.previews.keys()) {
      if (!retained.has(id)) this.previews.delete(id);
    }
    for (const turn of turns) {
      if (turn.images?.length && !this.previews.has(turn.id)) {
        this.previews.set(
          turn.id,
          turn.images.map((image) => ({ ...image })),
        );
      }
    }
  }

  get(turnId: string, index: number): QueryImg | undefined {
    return this.previews.get(turnId)?.[index];
  }

  release(turnId: string): void {
    this.previews.delete(turnId);
  }
}

export function historyImageDisplaySource(
  asset: HistoryImageAsset | undefined,
  fallback: QueryImg | undefined,
): string | null {
  if (asset?.status === "ready" && asset.data && asset.mediaType) {
    return `data:${asset.mediaType};base64,${asset.data}`;
  }
  return fallback
    ? `data:${fallback.media_type};base64,${fallback.data}`
    : null;
}
