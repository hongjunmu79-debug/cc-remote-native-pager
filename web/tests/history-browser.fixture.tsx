import { useCallback, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";

import "../src/index.css";
import type { Turn } from "../src/reducer";
import type { QueryImg } from "../src/protocol";
import { ChatView } from "../src/components/ChatView";
import { PendingImageAttachments } from "../src/components/PendingImageAttachments";

function finalTurn(id: string, paragraphs: number): Turn {
  const text = Array.from(
    { length: paragraphs },
    (_, index) => `${id} 的第 ${index + 1} 段动态高度内容，用于验证历史分页后的真实浏览器布局。`,
  ).join("\n\n");
  return {
    id,
    prompt: `用户问题 ${id}`,
    blocks: [{
      kind: "text",
      message_id: `${id}-message`,
      channel: "final",
      text,
      done: true,
    }],
    done: true,
    ts: Date.now(),
    doneTs: Date.now(),
  };
}

const INITIAL = [
  finalTurn("o1", 8),
  finalTurn("o2", 8),
  finalTurn("o3", 8),
  finalTurn("o4", 8),
];
function olderPage(page: number): Turn[] {
  const prefix = page === 1 ? "n" : `p${page}-`;
  return Array.from(
    { length: 8 },
    (_, index) => finalTurn(`${prefix}${index + 1}`, index === 7 ? 2 : 4),
  );
}
const SESSION_B = Array.from(
  { length: 4 },
  (_, index) => finalTurn(`b${index + 1}`, 6),
);

function timelineTurn(id: string): Turn {
  return {
    ...finalTurn(id, 3),
    blocks: [
      {
        kind: "process",
        item_id: `${id}-plan`,
        processKind: "plan",
        phase: "end",
        status: "completed",
        title: "计划",
        summary: "这个展开状态应跨虚拟卸载保留。",
        done: true,
      },
      {
        kind: "text",
        message_id: `${id}-reasoning`,
        channel: "thinking",
        text: "这段思考应当一次点击展开，并在虚拟卸载后保留状态。",
        done: true,
      },
      ...finalTurn(id, 3).blocks,
    ],
  };
}

function streamingTurn(id: string, paragraphs = 1): Turn {
  const text = Array.from(
    { length: paragraphs },
    (_, index) => `${id} 正在输出第 ${index + 1} 段，这些内容会让最新一轮持续增高。`,
  ).join("\n\n");
  return {
    id,
    prompt: `用户问题 ${id}`,
    blocks: [{
      kind: "text",
      message_id: `${id}-message`,
      channel: "final",
      text,
      done: false,
    }],
    done: false,
    ts: Date.now(),
  };
}

function dualImageTurn(): Turn {
  return {
    id: "dual-image",
    prompt: "这条消息只应占用一行图片布局",
    images: [{
      media_type: "image/png",
      data: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlK4h8AAAAASUVORK5CYII=",
    }],
    imageRefs: [{
      image_id: "history-image-1",
      media_type: "image/png",
      width: 1,
      height: 1,
      byte_size: 68,
    }],
    blocks: [],
    done: true,
    ts: Date.now(),
    doneTs: Date.now(),
  };
}

function compactToolsTurn(): Turn {
  return {
    id: "compact-tools",
    prompt: "连续工具调用应保持紧凑",
    blocks: [
      {
        kind: "tool",
        message_id: "compact-tool-message-1",
        tool_use_id: "compact-tool-1",
        tool: "shell",
        input: { command: "git status --short --branch" },
        done: true,
        result: { content: "clean", is_error: false },
      },
      {
        kind: "tool",
        message_id: "compact-tool-message-2",
        tool_use_id: "compact-tool-2",
        tool: "web_search",
        input: { query: "compact tool rows" },
        done: true,
        result: { content: "result", is_error: false },
      },
      {
        kind: "tool",
        message_id: "compact-tool-message-3",
        tool_use_id: "compact-tool-3",
        tool: "web_search",
        input: { query: "dense activity list" },
        done: true,
        result: { content: "result", is_error: false },
      },
    ],
    done: true,
    ts: Date.now(),
    doneTs: Date.now(),
  };
}

interface FixtureSession {
  turns: Turn[];
  cursor: string;
  hasMore: boolean;
  pagesLoaded: number;
}

export function HistoryBrowserFixture() {
  const params = useMemo(() => new URLSearchParams(window.location.search), []);
  const delayMs = Number(params.get("delay") ?? "30");
  const growthDelayMs = Number(params.get("growth-delay") ?? "500");
  const manualGrowth = params.has("manual-growth");
  const largeCount = Number(params.get("large") ?? "0");
  const pageCount = Math.max(1, Number(params.get("pages") ?? "1"));
  const large = largeCount > 0;
  const timeline = params.has("timeline");
  const interactiveTimeline = params.has("interactive-timeline");
  const dualImage = params.has("dual-image");
  const compactTools = params.has("compact-tools");
  const composerAttachment = params.has("composer-attachment");
  const composerResize = params.has("composer-resize");
  const timelineEngine = params.get("engine") === "claude" ? "claude" : "codex";
  const emptyFinalPage = params.has("empty-final");
  const initialA = useMemo(() => {
    if (dualImage) {
      return [dualImageTurn()];
    }
    if (compactTools) {
      return [compactToolsTurn()];
    }
    if (large) {
      return Array.from({ length: largeCount }, (_, index) =>
        finalTurn(`m${index + 1}`, 2));
    }
    if (timeline) {
      return [
        timelineTurn("timeline"),
        ...Array.from({ length: 80 }, (_, index) =>
          finalTurn(`f${index + 1}`, 4)),
      ];
    }
    if (interactiveTimeline) {
      return [
        timelineTurn("timeline"),
        streamingTurn("streaming"),
      ];
    }
    return INITIAL;
  }, [
    compactTools, dualImage, interactiveTimeline, large, largeCount, timeline,
  ]);
  const [sid, setSid] = useState("history-browser-session-a");
  const [sessions, setSessions] = useState<Record<string, FixtureSession>>({
    "history-browser-session-a": {
      turns: initialA,
      cursor: initialA[0]?.id ?? "",
      hasMore: !compactTools && !large && !timeline,
      pagesLoaded: 0,
    },
    "history-browser-session-b": {
      turns: SESSION_B,
      cursor: "b1",
      hasMore: false,
      pagesLoaded: 0,
    },
  });
  const [loads, setLoads] = useState(0);
  const [historyRevision, setHistoryRevision] = useState("revision-1");
  const [composerExpanded, setComposerExpanded] = useState(false);
  const [pendingImages, setPendingImages] = useState<QueryImg[]>(() =>
    composerAttachment ? [{
      media_type: "image/png",
      data: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlK4h8AAAAASUVORK5CYII=",
    }] : []);
  const active = sessions[sid];
  const growOlderRow = useCallback((targetSid: string) => {
    setSessions((current) => ({
      ...current,
      [targetSid]: {
        ...current[targetSid],
        turns: current[targetSid].turns.map((turn) => turn.id === "n8"
          ? finalTurn("n8", 28)
          : turn),
      },
    }));
  }, []);

  const loadMore = useCallback(() => {
    const requestSid = sid;
    if (!sessions[requestSid]?.hasMore) return false;
    setLoads((value) => value + 1);
    window.setTimeout(() => {
      if (emptyFinalPage) {
        setSessions((current) => ({
          ...current,
          [requestSid]: {
            ...current[requestSid],
            cursor: "history-start",
            hasMore: false,
          },
        }));
        return;
      }
      setSessions((current) => {
        const session = current[requestSid];
        const nextPage = session.pagesLoaded + 1;
        const page = olderPage(nextPage);
        return {
          ...current,
          [requestSid]: {
            ...session,
            turns: [...page, ...session.turns],
            cursor: page[0].id,
            hasMore: nextPage < pageCount,
            pagesLoaded: nextPage,
          },
        };
      });
      // Reproduce an image/Markdown/process row settling after the old 250 ms
      // anchor window has already expired.
      if (!manualGrowth) {
        window.setTimeout(() => growOlderRow(requestSid), growthDelayMs);
      }
    }, delayMs);
    return true;
  }, [
    delayMs, emptyFinalPage, growOlderRow, growthDelayMs, manualGrowth,
    pageCount, sessions, sid,
  ]);

  const appendTurn = () => {
    setSessions((current) => {
      const session = current[sid];
      const next = finalTurn(`live-${session.turns.length + 1}`, 4);
      return {
        ...current,
        [sid]: { ...session, turns: [...session.turns, next] },
      };
    });
  };

  const growStreamingTurn = () => {
    setSessions((current) => {
      const session = current[sid];
      return {
        ...current,
        [sid]: {
          ...session,
          turns: session.turns.map((turn) => turn.id === "streaming"
            ? streamingTurn(
              "streaming",
              Math.max(1, turn.blocks[0]?.kind === "text"
                ? turn.blocks[0].text.split("\n\n").length + 3
                : 4),
            )
            : turn),
        },
      };
    });
  };

  const replaceHistoryRevision = () => {
    const replacement = Array.from(
      { length: 24 },
      (_, index) => finalTurn(`r${index + 1}`, 3),
    );
    setSessions((current) => ({
      ...current,
      [sid]: {
        turns: [],
        cursor: "",
        hasMore: false,
        pagesLoaded: 0,
      },
    }));
    setHistoryRevision((current) =>
      current === "revision-1" ? "revision-2" : "revision-3");
    window.setTimeout(() => {
      setSessions((current) => ({
        ...current,
        [sid]: {
          turns: replacement,
          cursor: replacement[0].id,
          hasMore: false,
          pagesLoaded: 0,
        },
      }));
    }, 0);
  };

  return (
    <main style={{ height: "100dvh", display: "flex", flexDirection: "column" }}>
      <div style={{ flex: "none", minHeight: 24 }}>
        <output data-testid="load-count">{loads}</output>
        <button data-testid="switch-session" type="button"
          onClick={() => setSid((current) => current.endsWith("-a")
            ? "history-browser-session-b" : "history-browser-session-a")}>
          switch
        </button>
        <button data-testid="append-turn" type="button" onClick={appendTurn}>
          append
        </button>
        <button data-testid="replace-revision" type="button"
          onClick={replaceHistoryRevision}>
          replace revision
        </button>
        {composerAttachment && (
          <div className="attach show" data-testid="fixture-attachments">
            <PendingImageAttachments images={pendingImages}
              onRemove={(index) => setPendingImages((current) =>
                current.filter((_, candidate) => candidate !== index))} />
          </div>
        )}
        {interactiveTimeline && (
          <button data-testid="grow-stream" type="button"
            onClick={growStreamingTurn}>
            grow stream
          </button>
        )}
        {manualGrowth && (
          <button data-testid="grow-row" type="button"
            onClick={() => growOlderRow("history-browser-session-a")}>
            grow
          </button>
        )}
      </div>
      <ChatView
        sid={sid}
        turns={active.turns}
        engine={timelineEngine}
        hasMore={active.hasMore}
        historyRevision={historyRevision}
        historyCursor={active.cursor}
        onLoadMore={loadMore}
        onEdit={() => {}}
        onGetDiff={() => {}}
      />
      {composerResize && (
        <div data-testid="fixture-composer" style={{
          flex: "none",
          height: composerExpanded ? 132 : 48,
          borderTop: "1px solid #ddd",
        }}>
          <button data-testid="toggle-composer" type="button"
            onClick={() => setComposerExpanded((current) => !current)}>
            toggle composer actions
          </button>
        </div>
      )}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<HistoryBrowserFixture />);
