import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { ProviderAuthentication } from "./ProviderModelEffortFields.jsx";
import { apiUrl } from "../lib/api";

export default function ProviderSettings({ providerState }) {
  const { capabilities, error, loading, refresh } = providerState;
  return <div className="space-y-4 py-3">
    <p className="text-xs text-muted">Choose the coding apps Rem can use. Found providers are enabled by default. Choosing an executable overrides automatic discovery.</p>
    <button type="button" className="text-xs font-semibold text-brand" disabled={loading} onClick={refresh}>{loading ? "Checking providers…" : "Refresh providers"}</button>
    {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
    <CommitMessageSettings capabilities={capabilities} />
    {capabilities.map(provider => <ProviderRow key={provider.id} provider={provider} />)}
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
  const deniedDefault = provider.deniedModels?.includes(modelOverride);
  const missingModel = modelOverride && !deniedDefault && !models.some(model => model.id === modelOverride);
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
      return true;
    } catch (failure) { setError(failure.message || "Could not save provider settings"); return false; }
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
      <select aria-label={`${provider.displayName} status`} className="rounded border border-line bg-white px-2 py-1 text-xs" disabled={saving || provider.settingsPending}
        value={provider.settingsPending ? "pending" : (provider.enabled ?? provider.available) ? "enabled" : "disabled"}
        onChange={event => save({ enabled: event.target.value === "enabled" })}>
        {provider.settingsPending ? <option value="pending">Loading settings…</option> : null}
        <option value="enabled">Enabled</option><option value="disabled">Disabled</option>
      </select>
    </div>
    <p className="break-all text-xs text-muted">{provider.detected ? `Found: ${provider.executable}` : provider.discoveryStatus === "pending" ? "Checking executable…" : "Executable not found"}</p>
    {provider.refreshing ? <p role="status" className="text-xs text-muted">{provider.discoveredAt ? "Refreshing provider information…" : "Discovering provider models…"}</p> : null}
    {provider.version ? <p className="text-xs text-muted">Version: {provider.version}</p> : null}
    <label className="block text-xs text-muted">Executable override
      <input aria-label={`${provider.displayName} executable`} className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink" placeholder="Automatic discovery" value={draft} disabled={saving || provider.settingsPending}
        onFocus={() => { focused.current = true; }} onChange={event => setDraft(event.target.value)}
        onBlur={() => { focused.current = false; if (draft !== (provider.executableOverride || "")) void save({ executable: draft }); }}
        onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); } }} />
    </label>
    <button type="button" className="text-xs font-semibold text-brand disabled:opacity-50" disabled={saving || provider.settingsPending || !window.goferDesktop?.workspace?.selectPath} onClick={browse}>Choose executable…</button>
    <ModelAccess provider={provider} saving={saving} save={save} />
    {canSetDefaults ? <div className="space-y-2 pt-2">
      <p className="text-xs text-muted">Use these defaults when selecting this provider in Raticode.</p>
      <label className="block text-xs text-muted">Default model
        <select aria-label={`${provider.displayName} default model`} className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink" disabled={saving}
          value={deniedDefault ? "" : modelOverride} onChange={event => save({ defaultModel: event.target.value, defaultEffort: "" })}>
          <option value="">Automatic{provider.defaultModel ? ` (${provider.defaultModel})` : ""}</option>
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
      {missingModel || missingEffort || deniedDefault ? <p className="text-xs text-muted">A saved default is unavailable. Raticode uses an allowed model until you choose another default.</p> : null}
      {modelOverride || effortOverride ? <button type="button" className="text-xs font-semibold text-brand disabled:opacity-50" disabled={saving}
        onClick={() => save({ defaultModel: "", defaultEffort: "" })}>Reset model and effort defaults</button> : null}
    </div> : null}
    {!canSetDefaults && (modelOverride || effortOverride) ? <p className="text-xs text-muted">Saved defaults: {modelOverride || "Provider model"}{effortOverride ? `, ${effortOverride} effort` : ""}</p> : null}
    <ProviderAuthentication provider={provider} disabled={saving || provider.settingsPending} alwaysShow />
    {provider.refreshError ? <p role="alert" className="text-xs text-red-600">{provider.refreshError}</p> : null}
    {provider.error ? <p className="text-xs text-muted">{provider.error}</p> : null}
    {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
  </section>;
}

function ModelAccess({ provider, saving, save }) {
  const deniedKey = JSON.stringify(provider.deniedModels ?? []);
  const [denied, setDenied] = useState(() => JSON.parse(deniedKey));
  const [allowedSelection, setAllowedSelection] = useState([]);
  const [deniedSelection, setDeniedSelection] = useState([]);
  useEffect(() => { setDenied(JSON.parse(deniedKey)); }, [deniedKey]);
  const catalog = provider.discoveredModels ?? provider.models ?? [];
  const allowed = catalog.filter(model => !denied.includes(model.id));
  const deniedModels = denied.map(id => catalog.find(model => model.id === id) ?? { id, displayName: id });
  const disabled = saving || provider.settingsPending;
  async function move(toDenied, all = false) {
    const selection = all
      ? (toDenied ? allowed.map(model => model.id) : denied)
      : (toDenied ? allowedSelection : deniedSelection);
    const next = toDenied ? [...new Set([...denied, ...selection])] : denied.filter(id => !selection.includes(id));
    if (await save({ deniedModels: next })) {
      setDenied(next);
      setAllowedSelection([]);
      setDeniedSelection([]);
    }
  }
  function column(label, models, selection, setSelection) {
    return <label className="min-w-0 text-xs text-muted">
      <span className="mb-1 flex items-center justify-between gap-2"><span className="font-semibold text-ink">{label}</span><span>{models.length}</span></span>
      <select multiple size={6} aria-label={`${provider.displayName} ${label.toLowerCase()} models`} value={selection} disabled={disabled}
        onChange={event => setSelection(Array.from(event.target.selectedOptions, option => option.value))}
        className="h-36 w-full rounded border border-line bg-canvas p-1 text-xs text-ink focus-visible:outline-brand disabled:opacity-50">
        {models.map(model => <option className="px-2 py-1.5" key={model.id} value={model.id} title={model.id}>{model.displayName || model.id}</option>)}
      </select>
    </label>;
  }
  return <div className="space-y-2 pt-2">
    <p className="text-xs text-muted">Select models and use the arrows to move them, or use the double arrows to move all. Denied models stay out of model menus.</p>
    <div className="grid grid-cols-[minmax(0,1fr)_2rem_minmax(0,1fr)] items-center gap-2">
      {column("Allowed", allowed, allowedSelection, setAllowedSelection)}
      <div className="flex flex-col gap-2 pt-5">
        <button type="button" aria-label={`Deny all ${provider.displayName} models`} title="Deny all models" disabled={disabled || !allowed.length}
          className="grid h-8 w-8 place-items-center rounded border border-line text-brand hover:bg-canvas focus-visible:outline-brand disabled:opacity-30" onClick={() => move(true, true)}><ChevronsRight size={16} /></button>
        <button type="button" aria-label={`Deny selected ${provider.displayName} models`} title="Move to Denied" disabled={disabled || !allowedSelection.length}
          className="grid h-8 w-8 place-items-center rounded border border-line text-brand hover:bg-canvas focus-visible:outline-brand disabled:opacity-30" onClick={() => move(true)}><ArrowRight size={16} /></button>
        <button type="button" aria-label={`Allow selected ${provider.displayName} models`} title="Move to Allowed" disabled={disabled || !deniedSelection.length}
          className="grid h-8 w-8 place-items-center rounded border border-line text-brand hover:bg-canvas focus-visible:outline-brand disabled:opacity-30" onClick={() => move(false)}><ArrowLeft size={16} /></button>
        <button type="button" aria-label={`Allow all ${provider.displayName} models`} title="Allow all models" disabled={disabled || !denied.length}
          className="grid h-8 w-8 place-items-center rounded border border-line text-brand hover:bg-canvas focus-visible:outline-brand disabled:opacity-30" onClick={() => move(false, true)}><ChevronsLeft size={16} /></button>
      </div>
      {column("Denied", deniedModels, deniedSelection, setDeniedSelection)}
    </div>
    {!allowed.length ? <p className="text-xs text-muted">{catalog.length ? "All models are denied. Move a model to Allowed to use it." : "No models discovered yet. Refresh providers after signing in."}</p> : null}
  </div>;
}

function CommitMessageSettings({ capabilities }) {
  const [selection, setSelection] = useState({ provider: "", model: "" });
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
        if (!controller.signal.aborted) setSelection({ provider: payload.provider || "", model: payload.model || "" });
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
      setSelection(next);
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
    {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
  </section>;
}
