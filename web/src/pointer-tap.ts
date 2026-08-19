/** Separates an intentional tap/click from a pointer sequence used to scroll.
 * Native click remains responsible for keyboard activation and button
 * semantics; this guard only suppresses the synthetic click after a drag,
 * cancellation, or multi-touch gesture. */
export class PointerTapGuard {
  private readonly pointers = new Map<number, { x: number; y: number }>();
  private readonly threshold: number;
  private suppressPointerClick = false;

  constructor(threshold = 8) {
    this.threshold = threshold;
  }

  pointerDown(pointerId: number, x: number, y: number): void {
    if (this.pointers.size === 0) this.suppressPointerClick = false;
    this.pointers.set(pointerId, { x, y });
    if (this.pointers.size > 1) this.suppressPointerClick = true;
  }

  pointerMove(pointerId: number, x: number, y: number): void {
    const start = this.pointers.get(pointerId);
    if (!start) return;
    if (Math.hypot(x - start.x, y - start.y) > this.threshold) {
      this.suppressPointerClick = true;
    }
  }

  pointerUp(pointerId: number): void {
    this.pointers.delete(pointerId);
  }

  pointerCancel(pointerId: number): void {
    this.pointers.delete(pointerId);
    this.suppressPointerClick = true;
  }

  consumeClick(detail: number): boolean {
    if (detail === 0) return true;
    const allowed = !this.suppressPointerClick;
    this.suppressPointerClick = false;
    return allowed;
  }
}
