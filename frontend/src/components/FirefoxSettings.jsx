import { useEffect, useMemo, useRef, useState } from "react";

// Settings > Browser > Firefox: Firefox's settings, its extensions and
// importing from an everyday Firefox. The desktop app does the work and
// confirms installs, removals and imports in its own dialogs.

const TABS = [["settings", "Settings"], ["extensions", "Extensions"], ["import", "Import"]];
const IMPORT_CHOICES = [
  ["settings", "Settings", "Preferences such as privacy, fonts, languages and network."],
  ["bookmarks", "Bookmarks and history", ""],
  ["permissions", "Site permissions and zoom", "They apply in every Firefox tab in Raticode, Rem’s included."],
  ["extensions", "Extensions", "Reinstalled from their signed packages."],
  ["extensionData", "Extension settings and data", "Keeps the chosen extensions configured as they are now."],
  ["cookies", "Sign-ins to websites", "Copies cookies from that Firefox’s normal tabs, so sites stay signed in. Sign-ins from its other containers are left out, and Rem’s tabs stay signed out unless you share a site with Rem."],
];
const NEEDS_CLOSED = new Set(["bookmarks", "permissions", "extensionData", "cookies"]);
const BUTTON = "h-7 rounded-md border border-line bg-white px-2.5 text-[11px] font-semibold text-ink transition hover:bg-slate-50 disabled:cursor-default disabled:opacity-50";
const PRIMARY = "h-7 rounded-md bg-brand px-2.5 text-[11px] font-semibold text-white transition hover:bg-brand/90 disabled:cursor-default disabled:opacity-50";
const FIELD = "h-7 w-full rounded-md border border-line bg-white px-2 text-xs text-ink outline-none focus:border-brand";

function library(action, options) {
  const run = window.goferBrowser?.library;
  if (!run) return Promise.reject(new Error("Firefox settings are available in the Raticode desktop app."));
  return run(action, options);
}

export default function FirefoxSettings() {
  const [tab, setTab] = useState("settings");
  return (
    <div className="grid gap-3" data-testid="firefox-settings">
      <div aria-label="Firefox" className="flex gap-1" role="tablist">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            aria-selected={tab === id}
            className={`h-7 rounded-md px-2.5 text-[11px] font-semibold transition ${tab === id ? "bg-slate-100 text-ink" : "text-muted hover:text-ink"}`}
            role="tab"
            type="button"
            onClick={() => setTab(id)}
          >{label}</button>
        ))}
      </div>
      {tab === "settings" ? <FirefoxPreferences /> : tab === "extensions" ? <FirefoxExtensions /> : <FirefoxImport />}
    </div>
  );
}

function Status({ busy, error, note }) {
  if (busy) return <p className="text-[11px] text-muted" role="status">{busy}</p>;
  if (error) return <p className="text-[11px] text-amber-800" role="alert">{error}</p>;
  if (note) return <p className="text-[11px] text-muted" role="status">{note}</p>;
  return null;
}

function useAction() {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  async function run(label, task) {
    setBusy(label);
    setError("");
    try { return await task(); }
    catch (cause) { setError(cause instanceof Error ? cause.message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, "") : String(cause)); return null; }
    finally { setBusy(""); }
  }
  return { busy, error, run };
}

// ---------------------------------------------------------------------------
// Settings

function FirefoxPreferences() {
  const [data, setData] = useState(null);
  const [restartNeeded, setRestartNeeded] = useState(false);
  const { busy, error, run } = useAction();
  // Read once when the tab opens.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void run("Reading Firefox settings…", async () => setData(await library("firefox-settings"))); }, []);

  async function save(prefs) {
    const next = await run("Saving…", () => library("firefox-settings-save", { prefs }));
    if (!next) return;
    setData(next);
    setRestartNeeded(Boolean(next.restartNeeded));
  }
  async function restart() {
    const next = await run("Restarting Firefox…", () => library("firefox-restart"));
    if (next) { setData(next); setRestartNeeded(false); }
  }

  const groups = useMemo(() => {
    const map = new Map();
    for (const setting of data?.settings || []) map.set(setting.group, [...(map.get(setting.group) || []), setting]);
    return [...map];
  }, [data]);

  return (
    <div className="grid gap-3">
      <p className="text-[11px] leading-4 text-muted">
        Firefox’s own settings pages can’t appear in a Raticode tab, so the common ones are here. Changes apply when Firefox restarts.
      </p>
      {restartNeeded ? (
        <div className="flex items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5" role="status">
          <span className="text-[11px] text-amber-900">Restart Firefox to apply. Open Firefox tabs reload.</span>
          <button className={PRIMARY} disabled={Boolean(busy)} type="button" onClick={() => void restart()}>Restart Firefox</button>
        </div>
      ) : null}
      <Status busy={busy} error={error} />
      <FirefoxWindow />
      {groups.map(([group, settings]) => (
        <section key={group} aria-label={group} className="grid gap-1">
          <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">{group}</h4>
          {settings.map((setting) => <PreferenceRow key={setting.pref} setting={setting} disabled={Boolean(busy)} onChange={(value) => void save({ [setting.pref]: value })} />)}
        </section>
      ))}
      {data ? <CustomPreferences custom={data.custom} disabled={Boolean(busy)} onSave={save} /> : null}
      {data ? (
        <details className="rounded-md border border-line px-2.5 py-1.5">
          <summary className="cursor-pointer text-[11px] font-semibold text-ink">Settings Raticode keeps fixed ({data.required.length})</summary>
          <ul className="mt-2 grid gap-1">
            {data.required.map((item) => (
              <li key={item.name} className="text-[10px] leading-4 text-muted"><code className="text-ink">{item.name} = {JSON.stringify(item.value)}</code> · {item.reason}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

// Some sites, Google's sign-in among them, refuse a browser under remote
// control, and Firefox's own pages can't be streamed. A normal Firefox window
// on the same profile can do both; Raticode's Firefox tabs pause meanwhile.
const WINDOW_PAGES = [
  ["https://accounts.google.com/", "Sign in to a site"],
  ["about:preferences", "Firefox’s own settings"],
  ["about:addons", "Firefox add-ons"],
  ["about:preferences#sync", "Firefox account and Sync"],
];

function FirefoxWindow() {
  const { busy, error, run } = useAction();
  const [note, setNote] = useState("");
  async function open(url) {
    const result = await run("Opening a Firefox window…", () => library("firefox-sign-in-window", { url }));
    if (result) setNote(result.alreadyOpen ? "A Firefox window is already open. Close it to use Firefox in Raticode again." : "Firefox opened in its own window. Close it when you’re done; your Firefox tabs here come back.");
  }
  return (
    <section aria-label="Firefox in a window" className="grid gap-1.5 rounded-md border border-line px-2.5 py-2">
      <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">Firefox in a window</h4>
      <p className="text-[11px] leading-4 text-muted">
        Google won’t sign in while Raticode controls Firefox. Open this same Firefox in a regular window to sign in, or to use Firefox’s own settings, add-ons and account. Raticode’s Firefox tabs pause until you close it.
      </p>
      <div className="flex flex-wrap gap-1.5">
        {WINDOW_PAGES.map(([url, label]) => <button key={url} className={BUTTON} disabled={Boolean(busy)} type="button" onClick={() => void open(url)}>{label}…</button>)}
      </div>
      <Status busy={busy} error={error} note={note} />
    </section>
  );
}

function PreferenceRow({ disabled, onChange, setting }) {
  const id = `firefox-pref-${setting.pref.replace(/[^a-z0-9]/gi, "-")}`;
  let control;
  if (setting.type === "boolean") {
    control = <input checked={setting.value === true} disabled={disabled} id={id} type="checkbox" onChange={(event) => onChange(event.target.checked)} />;
  } else if (setting.type === "choice") {
    control = (
      <select className={FIELD} disabled={disabled} id={id} value={String(setting.value)} onChange={(event) => onChange(Number(event.target.value))}>
        {setting.options.map(([value, label]) => <option key={value} value={String(value)}>{label}</option>)}
      </select>
    );
  } else {
    control = <DraftInput disabled={disabled} id={id} numeric={setting.type === "integer"} placeholder={setting.type === "string" ? "Firefox default" : String(setting.default)} value={setting.value} onCommit={onChange} />;
  }
  return (
    <div className={`grid items-center gap-2 py-1 ${setting.type === "boolean" ? "grid-cols-[auto_1fr]" : "grid-cols-1 sm:grid-cols-[1fr_14rem]"}`}>
      {setting.type === "boolean" ? control : null}
      <label className="min-w-0 text-xs text-ink" htmlFor={id}>
        {setting.label}
        {setting.source === "firefox" ? <span className="ml-1.5 rounded border border-line px-1 text-[9px] font-semibold uppercase text-muted">imported</span> : null}
        {setting.description ? <span className="block text-[10px] leading-4 text-muted">{setting.description}</span> : null}
      </label>
      {setting.type === "boolean" ? null : control}
    </div>
  );
}

// Keeps what the user types while the field is focused; saves on blur or Enter.
function DraftInput({ disabled, id, numeric = false, onCommit, placeholder, value }) {
  const [draft, setDraft] = useState(value ?? "");
  const focused = useRef(false);
  useEffect(() => { if (!focused.current) setDraft(value ?? ""); }, [value]);
  const commit = () => {
    focused.current = false;
    if (String(draft) === String(value ?? "")) return;
    onCommit(numeric ? (draft === "" ? null : Number(draft)) : draft);
  };
  return (
    <input
      className={FIELD}
      disabled={disabled}
      id={id}
      inputMode={numeric ? "numeric" : undefined}
      placeholder={placeholder}
      spellCheck={false}
      value={draft}
      onBlur={commit}
      onChange={(event) => setDraft(event.target.value)}
      onFocus={() => { focused.current = true; }}
      onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
    />
  );
}

function CustomPreferences({ custom, disabled, onSave }) {
  const [name, setName] = useState("");
  const [type, setType] = useState("boolean");
  const [value, setValue] = useState("true");
  function add(event) {
    event.preventDefault();
    const parsed = type === "boolean" ? value === "true" : type === "integer" ? Number(value) : value;
    if (!name.trim() || (type === "integer" && !Number.isInteger(parsed))) return;
    void onSave({ [name.trim()]: parsed });
    setName("");
  }
  return (
    <details className="rounded-md border border-line px-2.5 py-1.5">
      <summary className="cursor-pointer text-[11px] font-semibold text-ink">Other Firefox preferences{custom.length ? ` (${custom.length})` : ""}</summary>
      <p className="mt-1 text-[10px] leading-4 text-muted">Set any preference you would change in about:config, by its exact name.</p>
      <ul className="mt-2 grid gap-1">
        {custom.map((item) => (
          <li key={item.name} className="flex items-center justify-between gap-2 text-[11px]">
            <code className="min-w-0 truncate text-ink">{item.name} = {JSON.stringify(item.value)}</code>
            <button className={BUTTON} disabled={disabled} type="button" onClick={() => void onSave({ [item.name]: null })}>Remove</button>
          </li>
        ))}
      </ul>
      <form className="mt-2 grid grid-cols-[1fr_6rem_7rem_auto] gap-1.5" onSubmit={add}>
        <input aria-label="Preference name" className={FIELD} placeholder="browser.example.pref" spellCheck={false} value={name} onChange={(event) => setName(event.target.value)} />
        <select aria-label="Preference type" className={FIELD} value={type} onChange={(event) => { setType(event.target.value); setValue(event.target.value === "boolean" ? "true" : ""); }}>
          <option value="boolean">On/off</option>
          <option value="integer">Number</option>
          <option value="string">Text</option>
        </select>
        {type === "boolean" ? (
          <select aria-label="Preference value" className={FIELD} value={value} onChange={(event) => setValue(event.target.value)}>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        ) : <input aria-label="Preference value" className={FIELD} spellCheck={false} value={value} onChange={(event) => setValue(event.target.value)} />}
        <button className={BUTTON} disabled={disabled || !name.trim()} type="submit">Set</button>
      </form>
    </details>
  );
}

// ---------------------------------------------------------------------------
// Extensions

function FirefoxExtensions() {
  const [installed, setInstalled] = useState(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [note, setNote] = useState("");
  const { busy, error, run } = useAction();
  // Read once when the tab opens.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void run("Starting Firefox to list its extensions…", async () => setInstalled((await library("firefox-extensions")).extensions)); }, []);

  function applied(result, message) {
    if (!result) return;
    if (result.extensions) setInstalled(result.extensions);
    setNote(result.cancelled ? "" : message(result));
  }
  async function search(event) {
    event.preventDefault();
    if (!query.trim()) return;
    const result = await run("Searching Firefox Add-ons…", () => library("firefox-addon-search", { text: query }));
    if (result) setResults(result.results);
  }

  const installedIds = new Set((installed || []).map((item) => item.id));
  return (
    <div className="grid gap-3">
      <p className="text-[11px] leading-4 text-muted">
        Extensions that work on web pages, such as ad blockers, run normally. Their toolbar buttons, pop-ups and options pages can’t appear in Raticode. Extensions also run in the Firefox tabs Rem uses.
      </p>
      <Status busy={busy} error={error} note={note} />
      <section aria-label="Installed extensions" className="grid gap-1">
        <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">Installed</h4>
        {installed && !installed.length ? <p className="text-[11px] text-muted">No extensions yet.</p> : null}
        {(installed || []).map((item) => (
          <div key={item.id} className="flex items-center justify-between gap-2 rounded-md border border-line px-2.5 py-1.5">
            <span className="min-w-0 text-xs text-ink">
              <span className="font-semibold">{item.name}</span> <span className="text-[10px] text-muted">{item.version}{item.active ? "" : " · off"}</span>
            </span>
            <button aria-label={`Remove ${item.name}`} className={BUTTON} disabled={Boolean(busy)} type="button" onClick={() => void run(`Removing ${item.name}…`, () => library("firefox-addon-remove", { id: item.id })).then((result) => applied(result, () => `Removed ${item.name}.`))}>Remove</button>
          </div>
        ))}
      </section>
      <section aria-label="Get extensions" className="grid gap-1.5">
        <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">Get extensions</h4>
        <form className="flex gap-1.5" role="search" onSubmit={search}>
          <input aria-label="Search Firefox Add-ons" className={FIELD} placeholder="Search Firefox Add-ons" value={query} onChange={(event) => setQuery(event.target.value)} />
          <button className={BUTTON} disabled={Boolean(busy) || !query.trim()} type="submit">Search</button>
          <button className={BUTTON} disabled={Boolean(busy)} type="button" onClick={() => void run("Waiting for a file…", () => library("firefox-addon-install-file")).then((result) => applied(result, (value) => `Added ${value.installed?.name || "the extension"}.`))}>From file…</button>
        </form>
        {results && !results.length ? <p className="text-[11px] text-muted">No extensions found.</p> : null}
        {(results || []).map((item) => (
          <div key={item.guid} className="flex items-start justify-between gap-2 rounded-md border border-line px-2.5 py-1.5">
            <span className="min-w-0 text-xs text-ink">
              <span className="font-semibold">{item.name}</span>
              {item.recommended ? <span className="ml-1.5 rounded border border-line px-1 text-[9px] font-semibold uppercase text-muted">recommended</span> : null}
              <span className="block text-[10px] leading-4 text-muted">{item.summary}</span>
              <span className="block text-[10px] text-muted">{item.users.toLocaleString()} users · {item.rating ? `${item.rating}★` : "no rating"}</span>
            </span>
            <button
              aria-label={`Add ${item.name}`}
              className={PRIMARY}
              disabled={Boolean(busy) || installedIds.has(item.guid)}
              type="button"
              onClick={() => void run(`Adding ${item.name}…`, () => library("firefox-addon-install", { key: item.slug })).then((result) => applied(result, () => `Added ${item.name}.`))}
            >{installedIds.has(item.guid) ? "Added" : "Add"}</button>
          </div>
        ))}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Import

function FirefoxImport() {
  const [profiles, setProfiles] = useState(null);
  const [selected, setSelected] = useState(null);
  const [items, setItems] = useState({ settings: true, extensions: true });
  const [extensionIds, setExtensionIds] = useState([]);
  const [closedConfirmed, setClosedConfirmed] = useState(false);
  const [result, setResult] = useState(null);
  const { busy, error, run } = useAction();

  function choose(profile) {
    setSelected(profile);
    setResult(null);
    setClosedConfirmed(false);
    setExtensionIds((profile.extensions || []).filter((item) => item.active).map((item) => item.id));
    setItems({ settings: profile.available.settings, extensions: profile.available.extensions });
  }
  async function pick(path) {
    const inspected = await run("Reading the profile…", () => library("firefox-profile-inspect", { path }));
    if (inspected?.profile) choose(inspected.profile);
  }
  async function start() {
    const done = await run("Importing… Firefox restarts when it finishes.", () => library("firefox-import", {
      path: selected.path,
      items,
      extensionIds,
      confirmedClosed: closedConfirmed,
    }));
    if (done?.imported) setResult(done.imported);
  }

  const running = selected?.state === "running";
  const unknown = selected?.state === "unknown";
  const closedOk = selected?.state === "closed" || (unknown && closedConfirmed);
  return (
    <div className="grid gap-3">
      <p className="text-[11px] leading-4 text-muted">
        Bring your everyday Firefox’s settings, bookmarks and extensions into Raticode’s Firefox. Raticode only reads that profile and never changes it. Saved passwords and your Firefox account aren’t imported.
      </p>
      <div className="flex flex-wrap gap-1.5">
        <button className={BUTTON} disabled={Boolean(busy)} type="button" onClick={() => void run("Looking for Firefox profiles…", async () => setProfiles((await library("firefox-profiles")).profiles))}>Find my Firefox profiles</button>
        <button className={BUTTON} disabled={Boolean(busy)} type="button" onClick={() => void run("Waiting for a folder…", () => library("firefox-profile-choose")).then((value) => value?.profile && choose(value.profile))}>Choose folder…</button>
      </div>
      <Status busy={busy} error={error} />
      {profiles && !profiles.length ? <p className="text-[11px] text-muted">No Firefox profiles found. Use Choose folder… to pick one.</p> : null}
      {profiles?.length ? (
        <div aria-label="Firefox profiles" className="grid gap-1" role="radiogroup">
          {profiles.map((profile) => (
            <label key={profile.path} className="flex items-center gap-2 rounded-md border border-line px-2.5 py-1.5 text-xs text-ink">
              <input checked={selected?.path === profile.path} name="firefox-profile" type="radio" onChange={() => void pick(profile.path)} />
              <span className="min-w-0 flex-1">
                <span className="font-semibold">{profile.name}</span>{profile.isDefault ? <span className="text-[10px] text-muted"> · default</span> : null}
                <span className="block truncate text-[10px] text-muted">{profile.path}</span>
              </span>
              <span className="text-[10px] text-muted">{profile.state === "running" ? "Firefox is open" : profile.state === "closed" ? "closed" : ""}</span>
            </label>
          ))}
        </div>
      ) : null}
      {selected ? (
        <section aria-label="What to import" className="grid gap-1.5 rounded-md border border-line px-2.5 py-2">
          {running ? <p className="text-[11px] text-amber-800">Firefox is using this profile. Close it to also import bookmarks, site permissions, sign-ins and extension data.</p> : null}
          {unknown ? (
            <label className="flex items-center gap-2 text-[11px] text-ink">
              <input checked={closedConfirmed} type="checkbox" onChange={(event) => setClosedConfirmed(event.target.checked)} />
              Firefox is closed. Raticode can’t check this on this system.
            </label>
          ) : null}
          {IMPORT_CHOICES.filter(([key]) => selected.available[key]).map(([key, label, description]) => {
            const blocked = NEEDS_CLOSED.has(key) && !closedOk;
            return (
              <label key={key} className={`flex items-start gap-2 text-xs ${blocked ? "text-muted" : "text-ink"}`}>
                <input checked={items[key] === true && !blocked} disabled={blocked || (key === "extensionData" && !items.extensions)} type="checkbox" onChange={(event) => setItems((current) => ({ ...current, [key]: event.target.checked }))} />
                <span>{label}{description ? <span className="block text-[10px] leading-4 text-muted">{description}</span> : null}</span>
              </label>
            );
          })}
          {items.extensions && selected.extensions.length ? (
            <div aria-label="Extensions to import" className="ml-5 grid gap-0.5">
              {selected.extensions.map((extension) => (
                <label key={extension.id} className="flex items-center gap-2 text-[11px] text-ink">
                  <input checked={extensionIds.includes(extension.id)} type="checkbox" onChange={(event) => setExtensionIds((current) => (event.target.checked ? [...current, extension.id] : current.filter((id) => id !== extension.id)))} />
                  {extension.name} <span className="text-[10px] text-muted">{extension.version}{extension.active ? "" : " · off"}</span>
                </label>
              ))}
            </div>
          ) : null}
          <div>
            <button
              className={PRIMARY}
              disabled={Boolean(busy) || !Object.entries(items).some(([key, wanted]) => wanted && !(NEEDS_CLOSED.has(key) && !closedOk))}
              type="button"
              onClick={() => void start()}
            >Import</button>
          </div>
        </section>
      ) : null}
      {result ? (
        <div className="rounded-md border border-line px-2.5 py-2 text-[11px] text-ink" role="status">
          <p className="font-semibold">Import finished.</p>
          {result.copied.length ? <p className="text-muted">Copied: {result.copied.length} file{result.copied.length === 1 ? "" : "s"}.</p> : null}
          {result.installed.length ? <p className="text-muted">Extensions added: {result.installed.join(", ")}.</p> : null}
          {result.failed.map((item) => <p key={item.name} className="text-amber-800">{item.name} wasn’t added: {item.reason}.</p>)}
          {(result.skipped || []).map((label) => <p key={label} className="text-amber-800">{label} weren’t copied: Raticode couldn’t read that Firefox’s database to leave out its other containers.</p>)}
          <p className="break-all text-[10px] text-muted">What the import replaced is in {result.backupDir}</p>
        </div>
      ) : null}
      <details className="rounded-md border border-line px-2.5 py-1.5">
        <summary className="cursor-pointer text-[11px] font-semibold text-ink">More</summary>
        <div className="mt-2 grid gap-2">
          <div>
            <button className={BUTTON} disabled={Boolean(busy)} type="button" onClick={() => void run("Resetting…", () => library("firefox-reset")).then((value) => value?.reset && setResult(null))}>Reset Raticode’s Firefox…</button>
            <p className="mt-1 text-[10px] leading-4 text-muted">Moves its settings, extensions and sign-ins to a backup folder and starts fresh.</p>
          </div>
        </div>
      </details>
    </div>
  );
}
