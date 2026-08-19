import { createContext, isValidElement, useContext, useEffect, useMemo, useRef,
  useState, type ComponentPropsWithoutRef, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { parseLocalFileTarget } from "../file-link";
import { Icon } from "../icons";
import {
  classifyMessageImageTarget,
  type InlineImageAsset,
} from "../inline-image-assets";

const CODEX_DIRECTIVE_LABELS: Record<string, string> = {
  "git-stage": "Git 变更已暂存",
  "git-commit": "Git 提交已创建",
  "git-create-branch": "Git 分支已创建",
  "git-push": "Git 分支已推送",
  "git-create-pr": "Pull Request 已创建",
  "created-thread": "Codex 任务已创建",
};

type MessagePart =
  | { kind: "markdown"; text: string }
  | { kind: "directive"; name: string; label: string };

function splitCodexDirectives(text: string): MessagePart[] {
  const parts: MessagePart[] = [];
  let markdown = "";
  let fence: "`" | "~" | null = null;
  const lines = text.split("\n");
  const flushMarkdown = () => {
    if (!markdown) return;
    parts.push({ kind: "markdown", text: markdown });
    markdown = "";
  };

  lines.forEach((line, index) => {
    const suffix = index < lines.length - 1 ? "\n" : "";
    const fenceMatch = line.match(/^\s*(`{3,}|~{3,})/);
    if (!fence) {
      const directive = line.match(
        /^::(git-stage|git-commit|git-create-branch|git-push|git-create-pr|created-thread)\{[^\n]*\}\s*$/,
      );
      if (directive) {
        flushMarkdown();
        const name = directive[1];
        parts.push({
          kind: "directive",
          name,
          label: CODEX_DIRECTIVE_LABELS[name],
        });
        return;
      }
    }
    markdown += line + suffix;
    if (!fence && fenceMatch) {
      fence = fenceMatch[1][0] as "`" | "~";
    } else if (fence && fenceMatch?.[1][0] === fence) {
      fence = null;
    }
  });
  flushMarkdown();
  return parts;
}

function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeText(node.props.children);
  return "";
}

function CopyableCodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const code = nodeText(children).replace(/\n$/, "");
  const copy = () => {
    void navigator.clipboard?.writeText(code).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  };
  return (
    <div className="message-code-block">
      <button type="button" className={"message-code-copy" + (copied ? " copied" : "")}
        onClick={copy} aria-label="复制代码" title={copied ? "已复制" : "复制代码"}>
        <Icon name={copied ? "check" : "copy"} size={13} />
        <span>{copied ? "已复制" : "复制"}</span>
      </button>
      <pre>{children}</pre>
    </div>
  );
}

interface MessageMarkdownContextValue {
  imageAssets?: Record<string, InlineImageAsset>;
  onLoadImage?: (path: string) => boolean;
  onOpenFile?: (path: string, line?: number) => void;
  onPreviewImage?: (src: string, alt: string) => void;
}

const MessageMarkdownContext = createContext<MessageMarkdownContextValue>({});
const externalImageDimensions = new Map<string, readonly [number, number]>();
const MAX_EXTERNAL_IMAGE_DIMENSIONS = 128;

function rememberExternalImageDimensions(
  src: string,
  dimensions: readonly [number, number],
): void {
  if (!/^https?:\/\//i.test(src)) return;
  externalImageDimensions.delete(src);
  externalImageDimensions.set(src, dimensions);
  while (externalImageDimensions.size > MAX_EXTERNAL_IMAGE_DIMENSIONS) {
    const oldest = externalImageDimensions.keys().next().value;
    if (typeof oldest !== "string") break;
    externalImageDimensions.delete(oldest);
  }
}

function MessageImage({ src, alt, title, asset, onLoadImage, onPreviewImage }: {
  src: string;
  alt?: string;
  title?: string;
  asset?: InlineImageAsset;
  onLoadImage?: (path: string) => boolean;
  onPreviewImage?: (src: string, alt: string) => void;
}) {
  const target = useMemo(() => classifyMessageImageTarget(src), [src]);
  const [blocked, setBlocked] = useState(false);
  const [naturalSize, setNaturalSize] = useState<{
    src: string;
    width: number;
    height: number;
  } | null>(null);

  useEffect(() => {
    setBlocked(false);
    if (target.kind !== "local" || asset || !onLoadImage) return;
    setBlocked(!onLoadImage(target.value));
  }, [asset, onLoadImage, target]);

  if (target.kind === "blocked") {
    return <span className="message-image-error">图片不可用</span>;
  }
  if (target.kind === "local" && asset?.status === "error") {
    return <span className="message-image-error">图片不可用</span>;
  }
  if (target.kind === "local" && (blocked || (!onLoadImage && !asset))) {
    return <span className="message-image-error">图片暂时无法加载</span>;
  }

  const resolved = target.kind === "external"
    ? target.value
    : asset?.status === "ready" && asset.data && asset.mediaType
      ? `data:${asset.mediaType};base64,${asset.data}`
      : null;
  if (!resolved) {
    return <span className="message-image-loading" role="status">
      <span className="thinking"><span/><span/><span/></span>
      {alt || "正在加载图片"}
    </span>;
  }

  const cachedSize = externalImageDimensions.get(resolved);
  const dimensions = asset?.width && asset.height
    ? [asset.width, asset.height] as const
    : naturalSize?.src === resolved
      ? [naturalSize.width, naturalSize.height] as const
      : cachedSize;
  const image = <img className="message-inline-image" src={resolved}
    alt={alt || ""} title={title} loading="lazy" decoding="async"
    width={dimensions?.[0]} height={dimensions?.[1]}
    referrerPolicy="no-referrer"
    onLoad={(event) => {
      const width = event.currentTarget.naturalWidth;
      const height = event.currentTarget.naturalHeight;
      if (width <= 0 || height <= 0) return;
      rememberExternalImageDimensions(resolved, [width, height]);
      if (dimensions?.[0] === width && dimensions[1] === height) return;
      setNaturalSize({ src: resolved, width, height });
    }} />;
  if (!onPreviewImage) return image;
  return <button type="button" className="message-image-trigger"
    aria-label={`预览图片${alt ? `：${alt}` : ""}`}
    onClick={() => onPreviewImage(resolved, alt || "图片预览")}>{image}</button>;
}

function MarkdownImage({ src, alt, title }: ComponentPropsWithoutRef<"img">) {
  const {
    imageAssets, onLoadImage, onPreviewImage,
  } = useContext(MessageMarkdownContext);
  const source = typeof src === "string" ? src : "";
  const target = classifyMessageImageTarget(source);
  const asset = target.kind === "local" ? imageAssets?.[target.value] : undefined;
  return <MessageImage src={source} alt={alt} title={title} asset={asset}
    onLoadImage={onLoadImage} onPreviewImage={onPreviewImage} />;
}

function MarkdownLink({
  href = "", children, title,
}: ComponentPropsWithoutRef<"a">) {
  const { onOpenFile } = useContext(MessageMarkdownContext);
  const file = parseLocalFileTarget(href);
  if (file && onOpenFile) {
    const location = file.line ? `${file.path}:${file.line}` : file.path;
    return <button type="button" className="message-file-link"
      title={`在 Remote 中打开 ${location}`}
      onClick={() => onOpenFile(file.path, file.line)}>{children}</button>;
  }
  if (/^https?:\/\//i.test(href) || /^mailto:/i.test(href)) {
    return <a href={href} target="_blank" rel="noopener noreferrer"
      title={title}>{children}</a>;
  }
  if (href.startsWith("#")) return <a href={href} title={title}>{children}</a>;
  return <span className="message-link-disabled"
    title="该链接无法在当前会话中打开">{children}</span>;
}

/** Module-stable renderer identities keep React from remounting decoded images
 * whenever a streaming block receives a new asset snapshot. */
const MESSAGE_MARKDOWN_COMPONENTS: Components = Object.freeze({
  pre: ({ children }: ComponentPropsWithoutRef<"pre">) =>
    <CopyableCodeBlock>{children}</CopyableCodeBlock>,
  img: MarkdownImage,
  a: MarkdownLink,
});
const MESSAGE_REMARK_PLUGINS = [remarkGfm];

// Streams markdown with a ~50ms throttle: re-parsing react-markdown on every
// token delta is wasteful, so we hold a "shown" buffer that catches up on a
// timer while streaming, and snaps to the full text when the block is done.
export function MessageBlock({ text, done, onOpenFile, imageAssets,
  onLoadImage, onPreviewImage }: {
  text: string;
  done: boolean;
  onOpenFile?: (path: string, line?: number) => void;
  imageAssets?: Record<string, InlineImageAsset>;
  onLoadImage?: (path: string) => boolean;
  onPreviewImage?: (src: string, alt: string) => void;
}) {
  const [shown, setShown] = useState(text);
  const latest = useRef(text);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    latest.current = text;
    if (done) {
      if (timer.current) { clearTimeout(timer.current); timer.current = null; }
      setShown(text);
      return;
    }
    if (timer.current) return; // a catch-up is already scheduled
    timer.current = setTimeout(() => {
      setShown(latest.current);
      timer.current = null;
    }, 50);
  }, [text, done]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const markdownContext = useMemo<MessageMarkdownContextValue>(() => ({
    imageAssets, onLoadImage, onOpenFile, onPreviewImage,
  }), [imageAssets, onLoadImage, onOpenFile, onPreviewImage]);
  const parts = useMemo(() => splitCodexDirectives(shown), [shown]);

  if (!shown) return null;
  return (
    <MessageMarkdownContext.Provider value={markdownContext}>
      <div className="prose">
        {parts.map((part, index) => part.kind === "markdown"
          ? <ReactMarkdown key={`markdown-${index}`}
              remarkPlugins={MESSAGE_REMARK_PLUGINS}
              components={MESSAGE_MARKDOWN_COMPONENTS}>{part.text}</ReactMarkdown>
          : <div key={`directive-${index}`} className="codex-directive-status"
              data-directive={part.name}>
              <Icon name="verify" size={14} />
              <span>{part.label}</span>
            </div>)}
        {!done && <span className="cursor" />}
      </div>
    </MessageMarkdownContext.Provider>
  );
}
