import SecondBrainProvider, { useResearchSelection } from "./SecondBrainProvider.jsx";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ArrowUpRight, CircleCheck, ExternalLink, FolderOpen, Plus, RefreshCw, ScrollText, ShieldCheck, TriangleAlert, Library, ChevronDown, ChevronRight } from "lucide-react";
import { CITATION_STATES, SUPPORT_LABELS, estimateTokens, formatTime } from "../lib/secondBrain.js";
import { useSecondBrain, viewFor, navigate, openDialog, openDocument, documentById, blockById, updateClaim, updateReport, updateReportCitation, startTask } from "../lib/secondBrainStore.js";
import { CitationState, Empty, Modal, Pill, ProgressBar, Segmented, TypeIcon } from "./SecondBrainParts.jsx";
import { OriginalDocument } from "./SecondBrainReader.jsx";
import { brainRequest } from "../lib/secondBrainApi.js";

function reportHealth(report) {
  const changed = report.citations.filter(citation => citation.state === "changed" || citation.state === "relocate" || citation.state === "unavailable");
  const unreviewed = report.claims.filter(claim => !claim.reviewed);
  const linked = report.claims.filter(claim => claim.citations.length).length;
  return { changed, unreviewed, linked };
}

function CitationChip({ citation, store, onOpen }) {
  const [open, setOpen] = useState(false);
  const doc = documentById(store, citation.docId);
  const block = blockById(doc, citation.blockId);
  const timer = useRef(null);
  const show = () => { clearTimeout(timer.current); setOpen(true); };
  const hide = () => { timer.current = setTimeout(() => setOpen(false), 120); };
  return <span className="sb-cite" onMouseEnter={show} onMouseLeave={hide}>
    <button type="button" className="sb-cite-number" data-state={citation.state} aria-label={`Citation ${citation.n}: ${doc?.title || "Unavailable source"}, ${CITATION_STATES[citation.state]?.label}`} aria-describedby={open ? `cite-card-${citation.id}` : undefined}
      onFocus={show} onBlur={hide} onClick={() => onOpen(citation)} onKeyDown={event => { if (event.key === "Escape") setOpen(false); }}>{citation.n}</button>
    {open ? <span role="tooltip" id={`cite-card-${citation.id}`} className="sb-cite-card" onMouseEnter={show} onMouseLeave={hide}>
      <strong><TypeIcon type={doc?.type} size={13} />{doc?.title || "Source unavailable"}</strong>
      <q>{citation.quote}</q>
      <small>{block ? `${doc.type === "pdf" && block.page ? `Page ${block.page} · ` : ""}${block.heading}` : "Location unknown"} · version {citation.version}</small>
      <CitationState state={citation.state} />
    </span> : null}
  </span>;
}

function ReportList({ brain, store }) {
  const reports = store.reports.filter(report => report.brainId === brain.id);
  return <div className="sb-page">
    <div className="sb-page-head"><div><h3>Reports</h3><p>Cited reports Rem generated from this brain. Each keeps the source versions it used, so every citation can be checked later.</p></div><button type="button" className="sb-button sb-primary" onClick={() => openDialog("report-builder", {})}><Plus size={14} />Create cited report</button></div>
    {reports.length ? <ul className="sb-report-list">{reports.map(report => { const health = reportHealth(report); const collection = store.collections.find(item => item.id === report.collectionId); return <li key={report.id}><button type="button" onClick={() => navigate("reports", { reportId: report.id })}>
      <ScrollText size={18} aria-hidden="true" />
      <span><strong>{report.title}</strong><small>{formatTime(report.generatedAt)} · {report.provider} · {report.citations.length} citations{collection ? ` · from “${collection.name}”` : ""}</small></span>
      <span className="sb-report-status">{health.changed.length ? <Pill tone="warning">{health.changed.length} citation{health.changed.length === 1 ? "" : "s"} to review</Pill> : null}{report.draft ? <Pill tone="brand">Draft</Pill> : null}{health.unreviewed.length ? <Pill>{health.unreviewed.length} claims unreviewed</Pill> : null}</span>
    </button></li>; })}</ul> : <Empty icon={ScrollText} title="No reports yet" actions={<button type="button" className="sb-button sb-primary" onClick={() => openDialog("report-builder", {})}>Create cited report</button>}>Gather evidence in a collection, then ask Rem for a report. Rem cites passages; Raticode checks every citation before showing the report.</Empty>}
  </div>;
}

function ReportDetail({ report, store, brain }) {
  const [panel, setPanel] = useState("sources");
  const [expanded, setExpanded] = useState({});
  const health = reportHealth(report);
  const collection = store.collections.find(item => item.id === report.collectionId);
  const byId = Object.fromEntries(report.citations.map(citation => [citation.id, citation]));
  const sections = [...new Set(report.claims.map(claim => claim.section))];
  const artifact = store.documents.find(doc => doc.path === report.artifactPath && doc.brainId === brain.id);
  const openCitation = citation => {
    if (citation.state === "changed") { openDialog("version-diff", { docId: citation.docId, from: citation.version, blockId: citation.blockId, citation, reportId: report.id }); return; }
    if (citation.state === "relocate") { openDialog("relocate", { citation, reportId: report.id }); return; }
    openDocument(citation.docId, { blockId: citation.blockId, quote: citation.quote });
  };
  const affected = report.claims.filter(claim => claim.citations.some(id => health.changed.some(citation => citation.id === id)));
  return <div className="sb-page sb-report-page">
    <button type="button" className="sb-back" onClick={() => navigate("reports", { reportId: "" })}><ArrowLeft size={14} />All reports</button>
    <div className="sb-page-head">
      <div><h3>{report.title}</h3><p>Generated {formatTime(report.generatedAt)} · {report.provider} · {report.modelReported} · {report.format.toUpperCase()}{collection ? <> · <span className="sb-nowrap">from <button type="button" className="sb-link" onClick={() => navigate("collections", { collectionId: collection.id, collectionTab: "evidence" })}>{collection.name}</button></span></> : null}</p></div>
      <div className="sb-actions">
        {report.draft ? <button type="button" className="sb-button sb-primary" disabled={health.unreviewed.length > 0 || health.changed.length > 0 || report.citations.some(citation => citation.state !== "found")} onClick={() => updateReport(report.id, { draft: false })}><CircleCheck size={14} />Publish reviewed report</button> : null}
        <button type="button" className="sb-button" onClick={() => artifact ? openDocument(artifact.id, { preview: false }) : window.goferDesktop?.workspace?.openPath?.(`${brain.root}/${report.artifactPath}`)}><ExternalLink size={14} />Open artifact</button>
        <button type="button" className="sb-button" onClick={() => window.goferDesktop?.workspace?.revealPath?.(`${brain.root}/${report.artifactPath}`)}><FolderOpen size={14} />Show in folder</button>
        <button type="button" className="sb-button" onClick={() => openDialog("report-builder", { collectionId: report.collectionId, question: report.question, revisionOf: report.id })}><RefreshCw size={14} />Regenerate</button>
      </div>
    </div>
    {health.changed.length ? <div className="sb-inline-note sb-callout" data-tone="warning" role="status"><TriangleAlert size={15} aria-hidden="true" /><div><strong>{health.changed.length} cited source{health.changed.length === 1 ? " has" : "s have"} changed since this report was generated.</strong><p>The report still points to the versions it used. {affected.length} claim{affected.length === 1 ? "" : "s"} may be affected.</p>
      <div className="sb-actions"><button type="button" className="sb-button" onClick={() => { setPanel("claims"); setExpanded(Object.fromEntries(affected.map(claim => [claim.id, true]))); }}>Review affected claims</button><button type="button" className="sb-button" onClick={() => openDialog("report-builder", { collectionId: report.collectionId, question: report.question, revisionOf: report.id })}>Generate revised report</button></div></div></div> : null}
    <div className="sb-report-layout">
      <article className="sb-report-body" aria-label="Report">
        {report.external ? <><p className="sb-hint">Saved from a Rem thread. Citation coverage includes only evidence explicitly supplied when saving. This artifact has no structured claim review.</p>{artifact ? <OriginalDocument doc={artifact} /> : <p role="status">The original artifact is unavailable.</p>}</> : null}
        <p className="sb-report-question"><small>Question</small>{report.question}</p>
        {sections.map(section => <section key={section}><h4>{section}</h4><p>{report.claims.filter(claim => claim.section === section).map(claim => <span key={claim.id} className="sb-claim" data-support={claim.support} data-affected={affected.includes(claim) || undefined}>
          {claim.text}{claim.citations.length ? <span className="sb-cites">{claim.citations.map(id => byId[id]).filter(Boolean).map(citation => <CitationChip key={citation.id} citation={citation} store={store} onOpen={openCitation} />)}</span> : <span className="sb-unsupported">Unsupported</span>}{" "}
        </span>)}</p></section>)}
      </article>
      <aside className="sb-report-side">
        <Segmented size="sm" label="Report panel" value={panel} options={[["sources", "Sources"], ["claims", "Claims"], ["provenance", "Provenance"]]} onChange={setPanel} />
        {panel === "sources" ? <ol className="sb-bibliography">{report.citations.map(citation => { const doc = documentById(store, citation.docId); const block = blockById(doc, citation.blockId); return <li key={citation.id}>
          <span className="sb-cite-number" data-state={citation.state}>{citation.n}</span>
          <div><button type="button" className="sb-link" onClick={() => openCitation(citation)}>{doc?.title || "Source unavailable"}</button><q>{citation.quote}</q><small>{block?.heading} · version <code className="sb-mono">{citation.version}</code>{doc && doc.version !== citation.version ? <> · now <code className="sb-mono">{doc.version}</code></> : null}</small>
            <div className="sb-bib-actions"><CitationState state={citation.state} />
              {citation.state === "changed" ? <button type="button" className="sb-mini-button" onClick={() => openCitation(citation)}>Compare versions</button> : null}
              {citation.state === "relocate" ? <button type="button" className="sb-mini-button" onClick={() => openCitation(citation)}>Review location</button> : null}
              {citation.state === "unreviewed" ? <button type="button" className="sb-mini-button" onClick={() => updateReportCitation(report.id, citation.id, { state: "found" })}><CircleCheck size={12} />Mark support reviewed</button> : null}
              {citation.state === "found" ? <button type="button" className="sb-mini-button" onClick={() => openCitation(citation)}>Open passage<ArrowUpRight size={12} /></button> : null}
            </div></div>
        </li>; })}</ol>
          : panel === "claims" ? <div className="sb-claims-review">
            <div className="sb-coverage"><div><strong>{health.linked} of {report.claims.length}</strong> claims link to evidence</div><ProgressBar value={(health.linked / Math.max(1, report.claims.length)) * 100} label="Claims with evidence links" /><p className="sb-hint">Coverage counts evidence links. It does not measure whether a claim is true.</p></div>
            <ul>{report.claims.map(claim => <li key={claim.id} data-affected={affected.includes(claim) || undefined}>
              <div className="sb-claim-row"><input type="checkbox" aria-label={`Reviewed: ${claim.text}`} checked={claim.reviewed} onChange={() => updateClaim(report.id, claim.id, { reviewed: !claim.reviewed })} /><p>{claim.text}</p></div>
              <div className="sb-claim-controls">
                <label><span className="sb-sr-only">Support</span><select aria-label="Support label" value={claim.support} data-support={claim.support} onChange={event => updateClaim(report.id, claim.id, { support: event.target.value })}>{SUPPORT_LABELS.map(([id, text]) => <option key={id} value={id}>{text}</option>)}</select></label>
                {claim.citations.length ? <button type="button" className="sb-link" aria-expanded={Boolean(expanded[claim.id])} onClick={() => setExpanded({ ...expanded, [claim.id]: !expanded[claim.id] })}>{expanded[claim.id] ? <ChevronDown size={12} /> : <ChevronRight size={12} />}Show supporting evidence</button> : <button type="button" className="sb-link" onClick={() => openDialog("find-support", { claim, reportId: report.id })}>Find a citation for this claim</button>}
                {affected.includes(claim) ? <Pill tone="warning">Source changed</Pill> : null}
              </div>
              {expanded[claim.id] ? <ul className="sb-claim-evidence">{claim.citations.map(id => byId[id]).filter(Boolean).map(citation => <li key={citation.id}><q>{citation.quote}</q><button type="button" className="sb-link" onClick={() => openCitation(citation)}>[{citation.n}] {documentById(store, citation.docId)?.title}</button></li>)}</ul> : null}
            </li>)}</ul>
          </div>
          : <dl className="sb-provenance">
            {[["Question", report.question], ["Collection", collection?.name || "None"], ["Source selection", report.sourceSelection?.mode === "auto" ? `Local search selected ${report.sourceSelection.selectedCount} of ${report.sourceSelection.candidateCount} eligible documents` : "Manual"], ["Source scope", report.scope === "selected" ? "Chosen sources only" : "Chosen sources and web research"], ["Filters", <code key="f" className="sb-mono">{report.filters || "None"}</code>],
              ...(report.sourceSelection?.mode === "auto" && report.sourceSelection.minimumRelevance !== undefined ? [["Minimum topic match", `${report.sourceSelection.minimumRelevance}%`], ["Top matches limit", report.sourceSelection.limit ?? "None"]] : []),
              ["Provider", report.provider], ["Model requested", report.model], ["Model reported", report.modelReported], ["Generated", formatTime(report.generatedAt)], ["Template", report.template], ["Format and theme", `${report.format.toUpperCase()} · ${report.theme}`],
              ["Retrieval generation", `#${report.retrievalGeneration}`], ["Output hash", <code key="h" className="sb-mono">{report.outputHash}</code>], ["Artifact", <code key="a" className="sb-mono">{report.artifactPath}</code>]].map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}
            <div><dt>Source versions</dt><dd><ul className="sb-version-list">{report.sourceVersions.map(source => { const doc = documentById(store, source.docId); return <li key={source.docId}><span>{doc?.title || source.docId}</span><code className="sb-mono">{source.version}</code>{doc?.version !== source.version ? <Pill tone="warning">Changed</Pill> : <Pill tone="success">Current</Pill>}</li>; })}</ul></dd></div>
            <p className="sb-hint">Rerunning with the same inputs can word the report differently. Provenance makes the inputs traceable, not the prose identical.</p>
          </dl>}
      </aside>
    </div>
  </div>;
}

export function ReportsView({ brain }) {
  const store = useSecondBrain();
  const view = viewFor(store);
  const report = store.reports.find(item => item.id === view.reportId && item.brainId === brain.id);
  return report ? <ReportDetail key={report.id} report={report} store={store} brain={brain} /> : <ReportList brain={brain} store={store} />;
}

export function ReportBuilder({ collectionId, sources: initialSources = [], question: initialQuestion, revisionOf, provider: initialProvider, onClose }) {
  const [provider, setProvider] = useResearchSelection(initialProvider);
  const store = useSecondBrain();
  const brain = store.brains.find(item => item.id === store.activeBrainId);
  const collections = store.collections.filter(item => item.brainId === brain.id);
  const [collection, setCollection] = useState(collectionId || "");
  const [sourceSelection, setSourceSelection] = useState(initialSources.length || collectionId ? "manual" : "auto");
  const [otherBrains, setOtherBrains] = useState([]);
  const chosenCollection = collections.find(item => item.id === collection);
  const candidates = useMemo(() => {
    const items = chosenCollection ? chosenCollection.sections.flatMap(section => section.items) : initialSources.length ? initialSources : store.documents.filter(doc => doc.brainId === brain.id && doc.extraction === "ready" && !doc.excluded && !doc.missing).map(doc => ({ docId: doc.id }));
    const ids = [...new Set(items.map(item => item.docId))];
    const extra = store.documents.filter(doc => otherBrains.includes(doc.brainId)).map(doc => doc.id);
    return [...new Set([...ids, ...extra])].map(id => documentById(store, id)).filter(doc => doc?.extraction === "ready" && !doc.excluded && !doc.missing);
  }, [chosenCollection, initialSources, store, brain.id, otherBrains]);
  const [excluded, setExcluded] = useState([]);
  const [question, setQuestion] = useState(initialQuestion || chosenCollection?.brief || "");
  const [format, setFormat] = useState("html");
  const [length, setLength] = useState("standard");
  const [audience, setAudience] = useState("");
  const [template, setTemplate] = useState("brief");
  const [scope, setScope] = useState("selected");
  const [coverage, setCoverage] = useState(initialSources.length || collectionId ? "whole" : "excerpts");
  const [includeGenerated, setIncludeGenerated] = useState(false);
  const [sourceFilter, setSourceFilter] = useState("");
  const [sourceRelevance, setSourceRelevance] = useState(0);
  const [sourceLimitText, setSourceLimitText] = useState("");
  const sourceLimit = sourceLimitText.trim() ? Number(sourceLimitText) : null;
  const sourceOptionsError = sourceLimit !== null && (!/^\d+$/.test(sourceLimitText.trim()) || !Number.isSafeInteger(sourceLimit) || sourceLimit < 1) ? "Enter a positive whole number, or leave top matches blank for no limit." : "";
  const [preview, setPreview] = useState(null);
  const previewKey = JSON.stringify({ brainId: brain.id, question: question.trim(), collectionId: collection, ...(initialSources.length && !collection ? { docIds: initialSources.map(item => item.docId) } : {}), includeGenerated, otherBrains, sourceRelevance, sourceLimit, generation: brain.index?.generation });
  useEffect(() => {
    if (sourceSelection !== "auto" || sourceOptionsError || !JSON.parse(previewKey).question) return;
    let active = true;
    const timer = setTimeout(() => {
      brainRequest("report_sources", JSON.parse(previewKey)).then(result => {
        if (active) setPreview({ key: previewKey, ...result });
      }).catch(error => { if (active) setPreview({ key: previewKey, sources: [], error: error.message }); });
    }, 300);
    return () => { active = false; clearTimeout(timer); };
  }, [previewKey, sourceSelection, sourceOptionsError]);
  const automatic = sourceSelection === "auto";
  const currentPreview = preview?.key === previewKey ? preview : null;
  const included = automatic ? (currentPreview?.sources || []).map(source => documentById(store, source.id) || source) : candidates.filter(doc => !excluded.includes(doc.id) && (includeGenerated || doc.origin !== "generated"));
  const matchingCandidates = candidates.filter(doc => `${doc.title} ${doc.path}`.toLocaleLowerCase().includes(sourceFilter.toLocaleLowerCase()));
  const tokens = included.reduce((total, doc) => total + (coverage === "whole" ? (doc.size || 0) / 4 : (doc.blocks || []).map(block => estimateTokens(block.text)).sort((a, b) => b - a).slice(0, 5).reduce((sum, count) => sum + count, 0)), 0);
  const budget = 200000;
  const generatedCount = candidates.filter(doc => doc.origin === "generated").length;
  function generate() {
    const title = question.trim().replace(/\?$/, "").slice(0, 80) || "Cited report";
    onClose();
    const sources = automatic ? (initialSources.length && !collection ? { docIds: initialSources.map(item => item.docId) } : {}) : { docIds: included.map(doc => doc.id) };
    void startTask({ kind: "report", label: `Generating “${title}”`, ...sources, sourceSelection, ...(automatic ? { sourceRelevance, sourceLimit } : {}), question, format, length, audience, template, scope, coverage, includeGenerated, otherBrains: automatic ? otherBrains : [], collectionId: collection, revisionOf, provider, review: true, allowCloudForTask: true });
  }
  return <Modal wide title={revisionOf ? "Generate revised report" : "Create cited report"} description="Raticode can find sources for your brief, or you can choose them. Every citation is checked against the pinned source versions." onClose={onClose}
    footer={<><span className="sb-footer-note">{automatic ? "Sources are selected and pinned when generation starts." : "Source versions are pinned when generation starts."}</span><button type="button" className="sb-button" onClick={onClose}>Cancel</button><button type="button" className="sb-button sb-primary" disabled={!included.length || !question.trim() || (automatic && Boolean(sourceOptionsError))} onClick={generate}><ScrollText size={14} />Generate report</button></>}>
    <div className="sb-builder">
      <div className="sb-builder-main">
        <label className="sb-field"><span>Question or brief</span><textarea rows={3} value={question} placeholder="What should the report answer?" onChange={event => setQuestion(event.target.value)} /></label>
        <div className="sb-field"><span>Source selection</span><Segmented label="Source selection" value={sourceSelection} options={[["auto", "Choose automatically"], ["manual", "Choose manually"]]} onChange={value => { if (value === "manual" && automatic && included.length) setExcluded(candidates.filter(doc => !included.some(source => source.id === doc.id)).map(doc => doc.id)); setSourceSelection(value); }} /></div>
        <label className="sb-field"><span>Look for evidence in</span><select value={collection} onChange={event => { setCollection(event.target.value); setExcluded([]); }}><option value="">{initialSources.length ? `Selected documents (${initialSources.length})` : "Documents in this brain"}</option>{collections.map(item => <option key={item.id} value={item.id}>Collection: {item.name}</option>)}</select></label>
        {automatic ? <>
          <label className="sb-field sb-source-relevance"><span>Minimum topic match: {sourceRelevance}%</span><input type="range" aria-label="Minimum topic match" min="0" max="100" step="1" value={sourceRelevance} onChange={event => setSourceRelevance(Number(event.target.value))} /><small>Broader matching at 0%; all topic words at 100%. Measures word overlap in text, titles, paths and tags, not AI confidence. Common words in your brief are ignored.</small></label>
          <label className="sb-field"><span>Top matches (optional)</span><input aria-label="Top matches" inputMode="numeric" value={sourceLimitText} placeholder="No limit" onChange={event => setSourceLimitText(event.target.value)} /><small>Leave blank to include every source that meets the topic match threshold.</small></label>
          <div className="sb-auto-sources" aria-live="polite" aria-busy={Boolean(question.trim() && !sourceOptionsError && !currentPreview)}>
          <p className="sb-hint">Raticode checks every eligible document and selects all matching sources{sourceLimit !== null && !sourceOptionsError ? `, limited to the top ${sourceLimit} matches you requested` : ""}. Other documents will not be read for this report.</p>
          {sourceOptionsError ? <p role="alert">{sourceOptionsError}</p> : !question.trim() ? <p>Enter a question or brief to find sources.</p> : !currentPreview ? <p role="status">Finding relevant sources…</p> : currentPreview.error ? <p role="alert">{currentPreview.error}</p> : !included.length ? <p>No eligible sources meet this topic match threshold. Lower it, add specific topic words or choose sources manually.</p> : <>
            <h4>{included.length} sources selected from {currentPreview.candidateCount} eligible documents</h4>
            {currentPreview.matchedCount > included.length ? <p className="sb-hint">{currentPreview.matchedCount} sources meet the threshold. Your top matches limit includes {included.length}.</p> : null}
            <ul className="sb-source-list">{included.map(doc => <li key={doc.id}><TypeIcon type={doc.type} size={14} /><span><strong>{doc.title}</strong><small>{doc.path}{doc.origin === "generated" ? " · previous Second Brain output" : ""}</small></span></li>)}</ul>
            <p className="sb-hint">This preview updates with your brief. Choose manually to adjust these sources.</p>
          </>}
        </div></> : <>
          <label className="sb-field"><span>Filter sources</span><input type="search" value={sourceFilter} placeholder="Find a title or path" onChange={event => setSourceFilter(event.target.value)} /></label>
          <div className="sb-actions"><button type="button" className="sb-button" onClick={() => setExcluded(candidates.map(doc => doc.id))}>Clear selection</button><button type="button" className="sb-button" onClick={() => setExcluded([])}>Select all</button></div>
          <fieldset className="sb-source-picker"><legend>Sources · {included.length} included</legend>
            {matchingCandidates.map(doc => { const previousOutput = doc.origin === "generated" && !includeGenerated; return <label key={doc.id} data-disabled={previousOutput || undefined}><input type="checkbox" disabled={previousOutput} checked={!excluded.includes(doc.id) && !previousOutput} onChange={() => setExcluded(excluded.includes(doc.id) ? excluded.filter(id => id !== doc.id) : [...excluded, doc.id])} /><TypeIcon type={doc.type} size={14} /><span><strong>{doc.title}</strong><small>{doc.path} · ≈{Math.round(doc.size / 4).toLocaleString()} tokens{previousOutput ? " · previous Second Brain output, excluded" : ""}</small></span></label>; })}
            {!matchingCandidates.length ? <p className="sb-hint">No documents match this source filter.</p> : null}
          </fieldset>
        </>}
        {generatedCount ? <label className="sb-check"><input type="checkbox" checked={includeGenerated} onChange={event => setIncludeGenerated(event.target.checked)} />Include previous Second Brain outputs ({generatedCount})</label> : null}
        <p className="sb-hint">Previous outputs are reports and summaries saved by Second Brain. Other documents remain eligible, including AI-written files. Raticode does not infer who wrote a document.</p>
      </div>
      <div className="sb-builder-side">
        <fieldset className="sb-checklist"><legend>Research scope</legend>
          <label><input type="radio" name="sb-scope" checked={scope === "selected"} onChange={() => setScope("selected")} /><span>Chosen sources only<small>Rem writes from the chosen evidence.</small></span></label>
          <label><input type="radio" name="sb-scope" checked={scope === "web"} onChange={() => setScope("web")} /><span>Chosen sources and web research<small>Web findings are cited separately.</small></span></label>
        </fieldset>
        {store.brains.filter(item => item.id !== brain.id && item.state === "available").length ? <fieldset className="sb-checklist"><legend>Also search other brains</legend>{store.brains.filter(item => item.id !== brain.id && item.state === "available").map(item => <label key={item.id}><input type="checkbox" checked={otherBrains.includes(item.id)} onChange={() => setOtherBrains(otherBrains.includes(item.id) ? otherBrains.filter(id => id !== item.id) : [...otherBrains, item.id])} /><span>{item.name}</span></label>)}</fieldset> : null}
        <div className="sb-field"><span>Format</span><Segmented size="sm" label="Format" value={format} options={[["html", "HTML"], ["md", "Markdown"], ["pdf", "PDF"]]} onChange={setFormat} /></div>
        <div className="sb-field"><span>Length</span><Segmented size="sm" label="Length" value={length} options={[["brief", "Brief"], ["standard", "Standard"], ["detailed", "Detailed"]]} onChange={setLength} /></div>
        <label className="sb-field"><span>Template</span><select value={template} onChange={event => setTemplate(event.target.value)}><option value="brief">Cited brief</option><option value="memo">Decision memo</option><option value="review">Literature review</option></select></label>
        <label className="sb-field"><span>Audience</span><input value={audience} placeholder="e.g. Product and engineering" onChange={event => setAudience(event.target.value)} /></label>
        <fieldset className="sb-checklist"><legend>Reading</legend>
          <label><input type="radio" name="sb-coverage" checked={coverage === "whole"} onChange={() => setCoverage("whole")} /><span>Read whole documents</span></label>
          <label><input type="radio" name="sb-coverage" checked={coverage === "excerpts"} onChange={() => setCoverage("excerpts")} /><span>Relevant excerpts only<small>The report states that it used excerpts.</small></span></label>
        </fieldset>
        <div className="sb-estimate" data-over={tokens > budget || undefined}><span>Context estimate</span><ProgressBar value={(tokens / budget) * 100} label="Estimated context use" /><small>≈{Math.round(tokens).toLocaleString()} of ~{budget.toLocaleString()} tokens</small>{tokens > budget ? <small>Too large for one pass. Rem summarizes each section, then combines them.</small> : null}</div>
        <SecondBrainProvider value={provider} onChange={setProvider} />
        <div className="sb-rem-line"><ShieldCheck size={14} aria-hidden="true" /><span>{provider.name} · {provider.model}<small>Uses the provider selected above. Changing the model later does not change the evidence.</small></span></div>
        {chosenCollection ? <p className="sb-hint"><Library size={12} aria-hidden="true" /> The report records “{chosenCollection.name}” as its evidence set.</p> : null}
      </div>
    </div>
  </Modal>;
}

export function reportsAffectedBy(store, docId) {
  return store.reports.filter(report => report.sourceVersions.some(source => source.docId === docId && documentById(store, docId)?.version !== source.version));
}
