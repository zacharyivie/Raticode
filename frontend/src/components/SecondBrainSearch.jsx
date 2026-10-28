import { useEffect, useMemo, useRef, useState } from "react";
import { BookmarkPlus, ChevronDown, Copy, FilePlus2, FolderOpen, Library, ListFilter, MessageSquareQuote, PanelLeft, Plus, Search, SearchX, Sparkles, Star, TriangleAlert, Upload, X, Scale, ScrollText } from "lucide-react";
import { DATE_FIELDS, DOCUMENT_TYPES, EMPTY_FILTERS, MATCH_LABELS, MATCH_MODES, ORIGINS, READING_STATES, EXTRACTION_STATES, CITATION_FILTERS, SEARCH_IN, SEARCH_MODES, SORTS, activeFilterCount, describeQuery, facetCounts, formatDay, parseQuery, queryText, searchLibrary, typeLabel, dateFieldLabel, readingLabel, originLabel, extractionLabel, citationFilterLabel, parseDateBound, matchesFilters, highlightSegments } from "../lib/secondBrain.js";
import { useSecondBrain, viewFor, libraryFor, updateSearch, resetSearch, openDocument, toggleSelection, clearSelection, openDialog, addToCollection, evidenceItem, notify, createCollection, navigate, isSampleLibrary } from "../lib/secondBrainStore.js";
import { Chip, DraftInput, Empty, Menu, Pill, Popover, Segmented, TypeIcon, Highlight, BrainMark, askRem } from "./SecondBrainParts.jsx";
import { brainRequest } from "../lib/secondBrainApi.js";
import SecondBrainReader from "./SecondBrainReader.jsx";

const toggle = (list = [], value) => list.includes(value) ? list.filter(item => item !== value) : [...list, value];

function CheckList({ label, options, values, onChange, counts }) {
  return <fieldset className="sb-checklist"><legend>{label}</legend>
    {options.map(([id, text]) => <label key={id}><input type="checkbox" checked={values.includes(id)} onChange={() => onChange(toggle(values, id))} /><span>{text}</span>{counts ? <small>{counts.get(id) || 0}</small> : null}</label>)}
  </fieldset>;
}

function FilterButton({ label, value, children, wide }) {
  return <Popover label={`${label} filter`} title={label} triggerClassName={`sb-filter-button ${value ? "is-active" : ""}`} className={wide ? "sb-filter-popover is-wide" : "sb-filter-popover"} trigger={<><span>{label}</span>{value ? <strong>{value}</strong> : null}<ChevronDown size={13} aria-hidden="true" /></>}>
    {children}
  </Popover>;
}

function DateFilter({ filters, set }) {
  const presets = [["Last 7 days", 7], ["Last 30 days", 30], ["Last 12 months", 365]];
  const invalidFrom = filters.from && parseDateBound(filters.from) === null;
  const invalidTo = filters.to && parseDateBound(filters.to, true) === null;
  return <div className="sb-date-filter">
    <label className="sb-field"><span>Date</span><select value={filters.dateField} onChange={event => set({ dateField: event.target.value })}>{DATE_FIELDS.map(([id, text]) => <option key={id} value={id}>{text}</option>)}</select></label>
    <div className="sb-date-range">
      <label className="sb-field"><span>From</span><DraftInput label="From date" placeholder="2026 or 2026-03-01" value={filters.from} onCommit={from => set({ from })} aria-invalid={invalidFrom || undefined} /></label>
      <label className="sb-field"><span>To</span><DraftInput label="To date" placeholder="2026-12" value={filters.to} onCommit={to => set({ to })} aria-invalid={invalidTo || undefined} /></label>
    </div>
    {invalidFrom || invalidTo ? <p className="sb-field-error" role="alert">Use YYYY, YYYY-MM or YYYY-MM-DD. The range is not applied until it is valid.</p> : null}
    <div className="sb-presets">{presets.map(([text, days]) => <button key={text} type="button" onClick={() => set({ from: new Date(Date.now() - days * 86400000).toISOString().slice(0, 10), to: "" })}>{text}</button>)}<button type="button" onClick={() => set({ from: `${new Date().getFullYear()}`, to: "" })}>This year</button></div>
    <label className="sb-check"><input type="checkbox" checked={filters.includeUnknownDates} onChange={event => set({ includeUnknownDates: event.target.checked })} />Include documents with an unknown {dateFieldLabel(filters.dateField).toLowerCase()} date</label>
    {filters.dateField === "published" ? <p className="sb-hint">Publication dates come from document metadata. Raticode never substitutes the file’s modified time.</p> : null}
    {filters.from || filters.to ? <button type="button" className="sb-link" onClick={() => set({ from: "", to: "" })}>Clear dates</button> : null}
  </div>;
}

function QueryBar({ brain, store, search, library, brainNames }) {
  const [draft, setDraft] = useState(search.text);
  const timer = useRef(null);
  const input = useRef(null);
  const lastPushed = useRef(search.text);
  useEffect(() => { if (search.text !== lastPushed.current) { setDraft(search.text); lastPushed.current = search.text; } }, [search.text]);
  useEffect(() => () => clearTimeout(timer.current), []);
  const push = text => { clearTimeout(timer.current); lastPushed.current = text; updateSearch({ text }); };
  const onType = text => { setDraft(text); clearTimeout(timer.current); timer.current = setTimeout(() => push(text), 180); };
  const filters = { ...EMPTY_FILTERS, ...search.filters };
  const set = patch => updateSearch({ filters: { ...filters, ...patch } });
  const parsed = useMemo(() => parseQuery(draft), [draft]);
  const typeCounts = useMemo(() => facetCounts(library.documents, "types", doc => doc.type, { filters: { ...EMPTY_FILTERS, ...search.filters }, text: search.text }, library), [library, search]);
  const tagCounts = useMemo(() => facetCounts(library.documents, "tags", doc => doc.tags, { filters: { ...EMPTY_FILTERS, ...search.filters }, text: search.text }, library), [library, search]);
  const [tagQuery, setTagQuery] = useState("");
  const allTags = [...tagCounts.keys()].concat(filters.tags.filter(tag => !tagCounts.has(tag))).sort();
  const authors = [...new Set(library.documents.map(doc => doc.author).filter(Boolean))].sort();
  const domains = [...new Set(library.documents.map(doc => doc.source?.domain).filter(Boolean))].sort();
  const projects = [...new Set(library.documents.map(doc => doc.project).filter(Boolean))].sort();
  const folders = [...new Set(library.documents.map(doc => doc.folder))].sort();
  const collections = store.collections.filter(item => item.brainId === brain.id);
  const moreCount = filters.authors.length + filters.domains.length + filters.projects.length + filters.reading.length + filters.origins.length + filters.extraction.length + filters.citations.length + (filters.includeGenerated === false ? 1 : 0);
  const scopeLabel = filters.brains.length > 1 ? `${filters.brains.length} brains` : filters.folder ? filters.folder : filters.collection ? store.collections.find(item => item.id === filters.collection)?.name : "";
  const chips = [];
  for (const id of filters.types) chips.push([`Type: ${typeLabel(id)}`, () => set({ types: toggle(filters.types, id) })]);
  for (const tag of filters.tags) chips.push([`Tag: ${tag}`, () => set({ tags: toggle(filters.tags, tag) })]);
  if (filters.from || filters.to) chips.push([`${dateFieldLabel(filters.dateField)}: ${filters.from || "…"} – ${filters.to || "now"}`, () => set({ from: "", to: "" })]);
  if (filters.folder) chips.push([`Folder: ${filters.folder}`, () => set({ folder: "" })]);
  if (filters.collection) chips.push([`Collection: ${store.collections.find(item => item.id === filters.collection)?.name || filters.collection}`, () => set({ collection: "" })]);
  if (filters.brains.length) chips.push([`Brains: ${filters.brains.map(id => brainNames[id]).join(", ")}`, () => set({ brains: [] })]);
  for (const value of filters.authors) chips.push([`Author: ${value}`, () => set({ authors: toggle(filters.authors, value) })]);
  for (const value of filters.domains) chips.push([`Source: ${value}`, () => set({ domains: toggle(filters.domains, value) })]);
  for (const value of filters.projects) chips.push([`Project: ${value}`, () => set({ projects: toggle(filters.projects, value) })]);
  for (const value of filters.reading) chips.push([readingLabel(value), () => set({ reading: toggle(filters.reading, value) })]);
  for (const value of filters.origins) chips.push([`Origin: ${originLabel(value)}`, () => set({ origins: toggle(filters.origins, value) })]);
  for (const value of filters.extraction) chips.push([`Extraction: ${extractionLabel(value)}`, () => set({ extraction: toggle(filters.extraction, value) })]);
  for (const value of filters.citations) chips.push([citationFilterLabel(value), () => set({ citations: toggle(filters.citations, value) })]);
  if (filters.includeGenerated === false) chips.push(["Excluding generated", () => set({ includeGenerated: true })]);
  const equivalent = queryText(search.text, filters);
  const description = describeQuery({ ...search, filters, brainName: brain.name, brainNames });
  // A plain word search explains itself; describe the query only when operators, fields or filters shape it.
  const explain = Boolean(equivalent) && (equivalent !== search.text.trim() || /["()]|\bOR\b|(^|\s)-\S|\w:/.test(search.text));
  const removeClause = clause => { const next = `${draft.slice(0, clause.start)}${draft.slice(clause.end)}`.replace(/\s{2,}/g, " ").trim(); setDraft(next); push(next); input.current?.focus(); };
  return <div className="sb-query">
    <div className="sb-query-row">
      <form role="search" className="sb-query-input" data-invalid={parsed.errors.length ? true : undefined} onSubmit={event => { event.preventDefault(); push(draft); }}>
        <Search size={16} aria-hidden="true" />
        <input ref={input} aria-label={`Search ${brain.name}`} aria-describedby={explain ? "sb-query-explain" : undefined} placeholder={'Search titles, passages and citations — try "event journal" tag:architecture'} value={draft} onChange={event => onType(event.target.value)} spellCheck={false} />
        {draft ? <button type="button" className="sb-icon-button" aria-label="Clear search text" title="Clear search text" onClick={() => { setDraft(""); push(""); input.current?.focus(); }}><X size={14} /></button> : null}
        <button type="button" className="sb-icon-button" aria-label="Save search" title="Save search" onClick={() => openDialog("save-search")}><BookmarkPlus size={15} /></button>
      </form>
      <Segmented label="Match mode" value={search.mode} options={SEARCH_MODES} onChange={mode => updateSearch({ mode })} />
    </div>
    {search.mode !== "exact" && !brain.settings.semantic ? <p className="sb-inline-note"><Sparkles size={13} aria-hidden="true" />Related-meaning search is off for this brain, so results use exact matching. <button type="button" className="sb-link" onClick={() => navigate("settings")}>Turn it on in brain settings</button></p> : null}
    <div className="sb-filter-row" aria-label="Filters">
      <FilterButton label="Scope" value={scopeLabel} wide>
        <fieldset className="sb-checklist"><legend>Brains</legend>
          {store.brains.map(item => <label key={item.id} data-disabled={item.state !== "available" || undefined}><input type="checkbox" disabled={item.state !== "available"} checked={filters.brains.length ? filters.brains.includes(item.id) : item.id === brain.id} onChange={() => { const base = filters.brains.length ? filters.brains : [brain.id]; const next = toggle(base, item.id); set({ brains: next.length === 1 && next[0] === brain.id ? [] : next }); }} /><BrainMark brain={item} size="sm" /><span>{item.name}</span>{item.state !== "available" ? <small>Unavailable</small> : null}</label>)}
          <button type="button" className="sb-link" onClick={() => set({ brains: store.brains.filter(item => item.state === "available").map(item => item.id) })}>Search all opened brains</button>
        </fieldset>
        <label className="sb-field"><span>Folder</span><select value={filters.folder} onChange={event => set({ folder: event.target.value })}><option value="">Whole brain</option>{folders.map(folder => <option key={folder} value={folder}>{folder}</option>)}</select></label>
        <label className="sb-check"><input type="checkbox" checked={filters.subfolders} onChange={event => set({ subfolders: event.target.checked })} />Include subfolders</label>
        <label className="sb-field"><span>Collection</span><select value={filters.collection} onChange={event => set({ collection: event.target.value })}><option value="">Any collection</option>{collections.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      </FilterButton>
      <FilterButton label="Type" value={filters.types.length ? filters.types.map(typeLabel).join(", ") : ""}>
        <CheckList label="Document type" options={DOCUMENT_TYPES} values={filters.types} counts={typeCounts} onChange={types => set({ types })} />
      </FilterButton>
      <FilterButton label="Tags" value={filters.tags.length ? filters.tags.join(filters.requireAllTags ? " + " : ", ") : ""}>
        <input className="sb-popover-search" aria-label="Find a tag" placeholder="Find a tag" value={tagQuery} onChange={event => setTagQuery(event.target.value)} />
        <CheckList label="Tags" options={allTags.filter(tag => tag.includes(tagQuery.toLowerCase())).map(tag => [tag, tag])} values={filters.tags} counts={tagCounts} onChange={tags => set({ tags })} />
        <label className="sb-check"><input type="checkbox" checked={filters.requireAllTags} onChange={event => set({ requireAllTags: event.target.checked })} />Require all selected tags</label>
      </FilterButton>
      <FilterButton label="Date" value={filters.from || filters.to ? `${dateFieldLabel(filters.dateField)} ${filters.from || "…"}–${filters.to || "now"}` : ""}>
        <DateFilter filters={filters} set={set} />
      </FilterButton>
      <FilterButton label="Search in" value={search.searchIn !== "all" || search.match !== "all" ? [search.searchIn !== "all" ? SEARCH_IN.find(([id]) => id === search.searchIn)[1] : "", search.match !== "all" ? MATCH_MODES.find(([id]) => id === search.match)[1] : ""].filter(Boolean).join(" · ") : ""}>
        <fieldset className="sb-checklist"><legend>Search in</legend>{SEARCH_IN.map(([id, text]) => <label key={id}><input type="radio" name="sb-search-in" checked={search.searchIn === id} onChange={() => updateSearch({ searchIn: id })} /><span>{text}</span></label>)}</fieldset>
        <fieldset className="sb-checklist"><legend>Words</legend>{MATCH_MODES.map(([id, text]) => <label key={id}><input type="radio" name="sb-match" checked={search.match === id} onChange={() => updateSearch({ match: id })} /><span>{text}</span></label>)}</fieldset>
      </FilterButton>
      <FilterButton label="More filters" value={moreCount ? String(moreCount) : ""} wide>
        <div className="sb-more-filters">
          <CheckList label="Reading state" options={READING_STATES} values={filters.reading} onChange={reading => set({ reading })} />
          <CheckList label="Citations" options={CITATION_FILTERS} values={filters.citations} onChange={citations => set({ citations })} />
          <CheckList label="Origin" options={ORIGINS} values={filters.origins} onChange={origins => set({ origins })} />
          <CheckList label="Extraction" options={EXTRACTION_STATES} values={filters.extraction} onChange={extraction => set({ extraction })} />
          {authors.length ? <CheckList label="Author" options={authors.map(item => [item, item])} values={filters.authors} onChange={values => set({ authors: values })} /> : null}
          {domains.length ? <CheckList label="Source domain" options={domains.map(item => [item, item])} values={filters.domains} onChange={values => set({ domains: values })} /> : null}
          {projects.length ? <CheckList label="Project" options={projects.map(item => [item, item])} values={filters.projects} onChange={values => set({ projects: values })} /> : null}
          <label className="sb-check"><input type="checkbox" checked={filters.includeGenerated !== false} onChange={event => set({ includeGenerated: event.target.checked })} />Include generated reports and summaries</label>
        </div>
      </FilterButton>
      {activeFilterCount(filters) || search.text || search.searchIn !== "all" || search.match !== "all" ? <button type="button" className="sb-link sb-clear-all" onClick={() => { setDraft(""); lastPushed.current = ""; resetSearch(); }}>Clear all</button> : null}
    </div>
    {chips.length || parsed.clauses.length ? <div className="sb-chips" aria-label="Active filters">
      {chips.map(([text, remove]) => <Chip key={text} onRemove={remove} removeLabel={`Remove ${text}`}>{text}</Chip>)}
      {parsed.clauses.map(clause => <Chip key={`${clause.start}`} onRemove={() => removeClause(clause)} removeLabel={`Remove ${draft.slice(clause.start, clause.end)} from the query`}><code>{draft.slice(clause.start, clause.end)}</code><small>from query</small></Chip>)}
    </div> : null}
    {parsed.errors.length ? <ul className="sb-query-errors" role="alert">{parsed.errors.map(error => <li key={error.start}><TriangleAlert size={13} aria-hidden="true" /><code>{error.text}</code><span>Not applied. {error.message}</span></li>)}</ul> : null}
    {explain ? <div className="sb-query-explain" id="sb-query-explain">
      <p>{description}</p>
      {equivalent ? <button type="button" className="sb-equivalent" title="Copy as text query" onClick={() => { navigator.clipboard?.writeText(equivalent); notify("Copied the text form of this search."); }}><code>{equivalent}</code><Copy size={12} aria-hidden="true" /></button> : null}
    </div> : null}
  </div>;
}

function NoResults({ brain, brains, library, search, onClear }) {
  const otherBrains = brains.filter(item => item.id !== brain.id && item.state === "available");
  const filters = { ...EMPTY_FILTERS, ...search.filters };
  const filtered = activeFilterCount(filters) > 0;
  const withoutFilters = filtered ? searchLibrary(library.documents, { ...search, filters: EMPTY_FILTERS }, library).results.length : 0;
  const pending = library.documents.filter(doc => doc.extraction !== "ready").length;
  if (!library.documents.length) return <Empty icon={Library} title="This brain has no documents yet" actions={<><button className="sb-button" type="button" onClick={() => window.goferDesktop?.workspace?.openPath?.(brain.root)}><FolderOpen size={14} />Open folder</button><button className="sb-button" type="button" onClick={() => openDialog("import")}><Upload size={14} />Import package</button></>}>Add Markdown, HTML, text or PDF files to <code className="sb-mono">{brain.root}</code>. Raticode indexes them automatically.</Empty>;
  const filtersAreTheCause = filtered && (withoutFilters > 0 || !search.text.trim());
  return <Empty icon={SearchX} title={filtersAreTheCause ? "No documents match these filters" : `No matches for “${search.text}”`} actions={<>
    {filtered ? <button className="sb-button" type="button" onClick={onClear}>Clear filters{withoutFilters ? ` · ${withoutFilters} match without them` : ""}</button> : null}
    {search.mode === "exact" && brain.settings.semantic ? <button className="sb-button" type="button" onClick={() => updateSearch({ mode: "hybrid" })}><Sparkles size={14} />Include related meaning</button> : null}
    {!filters.brains.length && otherBrains.length ? <button className="sb-button" type="button" onClick={() => updateSearch({ filters: { ...filters, brains: [brain.id, ...otherBrains.map(item => item.id)] } })}>Search all opened brains</button> : null}
  </>}>
    {filtersAreTheCause ? "Each filter family narrows the results. Remove a chip above or clear the filters to widen the search." : "Check the spelling, try fewer words, or search titles and content instead of a single field."}
    {pending ? ` ${pending} document${pending === 1 ? " is" : "s are"} still being extracted or need attention, so ${pending === 1 ? "it" : "they"} may match later.` : ""}
  </Empty>;
}

function ResultItem({ result, active, selected, multiBrain, brainOf, collections, onActivate, onOpen, onKeyDown, id }) {
  const { doc } = result;
  const sourceBrain = brainOf(doc.brainId);
  return <li id={id} role="option" aria-selected={active} className="sb-result" data-active={active || undefined} data-selected={selected || undefined} tabIndex={active ? 0 : -1} onKeyDown={onKeyDown}
    onClick={event => { if (!event.target.closest("button, input, a")) onActivate(); }} onDoubleClick={event => { if (!event.target.closest("button, input")) onOpen(); }}>
    <input type="checkbox" aria-label={`Select ${doc.title}`} checked={selected} onChange={() => toggleSelection(doc.id)} tabIndex={-1} />
    <div className="sb-result-main">
      <div className="sb-result-head">
        <TypeIcon type={doc.type} />
        <button type="button" className="sb-result-title" tabIndex={-1} onClick={onActivate}>{doc.title}</button>
        {doc.reading?.starred ? <Star size={12} className="sb-star" aria-label="Starred" /> : null}
        {!doc.reading?.read ? <span className="sb-unread" aria-label="Unread" title="Unread" /> : null}
        {result.matchKind !== "filter" ? <span className="sb-match" data-kind={result.matchKind}>{MATCH_LABELS[result.matchKind]}</span> : null}
      </div>
      <div className="sb-result-path sb-mono">{multiBrain ? <span className="sb-result-brain"><BrainMark brain={sourceBrain} size="sm" />{sourceBrain?.name}</span> : null}{doc.path}</div>
      <div className="sb-result-meta">
        <span>{typeLabel(doc.type)}</span>
        {doc.author ? <span>{doc.author}</span> : null}
        {doc.source?.domain ? <span>{doc.source.domain}</span> : null}
        <span>Modified {formatDay(doc.dates.modified, { relative: true })}</span>
        {doc.dates.published ? <span>Published {formatDay(doc.dates.published)}</span> : null}
        {doc.extraction !== "ready" ? <Pill tone={doc.extraction === "failed" ? "danger" : "warning"}>{extractionLabel(doc.extraction)}</Pill> : null}
        {doc.origin === "generated" ? <span>Generated</span> : null}
      </div>
      {result.matchKind !== "filter" ? <div className="sb-passages">
        {result.passages.map(({ block, segments }) => <button key={block.id} type="button" tabIndex={-1} className="sb-passage" onClick={() => openDocument(doc.id, { blockId: block.id })}>
          <small>{doc.type === "pdf" && block.page ? `Page ${block.page} · ` : ""}{block.heading}</small>
          <span className="sb-passage-text"><Highlight segments={segments} /></span>
        </button>)}
        {result.matchKind === "meaning" ? <p className="sb-why">Matched by the embedding model’s similarity to your question. Review the passage for relevance.</p> : null}
        {result.passageCount > result.passages.length ? <button type="button" tabIndex={-1} className="sb-link" onClick={onOpen}>{result.passageCount - result.passages.length} more matching passage{result.passageCount - result.passages.length === 1 ? "" : "s"}</button> : null}
      </div> : <p className="sb-result-snippet">{doc.blocks[0]?.text}</p>}
      <div className="sb-result-actions">
        <Menu label={`Add ${doc.title} to a collection`} triggerClassName="sb-mini-button" align="start" trigger={<><Plus size={13} />Add to collection</>} items={[
          ...collections.map(item => ({ label: item.name, icon: Library, onSelect: () => addToCollection(item.id, evidenceItem(doc.id)) })),
          { separator: true, key: "sep" },
          { label: "New collection…", icon: Plus, onSelect: () => openDialog("new-collection", { items: [evidenceItem(doc.id)] }) },
        ]} />
        <button type="button" tabIndex={-1} className="sb-mini-button" onClick={onOpen}><FilePlus2 size={13} />Open in tab</button>
      </div>
    </div>
  </li>;
}

export default function SearchView({ brain }) {
  const store = useSecondBrain();
  const view = viewFor(store);
  const search = view.search;
  const filters = { ...EMPTY_FILTERS, ...search.filters };
  const brainNames = Object.fromEntries(store.brains.map(item => [item.id, item.name]));
  const typedBrains = parseQuery(search.text).filters.brains || [];
  const selectedBrains = [...filters.brains, ...typedBrains];
  const scopeBrains = selectedBrains.length ? store.brains.filter(item => selectedBrains.some(value => value.toLowerCase() === item.id.toLowerCase() || value.toLowerCase() === item.name.toLowerCase())).map(item => item.id) : [brain.id];
  const scopeKey = scopeBrains.join(",");
  const multiBrain = scopeBrains.length > 1;
  const library = useMemo(() => libraryFor(store, brain.id), [store, brain.id]);
  const scopedLibrary = useMemo(() => {
    const ids = scopeKey.split(",");
    if (ids.length === 1 && ids[0] === brain.id) return library;
    const merged = ids.map(id => libraryFor(store, id));
    return { documents: merged.flatMap(item => item.documents), citedBy: Object.assign({}, ...merged.map(item => item.citedBy)), collectionMembers: library.collectionMembers, citationsByDoc: Object.assign({}, ...merged.map(item => item.citationsByDoc)), reportCitations: Object.assign({}, ...merged.map(item => item.reportCitations)), brainNames: library.brainNames };
  }, [store, scopeKey, library, brain.id]);
  const [semantic, setSemantic] = useState({ hits: [], error: "", key: "" });
  const searchKey = JSON.stringify([scopeKey, search.text, search.mode]);
  const generationKey = store.brains.filter(item => scopeBrains.includes(item.id)).map(item => item.index.generation).join(",");
  const meaningEnabled = store.brains.some(item => scopeBrains.includes(item.id) && item.settings.semantic);
  useEffect(() => {
    let active = true;
    if (search.mode === "exact" || !meaningEnabled || !search.text.trim() || isSampleLibrary()) return undefined;
    const parsed = parseQuery(search.text);
    const timer = setTimeout(() => brainRequest("search", { brainId: brain.id, brainIds: scopeKey.split(","), query: [...parsed.terms, ...parsed.phrases].join(" "), mode: search.mode }).then(result => {
      if (active) setSemantic({ hits: result.hits, error: "", key: searchKey });
    }).catch(error => { if (active) setSemantic({ hits: [], error: error.message, key: searchKey }); }), 180);
    return () => { active = false; clearTimeout(timer); };
  }, [brain.id, scopeKey, search.text, search.mode, searchKey, generationKey, meaningEnabled]);
  const { results } = useMemo(() => {
    const exact = searchLibrary(scopedLibrary.documents, { ...search, mode: isSampleLibrary() ? search.mode : "exact", filters: { ...EMPTY_FILTERS, ...search.filters } }, scopedLibrary);
    if (isSampleLibrary() || !meaningEnabled || search.mode === "exact" || semantic.key !== searchKey || semantic.error) return exact;
    const seen = new Set(search.mode === "hybrid" ? exact.results.map(item => item.doc.id) : []);
    const related = [];
    for (const hit of semantic.hits) {
      const doc = scopedLibrary.documents.find(item => item.id === hit.docId);
      if (!doc || seen.has(doc.id) || !matchesFilters(doc, search.filters, exact.parsed, scopedLibrary)) continue;
      const text = `${doc.title} ${doc.blocks.map(block => block.text).join(" ")}`.toLowerCase();
      if (exact.parsed.exclude.some(word => text.includes(word)) || exact.parsed.phrases.some(phrase => !text.includes(phrase))) continue;
      const block = doc.blocks.find(item => item.id === hit.blockId);
      if (!block) continue;
      seen.add(doc.id);
      related.push({ doc, score: hit.score, matchKind: "meaning", passageCount: 1, passages: [{ block, segments: highlightSegments(block.text, []) }] });
    }
    return { ...exact, results: search.mode === "semantic" ? related : [...exact.results, ...related] };
  }, [scopedLibrary, search, semantic, searchKey, meaningEnabled]);

  const passageTotal = results.reduce((total, result) => total + result.passageCount, 0);
  const [activeIndex, setActiveIndex] = useState(0);
  const [pane, setPane] = useState("results");
  const [resultsHidden, setResultsHidden] = useState(false);
  const listRef = useRef(null);
  useEffect(() => { setActiveIndex(0); }, [search]);
  const focusRequest = view.focus?.request;
  useEffect(() => { if (focusRequest) setPane("reader"); }, [focusRequest]);
  const collections = store.collections.filter(item => item.brainId === brain.id);
  const selection = view.selection.filter(id => store.documents.some(doc => doc.id === id));
  const selectedDocs = selection.map(id => store.documents.find(doc => doc.id === id));
  const activate = (index, preview = true) => { const result = results[index]; if (!result) return; setActiveIndex(index); openDocument(result.doc.id, { blockId: result.passages[0]?.block.id && result.matchKind !== "filter" ? result.passages[0].block.id : "", preview }); };
  const onKeyDown = (event, index) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next = Math.max(0, Math.min(results.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)));
      setActiveIndex(next);
      listRef.current?.querySelectorAll(".sb-result")[next]?.focus();
    } else if (event.key === "Enter") { event.preventDefault(); activate(index, false); }
    else if (event.key === " ") { event.preventDefault(); toggleSelection(results[index].doc.id); }
    else if (event.key.toLowerCase() === "p" && !event.metaKey && !event.ctrlKey) { event.preventDefault(); activate(index, true); }
  };
  const skipped = scopeBrains.map(id => store.brains.find(item => item.id === id)).filter(item => item && item.state !== "available");
  const grouped = multiBrain ? scopeBrains.map(id => [id, results.filter(result => result.doc.brainId === id)]).filter(([, list]) => list.length) : [[brain.id, results]];
  let position = -1;
  return <div className="sb-search" data-pane={pane}>
    {semantic.error && semantic.key === searchKey ? <p role="alert" className="sb-inline-note" data-tone="warning">{semantic.error}</p> : null}
    <QueryBar brain={brain} store={store} search={search} library={library} brainNames={brainNames} />
    <div className="sb-pane-switch" role="tablist" aria-label="Results and reader">
      <button type="button" role="tab" aria-selected={pane === "results"} onClick={() => setPane("results")}>Results · {results.length}</button>
      <button type="button" role="tab" aria-selected={pane === "reader"} onClick={() => setPane("reader")}>Reader{view.tabs.length ? ` · ${view.tabs.length}` : ""}</button>
    </div>
    <div className="sb-split" data-results-hidden={resultsHidden || undefined}>
      <section className="sb-results" aria-label="Search results">
        <div className="sb-results-head">
          <p><strong>{results.length} document{results.length === 1 ? "" : "s"}</strong>{passageTotal ? ` · ${passageTotal} passage${passageTotal === 1 ? "" : "s"}` : ""}{brain.index.state === "updating" ? <span className="sb-asof" title={`Index updating. ${brain.index.pending} documents are waiting for extraction.`}> · as of {new Date(brain.index.asOf).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span> : null}</p>
          <label className="sb-sort"><ListFilter size={13} aria-hidden="true" /><span className="sb-sr-only">Sort by</span><select aria-label="Sort results" value={search.sort} onChange={event => updateSearch({ sort: event.target.value })}>{SORTS.map(([id, text]) => <option key={id} value={id}>{text}</option>)}</select></label>
          <button type="button" className="sb-icon-button sb-hide-results" aria-label="Hide results" title="Hide results" onClick={() => setResultsHidden(true)}><PanelLeft size={15} /></button>
        </div>
        {skipped.length ? <p className="sb-inline-note"><TriangleAlert size={13} aria-hidden="true" />{skipped.map(item => item.name).join(", ")} {skipped.length === 1 ? "is" : "are"} unavailable and {skipped.length === 1 ? "was" : "were"} not searched.</p> : null}
        {selection.length ? <div className="sb-bulk" role="region" aria-label="Selected documents">
          <strong>{selection.length} selected</strong>
          <Menu label="Add selected to a collection" triggerClassName="sb-mini-button" align="start" trigger={<><Plus size={13} />Collection</>} items={[
            ...collections.map(item => ({ label: item.name, icon: Library, onSelect: () => { addToCollection(item.id, selection.map(id => evidenceItem(id))); clearSelection(); } })),
            { separator: true, key: "sep" },
            { label: "New collection…", icon: Plus, onSelect: () => openDialog("new-collection", { items: selection.map(id => evidenceItem(id)) }) },
          ]} />
          <button type="button" className="sb-mini-button" onClick={() => openDialog("summarize", { docIds: selection })}><Sparkles size={13} />Summarize</button>
          <button type="button" className="sb-mini-button" disabled={selection.length < 2} title={selection.length < 2 ? "Select at least two documents" : undefined} onClick={() => { const collection = createCollection(`Comparison · ${formatDay(new Date().toISOString())}`, selection.map(id => evidenceItem(id))); clearSelection(); navigate("collections", { collectionId: collection.id, collectionTab: "compare" }); }}><Scale size={13} />Compare</button>
          <button type="button" className="sb-mini-button" onClick={() => openDialog("report-builder", { sources: selection.map(id => ({ docId: id })) })}><ScrollText size={13} />Cited report</button>
          <button type="button" className="sb-mini-button" onClick={() => askRem({ brain, doc: selectedDocs[0], passages: selectedDocs.map(doc => ({ doc })), draft: "", title: `${selectedDocs.length} documents from ${brain.name}` })}><MessageSquareQuote size={13} />Ask Rem</button>
          <button type="button" className="sb-icon-button" aria-label="Clear selection" title="Clear selection" onClick={clearSelection}><X size={14} /></button>
        </div> : null}
        <div className="sb-results-scroll">
          {results.length ? <ul ref={listRef} role="listbox" aria-label="Matching documents" aria-multiselectable="true">
            {grouped.map(([brainId, list]) => <li key={brainId} role="presentation" className="sb-result-group">
              {multiBrain ? <h4><BrainMark brain={store.brains.find(item => item.id === brainId)} size="sm" />{brainNames[brainId]} <small>{list.length}</small></h4> : null}
              <ul role="presentation">{list.map(result => { position += 1; const index = position; return <ResultItem key={result.doc.id} id={`sb-result-${index}`} result={result} active={index === activeIndex} selected={selection.includes(result.doc.id)} multiBrain={multiBrain}
                brainOf={id => store.brains.find(item => item.id === id)} collections={collections} onActivate={() => activate(index)} onOpen={() => activate(index, false)} onKeyDown={event => onKeyDown(event, index)} />; })}</ul>
            </li>)}
          </ul> : <NoResults brain={brain} brains={store.brains} library={scopedLibrary} search={search} onClear={() => updateSearch({ filters: EMPTY_FILTERS })} />}
        </div>
      </section>
      <SecondBrainReader brain={brain} view={view} store={store} search={search} collections={collections} resultsHidden={resultsHidden} onShowResults={() => setResultsHidden(false)} />
    </div>
  </div>;
}
