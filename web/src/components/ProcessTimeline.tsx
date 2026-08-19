import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import type { Block, ProcessBlock, TextBlock, ToolBlock } from "../reducer";
import { Icon } from "../icons";
import { MessageBlock } from "./MessageBlock";
import { ToolGroup } from "./ToolGroup";
import { hasActiveProcess, processBlocks } from "../process-blocks";
import { filePathsFromInput } from "../file-changes";
import type { InlineImageAsset } from "../inline-image-assets";
import { PointerTapGuard } from "../pointer-tap";

function durationLabel(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) return `${minutes}m ${rest}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function statusIcon(status: ProcessBlock["status"], done: boolean) {
  if (!done && (status === "running" || status === "pending" || status === "unknown")) {
    return <span className="process-spin" />;
  }
  if (status === "failed" || status === "declined" || status === "cancelled"
      || status === "interrupted") {
    return <Icon name="close" size={14} />;
  }
  return <Icon name="verify" size={14} />;
}

function ProcessDisclosure({ className, summary, children, openOverride,
  onOpenChange, onInteractionStart, onInteractionEnd }: {
  className: string;
  summary: ReactNode;
  children: ReactNode;
  openOverride?: boolean;
  onOpenChange?: (open: boolean) => void;
  onInteractionStart?: () => number;
  onInteractionEnd?: (token: number) => void;
}) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const open = openOverride ?? uncontrolledOpen;
  const tapGuard = useRef(new PointerTapGuard());
  const interactionTokens = useRef(new Map<number, number>());
  const releaseInteractionFrame = useRef<number | null>(null);
  useEffect(() => () => {
    if (releaseInteractionFrame.current !== null) {
      window.cancelAnimationFrame(releaseInteractionFrame.current);
    }
    for (const token of interactionTokens.current.values()) {
      onInteractionEnd?.(token);
    }
    interactionTokens.current.clear();
  }, [onInteractionEnd]);
  const setOpen = (next: boolean) => {
    setUncontrolledOpen(next);
    onOpenChange?.(next);
  };
  const releaseInteractions = () => {
    if (releaseInteractionFrame.current !== null) {
      window.cancelAnimationFrame(releaseInteractionFrame.current);
    }
    releaseInteractionFrame.current = window.requestAnimationFrame(() => {
      releaseInteractionFrame.current = window.requestAnimationFrame(() => {
        releaseInteractionFrame.current = null;
        for (const token of interactionTokens.current.values()) {
          onInteractionEnd?.(token);
        }
        interactionTokens.current.clear();
      });
    });
  };
  return (
    <details className={className} open={open}>
      <summary
        onPointerDown={(event) => {
          tapGuard.current.pointerDown(
            event.pointerId, event.clientX, event.clientY,
          );
          event.currentTarget.setPointerCapture?.(event.pointerId);
          const token = onInteractionStart?.();
          if (token != null) interactionTokens.current.set(event.pointerId, token);
        }}
        onPointerMove={(event) => tapGuard.current.pointerMove(
          event.pointerId, event.clientX, event.clientY,
        )}
        onPointerUp={(event) => {
          tapGuard.current.pointerUp(event.pointerId);
          releaseInteractions();
        }}
        onPointerCancel={(event) => {
          tapGuard.current.pointerCancel(event.pointerId);
          releaseInteractions();
        }}
        onClick={(event) => {
          event.preventDefault();
          if (tapGuard.current.consumeClick(event.detail)) setOpen(!open);
        }}>
        {summary}
      </summary>
      {children}
    </details>
  );
}

const PROCESS_IC: Record<ProcessBlock["processKind"], string> = {
  reasoning: "spark",
  plan: "plan",
  command: "bash",
  file_change: "edit",
  mcp: "term",
  agent: "spark",
  hook: "shield",
  server_tool: "term",
  web_search: "research",
  task: "plan",
  terminal: "bash",
  model: "cpu",
  safety: "shield",
  diff: "edit",
  compaction: "simplify",
};

function ProcessActivity({ block, onOpenFile, openOverride, onOpenChange,
  onInteractionStart, onInteractionEnd }: {
  block: ProcessBlock;
  onOpenFile?: (path: string, line?: number) => void;
  openOverride?: boolean;
  onOpenChange?: (open: boolean) => void;
  onInteractionStart?: () => number;
  onInteractionEnd?: (token: number) => void;
}) {
  const filePaths = block.processKind === "file_change"
    ? filePathsFromInput(block.input) : [];
  const hasBody = !!(block.summary || block.detail || block.output || block.diff
    || block.progress || block.command || block.cwd || block.plan?.length
    || (block.input && Object.keys(block.input).length));
  const body = (
    <>
      {block.progress && <div className="process-progress">{block.progress}</div>}
      {block.explanation && <div className="process-copy">{block.explanation}</div>}
      {block.plan && block.plan.length > 0 && (
        <ol className="process-plan">
          {block.plan.map((entry, index) => (
            <li key={`${index}-${entry.step}`} className={`plan-${entry.status}`}>
              <span>{entry.status === "completed" ? "✓" : entry.status === "inProgress" ? "•" : "○"}</span>
              <span>{entry.step}</span>
            </li>
          ))}
        </ol>
      )}
      {block.command && <pre className="tool-pre process-command">$ {block.command}</pre>}
      {block.cwd && <div className="process-meta">{block.cwd}</div>}
      {block.summary && <div className="process-copy">{block.summary}</div>}
      {block.detail && <pre className="tool-pre">{block.detail}</pre>}
      {onOpenFile && filePaths.map((filePath) => (
        <button key={filePath} type="button" className="process-file-link"
          onClick={() => onOpenFile(filePath)}>
          <Icon name="file" size={14} /><span>{filePath}</span>
        </button>
      ))}
      {block.input && Object.keys(block.input).length > 0 && filePaths.length === 0 && (
        <pre className="tool-pre">{JSON.stringify(block.input, null, 2)}</pre>
      )}
      {block.output && <pre className="tool-pre">{block.output}{block.truncated ? "\n…(truncated)" : ""}</pre>}
      {block.diff && <pre className="tool-pre tool-diff">{block.diff}</pre>}
      {(block.exit_code != null || block.duration_ms != null) && (
        <div className="tool-meta">
          {block.exit_code != null && <span>exit {block.exit_code}</span>}
          {block.duration_ms != null && <span>{durationLabel(block.duration_ms)}</span>}
        </div>
      )}
    </>
  );

  if (!hasBody) {
    return (
      <div className={`process-activity process-${block.status}`}>
        <span className="process-item-ic"><Icon name={PROCESS_IC[block.processKind]} size={15} /></span>
        <span className="process-item-title">{block.title}</span>
        <span className="process-item-status">{statusIcon(block.status, block.done)}</span>
      </div>
    );
  }
  return (
    <ProcessDisclosure className={`process-activity process-${block.status}`}
      openOverride={openOverride} onOpenChange={onOpenChange}
      onInteractionStart={onInteractionStart}
      onInteractionEnd={onInteractionEnd}
      summary={
        <>
        <span className="process-item-ic"><Icon name={PROCESS_IC[block.processKind]} size={15} /></span>
        <span className="process-item-title">{block.title}</span>
        <span className="process-item-status">{statusIcon(block.status, block.done)}</span>
        <span className="process-item-chev"><Icon name="chev" size={14} /></span>
        </>
      }>
        <div className="process-item-body">{body}</div>
    </ProcessDisclosure>
  );
}

function TimelineItem({ block, onOpenFile, imageAssets, onLoadImage, onPreviewImage,
  itemOpen, onItemOpenChange, onInteractionStart, onInteractionEnd }: {
  block: Block;
  onOpenFile?: (path: string, line?: number) => void;
  imageAssets?: Record<string, InlineImageAsset>;
  onLoadImage?: (path: string) => boolean;
  onPreviewImage?: (src: string, alt: string) => void;
  itemOpen?: (key: string) => boolean | undefined;
  onItemOpenChange?: (key: string, open: boolean) => void;
  onInteractionStart?: () => number;
  onInteractionEnd?: (token: number) => void;
}) {
  if (block.kind === "process") {
    const key = `process:${block.item_id}`;
    return <ProcessActivity
      block={block as ProcessBlock} onOpenFile={onOpenFile}
      openOverride={itemOpen?.(key)}
      onOpenChange={(open) => onItemOpenChange?.(key, open)}
      onInteractionStart={onInteractionStart}
      onInteractionEnd={onInteractionEnd} />;
  }
  const text = block as TextBlock;
  if (text.channel === "thinking") {
    const key = `reasoning:${text.message_id}`;
    return (
      <ProcessDisclosure className="process-reasoning"
        openOverride={itemOpen?.(key)}
        onOpenChange={(open) => onItemOpenChange?.(key, open)}
        onInteractionStart={onInteractionStart}
        onInteractionEnd={onInteractionEnd}
        summary={<><Icon name="spark" size={14} /><span>思考</span>
          <Icon name="chev" size={13} /></>}>
        <div className="process-reasoning-body"><MessageBlock text={text.text}
          done={text.done} onOpenFile={onOpenFile} imageAssets={imageAssets}
          onLoadImage={onLoadImage} onPreviewImage={onPreviewImage} /></div>
      </ProcessDisclosure>
    );
  }
  return <div className="process-commentary"><MessageBlock text={text.text}
    done={text.done} onOpenFile={onOpenFile} imageAssets={imageAssets}
    onLoadImage={onLoadImage} onPreviewImage={onPreviewImage} /></div>;
}

type TimelineRow =
  | { kind: "item"; block: TextBlock | ProcessBlock }
  | { kind: "tools"; tools: ToolBlock[] };

function groupTimelineRows(items: Block[]): TimelineRow[] {
  const rows: TimelineRow[] = [];
  for (const block of items) {
    if (block.kind !== "tool") {
      rows.push({ kind: "item", block });
      continue;
    }
    const previous = rows[rows.length - 1];
    if (previous?.kind === "tools") previous.tools.push(block);
    else rows.push({ kind: "tools", tools: [block] });
  }
  return rows;
}

function isCodexPresentationNoise(block: Block): boolean {
  if (block.kind === "text" && block.channel === "thinking") return true;
  if (block.kind !== "process") return false;
  if (block.processKind === "reasoning") return true;
  if (block.processKind !== "hook") return false;
  // Successful/pending preToolUse hooks are implementation detail around each
  // command. Rendering them between ToolBlocks splits one useful tool batch
  // into a noisy Hook -> one tool -> Hook sequence. Keep actionable failures,
  // but let ordinary hooks disappear so adjacent tools collapse together.
  return !["failed", "declined", "cancelled", "interrupted"].includes(block.status);
}

export function ProcessTimeline({ blocks, done, durationMs, startTs, doneTs, onOpenFile,
  deferredCount = 0, detailLoading = false, onLoadDetail,
  imageAssets, onLoadImage, onPreviewImage, engine = "claude",
  openOverride, onOpenChange, itemOpen, onItemOpenChange,
  onInteractionStart, onInteractionEnd }: {
  blocks: Block[];
  done: boolean;
  durationMs?: number;
  startTs?: number;
  doneTs?: number;
  onOpenFile?: (path: string, line?: number) => void;
  deferredCount?: number;
  detailLoading?: boolean;
  onLoadDetail?: () => void;
  imageAssets?: Record<string, InlineImageAsset>;
  onLoadImage?: (path: string) => boolean;
  onPreviewImage?: (src: string, alt: string) => void;
  engine?: "claude" | "codex";
  openOverride?: boolean;
  onOpenChange?: (open: boolean) => void;
  itemOpen?: (key: string) => boolean | undefined;
  onItemOpenChange?: (key: string, open: boolean) => void;
  onInteractionStart?: () => number;
  onInteractionEnd?: (token: number) => void;
}) {
  // Codex does not expose its private chain of thought in official clients.
  // Keep actionable commentary, plans, hook failures and tools, but suppress
  // synthetic reasoning and successful hook plumbing so consecutive tool calls
  // collapse into one useful group.
  const items = processBlocks(blocks).filter((block) => engine !== "codex" || !(
    isCodexPresentationNoise(block)
  ));
  const complete = done && !hasActiveProcess(items);
  const [uncontrolledOpen, setUncontrolledOpen] = useState(!complete);
  const open = openOverride ?? uncontrolledOpen;
  const [now, setNow] = useState(Date.now());
  const manuallyToggled = useRef(false);
  const tapGuard = useRef(new PointerTapGuard());
  const interactionTokens = useRef(new Map<number, number>());
  const releaseInteractionFrame = useRef<number | null>(null);

  useEffect(() => {
    if (!manuallyToggled.current) setUncontrolledOpen(!complete);
  }, [complete]);
  useEffect(() => {
    if (complete) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [complete]);
  useEffect(() => () => {
    if (releaseInteractionFrame.current !== null) {
      window.cancelAnimationFrame(releaseInteractionFrame.current);
      releaseInteractionFrame.current = null;
    }
    for (const token of interactionTokens.current.values()) {
      onInteractionEnd?.(token);
    }
    interactionTokens.current.clear();
  }, [onInteractionEnd]);

  const hasDeferredDetail = items.length === 0 && deferredCount > 0;
  if (!items.length && !hasDeferredDetail) return null;
  // A completed timeline is collapsed. Do not allocate/group hundreds of
  // historical rows until the user actually opens it.
  const rows = open ? groupTimelineRows(items) : [];
  const toolCount = items.reduce((count, block) => count + (block.kind === "tool" ? 1 : 0), 0);
  const countLabel = hasDeferredDetail
    ? `${deferredCount} 项`
    : engine === "codex" && toolCount === items.length
      ? `${toolCount} 个工具调用`
      : `${items.length} 项`;
  const elapsed = complete
    ? durationMs != null && durationMs > 0
      ? durationMs
      : engine === "claude" && startTs != null && doneTs != null
        ? Math.max(0, doneTs - startTs)
        : durationMs ?? 0
    : Math.max(0, now - (startTs ?? now));
  const toggle = () => {
    manuallyToggled.current = true;
    if (hasDeferredDetail) {
      if (!detailLoading) onLoadDetail?.();
      setUncontrolledOpen(true);
      onOpenChange?.(true);
      return;
    }
    const next = !open;
    setUncontrolledOpen(next);
    onOpenChange?.(next);
  };
  const pointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    tapGuard.current.pointerDown(event.pointerId, event.clientX, event.clientY);
    const token = onInteractionStart?.();
    if (token != null) interactionTokens.current.set(event.pointerId, token);
  };
  const pointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
    tapGuard.current.pointerMove(event.pointerId, event.clientX, event.clientY);
  };
  const pointerUp = (event: ReactPointerEvent<HTMLButtonElement>) => {
    tapGuard.current.pointerUp(event.pointerId);
    releaseInteractions();
  };
  const pointerCancel = (event: ReactPointerEvent<HTMLButtonElement>) => {
    tapGuard.current.pointerCancel(event.pointerId);
    releaseInteractions();
  };
  const releaseInteractions = () => {
    if (releaseInteractionFrame.current !== null) {
      window.cancelAnimationFrame(releaseInteractionFrame.current);
    }
    // Native click is dispatched after pointerup in the same task. Keep the
    // viewport frozen through the following ResizeObserver frame as well, so
    // the clicked disclosure can settle before output following resumes.
    releaseInteractionFrame.current = window.requestAnimationFrame(() => {
      releaseInteractionFrame.current = window.requestAnimationFrame(() => {
        releaseInteractionFrame.current = null;
        for (const token of interactionTokens.current.values()) {
          onInteractionEnd?.(token);
        }
        interactionTokens.current.clear();
      });
    });
  };
  return (
    <section className={`turn-process${open && !hasDeferredDetail ? " open" : ""}`}>
      <button type="button" className="turn-process-head"
        aria-expanded={open && !hasDeferredDetail} aria-busy={detailLoading}
        onPointerDown={pointerDown} onPointerMove={pointerMove}
        onPointerUp={pointerUp} onPointerCancel={pointerCancel}
        onClick={(event) => {
          if (!tapGuard.current.consumeClick(event.detail)) {
            event.preventDefault();
            return;
          }
          toggle();
        }}>
        <span className={`turn-process-state${complete ? " done" : " running"}`}>
          {detailLoading || !complete
            ? <span className="process-spin" />
            : <Icon name="verify" size={14} />}
        </span>
        <span>{complete ? "已处理" : "正在处理"} {durationLabel(elapsed)}</span>
        <span className="turn-process-count">{countLabel}</span>
        <Icon name="chev" size={15} />
      </button>
      {open && !hasDeferredDetail && <div className="process-timeline">{rows.map((row) => (
        row.kind === "tools"
          ? <ToolGroup key={`tools-${row.tools[0].tool_use_id}`} tools={row.tools} />
          : <TimelineItem key={row.block.kind === "text"
              ? `text-${row.block.message_id}` : `process-${row.block.item_id}`}
              block={row.block} onOpenFile={onOpenFile}
              imageAssets={imageAssets} onLoadImage={onLoadImage}
              onPreviewImage={onPreviewImage}
              itemOpen={itemOpen} onItemOpenChange={onItemOpenChange}
              onInteractionStart={onInteractionStart}
              onInteractionEnd={onInteractionEnd} />
      ))}</div>}
    </section>
  );
}
