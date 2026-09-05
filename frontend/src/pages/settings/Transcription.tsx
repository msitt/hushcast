import { useState } from "react";
import { api, type TestResult } from "../../api/client";
import { CheckField, NumField, SaveBar, Section, TextField, testBadge, useSettingsForm } from "./shared";

const KEYS = [
  "transcription_base_url",
  "transcription_api_key",
  "transcription_model",
  "transcription_word_timestamps",
  "transcription_diarize",
  "transcription_max_upload_mb",
  "transcription_timeout_s",
  "transcription_extra_params",
] as const;

export function TranscriptionPage() {
  const { draft, set, save, saving, saved, jsonError, overrides } = useSettingsForm(KEYS);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult>();

  const runTest = async () => {
    setTesting(true);
    try {
      setTestResult(await api.testTranscription(overrides()));
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof Error ? err.message : String(err) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <form onSubmit={save}>
      <Section title="Transcription">
        <TextField draft={draft} set={set} k="transcription_base_url" label="Base URL"
          placeholder="https://api.example.com/v1"
          hint="OpenAI-compatible transcription server, e.g. https://api.openai.com/v1 or https://api.groq.com/openai/v1." />
        <div className="form-row">
          <TextField draft={draft} set={set} k="transcription_api_key" label="API key" type="password" />
          <TextField draft={draft} set={set} k="transcription_model" label="Model"
            hint="Model name the server expects, e.g. whisper-1 (OpenAI), whisper-large-v3 (groq), or large-v3 (whisperx)." />
        </div>
        <div className="form-row">
          <CheckField draft={draft} set={set} k="transcription_word_timestamps" label="Word timestamps"
            hint="Request per-word timing. When available, cut points are refined into pauses between words. Otherwise cuts snap to segment boundaries." />
          <CheckField draft={draft} set={set} k="transcription_diarize" label="Diarize speakers"
            hint="Label who is speaking (whisperx only). Helps the LLM spot inserted ads read by a voice not heard elsewhere." />
        </div>
        <div className="form-row">
          <NumField draft={draft} set={set} k="transcription_max_upload_mb" label="Max upload (MB)"
            hint="Size limit for transcription APIs. Set to 0 for no limit." />
          <NumField draft={draft} set={set} k="transcription_timeout_s" label="Timeout (s)"
            hint="Max wait for one transcription." />
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
          <button type="button" className="btn" disabled={testing} onClick={() => void runTest()}>
            {testing ? "Testing…" : "Test connection"}
          </button>
          {testBadge(testResult)}
        </div>
      </Section>
      <SaveBar saving={saving} saved={saved} />
    </form>
  );
}
