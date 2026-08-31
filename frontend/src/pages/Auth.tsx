import { useState, type FormEvent } from "react";
import { api, type AuthStatus } from "../api/client";
import hushcastIcon from "../assets/hushcast-icon.svg";

/** Full-screen setup (first run) or login card, shown by AuthGate instead of the app. */
export function AuthScreen({
  mode,
  onAuthenticated,
}: {
  mode: "setup" | "login";
  onAuthenticated: (status: AuthStatus) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (mode === "setup" && password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const status =
        mode === "setup"
          ? await api.authSetup({ username, password })
          : await api.authLogin({ username, password });
      onAuthenticated(status);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-screen">
      <div className="auth-card panel">
        <div className="auth-brand">
          <img src={hushcastIcon} alt="" className="brand-mark" width={26} height={26} />
          <span className="brand-name">Hushcast</span>
        </div>
        <div className="panel-body form-stack">
          {mode === "setup" && (
            <p className="field-hint">
              First run: create the login for this Hushcast instance.
            </p>
          )}
          <form className="form-stack" onSubmit={submit}>
            <label className="field">
              <span className="field-label">Username</span>
              <input
                autoFocus
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">Password</span>
              <input
                type="password"
                autoComplete={mode === "setup" ? "new-password" : "current-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              {mode === "setup" && <span className="field-hint">At least 8 characters.</span>}
            </label>
            {mode === "setup" && (
              <label className="field">
                <span className="field-label">Confirm password</span>
                <input
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
              </label>
            )}
            {error && <div className="inline-error">{error}</div>}
            <button type="submit" className="btn btn-primary" disabled={busy || !username || !password}>
              {busy ? "…" : mode === "setup" ? "Create login" : "Sign in"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
