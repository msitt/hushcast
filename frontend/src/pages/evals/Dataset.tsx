import { useState } from "react";
import { Link } from "react-router-dom";
import { DownloadSimpleIcon } from "@phosphor-icons/react";
import { api, type EvalSetLabel, type EvalSetOut } from "../../api/client";
import { useAsyncData } from "../../hooks";
import { formatDateTime, formatDuration } from "../../format";
import { Pager, usePaged } from "./Pager";

function truthSummary(label: EvalSetLabel): string {
  if (label.verdict === "excluded") return label.note ?? "No reason given";
  if (label.promo) return "Promo-only, nothing to cut";
  if (!label.ad_ranges) return `No ads in ${formatDuration(label.duration_s)}`;
  return `${label.ad_ranges} ad range${label.ad_ranges > 1 ? "s" : ""}, ${formatDuration(label.ad_s)} of ${formatDuration(label.duration_s)}`;
}

export function DatasetTab() {
  const { data, error } = useAsyncData<EvalSetOut>(() => api.evalSet(), []);
  const [feedFilter, setFeedFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const shown = (data?.labels ?? []).filter(
    (l) =>
      (!feedFilter || String(l.feed_id) === feedFilter) &&
      (!statusFilter ||
        (statusFilter === "out_of_date"
          ? !l.matches_segments
          : statusFilter === "in_hints"
            ? l.hints_leak
            : l.verdict === statusFilter))
  );
  const paged = usePaged(shown, `${feedFilter}|${statusFilter}`);

  if (error && !data) return <div className="inline-error">{error}</div>;
  if (!data) return <div className="loading">Loading eval set…</div>;

  const covered = data.feeds.filter((f) => f.verified > 0).length;
  const outOfDate = data.labels.filter((l) => !l.matches_segments).length;

  return (
    <>
      <p className="evals-intro">
        Episodes you have verified by hand are the ground truth for comparing detection models. Label an episode from
        its page: fix the detected segments, then verify them in the Eval label panel. Aim for at least 30 to 60
        verified episodes spread across your podcasts. More is better.
      </p>

      <div className="stat-row evals-stats">
        <div className="stat">
          <span className="stat-label">Verified</span>
          <span className="stat-value stat-accent">{data.verified}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Verified audio</span>
          <span className="stat-value">{data.verified > 0 ? formatDuration(data.verified_s) : "-"}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Podcasts covered</span>
          <span className="stat-value">
            {covered} of {data.feeds.length}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Excluded</span>
          <span className="stat-value">{data.excluded}</span>
        </div>
      </div>

      <section className="panel">
        <h2 className="panel-title">Coverage by podcast</h2>
        {data.feeds.length === 0 ? (
          <div className="empty-small">No podcasts yet.</div>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Podcast</th>
                  <th className="num">Verified</th>
                  <th className="num">Verified audio</th>
                  <th className="num">Excluded</th>
                  <th className="num" title="Unlabeled episodes whose detection has finished">
                    Left to label
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.feeds.map((f) => (
                  <tr key={f.feed_id}>
                    <td className="cell-title">
                      <Link to={`/podcasts/${f.feed_id}`}>{f.title}</Link>
                    </td>
                    <td className="num">{f.verified}</td>
                    <td className="num">{f.verified > 0 ? formatDuration(f.verified_s) : "-"}</td>
                    <td className="num">{f.excluded}</td>
                    <td className="num">{f.candidates}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2 className="panel-title">Labeled episodes</h2>
          {data.verified > 0 && (
            <a
              className="btn btn-small"
              href="/api/evals/export"
              download="hushcast-eval-dataset.json"
              title="The verified set with its transcripts, for running evals from the command line"
            >
              <DownloadSimpleIcon size={13} weight="bold" /> Download dataset
            </a>
          )}
        </div>
        {outOfDate > 0 && (
          <div className="inline-warning">
            {outOfDate} label{outOfDate > 1 ? "s no longer match" : " no longer matches"} the episode's segments. Evals
            keep using the saved ground truth. Open the episode to review it.
          </div>
        )}
        {data.labels.length === 0 ? (
          <div className="empty-small">
            Nothing labeled yet. Open a processed or expired episode, correct its segments, and verify it.
          </div>
        ) : (
          <>
            <div className="eval-filters">
              <label className="field field-inline">
                <span className="field-label">Podcast</span>
                <select value={feedFilter} onChange={(e) => setFeedFilter(e.target.value)}>
                  <option value="">All</option>
                  {data.feeds
                    .filter((f) => f.verified + f.excluded > 0)
                    .map((f) => (
                      <option key={f.feed_id} value={String(f.feed_id)}>
                        {f.title}
                      </option>
                    ))}
                </select>
              </label>
              <label className="field field-inline">
                <span className="field-label">Label</span>
                <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                  <option value="">All</option>
                  <option value="verified">Verified</option>
                  <option value="excluded">Excluded</option>
                  <option value="out_of_date">Out of date</option>
                  <option value="in_hints">In hints</option>
                </select>
              </label>
              {shown.length !== data.labels.length && (
                <span className="field-hint">
                  {shown.length} of {data.labels.length} shown
                </span>
              )}
            </div>
            {shown.length === 0 ? (
              <div className="empty-small">No labeled episodes match these filters.</div>
            ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Episode</th>
                  <th>Podcast</th>
                  <th>Label</th>
                  <th>Ground truth</th>
                  <th>Saved</th>
                </tr>
              </thead>
              <tbody>
                {paged.rows.map((l) => (
                  <tr key={l.episode_id}>
                    <td className="cell-title">
                      <Link to={`/episodes/${l.episode_id}`}>{l.episode_title}</Link>
                    </td>
                    <td className="cell-nowrap">{l.feed_title}</td>
                    <td className="cell-nowrap evals-chips">
                      {l.verdict === "verified" ? (
                        <span className="chip chip-tiny chip-green">verified</span>
                      ) : (
                        <span className="chip chip-tiny chip-dim">excluded</span>
                      )}
                      {!l.matches_segments && (
                        <span
                          className="chip chip-tiny chip-orange"
                          title="The episode's segments no longer match the saved ground truth"
                        >
                          out of date
                        </span>
                      )}
                      {l.hints_leak && (
                        <span
                          className="chip chip-tiny chip-gray"
                          title="Corrections on this episode were distilled into the current hints, so models score better on it than on a new episode. Results can be viewed without these episodes."
                        >
                          in hints
                        </span>
                      )}
                      {l.transcript_source === "llm_prompt" && (
                        <span
                          className="chip chip-tiny chip-gray"
                          title="The transcript copy was rebuilt from the detection prompt and has no word timestamps"
                        >
                          rebuilt
                        </span>
                      )}
                    </td>
                    <td>{truthSummary(l)}</td>
                    <td className="cell-nowrap">{formatDateTime(l.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
            )}
            <Pager {...paged} />
          </>
        )}
      </section>
    </>
  );
}
