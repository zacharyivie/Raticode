import { useEffect, useId, useRef, useState } from "react";
import { ExternalLink, KeyRound } from "lucide-react";
import { apiUrl } from "../lib/api.js";
import { usageDashboardUrl } from "../lib/usage.js";

export default function UsageCredentials({ provider, profile, onSaved }) {
  const [open, setOpen] = useState(false);
  return <div className="usage-credentials">
    <button className="usage-configure" type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <KeyRound size={13} aria-hidden="true" />{open ? "Close reporting settings" : "Configure usage reporting"}
    </button>
    {open ? <CredentialForm provider={provider} profile={profile} onSaved={onSaved} /> : null}
  </div>;
}

function CredentialForm({ provider, profile, onSaved }) {
  const [config, setConfig] = useState(null);
  const [draft, setDraft] = useState({});
  const [remove, setRemove] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const request = useRef(null);
  const id = useId();

  function acceptConfig(data) {
    setConfig(data);
    setDraft(Object.fromEntries(data.fields.map(field => [field.name, field.secret ? "" : field.value || ""])));
    setRemove([]);
  }

  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    const query = new URLSearchParams({ provider });
    if (profile) query.set("profile", profile);
    void (async () => {
      try {
        const response = await fetch(apiUrl(`/usage/credentials?${query}`), { signal: controller.signal });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Could not load reporting settings.");
        if (!controller.signal.aborted) acceptConfig(data);
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure.message || "Could not load reporting settings.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => request.current?.abort();
  }, [provider, profile]);

  async function save(event) {
    event.preventDefault();
    if (saving || !config?.storage_available) return;
    setSaving(true);
    setError("");
    setSaved(false);
    const controller = new AbortController();
    request.current = controller;
    const values = Object.fromEntries(config.fields
      .filter(field => !remove.includes(field.name) && (!field.secret || draft[field.name] !== ""))
      .map(field => [field.name, draft[field.name]]));
    try {
      const response = await fetch(apiUrl("/usage/credentials"), {
        method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, profile, values, remove }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not save reporting settings.");
      if (!controller.signal.aborted) {
        acceptConfig(data);
        setSaved(true);
        void onSaved();
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message || "Could not save reporting settings.");
    } finally {
      if (!controller.signal.aborted) setSaving(false);
    }
  }

  const helpUrl = usageDashboardUrl(config?.help_url);
  return <form className="usage-credential-form" aria-label="Usage reporting settings" onSubmit={save}>
    {loading ? <p role="status">Loading reporting settings…</p> : null}
    {error ? <p className="usage-error" role="alert">{error}</p> : null}
    {config ? <>
      <p>{config.description}</p>
      <p className="usage-credential-scope">{profile ? `Reporting for profile ${profile}.` : "Reporting for the default account."} Saved secrets stay in your operating system&apos;s credential store.</p>
      {!config.storage_available ? <p className="usage-error" role="alert">{config.storage_error || "The operating system credential store is unavailable. Unlock it to configure reporting."}</p> : null}
      <fieldset disabled={saving || !config.storage_available}>
        {config.fields.map(field => <div className="usage-credential-field" key={field.name}>
          <label htmlFor={`${id}-${field.name}`}>{field.label}{field.secret ? <span>{field.configured ? "Saved" : "Not saved"}</span> : null}</label>
          <input id={`${id}-${field.name}`} name={field.name} type={field.secret ? "password" : "text"}
            autoComplete="off" spellCheck={false} value={draft[field.name] ?? ""} disabled={remove.includes(field.name)}
            placeholder={field.secret && field.configured ? "Leave blank to keep the saved credential" : undefined}
            aria-describedby={field.help ? `${id}-${field.name}-help` : undefined}
            onChange={event => { setDraft(value => ({ ...value, [field.name]: event.target.value })); setSaved(false); }} />
          {field.help ? <p id={`${id}-${field.name}-help`} className="usage-field-help">{field.help}</p> : null}
          {field.secret && field.configured ? <label className="usage-remove-credential">
            <input type="checkbox" checked={remove.includes(field.name)} onChange={event => {
              setRemove(value => event.target.checked ? [...value, field.name] : value.filter(name => name !== field.name));
              setDraft(value => ({ ...value, [field.name]: "" }));
              setSaved(false);
            }} />Remove saved {field.label.toLowerCase()}
          </label> : null}
        </div>)}
        <button className="usage-save-credentials" type="submit">{saving ? "Saving…" : "Save reporting settings"}</button>
      </fieldset>
      {saved ? <p role="status" className="usage-credential-saved">Reporting settings saved. Usage is refreshing.</p> : null}
      {helpUrl ? <a href={helpUrl} target="_blank" rel="noreferrer">Reporting setup guide <ExternalLink size={12} aria-hidden="true" /></a> : null}
    </> : null}
  </form>;
}
