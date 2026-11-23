import { NavLink, Route, Routes } from "react-router-dom";
import { DatasetTab } from "./evals/Dataset";
import { RunsTab } from "./evals/Runs";
import { ResultsTab } from "./evals/Results";

const DOCS_URL = "https://github.com/msitt/hushcast/blob/master/docs/evals.md";

const TABS = [
  { to: "/evals", label: "Dataset", end: true },
  { to: "/evals/runs", label: "Runs", end: false },
  { to: "/evals/results", label: "Results", end: false },
];

export function EvalsPage() {
  return (
    <div className="page">
      <div className="page-header">
        <h1>Evals</h1>
        <a className="btn" href={DOCS_URL} target="_blank" rel="noreferrer">
          How evals work
        </a>
      </div>
      <nav className="settings-tabs">
        {TABS.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.end}
            className={({ isActive }) => `settings-tab${isActive ? " active" : ""}`}
          >
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<DatasetTab />} />
        <Route path="runs" element={<RunsTab />} />
        <Route path="results" element={<ResultsTab />} />
      </Routes>
    </div>
  );
}
