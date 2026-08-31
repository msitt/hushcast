import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api, UNAUTHENTICATED_EVENT, type AuthStatus } from "../api/client";
import { AuthScreen } from "../pages/Auth";

interface AuthContextValue {
  status: AuthStatus;
  signOut: () => Promise<void>;
  /** Update the cached username after a credentials change in Settings. */
  setUsername: (username: string) => void;
}

const Ctx = createContext<AuthContextValue | null>(null);

/** Auth state + sign-out, null only outside AuthGate. */
export function useAuth(): AuthContextValue | null {
  return useContext(Ctx);
}

/**
 * Blocks the app behind the server's auth mode: shows the first-run setup or
 * login screen until authenticated (or auth is disabled). Also flips back to
 * the login screen when any API call reports an expired session.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [probeError, setProbeError] = useState<string | null>(null);

  const probe = useCallback(async () => {
    setProbeError(null);
    try {
      setStatus(await api.authStatus());
    } catch (err) {
      setProbeError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void probe();
  }, [probe]);

  useEffect(() => {
    const onUnauthenticated = () =>
      setStatus((s) => (s && s.mode !== "disabled" ? { ...s, authenticated: false, username: null } : s));
    window.addEventListener(UNAUTHENTICATED_EVENT, onUnauthenticated);
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, onUnauthenticated);
  }, []);

  if (probeError) {
    return (
      <div className="auth-screen">
        <div className="auth-card panel">
          <div className="panel-body form-stack">
            <div className="inline-error">{probeError}</div>
            <button className="btn" onClick={() => void probe()}>
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }
  if (!status) return null; // brief blank while probing
  if (!status.authenticated) {
    return (
      <AuthScreen mode={status.mode === "setup" ? "setup" : "login"} onAuthenticated={setStatus} />
    );
  }

  const signOut = async () => {
    await api.authLogout();
    setStatus((s) => (s ? { ...s, authenticated: false, username: null } : s));
  };
  const setUsername = (username: string) => setStatus((s) => (s ? { ...s, username } : s));

  return <Ctx.Provider value={{ status, signOut, setUsername }}>{children}</Ctx.Provider>;
}
