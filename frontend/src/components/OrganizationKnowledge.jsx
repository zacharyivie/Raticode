import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Brain, Plus, RefreshCw } from "lucide-react";
import { KINDS, knowledgeEdges, layoutGraph, usedBy } from "../lib/organizationModel.js";
import { Avatar } from "./OrganizationViews.jsx";

const short = text => (text.length > 24 ? `${text.slice(0, 22)}…` : text);
const topicOf = (model, node) => (node.kind === "kb" ? model.knowledge[node.id]?.topic || "General" : model.knowledge[model.tickets[node.id]?.used[0]]?.topic || "General");

function visibleEntries(model, state) {
  const query = state.query.trim().toLocaleLowerCase();
  return Object.values(model.knowledge)
    .filter(entry => (state.project === "all" || entry.projectId === state.project || (state.project === "org" && !entry.projectId)) && state.kinds.has(entry.kind))
    .map(entry => ({ id: entry.id, match: Boolean(query) && `${entry.title} ${entry.summary}`.toLocaleLowerCase().includes(query) }));
}

function Graph({ model, state, selected, onOpen }) {
  const svg = useRef(null);
  const positions = useRef({});
  const laidOut = useRef({ key: "", width: 0 });
  const [size, setSize] = useState({ width: 960, height: 560 });
  const [, redraw] = useState(0);
  const [hot, setHot] = useState(null);
  const drag = useRef(null);

  useEffect(() => {
    const element = svg.current;
    if (!element || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.max(360, Math.round(entry.contentRect.width)), height = Math.round(entry.contentRect.height) || 560;
      setSize(current => (Math.abs(current.width - width) > 30 || current.height !== height ? { width, height } : current));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const { nodes, edges } = useMemo(() => {
    const visible = visibleEntries(model, state);
    const ids = new Set(visible.map(item => item.id));
    const graphNodes = visible.map(item => ({ id: item.id, kind: "kb", match: item.match }));
    const graphEdges = knowledgeEdges(model, { suggested: state.suggested }).filter(([a, b]) => ids.has(a) && ids.has(b)).map(([a, b, k]) => ({ a, b, k }));
    if (state.tickets) {
      for (const ticket of Object.values(model.tickets)) {
        const used = ticket.used.filter(id => ids.has(id));
        if (!used.length) continue;
        graphNodes.push({ id: ticket.id, kind: "ticket" });
        used.forEach(id => graphEdges.push({ a: ticket.id, b: id, k: ticket.observed.has(id) ? "read" : "planned" }));
      }
    }
    return { nodes: graphNodes, edges: graphEdges };
  }, [model, state]);

  const { width, height } = size;
  const key = `${nodes.map(n => n.id).join(",")}|${edges.length}|${Math.round(width / 30)}`;
  if (laidOut.current.key !== key) {
    if (laidOut.current.width && Math.abs(width - laidOut.current.width) > 30) Object.values(positions.current).forEach(p => { p.x *= width / laidOut.current.width; p.fixed = false; });
    layoutGraph(nodes, edges, width, height, positions.current, node => topicOf(model, node));
    laidOut.current = { key, width };
  }
  const P = positions.current;
  const degree = {};
  edges.forEach(edge => { degree[edge.a] = (degree[edge.a] || 0) + 1; degree[edge.b] = (degree[edge.b] || 0) + 1; });
  const topics = [...new Set(nodes.filter(n => n.kind === "kb").map(n => topicOf(model, n)))];
  const hulls = topics.map(topic => {
    const members = nodes.filter(n => n.kind === "kb" && topicOf(model, n) === topic && P[n.id]);
    if (!members.length) return null;
    const cx = members.reduce((sum, n) => sum + P[n.id].x, 0) / members.length, cy = members.reduce((sum, n) => sum + P[n.id].y, 0) / members.length;
    const r = Math.max(...members.map(n => Math.hypot(P[n.id].x - cx, P[n.id].y - cy))) + 42;
    return { topic, cx, cy, r };
  }).filter(Boolean);
  const near = new Set();
  if (hot) { near.add(hot); edges.forEach(e => { if (e.a === hot || e.b === hot) { near.add(e.a); near.add(e.b); } }); }
  const matches = nodes.filter(n => n.match).map(n => n.id);
  const dim = Boolean(hot) || matches.length > 0;
  const isHot = id => (hot ? near.has(id) : matches.includes(id));
  const point = event => { const p = svg.current.createSVGPoint(); p.x = event.clientX; p.y = event.clientY; return p.matrixTransform(svg.current.getScreenCTM().inverse()); };
  const open = node => onOpen(node.kind === "kb" ? `kb:${node.id}` : `ticket:${node.id}`);

  return <svg ref={svg} className={`oh-graph ${dim ? "dim" : ""}`} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Knowledge graph. Use the List view for a keyboard-friendly version.">
    <g>{hulls.map(h => <g key={h.topic} data-hull={h.topic}><circle className="g-hull" cx={h.cx} cy={h.cy} r={h.r} /><text className="g-hull-label" x={h.cx} y={h.cy - h.r + 16} textAnchor="middle">{h.topic}</text></g>)}</g>
    <g>{edges.map((edge, index) => P[edge.a] && P[edge.b] && <line key={index} className={`g-edge ${edge.k} ${hot && (edge.a === hot || edge.b === hot) ? "hot" : ""}`} x1={P[edge.a].x} y1={P[edge.a].y} x2={P[edge.b].x} y2={P[edge.b].y}><title>{edge.k}</title></line>)}</g>
    <g>{nodes.map(node => {
      const p = P[node.id];
      if (!p) return null;
      const handlers = {
        onPointerEnter: () => !drag.current && setHot(node.id),
        onPointerLeave: () => !drag.current && setHot(null),
        onPointerDown: event => { drag.current = { id: node.id, moved: false, start: point(event) }; event.currentTarget.setPointerCapture(event.pointerId); },
        onPointerMove: event => {
          const current = drag.current;
          if (!current || current.id !== node.id) return;
          const q = point(event);
          if (!current.moved && Math.hypot(q.x - current.start.x, q.y - current.start.y) < 4) return;
          current.moved = true; Object.assign(P[node.id], { x: q.x, y: q.y, fixed: true }); redraw(n => n + 1);
        },
        onPointerUp: () => { const current = drag.current; drag.current = null; if (current && !current.moved) open(node); },
        onKeyDown: event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(node); } },
      };
      if (node.kind === "ticket") {
        const ticket = model.tickets[node.id];
        return <g key={node.id} className={`g-node ${isHot(node.id) ? "hot" : ""}`} transform={`translate(${p.x},${p.y})`} tabIndex={0} role="button" aria-label={`${ticket.key}: ${ticket.title}`} {...handlers}>
          <rect x={-26} y={-10} width={52} height={20} rx={5} fill="var(--k-ticket)" /><text textAnchor="middle" y={4} className="g-ticket-label">{ticket.key}</text><title>{ticket.key}: {ticket.title}</title></g>;
      }
      const entry = model.knowledge[node.id];
      const r = 9 + (degree[node.id] || 0) * 1.6 + Math.min(6, usedBy(model, node.id).length);
      return <g key={node.id} className={`g-node ${selected === node.id ? "sel" : ""} ${node.match ? "match" : ""} ${isHot(node.id) ? "hot" : ""}`} transform={`translate(${p.x},${p.y})`} tabIndex={0} role="button" aria-label={`${KINDS[entry.kind]}: ${entry.title}`} {...handlers}>
        <circle r={r} fill={`var(--k-${entry.kind})`} />{entry.changedAt && <circle r={4} cx={r * 0.72} cy={-r * 0.72} fill="var(--review)" />}
        <text textAnchor="middle" y={r + 14}>{short(entry.title)}</text><title>{entry.title}</title></g>;
    })}</g>
  </svg>;
}

export function KnowledgeView({ model, state, setState, selected, onOpen, onAct, busy }) {
  const set = values => setState(current => ({ ...current, ...values }));
  const brain = model.config.remSecondBrain || {};
  const toggleKind = kind => setState(current => {
    const kinds = new Set(current.kinds);
    if (kinds.has(kind)) kinds.delete(kind); else kinds.add(kind);
    return { ...current, kinds: kinds.size ? kinds : new Set([kind]) };
  });
  const tools = <div className="oh-kb-tools">
    <div className="oh-seg" role="group" aria-label="View"><button type="button" aria-pressed={state.view === "graph"} onClick={() => set({ view: "graph" })}>Graph</button><button type="button" aria-pressed={state.view === "list"} onClick={() => set({ view: "list" })}>List</button></div>
    <label className="oh-sr" htmlFor="oh-kbq">Search knowledge</label><input type="search" id="oh-kbq" placeholder="Search notes" value={state.query} onChange={event => set({ query: event.target.value })} />
    <label className="oh-sr" htmlFor="oh-kb-project">Project</label>
    <select id="oh-kb-project" value={state.project} onChange={event => set({ project: event.target.value })}><option value="all">All projects</option><option value="org">Whole organization</option>{Object.values(model.projects).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
    {Object.entries(KINDS).map(([kind, label]) => <button key={kind} className="oh-kind-toggle" type="button" aria-pressed={state.kinds.has(kind)} onClick={() => toggleKind(kind)}><i style={{ background: `var(--k-${kind})` }} />{label}</button>)}
    {state.view === "graph" && <><label className="oh-toggle"><input type="checkbox" checked={state.suggested} onChange={event => set({ suggested: event.target.checked })} />Suggested links</label><label className="oh-toggle"><input type="checkbox" checked={state.tickets} onChange={event => set({ tickets: event.target.checked })} />Show tickets</label></>}
    <span className="grow" />
    {brain.enabled && <button className="oh-btn" type="button" onClick={() => onOpen("new:import")}><Brain size={14} />From Second Brain</button>}
    {Object.values(model.knowledge).some(entry => entry.source) && <button className="oh-btn ghost" type="button" disabled={busy} title="Reread Second Brain notes and flag changes" onClick={() => onAct("knowledge_refresh", {}, result => (result.changed?.length ? `${result.changed.length} notes changed in the Second Brain` : "Second Brain notes are up to date"))}><RefreshCw size={14} /></button>}
    <button className="oh-btn primary" type="button" onClick={() => onOpen("new:kb")}><Plus size={14} />New note</button>
  </div>;
  if (!Object.keys(model.knowledge).length) return <div className="oh-kb">{tools}<div className="oh-panel oh-blank"><Brain size={26} /><h2>Shared knowledge starts here</h2><p>Decisions, specs and findings the whole organization uses. Runs read them, quote them and link them to tickets.</p></div></div>;
  if (state.view === "list") {
    const visible = visibleEntries(model, state).filter(item => !state.query.trim() || item.match).map(item => model.knowledge[item.id]);
    const topics = [...new Set(visible.map(entry => entry.topic || "General"))].sort();
    return <div className="oh-kb">{tools}<div className="oh-panel oh-kb-list">{topics.map(topic => <div className="oh-kb-group" key={topic}><h3>{topic}</h3>
      {visible.filter(entry => (entry.topic || "General") === topic).map(entry => <button key={entry.id} className="oh-kb-row" type="button" onClick={() => onOpen(`kb:${entry.id}`)}>
        <span className="oh-kind"><i style={{ background: `var(--k-${entry.kind})` }} />{KINDS[entry.kind]}</span>
        <span><b>{entry.title}</b>{entry.changedAt && <> <span className="oh-ev warn"><AlertTriangle size={12} />changed</span></>}</span>
        <span className="oh-muted">{model.projects[entry.projectId]?.name || "Organization"}</span>
        <span className="oh-muted">{usedBy(model, entry.id).length} tickets</span>
        <span>{entry.author?.startsWith("employee:") ? <Avatar model={model} id={entry.author.slice(9)} /> : <span className="oh-muted">{entry.source ? "Second Brain" : entry.author === "user" ? "You" : "Rem"}</span>}</span>
      </button>)}</div>)}
      {!visible.length && <p className="oh-empty">No notes match.</p>}</div></div>;
  }
  return <div className="oh-kb">{tools}<div className="oh-panel oh-graph-wrap"><Graph model={model} state={state} selected={selected} onOpen={onOpen} />
    <div className="oh-graph-legend" aria-hidden="true">
      <span><svg viewBox="0 0 26 8"><line x1="0" y1="4" x2="26" y2="4" className="g-edge" /></svg>Written in the note</span>
      <span><svg viewBox="0 0 26 8"><line x1="0" y1="4" x2="26" y2="4" className="g-edge asserted" /></svg>Linked with a checked quote</span>
      {state.suggested && <span><svg viewBox="0 0 26 8"><line x1="0" y1="4" x2="26" y2="4" className="g-edge suggested" /></svg>Suggested, similar text</span>}
      {state.tickets && <span><svg viewBox="0 0 26 8"><line x1="0" y1="4" x2="26" y2="4" className="g-edge read" /></svg>Read by a ticket&apos;s run</span>}
    </div></div></div>;
}
