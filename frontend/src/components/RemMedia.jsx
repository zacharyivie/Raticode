// Files Rem attached to a reply. Pictures and videos show inline and open large
// when clicked; every file's name still opens it in the editor. Rem checked these
// files when it wrote the reply (rem_home.reply_files), and the desktop reads them
// through the same media preview the editor uses.
import { useEffect, useState } from "react";
import { FileText, ImageOff, Play } from "lucide-react";
import MediaViewer from "./MediaViewer.jsx";
import { formatAttachmentBytes } from "../lib/chatAttachments.js";
import { mediaKind } from "../lib/mediaKind.js";

export function replyMediaKind(file) {
  const kind = mediaKind(file?.name || file?.path);
  return kind === "image" || kind === "video" ? kind : "";
}

export function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  const total = Math.round(seconds);
  const [h, m, s] = [Math.floor(total / 3600), Math.floor((total % 3600) / 60), total % 60];
  const pad = value => String(value).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

// The desktop's media preview URL for one file, released when the preview leaves the chat.
function useMediaPreview(path) {
  const [state, setState] = useState({ url: "", error: "" });
  useEffect(() => {
    const bridge = typeof window === "undefined" ? null : window.goferDesktop?.textFiles;
    let disposed = false;
    let previewId = "";
    const release = id => { if (id) void Promise.resolve(bridge?.closePreview?.(id)).catch(() => {}); };
    setState({ url: "", error: "" });
    if (!bridge?.openPreview) {
      setState({ url: "", error: "unavailable" });
      return undefined;
    }
    Promise.resolve(bridge.openPreview(path)).then(payload => {
      previewId = payload?.id || "";
      if (disposed) { release(previewId); return; }
      setState(payload?.url ? { url: payload.url, error: "" } : { url: "", error: "unavailable" });
    }, () => { if (!disposed) setState({ url: "", error: "missing" }); });
    return () => { disposed = true; release(previewId); };
  }, [path]);
  return state;
}

export function ReplyFiles({ files, onOpenLink }) {
  const media = files.filter(file => replyMediaKind(file));
  const others = files.filter(file => !replyMediaKind(file));
  return <>
    {media.length ? <div className="rem-media-list" aria-label="Pictures and videos">
      {media.map(file => <ReplyMedia key={file.path} file={file} onOpenLink={onOpenLink} />)}
    </div> : null}
    {others.length ? <div className="rem-files" aria-label="Attached files">
      {others.map(file => <FileButton key={file.path} file={file} onOpenLink={onOpenLink} />)}
    </div> : null}
  </>;
}

function FileButton({ file, onOpenLink }) {
  return <button type="button" onClick={() => onOpenLink?.(file.path)} title={file.path}>
    <FileText size={14} aria-hidden="true" />
    <span>{file.name}</span>
    <small>{formatAttachmentBytes(file.size)}</small>
  </button>;
}

function ReplyMedia({ file, onOpenLink }) {
  const kind = replyMediaKind(file);
  const preview = useMediaPreview(file.path);
  const [broken, setBroken] = useState(false);
  const [duration, setDuration] = useState(0);
  const [open, setOpen] = useState(false);
  useEffect(() => { setBroken(false); setDuration(0); }, [preview.url]);
  const noun = kind === "video" ? "video" : "picture";
  const problem = preview.error === "missing" ? `This ${noun} is no longer on this computer.`
    : preview.error || broken ? `This ${noun} can't be previewed here. Open it from its name below.` : "";
  return <figure className="rem-media" data-kind={kind}>
    {problem ? <div className="rem-media-note" role="note"><ImageOff size={16} aria-hidden="true" /><span>{problem}</span></div>
      : !preview.url ? <span className="rem-media-loading" role="img" aria-label={`Loading ${file.name}`} />
        : <button type="button" className="rem-media-frame" aria-label={kind === "video" ? `Play ${file.name}` : `View ${file.name} larger`} onClick={() => setOpen(true)}>
          {kind === "video" ? <>
            {/* The first frame stands in as the poster; the player opens on click. */}
            <video aria-hidden="true" muted playsInline preload="metadata" tabIndex={-1} src={`${preview.url}#t=0.1`}
              onError={() => setBroken(true)} onLoadedMetadata={event => setDuration(event.currentTarget.duration)} />
            <span className="rem-media-play" aria-hidden="true"><Play size={18} fill="currentColor" /></span>
            {formatDuration(duration) ? <span className="rem-media-duration" aria-hidden="true">{formatDuration(duration)}</span> : null}
          </> : <img alt="" src={preview.url} onError={() => setBroken(true)} />}
        </button>}
    <figcaption><FileButton file={file} onOpenLink={onOpenLink} /></figcaption>
    {open ? <MediaViewer kind={kind} url={preview.url} name={file.name} onClose={() => setOpen(false)}
      actionLabel="Open in editor" onAction={() => { setOpen(false); onOpenLink?.(file.path); }} /> : null}
  </figure>;
}
