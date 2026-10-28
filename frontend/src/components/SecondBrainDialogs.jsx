import SecondBrainProvider, { useResearchSelection } from "./SecondBrainProvider.jsx";
import { useState } from "react";
import { AlertTriangle, CircleCheck, FolderOpen, Library, ShieldCheck, Sparkles, FileArchive, Files, Quote } from "lucide-react";
import { formatBytes, parseDateBound, searchLibrary, wordDiff, formatDay, formatTime } from "../lib/secondBrain.js";
import { useSecondBrain, viewFor, closeDialog, openDialog, createBrain, removeBrain, updateBrain, createCollection, updateCollection, addToCollection, navigate, saveSearch, documentById, blockById, updateReportCitation, updateClaim, updateReport, updateDocument, resolveHealth, startTask, notify, openDocument, captureSource, libraryFor, uid, getState, isSampleLibrary } from "../lib/secondBrainStore.js";
import { brainRequest } from "../lib/secondBrainApi.js";
import { DraftInput, Modal, Segmented, TypeIcon, askRem } from "./SecondBrainParts.jsx";
import { ReportBuilder, reportsAffectedBy } from "./SecondBrainReports.jsx";

const basename = path => path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
async function browse(options, current) {
  try { return await window.goferDesktop?.workspace?.selectPath?.({ ...options, currentPath: current || "" }); } catch { return ""; }
}
function PathField({ label, value, onChange, directory = true, placeholder, hint, save = false }) {
  return <div className="sb-field"><span>{label}</span><div className="sb-path-input"><DraftInput label={label} className="sb-mono" placeholder={placeholder} value={value} onCommit={onChange} /><button type="button" className="sb-button" onClick={async () => { const path = await browse({ directoryOnly: directory, fileOnly: !directory, save }, value); if (path) onChange(path); }}><FolderOpen size={14} />Browse…</button></div>{hint ? <small>{hint}</small> : null}</div>;
}
function Footer({ onCancel, confirm, disabled, onConfirm, danger, note }) {
  return <>{note ? <span className="sb-footer-note">{note}</span> : null}<button type="button" className="sb-button" onClick={onCancel}>Cancel</button><button type="button" className="sb-button sb-primary" data-danger={danger || undefined} disabled={disabled} onClick={onConfirm}>{confirm}</button></>;
}
function MetadataChoice({ value, onChange }) {
  return <fieldset className="sb-checklist"><legend>Library metadata</legend>
    <label><input type="radio" name="sb-new-metadata" checked={value === "portable"} onChange={() => onChange("portable")} /><span>Store in the brain folder<small>Collections and citations travel with the folder in <code className="sb-mono">.raticode/</code>.</small></span></label>
    <label><input type="radio" name="sb-new-metadata" checked={value === "app"} onChange={() => onChange("app")} /><span>Store in Raticode only<small>Best for read-only or shared folders.</small></span></label>
  </fieldset>;
}

function OpenFolder({ onClose }) {
  const [root, setRoot] = useState("");
  const [name, setName] = useState("");
  const [metadata, setMetadata] = useState("portable");
  const shown = name || basename(root);
  return <Modal title="Open existing folder" description="Use a folder you already keep notes in. Files stay where they are and remain usable in other apps." onClose={onClose}
    footer={<Footer onCancel={onClose} confirm="Open brain" disabled={!root.trim()} onConfirm={async () => { const brain = await createBrain({ name: shown || "Untitled brain", root, metadata }); if (brain) { onClose(); navigate("search"); } }} />}>
    <PathField label="Folder" value={root} onChange={setRoot} placeholder="/home/you/Notes" hint="Obsidian vaults work as they are. Their .obsidian folder is kept and not indexed." />
    <label className="sb-field"><span>Name</span><input value={name} placeholder={basename(root) || "Brain name"} onChange={event => setName(event.target.value)} /></label>
    <MetadataChoice value={metadata} onChange={setMetadata} />
  </Modal>;
}

function CreateBrain({ onClose }) {
  const [name, setName] = useState("");
  const [parent, setParent] = useState(getState().capabilities?.home || "");
  const [starter, setStarter] = useState(true);
  const [metadata, setMetadata] = useState("portable");
  const folder = `${parent.replace(/[\\/]+$/, "")}/${(name.trim() || "New brain").replace(/[\\/:*?"<>|]+/g, "-")}`;
  return <Modal title="Create brain" description="Creates a new folder for notes, captures and reports." onClose={onClose}
    footer={<Footer onCancel={onClose} confirm="Create brain" disabled={!name.trim()} onConfirm={async () => { const brain = await createBrain({ name: name.trim(), root: folder, metadata, create: true, starter }); if (brain) { onClose(); navigate("search"); } }} />}>
    <label className="sb-field"><span>Name</span><input value={name} placeholder="e.g. Field research" onChange={event => setName(event.target.value)} /></label>
    <PathField label="Location" value={parent} onChange={setParent} />
    <p className="sb-hint">Folder: <code className="sb-mono">{folder}</code></p>
    <label className="sb-check"><input type="checkbox" checked={starter} onChange={event => setStarter(event.target.checked)} />Add starter folders: Inbox, Captures, Reports</label>
    <MetadataChoice value={metadata} onChange={setMetadata} />
  </Modal>;
}

function ImportBrain({ onClose }) {
  const [step, setStep] = useState("choose");
  const [file, setFile] = useState("");
  const [identity, setIdentity] = useState("copy");
  const [duplicates, setDuplicates] = useState("independent");
  const [destination, setDestination] = useState(`${getState().capabilities?.home || ""}/Imported brain`);
  const [preview, setPreview] = useState(null);
  const [previewError, setPreviewError] = useState("");
  const [checking, setChecking] = useState(false);
  async function previewImport() {
    setChecking(true); setPreviewError("");
    try { setPreview(await brainRequest("import_preview", { path: file })); setStep("preview"); }
    catch (error) { setPreviewError(error.message); }
    finally { setChecking(false); }
  }
  const run = () => { onClose(); void startTask({ kind: "import", label: `Importing “${preview.name}”`, path: file, destination, identity, duplicates }); };
  return <Modal wide={step === "preview"} title={step === "choose" ? "Import brain" : `Import “${preview.name}”`} description={step === "choose" ? "Import a Raticode brain package (.zip). Nothing is written until you review the preview." : `${preview.format} · exported ${formatDay(preview.exported)} · ${formatBytes(preview.size)}`} onClose={onClose}
    footer={step === "choose" ? <Footer onCancel={onClose} confirm="Preview import" disabled={!file.trim() || checking} onConfirm={previewImport} /> : <><button type="button" className="sb-button" onClick={() => setStep("choose")}>Back</button><Footer onCancel={onClose} confirm="Import" onConfirm={run} note="Import runs in the background and can be cancelled." /></>}>
    {step === "choose" ? <>
      <PathField label="Package" directory={false} value={file} onChange={setFile} placeholder="/home/you/Downloads/field-research.zip" />
      {previewError ? <p role="alert" className="sb-inline-note" data-tone="warning">{previewError}</p> : null}
      <p className="sb-hint">To use an ordinary folder of notes instead, choose Open existing folder. Obsidian vaults and Zotero bibliographies can be added from brain settings.</p>
    </> : <div className="sb-import-preview">
      <div className="sb-import-counts">{preview.counts.map(([label, count]) => <div key={label}><strong>{count.toLocaleString()}</strong><span>{label}</span></div>)}</div>
      <p className="sb-inline-note" data-tone="success"><ShieldCheck size={14} aria-hidden="true" />Package checked: every path stays inside the destination, no links or special files, and the expanded size is within limits.</p>
      <section><h4>Needs your attention</h4><ul className="sb-issue-list">
        {preview.unsupported.map(([what, effect]) => <li key={what}><AlertTriangle size={14} aria-hidden="true" /><span><strong>{what}</strong><small>{effect}</small></span></li>)}
        <li><Files size={14} aria-hidden="true" /><span><strong>{preview.duplicates} documents match documents already in opened brains</strong><small>Matched by document ID and content hash.</small></span></li>
        {preview.conflicts.map(text => <li key={text}><AlertTriangle size={14} aria-hidden="true" /><span><strong>Destination conflict</strong><small>{text}</small></span></li>)}
        {preview.losses.map(text => <li key={text}><AlertTriangle size={14} aria-hidden="true" /><span><strong>Metadata change</strong><small>{text}</small></span></li>)}
      </ul></section>
      <div className="sb-form-grid">
        <fieldset className="sb-checklist"><legend>Brain identity</legend>
          <label><input type="radio" name="sb-identity" checked={identity === "copy"} onChange={() => setIdentity("copy")} /><span>Import as a new brain<small>Gets a new identity. Use when you want an independent copy.</small></span></label>
          <label><input type="radio" name="sb-identity" checked={identity === "restore"} onChange={() => setIdentity("restore")} /><span>Restore this brain<small>Keeps the original identity. Use when moving to a new computer.</small></span></label>
        </fieldset>
        <fieldset className="sb-checklist"><legend>Duplicate documents</legend>
          <label><input type="radio" name="sb-dupes" checked={duplicates === "independent"} onChange={() => setDuplicates("independent")} /><span>Keep independent copies</span></label>
          <label><input type="radio" name="sb-dupes" checked={duplicates === "merge"} onChange={() => setDuplicates("merge")} /><span>Keep known versions<small>Source history is retained. Copies keep independent identities across brains.</small></span></label>
        </fieldset>
      </div>
      <PathField label="Destination folder" value={destination} onChange={setDestination} />
    </div>}
  </Modal>;
}

function ExportBrain({ purpose: initialPurpose = "complete", collectionId, onClose }) {
  const store = useSecondBrain();
  const brain = store.brains.find(item => item.id === store.activeBrainId);
  const collections = store.collections.filter(item => item.brainId === brain.id);
  const [purpose, setPurpose] = useState(initialPurpose);
  const [collection, setCollection] = useState(collectionId || collections[0]?.id || "");
  const [snapshots, setSnapshots] = useState(true);
  const [verify, setVerify] = useState(true);
  const [destination, setDestination] = useState(`${store.capabilities?.home || ""}/${brain.name.toLowerCase().replace(/\s+/g, "-")}.zip`);
  const reports = store.reports.filter(report => report.brainId === brain.id && (purpose !== "collection" || report.collectionId === collection));
  const options = [
    ["documents", "Export documents", "Readable Markdown, HTML and original attachments for any app. No library metadata.", Files],
    ["collection", "Export research collection", "Selected sources, exact passages, locators and a bibliography.", Library],
    ["complete", "Export complete brain", "Every document plus collections, saved searches, citations, annotations and report provenance.", FileArchive],
  ];
  const size = store.documents.filter(doc => doc.brainId === brain.id).reduce((sum, doc) => sum + doc.size, 0);
  const snapshotSize = new TextEncoder().encode(JSON.stringify(store.documents.filter(doc => doc.brainId === brain.id).map(doc => doc.versions))).length;
  return <Modal wide title={`Export ${brain.name}`} description="Choose what the export is for. Each option includes different data." onClose={onClose}
    footer={<Footer onCancel={onClose} confirm="Export" disabled={purpose === "collection" && !collection} note={`Estimated size ${formatBytes(size + (snapshots && purpose !== "documents" ? snapshotSize : 0))}`} onConfirm={() => { onClose(); void startTask({ kind: "export", label: `Exporting ${brain.name}`, destination, purpose, collectionId: collection, snapshots, verify }); }} />}>
    <div className="sb-option-cards" role="radiogroup" aria-label="Export purpose">{options.map(([id, title, text, Icon]) => <label key={id} className="sb-option-card" data-checked={purpose === id || undefined}><input type="radio" name="sb-export" checked={purpose === id} onChange={() => setPurpose(id)} /><Icon size={18} aria-hidden="true" /><strong>{title}</strong><small>{text}</small></label>)}</div>
    {purpose === "collection" ? <label className="sb-field"><span>Collection</span><select value={collection} onChange={event => setCollection(event.target.value)}>{collections.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> : null}
    {purpose !== "documents" ? <>
      <label className="sb-check"><input type="checkbox" checked={snapshots} onChange={event => setSnapshots(event.target.checked)} />Include optional source history (+{formatBytes(snapshotSize)})</label>
      {!snapshots && reports.length ? <p className="sb-inline-note" data-tone="warning"><AlertTriangle size={14} aria-hidden="true" />Versions used by {reports.length} report{reports.length === 1 ? "" : "s"} are always retained. Other source history will be omitted.</p> : null}
    </> : <p className="sb-hint">Collections, citations and report provenance are not included. Use Export complete brain to keep them.</p>}
    {purpose === "complete" ? <label className="sb-check"><input type="checkbox" checked={verify} onChange={event => setVerify(event.target.checked)} />Verify that the package restores (recommended for backups)</label> : null}
    <PathField label="Save package as" directory={false} save value={destination} onChange={setDestination} />
    <p className="sb-hint"><ShieldCheck size={12} aria-hidden="true" /> Never exported: search indexes, downloaded models, absolute paths from this computer, folder access grants and credentials.</p>
  </Modal>;
}

function Summarize({ docIds, onClose, provider: initialProvider }) {
  const [provider, setProvider] = useResearchSelection(initialProvider);
  const store = useSecondBrain();
  const brain = store.brains.find(item => item.id === store.activeBrainId);
  const docs = docIds.map(id => documentById(store, id)).filter(Boolean);
  const [coverage, setCoverage] = useState("whole");
  const [sections, setSections] = useState(() => docs[0]?.blocks.map(block => block.id) || []);
  const [focus, setFocus] = useState("");
  const [save, setSave] = useState(true);
  const single = docs.length === 1;
  const tokens = docs.reduce((total, doc) => total + doc.size / 4, 0);
  const passages = coverage === "sections" && single ? docs[0].blocks.filter(block => sections.includes(block.id)).map(block => ({ doc: docs[0], block })) : docs.map(doc => ({ doc }));
  const coverageText = { whole: "the whole document", sections: "the selected sections", excerpts: "only the excerpts relevant to the focus question" }[coverage];
  return <Modal title={single ? `Summarize “${docs[0].title}”` : `Summarize ${docs.length} documents`} description={save ? "Rem creates a cited summary in Summaries/. Its sources and coverage are recorded." : "Attach the selected evidence to your current Rem thread and prepare a summary prompt."} onClose={onClose}
    footer={<Footer onCancel={onClose} confirm={<><Sparkles size={14} />Summarize with Rem</>} disabled={(coverage === "excerpts" && !focus.trim()) || (coverage === "sections" && !sections.length)} onConfirm={() => {
      if (save) {
        void startTask({ kind: "report", label: "Creating cited summary", docIds, question: `Summarize ${coverageText}${focus.trim() ? `, focusing on ${focus.trim()}` : ""}. State the coverage and what was not covered.`, provider, coverage, blockIds: coverage === "sections" ? sections : undefined, destination: "Summaries", format: "html", template: "summary", review: true, allowCloudForTask: true });
        onClose(); return;
      }
      askRem({ brain, doc: docs[0], passages, title: `Summary sources from ${brain.name}`, draft: `Summarize ${single ? "this document" : "these documents"} using ${coverageText}${focus.trim() ? `, focusing on: ${focus.trim()}` : ""}. State the coverage at the top, cite each source-derived point with its section, and say what was not covered.${!single ? " Note where the sources agree or differ." : ""}${save ? " Save the summary as a new generated artifact in Summaries/ and link it to its sources." : ""}` });
      onClose();
    }} />}>
    {!single ? <ul className="sb-doc-chips">{docs.map(doc => <li key={doc.id}><TypeIcon type={doc.type} size={13} />{doc.title}</li>)}</ul> : null}
    <fieldset className="sb-checklist"><legend>What should Rem read?</legend>
      <label><input type="radio" name="sb-sum" checked={coverage === "whole"} onChange={() => setCoverage("whole")} /><span>The whole {single ? "document" : "set"}<small>≈{Math.round(tokens).toLocaleString()} tokens.{tokens > 150000 ? " Too large for one pass — Rem summarizes each section with its locator, then combines them." : ""}</small></span></label>
      {single ? <label><input type="radio" name="sb-sum" checked={coverage === "sections"} onChange={() => setCoverage("sections")} /><span>Selected sections</span></label> : null}
      <label><input type="radio" name="sb-sum" checked={coverage === "excerpts"} onChange={() => setCoverage("excerpts")} /><span>Relevant excerpts only<small>Faster, but the summary will say it is partial.</small></span></label>
    </fieldset>
    {coverage === "sections" && single ? <fieldset className="sb-checklist sb-indent"><legend className="sb-sr-only">Sections</legend>{docs[0].blocks.map(block => <label key={block.id}><input type="checkbox" checked={sections.includes(block.id)} onChange={() => setSections(sections.includes(block.id) ? sections.filter(id => id !== block.id) : [...sections, block.id])} /><span>{block.heading}</span></label>)}</fieldset> : null}
    <label className="sb-field"><span>Focus {coverage === "excerpts" ? "" : "(optional)"}</span><input value={focus} placeholder="e.g. decisions about citation anchoring" onChange={event => setFocus(event.target.value)} /></label>
    <label className="sb-check"><input type="checkbox" checked={save} onChange={event => setSave(event.target.checked)} />Save the summary as a generated document linked to its sources</label>
    {save ? <SecondBrainProvider value={provider} onChange={setProvider} /> : <p className="sb-hint">Uses the provider and model selected in your Rem thread.</p>}
    <p className="sb-hint">{provider.name} · {provider.model}. Generated summaries are excluded from report evidence unless you include them.</p>
  </Modal>;
}

function VersionDiff({ docId, from, blockId, citation, reportId, onClose }) {
  const store = useSecondBrain();
  const doc = documentById(store, docId);
  const block = blockById(doc, blockId) || doc.blocks[0];
  const version = doc.versions.find(item => item.hash === from);
  const before = version?.blocks?.[block?.id] || citation?.snapshot || citation?.quote || block?.text || Object.values(version?.blocks || {}).join("\n\n");
  const after = block?.text || "No readable passage in the current source.";
  const parts = wordDiff(before, after);
  const quoteStillPresent = citation ? after.toLowerCase().includes(citation.quote.toLowerCase()) : null;
  const affected = reportsAffectedBy(store, docId);
  return <Modal wide title={`Changes to “${doc.title}”`} description={`Version ${from} compared with the current version ${doc.version}${block ? ` · ${block.heading}` : ""}.`} onClose={onClose}
    footer={citation ? <><span className="sb-footer-note">The report keeps version {from} until you accept a change.</span><button type="button" className="sb-button" onClick={() => { updateReportCitation(reportId, citation.id, { state: "unreviewed" }); onClose(); }}>Keep original and flag claim</button><button type="button" className="sb-button sb-primary" onClick={() => { if (!quoteStillPresent) { onClose(); openDialog("relocate", { citation, reportId }); return; } updateReportCitation(reportId, citation.id, { state: "unreviewed", version: doc.version, blockId: doc.blocks.find(item => item.text.includes(citation.quote))?.id || citation.blockId, quote: citation.quote }); onClose(); notify("Citation updated to the current version."); }}>{quoteStillPresent ? "Use current passage" : "Choose replacement passage"}</button></> : <button type="button" className="sb-button" onClick={onClose}>Close</button>}>
    <div className="sb-diff">
      <div><h4>Version {from}<small>{version ? formatTime(version.at) : ""}</small></h4><p>{parts.filter(part => part.kind !== "added").map((part, index) => part.kind === "removed" ? <del key={index}>{part.text}</del> : <span key={index}>{part.text}</span>)}</p></div>
      <div><h4>Current · {doc.version}<small>{formatTime(doc.dates.modified)}</small></h4><p>{parts.filter(part => part.kind !== "removed").map((part, index) => part.kind === "added" ? <ins key={index}>{part.text}</ins> : <span key={index}>{part.text}</span>)}</p></div>
    </div>
    {citation ? <p className="sb-inline-note" data-tone={quoteStillPresent ? "success" : "warning"}>{quoteStillPresent ? <CircleCheck size={14} aria-hidden="true" /> : <AlertTriangle size={14} aria-hidden="true" />}{quoteStillPresent ? "The cited quote still appears in the current version." : <>The cited quote “{citation.quote}” no longer appears. Check that the claim is still supported before using the current passage.</>}</p> : null}
    {affected.length ? <section><h4>Reports that used an older version</h4><ul className="sb-issue-list">{affected.map(report => <li key={report.id}><Quote size={14} aria-hidden="true" /><span><button type="button" className="sb-link" onClick={() => { onClose(); navigate("reports", { reportId: report.id }); }}>{report.title}</button><small>Generated {formatDay(report.generatedAt, { relative: true })}</small></span></li>)}</ul></section> : null}
  </Modal>;
}

function Relocate({ citation, reportId, issueId, onClose }) {
  const store = useSecondBrain();
  const doc = documentById(store, citation.docId);
  const words = citation.quote.toLowerCase().split(/\W+/).filter(word => word.length > 3);
  const candidates = doc.blocks.map(block => ({ block, score: words.filter(word => block.text.toLowerCase().includes(word)).length })).filter(item => item.score).sort((a, b) => b.score - a.score).slice(0, 3);
  const [choice, setChoice] = useState(candidates[0]?.block.id || "");
  return <Modal title="Confirm citation location" description={`The quote no longer matches one exact place in “${doc.title}”. Choose the passage that supports the claim.`} onClose={onClose}
    footer={<><button type="button" className="sb-button" onClick={() => { updateReportCitation(reportId, citation.id, { state: "unavailable" }); if (issueId) resolveHealth(issueId); onClose(); }}>No passage supports it</button><button type="button" className="sb-button sb-primary" disabled={!choice} onClick={() => { updateReportCitation(reportId, citation.id, { state: "unreviewed", blockId: choice, version: doc.version, quote: blockById(doc, choice).text }); if (issueId) resolveHealth(issueId); onClose(); notify("Citation location confirmed."); }}>Use selected passage</button></>}>
    <p className="sb-cited-quote"><small>Cited text</small>“{citation.quote}”</p>
    <fieldset className="sb-candidates"><legend>Possible locations</legend>{candidates.map(({ block }) => <label key={block.id} data-checked={choice === block.id || undefined}><input type="radio" name="sb-relocate" checked={choice === block.id} onChange={() => setChoice(block.id)} /><span><strong>{block.heading}</strong><small>{block.text}</small></span></label>)}</fieldset>
  </Modal>;
}

function Conflict({ conflict, onClose }) {
  const store = useSecondBrain();
  const sides = [conflict.left, conflict.right].map(side => ({ doc: documentById(store, side.docId), block: blockById(documentById(store, side.docId), side.blockId) }));
  return <Modal wide title="Inspect both passages" description={conflict.summary} onClose={onClose} footer={<button type="button" className="sb-button" onClick={onClose}>Done</button>}>
    <div className="sb-diff">{sides.map(({ doc, block }) => <div key={doc.id}><h4><TypeIcon type={doc.type} size={14} />{doc.title}<small>{block.heading}</small></h4><p>{block.text}</p><button type="button" className="sb-mini-button" onClick={() => { onClose(); openDocument(doc.id, { blockId: block.id, preview: false }); }}>Open in reader</button></div>)}</div>
  </Modal>;
}

function FindSupport({ claim, reportId, onClose }) {
  const store = useSecondBrain();
  const library = libraryFor(store);
  const words = claim.text.toLowerCase().split(/\W+/).filter(word => word.length > 4).slice(0, 6);
  const { results } = searchLibrary(library.documents, { text: words.join(" "), match: "any", filters: { includeGenerated: false } }, library);
  const candidates = results.flatMap(result => result.passages.map(passage => ({ doc: result.doc, block: passage.block }))).slice(0, 4);
  const [choice, setChoice] = useState("");
  return <Modal wide title="Find a citation for this claim" description="Candidate passages from this brain. Choose one only if it actually supports the claim." onClose={onClose}
    footer={<><button type="button" className="sb-button" onClick={() => { updateClaim(reportId, claim.id, { support: "unsupported", reviewed: true }); onClose(); }}>No adequate support found</button><button type="button" className="sb-button sb-primary" disabled={!choice} onClick={() => {
      const pick = candidates.find(item => item.block.id === choice);
      const citationId = uid("c");
      updateReport(reportId, report => ({ citations: [...report.citations, { id: citationId, n: report.citations.length + 1, docId: pick.doc.id, blockId: pick.block.id, version: pick.doc.version, quote: pick.block.text.split(/[.;]/)[0], state: "found" }], claims: report.claims.map(item => item.id === claim.id ? { ...item, citations: [...item.citations, citationId], support: "paraphrased" } : item) }));
      onClose(); notify("Citation added. Mark the claim reviewed when you're satisfied.");
    }}>Use as support</button></>}>
    <p className="sb-cited-quote"><small>Claim</small>{claim.text}</p>
    {candidates.length ? <fieldset className="sb-candidates"><legend>Candidates</legend>{candidates.map(({ doc, block }) => <label key={block.id} data-checked={choice === block.id || undefined}><input type="radio" name="sb-support" checked={choice === block.id} onChange={() => setChoice(block.id)} /><span><strong>{doc.title} · {block.heading}</strong><small>{block.text}</small></span></label>)}</fieldset> : <p className="sb-hint">No candidate passages found in this brain.</p>}
  </Modal>;
}

function EditMetadata({ docId, onClose }) {
  const store = useSecondBrain();
  const doc = documentById(store, docId);
  const [draft, setDraft] = useState({ title: doc.title, author: doc.author || "", published: doc.dates.published || "", tags: doc.tags.join(", "), url: doc.source?.url || "", doi: doc.source?.doi || "" });
  const invalidDate = draft.published && parseDateBound(draft.published) === null;
  const set = patch => setDraft({ ...draft, ...patch });
  return <Modal title="Edit metadata" description={doc.path} onClose={onClose}
    footer={<Footer onCancel={onClose} confirm="Save" disabled={invalidDate || !draft.title.trim()} onConfirm={() => {
      updateDocument(docId, current => ({ title: draft.title.trim(), author: draft.author.trim(), tags: draft.tags.split(",").map(tag => tag.trim().toLowerCase()).filter(Boolean), dates: { ...current.dates, published: draft.published ? parseDateBound(draft.published) : "" }, source: draft.url || draft.doi ? { ...current.source, url: draft.url, doi: draft.doi, domain: (() => { try { return new URL(draft.url).hostname.replace(/^www\./, ""); } catch { return current.source?.domain || ""; } })() } : current.source }));
      if (draft.author.trim() && draft.published) resolveHealth(getState().health.filter(issue => issue.docId === docId && issue.kind === "metadata").map(issue => issue.id));
      onClose(); notify("Metadata saved.");
    }} note="Stored in library metadata." />}>
    <div className="sb-form-grid">
      <label className="sb-field"><span>Title</span><input value={draft.title} onChange={event => set({ title: event.target.value })} /></label>
      <label className="sb-field"><span>Author</span><input value={draft.author} placeholder="Unknown" onChange={event => set({ author: event.target.value })} /></label>
      <label className="sb-field"><span>Published</span><input value={draft.published} placeholder="Unknown — e.g. 2026-03-14" aria-invalid={invalidDate || undefined} onChange={event => set({ published: event.target.value })} />{invalidDate ? <small className="sb-field-error">Use YYYY, YYYY-MM or YYYY-MM-DD.</small> : <small>Leave empty if unknown. Raticode won’t guess it.</small>}</label>
      <label className="sb-field"><span>Tags</span><input value={draft.tags} placeholder="comma, separated" onChange={event => set({ tags: event.target.value })} /></label>
      <label className="sb-field"><span>Source URL</span><input value={draft.url} placeholder="https://" onChange={event => set({ url: event.target.value })} /></label>
      <label className="sb-field"><span>DOI</span><input value={draft.doi} placeholder="10.xxxx/…" onChange={event => set({ doi: event.target.value })} /></label>
    </div>
  </Modal>;
}

function Capture({ mode: initialMode = "url", onClose }) {
  const [mode, setMode] = useState(initialMode);
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [excerpt, setExcerpt] = useState("");
  const validUrl = (() => { try { return ["http:", "https:"].includes(new URL(url).protocol); } catch { return false; } })();
  return <Modal title="Capture to Inbox" description="Captured sources wait in Inbox until you review them." onClose={onClose}
    footer={<Footer onCancel={onClose} confirm="Save to Inbox" disabled={mode === "url" ? !validUrl : !excerpt.trim()} onConfirm={() => { captureSource({ title: title.trim(), url: mode === "url" ? url : "", excerpt: excerpt.trim() }); onClose(); }} />}>
    <Segmented label="Capture type" value={mode} options={[["url", "Web page"], ["excerpt", "Excerpt"]]} onChange={setMode} />
    {mode === "url" ? <label className="sb-field"><span>Page address</span><input value={url} placeholder="https://" onChange={event => setUrl(event.target.value)} aria-invalid={url && !validUrl ? true : undefined} />{url && !validUrl ? <small className="sb-field-error">Enter a full http or https address.</small> : <small>Raticode saves a readable copy with the address and capture time. Scripts are not kept.</small>}</label> : null}
    <label className="sb-field"><span>Title {mode === "url" ? "(optional)" : ""}</span><input value={title} placeholder={mode === "url" ? "Taken from the page" : "Excerpt title"} onChange={event => setTitle(event.target.value)} /></label>
    {mode === "excerpt" ? <label className="sb-field"><span>Excerpt</span><textarea rows={5} value={excerpt} placeholder="Paste text" onChange={event => setExcerpt(event.target.value)} /></label> : null}
  </Modal>;
}

function Duplicate({ issue, onClose }) {
  const store = useSecondBrain();
  const [a, b] = [documentById(store, issue.otherId || issue.docs?.[0]), documentById(store, issue.docId)];
  return <Modal wide title="Exact duplicates" description="These files have identical content. Raticode doesn't assume they are the same document, so citations stay attached to the copy they name." onClose={onClose}
    footer={<><button type="button" className="sb-button" onClick={() => { resolveHealth(issue.id); onClose(); notify("Both copies kept as independent documents."); }}>Keep both</button><button type="button" className="sb-button sb-primary" onClick={() => { updateDocument(b.id, { extraction: "ready", excluded: true }); resolveHealth(issue.id); onClose(); notify(`Excluded ${b.path} from search. The file was not deleted.`); }}>Exclude the copy from search</button></>}>
    <div className="sb-diff">{[a, b].map(doc => <div key={doc.id}><h4><TypeIcon type={doc.type} size={14} />{doc.title}<small className="sb-mono">{doc.path}</small></h4><dl className="sb-facts">{[["Added", formatDay(doc.dates.added)], ["Modified", formatDay(doc.dates.modified)], ["Version", doc.version], ["Cited by", `${(libraryFor(store).citedBy[doc.id] || []).length} items`]].map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}</dl></div>)}</div>
  </Modal>;
}

function Ocr({ docIds, onClose }) {
  const store = useSecondBrain();
  const brain = store.brains.find(item => item.id === store.activeBrainId);
  const [language, setLanguage] = useState(brain.settings.ocrLanguage || "eng");
  const available = isSampleLibrary() || store.capabilities?.ocr;
  const docs = docIds.map(id => documentById(store, id));
  return <Modal title={`Run OCR on ${docs.length === 1 ? `“${docs[0].title}”` : `${docs.length} documents`}`} description="Recognizes text in page images so they become searchable." onClose={onClose}
    footer={<Footer onCancel={onClose} confirm="Run OCR" disabled={!available} onConfirm={() => { onClose(); void startTask({ kind: "ocr", label: `Recognizing ${docs.length} documents`, docIds, language }); }} />}>
    <label className="sb-field"><span>Document language</span><select value={language} onChange={event => setLanguage(event.target.value)}>{[["eng", "English"], ["deu", "German"], ["fra", "French"], ["spa", "Spanish"], ["jpn", "Japanese"]].map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
    <p className="sb-hint">OCR runs on this computer using Tesseract and Poppler. Review recognized text against the original PDF before citing it.</p>
    {!available ? <p className="sb-inline-note" data-tone="warning" role="status"><AlertTriangle size={14} aria-hidden="true" /><span>Install Tesseract and Poppler, including trained data for this language, then restart Raticode to enable OCR.</span></p> : null}
  </Modal>;
}

function Encoding({ docId, onClose }) {
  const store = useSecondBrain();
  const doc = documentById(store, docId);
  const [encoding, setEncoding] = useState("windows-1252");
  return <Modal title="Choose text encoding" description={`${doc.path} isn't valid UTF-8. Pick the encoding it was saved with.`} onClose={onClose}
    footer={<Footer onCancel={onClose} confirm="Extract again" onConfirm={() => { onClose(); void startTask({ kind: "encoding", label: "Extracting document text", docId, encoding }); }} />}>
    <label className="sb-field"><span>Encoding</span><select value={encoding} onChange={event => setEncoding(event.target.value)}>{["windows-1252", "iso-8859-1", "utf-16", "shift_jis"].map(item => <option key={item}>{item}</option>)}</select></label>
    <p className="sb-hint">The original file is preserved. Extraction errors appear in the task receipt.</p>
  </Modal>;
}

function LocateLink({ issue, onClose }) {
  const [path, setPath] = useState("");
  return <Modal title="Fix broken link" description={issue.detail} onClose={onClose}
    footer={<Footer onCancel={onClose} confirm="Update link" disabled={!path.trim()} onConfirm={() => { onClose(); void startTask({ kind: "repair-link", docId: issue.docId, old: issue.target, target: path }); }} />}>
    <label className="sb-field"><span>New path inside this brain</span><input value={path} onChange={event => setPath(event.target.value)} placeholder="Architecture/package-format-v2.md" /></label>
  </Modal>;
}

function Bibliography({ onClose, exporting = false }) {
  const [format, setFormat] = useState("csl");
  const [file, setFile] = useState("");
  return <Modal title={exporting ? "Export bibliography" : "Import bibliography"} description="CSL JSON, BibTeX and RIS preserve source records. Imported references become searchable notes." onClose={onClose}
    footer={<Footer onCancel={onClose} confirm={exporting ? "Export" : "Import"} disabled={!file.trim()} onConfirm={() => { onClose(); void startTask({ kind: "bibliography", label: exporting ? "Exporting bibliography" : "Importing bibliography", path: file, export: exporting }); }} />}>
    <Segmented label="Bibliography format" value={format} options={[["csl", "CSL-JSON"], ["bibtex", "BibTeX"], ["ris", "RIS"]]} onChange={setFormat} />
    <PathField label="File" directory={false} value={file} onChange={setFile} save={exporting} placeholder={format === "bibtex" ? "library.bib" : format === "ris" ? "library.ris" : "library.json"} />
    <p className="sb-hint">{format === "zotero" ? "Zotero collections import as tags; annotations are not carried over." : "Original CSL fields are retained with imported records. Export a JSON bibliography to preserve them."}</p>
  </Modal>;
}

function NewCollection({ items = [], onClose }) {
  const [name, setName] = useState("");
  const [brief, setBrief] = useState("");
  return <Modal title="New collection" description={items.length ? `Starts with ${items.length} item${items.length === 1 ? "" : "s"}.` : "Gather documents and passages for a question."} onClose={onClose}
    footer={<Footer onCancel={onClose} confirm="Create collection" disabled={!name.trim()} onConfirm={() => { const collection = createCollection(name.trim(), []); if (brief.trim()) updateCollection(collection.id, { brief: brief.trim() }); if (items.length) addToCollection(collection.id, items); onClose(); navigate("collections", { collectionId: collection.id, collectionTab: "evidence" }); }} />}>
    <label className="sb-field"><span>Name</span><input value={name} placeholder="e.g. Citation design evidence" onChange={event => setName(event.target.value)} /></label>
    <label className="sb-field"><span>Research question (optional)</span><textarea rows={3} value={brief} placeholder="What are you trying to find out?" onChange={event => setBrief(event.target.value)} /></label>
  </Modal>;
}

function SaveSearch({ onClose }) {
  const store = useSecondBrain();
  const view = viewFor(store);
  const [name, setName] = useState(view.search.text.slice(0, 40));
  return <Modal title="Save search" description="Saved searches keep the query and filters, and their results update as the library changes." onClose={onClose}
    footer={<Footer onCancel={onClose} confirm="Save" disabled={!name.trim()} onConfirm={() => { saveSearch(name.trim()); onClose(); }} />}>
    <label className="sb-field"><span>Name</span><input value={name} placeholder="e.g. Unread architecture notes" onChange={event => setName(event.target.value)} /></label>
    <p className="sb-hint">Query: <code className="sb-mono">{view.search.text || "Filters only"}</code></p>
  </Modal>;
}

function RemoveBrain({ brainId, onClose }) {
  const store = useSecondBrain();
  const brain = store.brains.find(item => item.id === brainId);
  return <Modal title={`Remove “${brain.name}” from the list?`} onClose={onClose}
    footer={<Footer onCancel={onClose} confirm="Remove from list" danger onConfirm={() => { removeBrain(brainId); onClose(); notify(`Removed “${brain.name}” from the list. Its folder was not changed.`); }} />}>
    <p>The folder <code className="sb-mono">{brain.root}</code> and every file in it stay where they are. You can open it again at any time.</p>
    <p className="sb-hint">{brain.settings.metadata === "portable" ? "Collections, citations and saved searches are stored in the folder and come back when you reopen it." : "Collections, citations and saved searches stored only in Raticode are removed. Export the brain first to keep them."} The local search index is deleted.</p>
  </Modal>;
}

function Locate({ brainId, onClose }) {
  const store = useSecondBrain();
  const brain = store.brains.find(item => item.id === brainId);
  const [root, setRoot] = useState(brain.root);
  return <Modal title={`Locate “${brain.name}”`} description="Choose the folder’s current location. Raticode checks the brain's identity before reconnecting it." onClose={onClose}
    footer={<Footer onCancel={onClose} confirm="Reconnect" disabled={!root.trim()} onConfirm={() => { updateBrain(brainId, current => ({ root: root.replace(/[\\/]+$/, ""), state: "available", lastOpened: new Date().toISOString(), index: { ...current.index, state: "updating", progress: 12, asOf: new Date().toISOString() } })); onClose(); navigate("search"); notify(`Reconnected “${brain.name}”. Checking for changes made while it was away.`); }} />}>
    <PathField label="Folder" value={root} onChange={setRoot} />
  </Modal>;
}

export default function SecondBrainDialogs({ provider }) {
  const store = useSecondBrain();
  const dialog = store.dialog;
  const brain = store.brains.find(item => item.id === store.activeBrainId);
  if (!dialog) return null;
  const close = () => closeDialog();
  const { kind, ...props } = dialog;
  if (kind === "open-folder") return <OpenFolder onClose={close} />;
  if (kind === "create") return <CreateBrain onClose={close} />;
  if (kind === "import") return <ImportBrain onClose={close} />;
  if (kind === "remove") return <RemoveBrain {...props} onClose={close} />;
  if (kind === "locate") return <Locate {...props} onClose={close} />;
  if (!brain || brain.state !== "available") return null;
  if (kind === "export") return <ExportBrain {...props} onClose={close} />;
  if (kind === "new-collection") return <NewCollection {...props} onClose={close} />;
  if (kind === "save-search") return <SaveSearch onClose={close} />;
  if (kind === "summarize") return <Summarize {...props} provider={provider} onClose={close} />;
  if (kind === "report-builder") return <ReportBuilder {...props} provider={provider} onClose={close} />;
  if (kind === "version-diff") return <VersionDiff {...props} onClose={close} />;
  if (kind === "relocate") return <Relocate {...props} onClose={close} />;
  if (kind === "conflict") return <Conflict {...props} onClose={close} />;
  if (kind === "find-support") return <FindSupport {...props} onClose={close} />;
  if (kind === "edit-metadata") return <EditMetadata {...props} onClose={close} />;
  if (kind === "capture") return <Capture {...props} onClose={close} />;
  if (kind === "duplicate") return <Duplicate {...props} onClose={close} />;
  if (kind === "ocr") return <Ocr {...props} onClose={close} />;
  if (kind === "encoding") return <Encoding {...props} onClose={close} />;
  if (kind === "locate-link") return <LocateLink {...props} onClose={close} />;
  if (kind === "bibliography") return <Bibliography {...props} onClose={close} />;
  return null;
}
