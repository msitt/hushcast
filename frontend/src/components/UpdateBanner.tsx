import { useEffect, useState } from "react";
import { XIcon } from "@phosphor-icons/react";
import { useSystemStatus } from "./SystemStatusContext";

/**
 * Banner shown when the backend reports a version different from the one baked
 * into this bundle, i.e. the server was updated under a long-open tab. Any
 * mismatch (upgrade or rollback) means a refresh loads the matching bundle.
 */
export function UpdateBanner() {
  const status = useSystemStatus();
  // Require the mismatch to persist across consecutive polls so a mid-deploy
  // container swap can't flash the banner.
  const [mismatchPolls, setMismatchPolls] = useState(0);
  const [dismissedVersion, setDismissedVersion] = useState<string | null>(null);

  const serverVersion = status?.version ?? null;
  const mismatch = serverVersion !== null && serverVersion !== __APP_VERSION__;

  useEffect(() => {
    if (!status) return;
    setMismatchPolls((n) => (mismatch ? n + 1 : 0));
  }, [status, mismatch]);

  if (!import.meta.env.PROD) return null;
  if (!mismatch || mismatchPolls < 2 || serverVersion === dismissedVersion) return null;

  return (
    <div className="update-banner">
      <span>
        Hushcast was updated to v{serverVersion} (this page is running v{__APP_VERSION__}).
      </span>
      <button className="btn btn-small" onClick={() => window.location.reload()}>
        Refresh
      </button>
      <button
        className="update-banner-close"
        aria-label="Dismiss"
        onClick={() => setDismissedVersion(serverVersion)}
      >
        <XIcon size={14} weight="bold" />
      </button>
    </div>
  );
}
