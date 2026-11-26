import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { ConfirmDialog } from "../../components/Modal";

type Plan = { redone: number; new: number };

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** Confirms retrying part of a finished run. Asks the server what it would redo
 * first, so the count shown is exactly what runs, and nothing is deleted until
 * the user confirms. */
export function RetryDialog({
  runId,
  runName,
  detection,
  onDone,
  onClose,
}: {
  runId: number;
  runName: string;
  /** one detection (repeat is 0-based). Unset: the failed and stale detections. */
  detection?: { episodeId: number; repeat: number };
  onDone: () => void;
  onClose: () => void;
}) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const body = detection ? { episode_id: detection.episodeId, repeat: detection.repeat } : {};

  useEffect(() => {
    api
      .retryEvalRun(runId, { ...body, dry_run: true })
      .then(setPlan)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
    // the request is fixed for the dialog's lifetime
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, detection?.episodeId, detection?.repeat]);

  const confirm = () => {
    setBusy(true);
    api
      .retryEvalRun(runId, body)
      .then(() => {
        onDone();
        onClose();
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
        setBusy(false);
      });
  };

  const title = detection ? `Retry ${runName}, run ${detection.repeat + 1}` : `Retry ${runName}`;
  if (error) {
    return <ConfirmDialog title={title} message={error} confirmLabel="Close" onConfirm={onClose} onCancel={onClose} />;
  }
  if (!plan) {
    return <ConfirmDialog title={title} message="Checking what would run…" busy onConfirm={onClose} onCancel={onClose} />;
  }
  const what =
    detection
      ? "Redo this detection"
      : `Redo ${plural(plan.redone, "detection")} that failed or were made before their label changed`;
  return (
    <ConfirmDialog
      title={title}
      message={
        <>
          {what}
          {plan.new > 0 && `, plus ${plural(plan.new, "new detection")} for episodes labeled since the run`}. The
          old {detection ? "result is" : "results are"} deleted first. This calls the model and costs money.
        </>
      }
      confirmLabel={`Run ${plural(plan.redone + plan.new, "detection")}`}
      busy={busy}
      onConfirm={confirm}
      onCancel={onClose}
    />
  );
}
