export const NATIVE_PAGER_BRIDGE_VERSION = 1 as const;
export const MAX_NATIVE_FRAME_BYTES = 256 * 1024;
export const MAX_NATIVE_COMMAND_BYTES = 16 * 1024;

export type NativeTaskLifecycle =
  | "offline" | "idle" | "starting" | "running"
  | "waitingAnswer" | "succeeded" | "interrupted";

export type NativeTaskActivity =
  | "thinking" | "reading" | "searching" | "editing"
  | "executing" | "testing" | "browsing" | "delegating";

export type NativeTaskCapability =
  | "openChat" | "interrupt" | "answer" | "pin";

export interface NativeSubagentProjection {
  id: string;
  title: string;
  state: "running" | "succeeded" | "failed" | "interrupted";
  latestStep?: string;
}

export interface NativeQuestionProjection {
  header?: string;
  question: string;
  options: string[];
  allowText: boolean;
  secret: boolean;
}

export interface NativeTaskProjection {
  id: string;
  engine: "claude" | "codex";
  projectName: string;
  title: string;
  lifecycle: NativeTaskLifecycle;
  activity?: NativeTaskActivity;
  latestStep?: string;
  startedAt: number;
  updatedAt: number;
  completedAt?: number;
  completedRevision?: string;
  pinned: boolean;
  focused: boolean;
  capabilities: NativeTaskCapability[];
  subagents: NativeSubagentProjection[];
  question?: NativeQuestionProjection;
}

export interface NativePagerSnapshotPayload {
  auth: "authenticated";
  connection: "connecting" | "connected" | "reconnecting" | "disconnected";
  wrapperOnline: boolean;
  machineId: string;
  focusedTaskId?: string;
  tasks: NativeTaskProjection[];
}

export interface NativeSnapshotEnvelope {
  bridgeVersion: typeof NATIVE_PAGER_BRIDGE_VERSION;
  type: "snapshot";
  bridgeInstanceId: string;
  sequence: number;
  emittedAt: number;
  payload: NativePagerSnapshotPayload;
}

export interface NativeHeartbeatEnvelope {
  bridgeVersion: typeof NATIVE_PAGER_BRIDGE_VERSION;
  type: "heartbeat";
  bridgeInstanceId: string;
  emittedAt: number;
}

export interface NativeCommandAckEnvelope {
  bridgeVersion: typeof NATIVE_PAGER_BRIDGE_VERSION;
  type: "commandAck";
  bridgeInstanceId: string;
  emittedAt: number;
  commandId: string;
  accepted: boolean;
  message?: string;
}

export type NativeOutboundEnvelope =
  | NativeSnapshotEnvelope | NativeHeartbeatEnvelope | NativeCommandAckEnvelope;

export type NativeCommandAction =
  | { kind: "focusTask"; taskId: string }
  | { kind: "interruptTask"; taskId: string }
  | { kind: "answerQuestion"; taskId: string; answer: string }
  | { kind: "setPinned"; taskId: string; pinned: boolean }
  | { kind: "refreshSessions" };

export interface NativeCommandEnvelope {
  bridgeVersion: typeof NATIVE_PAGER_BRIDGE_VERSION;
  type: "command";
  commandId: string;
  action: NativeCommandAction;
}

export interface NativeCommandResult {
  accepted: boolean;
  message?: string;
}

const TASK_ID = /^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$/;
const COMMAND_ID = /^[A-Za-z0-9-]{8,64}$/;

export function parseNativeCommand(raw: string): NativeCommandEnvelope | null {
  if (new TextEncoder().encode(raw).byteLength > MAX_NATIVE_COMMAND_BYTES) return null;
  let value: unknown;
  try { value = JSON.parse(raw); } catch { return null; }
  if (!value || typeof value !== "object") return null;
  const command = value as Partial<NativeCommandEnvelope>;
  if (command.bridgeVersion !== NATIVE_PAGER_BRIDGE_VERSION
      || command.type !== "command"
      || typeof command.commandId !== "string"
      || !COMMAND_ID.test(command.commandId)
      || !command.action || typeof command.action !== "object") return null;
  const action = command.action as Partial<NativeCommandAction> & {
    taskId?: unknown; answer?: unknown; pinned?: unknown;
  };
  if (action.kind === "refreshSessions") return command as NativeCommandEnvelope;
  if (typeof action.taskId !== "string" || !TASK_ID.test(action.taskId)) return null;
  if (action.kind === "focusTask" || action.kind === "interruptTask") {
    return command as NativeCommandEnvelope;
  }
  if (action.kind === "setPinned" && typeof action.pinned === "boolean") {
    return command as NativeCommandEnvelope;
  }
  if (action.kind === "answerQuestion" && typeof action.answer === "string"
      && new TextEncoder().encode(action.answer).byteLength <= 8 * 1024) {
    return command as NativeCommandEnvelope;
  }
  return null;
}

export function boundedNativeString(value: string | null | undefined,
                                    max: number): string | undefined {
  const normalized = value?.replace(/\s+/g, " ").trim();
  if (!normalized) return undefined;
  return normalized.length <= max
    ? normalized : `${normalized.slice(0, Math.max(0, max - 1))}…`;
}
