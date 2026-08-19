import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type SetStateAction,
} from "react";
import type { State, QueryImg, QueryFile, ContextReport, CollaborationModeName, SessionControl, EngineCapabilityKind } from "../protocol";
import { presentLegacyExternalControl, presentSessionControl } from "../session-control-ui";
import type { ConnState } from "../ws";
import { Icon } from "../icons";
import { clientSlashesFor, CODEX_PROMPTS, isKnownCodeOnlySlash, slashToken, matchCommands, parseSlash, modelsFor, effortsFor, permsFor, type Catalog } from "../data";
import { CommandSheet } from "./CommandSheet";
import { attachmentBytes, pickFiles } from "../img";
import type { PendingQuery } from "../reducer";
import { canEnqueueQuery } from "../runtime-drain";
import { ImeSubmitGuard } from "../ime-submit";
import {
  classifyBusySubmit,
  isComposerBusy,
  isInterruptSettling,
  isSettlingStopDisabled,
} from "../composer-submit";
import { workContextMetrics } from "../work-context";
import type { ComposerDraft, ComposerDraftStore } from "../composer-drafts";
import { PendingImageAttachments } from "./PendingImageAttachments";

interface Props {
  draftKey: string;
  draftStore: ComposerDraftStore;
  surface?: "code" | "work";
  state: State;
  connState: ConnState;
  wrapperOnline: boolean;
  sendMode: "interrupt" | "queue";
  setSendMode: (m: "interrupt" | "queue") => void;
  queue: PendingQuery[];
  allQueued: PendingQuery[];
  replaceableQueued: PendingQuery[];
  model: string;
  effort: string;
  perm: string;
  collaborationMode: CollaborationModeName;
  fast?: boolean | null;   // null until the wrapper reports the real service tier
  control?: SessionControl | null;
  // A native `claude`/`codex` in the terminal owns this session and is appending to
  // its transcript. We mirror it live but must NOT write: a cc session has a single
  // owner, so sending from here would fork the conversation.
  external?: boolean;
  takeoverPending?: boolean;
  takeoverMessage?: string | null;
  engine?: "claude" | "codex";
  catalog?: Catalog;   // engine-reported models/efforts; falls back to data.ts
  editPrompt: string | null;
  onEditConsumed: () => void;
  onSendQuery: (prompt: string, images?: QueryImg[], files?: QueryFile[]) => boolean;
  onInterrupt: () => void;
  onEnqueue: (query: PendingQuery) => void;
  onSetPending: (query: PendingQuery) => void;
  onDequeue: (i: number) => void;
  onSetModel: (model: string) => void;
  onSetEffort: (effort: string) => void;
  onSetServiceTier?: (tier: string) => void;
  onSetPerm: (perm: string) => void;
  onSetCollaborationMode: (mode: CollaborationModeName) => void;
  onClear: () => void;
  onContext: () => void;
  onOpenBtw?: () => void;
  onPreview?: (path: string) => void;
  onGoal?: (args: string) => void;
  onStatus?: () => void;
  onReview?: (
    target: "uncommittedChanges" | "baseBranch" | "commit" | "custom",
    value?: string,
  ) => void;
  onCompact?: () => void;
  onOpenExtensions?: (kind: EngineCapabilityKind | "all") => void;
  workArtifactCount?: number;
  onOpenArtifacts?: () => void;
  contextReport: ContextReport | null;
  contextError?: string | null;
}

export function Composer(p: Props) {
  const editPrompt = p.editPrompt;
  const onEditConsumed = p.onEditConsumed;
  const draftKeyRef = useRef(p.draftKey);
  const [draft, setDraft] = useState<ComposerDraft>(
    () => p.draftStore.get(p.draftKey));
  const input = draft.input;
  const images = draft.images;
  const files = draft.files;
  const updateDraft = useCallback((
    update: (current: ComposerDraft) => ComposerDraft,
  ) => {
    const key = draftKeyRef.current;
    setDraft((current) => {
      const next = update(current);
      p.draftStore.set(key, next);
      return next;
    });
  }, [p.draftStore]);
  const setInput = useCallback((next: SetStateAction<string>) => updateDraft((current) => ({
    ...current,
    input: typeof next === "function" ? next(current.input) : next,
  })), [updateDraft]);
  const setImages = useCallback((next: SetStateAction<QueryImg[]>) => updateDraft((current) => ({
    ...current,
    images: typeof next === "function" ? next(current.images) : next,
  })), [updateDraft]);
  const setFiles = useCallback((next: SetStateAction<QueryFile[]>) => updateDraft((current) => ({
    ...current,
    files: typeof next === "function" ? next(current.files) : next,
  })), [updateDraft]);
  const clearDraft = useCallback(() => updateDraft(() => ({
    input: "", images: [], files: [],
  })), [updateDraft]);
  // Only the modal pickers live in state now; the "/" command palette is a live
  // popover DERIVED from the composer text (no second input box).
  const [sheetKind, setSheetKind] = useState<"models" | "efforts" | "perms" | null>(null);
  const [ctxOpen, setCtxOpen] = useState(false);
  const ctxWrapRef = useRef<HTMLDivElement>(null);
  const workSettingsRef = useRef<HTMLDetailsElement>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const noticeTimer = useRef<number | null>(null);
  const [importing, setImporting] = useState(false);
  const [dragDepth, setDragDepth] = useState(0);
  const dragOver = dragDepth > 0;
  const taRef = useRef<HTMLTextAreaElement>(null);
  const imeSubmitRef = useRef(new ImeSubmitGuard());
  const buttonSendTimerRef = useRef<number | null>(null);
  const photoRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const pickFilesRef = useRef<(files: FileList | File[] | null) => Promise<void>>(
    async () => {});

  useLayoutEffect(() => {
    if (draftKeyRef.current === p.draftKey) return;
    draftKeyRef.current = p.draftKey;
    setDraft(p.draftStore.get(p.draftKey));
    setSheetKind(null);
    setCtxOpen(false);
    setNotice(null);
    if (noticeTimer.current !== null) {
      window.clearTimeout(noticeTimer.current);
      noticeTimer.current = null;
    }
    if (workSettingsRef.current?.open) workSettingsRef.current.open = false;
  }, [p.draftKey, p.draftStore]);

  // context popover: close on outside click
  useEffect(() => {
    if (!ctxOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (ctxWrapRef.current && !ctxWrapRef.current.contains(e.target as Node)) setCtxOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [ctxOpen]);

  // <details> has no controlled open state here, so keep one document-level
  // listener active for mouse and touch and close it when focus moves outside.
  useEffect(() => {
    const closeWorkSettings = () => {
      if (!workSettingsRef.current?.open) return;
      workSettingsRef.current.open = false;
      setCtxOpen(false);
    };
    const onPointerDown = (event: PointerEvent) => {
      const details = workSettingsRef.current;
      if (details?.open && event.target instanceof Node
          && !details.contains(event.target)) closeWorkSettings();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeWorkSettings();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  useEffect(() => () => {
    if (buttonSendTimerRef.current !== null) {
      window.clearTimeout(buttonSendTimerRef.current);
    }
  }, []);

  const busy = isComposerBusy(p.state);
  const offline = !p.wrapperOnline || p.connState !== "connected";
  const legacyControl = !p.control && p.external
    ? presentLegacyExternalControl(
        p.engine === "codex" ? "codex" : "claude",
        !!p.takeoverPending,
        p.takeoverMessage,
      ) : null;
  const controlUi = p.control ? presentSessionControl(p.control) : legacyControl;
  // The connection and authoritative write state independently gate input.
  const locked = offline || !!controlUi?.locked;
  const externalClaudeOwner = p.engine === "claude"
    ? (p.control?.control_mode === "external_cli" ? "外部 CLI"
      : p.control?.control_mode === "agent_view" ? "Agent View"
      : p.control?.control_mode === "desktop" ? "Claude Desktop"
      : (!p.control && p.external) ? "外部 CLI"
      : null)
    : null;
  // Claude's native surfaces do not expose their live model/effort/permission
  // controls to the Agent SDK. Keep Remote's saved takeover preferences visible,
  // but never present them as the active native runtime state.
  const deferredClaudeControls = externalClaudeOwner !== null;
  const hasText = input.trim().length > 0;
  const hasAttachments = images.length > 0 || files.length > 0;

  useEffect(() => {
    if (!locked) return;
    setSheetKind(null);
    setCtxOpen(false);
    if (workSettingsRef.current?.open) workSettingsRef.current.open = false;
  }, [locked]);

  // edit: refill the input box with a past prompt (user-bubble edit button)
  useEffect(() => {
    if (editPrompt != null) {
      setInput(editPrompt);
      onEditConsumed();
      setTimeout(() => taRef.current?.focus(), 0);
      if (taRef.current) {
        taRef.current.style.height = "auto";
        taRef.current.style.height = Math.min(taRef.current.scrollHeight, 132) + "px";
      }
    }
  }, [editPrompt, onEditConsumed, setInput]);

  const resetTaHeight = () => { if (taRef.current) taRef.current.style.height = "auto"; };
  const growTa = () => {
    if (taRef.current) {
      taRef.current.style.height = "auto";
      taRef.current.style.height = Math.min(taRef.current.scrollHeight, 132) + "px";
    }
  };
  useLayoutEffect(() => {
    growTa();
  }, [input, p.draftKey]);
  const focusTa = () => setTimeout(() => taRef.current?.focus(), 0);
  const flash = (msg: string) => {
    setNotice(msg);
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 2200);
  };

  const onInput = (v: string) => {
    setInput(v);
    growTa();
  };

  // Command palette = live suggestions derived from the input. Visible while the
  // user is typing a command name (after "/", before a space) that prefix-matches
  // at least one command. Typing past a match (/pet) hides it; deleting back
  // (/pe) shows it again — no separate input, no modal scrim over the composer.
  const cmdToken = slashToken(input);
  const cmdMatches = cmdToken !== null
    ? matchCommands(cmdToken, p.engine, p.surface ?? "code") : [];
  const cmdOpen = cmdMatches.length > 0;

  const onPickFiles = async (fl: FileList | File[] | null) => {
    if (importing) { flash("附件正在导入，请稍候"); return; }
    const targetDraftKey = draftKeyRef.current;
    setImporting(true);
    try {
      const batch = await pickFiles(
        fl, images.length + files.length, attachmentBytes(images, files));
      if (draftKeyRef.current === targetDraftKey) {
        if (batch.images.length) {
          setImages((previous) => [...previous, ...batch.images]);
        }
        if (batch.files.length) {
          setFiles((previous) => [...previous, ...batch.files]);
        }
      } else if (batch.images.length || batch.files.length) {
        const prior = p.draftStore.get(targetDraftKey);
        p.draftStore.set(targetDraftKey, {
          ...prior,
          images: [...prior.images, ...batch.images],
          files: [...prior.files, ...batch.files],
        });
      }
      if (batch.errors.length) flash(batch.errors.join("；"));
    } finally {
      setImporting(false);
    }
  };
  pickFilesRef.current = onPickFiles;

  // Whole-window drag-drop overlay. The effect is refreshed with the current
  // attachment limits/import state so its async drop handler never uses a stale
  // count or appends after a send.
  useEffect(() => {
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types || []).includes("Files");
    const onEnter = (e: DragEvent) => { if (hasFiles(e)) setDragDepth((d) => d + 1); };
    const onLeave = (e: DragEvent) => { if (hasFiles(e)) setDragDepth((d) => Math.max(0, d - 1)); };
    const onOver = (e: DragEvent) => { if (hasFiles(e)) e.preventDefault(); };
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      setDragDepth(0);
      if (e.dataTransfer?.files?.length) void pickFilesRef.current(e.dataTransfer.files);
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("dragover", onOver);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("drop", onDrop);
    };
  }, []);

  // paste images/files straight into the textarea (clipboard API)
  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.kind === "file") {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) { e.preventDefault(); void onPickFiles(files); }
  };

  // Send prompt text to cc, honoring busy/queue/interrupt rules.
  const submitPrompt = (prompt: string) => {
    if (importing) { flash("请等待附件导入完成"); return; }
    const query: PendingQuery = {
      prompt,
      images: images.length ? images : undefined,
      files: files.length ? files : undefined,
    };
    if (busy) {
      const action = classifyBusySubmit(
        p.state, p.sendMode, !!prompt || hasAttachments);
      if (action === "noop") return;
      const existing = p.sendMode === "queue" ? p.allQueued : p.replaceableQueued;
      if (!canEnqueueQuery(existing, query)) {
        flash("排队已满（最多 32 条 / 64 MiB），请先等待发送");
        return;
      }
      if (action === "enqueue") {
        p.onEnqueue(query);
        clearDraft(); resetTaHeight();
        return;
      }
      if (action === "interrupt-and-replace") p.onInterrupt();
      p.onSetPending(query);
      clearDraft(); resetTaHeight();
      return;
    }
    if (!prompt && !hasAttachments) return;
    if (p.onSendQuery(
        prompt, images.length ? images : undefined, files.length ? files : undefined)) {
      clearDraft(); resetTaHeight();
    }
  };

  // Client-side slash commands — never forwarded to cc. "/model <id>" passes the
  // id straight to set_model (hidden models included), aligning with Claude Code.
  const runClientSlash = (slash: string, args: string) => {
    switch (slash) {
      case "model":
        if (args) { p.onSetModel(args); flash(`正在切换模型：${args}`); }
        else setSheetKind("models");
        break;
      case "permissions": setSheetKind("perms"); break;
      case "clear": p.onClear(); break;
      // open the popup too (not just fetch) — same as clicking the context ring.
      case "context": p.onContext(); setCtxOpen(true); break;
      case "status": p.onStatus?.(); break;
      case "goal": p.onGoal?.(args); break;
      case "rewind": flash("Claude Rewind 暂未开放"); break;
      case "review": {
        const trimmed = args.trim();
        if (!trimmed) {
          p.onReview?.("uncommittedChanges");
        } else {
          const [kind, ...rest] = trimmed.split(/\s+/);
          const value = rest.join(" ").trim();
          if (kind === "base" || kind === "branch") {
            if (!value) { flash("用法：/review base <分支>"); return; }
            p.onReview?.("baseBranch", value);
          } else if (kind === "commit") {
            if (!value) { flash("用法：/review commit <SHA>"); return; }
            p.onReview?.("commit", value);
          } else if (kind === "custom") {
            if (!value) { flash("用法：/review custom <审查要求>"); return; }
            p.onReview?.("custom", value);
          } else {
            p.onReview?.("custom", trimmed);
          }
        }
        flash("正在启动 Codex 原生 Review…");
        break;
      }
      case "compact":
        if (args.trim()) { flash("/compact 不接受参数"); return; }
        p.onCompact?.();
        flash("正在启动 Codex 原生上下文压缩…");
        break;
      case "rollback": flash("Codex Rollback 暂未开放"); break;
      // /btw: open an ephemeral side-fork panel (both engines).
      case "btw": p.onOpenBtw?.(); break;
      case "preview":
        if (!args) {
          setInput("/preview ");
          focusTa();
          return;
        }
        p.onPreview?.(args);
        break;
      case "extensions": p.onOpenExtensions?.("all"); break;
      case "skills": p.onOpenExtensions?.("skill"); break;
      case "plugins": p.onOpenExtensions?.("plugin"); break;
      case "apps": p.onOpenExtensions?.("app"); break;
      case "mcp": p.onOpenExtensions?.("mcp"); break;
      case "hooks": p.onOpenExtensions?.("hook"); break;
      // Codex /fast flips only this thread's persisted service tier. The UI
      // waits for the wrapper's authoritative Fast event before changing state.
      case "fast": {
        p.onSetServiceTier?.("toggle");
        flash("正在切换快速模式…");
        break;
      }
      case "plan":
        if (p.engine === "codex") {
          p.onSetCollaborationMode("plan");
          flash("正在切换到 Plan 模式…");
        } else {
          p.onSetPerm("plan");
        }
        if (args) { submitPrompt(args); return; }
        break;
      case "normal":
        if (p.engine === "codex") {
          p.onSetCollaborationMode("default");
          flash("正在退出 Plan 模式…");
        } else {
          p.onSetPerm("bypassPermissions");
        }
        if (args) { submitPrompt(args); return; }
        break;
    }
    setInput(""); resetTaHeight();
  };

  // Pick a command from the palette. Client commands run now; cc skills
  // (/code-review …) fill the composer so the user can add args, then send.
  const pickCommand = (slash: string) => {
    if (clientSlashesFor(p.engine).has(slash)) { runClientSlash(slash, ""); focusTa(); return; }
    setInput("/" + slash + " ");
    focusTa(); growTa();
  };

  const send = (value = taRef.current?.value ?? input) => {
    if (locked || importing) return;
    const raw = value.trim();
    if (raw === "/") return;
    const parsed = parseSlash(raw);
    if (p.surface === "work" && parsed
        && isKnownCodeOnlySlash(parsed.slash, p.engine)) {
      flash(`/${parsed.slash} 是 Code 指令，请切换到 Code 使用`);
      return;
    }
    if (parsed && clientSlashesFor(p.engine).has(parsed.slash)) { runClientSlash(parsed.slash, parsed.args); return; }
    // Codex has no TUI slash layer over the app-server. /init is the one
    // compatibility prompt left; lifecycle operations above use native RPCs.
    if (p.engine === "codex" && parsed && CODEX_PROMPTS[parsed.slash]) {
      submitPrompt(CODEX_PROMPTS[parsed.slash] + (parsed.args ? "\n\n" + parsed.args : ""));
      return;
    }
    // Codex has no TUI slash layer, so an unknown "/xxx" is NOT a command — don't
    // ship it to the model as literal text (it would just improvise a fake reply,
    // e.g. "/fast" -> "已切到快节奏…"). Hint and keep the input. (cc is different:
    // it has its own slash/skill layer, so unknown slashes fall through to it.)
    if (p.engine === "codex" && parsed) { flash(`Codex 无此指令：/${parsed.slash}`); return; }
    // plain text, or a cc skill slash (/code-review …) forwarded verbatim to cc
    submitPrompt(raw);
  };

  const requestButtonSend = () => {
    if (buttonSendTimerRef.current !== null) return;
    buttonSendTimerRef.current = window.setTimeout(() => {
      buttonSendTimerRef.current = null;
      const buttonPrompt = taRef.current?.value ?? input;
      if (busy && !buttonPrompt.trim() && !hasAttachments) {
        if (p.state === "running") p.onInterrupt();
        return;
      }
      send(buttonPrompt);
    }, 0);
  };

  const stopping = busy && !hasText && !hasAttachments;
  const interruptSettling = isInterruptSettling(p.state);
  const sendIcon = !busy ? "send" : stopping ? "stop" : p.sendMode === "interrupt" ? "bolt" : "queue";
  const sendClass = "sendbtn" + ((stopping || (busy && p.sendMode === "interrupt" && (hasText || hasAttachments))) ? " interrupt" : "");
  const disabled = locked || importing || (!busy && !hasText && !hasAttachments)
    || isSettlingStopDisabled(p.state, hasText || hasAttachments);
  // Fall back to the raw id (not MODELS[0]) so a hidden model set via
  // "/model <id>" shows its actual id on the chip instead of "Mythos 5".
  const MODELS_E = modelsFor(p.engine, p.catalog), PERMS_E = permsFor(p.engine);
  const workSurface = p.surface === "work";
  const contextAvailable = p.contextReport?.available !== false;
  const workContext = workSurface && p.contextReport && contextAvailable
    ? workContextMetrics(p.contextReport)
    : null;
  // Do not substitute catalog defaults for an existing session.  Until the
  // wrapper reports its authoritative settings, the controls explicitly show
  // that they are still being read.  Unknown/hidden ids remain visible verbatim.
  const model = p.model
    ? (MODELS_E.find((m) => m.id === p.model)
      || { id: p.model, name: p.model, ds: "", ic: "cpu" })
    : null;
  const EFFORTS_E = effortsFor(p.engine, model?.id, p.catalog);
  const effort = p.effort
    ? (EFFORTS_E.find((e) => e.id === p.effort)
      || { id: p.effort, name: p.effort, ds: "", ic: "gauge3" })
    : null;
  const perm = p.perm
    ? (PERMS_E.find((x) => x.id === p.perm)
      || { id: p.perm, name: p.perm, short: p.perm, ds: "", ic: "shield" })
    : null;
  const stateZh: Record<State, string> = { idle: "空闲", running: "运行中", interrupting: "打断中", draining: "收尾中" };
  const modeCls = perm?.id === "plan" ? " plan" : perm?.danger ? " danger" : "";
  const hintBusy = p.state !== "idle";

  const inputControl = (placeholder: string) => (
    <textarea
      ref={taRef}
      rows={1}
      value={input}
      placeholder={importing ? "正在安全导入附件…"
        : offline ? "机器离线 — 等待重连…"
        : (controlUi?.placeholder ?? placeholder)}
      disabled={locked}
      onChange={(e) => onInput(e.target.value)}
      onCompositionStart={() => imeSubmitRef.current.startComposition()}
      onCompositionEnd={(e) => {
        imeSubmitRef.current.endComposition();
        onInput(e.currentTarget.value);
      }}
      onPaste={onPaste}
      onKeyDown={(e) => {
        if (e.key === "Escape" && cmdOpen) { setInput(""); resetTaHeight(); return; }
        if (!imeSubmitRef.current.shouldSubmitKey({
          key: e.key,
          shiftKey: e.shiftKey,
          isComposing: e.nativeEvent.isComposing,
          keyCode: e.nativeEvent.keyCode,
        })) return;
        e.preventDefault();
        if (cmdOpen && cmdToken) { pickCommand(cmdMatches[0].slash); return; }
        if (cmdToken === "") return;
        send(e.currentTarget.value);
      }}
    />
  );

  const sendControl = (
    <button className={sendClass}
      onPointerDown={() => {
        if (imeSubmitRef.current.shouldCommitBeforeButtonSubmit()) taRef.current?.blur();
      }}
      onClick={requestButtonSend}
      disabled={disabled}
      aria-label={interruptSettling && stopping ? "正在停止" : stopping ? "停止" : "发送"}>
      <Icon name={sendIcon} size={19} />
    </button>
  );

  return (
    <div className={workSurface ? "composer work-composer" : "composer"}>
      <div className={workSurface ? "composer-in work-composer-in" : "composer-in"}>
        {cmdOpen && (
          <div className="cmd-pop" role="listbox" aria-label="命令"
            data-lock-horizontal-swipe="true">
            {cmdMatches.map((c) => (
              <button key={c.slash} type="button" className="cmd" onClick={() => pickCommand(c.slash)}>
                <span className="cmd-ic"><Icon name={c.ic} size={17} /></span>
                <span className="cmd-tx">
                  <span className="cmd-nm"><span className="slash">/{c.slash}</span></span>
                  <span className="cmd-ds">{c.name} — {c.ds}</span>
                </span>
                <span className="cmd-kbd">↵</span>
              </button>
            ))}
          </div>
        )}
        {notice && <div className="composer-notice">{notice}</div>}
        {p.queue.length > 0 && (
          <div className="queued show">
            {p.queue.map((m, i) => (
              <span className="qchip" key={i}>
                <span className="qbadge">排队</span>
                <span className="qt">{m.prompt || (m.images?.length ? "图片" : "附件")}</span>
                <span className="qx" onClick={() => p.onDequeue(i)}><Icon name="close" size={12} /></span>
              </span>
            ))}
          </div>
        )}

        {hasAttachments && (
          <div className="attach show">
            <PendingImageAttachments key={p.draftKey} images={images}
              onRemove={(index) => setImages((previous) =>
                previous.filter((_, candidate) => candidate !== index))} />
            {files.map((f, i) => (
              <span key={i} className="attach-file">
                <Icon name="read" size={14} />
                <span className="attach-fn">{f.filename}</span>
                <button className="attach-x" onClick={() => setFiles(files.filter((_, j) => j !== i))} aria-label="移除"><Icon name="close" size={12} /></button>
              </span>
            ))}
          </div>
        )}

        {busy && (
          <div className="runbar show">
            <div className="seg">
              <button className={p.sendMode === "interrupt" ? "on" : ""} onClick={() => p.setSendMode("interrupt")}>
                <Icon name="bolt" size={14} />打断并发送
              </button>
              <button className={p.sendMode === "queue" ? "on" : ""} onClick={() => p.setSendMode("queue")}>
                <Icon name="queue" size={14} />排队
              </button>
            </div>
          </div>
        )}

        <input ref={photoRef} type="file" accept="image/*" multiple hidden
          aria-label="添加照片"
          onChange={(e) => { void onPickFiles(e.target.files); e.target.value = ""; }} />
        <input ref={fileRef} type="file" multiple hidden aria-label="添加文件"
          onChange={(e) => { void onPickFiles(e.target.files); e.target.value = ""; }} />

        {workSurface ? (
          <div className="work-compose-card">
            <div className="work-compose-caption">
              <span className="work-compose-icon"><Icon name="work" size={17} /></span>
              <span><b>继续这项工作</b><small>围绕当前资料生成、整理或修改 Artifacts</small></span>
              <span className="work-private"><Icon name="lock" size={12} />私有工作区</span>
            </div>
            <div className="work-inrow">
              {inputControl("描述接下来要完成的工作…")}
              {sendControl}
            </div>
            <div className="work-compose-foot">
              <button type="button" className="work-compose-tool"
                onClick={() => fileRef.current?.click()} disabled={locked || importing}
                aria-label="添加资料" title="添加资料">
                <Icon name="plus" size={15} /><span>添加资料</span>
              </button>
              {!!p.workArtifactCount && (
                <button type="button" className="work-compose-tool"
                  onClick={p.onOpenArtifacts}
                  aria-label={`Artifacts · ${p.workArtifactCount} 个文件`}
                  title="查看 Artifacts">
                  <Icon name="read" size={15} />
                  <span>Artifacts · {p.workArtifactCount}</span>
                </button>
              )}
              <details className="work-settings" ref={workSettingsRef}>
                <summary><Icon name="plan" size={15} /><span>工作设置</span></summary>
                <div className="work-settings-pop" ref={ctxWrapRef}>
                  <button type="button" onClick={() => setSheetKind("models")}
                    disabled={locked}>
                    <span>模型</span><b>{model?.name ?? "读取中"}</b>
                  </button>
                  <button type="button" onClick={() => setSheetKind("efforts")}
                    disabled={locked}>
                    <span>思考强度</span><b>{effort?.name ?? "读取中"}</b>
                  </button>
                  <button type="button" onClick={() => { p.onContext(); setCtxOpen((o) => !o); }}>
                    <span>会话上下文</span><b>{p.contextReport?.available === false
                      ? "暂不可用"
                      : workContext ? `${workContext.sessionPercentage.toFixed(0)}%` : "查看"}</b>
                  </button>
                  {ctxOpen && (
                    <div className="ctx-pop work-ctx-pop" role="dialog" aria-label="Work 上下文占用">
                      {p.contextReport?.available === false ? (
                        <div className="ctx-pop-loading">尚未收到 Codex 的 tokenUsage；完成一次模型回合后更新。</div>
                      ) : p.contextReport && workContext ? (
                        <>
                          <div className="ctx-pop-row"><span>{workContext.hasBreakdown ? "会话新增上下文" : "上下文窗口"}</span>
                            <span className="ctx-pop-nums">{workContext.sessionTokens.toLocaleString()} / {p.contextReport.max_tokens.toLocaleString()} ({workContext.sessionPercentage.toFixed(0)}%)</span>
                          </div>
                          <div className="ctx-pop-bar"><i style={{ width: `${Math.min(workContext.sessionPercentage, 100)}%` }} /></div>
                          {workContext.hasBreakdown && (
                            <div className="work-ctx-details">
                              <div className="ctx-pop-row"><span>真实总占用</span>
                                <span className="ctx-pop-nums">{workContext.totalTokens.toLocaleString()} / {p.contextReport.max_tokens.toLocaleString()} ({workContext.totalPercentage.toFixed(0)}%)</span>
                              </div>
                              <div className="ctx-pop-row"><span>Work 启动基线</span>
                                <span className="ctx-pop-nums">{workContext.fixedTokens.toLocaleString()}</span>
                              </div>
                            </div>
                          )}
                          <div className="ctx-pop-foot">{p.contextReport.model || ""}</div>
                        </>
                      ) : <div className="ctx-pop-loading">读取上下文占用…</div>}
                    </div>
                  )}
                </div>
              </details>
              <span className={`work-run-state ${p.state}`}><i />{
                p.state === "idle" ? "可以继续" : p.state === "running" ? "正在工作" : stateZh[p.state]
              }</span>
            </div>
          </div>
        ) : (<>
          <div className="inrow">
            <button className="cmdbtn" onClick={() => photoRef.current?.click()}
              aria-label="添加照片" title="添加照片"
              disabled={locked || importing}><Icon name="plus" size={19} /></button>
            {inputControl("发消息…  输入 / 唤起命令")}
            {sendControl}
          </div>
          <div className="hint">
          <button
            type="button"
            className={"hint-mode" + modeCls + (hintBusy ? " busy" : "")}
            onClick={() => setSheetKind("perms")}
            disabled={locked}
            title={deferredClaudeControls
              ? `${externalClaudeOwner} 当前权限模式未公开`
              : "点击切换权限模式"}
          >
            {deferredClaudeControls
              ? externalClaudeOwner
              : (perm ? perm.short : "Mode loading")} · {stateZh[p.state]}
            {!deferredClaudeControls && <span className="hint-mode-ch">▾</span>}
          </button>
          <span className="hint-kbds"><kbd>Enter</kbd> 发送 · <kbd>Shift+Tab</kbd> 切模式 · <kbd>/</kbd> 命令</span>
          <div className="hint-right" ref={ctxWrapRef}>
            {deferredClaudeControls && (
              <span className="hint-control-scope"
                title={`以下是 Remote 接管后的配置，不是${externalClaudeOwner}当前状态`}>
                接管后
              </span>
            )}
            <button className="hint-ctl" onClick={() => setSheetKind("models")}
              disabled={locked}
              title={deferredClaudeControls
                ? `Remote 接管后模型：${model?.name ?? "读取中"}；不是${externalClaudeOwner}当前模型`
                : "选择模型"}>{model?.name ?? "模型读取中"}</button>
            <button className="hint-ctl" onClick={() => setSheetKind("efforts")}
              disabled={locked}
              title={deferredClaudeControls
                ? `Remote 接管后思考强度：${effort?.name ?? "读取中"}；不是${externalClaudeOwner}当前强度`
                : "思考强度"}>{effort?.name ?? "强度读取中"}</button>
            {p.engine === "codex" && p.collaborationMode === "plan" && (
              <button
                className="hint-ctl collaboration-chip plan"
                onClick={() => p.onSetCollaborationMode("default")}
                disabled={locked}
                title="Plan 模式已开启；点击恢复默认模式（下条消息生效）"
                aria-pressed="true"
              >Plan</button>
            )}
            {p.engine === "codex" && (
              <button
                className={"hint-ctl fast-chip" + (p.fast ? " on" : "")}
                onClick={() => p.onSetServiceTier?.("toggle")}
                disabled={locked}
                title="Fast 服务档位:快 / 标准(下条消息生效)"
              >{p.fast == null ? "档位读取中" : p.fast ? "⚡ 快" : "标准"}</button>
            )}
            <button
              className={"hint-ring" + (contextAvailable ? "" : " unavailable")}
              aria-label="上下文占用"
              title="上下文占用"
              onClick={() => { p.onContext(); setCtxOpen((o) => !o); }}
            >
              <svg viewBox="0 0 36 36" width="20" height="20" aria-hidden="true">
                <circle className="hr-track" cx="18" cy="18" r="15" />
                <circle
                  className="hr-fill"
                  cx="18" cy="18" r="15"
                  strokeDasharray="94.25"
                  strokeDashoffset={94.25 * (1 - Math.min(
                    contextAvailable ? p.contextReport?.percentage ?? 0 : 0, 100) / 100)}
                  transform="rotate(-90 18 18)"
                />
              </svg>
            </button>
            {ctxOpen && (
              <div className="ctx-pop" role="dialog" aria-label="上下文占用">
                {p.contextError ? (
                  <div className="ctx-pop-loading" role="alert">{p.contextError}</div>
                ) : p.contextReport?.available === false ? (
                  <div className="ctx-pop-loading">尚未收到 Codex 的 tokenUsage；完成一次模型回合后会自动更新。上下文仍由 Codex 原生管理。</div>
                ) : p.contextReport ? (
                  <>
                    <div className="ctx-pop-row">
                      <span>上下文窗口</span>
                      <span className="ctx-pop-nums">
                        {p.contextReport.total_tokens.toLocaleString()} / {p.contextReport.max_tokens.toLocaleString()} ({p.contextReport.percentage.toFixed(0)}%)
                      </span>
                    </div>
                    <div className="ctx-pop-bar"><i style={{ width: `${Math.min(p.contextReport.percentage, 100)}%` }} /></div>
                    {p.contextReport.categories.length > 0 && (
                      <div className="ctx-pop-cats">
                        {p.contextReport.categories.map((c, i) => (
                          <div className="ctx-pop-cat" key={i}>
                            <span className="ctx-cat-dot" style={{ background: c.color }} />
                            <span className="ctx-pop-cat-name">{c.name}</span>
                            <span className="ctx-pop-cat-tok">{c.tokens.toLocaleString()}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {p.contextReport.is_auto_compact_enabled && <div className="ctx-pop-auto">autocompact 已启用</div>}
                    <div className="ctx-pop-foot">{p.contextReport.model || ""}</div>
                  </>
                ) : (
                  <div className="ctx-pop-loading">读取上下文占用…</div>
                )}
              </div>
            )}
          </div>
          </div>
        </>)}
      </div>

      <CommandSheet
        open={sheetKind !== null}
        kind={sheetKind ?? "models"}
        engine={p.engine}
        catalog={p.catalog}
        onClose={() => setSheetKind(null)}
        currentModel={p.model}
        onPickModel={(m) => { p.onSetModel(m); setSheetKind(null); }}
        currentEffort={p.effort}
        onPickEffort={(ef) => { p.onSetEffort(ef); setSheetKind(null); }}
        currentPerm={p.perm}
        onPickPerm={(perm) => { p.onSetPerm(perm); setSheetKind(null); }}
      />

      {dragOver && (
        <div className="drop-overlay" aria-hidden="true">
          <div className="drop-card">
            <span className="dc-ic"><Icon name="plus" size={36} /></span>
            <div className="dc-tx">拖拽文件到此处发送</div>
            <div className="dc-sub">图片直接进对话 · 其他文件写到 /tmp 用 @ 引用</div>
          </div>
        </div>
      )}
    </div>
  );
}
