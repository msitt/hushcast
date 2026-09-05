import { useState } from "react";
import { api, type TestResult } from "../../api/client";
import { NumField, SaveBar, Section, TextField, testBadge, useSettingsForm } from "./shared";

const KEYS = [
  "llm_base_url",
  "llm_api_key",
  "llm_model",
  "llm_temperature",
  "llm_context_budget_tokens",
  "llm_max_tokens",
  "llm_timeout_s",
  "detection_prompt",
  "global_learned_hints",
  "min_confidence",
  "min_duration_s",
  "merge_gap_s",
  "snap_tolerance_s",
  "refine_window_s",
  "refine_min_gap_s",
] as const;

export function AdDetectionPage() {
  const { draft, set, save, saving, saved, overrides } = useSettingsForm(KEYS);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult>();

  const runTest = async () => {
    setTesting(true);
    try {
      setTestResult(await api.testLlm(overrides()));
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof Error ? err.message : String(err) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <form onSubmit={save}>
      <Section title="Ad detection (LLM)">
        <TextField draft={draft} set={set} k="llm_base_url" label="Base URL"
          placeholder="https://api.example.com/v1"
          hint="OpenAI-compatible chat-completions endpoint, including the version path, e.g. https://api.openai.com/v1, https://ollama.com/v1, https://openrouter.ai/api/v1, etc." />
        <div className="form-row">
          <TextField draft={draft} set={set} k="llm_api_key" label="API key" type="password" />
          <TextField draft={draft} set={set} k="llm_model" label="Model" hint="Exact model ID the provider expects." />
        </div>
        <div className="form-row">
          <NumField draft={draft} set={set} k="llm_temperature" label="Temperature" step="0.1"
            hint="How much randomness the LLM adds to its output. Lower is more deterministic. 0.5–0.8 recommended." />
          <NumField draft={draft} set={set} k="llm_context_budget_tokens" label="Context budget (tokens)"
            hint="Transcripts estimated to exceed this are split into overlapping chunks. Set comfortably below your model's context window." />
        </div>
        <div className="form-row">
          <NumField draft={draft} set={set} k="llm_max_tokens" label="Max response tokens"
            hint="Completion token cap per request." />
          <NumField draft={draft} set={set} k="llm_timeout_s" label="Timeout (s)" hint="Max wait for one chat completion." />
        </div>
        <label className="field">
          <span className="field-label">Detection prompt</span>
          <textarea
            className="mono"
            rows={10}
            value={draft.detection_prompt as string}
            onChange={(e) => set("detection_prompt", e.target.value)}
          />
          <span className="field-hint">Clearing this field and saving resets the prompt to the server default.</span>
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
            AI-distilled from your corrections via each podcast's "Distill hints" button. Applied to every feed. Edit
            or clear freely. Podcast-specific guidance wins on conflict.
          </span>
        </label>
        <div className="test-row">
          <button type="button" className="btn" disabled={testing} onClick={() => void runTest()}>
            {testing ? "Testing…" : "Test connection"}
          </button>
          {testBadge(testResult)}
        </div>
      </Section>

      <Section title="Detection tuning">
        <div className="form-row">
          <NumField draft={draft} set={set} k="min_confidence" label="Min confidence (0–1)" step="0.05"
            hint="Detected segments below this LLM confidence are ignored." />
          <NumField draft={draft} set={set} k="min_duration_s" label="Min segment duration (s)" step="0.5"
            hint="Segments shorter than this are ignored. Filters one-line false positives." />
        </div>
        <div className="form-row">
          <NumField draft={draft} set={set} k="merge_gap_s" label="Merge gap (s)" step="0.5"
            hint="Detected segments closer together than this merge into a single cut." />
          <NumField draft={draft} set={set} k="snap_tolerance_s" label="Snap tolerance (s)" step="0.1"
            hint="Segment edges snap to the nearest transcript boundary within this window. An edge with no boundary nearby is treated as hallucinated and dropped." />
        </div>
        <div className="form-row">
          <NumField draft={draft} set={set} k="refine_window_s" label="Refine window (s)" step="0.5"
            hint="Cuts move into a nearby pause between words within this window. Wider and closer pauses win, and moves that would clip surrounding content are penalized extra. Applies when “Word timestamps” is on in Transcription." />
          <NumField draft={draft} set={set} k="refine_min_gap_s" label="Min pause (s)" step="0.05"
            hint="Shortest inter-word silence that qualifies as a cut point." />
        </div>
      </Section>
      <SaveBar saving={saving} saved={saved} />
    </form>
  );
}
