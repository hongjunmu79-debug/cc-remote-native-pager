import type { SessionList, Space } from "./protocol";

export function shouldAcceptSessionList(
  activeEngine: "claude" | "codex",
  activeSpace: Space,
  event: SessionList,
): boolean {
  return event.engine === activeEngine && (event.space ?? "code") === activeSpace;
}
