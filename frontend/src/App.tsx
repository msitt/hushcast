import { Link, NavLink, Outlet, Route, Routes } from "react-router-dom";
import { GearSixIcon, MicrophoneIcon, PulseIcon, SignOutIcon } from "@phosphor-icons/react";
import hushcastIcon from "./assets/hushcast-icon.svg";
import { ToastProvider } from "./components/Toasts";
import { AuthGate, useAuth } from "./components/AuthGate";
import { SystemStatusProvider } from "./components/SystemStatusContext";
import { StatusStrip } from "./components/StatusStrip";
import { UpdateBanner } from "./components/UpdateBanner";
import { PodcastsPage } from "./pages/Podcasts";
import { PodcastDetailPage } from "./pages/PodcastDetail";
import { EpisodeDetailPage } from "./pages/EpisodeDetail";
import { SettingsPage } from "./pages/Settings";
import { SystemPage } from "./pages/System";
import { useSystemStatus } from "./components/SystemStatusContext";

function Layout() {
  const status = useSystemStatus();
  const auth = useAuth();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link to="/" className="brand">
          <img src={hushcastIcon} alt="" className="brand-mark" width={24} height={24} />
          <span className="brand-name">Hushcast</span>
        </Link>
        <nav className="nav">
          <NavLink to="/" end className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            <span className="nav-icon"><MicrophoneIcon size={18} weight="duotone" /></span> Podcasts
          </NavLink>
          <NavLink to="/settings" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            <span className="nav-icon"><GearSixIcon size={18} weight="duotone" /></span> Settings
          </NavLink>
          <NavLink to="/system" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            <span className="nav-icon"><PulseIcon size={18} weight="duotone" /></span> System
            {status && status.alert_count > 0 ? (
              <span className="nav-badge">{status.alert_count}</span>
            ) : null}
          </NavLink>
        </nav>
        {auth?.status.mode === "login" && (
          <button
            type="button"
            className="nav-link sidebar-signout"
            title={auth.status.username ? `Signed in as ${auth.status.username}` : undefined}
            onClick={() => void auth.signOut()}
          >
            <span className="nav-icon"><SignOutIcon size={18} weight="duotone" /></span> Sign out
          </button>
        )}
      </aside>
      <div className="main-col">
        <UpdateBanner />
        <StatusStrip />
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      {/* AuthGate sits outside SystemStatusProvider so the status poller
          doesn't hammer 401s while the login screen is up. */}
      <AuthGate>
        <SystemStatusProvider>
          <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<PodcastsPage />} />
            <Route path="/podcasts/:id" element={<PodcastDetailPage />} />
            <Route path="/episodes/:id" element={<EpisodeDetailPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/system" element={<SystemPage />} />
            <Route path="*" element={<div className="empty-hero"><h2>Not found</h2><p>That page does not exist.</p></div>} />
          </Route>
          </Routes>
        </SystemStatusProvider>
      </AuthGate>
    </ToastProvider>
  );
}
