import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useAuth } from "../../components/AuthGate";
import { useToasts } from "../../components/Toasts";
import { ConfirmDialog } from "../../components/Modal";
import { CopyButton } from "../../components/CopyButton";
import { useSettingsContext } from "./context";
import { Section } from "./shared";

function FeedAccessSection() {
  const { settings, update } = useSettingsContext();
  const { toastError, toastSuccess } = useToasts();
  const [confirmRegen, setConfirmRegen] = useState(false);
  const [regenBusy, setRegenBusy] = useState(false);

  const regenerate = async () => {
    setRegenBusy(true);
    try {
      const { feed_token } = await api.regenerateToken();
      update({ ...settings, feed_token });
      toastSuccess("Feed token regenerated");
      setConfirmRegen(false);
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setRegenBusy(false);
    }
  };

  return (
    <Section title="Feed access">
      <p className="field-hint">
        Every subscription URL embeds this token. Regenerating it immediately breaks all existing podcast-app
        subscriptions.
      </p>
      <div className="token-row">
        <code className="token-value">{settings.feed_token}</code>
        <CopyButton text={settings.feed_token} label="Copy" small />
        <button type="button" className="btn btn-danger-outline btn-small" onClick={() => setConfirmRegen(true)}>
          Regenerate token
        </button>
      </div>

      {confirmRegen && (
        <ConfirmDialog
          title="Regenerate feed token"
          message="This will invalidate every existing subscription URL. All podcast apps subscribed to your hushcast feeds will stop updating until you re-subscribe them. Continue?"
          confirmLabel="Regenerate"
          danger
          busy={regenBusy}
          onConfirm={() => void regenerate()}
          onCancel={() => setConfirmRegen(false)}
        />
      )}
    </Section>
  );
}

export function AccountPage() {
  const auth = useAuth();
  const { toastError, toastSuccess } = useToasts();
  const [username, setUsername] = useState(auth?.status.username ?? "");
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [busy, setBusy] = useState(false);

  if (!auth || auth.status.mode !== "login") {
    return (
      <>
        <Section title="Account">
          <p className="field-hint">
            The web UI login is disabled (<code>HUSHCAST_AUTH=disabled</code>). Your reverse proxy is expected to
            handle authentication.
          </p>
        </Section>
        <FeedAccessSection />
      </>
    );
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (newPw !== confirmPw) {
      toastError("New passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const status = await api.authChange({
        current_password: currentPw,
        username: username.trim() !== auth.status.username ? username.trim() : undefined,
        new_password: newPw || undefined,
      });
      if (status.username) auth.setUsername(status.username);
      setCurrentPw("");
      setNewPw("");
      setConfirmPw("");
      toastSuccess("Login credentials updated");
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Section title="Authentication">
        <p className="field-hint">
          Changes the web UI login. Leave the password fields empty to keep the current password. To disable the
          login entirely (e.g. your reverse proxy handles auth), set <code>HUSHCAST_AUTH=disabled</code> and restart.
        </p>
        <form className="form-stack" onSubmit={submit}>
          <div className="form-row">
            <label className="field">
              <span className="field-label">Username</span>
              <input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">Current password</span>
              <input
                type="password"
                autoComplete="current-password"
                value={currentPw}
                onChange={(e) => setCurrentPw(e.target.value)}
              />
            </label>
          </div>
          <div className="form-row">
            <label className="field">
              <span className="field-label">New password</span>
              <input
                type="password"
                autoComplete="new-password"
                value={newPw}
                onChange={(e) => setNewPw(e.target.value)}
              />
              <span className="field-hint">At least 8 characters.</span>
            </label>
            <label className="field">
              <span className="field-label">Confirm new password</span>
              <input
                type="password"
                autoComplete="new-password"
                value={confirmPw}
                onChange={(e) => setConfirmPw(e.target.value)}
              />
            </label>
          </div>
          <div>
            <button type="submit" className="btn" disabled={busy || !currentPw}>
              {busy ? "Saving…" : "Update credentials"}
            </button>
          </div>
        </form>
      </Section>
      <FeedAccessSection />
    </>
  );
}
