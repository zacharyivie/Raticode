import { useEffect, useState } from "react";
import { apiUrl } from "../lib/api.js";

async function loadEngines(refresh, signal) {
  const response = await fetch(apiUrl(`/chat/transcribe/engines${refresh ? "?refresh=1" : ""}`), { signal });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not check speech recognition");
  return payload;
}

async function saveEngine(engine) {
  const response = await fetch(apiUrl("/chat/transcribe/engines"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ engine }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not save the speech setting");
  return payload;
}

// Which speech-to-text dictation uses. macOS and Windows have their own recognizers;
// Linux has none, so it always uses Raticode's offline model.
export default function SpeechEngineSettings() {
  const [state, setState] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    loadEngines(refresh > 0, controller.signal)
      .then((payload) => { if (!controller.signal.aborted) { setState(payload); setError(""); } })
      .catch((failure) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [refresh]);

  async function choose(engine) {
    setBusy(true); setError("");
    try {
      const saved = await saveEngine(engine);
      setState((current) => ({ ...current, engine: saved.engine }));
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  const system = state?.system;
  const offline = state?.offline;
  const offlineNote = offline?.installed
    ? `${offline.name} is installed.`
    : `${offline?.name ?? "The offline model"} downloads once, about ${offline?.downloadMb ?? 130} MB, the first time it's used.`;
  return (
    <div className="space-y-1.5" data-speech-engine={state?.engine ?? ""}>
      {system?.id ? (
        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label="Speech recognition"
            className="h-8 min-w-0 rounded-md border border-line bg-white px-2 text-xs text-ink disabled:opacity-60"
            disabled={!state || busy}
            value={state?.engine ?? "system"}
            onChange={(event) => void choose(event.target.value)}
          >
            <option value="system">Built into {system.id === "macos" ? "macOS" : "Windows"}</option>
            <option value="offline">Raticode offline model</option>
          </select>
          <button
            className="h-8 rounded-md border border-line px-2 text-xs text-ink hover:bg-slate-100 disabled:opacity-60"
            disabled={!state || busy}
            type="button"
            onClick={() => setRefresh((value) => value + 1)}
          >
            Check again
          </button>
        </div>
      ) : null}
      {!state && !error ? <p className="text-xs text-muted">Checking speech recognition…</p> : null}
      {system && !system.id ? <p className="text-xs text-muted">{system.reason} {offlineNote}</p> : null}
      {system?.id && state.engine === "system" ? (
        system.available ? (
          <p className="text-xs text-muted">
            {system.name} is ready{system.language ? ` (${system.language})` : ""}. Speech is recognized on this computer.
            {system.reason ? ` ${system.reason}` : ""}
          </p>
        ) : (
          <p role="status" className="text-xs text-amber-700 dark:text-amber-300">
            {system.reason} Until then, dictation uses Raticode&apos;s offline model if it&apos;s installed.
          </p>
        )
      ) : null}
      {system?.id && state.engine === "offline" ? <p className="text-xs text-muted">{offlineNote} Speech is recognized on this computer.</p> : null}
      {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
    </div>
  );
}
