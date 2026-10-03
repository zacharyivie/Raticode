export const CODE_DRAFTS_KEY = "raticode.codeDrafts.v1";

export function loadCodeDrafts(storage = globalThis.window?.localStorage) {
  try {
    const entries = JSON.parse(storage?.getItem(CODE_DRAFTS_KEY) || "[]");
    if (!Array.isArray(entries)) return [];
    return entries.filter(entry => Array.isArray(entry) && entry.length === 2
      && typeof entry[0] === "string" && entry[0].length < 8192
      && typeof entry[1]?.content === "string" && typeof entry[1]?.savedContent === "string"
      && entry[1].content !== entry[1].savedContent).map(([path, draft]) => [path, { ...draft, recovered: true }]);
  } catch { return []; }
}

export function saveCodeDrafts(sessions, storage = globalThis.window?.localStorage) {
  try {
    const drafts = [...sessions].filter(([, session]) => session.content !== session.savedContent)
      .map(([path, session]) => [path, { content: session.content, savedContent: session.savedContent, viewState: session.viewState }]);
    if (!storage) return drafts.length === 0;
    if (drafts.length) storage.setItem(CODE_DRAFTS_KEY, JSON.stringify(drafts));
    else storage.removeItem(CODE_DRAFTS_KEY);
    return true;
  } catch { return false; }
}
