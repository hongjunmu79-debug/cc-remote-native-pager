export interface CalendarDay {
  key: string;
  date: Date;
  day: number;
  inMonth: boolean;
  today: boolean;
}

const pad = (value: number) => String(value).padStart(2, "0");

export function toLocalDateTime(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
    + `T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function parseLocalDateTime(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match) return null;
  const [, year, month, day, hour, minute] = match.map(Number);
  const parsed = new Date(year, month - 1, day, hour, minute, 0, 0);
  if (parsed.getFullYear() !== year || parsed.getMonth() !== month - 1
      || parsed.getDate() !== day || parsed.getHours() !== hour
      || parsed.getMinutes() !== minute) return null;
  return parsed;
}

export function buildCalendarDays(month: Date, now = new Date()): CalendarDay[] {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  // Monday-first calendar: JS Sunday=0 becomes the final column.
  const offset = (first.getDay() + 6) % 7;
  const start = new Date(first.getFullYear(), first.getMonth(), 1 - offset);
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start.getFullYear(), start.getMonth(), start.getDate() + index);
    return {
      key: `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`,
      date,
      day: date.getDate(),
      inMonth: date.getMonth() === month.getMonth(),
      today: date.getFullYear() === now.getFullYear()
        && date.getMonth() === now.getMonth() && date.getDate() === now.getDate(),
    };
  });
}

export function placeDateTimePopover(
  trigger: { left: number; top: number; bottom: number },
  viewport: { width: number; height: number },
  popover = { width: 344, height: 472 },
): { left: number; top: number } {
  const margin = 16;
  const gap = 8;
  const width = Math.min(popover.width, viewport.width - margin * 2);
  const left = Math.min(Math.max(margin, trigger.left), viewport.width - width - margin);
  const roomBelow = viewport.height - margin - trigger.bottom;
  const roomAbove = trigger.top - margin;
  const idealTop = roomBelow >= popover.height
    ? trigger.bottom + gap
    : roomAbove >= popover.height
      ? trigger.top - popover.height - gap
      : viewport.height - popover.height - margin;
  const maxTop = Math.max(margin, viewport.height - popover.height - margin);
  return { left, top: Math.min(Math.max(margin, idealTop), maxTop) };
}

export { pad };
