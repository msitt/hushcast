import { Link } from "react-router-dom";
import { useSystemStatus } from "./SystemStatusContext";
import { ThemeToggle } from "./ThemeToggle";

export function StatusStrip() {
  const status = useSystemStatus();

  return (
    <div className="status-strip">
      {status ? (
        <>
          <span className={`strip-item strip-dot ${status.processing ? "dot-active" : "dot-idle"}`} />
          <span className="strip-item">
            Queue: <strong>{status.queue_depth}</strong>
          </span>
          {status.active.length > 0 ? (
            <span className="strip-item strip-active-jobs">
              {status.active.map((a, i) => (
                <Link key={`${a.episode_id}-${i}`} to={`/episodes/${a.episode_id}`} className="strip-job">
                  ep {a.episode_id}: {a.step}
                </Link>
              ))}
            </span>
          ) : (
            <span className="strip-item strip-muted">idle</span>
          )}
        </>
      ) : (
        <span className="strip-item strip-muted">Connecting to server…</span>
      )}
      <span className="strip-right">
        <ThemeToggle />
      </span>
    </div>
  );
}
