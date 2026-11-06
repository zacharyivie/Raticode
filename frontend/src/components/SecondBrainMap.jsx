import { useEffect, useMemo, useState } from "react";
import "./SecondBrainGraph.css";
import { ArrowLeft, Network, Pencil, Sparkles } from "lucide-react";
import { bubbleRadius, scalePoints } from "../lib/secondBrainGraph.js";
import { documentById, graphKey, nameTopic, navigate, openDocument, useBrainData, useSecondBrain, viewFor } from "../lib/secondBrainStore.js";
import { DraftInput, Empty, Pill, Segmented, TypeIcon } from "./SecondBrainParts.jsx";

const WIDTH = 900, HEIGHT = 540;

function activate(event, action) {
  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); action(); }
}

function TopicDocuments({ brain, topic, onBack, store }) {
  const { data, error } = useBrainData("topic_layout", { brainId: brain.id, topicId: topic.id }, `${topic.id}:${graphKey(store, brain.id)}`);
  const nodes = useMemo(() => scalePoints(data?.nodes || [], WIDTH, HEIGHT, 60), [data]);
  const at = Object.fromEntries(nodes.map(node => [node.docId, node]));
  return <div className="sb-map-stage">
    <div className="sb-map-toolbar"><button type="button" className="sb-back" onClick={onBack}><ArrowLeft size={14} />All topics</button><strong>{topic.label}</strong>{data && data.shown < data.total ? <small className="sb-muted">Showing the {data.shown} most connected of {data.total}</small> : null}</div>
    {error ? <p role="alert" className="sb-hint">{error}</p> : !data ? <p role="status" className="sb-hint">Laying out documents…</p> :
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="sb-map" role="group" aria-label={`Documents in topic ${topic.label}`}>
        {data.edges.map((edge, index) => at[edge.a] && at[edge.b] ? <line key={index} className="sb-edge" data-suggestion={edge.kind === "similar" || undefined} x1={at[edge.a].px} y1={at[edge.a].py} x2={at[edge.b].px} y2={at[edge.b].py} /> : null)}
        {nodes.map(node => { const doc = documentById(store, node.docId); const label = doc?.title || "Unavailable document"; return <g key={node.docId} className="sb-map-doc" role="button" tabIndex={0} aria-label={`Open ${label}`} onClick={() => openDocument(node.docId)} onKeyDown={event => activate(event, () => openDocument(node.docId))}>
          <title>{label}</title><circle cx={node.px} cy={node.py} r={node.docId === topic.central ? 10 : 7} /><text x={node.px} y={node.py - 12} textAnchor="middle">{label.slice(0, 30)}</text>
        </g>; })}
      </svg>}
  </div>;
}

function TopicPanel({ topic, store, projects, onExpand }) {
  const members = topic.members.map(id => documentById(store, id)).filter(Boolean);
  return <aside className="sb-map-panel" aria-label={`Topic ${topic.label}`}>
    <h4>{topic.label}</h4>
    <p className="sb-hint">{topic.size} documents{topic.terms.length && topic.named ? ` · words: ${topic.terms.join(", ")}` : ""}{topic.projects.length ? ` · mostly ${topic.projects.map(item => projects[item.projectId] || "unknown").join(", ")}` : ""}</p>
    <label className="sb-field"><span><Pencil size={12} aria-hidden="true" /> Name this topic</span><DraftInput label="Topic name" value={topic.named ? topic.name : ""} placeholder={topic.terms.join(" · ")} onCommit={value => nameTopic(topic, value.trim())} /></label>
    <button type="button" className="sb-button" onClick={onExpand}><Network size={14} />Show its documents on the map</button>
    <ul className="sb-map-members">{members.slice(0, 60).map(doc => <li key={doc.id}><TypeIcon type={doc.type} size={13} /><button type="button" className="sb-link" onClick={() => openDocument(doc.id)}>{doc.title}</button>{doc.id === topic.central ? <Pill>Most connected</Pill> : null}</li>)}</ul>
    {members.length > 60 ? <p className="sb-hint">{members.length - 60} more in the list view.</p> : null}
  </aside>;
}

export default function TopicMap({ brain, projectId = "", embedded = false }) {
  const store = useSecondBrain();
  const view = viewFor(store);
  const { data, error, loading } = useBrainData("topics", { brainId: brain.id }, graphKey(store, brain.id));
  const projectsData = useBrainData("projects", { brainId: brain.id }, graphKey(store, brain.id));
  const projectNames = Object.fromEntries((projectsData.data?.projects || []).map(item => [item.id, item.name]));
  const [mode, setMode] = useState("map");
  const [selected, setSelected] = useState(view.mapTopicId || "");
  const [expanded, setExpanded] = useState("");
  useEffect(() => { if (view.mapTopicId) { setSelected(view.mapTopicId); setExpanded(""); } }, [view.mapTopicId]);
  const topics = useMemo(() => (data?.topics || []).filter(topic => !projectId || topic.projects.some(item => item.projectId === projectId) || topic.members.some(id => projectsData.data?.assignments?.[id]?.projectId === projectId)), [data, projectId, projectsData.data]);
  const largest = Math.max(1, ...topics.map(topic => topic.size));
  const placed = useMemo(() => scalePoints(topics, WIDTH, HEIGHT, 90), [topics]);
  const at = Object.fromEntries(placed.map(topic => [topic.id, topic]));
  const current = topics.find(topic => topic.id === selected);
  const choose = id => { setSelected(id); if (!embedded) navigate("map", { mapTopicId: id }); };
  if (error) return <p role="alert" className="sb-hint">{error}</p>;
  if (!data) return <p role="status" className="sb-hint">{loading ? "Grouping documents into topics…" : ""}</p>;
  if (!topics.length) return <Empty icon={Sparkles} title={projectId ? "No topics for this project yet" : "No topics yet"}>Topics appear when at least three documents share enough content or links.</Empty>;
  const basisText = data.basis === "meaning" ? "Grouped by meaning with the local embedding model, plus links written in files." : "Grouped by the distinctive words documents share, plus links written in files. Turn on related-meaning search in Brain settings to group by meaning.";
  const body = expanded && current ? <TopicDocuments brain={brain} topic={current} store={store} onBack={() => setExpanded("")} /> : mode === "list" ? <div className="sb-table-wrap" role="region" aria-label="Topics" tabIndex={0}><table className="sb-table">
    <caption className="sb-sr-only">Topics in {brain.name}</caption>
    <thead><tr><th scope="col">Topic</th><th scope="col" className="sb-num">Documents</th><th scope="col">Projects</th><th scope="col">Most connected document</th></tr></thead>
    <tbody>{topics.map(topic => <tr key={topic.id}><th scope="row"><button type="button" className="sb-link" onClick={() => { choose(topic.id); setMode("map"); }}>{topic.label}</button></th><td className="sb-num">{topic.size}</td><td>{topic.projects.map(item => projectNames[item.projectId] || "").filter(Boolean).join(", ") || "None"}</td><td>{documentById(store, topic.central)?.title}</td></tr>)}</tbody>
  </table></div> : <div className="sb-map-stage">
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="sb-map" role="group" aria-label={`Topic map of ${brain.name}. ${topics.length} topics.`}>
      {(data.edges || []).map((edge, index) => at[edge.a] && at[edge.b] ? <line key={index} className="sb-edge" data-suggestion={!edge.written || undefined} strokeWidth={Math.min(6, 1 + edge.weight / 2)} x1={at[edge.a].px} y1={at[edge.a].py} x2={at[edge.b].px} y2={at[edge.b].py}><title>{edge.written ? `${edge.written} written link${edge.written === 1 ? "" : "s"} between these topics` : "Similar content (suggested)"}</title></line> : null)}
      {placed.map(topic => { const radius = bubbleRadius(topic.size, largest); const label = `${topic.label}, ${topic.size} documents`; return <g key={topic.id} className="sb-map-topic" data-selected={topic.id === selected || undefined} role="button" tabIndex={0} aria-pressed={topic.id === selected} aria-label={label} onClick={() => choose(topic.id)} onDoubleClick={() => { choose(topic.id); setExpanded(topic.id); }} onKeyDown={event => activate(event, () => choose(topic.id))}>
        <title>{label}</title>
        <circle cx={topic.px} cy={topic.py} r={radius} />
        <text x={topic.px} y={topic.py + 4} textAnchor="middle" className="sb-map-count">{topic.size}</text>
        <text x={topic.px} y={topic.py + radius + 15} textAnchor="middle">{topic.label.slice(0, 32)}</text>
      </g>; })}
    </svg>
  </div>;
  return <div className={embedded ? "sb-map-wrap" : "sb-page sb-map-page"}>
    {!embedded ? <div className="sb-page-head"><div><h3>Map</h3><p>{basisText} Topics are suggestions that change as the brain grows. Name a topic to keep it.</p></div></div> : <p className="sb-hint">{basisText}</p>}
    <div className="sb-map-controls"><Segmented label="Map or list" value={expanded ? "map" : mode} options={[["map", "Map"], ["list", "List"]]} onChange={value => { setMode(value); setExpanded(""); }} />
      <ul className="sb-map-legend" aria-label="Legend"><li><i className="sb-key-solid" aria-hidden="true" />Written links</li><li><i className="sb-key-dashed" aria-hidden="true" />Similar content (suggested)</li><li>Bubble size: documents</li></ul></div>
    <div className="sb-map-layout">{body}{current && !expanded ? <TopicPanel topic={current} store={store} projects={projectNames} onExpand={() => setExpanded(current.id)} /> : null}</div>
  </div>;
}
