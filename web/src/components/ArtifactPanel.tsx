import { useCallback, useEffect, useMemo, useRef, useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Artifact, PreviewAssetState } from "../reducer";
import { Icon } from "../icons";
import { PanelTabs } from "./PanelTabs";
import { GIT_DIFF_PAGE_LINES, pageGitDiff, type GitDiffSection } from "../diff";
import { classifyPreviewTarget } from "../preview-path";
import { parseLocalFileTarget } from "../file-link";
import { buildSandboxDocument } from "../html-preview";
import { clampPanelWidth } from "../responsive-layout";

const EMPTY_GIT_DIFF_SECTIONS: GitDiffSection[] = [];
const MAX_PREVIEW_ASSETS = 12;
const SOURCE_PAGE_LINES = 500;
const PANEL_WIDTH_KEY = "cc_remote_artifact_panel_width";
const URL_ATTRIBUTES = new Set(["src", "href", "xlink:href", "poster", "action", "formaction"]);
const UNSAFE_CSS = /(?:url\s*\(|@import|expression\s*\()/i;

function HtmlArtifactPreview({ content }: { content: string }) {
  const [document, setDocument] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const prepare = async () => {
      try {
        const { default: DOMPurify } = await import("dompurify");
        const clean = DOMPurify.sanitize(content, {
          FORBID_TAGS: ["script", "iframe", "object", "embed", "form", "base", "meta", "link"],
          FORBID_ATTR: ["srcset", "action", "formaction"],
        });
        const parsed = new DOMParser().parseFromString(clean, "text/html");
        for (const element of parsed.body.querySelectorAll("*")) {
          for (const attribute of Array.from(element.attributes)) {
            const name = attribute.name.toLowerCase();
            const value = attribute.value.trim();
            if (name.startsWith("on")) {
              element.removeAttribute(attribute.name);
            } else if (URL_ATTRIBUTES.has(name)) {
              const allowedAnchor = name === "href" && value.startsWith("#");
              const allowedImage = name === "src"
                && /^data:image\/(?:png|jpeg|gif|webp|avif);base64,/i.test(value);
              if (!allowedAnchor && !allowedImage) element.removeAttribute(attribute.name);
            } else if (name === "style" && UNSAFE_CSS.test(value)) {
              element.removeAttribute(attribute.name);
            }
          }
        }
        for (const style of parsed.body.querySelectorAll("style")) {
          if (UNSAFE_CSS.test(style.textContent || "")) style.remove();
        }
        if (cancelled) return;
        setDocument(buildSandboxDocument(parsed.body.innerHTML));
        setError(null);
      } catch {
        if (cancelled) return;
        setDocument(null);
        setError("HTML 安全处理失败");
      }
    };
    void prepare();
    return () => { cancelled = true; };
  }, [content]);

  if (error) return <div className="preview-error"><Icon name="read" size={18} />{error}</div>;
  if (!document) return <div className="diff-empty"><span className="thinking"><span/><span/><span/></span> 正在准备 HTML…</div>;
  return <iframe className="artifact-html-preview" title="HTML 预览"
    sandbox="" referrerPolicy="no-referrer" srcDoc={document} />;
}

function BinaryArtifactPreview({ data, mediaType, kind, title }: {
  data?: string;
  mediaType?: string;
  kind: "image" | "pdf";
  title: string;
}) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!data || !mediaType) {
      setObjectUrl(null);
      setError("预览数据不完整");
      return;
    }
    try {
      const binary = window.atob(data);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      const url = URL.createObjectURL(new Blob([bytes], { type: mediaType }));
      setObjectUrl(url);
      setError(null);
      return () => URL.revokeObjectURL(url);
    } catch {
      setObjectUrl(null);
      setError("预览数据损坏");
    }
  }, [data, mediaType]);

  if (error) return <div className="preview-error"><Icon name="read" size={18} />{error}</div>;
  if (!objectUrl) return <div className="diff-empty"><span className="thinking"><span/><span/><span/></span> 正在准备预览…</div>;
  if (kind === "image") {
    return <div className="artifact-image-stage"><img src={objectUrl} alt={title} /></div>;
  }
  return <iframe className="artifact-pdf-preview" src={objectUrl} title={`${title} PDF 预览`} />;
}

function SourceFile({ content, targetLine, artifactKey }: {
  content: string;
  targetLine?: number;
  artifactKey: string;
}) {
  const lines = useMemo(() => content.split("\n"), [content]);
  const focusLine = targetLine && targetLine <= lines.length ? targetLine : undefined;
  const initialPage = Math.min(
    Math.max(0, Math.floor(((targetLine || 1) - 1) / SOURCE_PAGE_LINES)),
    Math.max(0, Math.ceil(lines.length / SOURCE_PAGE_LINES) - 1),
  );
  const [pageState, setPageState] = useState({ key: artifactKey, page: initialPage });
  const page = pageState.key === artifactKey ? pageState.page : initialPage;
  const pageCount = Math.max(1, Math.ceil(lines.length / SOURCE_PAGE_LINES));
  const start = page * SOURCE_PAGE_LINES;
  const visible = lines.slice(start, start + SOURCE_PAGE_LINES);
  const targetRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!focusLine || Math.floor((focusLine - 1) / SOURCE_PAGE_LINES) !== page) return;
    const frame = window.requestAnimationFrame(() => {
      targetRef.current?.scrollIntoView({ block: "center" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [artifactKey, focusLine, page]);

  return <>
    {pageCount > 1 && <nav className="source-page-nav" aria-label="源文件分页">
      <button type="button" disabled={page === 0}
        onClick={() => setPageState({ key: artifactKey, page: page - 1 })}>上一页</button>
      <span>{start + 1}–{Math.min(lines.length, start + SOURCE_PAGE_LINES)} / {lines.length} 行</span>
      <button type="button" disabled={page + 1 >= pageCount}
        onClick={() => setPageState({ key: artifactKey, page: page + 1 })}>下一页</button>
    </nav>}
    <div className="source-file">
      {visible.map((text, index) => {
        const line = start + index + 1;
        const focused = line === focusLine;
        return <div key={line} ref={focused ? targetRef : undefined}
          className={"source-line" + (focused ? " focused" : "")}>
          <span className="source-line-no">{line}</span>
          <code>{text || " "}</code>
        </div>;
      })}
    </div>
  </>;
}

function PreviewImage({ markdownPath, src, alt, title, asset, requestAsset }: {
  markdownPath: string;
  src: string;
  alt?: string;
  title?: string;
  asset?: PreviewAssetState;
  requestAsset: (path: string) => boolean;
}) {
  const target = classifyPreviewTarget(markdownPath, src);
  const [blocked, setBlocked] = useState(false);

  useEffect(() => {
    if (target.kind !== "local" || asset?.data || asset?.error) return;
    setBlocked(!requestAsset(target.value));
  }, [asset?.data, asset?.error, requestAsset, target.kind, target.value]);

  if (target.kind === "external") {
    return <img src={target.value} alt={alt || ""} title={title}
      loading="lazy" referrerPolicy="no-referrer" />;
  }
  if (target.kind !== "local") {
    return <span className="preview-image-error" title={src}>图片路径不可用：{alt || src}</span>;
  }
  if (asset?.data && asset.mediaType) {
    return <img src={`data:${asset.mediaType};base64,${asset.data}`}
      alt={alt || ""} title={title} loading="lazy" />;
  }
  if (asset?.error) {
    return <span className="preview-image-error" title={asset.error}>图片不可用：{alt || src}</span>;
  }
  if (blocked) {
    return <span className="preview-image-error">本页本地图片超过 {MAX_PREVIEW_ASSETS} 张，已停止加载</span>;
  }
  return <span className="preview-image-loading"><span className="thinking"><span/><span/><span/></span> {alt || "正在加载图片"}</span>;
}

export function ArtifactPanel({ artifact, active, hasBtw, onTab, onClose,
  onRefresh, onOpenFile, onLoadPreviewAsset, onSaveMarkdown, onDirtyChange }: {
  artifact: Artifact;
  active: "diff" | "btw";
  hasBtw: boolean;
  onTab: (v: "diff" | "btw") => void;
  onClose: () => void;
  onRefresh?: (path: string, line?: number) => void;
  onOpenFile?: (path: string, line?: number) => void;
  onLoadPreviewAsset?: (path: string, previewId: string) => boolean;
  onSaveMarkdown?: (path: string, content: string, expectedSize: number,
    expectedMtimeNs: string, expectedRevision: string) => string | null;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const resizeRef = useRef<{
    pointerId: number;
    startX: number;
    startWidth: number;
  } | null>(null);
  const artifactKey = `${artifact.sid || ""}:${artifact.file}:${artifact.requestId || ""}`;
  const [pageState, setPageState] = useState({ key: artifactKey, page: 0 });
  const [modeState, setModeState] = useState<{ key: string; mode: "preview" | "source" }>({
    key: artifactKey, mode: "preview",
  });
  const [editorState, setEditorState] = useState({
    key: artifactKey,
    draft: artifact.content || "",
    baseline: artifact.content || "",
  });
  const requestedAssets = useRef<{
    key: string;
    paths: Set<string>;
    queued: string[];
    active?: string;
  }>({
    key: artifactKey, paths: new Set(), queued: [],
  });
  if (requestedAssets.current.key !== artifactKey) {
    requestedAssets.current = {
      key: artifactKey, paths: new Set(), queued: [],
    };
  }

  const requestedPage = pageState.key === artifactKey ? pageState.page : 0;
  const mode = modeState.key === artifactKey ? modeState.mode : "preview";
  const editor = editorState.key === artifactKey ? editorState : {
    key: artifactKey,
    draft: artifact.content || "",
    baseline: artifact.content || "",
  };
  const dirty = artifact.kind === "md" && editor.draft !== editor.baseline;
  const sections = artifact.kind === "gitdiff"
    ? (artifact.sections || EMPTY_GIT_DIFF_SECTIONS) : EMPTY_GIT_DIFF_SECTIONS;
  const page = useMemo(() => pageGitDiff(sections, requestedPage), [sections, requestedPage]);
  const showPage = (nextPage: number) => setPageState({ key: artifactKey, page: nextPage });
  const loading = !!artifact.loading;
  const empty = artifact.kind === "gitdiff" && !loading && sections.length === 0;

  useEffect(() => {
    const incoming = artifact.content || "";
    setEditorState((current) => {
      if (current.key !== artifactKey) {
        return { key: artifactKey, draft: incoming, baseline: incoming };
      }
      if (artifact.saveStatus === "saved" || current.draft === current.baseline) {
        if (current.draft === incoming && current.baseline === incoming) return current;
        return { key: artifactKey, draft: incoming, baseline: incoming };
      }
      return current;
    });
  }, [artifact.content, artifact.saveStatus, artifactKey]);

  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  useEffect(() => {
    if (artifact.kind !== "md" || mode !== "source" || loading) return;
    const frame = window.requestAnimationFrame(() => editorRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [artifact.kind, loading, mode]);

  const canSave = artifact.kind === "md" && !loading && !artifact.error
    && !artifact.truncated && typeof artifact.size === "number"
    && typeof artifact.mtimeNs === "string" && !!artifact.revision
    && !!onSaveMarkdown;
  const saveDraft = useCallback(() => {
    if (!canSave || !dirty || artifact.saving || !artifact.revision) return;
    onSaveMarkdown?.(
      artifact.file,
      editor.draft,
      artifact.size!,
      artifact.mtimeNs!,
      artifact.revision,
    );
  }, [artifact.file, artifact.mtimeNs, artifact.revision, artifact.size,
    artifact.saving, canSave, dirty, editor.draft, onSaveMarkdown]);

  const confirmDiscard = useCallback(() => (
    !dirty || window.confirm("Markdown 有未保存的修改，确定放弃吗？")
  ), [dirty]);

  const leavePanel = useCallback(() => {
    if (!confirmDiscard()) return;
    onDirtyChange?.(false);
    onClose();
  }, [confirmDiscard, onClose, onDirtyChange]);

  const switchPanelTab = useCallback((next: "diff" | "btw") => {
    if (next !== active && !confirmDiscard()) return;
    if (next !== active) onDirtyChange?.(false);
    onTab(next);
  }, [active, confirmDiscard, onDirtyChange, onTab]);

  const handlePanelKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (artifact.kind !== "md" || !(event.metaKey || event.ctrlKey)
        || event.key.toLowerCase() !== "s") return;
    event.preventDefault();
    saveDraft();
  };

  const applyPanelWidth = useCallback((requestedWidth: number, persist = false) => {
    const width = clampPanelWidth(requestedWidth, window.innerWidth);
    document.documentElement.style.setProperty("--panel-w", `${width}px`);
    if (persist) localStorage.setItem(PANEL_WIDTH_KEY, String(width));
    return width;
  }, []);

  useEffect(() => {
    if (!window.matchMedia("(min-width: 981px)").matches) return;
    const saved = Number.parseFloat(localStorage.getItem(PANEL_WIDTH_KEY) || "");
    if (Number.isFinite(saved)) applyPanelWidth(saved);
    const fitPanel = () => {
      if (!window.matchMedia("(min-width: 981px)").matches) return;
      const current = panelRef.current?.getBoundingClientRect().width;
      if (current) applyPanelWidth(current);
    };
    window.addEventListener("resize", fitPanel);
    return () => {
      window.removeEventListener("resize", fitPanel);
      document.documentElement.classList.remove("panel-resizing");
    };
  }, [applyPanelWidth]);

  const startResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!window.matchMedia("(min-width: 981px)").matches || !panelRef.current) return;
    resizeRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: panelRef.current.getBoundingClientRect().width,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    document.documentElement.classList.add("panel-resizing");
    event.preventDefault();
  };
  const moveResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const resize = resizeRef.current;
    if (!resize || resize.pointerId !== event.pointerId) return;
    applyPanelWidth(resize.startWidth + resize.startX - event.clientX);
  };
  const finishResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const resize = resizeRef.current;
    if (!resize || resize.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    resizeRef.current = null;
    document.documentElement.classList.remove("panel-resizing");
    const width = panelRef.current?.getBoundingClientRect().width;
    if (width) applyPanelWidth(width, true);
  };
  const resizeWithKeyboard = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const width = panelRef.current?.getBoundingClientRect().width;
    if (!width) return;
    applyPanelWidth(width + (event.key === "ArrowLeft" ? 24 : -24), true);
    event.preventDefault();
  };

  const sendNextAsset = useCallback(() => {
    const current = requestedAssets.current;
    if (current.key !== artifactKey || current.active
        || !artifact.requestId || !onLoadPreviewAsset) return;
    while (current.queued.length) {
      const path = current.queued.shift()!;
      if (onLoadPreviewAsset(path, artifact.requestId)) {
        current.active = path;
        return;
      }
      current.paths.delete(path);
    }
  }, [artifact.requestId, artifactKey, onLoadPreviewAsset]);

  useEffect(() => {
    const current = requestedAssets.current;
    if (current.key !== artifactKey) return;
    if (current.active && artifact.assets?.[current.active]) {
      current.active = undefined;
    }
    sendNextAsset();
  }, [artifact.assets, artifactKey, sendNextAsset]);

  const requestAsset = useCallback((path: string): boolean => {
    const current = requestedAssets.current;
    if (current.key !== artifactKey || !artifact.requestId
        || !onLoadPreviewAsset) return false;
    if (current.paths.has(path)) return true;
    if (current.paths.size >= MAX_PREVIEW_ASSETS) return false;
    current.paths.add(path);
    current.queued.push(path);
    sendNextAsset();
    return current.paths.has(path);
  }, [artifact.requestId, artifactKey, onLoadPreviewAsset, sendNextAsset]);

  const markdownComponents = useMemo<Components>(() => ({
    img: ({ src, alt, title }) => {
      const source = typeof src === "string" ? src : "";
      const target = classifyPreviewTarget(artifact.file, source);
      const asset = target.kind === "local" ? artifact.assets?.[target.value] : undefined;
      return <PreviewImage markdownPath={artifact.file} src={source} alt={alt}
        title={title} asset={asset} requestAsset={requestAsset} />;
    },
    a: ({ href, children, title }) => {
      const target = classifyPreviewTarget(artifact.file, href || "");
      if (target.kind === "external") {
        return <a href={target.value} target="_blank" rel="noopener noreferrer"
          title={title}>{children}</a>;
      }
      if (target.kind === "anchor") return <a href={target.value} title={title}>{children}</a>;
      if (target.kind === "local" && onOpenFile) {
        const source = parseLocalFileTarget(href || "");
        return <a href="#" title={target.value} onClick={(event) => {
          event.preventDefault();
          onOpenFile(target.value, source?.line);
        }}>{children}</a>;
      }
      return <span className="preview-link-disabled" title="该相对链接不会离开当前工作目录">{children}</span>;
    },
  }), [artifact.assets, artifact.file, onOpenFile, requestAsset]);

  const title = artifact.file.split("/").pop()
    || (["md", "file", "html", "image", "pdf"].includes(artifact.kind) ? "文件预览" : "改动");
  const renderedArtifact = ["image", "pdf"].includes(artifact.kind)
    || (artifact.kind === "html" && mode === "preview");

  return (
    <div className="artifact-panel" ref={panelRef} data-lock-horizontal-swipe="true"
      onKeyDown={handlePanelKeyDown}>
      <button type="button" className="panel-resizer"
        aria-label="调整文件面板宽度" title="左右拖动调整面板宽度"
        onPointerDown={startResize} onPointerMove={moveResize}
        onPointerUp={finishResize} onPointerCancel={finishResize}
        onKeyDown={resizeWithKeyboard} />
      <div className="artifact-head">
        {hasBtw ? <PanelTabs active={active} artifactKind={artifact.kind} onTab={switchPanelTab} />
          : <span className="artifact-title">{title}</span>}
        <span className="artifact-path" title={artifact.file}>{artifact.file || "所有改动"}</span>
        {["md", "html"].includes(artifact.kind) && !loading && !artifact.error && <div
          className="preview-modes" role="group"
          aria-label={`${artifact.kind === "html" ? "HTML" : "Markdown"} 显示模式`}>
          <button className={mode === "preview" ? "on" : ""}
            onClick={() => setModeState({ key: artifactKey, mode: "preview" })}>预览</button>
          <button className={mode === "source" ? "on" : ""}
            onClick={() => setModeState({ key: artifactKey, mode: "source" })}>源码</button>
        </div>}
        {artifact.kind === "md" && !loading && !artifact.error && <button
          type="button" className="markdown-save"
          disabled={!dirty || artifact.saving || !canSave}
          onClick={saveDraft}
          title={artifact.truncated ? "截断的文件不可编辑" : "保存 Markdown（Ctrl/⌘+S）"}>
          <Icon name={artifact.saving ? "refresh" : "check"} size={15} />
          {artifact.saving ? "保存中" : "保存"}
        </button>}
        {artifact.kind === "md" && artifact.saveStatus === "saved" && !dirty
          && <span className="markdown-save-state ok">已保存</span>}
        {artifact.convertedFrom && <span className="artifact-converted"
          title="由 nono 本机沙箱临时转换，VPS 不保存文件">
          {artifact.convertedFrom.toUpperCase()} → PDF
        </span>}
        {["md", "file", "html", "image", "pdf"].includes(artifact.kind) && <button className="iconbtn"
          onClick={() => onRefresh?.(artifact.file, artifact.line)}
          aria-label="刷新文件" title="重新读取文件"><Icon name="refresh" size={17} /></button>}
        <button className="iconbtn" onClick={leavePanel} aria-label="收起"><Icon name="chevrons-right" /></button>
      </div>
      <div className={`artifact-body${renderedArtifact ? " rendered-artifact-body" : ""}`}>
        {loading ? (
          <div className="diff-empty"><span className="thinking"><span/><span/><span/></span> {["md", "file", "html", "image", "pdf"].includes(artifact.kind) ? "正在读取文件…" : "正在读取 diff…"}</div>
        ) : artifact.error ? (
          <div className="preview-error"><Icon name="read" size={18} />{artifact.error}</div>
        ) : artifact.kind === "gitdiff" ? (
          empty ? (
            <div className="diff-empty">没有未提交的改动。</div>
          ) : (
            <>
              {page.totalLines > GIT_DIFF_PAGE_LINES && (
                <nav className="diff-page-nav" aria-label="Diff 分页">
                  <button type="button" disabled={page.page === 0}
                    onClick={() => showPage(page.page - 1)}>上一页</button>
                  <span>{page.startLine + 1}–{page.endLine} / {page.totalLines} 行</span>
                  <button type="button" disabled={page.page + 1 >= page.pageCount}
                    onClick={() => showPage(page.page + 1)}>下一页</button>
                </nav>
              )}
              <div className="diff-table">
                {page.sections.map((s, si) => (
                  <div className="diff-file" key={si}>
                    <div className="diff-file-h" title={s.file}>
                      <Icon name="edit" size={13} />
                      <span className="diff-file-nm">{s.file}</span>
                    </div>
                    {s.hunks.map((h, hi) => (
                      <div className="diff-hunk" key={hi}>
                        <div className="diff-hunk-h">{h.header}</div>
                        {h.lines.map((l, li) => (
                          <div className={"drow " + l.type} key={li}>
                            <span className="dno">{l.oldNo ?? ""}</span>
                            <span className="dno">{l.newNo ?? ""}</span>
                            <span className="dline">{l.text || " "}</span>
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </>
          )
        ) : artifact.kind === "diff" ? (
          <pre className="diff-pre">
            {artifact.diff?.map((l, i) => (
              <span key={i} className={"diff-" + l.type}>{(l.type === "add" ? "+" : l.type === "del" ? "−" : " ") + " " + l.text + "\n"}</span>
            ))}
          </pre>
        ) : artifact.kind === "html" ? (
          mode === "source"
            ? <SourceFile content={artifact.content || ""} artifactKey={artifactKey} />
            : <HtmlArtifactPreview content={artifact.content || ""} />
        ) : artifact.kind === "image" ? (
          <BinaryArtifactPreview data={artifact.data} mediaType={artifact.mediaType}
            kind="image" title={title} />
        ) : artifact.kind === "pdf" ? (
          <BinaryArtifactPreview data={artifact.data} mediaType={artifact.mediaType}
            kind="pdf" title={title} />
        ) : artifact.kind === "file" ? (
          <>
            {artifact.truncated && <div className="preview-truncated">文件共 {artifact.size?.toLocaleString()} 字节，仅预览前 512 KiB。</div>}
            <SourceFile content={artifact.content || ""} targetLine={artifact.line}
              artifactKey={artifactKey} />
          </>
        ) : artifact.kind === "md" ? (
          <>
            {artifact.truncated && <div className="preview-truncated">文件共 {artifact.size?.toLocaleString()} 字节，仅预览前 512 KiB。</div>}
            {artifact.saveError && <div className={"markdown-save-error " + (artifact.saveStatus || "error")}>
              {artifact.saveError}
            </div>}
            {mode === "source"
              ? <textarea ref={editorRef} className="markdown-editor"
                  aria-label="Markdown 源码编辑器" value={editor.draft}
                  readOnly={!canSave}
                  spellCheck={false}
                  onChange={(event) => setEditorState({
                    key: artifactKey,
                    draft: event.currentTarget.value,
                    baseline: editor.baseline,
                  })} />
              : <div className="prose markdown-preview"><ReactMarkdown
                  remarkPlugins={[remarkGfm]} components={markdownComponents}>{editor.draft}</ReactMarkdown></div>}
          </>
        ) : null}
      </div>
    </div>
  );
}
