import { useEffect, useState } from "react";
import { apiUrl } from "../lib/api";

export default function CommitMessageSettings({ capabilities }) {
  const [selection, setSelection] = useState({ provider: "", model: "" });
  const [autoCommit, setAutoCommit] = useState(false);
  const [template, setTemplate] = useState("");
  const [draft, setDraft] = useState("");
  const [defaultTemplate, setDefaultTemplate] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(apiUrl("/provider/commit-settings"), { signal: controller.signal });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "Could not load commit settings");
        if (!controller.signal.aborted) {
          setSelection({ provider: payload.provider || "", model: payload.model || "" });
          setAutoCommit(payload.autoCommit === true);
          setTemplate(payload.template || "");
          setDraft(payload.template || "");
          setDefaultTemplate(payload.defaultTemplate || "");
        }
      } catch (failure) { if (!controller.signal.aborted) setError(failure.message); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, []);
  async function save(next) {
    setSaving(true); setError("");
    try {
      const response = await fetch(apiUrl("/provider/commit-settings"), {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(next),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Could not save commit settings");
      if ("autoCommit" in next) setAutoCommit(next.autoCommit);
      if ("provider" in next) setSelection(next);
      if ("template" in next) {
        const saved = payload.template ?? (next.template.trim() ? next.template : defaultTemplate);
        setTemplate(saved);
        setDraft(saved);
      }
    } catch (failure) { setError(failure.message); }
    finally { setSaving(false); }
  }
  const selectedProvider = capabilities.find(provider => provider.id === selection.provider);
  const models = selectedProvider?.models ?? [];
  const available = models.some(model => model.id === selection.model);
  const providers = capabilities.filter(provider => provider.enabled !== false && provider.available && provider.models?.length);
  return <section className="space-y-2 border-t border-line pt-3">
    <h3 className="text-xs font-semibold">Commit messages</h3>
    <p className="text-xs text-muted">Choose which provider and model Rem uses to write commit messages.</p>
    <label className="block text-xs text-muted">Provider
      <select aria-label="Commit message provider" value={selection.provider} disabled={loading || saving}
        className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink"
        onChange={event => {
          const provider = providers.find(item => item.id === event.target.value);
          void save({ provider: provider?.id || "", model: provider?.defaultModel || provider?.models?.[0]?.id || "" });
        }}>
        <option value="">Use active Rem selection</option>
        {selection.provider && !providers.some(provider => provider.id === selection.provider) ? <option value={selection.provider} disabled>{selectedProvider?.displayName || selection.provider} (unavailable)</option> : null}
        {providers.map(provider => <option key={provider.id} value={provider.id}>{provider.displayName || provider.id}</option>)}
      </select>
    </label>
    {selection.provider ? <label className="block text-xs text-muted">Model
      <select aria-label="Commit message model" value={available ? selection.model : ""} disabled={loading || saving || !models.length}
        className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink"
        onChange={event => save({ provider: selection.provider, model: event.target.value })}>
        {!available ? <option value="" disabled>Choose an allowed model</option> : null}
        {models.map(model => <option key={model.id} value={model.id}>{model.displayName || model.id}</option>)}
      </select>
    </label> : null}
    {selection.provider && !available && selectedProvider?.discoveryStatus === "ready" ? <p role="status" className="text-xs text-muted">The saved commit model is unavailable or denied. Choose an allowed model before drafting a commit.</p> : null}
    <label className="flex items-center gap-2 text-xs text-ink">
      <input type="checkbox" checked={autoCommit} disabled={loading || saving} aria-label="Auto commit changes"
        onChange={event => void save({ autoCommit: event.target.checked })} />
      Auto commit changes
    </label>
    <p className="text-xs text-muted">Automatically accept the message generated by Rem and commit staged changes.</p>
    <label className="block text-xs text-muted">Message template
      <textarea aria-label="Commit message template" rows={8} maxLength={4000} value={draft} disabled={loading || saving}
        className="mt-1 w-full resize-y rounded border border-line bg-white px-2 py-1.5 font-mono text-xs text-ink"
        onChange={event => setDraft(event.target.value)}
        onBlur={() => { if (draft !== template) void save({ template: draft }); }} />
    </label>
    <p className="text-xs text-muted">Applies to generated messages and commits Rem writes in chat, across all providers. Use a one-line subject, a blank line, and up to 8 one-line change bullets. Changes save when you leave the field. An empty template restores the default.</p>
    <button type="button" className="text-xs font-semibold text-brand disabled:opacity-50" disabled={loading || saving || template === defaultTemplate}
      onClick={() => void save({ template: "" })}>Reset commit template</button>
    {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
  </section>;
}
