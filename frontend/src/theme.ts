import { useSyncExternalStore } from "react";

/**
 * Theme preference: an explicit light/dark choice, or "system" to follow the
 * OS. The resolved theme is applied as `data-theme="light" | "dark"` on
 * <html>. An inline script in index.html does the same before first paint.
 */
export type ThemePreference = "light" | "system" | "dark";

const STORAGE_KEY = "hushcast-theme";
const media = window.matchMedia("(prefers-color-scheme: dark)");
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
  const resolved = preference === "system" ? (media.matches ? "dark" : "light") : preference;
  document.documentElement.dataset.theme = resolved;
}

function notify() {
  apply();
  for (const fn of listeners) fn();
}

media.addEventListener("change", () => {
  if (preference === "system") notify();
});

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
