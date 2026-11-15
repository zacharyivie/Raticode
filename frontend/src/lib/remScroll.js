// When Rem's chat follows the newest message.
//
// The chat follows while you are at its end and stops as soon as you move up. A wheel
// turn animates over several frames, and new text, a message laying out as it scrolls
// near, or a picture loading can resize the chat during those frames. Deciding only by
// "within 80px of the end" kept following through the first frames of a turn upward, so
// those resizes pulled the chat back down and the turn stuttered or never moved.

// Close enough to the end that moving down to here resumes following.
export const FOLLOW_ZONE = 80;

// Distance between the bottom of what you see and the end of the chat.
export function gapToEnd(element) {
  return element.scrollHeight - element.scrollTop - element.clientHeight;
}

// Whether the chat follows after a scroll. `last` is the position at the previous scroll.
// Moving up (the top goes up and the end gets farther) stops following at once, even
// within the follow zone. Reaching the end, or moving down into the follow zone, resumes it.
// Anything else, such as the browser keeping your place while a message above resizes,
// leaves it as it was.
export function followsAfterScroll(following, { top, gap }, last) {
  if (gap <= 1) return true;
  if (last && top < last.top - 0.5 && gap > last.gap + 0.5) return false;
  if (gap >= FOLLOW_ZONE) return false;
  if (last && top > last.top + 0.5 && gap < last.gap - 0.5) return true;
  return following;
}

// A wheel turn upward over the chat stops following before the chat starts to move,
// unless something inside the message (a scrolling code block, say) takes the turn,
// or the chat can't move up at all.
export function wheelLeavesEnd(event, stream) {
  if (!(event.deltaY < 0) || event.ctrlKey || !stream || stream.scrollTop <= 0) return false;
  for (let node = event.target; node && node !== stream; node = node.parentElement) {
    if (node.scrollTop > 0 && node.scrollHeight > node.clientHeight + 1) {
      const overflow = globalThis.getComputedStyle?.(node).overflowY;
      if (overflow === "auto" || overflow === "scroll") return false;
    }
  }
  return true;
}

// The chat's message rows, top to bottom (none where the page can't be queried).
const messageRows = stream => [...(stream.querySelectorAll?.("[data-message-id]") || [])];

// How long Rem's chat holds your place after earlier messages load: long enough for the
// new rows, and rows just scrolled into view, to lay out at their real height.
export const HOLD_PLACE_MS = 2000;

// Where you are reading: the first message whose top is in view, and how far that top is
// from the chat's top. Earlier messages are added above it without moving it. A message
// cut off at the top isn't used, because its own height can still change as it lays out,
// which would move the one you are reading. With none in view, the cut-off one is used.
export function readingPlace(stream, now = Date.now()) {
  if (!stream?.getBoundingClientRect) return null;
  const view = stream.getBoundingClientRect();
  let cut = null;
  for (const node of messageRows(stream)) {
    const box = node.getBoundingClientRect();
    if (box.bottom <= view.top) continue;
    const place = { id: node.getAttribute("data-message-id"), offset: box.top - view.top, until: now + HOLD_PLACE_MS };
    if (box.top >= view.top - 0.5) return box.top < view.bottom ? place : cut || place;
    cut ||= place;
  }
  return cut;
}

// Scrolls so the message `place` names is back where it was. Measured from that message,
// not from the end: messages added above it can still change size after they lay out.
export function restoreReadingPlace(stream, place) {
  if (!stream?.getBoundingClientRect || !place) return;
  // Querying every row is fine here: it runs only while a page has just been added.
  const node = messageRows(stream).find(item => item.getAttribute("data-message-id") === place.id);
  if (!node) return;
  const moved = node.getBoundingClientRect().top - stream.getBoundingClientRect().top - place.offset;
  if (Math.abs(moved) >= 0.5) stream.scrollTop += moved;
}
