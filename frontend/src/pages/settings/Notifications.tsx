import { useState } from "react";
import { api, type TestResult } from "../../api/client";
import { useToasts } from "../../components/Toasts";
import { useSettingsContext } from "./context";
import { Section, testBadge } from "./shared";

function urlsFromText(text: string): string[] {
  return text.split("\n").map((s) => s.trim()).filter(Boolean);
}

export function NotificationsPage() {
  const { settings, update } = useSettingsContext();
  const { toastError, toastSuccess } = useToasts();
  const [urlsText, setUrlsText] = useState(settings.notification_urls.join("\n"));
  const [events, setEvents] = useState(settings.notification_events);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult>();

  const save = async () => {
    setSaving(true);
    try {
      const updated = await api.putSettings({
        notification_events: events,
        notification_urls: urlsFromText(urlsText),
      });
      update(updated);
      setUrlsText(updated.notification_urls.join("\n"));
      setEvents(updated.notification_events);
      toastSuccess("Notification settings saved");
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    try {
      setTestResult(await api.testNotifications({ notification_urls: urlsFromText(urlsText) }));
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof Error ? err.message : String(err) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <Section title="Notifications">
      <p className="field-hint">
        Get an alert when something needs your attention. Delivered via{" "}
        <a href="https://github.com/caronc/apprise#popular-notification-services" target="_blank" rel="noreferrer">
          Apprise
        </a>
        , which supports Discord, Slack, ntfy, email, Telegram, a generic webhook, and many others.
      </p>
      <label className="field">
        <span className="field-label">Notification URLs</span>
        <textarea
          className="mono"
          rows={3}
          placeholder="https://discord.com/api/webhooks/webhook_id/webhook_token"
          value={urlsText}
          onChange={(e) => setUrlsText(e.target.value)}
        />
        <span className="field-hint">
          One Apprise URL per line. See the{" "}
          <a href="https://appriseit.com/services/" target="_blank" rel="noreferrer">
            Apprise documentation
          </a>{" "}
          for the syntax each service expects. Leave empty to disable notifications entirely.
        </span>
      </label>
      <div className="form-row">
        <label className="field field-inline">
          <input
            type="checkbox"
            checked={events.episode_retries_exhausted}
            onChange={(e) => setEvents((ev) => ({ ...ev, episode_retries_exhausted: e.target.checked }))}
          />
          <span>Episode failed</span>
        </label>
        <label className="field field-inline">
          <input
            type="checkbox"
            checked={events.feed_poll_failing}
            onChange={(e) => setEvents((ev) => ({ ...ev, feed_poll_failing: e.target.checked }))}
          />
          <span>Feed polling failing repeatedly</span>
        </label>
      </div>
      <div className="test-row">
        <button type="button" className="btn" disabled={testing} onClick={() => void runTest()}>
          {testing ? "Sending…" : "Send test notification"}
        </button>
        {testBadge(testResult)}
        <button type="button" className="btn btn-primary" disabled={saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </Section>
  );
}
