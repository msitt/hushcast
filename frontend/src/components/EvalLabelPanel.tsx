import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRightIcon } from "@phosphor-icons/react";
import { ACTIVE_STATUSES, api, type EpisodeDetailOut, type EvalVerdict } from "../api/client";
import { formatDateTime, formatDuration, formatTimestamp } from "../format";
import { ConfirmDialog } from "./Modal";
import { useToasts } from "./Toasts";

// Detection has finished, so the segments are something a human can sign off on.
// A skipped episode only qualifies when it has segments (a dropped promo-only episode).
function canBeVerified(ep: EpisodeDetailOut): boolean {
  if (!ep.has_transcript) return false;
  if (["processed", "expired", "review"].includes(ep.status)) return true;
  return ep.status === "skipped" && ep.segments.length > 0;
}

export function EvalLabelPanel({ ep, onChanged }: { ep: EpisodeDetailOut; onChanged: () => void }) {
  const { toastError, toastSuccess } = useToasts();
  const [busy, setBusy] = useState(false);
  const [excluding, setExcluding] = useState(false);
  const [note, setNote] = useState("");
  const [confirm, setConfirm] = useState<"replace" | "remove" | null>(null);
  const [summary, setSummary] = useState<{ verified: number; excluded: number } | null>(null);
  const label = ep.eval_label;

  useEffect(() => {
    api.evalsSummary().then(setSummary, () => setSummary(null));
  }, [label?.verdict, label?.updated_at]);
  // a different episode: drop any half-finished interaction
  useEffect(() => {
    setExcluding(false);
    setConfirm(null);
    setNote("");
  }, [ep.id]);

  const settling = ACTIVE_STATUSES.includes(ep.status) || ep.status === "queued";
  const verifiable = canBeVerified(ep);
  // Nothing to say about an episode that can't be labeled and has no label.
  if (!label && !verifiable) return null;

  const save = async (verdict: EvalVerdict, noteText?: string) => {
    setBusy(true);
    try {
      await api.putEvalLabel(ep.id, { verdict, note: noteText ?? null });
      toastSuccess(verdict === "verified" ? "Saved as verified ground truth" : "Excluded from the eval set");
      setExcluding(false);
      setConfirm(null);
      setNote("");
      onChanged();
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await api.deleteEvalLabel(ep.id);
      toastSuccess("Eval label removed");
      setConfirm(null);
      onChanged();
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const verified = label?.verdict === "verified";
  const stale = verified && !label.matches_segments;
  // A correction made after the label was saved means the user is still
  // editing, and saving again is the obvious next step. A mismatch with no
  // newer correction means the segments were replaced (a re-detect), and
  // saving would overwrite checked truth with unchecked model output.
  const savedAt = label ? Date.parse(label.updated_at) : 0;
  // Optional ranges carry no timestamp (they are not corrections), so one the
  // saved label doesn't cover counts as the user still editing too.
  const optional = label?.optional ?? [];
  const optionalAdded = ep.segments.some(
    (s) => s.source === "optional" && !optional.some((o) => o[0] <= s.start_s && o[1] >= s.end_s)
  );
  const correctedSince =
    stale &&
    (optionalAdded || ep.segments.some((s) => s.corrected_at != null && Date.parse(s.corrected_at) > savedAt));
  const truth = label?.truth ?? [];
  const truthTotal = truth.reduce((sum, t) => sum + (t.end - t.start), 0);

  return (
    <section className="panel">
      <h2 className="panel-title">Eval label</h2>
      <div className="eval-label">
        {!label && (
          <p className="field-hint">
            Once the segments above are right, verify them to use this episode as ground truth when comparing detection
            models. Audio that is fine to cut or to keep, such as a network ident, can be marked optional from the
            transcript so models are not scored on it. Exclude the episode instead if it can't serve as truth, for
            example when the transcript is bad.
          </p>
        )}
        {verified && (
          <>
            <p className="eval-label-status">
              <span className="chip chip-tiny chip-green">verified</span>{" "}
              {label.promo
                ? "Promo-only episode, nothing should be cut"
                : truth.length > 0
                  ? `${truth.length} ad range${truth.length > 1 ? "s" : ""}, ${formatDuration(truthTotal)} total`
                  : "No ads"}{" "}
              <span className="field-hint">· Saved {formatDateTime(label.updated_at)}</span>
            </p>
            {!label.promo && truth.length > 0 && (
              <p className="field-hint">
                Ground truth:{" "}
                {truth.map((t) => `${formatTimestamp(t.start)}–${formatTimestamp(t.end)}`).join(" · ")}
              </p>
            )}
            {!label.promo && optional.length > 0 && (
              <p className="field-hint">
                Optional, ignored when scoring:{" "}
                {optional.map((o) => `${formatTimestamp(o[0])}–${formatTimestamp(o[1])}`).join(" · ")}
              </p>
            )}
            {label.transcript_source === "llm_prompt" && (
              <p className="field-hint">
                The transcript copy was rebuilt from the detection prompt, so it has no word timestamps.
              </p>
            )}
            {stale && (
              <div className="inline-warning">
                {correctedSince
                  ? "You corrected the segments after this label was saved. Save again to update the ground truth."
                  : "The segments above no longer match the saved ground truth, most likely because detection ran again. Evals keep using the saved copy. Replace it only after checking the new segments."}
              </div>
            )}
          </>
        )}
        {label?.verdict === "excluded" && (
          <p className="eval-label-status">
            <span className="chip chip-tiny chip-dim">excluded</span> {label.note ?? "No reason given"}{" "}
            <span className="field-hint">· Saved {formatDateTime(label.updated_at)}</span>
          </p>
        )}

        {excluding ? (
          <form
            className="eval-exclude"
            onSubmit={(e) => {
              e.preventDefault();
              void save("excluded", note);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") setExcluding(false);
            }}
          >
            <input
              autoFocus
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Reason, e.g. the transcript has another feed's ads"
              aria-label="Reason for excluding"
            />
            <button type="submit" className="btn btn-small btn-primary" disabled={busy}>
              Exclude
            </button>
            <button type="button" className="btn btn-small" disabled={busy} onClick={() => setExcluding(false)}>
              Cancel
            </button>
          </form>
        ) : (
          <div className="detail-actions">
            {!verified && (
              <button
                className="btn btn-small btn-primary"
                disabled={busy || !verifiable}
                title={
                  verifiable
                    ? "Save these segments as the episode's ground truth"
                    : settling
                      ? "Wait for processing to finish"
                      : "This episode has no finished detection to verify"
                }
                onClick={() => void save("verified")}
              >
                Verify segments
              </button>
            )}
            {stale && correctedSince && (
              <button
                className="btn btn-small btn-primary"
                disabled={busy || !verifiable}
                onClick={() => void save("verified")}
              >
                Save again
              </button>
            )}
            {stale && !correctedSince && (
              <button className="btn btn-small" disabled={busy || !verifiable} onClick={() => setConfirm("replace")}>
                Replace ground truth…
              </button>
            )}
            {label?.verdict !== "excluded" && (
              <button
                className="btn btn-small"
                disabled={busy || settling}
                onClick={() => {
                  setNote("");
                  setExcluding(true);
                }}
              >
                Exclude…
              </button>
            )}
            {label && (
              <button
                className="btn btn-small"
                disabled={busy}
                onClick={() => (verified ? setConfirm("remove") : void remove())}
              >
                Remove label
              </button>
            )}
            {ep.next_unlabeled_id != null && (
              <Link className="eval-next" to={`/episodes/${ep.next_unlabeled_id}`}>
                Next unlabeled episode <ArrowRightIcon size={13} weight="bold" />
              </Link>
            )}
          </div>
        )}
        {summary && (
          <p className="field-hint">
            <Link to="/evals">
              Eval set: {summary.verified} verified, {summary.excluded} excluded
            </Link>
          </p>
        )}
      </div>

      {confirm === "replace" && (
        <ConfirmDialog
          title="Replace ground truth"
          message="This overwrites the saved ground truth with the segments shown on this page. If they came from a new detection run, check them first, because evals will treat them as correct."
          confirmLabel="Replace"
          danger
          busy={busy}
          onConfirm={() => void save("verified")}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm === "remove" && (
        <ConfirmDialog
          title="Remove eval label"
          message="This deletes the saved ground truth and the label's copy of the transcript. The episode leaves the eval set."
          confirmLabel="Remove"
          danger
          busy={busy}
          onConfirm={() => void remove()}
          onCancel={() => setConfirm(null)}
        />
      )}
    </section>
  );
}
