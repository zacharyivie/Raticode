// The preview a clicked attachment opens: pictures, video and sound play in the
// viewer, text and code show as text, and other files get a card that opens them in
// the editor or their usual app. Loaded on first click, outside the main bundle.
import { useEffect, useState } from "react";
import MediaViewer from "./MediaViewer.jsx";
import { KindBadge, useAttachmentSource } from "./AttachmentTiles.jsx";
import { formatAttachmentBytes } from "../lib/chatAttachments.js";
import { fileKind, previewMode, previewText, TEXT_PREVIEW_BYTES } from "../lib/fileKinds.js";

async function attachmentBytes(attachment, path) {
  if (typeof Blob !== "undefined" && attachment.file instanceof Blob) {
    return new Uint8Array(await attachment.file.slice(0, TEXT_PREVIEW_BYTES + 1).arrayBuffer());
  }
  const read = window.goferDesktop?.textFiles?.readAttachment;
  if (!read) throw new Error("unavailable");
  const binary = atob((await read(path)).data || "");
  const bytes = new Uint8Array(Math.min(binary.length, TEXT_PREVIEW_BYTES + 1));
  for (let index = 0; index < bytes.length; index += 1) bytes[index] = binary.charCodeAt(index);
  // Only the start is kept; the rest is never decoded.
  return binary.length > bytes.length ? Object.assign(bytes, { more: true }) : bytes;
}

export default function AttachmentPreview({ attachment, onClose, onOpenLink }) {
  const mode = previewMode(attachment.name, attachment.type);
  const media = mode === "image" || mode === "video" || mode === "audio";
  const source = useAttachmentSource(attachment, media);
  const [text, setText] = useState(undefined);
  useEffect(() => {
    if (mode !== "text" || source.status !== "ready") return undefined;
    let current = true;
    attachmentBytes(attachment, source.path).then(bytes => {
      const shown = previewText(bytes);
      if (current) setText(shown ? { ...shown, truncated: shown.truncated || Boolean(bytes.more) } : null);
    }, () => { if (current) setText({ error: true }); });
    return () => { current = false; };
  }, [attachment, mode, source.path, source.status]);

  const actions = [];
  if (source.path && onOpenLink) actions.push({ label: "Open in editor", onClick: () => { onClose(); onOpenLink(source.path); } });
  const openPath = window.goferDesktop?.workspace?.openPath;
  if (source.path && openPath) actions.push({ label: "Open in its app", onClick: () => { onClose(); void Promise.resolve(openPath(source.path)).catch(() => {}); } });

  const { kind, label } = fileKind(attachment.name, attachment.type);
  const card = message => (
    <div className="flex max-w-md flex-col items-center gap-3 text-center" role="note">
      <span className="h-24 w-24 overflow-hidden rounded-2xl"><KindBadge kind={kind} label={label} large /></span>
      <p className="text-sm text-white">{attachment.name}</p>
      <p className="text-xs text-white/70">{label} · {formatAttachmentBytes(attachment.size)}</p>
      <p className="text-sm text-white/85">{message}</p>
    </div>
  );
  let content;
  if (source.status === "missing") content = card("This file is no longer available. It may have been removed along with its thread.");
  else if (source.status === "unavailable") content = card("Previews need the Raticode desktop app.");
  else if (source.status === "loading" || (mode === "text" && text === undefined)) content = <p role="status" className="text-sm text-white/80">Opening {attachment.name}…</p>;
  else if (mode === "text") {
    content = text === null ? card("This file doesn't look like text, so it can't be shown here.")
      : text.error ? card("This file couldn't be read.")
        : <div className="flex max-h-full w-full max-w-5xl flex-col gap-2">
          <pre className="min-h-0 overflow-auto whitespace-pre-wrap break-words rounded-md bg-[#141416] p-4 text-left font-mono text-xs leading-5 text-slate-100" tabIndex={0} aria-label={`Contents of ${attachment.name}`}>{text.text}</pre>
          {text.truncated ? <p className="text-xs text-white/70">Showing the first {formatAttachmentBytes(TEXT_PREVIEW_BYTES)}. Open it to see the rest.</p> : null}
        </div>;
  } else if (!media) {
    content = card(mode === "other" && kind === "pdf" ? "PDFs open in the editor's preview." : "Raticode can't show this kind of file here. Open it to see it.");
  }
  return <MediaViewer kind={media ? mode : mode === "text" ? "text" : "file"} url={source.url} name={attachment.name} onClose={onClose} actions={actions}>
    {media && source.status === "ready" ? undefined : content}
  </MediaViewer>;
}
