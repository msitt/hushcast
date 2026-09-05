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
import { SettingsLayout } from "./pages/settings/SettingsLayout";
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
          <NavLink to="/" end aria-label="Podcasts" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            <span className="nav-icon"><MicrophoneIcon size={18} weight="duotone" /></span>
            <span className="nav-label">Podcasts</span>
          </NavLink>
          <NavLink to="/settings" aria-label="Settings" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            <span className="nav-icon"><GearSixIcon size={18} weight="duotone" /></span>
            <span className="nav-label">Settings</span>
          </NavLink>
          <NavLink to="/system" aria-label="System" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            <span className="nav-icon"><PulseIcon size={18} weight="duotone" /></span>
            <span className="nav-label">System</span>
            {status && status.alert_count > 0 ? (
              <span className="nav-badge">{status.alert_count}</span>
            ) : null}
          </NavLink>
        </nav>
        {auth?.status.mode === "login" && (
          <button
            type="button"
            className="nav-link sidebar-signout"
            aria-label="Sign out"
            title={auth.status.username ? `Signed in as ${auth.status.username}` : undefined}
            onClick={() => void auth.signOut()}
          >
            <span className="nav-icon"><SignOutIcon size={18} weight="duotone" /></span>
            <span className="nav-label">Sign out</span>
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
            <Route path="/settings/*" element={<SettingsLayout />} />
            <Route path="/system" element={<SystemPage />} />
            <Route path="*" element={<div className="empty-hero"><h2>Not found</h2><p>That page does not exist.</p></div>} />
          </Route>
          </Routes>
        </SystemStatusProvider>
      </AuthGate>
    </ToastProvider>
  );
}
