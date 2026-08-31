import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { HeadphonesIcon, MicrophoneIcon, PlusIcon } from "@phosphor-icons/react";
import { api, type FeedOut } from "../api/client";
import { useAsyncData } from "../hooks";
import { useProcessingActive } from "../components/SystemStatusContext";
import { useToasts } from "../components/Toasts";
import { Modal } from "../components/Modal";
import { CopyButton } from "../components/CopyButton";

function AddPodcastDialog({ onClose, onAdded }: { onClose: () => void; onAdded: (feed: FeedOut) => void }) {
  const [url, setUrl] = useState("");
  const [whitelisted, setWhitelisted] = useState(false);
  const [hints, setHints] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!url.trim()) {
      setError("RSS URL is required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const feed = await api.createFeed({
        url: url.trim(),
        ...(whitelisted ? { whitelisted: true } : {}),
        ...(hints.trim() !== "" ? { detection_hints: hints.trim() } : {}),
      });
      onAdded(feed);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <Modal title="Add podcast" onClose={onClose}>
      <form onSubmit={submit} className="form-stack">
        {error && <div className="inline-error">{error}</div>}
        <label className="field">
          <span className="field-label">RSS feed URL</span>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/feed.xml"
            autoFocus
            required
          />
        </label>
        <label className="field field-inline">
          <input type="checkbox" checked={whitelisted} onChange={(e) => setWhitelisted(e.target.checked)} />
          <span>Whitelisted (pass episodes through without ad removal)</span>
        </label>
        <label className="field">
          <span className="field-label">Detection hints (optional)</span>
          <textarea
            rows={3}
            value={hints}
            onChange={(e) => setHints(e.target.value)}
            placeholder="e.g. Host reads ads for mattress brands. Sponsor breaks start with a jingle"
          />
        </label>
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Adding…" : "Add podcast"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function FeedCard({ feed }: { feed: FeedOut }) {
  const { toastInfo } = useToasts();
  return (
    <Link to={`/podcasts/${feed.id}`} className="feed-card">
      <div className="feed-art">
        {feed.image_url ? (
          <img src={feed.image_url} alt="" loading="lazy" />
        ) : (
          <div className="feed-art-placeholder"><MicrophoneIcon size={44} weight="duotone" /></div>
        )}
        {!feed.enabled && <span className="feed-disabled-tag">disabled</span>}
      </div>
      <div className="feed-card-body">
        <h3 className="feed-title" title={feed.title}>
          {feed.title || feed.source_url}
        </h3>
        <div className="feed-meta">
          <span>
            {feed.processed_count}/{feed.episode_count} processed
          </span>
          {feed.whitelisted && <span className="chip chip-gray chip-tiny">whitelisted</span>}
          {feed.poll_error && (
            <span className="chip chip-red chip-tiny" title={feed.poll_error}>
              poll error
            </span>
          )}
          {feed.failed_count > 0 && (
            <span
              className="chip chip-red chip-tiny"
              title={`${feed.failed_count} episode${feed.failed_count !== 1 ? "s" : ""} failed processing`}
            >
              {feed.failed_count} failed
            </span>
          )}
        </div>
        <div className="feed-card-actions">
          <CopyButton
            text={feed.subscribe_url}
            label="Copy feed URL"
            small
            onCopied={
              feed.processed_count === 0
                ? () =>
                    toastInfo(
                      "This feed has no processed episodes yet. Some podcast apps reject empty feeds. If subscribing fails, try again once an episode finishes.",
                    )
                : undefined
            }
          />
        </div>
      </div>
    </Link>
  );
}

export function PodcastsPage() {
  const processing = useProcessingActive();
  const { data: feeds, error, loading, reload } = useAsyncData<FeedOut[]>(() => api.listFeeds(), [], {
    intervalMs: processing ? 5000 : 0,
  });
  const [showAdd, setShowAdd] = useState(false);
  const navigate = useNavigate();
  const { toastSuccess } = useToasts();

  return (
    <div className="page">
      <div className="page-header">
        <h1>Podcasts</h1>
        <button className="btn btn-primary" onClick={() => setShowAdd(true)}>
          <PlusIcon size={15} weight="bold" /> Add podcast
        </button>
      </div>

      {error && (
        <div className="inline-error">
          {error}{" "}
          <button className="btn btn-small" onClick={reload}>
            Retry
          </button>
        </div>
      )}

      {loading && !feeds && <div className="loading">Loading podcasts…</div>}

      {feeds && feeds.length === 0 && (
        <div className="empty-hero">
          <div className="empty-icon"><HeadphonesIcon size={36} weight="duotone" /></div>
          <h2>No podcasts yet</h2>
          <p>
            Add an RSS feed and hushcast will download episodes, transcribe them, find the ads, and serve you a
            clean feed to subscribe to.
          </p>
          <button className="btn btn-primary" onClick={() => setShowAdd(true)}>
            Add your first podcast
          </button>
        </div>
      )}

      {feeds && feeds.length > 0 && (
        <div className="feed-grid">
          {feeds.map((f) => (
            <FeedCard key={f.id} feed={f} />
          ))}
        </div>
      )}

      {showAdd && (
        <AddPodcastDialog
          onClose={() => setShowAdd(false)}
          onAdded={(feed) => {
            toastSuccess(`Added "${feed.title || feed.source_url}"`);
            setShowAdd(false);
            navigate(`/podcasts/${feed.id}`);
          }}
        />
      )}
    </div>
  );
}
