import { useEffect, useState } from "react";
import { apiUrl } from "../lib/api.js";

// How often the offline model's download progress is read while it runs.
export const MODEL_POLL_MS = 700;
const ACTIVE_DOWNLOAD = new Set(["starting", "downloading", "installing"]);

async function request(path, { method = "GET", body, signal, failure } = {}) {
  const response = await fetch(apiUrl(path), {
    method,
    signal,
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || failure);
  return payload;
}

const loadEngines = (refresh, signal) => request(`/chat/transcribe/engines${refresh ? "?refresh=1" : ""}`, { signal, failure: "Could not check speech recognition" });
const saveEngine = (engine) => request("/chat/transcribe/engines", { method: "POST", body: { engine }, failure: "Could not save the speech setting" });
const loadModel = (signal) => request("/chat/transcribe/model", { signal, failure: "Could not check the offline model" });
const startModelDownload = () => request("/chat/transcribe/model", { method: "POST", body: {}, failure: "Could not start the download" });
const tryRecognizer = () => request("/chat/transcribe/engines/test", { method: "POST", body: {}, failure: "Could not test speech recognition" });

function megabytes(bytes) {
  return Math.round(bytes / (1024 * 1024));
}

// Which speech-to-text dictation uses. macOS and Windows have their own recognizers;
// Linux has none, so it always uses Raticode's offline model, which is downloaded here.
export default function SpeechEngineSettings() {
  const [state, setState] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [testing, setTesting] = useState(false);
  const [tried, setTried] = useState(null);
  useEffect(() => {
    const controller = new AbortController();
    loadEngines(refresh > 0, controller.signal)
      .then((payload) => { if (!controller.signal.aborted) { setState(payload); setError(""); } })
      .catch((failure) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [refresh]);

  const offline = state?.offline;
  const downloading = ACTIVE_DOWNLOAD.has(offline?.download?.state);
  useEffect(() => {
    if (!downloading) return undefined;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      loadModel(controller.signal)
        .then((status) => { if (!controller.signal.aborted) setState((current) => ({ ...current, offline: status })); })
        .catch((failure) => { if (!controller.signal.aborted) setError(failure.message); });
    }, MODEL_POLL_MS);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [downloading]);

  async function choose(engine) {
    setBusy(true); setError("");
    try {
      const saved = await saveEngine(engine);
      setState((current) => ({ ...current, engine: saved.engine }));
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  async function download() {
    setError("");
    try {
      const status = await startModelDownload();
      setState((current) => ({ ...current, offline: status }));
    } catch (failure) { setError(failure.message); }
  }

  async function test() {
    setTesting(true); setTried(null); setError("");
    try {
      const { test: result, ...engines } = await tryRecognizer();
      setTried(result);
      setState((current) => ({ ...current, ...engines }));
    } catch (failure) { setError(failure.message); }
    finally { setTesting(false); }
  }

  const system = state?.system;
  const controlClass = "h-8 rounded-md border border-line px-2 text-xs text-ink hover:bg-slate-100 disabled:opacity-60";
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
          <button className={controlClass} disabled={!state || busy || testing} type="button" onClick={() => setRefresh((value) => value + 1)}>
            Check again
          </button>
          {system.id === "macos" ? (
            <button className={controlClass} disabled={!state || busy || testing} type="button" onClick={() => void test()}>
              Test speech recognition
            </button>
          ) : null}
        </div>
      ) : null}
      {!state && !error ? <p className="text-xs text-muted">Checking speech recognition…</p> : null}
      {system && !system.id ? <p className="text-xs text-muted">{system.reason}</p> : null}
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
      {testing ? (
        <p role="status" className="text-xs text-muted">Testing {system?.name ?? "speech recognition"}… If macOS asks, allow speech recognition for Raticode.</p>
      ) : null}
      {tried && !testing ? (
        <p role="status" className={tried.ok ? "text-xs text-muted" : "text-xs text-amber-700 dark:text-amber-300"} data-speech-test={tried.ok ? "ok" : "failed"}>
          {tried.message}
        </p>
      ) : null}
      {offline ? <OfflineModel offline={offline} onDownload={() => void download()} controlClass={controlClass} /> : null}
      {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
    </div>
  );
}

// The offline model: installed, downloading with progress, failed with its reason, or a
// button to download it. Dictation never downloads it on its own.
function OfflineModel({ offline, onDownload, controlClass }) {
  const name = offline.name ?? "Raticode's offline speech model";
  const download = offline.download;
  if (offline.installed && !ACTIVE_DOWNLOAD.has(download?.state)) {
    return <p className="text-xs text-muted" data-offline-model="installed">{name} is installed. Speech is recognized on this computer.</p>;
  }
  if (ACTIVE_DOWNLOAD.has(download?.state)) {
    const total = download.totalBytes || 0;
    const received = download.receivedBytes || 0;
    const percent = total ? Math.min(100, Math.floor((received * 100) / total)) : null;
    const label = download.state === "downloading"
      ? `Downloading ${name}… ${total ? `${megabytes(received)} of ${megabytes(total)} MB` : `${megabytes(received)} MB`}`
      : download.state === "installing" ? `Checking and installing ${name}…` : `Starting the download of ${name}…`;
    return (
      <div className="space-y-1" data-offline-model="downloading">
        <p className="text-xs text-muted">{label}</p>
        <div
          aria-label="Offline speech model download"
          aria-valuemax={100}
          aria-valuemin={0}
          {...(percent === null ? {} : { "aria-valuenow": percent })}
          className="h-1.5 w-full max-w-xs overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700"
          role="progressbar"
        >
          <div
            className={`h-full rounded-full bg-sky-500 transition-[width] ${percent === null ? "w-1/3 animate-pulse" : ""}`}
            style={percent === null ? undefined : { width: `${percent}%` }}
          />
        </div>
      </div>
    );
  }
  const failed = download?.state === "failed";
  return (
    <div className="space-y-1" data-offline-model={failed ? "failed" : "missing"}>
      {failed ? (
        <p role="alert" className="text-xs text-amber-700 dark:text-amber-300">{download.error || `${name} couldn't be downloaded.`}</p>
      ) : (
        <p className="text-xs text-muted">
          {name} isn&apos;t downloaded yet. It&apos;s about {offline.downloadMb ?? 130} MB, downloaded once, and then works without a connection.
        </p>
      )}
      <button className={controlClass} type="button" onClick={onDownload}>
        {failed ? "Try the download again" : "Download offline model"}
      </button>
    </div>
  );
}
