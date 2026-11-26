import { useCallback, useEffect, useRef, useState } from "react";
import { DatabaseZap } from "lucide-react";
import { apiJson } from "../lib/api";

const SMALL_BUTTON_CLASS =
  "inline-flex h-7 items-center gap-1 rounded-md border border-line bg-white px-2 text-[11px] font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50";

const request = (workflowId, options) =>
  apiJson(`/rattish/cache?workflowId=${encodeURIComponent(workflowId)}`, { ...options, failure: status => `Request failed (${status}).` });

export const resultCacheApi = {
  load: (workflowId) => request(workflowId),
  clear: (workflowId) => request(workflowId, { method: "DELETE" }),
};

export function formatCacheSize(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

// Workflow settings > Result cache: saved answers of common-llm-task and jev nodes
// that set cache: DURATION. Clearing makes those nodes call their model again.
export function WorkflowResultCacheSection({ active = true, workflowId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const generation = useRef(0);

  const refresh = useCallback(async () => {
    if (!workflowId) return;
    const current = ++generation.current;
    try {
      const payload = await resultCacheApi.load(workflowId);
      if (current === generation.current) {
        setData(payload);
        setError("");
      }
    } catch (failure) {
      if (current === generation.current) setError(failure.message);
    }
  }, [workflowId]);

  useEffect(() => {
    setNotice("");
    if (active) void refresh();
  }, [active, refresh]);

  async function clear() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const payload = await resultCacheApi.clear(workflowId);
      generation.current += 1;
      setData(payload);
      setNotice(`Removed ${payload.removed} saved ${payload.removed === 1 ? "answer" : "answers"}.`);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  if (!workflowId) return null;
  const entries = data?.entries ?? 0;
  return (
    <section
      aria-label="Result cache"
      className="space-y-3 rounded-lg border border-line p-3"
      data-workflow-result-cache="true"
    >
      <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-muted">Result cache</h3>
      <p className="text-xs leading-5 text-muted">
        LLM task and Jev nodes with <code>cache: 7d</code> reuse a saved answer while it is that
        young and the provider, model, prompt, inputs and node are unchanged. A cached step makes no
        model call and records 0 tokens and $0. Test sets always skip the cache.
      </p>
      <div className="flex items-center justify-between gap-2 rounded-lg border border-line px-2.5 py-1.5">
        <span className="flex min-w-0 items-center gap-1.5 text-[12px] text-ink" role="status">
          <DatabaseZap aria-hidden="true" className="shrink-0 text-muted" size={12} />
          {data === null && !error
            ? "Loading…"
            : `${entries} saved ${entries === 1 ? "answer" : "answers"}, ${formatCacheSize(data?.bytes)} of ${formatCacheSize(data?.maxBytes)}`}
        </span>
        <button
          className={SMALL_BUTTON_CLASS}
          disabled={busy || data === null || !entries}
          title="Remove this workflow's saved answers"
          type="button"
          onClick={clear}
        >
          {busy ? "Clearing…" : "Clear cache"}
        </button>
      </div>
      {notice ? <p className="text-xs text-muted" role="status">{notice}</p> : null}
      {error ? (
        <p className="text-xs text-red-700" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
