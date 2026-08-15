export interface TerminalCardAnchor {
  bottom: number;
  right: number;
}

export interface TerminalCardViewport {
  height: number;
  width: number;
}

export interface TerminalCardPosition {
  left: number;
  top: number;
  width: number;
}

const CARD_WIDTH = 350;
const EDGE_GAP = 10;
const TRIGGER_GAP = 8;

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

/**
 * Places the terminal status card inside the fixed-position layout viewport.
 * Native Android hosts intentionally center it because older WebViews can use
 * a desktop-width layout viewport even when the physical screen is narrow.
 */
export function positionTerminalCard(
  anchor: TerminalCardAnchor,
  viewport: TerminalCardViewport,
  centerHorizontally: boolean,
): TerminalCardPosition {
  const availableWidth = Math.max(0, viewport.width - EDGE_GAP * 2);
  const width = Math.min(CARD_WIDTH, availableWidth);
  const maximumLeft = Math.max(EDGE_GAP, viewport.width - width - EDGE_GAP);
  const preferredLeft = centerHorizontally
    ? (viewport.width - width) / 2
    : anchor.right - width;

  return {
    left: clamp(preferredLeft, EDGE_GAP, maximumLeft),
    top: clamp(anchor.bottom + TRIGGER_GAP, EDGE_GAP, Math.max(EDGE_GAP, viewport.height - 24)),
    width,
  };
}
