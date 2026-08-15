import { useEffect, useRef } from "react";
import {
  MAX_NATIVE_FRAME_BYTES,
  NATIVE_PAGER_BRIDGE_VERSION,
  parseNativeCommand,
  type NativeCommandEnvelope,
  type NativeCommandResult,
  type NativeOutboundEnvelope,
  type NativePagerSnapshotPayload,
} from "./contract.ts";

declare global {
  interface Window {
    ccRemoteNative?: { postMessage: (message: string) => void };
    __CC_REMOTE_NATIVE_RECEIVE__?: (message: string) => void;
  }
}

export interface NativePagerBridgeProps {
  snapshot: NativePagerSnapshotPayload;
  onCommand: (command: NativeCommandEnvelope) => NativeCommandResult | Promise<NativeCommandResult>;
}

function postNative(envelope: NativeOutboundEnvelope): boolean {
  const target = window.ccRemoteNative;
  if (!target || typeof target.postMessage !== "function") return false;
  const raw = JSON.stringify(envelope);
  if (new TextEncoder().encode(raw).byteLength > MAX_NATIVE_FRAME_BYTES) return false;
  try { target.postMessage(raw); return true; } catch { return false; }
}

function safeMessage(value: unknown): string {
  if (!(value instanceof Error)) return "命令处理失败";
  const normalized = value.message.replace(/\s+/g, " ").trim();
  return normalized.slice(0, 240) || "命令处理失败";
}

export function NativePagerBridge({ snapshot, onCommand }: NativePagerBridgeProps) {
  const bridgeInstanceId = useRef(crypto.randomUUID());
  const sequence = useRef(0);
  const handler = useRef(onCommand);
  const commandResults = useRef(new Map<string, Promise<NativeCommandResult>>());
  handler.current = onCommand;
  const snapshotJson = JSON.stringify(snapshot);

  useEffect(() => {
    const receive = (raw: string) => {
      const command = parseNativeCommand(raw);
      if (!command) return;
      let result = commandResults.current.get(command.commandId);
      if (!result) {
        result = Promise.resolve(handler.current(command)).catch((error: unknown) => ({
          accepted: false,
          message: safeMessage(error),
        }));
        commandResults.current.set(command.commandId, result);
        while (commandResults.current.size > 128) {
          const oldest = commandResults.current.keys().next().value;
          if (oldest === undefined) break;
          commandResults.current.delete(oldest);
        }
      }
      void result.then((outcome) => {
        postNative({
          bridgeVersion: NATIVE_PAGER_BRIDGE_VERSION,
          type: "commandAck",
          bridgeInstanceId: bridgeInstanceId.current,
          emittedAt: Date.now(),
          commandId: command.commandId,
          accepted: outcome.accepted,
          message: outcome.message,
        });
      });
    };
    window.__CC_REMOTE_NATIVE_RECEIVE__ = receive;
    return () => {
      if (window.__CC_REMOTE_NATIVE_RECEIVE__ === receive) {
        delete window.__CC_REMOTE_NATIVE_RECEIVE__;
      }
    };
  }, []);

  useEffect(() => {
    sequence.current += 1;
    const payload = JSON.parse(snapshotJson) as NativePagerSnapshotPayload;
    postNative({
      bridgeVersion: NATIVE_PAGER_BRIDGE_VERSION,
      type: "snapshot",
      bridgeInstanceId: bridgeInstanceId.current,
      sequence: sequence.current,
      emittedAt: Date.now(),
      payload,
    });
  }, [snapshotJson]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      postNative({
        bridgeVersion: NATIVE_PAGER_BRIDGE_VERSION,
        type: "heartbeat",
        bridgeInstanceId: bridgeInstanceId.current,
        emittedAt: Date.now(),
      });
    }, 15_000);
    return () => window.clearInterval(timer);
  }, []);

  return null;
}
