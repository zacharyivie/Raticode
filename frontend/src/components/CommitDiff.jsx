import { memo, useDeferredValue, useEffect, useId, useMemo, useRef, useState } from "react";
import { Search, X, ListFilter, RotateCcw, ChevronUp, ChevronDown, MessageSquarePlus, Trash2 } from "lucide-react";
import { languageForPath } from "../lib/editorLanguage.js";
import { filterCommitFiles, indexCommitMatches, searchCommitFiles } from "../lib/commitDiff.js";

const STATUS_LABELS = { A: "Added", D: "Deleted", M: "Modified", R: "Renamed", C: "Copied", T: "Type changed" };
const DEFAULT_EDITOR_SETTINGS = {};
const EMPTY_FILES = [];
const EMPTY_MATCHES = [];
const EMPTY_COMMENTS = [];
const SIDE_LABELS = { modified: "After", original: "Before" };

// One line comment being written: the line stays a string while it's edited.
function CommentDraft({ file, draft, setDraft, onComment }) {
  const lineId = useId();
  const line = Number.parseInt(draft.line, 10);
  const valid = Number.isInteger(line) && line > 0 && draft.body.trim();
  const save = event => {
    event.preventDefault();
    if (!valid) return;
    onComment({ path: file.path, side: draft.side, line, body: draft.body.trim() });
    setDraft(null);
  };
  return <form className="commit-diff-comment-form" onSubmit={save} aria-label={`Comment on ${file.path}`}>
    <div className="commit-diff-comment-where">
      <label htmlFor={lineId}>Line</label>
      <input id={lineId} aria-label="Line number" inputMode="numeric" value={draft.line} onChange={event => setDraft({ ...draft, line: event.target.value })} />
      <label className="commit-diff-sr" htmlFor={`${lineId}-side`}>Side</label>
      <select id={`${lineId}-side`} value={draft.side} onChange={event => setDraft({ ...draft, side: event.target.value })}>
        <option value="modified">After the change</option>
        {file.status !== "A" ? <option value="original">Before the change</option> : null}
      </select>
    </div>
    <label className="commit-diff-sr" htmlFor={`${lineId}-body`}>Comment</label>
    <textarea id={`${lineId}-body`} aria-label="Comment text" autoFocus rows={2} maxLength={4000} placeholder="What should the worker change here?" value={draft.body}
      onChange={event => setDraft({ ...draft, body: event.target.value })}
      onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); setDraft(null); } if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) save(event); }} />
    <div className="commit-diff-comment-actions">
      <button type="button" className="is-primary" disabled={!valid} onClick={save}>Add comment</button>
      <button type="button" onClick={() => setDraft(null)}>Cancel</button>
    </div>
  </form>;
}

const CommitFileDiff = memo(function CommitFileDiff({ file, theme, editorSettings, query, matches, registry, comments = EMPTY_COMMENTS, onComment, onRemoveComment }) {
  const [draft, setDraft] = useState(null);
  const commentingRef = useRef(null);
  commentingRef.current = onComment ? setDraft : null;
  const markRef = useRef(null);
  const articleRef = useRef(null);
  const containerRef = useRef(null);
  const searchRef = useRef(null);
  const queryRef = useRef({ query, matches });
  const [error, setError] = useState("");

  const commentsRef = useRef(comments);
  useEffect(() => {
    commentsRef.current = comments;
    markRef.current?.(comments);
  }, [comments]);

  useEffect(() => {
    queryRef.current = { query, matches };
    searchRef.current?.(query, matches);
  }, [query, matches]);

  useEffect(() => {
    let cancelled = false;
    let editor;
    let viewModel;
    let original;
    let modified;
    let frame;
    const listeners = [];
    const container = containerRef.current;
    setError("");
    let loading;
    let activeDecoration;
    let revealVersion = 0;
    const api = {
      measure() {
        const article = articleRef.current;
        return { title: (article?.getBoundingClientRect().top || 0) + 16,
          content: container?.getBoundingClientRect().top || 0, height: container?.clientHeight || 0 };
      },
      top(match, layout = api.measure()) {
        if (match.side === "path" || !container) return layout.title;
        const side = match.side === "original" ? editor?.getOriginalEditor() : editor?.getModifiedEditor();
        const offset = side ? side.getTopForLineNumber(match.range.startLineNumber) :
          (match.start / Math.max(1, file[match.side].length)) * layout.height;
        return layout.content + offset;
      },
      clear() {
        revealVersion++;
        activeDecoration?.clear();
        articleRef.current?.querySelectorAll(".is-current").forEach(node => node.classList.remove("is-current"));
      },
      async reveal(match) {
        api.clear();
        const version = revealVersion;
        if (match.side === "path" || file.binary) {
          articleRef.current?.querySelector(`[data-match-start="${match.start}"]`)?.classList.add("is-current");
          return;
        }
        await load();
        if (cancelled || !editor || version !== revealVersion) return;
        const side = match.side === "original" ? editor.getOriginalEditor() : editor.getModifiedEditor();
        side.revealRange(match.range);
        activeDecoration?.clear();
        activeDecoration = side.createDecorationsCollection([{ range: match.range, options: { inlineClassName: "commit-diff-current-match" } }]);
        // Let expanded unchanged regions and any new editor height settle first.
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      },
    };
    const entries = registry.current;
    entries.set(file, api);
    const load = () => loading ||= loadEditor();
    const loadEditor = async () => {
      try {
        const { loadRattishMonaco } = await import("../lib/monaco.js");
        if (cancelled || !container) return;
        const monaco = loadRattishMonaco();
        original = monaco.editor.createModel(file.original, languageForPath(file.oldPath));
        modified = monaco.editor.createModel(file.modified, languageForPath(file.path));
        editor = monaco.editor.createDiffEditor(container, {
          ariaLabel: `Changes in ${file.path}`,
          originalAriaLabel: `Before: ${file.oldPath}`,
          modifiedAriaLabel: `After: ${file.path}`,
          readOnly: true,
          domReadOnly: true,
          // Keep the hidden textarea input, as in CodeWorkspace.
          editContext: false,
          originalEditable: false,
          renderSideBySide: true,
          useInlineViewWhenSpaceIsLimited: false,
          enableSplitViewResizing: true,
          automaticLayout: true,
          theme: theme === "dark" ? "gofer-rattish-dark" : "gofer-rattish-light",
          fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
          fontSize: editorSettings.fontSize,
          lineHeight: editorSettings.lineHeight,
          tabSize: editorSettings.tabSize,
          minimap: { enabled: false },
          wordWrap: editorSettings.wordWrap ? "on" : "off",
          diffWordWrap: editorSettings.wordWrap ? "on" : "off",
          ignoreTrimWhitespace: false,
          hideUnchangedRegions: { enabled: true, contextLineCount: 3, minimumLineCount: 8, revealLineCount: 20 },
          scrollBeyondLastLine: false,
          scrollbar: { vertical: "hidden", alwaysConsumeMouseWheel: false },
          renderOverviewRuler: false,
          overviewRulerLanes: 0,
          padding: { top: 8, bottom: 8 },
        });
        // Grow each comparison to its content so the commit has one vertical scroll.
        const resize = () => {
          cancelAnimationFrame(frame);
          frame = requestAnimationFrame(() => {
            if (cancelled) return;
            const height = Math.max(80, editor.getOriginalEditor().getContentHeight(), editor.getModifiedEditor().getContentHeight());
            if (container.style.height !== `${height}px`) {
              container.style.height = `${height}px`;
              editor.layout();
            }
          });
        };
        listeners.push(editor.getOriginalEditor().onDidContentSizeChange(resize), editor.getModifiedEditor().onDidContentSizeChange(resize), editor.onDidUpdateDiff(resize));
        // Own the view model explicitly. Monaco 0.52 retains an internal reference
        // when setModel creates it, so editor.dispose alone leaves diff work alive.
        viewModel = editor.createViewModel({ original, modified });
        editor.setModel(viewModel);
        const sides = [editor.getOriginalEditor(), editor.getModifiedEditor()];
        const decorations = sides.map(side => side.createDecorationsCollection());
        searchRef.current = (text, found) => {
          // Search includes unchanged text, so expose it while a query is active.
          editor.updateOptions({ hideUnchangedRegions: { enabled: !text } });
          sides.forEach((side, index) => decorations[index].set(found.filter(match => match.side === (index === 0 ? "original" : "modified")).map(match => ({
            range: match.range, options: { inlineClassName: "commit-diff-match" },
          }))));
          resize();
        };
        searchRef.current(queryRef.current.query, queryRef.current.matches);
        // Clicking a line number starts a comment on that line, on that side.
        const gutter = new Set([monaco.editor.MouseTargetType.GUTTER_LINE_NUMBERS, monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN, monaco.editor.MouseTargetType.GUTTER_LINE_DECORATIONS]);
        sides.forEach((side, index) => listeners.push(side.onMouseDown(event => {
          const lineNumber = event.target.position?.lineNumber;
          if (!commentingRef.current || !gutter.has(event.target.type) || !lineNumber) return;
          commentingRef.current({ side: index === 0 ? "original" : "modified", line: String(lineNumber), body: "" });
        })));
        const marks = sides.map(side => side.createDecorationsCollection());
        markRef.current = list => sides.forEach((side, index) => marks[index].set(list.filter(item => item.side === (index === 0 ? "original" : "modified")).map(item => ({
          range: new monaco.Range(item.line, 1, item.line, 1),
          options: { isWholeLine: true, className: "commit-diff-commented-line", linesDecorationsClassName: "commit-diff-commented-gutter" },
        }))));
        markRef.current(commentsRef.current);
        resize();
      } catch (cause) {
        if (!cancelled) setError(cause.message || "Unable to display this file diff.");
      }
    };
    // Defer Monaco instances below the viewport until the user approaches them.
    const observer = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); void load(); }
    }, { rootMargin: "600px" });
    if (!file.binary) {
      if (observer) observer.observe(container);
      else void load();
    }
    return () => {
      cancelled = true;
      entries.delete(file);
      activeDecoration?.clear();
      searchRef.current = null;
      markRef.current = null;
      observer?.disconnect();
      cancelAnimationFrame(frame);
      listeners.forEach(listener => listener.dispose());
      editor?.dispose();
      // Cancel pending worker diffs before disposing their source text models.
      viewModel?.dispose();
      original?.dispose();
      modified?.dispose();
    };
  }, [file, theme, editorSettings, registry]);

  const title = file.oldPath && file.oldPath !== file.path ? `${file.oldPath} → ${file.path}` : file.path;
  const pathMatches = matches.filter(match => match.side === "path");
  let titleCursor = 0;
  const highlightedTitle = pathMatches.flatMap(match => {
    const parts = [title.slice(titleCursor, match.start), <mark key={match.start} data-match-start={match.start} className="commit-diff-match">{title.slice(match.start, match.end)}</mark>];
    titleCursor = match.end;
    return parts;
  });
  return <article ref={articleRef} aria-label={`Changed file ${file.path}`} className="min-w-0 border-b border-line">
    <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line bg-canvas px-4 py-2 text-xs">
      <h3 className="min-w-0 flex-1 break-all font-mono font-semibold text-ink">{highlightedTitle}{title.slice(titleCursor)}</h3>
      <span className="text-muted">{STATUS_LABELS[file.status] || file.status}{file.submodule ? " · Submodule" : ""}</span>
      {file.oldMode !== file.newMode && file.status !== "A" && file.status !== "D" ? <span className="text-muted">Mode {file.oldMode} → {file.newMode}</span> : null}
      {file.uncommitted ? <span className="text-muted">Not committed yet</span> : null}
      {onComment && !file.binary ? <button type="button" className="commit-diff-comment-open" aria-label={`Comment on a line of ${file.path}`} title="Comment on a line (or click a line number)" onClick={() => setDraft(current => current || { side: "modified", line: "", body: "" })}><MessageSquarePlus size={13} aria-hidden="true" />Comment</button> : null}
    </header>
    {comments.length || draft ? <div className="commit-diff-comments">
      {comments.length ? <ul aria-label={`Comments on ${file.path}`}>{comments.map(item => <li key={item.key}>
        <span className="commit-diff-comment-line">{SIDE_LABELS[item.side]} line {item.line}</span>
        <p>{item.body}</p>
        {onRemoveComment ? <button type="button" aria-label={`Remove comment on line ${item.line}`} title="Remove comment" onClick={() => onRemoveComment(item.key)}><Trash2 size={12} aria-hidden="true" /></button> : null}
      </li>)}</ul> : null}
      {draft ? <CommentDraft file={file} draft={draft} setDraft={setDraft} onComment={onComment} /> : null}
    </div> : null}
    {file.binary ? <p className="px-4 py-5 text-xs text-muted">Binary file. Text comparison is unavailable.</p> : <>
      <div className="grid grid-cols-2 border-b border-line text-xs text-muted">
        <span className="border-r border-line px-4 py-1">Before{file.status === "A" ? " · File did not exist" : ""}</span>
        <span className="px-4 py-1">After{file.status === "D" ? " · File deleted" : ""}</span>
      </div>
      {error ? <p role="alert" className="p-4 text-xs text-red-700 dark:text-red-300">{error}</p> : null}
      <div ref={containerRef} className={error ? "hidden" : "min-w-0"} style={{ height: 240 }} />
    </>}
  </article>;
});

// Typing in the toolbar must not reconcile every loaded Monaco comparison.
const CommitFileList = memo(function CommitFileList({ files, theme, editorSettings, query, matchIndex, registry, commentsByPath, onComment, onRemoveComment }) {
  return files.map(file => <CommitFileDiff key={file.path} file={file} theme={theme} editorSettings={editorSettings} query={query} matches={matchIndex.get(file) || EMPTY_MATCHES} registry={registry}
    comments={commentsByPath?.get(file.path) || EMPTY_COMMENTS} onComment={onComment} onRemoveComment={onRemoveComment} />);
});

// The outer scrollbar represents the whole commit, including unloaded editors.
// Coalesce nearby hits into pixel rows so common queries don't create huge DOMs.
function MatchOverview({ matches, activeMatch, registry, scrollRef, contentRef }) {
  const [markers, setMarkers] = useState([]);
  useEffect(() => {
    const scroll = scrollRef.current;
    if (!scroll || !matches.length) { setMarkers([]); return undefined; }
    let frame;
    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const top = scroll.getBoundingClientRect().top;
        const height = scroll.clientHeight;
        const scrollHeight = scroll.scrollHeight;
        const scrollTop = scroll.scrollTop;
        const rows = new Map();
        const layouts = new Map();
        for (const match of matches) {
          const api = registry.current.get(match.file);
          if (!api) continue;
          if (!layouts.has(match.file)) layouts.set(match.file, api.measure());
          const y = Math.max(0, Math.min(height - 3, Math.round((api.top(match, layouts.get(match.file)) - top + scrollTop) / scrollHeight * height)));
          rows.set(y, rows.get(y) || match === activeMatch);
        }
        setMarkers([...rows].map(([y, active]) => ({ y, active })));
      });
    };
    const observer = new ResizeObserver(measure);
    observer.observe(scroll);
    observer.observe(contentRef.current);
    measure();
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [matches, activeMatch, registry, scrollRef, contentRef]);
  return <div className="commit-diff-overview" aria-hidden="true">{markers.map(marker => <i key={marker.y} className={marker.active ? "is-current" : ""} style={{ top: marker.y }} />)}</div>;
}

// A commit from Source Control, or a ticket's patch given as ``payload`` (the same file
// list). With ``onComment``, line numbers and each file's Comment button add line comments.
export default function CommitDiff({ projectRoot, hash = "", theme, editorSettings = DEFAULT_EDITOR_SETTINGS, payload = null, heading = "", subheading = "", label = "", comments = EMPTY_COMMENTS, onComment = null, onRemoveComment = null }) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState("");
  const [include, setInclude] = useState("");
  const [exclude, setExclude] = useState("");
  const [pathFilters, setPathFilters] = useState({ include: "", exclude: "" });
  const deferredQuery = useDeferredValue(query);
  const filterHintId = useId();
  const searchInputRef = useRef(null);
  const scrollRef = useRef(null);
  const contentRef = useRef(null);
  const registry = useRef(new Map());
  const [selection, setSelection] = useState(null);
  const [navigation, setNavigation] = useState(null);
  const files = result?.files || EMPTY_FILES;
  const commentsByPath = useMemo(() => {
    const grouped = new Map();
    for (const item of comments) grouped.set(item.path, [...(grouped.get(item.path) || []), item]);
    return grouped;
  }, [comments]);
  // File contents are immutable for this commit. Reuse search results when only
  // path patterns change, instead of lowercasing and scanning both revisions again.
  const searchedFiles = useMemo(() => searchCommitFiles(files, deferredQuery), [files, deferredQuery]);
  const visibleFiles = useMemo(() => filterCommitFiles(searchedFiles, pathFilters), [searchedFiles, pathFilters]);
  const matchIndex = useMemo(() => indexCommitMatches(searchedFiles, deferredQuery), [searchedFiles, deferredQuery]);
  const matches = useMemo(() => visibleFiles.flatMap(file => matchIndex.get(file) || EMPTY_MATCHES), [visibleFiles, matchIndex]);
  const activeIndex = selection?.matches === matches ? selection.index : -1;
  const activeMatch = matches[activeIndex];
  const pendingSearch = query !== deferredQuery;
  const navigate = direction => setNavigation({ direction });

  useEffect(() => {
    if (!navigation || pendingSearch) return;
    setSelection(previous => {
      const current = previous?.matches === matches ? previous.index : -1;
      const index = matches.length ? (current < 0 ? (navigation.direction > 0 ? 0 : matches.length - 1) : (current + navigation.direction + matches.length) % matches.length) : -1;
      return { matches, index };
    });
    setNavigation(null);
  }, [navigation, pendingSearch, matches]);

  useEffect(() => {
    if (!activeMatch) return undefined;
    let cancelled = false;
    const api = registry.current.get(activeMatch.file);
    void api?.reveal(activeMatch).then(() => {
      const scroll = scrollRef.current;
      if (cancelled || !scroll) return;
      scroll.scrollTo({ top: scroll.scrollTop + api.top(activeMatch) - scroll.getBoundingClientRect().top - scroll.clientHeight / 2 });
    });
    return () => { cancelled = true; api?.clear(); };
  }, [activeMatch, selection, theme, editorSettings]);

  const pendingFilters = include !== pathFilters.include || exclude !== pathFilters.exclude;
  const filtering = Boolean(query || include.trim() || exclude.trim());
  const applyPathFilters = () => { if (pendingFilters) setPathFilters({ include, exclude }); };
  const onFilterKeyDown = event => { if (event.key === "Enter") applyPathFilters(); };
  const resetFilters = () => { setQuery(""); setInclude(""); setExclude(""); setPathFilters({ include: "", exclude: "" }); searchInputRef.current?.focus(); };

  useEffect(() => {
    if (!pendingFilters) return undefined;
    // A partially typed glob such as '*' can hide every file. Wait for the draft
    // to settle so adding a file type does not repeatedly dispose/recreate Monaco.
    const timer = setTimeout(() => setPathFilters({ include, exclude }), 180);
    return () => clearTimeout(timer);
  }, [include, exclude, pendingFilters]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [deferredQuery, pathFilters]);

  useEffect(() => {
    let cancelled = false;
    setResult(payload);
    setError("");
    if (payload) return undefined;
    async function load() {
      try {
        const payload = await window.goferDesktop?.workspace?.gitRepoAction?.(projectRoot, "commit-diff", { hash });
        if (!payload || payload.error) throw new Error(payload?.error || "Commit diffs require the desktop app.");
        if (!cancelled) setResult(payload);
      } catch (cause) {
        if (!cancelled) setError(cause.message || "Unable to load commit diff.");
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [projectRoot, hash, revision, payload]);

  return <section aria-label={label || `Commit diff ${hash.slice(0, 8)}`} className="commit-diff flex min-h-0 min-w-0 flex-1 flex-col">
    <header className="commit-diff-header shrink-0 border-b border-line text-xs">
      {payload ? (heading || subheading ? <div className="space-y-1 px-4 pb-3 pt-3">
        {heading ? <h2 className="break-words font-semibold text-ink">{heading}</h2> : null}
        {subheading ? <p className="break-all text-muted">{subheading}</p> : null}
      </div> : null) : <div className="space-y-1 px-4 pb-3 pt-3">
      <h2 className="break-words font-semibold text-ink">{result?.subject || `Commit ${hash.slice(0, 8)}`}</h2>
      <p className="break-all text-muted">{projectRoot}</p>
      <p className="text-muted">{result ? `${result.parentHash ? `${result.parentHash.slice(0, 8)} → ${hash.slice(0, 8)} · First parent` : `Empty tree → ${hash.slice(0, 8)} · Initial commit`} · Read only` : hash.slice(0, 8)}</p>
      </div>}
      <div className="commit-diff-tools" role="search" aria-label="Search commit diff">
        <div className="commit-diff-search">
          <Search size={15} aria-hidden="true" className="shrink-0 text-muted" />
          <input ref={searchInputRef} aria-label="Search diff" placeholder="Search file paths or text in either revision…" value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.nativeEvent.isComposing) return; if (event.key === "Enter") { event.preventDefault(); navigate(event.shiftKey ? -1 : 1); } if (event.key === "Escape") { event.stopPropagation(); setQuery(""); } }} spellCheck={false} />
          {query ? <>
            <span className="commit-diff-match-count" role="status" aria-live="polite">{pendingSearch ? "Searching…" : `${activeIndex + 1} of ${matches.length}`}</span>
            <button type="button" aria-label="Previous match" title="Previous match (Shift+Enter)" disabled={!matches.length || pendingSearch} onClick={() => navigate(-1)}><ChevronUp size={14} /></button>
            <button type="button" aria-label="Next match" title="Next match (Enter)" disabled={!matches.length || pendingSearch} onClick={() => navigate(1)}><ChevronDown size={14} /></button>
          </> : null}
          {query ? <button type="button" aria-label="Clear diff search" title="Clear search" onClick={() => { setQuery(""); searchInputRef.current?.focus(); }}><X size={14} /></button> : null}
        </div>
        <div className="commit-diff-filters">
          <label className={`commit-diff-filter ${include.trim() ? "is-active" : ""}`}>
            <span>Include</span>
            <input aria-label="Files to include" aria-describedby={filterHintId} placeholder="All files, e.g. src/**, *.py" value={include} onChange={event => setInclude(event.target.value)} onBlur={applyPathFilters} onKeyDown={onFilterKeyDown} spellCheck={false} />
          </label>
          <label className={`commit-diff-filter ${exclude.trim() ? "is-active" : ""}`}>
            <span>Exclude</span>
            <input aria-label="Files to exclude" aria-describedby={filterHintId} placeholder="e.g. tests/**, *.lock" value={exclude} onChange={event => setExclude(event.target.value)} onBlur={applyPathFilters} onKeyDown={onFilterKeyDown} spellCheck={false} />
          </label>
        </div>
        <div className="commit-diff-filter-summary">
          <span role="status" className="commit-diff-count"><ListFilter size={13} aria-hidden="true" />{result ? pendingFilters ? "Updating files…" : filtering ? `${visibleFiles.length} of ${files.length} files` : `${files.length} changed ${files.length === 1 ? "file" : "files"}` : "Loading files…"}</span>
          <span id={filterHintId} className="commit-diff-filter-hint">Separate patterns with commas. Use *, ** or ?.</span>
          <button type="button" disabled={!filtering} onClick={resetFilters} className="commit-diff-reset"><RotateCcw size={12} aria-hidden="true" />Reset</button>
        </div>
      </div>
    </header>
    {error ? <div role="alert" className="p-4 text-xs text-red-700 dark:text-red-300"><p className="whitespace-pre-wrap">{error}</p><button type="button" className="mt-3 rounded border border-line px-3 py-1 text-ink" onClick={() => setRevision(value => value + 1)}>Retry commit diff</button></div>
      : !result ? <p role="status" className="p-4 text-xs text-muted">Loading commit diff…</p>
      : !result.files?.length ? <p className="p-4 text-xs text-muted">{payload ? "No changes on this branch." : "No changes in this commit."}</p>
      : <div className="relative flex min-h-0 flex-1">
      <div ref={scrollRef} aria-label="Changed files" aria-busy={pendingFilters || query !== deferredQuery} tabIndex={0} className="commit-diff-scroll min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden">
        <div ref={contentRef}>
        {!visibleFiles.length ? <div className="commit-diff-empty"><Search size={22} aria-hidden="true" className="text-muted" /><h3>No files match</h3><p>Try different search text or adjust the file patterns.</p><button type="button" onClick={resetFilters}>Clear search and filters</button></div> : null}
        <CommitFileList files={visibleFiles} theme={theme} editorSettings={editorSettings} query={deferredQuery} matchIndex={matchIndex} registry={registry} commentsByPath={onComment ? commentsByPath : null} onComment={onComment} onRemoveComment={onRemoveComment} />
        </div>
      </div>
      <MatchOverview matches={matches} activeMatch={activeMatch} registry={registry} scrollRef={scrollRef} contentRef={contentRef} />
      </div>}
  </section>;
}
