// Track a resize drag without re-rendering the app on every pointer event.
// Moves are coalesced to one update per animation frame. Pointer capture keeps
// the drag alive while the pointer crosses editors, webviews, or iframes.
export function trackPointerDrag(event, { cursor, onEnd, onMove }) {
  if (event.button !== undefined && event.button !== 0) return false;
  event.preventDefault();

  const handle = event.currentTarget;
  const pointerId = event.pointerId;
  const body = document.body;
  const previousCursor = body.style.cursor;
  const previousUserSelect = body.style.userSelect;
  const requestFrame = window.requestAnimationFrame?.bind(window) ?? ((callback) => window.setTimeout(callback, 16));
  const cancelFrame = window.cancelAnimationFrame?.bind(window) ?? window.clearTimeout.bind(window);
  let frame = 0;
  let pending = null;
  let finished = false;

  body.style.cursor = cursor;
  body.style.userSelect = "none";
  try {
    handle?.setPointerCapture?.(pointerId);
  } catch {
    // The pointer may already be released; window listeners still end the drag.
  }

  function flush() {
    frame = 0;
    if (!pending) return;
    const point = pending;
    pending = null;
    onMove(point);
  }

  function handlePointerMove(moveEvent) {
    if (pointerId !== undefined && moveEvent.pointerId !== undefined && moveEvent.pointerId !== pointerId) return;
    pending = { clientX: moveEvent.clientX, clientY: moveEvent.clientY };
    if (!frame) frame = requestFrame(flush);
  }

  function finish() {
    if (finished) return;
    finished = true;
    if (frame) cancelFrame(frame);
    flush();
    body.style.cursor = previousCursor;
    body.style.userSelect = previousUserSelect;
    window.removeEventListener("pointermove", handlePointerMove);
    window.removeEventListener("pointerup", finish);
    window.removeEventListener("pointercancel", finish);
    window.removeEventListener("blur", finish);
    handle?.removeEventListener?.("lostpointercapture", finish);
    try {
      if (handle?.hasPointerCapture?.(pointerId)) handle.releasePointerCapture(pointerId);
    } catch {
      // Capture already ended with the pointer.
    }
    onEnd?.();
  }

  window.addEventListener("pointermove", handlePointerMove);
  window.addEventListener("pointerup", finish);
  window.addEventListener("pointercancel", finish);
  window.addEventListener("blur", finish);
  handle?.addEventListener?.("lostpointercapture", finish);
  return true;
}
