import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

import { classifyPreviewTarget, isMarkdownPath } from "../src/preview-path.ts";
import { parseLocalFileTarget } from "../src/file-link.ts";
import {
  InlineImageAssetCache,
  classifyMessageImageTarget,
} from "../src/inline-image-assets.ts";
import { imageDimensionsFromBase64, queryImageDimensions } from "../src/img.ts";
import { collectTurnFileChanges, filePathsFromInput, mutatedFilePaths } from "../src/file-changes.ts";
import type { ServerEvent } from "../src/protocol.ts";

assert.deepEqual(classifyPreviewTarget("docs/README.md", "./img/a.png"), {
  kind: "local", value: "docs/img/a.png",
});
assert.deepEqual(classifyPreviewTarget("docs/README.md", "../root.png?raw=1"), {
  kind: "local", value: "root.png",
});
assert.equal(classifyPreviewTarget("README.md", "../secret.png").kind, "blocked");
assert.equal(classifyPreviewTarget("README.md", "/etc/passwd").kind, "blocked");
assert.equal(classifyPreviewTarget("README.md", "file:///etc/passwd").kind, "blocked");
assert.equal(classifyPreviewTarget("README.md", "//example.com/a.png").kind, "blocked");
assert.deepEqual(classifyPreviewTarget("README.md", "https://example.com/a.png"), {
  kind: "external", value: "https://example.com/a.png",
});
assert.deepEqual(classifyPreviewTarget("README.md", "#section"), {
  kind: "anchor", value: "#section",
});
assert.equal(isMarkdownPath("docs/guide.MD#intro"), true);
assert.equal(isMarkdownPath("docs/image.png"), false);
assert.deepEqual(parseLocalFileTarget(
  "/home/nancy/project/codex_stream.py:731"), {
  path: "/home/nancy/project/codex_stream.py", line: 731, column: undefined,
});
assert.deepEqual(parseLocalFileTarget("src/app.ts#L42C7"), {
  path: "src/app.ts", line: 42, column: 7,
});
assert.deepEqual(parseLocalFileTarget("file:///tmp/a%20b.py:9"), {
  path: "/tmp/a b.py", line: 9, column: undefined,
});
assert.equal(parseLocalFileTarget("https://example.com/a.py:9"), null);
assert.equal(parseLocalFileTarget("#L9"), null);
assert.deepEqual(classifyMessageImageTarget(
  "/Volumes/MuggleSSD/workspace/project/tmp-auth.png"), {
  kind: "local", value: "/Volumes/MuggleSSD/workspace/project/tmp-auth.png",
});
assert.deepEqual(classifyMessageImageTarget("screenshots/result.webp?raw=1"), {
  kind: "local", value: "screenshots/result.webp",
});
assert.deepEqual(classifyMessageImageTarget("https://example.com/result.png"), {
  kind: "external", value: "https://example.com/result.png",
});
assert.equal(classifyMessageImageTarget("data:image/png;base64,cG5n").kind, "blocked");
assert.equal(classifyMessageImageTarget("/etc/password.txt").kind, "blocked");

const inlineAssets = new InlineImageAssetCache(2);
assert.equal(inlineAssets.begin({
  sid: "session-1", path: "qr.png", previewId: "preview-1", requestId: "request-1",
}), true);
assert.equal(inlineAssets.begin({
  sid: "session-1", path: "qr.png", previewId: "preview-2", requestId: "request-2",
}), false, "one visible local image must have at most one in-flight request");
assert.equal(inlineAssets.accept({
  v: 19, type: "preview_asset", ts: 1, sid: "other-session",
  path: "qr.png", preview_id: "preview-1", request_id: "request-1",
  media_type: "image/png", data: "cG5n",
}), false, "a response from another session must not satisfy the request");
assert.equal(inlineAssets.accept({
  v: 19, type: "preview_asset", ts: 2, sid: "session-1",
  path: "qr.png", preview_id: "preview-1", request_id: "request-1",
  media_type: "image/png", data: "cG5n",
}), true);
assert.deepEqual(inlineAssets.forSession("session-1")["qr.png"], {
  status: "ready", mediaType: "image/png", data: "cG5n",
});
assert.equal(inlineAssets.forSession("other-session")["qr.png"], undefined,
  "a background response must never populate the focused session's asset view");
assert.equal(inlineAssets.dropSession("session-1"), true);
assert.equal(inlineAssets.forSession("session-1")["qr.png"], undefined,
  "a destructive history invalidation must evict the session's rendered assets");

const pngHeader = new Uint8Array(24);
pngHeader.set([0x89, ...new TextEncoder().encode("PNG\r\n\x1a\n")], 0);
pngHeader.set(new TextEncoder().encode("IHDR"), 12);
new DataView(pngHeader.buffer).setUint32(16, 640);
new DataView(pngHeader.buffer).setUint32(20, 480);
const pngHeaderBase64 = Buffer.from(pngHeader).toString("base64");
assert.deepEqual(imageDimensionsFromBase64(pngHeaderBase64, "image/png"), [640, 480],
  "base64 chat images expose dimensions without decoding a DOM image");
assert.deepEqual(queryImageDimensions({
  media_type: "image/png", data: pngHeaderBase64,
}), [640, 480], "wire-compatible QueryImg objects can provide local layout metadata");
new DataView(pngHeader.buffer).setUint32(16, 8192);
new DataView(pngHeader.buffer).setUint32(20, 8192);
assert.equal(imageDimensionsFromBase64(
  Buffer.from(pngHeader).toString("base64"), "image/png",
), null, "untrusted image headers cannot reserve an unbounded layout box");
new DataView(pngHeader.buffer).setUint32(16, 640);
new DataView(pngHeader.buffer).setUint32(20, 480);

const sizedInlineAssets = new InlineImageAssetCache(2);
assert.equal(sizedInlineAssets.begin({
  sid: "session-1", path: "sized-qr.png",
  previewId: "preview-sized", requestId: "request-sized",
}), true);
assert.equal(sizedInlineAssets.accept({
  v: 19, type: "preview_asset", ts: 3, sid: "session-1",
  path: "sized-qr.png", preview_id: "preview-sized",
  request_id: "request-sized", media_type: "image/png", data: pngHeaderBase64,
}), true);
assert.deepEqual(sizedInlineAssets.forSession("session-1")["sized-qr.png"], {
  status: "ready", mediaType: "image/png", data: pngHeaderBase64,
  width: 640, height: 480,
}, "local Markdown images keep an intrinsic first-frame aspect ratio");
assert.deepEqual(mutatedFilePaths("Write", {
  file_path: "/tmp/claude.txt",
}), ["/tmp/claude.txt"]);
assert.deepEqual(mutatedFilePaths("apply_patch", {
  changes: [
    { path: "/tmp/codex.txt", kind: "add" },
    { path: "/tmp/old.txt", move_path: "/tmp/new.txt", kind: "move" },
  ],
}), ["/tmp/codex.txt", "/tmp/old.txt", "/tmp/new.txt"]);
assert.deepEqual(filePathsFromInput({
  file_paths: ["/tmp/a", "/tmp/a"],
  changes: { "/tmp/b": { type: "add" } },
}), ["/tmp/a", "/tmp/b"]);
assert.deepEqual(mutatedFilePaths("Read", {
  file_path: "/tmp/secret.txt",
}), [], "read-only tools must never be treated as mutations");
assert.deepEqual(collectTurnFileChanges([
  { kind: "tool", tool: "apply_patch", input: {
    file_paths: ["/tmp/current-turn.md"],
  }, result: { diff: "--- /dev/null\n+++ /tmp/current-turn.md\n@@ -0,0 +1 @@\n+1\n" } },
  { kind: "tool", tool: "Read", input: {
    file_path: "/home/nancy/project/unrelated.py",
  }, result: { diff: "--- a/unrelated.py\n+++ b/unrelated.py\n" } },
]), {
  paths: ["/tmp/current-turn.md"],
  diff: "--- /dev/null\n+++ /tmp/current-turn.md\n@@ -0,0 +1 @@\n+1",
}, "a turn summary must use only its mutation events, never the worktree diff");

const harness = await createServer({
  root: process.cwd(),
  appType: "custom",
  logLevel: "silent",
  server: { middlewareMode: true, watch: null },
});
try {
  const { initialState, reduce } = await harness.ssrLoadModule("/src/reducer.ts");
  const { ArtifactPanel } = await harness.ssrLoadModule(
    "/src/components/ArtifactPanel.tsx");
  const { buildSandboxDocument } = await harness.ssrLoadModule(
    "/src/html-preview.ts");
  const { MessageBlock } = await harness.ssrLoadModule(
    "/src/components/MessageBlock.tsx");
  const codeCopyMarkup = renderToStaticMarkup(createElement(MessageBlock, {
    text: "请执行：\n\n```sh\necho ready\n```",
    done: true,
  }));
  assert.match(codeCopyMarkup, /aria-label="复制代码"/,
    "fenced commands need a local copy action without scrolling to turn end");
  assert.match(codeCopyMarkup, /echo ready/);
  const codexDirectiveMarkup = renderToStaticMarkup(createElement(MessageBlock, {
    text: "提交完成。\n\n::git-commit{cwd=\"/tmp/private-project\"}",
    done: true,
  }));
  assert.match(codexDirectiveMarkup, /Git 提交已创建/,
    "Codex App git directives need a native status instead of leaking wire text");
  assert.doesNotMatch(codexDirectiveMarkup, /::git-commit|private-project/,
    "directive attributes are local UI metadata and must not render as prose");
  const fencedDirectiveMarkup = renderToStaticMarkup(createElement(MessageBlock, {
    text: "```text\n::git-commit{cwd=\"/tmp/example\"}\n```",
    done: true,
  }));
  assert.match(fencedDirectiveMarkup, /::git-commit/,
    "a directive-shaped line inside a code fence remains literal code");
  const localQrMarkup = renderToStaticMarkup(createElement(MessageBlock, {
    text: "![飞书授权二维码](/Volumes/MuggleSSD/workspace/project/tmp-auth.png)",
    done: true,
    imageAssets: {},
    onLoadImage: () => true,
    onPreviewImage: () => {},
  }));
  assert.match(localQrMarkup, /message-image-loading/);
  assert.doesNotMatch(localQrMarkup, /src="\/Volumes\//,
    "a local assistant image path must never become a public HTTP request");
  const loadedQrMarkup = renderToStaticMarkup(createElement(MessageBlock, {
    text: "![飞书授权二维码](/Volumes/MuggleSSD/workspace/project/tmp-auth.png)",
    done: true,
    imageAssets: {
      "/Volumes/MuggleSSD/workspace/project/tmp-auth.png": {
        status: "ready", mediaType: "image/png", data: pngHeaderBase64,
        width: 640, height: 480,
      },
    },
    onPreviewImage: () => {},
  }));
  assert.match(loadedQrMarkup, /class="message-image-trigger"/);
  assert.match(loadedQrMarkup, /src="data:image\/png;base64,/);
  assert.match(loadedQrMarkup, /width="640"/);
  assert.match(loadedQrMarkup, /height="480"/);
  assert.match(loadedQrMarkup, /aria-label="预览图片：飞书授权二维码"/);

  const { NewChatView } = await harness.ssrLoadModule(
    "/src/components/NewChatView.tsx");
  const newChatMarkup = renderToStaticMarkup(createElement(NewChatView, {
    cwd: "/tmp/project",
    onPickCwd: () => {},
    onSend: () => true,
  }));
  assert.match(newChatMarkup, /aria-label="添加照片"/);
  assert.match(newChatMarkup, /aria-label="添加文件"/);
  assert.equal(
    (newChatMarkup.match(/<button[^>]+aria-label="添加照片"/g) ?? []).length, 1);
  assert.equal(
    (newChatMarkup.match(/<button[^>]+aria-label="添加文件"/g) ?? []).length, 0);
  assert.match(newChatMarkup, /type="file"[^>]*accept="image\/\*"[^>]*multiple/,
    "iPhone photo selection needs a dedicated multi-select image input");
  let state = reduce(initialState, {
    type: "open_file_loading",
    file: "README.md",
    sid: "session-1",
    requestId: "preview-new",
    kind: "md",
  });
  const loading = state;

  state = reduce(state, { type: "event", event: {
    v: 10,
    type: "file_preview",
    ts: 1,
    sid: "session-1",
    path: "README.md",
    request_id: "preview-old",
    format: "markdown",
    content: "stale",
    size: 5,
    truncated: false,
    mtime_ns: "1",
  } as ServerEvent });
  assert.equal(state, loading,
    "a stale preview response must not replace the open request");

  state = reduce(state, { type: "event", event: {
    v: 10,
    type: "file_preview",
    ts: 2,
    sid: "session-1",
    path: "docs/README.md",
    request_id: "preview-new",
    format: "markdown",
    content: "# current",
    size: 9,
    truncated: false,
    mtime_ns: "2",
    revision: "a".repeat(64),
  } as ServerEvent });
  assert.equal(state.artifact?.file, "docs/README.md");
  assert.equal(state.artifact?.content, "# current");
  assert.equal(state.artifact?.revision, "a".repeat(64));
  assert.equal(state.artifact?.loading, undefined);

  const rendered = state;
  state = reduce(state, { type: "event", event: {
    v: 10,
    type: "preview_asset",
    ts: 3,
    sid: "other-session",
    path: "docs/image.png",
    preview_id: "preview-new",
    request_id: "asset-wrong-session",
    media_type: "image/png",
    data: "cG5n",
  } as ServerEvent });
  assert.equal(state, rendered, "assets from another session must be ignored");

  state = reduce(state, { type: "event", event: {
    v: 10,
    type: "preview_asset",
    ts: 4,
    sid: "session-1",
    path: "docs/image.png",
    preview_id: "preview-new",
    request_id: "asset-1",
    media_type: "image/png",
    data: "cG5n",
  } as ServerEvent });
  assert.deepEqual(state.artifact?.assets?.["docs/image.png"], {
    mediaType: "image/png", data: "cG5n", error: undefined,
  });

  state = reduce(state, {
    type: "start_file_save",
    requestId: "save-1",
    content: "# edited",
  });
  const saving = state;
  state = reduce(state, { type: "event", event: {
    v: 10,
    type: "file_save_result",
    ts: 5,
    sid: "session-1",
    path: "docs/README.md",
    request_id: "stale-save",
    status: "saved",
    size: 8,
    mtime_ns: "3",
    revision: "b".repeat(64),
  } as ServerEvent });
  assert.equal(state, saving, "a stale save response must be ignored");

  state = reduce(state, { type: "event", event: {
    v: 10,
    type: "file_save_result",
    ts: 6,
    sid: "session-1",
    path: "docs/README.md",
    request_id: "save-1",
    status: "conflict",
    size: 12,
    mtime_ns: "4",
    revision: "c".repeat(64),
    error: "文件已修改",
  } as ServerEvent });
  assert.equal(state.artifact?.content, "# current");
  assert.equal(state.artifact?.saveStatus, "conflict");
  assert.equal(state.artifact?.saveError, "文件已修改");

  state = reduce(state, {
    type: "start_file_save",
    requestId: "save-2",
    content: "# edited",
  });
  state = reduce(state, { type: "event", event: {
    v: 10,
    type: "file_save_result",
    ts: 7,
    sid: "session-1",
    path: "docs/README.md",
    request_id: "save-2",
    status: "saved",
    size: 8,
    mtime_ns: "5",
    revision: "d".repeat(64),
  } as ServerEvent });
  assert.equal(state.artifact?.content, "# edited");
  assert.equal(state.artifact?.saveStatus, "saved");
  assert.equal(state.artifact?.revision, "d".repeat(64));

  state = reduce(state, {
    type: "open_file_loading",
    file: "/home/nancy/project/codex_stream.py",
    sid: "session-1",
    requestId: "source-1",
    kind: "file",
    line: 731,
  });
  state = reduce(state, { type: "event", event: {
    v: 10,
    type: "file_preview",
    ts: 5,
    sid: "session-1",
    path: "cc_remote/wrapper/codex_stream.py",
    request_id: "source-1",
    format: "text",
    content: "source",
    size: 6,
    truncated: false,
    mtime_ns: "3",
  } as ServerEvent });
  assert.equal(state.artifact?.kind, "file");
  assert.equal(state.artifact?.line, 731);
  assert.equal(state.artifact?.file, "cc_remote/wrapper/codex_stream.py");

  const markup = renderToStaticMarkup(createElement(ArtifactPanel, {
    artifact: {
      file: "docs/README.md",
      sid: "session-1",
      requestId: "preview-new",
      kind: "md",
      content: "# Preview\n\n<script>alert(1)</script>",
      size: 42,
      mtimeNs: "2",
      revision: "a".repeat(64),
      assets: {},
    },
    active: "diff",
    hasBtw: false,
    onTab: () => {},
    onClose: () => {},
  }));
  assert.match(markup, /markdown-preview/);
  assert.match(markup, /panel-resizer/);
  assert.match(markup, /data-lock-horizontal-swipe="true"/);
  assert.match(markup, />预览</);
  assert.match(markup, />源码</);
  assert.match(markup, />保存</);
  assert.match(markup, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(markup, /<script>/);

  const messageMarkup = renderToStaticMarkup(createElement(MessageBlock, {
    text: "[codex_stream.py](/home/nancy/project/codex_stream.py:731)",
    done: true,
    onOpenFile: () => {},
  }));
  assert.match(messageMarkup, /message-file-link/);
  assert.match(messageMarkup, /在 Remote 中打开/);
  assert.doesNotMatch(messageMarkup, /href="\/home\/nancy/);

  const source = Array.from({ length: 740 }, (_, index) => `line ${index + 1}`).join("\n");
  const sourceMarkup = renderToStaticMarkup(createElement(ArtifactPanel, {
    artifact: {
      file: "cc_remote/wrapper/codex_stream.py",
      sid: "session-1",
      requestId: "source-1",
      kind: "file",
      content: source,
      line: 731,
      assets: {},
    },
    active: "diff",
    hasBtw: false,
    onTab: () => {},
    onClose: () => {},
  }));
  assert.match(sourceMarkup, /source-line focused/);
  assert.match(sourceMarkup, />731<\/span><code>line 731<\/code>/);
  assert.match(sourceMarkup, /501–740 \/ 740 行/);

  state = reduce(state, {
    type: "open_file_loading",
    file: "report.pdf",
    sid: "session-1",
    requestId: "binary-1",
    kind: "file",
  });
  state = reduce(state, { type: "event", event: {
    v: 19,
    type: "file_preview",
    ts: 6,
    sid: "session-1",
    path: "report.pptx",
    request_id: "binary-1",
    format: "pdf",
    content: "",
    media_type: "application/pdf",
    data: "JVBERi0xLjcK",
    converted_from: "pptx",
    size: 8192,
    truncated: false,
    mtime_ns: "4",
  } as ServerEvent });
  assert.equal(state.artifact?.kind, "pdf");
  assert.equal(state.artifact?.mediaType, "application/pdf");
  assert.equal(state.artifact?.convertedFrom, "pptx");

  const pdfMarkup = renderToStaticMarkup(createElement(ArtifactPanel, {
    artifact: state.artifact!,
    active: "diff",
    hasBtw: false,
    onTab: () => {},
    onClose: () => {},
  }));
  assert.match(pdfMarkup, /rendered-artifact-body/);
  assert.match(pdfMarkup, /PPTX → PDF/);
  assert.match(pdfMarkup, /正在准备预览/);

  const sandbox = buildSandboxDocument("<h1>safe</h1>");
  assert.match(sandbox, /Content-Security-Policy/);
  assert.match(sandbox, /default-src &#39;none&#39;|default-src 'none'/);
  assert.match(sandbox, /<body><h1>safe<\/h1><\/body>/);

  const artifactPanelSource = readFileSync(
    resolve(process.cwd(), "src/components/ArtifactPanel.tsx"), "utf8");
  assert.match(artifactPanelSource, /DOMPurify\.sanitize/);
  assert.match(artifactPanelSource, /sandbox=""/);
  assert.match(artifactPanelSource, /FORBID_TAGS/);
  assert.match(artifactPanelSource, /\["md", "html"\]\.includes\(artifact\.kind\)/);
  assert.match(artifactPanelSource, /artifact\.kind === "html" && mode === "preview"/);
  assert.match(artifactPanelSource, /mode === "source"[\s\S]*?<SourceFile content=\{artifact\.content/);
} finally {
  await harness.close();
}

console.log("markdown preview tests passed");
