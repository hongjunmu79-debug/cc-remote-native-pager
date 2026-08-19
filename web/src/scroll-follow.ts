export interface ScrollMetrics {
  scrollHeight: number;
  scrollTop: number;
  clientHeight: number;
}

export interface ScrollFollowSnapshot {
  followOutput: boolean;
  nearBottom: boolean;
}

export interface BottomMeasurement {
  distance: number;
  atBottom: boolean;
  nearBottom: boolean;
}

export const NEAR_BOTTOM_PX = 80;
export const AT_BOTTOM_PX = 2;
export const AUTO_LOAD_HISTORY_TOP_PX = 72;
export const HISTORY_EDGE_PX = 1;
export const HISTORY_ANCHOR_EPSILON_PX = 0.5;

const SCROLL_DIRECTION_EPSILON_PX = 0.5;

export function measureBottom(metrics: ScrollMetrics): BottomMeasurement {
  const distance = Math.max(
    0,
    metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight,
  );
  return {
    distance,
    atBottom: distance <= AT_BOTTOM_PX,
    nearBottom: distance <= NEAR_BOTTOM_PX,
  };
}

export interface HistoryAnchorPoint {
  anchorTurnId: string;
  oldestTurnId: string | null;
  anchorOffset: number;
}

export interface HistoryAnchorTransaction extends HistoryAnchorPoint {
  sid: string | null;
  revision: string | null;
  before: string | null;
  source: "local" | "server";
  generation: number;
  phase: "pending" | "rendering" | "applied";
}

export interface HistoryPageBoundary {
  sid: string | null;
  revision: string | null;
  cursor: string | null;
  hasMore: boolean;
}

export type HistoryPageStatus = "pending" | "complete" | "stale";

/** Compare an older-page request with the authoritative boundary installed by
 * the reducer. Runtime turn count is intentionally absent: bounded projection
 * can replace rows while retaining the same length. */
export function historyPageStatus(
  transaction: HistoryAnchorTransaction,
  boundary: HistoryPageBoundary,
): HistoryPageStatus {
  if (transaction.sid !== boundary.sid
      || transaction.revision !== boundary.revision) return "stale";
  if (transaction.source !== "server") return "complete";
  return transaction.before !== boundary.cursor || !boundary.hasMore
    ? "complete" : "pending";
}

/** Owns the short-lived viewport transaction across an async history prepend.
 * The old/new boundary is frozen when the request starts. Rubber-banding at
 * the history edge keeps the request alive, but leaving that edge before the
 * response cancels it so a delayed page cannot move the user's viewport. */
export class HistoryAnchorController {
  private nextGeneration = 1;
  private transaction: HistoryAnchorTransaction | null = null;

  begin(input: Omit<HistoryAnchorTransaction, "generation" | "phase">): number {
    const generation = this.nextGeneration++;
    this.transaction = { ...input, generation, phase: "pending" };
    return generation;
  }

  current(): HistoryAnchorTransaction | null {
    return this.transaction ? { ...this.transaction } : null;
  }

  observeUserScroll(stillAtHistoryEdge: boolean): void {
    if (!this.transaction) return;
    if (this.transaction.phase === "pending" && stillAtHistoryEdge) return;
    this.transaction = null;
  }

  markRendering(generation: number): boolean {
    if (!this.transaction || this.transaction.generation !== generation
        || this.transaction.phase !== "pending") return false;
    this.transaction = { ...this.transaction, phase: "rendering" };
    return true;
  }

  markApplied(generation: number): boolean {
    if (!this.transaction || this.transaction.generation !== generation) return false;
    this.transaction = { ...this.transaction, phase: "applied" };
    return true;
  }

  /** Preserve the user's new reading row after an installed page while the
   * original touch is still active. The transaction stays alive so a deferred
   * virtualizer measurement can still be corrected against this newer row. */
  rebase(generation: number, point: HistoryAnchorPoint): boolean {
    if (!this.transaction || this.transaction.generation !== generation
        || this.transaction.phase !== "applied") return false;
    this.transaction = { ...this.transaction, ...point };
    return true;
  }

  cancel(generation?: number): boolean {
    if (!this.transaction
        || (generation != null && this.transaction.generation !== generation)) {
      return false;
    }
    this.transaction = null;
    return true;
  }
}

/** A pull gesture may request at most one older page. Network completion while
 * the finger is still down must not cascade through the entire history. */
export class OlderHistoryLoadGate {
  private gestureActive = false;
  private usedInGesture = false;
  private pending = false;

  beginGesture(): void {
    if (this.gestureActive) return;
    this.gestureActive = true;
    this.usedInGesture = false;
  }

  acquire(): boolean {
    if (!this.gestureActive || this.usedInGesture || this.pending) return false;
    this.usedInGesture = true;
    this.pending = true;
    return true;
  }

  complete(): void {
    this.pending = false;
  }

  endGesture(): void {
    this.gestureActive = false;
  }
}

/** Auto-pagination is gesture-driven, never a side effect of merely painting a
 * short session at scrollTop=0. The threshold hides the network round trip
 * behind the final few pixels of the user's upward scroll. */
export function shouldAutoLoadOlderHistory(
  metrics: ScrollMetrics,
  movingTowardHistory: boolean,
  canLoadOlder: boolean,
): boolean {
  return canLoadOlder
    && movingTowardHistory
    && metrics.scrollTop <= AUTO_LOAD_HISTORY_TOP_PX;
}

/** Wheel/touch handlers use this only when the viewport cannot scroll farther.
 * Movement before the edge is handled by the later scroll event, after the
 * browser has established the position that history prepending must preserve. */
export function isAtHistoryEdge(metrics: ScrollMetrics): boolean {
  return metrics.scrollTop <= HISTORY_EDGE_PX;
}

/**
 * Keeps output-follow intent separate from the current geometric position.
 * Being close to the bottom must never re-enable following after the user has
 * asked to read history; only reaching the actual bottom (or an explicit
 * resume) does that.
 */
export class ScrollFollowController {
  private current: ScrollFollowSnapshot = {
    followOutput: true,
    nearBottom: true,
  };

  private lastScrollTop = 0;

  snapshot(): ScrollFollowSnapshot {
    return { ...this.current };
  }

  isFollowing(): boolean {
    return this.current.followOutput;
  }

  reset(metrics: ScrollMetrics): ScrollFollowSnapshot {
    this.lastScrollTop = metrics.scrollTop;
    this.current = {
      followOutput: true,
      nearBottom: measureBottom(metrics).nearBottom,
    };
    return this.snapshot();
  }

  pause(metrics: ScrollMetrics): ScrollFollowSnapshot {
    this.lastScrollTop = metrics.scrollTop;
    this.current = {
      followOutput: false,
      nearBottom: measureBottom(metrics).nearBottom,
    };
    return this.snapshot();
  }

  resume(metrics: ScrollMetrics): ScrollFollowSnapshot {
    this.lastScrollTop = metrics.scrollTop;
    this.current = {
      followOutput: true,
      nearBottom: measureBottom(metrics).nearBottom,
    };
    return this.snapshot();
  }

  /** Record a DOM scrollTop write without treating its later scroll event as intent. */
  recordProgrammaticScroll(metrics: ScrollMetrics): ScrollFollowSnapshot {
    this.lastScrollTop = metrics.scrollTop;
    this.current = {
      ...this.current,
      nearBottom: measureBottom(metrics).nearBottom,
    };
    return this.snapshot();
  }

  /** Content resized. Update geometry, but never infer user intent from layout. */
  observeLayout(metrics: ScrollMetrics): ScrollFollowSnapshot {
    this.current = {
      ...this.current,
      nearBottom: measureBottom(metrics).nearBottom,
    };
    return this.snapshot();
  }

  /** Handle a real viewport movement, including scrollbar and keyboard scrolls. */
  observeScroll(metrics: ScrollMetrics): ScrollFollowSnapshot {
    const movingTowardHistory =
      metrics.scrollTop < this.lastScrollTop - SCROLL_DIRECTION_EPSILON_PX;
    const movingTowardLatest =
      metrics.scrollTop > this.lastScrollTop + SCROLL_DIRECTION_EPSILON_PX;
    const measurement = measureBottom(metrics);

    let followOutput = this.current.followOutput;
    // Layout shrinkage can clamp scrollTop downward while the viewport remains
    // at the real bottom. That is geometry, not an upward reading gesture.
    if (movingTowardHistory && !measurement.atBottom) {
      followOutput = false;
    } else if (!followOutput && movingTowardLatest && measurement.atBottom) {
      followOutput = true;
    }

    this.lastScrollTop = metrics.scrollTop;
    this.current = { followOutput, nearBottom: measurement.nearBottom };
    return this.snapshot();
  }
}

export interface FrameCoalescer {
  schedule: (task: () => void) => void;
  cancel: () => void;
}

/** Collapse arbitrarily many stream/layout updates into one write per frame. */
export function createFrameCoalescer(
  requestFrame: (callback: () => void) => number,
  cancelFrame: (id: number) => void,
): FrameCoalescer {
  let frameId: number | null = null;
  let pendingTask: (() => void) | null = null;

  return {
    schedule(task) {
      pendingTask = task;
      if (frameId != null) return;
      frameId = requestFrame(() => {
        frameId = null;
        const run = pendingTask;
        pendingTask = null;
        run?.();
      });
    },
    cancel() {
      pendingTask = null;
      if (frameId == null) return;
      cancelFrame(frameId);
      frameId = null;
    },
  };
}
