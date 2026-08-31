import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeftIcon, CaretDownIcon, CaretLeftIcon, CaretRightIcon, WarningIcon } from "@phosphor-icons/react";
import {
  api,
  type CueOut,
  type EpisodeDetailOut,
  type JobOut,
  type LlmCallDetail,
  type LlmCallSummary,
  type RawTranscriptionCall,
  type RawTranscriptOut,
  type SegmentOut,
  type TranscriptOut,
  type TranscriptWord,
} from "../api/client";
import { useAsyncData } from "../hooks";
import { useProcessingActive } from "../components/SystemStatusContext";
import { useToasts } from "../components/Toasts";
import { CopyButton, CopyPre } from "../components/CopyButton";
import { StatusChip } from "../components/StatusChip";
import {
  formatDate,
  formatDateTime,
  formatDuration,
  formatElapsed,
  formatTimestamp,
} from "../format";

const CATEGORY_CLASS: Record<string, string> = {
  ad: "chip-red",
  sponsor: "chip-orange",
  self_promo: "chip-purple",
};

const REPROCESS_OPTIONS = [
  { step: "detect", label: "Re-detect ads", hint: "keeps transcript" },
  { step: "transcribe", label: "Re-transcribe", hint: "new transcript, cues and ads" },
  { step: "download", label: "Redo from download", hint: "refetches source audio" },
] as const;

function ReprocessMenu({ disabled, onSelect }: { disabled: boolean; onSelect: (step: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  return (
    <span className="menu-wrap" ref={ref}>
      <button className="btn" disabled={disabled} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        Reprocess <CaretDownIcon size={12} weight="bold" />
      </button>
      {open && (
        <div className="menu" role="menu">
          {REPROCESS_OPTIONS.map((o) => (
            <button
              key={o.step}
              className="menu-item"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onSelect(o.step);
              }}
            >
              <span>{o.label}</span>
              <span className="menu-item-hint">{o.hint}</span>
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

function EpisodeDescription({ html }: { html: string }) {
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || expanded) return;
    const check = () => setOverflowing(el.scrollHeight > el.clientHeight + 1);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [html, expanded]);
  return (
    <div className="panel-body">
      <div
        ref={ref}
        className={`episode-desc${expanded ? "" : " episode-desc-collapsed"}`}
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {overflowing && (
        <button className="link-btn desc-toggle" onClick={() => setExpanded((e) => !e)}>
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}

function JobRow({ job }: { job: JobOut }) {
  const [open, setOpen] = useState(false);
  const metrics = job.metrics ?? {};
  const hasDetail = !!job.log_text || Object.keys(metrics).length > 0 || !!job.error;
  return (
    <div className={`job-row job-${job.status}`}>
      <button className="job-summary" onClick={() => hasDetail && setOpen((o) => !o)} disabled={!hasDetail}>
        <span className={`caret${open ? " caret-open" : ""}`}>{hasDetail ? <CaretRightIcon size={14} weight="bold" /> : null}</span>
        <span className="job-step">{job.step}</span>
        <span className={`job-status job-status-${job.status}`}>{job.status}</span>
        <span className="job-times">
          {formatDateTime(job.started_at)}
          {job.finished_at ? ` → ${formatDateTime(job.finished_at)}` : job.started_at ? " → running" : ""}
        </span>
        <span className="job-elapsed">{formatElapsed(job.started_at, job.finished_at)}</span>
      </button>
      {job.error && !open && <div className="job-error-inline">{job.error}</div>}
      {open && (
        <div className="job-detail">
          {job.error && <div className="inline-error">{job.error}</div>}
          {Object.keys(metrics).length > 0 && (
            <dl className="metrics-grid">
              {Object.entries(metrics).map(([k, v]) => (
                <div key={k} className="metric">
                  <dt>{k}</dt>
                  <dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
                </div>
              ))}
            </dl>
          )}
          {job.log_text && <CopyPre text={job.log_text} />}
        </div>
      )}
    </div>
  );
}

function llmCallOutcome(call: {
  status_code: number | null;
  finish_reason: string | null;
}): { label: string; chip: string } | null {
  if (call.status_code != null && call.status_code !== 200)
    return { label: `HTTP ${call.status_code}`, chip: "chip-red" };
  if (call.finish_reason === "length") return { label: "length", chip: "chip-yellow" };
  if (call.finish_reason) return { label: call.finish_reason, chip: "chip-gray" };
  return null;
}

// Pretty-print response content when it's valid JSON. Anything else passes through untouched.
function prettyIfJson(text: string): string {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return text;
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return text;
  }
}

// The response_format param rendered compactly: "json_schema (ad_segments, strict)"
function describeResponseFormat(params: Record<string, unknown> | null): string | null {
  const rf = params?.response_format as { type?: string; json_schema?: { name?: string; strict?: boolean } } | undefined;
  if (!rf?.type) return null;
  if (rf.type === "json_schema" && rf.json_schema) {
    return `json_schema (${rf.json_schema.name ?? "?"}${rf.json_schema.strict ? ", strict" : ""})`;
  }
  return rf.type;
}

function LlmCallRow({ call }: { call: LlmCallSummary }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<LlmCallDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"content" | "reasoning" | "raw">("content");
  const [showSchema, setShowSchema] = useState(false);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !detail && !loading) {
      setLoading(true);
      try {
        setDetail(await api.getLlmCall(call.id));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    }
  };

  const outcome = llmCallOutcome(call);
  const responseFormat = detail ? describeResponseFormat(detail.params) : null;
  const paramBits: string[] = [];
  if (detail?.params) {
    for (const key of ["temperature", "max_tokens"]) {
      if (detail.params[key] != null) paramBits.push(`${key} ${String(detail.params[key])}`);
    }
    if (responseFormat) paramBits.push(`response_format: ${responseFormat}`);
  }

  return (
    <div className="job-row">
      <button className="job-summary" onClick={() => void toggle()}>
        <span className={`caret${open ? " caret-open" : ""}`}><CaretRightIcon size={14} weight="bold" /></span>
        <span className="job-step">{call.model}</span>
        {outcome && <span className={`chip chip-tiny ${outcome.chip}`}>{outcome.label}</span>}
        <span className="job-times">
          {formatDateTime(call.created_at)}
          {call.provider ? ` · via ${call.provider}` : ` · ${call.url}`} ·{" "}
          {call.prompt_tokens != null
            ? `${call.prompt_tokens} → ${call.completion_tokens ?? "?"} tokens` +
              (call.reasoning_tokens != null ? ` (${call.reasoning_tokens} reasoning)` : "")
            : `${(call.request_chars / 1000).toFixed(1)}k chars in`}
          {call.event_count > 0 && (
            <>
              {" · "}
              <WarningIcon size={12} weight="bold" /> {call.event_count} event{call.event_count > 1 ? "s" : ""}
            </>
          )}
        </span>
        <span className="job-elapsed">{call.elapsed_s.toFixed(1)}s</span>
      </button>
      {open && (
        <div className="job-detail">
          {loading && <div className="loading loading-subtle">Loading call…</div>}
          {error && <div className="inline-error">{error}</div>}
          {detail && (
            <>
              {detail.events.length > 0 && (
                <div style={{ margin: "10px 0 4px" }}>
                  <div className="field-label">events</div>
                  <ul className="field-hint" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                    {detail.events.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </div>
              )}
              {paramBits.length > 0 && (
                <div className="field-hint" style={{ margin: "10px 0 4px" }}>
                  {paramBits.join(" · ")}
                  {responseFormat?.startsWith("json_schema") && (
                    <>
                      {" "}
                      <button className="btn btn-small" onClick={() => setShowSchema((v) => !v)}>
                        {showSchema ? "Hide schema" : "Show schema"}
                      </button>
                    </>
                  )}
                </div>
              )}
              {showSchema && detail.params?.response_format != null && (
                <CopyPre text={JSON.stringify(detail.params.response_format, null, 2)} />
              )}
              {detail.messages.map((m, i) => (
                <div key={i}>
                  <div className="field-label" style={{ margin: "10px 0 4px" }}>
                    request [{m.role}]
                  </div>
                  <CopyPre text={m.content} />
                </div>
              ))}
              <div className="field-label" style={{ margin: "10px 0 4px", display: "flex", gap: 6, alignItems: "center" }}>
                response
                {(detail.reasoning != null || detail.response_raw != null) && (
                  <>
                    <button
                      className="btn btn-small"
                      disabled={tab === "content"}
                      onClick={() => setTab("content")}
                    >
                      Content
                    </button>
                    {detail.reasoning != null && (
                      <button
                        className="btn btn-small"
                        disabled={tab === "reasoning"}
                        onClick={() => setTab("reasoning")}
                      >
                        Reasoning
                      </button>
                    )}
                    {detail.response_raw != null && (
                      <button className="btn btn-small" disabled={tab === "raw"} onClick={() => setTab("raw")}>
                        Raw
                      </button>
                    )}
                  </>
                )}
              </div>
              {tab === "content" && <CopyPre text={prettyIfJson(detail.response)} />}
              {tab === "reasoning" && detail.reasoning != null && <CopyPre text={detail.reasoning} />}
              {tab === "raw" && detail.response_raw != null && (
                <CopyPre text={JSON.stringify(detail.response_raw, null, 2)} />
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function LlmCallsPanel({ episodeId }: { episodeId: string }) {
  const [open, setOpen] = useState(false);
  const [calls, setCalls] = useState<LlmCallSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !calls && !loading) {
      setLoading(true);
      try {
        setCalls(await api.getLlmCalls(episodeId));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    }
  };

  return (
    <section className="panel collapsible">
      <button className="panel-toggle" onClick={() => void toggle()} aria-expanded={open}>
        <span className={`caret${open ? " caret-open" : ""}`}><CaretRightIcon size={14} weight="bold" /></span> LLM calls
      </button>
      {open && (
        <div className="panel-body">
          {loading && <div className="loading loading-subtle">Loading…</div>}
          {error && <div className="inline-error">{error}</div>}
          {calls && calls.length === 0 && <div className="empty-small">No LLM calls recorded for this episode.</div>}
          {calls && calls.length > 0 && (
            <div className="jobs-list">
              {calls.map((c) => (
                <LlmCallRow key={c.id} call={c} />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// Tolerant extraction of segment lines from a raw provider payload: flat
// OpenAI/Groq shape ({segments: [...]}) or whisperx's nested one
// ({segments: {segments: [...]}}). Returns null when neither matches.
function extractRawSegments(payload: unknown): { start: number; end: number; text: string }[] | null {
  if (!payload || typeof payload !== "object") return null;
  let segs = (payload as { segments?: unknown }).segments;
  if (segs && !Array.isArray(segs) && typeof segs === "object") {
    segs = (segs as { segments?: unknown }).segments;
  }
  if (!Array.isArray(segs)) return null;
  const out: { start: number; end: number; text: string }[] = [];
  for (const s of segs) {
    if (s && typeof s === "object" && typeof (s as { text?: unknown }).text === "string") {
      const seg = s as { text: string; start?: unknown; end?: unknown };
      out.push({ start: Number(seg.start ?? 0), end: Number(seg.end ?? 0), text: seg.text });
    }
  }
  return out.length > 0 ? out : null;
}

function RawTranscriptionCallRow({ call }: { call: RawTranscriptionCall }) {
  const [open, setOpen] = useState(false);
  const [showJson, setShowJson] = useState(false);
  const segs = extractRawSegments(call.payload);
  const multi = call.parts > 1;
  return (
    <div className="job-row">
      <button className="job-summary" onClick={() => setOpen((o) => !o)}>
        <span className={`caret${open ? " caret-open" : ""}`}><CaretRightIcon size={14} weight="bold" /></span>
        <span className="job-step">{multi ? `part ${call.part}/${call.parts}` : call.model}</span>
        <span className="job-times">
          {call.recorded_at && `${formatDateTime(call.recorded_at)} · `}
          {multi && `${call.model} · `}
          {multi && call.owned_start_s != null && call.owned_end_s != null
            ? `covers ${formatTimestamp(call.owned_start_s)}–${formatTimestamp(call.owned_end_s)} (audio from ${formatTimestamp(call.offset_s)}) · `
            : ""}
          {segs ? `${segs.length} segments` : "unrecognized payload shape"}
        </span>
        <span className="job-elapsed">{call.elapsed_s.toFixed(1)}s</span>
      </button>
      {open && (
        <div className="job-detail">
          {multi && (
            <p className="field-hint">
              Timestamps below are relative to this part's audio slice. Add {formatTimestamp(call.offset_s)} for
              episode time. Overlap outside the covered range is dropped when parts are stitched.
            </p>
          )}
          <div className="field-label" style={{ margin: "10px 0 4px" }}>
            <button className="btn btn-small" onClick={() => setShowJson((v) => !v)}>
              {showJson ? "Show segments" : "Show raw JSON"}
            </button>
          </div>
          {showJson || !segs ? (
            <CopyPre text={JSON.stringify(call.payload, null, 2)} />
          ) : (
            <div className="copy-wrap">
              <CopyButton
                small
                className="copy-overlay"
                text={() =>
                  segs
                    .map((s) => `[${formatTimestamp(s.start)}–${formatTimestamp(s.end)}] ${s.text}`)
                    .join("\n")
                }
              />
              <div className="transcript">
                {segs.map((s, i) => (
                  <div key={i} className="transcript-line">
                    <span className="transcript-time">
                      [{formatTimestamp(s.start)}–{formatTimestamp(s.end)}]
                    </span>{" "}
                    {s.text}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function RawTranscriptionPanel({ episodeId }: { episodeId: string }) {
  const [open, setOpen] = useState(false);
  const [raw, setRaw] = useState<RawTranscriptOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !raw && !loading) {
      setLoading(true);
      try {
        setRaw(await api.getRawTranscript(episodeId));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    }
  };

  return (
    <section className="panel collapsible">
      <button className="panel-toggle" onClick={() => void toggle()} aria-expanded={open}>
        <span className={`caret${open ? " caret-open" : ""}`}><CaretRightIcon size={14} weight="bold" /></span> Transcription calls
      </button>
      {open && (
        <div className="panel-body">
          <p className="field-hint">
            Raw provider responses, one per API call, kept across reprocesses (oldest first), before sentence
            splitting, word-timestamp merging, and cue interleaving. The Transcript panel shows the cleaned, final
            version of the latest run.
          </p>
          {loading && <div className="loading loading-subtle">Loading…</div>}
          {error && <div className="inline-error">{error}</div>}
          {raw && raw.calls.length === 0 && <div className="empty-small">No transcription calls recorded.</div>}
          {raw && raw.calls.length > 0 && (
            <div className="jobs-list">
              {raw.calls.map((c, i) => (
                <RawTranscriptionCallRow key={i} call={c} />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

const CUE_LABELS: Record<string, string> = {
  silence: "SILENCE",
  noenergy: "SILENCE",
  music: "MUSIC",
  noise: "NOISE",
};

function TranscriptViewer({
  episodeId,
  segments,
  hasCues,
  onMarkAd,
  jumpRequest,
}: {
  episodeId: string;
  segments: SegmentOut[];
  hasCues?: boolean;
  onMarkAd?: (startS: number, endS: number, notAd: boolean) => Promise<void>;
  jumpRequest?: { t: number; nonce: number } | null;
}) {
  const [open, setOpen] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptOut | null>(null);
  const [cues, setCues] = useState<CueOut[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const pendingJumpRef = useRef<number | null>(null);
  const flashTimer = useRef<number | undefined>(undefined);
  const [flashIdx, setFlashIdx] = useState<number | null>(null);
  // Position in the removed-block list for the prev/next ad buttons.
  const [adIdx, setAdIdx] = useState<number | null>(null);
  // Two-click range selection for "mark as ad": first click anchors, second completes.
  const [anchor, setAnchor] = useState<number | null>(null);
  const [selEnd, setSelEnd] = useState<number | null>(null);
  const [marking, setMarking] = useState(false);

  const clickLine = (i: number) => {
    if (!onMarkAd) return;
    if (anchor === null || selEnd !== null) {
      setAnchor(i);
      setSelEnd(null);
    } else {
      setSelEnd(i);
    }
  };
  const selRange: [number, number] | null =
    anchor !== null ? [Math.min(anchor, selEnd ?? anchor), Math.max(anchor, selEnd ?? anchor)] : null;
  const clearSelection = () => {
    setAnchor(null);
    setSelEnd(null);
  };
  const confirmMark = async (notAd: boolean) => {
    if (!transcript || !selRange || !onMarkAd) return;
    setMarking(true);
    try {
      await onMarkAd(transcript.segments[selRange[0]].start, transcript.segments[selRange[1]].end, notAd);
      clearSelection();
    } finally {
      setMarking(false);
    }
  };

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !transcript && !loading) {
      setLoading(true);
      setError(null);
      try {
        setTranscript(await api.getTranscript(episodeId));
        if (hasCues) {
          try {
            setCues(await api.getCues(episodeId));
          } catch {
            /* cues are decorative, ignore */
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    }
  };

  const removed = segments.filter((s) => !s.kept);
  const adBlocks = [...removed].sort((a, b) => a.start_s - b.start_s);

  // First transcript line still in play at time t (ad block starts are snapped
  // to line boundaries, so this lands on the block's first line).
  const lineIndexForTime = (t: number): number => {
    if (!transcript) return 0;
    const i = transcript.segments.findIndex((seg) => seg.end > t + 0.05);
    return i === -1 ? transcript.segments.length - 1 : i;
  };
  const jumpToLine = (i: number) => {
    const el = listRef.current?.querySelector(`[data-line="${i}"]`);
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
    setFlashIdx(i);
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlashIdx(null), 1600);
  };
  useEffect(() => () => window.clearTimeout(flashTimer.current), []);

  // A jump from the segments table opens the panel first if needed. The actual
  // scroll waits below until the transcript has loaded and rendered.
  useEffect(() => {
    if (!jumpRequest) return;
    pendingJumpRef.current = jumpRequest.t;
    if (!open) void toggle();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jumpRequest]);
  useEffect(() => {
    if (pendingJumpRef.current == null || !open || !transcript) return;
    const t = pendingJumpRef.current;
    pendingJumpRef.current = null;
    jumpToLine(lineIndexForTime(t));
    // Keep the prev/next ad buttons in step: if the jump target is (inside) a
    // removed block, make it the current one. Otherwise reset the counter.
    const i = adBlocks.findIndex((b) => t >= b.start_s - 0.05 && t < b.end_s);
    setAdIdx(i === -1 ? null : i);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jumpRequest, open, transcript]);

  const stepAd = (dir: 1 | -1) => {
    if (adBlocks.length === 0) return;
    const next =
      adIdx === null
        ? dir === 1
          ? 0
          : adBlocks.length - 1
        : (adIdx + dir + adBlocks.length) % adBlocks.length;
    setAdIdx(next);
    jumpToLine(lineIndexForTime(adBlocks[next].start_s));
  };
  // Highlight only meaningful overlap. A sub-second sliver at a snapped cut
  // boundary shouldn't paint a whole line red.
  const inAd = (start: number, end: number) =>
    removed.some((s) => {
      const overlap = Math.min(end, s.end_s) - Math.max(start, s.start_s);
      return overlap > Math.min(0.5, (end - start) / 2);
    });
  // For a line only partially inside a removed block, highlight just the
  // affected words (a word counts as removed when its midpoint falls inside
  // the block). Returns null when the whole line should be painted instead:
  // no word timestamps, every word affected, or none (overlap sits in silence).
  const removedWordFlags = (words: TranscriptWord[] | null | undefined): boolean[] | null => {
    if (!words || words.length === 0) return null;
    const flags = words.map((w) => {
      const mid = (w.start + w.end) / 2;
      return removed.some((s) => mid >= s.start_s && mid < s.end_s);
    });
    if (flags.every(Boolean) || !flags.some(Boolean)) return null;
    return flags;
  };
  // Consecutive same-state words grouped into runs, so a partial line renders
  // as a handful of spans instead of one per word.
  const wordRuns = (words: TranscriptWord[], flags: boolean[]) => {
    const runs: { text: string; ad: boolean; start: number; end: number }[] = [];
    words.forEach((w, i) => {
      const last = runs[runs.length - 1];
      if (last && last.ad === flags[i]) {
        last.text += ` ${w.text}`;
        last.end = w.end;
      } else {
        runs.push({ text: w.text, ad: flags[i], start: w.start, end: w.end });
      }
    });
    return runs;
  };

  return (
    <section className="panel collapsible">
      <button className="panel-toggle" onClick={() => void toggle()} aria-expanded={open}>
        <span className={`caret${open ? " caret-open" : ""}`}><CaretRightIcon size={14} weight="bold" /></span> Transcript
      </button>
      {open && (
        <div className="panel-body">
          {loading && <div className="loading">Loading transcript…</div>}
          {error && <div className="inline-error">{error}</div>}
          {transcript && (
            <>
              <div className="transcript-meta">
                {transcript.language && <span>Language: {transcript.language}</span>}
                {transcript.duration != null && <span>Duration: {formatDuration(transcript.duration)}</span>}
                {adBlocks.length > 0 && (
                  <span className="ad-nav">
                    <button className="btn btn-small" title="Jump to previous ad block" onClick={() => stepAd(-1)}>
                      <CaretLeftIcon size={12} weight="bold" /> Prev ad
                    </button>
                    <button className="btn btn-small" title="Jump to next ad block" onClick={() => stepAd(1)}>
                      Next ad <CaretRightIcon size={12} weight="bold" />
                    </button>
                    <span>
                      {adIdx !== null ? `${adIdx + 1} / ${adBlocks.length}` : `${adBlocks.length} ad block${adBlocks.length > 1 ? "s" : ""}`}
                    </span>
                  </span>
                )}
                {onMarkAd && (
                  <span>
                    {selRange === null
                      ? "Click a line to start marking a missed ad."
                      : selEnd === null
                        ? "Now click the last line of the ad."
                        : ""}
                  </span>
                )}
              </div>
              {selRange !== null && selEnd !== null && transcript && (
                <div className="mark-bar">
                  {(() => {
                    const startS = transcript.segments[selRange[0]].start;
                    const endS = transcript.segments[selRange[1]].end;
                    const EPS = 0.5; // snapped cut boundaries vs raw line times drift by fractions of a second
                    const contained = removed.some((s) => s.start_s <= startS + EPS && s.end_s >= endS - EPS);
                    const overlapping = removed.some(
                      (s) => Math.min(endS, s.end_s) - Math.max(startS, s.start_s) > EPS
                    );
                    const range = `${formatTimestamp(startS)}–${formatTimestamp(endS)}`;
                    return (
                      <>
                        <span>
                          {contained
                            ? `${range} is inside a detected ad block.`
                            : overlapping
                              ? `${range} overlaps a detected ad block.`
                              : `Mark ${range} as a missed ad?`}
                        </span>
                        {!contained && (
                          <button
                            className="btn btn-small btn-primary"
                            disabled={marking}
                            onClick={() => void confirmMark(false)}
                          >
                            {marking ? "Marking…" : overlapping ? "Extend: mark as ad" : "Mark as ad"}
                          </button>
                        )}
                        {overlapping && (
                          <button
                            className={`btn btn-small${contained ? " btn-primary" : ""}`}
                            disabled={marking}
                            onClick={() => void confirmMark(true)}
                          >
                            {marking ? "Marking…" : "Mark as not-an-ad"}
                          </button>
                        )}
                        <button className="btn btn-small" disabled={marking} onClick={clearSelection}>
                          Cancel
                        </button>
                      </>
                    );
                  })()}
                </div>
              )}
              <div className="copy-wrap">
                <CopyButton
                  small
                  className="copy-overlay"
                  text={() =>
                    transcript.segments
                      .map(
                        (s) =>
                          `[${formatTimestamp(s.start)}–${formatTimestamp(s.end)}]${s.speaker ? ` ${s.speaker}:` : ""} ${s.text}`
                      )
                      .join("\n")
                  }
                />
                <div className="transcript" ref={listRef}>
                {(() => {
                  // Cue rows are interleaved by time but stay non-clickable and
                  // outside the index-based mark-ad selection, which addresses
                  // transcript.segments positions only.
                  const cueRow = (c: CueOut, key: string) => (
                    <div key={key} className={`transcript-line transcript-cue${inAd(c.start, c.end) ? " transcript-ad" : ""}`}>
                      <span className="transcript-time">
                        [{formatTimestamp(c.start)}–{formatTimestamp(c.end)}]
                      </span>{" "}
                      [{CUE_LABELS[c.kind] ?? c.kind.toUpperCase()}] ({(c.end - c.start).toFixed(1)}s)
                    </div>
                  );
                  const rows: ReactNode[] = [];
                  let ci = 0;
                  transcript.segments.forEach((seg, i) => {
                    while (cues && ci < cues.length && cues[ci].start <= seg.start) {
                      rows.push(cueRow(cues[ci], `c${ci}`));
                      ci++;
                    }
                    const selected = selRange !== null && i >= selRange[0] && i <= selRange[1];
                    const hit = inAd(seg.start, seg.end);
                    const flags = hit ? removedWordFlags(seg.words) : null;
                    const runs = flags ? wordRuns(seg.words!, flags) : null;
                    const cls = `transcript-line${hit && !flags ? " transcript-ad" : ""}${
                      selected ? " transcript-selected" : ""
                    }${onMarkAd ? " transcript-clickable" : ""}${flashIdx === i ? " transcript-flash" : ""}`;
                    rows.push(
                      <div key={i} data-line={i} className={cls} onClick={() => clickLine(i)}>
                        <span className="transcript-time">
                          [{formatTimestamp(seg.start)}–{formatTimestamp(seg.end)}]
                        </span>{" "}
                        {seg.speaker ? <span className="transcript-speaker">{seg.speaker}:</span> : null}{" "}
                        {runs
                          ? runs.map((run, j) => (
                              <span
                                key={j}
                                className={run.ad ? "transcript-ad-words" : undefined}
                                title={run.ad ? `Cut ${formatTimestamp(run.start)}–${formatTimestamp(run.end)}` : undefined}
                              >
                                {run.text}
                                {j < runs.length - 1 ? " " : ""}
                              </span>
                            ))
                          : seg.text}
                      </div>
                    );
                  });
                  while (cues && ci < cues.length) {
                    rows.push(cueRow(cues[ci], `c${ci}`));
                    ci++;
                  }
                  return rows;
                })()}
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}

export function EpisodeDetailPage() {
  const { id } = useParams<{ id: string }>();
  const processing = useProcessingActive();
  const { toastError, toastSuccess } = useToasts();
  const [busy, setBusy] = useState(false);
  // nonce so re-clicking the same segment re-triggers the transcript jump
  const [jump, setJump] = useState<{ t: number; nonce: number } | null>(null);
  const jumpToTranscript = (t: number) => setJump((j) => ({ t, nonce: (j?.nonce ?? 0) + 1 }));

  const { data: ep, error, loading, reload } = useAsyncData<EpisodeDetailOut>(
    () => api.getEpisode(id!),
    [id],
    { intervalMs: processing ? 5000 : 0 }
  );

  const run = async (label: string, fn: () => Promise<{ ok: boolean }>) => {
    setBusy(true);
    try {
      await fn();
      toastSuccess(`${label} queued`);
      reload();
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const dismiss = async () => {
    setBusy(true);
    try {
      await api.dismissEpisode(id!);
      toastSuccess("Episode dismissed (moved to skipped)");
      reload();
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const correctSegment = async (segmentId: number, kept: boolean) => {
    setBusy(true);
    try {
      await api.patchSegment(segmentId, kept);
      toastSuccess(kept ? "Marked as not-an-ad, feeds the next distill" : "Correction undone");
      reload();
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const removeManualSegment = async (segmentId: number) => {
    setBusy(true);
    try {
      await api.deleteSegment(segmentId);
      toastSuccess("Manual segment removed");
      reload();
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const markRange = async (startS: number, endS: number, notAd: boolean) => {
    try {
      await api.addSegment(String(ep!.id), { start_s: startS, end_s: endS, not_ad: notAd });
      toastSuccess(
        notAd
          ? `Marked ${formatTimestamp(startS)}–${formatTimestamp(endS)} as not-an-ad`
          : `Marked ${formatTimestamp(startS)}–${formatTimestamp(endS)} as a missed ad`
      );
      reload();
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    }
  };

  if (error && !ep) {
    return (
      <div className="page">
        <div className="inline-error">{error}</div>
        <Link to="/" className="btn">
          <ArrowLeftIcon size={15} /> Back to podcasts
        </Link>
      </div>
    );
  }
  if (!ep) return <div className="page loading">Loading episode…</div>;

  const canProcess = ["skipped", "discovered", "expired"].includes(ep.status);
  const removedSegments = ep.segments.filter((s) => !s.kept);

  return (
    <div className="page">
      <Link to={`/podcasts/${ep.feed_id}`} className="back-link">
        <ArrowLeftIcon size={14} /> Back to podcast
      </Link>

      <header className="episode-header">
        <h1>{ep.title}</h1>
        <div className="detail-stats">
          <StatusChip status={ep.status} />
          <span>Published {formatDate(ep.published_at)}</span>
          {ep.retry_count > 0 && <span>{ep.retry_count} retries</span>}
        </div>
        {ep.status === "failed" && ep.status_detail && (
          <div className="inline-error">{ep.status_detail}</div>
        )}
        <div className="stat-row">
          <div className="stat">
            <span className="stat-label">Original</span>
            <span className="stat-value">{formatDuration(ep.duration_s)}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Processed</span>
            <span className="stat-value">{formatDuration(ep.processed_duration_s)}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Ads removed</span>
            <span className="stat-value stat-accent">{formatDuration(ep.ad_seconds_removed)}</span>
          </div>
        </div>
        <div className="detail-actions">
          {canProcess && (
            <button className="btn btn-primary" disabled={busy} onClick={() => void run("Process", () => api.processEpisode(ep.id))}>
              Process
            </button>
          )}
          {ep.status === "failed" && (
            <>
              <button className="btn btn-primary" disabled={busy} onClick={() => void run("Retry", () => api.retryEpisode(ep.id))}>
                Retry
              </button>
              <button className="btn" disabled={busy} onClick={() => void dismiss()} title="Give up on this episode: mark it skipped so it stops alerting">
                Dismiss
              </button>
            </>
          )}
          {ep.status === "processed" && (
            <a className="btn" href={`/api/episodes/${ep.id}/download`} download>
              Download MP3
            </a>
          )}
          {ep.has_original && (
            <a className="btn" href={`/api/episodes/${ep.id}/download-original`} download title="Download the unmodified source audio (kept originals)">
              Download original
            </a>
          )}
          {ep.status === "processed" && (
            <ReprocessMenu
              disabled={busy}
              onSelect={(step) => {
                const label = REPROCESS_OPTIONS.find((o) => o.step === step)?.label ?? "Reprocess";
                void run(label, () => api.reprocessEpisode(ep.id, step));
              }}
            />
          )}
        </div>
      </header>

      {ep.description_html && (
        <section className="panel">
          <EpisodeDescription html={ep.description_html} />
        </section>
      )}

      <section className="panel">
        <h2 className="panel-title">Jobs</h2>
        {ep.jobs.length === 0 ? (
          <div className="empty-small">No jobs yet.</div>
        ) : (
          <div className="jobs-list">
            {ep.jobs.map((j) => (
              <JobRow key={j.id} job={j} />
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <h2 className="panel-title">Detected segments</h2>
        <p className="field-hint">
          Corrections are training signal: they feed the podcast's "Distill hints" step but never re-cut existing
          audio.
        </p>
        {ep.segments.length === 0 ? (
          <div className="empty-small">No segments detected.</div>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Range</th>
                  <th>Duration</th>
                  <th>Category</th>
                  <th>Confidence</th>
                  <th>Reason</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {ep.segments.map((s) => (
                  <tr key={s.id} className={s.kept ? "segment-kept" : undefined}>
                    <td className="cell-nowrap">
                      {ep.has_transcript ? (
                        <button
                          className="link-btn"
                          title="Show this segment in the transcript"
                          onClick={() => jumpToTranscript(s.start_s)}
                        >
                          {formatTimestamp(s.start_s)} – {formatTimestamp(s.end_s)}
                        </button>
                      ) : (
                        <>
                          {formatTimestamp(s.start_s)} – {formatTimestamp(s.end_s)}
                        </>
                      )}
                    </td>
                    <td className="cell-nowrap">{formatDuration(s.end_s - s.start_s)}</td>
                    <td>
                      <span className={`chip chip-tiny ${CATEGORY_CLASS[s.category] ?? "chip-gray"}`}>
                        {s.category}
                      </span>
                      {s.source === "manual" && <span className="chip chip-tiny chip-blue">manual</span>}
                      {s.kept && <span className="chip chip-tiny chip-dim">not an ad</span>}
                    </td>
                    <td className="cell-nowrap">{Math.round(s.confidence * 100)}%</td>
                    <td className="cell-reason">{s.reason ?? "-"}</td>
                    <td className="cell-action">
                      {s.source === "llm" ? (
                        <button
                          className="btn btn-small"
                          disabled={busy}
                          onClick={() => void correctSegment(s.id, !s.kept)}
                        >
                          {s.kept ? "Undo" : "Not an ad"}
                        </button>
                      ) : (
                        <button
                          className="btn btn-small"
                          disabled={busy}
                          onClick={() => void removeManualSegment(s.id)}
                        >
                          Remove
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {ep.has_raw_transcript && <RawTranscriptionPanel episodeId={String(ep.id)} />}

      <LlmCallsPanel episodeId={String(ep.id)} />

      {ep.has_transcript && (
        <TranscriptViewer
          episodeId={String(ep.id)}
          segments={removedSegments}
          hasCues={ep.has_cues}
          onMarkAd={markRange}
          jumpRequest={jump}
        />
      )}
      {loading && <div className="loading loading-subtle">Refreshing…</div>}
    </div>
  );
}
