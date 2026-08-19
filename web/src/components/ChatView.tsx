import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type TouchEvent,
  type WheelEvent,
} from "react";
import {
  defaultRangeExtractor,
  useVirtualizer,
} from "@tanstack/react-virtual";
import type { Turn } from "../reducer";
import type { Space } from "../protocol";
import { MessageBlock } from "./MessageBlock";
import { Icon, ClaudeMark, ClaudeWorking, ClaudeSpark } from "../icons";
import { canForkTurn } from "../session-worktree";
import { ProcessTimeline } from "./ProcessTimeline";
import { finalTextBlocks, hasActiveProcess } from "../process-blocks";
import { isMarkdownPath } from "../preview-path";
import { collectTurnFileChanges } from "../file-changes";
import type { InlineImageAsset } from "../inline-image-assets";
import {
  historyImageAssetKey,
  type HistoryImageAsset,
  type HistoryImageVariant,
} from "../history-image-assets";
import { ImageLightbox } from "./ImageLightbox";
import { presentHistoricalTurnProblem } from "../problem-presentation";
import { queryImageDimensions } from "../img";
import {
  updateTurnKeySnapshot,
  type TurnKeySnapshot,
} from "../virtual-turn-keys";
import {
  historyImageDisplaySource,
  TurnImagePreviewCache,
} from "../turn-image-previews";
import {
  HistoryAnchorController,
  historyPageStatus,
  isAtHistoryEdge,
  measureBottom,
  OlderHistoryLoadGate,
  shouldAutoLoadOlderHistory,
  ScrollFollowController,
  type HistoryAnchorPoint,
  type ScrollFollowSnapshot,
  type ScrollMetrics,
} from "../scroll-follow";
import {
  ScrollCoordinator,
  type ScrollCommand,
} from "../scroll-coordinator";

const WHEEL_GESTURE_IDLE_MS = 180;
const HISTORY_VIRTUAL_ESTIMATE_PX = 280;
const HISTORY_VIRTUAL_OVERSCAN = 6;
const HISTORY_TURN_GAP_PX = 22;
const HISTORY_LOAD_HEADER_PX = 52;
const USER_SCROLL_INTENT_IDLE_MS = 260;

type UserScrollDirection = "history" | "latest" | "unknown";

interface CapturedHistoryBoundary extends HistoryAnchorPoint {
  anchorOffset: number;
}

function readScrollMetrics(el: HTMLDivElement): ScrollMetrics {
  return {
    scrollHeight: el.scrollHeight,
    scrollTop: el.scrollTop,
    clientHeight: el.clientHeight,
  };
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function HistoryUserImage({ turnId, imageId, width, height, asset, fallback, onLoad,
  onPreview }: {
  turnId: string;
  imageId: string;
  width: number;
  height: number;
  asset?: HistoryImageAsset;
  fallback?: NonNullable<Turn["images"]>[number];
  onLoad?: (turnId: string, imageId: string, variant: HistoryImageVariant) => boolean;
  onPreview: () => void;
}) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (asset || !onLoad) return;
    const node = triggerRef.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      onLoad(turnId, imageId, "thumbnail");
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      onLoad(turnId, imageId, "thumbnail");
      observer.disconnect();
    }, { rootMargin: "500px 0px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [asset, imageId, onLoad, turnId]);

  const src = historyImageDisplaySource(asset, fallback);
  return (
    <button ref={triggerRef} type="button"
      className="ubub-image-trigger history-image-trigger"
      style={{ aspectRatio: `${width} / ${height}` }}
      aria-label="预览用户发送的图片"
      disabled={!src}
      onClick={onPreview}>
      {src
        ? <img src={src} className="ubub-img" alt="用户发送的图片" />
        : <span className="history-image-placeholder" aria-hidden="true" />}
    </button>
  );
}

export function ChatView({ sid, turns, engine = "claude", loading, hasMore,
  historyRevision = null, historyCursor = null,
  onLoadMore, onLoadDetail, onEdit, onGetDiff, onOpenTurnDiff, onPreviewMarkdown, onOpenFile,
  onOpenArtifacts, onFork, forkingPointId, imageAssets, onLoadImage,
  historyImageAssets, onLoadHistoryImage,
  surface = "code" }: {
  sid: string | null;
  turns: Turn[];
  surface?: Space;
  engine?: "claude" | "codex";
  loading?: boolean;
  hasMore?: boolean;
  historyRevision?: string | null;
  historyCursor?: string | null;
  onLoadMore?: () => boolean;
  onLoadDetail?: (turnId: string) => void;
  onEdit: (prompt: string) => void;
  onGetDiff: (file: string) => void;
  onOpenTurnDiff?: (files: string[], diff: string) => void;
  onPreviewMarkdown?: (file: string) => void;
  onOpenFile?: (file: string, line?: number) => void;
  onOpenArtifacts?: () => void;
  onFork?: (forkPointId: string) => void;
  forkingPointId?: string | null;
  imageAssets?: Record<string, InlineImageAsset>;
  onLoadImage?: (path: string) => boolean;
  historyImageAssets?: Record<string, HistoryImageAsset>;
  onLoadHistoryImage?: (
    turnId: string, imageId: string, variant: HistoryImageVariant,
  ) => boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<ScrollFollowController | null>(null);
  if (!controllerRef.current) controllerRef.current = new ScrollFollowController();
  const scrollCoordinatorRef = useRef(new ScrollCoordinator());
  const [scrollPolicyEpoch, setScrollPolicyEpoch] = useState(0);
  const [scrollState, setScrollState] = useState<ScrollFollowSnapshot>(() =>
    controllerRef.current!.snapshot());
  const [zoom, setZoom] = useState<
    | { kind: "data"; src: string; alt: string }
    | { kind: "history"; turnId: string; imageId: string; alt: string }
    | null
  >(null);
  const historyAnchorRef = useRef(new HistoryAnchorController());
  const historyRequestRef = useRef<{
    sid: string | null;
    revision: string | null;
    before: string | null;
  } | null>(null);
  const turnNodeRefs = useRef(new Map<string, HTMLDivElement>());
  const historyReleaseFrameRef = useRef<number | null>(null);
  const historyLoadGateRef = useRef(new OlderHistoryLoadGate());
  const wheelHistoryLoadGateRef = useRef(new OlderHistoryLoadGate());
  const wheelGestureTimerRef = useRef<number | null>(null);
  const wheelGestureActiveRef = useRef(false);
  const autoLoadedBoundaryRef = useRef<string | null>(null);
  const lastScrollTopRef = useRef(0);
  const renderedScrollScopeRef = useRef<string | null>(null);
  const touchYRef = useRef<number | null>(null);
  const touchRebaseAppliedRef = useRef(false);
  const userScrollIntentRef = useRef(false);
  const userScrollDirectionRef = useRef<UserScrollDirection | null>(null);
  const userScrollIntentTimerRef = useRef<number | null>(null);
  const turnKeySnapshotRef = useRef<TurnKeySnapshot | null>(null);
  const turnImagePreviewCacheRef = useRef(new TurnImagePreviewCache());
  const scrollScope = `${sid ?? ""}\u0000${historyRevision ?? ""}`;
  turnImagePreviewCacheRef.current.update(sid, turns);
  const turnKeySnapshot = updateTurnKeySnapshot(
    turnKeySnapshotRef.current,
    turns,
    scrollScope,
  );
  turnKeySnapshotRef.current = turnKeySnapshot;
  const [activeHistoryGeneration, setActiveHistoryGeneration] = useState<number | null>(null);
  const [measurementBoundary, setMeasurementBoundary] = useState<{
    sid: string | null;
    revision: string | null;
    turnId: string;
    anchorOffset: number;
  } | null>(null);
  const [processDisclosureOpen, setProcessDisclosureOpen] = useState<
    Record<string, boolean>
  >({});
  const rememberProcessDisclosure = useCallback((key: string, open: boolean) => {
    setProcessDisclosureOpen((current) => {
      const next = { ...current, [key]: open };
      const keys = Object.keys(next);
      if (keys.length > 512) delete next[keys[0]];
      return next;
    });
  }, []);
  const canLoadOlder = !!hasMore;
  const historyInsetRef = useRef({
    scope: scrollScope,
    enabled: canLoadOlder,
  });
  if (historyInsetRef.current.scope !== scrollScope) {
    historyInsetRef.current = { scope: scrollScope, enabled: canLoadOlder };
  } else if (canLoadOlder) {
    historyInsetRef.current.enabled = true;
  }
  const historyTopInset = historyInsetRef.current.enabled
    ? HISTORY_LOAD_HEADER_PX : 0;
  const activeHistoryAnchor = historyAnchorRef.current.current();
  const keyedPrependActive = activeHistoryGeneration !== null
    && activeHistoryAnchor?.generation === activeHistoryGeneration
    && activeHistoryAnchor.sid === sid
    && activeHistoryAnchor.revision === historyRevision;
  const keyedPrependResponseReady = keyedPrependActive
    && historyPageStatus(activeHistoryAnchor, {
      sid, revision: historyRevision, cursor: historyCursor,
      hasMore: !!hasMore,
    }) === "complete";
  const scopedMeasurementBoundary = measurementBoundary?.sid === sid
      && measurementBoundary.revision === historyRevision
    ? measurementBoundary : null;
  const retainedMeasurementBoundary = scopedMeasurementBoundary
    ?? (keyedPrependActive && activeHistoryAnchor ? {
      sid,
      revision: historyRevision,
      turnId: activeHistoryAnchor.anchorTurnId,
      anchorOffset: activeHistoryAnchor.anchorOffset,
    } : null);
  // Read the epoch so pointer interaction changes synchronously reconfigure
  // the virtualizer even when no other chat state changed.
  void scrollPolicyEpoch;
  const virtualScrollPolicy = scrollCoordinatorRef.current.policy(
    scrollState.followOutput,
  );
  const virtualizer = useVirtualizer({
    count: turns.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => HISTORY_VIRTUAL_ESTIMATE_PX,
    getItemKey: turnKeySnapshot.getItemKey,
    // TanStack owns every viewport write. The coordinator only selects policy
    // and serializes explicit bottom requests with interactive pointer locks.
    anchorTo: virtualScrollPolicy.anchorTo,
    followOnAppend: virtualScrollPolicy.followOnAppend,
    scrollEndThreshold: 80,
    overscan: HISTORY_VIRTUAL_OVERSCAN,
    rangeExtractor: (range) => {
      const indexes = defaultRangeExtractor(range);
      const boundaryIndex = retainedMeasurementBoundary
        ? turns.findIndex((turn) => turn.id === retainedMeasurementBoundary.turnId)
        : -1;
      if (boundaryIndex < 0 || indexes.includes(boundaryIndex)) return indexes;
      return [...indexes, boundaryIndex].sort((left, right) => left - right);
    },
    gap: HISTORY_TURN_GAP_PX,
    paddingStart: historyTopInset,
    paddingEnd: 8,
    useAnimationFrameWithResizeObserver: true,
  });
  const measurementBoundaryIndex = retainedMeasurementBoundary
    ? turns.findIndex((turn) => turn.id === retainedMeasurementBoundary.turnId)
    : -1;
  if (!virtualScrollPolicy.allowResizeAdjustment) {
    virtualizer.shouldAdjustScrollPositionOnItemSizeChange = () => false;
  } else if (!scrollState.followOutput && measurementBoundaryIndex >= 0) {
    // TanStack remains the sole scroll writer. This predicate only tells it
    // which late measurements live completely before the user's reading row.
    virtualizer.shouldAdjustScrollPositionOnItemSizeChange =
      (item) => item.index < measurementBoundaryIndex;
  } else {
    virtualizer.shouldAdjustScrollPositionOnItemSizeChange = undefined;
  }

  const measureTurnOffset = (turnId: string): number | null => {
    const el = scrollRef.current;
    const node = turnNodeRefs.current.get(turnId);
    if (!el || !node) return null;
    return node.getBoundingClientRect().top - el.getBoundingClientRect().top;
  };

  const captureHistoryBoundary = (): CapturedHistoryBoundary | null => {
    const el = scrollRef.current;
    const viewportTop = el?.getBoundingClientRect().top;
    let anchorTurnId: string | null = null;
    let anchorOffset = 0;
    let bestDistance = Number.POSITIVE_INFINITY;
    if (el && viewportTop != null) {
      for (const [turnId, node] of turnNodeRefs.current) {
        const rect = node.getBoundingClientRect();
        if (rect.bottom <= viewportTop || rect.top >= viewportTop + el.clientHeight) continue;
        const distance = Math.abs(rect.top - viewportTop);
        if (distance < bestDistance) {
          anchorTurnId = turnId;
          anchorOffset = rect.top - viewportTop;
          bestDistance = distance;
        }
      }
    }
    anchorTurnId ??= turns[0]?.id ?? null;
    if (!anchorTurnId) return null;
    anchorOffset = measureTurnOffset(anchorTurnId) ?? anchorOffset;
    return {
      anchorTurnId,
      oldestTurnId: turns[0]?.id ?? null,
      anchorOffset,
    };
  };

  const cancelHistoryAnchor = useCallback((generation?: number): boolean => {
    const cancelled = historyAnchorRef.current.cancel(generation);
    if (!cancelled) return false;
    if (historyReleaseFrameRef.current !== null) {
      window.cancelAnimationFrame(historyReleaseFrameRef.current);
      historyReleaseFrameRef.current = null;
    }
    setActiveHistoryGeneration(null);
    return cancelled;
  }, []);

  const syncScrollState = useCallback((next: ScrollFollowSnapshot) => {
    setScrollState((previous) =>
      previous.followOutput === next.followOutput && previous.nearBottom === next.nearBottom
        ? previous
        : next);
  }, []);

  const applyScrollCommand = useCallback((command: ScrollCommand | null) => {
    if (!command) return;
    const el = scrollRef.current;
    const controller = controllerRef.current;
    if (!el || !controller) return;
    if (command.kind === "bottom") {
      virtualizer.scrollToEnd({ behavior: command.behavior });
    } else {
      virtualizer.scrollToOffset(command.offset, { behavior: "auto" });
    }
    lastScrollTopRef.current = el.scrollTop;
    syncScrollState(controller.recordProgrammaticScroll(readScrollMetrics(el)));
  }, [syncScrollState, virtualizer]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let previousHeight = el.clientHeight;
    let frame: number | null = null;
    const observer = new ResizeObserver(() => {
      const nextHeight = el.clientHeight;
      if (Math.abs(nextHeight - previousHeight) < 0.5) return;
      previousHeight = nextHeight;
      if (!controllerRef.current?.isFollowing()) return;
      if (frame !== null) window.cancelAnimationFrame(frame);
      // Composer actions and the mobile keyboard resize the thread from
      // outside the virtual list. Let both ResizeObservers commit the new
      // viewport size, then restore the live-tail intent through TanStack's
      // sole scroll writer. Reading-history state never enters this branch.
      frame = window.requestAnimationFrame(() => {
        frame = null;
        if (!controllerRef.current?.isFollowing()
            || userScrollIntentRef.current
            || touchYRef.current !== null) return;
        applyScrollCommand(
          scrollCoordinatorRef.current.requestBottom("auto"),
        );
      });
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, [applyScrollCommand, scrollScope]);

  const pauseOutputFollow = useCallback(() => {
    const el = scrollRef.current;
    const controller = controllerRef.current;
    if (!el || !controller) return;
    syncScrollState(controller.pause(readScrollMetrics(el)));
  }, [syncScrollState]);

  const completeHistoryLoadGates = useCallback(() => {
    historyLoadGateRef.current.complete();
    wheelHistoryLoadGateRef.current.complete();
  }, []);

  const scheduleHistoryAnchorRelease = useCallback((generation: number) => {
    if (touchYRef.current !== null
        || scrollCoordinatorRef.current.isInteractionLocked()) return;
    if (historyReleaseFrameRef.current !== null) {
      window.cancelAnimationFrame(historyReleaseFrameRef.current);
    }
    historyReleaseFrameRef.current = window.requestAnimationFrame(() => {
      historyReleaseFrameRef.current = null;
      cancelHistoryAnchor(generation);
      completeHistoryLoadGates();
    });
  }, [cancelHistoryAnchor, completeHistoryLoadGates]);

  // Freeze the old/new boundary before issuing an asynchronous older-page read.
  const doLoadMore = (): boolean => {
    if (historyAnchorRef.current.current()) return false;
    const el = scrollRef.current;
    const point = el ? captureHistoryBoundary() : null;
    if (el) pauseOutputFollow();
    historyRequestRef.current = {
      sid,
      revision: historyRevision,
      before: historyCursor,
    };
    let generation: number | null = null;
    if (point) {
      cancelHistoryAnchor();
      setMeasurementBoundary({
        sid,
        revision: historyRevision,
        turnId: point.anchorTurnId,
        anchorOffset: point.anchorOffset,
      });
      generation = historyAnchorRef.current.begin({
        sid, revision: historyRevision, before: historyCursor,
        source: "server",
        anchorTurnId: point.anchorTurnId,
        oldestTurnId: point.oldestTurnId,
        anchorOffset: point.anchorOffset,
      });
      setActiveHistoryGeneration(generation);
    }
    if (!onLoadMore?.()) {
      historyRequestRef.current = null;
      if (generation != null) cancelHistoryAnchor(generation);
      completeHistoryLoadGates();
      return false;
    }
    return true;
  };

  // Scroll/touch events can repeat many times while a finger or wheel remains
  // pinned at the top. Touch/wheel gates allow one request per gesture; plain
  // scroll/keyboard events additionally use the visible boundary as their gate.
  const maybeAutoLoadOlder = (
    movingTowardHistory: boolean,
    source: "touch" | "wheel" | "other",
  ) => {
    const el = scrollRef.current;
    if (!el || !shouldAutoLoadOlderHistory(
      readScrollMetrics(el), movingTowardHistory, canLoadOlder,
    )) return;
    const boundary = [
      sid ?? "", turns[0]?.id ?? "",
      turns.length, historyRevision ?? "", historyCursor ?? "", hasMore ? 1 : 0,
    ].join("\u0000");
    if (source === "other" && autoLoadedBoundaryRef.current === boundary) return;
    const gestureGate = source === "touch"
      ? historyLoadGateRef.current
      : source === "wheel" ? wheelHistoryLoadGateRef.current : null;
    if (gestureGate && !gestureGate.acquire()) return;
    if (doLoadMore()) {
      autoLoadedBoundaryRef.current = boundary;
    } else {
      gestureGate?.complete();
    }
  };

  useLayoutEffect(() => {
    const request = historyRequestRef.current;
    if (request && (request.sid !== sid
        || request.revision !== historyRevision
        || request.before !== historyCursor
        || !hasMore)) {
      historyRequestRef.current = null;
    }
    const anchor = historyAnchorRef.current.current();
    if (!anchor) return;
    if (anchor.sid !== sid || anchor.revision !== historyRevision) {
      cancelHistoryAnchor(anchor.generation);
      completeHistoryLoadGates();
      return;
    }
    if (anchor.source !== "server") return;
    if (anchor.phase === "applied") {
      scheduleHistoryAnchorRelease(anchor.generation);
      return;
    }
    if (anchor.phase !== "pending") return;
    const pageStatus = historyPageStatus(anchor, {
      sid, revision: historyRevision, cursor: historyCursor,
      hasMore: !!hasMore,
    });
    if (pageStatus === "pending") return;
    if (pageStatus === "stale") {
      cancelHistoryAnchor(anchor.generation);
      completeHistoryLoadGates();
      return;
    }

    if (!turns.some((turn) => turn.id === anchor.anchorTurnId)) {
      // The bounded projection no longer contains the reading row. Never
      // manufacture a viewport movement from cursor metadata alone.
      cancelHistoryAnchor(anchor.generation);
      completeHistoryLoadGates();
      return;
    }
    if (historyAnchorRef.current.markRendering(anchor.generation)) {
      historyAnchorRef.current.markApplied(anchor.generation);
      scheduleHistoryAnchorRelease(anchor.generation);
    }
  }, [
    cancelHistoryAnchor, completeHistoryLoadGates, hasMore, historyCursor,
    historyRevision, scheduleHistoryAnchorRelease, sid, turns,
  ]);

  // WebKit can clamp the virtualizer's keyed prepend adjustment against the
  // previous sizer height. The coordinator owns the one residual correction:
  // it is scoped to the retained reading turn and never runs during touch or
  // an interactive control press.
  useLayoutEffect(() => {
    const boundary = retainedMeasurementBoundary;
    const el = scrollRef.current;
    const controller = controllerRef.current;
    if (!boundary || !el || !controller
        || boundary.sid !== sid
        || boundary.revision !== historyRevision
        || touchYRef.current !== null
        || (userScrollIntentRef.current && !keyedPrependResponseReady)
        || scrollCoordinatorRef.current.isInteractionLocked()) return;
    const currentOffset = measureTurnOffset(boundary.turnId);
    if (currentOffset == null) return;
    const delta = currentOffset - boundary.anchorOffset;
    if (Math.abs(delta) <= 0.5) return;
    applyScrollCommand(scrollCoordinatorRef.current.requestOffset(
      el.scrollTop + delta,
    ));
  });

  useLayoutEffect(() => {
    const el = scrollRef.current;
    const controller = controllerRef.current;
    if (!el || !controller) return;

    // Initial mount, session switches, and authoritative revision replacements
    // are anchored synchronously before paint. Commit the scope only once the
    // replacement thread exists; an intermediate empty projection has no
    // viewport on which the reset command can be consumed.
    if (renderedScrollScopeRef.current !== scrollScope) {
      renderedScrollScopeRef.current = scrollScope;
      cancelHistoryAnchor();
      touchYRef.current = null;
      touchRebaseAppliedRef.current = false;
      userScrollIntentRef.current = false;
      userScrollDirectionRef.current = null;
      if (userScrollIntentTimerRef.current !== null) {
        window.clearTimeout(userScrollIntentTimerRef.current);
        userScrollIntentTimerRef.current = null;
      }
      if (wheelGestureTimerRef.current !== null) {
        window.clearTimeout(wheelGestureTimerRef.current);
        wheelGestureTimerRef.current = null;
      }
      wheelGestureActiveRef.current = false;
      historyRequestRef.current = null;
      wheelHistoryLoadGateRef.current.complete();
      wheelHistoryLoadGateRef.current.endGesture();
      setMeasurementBoundary(null);
      applyScrollCommand(scrollCoordinatorRef.current.reset());
      syncScrollState(controller.reset(readScrollMetrics(el)));
      return;
    }

    if (!controller.isFollowing()) {
      syncScrollState(controller.observeLayout(readScrollMetrics(el)));
    }
  }, [
    applyScrollCommand, cancelHistoryAnchor, scrollScope, syncScrollState, turns,
  ]);

  useEffect(() => {
    return () => {
      cancelHistoryAnchor();
      if (wheelGestureTimerRef.current !== null) {
        window.clearTimeout(wheelGestureTimerRef.current);
      }
      if (userScrollIntentTimerRef.current !== null) {
        window.clearTimeout(userScrollIntentTimerRef.current);
      }
    };
  }, [cancelHistoryAnchor]);

  useEffect(() => setZoom(null), [sid]);

  const markUserScrollIntent = (direction: UserScrollDirection) => {
    setMeasurementBoundary(null);
    userScrollIntentRef.current = true;
    userScrollDirectionRef.current = direction;
    if (userScrollIntentTimerRef.current !== null) {
      window.clearTimeout(userScrollIntentTimerRef.current);
    }
    userScrollIntentTimerRef.current = window.setTimeout(() => {
      userScrollIntentTimerRef.current = null;
      userScrollIntentRef.current = false;
      userScrollDirectionRef.current = null;
      const controller = controllerRef.current;
      const point = captureHistoryBoundary();
      const request = historyRequestRef.current;
      if (point && (!controller?.isFollowing()
          || (request?.sid === sid
            && request.revision === historyRevision))) {
        setMeasurementBoundary({
          sid,
          revision: historyRevision,
          turnId: point.anchorTurnId,
          anchorOffset: point.anchorOffset,
        });
      }
    }, USER_SCROLL_INTENT_IDLE_MS);
  };

  const onScroll = () => {
    const el = scrollRef.current;
    const controller = controllerRef.current;
    if (!el || !controller) return;
    const metrics = readScrollMetrics(el);
    const movingTowardHistory = metrics.scrollTop < lastScrollTopRef.current - 0.5;
    const movingTowardLatest = metrics.scrollTop > lastScrollTopRef.current + 0.5;
    const movementDirection: UserScrollDirection | null = movingTowardHistory
      ? "history" : movingTowardLatest ? "latest" : null;
    const intendedDirection = userScrollDirectionRef.current;
    const currentHistoryAnchor = historyAnchorRef.current.current();
    const explicitAppliedMovement = keyedPrependResponseReady
      && currentHistoryAnchor?.phase === "applied"
      && intendedDirection !== null
      && intendedDirection !== "unknown";
    const userDrivenScroll = userScrollIntentRef.current
      && movementDirection !== null
      && (intendedDirection === "unknown" || intendedDirection === movementDirection)
      && (!keyedPrependResponseReady || explicitAppliedMovement);
    if (userDrivenScroll && intendedDirection) {
      markUserScrollIntent(intendedDirection);
    }
    if (userDrivenScroll && currentHistoryAnchor) {
      if (currentHistoryAnchor.phase === "applied") {
        const point = captureHistoryBoundary();
        if (point && historyAnchorRef.current.rebase(
          currentHistoryAnchor.generation,
          point,
        )) {
          setMeasurementBoundary({
            sid,
            revision: historyRevision,
            turnId: point.anchorTurnId,
            anchorOffset: point.anchorOffset,
          });
        }
      } else {
        historyAnchorRef.current.observeUserScroll(isAtHistoryEdge(metrics));
        if (!historyAnchorRef.current.current()) {
          setActiveHistoryGeneration(null);
          completeHistoryLoadGates();
        }
      }
    }
    const request = historyRequestRef.current;
    if (userDrivenScroll && request?.sid === sid
        && request.revision === historyRevision) {
      const point = captureHistoryBoundary();
      if (point) {
        setMeasurementBoundary({
          sid,
          revision: historyRevision,
          turnId: point.anchorTurnId,
          anchorOffset: point.anchorOffset,
        });
      }
    }
    lastScrollTopRef.current = metrics.scrollTop;
    const nextScrollState = userDrivenScroll
      ? controller.observeScroll(metrics)
      : controller.recordProgrammaticScroll(metrics);
    if (nextScrollState.followOutput && !scrollState.followOutput
        && !historyRequestRef.current) {
      setMeasurementBoundary(null);
    }
    syncScrollState(nextScrollState);
    maybeAutoLoadOlder(
      movingTowardHistory && userDrivenScroll,
      touchYRef.current != null ? "touch"
        : wheelGestureActiveRef.current ? "wheel" : "other",
    );
  };

  const onWheel = (event: WheelEvent<HTMLDivElement>) => {
    markUserScrollIntent(event.deltaY < 0 ? "history" : "latest");
    if (event.deltaY > 0) {
      const anchor = historyAnchorRef.current.current();
      if (anchor?.phase === "pending" || anchor?.phase === "rendering") {
        if (cancelHistoryAnchor(anchor.generation)) {
          setMeasurementBoundary(null);
        }
        completeHistoryLoadGates();
      }
    }
    if (event.deltaY < 0) {
      pauseOutputFollow();
      if (!wheelGestureActiveRef.current) {
        wheelGestureActiveRef.current = true;
        wheelHistoryLoadGateRef.current.beginGesture();
      }
      if (wheelGestureTimerRef.current !== null) {
        window.clearTimeout(wheelGestureTimerRef.current);
      }
      wheelGestureTimerRef.current = window.setTimeout(() => {
        wheelGestureTimerRef.current = null;
        wheelGestureActiveRef.current = false;
        wheelHistoryLoadGateRef.current.endGesture();
      }, WHEEL_GESTURE_IDLE_MS);
      const el = scrollRef.current;
      if (el && isAtHistoryEdge(readScrollMetrics(el))) {
        maybeAutoLoadOlder(true, "wheel");
      }
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (["ArrowUp", "PageUp", "Home"].includes(event.key)) {
      markUserScrollIntent("history");
    } else if (["ArrowDown", "PageDown", "End", " "].includes(event.key)) {
      markUserScrollIntent("latest");
    }
  };

  const onTouchStart = (event: TouchEvent<HTMLDivElement>) => {
    markUserScrollIntent("unknown");
    historyLoadGateRef.current.beginGesture();
    touchRebaseAppliedRef.current = false;
    touchYRef.current = event.touches[0]?.clientY ?? null;
  };

  const onTouchMove = (event: TouchEvent<HTMLDivElement>) => {
    const currentY = event.touches[0]?.clientY;
    const previousY = touchYRef.current;
    if (currentY == null || previousY == null) return;
    // A finger moving down scrolls the viewport toward earlier messages.
    if (currentY > previousY) {
      markUserScrollIntent("history");
      pauseOutputFollow();
      const el = scrollRef.current;
      if (el && isAtHistoryEdge(readScrollMetrics(el))) {
        maybeAutoLoadOlder(true, "touch");
      }
    } else if (currentY < previousY) {
      markUserScrollIntent("latest");
      const anchor = historyAnchorRef.current.current();
      if (anchor?.phase === "applied") {
        touchRebaseAppliedRef.current = true;
      }
      if (anchor?.phase === "pending" || anchor?.phase === "rendering") {
        if (cancelHistoryAnchor(anchor.generation)) {
          setMeasurementBoundary(null);
        }
        completeHistoryLoadGates();
      }
    }
    touchYRef.current = currentY;
  };

  const rebaseAppliedHistoryAnchor = () => {
    const anchor = historyAnchorRef.current.current();
    const point = anchor?.phase === "applied" ? captureHistoryBoundary() : null;
    if (!anchor || !point || !historyAnchorRef.current.rebase(
      anchor.generation,
      point,
    )) return;
    setMeasurementBoundary({
      sid,
      revision: historyRevision,
      turnId: point.anchorTurnId,
      anchorOffset: point.anchorOffset,
    });
  };

  const clearTouch = () => {
    // Mobile WebKit may defer React's scroll event until touchend. Capture the
    // DOM's already-moved reading row before clearing the touch lock, otherwise
    // the residual prepend correction can restore the pre-gesture row first.
    if (touchRebaseAppliedRef.current) {
      rebaseAppliedHistoryAnchor();
    }
    touchRebaseAppliedRef.current = false;
    touchYRef.current = null;
    historyLoadGateRef.current.endGesture();
    // A page can finish while the finger is still down. Re-render after the
    // native touch ends so the retained history transaction can correct and
    // release its exact reading boundary without fighting the gesture.
    setScrollPolicyEpoch((value) => value + 1);
  };

  const scrollToBottom = () => {
    const el = scrollRef.current;
    const controller = controllerRef.current;
    if (!el || !controller) return;
    cancelHistoryAnchor();
    historyRequestRef.current = null;
    setMeasurementBoundary(null);
    syncScrollState(controller.resume(readScrollMetrics(el)));
    applyScrollCommand(
      scrollCoordinatorRef.current.requestBottom("smooth"),
    );
  };

  const beginProcessInteraction = useCallback((): number => {
    const el = scrollRef.current;
    const resumeAtBottom = !!el
      && (controllerRef.current?.isFollowing() ?? false)
      && measureBottom(readScrollMetrics(el)).atBottom;
    const token = scrollCoordinatorRef.current.beginInteraction(resumeAtBottom);
    setScrollPolicyEpoch((value) => value + 1);
    return token;
  }, []);

  const endProcessInteraction = useCallback((token: number): void => {
    const command = scrollCoordinatorRef.current.endInteraction(
      token,
      controllerRef.current?.isFollowing() ?? false,
    );
    setScrollPolicyEpoch((value) => value + 1);
    if (command) {
      window.requestAnimationFrame(() => applyScrollCommand(command));
    }
  }, [applyScrollCommand]);

  const [copiedId, setCopiedId] = useState<string | null>(null);
  const copyText = (id: string, text: string) => {
    navigator.clipboard?.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 1500);
  };
  const aiText = (t: Turn) => finalTextBlocks(t.blocks).map((block) => block.text).join("\n\n");

  // Collect engine-neutral file mutations. The helper also understands old
  // Claude file_path and Codex changes payloads already stored in browser cache.
  const fileChips = (t: Turn) => {
    const changes = collectTurnFileChanges(t.blocks);
    if (!changes.paths.length) return null;
    const arr = changes.paths;
    const openSummary = () => {
      if (surface !== "work") {
        if (changes.diff && onOpenTurnDiff) {
          onOpenTurnDiff(arr, changes.diff);
        } else if (arr.length === 1) {
          // Compatibility fallback for old history without a persisted diff.
          // It remains path-scoped and must never open the whole worktree.
          onGetDiff(arr[0]);
        }
        return;
      }
      if (arr.length === 1 && onOpenFile) {
        onOpenFile(arr[0]);
        return;
      }
      onOpenArtifacts?.();
    };
    return (
      <div className="turn-files">
        <button className="turn-files-summary" onClick={openSummary}
          title={surface === "work" ? "预览 Artifacts" : "查看本轮改动"}>
          <Icon name={surface === "work" ? "folder" : "edit"} size={13} />{
            surface === "work" ? `Artifacts · ${arr.length} 个文件` : `改动 ${arr.length} 个文件`
          }
        </button>
        <div className="turn-files-list">
          {arr.map((f) => {
            const markdown = surface !== "work" && isMarkdownPath(f) && !!onPreviewMarkdown;
            return <button key={f} className={"turn-file-chip" + (markdown ? " markdown" : "")}
              onClick={() => markdown ? onPreviewMarkdown(f) : onGetDiff(f)}
              title={markdown ? `预览 ${f}` : `查看 ${f} 的 diff`}>
              <Icon name={markdown ? "read" : "edit"} size={12} />
              {f.split("/").pop()}
              {markdown && <span className="turn-file-action">预览</span>}
            </button>;
          })}
        </div>
      </div>
    );
  };

  const measuredVirtualItems = virtualizer.getVirtualItems();
  const renderedVirtualItems = measuredVirtualItems.length > 0
    ? measuredVirtualItems
    : turns.slice(-4).map((_, offset) => {
      const index = Math.max(0, turns.length - 4) + offset;
      return {
        index,
        key: turnKeySnapshot.getItemKey(index),
        start: historyTopInset
          + index * (HISTORY_VIRTUAL_ESTIMATE_PX + HISTORY_TURN_GAP_PX),
      };
    });

  if (turns.length === 0) {
    if (loading) {
      return (
        <div className="empty">
          <div className="spinner" aria-label="加载中" />
          <p className="loading-tx">加载会话历史…</p>
        </div>
      );
    }
    return (
      <div className="empty">
        <div className="glyph"><ClaudeMark size={30} /></div>
        <h2>{surface === "work" ? "工作区已就绪" : "已连接"}</h2>
        <p>{surface === "work"
          ? "添加资料并描述成果，我会把生成的文档和文件留在这项工作的私有目录。"
          : <>发一条消息开始，或用 <code>/</code> 唤起命令面板（Plan mode、review、技能…）。</>}</p>
      </div>
    );
  }

  return (
    <div className={surface === "work" ? "thread-shell work-thread-shell" : "thread-shell"}>
      <div className="thread" ref={scrollRef}
        onScroll={onScroll} onWheel={onWheel}
        onKeyDownCapture={onKeyDown}
        onPointerDown={() => { markUserScrollIntent("unknown"); }}
        onTouchStart={onTouchStart} onTouchMove={onTouchMove}
        onTouchEnd={clearTouch} onTouchCancel={clearTouch}>
        <div className="thread-in virtual-thread-in" style={{
          height: `${virtualizer.getTotalSize()}px`,
          position: "relative",
        }}>
          {canLoadOlder && (
            <div className="load-more-wrap virtual-history-loader">
              <button className="load-more-btn" onClick={doLoadMore}>
                加载更早的历史
              </button>
            </div>
          )}
          {renderedVirtualItems.map((virtualItem) => {
            const t = turns[virtualItem.index];
            if (!t) return null;
            const ti = virtualItem.index;
            const activeProcess = hasActiveProcess(t.blocks);
            const finalBlocks = finalTextBlocks(t.blocks);
            const working = !t.done || activeProcess;
            const showProcessTimeline = t.blocks.length > 0
              || (!!t.detailEventCount && !t.detailLoaded);
            const workingLabel = t.progress
              ?? (activeProcess ? "处理中"
                : finalBlocks.length > 0 ? "回答中" : "思考中");
            const processOpenKey = `${scrollScope}\u0000turn:${t.id}`;
            const historyTurnId = t.historyTurnId ?? t.id;
            const historyImagesReady = !!t.imageRefs?.length
              && t.imageRefs.every((image) => (
                historyImageAssets?.[historyImageAssetKey(
                  historyTurnId, image.image_id, "thumbnail")
                ]?.status === "ready"
              ));
            if (historyImagesReady) {
              turnImagePreviewCacheRef.current.release(t.id);
            }
            return (
            <div className="turn" key={virtualItem.key}
              data-index={virtualItem.index} data-turn-id={t.id}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${virtualItem.start}px)`,
              }}
              ref={(node) => {
                virtualizer.measureElement(node);
                if (node) {
                  turnNodeRefs.current.set(t.id, node);
                } else {
                  turnNodeRefs.current.delete(t.id);
                }
              }}>
            {(t.prompt || (t.images && t.images.length) || (t.imageRefs && t.imageRefs.length) || (t.files && t.files.length)) && (
              <div className="ubub-wrap">
                {t.prompt && <div className="ubub">{t.prompt}</div>}
                {t.images && t.images.length > 0 && (
                  <div className="ubub-imgs">
                    {t.images.map((img, i) => {
                      const src = `data:${img.media_type};base64,${img.data}`;
                      const [width, height] = queryImageDimensions(img)
                        ?? [180, 180];
                      return <button key={i} type="button" className="ubub-image-trigger"
                        style={{ aspectRatio: `${width} / ${height}` }}
                        aria-label="预览用户发送的图片"
                        onClick={() => setZoom({ kind: "data", src, alt: "用户发送的图片" })}>
                        <img src={src} className="ubub-img" width={width}
                          height={height} alt="用户发送的图片" />
                      </button>;
                    })}
                  </div>
                )}
                {(!t.images || t.images.length === 0)
                  && t.imageRefs && t.imageRefs.length > 0 && (
                  <div className="ubub-imgs">
                    {t.imageRefs.map((image, imageIndex) => {
                      const thumbnail = historyImageAssets?.[
                        historyImageAssetKey(
                          historyTurnId, image.image_id, "thumbnail")
                      ];
                      const fallback = turnImagePreviewCacheRef.current.get(
                        t.id, imageIndex);
                      return <HistoryUserImage key={image.image_id}
                        turnId={historyTurnId} imageId={image.image_id}
                        width={image.width} height={image.height}
                        asset={thumbnail} fallback={fallback}
                        onLoad={onLoadHistoryImage}
                        onPreview={() => {
                          if (fallback) {
                            setZoom({
                              kind: "data",
                              src: `data:${fallback.media_type};base64,${fallback.data}`,
                              alt: "用户发送的图片",
                            });
                            return;
                          }
                          onLoadHistoryImage?.(
                            historyTurnId, image.image_id, "full");
                          setZoom({
                            kind: "history", turnId: historyTurnId,
                            imageId: image.image_id, alt: "用户发送的图片",
                          });
                        }} />;
                    })}
                  </div>
                )}
                {t.files && t.files.length > 0 && (
                  <div className="ubub-files">
                    {t.files.map((f, i) => (
                      <span key={i} className="ubub-file"><Icon name="read" size={14} />{f.filename}</span>
                    ))}
                  </div>
                )}
                <div className="ubub-meta">
                  {t.ts && <span className="ubub-time">{formatTime(t.ts)}</span>}
                  {t.prompt && <button className="ubub-act" onClick={() => onEdit(t.prompt!)} aria-label="编辑"><Icon name="edit" size={13} /></button>}
                  {t.prompt && <button className={"ubub-act" + (copiedId === t.id ? " copied" : "")} onClick={() => copyText(t.id, t.prompt!)} aria-label="复制"><Icon name="check" size={13} /></button>}
                </div>
              </div>
            )}
            {showProcessTimeline && (
              <ProcessTimeline blocks={t.blocks} done={t.done} engine={engine}
                durationMs={t.durationMs} startTs={t.ts} doneTs={t.doneTs}
                deferredCount={!t.detailLoaded ? t.detailEventCount : 0}
                detailLoading={t.detailLoading}
                onLoadDetail={onLoadDetail ? () => onLoadDetail(t.id) : undefined}
                onOpenFile={onOpenFile} imageAssets={imageAssets}
                onLoadImage={onLoadImage}
                onInteractionStart={beginProcessInteraction}
                onInteractionEnd={endProcessInteraction}
                openOverride={processDisclosureOpen[`${processOpenKey}\u0000outer`]}
                onOpenChange={(open) => rememberProcessDisclosure(
                  `${processOpenKey}\u0000outer`, open,
                )}
                itemOpen={(key) =>
                  processDisclosureOpen[`${processOpenKey}\u0000${key}`]}
                onItemOpenChange={(key, open) => rememberProcessDisclosure(
                  `${processOpenKey}\u0000${key}`, open,
                )}
                onPreviewImage={(src, alt) => setZoom({ kind: "data", src, alt })} />
            )}
            {t.blocks.length > 0 && (
              <>
                {finalBlocks.map((block) => (
                  <MessageBlock key={block.message_id} text={block.text}
                    done={block.done} onOpenFile={onOpenFile}
                    imageAssets={imageAssets} onLoadImage={onLoadImage}
                    onPreviewImage={(src, alt) => setZoom({ kind: "data", src, alt })} />
                ))}
                {t.done && (
                  <>
                    <div className="ubub-meta ai-meta">
                      {t.doneTs && <span className="ubub-time">{formatTime(t.doneTs)}</span>}
                      {finalBlocks.length > 0 && <button
                        className={"ubub-act" + (copiedId === t.id + "-ai" ? " copied" : "")}
                        onClick={() => copyText(t.id + "-ai", aiText(t))}
                        aria-label="复制">
                        <Icon name="check" size={13} />
                      </button>}
                      {onFork && canForkTurn(engine, t) && (
                        <button className="ubub-act" aria-label="派生"
                          data-tooltip="从此回复派生新会话"
                          aria-busy={forkingPointId === t.forkPointId}
                          disabled={!!forkingPointId}
                          onClick={() => onFork(t.forkPointId)}>
                          <Icon name="branch" size={13} />
                        </button>
                      )}
                    </div>
                    {ti === turns.length - 1 && !working
                      && <div className="turn-done-mark"><ClaudeSpark size={22} /></div>}
                  </>
                )}
              </>
            )}
              {working && (
                <div className="turn-working" role="status" aria-live="polite">
                  <ClaudeWorking size={24} />
                  <span className="turn-working-tx">{workingLabel}</span>
                </div>
              )}
              {fileChips(t)}
              {t.interrupted && <div className="note interrupted">— 已打断 —</div>}
              {t.error && <div className="note interrupted">{
                presentHistoricalTurnProblem(t.error)
              }</div>}
            </div>
            );
          })}
        </div>
      </div>
      {(!scrollState.followOutput || !scrollState.nearBottom) && (
        <div className="scroll-bottom-wrap">
          <button className="scroll-bottom-btn" onClick={scrollToBottom} aria-label="滚动到底部">
            <Icon name="chev" size={20} />
          </button>
        </div>
      )}
      {zoom && (() => {
        const asset = zoom.kind === "history" ? historyImageAssets?.[
          historyImageAssetKey(zoom.turnId, zoom.imageId, "full")
        ] ?? historyImageAssets?.[
          historyImageAssetKey(zoom.turnId, zoom.imageId, "thumbnail")
        ] : null;
        const src = zoom.kind === "data" ? zoom.src
          : asset?.status === "ready" && asset.data && asset.mediaType
            ? `data:${asset.mediaType};base64,${asset.data}` : null;
        return src ? (
        <ImageLightbox key={sid ?? ""} src={src} alt={zoom.alt}
          onClose={() => setZoom(null)} />
        ) : null;
      })()}
    </div>
  );
}
