import assert from "node:assert/strict";
import {
  NATIVE_PAGER_BRIDGE_VERSION,
  parseNativeCommand,
} from "../src/native-pager/contract.ts";
import { projectNativePagerSnapshot } from "../src/native-pager/projector.ts";
import type { AppState, SessionRuntime } from "../src/reducer.ts";

const runtime = {
  state: "running",
  syncReady: true,
  mirroredRunning: false,
  external: false,
  control: null,
  pendingQuestion: null,
  turns: [{
  id: "turn-1", prompt: "private prompt", done: false, ts: 1_000,
  blocks: [{
    kind: "process", item_id: "proc-1", processKind: "file_change",
    phase: "start", status: "running", title: "编辑 MainActivity.kt",
    output: "SECRET_RAW_TOOL_OUTPUT", done: false,
  }],
  }],
} as unknown as SessionRuntime;

const state: AppState = {
  connState: "connected",
  wrapperOnline: true,
  focusedSid: "session-1",
  sessions: [{
    session_id: "session-1", summary: "Native Pager", cwd: "C:\\work\\pager",
    engine: "codex", state: "running", pinned: true,
  }],
  runtimes: { "session-1": runtime },
} as unknown as AppState;

const snapshot = projectNativePagerSnapshot(state, {
  machineId: "max-matebook", now: 5_000,
});
assert.equal(snapshot.tasks.length, 1);
assert.equal(snapshot.tasks[0].lifecycle, "running");
assert.equal(snapshot.tasks[0].activity, "editing");
assert.equal(snapshot.tasks[0].projectName, "pager");
assert.equal(snapshot.tasks[0].focused, true);
assert(snapshot.tasks[0].capabilities.includes("interrupt"));
assert(!JSON.stringify(snapshot).includes("SECRET_RAW_TOOL_OUTPUT"));
assert(!JSON.stringify(snapshot).includes("private prompt"));

runtime.pendingQuestion = {
  ask_id: "ask-1", question: "选择发布方式", options: [{ label: "灰度" }],
  allow_text: true, secret: false,
};
const waiting = projectNativePagerSnapshot(state, {
  machineId: "max-matebook", now: 5_000,
});
assert.equal(waiting.tasks[0].lifecycle, "waitingAnswer");
assert(waiting.tasks[0].capabilities.includes("answer"));

const valid = JSON.stringify({
  bridgeVersion: NATIVE_PAGER_BRIDGE_VERSION,
  type: "command",
  commandId: "12345678-abcd",
  action: { kind: "answerQuestion", taskId: "session-1", answer: "灰度" },
});
assert.equal(parseNativeCommand(valid)?.action.kind, "answerQuestion");
assert.equal(parseNativeCommand(valid.replace('"bridgeVersion":1', '"bridgeVersion":2')), null);
assert.equal(parseNativeCommand("not-json"), null);
assert.equal(parseNativeCommand(JSON.stringify({
  bridgeVersion: 1, type: "command", commandId: "short",
  action: { kind: "refreshSessions" },
})), null);

console.log("native pager bridge tests passed");
