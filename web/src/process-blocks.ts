import type { Block, TextBlock } from "./reducer";

export function processBlocks(blocks: Block[]): Block[] {
  const dedicatedAgents = new Set(blocks.flatMap((block) => (
    block.kind === "process" && block.processKind === "agent" && block.parent_id
      ? [block.parent_id] : []
  )));
  return blocks.filter((block) => {
    if (block.kind === "text") {
      return block.text.length > 0
        && (block.channel === "thinking" || block.channel === "commentary");
    }
    // Keep ToolUse in reducer state for result correlation and older peers,
    // while presenting the dedicated live agent lifecycle only once.
    if (block.kind === "tool"
        && (block.category === "agent"
          || ["agent", "task"].includes(block.tool.toLowerCase()))) {
      return !dedicatedAgents.has(block.tool_use_id);
    }
    return true;
  });
}

export function finalTextBlocks(blocks: Block[]): TextBlock[] {
  return blocks.filter((block): block is TextBlock => block.kind === "text"
    && block.text.length > 0
    && (block.channel == null || block.channel === "final" || block.channel === "unknown"));
}

/** A main answer can finish before a background task or agent reports its
 * final lifecycle event. Keep the process shell live for those late updates
 * instead of presenting a running child as an already-completed turn. */
export function hasActiveProcess(blocks: Block[]): boolean {
  return blocks.some((block) =>
    (block.kind === "tool" || block.kind === "process") && !block.done);
}
