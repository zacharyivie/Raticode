import SecondBrainProvider, { useResearchSelection } from "./SecondBrainProvider.jsx";
import { useState } from "react";
import { ArrowLeft, CircleHelp, Download, Library, MessageSquareQuote, MoreHorizontal, Plus, Scale, ScrollText, Search, Sparkles, Trash2, TriangleAlert, X, ArrowUpRight, Pencil } from "lucide-react";
import { formatDay, formatTime } from "../lib/secondBrain.js";
import { useSecondBrain, viewFor, navigate, updateCollection, startTask, deleteCollection, openDialog, openDocument, documentById, blockById, applySavedSearch, deleteSavedSearch, uid, libraryFor } from "../lib/secondBrainStore.js";
import { CitationState, DraftInput, Empty, Menu, Pill, TypeIcon, askRem } from "./SecondBrainParts.jsx";
import { searchLibrary } from "../lib/secondBrain.js";

// Keeps a multi-line draft locally while editing and saves it on blur.
function DraftArea({ value, onCommit, label, ...props }) {
  const [draft, setDraft] = useState(value || "");
  const [focused, setFocused] = useState(false);
  const shown = focused ? draft : value || "";
  return <textarea aria-label={label} value={shown} {...props} onFocus={() => { setDraft(value || ""); setFocused(true); }} onChange={event => setDraft(event.target.value)} onBlur={() => { setFocused(false); if (draft !== (value || "")) onCommit(draft); }} />;
}

const itemCount = collection => collection.sections.reduce((total, section) => total + section.items.length, 0);
const sourceIds = collection => [...new Set(collection.sections.flatMap(section => section.items.map(item => item.docId)))];

function CollectionList({ brain, store }) {
  const collections = store.collections.filter(item => item.brainId === brain.id);
  const saved = store.savedSearches.filter(item => item.brainId === brain.id);
  const library = libraryFor(store, brain.id);
  return <div className="sb-page">
    <div className="sb-page-head"><div><h3>Collections</h3><p>Fixed sets of documents and passages gathered for a question. Unlike saved searches, a collection only changes when you change it, so reports can name exactly what they used.</p></div><button className="sb-button sb-primary" type="button" onClick={() => openDialog("new-collection")}><Plus size={14} />New collection</button></div>
    {collections.length ? <div className="sb-cards">{collections.map(collection => { const sources = sourceIds(collection); const changed = collection.sections.flatMap(section => section.items).filter(item => documentById(store, item.docId)?.version !== item.version).length; const reports = store.reports.filter(report => report.collectionId === collection.id).length; return <button key={collection.id} type="button" className="sb-card" onClick={() => navigate("collections", { collectionId: collection.id, collectionTab: "evidence" })}>
      <span className="sb-card-title"><Library size={16} aria-hidden="true" /><strong>{collection.name}</strong></span>
      <p>{collection.brief || "No research brief yet."}</p>
      <span className="sb-card-meta"><span>{itemCount(collection)} items</span><span>{sources.length} sources</span>{collection.questions.filter(question => !question.done).length ? <span>{collection.questions.filter(question => !question.done).length} open questions</span> : null}{reports ? <span>{reports} report{reports === 1 ? "" : "s"}</span> : null}</span>
      <span className="sb-card-foot"><small>Updated {formatDay(collection.updated, { relative: true })}</small>{changed ? <Pill tone="warning">{changed} source{changed === 1 ? "" : "s"} changed</Pill> : null}</span>
    </button>; })}</div> : <Empty icon={Library} title="No collections yet" actions={<button className="sb-button sb-primary" type="button" onClick={() => openDialog("new-collection")}><Plus size={14} />New collection</button>}>Start from a question, then add documents or passages from search results and the reader.</Empty>}
    <div className="sb-page-head sb-subhead"><div><h3>Saved searches</h3><p>Saved searches store criteria and update as the library changes.</p></div></div>
    {saved.length ? <div className="sb-table-wrap"><table className="sb-table"><thead><tr><th scope="col">Name</th><th scope="col">Query</th><th scope="col" className="sb-num">Results</th><th scope="col"><span className="sb-sr-only">Actions</span></th></tr></thead><tbody>
      {saved.map(item => <tr key={item.id}><th scope="row">{item.name}</th><td><code className="sb-mono">{item.text || "Filters only"}</code></td><td className="sb-num">{searchLibrary(library.documents, { text: item.text, filters: item.filters }, library).results.length}</td>
        <td className="sb-row-actions"><button type="button" className="sb-mini-button" onClick={() => applySavedSearch(item.id)}><Search size={12} />Run</button><button type="button" className="sb-icon-button" aria-label={`Delete ${item.name}`} title="Delete saved search" onClick={() => deleteSavedSearch(item.id)}><Trash2 size={14} /></button></td></tr>)}
    </tbody></table></div> : <p className="sb-hint">Use Save search in the search bar to keep a query here.</p>}
  </div>;
}

function EvidenceItem({ item, collection, store, section }) {
  const doc = documentById(store, item.docId);
  if (!doc) return <li className="sb-evidence" data-missing><TriangleAlert size={14} />Source unavailable. The citation record is kept.</li>;
  const block = blockById(doc, item.blockId);
  const changed = doc.version !== item.version;
  const update = patch => updateCollection(collection.id, current => ({ sections: current.sections.map(entry => ({ ...entry, items: entry.items.map(candidate => candidate.id === item.id ? { ...candidate, ...patch } : candidate) })) }));
  const remove = () => updateCollection(collection.id, current => ({ sections: current.sections.map(entry => ({ ...entry, items: entry.items.filter(candidate => candidate.id !== item.id) })) }));
  const move = target => updateCollection(collection.id, current => ({ sections: current.sections.map(entry => entry.id === section.id ? { ...entry, items: entry.items.filter(candidate => candidate.id !== item.id) } : entry.id === target ? { ...entry, items: [...entry.items, item] } : entry) }));
  return <li className="sb-evidence" data-kind={item.kind}>
    {item.kind === "passage" ? <blockquote>{item.quote || block?.text}</blockquote> : <p className="sb-evidence-doc"><TypeIcon type={doc.type} />Whole document</p>}
    <div className="sb-evidence-source">
      <button type="button" className="sb-link" onClick={() => openDocument(doc.id, { blockId: item.blockId, quote: item.quote })}><TypeIcon type={doc.type} size={13} />{doc.title}</button>
      {block ? <small>{doc.type === "pdf" && block.page ? `page ${block.page} · ` : ""}{block.heading}</small> : null}
      <small>version <code className="sb-mono">{item.version}</code></small>
      {changed ? <CitationState state="changed" /> : null}
      <div className="sb-evidence-actions">
        {changed ? <button type="button" className="sb-mini-button" onClick={() => openDialog("version-diff", { docId: doc.id, from: item.version, blockId: item.blockId })}>Review change</button> : null}
        {collection.sections.length > 1 ? <label className="sb-move"><span className="sb-sr-only">Move to section</span><select aria-label="Move to section" title="Move to section" value={section.id} onChange={event => move(event.target.value)}>{collection.sections.map(entry => <option key={entry.id} value={entry.id}>{entry.title}</option>)}</select></label> : null}
        <button type="button" className="sb-icon-button" aria-label="Remove from collection" title="Remove from collection" onClick={remove}><X size={14} /></button>
      </div>
    </div>
    <DraftInput label={`Note for ${doc.title}`} className="sb-note-input" placeholder="Add a note about why this matters" value={item.note} onCommit={note => update({ note })} />
  </li>;
}

function CompareTab({ collection, store, brain }) {
  const [provider, setProvider] = useResearchSelection();
  const docs = sourceIds(collection).map(id => documentById(store, id)).filter(Boolean);
  const [question, setQuestion] = useState("");
  const [chosen, setChosen] = useState(() => docs.slice(0, 4).map(doc => doc.id));
  const comparison = collection.comparisonDraft || collection.comparison;
  if (!comparison) return <div className="sb-compare-builder">
    <h4>Compare sources</h4>
    <p className="sb-hint">Rem builds a table of what each source says about your question, with a citation in every supported cell and “Not addressed” where a source is silent.</p>
    <label className="sb-field"><span>Question</span><input value={question} placeholder="What does each source say about…" onChange={event => setQuestion(event.target.value)} /></label>
    <fieldset className="sb-checklist"><legend>Sources ({chosen.length} selected)</legend>{docs.map(doc => <label key={doc.id}><input type="checkbox" checked={chosen.includes(doc.id)} onChange={() => setChosen(chosen.includes(doc.id) ? chosen.filter(id => id !== doc.id) : [...chosen, doc.id])} /><TypeIcon type={doc.type} size={13} /><span>{doc.title}</span></label>)}</fieldset>
    <SecondBrainProvider value={provider} onChange={setProvider} />
    <div className="sb-actions"><button type="button" className="sb-button sb-primary" disabled={!question.trim() || chosen.length < 2} onClick={() => startTask({ kind: "compare", collectionId: collection.id, docIds: chosen, question: question.trim(), provider, allowCloudForTask: true })}><Scale size={14} />Build comparison</button>
      <button type="button" className="sb-button" disabled={!question.trim() || chosen.length < 2} onClick={() => askRem({ brain, doc: documentById(store, chosen[0]), passages: chosen.map(id => ({ doc: documentById(store, id) })), title: `Sources from ${collection.name}`, draft: `Look for conflicting evidence among the attached sources about: “${question.trim()}”. Quote both passages for every disagreement and say when no conflict was found.` })}>Look for conflicting evidence</button></div>
  </div>;
  const sources = comparison.sources.map(id => documentById(store, id)).filter(Boolean);
  return <div className="sb-compare">
    {collection.comparisonDraft ? <div className="sb-inline-note" data-tone="warning" role="status">Draft comparison from a research job. Review its evidence before accepting it.<button type="button" className="sb-button" onClick={() => updateCollection(collection.id, { comparison: collection.comparisonDraft, comparisonDraft: null })}>Accept comparison</button><button type="button" className="sb-button" onClick={() => updateCollection(collection.id, { comparisonDraft: null })}>Discard draft</button></div> : null}
    <div className="sb-compare-head"><div><small>Question</small><h4>{comparison.question}</h4></div><div className="sb-actions"><button type="button" className="sb-button" onClick={() => askRem({ brain, doc: sources[0], passages: sources.map(doc => ({ doc })), title: `Sources from ${collection.name}`, draft: `Look for conflicting evidence among the attached sources about: “${comparison.question}”. Quote both passages for every disagreement.` })}><TriangleAlert size={14} />Look for conflicting evidence</button></div></div>
    <div className="sb-table-wrap"><table className="sb-table sb-compare-table"><caption className="sb-sr-only">What each source says</caption><thead><tr><th scope="col">Aspect</th>{sources.map(doc => <th key={doc.id} scope="col"><button type="button" className="sb-link" onClick={() => openDocument(doc.id)}>{doc.title}</button></th>)}</tr></thead>
      <tbody>{comparison.rows.map(row => <tr key={row.id}><th scope="row">{row.aspect}</th>{sources.map(doc => { const cell = row.cells[doc.id]; return <td key={doc.id}>{cell ? <><span>{cell.text}</span> <button type="button" className="sb-cite-chip" title={`Open ${doc.title} at ${blockById(doc, cell.blockId)?.heading}`} onClick={() => openDocument(doc.id, { blockId: cell.blockId })}>{blockById(doc, cell.blockId)?.heading}<ArrowUpRight size={11} aria-hidden="true" /></button></> : <span className="sb-muted">Not addressed</span>}</td>; })}</tr>)}</tbody>
    </table></div>
    {comparison.conflicts?.length ? <section className="sb-conflicts"><h4><TriangleAlert size={14} aria-hidden="true" />Possible disagreement</h4>{comparison.conflicts.map(conflict => <div key={conflict.id} className="sb-conflict"><p>{conflict.summary}</p><p className="sb-hint">Proposed by Rem. Inspect both passages before relying on this comparison.</p><button type="button" className="sb-button" onClick={() => openDialog("conflict", { conflict })}>Inspect both passages</button></div>)}</section> : null}
  </div>;
}

function CollectionDetail({ collection, store, brain, tab }) {
  const docs = sourceIds(collection).map(id => documentById(store, id)).filter(Boolean);
  const [question, setQuestion] = useState("");
  const reports = store.reports.filter(report => report.collectionId === collection.id);
  const changed = collection.sections.flatMap(section => section.items).filter(item => documentById(store, item.docId)?.version !== item.version);
  const setTab = collectionTab => navigate("collections", { collectionId: collection.id, collectionTab });
  return <div className="sb-page">
    <button type="button" className="sb-back" onClick={() => navigate("collections", { collectionId: "" })}><ArrowLeft size={14} />All collections</button>
    <div className="sb-page-head">
      <div className="sb-collection-title"><DraftInput label="Collection name" className="sb-title-input" value={collection.name} onCommit={name => name.trim() && updateCollection(collection.id, { name: name.trim() })} />
        <p>{itemCount(collection)} items · {docs.length} sources · versions pinned when added · updated {formatDay(collection.updated, { relative: true })}</p></div>
      <div className="sb-actions">
        <button type="button" className="sb-button" onClick={() => openDialog("summarize", { docIds: docs.map(doc => doc.id), collectionId: collection.id })}><Sparkles size={14} />Summarize</button>
        <button type="button" className="sb-button sb-primary" onClick={() => openDialog("report-builder", { collectionId: collection.id })}><ScrollText size={14} />Create cited report</button>
        <Menu label="More collection actions" triggerClassName="sb-icon-button" trigger={<MoreHorizontal size={16} />} items={[
          { label: "Ask Rem about these sources", icon: MessageSquareQuote, onSelect: () => askRem({ brain, doc: docs[0], passages: collection.sections.flatMap(section => section.items).map(item => ({ doc: documentById(store, item.docId), block: blockById(documentById(store, item.docId), item.blockId) })).filter(entry => entry.doc), title: `Collection: ${collection.name}`, draft: collection.brief ? `${collection.brief}\n\nUse only the attached collection evidence and cite it.` : "" }) },
          { label: "Export research collection…", icon: Download, onSelect: () => openDialog("export", { purpose: "collection", collectionId: collection.id }) },
          { separator: true, key: "s" },
          { label: "Delete collection", icon: Trash2, danger: true, onSelect: () => { deleteCollection(collection.id); navigate("collections", { collectionId: "" }); } },
        ]} />
      </div>
    </div>
    <nav className="sb-subtabs" aria-label="Collection views">{[["evidence", "Evidence"], ["compare", "Compare sources"], ["reports", `Reports · ${reports.length}`]].map(([id, label]) => <button key={id} type="button" aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}>{label}</button>)}</nav>
    {tab === "compare" ? <CompareTab collection={collection} store={store} brain={brain} /> : tab === "reports" ? <div>{reports.length ? <ul className="sb-report-list">{reports.map(report => <li key={report.id}><button type="button" onClick={() => navigate("reports", { reportId: report.id })}><ScrollText size={16} aria-hidden="true" /><span><strong>{report.title}</strong><small>{formatTime(report.generatedAt)} · {report.citations.length} citations · {report.provider}</small></span></button></li>)}</ul> : <Empty icon={ScrollText} title="No reports from this collection yet" actions={<button className="sb-button sb-primary" type="button" onClick={() => openDialog("report-builder", { collectionId: collection.id })}>Create cited report</button>} />}</div> : <div className="sb-notebook">
      <div className="sb-notebook-main">
        <section className="sb-brief"><label htmlFor={`brief-${collection.id}`}><h4>Research brief</h4></label><DraftArea id={`brief-${collection.id}`} label="Research brief" rows={3} placeholder="What question is this collection answering?" value={collection.brief} onCommit={brief => updateCollection(collection.id, { brief })} /></section>
        {changed.length ? <div className="sb-inline-note" data-tone="warning"><TriangleAlert size={14} aria-hidden="true" />{changed.length} item{changed.length === 1 ? "" : "s"} came from a version that has since changed. Each keeps the version it was collected from until you review it.</div> : null}
        {collection.sections.map(section => <section key={section.id} className="sb-evidence-section">
          <div className="sb-section-head"><DraftInput label="Section title" className="sb-section-input" value={section.title} onCommit={title => updateCollection(collection.id, current => ({ sections: current.sections.map(entry => entry.id === section.id ? { ...entry, title } : entry) }))} /><Pencil size={12} aria-hidden="true" className="sb-muted" /><small>{section.items.length}</small>
            {collection.sections.length > 1 && !section.items.length ? <button type="button" className="sb-icon-button" aria-label={`Remove section ${section.title}`} onClick={() => updateCollection(collection.id, current => ({ sections: current.sections.filter(entry => entry.id !== section.id) }))}><X size={14} /></button> : null}</div>
          {section.items.length ? <ul className="sb-evidence-list">{section.items.map(item => <EvidenceItem key={item.id} item={item} collection={collection} section={section} store={store} />)}</ul> : <p className="sb-hint">Add passages from the reader, or move items here.</p>}
        </section>)}
        <div className="sb-actions"><button type="button" className="sb-button" onClick={() => updateCollection(collection.id, current => ({ sections: [...current.sections, { id: uid("sec"), title: "New section", items: [] }] }))}><Plus size={14} />Add section</button><button type="button" className="sb-button" onClick={() => navigate("search")}><Search size={14} />Find more evidence</button></div>
      </div>
      <aside className="sb-notebook-side">
        <section><h4><CircleHelp size={14} aria-hidden="true" />Open questions</h4>
          <ul className="sb-questions">{collection.questions.map(item => <li key={item.id}><label><input type="checkbox" checked={item.done} onChange={() => updateCollection(collection.id, current => ({ questions: current.questions.map(entry => entry.id === item.id ? { ...entry, done: !entry.done } : entry) }))} /><span data-done={item.done || undefined}>{item.text}</span></label><button type="button" className="sb-icon-button" aria-label="Remove question" onClick={() => updateCollection(collection.id, current => ({ questions: current.questions.filter(entry => entry.id !== item.id) }))}><X size={12} /></button></li>)}</ul>
          <form className="sb-inline-form" onSubmit={event => { event.preventDefault(); if (!question.trim()) return; updateCollection(collection.id, current => ({ questions: [...current.questions, { id: uid("q"), text: question.trim(), done: false }] })); setQuestion(""); }}><input aria-label="New question" placeholder="Add a question" value={question} onChange={event => setQuestion(event.target.value)} /><button type="submit" className="sb-icon-button" aria-label="Add question"><Plus size={14} /></button></form>
        </section>
        <section><h4>Sources</h4><ul className="sb-source-list">{docs.map(doc => { const versions = [...new Set(collection.sections.flatMap(section => section.items.filter(item => item.docId === doc.id).map(item => item.version)))]; return <li key={doc.id}><TypeIcon type={doc.type} size={13} /><span><button type="button" className="sb-link" onClick={() => openDocument(doc.id)}>{doc.title}</button><small>Pinned {versions.map(version => <code key={version} className="sb-mono">{version}</code>)}{versions.some(version => version !== doc.version) ? <> · current <code className="sb-mono">{doc.version}</code></> : null}</small></span></li>; })}</ul></section>
      </aside>
    </div>}
  </div>;
}

export function CollectionsView({ brain }) {
  const store = useSecondBrain();
  const view = viewFor(store);
  const collection = store.collections.find(item => item.id === view.collectionId && item.brainId === brain.id);
  return collection ? <CollectionDetail key={collection.id} collection={collection} store={store} brain={brain} tab={view.collectionTab || "evidence"} /> : <CollectionList brain={brain} store={store} />;
}
