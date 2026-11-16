import { useSyncExternalStore } from "react";

/**
 * Theme preference: an explicit light/dark choice, or "system" to follow the
 * OS. Applied by setting <meta name="color-scheme"> to "light", "dark", or
 * "light dark". CSS maps that meta onto the root `color-scheme`, so
 * `light-dark()` tokens and native UI follow it, and "system" tracks OS
 * changes without any JS. An inline script in index.html sets the meta
 * before first paint.
 */
export type ThemePreference = "light" | "system" | "dark";

const STORAGE_KEY = "hushcast-theme";
const listeners = new Set<() => void>();

function readPreference(): ThemePreference {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "light" || v === "dark") return v;
  } catch {
    /* storage unavailable */
  }
  return "system";
}

let preference = readPreference();

function apply() {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="color-scheme"]');
  if (meta) meta.content = preference === "system" ? "light dark" : preference;
}

function notify() {
  apply();
  for (const fn of listeners) fn();
}

// Keep tabs in sync.
window.addEventListener("storage", (e) => {
  if (e.key === STORAGE_KEY) {
    preference = readPreference();
    notify();
  }
});

export function setThemePreference(pref: ThemePreference) {
  preference = pref;
  try {
    if (pref === "system") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, pref);
  } catch {
    /* storage unavailable, still applies for this page */
  }
  notify();
}

export function useThemePreference(): ThemePreference {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    () => preference
  );
}
