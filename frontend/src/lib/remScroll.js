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
