import { useMemo, useState } from "react";
import "./SecondBrainGraph.css";
import { Bot, FolderKanban, Link2, MessagesSquare, Network, ScrollText, Sparkles, Ticket, TriangleAlert } from "lucide-react";
import { formatDay } from "../lib/secondBrain.js";
import { basisLabel, connectionItems, egoLayout, isSuggestion, makerLabel } from "../lib/secondBrainGraph.js";
import { documentById, graphKey, navigate, openDocument, setDocumentProject, useBrainData } from "../lib/secondBrainStore.js";
import { Pill, TypeIcon } from "./SecondBrainParts.jsx";

// A basis badge says how Raticode knows a relationship; suggestions look different.
export function Basis({ basis, detail }) {
  const tone = isSuggestion(basis) || basis === "suggested" ? "brand" : basis === "asserted" ? "warning" : "neutral";
  return <span className="sb-basis" data-suggestion={isSuggestion(basis) || basis === "suggested" || undefined} title={detail || undefined}><Pill tone={tone}>{basisLabel(basis)}</Pill></span>;
}

function EgoGraph({ doc, items, store }) {
  const layout = useMemo(() => egoLayout({ title: doc.title }, items), [doc.title, items]);
  const open = item => item.docId ? openDocument(item.docId) : item.reportId ? navigate("reports", { reportId: item.reportId }) : null;
  return <figure className="sb-ego">
    <svg viewBox="0 0 520 300" role="group" aria-label={`Connections of ${doc.title}`}>
      {layout.nodes.map(node => <line key={`edge-${node.key}`} x1={layout.center.x} y1={layout.center.y} x2={node.x} y2={node.y} className="sb-edge" data-suggestion={isSuggestion(node.basis) || undefined} data-claim={node.basis === "asserted" || undefined} />)}
      <g className="sb-ego-center"><circle cx={layout.center.x} cy={layout.center.y} r="26" /><text x={layout.center.x} y={layout.center.y + 44} textAnchor="middle">{doc.title.slice(0, 34)}</text></g>
      {layout.nodes.map(node => {
        const clickable = Boolean(node.docId && documentById(store, node.docId)) || Boolean(node.reportId);
        const label = `${node.relation}: ${node.title || "Untitled"}. ${basisLabel(node.basis)}`;
        return <g key={node.key} className="sb-ego-node" data-suggestion={isSuggestion(node.basis) || undefined} role={clickable ? "button" : "img"} tabIndex={clickable ? 0 : -1} aria-label={label}
          onClick={() => clickable && open(node)} onKeyDown={event => { if (clickable && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); open(node); } }}>
          <title>{label}</title>
          <circle cx={node.x} cy={node.y} r="9" />
          <text x={node.x} y={node.y + (node.y > layout.center.y ? 24 : -15)} textAnchor="middle">{(node.title || "").slice(0, 24)}</text>
        </g>;
      })}
    </svg>
    <figcaption className="sb-hint">Solid lines are links written in files or uses Raticode saw. Dashed lines are suggestions from similar content. {layout.hidden ? `${layout.hidden} more are in the lists.` : ""}</figcaption>
  </figure>;
}

function ProjectLine({ data, doc, projects }) {
  const [choice, setChoice] = useState("");
  if (data.project) return <p className="sb-conn-line"><FolderKanban size={14} aria-hidden="true" /><button type="button" className="sb-link" onClick={() => navigate("projects", { projectId: data.project.projectId, projectTab: "reports" })}>{data.project.name}</button><Basis basis={data.project.basis} detail={data.project.detail} /><small className="sb-muted">{data.project.detail}</small></p>;
  return <div className="sb-conn-line">
    <FolderKanban size={14} aria-hidden="true" /><span className="sb-muted">Not in a project.</span>
    {data.suggestedProject ? <><span>Maybe <strong>{data.suggestedProject.name}</strong></span><Basis basis="suggested" detail={data.suggestedProject.detail} /><button type="button" className="sb-mini-button" onClick={() => setDocumentProject(doc.id, data.suggestedProject.name)}>Add to {data.suggestedProject.name}</button></> : null}
    {projects.length ? <label className="sb-inline-select"><span className="sb-sr-only">Add to a project</span><select value={choice} onChange={event => { setChoice(event.target.value); if (event.target.value) setDocumentProject(doc.id, event.target.value); }}><option value="">Add to a project…</option>{projects.map(project => <option key={project.id} value={project.name}>{project.name}</option>)}</select></label> : null}
  </div>;
}

export default function Connections({ doc, store }) {
  const key = `${doc.id}:${doc.version}:${graphKey(store, doc.brainId)}`;
  const { data, error, loading } = useBrainData("connections", { id: doc.id }, key);
  const projectsData = useBrainData("projects", { brainId: doc.brainId }, graphKey(store, doc.brainId));
  const [graph, setGraph] = useState(false);
  const title = id => documentById(store, id)?.title || "Unavailable document";
  const items = useMemo(() => connectionItems(data, title), [data, store.documents]); // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <section className="sb-connections"><h4>Connections</h4><p role="alert" className="sb-hint">{error}</p></section>;
  if (!data) return <section className="sb-connections"><h4>Connections</h4><p role="status" className="sb-hint">{loading ? "Finding connections…" : "No connections."}</p></section>;
  const similar = (data.similar || []).filter(item => !item.revision);
  const revisions = (data.similar || []).filter(item => item.revision);
  const docLink = id => documentById(store, id) ? <button type="button" className="sb-link" onClick={() => openDocument(id)}>{title(id)}</button> : <span className="sb-muted">{title(id)}</span>;
  return <section className="sb-connections" aria-labelledby={`connections-${doc.id}`}>
    <div className="sb-details-head"><h4 id={`connections-${doc.id}`}>Connections</h4>
      {items.length ? <button type="button" className="sb-mini-button" aria-pressed={graph} onClick={() => setGraph(!graph)}><Network size={13} />{graph ? "Show lists" : "Show as graph"}</button> : null}</div>
    <ProjectLine data={data} doc={doc} projects={projectsData.data?.projects || []} />
    {data.topic ? <p className="sb-conn-line"><Sparkles size={14} aria-hidden="true" />Topic <button type="button" className="sb-link" onClick={() => navigate("map", { mapTopicId: data.topic.id })}>{data.topic.label}</button><Basis basis="suggested" detail="Grouped by similar content and links" /><small className="sb-muted">{data.topic.size} documents</small></p> : null}
    {graph ? <EgoGraph doc={doc} items={items} store={store} /> : <div className="sb-conn-groups">
      <div><h5>Made by</h5>{data.madeBy.length ? <ul>{data.madeBy.map((item, index) => <li key={index}>
        {item.kind === "organization" ? <Ticket size={13} aria-hidden="true" /> : <Bot size={13} aria-hidden="true" />}
        <span><strong>{makerLabel(item.kind)}</strong>{item.title ? ` · ${item.title}` : ""}{item.organization ? ` · ${item.organization}${item.ticket ? ` ${item.ticket}` : ""}` : ""}
          <small>{[item.at ? formatDay(item.at) : "", item.provider, item.model].filter(Boolean).join(" · ")}</small>
          {item.hashMatches === false ? <small className="sb-warn-text"><TriangleAlert size={11} aria-hidden="true" />The file changed after it was saved.</small> : null}</span>
        <Basis basis={item.basis} />
      </li>)}</ul> : <p className="sb-hint">No record of who made this file. Files you write yourself have none.</p>}</div>
      <div><h5>Links</h5>{data.links.cites.length || data.links.citedBy.length ? <ul>
        {data.links.cites.map(id => <li key={`to-${id}`}><Link2 size={13} aria-hidden="true" /><span>Links to {docLink(id)}</span><Basis basis="written" /></li>)}
        {data.links.citedBy.map(id => <li key={`from-${id}`}><Link2 size={13} aria-hidden="true" /><span>Linked from {docLink(id)}</span><Basis basis="written" /></li>)}
      </ul> : <p className="sb-hint">No links to or from other documents in this brain.</p>}</div>
      <div><h5>Used by</h5>{data.usedBy.length ? <ul>{data.usedBy.map((item, index) => <li key={index}>
        {item.kind === "report" ? <ScrollText size={13} aria-hidden="true" /> : item.kind === "thread" ? <MessagesSquare size={13} aria-hidden="true" /> : <Ticket size={13} aria-hidden="true" />}
        <span>{item.kind === "report" ? <>Cited {item.count === 1 ? "once" : `${item.count} times`} by <button type="button" className="sb-link" onClick={() => navigate("reports", { reportId: item.reportId })}>{item.title}</button></>
          : item.kind === "thread" ? <>Read in <strong>{item.title}</strong></>
          : item.kind === "claim" ? <>{item.ticket ? <strong>{item.ticket} </strong> : null}claim “{item.title}” quotes it</>
          : <>{item.relation === "read" ? "Read for" : "Linked to"} <strong>{item.ticket}</strong> {item.title}</>}
          <small>{[item.organization, item.at ? formatDay(item.at) : ""].filter(Boolean).join(" · ")}{item.olderVersion ? " · an earlier version" : ""}</small>
          {item.changed ? <small className="sb-warn-text"><TriangleAlert size={11} aria-hidden="true" />{item.changed} citation{item.changed === 1 ? "" : "s"} need review</small> : null}
          {item.kind === "claim" && item.state !== "ok" ? <small className="sb-warn-text"><TriangleAlert size={11} aria-hidden="true" />The quote no longer matches the note</small> : null}</span>
        <Basis basis={item.basis} />
      </li>)}</ul> : <p className="sb-hint">No report, thread or ticket has used this yet.</p>}</div>
      <div className="sb-conn-suggested"><h5>Similar documents <Pill tone="brand">Suggested</Pill></h5>
        {similar.length ? <ul>{similar.map(item => <li key={item.docId}><TypeIcon type={documentById(store, item.docId)?.type} size={13} /><span>{docLink(item.docId)}</span><Basis basis={item.basis} /></li>)}</ul> : <p className="sb-hint">Nothing similar enough yet.</p>}
        {revisions.length ? <p className="sb-hint">Other versions: {revisions.map((item, index) => <span key={item.docId}>{index ? ", " : ""}{docLink(item.docId)}</span>)}</p> : null}
        <p className="sb-hint">{similar[0]?.basis === "meaning" ? "Ranked by the local embedding model." : "Ranked by distinctive words they share. Turn on related-meaning search in Brain settings to rank by meaning."} Suggestions are not recorded links.</p>
      </div>
    </div>}
  </section>;
}
