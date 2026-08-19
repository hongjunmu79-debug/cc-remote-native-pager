import { useEffect } from "react";
import { Icon } from "../icons";

interface Props {
  banner?: string;
  replaying: boolean;
  truncated: boolean;
  busy: boolean;
  onDismiss: (banner: string) => void;
}

export const TRANSIENT_BANNER_TTL_MS = 6_000;

export function ReconnectBanner({
  banner, replaying, truncated: _truncated, busy, onDismiss,
}: Props) {
  useEffect(() => {
    if (!banner || busy) return;
    const timer = window.setTimeout(
      () => onDismiss(banner), TRANSIENT_BANNER_TTL_MS);
    return () => window.clearTimeout(timer);
  }, [banner, busy, onDismiss]);

  const parts: string[] = [];
  if (replaying) parts.push("正在补发历史…");
  if (banner) parts.push(banner);
  // A replay gap is repaired by the authoritative summary request.  Keep its
  // internal flag for recovery logic, but do not turn it into a sticky warning.
  const text = parts.join(" · ");
  if (!text) return null;
  return (
    <div className="banner show" role="status" aria-live="polite">
      {busy && <span className="sp" aria-hidden="true" />}
      <span className="banner-copy">{text}</span>
      {banner && <button type="button" className="banner-dismiss"
        onClick={() => onDismiss(banner)} aria-label="关闭提示" title="关闭提示">
        <Icon name="close" size={15} />
      </button>}
    </div>
  );
}
