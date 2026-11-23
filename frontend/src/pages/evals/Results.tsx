import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  type EvalEpisodeCell,
  type EvalMetricRow,
  type EvalRepeatResult,
  type EvalResultEpisode,
  type EvalResultsOut,
  type EvalRunOut,
  type EvalScoredRun,
  type EvalSegment,
  type EvalSpread,
} from "../../api/client";
import { ConfirmDialog, Modal } from "../../components/Modal";
import { useToasts } from "../../components/Toasts";
import { useAsyncData } from "../../hooks";
import { formatCost, formatDate, formatDuration, formatTimestamp } from "../../format";
import { Pager, usePaged } from "./Pager";

const MAX_COMPARED = 6;

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// ---------------------------------------------------------------------------
// formatting

function formatMetric(row: EvalMetricRow, v: number | null): string {
  if (v == null) return "-";
  if (row.key === "cost_per_episode_usd") return formatCost(v);
  return row.kind === "pct" ? `${(100 * v).toFixed(row.digits)}%` : v.toFixed(row.digits);
}

function MetricCell({ row, spread, best }: { row: EvalMetricRow; spread: EvalSpread | undefined; best: boolean }) {
  if (!spread || spread.mean == null) return <td className="num">-</td>;
  // a spread too small to show ("0.0–0.0") is no spread
  const varies =
    spread.min != null && spread.max != null && formatMetric(row, spread.min) !== formatMetric(row, spread.max);
  return (
    <td className={`num${best ? " eval-best" : ""}`} title={best ? "Best of the compared runs" : undefined}>
      {formatMetric(row, spread.mean)}
      {varies && (
        <span className="eval-range">
          {" "}
          {formatMetric(row, spread.min)}–{formatMetric(row, spread.max)}
        </span>
      )}
    </td>
  );
}

function bestRunIds(row: EvalMetricRow, runs: EvalScoredRun[], pick: (r: EvalScoredRun) => Record<string, EvalSpread> | null): Set<number> {
  // compared as displayed, so two cells that both read "0.0" tie
  const shown = (v: number) => Number((row.kind === "pct" ? 100 * v : v).toFixed(row.digits));
  const values = runs
    .map((r) => ({ id: r.run_id, v: pick(r)?.[row.key]?.mean ?? null }))
    .filter((x): x is { id: number; v: number } => x.v != null)
    .map((x) => ({ id: x.id, v: shown(x.v) }));
  if (values.length < 2 || row.better === "none") return new Set();
  const target = row.better === "higher" ? Math.max(...values.map((x) => x.v)) : Math.min(...values.map((x) => x.v));
  const winners = values.filter((x) => x.v === target);
  // everyone tied: nothing to point out
  return winners.length === values.length ? new Set() : new Set(winners.map((x) => x.id));
}

// ---------------------------------------------------------------------------
// cost versus error chart

function niceTicks(max: number): number[] {
  if (!(max > 0)) return [0, 1];
  const rough = max / 4;
  const pow = Math.pow(10, Math.floor(Math.log10(rough)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= rough) ?? 10 * pow;
  const ticks = [];
  for (let t = 0; t < max + step * 0.999; t += step) ticks.push(Number(t.toPrecision(12)));
  return ticks;
}

interface ChartPoint {
  id: number;
  name: string;
  cost: number;
  error: number;
}

function CostErrorChart({ points, unknown }: { points: ChartPoint[]; unknown: string[] }) {
  const W = 680;
  const H = 300;
  const m = { l: 58, r: 28, t: 14, b: 44 };
  const maxCost = Math.max(...points.map((p) => p.cost));
  // every model free: still draw an axis, with the dots sitting at zero
  const xTicks = niceTicks(maxCost > 0 ? maxCost : 0.01);
  const yTicks = niceTicks(Math.max(...points.map((p) => p.error), 1));
  const xMax = xTicks[xTicks.length - 1];
  const yMax = yTicks[yTicks.length - 1];
  const x = (v: number) => m.l + ((W - m.l - m.r) * v) / xMax;
  const y = (v: number) => H - m.b - ((H - m.t - m.b) * v) / yMax;

  return (
    <figure className="eval-chart">
      <figcaption>
        <strong>Cost versus error.</strong> Each dot is one model. Lower and further left is better. A model is only
        worth its price if nothing cheaper has a lower error.
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Scatter chart of cost per episode against weighted error">
        {yTicks.map((t) => (
          <g key={`y${t}`}>
            <line className="eval-grid" x1={m.l} x2={W - m.r} y1={y(t)} y2={y(t)} />
            <text className="eval-tick" x={m.l - 8} y={y(t)} textAnchor="end" dominantBaseline="middle">
              {t}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <g key={`x${t}`}>
            <line className="eval-grid" x1={x(t)} x2={x(t)} y1={m.t} y2={H - m.b} />
            <text className="eval-tick" x={x(t)} y={H - m.b + 16} textAnchor="middle">
              {t === 0 ? "$0" : `$${t < 0.01 ? t.toFixed(4) : t < 1 ? t.toFixed(3) : t.toFixed(2)}`}
            </text>
          </g>
        ))}
        <text className="eval-axis-title" x={(m.l + W - m.r) / 2} y={H - 6} textAnchor="middle">
          Cost per episode
        </text>
        <text className="eval-axis-title" transform={`translate(13 ${(m.t + H - m.b) / 2}) rotate(-90)`} textAnchor="middle">
          Weighted error, s per hour
        </text>
        {points.map((p) => {
          const px = x(p.cost);
          const py = y(p.error);
          const flip = px > W * 0.72;
          return (
            <g key={p.id} className="eval-point" tabIndex={0}>
              <title>{`${p.name}: ${formatCost(p.cost)} per episode, ${p.error.toFixed(1)} s/h weighted error`}</title>
              {/* a hit area far larger than the dot */}
              <circle cx={px} cy={py} r={14} fill="transparent" />
              <circle className="eval-dot" cx={px} cy={py} r={5} />
              <text className="eval-point-label" x={px + (flip ? -10 : 10)} y={py} textAnchor={flip ? "end" : "start"} dominantBaseline="middle">
                {p.name}
              </text>
            </g>
          );
        })}
      </svg>
      {unknown.length > 0 && (
        <p className="field-hint">
          Not plotted, cost unknown: {unknown.join(", ")}. The provider reported no cost for some calls and the model
          has no prices set.
        </p>
      )}
    </figure>
  );
}

// ---------------------------------------------------------------------------
// episode timeline

type Iv = [number, number];

function union(a: Iv[]): Iv[] {
  const out: Iv[] = [];
  for (const [s, e] of [...a].filter((i) => i[1] > i[0]).sort((p, q) => p[0] - q[0])) {
    const last = out[out.length - 1];
    if (last && s <= last[1]) last[1] = Math.max(last[1], e);
    else out.push([s, e]);
  }
  return out;
}

function subtract(a: Iv[], b: Iv[]): Iv[] {
  const out: Iv[] = [];
  const cut = union(b);
  for (const [s, e] of union(a)) {
    let cursor = s;
    for (const [bs, be] of cut) {
      if (be <= cursor || bs >= e) continue;
      if (bs > cursor) out.push([cursor, bs]);
      cursor = Math.max(cursor, be);
      if (cursor >= e) break;
    }
    if (cursor < e) out.push([cursor, e]);
  }
  return out;
}

function Lane({ duration, parts }: { duration: number; parts: { iv: Iv; kind: string; label: string }[] }) {
  return (
    <div className="eval-lane">
      {parts.map((p, i) => (
        <span
          key={i}
          className={`eval-seg eval-seg-${p.kind}`}
          style={{ left: `${(100 * p.iv[0]) / duration}%`, width: `${Math.max(0.35, (100 * (p.iv[1] - p.iv[0])) / duration)}%` }}
          title={`${p.label} ${formatTimestamp(p.iv[0])}–${formatTimestamp(p.iv[1])} (${formatDuration(p.iv[1] - p.iv[0])})`}
        />
      ))}
    </div>
  );
}

function repeatParts(truth: Iv[], optional: Iv[], r: EvalRepeatResult, collar: number) {
  // promo and failed outcomes cut nothing, whatever segments were returned
  const cuts = r.outcome === "cut" || r.outcome === "review" ? union(r.segments.map((s: EvalSegment) => [s[0], s[1]] as Iv)) : [];
  // An error no longer than the collar scores as zero, so it isn't drawn either.
  const counts = (iv: Iv) => iv[1] - iv[0] > collar;
  return [
    ...subtract(cuts, subtract(cuts, truth)).map((iv) => ({ iv, kind: "correct", label: "Ad cut" })),
    ...subtract(subtract(cuts, truth), optional).filter(counts).map((iv) => ({ iv, kind: "content", label: "Content cut" })),
    ...subtract(cuts, subtract(cuts, optional)).map((iv) => ({ iv, kind: "optional", label: "Optional, cut (not scored)" })),
    ...subtract(truth, cuts).filter(counts).map((iv) => ({ iv, kind: "missed", label: "Ad missed" })),
  ];
}

function repeatSummary(r: EvalRepeatResult, promo: boolean): string {
  if (r.outcome === "error") return `failed: ${r.error ?? "no usable reply"}`;
  if (promo) return r.outcome === "promo" ? "called it promo-only, correct" : `called it ${r.outcome}, wrong`;
  if (r.outcome === "promo") return "called it promo-only and cut nothing";
  const bits = [`${formatDuration(r.content_cut_s)} content cut`, `${formatDuration(r.missed_ad_s)} ad missed`];
  if (r.outcome === "review") bits.push("held for review");
  return bits.join(" · ");
}

function EpisodeDialog({
  episode,
  runs,
  collar,
  onClose,
}: {
  episode: EvalResultEpisode;
  runs: EvalScoredRun[];
  collar: number;
  onClose: () => void;
}) {
  const optional = union(episode.optional.map((o) => [o[0], o[1]] as Iv));
  const truth = subtract(union(episode.truth.map((t) => [t[0], t[1]] as Iv)), optional);
  const duration = episode.duration_s || 1;
  return (
    <Modal title={episode.episode_title} onClose={onClose} wide>
      <p className="field-hint">
        {episode.feed_title} · {formatDuration(episode.duration_s)} ·{" "}
        {episode.promo ? "promo-only episode" : `${truth.length} ad range${truth.length === 1 ? "" : "s"}`} ·{" "}
        <Link to={`/episodes/${episode.episode_id}`}>Open episode</Link>
      </p>
      <div className="eval-legend" aria-hidden="true">
        <span><i className="eval-seg-truth" /> ground truth</span>
        <span><i className="eval-seg-correct" /> ad cut</span>
        <span><i className="eval-seg-content" /> content cut</span>
        <span><i className="eval-seg-missed" /> ad missed</span>
        {optional.length > 0 && <span><i className="eval-seg-optional" /> optional, not scored</span>}
      </div>
      <div className="eval-timeline">
        <div className="eval-lane-label">Truth</div>
        <Lane
          duration={duration}
          parts={[
            ...truth.map((iv) => ({ iv, kind: "truth", label: "Ad" })),
            ...optional.map((iv) => ({ iv, kind: "optional", label: "Optional (not scored)" })),
          ]}
        />
        <div className="eval-lane-note">
          {episode.promo ? "nothing should be cut" : truth.length === 0 ? "no ads" : formatDuration(truth.reduce((n, t) => n + t[1] - t[0], 0))}
        </div>
        {runs.map((run) => {
          const cell: EvalEpisodeCell | undefined = run.episodes[String(episode.episode_id)];
          return (
            <div className="eval-run-group" key={run.run_id}>
              <div className="eval-run-name">{run.name}</div>
              {(cell?.repeats ?? []).map((r) => (
                <div className="eval-lane-row" key={r.repeat}>
                  <div className="eval-lane-label">Run {r.repeat + 1}</div>
                  <Lane duration={duration} parts={episode.promo ? [] : repeatParts(truth, optional, r, collar)} />
                  <div className="eval-lane-note">{repeatSummary(r, episode.promo)}</div>
                </div>
              ))}
            </div>
          );
        })}
        <div className="eval-lane-label" />
        <div className="eval-axis">
          <span>0:00</span>
          <span>{formatTimestamp(duration / 2)}</span>
          <span>{formatTimestamp(duration)}</span>
        </div>
        <div />
      </div>
      <p className="field-hint">
        The seconds listed leave out the collar around each ground-truth boundary. Differences no longer than the
        collar ({collar} s) count as zero and are not drawn.
      </p>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// page

function defaultSelection(runs: EvalRunOut[]): number[] {
  // One run per model name: the one covering most of today's verified set, the
  // newest on a tie. Then only the runs covering the whole set, because a run
  // that lacks episodes shrinks the comparison for every other run. When no
  // run is complete (a label was just added), the ones with the best coverage.
  const best = new Map<string, EvalRunOut>();
  for (const r of [...runs].sort((a, b) => b.id - a.id)) {
    if (r.done === 0) continue;
    const cur = best.get(r.name);
    if (!cur || r.covered > cur.covered) best.set(r.name, r);
  }
  const candidates = [...best.values()];
  const top = Math.max(0, ...candidates.map((r) => r.covered));
  return candidates
    .filter((r) => r.covered === top)
    .sort((a, b) => b.id - a.id)
    .slice(0, MAX_COMPARED)
    .map((r) => r.id)
    .sort((a, b) => a - b);
}

const SELECTION_KEY = "hushcast.evals.compare";

/** The selection saved in this browser, while it is still current: it is
 * dropped once a run it never saw has results, so new runs are not hidden. */
function savedSelection(runs: EvalRunOut[]): number[] | null {
  try {
    const raw = JSON.parse(localStorage.getItem(SELECTION_KEY) ?? "null") as { ids?: unknown; newest?: unknown } | null;
    if (!raw || !Array.isArray(raw.ids) || typeof raw.newest !== "number") return null;
    const withResults = runs.filter((r) => r.done > 0);
    if (withResults.some((r) => r.id > (raw.newest as number))) return null;
    const ids = raw.ids.filter((id): id is number => withResults.some((r) => r.id === id));
    return ids.length > 0 ? ids.slice(0, MAX_COMPARED) : null;
  } catch {
    return null;
  }
}

function saveSelection(ids: number[], runs: EvalRunOut[]) {
  try {
    const newest = Math.max(0, ...runs.filter((r) => r.done > 0).map((r) => r.id));
    localStorage.setItem(SELECTION_KEY, JSON.stringify({ ids, newest }));
  } catch {
    // storage unavailable: the selection just isn't remembered
  }
}

function cellText(cell: EvalEpisodeCell | undefined, promo: boolean): string {
  if (!cell || cell.repeats.length === 0) return "-";
  if (promo) return `${cell.repeats.filter((r) => r.outcome_ok).length}/${cell.repeats.length} correct`;
  if (cell.weighted_s == null) return "-";
  return `${cell.weighted_s.toFixed(0)} (${(cell.content_cut_s ?? 0).toFixed(0)} / ${(cell.missed_ad_s ?? 0).toFixed(0)})`;
}

/** Largest difference in weighted error between the compared runs, null when fewer than two have a score. */
function episodeGap(runs: EvalScoredRun[], episodeId: number): number | null {
  const values = runs
    .map((r) => r.episodes[String(episodeId)]?.weighted_s)
    .filter((v): v is number => v != null);
  return values.length < 2 ? null : Math.max(...values) - Math.min(...values);
}

export function ResultsTab() {
  const { toastError, toastSuccess } = useToasts();
  const { data: allRuns, error: runsError } = useAsyncData<EvalRunOut[]>(() => api.evalRuns(), []);
  const [selected, setSelected] = useState<number[] | null>(null);
  const [weight, setWeight] = useState("3");
  const [collar, setCollar] = useState("1");
  const [blunder, setBlunder] = useState("5");
  const [data, setData] = useState<EvalResultsOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [skipLeaked, setSkipLeaked] = useState(false);
  const [feedFilter, setFeedFilter] = useState("");
  const [sortBy, setSortBy] = useState("");
  const [openEpisode, setOpenEpisode] = useState<number | null>(null);
  const [promote, setPromote] = useState<EvalScoredRun | null>(null);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (allRuns && selected === null) setSelected(savedSelection(allRuns) ?? defaultSelection(allRuns));
  }, [allRuns, selected]);

  const cfg = useMemo(() => {
    const num = (text: string, fallback: number) => {
      const n = Number(text);
      return text.trim() !== "" && isFinite(n) && n >= 0 ? n : fallback;
    };
    return { content_weight: num(weight, 3), collar: num(collar, 1), blunder: num(blunder, 5) };
  }, [weight, collar, blunder]);

  useEffect(() => {
    if (!selected || selected.length === 0) {
      setData(null);
      return;
    }
    const timer = window.setTimeout(() => {
      abortRef.current?.abort();
      const ctl = new AbortController();
      abortRef.current = ctl;
      api
        .evalResults(selected, cfg, ctl.signal)
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return;
          setError(errorText(err));
        });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [selected, cfg]);

  const runs = data?.runs ?? [];
  const canSkipLeaked = !!data && data.leaked > 0 && runs.some((r) => r.metrics_clean);
  const useClean = skipLeaked && canSkipLeaked;
  const pick = (r: EvalScoredRun) => (useClean ? r.metrics_clean : r.metrics);
  const episodes = (data?.episodes ?? []).filter((e) => !(useClean && e.hints_leak));
  const feedTitles = [...new Set(episodes.map((e) => e.feed_title))].sort((a, b) => a.localeCompare(b));
  const activeFeed = feedTitles.includes(feedFilter) ? feedFilter : "";
  // "run:<id>" sorts by that run's weighted error, "gap" by how much the runs disagree. Worst first,
  // and episodes without a score (promo-only, or no result) go last.
  const sortRun = sortBy.startsWith("run:") ? runs.find((r) => String(r.run_id) === sortBy.slice(4)) : undefined;
  const activeSort = sortBy === "gap" && runs.length > 1 ? "gap" : sortRun ? sortBy : "";
  const sortValue = (episodeId: number): number | null =>
    activeSort === "gap"
      ? episodeGap(runs, episodeId)
      : sortRun
        ? (sortRun.episodes[String(episodeId)]?.weighted_s ?? null)
        : null;
  const filtered = episodes.filter((e) => !activeFeed || e.feed_title === activeFeed);
  const listed = activeSort
    ? [...filtered].sort((a, b) => (sortValue(b.episode_id) ?? -1) - (sortValue(a.episode_id) ?? -1))
    : filtered;
  const paged = usePaged(listed, `${activeFeed}|${activeSort}|${useClean}|${runs.map((r) => r.run_id).join(",")}`);

  if (runsError && !allRuns) return <div className="inline-error">{runsError}</div>;
  if (!allRuns || selected === null) return <div className="loading">Loading runs…</div>;

  const withResults = allRuns.filter((r) => r.done > 0);
  if (withResults.length === 0) {
    return (
      <section className="panel">
        <div className="empty-small">
          No results yet. Start a run from the <Link to="/evals/runs">Runs</Link> tab.
        </div>
      </section>
    );
  }

  const toggleRun = (id: number) => {
    const next = selected.includes(id)
      ? selected.filter((x) => x !== id)
      : selected.length >= MAX_COMPARED
        ? selected
        : [...selected, id].sort((a, b) => a - b);
    setSelected(next);
    saveSelection(next, allRuns);
  };

  const stale = runs.filter((r) => r.stale_episodes > 0);
  const unfinished = runs.filter((r) => r.status !== "done");
  const points: ChartPoint[] = [];
  const unknownCost: string[] = [];
  for (const r of runs) {
    const m = pick(r);
    const err = m?.weighted_error_per_h?.mean;
    if (err == null) continue;
    const cost = m?.cost_per_episode_usd?.mean;
    if (cost == null) unknownCost.push(r.name);
    else points.push({ id: r.run_id, name: r.name, cost, error: err });
  }
  const opened = openEpisode != null ? data?.episodes.find((e) => e.episode_id === openEpisode) : undefined;

  const doPromote = async (run: EvalScoredRun) => {
    if (run.model_id == null) return;
    setBusy(true);
    try {
      const r = await api.promoteEvalModel(run.model_id);
      toastSuccess(
        `Detection now uses ${run.model}` +
          (r.extra_params_not_applied ? ". Its extra request parameters were not carried over." : "")
      );
      setPromote(null);
    } catch (err) {
      toastError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className="panel">
        <h2 className="panel-title">Compare</h2>
        <div className="eval-run-picker">
          {withResults.map((r) => (
            <label key={r.id} className={`eval-run-option${selected.includes(r.id) ? " selected" : ""}`}>
              <input type="checkbox" checked={selected.includes(r.id)} onChange={() => toggleRun(r.id)} />
              <span>
                {r.name}
                <span className="field-hint">
                  {" "}
                  {formatDate(r.created_at)}
                  {r.hints_mode === "none" ? " · no hints" : ""}
                  {r.status !== "done" ? ` · ${r.status}` : ""}
                  {r.covered < r.verified ? ` · covers ${r.covered} of ${r.verified}` : ""}
                </span>
              </span>
            </label>
          ))}
        </div>
        <div className="eval-controls">
          <label className="field">
            <span className="field-label">Content weight</span>
            <input type="number" min="0" step="0.5" value={weight} onChange={(e) => setWeight(e.target.value)} />
            <span className="field-hint">Seconds of missed ad that one second of cut content is worth.</span>
          </label>
          <label className="field">
            <span className="field-label">Collar, seconds</span>
            <input type="number" min="0" step="0.5" value={collar} onChange={(e) => setCollar(e.target.value)} />
            <span className="field-hint">Ignored on each side of every ground-truth boundary.</span>
          </label>
          <label className="field">
            <span className="field-label">Blunder, seconds</span>
            <input type="number" min="0" step="1" value={blunder} onChange={(e) => setBlunder(e.target.value)} />
            <span className="field-hint">Content cut in one episode that counts as a blunder.</span>
          </label>
        </div>
      </section>

      {error && <div className="inline-error">{error}</div>}
      {selected.length === 0 && <div className="empty-small">Select at least one run to see its results.</div>}

      {data && runs.length > 0 && (
        <>
          {data.mixed_conditions && (
            <div className="inline-warning">
              These runs were not made under the same conditions: the hints or detection settings changed between
              them, or one ran without hints. Differences may come from that and not from the models.
            </div>
          )}
          {unfinished.length > 0 && (
            <div className="inline-warning">
              {unfinished.map((r) => `${r.name} (${r.status})`).join(", ")}: not finished, so only the episodes every
              compared run has results for are shown.
            </div>
          )}
          {stale.length > 0 && (
            <div className="inline-warning">
              {stale.map((r) => `${r.name}: ${r.stale_episodes}`).join(", ")} episode result
              {stale.reduce((n, r) => n + r.stale_episodes, 0) > 1 ? "s were" : " was"} left out because the episode's
              transcript copy changed after the run. Run the model again to cover them.
            </div>
          )}

          {episodes.length === 0 ? (
            <div className="empty-small">The selected runs have no episodes in common yet.</div>
          ) : (
            <>
              <section className="panel">
                <div className="panel-head">
                  <h2 className="panel-title">
                    Scores over {episodes.length} episode{episodes.length > 1 ? "s" : ""}
                  </h2>
                  {canSkipLeaked && (
                    <label
                      className="field field-inline"
                      title="Corrections on these episodes were distilled into the current hints, so the hints partly describe their answers and flatter every model. If the ranking holds without them, the leak does not matter."
                    >
                      <input type="checkbox" checked={skipLeaked} onChange={(e) => setSkipLeaked(e.target.checked)} />
                      <span>
                        Leave out {data.leaked} episode{data.leaked > 1 ? "s" : ""} the hints learned from
                      </span>
                    </label>
                  )}
                </div>
                <div className="table-scroll">
                  <table className="data-table eval-metrics">
                    <thead>
                      <tr>
                        <th>Metric</th>
                        {runs.map((r) => (
                          <th key={r.run_id} className="num">
                            {r.name}
                            <div className="eval-th-model">{r.model}</div>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.metric_rows.map((row) => {
                        const best = bestRunIds(row, runs, pick);
                        return (
                          <tr key={row.key}>
                            <td>
                              {row.label}
                              {row.better === "higher" && <span className="field-hint"> (higher is better)</span>}
                            </td>
                            {runs.map((r) => (
                              <MetricCell key={r.run_id} row={row} spread={pick(r)?.[row.key]} best={best.has(r.run_id)} />
                            ))}
                          </tr>
                        );
                      })}
                      <tr>
                        <td></td>
                        {runs.map((r) => (
                          <td key={r.run_id} className="num">
                            {r.model_id != null && (
                              <button
                                className="btn btn-small"
                                title="Make this the model production detection uses"
                                onClick={() => setPromote(r)}
                              >
                                Use this model
                              </button>
                            )}
                          </td>
                        ))}
                      </tr>
                    </tbody>
                  </table>
                </div>
                <p className="field-hint">
                  Lower is better unless noted. Where repeats differ, the range across repeats follows the mean.
                </p>
              </section>

              {points.length > 0 && (
                <section className="panel">
                  <CostErrorChart points={points} unknown={unknownCost} />
                </section>
              )}

              <section className="panel">
                <h2 className="panel-title">By episode</h2>
                <p className="field-hint">
                  Weighted error in seconds, then content cut and ad missed. Select a cell to see where the cuts fell.
                </p>
                <div className="eval-filters">
                  <label className="field field-inline">
                    <span className="field-label">Podcast</span>
                    <select value={activeFeed} onChange={(e) => setFeedFilter(e.target.value)}>
                      <option value="">All</option>
                      {feedTitles.map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field field-inline">
                    <span className="field-label">Sort</span>
                    <select value={activeSort} onChange={(e) => setSortBy(e.target.value)}>
                      <option value="">Episode order</option>
                      {runs.length > 1 && <option value="gap">Largest difference between models</option>}
                      {runs.map((r) => (
                        <option key={r.run_id} value={`run:${r.run_id}`}>
                          Worst for {r.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  {listed.length !== episodes.length && (
                    <span className="field-hint">
                      {listed.length} of {episodes.length} shown
                    </span>
                  )}
                </div>
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Episode</th>
                        <th className="num">Length</th>
                        {runs.map((r) => (
                          <th key={r.run_id} className="num">
                            {r.name}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {paged.rows.map((e) => (
                        <tr key={e.episode_id}>
                          <td className="cell-title">
                            <button className="link-btn" onClick={() => setOpenEpisode(e.episode_id)}>
                              {e.episode_title}
                            </button>
                            <div className="field-hint">
                              {e.feed_title}
                              {e.promo ? " · promo-only" : ""}
                              {e.hints_leak ? " · in hints" : ""}
                            </div>
                          </td>
                          <td className="num cell-nowrap">{formatDuration(e.duration_s)}</td>
                          {runs.map((r) => {
                            const cell = r.episodes[String(e.episode_id)];
                            const flags = [...new Set((cell?.repeats ?? []).map((x) => x.outcome))].filter(
                              (o) => o === "error" || o === "review" || (o === "promo" && !e.promo)
                            );
                            const blunderHit = !e.promo && (cell?.content_cut_s ?? 0) > data.config.blunder_s;
                            return (
                              <td key={r.run_id} className="num cell-nowrap">
                                <button className="link-btn eval-cell" onClick={() => setOpenEpisode(e.episode_id)}>
                                  {cellText(cell, e.promo)}
                                </button>
                                {blunderHit && <span className="chip chip-tiny chip-red eval-chip-gap">blunder</span>}
                                {flags.map((f) => (
                                  <span key={f} className="chip chip-tiny chip-orange eval-chip-gap">
                                    {f === "error" ? "failed" : f}
                                  </span>
                                ))}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Pager {...paged} />
              </section>
            </>
          )}
        </>
      )}

      {opened && data && (
        <EpisodeDialog episode={opened} runs={runs} collar={data.config.collar_s} onClose={() => setOpenEpisode(null)} />
      )}
      {promote && (
        <ConfirmDialog
          title="Use this model for detection"
          message={`New episodes will be detected with ${promote.model}. Already processed episodes are not touched. You can change it back under Settings.`}
          confirmLabel="Use this model"
          busy={busy}
          onConfirm={() => void doPromote(promote)}
          onCancel={() => setPromote(null)}
        />
      )}
    </>
  );
}
