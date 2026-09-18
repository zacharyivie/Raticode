import { useEffect, useRef, useState } from "react";
import { ProviderAuthentication, ProviderDiscoveryLoading } from "./ProviderModelEffortFields.jsx";
import { apiUrl } from "../lib/api";

export default function ProviderSettings({ providerState }) {
  const { capabilities, error, loading, refresh } = providerState;
  return <div className="space-y-4 py-3">
    <p className="text-xs text-muted">Choose the coding apps Rem can use. Found providers are enabled by default. Choosing an executable overrides automatic discovery.</p>
    <button type="button" className="text-xs font-semibold text-brand" disabled={loading} onClick={refresh}>{loading ? "Checking providers…" : "Refresh providers"}</button>
    {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
    {loading ? <ProviderDiscoveryLoading /> : capabilities.map(provider => <ProviderRow key={provider.id} provider={provider} />)}
  </div>;
}

function ProviderRow({ provider }) {
  const [draft, setDraft] = useState(provider.executableOverride || "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const focused = useRef(false);
  const models = provider.models ?? [];
  const modelOverride = provider.defaultModelOverride || "";
  const effortOverride = provider.defaultEffortOverride || "";
  const defaultModel = models.find(model => model.id === provider.defaultModel) ?? models[0];
  const efforts = defaultModel?.efforts ?? [];
  const canSetDefaults = provider.available && provider.discoveryStatus === "ready" && models.length > 0;
  const missingModel = modelOverride && !models.some(model => model.id === modelOverride);
  const missingEffort = effortOverride && !efforts.some(effort => effort.id === effortOverride);
  useEffect(() => { if (!focused.current) setDraft(provider.executableOverride || ""); }, [provider.executableOverride]);
  async function save(changes) {
    setSaving(true);
    setError("");
    try {
      const response = await fetch(apiUrl("/provider/settings"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: provider.id, ...changes }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Could not save provider settings");
      window.dispatchEvent(new CustomEvent("raticode:providers-changed"));
    } catch (failure) { setError(failure.message || "Could not save provider settings"); }
    finally { setSaving(false); }
  }
  async function browse() {
    try {
      const path = await window.goferDesktop.workspace.selectPath({ fileOnly: true, currentPath: draft || provider.executable || "" });
      if (path) { setDraft(path); await save({ executable: path }); }
    } catch (failure) { setError(failure.message || "Could not open file picker"); }
  }
  return <section className="space-y-2 border-t border-line pt-3">
    <div className="flex items-center justify-between gap-3">
      <h3 className="text-xs font-semibold">{provider.displayName || provider.id}</h3>
      <select aria-label={`${provider.displayName} status`} className="rounded border border-line bg-white px-2 py-1 text-xs" disabled={saving}
        value={(provider.enabled ?? provider.available) ? "enabled" : "disabled"}
        onChange={event => save({ enabled: event.target.value === "enabled" })}>
        <option value="enabled">Enabled</option><option value="disabled">Disabled</option>
      </select>
    </div>
    <p className="break-all text-xs text-muted">{provider.detected ? `Found: ${provider.executable}` : "Executable not found"}</p>
    <label className="block text-xs text-muted">Executable override
      <input aria-label={`${provider.displayName} executable`} className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink" placeholder="Automatic discovery" value={draft} disabled={saving}
        onFocus={() => { focused.current = true; }} onChange={event => setDraft(event.target.value)}
        onBlur={() => { focused.current = false; if (draft !== (provider.executableOverride || "")) void save({ executable: draft }); }}
        onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); } }} />
    </label>
    <button type="button" className="text-xs font-semibold text-brand disabled:opacity-50" disabled={saving || !window.goferDesktop?.workspace?.selectPath} onClick={browse}>Choose executable…</button>
    {canSetDefaults ? <div className="space-y-2 pt-2">
      <p className="text-xs text-muted">Use these defaults when selecting this provider in Raticode.</p>
      <label className="block text-xs text-muted">Default model
        <select aria-label={`${provider.displayName} default model`} className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink" disabled={saving}
          value={modelOverride} onChange={event => save({ defaultModel: event.target.value, defaultEffort: "" })}>
          <option value="">Provider default{provider.providerDefaultModel ? ` (${provider.providerDefaultModel})` : ""}</option>
          {missingModel ? <option value={modelOverride}>{modelOverride} (unavailable)</option> : null}
          {models.map(model => <option key={model.id} value={model.id}>{model.displayName || model.id}</option>)}
        </select>
      </label>
      <label className="block text-xs text-muted">Default effort
        <select aria-label={`${provider.displayName} default effort`} className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink" disabled={saving || !efforts.length || Boolean(missingModel)}
          value={effortOverride} onChange={event => save({ defaultEffort: event.target.value })}>
          <option value="">{efforts.length ? `Provider default${defaultModel?.providerDefaultEffort ? ` (${defaultModel.providerDefaultEffort})` : ""}` : "Not supported by this model"}</option>
          {missingEffort ? <option value={effortOverride}>{effortOverride} (unavailable)</option> : null}
          {efforts.map(effort => <option key={effort.id} value={effort.id}>{effort.displayName || effort.id}</option>)}
        </select>
      </label>
      {missingModel || missingEffort ? <p className="text-xs text-muted">A saved default is unavailable. Raticode uses the provider default until you choose an available option.</p> : null}
      {modelOverride || effortOverride ? <button type="button" className="text-xs font-semibold text-brand disabled:opacity-50" disabled={saving}
        onClick={() => save({ defaultModel: "", defaultEffort: "" })}>Reset model and effort defaults</button> : null}
    </div> : null}
    <ProviderAuthentication provider={provider} disabled={saving} alwaysShow />
    {provider.error ? <p className="text-xs text-muted">{provider.error}</p> : null}
    {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
  </section>;
}
