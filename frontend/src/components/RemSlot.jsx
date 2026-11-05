import { useLayoutEffect, useRef } from "react";

// Rem's chat is one React instance that moves between the main area and the
// side pane. It renders into this host through a portal, and the visible slot
// moves the host's DOM node. Remounting instead re-rendered the whole
// conversation on every move, which froze the window for long histories.
export function createRemHost() {
  const host = document.createElement("div");
  host.className = "contents";
  host.setAttribute("data-rem-host", "true");
  // Scroll events don't bubble, so the stream's position is caught on the way down.
  host.addEventListener("scroll", event => { if (event.target.classList?.contains("rem-stream")) rememberRemScroll(host); }, true);
  return host;
}

// A node out of the document loses its scroll offset, so the stream's position
// is kept across the move. A stream following the newest message keeps following.
export function rememberRemScroll(host) {
  const stream = host.querySelector(".rem-stream");
  if (!stream) return;
  host.remScroll = { top: stream.scrollTop, pinned: stream.scrollHeight - stream.scrollTop - stream.clientHeight < 80 };
}

export function restoreRemScroll(host) {
  const stream = host.querySelector(".rem-stream");
  const saved = host.remScroll;
  if (!stream || !saved) return;
  stream.scrollTop = saved.pinned ? stream.scrollHeight : saved.top;
}

export function RemSlot({ host }) {
  const slotRef = useRef(null);
  useLayoutEffect(() => {
    slotRef.current.appendChild(host);
    restoreRemScroll(host);
    return () => {
      if (host.isConnected) rememberRemScroll(host);
      host.parentNode?.removeChild(host);
    };
  }, [host]);
  return <div ref={slotRef} className="contents" data-rem-slot="true" />;
}
