import { useEffect, useState } from "react";
import type { Components } from "react-markdown";

type MarkdownModule = {
  ReactMarkdown: typeof import("react-markdown").default;
  remarkGfm: typeof import("remark-gfm").default;
};

let loadPromise: Promise<MarkdownModule> | null = null;
let loadedMod: MarkdownModule | null = null;

function loadMarkdown(): Promise<MarkdownModule> {
  return (loadPromise ??= Promise.all([
    import("react-markdown"),
    import("remark-gfm"),
  ]).then(([markdown, gfm]) => {
    const loaded = { ReactMarkdown: markdown.default, remarkGfm: gfm.default };
    loadedMod = loaded;
    return loaded;
  }));
}

// SSR (tests render synchronously via renderToStaticMarkup) never runs
// useEffect, so the async chunk can't resolve there — preload it so the first
// render is real markdown. Client builds define import.meta.env.SSR=false and
// dead-code-eliminate this branch, keeping the lazy split.
if (import.meta.env.SSR) {
  await loadMarkdown();
}

// Lazy markdown renderer: the react-markdown/remark-gfm stack (~150-220KB) is
// fetched on first use instead of on first paint. Until it arrives we render
// plain text, then swap in the formatted output for the same string.
export function Markdown({ children, components }: {
  children: string;
  components?: Components;
}) {
  const [mod, setMod] = useState<MarkdownModule | null>(loadedMod);

  useEffect(() => {
    let alive = true;
    loadMarkdown().then((loaded) => {
      if (alive) setMod(loaded);
    });
    return () => {
      alive = false;
    };
  }, []);

  if (!mod) {
    return <span style={{ whiteSpace: "pre-wrap" }}>{children}</span>;
  }
  return (
    <mod.ReactMarkdown remarkPlugins={[mod.remarkGfm]} components={components}>
      {children}
    </mod.ReactMarkdown>
  );
}
