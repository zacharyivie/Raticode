import { useEffect, useState } from "react";
import { loadVoiceTidy, saveVoiceTidy } from "../lib/voiceTidy.js";
import { menuModels } from "../lib/providerModels.js";

// The model list for a provider, plus a full model ID the provider lists only by alias.
function modelOptions(provider, selection) {
  const models = menuModels(provider, selection?.model).map((model) => [model.id, model.displayName || model.id]);
  if (selection?.provider === provider?.id && selection?.model && !models.some(([id]) => id === selection.model)) {
    models.unshift([selection.model, selection.label || selection.model]);
  }
  return models;
}

export default function VoiceTidySettings({ capabilities, defaultProvider = "" }) {
  const [preference, setPreference] = useState(null);
  const [selection, setSelection] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    loadVoiceTidy({ defaultProvider, signal: controller.signal })
      .then((payload) => {
        if (controller.signal.aborted) return;
        setPreference(payload);
        setSelection(payload.selection);
      })
      .catch((failure) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [defaultProvider, revision]);
  async function save(changes) {
    setSaving(true); setError("");
    try {
      setPreference(await saveVoiceTidy(changes));
      setRevision((value) => value + 1);
    } catch (failure) { setError(failure.message); }
    finally { setSaving(false); }
  }
  const loading = preference === null;
  const providers = capabilities.filter((provider) => provider.enabled !== false && provider.available);
  const defaultName = capabilities.find((provider) => provider.id === defaultProvider)?.displayName || defaultProvider || "default provider";
  const chosenProvider = capabilities.find((provider) => provider.id === selection?.provider);
  const models = modelOptions(chosenProvider, selection);
  const recipient = selection?.providerName || chosenProvider?.displayName || selection?.provider || "the provider";
  return <section className="space-y-2 border-t border-line pt-3">
    <h3 className="text-xs font-semibold">Tidy voice messages</h3>
    <p className="text-xs text-muted">After you dictate, a fast model adds punctuation, fixes misheard names and drops filler words. Your words appear when it’s done, ready to edit. Applies to the message box here and to voice mode on Raticode Mobile.</p>
    <label className="flex items-center gap-2 text-xs text-ink">
      <input type="checkbox" aria-label="Tidy voice messages" checked={preference?.enabled === true} disabled={loading || saving}
        onChange={(event) => void save({ enabled: event.target.checked })} />
      Tidy voice messages
    </label>
    <label className="block text-xs text-muted">Provider
      <select aria-label="Voice tidy provider" value={preference?.provider || ""} disabled={loading || saving}
        className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink"
        onChange={(event) => void save({ provider: event.target.value })}>
        <option value="">Same as default provider ({defaultName})</option>
        {preference?.provider && !providers.some((provider) => provider.id === preference.provider)
          ? <option value={preference.provider} disabled>{chosenProvider?.displayName || preference.provider} (unavailable)</option> : null}
        {providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.displayName || provider.id}</option>)}
      </select>
    </label>
    <label className="block text-xs text-muted">Model
      <select aria-label="Voice tidy model" value={preference?.model || ""} disabled={loading || saving || !selection?.provider}
        className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink"
        onChange={(event) => void save({ provider: selection.provider, model: event.target.value })}>
        <option value="">{!preference?.model && selection?.label ? `Fast model (${selection.label})` : "Fast model"}</option>
        {models.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select>
    </label>
    {selection && !selection.available ? <p role="status" className="text-xs text-amber-700 dark:text-amber-300">{selection.reason} Until then your words appear as heard.</p> : null}
    {selection?.available ? <p className="text-xs text-muted">Uses {selection.label}{selection.effort ? ` at ${selection.effort} effort` : ""}. What you dictate goes to {recipient}{selection.context ? ", with names from Rem's chat" : ""}. If tidying fails or takes over 15 seconds, your words appear as heard.</p> : null}
    <label className="flex items-center gap-2 text-xs text-ink">
      <input type="checkbox" aria-label="Use Rem's chat to spell names" checked={preference?.context !== false} disabled={loading || saving}
        onChange={(event) => void save({ context: event.target.checked })} />
      Use Rem’s chat to spell names
    </label>
    <p className="text-xs text-muted">Sends project names, recent task titles and Rem’s last few chat messages with each dictation, leaving out anything that looks like a password or key.</p>
    {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
  </section>;
}
