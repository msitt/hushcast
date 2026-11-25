import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { PlusIcon } from "@phosphor-icons/react";
import {
  api,
  type EvalEstimate,
  type EvalModelIn,
  type EvalModelOut,
  type EvalRunOut,
  type Settings,
  type TestResult,
} from "../../api/client";
import { ConfirmDialog, Modal } from "../../components/Modal";
import { useToasts } from "../../components/Toasts";
import { useAsyncData } from "../../hooks";
import { formatCost, formatDateTime, formatTokens } from "../../format";
import { SECRET_MASK, testBadge } from "../settings/shared";
import { SecretInput } from "../../components/SecretInput";

const ACTIVE = ["queued", "running"];

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// ---------------------------------------------------------------------------
// eval connection

function ConnectionPanel({ onSaved }: { onSaved: () => void }) {
  const { toastError, toastSuccess } = useToasts();
  const { data: settings, reload } = useAsyncData<Settings>(() => api.getSettings(), []);
  const [url, setUrl] = useState("");
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!settings) return;
    setUrl(settings.eval_llm_base_url ?? "");
    setKey(settings.eval_llm_api_key ?? "");
  }, [settings]);

  if (!settings) return null;
  const dirty = url !== (settings.eval_llm_base_url ?? "") || key !== (settings.eval_llm_api_key ?? "");

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      await api.putSettings({ eval_llm_base_url: url.trim(), eval_llm_api_key: key });
      toastSuccess("Eval connection saved");
      reload();
      onSaved();
    } catch (err) {
      toastError(errorText(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="panel">
      <h2 className="panel-title">Eval connection</h2>
      <form className="panel-body form-stack" onSubmit={save}>
        <p className="field-hint">
          Where eval runs send their requests. Leave both blank to use the production connection from Settings.
        </p>
        <div className="form-row">
          <label className="field">
            <span className="field-label">Base URL</span>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder={settings.llm_base_url || "https://gateway.example/v1"}
            />
            <span className="field-hint">Blank uses the production URL.</span>
          </label>
          <label className="field">
            <span className="field-label">API key</span>
            <SecretInput
              value={key}
              onChange={(e) => setKey(e.target.value)}
              onFocus={() => key === SECRET_MASK && setKey("")}
              reveal={async () => (await api.revealSecret("eval_llm_api_key")).value}
              placeholder="Blank uses the production key"
            />
            <span className="field-hint">
              A key is never sent to a different endpoint than the one it was entered for.
            </span>
          </label>
        </div>
        <div className="detail-actions">
          <button type="submit" className="btn btn-small btn-primary" disabled={saving || !dirty}>
            {saving ? "Saving…" : "Save connection"}
          </button>
        </div>
      </form>
    </section>
  );
}

// ---------------------------------------------------------------------------
// model dialog

interface ModelDraft {
  name: string;
  model: string;
  base_url: string;
  api_key: string;
  temperature: string;
  extra_params: string;
  input_per_mtok: string;
  output_per_mtok: string;
}

function toDraft(m: EvalModelOut | null): ModelDraft {
  return {
    name: m?.name ?? "",
    model: m?.model ?? "",
    base_url: m?.base_url ?? "",
    api_key: m?.has_api_key ? SECRET_MASK : "",
    temperature: m?.temperature != null ? String(m.temperature) : "",
    extra_params: m?.extra_params ? JSON.stringify(m.extra_params, null, 2) : "",
    input_per_mtok: m?.input_per_mtok != null ? String(m.input_per_mtok) : "",
    output_per_mtok: m?.output_per_mtok != null ? String(m.output_per_mtok) : "",
  };
}

function numberOrNull(text: string, label: string): number | null {
  if (!text.trim()) return null;
  const n = Number(text);
  if (!isFinite(n)) throw new Error(`${label} must be a number`);
  return n;
}

function fromDraft(d: ModelDraft): EvalModelIn {
  let extra: Record<string, unknown> | null = null;
  if (d.extra_params.trim()) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(d.extra_params);
    } catch {
      throw new Error("Extra request parameters must be valid JSON");
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
      throw new Error("Extra request parameters must be a JSON object");
    extra = parsed as Record<string, unknown>;
  }
  return {
    name: d.name.trim(),
    model: d.model.trim(),
    base_url: d.base_url.trim() || null,
    // the mask means "keep the stored key"
    api_key: d.api_key === SECRET_MASK ? null : d.api_key,
    temperature: numberOrNull(d.temperature, "Temperature"),
    extra_params: extra,
    input_per_mtok: numberOrNull(d.input_per_mtok, "Input price"),
    output_per_mtok: numberOrNull(d.output_per_mtok, "Output price"),
  };
}

function ModelDialog({
  model,
  defaultUrl,
  onClose,
  onSaved,
}: {
  model: EvalModelOut | null;
  defaultUrl: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [d, setD] = useState<ModelDraft>(() => toDraft(model));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const set = (k: keyof ModelDraft) => (e: { target: { value: string } }) => setD((p) => ({ ...p, [k]: e.target.value }));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const body = fromDraft(d);
      if (model) await api.updateEvalModel(model.id, body);
      else await api.createEvalModel(body);
      onSaved();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={model ? "Edit model" : "Add model"} onClose={onClose} wide>
      <form className="form-stack" onSubmit={submit}>
        <div className="form-row">
          <label className="field">
            <span className="field-label">Name</span>
            <input value={d.name} onChange={set("name")} placeholder="GLM 5.3 Flash" required autoFocus />
            <span className="field-hint">How it appears in runs and results.</span>
          </label>
          <label className="field">
            <span className="field-label">Model ID</span>
            <input value={d.model} onChange={set("model")} placeholder="openrouter/z-ai/glm-5.3-flash" required />
            <span className="field-hint">Exactly as the provider or gateway expects it.</span>
          </label>
        </div>
        <div className="form-row">
          <label className="field">
            <span className="field-label">Base URL (optional)</span>
            <input value={d.base_url} onChange={set("base_url")} placeholder={defaultUrl} />
            <span className="field-hint">Only for a provider other than the eval connection.</span>
          </label>
          <label className="field">
            <span className="field-label">API key (optional)</span>
            <SecretInput
              value={d.api_key}
              onChange={set("api_key")}
              onFocus={() => d.api_key === SECRET_MASK && setD((p) => ({ ...p, api_key: "" }))}
              reveal={model ? async () => (await api.revealEvalModelKey(model.id)).value : undefined}
            />
            <span className="field-hint">
              Overrides the eval connection's key. Required when the base URL above is set.
            </span>
          </label>
        </div>
        <div className="form-row">
          <label className="field">
            <span className="field-label">Temperature (optional)</span>
            <input type="number" step="0.1" value={d.temperature} onChange={set("temperature")} placeholder="Production setting" />
          </label>
          <label className="field">
            <span className="field-label">Input price (optional)</span>
            <input type="number" step="any" min="0" value={d.input_per_mtok} onChange={set("input_per_mtok")} placeholder="USD per 1M tokens" />
          </label>
          <label className="field">
            <span className="field-label">Output price (optional)</span>
            <input type="number" step="any" min="0" value={d.output_per_mtok} onChange={set("output_per_mtok")} placeholder="USD per 1M tokens" />
          </label>
        </div>
        <p className="field-hint">
          Prices are in USD per million tokens and only a fallback. A cost the provider reports with each response is
          always used first, and a reported cost of zero counts as free.
        </p>
        <label className="field">
          <span className="field-label">Extra request parameters, JSON (optional)</span>
          <textarea
            className="mono"
            rows={3}
            value={d.extra_params}
            onChange={set("extra_params")}
            placeholder='{"reasoning": {"effort": "low"}}'
          />
          <span className="field-hint">Added to every request body, for things like reasoning effort.</span>
        </label>
        {error && <div className="inline-error">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : model ? "Save" : "Add model"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// runs

const STATUS_CHIP: Record<string, string> = {
  queued: "chip-gray",
  running: "chip-blue",
  done: "chip-green",
  cancelled: "chip-dim",
  interrupted: "chip-orange",
  failed: "chip-red",
};

function RunRow({ run, busy, act }: { run: EvalRunOut; busy: boolean; act: (label: string, fn: () => Promise<unknown>) => void }) {
  const pct = run.total > 0 ? Math.min(100, (100 * run.done) / run.total) : 0;
  const active = ACTIVE.includes(run.status);
  return (
    <tr>
      <td className="cell-title">
        {run.name}
        <div className="field-hint mono">{run.model}</div>
      </td>
      <td className="cell-nowrap">
        <span className={`chip chip-tiny ${STATUS_CHIP[run.status] ?? "chip-gray"}`}>{run.status}</span>
        {run.hints_mode === "none" && <span className="chip chip-tiny chip-gray eval-chip-gap">no hints</span>}
      </td>
      <td className="eval-progress-cell">
        <div
          className="eval-progress"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={run.total}
          aria-valuenow={run.done}
          aria-label={`${run.name} progress`}
        >
          <div className="eval-progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="field-hint">
          {run.done} of {run.total} detections
          {run.errors > 0 && ` · ${run.errors} failed`}
        </div>
        {run.error && <div className="job-error-inline">{run.error}</div>}
      </td>
      <td className="num cell-nowrap">
        {run.done > 0 ? `${formatTokens(run.prompt_tokens)} in · ${formatTokens(run.completion_tokens)} out` : "-"}
      </td>
      <td
        className="num cell-nowrap"
        title={
          run.done > 0 && run.cost_usd == null
            ? "The provider reported no cost for some calls and the model has no prices set"
            : undefined
        }
      >
        {run.done > 0 ? formatCost(run.cost_usd) : "-"}
      </td>
      <td className="cell-nowrap">{formatDateTime(run.started_at ?? run.created_at)}</td>
      <td className="cell-action cell-nowrap">
        {active && (
          <button className="btn btn-small" disabled={busy} onClick={() => act("Run cancelled", () => api.cancelEvalRun(run.id))}>
            Cancel
          </button>
        )}
        {["cancelled", "interrupted", "failed"].includes(run.status) && run.model_id != null && (
          <button
            className="btn btn-small"
            disabled={busy}
            title="Continue this run. Only the detections that are missing are made."
            onClick={() => act("Run resumed", () => api.resumeEvalRun(run.id))}
          >
            Resume
          </button>
        )}
        {!active && (
          <button className="btn btn-small eval-chip-gap" disabled={busy} onClick={() => act("Run deleted", () => api.deleteEvalRun(run.id))}>
            Delete
          </button>
        )}
      </td>
    </tr>
  );
}

export function RunsTab() {
  const { toastError, toastSuccess } = useToasts();
  const { data: models, reload: reloadModels } = useAsyncData<EvalModelOut[]>(() => api.evalModels(), []);
  const [anyActive, setAnyActive] = useState(true);
  const { data: runs, reload: reloadRuns } = useAsyncData<EvalRunOut[]>(() => api.evalRuns(), [], {
    intervalMs: anyActive ? 2500 : 0,
  });
  useEffect(() => {
    if (runs) setAnyActive(runs.some((r) => ACTIVE.includes(r.status)));
  }, [runs]);

  const [editing, setEditing] = useState<EvalModelOut | "new" | null>(null);
  const [deleting, setDeleting] = useState<EvalModelOut | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [tests, setTests] = useState<Record<number, TestResult | "pending">>({});
  const [repeats, setRepeats] = useState(3);
  const [hints, setHints] = useState<"current" | "none">("current");
  const [estimate, setEstimate] = useState<EvalEstimate | "loading" | null>(null);
  const [busy, setBusy] = useState(false);

  const chosen = (models ?? []).filter((m) => selected.has(m.id));

  const act = (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    fn()
      .then(() => {
        toastSuccess(label);
        setAnyActive(true);
        reloadRuns();
      })
      .catch((err: unknown) => toastError(errorText(err)))
      .finally(() => setBusy(false));
  };

  const test = async (m: EvalModelOut) => {
    setTests((t) => ({ ...t, [m.id]: "pending" }));
    try {
      const r = await api.testEvalModel(m.id);
      setTests((t) => ({ ...t, [m.id]: r }));
    } catch (err) {
      setTests((t) => ({ ...t, [m.id]: { ok: false, message: errorText(err) } }));
    }
  };

  const openStart = async () => {
    setEstimate("loading");
    try {
      setEstimate(await api.evalEstimate(repeats));
    } catch (err) {
      setEstimate(null);
      toastError(errorText(err));
    }
  };

  const start = async () => {
    setBusy(true);
    try {
      await api.startEvalRuns({ model_ids: chosen.map((m) => m.id), repeats, hints_mode: hints });
      toastSuccess(`${chosen.length} run${chosen.length > 1 ? "s" : ""} started`);
      setEstimate(null);
      setSelected(new Set());
      setAnyActive(true);
      reloadRuns();
    } catch (err) {
      toastError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const removeModel = async (m: EvalModelOut) => {
    setBusy(true);
    try {
      await api.deleteEvalModel(m.id);
      toastSuccess("Model removed");
      setDeleting(null);
      setSelected((s) => new Set([...s].filter((id) => id !== m.id)));
      reloadModels();
      reloadRuns();
    } catch (err) {
      toastError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (id: number) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const defaultUrl = models?.find((m) => !m.base_url)?.effective_base_url ?? "";

  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <h2 className="panel-title">Models</h2>
          <button className="btn btn-small" onClick={() => setEditing("new")}>
            <PlusIcon size={13} weight="bold" /> Add model
          </button>
        </div>
        {!models ? (
          <div className="loading loading-subtle">Loading…</div>
        ) : models.length === 0 ? (
          <div className="empty-small">
            No models yet. Add the ones you want to compare. A model only needs a name and its model ID when it is
            reachable through the eval connection.
          </div>
        ) : (
          <>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th aria-label="Include in run"></th>
                    <th>Name</th>
                    <th>Model ID</th>
                    <th>Endpoint</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((m) => {
                    const t = tests[m.id];
                    return (
                      <tr key={m.id}>
                        <td className="eval-check-cell">
                          <input
                            type="checkbox"
                            checked={selected.has(m.id)}
                            onChange={() => toggle(m.id)}
                            aria-label={`Include ${m.name} in the next run`}
                          />
                        </td>
                        <td className="cell-title">{m.name}</td>
                        <td className="mono">{m.model}</td>
                        <td>
                          {m.effective_base_url}
                          {m.base_url ? (
                            <span className="chip chip-tiny chip-gray eval-chip-gap">own endpoint</span>
                          ) : (
                            m.has_api_key && <span className="chip chip-tiny chip-gray eval-chip-gap">own key</span>
                          )}
                          {t && t !== "pending" && <div>{testBadge(t)}</div>}
                        </td>
                        <td className="cell-action cell-nowrap">
                          <button className="btn btn-small" disabled={t === "pending"} onClick={() => void test(m)}>
                            {t === "pending" ? "Testing…" : "Test"}
                          </button>
                          <button className="btn btn-small eval-chip-gap" onClick={() => setEditing(m)}>
                            Edit
                          </button>
                          <button className="btn btn-small eval-chip-gap" onClick={() => setDeleting(m)}>
                            Delete
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="eval-start">
              <label className="field">
                <span className="field-label">Repeats</span>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={repeats}
                  onChange={(e) => setRepeats(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
                />
              </label>
              <label className="field">
                <span className="field-label">Hints</span>
                <select value={hints} onChange={(e) => setHints(e.target.value as "current" | "none")}>
                  <option value="current">Current hints</option>
                  <option value="none">No hints</option>
                </select>
              </label>
              <button
                className="btn btn-primary"
                disabled={chosen.length === 0 || busy || estimate === "loading"}
                onClick={() => void openStart()}
              >
                {chosen.length === 0
                  ? "Select models to run"
                  : `Run ${chosen.length} model${chosen.length > 1 ? "s" : ""}…`}
              </button>
            </div>
            <p className="field-hint">
              Each episode is detected once per repeat, because a model's output varies between calls. Runs continue in
              the background and share the provider with regular processing.
            </p>
          </>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2 className="panel-title">Runs</h2>
          {runs && runs.some((r) => r.done > 0) && (
            <Link className="btn btn-small" to="/evals/results">
              Compare results
            </Link>
          )}
        </div>
        {!runs ? (
          <div className="loading loading-subtle">Loading…</div>
        ) : runs.length === 0 ? (
          <div className="empty-small">No runs yet.</div>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Status</th>
                  <th>Progress</th>
                  <th className="num">Tokens</th>
                  <th className="num">Cost</th>
                  <th>Started</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <RunRow key={r.id} run={r} busy={busy} act={act} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <ConnectionPanel onSaved={reloadModels} />

      {editing && (
        <ModelDialog
          model={editing === "new" ? null : editing}
          defaultUrl={defaultUrl}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            toastSuccess("Model saved");
            reloadModels();
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="Remove model"
          message={`Remove ${deleting.name}? Its finished runs and their results are kept, but they can no longer be resumed.`}
          confirmLabel="Remove"
          danger
          busy={busy}
          onConfirm={() => void removeModel(deleting)}
          onCancel={() => setDeleting(null)}
        />
      )}
      {estimate && estimate !== "loading" && (
        <ConfirmDialog
          title={`Run ${chosen.length} model${chosen.length > 1 ? "s" : ""}`}
          message={
            estimate.episodes === 0 ? (
              "There are no verified episodes yet. Label some first."
            ) : (
              <>
                {chosen.map((m) => m.name).join(", ")} over {estimate.episodes} verified episode
                {estimate.episodes > 1 ? "s" : ""}, {repeats} time{repeats > 1 ? "s" : ""} each
                {hints === "none" ? ", without hints" : ""}. That is {estimate.detections * chosen.length} detections and
                about {formatTokens(estimate.input_tokens * chosen.length)} input tokens in total
                {chosen.length > 1 ? ` (${formatTokens(estimate.input_tokens)} per model)` : ""}. Output and reasoning
                tokens come on top and depend on the model.
              </>
            )
          }
          confirmLabel="Start"
          busy={busy}
          onConfirm={() => (estimate.episodes === 0 ? setEstimate(null) : void start())}
          onCancel={() => setEstimate(null)}
        />
      )}
    </>
  );
}
