import { useEffect, useRef, useState } from "react";
import {
  Bookmark,
  Bot,
  Clock,
  Cookie,
  Download,
  FolderOpen,
  Globe,
  History,
  Info,
  KeyRound,
  Lock,
  Pause,
  Play,
  Search,
  ShieldAlert,
  Star,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { downloadStatus, formatZoom, hostLabel, relativeTime } from "../lib/browserUi.js";

const BUTTON = "rounded-md border border-line bg-white px-2.5 py-1 text-xs font-medium text-ink hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand disabled:opacity-40";
const PRIMARY = "rounded-md bg-brand px-2.5 py-1 text-xs font-semibold text-white hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand";

// ---------------------------------------------------------------------------
// Address bar pieces

const SECURITY_ICONS = {
  secure: { Icon: Lock, tone: "text-muted" },
  insecure: { Icon: ShieldAlert, tone: "text-red-700" },
  warning: { Icon: TriangleAlert, tone: "text-amber-700" },
  error: { Icon: TriangleAlert, tone: "text-red-700" },
  local: { Icon: Info, tone: "text-muted" },
  internal: { Icon: Globe, tone: "text-muted" },
};

export function SecurityChip({ security, onClick }) {
  const level = security?.level || "internal";
  const { Icon, tone } = SECURITY_ICONS[level] || SECURITY_ICONS.internal;
  const showText = level === "insecure" || level === "warning" || level === "error";
  return (
    <button
      aria-label={`${security?.label || "Site information"}. View site information`}
      className={`absolute left-1 top-1/2 z-10 flex h-5 -translate-y-1/2 items-center gap-1 rounded px-1 text-[10px] font-semibold hover:bg-slate-100 ${tone}`}
      title={security?.label || "Site information"}
      type="button"
      onClick={onClick}
    >
      <Icon aria-hidden="true" size={12} />
      {showText ? <span className="hidden sm:inline">Not secure</span> : null}
    </button>
  );
}

export function AddressSuggestions({ rows, activeIndex, onPick, onHover, id }) {
  if (!rows.length) return null;
  const icon = { navigate: Globe, search: Search, tab: Globe, bookmark: Star, history: Clock };
  return (
    <ul id={id} role="listbox" aria-label="Address suggestions" className="absolute left-0 right-0 top-8 z-40 overflow-hidden rounded-md border border-line bg-white py-1 shadow-lg">
      {rows.map((row, index) => {
        const Icon = icon[row.kind] || Globe;
        return (
          <li
            key={`${row.kind}:${row.value}`}
            id={`${id}-${index}`}
            role="option"
            aria-selected={index === activeIndex}
            className={`flex cursor-default items-center gap-2 px-2.5 py-1.5 text-xs ${index === activeIndex ? "bg-slate-100" : ""}`}
            onMouseDown={(event) => { event.preventDefault(); onPick(row); }}
            onMouseEnter={() => onHover(index)}
          >
            <Icon aria-hidden="true" className="shrink-0 text-muted" size={12} />
            <span className="min-w-0 truncate text-ink">
              {row.kind === "search" ? <>{row.label} <span className="text-muted">— Search</span></> : row.label}
            </span>
            {row.url && row.label !== row.url ? <span className="min-w-0 shrink truncate text-muted">{hostLabel(row.url)}</span> : null}
            {row.kind === "tab" ? <span className="ml-auto shrink-0 rounded bg-slate-100 px-1.5 text-[10px] text-muted">Switch to tab</span> : null}
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Prompts shown under the toolbar

export function PromptBar({ prompt, onRespond }) {
  if (!prompt) return null;
  if (prompt.kind === "permission") {
    return (
      <div role="alertdialog" aria-label="Site permission request" className="flex flex-wrap items-center gap-2 border-b border-line bg-sky-50 px-3 py-2 text-xs text-ink">
        <span className="min-w-0 flex-1">
          <strong className="font-semibold">{hostLabel(prompt.origin)}</strong> wants to {prompt.permissions.map((item) => item.label.charAt(0).toLowerCase() + item.label.slice(1)).join(" and ")}.
        </span>
        <button className={PRIMARY} type="button" onClick={() => onRespond("allow")}>Allow</button>
        <button className={BUTTON} type="button" onClick={() => onRespond("allow-once")}>Allow this time</button>
        <button className={BUTTON} type="button" onClick={() => onRespond("block")}>Block</button>
        <button aria-label="Dismiss request" className="rounded p-1 text-muted hover:bg-slate-100" type="button" onClick={() => onRespond(null)}><X size={12} /></button>
      </div>
    );
  }
  if (prompt.kind === "rem-confirm") {
    return (
      <div role="alertdialog" aria-label={`${prompt.agentLabel || "Rem"} needs approval`} className="flex flex-wrap items-center gap-2 border-b border-line bg-amber-50 px-3 py-2 text-xs text-ink">
        <Bot aria-hidden="true" className="shrink-0 text-amber-800" size={14} />
        <span className="min-w-0 flex-1">
          <strong className="font-semibold">{prompt.agentLabel || "Rem"}</strong> wants to <strong className="font-semibold">{prompt.label || prompt.action}</strong>
          {prompt.url ? <> on {hostLabel(prompt.url)}</> : null}. Approve only if you asked for this.
        </span>
        <button className={PRIMARY} type="button" onClick={() => onRespond("approve")}>Approve</button>
        <button className={BUTTON} type="button" onClick={() => onRespond(null)}>Decline</button>
      </div>
    );
  }
  if (prompt.kind === "auth") return <AuthPrompt prompt={prompt} onRespond={onRespond} />;
  if (prompt.kind === "client-certificate") return <ClientCertificatePrompt prompt={prompt} onRespond={onRespond} />;
  return null;
}

function AuthPrompt({ prompt, onRespond }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const userRef = useRef(null);
  useEffect(() => { userRef.current?.focus(); }, []);
  return (
    <form
      aria-label="Sign in to site"
      className="flex flex-wrap items-center gap-2 border-b border-line bg-sky-50 px-3 py-2 text-xs text-ink"
      onSubmit={(event) => {
        event.preventDefault();
        onRespond({ username, password });
        setPassword("");
      }}
    >
      <KeyRound aria-hidden="true" className="shrink-0 text-muted" size={14} />
      <span className="min-w-0">
        Sign in to <strong className="font-semibold">{prompt.host}</strong>
        {prompt.isProxy ? " (proxy)" : ""}{prompt.realm ? ` — “${prompt.realm}”` : ""}. Raticode does not save these credentials.
      </span>
      <input ref={userRef} aria-label="Username" autoComplete="username" className="h-6 w-32 rounded border border-line px-2" value={username} onChange={(event) => setUsername(event.target.value)} />
      <input aria-label="Password" autoComplete="current-password" className="h-6 w-32 rounded border border-line px-2" type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
      <button className={PRIMARY} type="submit">Sign in</button>
      <button className={BUTTON} type="button" onClick={() => { setPassword(""); onRespond(null); }}>Cancel</button>
    </form>
  );
}

function ClientCertificatePrompt({ prompt, onRespond }) {
  const [index, setIndex] = useState(prompt.certificates?.[0]?.index ?? 0);
  return (
    <div role="alertdialog" aria-label="Choose a certificate" className="flex flex-wrap items-center gap-2 border-b border-line bg-sky-50 px-3 py-2 text-xs text-ink">
      <span className="min-w-0"><strong className="font-semibold">{prompt.host}</strong> asks for a certificate to identify you.</span>
      <select aria-label="Certificate" className="h-6 max-w-xs rounded border border-line bg-white px-1" value={index} onChange={(event) => setIndex(Number(event.target.value))}>
        {(prompt.certificates || []).map((item) => (
          <option key={item.index} value={item.index}>{item.subject || "Certificate"} — {item.issuer}</option>
        ))}
      </select>
      <button className={PRIMARY} type="button" onClick={() => onRespond({ index })}>Use certificate</button>
      <button className={BUTTON} type="button" onClick={() => onRespond(null)}>Don&apos;t send</button>
    </div>
  );
}

export function BlockedPopupsBar({ popups, onOpen, onAllow, onDismiss }) {
  if (!popups?.length) return null;
  return (
    <div role="status" className="flex flex-wrap items-center gap-2 border-b border-line bg-slate-50 px-3 py-1.5 text-xs text-ink">
      <span className="min-w-0 flex-1">
        Pop-up blocked{popups.length > 1 ? ` (${popups.length})` : ""}: <span className="text-muted">{hostLabel(popups.at(-1).url)}</span>
      </span>
      <button className={BUTTON} type="button" onClick={() => onOpen(popups.length - 1)}>Open</button>
      <button className={BUTTON} type="button" onClick={onAllow}>Always allow on this site</button>
      <button aria-label="Dismiss blocked pop-ups" className="rounded p-1 text-muted hover:bg-slate-100" type="button" onClick={onDismiss}><X size={12} /></button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page overlays: load errors, certificate interstitial, crashes

export function PageOverlay({ state, onRetry, onBack, onProceed, onReload, onDismissUnresponsive }) {
  if (state.certificateError && state.loadError?.kind === "certificate") {
    const failure = state.certificateError;
    return (
      <Overlay tone="danger" title="Your connection is not private" icon={ShieldAlert}>
        <p>
          Attackers might be trying to steal your information from <strong>{failure.host}</strong> (for example, passwords or messages).
        </p>
        <p className="mt-2 font-mono text-[11px] text-muted">{failure.error}</p>
        {failure.issuer ? <p className="mt-1 text-[11px] text-muted">Issued by {failure.issuer}{failure.validExpiry ? ` · expires ${new Date(failure.validExpiry * 1000).toLocaleDateString()}` : ""}</p> : null}
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <button className={PRIMARY} type="button" onClick={onBack}>Back to safety</button>
          {failure.canProceed && failure.fingerprint ? (
            <button className={BUTTON} type="button" onClick={onProceed}>Continue to {failure.host} (unsafe)</button>
          ) : null}
        </div>
      </Overlay>
    );
  }
  if (state.crashed) {
    return (
      <Overlay title="This page crashed" icon={TriangleAlert}>
        <p>The page stopped unexpectedly ({state.crashed.reason}).</p>
        <div className="mt-4"><button className={PRIMARY} type="button" onClick={onReload}>Reload</button></div>
      </Overlay>
    );
  }
  if (state.loadError) {
    return (
      <Overlay title={state.loadError.title || "Could not display this page"} icon={Globe}>
        <p>{state.loadError.detail}</p>
        {state.loadError.url ? <p className="mt-1 truncate text-[11px] text-muted">{state.loadError.url}</p> : null}
        <div className="mt-4"><button className={PRIMARY} type="button" onClick={onRetry}>Retry</button></div>
      </Overlay>
    );
  }
  if (state.error && !state.ready) {
    return (
      <Overlay title="Could not display this page" icon={Globe}>
        <p>{state.error}</p>
      </Overlay>
    );
  }
  if (state.unresponsive) {
    return (
      <div role="alert" className="absolute inset-x-0 top-0 z-20 flex items-center gap-2 border-b border-line bg-amber-50 px-3 py-2 text-xs text-ink">
        <span className="flex-1">This page isn&apos;t responding.</span>
        <button className={BUTTON} type="button" onClick={onDismissUnresponsive}>Wait</button>
        <button className={PRIMARY} type="button" onClick={onReload}>Reload</button>
      </div>
    );
  }
  return null;
}

function Overlay({ children, icon: Icon, title, tone }) {
  return (
    <div className="absolute inset-0 z-10 grid place-items-center bg-white px-8 text-center">
      <div className="max-w-lg text-xs leading-5 text-muted">
        {Icon ? <Icon aria-hidden="true" className={`mx-auto mb-3 ${tone === "danger" ? "text-red-700" : "text-muted"}`} size={28} /> : null}
        <p className="text-sm font-semibold text-ink">{title}</p>
        <div className="mt-1">{children}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Rem's presence in a tab

export function RemChip({ agent, signedIn = false, onPause, onStop, onTakeOver }) {
  const [open, setOpen] = useState(false);
  if (!agent) return null;
  const label = agent.label || "Rem";
  return (
    <div className="relative shrink-0">
      <button
        aria-expanded={open}
        className={`inline-flex h-6 items-center gap-1 rounded-full border px-2 text-[10px] font-semibold ${agent.paused ? "border-amber-300 bg-amber-50 text-amber-800" : "border-line bg-white text-muted"}`}
        title={agent.shared ? `${label} can control this tab because you shared it` : `${label} opened this tab and can control it`}
        type="button"
        onClick={() => setOpen((value) => !value)}
      ><Bot aria-hidden="true" size={12} />{agent.paused ? `${label} paused` : agent.shared ? `Shared with ${label}` : `Controlled by ${label}`}{signedIn ? " · your sign-in" : ""}</button>
      {open ? (
        <div role="dialog" aria-label={`${label} activity`} className="absolute right-0 top-8 z-40 w-80 rounded-md border border-line bg-white p-3 text-xs text-ink shadow-lg">
          <div className="mb-2 flex items-center justify-between">
            <p className="font-semibold">{label} in this tab</p>
            <button aria-label="Close" className="rounded p-1 text-muted hover:bg-slate-100" type="button" onClick={() => setOpen(false)}><X size={12} /></button>
          </div>
          {signedIn || agent.shared ? <p className="mb-2 rounded bg-amber-50 px-2 py-1">{label} is using your sign-in on this site. It asks you before submitting or running scripts.</p> : null}
          {agent.dialog ? <p className="mb-2 rounded bg-amber-50 px-2 py-1">Page dialog waiting for {label}: “{agent.dialog.message}”</p> : null}
          <ol className="max-h-48 space-y-1 overflow-auto" aria-label="Recent actions">
            {(agent.log || []).length ? [...agent.log].reverse().map((entry, index) => (
              <li key={`${entry.at}-${index}`} className="flex gap-2">
                <span className="shrink-0 text-muted">{relativeTime(entry.at)}</span>
                <span className="min-w-0"><span className="font-medium">{entry.action}</span>{entry.detail ? <span className="text-muted"> · {entry.detail}</span> : null}</span>
              </li>
            )) : <li className="text-muted">No actions yet.</li>}
          </ol>
          <div className="mt-3 flex flex-wrap gap-2">
            <button className={BUTTON} type="button" onClick={() => onPause(!agent.paused)}>
              {agent.paused ? <><Play aria-hidden="true" className="mr-1 inline" size={11} />Resume {label}</> : <><Pause aria-hidden="true" className="mr-1 inline" size={11} />Pause {label}</>}
            </button>
            {agent.shared
              ? <button className={BUTTON} type="button" onClick={() => { setOpen(false); onStop(); }}>Stop sharing</button>
              : <button className={BUTTON} type="button" onClick={() => { setOpen(false); onTakeOver(); }}>Take over this tab</button>}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Site information

export function SiteInfoPopover({ info, onClose, onPermission, onClearData, onResetZoom }) {
  const ref = useRef(null);
  useEffect(() => { ref.current?.focus(); }, []);
  if (!info) return null;
  const level = info.security?.level;
  return (
    <div ref={ref} tabIndex={-1} role="dialog" aria-label="Site information" className="absolute left-2 top-10 z-40 w-80 rounded-md border border-line bg-white p-3 text-xs text-ink shadow-lg outline-none"
      onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); onClose(); } }}>
      <div className="mb-2 flex items-center justify-between">
        <p className="truncate font-semibold">{info.host || "This page"}</p>
        <button aria-label="Close site information" className="rounded p-1 text-muted hover:bg-slate-100" type="button" onClick={onClose}><X size={12} /></button>
      </div>
      <p className={level === "secure" ? "text-emerald-800" : level === "insecure" || level === "error" ? "text-red-700" : level === "warning" ? "text-amber-800" : "text-muted"}>
        {info.security?.label}
      </p>
      {level === "insecure" ? <p className="mt-1 text-muted">Don&apos;t enter passwords or payment details on this site. Others on the network can read them.</p> : null}
      {info.certificate ? (
        <p className="mt-1 text-[11px] text-muted">Certificate for {info.certificate.subject} from {info.certificate.issuer}{info.certificate.validExpiry ? `, valid until ${new Date(info.certificate.validExpiry * 1000).toLocaleDateString()}` : ""}.</p>
      ) : null}
      {info.profile === "rem" ? <p className="mt-2 rounded bg-slate-50 px-2 py-1 text-muted">This tab uses Rem&apos;s separate browser profile.</p> : null}
      {info.remSignIn ? <p className="mt-2 rounded bg-amber-50 px-2 py-1">Rem can use your sign-in on this site. Change this in Settings &gt; Browser.</p> : null}
      {info.permissions?.length || info.popups === "allow" ? (
        <div className="mt-3">
          <p className="mb-1 font-semibold">Permissions</p>
          <ul className="space-y-1">
            {info.permissions.map((item) => (
              <li key={item.key} className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate">{item.label}</span>
                <select aria-label={item.label} className="h-6 rounded border border-line bg-white px-1" value={item.value} onChange={(event) => onPermission(item.key, event.target.value === "ask" ? "" : event.target.value)}>
                  <option value="allow">Allow</option>
                  <option value="block">Block</option>
                  <option value="ask">Ask</option>
                </select>
              </li>
            ))}
            {info.popups === "allow" ? (
              <li className="flex items-center justify-between gap-2">
                <span>Pop-ups and redirects</span>
                <button className={BUTTON} type="button" onClick={() => onPermission("popups", "")}>Block again</button>
              </li>
            ) : null}
          </ul>
        </div>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {info.zoom && info.zoom !== 1 ? <button className={BUTTON} type="button" onClick={onResetZoom}>Reset zoom ({formatZoom(info.zoom)})</button> : null}
        {info.origin && info.origin !== "file://" ? <button className={BUTTON} type="button" onClick={onClearData}><Cookie aria-hidden="true" className="mr-1 inline" size={11} />Clear cookies and site data</button> : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// History, bookmarks, downloads and site data

export function LibraryPanel({ view, onViewChange, onClose, onOpenUrl, bridge }) {
  const views = [["history", "History", History], ["bookmarks", "Bookmarks", Bookmark], ["downloads", "Downloads", Download], ["data", "Site data", Cookie]];
  const panelRef = useRef(null);
  useEffect(() => { panelRef.current?.focus(); }, []);
  return (
    <div
      ref={panelRef}
      tabIndex={-1}
      role="dialog"
      aria-label="Browser library"
      className="absolute bottom-2 right-2 top-10 z-40 flex w-[min(28rem,calc(100%-1rem))] flex-col rounded-md border border-line bg-white text-xs text-ink shadow-xl outline-none"
      onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); onClose(); } }}
    >
      <div className="flex items-center gap-1 border-b border-line p-1.5" role="tablist" aria-label="Library sections">
        {views.map(([id, label, Icon]) => (
          <button key={id} aria-selected={view === id} role="tab" className={`flex items-center gap-1 rounded px-2 py-1 ${view === id ? "bg-slate-100 font-semibold" : "text-muted hover:text-ink"}`} type="button" onClick={() => onViewChange(id)}>
            <Icon aria-hidden="true" size={12} />{label}
          </button>
        ))}
        <button aria-label="Close library" className="ml-auto rounded p-1 text-muted hover:bg-slate-100" type="button" onClick={onClose}><X size={12} /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2">
        {view === "history" ? <HistoryView bridge={bridge} onOpenUrl={onOpenUrl} /> : null}
        {view === "bookmarks" ? <BookmarksView bridge={bridge} onOpenUrl={onOpenUrl} /> : null}
        {view === "downloads" ? <DownloadsView bridge={bridge} /> : null}
        {view === "data" ? <SiteDataView bridge={bridge} /> : null}
      </div>
    </div>
  );
}

function useLibrary(bridge, action, text, kinds) {
  const [items, setItems] = useState(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => bridge?.onLibraryChanged?.((change) => {
    if (kinds.includes(change?.kind) || change?.kind === "all") setRevision((value) => value + 1);
  }), [bridge, kinds]);
  useEffect(() => {
    let active = true;
    bridge?.library?.(action, { text }).then((result) => {
      if (active) { setItems(result?.[action] || []); setError(""); }
    }, (failure) => { if (active) setError(failure.message); });
    return () => { active = false; };
  }, [bridge, action, text, revision]);
  return { items, error, refresh: () => setRevision((value) => value + 1) };
}

const HISTORY_KINDS = ["history"];
const BOOKMARK_KINDS = ["bookmarks"];

function SearchBox({ label, value, onChange }) {
  return (
    <label className="mb-2 flex items-center gap-1.5 rounded border border-line px-2">
      <Search aria-hidden="true" className="text-muted" size={12} />
      <input aria-label={label} className="h-7 min-w-0 flex-1 bg-transparent outline-none" placeholder={label} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function HistoryView({ bridge, onOpenUrl }) {
  const [query, setQuery] = useState("");
  const { items, error } = useLibrary(bridge, "history", query, HISTORY_KINDS);
  const [clearing, setClearing] = useState(false);
  return (
    <>
      <SearchBox label="Search history" value={query} onChange={setQuery} />
      <div className="mb-2 flex justify-end"><button className={BUTTON} type="button" onClick={() => setClearing((value) => !value)}>Clear browsing data…</button></div>
      {clearing ? <ClearDataForm bridge={bridge} onDone={() => setClearing(false)} /> : null}
      {error ? <p role="alert" className="text-red-700">{error}</p> : null}
      {items && !items.length ? <p className="p-2 text-muted">{query ? "No pages match." : "Pages you visit appear here."}</p> : null}
      <ul className="space-y-0.5">
        {(items || []).map((item) => (
          <li key={item.url} className="group flex items-center gap-2 rounded px-1.5 py-1 hover:bg-slate-50">
            <button className="min-w-0 flex-1 text-left" type="button" onClick={() => onOpenUrl(item.url)}>
              <span className="block truncate text-ink">{item.title || item.url}</span>
              <span className="block truncate text-[10px] text-muted">{hostLabel(item.url)} · {relativeTime(item.lastVisit)}{item.visitCount > 1 ? ` · ${item.visitCount} visits` : ""}</span>
            </button>
            <button aria-label={`Remove ${item.title || item.url} from history`} className="rounded p-1 text-muted opacity-0 hover:bg-slate-100 focus:opacity-100 group-hover:opacity-100" type="button" onClick={() => void bridge.library("history-remove", { url: item.url })}><Trash2 size={12} /></button>
          </li>
        ))}
      </ul>
    </>
  );
}

function BookmarksView({ bridge, onOpenUrl }) {
  const [query, setQuery] = useState("");
  const { items, error } = useLibrary(bridge, "bookmarks", query, BOOKMARK_KINDS);
  return (
    <>
      <SearchBox label="Search bookmarks" value={query} onChange={setQuery} />
      {error ? <p role="alert" className="text-red-700">{error}</p> : null}
      {items && !items.length ? <p className="p-2 text-muted">{query ? "No bookmarks match." : "Bookmark a page with the star in the address bar or Ctrl+D."}</p> : null}
      <ul className="space-y-0.5">
        {(items || []).map((item) => <BookmarkRow key={item.id} bridge={bridge} item={item} onOpenUrl={onOpenUrl} />)}
      </ul>
    </>
  );
}

// Editing keeps a local draft and commits on blur or Enter.
function BookmarkRow({ bridge, item, onOpenUrl }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.title);
  const commit = () => {
    setEditing(false);
    if (draft.trim() !== item.title) void bridge.library("bookmark-update", { id: item.id, title: draft });
  };
  return (
    <li className="group flex items-center gap-2 rounded px-1.5 py-1 hover:bg-slate-50">
      {editing ? (
        <input
          aria-label="Bookmark name"
          autoFocus
          className="h-6 min-w-0 flex-1 rounded border border-line px-1.5"
          value={draft}
          onBlur={commit}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") { event.preventDefault(); commit(); }
            if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setDraft(item.title); setEditing(false); }
          }}
        />
      ) : (
        <button className="min-w-0 flex-1 text-left" type="button" onClick={() => onOpenUrl(item.url)}>
          <span className="block truncate text-ink">{item.title}</span>
          <span className="block truncate text-[10px] text-muted">{hostLabel(item.url)}</span>
        </button>
      )}
      <button aria-label={`Rename ${item.title}`} className="rounded px-1.5 py-0.5 text-[10px] text-muted opacity-0 hover:bg-slate-100 focus:opacity-100 group-hover:opacity-100" type="button" onClick={() => { setDraft(item.title); setEditing(true); }}>Rename</button>
      <button aria-label={`Delete bookmark ${item.title}`} className="rounded p-1 text-muted opacity-0 hover:bg-slate-100 focus:opacity-100 group-hover:opacity-100" type="button" onClick={() => void bridge.library("bookmark-remove", { id: item.id })}><Trash2 size={12} /></button>
    </li>
  );
}

export function DownloadsView({ bridge }) {
  const [downloads, setDownloads] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    bridge?.library?.("downloads").then((result) => { if (active) setDownloads(result?.downloads || []); }, (failure) => setError(failure.message));
    const unsubscribe = bridge?.onDownloads?.((payload) => setDownloads(payload?.downloads || []));
    return () => { active = false; unsubscribe?.(); };
  }, [bridge]);
  const act = (command, id) => bridge.library("download", { command, id }).then((result) => {
    setDownloads(result?.downloads || []);
    setError("");
  }, (failure) => setError(failure.message));
  return (
    <>
      <div className="mb-2 flex justify-end"><button className={BUTTON} type="button" disabled={!downloads?.length} onClick={() => void act("clear", "")}>Clear list</button></div>
      {error ? <p role="alert" className="mb-2 text-red-700">{error}</p> : null}
      {downloads && !downloads.length ? <p className="p-2 text-muted">Files you download appear here. They are saved to your Downloads folder.</p> : null}
      <ul className="space-y-1.5">
        {(downloads || []).map((item) => {
          const status = downloadStatus(item);
          return (
            <li key={item.id} className={`rounded border px-2 py-1.5 ${item.state === "quarantined" ? "border-amber-300 bg-amber-50" : "border-line"}`}>
              <div className="flex items-center gap-2">
                {item.state === "quarantined" ? <TriangleAlert aria-hidden="true" className="shrink-0 text-amber-800" size={12} /> : <Download aria-hidden="true" className="shrink-0 text-muted" size={12} />}
                <span className="min-w-0 flex-1 truncate font-medium">{item.filename}</span>
                {item.byRem ? <span className="shrink-0 rounded bg-slate-100 px-1 text-[10px] text-muted">by Rem</span> : null}
              </div>
              <p className="mt-0.5 truncate text-[10px] text-muted">{status.label} · {hostLabel(item.url)}</p>
              {item.state === "progressing" && status.percent !== null ? (
                <div className="mt-1 h-1 overflow-hidden rounded bg-slate-200" role="progressbar" aria-label={`${item.filename} progress`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={status.percent}>
                  <div className="h-full bg-brand" style={{ width: `${status.percent}%` }} />
                </div>
              ) : null}
              <div className="mt-1 flex flex-wrap gap-1.5">
                {item.state === "progressing" ? (
                  <>
                    <button className={BUTTON} type="button" onClick={() => void act(item.paused ? "resume" : "pause", item.id)}>{item.paused ? "Resume" : "Pause"}</button>
                    <button className={BUTTON} type="button" onClick={() => void act("cancel", item.id)}>Cancel</button>
                  </>
                ) : null}
                {item.state === "completed" ? (
                  <>
                    <button className={BUTTON} type="button" onClick={() => void act("open", item.id)}>Open</button>
                    <button className={BUTTON} type="button" onClick={() => void act("show", item.id)}><FolderOpen aria-hidden="true" className="mr-1 inline" size={11} />Show in folder</button>
                  </>
                ) : null}
                {item.state === "quarantined" ? (
                  <>
                    <button className={BUTTON} type="button" onClick={() => void act("discard", item.id)}>Discard</button>
                    <button className={BUTTON} type="button" onClick={() => void act("keep", item.id)}>Keep anyway</button>
                  </>
                ) : null}
                {item.state === "interrupted" || item.state === "cancelled" ? <button className={BUTTON} type="button" onClick={() => void act("retry", item.id)}>Retry</button> : null}
                {item.state !== "progressing" ? <button aria-label={`Remove ${item.filename} from list`} className="rounded p-1 text-muted hover:bg-slate-100" type="button" onClick={() => void act("remove", item.id)}><X size={11} /></button> : null}
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}

function SiteDataView({ bridge }) {
  const [profile, setProfile] = useState("user");
  const [sites, setSites] = useState(null);
  const [error, setError] = useState("");
  const [clearing, setClearing] = useState(false);
  useEffect(() => {
    let active = true;
    bridge?.library?.("sites", { profile }).then((result) => { if (active) setSites(result?.sites || []); }, (failure) => setError(failure.message));
    return () => { active = false; };
  }, [bridge, profile]);
  return (
    <>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1">Profile
          <select aria-label="Browser profile" className="h-6 rounded border border-line bg-white px-1" value={profile} onChange={(event) => { setSites(null); setProfile(event.target.value); }}>
            <option value="user">Your browsing</option>
            <option value="rem">Rem&apos;s browsing</option>
          </select>
        </label>
        <button className={`${BUTTON} ml-auto`} type="button" onClick={() => setClearing((value) => !value)}>Clear browsing data…</button>
      </div>
      {clearing ? <ClearDataForm bridge={bridge} profile={profile} onDone={() => { setClearing(false); setSites(null); bridge.library("sites", { profile }).then((result) => setSites(result?.sites || [])); }} /> : null}
      {error ? <p role="alert" className="text-red-700">{error}</p> : null}
      {sites && !sites.length ? <p className="p-2 text-muted">No sites have stored data.</p> : null}
      <ul className="space-y-0.5">
        {(sites || []).map((site) => (
          <li key={site.host} className="flex items-center gap-2 rounded px-1.5 py-1 hover:bg-slate-50">
            <span className="min-w-0 flex-1">
              <span className="block truncate">{site.host}</span>
              <span className="block truncate text-[10px] text-muted">
                {[site.cookies ? `${site.cookies} cookie${site.cookies === 1 ? "" : "s"}` : "", site.permissions && Object.keys(site.permissions).length ? "permissions" : "", site.zoom ? `zoom ${formatZoom(site.zoom)}` : "", site.popups === "allow" ? "pop-ups allowed" : ""].filter(Boolean).join(" · ")}
              </span>
            </span>
            <button aria-label={`Clear data for ${site.host}`} className={BUTTON} type="button" onClick={() => bridge.library("site-clear", { host: site.host, origin: site.origin, profile }).then((result) => setSites(result?.sites || []), (failure) => setError(failure.message))}>Clear</button>
          </li>
        ))}
      </ul>
    </>
  );
}

function ClearDataForm({ bridge, profile = "user", onDone }) {
  const [range, setRange] = useState("hour");
  const [choices, setChoices] = useState({ history: true, cookies: false, cache: true, downloads: false, siteSettings: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const toggle = (key) => setChoices((current) => ({ ...current, [key]: !current[key] }));
  const options = [
    ...(profile === "user" ? [["history", "Browsing history"]] : []),
    ["cookies", "Cookies and site data (signs you out of most sites; always all time)"],
    ["cache", "Cached images and files"],
    ["downloads", "Downloads list (files stay on disk)"],
    ...(profile === "user" ? [["siteSettings", "Site settings (permissions, zoom, pop-ups)"]] : []),
  ];
  return (
    <form className="mb-3 rounded border border-line bg-slate-50 p-2" aria-label="Clear browsing data" onSubmit={(event) => {
      event.preventDefault();
      setBusy(true);
      bridge.library("clear-data", { ...choices, range, profile }).then(() => { setBusy(false); onDone(); }, (failure) => { setBusy(false); setError(failure.message); });
    }}>
      {profile === "user" ? (
        <label className="mb-2 flex items-center gap-2">History time range
          <select aria-label="Time range" className="h-6 rounded border border-line bg-white px-1" value={range} onChange={(event) => setRange(event.target.value)}>
            <option value="hour">Last hour</option>
            <option value="day">Last 24 hours</option>
            <option value="week">Last 7 days</option>
            <option value="month">Last 4 weeks</option>
            <option value="all">All time</option>
          </select>
        </label>
      ) : null}
      {options.map(([key, label]) => (
        <label key={key} className="flex items-center gap-2 py-0.5">
          <input checked={choices[key]} type="checkbox" onChange={() => toggle(key)} />{label}
        </label>
      ))}
      {error ? <p role="alert" className="text-red-700">{error}</p> : null}
      <div className="mt-2 flex gap-2">
        <button className={PRIMARY} disabled={busy} type="submit">{busy ? "Clearing…" : "Clear data"}</button>
        <button className={BUTTON} type="button" onClick={onDone}>Cancel</button>
      </div>
    </form>
  );
}
