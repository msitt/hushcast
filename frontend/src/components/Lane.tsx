import { formatDuration, formatTimestamp } from "../format";
import type { Iv } from "../intervals";

export interface LanePart {
  iv: Iv;
  /** Picks the look: the span gets the class eval-seg-{kind}. */
  kind: string;
  label: string;
  /** Ties the span to something outside the lane, for activeId and onHover. */
  id?: number;
  onClick?: () => void;
}

/** One horizontal bar over an episode's duration, with a span per part. Later parts draw on top. */
export function Lane({
  duration,
  parts,
  activeId,
  onHover,
}: {
  duration: number;
  parts: LanePart[];
  activeId?: number | null;
  onHover?: (id: number | null) => void;
}) {
  return (
    <div className="eval-lane">
      {parts.map((p, i) => {
        const title = `${p.label} ${formatTimestamp(p.iv[0])}–${formatTimestamp(p.iv[1])} (${formatDuration(p.iv[1] - p.iv[0])})`;
        const shared = {
          className: `eval-seg eval-seg-${p.kind}${p.id !== undefined && p.id === activeId ? " eval-seg-active" : ""}`,
          style: {
            left: `${(100 * p.iv[0]) / duration}%`,
            width: `${Math.max(0.35, (100 * (p.iv[1] - p.iv[0])) / duration)}%`,
          },
          title,
          onMouseEnter: onHover && p.id !== undefined ? () => onHover(p.id!) : undefined,
          onMouseLeave: onHover && p.id !== undefined ? () => onHover(null) : undefined,
        };
        return p.onClick ? (
          <button key={i} type="button" aria-label={title} onClick={p.onClick} {...shared} />
        ) : (
          <span key={i} {...shared} />
        );
      })}
    </div>
  );
}
