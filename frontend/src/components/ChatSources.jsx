import { useEffect, useState } from "react";
import { ChevronDown, Globe, Play } from "lucide-react";
import { apiUrl } from "../lib/api.js";
import { sourceDomain, videoLinks } from "../lib/chatSources.js";

const COLLAPSED = 4;
const previews = new Map();
const waiting = [];
let active = 0;

// The backend fetches previews so the chat never loads arbitrary pages itself.
// A few run at once; each URL is asked for once per session.
export function loadLinkPreview(url, fetchImpl = fetch) {
  if (!previews.has(url)) {
    previews.set(url, new Promise(resolve => { waiting.push({ url, resolve, fetchImpl }); pump(); }));
  }
  return previews.get(url);
}

function pump() {
  while (active < 4 && waiting.length) {
    const { url, resolve, fetchImpl } = waiting.shift();
    active += 1;
    fetchImpl(apiUrl(`/api/link-preview?url=${encodeURIComponent(url)}`))
      .then(response => response.ok ? response.json() : null)
      .then(payload => resolve(payload?.preview ?? null), () => resolve(null))
      .finally(() => { active -= 1; pump(); });
  }
}

function usePreview(url) {
  const [preview, setPreview] = useState(null);
  useEffect(() => {
    let current = true;
    void loadLinkPreview(url).then(value => { if (current) setPreview(value); });
    return () => { current = false; };
  }, [url]);
  return preview;
}

function openLink(event, url) {
  event.preventDefault();
  // Raticode opens web links in its own browser tab.
  window.open(url, "_blank", "noopener,noreferrer");
}

export default function ChatSources({ body, sources = [] }) {
  const [expanded, setExpanded] = useState(false);
  const videos = videoLinks(body, sources);
  const pages = sources.filter(source => !videos.some(video => sameVideo(video, source.url)));
  const shown = expanded ? pages : pages.slice(0, COLLAPSED);
  if (!videos.length && !pages.length) return null;
  return (
    <div className="@container mt-2 space-y-2 border-t border-line/70 pt-2">
      {videos.length ? (
        <ul aria-label="Videos" className="grid grid-cols-1 gap-2 @min-[400px]:grid-cols-2">
          {videos.map(url => <li key={url}><VideoCard url={url} /></li>)}
        </ul>
      ) : null}
      {pages.length ? (
        <section aria-label={`${pages.length} ${pages.length === 1 ? "source" : "sources"}`}>
          <h4 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-muted">
            <Globe aria-hidden="true" size={12} />
            {pages.length} {pages.length === 1 ? "source" : "sources"}
          </h4>
          <ul className="grid grid-cols-1 gap-1.5 @min-[400px]:grid-cols-2">
            {shown.map(source => <li key={source.url} className="min-w-0"><SourceCard source={source} /></li>)}
          </ul>
          {pages.length > COLLAPSED ? (
            <button
              aria-expanded={expanded}
              className="mt-1.5 inline-flex items-center gap-1 rounded px-1 text-[11px] text-muted outline-none transition hover:text-ink focus-visible:ring-2 focus-visible:ring-brand/30"
              type="button"
              onClick={() => setExpanded(current => !current)}
            >
              {expanded ? "Show fewer sources" : `Show ${pages.length - COLLAPSED} more`}
              <ChevronDown aria-hidden="true" className={`transition-transform ${expanded ? "rotate-180" : ""}`} size={12} />
            </button>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function SourceCard({ source }) {
  const preview = usePreview(source.url);
  const domain = sourceDomain(preview?.url || source.url);
  const title = preview?.title || source.title || domain;
  return (
    <a
      className="group flex h-full min-w-0 items-center gap-2 rounded-lg border border-line bg-white p-1.5 text-left no-underline outline-none transition hover:border-brand/50 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-brand/30 dark:bg-[#181818] dark:hover:bg-[#202022]"
      href={source.url}
      rel="noreferrer"
      target="_blank"
      title={preview?.description || source.url}
      onClick={event => openLink(event, source.url)}
    >
      <span className="grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-md bg-slate-100 text-[11px] font-semibold uppercase text-muted dark:bg-[#26262a]">
        {preview?.image
          ? <img alt="" className="h-full w-full object-cover" src={preview.image} />
          : domain.slice(0, 1)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="line-clamp-2 text-[11px] font-medium leading-4 text-ink group-hover:text-brand">{title}</span>
        <span className="block truncate text-[10px] leading-4 text-muted">
          {preview?.siteName && preview.siteName.toLowerCase() !== domain ? `${preview.siteName} · ${domain}` : domain}
        </span>
      </span>
    </a>
  );
}

function VideoCard({ url }) {
  const preview = usePreview(url);
  return (
    <a
      className="group block overflow-hidden rounded-lg border border-line bg-white no-underline outline-none transition hover:border-brand/50 focus-visible:ring-2 focus-visible:ring-brand/30 dark:bg-[#181818]"
      href={url}
      rel="noreferrer"
      target="_blank"
      onClick={event => openLink(event, url)}
    >
      <span className="relative block aspect-video bg-slate-900">
        {preview?.image ? <img alt="" className="h-full w-full object-cover" src={preview.image} /> : null}
        <span aria-hidden="true" className="absolute inset-0 grid place-items-center">
          <span className="grid h-9 w-9 place-items-center rounded-full bg-black/65 text-white transition group-hover:bg-red-600">
            <Play fill="currentColor" size={16} />
          </span>
        </span>
      </span>
      <span className="block px-2 py-1.5">
        <span className="line-clamp-2 text-[11px] font-medium leading-4 text-ink group-hover:text-brand">
          {preview?.title || "YouTube video"}
        </span>
        <span className="block truncate text-[10px] leading-4 text-muted">
          {preview?.description ? `${preview.description} · YouTube` : "YouTube"}
        </span>
      </span>
    </a>
  );
}

function sameVideo(videoUrl, url) {
  const id = new URL(videoUrl).searchParams.get("v");
  return Boolean(id) && String(url).includes(id);
}
