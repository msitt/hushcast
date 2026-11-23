import type { EpisodeDetailOut, SegmentOut } from "../api/client";
import { formatDuration, formatTimestamp } from "../format";
import { subtract, union, type Iv } from "../intervals";
import { Lane, type LanePart } from "./Lane";

// Drawn in this order, so corrections and optional ranges sit on top of the ads they overlap.
const KINDS = ["correct", "manual", "missed", "promo", "content", "notad", "optional"] as const;
type Kind = (typeof KINDS)[number];

const LEGEND: Record<Kind, string> = {
  correct: "ad",
  manual: "ad you added",
  missed: "ad you added, still in the audio",
  promo: "promo-only, not cut",
  content: "not an ad, already cut",
  notad: "not an ad",
  optional: "optional, not scored",
};

/**
 * When the served audio was last cut. Null while nothing has been cut from these segments, and also
 * when the cut job is no longer on record, in which case nothing is claimed about the audio.
 */
function lastCutAt(ep: EpisodeDetailOut): number | null {
  if (ep.status !== "processed" && ep.status !== "expired") return null;
  const times = ep.jobs
    .filter((j) => j.step === "cut" && j.status === "success" && j.finished_at)
    .map((j) => Date.parse(j.finished_at!));
  return times.length > 0 ? Math.max(...times) : null;
}

function classify(s: SegmentOut, cutAt: number | null): { kind: Kind; label: string } {
  if (s.source === "optional") return { kind: "optional", label: "Optional, not scored by evals" };
  if (s.source === "promo") return { kind: "promo", label: "Promo-only episode, not cut" };
  // Corrections never re-cut audio, so one made after the cut describes audio that was served wrong.
  const afterCut = cutAt !== null && s.corrected_at !== null && Date.parse(s.corrected_at) > cutAt;
  if (!s.kept) {
    if (s.source === "manual") {
      return afterCut
        ? { kind: "missed", label: "Ad you added, still in the audio" }
        : { kind: "manual", label: cutAt !== null ? "Ad you added, cut" : "Ad you added" };
    }
    return { kind: "correct", label: cutAt !== null ? `Ad (${s.category}), cut` : `Ad (${s.category})` };
  }
  return afterCut
    ? { kind: "content", label: "Not an ad, but already cut" }
    : { kind: "notad", label: cutAt !== null ? "Not an ad, kept" : "Not an ad" };
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** The episode's segments on a timeline. */
export function EpisodeTimeline({
  ep,
  activeId,
  onHover,
  onJump,
}: {
  ep: EpisodeDetailOut;
  activeId: number | null;
  onHover: (id: number | null) => void;
  onJump?: (t: number) => void;
}) {
  const duration = ep.duration_s;
  if (!duration || ep.segments.length === 0) return null;

  const cutAt = lastCutAt(ep);
  const classified = ep.segments.map((s) => ({ s, ...classify(s, cutAt) }));
  const parts: LanePart[] = [...classified]
    .sort((a, b) => KINDS.indexOf(a.kind) - KINDS.indexOf(b.kind))
    .map(({ s, kind, label }) => ({
      iv: [s.start_s, s.end_s],
      kind,
      label,
      id: s.id,
      onClick: onJump ? () => onJump(s.start_s) : undefined,
    }));
  const present = KINDS.filter((k) => classified.some((c) => c.kind === k));

  const ivs = (rows: SegmentOut[]) => rows.map((s) => [s.start_s, s.end_s] as Iv);
  const optional = union(ivs(ep.segments.filter((s) => s.source === "optional")));
  const ads = subtract(
    subtract(
      ivs(ep.segments.filter((s) => !s.kept && s.source !== "optional")),
      ivs(ep.segments.filter((s) => s.kept && s.source === "manual"))
    ),
    optional
  );

  const summary = [
    ads.length === 0 ? "No ad ranges" : `${plural(ads.length, "ad range")}, ${formatDuration(ads.reduce((n, a) => n + a[1] - a[0], 0))} total`,
  ];
  if (optional.length > 0) summary.push(plural(optional.length, "optional range"));

  return (
    <div className="segment-timeline">
      <div className="eval-legend" aria-hidden="true">
        {present.map((k) => (
          <span key={k}>
            <i className={`eval-seg-${k}`} /> {LEGEND[k]}
          </span>
        ))}
      </div>
      <Lane duration={duration} parts={parts} activeId={activeId} onHover={onHover} />
      <div className="eval-axis">
        <span>0:00</span>
        <span>{formatTimestamp(duration / 2)}</span>
        <span>{formatTimestamp(duration)}</span>
      </div>
      <p className="field-hint">{summary.join(" · ")}</p>
    </div>
  );
}
