import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../icons";
import {
  buildCalendarDays, pad, parseLocalDateTime, placeDateTimePopover,
  toLocalDateTime,
} from "../date-time";

interface Props {
  value: string;
  onChange: (value: string) => void;
  label?: string;
}

function roundedSoon(): Date {
  const date = new Date(Date.now() + 30 * 60 * 1000);
  date.setMinutes(Math.ceil(date.getMinutes() / 5) * 5, 0, 0);
  return date;
}

function displayValue(value: string): string {
  const date = parseLocalDateTime(value);
  if (!date) return "选择执行日期和时间";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long", day: "numeric", weekday: "short", hour: "2-digit",
    minute: "2-digit", hour12: false,
  }).format(date);
}

export function DateTimePicker({ value, onChange, label = "执行时间" }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState({ left: 16, top: 16 });
  const [draft, setDraft] = useState(value);
  const parsedDraft = parseLocalDateTime(draft);
  const [viewMonth, setViewMonth] = useState(() => parsedDraft ?? new Date());

  useEffect(() => {
    if (!open) setDraft(value);
  }, [value, open]);

  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const closeOnResize = () => setOpen(false);
    document.addEventListener("keydown", close);
    window.addEventListener("resize", closeOnResize);
    return () => {
      document.removeEventListener("keydown", close);
      window.removeEventListener("resize", closeOnResize);
    };
  }, [open]);

  const days = useMemo(() => buildCalendarDays(viewMonth), [viewMonth]);
  const selected = parsedDraft;
  const selectedKey = selected
    ? `${selected.getFullYear()}-${selected.getMonth()}-${selected.getDate()}` : "";

  const openPicker = () => {
    const initial = parseLocalDateTime(value) ?? roundedSoon();
    const rect = rootRef.current?.getBoundingClientRect();
    if (rect) {
      setAnchor(placeDateTimePopover(rect, {
        width: window.innerWidth, height: window.innerHeight,
      }));
    }
    setDraft(toLocalDateTime(initial));
    setViewMonth(new Date(initial.getFullYear(), initial.getMonth(), 1));
    setOpen(true);
  };

  const chooseDate = (date: Date) => {
    const current = parseLocalDateTime(draft) ?? roundedSoon();
    current.setFullYear(date.getFullYear(), date.getMonth(), date.getDate());
    setDraft(toLocalDateTime(current));
    if (date.getMonth() !== viewMonth.getMonth()) {
      setViewMonth(new Date(date.getFullYear(), date.getMonth(), 1));
    }
  };

  const chooseQuickDate = (daysFromToday: number) => {
    const date = roundedSoon();
    date.setDate(date.getDate() + daysFromToday);
    setDraft(toLocalDateTime(date));
    setViewMonth(new Date(date.getFullYear(), date.getMonth(), 1));
  };

  const setTimePart = (part: "hour" | "minute", next: number) => {
    const date = parseLocalDateTime(draft) ?? roundedSoon();
    if (part === "hour") date.setHours(next);
    else date.setMinutes(next);
    setDraft(toLocalDateTime(date));
  };

  return <div className="date-time-picker" ref={rootRef}>
    <button type="button" className={`date-time-trigger${value ? " has-value" : ""}`}
      onClick={openPicker} aria-haspopup="dialog" aria-expanded={open}>
      <span className="date-time-trigger-icon"><Icon name="calendar" size={17} /></span>
      <span><small>{label}</small><b>{displayValue(value)}</b></span>
      <Icon name="chev" size={16} />
    </button>
    {open && createPortal(<>
      <button type="button" className="date-time-scrim" aria-label="关闭日期选择器"
        onClick={() => setOpen(false)} />
      <div className="date-time-popover" role="dialog" aria-modal="true"
        aria-label="选择执行日期和时间" style={{
          "--date-time-left": `${anchor.left}px`,
          "--date-time-top": `${anchor.top}px`,
        } as CSSProperties}>
        <header>
          <button type="button" onClick={() => setViewMonth(new Date(
            viewMonth.getFullYear(), viewMonth.getMonth() - 1, 1))}
            aria-label="上个月"><Icon name="chevron-left" size={17} /></button>
          <strong>{viewMonth.getFullYear()}年 {viewMonth.getMonth() + 1}月</strong>
          <button type="button" onClick={() => setViewMonth(new Date(
            viewMonth.getFullYear(), viewMonth.getMonth() + 1, 1))}
            aria-label="下个月"><Icon name="chevron-right" size={17} /></button>
        </header>
        <div className="date-time-quick">
          <button type="button" onClick={() => chooseQuickDate(0)}>今天</button>
          <button type="button" onClick={() => chooseQuickDate(1)}>明天</button>
          <button type="button" onClick={() => chooseQuickDate(7)}>下周</button>
        </div>
        <div className="date-time-week" aria-hidden="true">
          {Array.from("一二三四五六日").map((day) => <span key={day}>{day}</span>)}
        </div>
        <div className="date-time-days" role="grid">
          {days.map((item) => {
            const selectedDay = item.key === selectedKey;
            return <button key={item.key} type="button" role="gridcell"
              className={`${item.inMonth ? "" : "outside"}${item.today ? " today" : ""}${selectedDay ? " selected" : ""}`}
              aria-label={`${item.date.getFullYear()}年${item.date.getMonth() + 1}月${item.day}日`}
              aria-selected={selectedDay} onClick={() => chooseDate(item.date)}>
              {item.day}
            </button>;
          })}
        </div>
        <div className="date-time-clock">
          <span><Icon name="clock" size={16} />时间</span>
          <label><span>时</span><select value={selected?.getHours() ?? 0}
            onChange={(event) => setTimePart("hour", Number(event.target.value))}>
            {Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>{pad(hour)}</option>)}
          </select></label>
          <i>:</i>
          <label><span>分</span><select value={selected?.getMinutes() ?? 0}
            onChange={(event) => setTimePart("minute", Number(event.target.value))}>
            {Array.from({ length: 60 }, (_, minute) => <option key={minute} value={minute}>{pad(minute)}</option>)}
          </select></label>
        </div>
        <footer>
          <button type="button" className="date-time-clear" onClick={() => {
            setDraft(""); onChange(""); setOpen(false);
          }}>清除</button>
          <span />
          <button type="button" onClick={() => setOpen(false)}>取消</button>
          <button type="button" className="primary" onClick={() => {
            if (parseLocalDateTime(draft)) onChange(draft);
            setOpen(false);
          }}>完成</button>
        </footer>
      </div>
    </>, document.body)}
  </div>;
}
