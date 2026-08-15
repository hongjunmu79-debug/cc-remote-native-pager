import type {
  AppState, Block, ProcessBlock, SessionRuntime, ToolBlock, Turn,
} from "../reducer.ts";
import { mergeSessionActivityState } from "../session-order.ts";
import type { SessionInfo, State } from "../protocol.ts";
import {
  boundedNativeString,
  type NativePagerSnapshotPayload,
  type NativeSubagentProjection,
  type NativeTaskActivity,
  type NativeTaskCapability,
  type NativeTaskLifecycle,
  type NativeTaskProjection,
} from "./contract.ts";

const MAX_TASKS = 64;
const MAX_SUBAGENTS = 16;

export interface NativeProjectionContext {
  machineId: string;
  now?: number;
}

function parseTime(value: string | null | undefined): number | undefined {
  if (!value) return undefined;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function projectName(session: SessionInfo): string {
  const cwd = session.cwd?.replace(/[\\/]+$/, "");
  const leaf = cwd?.split(/[\\/]/).filter(Boolean).pop();
  return boundedNativeString(leaf, 80) ?? "cc-remote";
}

function latestTurn(runtime: SessionRuntime | undefined): Turn | undefined {
  return runtime?.turns[runtime.turns.length - 1];
}

function activeBlock(turn: Turn | undefined): Block | undefined {
  if (!turn) return undefined;
  for (let index = turn.blocks.length - 1; index >= 0; index -= 1) {
    const block = turn.blocks[index];
    if ((block.kind === "process" || block.kind === "tool") && !block.done) {
      return block;
    }
  }
  return undefined;
}

function isTesting(block: ProcessBlock | ToolBlock): boolean {
  const haystack = block.kind === "process"
    ? `${block.title} ${block.command ?? ""}` : `${block.title ?? ""} ${block.tool}`;
  return /(^|\b)(test|tests|testing|pytest|jest|vitest|gradle.*test|npm.*test)(\b|$)/i.test(haystack);
}

function activityFor(block: Block | undefined): NativeTaskActivity | undefined {
  if (!block || block.kind === "text") return undefined;
  if (isTesting(block)) return "testing";
  if (block.kind === "tool") {
    if (block.category === "file") return "reading";
    if (block.category === "command") return "executing";
    if (block.category === "agent") return "delegating";
    if (block.category === "web_search") return "searching";
    if (block.category === "mcp" || block.category === "server_tool") return "browsing";
    return "executing";
  }
  switch (block.processKind) {
    case "reasoning": case "plan": return "thinking";
    case "file_change": case "diff": return "editing";
    case "web_search": return "searching";
    case "agent": case "task": return "delegating";
    case "mcp": case "server_tool": return "browsing";
    case "command": case "terminal": case "hook": return "executing";
    default: return "thinking";
  }
}

function lifecycleFor(
  session: SessionInfo,
  runtime: SessionRuntime | undefined,
  effectiveState: State,
  wrapperOnline: boolean,
): NativeTaskLifecycle {
  if (!wrapperOnline) return "offline";
  if (runtime?.pendingQuestion) return "waitingAnswer";
  if (session.session_id.startsWith("tmp-") && effectiveState === "idle") return "starting";
  if (effectiveState !== "idle") return "running";
  const turn = latestTurn(runtime);
  if (turn?.done && (turn.interrupted || turn.error)) return "interrupted";
  if (turn?.done) return "succeeded";
  return "idle";
}

function subagentsFor(turn: Turn | undefined): NativeSubagentProjection[] {
  if (!turn) return [];
  const result: NativeSubagentProjection[] = [];
  for (const block of turn.blocks) {
    if (block.kind !== "process"
        || (block.processKind !== "agent" && block.processKind !== "task")) continue;
    const state = block.status === "succeeded" ? "succeeded"
      : block.status === "failed" ? "failed"
      : block.status === "cancelled" || block.status === "interrupted"
        ? "interrupted" : "running";
    result.push({
      id: boundedNativeString(block.item_id, 256) ?? `agent-${result.length}`,
      title: boundedNativeString(block.title, 120) ?? "子任务",
      state,
      latestStep: boundedNativeString(block.progress ?? block.summary, 160),
    });
    if (result.length >= MAX_SUBAGENTS) break;
  }
  return result;
}

function latestStepFor(
  runtime: SessionRuntime | undefined,
  lifecycle: NativeTaskLifecycle,
  block: Block | undefined,
): string | undefined {
  if (runtime?.pendingQuestion) {
    return boundedNativeString(runtime.pendingQuestion.question, 240);
  }
  if (block?.kind === "process") {
    return boundedNativeString(block.progress ?? block.summary ?? block.title, 240);
  }
  if (block?.kind === "tool") {
    return boundedNativeString(block.progress ?? block.title ?? block.tool, 240);
  }
  if (lifecycle === "succeeded") return "任务已完成";
  if (lifecycle === "interrupted") return "任务已中断";
  if (lifecycle === "offline") return "电脑端暂未连接";
  return undefined;
}

function capabilitiesFor(
  runtime: SessionRuntime | undefined,
  lifecycle: NativeTaskLifecycle,
  focused: boolean,
): NativeTaskCapability[] {
  const result: NativeTaskCapability[] = ["openChat", "pin"];
  const writable = runtime?.control
    ? runtime.control.write_state === "writable" : !runtime?.external;
  if (focused && lifecycle === "running" && writable) result.push("interrupt");
  if (focused && runtime?.pendingQuestion) result.push("answer");
  return result;
}

function projectTask(
  session: SessionInfo,
  state: AppState,
  now: number,
): NativeTaskProjection {
  const runtime = state.runtimes[session.session_id];
  const effectiveState = mergeSessionActivityState(
    session.state,
    runtime?.state,
    runtime?.mirroredRunning,
  ) ?? "idle";
  const lifecycle = lifecycleFor(session, runtime, effectiveState, state.wrapperOnline);
  const turn = latestTurn(runtime);
  const block = activeBlock(turn);
  const modifiedAt = parseTime(session.last_modified);
  const startedAt = turn?.ts ?? modifiedAt ?? now;
  const updatedAt = turn?.doneTs ?? modifiedAt ?? turn?.ts ?? now;
  const completedAt = turn?.done ? (turn.doneTs ?? updatedAt) : undefined;
  const focused = state.focusedSid === session.session_id;
  const project = projectName(session);
  const question = runtime?.pendingQuestion;
  return {
    id: session.session_id,
    engine: session.engine === "claude" ? "claude" : "codex",
    projectName: project,
    title: boundedNativeString(
      session.summary ?? session.first_prompt ?? project, 160) ?? project,
    lifecycle,
    activity: lifecycle === "running" ? (activityFor(block) ?? "thinking") : undefined,
    latestStep: latestStepFor(runtime, lifecycle, block),
    startedAt,
    updatedAt,
    completedAt,
    completedRevision: completedAt && turn
      ? `${turn.id}:${completedAt}:${turn.interrupted ? "i" : turn.error ? "e" : "s"}`
      : undefined,
    pinned: !!session.pinned,
    focused,
    capabilities: capabilitiesFor(runtime, lifecycle, focused),
    subagents: subagentsFor(turn),
    question: question ? {
      header: boundedNativeString(question.header, 80),
      question: boundedNativeString(question.question, 512) ?? "需要回答",
      options: question.options.slice(0, 12).map((option) => (
        boundedNativeString(option.label, 120) ?? "选项"
      )),
      allowText: question.allow_text !== false,
      secret: !!question.secret,
    } : undefined,
  };
}

export function projectNativePagerSnapshot(
  state: AppState,
  context: NativeProjectionContext,
): NativePagerSnapshotPayload {
  const now = context.now ?? Date.now();
  return {
    auth: "authenticated",
    connection: state.connState,
    wrapperOnline: state.wrapperOnline,
    machineId: boundedNativeString(context.machineId, 128) ?? "default",
    focusedTaskId: state.focusedSid ?? undefined,
    tasks: state.sessions.slice(0, MAX_TASKS).map((session) => (
      projectTask(session, state, now)
    )),
  };
}
