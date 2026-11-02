// A web image in chat, fetched by the desktop backend so the site never sees this
// window's request. Loaded on demand by MarkdownContent.
import { useEffect, useState } from "react";
import { Image as ImageIcon } from "lucide-react";
import { apiUrl } from "../lib/api.js";
import MediaViewer from "./MediaViewer.jsx";

const webImages = new Map();

function loadWebImage(src) {
  if (!webImages.has(src)) {
    if (webImages.size > 120) webImages.delete(webImages.keys().next().value);
    webImages.set(src, fetch(apiUrl(`/api/media-image?url=${encodeURIComponent(src)}`))
      .then(response => (response.ok ? response.json() : null))
      .then(payload => (payload?.image ? `data:${payload.image.mime};base64,${payload.image.data}` : null), () => null));
  }
  return webImages.get(src);
}

// While it loads a quiet placeholder keeps the space. A click opens it large, with
// a button for the browser tab; if it can't load, a link opens the tab instead.
export default function WebImage({ alt, src }) {
  const [image, setImage] = useState(undefined);
  const [viewing, setViewing] = useState(false);
  useEffect(() => {
    let current = true;
    void loadWebImage(src).then(value => { if (current) setImage(value); });
    return () => { current = false; };
  }, [src]);
  const open = () => window.open(src, "_blank", "noopener,noreferrer");
  if (image) {
    return <>
      <button type="button" aria-label={`View ${alt || "image"} larger`} className="block max-w-full cursor-zoom-in rounded-lg p-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand" onClick={() => setViewing(true)}>
        <img alt={alt} className="max-h-[32rem] max-w-full rounded-lg object-contain" src={image} title={alt || undefined} />
      </button>
      {viewing ? <MediaViewer kind="image" url={image} name={alt || "Image"} onClose={() => setViewing(false)} actionLabel="Open in browser" onAction={() => { setViewing(false); open(); }} /> : null}
    </>;
  }
  let host = "";
  try { host = new URL(src).hostname; } catch { /* shown without a host */ }
  if (image === undefined) {
    return <span role="img" aria-label={`Loading image${alt ? `: ${alt}` : ""}`} className="block h-40 w-full max-w-md animate-pulse rounded-lg bg-slate-100 motion-reduce:animate-none dark:bg-[#26262a]" />;
  }
  return (
    <button type="button" className="inline-flex max-w-full items-center gap-2 rounded-lg border border-line px-3 py-2 text-left text-xs text-muted hover:text-ink" onClick={open}>
      <ImageIcon aria-hidden="true" size={14} />
      <span className="truncate">{alt || "Image"} · open {host || "image"} in a browser tab</span>
    </button>
  );
}
