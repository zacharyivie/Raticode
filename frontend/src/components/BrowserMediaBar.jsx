import { useEffect, useState } from "react";
import { Music2, Pause, Play, SkipBack, SkipForward } from "lucide-react";
import { mediaForBar } from "../lib/browserMedia.js";

const FAILURE_MS = 4000;
const GECKO_MEDIA_ID = "gecko";

function Artwork({ url }) {
  const [broken, setBroken] = useState(false);
  if (!url || broken) return <Music2 aria-hidden="true" className="shrink-0 text-muted" size={14} />;
  return <img alt="" className="h-5 w-5 shrink-0 rounded-sm object-cover" decoding="async" draggable={false} referrerPolicy="no-referrer" src={url} onError={() => setBroken(true)} />;
}

function Control({ disabled, icon: Icon, label, onClick, reason }) {
  return (
    <button
      aria-label={label}
      className="flex h-[22px] w-[22px] items-center justify-center rounded text-ink hover:bg-canvas disabled:cursor-not-allowed disabled:text-muted disabled:opacity-50 disabled:hover:bg-transparent"
      disabled={disabled}
      title={disabled ? reason : label}
      type="button"
      // Pressing a control leaves the keyboard wherever the user was typing.
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
    ><Icon aria-hidden="true" size={13} /></button>
  );
}

// The strip above the main content while a browser tab plays. The main process
// decides what counts as playing; this shows only what it reports. viewedTabs
// are the editor tabs on screen: their media is already in view, so the bar
// speaks for another tab or stays away.
export default function BrowserMediaBar({ viewedTabs = [] }) {
  const bridge = window.goferBrowser;
  const [sessions, setSessions] = useState([]);
  const [geckoMedia, setGeckoMedia] = useState(null);
  const [failure, setFailure] = useState("");

  useEffect(() => {
    if (!bridge?.onMedia) return undefined;
    let current = true;
    let heard = false;
    const unsubscribe = bridge.onMedia((payload) => {
      heard = true;
      setSessions(Array.isArray(payload?.sessions) ? payload.sessions : []);
    });
    void bridge.mediaSessions?.().then((payload) => {
      if (current && !heard) setSessions(Array.isArray(payload?.sessions) ? payload.sessions : []);
    }).catch(() => {});
    return () => {
      current = false;
      unsubscribe?.();
    };
  }, [bridge]);

  // Gecko is a real Firefox window, followed separately in the main process.
  useEffect(() => {
    if (!bridge?.onGecko) return undefined;
    let current = true;
    let heard = false;
    const unsubscribe = bridge.onGecko((status) => {
      heard = true;
      setGeckoMedia(status?.media || null);
    });
    void bridge.gecko?.("status").then((status) => {
      if (current && !heard) setGeckoMedia(status?.media || null);
    }).catch(() => {});
    return () => {
      current = false;
      unsubscribe?.();
    };
  }, [bridge]);

  useEffect(() => {
    if (!failure) return undefined;
    const timer = window.setTimeout(() => setFailure(""), FAILURE_MS);
    return () => window.clearTimeout(timer);
  }, [failure]);

  const media = mediaForBar(sessions, geckoMedia, viewedTabs);
  if (!media) return null;

  function send(command) {
    setFailure("");
    Promise.resolve(media.id === GECKO_MEDIA_ID ? bridge.gecko("media-control", { command }) : bridge.mediaControl(media.id, command))
      .then((result) => { if (result?.handled === false) setFailure("The page didn't respond"); })
      .catch(() => setFailure("The page didn't respond"));
  }

  const detail = media.artist || media.site;
  return (
    <section aria-label="Playing in a browser tab" className="flex h-7 shrink-0 items-center gap-2 border-b border-line bg-surface px-2 text-xs text-ink" data-testid="browser-media-bar">
      <Artwork key={media.artwork} url={media.artwork} />
      <div className="min-w-0 flex-1 truncate" title={[media.title, detail].filter(Boolean).join(" · ")}>
        <span className="font-medium">{media.title}</span>
        {detail && detail !== media.title ? <span className="text-muted"> · {detail}</span> : null}
        {failure ? <span className="text-muted" role="status"> · {failure}</span> : null}
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        <Control disabled={!media.canPrevious} icon={SkipBack} label="Previous" reason="This page has no previous track" onClick={() => send("previous")} />
        <Control disabled={!media.canPlayPause} icon={media.playing ? Pause : Play} label={media.playing ? "Pause" : "Play"} reason="This page's player can't be controlled from here" onClick={() => send("play-pause")} />
        <Control disabled={!media.canNext} icon={SkipForward} label="Next" reason="This page has no next track" onClick={() => send("next")} />
      </div>
    </section>
  );
}
