// API types and typed fetch helpers for the hushcast backend.

export type EpisodeStatus =
  | "discovered"
  | "skipped"
  | "queued"
  | "downloading"
  | "transcribing"
  | "detecting"
  | "cutting"
  | "processed"
  | "failed"
  | "expired";

export const ALL_STATUSES: EpisodeStatus[] = [
  "discovered",
  "skipped",
  "queued",
  "downloading",
  "transcribing",
  "detecting",
  "cutting",
  "processed",
  "failed",
  "expired",
];

export const ACTIVE_STATUSES: EpisodeStatus[] = [
  "queued",
  "downloading",
  "transcribing",
  "detecting",
  "cutting",
];

export interface FeedOut {
  id: number;
  slug: string;
  source_url: string;
  title: string;
  image_url: string | null;
  description: string | null;
  enabled: boolean;
  whitelisted: boolean;
  detection_hints: string | null;
  learned_hints: string | null;
  hints_distilled_at: string | null;
  last_polled_at: string | null;
  poll_error: string | null;
  episode_count: number;
  processed_count: number;
  failed_count: number;
  new_corrections: number;
  subscribe_url: string;
}

export interface DistillProposal {
  feed_hints: string;
  global_hints: string;
  corrections_used: number;
  current_feed_hints: string;
  current_global_hints: string;
}

export interface EpisodeOut {
  id: number;
  feed_id: number;
  guid: string;
  title: string;
  published_at: string | null;
  status: EpisodeStatus;
  status_detail: string | null;
  retry_count: number;
  duration_s: number | null;
  processed_duration_s: number | null;
  processed_bytes: number | null;
  ad_seconds_removed: number | null;
  updated_at: string | null;
}

export interface SegmentOut {
  id: number;
  start_s: number;
  end_s: number;
  category: string;
  confidence: number;
  reason: string | null;
  kept: boolean;
  source: "llm" | "manual";
  corrected_at: string | null;
}

export interface JobOut {
  id: number;
  step: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  log_text: string | null;
  metrics: Record<string, unknown> | null;
}

export interface EpisodeDetailOut extends EpisodeOut {
  description_html: string | null;
  source_enclosure_url: string | null;
  segments: SegmentOut[];
  jobs: JobOut[];
  has_transcript: boolean;
  has_raw_transcript: boolean;
  has_cues: boolean;
  has_original: boolean;
}

export interface EpisodeListOut {
  total: number;
  page: number;
  page_size: number;
  items: EpisodeOut[];
}

export interface TranscriptWord {
  text: string;
  start: number;
  end: number;
}

export interface TranscriptSegment {
  text: string;
  start: number;
  end: number;
  speaker?: string | null;
  words?: TranscriptWord[];
}

export interface TranscriptOut {
  language: string | null;
  duration: number | null;
  segments: TranscriptSegment[];
}

export interface RawTranscriptionCall {
  recorded_at?: string | null;
  part: number;
  parts: number;
  offset_s: number;
  owned_start_s: number | null;
  owned_end_s: number | null;
  elapsed_s: number;
  url: string;
  model: string;
  payload: unknown;
}

export interface RawTranscriptOut {
  calls: RawTranscriptionCall[];
}

export interface CueOut {
  start: number;
  end: number;
  kind: "silence" | "music" | "noise" | "noenergy";
}

export interface SystemStatus {
  version: string;
  queue_depth: number;
  active: { episode_id: number; step: string }[];
  episode_counts: Partial<Record<EpisodeStatus, number>>;
  processing: boolean;
  alert_count: number;
}

export type AuthMode = "disabled" | "setup" | "login";

export interface AuthStatus {
  mode: AuthMode;
  authenticated: boolean;
  username: string | null;
}

/** Fired when any API call (outside /api/auth) gets a 401: session expired. */
export const UNAUTHENTICATED_EVENT = "hushcast:unauthenticated";

export interface SystemInfo {
  version: string;
  python_version: string;
  platform: string;
  ffmpeg_version: string | null;
  started_at: string;
  config_dir: string;
  data_dir: string;
  database_path: string;
  public_url: string;
  auth_mode: AuthMode;
  log_level: string;
  log_level_env_override: string | null;
  python_executable: string;
  next_poll_at: string | null;
  next_cleanup_at: string | null;
}

export interface SystemStorage {
  volumes: { name: string; path: string; total_bytes: number; used_bytes: number; free_bytes: number }[];
  breakdown: { name: string; bytes: number }[];
}

export interface SystemAlert {
  severity: "error" | "warning";
  kind: string;
  message: string;
  link: string | null;
  feed_id?: number;
}

export interface LogRecord {
  ts: string;
  level: string;
  logger: string;
  message: string;
}

export interface LlmModelStats {
  provider: string;
  model: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  elapsed_s: number;
}

export interface TranscriptionModelStats {
  provider: string;
  model: string;
  calls: number;
  audio_s: number;
  elapsed_s: number;
}

export interface SystemStats {
  episodes_processed: number;
  duration_processed_s: number;
  ad_seconds_removed: number;
  ad_pct: number | null;
  ad_segments_cut: number;
  corrections: number;
  bytes_saved: number;
  top_feeds: {
    feed_id: number;
    title: string;
    episodes: number;
    duration_s: number;
    ad_seconds: number;
    ad_pct: number;
  }[];
  llm: {
    total: { calls: number; prompt_tokens: number; completion_tokens: number; elapsed_s: number };
    by_model: LlmModelStats[];
  };
  transcription: {
    total: { calls: number; audio_s: number; elapsed_s: number };
    by_model: TranscriptionModelStats[];
  };
}

export interface Settings {
  transcription_base_url: string;
  transcription_api_key: string;
  transcription_model: string;
  transcription_word_timestamps: boolean;
  transcription_diarize: boolean;
  transcription_max_upload_mb: number;
  transcription_timeout_s: number;
  transcription_extra_params: Record<string, unknown>;
  llm_base_url: string;
  llm_api_key: string;
  llm_model: string;
  llm_temperature: number;
  llm_context_budget_tokens: number;
  llm_max_tokens: number;
  llm_timeout_s: number;
  detection_prompt: string;
  global_learned_hints: string;
  poll_interval_minutes: number;
  max_concurrent_episodes: number;
  max_episode_retries: number;
  keep_originals: boolean;
  keep_originals_days: number;
  min_confidence: number;
  min_duration_s: number;
  merge_gap_s: number;
  snap_tolerance_s: number;
  refine_window_s: number;
  refine_min_gap_s: number;
  cue_provider: "off" | "silence" | "remote";
  cue_remote_base_url: string;
  cue_remote_timeout_s: number;
  cue_silence_noise_db: number;
  cue_min_silence_s: number;
  cue_prompt_annotations: boolean;
  cue_min_prompt_duration_s: number;
  cue_bridge_max_gap_s: number;
  cue_edge_max_extension_s: number;
  max_kept_episodes: number;
  mp3_quality: string;
  log_level: string;
  feed_token: string;
}

export interface LlmCallSummary {
  id: number;
  created_at: string;
  url: string;
  model: string;
  provider: string | null;
  status_code: number | null;
  finish_reason: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  reasoning_tokens: number | null;
  elapsed_s: number;
  request_chars: number;
  response_chars: number;
  event_count: number;
}

export interface LlmCallDetail extends Omit<LlmCallSummary, "request_chars" | "response_chars" | "event_count"> {
  messages: { role: string; content: string }[];
  params: Record<string, unknown> | null;
  response: string;
  response_raw: unknown;
  reasoning: string | null;
  events: string[];
}

export interface TestResult {
  ok: boolean;
  message: string;
}

export interface FeedCreate {
  url: string;
  whitelisted?: boolean;
  detection_hints?: string;
}

export interface FeedPatch {
  enabled?: boolean;
  whitelisted?: boolean;
  detection_hints?: string | null;
  learned_hints?: string | null;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
      ...init,
    });
  } catch {
    throw new ApiError(0, "Network error: is the server running?");
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
      else if (body && body.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    if (res.status === 401 && !path.startsWith("/api/auth/")) {
      window.dispatchEvent(new Event(UNAUTHENTICATED_EVENT));
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  authStatus: () => get<AuthStatus>("/api/auth/status"),
  authSetup: (body: { username: string; password: string }) => post<AuthStatus>("/api/auth/setup", body),
  authLogin: (body: { username: string; password: string }) => post<AuthStatus>("/api/auth/login", body),
  authLogout: () => post<{ ok: boolean }>("/api/auth/logout"),
  authChange: (body: { current_password: string; username?: string; new_password?: string }) =>
    post<AuthStatus>("/api/auth/change", body),
  listFeeds: () => get<FeedOut[]>("/api/feeds"),
  getFeed: (id: number | string) => get<FeedOut>(`/api/feeds/${id}`),
  createFeed: (body: FeedCreate) => post<FeedOut>("/api/feeds", body),
  patchFeed: (id: number | string, body: FeedPatch) =>
    request<FeedOut>(`/api/feeds/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteFeed: (id: number | string) => request<void>(`/api/feeds/${id}`, { method: "DELETE" }),
  pollFeed: (id: number | string) => post<{ ok: boolean }>(`/api/feeds/${id}/poll`),
  listEpisodes: (feedId: number | string, params: { status?: string; page?: number; page_size?: number }) => {
    const q = new URLSearchParams();
    if (params.status) q.set("status", params.status);
    if (params.page) q.set("page", String(params.page));
    if (params.page_size) q.set("page_size", String(params.page_size));
    const qs = q.toString();
    return get<EpisodeListOut>(`/api/feeds/${feedId}/episodes${qs ? `?${qs}` : ""}`);
  },
  getEpisode: (id: number | string) => get<EpisodeDetailOut>(`/api/episodes/${id}`),
  patchSegment: (id: number, kept: boolean) =>
    request<SegmentOut>(`/api/segments/${id}`, { method: "PATCH", body: JSON.stringify({ kept }) }),
  addSegment: (
    episodeId: number | string,
    body: { start_s: number; end_s: number; category?: string; not_ad?: boolean }
  ) => post<SegmentOut>(`/api/episodes/${episodeId}/segments`, body),
  deleteSegment: (id: number) => request<void>(`/api/segments/${id}`, { method: "DELETE" }),
  distillFeed: (feedId: number | string) => post<DistillProposal>(`/api/feeds/${feedId}/distill`),
  getLlmCalls: (episodeId: number | string) => get<LlmCallSummary[]>(`/api/episodes/${episodeId}/llm-calls`),
  getLlmCall: (callId: number) => get<LlmCallDetail>(`/api/llm-calls/${callId}`),
  getTranscript: (id: number | string) => get<TranscriptOut>(`/api/episodes/${id}/transcript`),
  getRawTranscript: (id: number | string) => get<RawTranscriptOut>(`/api/episodes/${id}/transcript/raw`),
  getCues: (id: number | string) => get<CueOut[]>(`/api/episodes/${id}/cues`),
  processEpisode: (id: number | string) => post<{ ok: boolean }>(`/api/episodes/${id}/process`),
  retryEpisode: (id: number | string) => post<{ ok: boolean }>(`/api/episodes/${id}/retry`),
  dismissEpisode: (id: number | string) => post<{ ok: boolean }>(`/api/episodes/${id}/dismiss`),
  dismissAllFailed: (feedId?: number) =>
    post<{ dismissed: number }>(`/api/episodes/dismiss-failed${feedId != null ? `?feed_id=${feedId}` : ""}`),
  reprocessEpisode: (id: number | string, fromStep: string) =>
    post<{ ok: boolean }>(`/api/episodes/${id}/reprocess?from_step=${encodeURIComponent(fromStep)}`),
  getSettings: () => get<Settings>("/api/settings"),
  putSettings: (changed: Partial<Settings>) =>
    request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(changed) }),
  testTranscription: (overrides?: Partial<Settings>) =>
    post<TestResult>("/api/settings/test/transcription", overrides ?? {}),
  testLlm: (overrides?: Partial<Settings>) =>
    post<TestResult>("/api/settings/test/llm", overrides ?? {}),
  regenerateToken: () => post<{ feed_token: string }>("/api/settings/regenerate-token"),
  systemStatus: () => get<SystemStatus>("/api/system/status"),
  systemInfo: () => get<SystemInfo>("/api/system/info"),
  systemStorage: () => get<SystemStorage>("/api/system/storage"),
  systemAlerts: () => get<SystemAlert[]>("/api/system/alerts"),
  systemStats: () => get<SystemStats>("/api/system/stats"),
  systemLogs: (level: string, limit = 500) =>
    get<{ records: LogRecord[]; capacity: number }>(
      `/api/system/logs?level=${encodeURIComponent(level)}&limit=${limit}`
    ),
};
