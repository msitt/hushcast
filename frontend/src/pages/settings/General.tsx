import { CheckField, NumField, SaveBar, Section, useSettingsForm } from "./shared";

const KEYS = [
  "poll_interval_minutes",
  "max_concurrent_episodes",
  "max_episode_retries",
  "keep_originals",
  "keep_originals_days",
  "max_kept_episodes",
  "max_kept_days",
  "mp3_quality",
] as const;

export function GeneralPage() {
  const { draft, set, save, saving, saved } = useSettingsForm(KEYS);

  return (
    <form onSubmit={save}>
      <Section title="Processing">
        <div className="form-row">
          <NumField draft={draft} set={set} k="poll_interval_minutes" label="Poll interval (min)"
            hint="How often source feeds are checked for new episodes." />
        </div>
        <div className="form-row">
          <NumField draft={draft} set={set} k="max_concurrent_episodes" label="Max concurrent episodes"
            hint="Episodes processed in parallel. Takes effect on restart." />
          <NumField draft={draft} set={set} k="max_episode_retries" label="Max episode retries"
            hint="Failed episodes auto-requeue on the next poll up to this many times." />
        </div>
        <div className="form-row">
          <CheckField draft={draft} set={set} k="keep_originals" label="Keep original files"
            hint="Keep the downloaded source audio after processing, useful for auditing detection. Off = deleted once the clean file exists." />
          <NumField draft={draft} set={set} k="keep_originals_days" label="Keep originals (days)"
            hint="When keeping originals, delete them after this many days." />
        </div>
        <div className="form-row">
          <NumField draft={draft} set={set} k="max_kept_episodes" label="Max kept episodes"
            hint="Per podcast, expire processed audio beyond the newest N episodes. Combines with “Max kept days”. 0 = keep everything." />
          <NumField draft={draft} set={set} k="max_kept_days" label="Max kept days"
            hint="Expire processed audio this many days after processing finished. An episode expires when it exceeds this or “Max kept episodes”, whichever hits first. 0 = keep everything." />
        </div>
        <NumField draft={draft} set={set} k="mp3_quality" label="MP3 quality"
          hint="libmp3lame VBR level 0-9 for the output audio, lower is better quality. 4 ≈ 165 kbps, 2 ≈ 190 kbps." />
      </Section>
      <SaveBar saving={saving} saved={saved} />
    </form>
  );
}
