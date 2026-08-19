type Schedule = (callback: () => void, delayMs: number) => number;
type Cancel = (timer: number) => void;

/** One silent repair attempt for a non-authoritative history/detail response.
 *
 * A second failed response ends the cycle. A later explicit user request can
 * start a fresh cycle, so this never becomes an unbounded background loop.
 */
export class RecoverableReadCoordinator {
  private readonly state = new Map<string, "scheduled" | "attempted">();
  private readonly timers = new Map<string, number>();
  private readonly schedule: Schedule;
  private readonly cancel: Cancel;
  private readonly delayMs: number;

  constructor(
    schedule: Schedule,
    cancel: Cancel,
    delayMs = 250,
  ) {
    this.schedule = schedule;
    this.cancel = cancel;
    this.delayMs = delayMs;
  }

  retry(key: string, read: () => void): boolean {
    const state = this.state.get(key);
    if (state === "scheduled") return false;
    if (state === "attempted") {
      this.state.delete(key);
      return false;
    }
    this.state.set(key, "scheduled");
    const timer = this.schedule(() => {
      this.timers.delete(key);
      if (this.state.get(key) !== "scheduled") return;
      this.state.set(key, "attempted");
      read();
    }, this.delayMs);
    this.timers.set(key, timer);
    return true;
  }

  complete(key: string): void {
    const timer = this.timers.get(key);
    if (timer !== undefined) this.cancel(timer);
    this.timers.delete(key);
    this.state.delete(key);
  }

  clear(): void {
    for (const timer of this.timers.values()) this.cancel(timer);
    this.timers.clear();
    this.state.clear();
  }
}
