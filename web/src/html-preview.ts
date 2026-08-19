const PREVIEW_CSP = "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; "
  + "font-src data:; object-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'";

export function buildSandboxDocument(body: string): string {
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${PREVIEW_CSP}"><meta name="viewport" content="width=device-width,initial-scale=1"><style>html{color-scheme:light dark}*{box-sizing:border-box}body{margin:0;padding:18px;color:#25231f;background:#fff;font:15px/1.65 system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;overflow-wrap:anywhere}img,svg,canvas,video{max-width:100%;height:auto}table{display:block;max-width:100%;overflow-x:auto;border-collapse:collapse}pre{max-width:100%;white-space:pre-wrap;overflow-wrap:anywhere}a{color:#6256b4}@media(prefers-color-scheme:dark){body{color:#e8e6ee;background:#15151d}a{color:#aaa4ff}}</style></head><body>${body}</body></html>`;
}
