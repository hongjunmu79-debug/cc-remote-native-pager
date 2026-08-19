import type { ErrorMsg } from "./protocol";

const OWNERSHIP_GUIDANCE = /(本机终端|原生 .*CLI|Codex App|Claude TUI)/;

function ownershipMessage(message: string): string | null {
  if (!OWNERSHIP_GUIDANCE.test(message)) return null;
  // Ownership rejections are deliberately authored user copy.  Still remove
  // protocol/implementation nouns that should never become product UI.
  return message
    .replace(/\b(?:error|exception|traceback|crash)\b/gi, "问题")
    .replace(/\bcc_[a-z_]+\b/gi, "")
    .trim();
}

export function presentTurnProblem(error: Pick<ErrorMsg, "code" | "message">): string {
  if (error.code === "busy") {
    return ownershipMessage(error.message)
      ?? "本次消息未发送，会话当前不可写，请稍后重试。";
  }
  if (error.code === "not_running") {
    return "本次消息未发送，会话暂时不可用，请重新进入后重试。";
  }
  if (error.code === "bad_prompt") {
    return "消息内容无法发送，请检查输入或附件后重试。";
  }
  if (error.code === "drain_timeout") {
    return "停止操作未及时完成，会话正在恢复。";
  }
  return "本次回复未完成，请重试。";
}

export function presentCommandProblem(
  error: Pick<ErrorMsg, "code" | "message">,
): string {
  switch (error.code) {
    case "wrapper_offline":
      return "设备正在重新连接…";
    case "invalid_cwd":
      return "所选目录不存在或不可用，请重新选择。";
    case "busy":
      return ownershipMessage(error.message)
        ?? "当前操作暂时无法执行，请稍后重试。";
    case "not_running":
      return "当前会话暂时不可用，请重新进入后重试。";
    case "bad_prompt":
      return "输入内容无效，请检查后重试。";
    case "auth":
      return "当前操作不适用于这个会话。";
    case "protocol":
      return "页面版本已更新，请刷新后重试。";
    case "fork_reconciling":
      return "正在确认派生结果，请稍候…";
    default:
      return "操作未完成，请稍后重试。";
  }
}

export function presentHistoricalTurnProblem(message: string): string {
  const normalized = message.trim().toLowerCase();
  if (!normalized || normalized === "error") return "该轮未正常结束";
  return message;
}
