import { createContext, useContext } from "react";
import type { Settings } from "../../api/client";

export interface SettingsCtx {
  settings: Settings;
  /** Merge a fresh Settings object (e.g. an api.putSettings response) into shared state. */
  update: (updated: Settings) => void;
}

const Ctx = createContext<SettingsCtx | null>(null);

export const SettingsProvider = Ctx.Provider;

export function useSettingsContext(): SettingsCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useSettingsContext must be used within the settings layout");
  return ctx;
}
