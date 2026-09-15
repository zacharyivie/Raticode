import { apiUrl } from "./api.js";

export function conventionalCommitMessage(text) {
  const message = String(text || "").trim().replace(/^```(?:text)?\n([\s\S]*?)\n```$/, "$1").trim();
  if (!/^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^\r\n()]+\))?!?: [^\r\n]+(?:\n|$)/.test(message)) {
    throw new Error("Rem did not return a Conventional Commit message. Try generating again.");
  }
  return message;
}

export async function generateConventionalCommit({ provider, model, effort, diff, projectRoot, inspectStaged = false, signal }) {
  inspectStaged ||= (diff?.length || 0) > 120000;
  const response = await fetch(apiUrl("/chat/commit-message"), {
    method: "POST", headers: { "Content-Type": "application/json" }, signal,
    body: JSON.stringify({ provider, model, ...(effort ? { effort } : {}), ...(inspectStaged ? { inspectStaged: true, projectRoot, grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(projectRoot) || undefined } : { diff }) }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Rem returned ${response.status}`);
  return conventionalCommitMessage(payload.message);
}
