// Files attached as context, in the composer and on sent messages: a thumbnail for
// pictures and videos, a file-type tile for everything else, and a preview on click.
// Thumbnails come from the picked file itself before sending, and afterwards from the
// stored upload through the desktop's media preview, the path the editor uses.
import { lazy, Suspense, useEffect, useState } from "react";
import { File, FileArchive, FileAudio, FileCode, FileImage, FileSpreadsheet, FileText, FileVideo, Play, Presentation, X } from "lucide-react";
import { apiUrl } from "../lib/api.js";
import { formatAttachmentBytes } from "../lib/chatAttachments.js";
import { fileKind } from "../lib/fileKinds.js";

const AttachmentPreview = lazy(() => import("./AttachmentPreview.jsx"));

const ICONS = { image: FileImage, video: FileVideo, audio: FileAudio, pdf: FileText, sheet: FileSpreadsheet, code: FileCode, text: FileText, archive: FileArchive, document: Presentation, file: File };
const TINTS = {
  image: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200",
  video: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-200",
  audio: "bg-pink-100 text-pink-800 dark:bg-pink-950 dark:text-pink-200",
  pdf: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200",
  sheet: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  code: "bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-200",
  text: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200",
  archive: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  document: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-200",
  file: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200",
};

// Where an attachment's bytes are, and a URL to show it when `media` is set. status is
// loading, ready, missing (the stored file is gone) or unavailable (no desktop bridge).
export function useAttachmentSource(attachment, media) {
  const file = typeof Blob !== "undefined" && attachment?.file instanceof Blob ? attachment.file : null;
  const known = attachment?.path || "";
  const storage = attachment?.storageName || "";
  const [state, setState] = useState({ url: "", path: known, status: "loading" });
  useEffect(() => {
    let disposed = false;
    let previewId = "";
    let objectUrl = "";
    const bridge = typeof window === "undefined" ? null : window.goferDesktop?.textFiles;
    const settle = next => { if (!disposed) setState(next); };
    setState({ url: "", path: known, status: "loading" });
    (async () => {
      if (file) {
        if (media) objectUrl = URL.createObjectURL(file);
        return settle({ url: objectUrl, path: "", status: "ready" });
      }
      let path = known;
      if (!path && storage) {
        const response = await fetch(apiUrl(`/api/chat/attachments/locate?storageName=${encodeURIComponent(storage)}`));
        if (!response.ok) return settle({ url: "", path: "", status: response.status === 404 ? "missing" : "unavailable" });
        path = String((await response.json()).path || "");
      }
      if (!path) return settle({ url: "", path: "", status: "unavailable" });
      if (!media) return settle({ url: "", path, status: "ready" });
      if (!bridge?.openPreview) return settle({ url: "", path, status: "unavailable" });
      const payload = await bridge.openPreview(path);
      previewId = payload?.id || "";
      if (disposed) return;
      settle(payload?.url ? { url: payload.url, path, status: "ready" } : { url: "", path, status: "unavailable" });
    })().catch(() => settle({ url: "", path: known, status: known || storage ? "missing" : "unavailable" }));
    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      if (previewId) void Promise.resolve(bridge?.closePreview?.(previewId)).catch(() => {});
    };
  }, [file, known, storage, media]);
  return state;
}

export function KindBadge({ kind, label, large = false }) {
  const Icon = ICONS[kind] || File;
  return <span aria-hidden="true" className={`grid h-full w-full place-items-center rounded-[inherit] ${TINTS[kind] || TINTS.file}`}>
    <span className="flex flex-col items-center gap-0.5">
      <Icon size={large ? 26 : 16} />
      <span className={`font-semibold tracking-wide ${large ? "text-[11px]" : "text-[8px]"}`}>{label}</span>
    </span>
  </span>;
}

// One attachment. `variant` is composer (a compact chip with a small thumbnail and a
// remove button) or message (pictures and videos get a larger thumbnail).
export function AttachmentTile({ attachment, variant = "message", inverse = false, onRemove, onOpenLink }) {
  const { kind, label } = fileKind(attachment.name, attachment.type);
  const visual = kind === "image" || kind === "video";
  const source = useAttachmentSource(attachment, visual);
  const [broken, setBroken] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => setBroken(false), [source.url]);
  const media = visual && source.url && !broken;
  const large = variant === "message" && media;
  const size = formatAttachmentBytes(attachment.size);
  const missing = source.status === "missing";
  const thumb = media
    ? kind === "image"
      ? <img alt="" className="h-full w-full object-cover" src={source.url} onError={() => setBroken(true)} />
      : <>
        <video aria-hidden="true" className="pointer-events-none h-full w-full object-cover" muted playsInline preload="metadata" tabIndex={-1} src={`${source.url}#t=0.1`} onError={() => setBroken(true)} />
        <span aria-hidden="true" className="absolute inset-0 grid place-items-center"><span className="grid h-7 w-7 place-items-center rounded-full bg-black/60 text-white"><Play fill="currentColor" size={12} /></span></span>
      </>
    : <KindBadge kind={kind} label={label} large={large} />;
  const tone = inverse ? "border-white/25 bg-white/10 text-white" : "border-line bg-slate-50 text-muted dark:bg-[#1f1f22]";
  return (
    <span className={`relative inline-flex min-w-0 max-w-full rounded-lg border ${tone}`} data-attachment-kind={kind} data-attachment-missing={missing || undefined}>
      <button
        type="button"
        aria-label={`Preview ${attachment.name}`}
        title={`${attachment.name} · ${size}${missing ? " · no longer available" : ""}`}
        className={`flex min-w-0 max-w-full rounded-[inherit] text-left outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/60 ${large ? "flex-col" : "items-center gap-2 p-1 pr-2"}`}
        onClick={() => setOpen(true)}
      >
        <span className={`relative shrink-0 overflow-hidden ${large ? "h-28 w-40 rounded-t-[7px] bg-black/5" : "h-10 w-10 rounded-md"}`}>{thumb}</span>
        <span className={`flex min-w-0 flex-col ${large ? "w-40 px-2 py-1" : ""}`}>
          <span className={`attachment-name truncate text-[11px] ${inverse ? "text-white" : "text-ink"} ${large ? "" : "max-w-40"}`}>{attachment.name}</span>
          <span className={`attachment-meta text-[10px] ${inverse ? "text-white/70" : "text-muted"}`}>{missing ? "No longer available" : `${label} · ${size}`}</span>
        </span>
      </button>
      {onRemove ? (
        <button
          aria-label={`Remove ${attachment.name}`}
          className="mr-1 grid h-5 w-5 shrink-0 place-items-center self-center rounded text-muted transition hover:bg-slate-200 hover:text-ink dark:hover:bg-white/10"
          title={`Remove ${attachment.name}`}
          type="button"
          onClick={onRemove}
        >
          <X aria-hidden="true" size={11} />
        </button>
      ) : null}
      {open ? <Suspense fallback={null}><AttachmentPreview attachment={attachment} onClose={() => setOpen(false)} onOpenLink={onOpenLink} /></Suspense> : null}
    </span>
  );
}
