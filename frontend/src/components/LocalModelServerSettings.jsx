import { useEffect, useState } from "react";
import { apiUrl } from "../lib/api";

export const DEFAULT_LOCAL_SERVER_URL = "http://127.0.0.1:11434/v1";

// Mirrors is_loopback_base_url in gofer/core/provider_profiles.py. The backend decides;
// this only picks which fields and warning the form shows while the user types.
export function isLoopbackAddress(address) {
  let host;
  try { host = new URL(address).hostname.toLowerCase().replace(/\.$/, ""); } catch { return false; }
  if (host === "localhost" || host.endsWith(".localhost")) return true;
  if (host === "[::1]") return true;
  return /^127(?:\.\d{1,3}){3}$/.test(host);
}

export function keySecretName(profileName) {
  const base = profileName.toUpperCase().replace(/[^A-Z0-9_]/g, "_").replace(/^([^A-Z_])/, "_$1");
  return `${base || "LOCAL_MODEL"}_API_KEY`;
}

// Local model servers (Ollama, llama.cpp, LM Studio) are provider profiles for workflow
// agent nodes. Raticode doesn't install one; this card points profiles at one the user runs.
// A key for a server on another computer goes to the keychain as a workflow secret and is
// never shown again.
export default function LocalModelServerSettings() {
  const [profiles, setProfiles] = useState(null);
  const [takenNames, setTakenNames] = useState([]);
  const [name, setName] = useState("ollama");
  const [address, setAddress] = useState(DEFAULT_LOCAL_SERVER_URL);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [checks, setChecks] = useState({});

  async function request(path, options = {}) {
    const response = await fetch(apiUrl(path), options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || "Local model server settings couldn't be saved.");
    return payload;
  }
  async function load() {
    const payload = await request("/provider/profiles", {});
    const all = Array.isArray(payload.profiles) ? payload.profiles : [];
    setTakenNames(all.map(profile => profile.name));
    setProfiles(all.filter(profile => profile.local));
  }
  useEffect(() => {
    load().catch(failure => { setProfiles([]); setError(failure.message || "Local model server profiles couldn't be loaded."); });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- load once when the card opens

  const post = body => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  async function check(profileName) {
    setBusy(`check:${profileName}`);
    try {
      const payload = await request(`/provider/profiles/${encodeURIComponent(profileName)}/check`, post({}));
      setChecks(current => ({ ...current, [profileName]: payload.check }));
    } catch (failure) {
      setChecks(current => ({ ...current, [profileName]: { reachable: false, error: failure.message } }));
    } finally { setBusy(""); }
  }
  const remote = address.trim() !== "" && !isLoopbackAddress(address.trim());
  async function add(event) {
    event.preventDefault();
    const profileName = name.trim();
    if (!profileName || !address.trim() || (remote && !key.trim())) return;
    setError("");
    if (takenNames.includes(profileName)) { setError(`A provider profile named ${profileName} already exists. Choose another name.`); return; }
    setBusy("add");
    try {
      const profile = { name: profileName, subscription: "openai_api", local: true, api_base_url: address.trim() };
      if (remote) {
        profile.api_key_secret = keySecretName(profileName);
        await request("/rattish/secrets", post({ name: profile.api_key_secret, value: key }));
        setKey("");
      }
      await request("/provider/profiles", post(profile));
      await load();
      setBusy("");
      await check(profileName);
    } catch (failure) {
      setError(failure.message || "The local model server profile couldn't be saved.");
    } finally { setBusy(""); }
  }
  async function remove(profileName) {
    setBusy(`remove:${profileName}`);
    setError("");
    try {
      await request(`/provider/profiles/${encodeURIComponent(profileName)}`, { method: "DELETE" });
      setChecks(current => { const next = { ...current }; delete next[profileName]; return next; });
      await load();
    } catch (failure) { setError(failure.message || "The profile couldn't be removed."); }
    finally { setBusy(""); }
  }

  const input = "mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink";
  return <section aria-labelledby="local-model-servers-heading" className="space-y-2 border-t border-line pt-3">
    <h3 id="local-model-servers-heading" className="text-xs font-semibold">Local model servers</h3>
    <p className="text-xs text-muted">{"Workflow agent nodes can use a model server you run, such as Ollama, llama.cpp or LM Studio, through its OpenAI-compatible API. Raticode doesn't install a server or download models. Calls cost $0 in usage totals."}</p>
    {profiles === null && !error ? <p role="status" className="text-xs text-muted">Loading local model servers…</p> : null}
    {(profiles ?? []).map(profile => {
      const result = checks[profile.name];
      return <div key={profile.name} className="space-y-1 rounded border border-line p-2">
        <div className="flex items-center justify-between gap-3">
          <h4 className="text-xs font-semibold">{profile.name}</h4>
          <span className="text-xs text-muted">{profile.loopback ? "On this computer" : "On another computer"}</span>
        </div>
        <p className="break-all text-xs text-muted">{profile.api_base_url}</p>
        {profile.warning ? <p className="text-xs text-amber-700">Warning: {profile.warning}</p> : null}
        <div className="flex flex-wrap gap-3">
          <button type="button" className="text-xs font-semibold text-brand disabled:opacity-50" disabled={Boolean(busy)} onClick={() => check(profile.name)}>{busy === `check:${profile.name}` ? "Checking server…" : "Check server"}</button>
          <button type="button" className="text-xs font-semibold text-red-700 disabled:opacity-50" disabled={Boolean(busy)} onClick={() => remove(profile.name)}>{busy === `remove:${profile.name}` ? "Removing…" : "Remove"}</button>
        </div>
        {result ? result.reachable
          ? <p role="status" className="text-xs text-muted">{result.models?.length ? `The server answered. Models: ${result.models.join(", ")}` : "The server answered but lists no models. Load or pull one in the server first."}</p>
          : <p role="alert" className="text-xs text-red-700">{result.error}</p> : null}
        <p className="text-xs text-muted">In an agent node, use <code>provider: openai-api</code>, <code>profile: {profile.name}</code> and a <code>model</code> the server lists.</p>
      </div>;
    })}
    <form className="space-y-2" onSubmit={add}>
      <h4 className="text-xs font-semibold">Add a local model server</h4>
      <label className="block text-xs text-muted">Profile name
        <input aria-label="Local model server profile name" className={input} value={name} disabled={Boolean(busy)} onChange={event => setName(event.target.value)} />
      </label>
      <label className="block text-xs text-muted">Server address
        <input aria-label="Local model server address" className={input} spellCheck={false} placeholder={DEFAULT_LOCAL_SERVER_URL} value={address} disabled={Boolean(busy)} onChange={event => setAddress(event.target.value)} />
      </label>
      <p className="text-xs text-muted">{`Ollama uses ${DEFAULT_LOCAL_SERVER_URL}, llama.cpp's llama-server http://127.0.0.1:8080/v1 and LM Studio http://127.0.0.1:1234/v1.`}</p>
      {remote ? <>
        <p role="note" className="text-xs text-amber-700">{"Warning: this address isn't on this computer. Prompts and replies travel over the network, so the server needs an API key. Use HTTPS or a network you trust."}</p>
        <label className="block text-xs text-muted">API key
          <input aria-label="Local model server API key" type="password" autoComplete="off" spellCheck={false} className={input} placeholder="Saved in the system keychain" value={key} disabled={Boolean(busy)} onChange={event => setKey(event.target.value)} />
        </label>
        {name.trim() ? <p className="text-xs text-muted">Saved as the workflow secret {keySecretName(name.trim())}.</p> : null}
      </> : null}
      <button type="submit" className="rounded border border-line px-2 py-1.5 text-xs font-semibold text-brand disabled:opacity-50"
        disabled={Boolean(busy) || !name.trim() || !address.trim() || (remote && !key.trim())}>{busy === "add" ? "Adding…" : "Add server"}</button>
    </form>
    {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
  </section>;
}
