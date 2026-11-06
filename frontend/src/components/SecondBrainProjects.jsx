import { useMemo, useState } from "react";
import "./SecondBrainGraph.css";
import { ArrowLeft, FolderKanban, FolderOpen, GitMerge, MessagesSquare, Pencil, Plus, ScrollText, Search } from "lucide-react";
import { formatDay } from "../lib/secondBrain.js";
import { makerLabel, projectDocuments } from "../lib/secondBrainGraph.js";
import { graphKey, mergeProjects, navigate, openDocument, saveProject, setDocumentProject, uid, useBrainData, useSecondBrain, viewFor } from "../lib/secondBrainStore.js";
import { Basis } from "./SecondBrainConnections.jsx";
import TopicMap from "./SecondBrainMap.jsx";
import { DraftInput, Empty, Segmented, TypeIcon } from "./SecondBrainParts.jsx";

const total = counts => counts.reports + counts.knowledge + counts.conversations;

function countLine(counts) {
  const parts = [[counts.reports, "report"], [counts.knowledge, "note"], [counts.conversations, "conversation"]].filter(([count]) => count).map(([count, word]) => `${count} ${word}${count === 1 ? "" : "s"}`);
  return parts.join(" · ") || "Nothing yet";
}

function maker(report) {
  if (!report) return "";
  const provenance = report.provenance || {};
  if (!report.external) return `${makerLabel("brain_report")} · ${report.provider}`;
  return [makerLabel(provenance.savedVia || "rem_thread"), provenance.taskTitle || provenance.threadTitle].filter(Boolean).join(" · ");
}

function DocumentRows({ rows, reportsByPath, filter }) {
  const shown = rows.filter(row => !filter || `${row.doc.title} ${row.doc.path}`.toLowerCase().includes(filter.toLowerCase()));
  if (!shown.length) return <p className="sb-hint">{filter ? "Nothing matches the filter." : "Nothing here yet."}</p>;
  return <ul className="sb-project-rows">{shown.map(({ doc, assignment }) => {
    const report = reportsByPath[doc.path];
    return <li key={doc.id}>
      <TypeIcon type={doc.type} size={14} />
      <button type="button" className="sb-project-title" onClick={() => openDocument(doc.id, { preview: false })} title={doc.path}>{doc.title}</button>
      <small className="sb-muted">{[formatDay(doc.dates.modified), maker(report)].filter(Boolean).join(" · ")}</small>
      <Basis basis={assignment.basis} detail={assignment.detail} />
    </li>;
  })}</ul>;
}

function ProjectHeader({ project, projects, brain }) {
  const [renaming, setRenaming] = useState(false);
  const [aliasField, setAliasField] = useState(0);
  const others = projects.filter(item => item.id !== project.id);
  const saveName = name => {
    setRenaming(false);
    if (!name || name === project.name) return;
    // The old name stays as another name, so files and folders using it still match.
    const record = { id: project.saved ? project.id : uid("project"), brainId: brain.id, name, aliases: [...new Set([...(project.aliases || []), project.name])], roots: project.roots || [], color: project.color || "" };
    saveProject(record);
    navigate("projects", { projectId: record.id });
  };
  const addAlias = alias => {
    setAliasField(value => value + 1);
    if (!alias.trim()) return;
    const record = { id: project.saved ? project.id : uid("project"), brainId: brain.id, name: project.name, aliases: [...new Set([...(project.aliases || []), alias.trim()])], roots: project.roots || [], color: project.color || "" };
    saveProject(record);
    navigate("projects", { projectId: record.id });
  };
  return <div className="sb-page-head"><div>
    {renaming ? <DraftInput label="Project name" value={project.name} onCommit={value => saveName(value.trim())} autoFocus /> : <h3>{project.name}</h3>}
    <p>{countLine(project.counts)}{project.aliases?.length ? ` · also called ${project.aliases.join(", ")}` : ""}</p>
    {project.roots?.length ? <p className="sb-mono sb-hint">{project.roots.join(" · ")}</p> : null}
  </div>
  <div className="sb-actions">
    <button type="button" className="sb-button" onClick={() => setRenaming(true)}><Pencil size={14} />Rename</button>
    <label className="sb-inline-select"><span className="sb-sr-only">Another name for this project</span><DraftInput key={aliasField} label="Add another name" placeholder="Add another name…" value="" onCommit={addAlias} /></label>
    {others.length ? <label className="sb-inline-select"><span className="sb-sr-only">Merge this project into another</span><select value="" onChange={event => { const into = others.find(item => item.id === event.target.value); if (into) { const merged = mergeProjects(project, into); navigate("projects", { projectId: merged.id }); } }}>
      <option value="">Merge into…</option>{others.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> : null}
  </div></div>;
}

function ProjectPage({ brain, project, result, store, view }) {
  const [filter, setFilter] = useState("");
  const documents = store.documents.filter(doc => doc.brainId === brain.id);
  const { reports, knowledge } = useMemo(() => projectDocuments(result, documents, project.id), [result, documents, project.id]);
  const reportsByPath = Object.fromEntries(store.reports.filter(item => item.brainId === brain.id).map(item => [item.artifactPath, item]));
  const threads = result.conversations.filter(thread => thread.projectId === project.id);
  const tab = view.projectTab || "reports";
  return <div className="sb-page">
    <button type="button" className="sb-back" onClick={() => navigate("projects", { projectId: "" })}><ArrowLeft size={14} />All projects</button>
    <ProjectHeader project={project} projects={result.projects} brain={brain} />
    <div className="sb-project-tabs">
      <Segmented label="Show" value={tab} onChange={value => navigate("projects", { projectTab: value })} options={[["reports", `Reports ${reports.length}`], ["knowledge", `Notes ${knowledge.length}`], ["conversations", `Conversations ${threads.length}`], ["map", "Map"]]} />
      {tab === "reports" || tab === "knowledge" ? <label className="sb-filter"><Search size={13} aria-hidden="true" /><input aria-label="Filter by title" placeholder="Filter by title" value={filter} onChange={event => setFilter(event.target.value)} /></label> : null}
    </div>
    {tab === "reports" ? <DocumentRows rows={reports} reportsByPath={reportsByPath} filter={filter} />
      : tab === "knowledge" ? <DocumentRows rows={knowledge} reportsByPath={reportsByPath} filter={filter} />
      : tab === "conversations" ? (threads.length ? <ul className="sb-project-rows">{threads.map(thread => <li key={thread.threadId}>
        <MessagesSquare size={14} aria-hidden="true" /><span className="sb-project-title">{thread.title}</span>
        <small className="sb-muted">{[thread.updatedAt ? formatDay(thread.updatedAt) : "", `${thread.messageCount} messages`].filter(Boolean).join(" · ")}</small>
        <button type="button" className="sb-mini-button" onClick={() => window.goferDesktop?.workspace?.revealPath?.(`${brain.root}/${thread.snapshot}`)}><FolderOpen size={12} />Show in folder</button>
      </li>)}</ul> : <p className="sb-hint">No archived Rem conversations ran in this project’s folders. Conversations appear here when the conversation archive is stored in this brain.</p>)
      : <TopicMap brain={brain} projectId={project.id} embedded />}
  </div>;
}

function Unassigned({ result, store }) {
  const [filter, setFilter] = useState("");
  const ids = new Set(result.unassigned);
  const docs = store.documents.filter(doc => ids.has(doc.id)).filter(doc => !filter || `${doc.title} ${doc.path}`.toLowerCase().includes(filter.toLowerCase())).sort((a, b) => a.path.localeCompare(b.path));
  return <div className="sb-page">
    <button type="button" className="sb-back" onClick={() => navigate("projects", { projectId: "" })}><ArrowLeft size={14} />All projects</button>
    <div className="sb-page-head"><div><h3>Not in a project</h3><p>{ids.size} documents have no project in their folder, file or history. Add one, or leave them; nothing is moved.</p></div></div>
    <label className="sb-filter"><Search size={13} aria-hidden="true" /><input aria-label="Filter by title or path" placeholder="Filter by title or path" value={filter} onChange={event => setFilter(event.target.value)} /></label>
    <ul className="sb-project-rows">{docs.slice(0, 300).map(doc => <li key={doc.id}>
      <TypeIcon type={doc.type} size={14} />
      <button type="button" className="sb-project-title" onClick={() => openDocument(doc.id, { preview: false })} title={doc.path}>{doc.title}</button>
      <small className="sb-mono sb-muted">{doc.path}</small>
      <label className="sb-inline-select"><span className="sb-sr-only">Add {doc.title} to a project</span><select value="" onChange={event => event.target.value && setDocumentProject(doc.id, event.target.value)}>
        <option value="">Add to…</option>{result.projects.map(project => <option key={project.id} value={project.name}>{project.name}</option>)}</select></label>
    </li>)}</ul>
    {docs.length > 300 ? <p className="sb-hint">Showing 300 of {docs.length}. Filter to find others.</p> : null}
  </div>;
}

export function ProjectsView({ brain }) {
  const store = useSecondBrain();
  const view = viewFor(store);
  const { data, error, loading } = useBrainData("projects", { brainId: brain.id }, graphKey(store, brain.id));
  const [creating, setCreating] = useState(false);
  if (error) return <div className="sb-page"><p role="alert">{error}</p></div>;
  if (!data) return <div className="sb-page"><p role="status" className="sb-hint">{loading ? "Finding projects…" : ""}</p></div>;
  if (view.projectId === "unassigned") return <Unassigned result={data} store={store} />;
  const project = data.projects.find(item => item.id === view.projectId);
  if (project) return <ProjectPage brain={brain} project={project} result={data} store={store} view={view} />;
  const merges = data.suggestedMerges.map(item => [data.projects.find(p => p.id === item.from), data.projects.find(p => p.id === item.into)]).filter(([a, b]) => a && b);
  return <div className="sb-page">
    <div className="sb-page-head"><div><h3>Projects</h3><p>Reports, notes and conversations grouped by project. Each one shows how Raticode knows: a Rem run that saved it, the file itself, its folder, or you.</p></div>
      <div className="sb-actions">{creating ? <DraftInput label="New project name" placeholder="Project name" value="" autoFocus onCommit={name => { setCreating(false); if (name.trim()) saveProject({ id: uid("project"), brainId: brain.id, name: name.trim(), aliases: [], roots: [], color: "" }); }} /> : <button type="button" className="sb-button" onClick={() => setCreating(true)}><Plus size={14} />New project</button>}</div></div>
    {data.projects.length ? <ul className="sb-project-list">{data.projects.map(item => <li key={item.id}><button type="button" onClick={() => navigate("projects", { projectId: item.id, projectTab: "reports" })}>
      <FolderKanban size={18} aria-hidden="true" />
      <span><strong>{item.name}</strong><small>{countLine(item.counts)}{item.aliases.length ? ` · also ${item.aliases.join(", ")}` : ""}</small></span>
      <span className="sb-project-bar" aria-hidden="true"><i style={{ width: `${Math.max(4, 100 * total(item.counts) / Math.max(...data.projects.map(p => total(p.counts)), 1))}%` }} /></span>
    </button></li>)}
      <li><button type="button" onClick={() => navigate("projects", { projectId: "unassigned" })}><ScrollText size={18} aria-hidden="true" /><span><strong>Not in a project</strong><small>{data.unassigned.length} documents</small></span></button></li>
    </ul> : <Empty icon={FolderKanban} title="No projects yet">Projects come from folders like Reports/&lt;Project&gt;/, a project named in a file, or the folder a Rem task worked in.</Empty>}
    {merges.length ? <details className="sb-merge-hints"><summary>{merges.length} possible duplicate{merges.length === 1 ? "" : "s"}</summary><ul>{merges.map(([from, into]) => <li key={`${from.id}-${into.id}`}>
      <span>“{from.name}” and “{into.name}”</span><button type="button" className="sb-mini-button" onClick={() => mergeProjects(from, into)}><GitMerge size={12} />Merge into {into.name}</button><button type="button" className="sb-mini-button" onClick={() => mergeProjects(into, from)}><GitMerge size={12} />Merge into {from.name}</button>
    </li>)}</ul><p className="sb-hint">Names that start the same way. Merging keeps the other name, so its folders still count.</p></details> : null}
  </div>;
}
