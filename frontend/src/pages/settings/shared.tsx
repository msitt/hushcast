import { useEffect, useRef, useState, type FormEvent } from "react";
import { CheckIcon, XIcon } from "@phosphor-icons/react";
import { api, type Settings, type TestResult } from "../../api/client";
import { useToasts } from "../../components/Toasts";
import { useSettingsContext } from "./context";

// Shared machinery for the per-tab settings forms: each settings page edits its own
// subset of Settings keys with its own draft/save state, kept loosely typed
// (Record<string, string | boolean>) the way the original single-page form was.

export const SECRET_MASK = "••••••••";

export const NUMBER_KEYS: (keyof Settings)[] = [
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
  "max_kept_days",
  "mp3_quality",
];

export const SECRET_KEYS: (keyof Settings)[] = ["transcription_api_key", "llm_api_key"];

export type Draft = Record<string, string | boolean>;

export function toDraft(s: Settings, keys: readonly (keyof Settings)[]): Draft {
  const d: Draft = {};
  for (const k of keys) {
    const v = s[k];
    if (typeof v === "boolean") d[k] = v;
    else if (k === "transcription_extra_params") d[k] = JSON.stringify(v ?? {}, null, 2);
    else d[k] = v == null ? "" : String(v);
  }
  return d;
}

export function buildChanged(
  draft: Draft,
  original: Draft,
  opts?: { onJsonError?: (msg: string) => void; onNumberError?: (key: string) => void }
): Partial<Settings> | null {
  const changed: Record<string, unknown> = {};
  for (const key of Object.keys(draft)) {
    if (key === "feed_token") continue;
    const cur = draft[key];
    if (cur === original[key]) continue;
    // Never send an unedited masked secret back.
    if (SECRET_KEYS.includes(key as keyof Settings) && cur === SECRET_MASK) continue;
    if (key === "transcription_extra_params") {
      try {
        const parsed = JSON.parse((cur as string) || "{}");
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          opts?.onJsonError?.("Extra params must be a JSON object.");
          return null;
        }
        changed[key] = parsed;
      } catch {
        opts?.onJsonError?.("Extra params is not valid JSON.");
        return null;
      }
      continue;
    }
    if (typeof cur === "boolean") {
      changed[key] = cur;
    } else if (NUMBER_KEYS.includes(key as keyof Settings)) {
      if ((cur as string).trim() === "") {
        changed[key] = null;
      } else {
        const n = Number(cur);
        if (isNaN(n)) {
          opts?.onNumberError?.(key);
          return null;
        }
        changed[key] = n;
      }
    } else {
      changed[key] = cur;
    }
  }
  return changed as Partial<Settings>;
}

/** Draft/save state for one settings tab, scoped to a fixed subset of Settings keys. */
export function useSettingsForm(keys: readonly (keyof Settings)[]) {
  const { settings, update } = useSettingsContext();
  const [original, setOriginal] = useState<Draft>(() => toDraft(settings, keys));
  const [draft, setDraft] = useState<Draft>(() => toDraft(settings, keys));
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const savedTimer = useRef<number | undefined>(undefined);
  const { toastError, toastSuccess } = useToasts();

  useEffect(() => {
    const d = toDraft(settings, keys);
    setOriginal(d);
    setDraft(d);
    // `keys` is a stable per-page constant; only re-derive when the shared settings object changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

  const set = (key: string, value: string | boolean) => {
    setDraft((d) => ({ ...d, [key]: value }));
    if (key === "transcription_extra_params") setJsonError(null);
  };

  const save = async (e?: FormEvent) => {
    e?.preventDefault();
    setJsonError(null);
    const changed = buildChanged(draft, original, {
      onJsonError: setJsonError,
      onNumberError: (key) => toastError(`"${key}" must be a number.`),
    });
    if (changed === null) return;
    if (Object.keys(changed).length === 0) {
      toastSuccess("Nothing to save");
      return;
    }
    setSaving(true);
    try {
      const updated = await api.putSettings(changed);
      update(updated);
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

  /** Draft edits merged over the last-saved values, for sending as test-connection overrides. */
  const overrides = () => buildChanged(draft, original, { onJsonError: setJsonError }) ?? {};

  return { draft, set, save, saving, saved, jsonError, overrides };
}

export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel settings-section">
      <h2 className="panel-title">{title}</h2>
      <div className="panel-body form-stack">{children}</div>
    </section>
  );
}

export function testBadge(r?: TestResult) {
  return (
    r && (
      <span className={`test-result ${r.ok ? "test-ok" : "test-fail"}`}>
        {r.ok ? <CheckIcon size={14} weight="bold" /> : <XIcon size={14} weight="bold" />} {r.message}
      </span>
    )
  );
}

export function TextField({
  draft,
  set,
  k,
  label,
  type = "text",
  step,
  placeholder,
  hint,
}: {
  draft: Draft;
  set: (key: string, value: string | boolean) => void;
  k: string;
  label: string;
  type?: string;
  step?: string;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <input
        type={type}
        step={step}
        placeholder={placeholder}
        value={draft[k] as string}
        onChange={(e) => set(k, e.target.value)}
      />
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function NumField(props: Omit<Parameters<typeof TextField>[0], "type"> & { step?: string }) {
  return <TextField {...props} type="number" step={props.step ?? "1"} />;
}

export function SelectField({
  draft,
  set,
  k,
  label,
  options,
  hint,
}: {
  draft: Draft;
  set: (key: string, value: string | boolean) => void;
  k: string;
  label: string;
  options: { value: string; label: string }[];
  hint?: string;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <select value={draft[k] as string} onChange={(e) => set(k, e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function CheckField({
  draft,
  set,
  k,
  label,
  hint,
}: {
  draft: Draft;
  set: (key: string, value: string | boolean) => void;
  k: string;
  label: string;
  hint?: string;
}) {
  return (
    <div className="field">
      <label className="field field-inline">
        <input type="checkbox" checked={draft[k] as boolean} onChange={(e) => set(k, e.target.checked)} />
        <span>{label}</span>
      </label>
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}

export function SaveBar({ saving, saved }: { saving: boolean; saved: boolean }) {
  return (
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
  );
}
