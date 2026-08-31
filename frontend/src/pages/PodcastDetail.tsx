import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeftIcon,
  CaretLeftIcon,
  CaretRightIcon,
  DownloadSimpleIcon,
  MicrophoneIcon,
} from "@phosphor-icons/react";
import {
  ALL_STATUSES,
  api,
  type DistillProposal,
  type EpisodeListOut,
  type EpisodeOut,
  type FeedOut,
} from "../api/client";
import { useAsyncData } from "../hooks";
import { useProcessingActive } from "../components/SystemStatusContext";
import { useToasts } from "../components/Toasts";
import { ConfirmDialog, Modal } from "../components/Modal";
import { CopyButton } from "../components/CopyButton";
import { StatusChip } from "../components/StatusChip";
import { formatDate, formatDateTime, formatDuration, stripHtml } from "../format";

function episodeAction(ep: EpisodeOut): { label: string; run: () => Promise<{ ok: boolean }> } | null {
  switch (ep.status) {
    case "skipped":
    case "discovered":
    case "expired":
      return { label: "Process", run: () => api.processEpisode(ep.id) };
    case "failed":
      return { label: "Retry", run: () => api.retryEpisode(ep.id) };
    case "processed":
      return { label: "Re-detect ads", run: () => api.reprocessEpisode(ep.id, "detect") };
    default:
      return null;
  }
}

function FeedSettingsPanel({ feed, onSaved }: { feed: FeedOut; onSaved: (f: FeedOut) => void }) {
  const [open, setOpen] = useState(false);
  const [enabled, setEnabled] = useState(feed.enabled);
  const [whitelisted, setWhitelisted] = useState(feed.whitelisted);
  const [hints, setHints] = useState(feed.detection_hints ?? "");
  const [busy, setBusy] = useState(false);
  const { toastError, toastSuccess } = useToasts();

  // Re-sync form when the feed object changes (e.g. after save or refresh).
  useEffect(() => {
    setEnabled(feed.enabled);
    setWhitelisted(feed.whitelisted);
    setHints(feed.detection_hints ?? "");
  }, [feed]);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const updated = await api.patchFeed(feed.id, {
        enabled,
        whitelisted,
        detection_hints: hints.trim() === "" ? null : hints,
      });
      onSaved(updated);
      toastSuccess("Feed settings saved");
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel collapsible">
      <button className="panel-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className={`caret${open ? " caret-open" : ""}`}><CaretRightIcon size={14} weight="bold" /></span> Feed settings
      </button>
      {open && (
        <form className="panel-body form-stack" onSubmit={save}>
          <div className="form-row">
            <label className="field field-inline">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              <span>Enabled (poll and process new episodes)</span>
            </label>
            <label className="field field-inline">
              <input type="checkbox" checked={whitelisted} onChange={(e) => setWhitelisted(e.target.checked)} />
              <span>Whitelisted (no ad removal)</span>
            </label>
          </div>
          <label className="field">
            <span className="field-label">Detection hints</span>
            <textarea
              rows={3}
              value={hints}
              onChange={(e) => setHints(e.target.value)}
              placeholder="Extra context for the ad-detection LLM specific to this show"
            />
            <span className="field-hint">Yours alone, the AI never edits this field.</span>
          </label>
          {feed.learned_hints && (
            <label className="field">
              <span className="field-label">Learned hints (from your corrections)</span>
              <textarea className="mono" rows={4} value={feed.learned_hints} readOnly />
              <span className="field-hint">
                Distilled from corrections and applied alongside your hints. Update it via "Distill hints", or{" "}
                <button
                  type="button"
                  className="link-btn"
                  onClick={() => {
                    void (async () => {
                      try {
                        onSaved(await api.patchFeed(feed.id, { learned_hints: "" }));
                        toastSuccess("Learned hints cleared");
                      } catch (err) {
                        toastError(err instanceof Error ? err.message : String(err));
                      }
                    })();
                  }}
                >
                  clear it
                </button>
                .
              </span>
            </label>
          )}
          <div>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function DistillModal({
  feed,
  onClose,
  onAccepted,
}: {
  feed: FeedOut;
  onClose: () => void;
  onAccepted: (f: FeedOut) => void;
}) {
  const [proposal, setProposal] = useState<DistillProposal | null>(null);
  const [feedHints, setFeedHints] = useState("");
  const [globalHints, setGlobalHints] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [accepting, setAccepting] = useState(false);
  const { toastError, toastSuccess } = useToasts();

  useEffect(() => {
    let cancelled = false;
    api
      .distillFeed(feed.id)
      .then((p) => {
        if (cancelled) return;
        setProposal(p);
        setFeedHints(p.feed_hints);
        setGlobalHints(p.global_hints);
      })
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
  }, [feed.id]);

  const accept = async () => {
    setAccepting(true);
    try {
      const updated = await api.patchFeed(feed.id, { learned_hints: feedHints });
      if (proposal && globalHints !== proposal.current_global_hints) {
        await api.putSettings({ global_learned_hints: globalHints });
      }
      toastSuccess("Learned hints applied");
      onAccepted(updated);
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
      setAccepting(false);
    }
  };

  return (
    <Modal title="Distill detection hints" onClose={onClose} wide>
      {error && <div className="inline-error">{error}</div>}
      {!proposal && !error && <div className="loading">Analyzing corrections with the LLM…</div>}
      {proposal && (
        <div className="form-stack">
          <p className="field-hint">
            Proposal from {proposal.corrections_used} correction(s). Edit freely, move lines between the two
            scopes if the AI misjudged one. Nothing is saved until you accept.
          </p>
          <label className="field">
            <span className="field-label">This podcast only</span>
            <textarea className="mono" rows={14} value={feedHints} onChange={(e) => setFeedHints(e.target.value)} />
            <span className="field-hint">Replaces this feed's current learned hints.</span>
          </label>
          <label className="field">
            <span className="field-label">All podcasts (global)</span>
            <textarea className="mono" rows={14} value={globalHints} onChange={(e) => setGlobalHints(e.target.value)} />
            <span className="field-hint">
              Show-independent patterns only. Applied to every feed. Podcast-specific guidance wins on conflict.
            </span>
          </label>
          <div className="modal-actions">
            <button className="btn" onClick={onClose} disabled={accepting}>
              Discard
            </button>
            <button className="btn btn-primary" onClick={() => void accept()} disabled={accepting}>
              {accepting ? "Applying…" : "Accept"}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

export function PodcastDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { toastError, toastSuccess, toastInfo } = useToasts();
  const processing = useProcessingActive();

  // Status filter lives in the URL (?status=failed) so alerts can deep-link to it.
  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = searchParams.get("status") ?? "";
  const setStatusFilter = (status: string) => {
    setSearchParams(status ? { status } : {}, { replace: true });
  };
  const [page, setPage] = useState(1);
  useEffect(() => {
    setPage(1);
  }, [statusFilter]);
  const pageSize = 25;
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [distillOpen, setDistillOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionBusy, setActionBusy] = useState<number | null>(null);

  const feedState = useAsyncData<FeedOut>(() => api.getFeed(id!), [id], {
    intervalMs: processing ? 5000 : 0,
  });
  const episodesState = useAsyncData<EpisodeListOut>(
    () => api.listEpisodes(id!, { status: statusFilter || undefined, page, page_size: pageSize }),
    [id, statusFilter, page],
    { intervalMs: processing ? 5000 : 0 }
  );

  const feed = feedState.data;
  const episodes = episodesState.data;
  const totalPages = episodes ? Math.max(1, Math.ceil(episodes.total / episodes.page_size)) : 1;

  const pollNow = async () => {
    try {
      await api.pollFeed(id!);
      toastSuccess("Poll queued");
      feedState.reload();
      episodesState.reload();
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    }
  };

  const doDelete = async () => {
    setDeleting(true);
    try {
      await api.deleteFeed(id!);
      toastSuccess("Podcast deleted");
      navigate("/");
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  const dismissRow = async (ep: EpisodeOut) => {
    setActionBusy(ep.id);
    try {
      await api.dismissEpisode(ep.id);
      toastSuccess(`Dismissed "${ep.title}" (moved to skipped)`);
      episodesState.reload();
      feedState.reload();
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionBusy(null);
    }
  };

  const runAction = async (ep: EpisodeOut) => {
    const action = episodeAction(ep);
    if (!action) return;
    setActionBusy(ep.id);
    try {
      await action.run();
      toastSuccess(`${action.label} queued for "${ep.title}"`);
      episodesState.reload();
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionBusy(null);
    }
  };

  if (feedState.error && !feed) {
    return (
      <div className="page">
        <div className="inline-error">{feedState.error}</div>
        <Link to="/" className="btn">
          <ArrowLeftIcon size={15} /> Back to podcasts
        </Link>
      </div>
    );
  }

  return (
    <div className="page">
      <Link to="/" className="back-link">
        <ArrowLeftIcon size={14} /> Podcasts
      </Link>

      {!feed ? (
        <div className="loading">Loading podcast…</div>
      ) : (
        <>
          <header className="detail-header">
            <div className="detail-art">
              {feed.image_url ? <img src={feed.image_url} alt="" /> : <div className="feed-art-placeholder"><MicrophoneIcon size={44} weight="duotone" /></div>}
            </div>
            <div className="detail-info">
              <h1>{feed.title || feed.source_url}</h1>
              {feed.description && <p className="detail-desc">{stripHtml(feed.description)}</p>}
              <div className="detail-stats">
                <span>
                  {feed.processed_count}/{feed.episode_count} episodes processed
                </span>
                <span>Last polled: {formatDateTime(feed.last_polled_at)}</span>
                {feed.whitelisted && <span className="chip chip-gray chip-tiny">whitelisted</span>}
                {!feed.enabled && <span className="chip chip-dim chip-tiny">disabled</span>}
              </div>
              {feed.poll_error && <div className="inline-error">Poll error: {feed.poll_error}</div>}
              <div className="detail-actions">
                <CopyButton
                  text={feed.subscribe_url}
                  label="Copy subscribe URL"
                  onCopied={
                    feed.processed_count === 0
                      ? () =>
                          toastInfo(
                            "This feed has no processed episodes yet. Some podcast apps reject empty feeds. If subscribing fails, try again once an episode finishes.",
                          )
                      : undefined
                  }
                />
                <button className="btn" onClick={() => void pollNow()}>
                  Poll now
                </button>
                {feed.new_corrections > 0 && (
                  <button className="btn" onClick={() => setDistillOpen(true)}>
                    Distill hints ({feed.new_corrections})
                  </button>
                )}
                <button className="btn btn-danger-outline" onClick={() => setConfirmDelete(true)}>
                  Delete
                </button>
              </div>
            </div>
          </header>

          <FeedSettingsPanel feed={feed} onSaved={(f) => feedState.setData(f)} />

          {distillOpen && (
            <DistillModal
              feed={feed}
              onClose={() => setDistillOpen(false)}
              onAccepted={(f) => {
                feedState.setData(f);
                setDistillOpen(false);
              }}
            />
          )}

          <section className="panel">
            <div className="table-toolbar">
              <h2>Episodes</h2>
              <label className="field field-inline">
                <span className="field-label">Status</span>
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                >
                  <option value="">All</option>
                  {ALL_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {episodesState.error && <div className="inline-error">{episodesState.error}</div>}
            {episodesState.loading && !episodes && <div className="loading">Loading episodes…</div>}

            {episodes && episodes.items.length === 0 && (
              <div className="empty-small">No episodes{statusFilter ? ` with status "${statusFilter}"` : ""}.</div>
            )}

            {episodes && episodes.items.length > 0 && (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Title</th>
                      <th>Published</th>
                      <th>Status</th>
                      <th>Ads removed</th>
                      <th>Updated</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {episodes.items.map((ep) => {
                      const action = episodeAction(ep);
                      return (
                        <tr key={ep.id}>
                          <td className="cell-title">
                            <Link to={`/episodes/${ep.id}`}>{ep.title}</Link>
                          </td>
                          <td className="cell-nowrap">{formatDate(ep.published_at)}</td>
                          <td>
                            <StatusChip status={ep.status} />
                          </td>
                          <td className="cell-nowrap">
                            {ep.ad_seconds_removed != null && ep.ad_seconds_removed > 0
                              ? formatDuration(ep.ad_seconds_removed)
                              : "-"}
                          </td>
                          <td className="cell-nowrap">{formatDateTime(ep.updated_at)}</td>
                          <td className="cell-action">
                            {ep.status === "processed" && (
                              <a
                                className="btn btn-small"
                                href={`/api/episodes/${ep.id}/download`}
                                download
                                title="Download processed MP3"
                                aria-label="Download processed MP3"
                              >
                                <DownloadSimpleIcon size={14} />
                              </a>
                            )}{" "}
                            {action && (
                              <button
                                className="btn btn-small"
                                disabled={actionBusy === ep.id}
                                onClick={() => void runAction(ep)}
                              >
                                {actionBusy === ep.id ? "…" : action.label}
                              </button>
                            )}{" "}
                            {ep.status === "failed" && (
                              <button
                                className="btn btn-small"
                                disabled={actionBusy === ep.id}
                                onClick={() => void dismissRow(ep)}
                                title="Give up on this episode: mark it skipped so it stops alerting"
                              >
                                Dismiss
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {episodes && episodes.total > episodes.page_size && (
              <div className="pagination">
                <button className="btn btn-small" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  <CaretLeftIcon size={12} weight="bold" /> Prev
                </button>
                <span className="pagination-info">
                  Page {episodes.page} of {totalPages} · {episodes.total} episodes
                </span>
                <button
                  className="btn btn-small"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next <CaretRightIcon size={12} weight="bold" />
                </button>
              </div>
            )}
          </section>
        </>
      )}

      {confirmDelete && feed && (
        <ConfirmDialog
          title="Delete podcast"
          message={
            <>
              Delete <strong>{feed.title || feed.source_url}</strong> and all of its downloaded/processed
              episodes? This cannot be undone.
            </>
          }
          confirmLabel="Delete"
          danger
          busy={deleting}
          onConfirm={() => void doDelete()}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}
