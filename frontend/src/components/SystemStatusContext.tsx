import { createContext, useContext, type ReactNode } from "react";
import { api, type SystemStatus } from "../api/client";
import { useAsyncData } from "../hooks";

const Ctx = createContext<SystemStatus | null>(null);

/** Latest system status, polled every 5s while the tab is visible. Null until first load. */
export function useSystemStatus(): SystemStatus | null {
  return useContext(Ctx);
}

/** True while the backend reports any active processing. */
export function useProcessingActive(): boolean {
  return useContext(Ctx)?.processing ?? false;
}

export function SystemStatusProvider({ children }: { children: ReactNode }) {
  const { data } = useAsyncData<SystemStatus>(() => api.systemStatus(), [], { intervalMs: 5000 });
  return <Ctx.Provider value={data}>{children}</Ctx.Provider>;
}
