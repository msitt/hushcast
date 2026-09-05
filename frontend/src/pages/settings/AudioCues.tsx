import { CheckField, NumField, SaveBar, Section, SelectField, TextField, useSettingsForm } from "./shared";

const KEYS = [
  "cue_provider",
  "cue_remote_base_url",
  "cue_remote_timeout_s",
  "cue_silence_noise_db",
  "cue_min_silence_s",
  "cue_prompt_annotations",
  "cue_min_prompt_duration_s",
  "cue_bridge_max_gap_s",
  "cue_edge_max_extension_s",
] as const;

export function AudioCuesPage() {
  const { draft, set, save, saving, saved } = useSettingsForm(KEYS);

  return (
    <form onSubmit={save}>
      <Section title="Audio cues">
        <p className="field-hint">
          Optional: Classify non-speech audio (silence, music) and use it to inform ad detection. Cue lines in the
          LLM prompt, merging ad spots separated only by music/silence, and trimming intro/outro music around edge
          ads.
        </p>
        <SelectField
          draft={draft}
          set={set}
          k="cue_provider"
          label="Cue provider"
          options={[
            { value: "off", label: "Off" },
            { value: "silence", label: "Built-in silence detection (ffmpeg)" },
            { value: "remote", label: "Remote service (inaSpeechSegmenter-style)" },
          ]}
          hint="Built-in detection finds silences only. A remote service also labels music and noise. Takes effect on the next processed episode."
        />
        {draft.cue_provider === "remote" && (
          <div className="form-row">
            <TextField draft={draft} set={set} k="cue_remote_base_url" label="Remote base URL"
              placeholder="http://localhost:8001"
              hint="POSTs the audio to {base}/segment. If the service fails, processing falls back to built-in silence detection." />
            <NumField draft={draft} set={set} k="cue_remote_timeout_s" label="Remote timeout (s)" />
          </div>
        )}
        {draft.cue_provider === "silence" && (
          <div className="form-row">
            <NumField draft={draft} set={set} k="cue_silence_noise_db" label="Noise floor (dB)"
              hint="Audio below this level counts as silence. -35 dB is a reasonable default for produced podcasts." />
            <NumField draft={draft} set={set} k="cue_min_silence_s" label="Min silence (s)" step="0.1"
              hint="Quiet stretches shorter than this are ignored." />
          </div>
        )}
        {draft.cue_provider !== "off" && (
          <>
            <div className="form-row">
              <CheckField draft={draft} set={set} k="cue_prompt_annotations" label="Annotate the detection prompt"
                hint="Interleave cue lines like “[122.4-127.6] [MUSIC] (5.2s)” with the transcript so the LLM can see ad-break stings." />
              <NumField draft={draft} set={set} k="cue_min_prompt_duration_s" label="Min cue for prompt (s)" step="0.5"
                hint="Cues shorter than this are hidden from the prompt to save tokens." />
            </div>
            <div className="form-row">
              <NumField draft={draft} set={set} k="cue_bridge_max_gap_s" label="Bridge gap up to (s)" step="0.5"
                hint="Merge consecutive ad segments whose gap is this short and fully non-speech (music/silence). 0 = off." />
              <NumField draft={draft} set={set} k="cue_edge_max_extension_s" label="Extend edge ads up to (s)" step="0.5"
                hint="Extend an ad at the very start/end of the episode across a leading/trailing music sting. 0 = off." />
            </div>
          </>
        )}
      </Section>
      <SaveBar saving={saving} saved={saved} />
    </form>
  );
}
