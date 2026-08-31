import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type SystemAlert } from "../api/client";
import { formatBytes, formatDuration, formatElapsed } from "../format";
import { useAsyncData } from "../hooks";
import { useSystemStatus } from "../components/SystemStatusContext";
import { useToasts } from "../components/Toasts";
import { ConfirmDialog } from "../components/Modal";

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"];

function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="stat-tile">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {hint ? <div className="stat-hint">{hint}</div> : null}
    </div>
  );
}

function AlertRow({ alert, onDismissFailed }: { alert: SystemAlert; onDismissFailed?: (alert: SystemAlert) => void }) {
  const body = (
    <>
      <span className={`alert-dot alert-${alert.severity}`} />
      <span>{alert.message}</span>
      {alert.kind === "failed_episodes" && onDismissFailed && (
        <button
          className="btn btn-small alert-action"
          title="Give up on these episodes: mark them skipped so they stop alerting"
          onClick={(e) => {
            // The row itself is a Link. Keep the button from navigating.
            e.preventDefault();
            e.stopPropagation();
            onDismissFailed(alert);
          }}
        >
          Dismiss all
        </button>
      )}
    </>
  );
  return alert.link ? (
    <Link to={alert.link} className="alert-row">
      {body}
    </Link>
  ) : (
    <div className="alert-row">{body}</div>
  );
}

function formatCount(n: number): string {
  return n.toLocaleString();
}

function formatTokens(n: number): string {
  if (n >= 1_000_000_000_000) return `${(n / 1_000_000_000_000).toFixed(1)}T`;
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  return n.toLocaleString();
}

function formatSpeed(audioS: number, elapsedS: number): string | null {
  if (elapsedS <= 0 || audioS <= 0) return null;
  return `${(audioS / elapsedS).toFixed(1)}×`;
}

export function SystemPage() {
  const status = useSystemStatus();
  const { data: info } = useAsyncData(() => api.systemInfo(), []);
  const { data: stats } = useAsyncData(() => api.systemStats(), [], { intervalMs: 60_000 });
  const { data: storage } = useAsyncData(() => api.systemStorage(), [], { intervalMs: 60_000 });
  const { data: alerts, reload: reloadAlerts } = useAsyncData(() => api.systemAlerts(), [], {
    intervalMs: 15_000,
  });

  const { toastError, toastSuccess } = useToasts();

  const [confirmDismiss, setConfirmDismiss] = useState<SystemAlert | null>(null);
  const [dismissBusy, setDismissBusy] = useState(false);
  const dismissFailed = async (alert: SystemAlert) => {
    setDismissBusy(true);
    try {
      const { dismissed } = await api.dismissAllFailed(alert.feed_id);
      toastSuccess(`Dismissed ${dismissed} failed episode${dismissed !== 1 ? "s" : ""} (moved to skipped)`);
      setConfirmDismiss(null);
      reloadAlerts();
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setDismissBusy(false);
    }
  };

  // Server-side capture level (the `log_level` setting), editable in place
  // unless HUSHCAST_LOG_LEVEL overrides it. Distinct from the log *view*
  // filter below.
  const [serverLogLevel, setServerLogLevel] = useState<string | null>(null);
  useEffect(() => {
    if (info) setServerLogLevel(info.log_level);
  }, [info]);
  const changeServerLogLevel = async (level: string) => {
    const prev = serverLogLevel;
    setServerLogLevel(level);
    try {
      await api.putSettings({ log_level: level });
    } catch (err) {
      setServerLogLevel(prev);
      toastError(err instanceof Error ? err.message : String(err));
    }
  };

  const [logLevel, setLogLevel] = useState("INFO");
  const [logsLive, setLogsLive] = useState(true);
  const { data: logs } = useAsyncData(() => api.systemLogs(logLevel), [logLevel], {
    intervalMs: logsLive ? 3000 : 0,
  });

  // Re-render every 30s so the uptime figure stays fresh.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 30_000);
    return () => window.clearInterval(id);
  }, []);

  // Keep the log view pinned to the newest lines unless the user scrolled up.
  const logRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  useEffect(() => {
    const el = logRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [logs]);

  return (
    <div>
      <div className="page-header">
        <h1>System</h1>
      </div>

      {alerts && alerts.length > 0 ? (
        <section className="panel panel-alerts">
          <h2 className="panel-title">Alerts</h2>
          {alerts.map((a, i) => (
            <AlertRow key={`${a.kind}-${i}`} alert={a} onDismissFailed={setConfirmDismiss} />
          ))}
        </section>
      ) : alerts ? (
        <section className="panel">
          <div className="alert-row alert-ok">
            <span className="alert-dot alert-good" />
            <span>All good, no alerts.</span>
          </div>
        </section>
      ) : null}

      {confirmDismiss && (
        <ConfirmDialog
          title="Dismiss failed episodes"
          message={
            <>
              {confirmDismiss.message}. Mark {confirmDismiss.feed_id != null ? "them" : "all failed episodes"} as
              skipped? They will stop alerting but can be re-queued later via Process.
            </>
          }
          confirmLabel="Dismiss all"
          busy={dismissBusy}
          onConfirm={() => void dismissFailed(confirmDismiss)}
          onCancel={() => setConfirmDismiss(null)}
        />
      )}

      {stats ? (
        <>
          <div className="stat-grid">
            <StatTile label="Episodes processed" value={formatCount(stats.episodes_processed)} />
            <StatTile label="Audio processed" value={formatDuration(stats.duration_processed_s)} />
            <StatTile
              label="Ads removed"
              value={formatDuration(stats.ad_seconds_removed)}
            />
            <StatTile
              label="Average ad share"
              value={stats.ad_pct == null ? "-" : `${stats.ad_pct.toFixed(1)}%`}
            />
            <StatTile label="Ad breaks cut" value={formatCount(stats.ad_segments_cut)} />
            {stats.bytes_saved > 0 && (
              <StatTile
                label="Bandwidth saved"
                value={formatBytes(stats.bytes_saved)}
              />
            )}
            {stats.corrections > 0 && (
              <StatTile label="Corrections made" value={formatCount(stats.corrections)} />
            )}
          </div>

          {stats.top_feeds.length > 0 ? (
            <section className="panel">
              <h2 className="panel-title">Most ad-heavy podcasts</h2>
              <table className="sys-table">
                <thead>
                  <tr>
                    <th>Podcast</th>
                    <th className="num">Episodes</th>
                    <th className="num">Ads removed</th>
                    <th className="num">Ad share</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.top_feeds.map((f) => (
                    <tr key={f.feed_id}>
                      <td>
                        <Link to={`/podcasts/${f.feed_id}`}>{f.title || `Feed ${f.feed_id}`}</Link>
                      </td>
                      <td className="num">{f.episodes}</td>
                      <td className="num">{formatDuration(f.ad_seconds)}</td>
                      <td className="num">{f.ad_pct.toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ) : null}

          <section className="panel">
            <h2 className="panel-title">Transcription usage</h2>
            {stats.transcription.total.calls === 0 ? (
              <p className="empty-small">No transcription calls recorded yet.</p>
            ) : (
              <>
                <p className="sys-llm-total">
                  {formatCount(stats.transcription.total.calls)} calls ·{" "}
                  {formatDuration(stats.transcription.total.audio_s)} of audio ·{" "}
                  {formatDuration(stats.transcription.total.elapsed_s)} total time
                  {formatSpeed(stats.transcription.total.audio_s, stats.transcription.total.elapsed_s)
                    ? ` · ${formatSpeed(stats.transcription.total.audio_s, stats.transcription.total.elapsed_s)} realtime`
                    : ""}
                </p>
                <table className="sys-table">
                  <thead>
                    <tr>
                      <th>Model</th>
                      <th>Provider</th>
                      <th className="num">Calls</th>
                      <th className="num">Audio</th>
                      <th className="num">Time</th>
                      <th className="num">Speed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.transcription.by_model.map((m) => (
                      <tr key={`${m.provider}/${m.model}`}>
                        <td className="mono">{m.model}</td>
                        <td>{m.provider}</td>
                        <td className="num">{formatCount(m.calls)}</td>
                        <td className="num">{formatDuration(m.audio_s)}</td>
                        <td className="num">{formatDuration(m.elapsed_s)}</td>
                        <td className="num">{formatSpeed(m.audio_s, m.elapsed_s) ?? "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </section>

          <section className="panel">
            <h2 className="panel-title">Ad-detection LLM usage</h2>
            {stats.llm.total.calls === 0 ? (
              <p className="empty-small">No LLM calls recorded yet.</p>
            ) : (
              <>
                <p className="sys-llm-total">
                  {formatCount(stats.llm.total.calls)} calls ·{" "}
                  {formatTokens(stats.llm.total.prompt_tokens)} tokens in ·{" "}
                  {formatTokens(stats.llm.total.completion_tokens)} out ·{" "}
                  {formatDuration(stats.llm.total.elapsed_s)} total time
                </p>
                <table className="sys-table">
                  <thead>
                    <tr>
                      <th>Model</th>
                      <th>Provider</th>
                      <th className="num">Calls</th>
                      <th className="num">Tokens in</th>
                      <th className="num">Tokens out</th>
                      <th className="num">Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.llm.by_model.map((m) => (
                      <tr key={`${m.provider}/${m.model}`}>
                        <td className="mono">{m.model}</td>
                        <td>{m.provider}</td>
                        <td className="num">{formatCount(m.calls)}</td>
                        <td className="num">{formatTokens(m.prompt_tokens)}</td>
                        <td className="num">{formatTokens(m.completion_tokens)}</td>
                        <td className="num">{formatDuration(m.elapsed_s)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="field-hint">
                  Token counts depend on the provider reporting usage. Some local servers omit it.
                </p>
              </>
            )}
          </section>
        </>
      ) : null}

      <div className="sys-columns">
        <section className="panel">
          <h2 className="panel-title">Environment</h2>
          {info ? (
            <dl className="kv-list">
              <dt>Version</dt>
              <dd>{info.version}</dd>
              <dt>Uptime</dt>
              <dd>{formatElapsed(info.started_at, null)}</dd>
              <dt>Python</dt>
              <dd>{info.python_version}</dd>
              <dt>Platform</dt>
              <dd>{info.platform}</dd>
              <dt>ffmpeg</dt>
              <dd>{info.ffmpeg_version ?? "not found"}</dd>
              <dt>Log level</dt>
              <dd>
                {info.log_level_env_override ? (
                  <>
                    {info.log_level_env_override}{" "}
                    <span className="field-hint">set by HUSHCAST_LOG_LEVEL</span>
                  </>
                ) : (
                  <select
                    value={serverLogLevel ?? info.log_level}
                    onChange={(e) => changeServerLogLevel(e.target.value)}
                  >
                    {LOG_LEVELS.map((l) => (
                      <option key={l} value={l}>
                        {l}
                      </option>
                    ))}
                  </select>
                )}
              </dd>
              <dt>Public URL</dt>
              <dd className="mono">{info.public_url}</dd>
              <dt>Web UI auth</dt>
              <dd>
                {info.auth_mode === "disabled" ? (
                  <>
                    disabled <span className="field-hint">set by HUSHCAST_AUTH</span>
                  </>
                ) : (
                  "password login"
                )}
              </dd>
              <dt>Config dir</dt>
              <dd className="mono">{info.config_dir}</dd>
              <dt>Data dir</dt>
              <dd className="mono">{info.data_dir}</dd>
              <dt>Next feed poll</dt>
              <dd>{info.next_poll_at ? formatElapsed(new Date().toISOString(), info.next_poll_at) + " from now" : "-"}</dd>
              {status ? (
                <>
                  <dt>Queue</dt>
                  <dd>
                    {status.queue_depth} queued
                    {status.active.length > 0 ? `, ${status.active.length} active` : ""}
                  </dd>
                </>
              ) : null}
            </dl>
          ) : (
            <p className="loading-subtle">Loading…</p>
          )}
        </section>

        <section className="panel">
          <h2 className="panel-title">Storage</h2>
          {storage ? (
            <>
              {storage.volumes.map((v) => (
                <div key={v.name} className="vol-row">
                  <div className="vol-head">
                    <span className="vol-name">{v.name}</span>
                    <span className="vol-free">{formatBytes(v.free_bytes)} free of {formatBytes(v.total_bytes)}</span>
                  </div>
                  <div className="vol-bar">
                    <div
                      className="vol-bar-used"
                      style={{ width: `${Math.min(100, (v.used_bytes / v.total_bytes) * 100)}%` }}
                    />
                  </div>
                </div>
              ))}
              <dl className="kv-list kv-tight">
                {storage.breakdown.map((b) => (
                  <div key={b.name} className="kv-pair">
                    <dt>{b.name}</dt>
                    <dd>{formatBytes(b.bytes)}</dd>
                  </div>
                ))}
              </dl>
            </>
          ) : (
            <p className="loading-subtle">Loading…</p>
          )}
        </section>
      </div>

      <section className="panel">
        <div className="log-toolbar">
          <h2 className="panel-title">Logs</h2>
          <select value={logLevel} onChange={(e) => setLogLevel(e.target.value)}>
            {LOG_LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}+
              </option>
            ))}
          </select>
          <label className="field-inline">
            <input type="checkbox" checked={logsLive} onChange={(e) => setLogsLive(e.target.checked)} />
            auto-refresh
          </label>
        </div>
        <div
          className="log-view"
          ref={logRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          }}
        >
          {logs && logs.records.length > 0 ? (
            logs.records.map((r, i) => (
              <div key={i} className={`log-line log-${r.level.toLowerCase()}`}>
                <span className="log-ts">{new Date(r.ts).toLocaleTimeString()}</span>
                <span className="log-level">{r.level}</span>
                <span className="log-logger">{r.logger}</span>
                <span className="log-msg">{r.message}</span>
              </div>
            ))
          ) : (
            <div className="log-empty">No log entries at this level yet.</div>
          )}
        </div>
        <p className="field-hint">
          In-memory only (last {logs?.capacity ?? 1000} entries since startup). Use <code>docker logs</code> for full history.
        </p>
      </section>
    </div>
  );
}
