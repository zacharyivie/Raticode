import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, RefreshCw, X } from "lucide-react";
import { hasUnsavedCodeChanges } from "../lib/codeEditorSessions.js";

export default function ProjectSearch({ rootPath, active, focusRequest, onOpenFile, onReplace, onBusy, disabled = false }) {
  const inputRef = useRef(null);
  const previousFocusRequest = useRef(focusRequest);
  const [query, setQuery] = useState("");
  const [matchCase, setMatchCase] = useState(false);
  const [wholeWord, setWholeWord] = useState(false);
  const [regex, setRegex] = useState(false);
  const [include, setInclude] = useState("");
  const [exclude, setExclude] = useState("");
  const [excludeRegex, setExcludeRegex] = useState(false);
  const [showReplace, setShowReplace] = useState(false);
  const [replacement, setReplacement] = useState("");
  const [replacing, setReplacing] = useState(false);
  const [notice, setNotice] = useState("");
  const [replaceError, setReplaceError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [state, setState] = useState({ status: "idle" });
  useEffect(() => {
    if (active && previousFocusRequest.current !== focusRequest) inputRef.current?.focus();
    previousFocusRequest.current = focusRequest;
  }, [active, focusRequest]);
  useEffect(() => {
    let cancelled = false;
    if (!query || !rootPath) { setState({ status: "idle" }); return; }
    setState({ status: "loading" });
    const timer = setTimeout(async () => {
      try {
        const search = window.goferDesktop?.workspace?.searchProject;
        if (!search) throw new Error("Project search requires the desktop app.");
        const result = await search(rootPath, { query, matchCase, wholeWord, regex, include, exclude, excludeRegex });
        if (!cancelled) setState({ status: "ready", ...result });
      } catch (error) {
        if (!cancelled) setState({ status: "error", message: error.message || String(error) });
      }
    }, 250);
  return () => { cancelled = true; clearTimeout(timer); };
  }, [query, rootPath, matchCase, wholeWord, regex, include, exclude, excludeRegex, refresh]);
  async function replaceFiles(files) {
    if (replacing || disabled || state.status !== "ready" || state.truncated) return;
    setReplaceError("");
    setNotice("");
    if (hasUnsavedCodeChanges(rootPath)) { setReplaceError("Save your unsaved editor changes before replacing."); return; }
    const count = files.reduce((total, file) => total + file.matches.length, 0);
    if (!window.confirm(`Replace ${count} matches in ${files.length} files? This changes files on disk.`)) return;
    setReplacing(true);
    onBusy?.(true);
    window.dispatchEvent(new CustomEvent("gofer:git-working-tree-busy", { detail: { rootPath, busy: true } }));
    try {
      const replace = window.goferDesktop?.workspace?.replaceProject;
      if (!replace) throw new Error("Restart the desktop app to enable replacement.");
      const result = await replace(rootPath, { query, matchCase, wholeWord, regex, include, exclude, excludeRegex, replacement, files: files.map(({ path, hash }) => ({ path, hash })) });
      setNotice(`Replaced ${result.count} ${result.count === 1 ? "match" : "matches"} in ${result.changed.length} ${result.changed.length === 1 ? "file" : "files"}.`);
      if (result.error) setReplaceError(result.error);
    } catch (error) { setReplaceError(error.message || String(error)); }
    finally {
      onReplace?.();
      window.dispatchEvent(new CustomEvent("gofer:git-working-tree-busy", { detail: { rootPath, busy: false } }));
      setReplacing(false);
      onBusy?.(false);
      setRefresh((value) => value + 1);
    }
  }
  return (
    <section id="sidebar-panel-search" role="tabpanel" aria-labelledby="sidebar-tab-search" hidden={!active} className={`min-h-0 min-w-0 flex-1 flex-col text-ink ${active ? "flex" : "hidden"}`}>
      <div className="flex h-7 shrink-0 items-center justify-between px-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">Search</span>
        <button type="button" aria-label="Refresh search" title="Refresh search" disabled={!query || !rootPath} onClick={() => setRefresh((value) => value + 1)} className="rounded p-1 text-muted hover:text-ink disabled:opacity-40"><RefreshCw size={14} /></button>
      </div>
      <fieldset disabled={replacing || disabled} className="min-w-0 space-y-2 px-2 pb-2">
        <div className="space-y-1.5">
          <div className="flex items-center gap-1 rounded border border-line bg-white focus-within:border-brand">
            <input ref={inputRef} aria-label="Search project" placeholder={regex ? "Search regular expression" : "Search text"} value={query} disabled={!rootPath} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") setRefresh((value) => value + 1); }} className="min-w-0 flex-1 bg-transparent px-2 py-1.5 text-xs outline-none" />
            {query && <button type="button" aria-label="Clear search" title="Clear search" onClick={() => { setQuery(""); inputRef.current?.focus(); }} className="mr-1 rounded p-1 text-muted hover:text-ink"><X size={13} /></button>}
          </div>
          <div className="flex gap-1">
            {[["Match case", "Aa", matchCase, setMatchCase], ["Match whole word", "ab", wholeWord, setWholeWord], ["Use regular expression", ".*", regex, setRegex]].map(([label, text, selected, setSelected]) => (
              <button key={label} type="button" aria-label={label} title={label} aria-pressed={selected} onClick={() => setSelected(!selected)} className={`rounded border px-1.5 py-0.5 text-xs focus-visible:outline-brand ${selected ? "border-brand bg-indigo-50 text-brand" : "border-line text-muted hover:text-ink"}`}>{text}</button>
            ))}
          </div>
          {regex && <p className="text-[11px] leading-4 text-muted">Regex: omit the / delimiters.</p>}
          <button type="button" aria-expanded={showReplace} onClick={() => setShowReplace(!showReplace)} className="flex items-center gap-1 rounded text-xs text-muted hover:text-ink focus-visible:outline-brand">
            {showReplace ? <ChevronDown size={13} /> : <ChevronRight size={13} />} Replace
          </button>
          {showReplace && <div className="space-y-2">
            <input aria-label="Replace with" placeholder="Replace with" value={replacement} onChange={(event) => setReplacement(event.target.value)} className="w-full min-w-0 rounded border border-line bg-white px-2 py-1.5 text-xs focus:border-brand focus:outline-none" />
            {regex && <p className="text-xs text-muted">Use $1, $2 or $&amp; for captured text.</p>}
            <button type="button" disabled={state.status !== "ready" || !state.count || state.truncated} onClick={() => void replaceFiles(state.files)} className="rounded border border-line px-2 py-1 text-xs hover:bg-slate-100 disabled:opacity-40 focus-visible:outline-brand">{replacing ? "Replacing…" : "Replace all"}</button>
          </div>}
        </div>
        <fieldset className="min-w-0 space-y-2 border-t border-line pt-2" aria-label="File filters">
          <label className="block space-y-1 text-xs text-muted">
            <span>Files to include</span>
            <input aria-label="Files to include" aria-describedby="search-filter-hint" placeholder="e.g. src/**, *.py" value={include} onChange={(event) => setInclude(event.target.value)} className="w-full min-w-0 rounded border border-line bg-white px-2 py-1.5 text-xs text-ink focus:border-brand focus:outline-none" />
          </label>
          <div className="space-y-1 text-xs text-muted">
            <div className="flex items-center justify-between gap-2">
              <label htmlFor="search-exclude">Files to exclude</label>
              <button type="button" aria-label="Use regular expression for exclusions" title="Use regular expression for exclusions" aria-pressed={excludeRegex} onClick={() => setExcludeRegex(!excludeRegex)} className={`rounded border px-1.5 py-0.5 text-xs focus-visible:outline-brand ${excludeRegex ? "border-brand bg-indigo-50 text-brand" : "border-line text-muted hover:text-ink"}`}>.*</button>
            </div>
            <input id="search-exclude" aria-label="Files to exclude" aria-describedby={excludeRegex ? "search-filter-hint search-exclude-hint" : "search-filter-hint"} placeholder={excludeRegex ? "e.g. \\.(log|json)$" : "e.g. *.log, dist/**, lock.json"} value={exclude} onChange={(event) => setExclude(event.target.value)} className="w-full min-w-0 rounded border border-line bg-white px-2 py-1.5 text-xs text-ink focus:border-brand focus:outline-none" />
          </div>
          <p id="search-filter-hint" className="text-[11px] leading-4 text-muted">Comma-separated globs. Empty include = all files. Git ignores apply.</p>
          {excludeRegex && <p id="search-exclude-hint" className="text-[11px] leading-4 text-muted">Exclude regex: case-sensitive paths, | for alternatives, no / delimiters.</p>}
        </fieldset>
        {notice && <p role="status" className="text-xs text-muted">{notice}</p>}
        {replaceError && <p role="alert" className="break-words text-xs text-red-600">{replaceError}</p>}
        <p role="status" className="text-xs text-muted">
          {!rootPath ? "Open a project to search its files." : state.status === "loading" ? "Searching…" : state.status === "ready" ? state.count ? `${state.count} results in ${state.files.length} files` : "No results found." : state.status === "error" ? "Search failed." : ""}
        </p>
        {state.status === "error" && <p role="alert" className="break-words text-xs text-red-600">{state.message}</p>}
        {state.truncated && <p className="text-xs text-muted">Search limit reached. Narrow your search to see more results.</p>}
        {state.skipped > 0 && <p className="text-xs text-muted">{state.skipped} large or unreadable files skipped.</p>}
      </fieldset>
      <div className="min-h-0 flex-1 overflow-auto pb-2">
        {state.status === "ready" && state.files.map((file) => (
          <details key={file.path} open className="text-xs">
            <summary title={file.relativePath} className="cursor-pointer truncate px-2 py-1 font-medium hover:bg-slate-100">{file.relativePath} <span className="text-muted">{file.matches.length}</span></summary>
            {showReplace && <button type="button" disabled={replacing || disabled || state.truncated} aria-label={`Replace matches in ${file.relativePath}`} onClick={() => void replaceFiles([file])} className="mx-2 rounded px-1 py-1 text-xs text-brand hover:bg-indigo-50 disabled:opacity-40 focus-visible:outline-brand">Replace in file</button>}
            {file.matches.map((match) => (
              <button key={`${match.lineNumber}:${match.column}`} type="button" title={`${file.relativePath}:${match.lineNumber}:${match.column}`} onClick={() => onOpenFile?.(file.path, { preview: true, lineNumber: match.lineNumber, column: match.column })} className="flex w-full gap-2 px-3 py-1 text-left hover:bg-slate-100 focus-visible:outline focus-visible:outline-brand">
                <span className="shrink-0 text-muted">{match.lineNumber}</span>
                <span className="truncate whitespace-pre font-mono">{match.text.slice(0, match.offset)}<mark className="rounded-sm bg-indigo-100 text-ink">{match.text.slice(match.offset, match.offset + match.length)}</mark>{match.text.slice(match.offset + match.length)}</span>
              </button>
            ))}
          </details>
        ))}
      </div>
    </section>
  );
}
