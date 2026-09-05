import { useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, type Settings } from "../../api/client";
import { useAsyncData } from "../../hooks";
import { SettingsProvider } from "./context";
import { GeneralPage } from "./General";
import { TranscriptionPage } from "./Transcription";
import { AdDetectionPage } from "./AdDetection";
import { AudioCuesPage } from "./AudioCues";
import { NotificationsPage } from "./Notifications";
import { AccountPage } from "./Account";

const TABS = [
  { to: "general", label: "General" },
  { to: "transcription", label: "Transcription" },
  { to: "detection", label: "Ad detection" },
  { to: "cues", label: "Audio cues" },
  { to: "notifications", label: "Notifications" },
  { to: "account", label: "Account" },
];

export function SettingsLayout() {
  const { data: settings, error, loading } = useAsyncData<Settings>(() => api.getSettings(), []);
  const [current, setCurrent] = useState<Settings | null>(null);
  const active = current ?? settings;

  if (error && !active) return <div className="page"><div className="inline-error">{error}</div></div>;
  if (loading || !active) return <div className="page loading">Loading settings…</div>;

  return (
    <div className="page">
      <div className="page-header">
        <h1>Settings</h1>
      </div>
      <nav className="settings-tabs">
        {TABS.map((t) => (
          <NavLink
            key={t.to}
            to={`/settings/${t.to}`}
            className={({ isActive }) => `settings-tab${isActive ? " active" : ""}`}
          >
            {t.label}
          </NavLink>
        ))}
      </nav>
      <SettingsProvider value={{ settings: active, update: setCurrent }}>
        <Routes>
          <Route index element={<Navigate to="/settings/general" replace />} />
          <Route path="general" element={<GeneralPage />} />
          <Route path="transcription" element={<TranscriptionPage />} />
          <Route path="detection" element={<AdDetectionPage />} />
          <Route path="cues" element={<AudioCuesPage />} />
          <Route path="notifications" element={<NotificationsPage />} />
          <Route path="account" element={<AccountPage />} />
        </Routes>
      </SettingsProvider>
    </div>
  );
}
