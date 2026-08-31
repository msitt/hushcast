import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { CheckIcon, XIcon } from "@phosphor-icons/react";
import { api, type Settings, type TestResult } from "../api/client";
import { useAsyncData } from "../hooks";
import { useAuth } from "../components/AuthGate";
import { useToasts } from "../components/Toasts";
import { ConfirmDialog } from "../components/Modal";
import { CopyButton } from "../components/CopyButton";

const SECRET_MASK = "••••••••";

type Draft = {
  [K in keyof Settings]: Settings[K] extends boolean ? boolean : string;
};

function toDraft(s: Settings): Draft {
  const d = {} as Record<string, string | boolean>;
  for (const [k, v] of Object.entries(s)) {
    if (typeof v === "boolean") d[k] = v;
    else if (k === "transcription_extra_params") d[k] = JSON.stringify(v ?? {}, null, 2);
    else d[k] = v == null ? "" : String(v);
  }
  return d as Draft;
}

const NUMBER_KEYS: (keyof Settings)[] = [
  "transcription_max_upload_mb",
  "transcription_timeout_s",
  "llm_temperature",
  "llm_context_budget_tokens",
  "llm_max_tokens",
  "llm_timeout_s",
  "poll_interval_minutes",
  "max_concurrent_episodes",
  "max_episode_retries",
  "keep_originals_days",
  "min_confidence",
  "min_duration_s",
  "merge_gap_s",
  "snap_tolerance_s",
  "refine_window_s",
  "refine_min_gap_s",
  "cue_remote_timeout_s",
  "cue_silence_noise_db",
  "cue_min_silence_s",
  "cue_min_prompt_duration_s",
  "cue_bridge_max_gap_s",
  "cue_edge_max_extension_s",
  "max_kept_episodes",
  "mp3_quality",
];

const SECRET_KEYS: (keyof Settings)[] = ["transcription_api_key", "llm_api_key"];

function AuthSection() {
  const auth = useAuth();
  const { toastError, toastSuccess } = useToasts();
  const [username, setUsername] = useState(auth?.status.username ?? "");
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [busy, setBusy] = useState(false);

  if (!auth || auth.status.mode !== "login") return null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (newPw !== confirmPw) {
      toastError("New passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const status = await api.authChange({
        current_password: currentPw,
        username: username.trim() !== auth.status.username ? username.trim() : undefined,
        new_password: newPw || undefined,
      });
      if (status.username) auth.setUsername(status.username);
      setCurrentPw("");
      setNewPw("");
      setConfirmPw("");
      toastSuccess("Login credentials updated");
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Authentication">
      <p className="field-hint">
        Changes the web UI login. Leave the password fields empty to keep the current password.
        To disable the login entirely (e.g. your reverse proxy handles auth), set{" "}
        <code>HUSHCAST_AUTH=disabled</code> and restart.
      </p>
      <form className="form-stack" onSubmit={submit}>
        <div className="form-row">
          <label className="field">
            <span className="field-label">Username</span>
            <input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">Current password</span>
            <input
              type="password"
              autoComplete="current-password"
              value={currentPw}
              onChange={(e) => setCurrentPw(e.target.value)}
            />
          </label>
        </div>
        <div className="form-row">
          <label className="field">
            <span className="field-label">New password</span>
            <input
              type="password"
              autoComplete="new-password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
            />
            <span className="field-hint">At least 8 characters.</span>
          </label>
          <label className="field">
            <span className="field-label">Confirm new password</span>
            <input
              type="password"
              autoComplete="new-password"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
            />
          </label>
        </div>
        <div>
          <button type="submit" className="btn" disabled={busy || !currentPw}>
            {busy ? "Saving…" : "Update credentials"}
          </button>
        </div>
      </form>
    </Section>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="panel settings-section">
      <h2 className="panel-title">{title}</h2>
      <div className="panel-body form-stack">{children}</div>
    </section>
  );
}

export function SettingsPage() {
  const { data: settings, error, loading } = useAsyncData<Settings>(() => api.getSettings(), []);
  const [original, setOriginal] = useState<Draft | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const savedTimer = useRef<number | undefined>(undefined);
  const [testResults, setTestResults] = useState<{ transcription?: TestResult; llm?: TestResult }>({});
  const [testing, setTesting] = useState<"transcription" | "llm" | null>(null);
  const [confirmRegen, setConfirmRegen] = useState(false);
  const [regenBusy, setRegenBusy] = useState(false);
  const { toastError, toastSuccess } = useToasts();

  useEffect(() => {
    if (settings) {
      const d = toDraft(settings);
      setOriginal(d);
      setDraft(d);
    }
  }, [settings]);

  if (error && !draft) return <div className="page"><div className="inline-error">{error}</div></div>;
  if (loading || !draft || !original) return <div className="page loading">Loading settings…</div>;

  const set = <K extends keyof Settings>(key: K, value: string | boolean) => {
    setDraft((d) => (d ? { ...d, [key]: value } : d));
    if (key === "transcription_extra_params") setJsonError(null);
  };

  const text = (
    key: keyof Settings,
    label: string,
    opts?: { type?: string; step?: string; placeholder?: string; hint?: string }
  ) => (
    <label className="field">
      <span className="field-label">{label}</span>
      <input
        type={opts?.type ?? "text"}
        step={opts?.step}
        placeholder={opts?.placeholder}
        value={draft[key] as string}
        onChange={(e) => set(key, e.target.value)}
      />
      {opts?.hint && <span className="field-hint">{opts.hint}</span>}
    </label>
  );

  const num = (key: keyof Settings, label: string, opts?: { step?: string; hint?: string }) =>
    text(key, label, { type: "number", step: opts?.step ?? "1", hint: opts?.hint });

  const select = (
    key: keyof Settings,
    label: string,
    options: { value: string; label: string }[],
    hint?: string
  ) => (
    <label className="field">
      <span className="field-label">{label}</span>
      <select value={draft[key] as string} onChange={(e) => set(key, e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );

  const check = (key: keyof Settings, label: string, hint?: string) => (
    <div className="field">
      <label className="field field-inline">
        <input type="checkbox" checked={draft[key] as boolean} onChange={(e) => set(key, e.target.checked)} />
        <span>{label}</span>
      </label>
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );

  const buildChanged = (): Partial<Settings> | null => {
    const changed: Record<string, unknown> = {};
    for (const key of Object.keys(draft) as (keyof Settings)[]) {
      if (key === "feed_token") continue;
      const cur = draft[key];
      if (cur === original[key]) continue;
      // Never send an unedited masked secret back.
      if (SECRET_KEYS.includes(key) && cur === SECRET_MASK) continue;
      if (key === "transcription_extra_params") {
        try {
          const parsed = JSON.parse((cur as string) || "{}");
          if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
            setJsonError("Extra params must be a JSON object.");
            return null;
          }
          changed[key] = parsed;
        } catch {
          setJsonError("Extra params is not valid JSON.");
          return null;
        }
        continue;
      }
      if (typeof cur === "boolean") {
        changed[key] = cur;
      } else if (NUMBER_KEYS.includes(key)) {
        if ((cur as string).trim() === "") {
          changed[key] = null;
        } else {
          const n = Number(cur);
          if (isNaN(n)) {
            toastError(`"${key}" must be a number.`);
            return null;
          }
          changed[key] = n;
        }
      } else {
        changed[key] = cur;
      }
    }
    return changed as Partial<Settings>;
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setJsonError(null);
    const changed = buildChanged();
    if (changed === null) return;
    if (Object.keys(changed).length === 0) {
      toastSuccess("Nothing to save");
      return;
    }
    setSaving(true);
    try {
      const updated = await api.putSettings(changed);
      const d = toDraft(updated);
      setOriginal(d);
      setDraft(d);
      toastSuccess("Settings saved");
      setSaved(true);
      window.clearTimeout(savedTimer.current);
      savedTimer.current = window.setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (which: "transcription" | "llm") => {
    setTesting(which);
    try {
      // Send unsaved form edits as overrides so the test reflects what's on screen.
      const overrides = buildChanged() ?? {};
      const result =
        which === "transcription" ? await api.testTranscription(overrides) : await api.testLlm(overrides);
      setTestResults((r) => ({ ...r, [which]: result }));
    } catch (err) {
      setTestResults((r) => ({
        ...r,
        [which]: { ok: false, message: err instanceof Error ? err.message : String(err) },
      }));
    } finally {
      setTesting(null);
    }
  };

  const regenerate = async () => {
    setRegenBusy(true);
    try {
      const { feed_token } = await api.regenerateToken();
      setOriginal((o) => (o ? { ...o, feed_token } : o));
      setDraft((d) => (d ? { ...d, feed_token } : d));
      toastSuccess("Feed token regenerated");
      setConfirmRegen(false);
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setRegenBusy(false);
    }
  };

  const testBadge = (r?: TestResult) =>
    r && (
      <span className={`test-result ${r.ok ? "test-ok" : "test-fail"}`}>
        {r.ok ? <CheckIcon size={14} weight="bold" /> : <XIcon size={14} weight="bold" />} {r.message}
      </span>
    );

  return (
    <div className="page">
      <div className="page-header">
        <h1>Settings</h1>
      </div>
      <form onSubmit={save}>
        <Section title="Transcription">
          {text("transcription_base_url", "Base URL", {
            placeholder: "https://api.example.com/v1",
            hint: "OpenAI-compatible transcription server, e.g. https://api.openai.com/v1 or https://api.groq.com/openai/v1.",
          })}
          <div className="form-row">
            {text("transcription_api_key", "API key", {
              type: "password",
            })}
            {text("transcription_model", "Model", {
              hint: "Model name the server expects, e.g. whisper-1 (OpenAI), whisper-large-v3 (groq), or large-v3 (whisperx).",
            })}
          </div>
          <div className="form-row">
            {check(
              "transcription_word_timestamps",
              "Word timestamps",
              "Request per-word timing. When available, cut points are refined into pauses between words. Otherwise cuts snap to segment boundaries."
            )}
            {check(
              "transcription_diarize",
              "Diarize speakers",
              "Label who is speaking (whisperx only). Helps the LLM spot inserted ads read by a voice not heard elsewhere."
            )}
          </div>
          <div className="form-row">
            {num("transcription_max_upload_mb", "Max upload (MB)", {
              hint: "Size limit for transcription APIs. Set to 0 for no limit.",
            })}
            {num("transcription_timeout_s", "Timeout (s)", {
              hint: "Max wait for one transcription.",
            })}
          </div>
          <label className="field">
            <span className="field-label">Extra params (JSON object)</span>
            <textarea
              className="mono"
              rows={4}
              value={draft.transcription_extra_params as string}
              onChange={(e) => set("transcription_extra_params", e.target.value)}
            />
            <span className="field-hint">
              Merged into the transcription request as-is, for provider-specific options like VAD chunk size, batch
              size, or hotwords.
            </span>
          </label>
          {jsonError && <div className="inline-error">{jsonError}</div>}
          <div className="test-row">
            <button
              type="button"
              className="btn"
              disabled={testing !== null}
              onClick={() => void runTest("transcription")}
            >
              {testing === "transcription" ? "Testing…" : "Test connection"}
            </button>
            {testBadge(testResults.transcription)}
          </div>
        </Section>

        <Section title="Ad detection (LLM)">
          {text("llm_base_url", "Base URL", {
            placeholder: "https://api.example.com/v1",
            hint: "OpenAI-compatible chat-completions endpoint, including the version path, e.g. https://api.openai.com/v1, https://ollama.com/v1, https://openrouter.ai/api/v1, etc.",
          })}
          <div className="form-row">
            {text("llm_api_key", "API key", { type: "password" })}
            {text("llm_model", "Model", {
              hint: "Exact model ID the provider expects.",
            })}
          </div>
          <div className="form-row">
            {num("llm_temperature", "Temperature", {
              step: "0.1",
              hint: "How much randomness the LLM adds to its output. Lower is more deterministic. 0.5–0.8 recommended.",
            })}
            {num("llm_context_budget_tokens", "Context budget (tokens)", {
              hint: "Transcripts estimated to exceed this are split into overlapping chunks. Set comfortably below your model's context window.",
            })}
          </div>
          <div className="form-row">
            {num("llm_max_tokens", "Max response tokens", {
              hint: "Completion token cap per request.",
            })}
            {num("llm_timeout_s", "Timeout (s)", {
              hint: "Max wait for one chat completion.",
            })}
          </div>
          <label className="field">
            <span className="field-label">Detection prompt</span>
            <textarea
              className="mono"
              rows={10}
              value={draft.detection_prompt as string}
              onChange={(e) => set("detection_prompt", e.target.value)}
            />
            <span className="field-hint">
              Clearing this field and saving resets the prompt to the server default.
            </span>
          </label>
          <label className="field">
            <span className="field-label">Global learned hints</span>
            <textarea
              className="mono"
              rows={14}
              value={draft.global_learned_hints as string}
              onChange={(e) => set("global_learned_hints", e.target.value)}
            />
            <span className="field-hint">
              AI-distilled from your corrections via each podcast's "Distill hints" button. Applied to every feed.
              Edit or clear freely. Podcast-specific guidance wins on conflict.
            </span>
          </label>
          <div className="test-row">
            <button type="button" className="btn" disabled={testing !== null} onClick={() => void runTest("llm")}>
              {testing === "llm" ? "Testing…" : "Test connection"}
            </button>
            {testBadge(testResults.llm)}
          </div>
        </Section>

        <Section title="Audio cues">
          <p className="field-hint">
            Optional: classify non-speech audio (silence, music) and use it to inform ad detection: cue lines in the
            LLM prompt, merging ad spots separated only by music/silence, and trimming intro/outro music around edge
            ads.
          </p>
          {select(
            "cue_provider",
            "Cue provider",
            [
              { value: "off", label: "Off" },
              { value: "silence", label: "Built-in silence detection (ffmpeg)" },
              { value: "remote", label: "Remote service (inaSpeechSegmenter-style)" },
            ],
            "Built-in detection finds silences only. A remote service also labels music and noise. Takes effect on the next processed episode."
          )}
          {draft.cue_provider === "remote" && (
            <div className="form-row">
              {text("cue_remote_base_url", "Remote base URL", {
                placeholder: "http://localhost:8001",
                hint: "POSTs the audio to {base}/segment. If the service fails, processing falls back to built-in silence detection.",
              })}
              {num("cue_remote_timeout_s", "Remote timeout (s)")}
            </div>
          )}
          {draft.cue_provider === "silence" && (
            <div className="form-row">
              {num("cue_silence_noise_db", "Noise floor (dB)", {
                hint: "Audio below this level counts as silence. -35 dB is a reasonable default for produced podcasts.",
              })}
              {num("cue_min_silence_s", "Min silence (s)", {
                step: "0.1",
                hint: "Quiet stretches shorter than this are ignored.",
              })}
            </div>
          )}
          {draft.cue_provider !== "off" && (
            <>
              <div className="form-row">
                {check(
                  "cue_prompt_annotations",
                  "Annotate the detection prompt",
                  "Interleave cue lines like “[122.4-127.6] [MUSIC] (5.2s)” with the transcript so the LLM can see ad-break stings."
                )}
                {num("cue_min_prompt_duration_s", "Min cue for prompt (s)", {
                  step: "0.5",
                  hint: "Cues shorter than this are hidden from the prompt to save tokens.",
                })}
              </div>
              <div className="form-row">
                {num("cue_bridge_max_gap_s", "Bridge gap up to (s)", {
                  step: "0.5",
                  hint: "Merge consecutive ad segments whose gap is this short and fully non-speech (music/silence). 0 = off.",
                })}
                {num("cue_edge_max_extension_s", "Extend edge ads up to (s)", {
                  step: "0.5",
                  hint: "Extend an ad at the very start/end of the episode across a leading/trailing music sting. 0 = off.",
                })}
              </div>
            </>
          )}
        </Section>

        <Section title="Processing">
          <div className="form-row">
            {num("poll_interval_minutes", "Poll interval (min)", {
              hint: "How often source feeds are checked for new episodes.",
            })}
          </div>
          <div className="form-row">
            {num("max_concurrent_episodes", "Max concurrent episodes", {
              hint: "Episodes processed in parallel. Takes effect on restart.",
            })}
            {num("max_episode_retries", "Max episode retries", {
              hint: "Failed episodes auto-requeue on the next poll up to this many times.",
            })}
          </div>
          <div className="form-row">
            {check(
              "keep_originals",
              "Keep original files",
              "Keep the downloaded source audio after processing, useful for auditing detection. Off = deleted once the clean file exists."
            )}
            {num("keep_originals_days", "Keep originals (days)", {
              hint: "When keeping originals, delete them after this many days.",
            })}
          </div>
          <div className="form-row">
            {num("min_confidence", "Min confidence (0–1)", {
              step: "0.05",
              hint: "Detected segments below this LLM confidence are ignored.",
            })}
            {num("min_duration_s", "Min segment duration (s)", {
              step: "0.5",
              hint: "Segments shorter than this are ignored. Filters one-line false positives.",
            })}
          </div>
          <div className="form-row">
            {num("merge_gap_s", "Merge gap (s)", {
              step: "0.5",
              hint: "Detected segments closer together than this merge into a single cut.",
            })}
            {num("snap_tolerance_s", "Snap tolerance (s)", {
              step: "0.1",
              hint: "Segment edges snap to the nearest transcript boundary within this window. An edge with no boundary nearby is treated as hallucinated and dropped.",
            })}
          </div>
          <div className="form-row">
            {num("refine_window_s", "Refine window (s)", {
              step: "0.5",
              hint: "Cuts move into a nearby pause between words within this window. Wider and closer pauses win, and moves that would clip surrounding content are penalized extra. Applies when “Word timestamps” is on in Transcription.",
            })}
            {num("refine_min_gap_s", "Min pause (s)", {
              step: "0.05",
              hint: "Shortest inter-word silence that qualifies as a cut point.",
            })}
          </div>
          <div className="form-row">
            {num("max_kept_episodes", "Max kept episodes", {
              hint: "Per podcast, expire processed audio beyond the newest N episodes. 0 = keep everything.",
            })}
          </div>
          {num("mp3_quality", "MP3 quality", {
            hint: "libmp3lame VBR level 0-9 for the output audio, lower is better quality. 4 ≈ 165 kbps, 2 ≈ 190 kbps.",
          })}
        </Section>

        <div className="save-bar">
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : "Save settings"}
          </button>
          {saved && (
            <span className="save-ok">
              <CheckIcon size={14} weight="bold" /> Saved
            </span>
          )}
        </div>
      </form>

      <Section title="Feed access">
        <p className="field-hint">
          Every subscription URL embeds this token. Regenerating it immediately breaks all existing podcast-app
          subscriptions.
        </p>
        <div className="token-row">
          <code className="token-value">{draft.feed_token as string}</code>
          <CopyButton text={draft.feed_token as string} label="Copy" small />
          <button type="button" className="btn btn-danger-outline btn-small" onClick={() => setConfirmRegen(true)}>
            Regenerate token
          </button>
        </div>
      </Section>

      <AuthSection />

      {confirmRegen && (
        <ConfirmDialog
          title="Regenerate feed token"
          message="This will invalidate every existing subscription URL. All podcast apps subscribed to your hushcast feeds will stop updating until you re-subscribe them. Continue?"
          confirmLabel="Regenerate"
          danger
          busy={regenBusy}
          onConfirm={() => void regenerate()}
          onCancel={() => setConfirmRegen(false)}
        />
      )}
    </div>
  );
}
