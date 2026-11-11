// Searching Rem's chat. The desktop backend searches the whole saved history
// (GET /api/rem/search) and sends each match's snippet already split into plain and
// matched parts, so this side never counts characters to highlight.

export const SEARCH_DELAY_MS = 250;
export const MAX_QUERY = 200;

const IDLE = Object.freeze({ query: "", status: "idle", total: 0, results: [], next: null, error: "", loadingMore: false });

// Typing waits SEARCH_DELAY_MS before it searches, and an answer to an older query
// never replaces a newer one. Results stay on screen while the next search runs.
export function createRemSearch({ search, onChange = () => {}, delay = SEARCH_DELAY_MS, timers = globalThis }) {
  let state = IDLE;
  let timer = null;
  let token = 0;
  const publish = patch => { state = { ...state, ...patch }; onChange(state); };
  async function run(query, before, id) {
    try {
      const found = await search(query, before);
      if (id !== token) return;
      const results = Array.isArray(found?.results) ? found.results : [];
      publish({
        status: "ready",
        total: Number(found?.total) || 0,
        results: before == null ? results : [...state.results, ...results],
        next: found?.next ?? null,
        error: "",
        loadingMore: false,
      });
    } catch (cause) {
      if (id !== token) return;
      const error = cause?.message || "Couldn't search Rem's chat.";
      // A failed "more" keeps the results already shown.
      publish(before == null ? { status: "error", error, loadingMore: false } : { error, loadingMore: false });
    }
  }
  const start = (query, wait) => {
    timers.clearTimeout(timer);
    const id = ++token;
    const words = query.trim();
    if (!words) { state = { ...IDLE, query }; onChange(state); return; }
    publish({ query, status: "loading", error: "", loadingMore: false });
    if (wait) timer = timers.setTimeout(() => void run(words, null, id), delay);
    else void run(words, null, id);
  };
  return {
    get state() { return state; },
    setQuery(query) { start(String(query ?? "").slice(0, MAX_QUERY), true); },
    retry() { start(state.query, false); },
    more() {
      if (state.next == null || state.loadingMore || state.status !== "ready") return;
      const id = token;
      publish({ loadingMore: true, error: "" });
      void run(state.query.trim(), state.next, id);
    },
    dispose() { timers.clearTimeout(timer); ++token; },
  };
}

// The distinct words of a search, as the desktop matches them, to find in the message a result opens.
export function searchWords(query = "") {
  const seen = new Map();
  for (const word of String(query).slice(0, MAX_QUERY).split(/\s+/)) if (word && !seen.has(word.toLowerCase())) seen.set(word.toLowerCase(), word);
  return [...seen.values()].slice(0, 8);
}

// The count line under the search box.
export function searchSummary({ status, total, results, query }) {
  if (!query.trim()) return "";
  if (status === "loading" && !results.length) return "Searching…";
  if (status === "error") return "";
  if (!total) return "No matches";
  const count = `${total.toLocaleString()} ${total === 1 ? "match" : "matches"}`;
  return results.length < total ? `${count}, showing ${results.length.toLocaleString()}` : count;
}

const WHERE = { task: "In the task title", file: "In a file name", details: "In the details", result: "In the result" };

// Who wrote a result and, for a match outside the message text, where it was found.
export function resultLabel(result) {
  const who = result.role === "user" ? "You" : result.kind === "task_update" ? "Task update" : "Rem";
  return { who, where: WHERE[result.where] || "" };
}

// Snippet parts as text, for a screen reader's label of the whole result.
export function partsText(parts = []) {
  return parts.map(part => part.text).join("");
}
