const MUTATING_FILE_TOOLS = new Set([
  "write", "edit", "multiedit", "notebookedit", "editfile",
  "apply_patch", "filechange",
]);

function pushPath(paths: string[], seen: Set<string>, value: unknown): void {
  if (typeof value !== "string") return;
  const path = value.trim();
  if (!path || seen.has(path)) return;
  seen.add(path);
  paths.push(path);
}

/** Read the canonical field and both engines' legacy mutation payloads. */
export function filePathsFromInput(input?: Record<string, unknown> | null): string[] {
  if (!input) return [];
  const paths: string[] = [];
  const seen = new Set<string>();
  const canonical = input.file_paths;
  if (Array.isArray(canonical)) {
    canonical.forEach((path) => pushPath(paths, seen, path));
  }
  for (const key of ["file_path", "path", "notebook_path"] as const) {
    pushPath(paths, seen, input[key]);
  }

  const changes = input.changes;
  if (Array.isArray(changes)) {
    for (const change of changes) {
      if (!change || typeof change !== "object" || Array.isArray(change)) continue;
      const record = change as Record<string, unknown>;
      for (const key of ["path", "move_path", "destination_path", "to"] as const) {
        pushPath(paths, seen, record[key]);
      }
    }
  } else if (changes && typeof changes === "object") {
    for (const [path, change] of Object.entries(changes)) {
      pushPath(paths, seen, path);
      if (!change || typeof change !== "object" || Array.isArray(change)) continue;
      const record = change as Record<string, unknown>;
      for (const key of ["path", "move_path", "destination_path", "to"] as const) {
        pushPath(paths, seen, record[key]);
      }
    }
  }
  return paths;
}

export function mutatedFilePaths(tool: string, input: Record<string, unknown>): string[] {
  if (!MUTATING_FILE_TOOLS.has(tool.toLowerCase())) return [];
  return filePathsFromInput(input);
}

interface MutationBlock {
  kind: string;
  tool?: string | null;
  input?: Record<string, unknown> | null;
  diff?: string | null;
  result?: { diff?: string | null };
}

/** Collect only the paths and authoritative diffs emitted by one turn.
 *
 * The returned diff is deliberately not recomputed from the current worktree:
 * doing that would mix unrelated dirty files into an older turn's change card.
 */
export function collectTurnFileChanges(blocks: MutationBlock[]): {
  paths: string[];
  diff: string;
} {
  const paths: string[] = [];
  const seenPaths = new Set<string>();
  const diffs: string[] = [];
  const seenDiffs = new Set<string>();
  for (const block of blocks) {
    if (block.kind !== "tool" || !block.tool || !block.input) continue;
    const blockPaths = mutatedFilePaths(block.tool, block.input);
    if (!blockPaths.length) continue;
    for (const path of blockPaths) pushPath(paths, seenPaths, path);
    const diff = block.result?.diff ?? block.diff;
    if (typeof diff !== "string" || !diff.trim() || seenDiffs.has(diff)) continue;
    seenDiffs.add(diff);
    diffs.push(diff.trimEnd());
  }
  return { paths, diff: diffs.join("\n") };
}
