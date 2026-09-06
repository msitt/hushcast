import { useEffect, useRef, useState, type FormEvent } from "react";
import { MagnifyingGlassIcon, MicrophoneIcon } from "@phosphor-icons/react";
import { api, ApiError, type FeedOut, type SearchResult } from "../api/client";
import { formatAge } from "../format";
import { Modal } from "./Modal";

const SEARCH_DEBOUNCE_MS = 400;
const MIN_QUERY_CHARS = 2;

function looksLikeUrl(s: string): boolean {
  return /^https?:\/\//i.test(s.trim());
}

function ResultRow({
  result,
  onPick,
  selected,
}: {
  result: SearchResult;
  onPick?: () => void;
  selected?: boolean;
}) {
  const facts: string[] = [];
  if (result.episode_count != null) {
    facts.push(`${result.episode_count.toLocaleString()} episode${result.episode_count === 1 ? "" : "s"}`);
  }
  if (result.latest_episode_at) facts.push(`latest ${formatAge(result.latest_episode_at)}`);
  if (result.feed_host) facts.push(result.feed_host);

  const body = (
    <>
      <div className="search-art">
        {result.artwork_url ? (
          <img src={result.artwork_url} alt="" loading="lazy" />
        ) : (
          <div className="feed-art-placeholder"><MicrophoneIcon size={24} weight="duotone" /></div>
        )}
      </div>
      <div className="search-body">
        <div className="search-title-row">
          <span className="search-title" title={result.title}>{result.title}</span>
          {result.explicit && <span className="chip chip-gray chip-tiny">explicit</span>}
          {result.already_added && <span className="chip chip-green chip-tiny">added</span>}
        </div>
        <div className="search-meta">
          {[result.author, result.genre].filter(Boolean).join(" · ")}
        </div>
        {facts.length > 0 && <div className="search-meta search-facts">{facts.join(" · ")}</div>}
      </div>
    </>
  );

  if (!onPick) return <div className={`search-result${selected ? " search-result-selected" : ""}`}>{body}</div>;
  return (
    <button
      type="button"
      className="search-result"
      onClick={onPick}
      disabled={result.already_added}
      title={result.already_added ? "Already added" : result.feed_url}
    >
      {body}
    </button>
  );
}

export function AddPodcastDialog({ onClose, onAdded }: { onClose: () => void; onAdded: (feed: FeedOut) => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [picked, setPicked] = useState<SearchResult | null>(null);
  const [whitelisted, setWhitelisted] = useState(false);
  const [hints, setHints] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const isUrl = looksLikeUrl(query);
  const trimmed = query.trim();

  // Debounced directory search. Aborts the in-flight request when the query
  // changes so a slow earlier response can't overwrite a newer one.
  useEffect(() => {
    if (picked || isUrl || trimmed.length < MIN_QUERY_CHARS) {
      setResults(null);
      setSearching(false);
      setSearchError(null);
      return;
    }
    const controller = new AbortController();
    setSearching(true);
    const timer = window.setTimeout(async () => {
      try {
        const rs = await api.searchPodcasts(trimmed, controller.signal);
        setResults(rs);
        setSearchError(null);
      } catch (err) {
        if (controller.signal.aborted) return;
        setResults(null);
        setSearchError(err instanceof ApiError ? err.message : String(err));
      } finally {
        if (!controller.signal.aborted) setSearching(false);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [trimmed, isUrl, picked]);

  const feedUrl = picked ? picked.feed_url : isUrl ? trimmed : "";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!feedUrl) {
      setError(isUrl || !trimmed ? "RSS URL is required." : "Pick a podcast from the results, or paste its RSS URL.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const feed = await api.createFeed({
        url: feedUrl,
        ...(whitelisted ? { whitelisted: true } : {}),
        ...(hints.trim() !== "" ? { detection_hints: hints.trim() } : {}),
      });
      onAdded(feed);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  const changePick = () => {
    setPicked(null);
    setError(null);
    // Let the search field mount again before focusing it.
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  return (
    <Modal title="Add podcast" onClose={onClose}>
      <form onSubmit={submit} className="form-stack">
        {error && <div className="inline-error">{error}</div>}

        {picked ? (
          <div className="field">
            <span className="field-label">Podcast</span>
            <ResultRow result={picked} selected />
            <div className="search-picked-actions">
              <span className="search-picked-url" title={picked.feed_url}>{picked.feed_url}</span>
              <button type="button" className="btn btn-small" onClick={changePick} disabled={busy}>
                Change
              </button>
            </div>
          </div>
        ) : (
          <label className="field">
            <span className="field-label">Search by name, or paste an RSS feed URL</span>
            <div className="search-input-wrap">
              <MagnifyingGlassIcon size={15} className="search-input-icon" />
              <input
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="e.g. The Daily, or https://example.com/feed.xml"
                autoFocus
                autoComplete="off"
                spellCheck={false}
              />
            </div>
          </label>
        )}

        {!picked && !isUrl && trimmed.length >= MIN_QUERY_CHARS && (
          <div className="search-results" aria-live="polite">
            {searchError && (
              <div className="inline-error">
                {searchError}. You can still paste the RSS URL directly.
              </div>
            )}
            {!searchError && searching && !results && <div className="search-status">Searching…</div>}
            {!searchError && results && results.length === 0 && (
              <div className="search-status">
                No podcasts found for "{trimmed}". Try a different name, or paste the RSS URL.
              </div>
            )}
            {results && results.length > 0 && (
              <div className={`search-list${searching ? " search-list-stale" : ""}`}>
                {results.map((r) => (
                  <ResultRow key={r.feed_url} result={r} onPick={() => setPicked(r)} />
                ))}
              </div>
            )}
          </div>
        )}

        {(picked || isUrl) && (
          <>
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
          </>
        )}

        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || !feedUrl}>
            {busy ? "Adding…" : "Add podcast"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
