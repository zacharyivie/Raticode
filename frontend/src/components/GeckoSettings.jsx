import { useEffect, useState } from "react";

// Settings > Browser > Raticode (gecko). Gecko's own settings, bookmarks,
// extensions and Mozilla account are inside the Raticode (gecko) tab; these are the things that have to
// happen while it isn't running.

const BUTTON = "h-7 rounded-md border border-line bg-white px-2.5 text-[11px] font-semibold text-ink transition hover:bg-slate-50 disabled:cursor-default disabled:opacity-50";
const FIELD = "h-8 w-full rounded-md border border-line bg-white px-2 text-xs text-ink outline-none focus:border-brand";

function cleanError(cause) {
  return String(cause?.message || cause || "That didn't work.").replace(/^Error invoking remote method '[^']+': (?:Error: )?/, "");
}

export default function GeckoSettings() {
  const gecko = window.goferBrowser?.gecko;
  const [status, setStatus] = useState(null);
  const [newName, setNewName] = useState("");
  const [sources, setSources] = useState(null);
  const [source, setSource] = useState("");
  const [details, setDetails] = useState(null);
  const [chosen, setChosen] = useState({});
  const [extensionIds, setExtensionIds] = useState([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  async function run(work) {
    setBusy(true);
    setError("");
    try {
      return await work();
    } catch (cause) {
      setError(cleanError(cause));
      return null;
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!gecko) return;
    void gecko("status").then(setStatus).catch((cause) => setError(cleanError(cause)));
    void gecko("import-sources").then((found) => {
      setSources(found);
      if (found.sources[0]) setSource(found.sources[0].path);
    }).catch((cause) => setError(cleanError(cause)));
  }, [gecko]);

  useEffect(() => {
    if (!gecko || !source) { setDetails(null); return undefined; }
    let cancelled = false;
    void gecko("import-inspect", { path: source }).then((found) => {
      if (cancelled) return;
      setDetails(found);
      setChosen({});
      setExtensionIds(found.extensions.map((item) => item.id));
    }).catch((cause) => { if (!cancelled) setError(cleanError(cause)); });
    return () => { cancelled = true; };
  }, [gecko, source]);

  if (!gecko) return <p className="text-[11px] text-muted">Raticode (gecko) needs Raticode&apos;s desktop app.</p>;

  async function importNow() {
    const result = await run(() => gecko("import", { path: source, items: chosen, extensionIds }));
    if (!result || result.cancelled) return;
    setStatus(result);
    const count = result.imported.extensions.length;
    const skipped = result.imported.skipped.length ? ` Skipped: ${result.imported.skipped.join(", ")}.` : "";
    setNote(`Imported${count ? `, with ${count} extension${count === 1 ? "" : "s"}` : ""}. Open the Raticode (gecko) tab to use it.${skipped}`);
  }

  const items = sources ? Object.entries(sources.items).filter(([key]) => details?.available?.[key]) : [];
  const anything = Object.values(chosen).some(Boolean);
  return (
    <div className="grid gap-3" data-testid="gecko-settings">
      <p className="text-[11px] text-muted">For private browsing, use Firefox&apos;s own New private window (Ctrl+Shift+P) in the Raticode (gecko) tab. It opens as its own window, outside Raticode.</p>
      <p className="text-[11px] text-muted">To bring everything across, including passwords, sign in to your Mozilla account inside Raticode (gecko) and turn on Sync.</p>
      <div className="grid gap-1">
        <span className="text-[11px] font-semibold text-ink">Profile</span>
        <select aria-label="Raticode (gecko) profile" className={FIELD} disabled={busy || !status} value={status?.profile || "default"} onChange={(event) => void run(async () => setStatus(await gecko("profile-choose", { id: event.target.value })))}>
          {(status?.profiles || []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <div className="flex gap-2">
          <input aria-label="New profile name" className={FIELD} maxLength={40} placeholder="New profile" value={newName} onChange={(event) => setNewName(event.target.value)} />
          <button type="button" className={BUTTON} disabled={busy || !newName.trim()} onClick={() => void run(async () => { setStatus(await gecko("profile-create", { name: newName })); setNewName(""); })}>Add</button>
        </div>
        <p className="text-[11px] text-muted">Each profile has its own sign-ins, extensions and bookmarks. Switching closes Raticode (gecko).</p>
      </div>
      <div className="grid gap-1">
        <span className="text-[11px] font-semibold text-ink">Import from your everyday Firefox</span>
        {sources && !sources.sources.length ? <p className="text-[11px] text-muted">No Firefox profiles were found on this computer.</p> : null}
        {sources?.sources.length ? (
          <select aria-label="Firefox profile to import" className={FIELD} disabled={busy} value={source} onChange={(event) => setSource(event.target.value)}>
            {sources.sources.map((item) => <option key={item.path} value={item.path}>{item.name}{item.isDefault ? " (default)" : ""}</option>)}
          </select>
        ) : null}
        {items.map(([key, label]) => (
          <label key={key} className="flex items-center gap-2 text-[11px] text-ink">
            <input type="checkbox" checked={chosen[key] === true} disabled={busy} onChange={(event) => setChosen((current) => ({ ...current, [key]: event.target.checked }))} />
            {label}
          </label>
        ))}
        {chosen.extensions && details?.extensions.length ? (
          <div className="grid gap-1 pl-5">
            {details.extensions.map((item) => (
              <label key={item.id} className="flex items-center gap-2 text-[11px] text-ink">
                <input type="checkbox" checked={extensionIds.includes(item.id)} disabled={busy} onChange={(event) => setExtensionIds((current) => (event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id)))} />
                {item.name}{item.active ? "" : " (off in Firefox)"}
              </label>
            ))}
          </div>
        ) : null}
        {details?.state === "locked" ? <p className="text-[11px] text-amber-700">Close Firefox to import bookmarks, sign-ins and extension data.</p> : null}
        {details ? <div><button type="button" className={BUTTON} disabled={busy || !anything} onClick={() => void importNow()}>Import</button></div> : null}
      </div>
      {note ? <p role="status" className="text-[11px] text-muted">{note}</p> : null}
      {error ? <p role="alert" className="text-[11px] text-amber-700">{error}</p> : null}
    </div>
  );
}
