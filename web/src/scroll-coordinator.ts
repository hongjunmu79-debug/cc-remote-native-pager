export type VirtualScrollAnchor = "start" | "end";
export type VirtualFollowOnAppend = false | "auto";
export type ScrollBehaviorMode = "auto" | "smooth";

export interface VirtualScrollPolicy {
  anchorTo: VirtualScrollAnchor;
  followOnAppend: VirtualFollowOnAppend;
  allowResizeAdjustment: boolean;
}

export type ScrollCommand =
  | { kind: "bottom"; behavior: ScrollBehaviorMode }
  | { kind: "offset"; offset: number };

/**
 * Serializes the product's scroll intent while the virtualizer remains the
 * only component that writes the viewport. In particular, a press on an
 * interactive row freezes automatic end/resize adjustment until the native
 * click has been delivered.
 */
export class ScrollCoordinator {
  private nextInteraction = 1;
  private interactions = new Map<number, boolean>();
  private pendingBottom: ScrollBehaviorMode | null = null;

  policy(followOutput: boolean): VirtualScrollPolicy {
    if (this.interactions.size > 0) {
      return {
        anchorTo: "start",
        followOnAppend: false,
        allowResizeAdjustment: false,
      };
    }
    return {
      anchorTo: "end",
      followOnAppend: followOutput ? "auto" : false,
      allowResizeAdjustment: true,
    };
  }

  beginInteraction(resumeAtBottom = false): number {
    const token = this.nextInteraction++;
    this.interactions.set(token, resumeAtBottom);
    return token;
  }

  requestBottom(behavior: ScrollBehaviorMode): ScrollCommand | null {
    if (this.interactions.size > 0) {
      if (behavior === "smooth" || this.pendingBottom === null) {
        this.pendingBottom = behavior;
      }
      return null;
    }
    return { kind: "bottom", behavior };
  }

  requestOffset(offset: number): ScrollCommand | null {
    if (this.interactions.size > 0 || !Number.isFinite(offset)) return null;
    return { kind: "offset", offset: Math.max(0, offset) };
  }

  endInteraction(token: number, followOutput: boolean): ScrollCommand | null {
    const resumeAtBottom = this.interactions.get(token) ?? false;
    if (!this.interactions.delete(token) || this.interactions.size > 0) {
      return null;
    }
    const behavior = this.pendingBottom
      ?? (followOutput && resumeAtBottom ? "auto" : null);
    this.pendingBottom = null;
    return behavior ? { kind: "bottom", behavior } : null;
  }

  reset(): ScrollCommand {
    this.interactions.clear();
    this.pendingBottom = null;
    return { kind: "bottom", behavior: "auto" };
  }

  isInteractionLocked(): boolean {
    return this.interactions.size > 0;
  }
}
