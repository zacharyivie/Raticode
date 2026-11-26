import { apiUrl } from "./api.js";

// Commit messages in the Source Control view: Rem writes one from the staged
// changes, and a message is checked for Co-authored-by lines before committing.

export function conventionalCommitMessage(text) {
  const message = String(text || "").trim().replace(/^```(?:text)?\n([\s\S]*?)\n```$/, "$1").trim();
  if (!/^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^\r\n()]+\))?!?: [^\r\n]+(?:\n|$)/.test(message)) {
    throw new Error("Rem did not return a Conventional Commit message. Try generating again.");
  }
  return message;
}

export async function generateConventionalCommit({ provider, model, effort, permissionMode, diff, projectRoot, inspectStaged = false, signal }) {
  inspectStaged ||= (diff?.length || 0) > 120000;
  const response = await fetch(apiUrl("/chat/commit-message"), {
    method: "POST", headers: { "Content-Type": "application/json" }, signal,
    body: JSON.stringify({ provider, model, ...(effort ? { effort } : {}), ...(permissionMode ? { permissionMode } : {}), ...(inspectStaged ? { inspectStaged: true, projectRoot, grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(projectRoot) || undefined } : { diff }) }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Rem returned ${response.status}`);
  return conventionalCommitMessage(payload.message);
}

// Settings > Rem > Remove Co-authored-by lines. The backend's Python check decides
// whether the setting is on and what to remove; a message that never mentions
// co-authored-by can't have such a line, so it skips the round trip.
export async function cleanCommitMessage(message, fetcher = fetch) {
  if (!/co-authored-by/i.test(message)) return message;
  let payload;
  try {
    const response = await fetcher(apiUrl("/provider/commit-message/clean"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }),
    });
    payload = await response.json();
    if (!response.ok) throw new Error(payload?.error || `HTTP ${response.status}`);
  } catch (cause) {
    throw new Error(`Couldn't check the message for Co-authored-by lines, so nothing was committed: ${cause.message}`, { cause });
  }
  if (typeof payload?.message !== "string") throw new Error("Couldn't check the message for Co-authored-by lines, so nothing was committed.");
  return payload.message;
}
