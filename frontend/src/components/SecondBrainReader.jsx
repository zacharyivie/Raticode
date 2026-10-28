import { useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, ChevronDown, ChevronUp, Copy, ExternalLink, Eye, EyeOff, FileClock, Highlighter, Library, ListTree, MessageSquareQuote, MoreHorizontal, PanelLeft, Plus, Quote, Search, Sparkles, Star, Trash2, X, ZoomIn, ZoomOut, Link2, CircleCheck } from "lucide-react";
import { extractionLabel, formatCitation, formatDay, formatTime, formatBytes, originLabel, parseQuery, typeLabel } from "../lib/secondBrain.js";
import { activateTab, closeTab, pinTab, openDocument, toggleReading, saveCitation, removeCitation, addToCollection, evidenceItem, openDialog, notify, libraryFor, documentById, navigate, isSampleLibrary } from "../lib/secondBrainStore.js";
import { Empty, Menu, Pill, Segmented, TypeIcon, askRem } from "./SecondBrainParts.jsx";

function ranges(text, needle, kind) {
  const out = [];
  if (!needle) return out;
  const haystack = text.toLowerCase(), target = needle.toLowerCase();
  for (let index = haystack.indexOf(target); index >= 0; index = haystack.indexOf(target, index + target.length)) out.push({ start: index, end: index + target.length, kind });
  return out;
}
// Splits text into spans carrying every decoration that covers them (search hits, find hits, saved citations).
export function decorate(text, marks) {
  const all = marks.flatMap(mark => ranges(text, mark.needle, mark.kind));
  if (!all.length) return [{ text, kinds: [] }];
  const points = [...new Set([0, text.length, ...all.flatMap(range => [range.start, range.end])])].sort((a, b) => a - b);
  return points.slice(0, -1).map((start, index) => ({ text: text.slice(start, points[index + 1]), kinds: all.filter(range => range.start <= start && range.end >= points[index + 1]).map(range => range.kind) })).filter(part => part.text);
}

export function OriginalDocument({ doc }) {
  const [original, setOriginal] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    let objectUrl;
    setOriginal(null); setError("");
    import("../lib/secondBrainApi.js").then(({ brainRequest }) => brainRequest("original", { id: doc.id })).then(result => {
      if (!active) return;
      if (result.mime === "application/pdf") {
        const bytes = Uint8Array.from(atob(result.data), value => value.charCodeAt(0));
        objectUrl = URL.createObjectURL(new Blob([bytes], { type: result.mime }));
        setOriginal({ ...result, url: objectUrl });
      } else setOriginal(result);
    }).catch(reason => { if (active) setError(reason.message); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [doc.id, doc.version]);
  if (error) return <p role="alert">{error}</p>;
  if (!original) return <p role="status">Loading original file…</p>;
  if (original.mime === "application/pdf") return <iframe title={`Original PDF: ${doc.title}`} src={original.url} className="sb-original-frame" referrerPolicy="no-referrer" />;
  if (original.mime === "text/plain") return <pre className="sb-original-text">{original.content}</pre>;
  // This CSP is prepended before any authored markup. Styling stays intact; network and scripts are blocked.
  const policy = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; form-action 'none'; base-uri 'none'">`;
  return <iframe title={`Original: ${doc.title}`} className="sb-original-frame" sandbox="" referrerPolicy="no-referrer" srcDoc={policy + original.content} />;
}

function DocumentDetails({ doc, store }) {
  const [relatedIds, setRelatedIds] = useState(doc.related || []);
  useEffect(() => {
    if (isSampleLibrary() || !store.brains.find(brain => brain.id === doc.brainId)?.settings.semantic) return undefined;
    let active = true;
    import("../lib/secondBrainApi.js").then(({ brainRequest }) => brainRequest("related", { id: doc.id })).then(ids => { if (active) setRelatedIds(ids); }).catch(() => { if (active) setRelatedIds([]); });
    return () => { active = false; };
  }, [doc.id, doc.version, doc.brainId, store.brains]);
  const library = libraryFor(store, doc.brainId);
  const citedBy = (library.citedBy[doc.id] || []).map(id => store.reports.find(report => report.id === id) || documentById(store, id)).filter(Boolean);
  const cites = (doc.cites || []).map(id => documentById(store, id)).filter(Boolean);
  const related = relatedIds.map(id => documentById(store, id)).filter(Boolean);
  const citations = library.citationsByDoc[doc.id] || [];
  const reportsUsing = store.reports.filter(report => report.sourceVersions.some(source => source.docId === doc.id));
  const versions = [{ hash: doc.version, at: doc.dates.modified, note: "Current source" }, ...doc.versions.filter(version => version.hash !== doc.version)];
  const linkTo = item => item.citations ? <button type="button" className="sb-link" onClick={() => navigate("reports", { reportId: item.id })}>{item.title}</button> : <button type="button" className="sb-link" onClick={() => openDocument(item.id)}>{item.title}</button>;
  const facts = [["Path", <code className="sb-mono" key="p">{doc.path}</code>], ["Type", typeLabel(doc.type)], ["Author", doc.author || "Unknown"], ["Project", doc.project || "None"], ["Tags", doc.tags.length ? doc.tags.join(", ") : "None"],
    ["Source", doc.source?.url ? <a key="s" href={doc.source.url} onClick={event => { event.preventDefault(); window.open(doc.source.url, "_blank", "noopener"); }}>{doc.source.domain}</a> : "None"], ["DOI", doc.source?.doi || "None"],
    ["Added", formatDay(doc.dates.added)], ["Modified", formatDay(doc.dates.modified)], ["Published", doc.dates.published ? formatDay(doc.dates.published) : "Unknown"], ...(doc.dates.captured ? [["Captured", formatTime(doc.dates.captured)]] : []),
    ["Origin", originLabel(doc.origin)], ["Extraction", extractionLabel(doc.extraction)], ["Size", formatBytes(doc.size)], ["Document ID", <code className="sb-mono" key="i">{doc.id}</code>]];
  return <div className="sb-details">
    <section><div className="sb-details-head"><h4>Metadata</h4><button type="button" className="sb-mini-button" onClick={() => openDialog("edit-metadata", { docId: doc.id })}>Edit metadata</button></div><dl className="sb-facts">{facts.map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}</dl></section>
    <section><h4>Versions</h4><ol className="sb-versions">{versions.map((version, index) => { const used = reportsUsing.filter(report => report.sourceVersions.some(source => source.docId === doc.id && source.version === version.hash)); return <li key={version.hash + index}>
      <code className="sb-mono">{version.hash}</code><span>{version.note}<small>{formatTime(version.at)}</small></span>
      {index === 0 ? <Pill tone="success">Current</Pill> : <button type="button" className="sb-mini-button" onClick={() => openDialog("version-diff", { docId: doc.id, from: version.hash })}>Compare with current</button>}
      {used.length ? <small className="sb-version-use">Used by {used.map(report => report.title).join(", ")}</small> : null}
    </li>; })}</ol></section>
    <section><h4>Saved citations <small>{citations.length}</small></h4>{citations.length ? <ul className="sb-saved-citations">{citations.map(citation => <li key={citation.id}>
      <button type="button" className="sb-quote" onClick={() => openDocument(doc.id, { blockId: citation.blockId, quote: citation.quote })}>“{citation.quote}”</button>
      <small>{doc.blocks.find(block => block.id === citation.blockId)?.heading} · version {citation.version}{citation.version !== doc.version ? " · source changed since saved" : ""}</small>
      {citation.note ? <p>{citation.note}</p> : null}
      <div><button type="button" className="sb-mini-button" onClick={() => { navigator.clipboard?.writeText(`“${citation.quote}” (${formatCitation(doc, doc.blocks.find(block => block.id === citation.blockId))})`); notify("Citation copied."); }}><Copy size={12} />Copy</button><button type="button" className="sb-mini-button" onClick={() => removeCitation(citation.id)}><Trash2 size={12} />Remove</button></div>
    </li>)}</ul> : <p className="sb-hint">Select text in the reading view and choose Save citation.</p>}</section>
    <section><h4>Relationships</h4>
      <div className="sb-relations">
        <div><h5>Cites</h5>{cites.length ? <ul>{cites.map(item => <li key={item.id}><TypeIcon type={item.type} size={13} />{linkTo(item)}</li>)}</ul> : <p className="sb-hint">No recorded citations.</p>}</div>
        <div><h5>Cited by</h5>{citedBy.length ? <ul>{citedBy.map(item => <li key={item.id}>{item.citations ? <Quote size={13} aria-hidden="true" /> : <TypeIcon type={item.type} size={13} />}{linkTo(item)}</li>)}</ul> : <p className="sb-hint">Nothing cites this document yet.</p>}</div>
        <div><h5>Related documents <Pill tone="brand">Suggested</Pill></h5>{related.length ? <ul>{related.map(item => <li key={item.id}><TypeIcon type={item.type} size={13} />{linkTo(item)}</li>)}</ul> : <p className="sb-hint">No suggestions yet.</p>}<p className="sb-hint">Suggestions come from similar meaning, not recorded links.</p></div>
      </div>
    </section>
  </div>;
}

export default function SecondBrainReader({ brain, view, store, search, collections, resultsHidden, onShowResults }) {
  const doc = view.activeDocId ? documentById(store, view.activeDocId) : null;
  const [mode, setMode] = useState("reading");
  const [toc, setToc] = useState(true);
  const [zoom, setZoom] = useState(100);
  const [find, setFind] = useState({ open: false, text: "", index: 0 });
  const [hitIndex, setHitIndex] = useState(0);
  const [selection, setSelection] = useState(null);
  const [flash, setFlash] = useState("");
  const articleRef = useRef(null);
  const bodyRef = useRef(null);
  const findRef = useRef(null);
  const parsed = useMemo(() => parseQuery(search.text), [search.text]);
  const terms = [...parsed.terms, ...parsed.phrases].filter(term => term.length > 1);
  const library = useMemo(() => libraryFor(store, doc?.brainId || brain.id), [store, doc?.brainId, brain.id]);
  const citations = doc ? library.citationsByDoc[doc.id] || [] : [];
  const focusQuote = doc && view.focus?.docId === doc.id ? view.focus.quote || "" : "";
  const marks = [...terms.map(needle => ({ needle, kind: "hit" })), ...(find.open && find.text.length > 1 ? [{ needle: find.text, kind: "find" }] : []), ...citations.map(citation => ({ needle: citation.quote, kind: "cited" })), ...(focusQuote ? [{ needle: focusQuote, kind: "focus" }] : [])];
  const findTotal = doc && find.open && find.text.length > 1 ? doc.blocks.reduce((total, block) => total + ranges(block.text, find.text, "find").length, 0) : 0;
  const hitTotal = doc ? doc.blocks.reduce((total, block) => total + terms.reduce((sum, term) => sum + ranges(block.text, term, "hit").length, 0), 0) : 0;

  useEffect(() => { setSelection(null); setMode("reading"); setHitIndex(0); }, [doc?.id]);
  const focus = view.focus;
  const handledFocus = useRef(0);
  useEffect(() => {
    if (!focus || focus.docId !== doc?.id || handledFocus.current === focus.request) return;
    handledFocus.current = focus.request;
    setMode("reading");
    requestAnimationFrame(() => {
      const target = focus.blockId ? articleRef.current?.querySelector(`[data-block="${focus.blockId}"]`) : articleRef.current?.querySelector("mark");
      target?.scrollIntoView({ block: "center", behavior: document.documentElement.dataset.reducedMotion === "true" ? "auto" : "smooth" });
      if (focus.blockId) { setFlash(focus.blockId); setTimeout(() => setFlash(""), 1800); }
    });
  }, [focus, doc?.id]);
  useEffect(() => { articleRef.current?.querySelector(`[data-find-index="${find.index}"]`)?.scrollIntoView({ block: "center" }); }, [find.index, find.text]);
  useEffect(() => { if (find.open) findRef.current?.focus(); }, [find.open]);
  const stepHit = delta => { if (!hitTotal) return; const next = (hitIndex + delta + hitTotal) % hitTotal; setHitIndex(next); articleRef.current?.querySelectorAll("mark.sb-hit")[next]?.scrollIntoView({ block: "center" }); };

  function captureSelection() {
    const selected = window.getSelection();
    const text = selected?.toString().trim();
    if (!text || !selected.rangeCount) { setSelection(null); return; }
    const anchor = selected.anchorNode?.parentElement?.closest("[data-block]");
    const focusNode = selected.focusNode?.parentElement?.closest("[data-block]");
    if (!anchor || !articleRef.current?.contains(anchor)) { setSelection(null); return; }
    const rect = selected.getRangeAt(0).getBoundingClientRect();
    const host = bodyRef.current.getBoundingClientRect();
    const range = selected.getRangeAt(0);
    const parts = [...articleRef.current.querySelectorAll("[data-block] p")].filter(paragraph => range.intersectsNode(paragraph)).slice(0, 30).map(paragraph => {
      const clipped = document.createRange(); clipped.selectNodeContents(paragraph);
      if (range.compareBoundaryPoints(Range.START_TO_START, clipped) > 0) clipped.setStart(range.startContainer, range.startOffset);
      if (range.compareBoundaryPoints(Range.END_TO_END, clipped) < 0) clipped.setEnd(range.endContainer, range.endOffset);
      const quote = clipped.toString().trim();
      const blockId = paragraph.closest("[data-block]").dataset.block;
      return quote && doc.blocks.find(block => block.id === blockId)?.text.includes(quote) ? { blockId, quote } : null;
    }).filter(Boolean);
    if (!parts.length) { setSelection(null); return; }
    setSelection({ text: parts.map(part => part.quote).join("\n\n"), parts, blockId: parts[0].blockId, crossesBlocks: focusNode !== anchor, top: rect.top - host.top + bodyRef.current.scrollTop - 44, left: Math.max(8, Math.min(rect.left - host.left + rect.width / 2 - 170, host.width - 350)) });
  }

  function onKeyDown(event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") { event.preventDefault(); event.stopPropagation(); setFind(current => ({ ...current, open: true })); setTimeout(() => findRef.current?.select(), 0); }
  }
  let findCounter = -1;
  return <section className="sb-reader" aria-label="Document reader" onKeyDown={onKeyDown}>
    <div className="sb-reader-tabs" role="tablist" aria-label="Open documents">
      {resultsHidden ? <button type="button" className="sb-icon-button" aria-label="Show results" title="Show results" onClick={onShowResults}><PanelLeft size={15} /></button> : null}
      {view.tabs.map(tab => { const item = documentById(store, tab.docId); if (!item) return null; return <div key={tab.docId} className="sb-reader-tab" data-active={tab.docId === view.activeDocId || undefined} data-preview={tab.preview || undefined}>
        <button type="button" role="tab" aria-selected={tab.docId === view.activeDocId} title={`${item.path}${tab.preview ? " · Preview (double-click to keep open)" : ""}`} onClick={() => activateTab(tab.docId)} onDoubleClick={() => pinTab(tab.docId)}><TypeIcon type={item.type} size={13} /><span>{item.title}</span></button>
        <button type="button" className="sb-tab-close" aria-label={`Close ${item.title}`} title="Close" onClick={() => closeTab(tab.docId)}><X size={12} /></button>
      </div>; })}
    </div>
    {!doc ? <Empty icon={BookOpen} title="Choose a result to read it here">Click a result to preview it at the matching passage. Double-click or press <kbd>Enter</kbd> to keep it open in a tab. Use <kbd>↑</kbd> <kbd>↓</kbd> to move through results and <kbd>Space</kbd> to select.</Empty> : <>
      <header className="sb-reader-head">
        <div className="sb-reader-title">
          <TypeIcon type={doc.type} size={18} />
          <h3>{doc.title}</h3>
          <button type="button" className="sb-icon-button" aria-pressed={Boolean(doc.reading?.starred)} aria-label={doc.reading?.starred ? "Unstar" : "Star"} title={doc.reading?.starred ? "Unstar" : "Star"} onClick={() => toggleReading(doc.id, "starred")}><Star size={15} fill={doc.reading?.starred ? "currentColor" : "none"} /></button>
        </div>
        <p className="sb-reader-meta"><span className="sb-mono">{doc.path}</span><span>Modified {formatDay(doc.dates.modified)}</span><span title="Content version used by citations">Version <code className="sb-mono">{doc.version}</code></span>{doc.extraction !== "ready" ? <Pill tone={doc.extraction === "failed" ? "danger" : "warning"}>{extractionLabel(doc.extraction)}</Pill> : null}{doc.reading?.reviewed ? <Pill tone="brand">Reviewed</Pill> : null}</p>
        <div className="sb-reader-tools">
          <Segmented size="sm" label="Document view" value={mode} options={[["reading", "Reading"], ["original", "Original"], ["details", "Details"]]} onChange={setMode} />
          <span className="sb-tool-sep" aria-hidden="true" />
          {mode === "reading" ? <>
            <button type="button" className="sb-icon-button" aria-label="Find in document" title="Find in document (Ctrl+F)" aria-pressed={find.open} onClick={() => setFind(current => ({ ...current, open: !current.open }))}><Search size={15} /></button>
            <button type="button" className="sb-icon-button" aria-label="Table of contents" title="Table of contents" aria-pressed={toc} onClick={() => setToc(!toc)}><ListTree size={15} /></button>
            <button type="button" className="sb-icon-button" aria-label="Zoom out" title="Zoom out" disabled={zoom <= 80} onClick={() => setZoom(zoom - 10)}><ZoomOut size={15} /></button>
            <span className="sb-zoom" aria-live="polite">{zoom}%</span>
            <button type="button" className="sb-icon-button" aria-label="Zoom in" title="Zoom in" disabled={zoom >= 150} onClick={() => setZoom(zoom + 10)}><ZoomIn size={15} /></button>
            <span className="sb-tool-sep" aria-hidden="true" />
          </> : null}
          <button type="button" className="sb-mini-button" onClick={() => { navigator.clipboard?.writeText(formatCitation(doc, null, "full")); notify("Citation copied."); }}><Copy size={13} />Copy citation</button>
          <button type="button" className="sb-mini-button" onClick={() => openDialog("summarize", { docIds: [doc.id] })}><Sparkles size={13} />Summarize</button>
          <button type="button" className="sb-icon-button" aria-label="Open original file" title="Open original file" onClick={() => window.goferDesktop?.workspace?.openPath?.(`${brain.root}/${doc.path}`)}><ExternalLink size={15} /></button>
          <Menu label="More document actions" triggerClassName="sb-icon-button" trigger={<MoreHorizontal size={15} />} items={[
            ...collections.map(item => ({ label: `Add to ${item.name}`, icon: Library, onSelect: () => addToCollection(item.id, evidenceItem(doc.id)) })),
            { label: "Add to new collection…", icon: Plus, onSelect: () => openDialog("new-collection", { items: [evidenceItem(doc.id)] }) },
            { separator: true, key: "a" },
            { label: "Ask Rem about this document", icon: MessageSquareQuote, onSelect: () => askRem({ brain, doc, passages: [{ doc }], draft: "" }) },
            { label: "Create cited report…", icon: Quote, onSelect: () => openDialog("report-builder", { sources: [{ docId: doc.id }] }) },
            { separator: true, key: "b" },
            { label: doc.reading?.reviewed ? "Mark not reviewed" : "Mark reviewed", icon: CircleCheck, onSelect: () => toggleReading(doc.id, "reviewed") },
            { label: doc.reading?.read ? "Mark unread" : "Mark read", icon: doc.reading?.read ? EyeOff : Eye, onSelect: () => toggleReading(doc.id, "read") },
            { label: "Version history", icon: FileClock, onSelect: () => setMode("details") },
            { label: "Reveal in folder", icon: Link2, onSelect: () => window.goferDesktop?.workspace?.revealPath?.(`${brain.root}/${doc.path}`) },
          ]} />
        </div>
      </header>
      {find.open && mode === "reading" ? <div className="sb-find" role="search">
        <Search size={14} aria-hidden="true" />
        <input ref={findRef} aria-label="Find in document" placeholder="Find in document" value={find.text} onChange={event => setFind({ ...find, text: event.target.value, index: 0 })}
          onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); if (findTotal) setFind({ ...find, index: (find.index + (event.shiftKey ? findTotal - 1 : 1)) % findTotal }); } if (event.key === "Escape") { event.stopPropagation(); setFind({ ...find, open: false }); } }} />
        <span className="sb-find-count" aria-live="polite">{find.text.length > 1 ? (findTotal ? `${find.index + 1} of ${findTotal}` : "No matches") : ""}</span>
        <button type="button" className="sb-icon-button" aria-label="Previous match" disabled={!findTotal} onClick={() => setFind({ ...find, index: (find.index + findTotal - 1) % findTotal })}><ChevronUp size={14} /></button>
        <button type="button" className="sb-icon-button" aria-label="Next match" disabled={!findTotal} onClick={() => setFind({ ...find, index: (find.index + 1) % findTotal })}><ChevronDown size={14} /></button>
        <button type="button" className="sb-icon-button" aria-label="Close find" onClick={() => setFind({ ...find, open: false })}><X size={14} /></button>
      </div> : null}
      {hitTotal && mode === "reading" ? <div className="sb-hits"><Highlighter size={13} aria-hidden="true" /><span>{hitTotal} search match{hitTotal === 1 ? "" : "es"} in this document</span><button type="button" className="sb-icon-button" aria-label="Previous search match" onClick={() => stepHit(-1)}><ChevronUp size={14} /></button><button type="button" className="sb-icon-button" aria-label="Next search match" onClick={() => stepHit(1)}><ChevronDown size={14} /></button></div> : null}
      <div ref={bodyRef} className="sb-reader-body" data-mode={mode} data-toc={toc && mode === "reading" ? true : undefined} onMouseUp={mode === "reading" ? () => setTimeout(captureSelection, 0) : undefined}>
        {mode === "reading" ? <>
          {toc ? <nav className="sb-toc" aria-label="Contents"><p>Contents</p><ol>{doc.blocks.map(block => <li key={block.id}><button type="button" data-active={flash === block.id || undefined} onClick={() => articleRef.current?.querySelector(`[data-block="${block.id}"]`)?.scrollIntoView({ block: "start" })}>{block.page ? <small>p. {block.page}</small> : null}{block.heading}</button></li>)}</ol></nav> : null}
          <article ref={articleRef} className="sb-doc" style={{ fontSize: `${(15 * zoom) / 100}px` }}>
            {doc.missing ? <div className="sb-doc-warning" role="note"><strong>The original file is unavailable.</strong> These are retained passages from its last indexed version. Restore the source file to read the current original.</div> : null}
            {doc.extraction === "ocr" ? <div className="sb-doc-warning" role="note"><strong>No searchable text yet.</strong> This PDF contains page images. Run OCR from Library health to extract text; OCR text is labeled and kept separate from the original page.<button type="button" className="sb-mini-button" onClick={() => navigate("health")}>Open Library health</button></div> : null}
            {doc.extraction === "failed" ? <div className="sb-doc-warning" data-tone="danger" role="note"><strong>Extraction failed.</strong> {doc.blocks[0]?.text} The original file is unchanged.<button type="button" className="sb-mini-button" onClick={() => navigate("health")}>Fix in Library health</button></div> : null}
            {doc.extraction !== "failed" ? <h1>{doc.title}</h1> : null}
            {doc.extraction === "ready" || doc.extraction === "pending" || doc.missing ? doc.blocks.map(block => <section key={block.id} data-block={block.id} data-flash={flash === block.id || undefined}>
              <h2>{block.heading}{block.page ? <small> · page {block.page}</small> : null}</h2>
              <p>{decorate(block.text, marks).map((part, index) => {
                if (!part.kinds.length) return <span key={index}>{part.text}</span>;
                const isFind = part.kinds.includes("find");
                if (isFind) findCounter += 1;
                const classes = [part.kinds.includes("hit") && "sb-hit", isFind && "sb-find-hit", isFind && findCounter === find.index && "is-current", part.kinds.includes("cited") && "sb-cited", part.kinds.includes("focus") && "sb-focus-quote"].filter(Boolean).join(" ");
                return part.kinds.some(kind => kind === "hit" || kind === "find" || kind === "focus") ? <mark key={index} className={classes} data-find-index={isFind ? findCounter : undefined}>{part.text}</mark> : <span key={index} className={classes} title="Saved citation">{part.text}</span>;
              })}</p>
            </section>) : null}
          </article>
          {selection ? <div className="sb-selection-bar" role="toolbar" aria-label="Selected passage actions" style={{ top: Math.max(4, selection.top), left: selection.left }}>
            <button type="button" onClick={() => { for (const part of selection.parts) saveCitation({ docId: doc.id, ...part }); setSelection(null); window.getSelection()?.removeAllRanges(); }}><Quote size={13} />Save citation</button>
            <Menu label="Add passage to a collection" triggerClassName="" align="start" trigger={<><Library size={13} />Collect</>} items={[
              ...collections.map(item => ({ label: item.name, icon: Library, onSelect: () => { addToCollection(item.id, selection.parts.map(part => ({ ...evidenceItem(doc.id, part.blockId), quote: part.quote }))); setSelection(null); } })),
              { separator: true, key: "s" },
              { label: "New collection…", icon: Plus, onSelect: () => { openDialog("new-collection", { items: selection.parts.map(part => ({ ...evidenceItem(doc.id, part.blockId), quote: part.quote })) }); setSelection(null); } },
            ]} />
            <button type="button" onClick={() => { askRem({ brain, doc, passages: selection.parts.map(part => ({ doc, block: doc.blocks.find(block => block.id === part.blockId) })), draft: `Explain this passage from “${doc.title}” using its surrounding context:\n\n“${selection.text}”` }); setSelection(null); }}><Sparkles size={13} />Explain</button>
            <button type="button" onClick={() => { askRem({ brain, doc, passages: selection.parts.map(part => ({ doc, block: doc.blocks.find(block => block.id === part.blockId) })), draft: `About this passage from “${doc.title}”:\n\n“${selection.text}”\n\n` }); setSelection(null); }}><MessageSquareQuote size={13} />Ask Rem</button>
            <button type="button" aria-label="Dismiss" onClick={() => { setSelection(null); window.getSelection()?.removeAllRanges(); }}><X size={13} /></button>
            {selection.crossesBlocks ? <small>Saves {selection.parts.length} exact passage citations. Up to 30 passages per selection.</small> : null}
          </div> : null}
        </> : mode === "original" ? <div className="sb-original">
          <p className="sb-original-note">{doc.type === "pdf" ? "Original PDF pages. OCR or extracted text is shown separately in Reading." : doc.type === "note" || doc.type === "text" ? "Original file contents." : "Original styling preserved. Scripts, forms and external requests are blocked."}</p>
          <OriginalDocument doc={doc} />
        </div> : <DocumentDetails doc={doc} store={store} />}
      </div>
    </>}
  </section>;
}
