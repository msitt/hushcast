import { ACTIVE_STATUSES, type EpisodeStatus } from "../api/client";

const CLASS: Record<EpisodeStatus, string> = {
  processed: "chip-green",
  failed: "chip-red",
  queued: "chip-yellow",
  downloading: "chip-blue",
  transcribing: "chip-blue",
  detecting: "chip-blue",
  cutting: "chip-blue",
  skipped: "chip-gray",
  discovered: "chip-gray",
  expired: "chip-dim",
};

export function StatusChip({ status }: { status: EpisodeStatus }) {
  const active = ACTIVE_STATUSES.includes(status) && status !== "queued";
  return (
    <span className={`chip ${CLASS[status] ?? "chip-gray"}${active ? " chip-active" : ""}`}>
      {active && <span className="chip-pulse" />}
      {status}
    </span>
  );
}
