// An organization ticket's conversation as people should read it. Mirrors
// visible_comments in src/gofer/ui/rem_ticket_activity.py; stored entries never change.
//
// Older Raticode wrote a status entry whose text repeated the comment just above it (the
// worker's report, or the review's note), so the same words showed twice. Such an entry keeps
// its headline and loses the repeated text. A comment that repeats the same author's previous
// comment within REPEAT_WINDOW_MS is left out; the same words said later, by someone else, or
// with a different review decision are kept.

export const REPEAT_WINDOW_MS = 120_000;

export function repeats(body = "", said = "") {
  const text = String(body).trim().replace(/…$/, "").trimEnd();
  return Boolean(text) && String(said).trim().startsWith(text);
}

function sameComment(earlier, later) {
  if (["actor", "body", "decision"].some(key => String(earlier[key] || "") !== String(later[key] || ""))) return false;
  const first = Date.parse(earlier.at), second = Date.parse(later.at);
  return Number.isFinite(first) && Number.isFinite(second) && Math.abs(second - first) <= REPEAT_WINDOW_MS;
}

export function visibleComments(comments = []) {
  const shown = [];
  let sinceStatus = [];
  for (const comment of comments) {
    if (comment.kind === "status") {
      const copied = comment.body && sinceStatus.some(c => repeats(comment.body, c.body));
      shown.push(copied ? { ...comment, body: "" } : comment);
      sinceStatus = [];
      continue;
    }
    const previous = shown.findLast(c => c.kind !== "status");
    if (previous && sameComment(previous, comment)) continue;
    shown.push(comment);
    sinceStatus.push(comment);
  }
  return shown;
}

const STATUS_NAMES = { backlog: "Backlog", todo: "To do", in_progress: "In progress", in_review: "In review", blocked: "Blocked", done: "Done", cancelled: "Cancelled" };

// A status entry's headline and its move, such as "In progress → In review".
export function statusLine(entry) {
  const name = status => STATUS_NAMES[status] || status;
  return {
    title: entry.title || (entry.to ? `Moved to ${name(entry.to)}` : "Status changed"),
    move: entry.from && entry.to ? `${name(entry.from)} → ${name(entry.to)}` : "",
  };
}
