// A large picture, a video or sound player, or other content (`children`) over the
// app, scaled to fill the window as on the phone. Escape, Close or a click on the
// margin returns to the chat.
import { useState } from "react";
import { ExternalLink, X } from "lucide-react";
import { Dialog } from "./Dialog.jsx";

const TITLES = { image: "Picture", video: "Video", audio: "Sound" };

export default function MediaViewer({ kind, url, name, onClose, actionLabel = "", onAction, actions = [], children }) {
  const [error, setError] = useState("");
  const closeOnBackdrop = event => { if (event.target === event.currentTarget) onClose(); };
  const buttons = [...(onAction ? [{ label: actionLabel, onClick: onAction }] : []), ...actions];
  return (
    <Dialog
      title={`${TITLES[kind] || "File"}: ${name}`}
      onClose={onClose}
      overlayClassName="fixed inset-0 z-50 flex flex-col bg-black/95 text-white"
      panelClassName="flex min-h-0 flex-1 flex-col outline-none"
      panelProps={{ "data-media-viewer": kind }}
    >
      <div className="flex items-center gap-2 px-3 py-2 text-sm">
        <span className="min-w-0 flex-1 truncate" title={name}>{name}</span>
        {buttons.map(button => (
          <button key={button.label} type="button" className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-white/85 hover:bg-white/10 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white" onClick={button.onClick}>
            <ExternalLink aria-hidden="true" size={14} />{button.label}
          </button>
        ))}
        <button type="button" aria-label="Close" data-dialog-initial-focus className="grid h-8 w-8 place-items-center rounded-md text-white/85 hover:bg-white/10 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-white" onClick={onClose}>
          <X aria-hidden="true" size={18} />
        </button>
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center px-4 pb-4" onClick={closeOnBackdrop}>
        {children ?? (error ? <p role="alert" className="max-w-md text-center text-sm text-white/85">{error}</p>
          : kind === "video"
            // The click that opened the player lets it start with sound.
            ? <video aria-label={`Play ${name}`} autoPlay controls playsInline className="h-full w-full object-contain" src={url}
              onError={() => setError("This video can't be played here. Its format or codec may not be supported.")} />
            : kind === "audio"
              ? <audio aria-label={`Play ${name}`} autoPlay controls className="w-full max-w-lg" src={url}
                onError={() => setError("This sound can't be played here. Its format may not be supported.")} />
              : <img alt={name} className="h-full w-full object-contain" src={url}
                onError={() => setError("This picture can't be shown.")} />)}
      </div>
    </Dialog>
  );
}
