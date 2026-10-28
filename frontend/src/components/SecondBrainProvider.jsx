import { createContext, useContext, useState } from "react";
import { DraftInput } from "./SecondBrainParts.jsx";
import { ProviderModelEffortFields } from "./ProviderModelEffortFields.jsx";

export const ResearchProviderContext = createContext({ provider: { id: "codex", model: "cli-default" }, capabilities: [], profiles: [] });

export function researchProviderDetails(value, capabilities = [], profiles = []) {
  const profile = profiles.find(item => item.name === value.profile && item.id === value.id);
  return { ...value, name: profile?.name || capabilities.find(item => item.id === value.id)?.displayName || value.id, local: profile?.local === true };
}

export function useResearchSelection(initial) {
  const context = useContext(ResearchProviderContext);
  const [selection, setSelection] = useState(initial || context.provider);
  return [researchProviderDetails(selection, context.capabilities, context.profiles), setSelection];
}

export default function SecondBrainProvider({ value, onChange }) {
  const { capabilities, loading, error, refresh, profiles } = useContext(ResearchProviderContext);
  const profile = profiles.find(item => item.name === value.profile && item.id === value.id);
  const apiIds = [...new Set(profiles.filter(item => ["openai_api", "anthropic_api"].includes(item.id)).map(item => item.id))];
  const catalog = [...capabilities, ...apiIds.map(id => {
    const matching = profiles.filter(item => item.id === id);
    const models = [...new Set([...matching.map(item => item.model), ...(value.id === id ? [value.model] : [])].filter(model => model && model !== "cli-default"))];
    return { id, displayName: id === "openai_api" ? "OpenAI API" : "Anthropic API", available: true, models: models.map(model => ({ id: model })), defaultModel: models[0] };
  })];
  return <section className="sb-research-provider" aria-label="Research provider">
    <h4>Provider and model</h4>
    <ProviderModelEffortFields capabilities={catalog} loading={loading} provider={value.id} model={value.model === "cli-default" ? "" : value.model} effort={value.effort || ""} onRefresh={refresh}
      onChange={patch => {
        const nextProfile = patch.provider ? profiles.find(item => item.id === patch.provider && apiIds.includes(item.id)) : profile;
        onChange({ ...value, ...patch, id: patch.provider || value.id, profile: nextProfile?.name || null });
      }} />
    {apiIds.includes(value.id) ? <label className="sb-field"><span>Custom model</span><DraftInput label="Research model" value={value.model || ""} onCommit={model => onChange({ ...value, model })} /></label> : null}
    {["grok", "antigravity"].includes(value.id) ? <p className="sb-hint">Uses this provider’s CLI permissions.</p> : null}
    {error ? <p role="alert" className="sb-hint">{error}</p> : null}
    <label className="sb-field"><span>Provider profile</span><select value={profile?.name || ""} onChange={event => {
      const profile = profiles.find(item => item.name === event.target.value);
      onChange(profile ? { id: profile.id, profile: profile.name, model: profile.model || "cli-default", effort: profile.effort || "" } : { ...value, profile: null });
    }}><option value="">Provider defaults</option>{profiles.map(profile => <option key={profile.name} value={profile.name}>{profile.name}{profile.local ? " · local" : ""}</option>)}</select><small>Manage providers and profiles in provider settings.</small></label>
  </section>;
}
