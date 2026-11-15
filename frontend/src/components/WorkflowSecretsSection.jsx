import { useCallback, useEffect, useRef, useState } from "react";
import { FileKey, KeyRound, Plus, Trash2 } from "lucide-react";
import { apiUrl } from "../lib/api";

// Values go to the backend once and live in the system keychain. No response returns
// one, so the value field always starts empty and is cleared after saving.
const INPUT_CLASS =
  "mt-1 h-9 w-full rounded-lg border border-line bg-white px-2.5 text-[13px] text-ink outline-none transition focus:border-indigo-500";
const SMALL_BUTTON_CLASS =
  "inline-flex h-7 items-center gap-1 rounded-md border border-line bg-white px-2 text-[11px] font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50";
const STATUS = {
  saved: ["Saved", "border-emerald-500/60 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"],
  "secrets folder": ["Secrets folder", "border-sky-500/60 bg-sky-500/10 text-sky-700 dark:text-sky-300"],
  environment: ["From environment", "border-line bg-slate-100 text-slate-700"],
  missing: ["Missing", "border-amber-500/60 bg-amber-500/10 text-amber-700 dark:text-amber-300"],
};

async function request(path, options) {
  const response = await fetch(apiUrl(`/rattish/secrets${path}`), options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

export const secretsApi = {
  load: (workflowId) => request(workflowId ? `?workflowId=${encodeURIComponent(workflowId)}` : ""),
  save: (workflowId, name, value) =>
    request(workflowId ? `?workflowId=${encodeURIComponent(workflowId)}` : "", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, value }),
    }),
  remove: (workflowId, name) =>
    request(
      `/${encodeURIComponent(name)}${workflowId ? `?workflowId=${encodeURIComponent(workflowId)}` : ""}`,
      { method: "DELETE" },
    ),
};

function formatSaved(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : `Saved ${date.toLocaleString()}`;
}

function StatusPill({ status }) {
  const [label, tone] = STATUS[status] ?? STATUS.missing;
  return (
    <span className={`inline-flex shrink-0 items-center rounded-full border px-1.5 py-px text-[10px] font-semibold ${tone}`}>
      {label}
    </span>
  );
}

function SecretForm({ busy, initialName, nameLocked, onCancel, onSave }) {
  const [name, setName] = useState(initialName);
  const [value, setValue] = useState("");
  const valueRef = useRef(null);

  useEffect(() => {
    if (nameLocked) valueRef.current?.focus();
  }, [nameLocked]);

  return (
    <form
      aria-label={nameLocked ? `Replace ${name}` : "Add secret"}
      className="space-y-2 rounded-lg border border-brand/30 bg-white p-3 shadow-sm"
      onSubmit={async (event) => {
        event.preventDefault();
        if (!name.trim() || !value) return;
        if (await onSave(name.trim(), value)) setValue("");
      }}
    >
      <label className="block">
        <span className="text-xs font-medium text-muted">Name</span>
        <input
          autoComplete="off"
          className={`${INPUT_CLASS} font-mono`}
          disabled={busy || nameLocked}
          placeholder="SERVICE_TOKEN"
          spellCheck={false}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className="block">
        <span className="text-xs font-medium text-muted">Value</span>
        <input
          ref={valueRef}
          autoComplete="off"
          className={INPUT_CLASS}
          disabled={busy}
          placeholder="Paste the value"
          spellCheck={false}
          type="password"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
      </label>
      <p className="text-xs leading-5 text-muted">
        Raticode won&apos;t show this value again. For a value with line breaks, use{" "}
        <code>Raticode rattish secrets set NAME --stdin</code>.
      </p>
      <div className="flex justify-end gap-2">
        <button className={SMALL_BUTTON_CLASS} disabled={busy} type="button" onClick={onCancel}>
          Cancel
        </button>
        <button
          className="inline-flex h-7 items-center rounded-md bg-brand px-2.5 text-[11px] font-semibold text-white transition hover:bg-brand-strong disabled:cursor-not-allowed disabled:opacity-50"
          disabled={busy || !name.trim() || !value}
          type="submit"
        >
          {busy ? "Saving…" : "Save secret"}
        </button>
      </div>
    </form>
  );
}

export function WorkflowSecretsSection({ active = true, workflowId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [form, setForm] = useState(null);
  const [confirming, setConfirming] = useState("");
  const generation = useRef(0);

  const refresh = useCallback(async () => {
    const current = ++generation.current;
    try {
      const payload = await secretsApi.load(workflowId);
      if (current === generation.current) {
        setData(payload);
        setError("");
      }
    } catch (failure) {
      if (current === generation.current) setError(failure.message);
    }
  }, [workflowId]);

  useEffect(() => {
    setForm(null);
    setConfirming("");
    if (active) void refresh();
  }, [active, refresh]);

  async function save(name, value) {
    setBusy("save");
    setError("");
    try {
      setData(await secretsApi.save(workflowId, name, value));
      setForm(null);
      return true;
    } catch (failure) {
      setError(failure.message);
      return false;
    } finally {
      setBusy("");
    }
  }

  async function remove(name) {
    setBusy(`remove:${name}`);
    setError("");
    try {
      setData(await secretsApi.remove(workflowId, name));
      setConfirming("");
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy("");
    }
  }

  const secrets = data?.secrets ?? [];
  const used = data?.used ?? [];
  const folder = data?.folder ?? [];
  return (
    <section aria-label="Secrets" className="space-y-3 rounded-lg border border-line p-3" data-workflow-secrets="true">
      <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-muted">Secrets</h3>
      <p className="text-xs leading-5 text-muted">
        Kept in this computer&apos;s system keychain and shared by your workflows.{" "}
        <code>secret[&quot;NAME&quot;]</code> reads a saved value first, then a headless server&apos;s secrets folder,
        then the environment variable with the same name.
      </p>

      {used.length ? (
        <div className="space-y-1.5">
          <h4 className="text-xs font-semibold text-slate-700">This workflow uses</h4>
          <ul aria-label="Secrets this workflow uses" className="space-y-1">
            {used.map((item) => (
              <li key={item.name} className="flex items-center justify-between gap-2 rounded-lg border border-line px-2.5 py-1.5">
                <span className="min-w-0 truncate font-mono text-[12px] text-ink">{item.name}</span>
                <span className="flex shrink-0 items-center gap-1.5">
                  <StatusPill status={item.status} />
                  {item.status !== "saved" ? (
                    <button
                      className={SMALL_BUTTON_CLASS}
                      disabled={Boolean(busy)}
                      title={`Save ${item.name} in the keychain`}
                      type="button"
                      onClick={() => setForm({ name: item.name, locked: true })}
                    >
                      Save value
                    </button>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="space-y-1.5">
        <h4 className="text-xs font-semibold text-slate-700">Saved on this computer</h4>
        {data === null && !error ? <p className="text-xs text-muted" role="status">Loading secrets…</p> : null}
        {data !== null && !secrets.length ? (
          <p className="text-xs leading-5 text-muted">No secrets saved yet.</p>
        ) : null}
        {secrets.length ? (
          <ul aria-label="Saved secrets" className="space-y-1">
            {secrets.map((item) => (
              <li key={item.name} className="rounded-lg border border-line px-2.5 py-1.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <KeyRound aria-hidden="true" className="shrink-0 text-muted" size={12} />
                    <span className="truncate font-mono text-[12px] text-ink">{item.name}</span>
                  </span>
                  {confirming === item.name ? (
                    <span className="flex shrink-0 items-center gap-1.5">
                      <button
                        className="inline-flex h-7 items-center rounded-md bg-red-600 px-2 text-[11px] font-semibold text-white transition hover:bg-red-700 disabled:opacity-50"
                        disabled={Boolean(busy)}
                        type="button"
                        onClick={() => remove(item.name)}
                      >
                        {busy === `remove:${item.name}` ? "Removing…" : `Remove ${item.name}`}
                      </button>
                      <button className={SMALL_BUTTON_CLASS} disabled={Boolean(busy)} type="button" onClick={() => setConfirming("")}>
                        Keep
                      </button>
                    </span>
                  ) : (
                    <span className="flex shrink-0 items-center gap-1.5">
                      <button
                        className={SMALL_BUTTON_CLASS}
                        disabled={Boolean(busy)}
                        title={`Replace the saved value of ${item.name}`}
                        type="button"
                        onClick={() => setForm({ name: item.name, locked: true })}
                      >
                        Replace
                      </button>
                      <button
                        aria-label={`Remove ${item.name}`}
                        className="grid h-7 w-7 place-items-center rounded-md border border-line bg-white text-muted transition hover:border-red-200 hover:bg-red-50 hover:text-red-700 disabled:opacity-50"
                        disabled={Boolean(busy)}
                        title={`Remove ${item.name}`}
                        type="button"
                        onClick={() => setConfirming(item.name)}
                      >
                        <Trash2 size={13} />
                      </button>
                    </span>
                  )}
                </div>
                <p className="mt-0.5 text-[11px] leading-4 text-muted">
                  {[
                    formatSaved(item.updatedAt),
                    item.inSecretsFolder ? "Also in the secrets folder; the saved value wins." : "",
                    item.inEnvironment ? "Also set in the environment; the saved value wins." : "",
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      {folder.length ? (
        <div className="space-y-1.5">
          <h4 className="text-xs font-semibold text-slate-700">From the secrets folder</h4>
          <p className="text-xs leading-5 text-muted">
            Read-only files this server&apos;s backend reads when the keychain has no saved value. Replace or
            remove a file to change it.
          </p>
          <ul aria-label="Secrets folder secrets" className="space-y-1">
            {folder.map((item) => (
              <li key={item.name} className="rounded-lg border border-line px-2.5 py-1.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <FileKey aria-hidden="true" className="shrink-0 text-muted" size={12} />
                    <span className="truncate font-mono text-[12px] text-ink">{item.name}</span>
                  </span>
                  <StatusPill status="secrets folder" />
                </div>
                <p className="mt-0.5 truncate text-[11px] leading-4 text-muted">
                  {[item.file, item.alsoSaved ? "Also saved in the keychain, which wins." : ""].filter(Boolean).join(" · ")}
                </p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {form ? (
        <SecretForm
          key={`${form.name}:${form.locked}`}
          busy={busy === "save"}
          initialName={form.name}
          nameLocked={form.locked}
          onCancel={() => setForm(null)}
          onSave={save}
        />
      ) : (
        <button
          className={SMALL_BUTTON_CLASS}
          disabled={Boolean(busy)}
          type="button"
          onClick={() => setForm({ name: "", locked: false })}
        >
          <Plus size={12} />
          Add secret
        </button>
      )}
      {error ? (
        <p className="text-xs leading-5 text-red-700" role="alert">{error}</p>
      ) : null}
    </section>
  );
}
