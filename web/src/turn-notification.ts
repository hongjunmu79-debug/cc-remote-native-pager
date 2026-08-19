import type { TurnEnd, TurnResult } from "./protocol";

export type TurnNotificationOutcome = "success" | "failed" | "interrupted";

export function classifyTurnNotification(
  result: TurnResult,
): TurnNotificationOutcome {
  const subtype = result.subtype.toLowerCase();
  if (["error_during_execution", "interrupted", "cancelled", "canceled"].includes(subtype)) {
    return "interrupted";
  }
  return result.is_error ? "failed" : "success";
}

export function turnNotificationBody(label: string, result: TurnResult): string {
  switch (classifyTurnNotification(result)) {
    case "interrupted": return `${label} 会话已中断`;
    case "failed": return `${label} 会话执行失败`;
    default: return `${label} 会话已经完成`;
  }
}

export function turnNotificationTag(message: TurnEnd): string {
  const boundary = message.turn_id ?? message.seq ?? message.ts;
  return `turn-${message.sid ?? "unknown"}-${boundary}`;
}
