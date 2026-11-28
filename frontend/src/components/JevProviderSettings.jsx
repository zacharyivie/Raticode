import { useEffect, useRef, useState } from "react";
import { apiJson } from "../lib/api";
import { dateTime } from "../lib/timeText.js";

// Jev is a classification provider for jev workflow nodes, not a coding provider, so it
// gets its own card. The key goes to the backend once and lives in the system keychain;
// no response ever returns it, so the field always starts empty. Inside Settings > Providers
// the card sits in a group that supplies its heading (labelledBy) and summarizes the status
// it reports (onStatusChange).
export default function JevProviderSettings({ labelledBy = "", onStatusChange } = {}) {
  const [status, setStatus] = useState(null);
  const reportStatus = useRef(onStatusChange);
  reportStatus.current = onStatusChange;
  useEffect(() => { if (status) reportStatus.current?.(status); }, [status]);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const request = (path, options = {}) => apiJson(path, { ...options, failure: "Jev settings couldn't be saved." });

  useEffect(() => {
    let live = true;
    request("/classifiers/jev")
      .then(payload => { if (live) setStatus(payload); })
      .catch(failure => { if (live) setError(failure.message || "Jev settings couldn't be loaded."); });
    return () => { live = false; };
  }, []);

  async function run(label, path, options) {
    setBusy(label);
    setError("");
    try {
      setStatus(await request(path, options));
      return true;
    } catch (failure) {
      setError(failure.message || "Jev settings couldn't be saved.");
      return false;
    } finally {
      setBusy("");
    }
  }

  async function saveKey(event) {
    event.preventDefault();
    if (!key.trim()) return;
    if (await run("save", "/classifiers/jev/key", { method: "POST", body: { key } })) setKey("");
  }

  const configured = Boolean(status?.configured);
  const enabled = status?.enabled !== false;
  const models = status?.models ?? [];
  return <section aria-labelledby={labelledBy || "classification-models-heading"} className={labelledBy ? "space-y-2" : "space-y-2 border-t border-line pt-3"}>
    {labelledBy ? null : <h3 id="classification-models-heading" className="text-xs font-semibold">Classification models</h3>}
    <p className="text-xs text-muted">{"Used by jev workflow nodes to answer yes/no and multiple-choice questions. These aren't coding providers, so Rem's model picker, Organizations and agent nodes don't list them."}</p>
    <div className="flex items-center justify-between gap-3 pt-1">
      <h4 className="text-xs font-semibold">Jev (TypeSafe)</h4>
      {configured ? <select aria-label="Jev status" className="rounded border border-line bg-white px-2 py-1 text-xs" disabled={Boolean(busy)}
        value={enabled ? "enabled" : "disabled"} onChange={event => run("toggle", "/classifiers/jev/settings", { method: "POST", body: { enabled: event.target.value === "enabled" } })}>
        <option value="enabled">Enabled</option><option value="disabled">Disabled</option>
      </select> : null}
    </div>
    <p role="status" className="text-xs text-muted">{status === null && !error ? "Loading Jev settings…" : configured ? "Key saved in the system keychain." : "No key saved."}</p>
    <form className="flex items-end gap-2" onSubmit={saveKey}>
      <label className="block min-w-0 flex-1 text-xs text-muted">{configured ? "Replace API key" : "API key"}
        <input aria-label="Jev API key" type="password" autoComplete="off" spellCheck={false} className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink"
          placeholder="Paste a TypeSafe API key" value={key} disabled={Boolean(busy)} onChange={event => setKey(event.target.value)} />
      </label>
      <button type="submit" className="rounded border border-line px-2 py-1.5 text-xs font-semibold text-brand disabled:opacity-50" disabled={Boolean(busy) || !key.trim()}>{busy === "save" ? "Saving…" : "Save"}</button>
    </form>
    {configured ? <div className="flex flex-wrap gap-3">
      <button type="button" className="text-xs font-semibold text-brand disabled:opacity-50" disabled={Boolean(busy) || !enabled} onClick={() => run("check", "/classifiers/jev/check", { method: "POST", body: {} })}>{busy === "check" ? "Checking key…" : "Check key"}</button>
      <button type="button" className="text-xs font-semibold text-red-700 disabled:opacity-50" disabled={Boolean(busy)} onClick={() => run("remove", "/classifiers/jev/key", { method: "DELETE" })}>{busy === "remove" ? "Removing…" : "Remove key"}</button>
    </div> : null}
    {status?.checkedAt ? <p className={`text-xs ${status.checkError ? "text-red-700" : "text-muted"}`}>
      {status.checkError ? `Check failed: ${status.checkError}` : "Jev accepted the key."} Checked {dateTime(status.checkedAt)}.
    </p> : null}
    {models.length ? <p className="text-xs text-muted">Models Jev lists: {models.join(", ")}</p> : null}
    {status?.defaultModel ? <p className="text-xs text-muted">Nodes use {status.defaultModel} unless they set model. Text a jev node classifies is sent to TypeSafe, and each call is billed by input tokens.</p> : null}
    {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
  </section>;
}
