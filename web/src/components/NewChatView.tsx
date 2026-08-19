// Empty-state "new chat" page: a centered composer (a la Claude app / Codex)
// with a working directory and optional attachments. Model, effort, and Codex
// modes use the local defaults; users can change them after the session starts.
import { useEffect, useRef, useState, type ClipboardEvent } from "react";
import { Icon } from "../icons";
import { attachmentBytes, pickFiles } from "../img";
import type { CodexPermissionMode, CodexServiceTier, CollaborationModeName, QueryImg, QueryFile, Space, WorkDashboard } from "../protocol";
import { ImeSubmitGuard } from "../ime-submit";
import { PendingImageAttachments } from "./PendingImageAttachments";

interface Props {
  cwd: string;
  space?: Space;
  engine?: "claude" | "codex";  // which backend this new chat will use
  autoFocus?: boolean;
  createError?: string | null;
  workDashboard?: WorkDashboard | null;
  selectedProjectId?: string | null;
  onSelectProject?: (projectId: string | null) => void;
  onManageWork?: () => void;
  onPickCwd: () => void;  // open the directory picker
  onSend: (prompt: string, images?: QueryImg[], files?: QueryFile[],
           collaborationMode?: CollaborationModeName,
           permissionMode?: CodexPermissionMode,
           serviceTier?: CodexServiceTier) => boolean;
}

export function NewChatView({ cwd, space = "code", engine = "claude", autoFocus = true,
  createError,
  workDashboard, selectedProjectId, onSelectProject, onManageWork, onPickCwd,
  onSend }: Props) {
  const [text, setText] = useState("");
  const [images, setImages] = useState<QueryImg[]>([]);
  const [files, setFiles] = useState<QueryFile[]>([]);
  const [importing, setImporting] = useState(false);
  const [creating, setCreating] = useState(false);
  const photoRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const imeSubmitRef = useRef(new ImeSubmitGuard());
  const buttonSendTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (createError) setCreating(false);
  }, [createError]);

  useEffect(() => () => {
    if (buttonSendTimerRef.current !== null) {
      window.clearTimeout(buttonSendTimerRef.current);
    }
  }, []);

  const hasAttachments = images.length > 0 || files.length > 0;
  const canSend = (text.trim().length > 0 || hasAttachments) && !creating && !importing;

  const onPick = async (fl: FileList | File[] | null) => {
    if (importing) return;
    setImporting(true);
    try {
      const batch = await pickFiles(
        fl, images.length + files.length, attachmentBytes(images, files));
      if (batch.images.length) setImages((previous) => [...previous, ...batch.images]);
      if (batch.files.length) setFiles((previous) => [...previous, ...batch.files]);
      if (batch.errors.length) window.alert(batch.errors.join("；"));
    } finally {
      setImporting(false);
    }
  };

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const fs: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.kind === "file") { const f = it.getAsFile(); if (f) fs.push(f); }
    }
    if (fs.length) { e.preventDefault(); void onPick(fs); }
  };

  const send = (value = taRef.current?.value ?? text) => {
    const prompt = value.trim();
    if ((!prompt && !hasAttachments) || creating || importing) return;
    setCreating(true);
    const queued = onSend(
      prompt, images.length ? images : undefined, files.length ? files : undefined,
      engine === "codex" ? "default" : undefined,
      engine === "codex" ? (space === "work" ? "on-request" : "never") : undefined,
      engine === "codex" ? "default" : undefined);
    if (!queued) setCreating(false);
  };

  const requestButtonSend = () => {
    if (buttonSendTimerRef.current !== null) return;
    buttonSendTimerRef.current = window.setTimeout(() => {
      buttonSendTimerRef.current = null;
      send();
    }, 0);
  };

  return (
    <div className={"newchat " + (space === "work" ? "work-newchat" : "code-newchat")}>
      <div className="newchat-card">
        <div className="newchat-greet">{space === "work"
          ? "开始一项工作"
          : engine === "codex" ? "开始 Codex 新对话" : "开始新对话"}
          <span className={`newchat-engine ${engine}`}>{engine === "codex" ? "◇ Codex" : "✳ Claude"}</span>
        </div>
        {space === "work" ? (
          <>
            <div className="work-private-note"><Icon name="lock" size={14} />
              默认只访问这项工作的私有目录；需要其他资料时直接上传。
            </div>
            <div className="work-project-bar">
              <select value={selectedProjectId ?? ""}
                onChange={(event) => onSelectProject?.(event.target.value || null)}>
                <option value="">不归入项目</option>
                {(workDashboard?.projects ?? []).map((project) =>
                  <option key={project.project_id} value={project.project_id}>{project.name}</option>)}
              </select>
              <button type="button" onClick={onManageWork}><Icon name="folder" size={15} />管理项目与资料</button>
            </div>
            {workDashboard && <div className="work-overview">
              <span>{workDashboard.projects.length} 个项目</span>
              <span>{workDashboard.sources.length} 份资料</span>
              <span>{workDashboard.schedules.length} 个定时任务</span>
              <span>{workDashboard.plugins.length} 个工作模板</span>
            </div>}
          </>
        ) : (
          <button className="newchat-cwd" onClick={onPickCwd} title="更改工作目录" disabled={creating}>
            <Icon name="folder" size={16} />
            <span className="newchat-cwd-path">{cwd === "~" ? "~ · 主目录" : (cwd || "未指定目录")}</span>
            <Icon name="edit" size={13} />
          </button>
        )}

        {space === "work" && !text && !hasAttachments && (
          <div className="work-starters" aria-label="常用工作类型">
            {[
              ["read", "整理文档", "帮我整理这份资料，输出一份结构清晰的文档。"],
              ["plan", "分析表格", "分析我上传的表格，找出关键结论并生成图表。"],
              ["book", "建立资料库", "把我提供的资料整理成可持续补充的知识库。"],
              ["spark", "制作演示", "根据我提供的内容制作一份演示文稿。"],
            ].map(([icon, label, prompt]) => (
              <button key={label} type="button" onClick={() => {
                setText(prompt); window.setTimeout(() => taRef.current?.focus(), 0);
              }}><Icon name={icon} size={16} /><span>{label}</span></button>
            ))}
          </div>
        )}

        {hasAttachments && (
          <div className="attach show newchat-attach">
            <PendingImageAttachments images={images}
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

        <textarea className="newchat-input"
          placeholder={space === "work" ? "描述要完成的工作，或上传文档、表格、演示…" : "发条消息开始…"} ref={taRef}
          value={text} onChange={(e) => setText(e.target.value)} onPaste={onPaste}
          autoFocus={autoFocus} rows={3}
          disabled={creating || importing}
          onCompositionStart={() => imeSubmitRef.current.startComposition()}
          onCompositionEnd={(e) => {
            imeSubmitRef.current.endComposition();
            setText(e.currentTarget.value);
          }}
          onKeyDown={(e) => {
            if (!imeSubmitRef.current.shouldSubmitKey({
              key: e.key, shiftKey: e.shiftKey,
              isComposing: e.nativeEvent.isComposing, keyCode: e.nativeEvent.keyCode,
            })) return;
            e.preventDefault();
            send(e.currentTarget.value);
          }} />

        <div className="newchat-foot">
          <div className="newchat-ctls">
            <button type="button" className="cmdbtn"
              onClick={() => (space === "work"
                ? fileRef.current : photoRef.current)?.click()}
              aria-label={space === "work" ? "添加资料" : "添加照片"}
              title={space === "work" ? "添加资料" : "添加照片"}
              disabled={creating || importing}>
              <Icon name="plus" size={18} />
            </button>
            <input ref={photoRef} type="file" accept="image/*" multiple
              aria-label="添加照片" hidden
              onChange={(e) => { void onPick(e.target.files); e.target.value = ""; }} />
            <input ref={fileRef} type="file" multiple aria-label="添加文件" hidden
              onChange={(e) => { void onPick(e.target.files); e.target.value = ""; }} />
          </div>
          <div className="newchat-foot-right">
            <span className="newchat-hint">{createError
              ? `创建失败：${createError}`
              : importing ? "正在导入附件…" : creating ? "正在创建会话…" : "Enter 发送"}</span>
            <button className="newchat-send"
              onPointerDown={() => {
                if (imeSubmitRef.current.shouldCommitBeforeButtonSubmit()) taRef.current?.blur();
              }}
              onClick={requestButtonSend}
              disabled={!canSend}>
              <Icon name="send" size={16} />开始
            </button>
          </div>
        </div>
      </div>

    </div>
  );
}
