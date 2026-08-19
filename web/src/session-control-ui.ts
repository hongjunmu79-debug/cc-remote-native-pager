import type { Engine, SessionControl } from "./protocol";
import { sessionControlLocksInput } from "./protocol";

export type TerminalControlTone =
  | "remote"
  | "attached"
  | "attention"
  | "disconnected";

export interface ControlPresentation {
  locked: boolean;
  pending: boolean;
  disconnected: boolean;
  title: string;
  detail: string;
  placeholder?: string;
  action?: "迁移" | "已登记";
  tone: TerminalControlTone;
  connection: string;
  backend: string;
  terminal: string;
  remote: string;
}

const DISCONNECTED_REASON = /(?:session[_ -]?exited|broker.{0,12}(?:断开|退出)|(?:连接|通道).{0,8}(?:断开|退出)|disconnected|not connected)/i;

/** A producer can briefly retain input_busy while reporting that its terminal
 * transport has exited. Copy and colour must follow the stronger reason rather
 * than telling the user that a dead channel is merely busy. */
export function sessionControlDisconnected(control: SessionControl): boolean {
  return DISCONNECTED_REASON.test(control.reason?.trim() ?? "");
}

function writeLabel(control: SessionControl, disconnected: boolean): string {
  if (disconnected) return "等待恢复";
  if (control.control_mode === "external_cli"
      || control.control_mode === "agent_view"
      || control.control_mode === "desktop") {
    return control.write_state === "takeover_pending" ? "等待接管" : "只读";
  }
  switch (control.write_state) {
    case "writable": return "可写";
    case "read_only": return "只读";
    case "takeover_pending": return "等待接管";
    case "input_busy": return "输入忙碌";
  }
}

function toneFor(control: SessionControl, disconnected: boolean): TerminalControlTone {
  if (disconnected) return "disconnected";
  if (sessionControlLocksInput(control)) return "attention";
  if ((control.control_mode === "codex_shared" || control.control_mode === "claude_broker")
      && control.terminal_attached) return "attached";
  return "remote";
}

/** Map authoritative SessionControl state to both Composer gating and the
 * terminal-status card. Shared backend connectivity is deliberately distinct
 * from a terminal whose ownership has been confirmed for this exact session. */
export function presentSessionControl(control: SessionControl): ControlPresentation {
  const locked = sessionControlLocksInput(control);
  const pending = control.write_state === "takeover_pending";
  const busy = control.write_state === "input_busy";
  const disconnected = sessionControlDisconnected(control);
  const canMigrate = control.can_takeover === true
    && control.control_mode !== "agent_view"
    && control.control_mode !== "desktop";
  const action = canMigrate ? (pending ? "已登记" : "迁移") : undefined;
  const reason = control.reason?.trim();
  const base = {
    locked, pending, disconnected, action,
    tone: toneFor(control, disconnected),
    remote: writeLabel(control, disconnected),
  } as const;

  if (disconnected) {
    return {
      ...base,
      title: "终端连接已断开",
      detail: reason || "终端通道已经退出，正在恢复 Remote 控制。",
      placeholder: locked ? "终端连接已断开 — 正在恢复 Remote…" : undefined,
      connection: control.control_mode === "claude_broker"
        ? "Claude Broker 共享通道" : "共享终端通道",
      backend: "连接异常",
      terminal: "已断开",
    };
  }

  switch (control.control_mode) {
    case "codex_shared":
      return {
        ...base,
        title: busy ? "共享输入忙碌"
          : control.terminal_attached ? "终端双向连接" : "Codex 后台通道可用",
        detail: reason || (locked
          ? (pending ? "正在等待共享写入通道切换。" : "共享写入通道暂时不可用，请稍候。")
          : (control.terminal_attached
              ? "浏览器与终端共享同一会话，可从任意一端继续输入。"
              : "app-server 共享后台可用，当前未检测到本机终端；Remote 仍可直接控制此会话。")),
        placeholder: locked
          ? (pending ? "正在等待共享写入通道…" : "共享输入通道忙碌 — 请稍候…")
          : undefined,
        connection: "Codex app-server 后台通道",
        backend: "可用",
        terminal: control.terminal_attached ? "已确认双向连接" : "未检测到本机终端",
      };
    case "claude_broker":
      return {
        ...base,
        title: busy ? "Claude 输入通道忙碌"
          : control.terminal_attached ? "终端双向连接" : "Claude Broker 通道可用",
        detail: reason || (locked
          ? (pending ? "正在等待 Broker 切换写入通道。" : "Broker 当前仅同步会话，暂不能输入。")
          : (control.terminal_attached
              ? "浏览器与终端共享同一会话，可从任意一端继续输入。"
              : "Broker 共享后台在线，当前没有终端连接。")),
        placeholder: locked
          ? (pending ? "正在等待 Broker 切换…" : "Claude 输入通道忙碌 — 请稍候…")
          : undefined,
        connection: "Claude Broker 共享通道",
        backend: "可用",
        terminal: control.terminal_attached ? "已确认双向连接" : "未检测到本机终端",
      };
    case "external_cli":
      return {
        ...base,
        title: pending ? "等待接管" : "外部 CLI 只读镜像",
        detail: reason || (pending
          ? "已登记接管请求，将在安全边界切换到 Remote 控制。"
          : canMigrate
            ? "会话正以只读方式实时同步；接管后即可从这里继续。"
            : "会话正以只读方式实时同步，请在外部 CLI 中继续。"),
        placeholder: pending ? "正在等待控制权接管…" : "外部 CLI 控制中 — Web 只读",
        connection: "外部 CLI 实时镜像",
        backend: "未使用共享后台",
        terminal: "已确认外部终端",
      };
    case "agent_view":
      return {
        ...base,
        title: "代理视图 · 只读",
        detail: reason || "这是代理运行视图，可观察进展，但不能从这里发送输入。",
        placeholder: "代理视图 — 仅供查看",
        connection: "代理观察通道",
        backend: "已连接",
        terminal: "不适用",
      };
    case "desktop":
      return {
        ...base,
        title: "Codex App 使用中 · Web 只读",
        detail: reason || "Codex App 正在运行此会话；消息会实时同步，完成后 Web 自动恢复可写。",
        placeholder: "Codex App 使用中 — Web 只读",
        connection: "Codex App 私有 app-server",
        backend: "App 会话运行中",
        terminal: "Codex App 使用中",
      };
    case "remote":
      return {
        ...base,
        title: pending ? "等待接管" : busy ? "Remote 输入忙碌"
          : locked ? "Remote 会话只读" : "终端未连接",
        detail: reason || (pending
          ? "正在等待 Remote 写入通道切换。"
          : busy ? "Remote 输入通道正在处理请求，请稍候。"
            : locked ? "当前 Remote 会话暂时只读。"
              : "当前会话由 Remote 直接控制，没有终端占用。"),
        placeholder: locked
          ? (pending ? "正在等待控制权接管…" : "当前会话暂时只读")
          : undefined,
        connection: "Remote 直连",
        backend: "未使用共享后台",
        terminal: "未连接",
      };
  }
}

export function presentMissingSessionControl(engine: Engine): ControlPresentation {
  return {
    locked: false,
    pending: false,
    disconnected: false,
    title: "终端状态未知",
    detail: "正在等待服务端报告当前会话的终端连接状态。",
    tone: "remote",
    connection: "Remote 直连",
    backend: engine === "codex" ? "等待 Codex 状态" : "等待 Claude 状态",
    terminal: "未确认",
    remote: "等待状态",
  };
}

/** Compatibility presentation for pre-v15 wrappers. Keep the old read-only
 * mirror operable during rolling deploys without pretending it is revisioned. */
export function presentLegacyExternalControl(
  engine: Engine,
  pending: boolean,
  message?: string | null,
): ControlPresentation {
  return {
    locked: true,
    pending,
    disconnected: false,
    title: pending ? "等待接管" : "外部 CLI 只读镜像",
    detail: message?.trim() || (engine === "codex"
      ? "会话正由外部 Codex 驱动并实时同步；接管后即可从这里继续。"
      : "会话正由外部 Claude Code 驱动并实时同步；接管后即可从这里继续。"),
    placeholder: pending ? "正在等待控制权接管…" : "外部 CLI 控制中 — Web 只读",
    action: pending ? "已登记" : "迁移",
    tone: "attention",
    connection: "外部 CLI 实时镜像（兼容模式）",
    backend: "旧版状态通道",
    terminal: "已检测外部终端",
    remote: pending ? "等待接管" : "只读",
  };
}
