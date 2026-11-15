import { useEffect, useRef, useState } from "react";
import { Loader2, Monitor, X } from "lucide-react";
import { AttachmentTile } from "./AttachmentTiles.jsx";
import { screenBridge, screenFileName, screenFromCapture, useScreenContextEnabled, useScreenStatus } from "../lib/screenContext.js";
import { setRemScreen, updateRemComposer } from "../lib/remComposer.js";

// Show Rem your screen, in Rem's message box: the Screen button, the chooser of screens and
// windows, and the screenshot waiting to be sent. Nothing is captured until you pick
// something; on Wayland the desktop's own dialog does the choosing.

async function take(sourceId = "") {
  const bridge = screenBridge();
  if (!bridge?.capture) return { ok: false, error: "Only the Raticode desktop app can take a screenshot." };
  try {
    return (await bridge.capture(sourceId)) ?? { ok: false, error: "Raticode couldn't take a screenshot." };
  } catch {
    return { ok: false, error: "Raticode couldn't take a screenshot." };
  }
}

function keep(result) {
  if (!result?.ok) {
    if (result?.code !== "cancelled") updateRemComposer({ error: result?.error || "Raticode couldn't take a screenshot." });
    return false;
  }
  const screen = screenFromCapture(result);
  if (!screen) {
    updateRemComposer({ error: "The screenshot is too large to send to Rem." });
    return false;
  }
  setRemScreen(screen);
  return true;
}

export function ScreenButton({ disabled = false }) {
  const enabled = useScreenContextEnabled();
  const status = useScreenStatus();
  const [open, setOpen] = useState(false);
  const [working, setWorking] = useState(false);
  if (!enabled || !screenBridge()) return null;
  const portal = status?.support === "portal";
  const label = "Show Rem your screen";
  return <span className="relative inline-flex">
    <button
      aria-label={label}
      aria-expanded={portal ? undefined : open}
      aria-haspopup={portal ? undefined : "dialog"}
      className="grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
      data-screen-button
      disabled={disabled || working}
      title={portal ? `${label}: your desktop asks which screen or window` : `${label}: choose a screen or window`}
      type="button"
      onClick={async () => {
        if (!portal) {
          setOpen((current) => !current);
          return;
        }
        setWorking(true);
        keep(await take());
        setWorking(false);
      }}
    >
      {working ? <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={15} /> : <Monitor aria-hidden="true" size={16} />}
    </button>
    {open ? <ScreenChooser onClose={() => setOpen(false)} /> : null}
  </span>;
}

// Screens first, then windows, each with a small preview. The previews live only while
// this is open.
function ScreenChooser({ onClose }) {
  const [state, setState] = useState({ status: "loading", sources: [], error: "", code: "" });
  const [taking, setTaking] = useState("");
  const panelRef = useRef(null);
  useEffect(() => {
    let cancelled = false;
    const bridge = screenBridge();
    Promise.resolve(bridge?.sources?.())
      .then((result) => {
        if (cancelled) return;
        if (!result?.ok) setState({ status: "error", sources: [], error: result?.error || "Raticode couldn't list your screens.", code: result?.code || "" });
        else setState({ status: "ready", sources: result.sources || [], error: "", code: "" });
      }, () => { if (!cancelled) setState({ status: "error", sources: [], error: "Raticode couldn't list your screens.", code: "" }); });
    return () => { cancelled = true; };
  }, []);
  useEffect(() => {
    if (state.status !== "loading") panelRef.current?.querySelector("button[data-screen-source], button[data-screen-close]")?.focus();
  }, [state.status]);
  useEffect(() => {
    const away = (event) => { if (panelRef.current && !panelRef.current.parentElement?.contains(event.target)) onClose(); };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [onClose]);

  async function pick(source) {
    setTaking(source.id);
    const result = await take(source.id);
    setTaking("");
    if (keep(result)) onClose();
    else if (result?.error) setState((current) => ({ ...current, error: result.error, code: result.code || "" }));
  }

  const screens = state.sources.filter((source) => source.kind === "screen");
  const windows = state.sources.filter((source) => source.kind === "window");
  const group = (title, items) => items.length ? <div>
    <p className="px-1 pb-1 text-[10px] font-semibold uppercase tracking-wide text-muted">{title}</p>
    <ul className="grid grid-cols-2 gap-1.5">
      {items.map((source) => <li key={source.id}>
        <button
          type="button"
          className="flex w-full flex-col gap-1 rounded-md border border-line p-1 text-left outline-none hover:border-indigo-400 focus-visible:ring-2 focus-visible:ring-indigo-500/60 disabled:opacity-50"
          data-screen-source={source.kind}
          disabled={Boolean(taking)}
          title={source.name}
          onClick={() => void pick(source)}
        >
          <span className="relative block aspect-video w-full overflow-hidden rounded bg-slate-100 dark:bg-slate-800">
            {source.thumbnail ? <img alt="" className="h-full w-full object-contain" src={source.thumbnail} /> : null}
            {taking === source.id ? <span className="absolute inset-0 grid place-items-center bg-black/40 text-white"><Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={16} /></span> : null}
          </span>
          <span className="truncate text-[11px] text-ink">{source.name}</span>
        </button>
      </li>)}
    </ul>
  </div> : null;

  return <div
    ref={panelRef}
    aria-label="Choose what Rem sees"
    className="absolute bottom-10 left-0 z-30 w-[min(420px,80vw)] rounded-xl border border-line bg-white p-2 shadow-lg dark:bg-[#18181b]"
    data-screen-chooser={state.status}
    role="dialog"
    onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } }}
  >
    <div className="flex items-start justify-between gap-2 px-1 pb-2">
      <p className="text-[11px] leading-4 text-muted">Pick a screen or window. Rem gets one screenshot with your next message, for that reply only. You can remove it before sending.</p>
      <button aria-label="Cancel" className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 hover:text-ink" data-screen-close title="Cancel (Esc)" type="button" onClick={onClose}><X aria-hidden="true" size={13} /></button>
    </div>
    {state.status === "loading" ? <p role="status" className="px-1 py-3 text-[11px] text-muted">Looking for screens and windows…</p> : null}
    {state.error ? <div role="alert" className="space-y-1 px-1 pb-2 text-[11px] text-red-600">
      <p>{state.error}</p>
      {state.code === "permission" && screenBridge()?.openPrivacySettings ? <button className="rounded-md border border-line px-2 py-1 text-ink hover:bg-slate-50" type="button" onClick={() => void screenBridge().openPrivacySettings()}>Open System Settings</button> : null}
    </div> : null}
    {state.status === "ready" && !state.sources.length && !state.error ? <p role="status" className="px-1 py-3 text-[11px] text-muted">No screens or windows were found.</p> : null}
    <div className="max-h-80 space-y-2 overflow-y-auto">
      {group("Screens", screens)}
      {group("Windows", windows)}
    </div>
  </div>;
}

// The screenshot waiting to go with the next message: a preview that opens full size, and a
// remove button.
export function ScreenTile({ screen }) {
  if (!screen) return null;
  const attachment = { id: screen.id, file: screen.file, name: screenFileName(screen.name, screen.type), size: screen.size, type: screen.type };
  return <span className="inline-flex" data-screen-capture={screen.kind}>
    <AttachmentTile attachment={attachment} variant="composer" onRemove={() => setRemScreen(null)} />
  </span>;
}

export function screenNote(screen) {
  if (!screen) return "";
  return `Rem will see this screenshot of ${screen.name} with this message only. It isn't saved; remove it to send without it.`;
}

// On a sent message: which screen or window Rem was shown. The picture itself isn't kept.
export function ScreenSentChip({ name }) {
  if (!name) return null;
  return <span className="rem-msg-source" data-screen-sent title="Rem saw this screenshot for that reply only. It wasn't saved.">
    <Monitor aria-hidden="true" size={11} className="mr-1 inline align-[-1px]" />Showed Rem {name}
  </span>;
}
