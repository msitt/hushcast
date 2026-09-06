import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { HeadphonesIcon, MicrophoneIcon, PlusIcon } from "@phosphor-icons/react";
import { api, type FeedOut } from "../api/client";
import { useAsyncData } from "../hooks";
import { useProcessingActive } from "../components/SystemStatusContext";
import { useToasts } from "../components/Toasts";
import { AddPodcastDialog } from "../components/AddPodcastDialog";
import { CopyButton } from "../components/CopyButton";

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
            Search for a podcast or add an RSS feed, and hushcast will download episodes, transcribe them, find the ads, and serve you a
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
