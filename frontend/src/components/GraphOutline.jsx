import { useEffect, useRef, useState } from "react";
import { ArrowRight, GitBranch, Network } from "lucide-react";

// A Rattish route's label, with its predicate exactly as written.
export function edgeLabel({ mode, predicateSource } = {}) {
  if (mode === "otherwise") return "otherwise";
  if (mode === "when") return predicateSource ? `when ${predicateSource}` : "when";
  return "always";
}

function relationshipSummary(nodeId, nodesById, edges) {
  const incoming = edges.filter((edge) => edge.to === nodeId);
  const outgoing = edges.filter((edge) => edge.from === nodeId);
  const names = (items, endpoint) =>
    items.map((edge) => nodesById[edge[endpoint]]?.label ?? edge[endpoint]).join(", ");
  return {
    incoming,
    outgoing,
    text: `${incoming.length} incoming${incoming.length ? ` from ${names(incoming, "from")}` : ""}; ${outgoing.length} outgoing${outgoing.length ? ` to ${names(outgoing, "to")}` : ""}`,
  };
}

export default function GraphOutline({
  announcement,
  connectionFrom,
  embedded = false,
  edgeDiagnostics,
  edges,
  focusRequest,
  nodeDiagnostics,
  nodes,
  nodeStatuses,
  selectedEdgeId,
  selectedNodeId,
  onCancelConnection,
  onConnect,
  onDeleteEdge,
  onDeleteNode,
  onDuplicateNode,
  onOpenEdge,
  onOpenNode,
  onSelectEdge,
  onSelectNode,
  onStartConnection,
}) {
  const itemRefs = useRef(new Map());
  const [pendingFocusKey, setPendingFocusKey] = useState(null);
  const nodesById = Object.fromEntries(nodes.map((node) => [node.id, node]));
  const itemKeys = [
    ...nodes.map((node) => `node:${node.id}`),
    ...edges.map((edge) => `edge:${edge.id}`),
  ];

  useEffect(() => {
    if (!pendingFocusKey) return;
    const nextItem = itemRefs.current.get(pendingFocusKey);
    if (!nextItem) return;
    nextItem.focus();
    setPendingFocusKey(null);
  }, [edges, nodes, pendingFocusKey]);

  useEffect(() => {
    if (!focusRequest?.itemKey) return;
    itemRefs.current.get(focusRequest.itemKey)?.focus();
  }, [focusRequest]);

  function focusMutationResult(itemKey) {
    if (itemKey) setPendingFocusKey(itemKey);
  }

  function focusRelative(itemKey, offset) {
    const index = itemKeys.indexOf(itemKey);
    if (index < 0 || !itemKeys.length) return;
    const nextIndex = (index + offset + itemKeys.length) % itemKeys.length;
    itemRefs.current.get(itemKeys[nextIndex])?.focus();
  }

  function handleCommonKeyDown(event, itemKey) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      focusRelative(itemKey, 1);
      return true;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      focusRelative(itemKey, -1);
      return true;
    }
    if (event.key === "Home") {
      event.preventDefault();
      itemRefs.current.get(itemKeys[0])?.focus();
      return true;
    }
    if (event.key === "End") {
      event.preventDefault();
      itemRefs.current.get(itemKeys.at(-1))?.focus();
      return true;
    }
    return false;
  }

  return (
    <nav
      aria-label="Graph outline"
      className={embedded
        ? "workflow-scrollbar flex max-h-80 w-full flex-col overflow-hidden bg-white"
        : "workflow-scrollbar absolute bottom-4 left-4 top-[7.75rem] z-30 flex w-64 flex-col overflow-hidden rounded-lg border border-line bg-white shadow-panel"}
      onWheel={(event) => event.stopPropagation()}
    >
      <div aria-atomic="true" aria-live="polite" className="sr-only" role="status">
        {announcement}
      </div>
      <div className="border-b border-line px-3 py-2.5">
        <div className="flex items-center gap-2">
          <Network aria-hidden="true" className="text-brand" size={15} />
          <h2 className="text-xs font-semibold text-ink">Graph outline</h2>
        </div>
        <p className="mt-1 text-[11px] leading-4 text-muted">
          Arrow keys navigate. Enter opens. C connects.
        </p>
      </div>
      {connectionFrom ? (
        <div className="border-b border-indigo-100 bg-indigo-50 px-3 py-2 text-[11px] leading-4 text-indigo-700">
          Connecting from <strong>{nodesById[connectionFrom]?.label ?? connectionFrom}</strong>.
          Choose a node and press Enter. <button className="underline" type="button" onClick={onCancelConnection}>Cancel</button>
        </div>
      ) : null}
      <div className="workflow-scrollbar min-h-0 flex-1 overflow-y-auto p-2">
        <h3 className="px-1 pb-1 text-[11px] font-semibold text-muted">Nodes ({nodes.length})</h3>
        {nodes.length ? (
          <ul className="space-y-1">
            {nodes.map((node) => {
              const itemKey = `node:${node.id}`;
              const relationships = relationshipSummary(node.id, nodesById, edges);
              const diagnostics = nodeDiagnostics[node.id] ?? [];
              const status = nodeStatuses[node.id] ?? "not run";
              const validation = diagnostics.some((item) => item.severity === "error")
                ? "validation error"
                : diagnostics.some((item) => item.severity === "warning")
                  ? "validation warning"
                  : "valid";
              const description = `${node.label}, ${node.type}, status ${status}, ${relationships.text}, ${validation}`;
              const selected = selectedNodeId === node.id;
              return (
                <li key={node.id}>
                  <button
                    ref={(element) => element ? itemRefs.current.set(itemKey, element) : itemRefs.current.delete(itemKey)}
                    aria-current={selected ? "true" : undefined}
                    aria-label={description}
                    className={`w-full rounded-md px-2.5 py-2 text-left transition ${
                      selected
                        ? "bg-indigo-50 text-indigo-700"
                        : "text-slate-700 hover:bg-slate-50 hover:text-ink"
                    }`}
                    type="button"
                    onClick={() => onSelectNode(node.id)}
                    onFocus={() => onSelectNode(node.id)}
                    onKeyDown={(event) => {
                      if (handleCommonKeyDown(event, itemKey)) return;
                      if (event.key.toLowerCase() === "c" && !event.ctrlKey && !event.metaKey) {
                        event.preventDefault();
                        onStartConnection(node.id);
                      } else if (event.key === "Enter") {
                        event.preventDefault();
                        if (connectionFrom) {
                          focusMutationResult(onConnect(connectionFrom, node.id));
                        }
                        else onOpenNode(node.id);
                      } else if (event.key === "Delete" || event.key === "Backspace") {
                        event.preventDefault();
                        focusMutationResult(onDeleteNode(node.id));
                      } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "d") {
                        event.preventDefault();
                        focusMutationResult(onDuplicateNode(node.id));
                      } else if (event.key === "Escape" && connectionFrom) {
                        event.preventDefault();
                        onCancelConnection();
                      }
                    }}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-medium">{node.label}</span>
                      <span className="shrink-0 text-[10px] text-muted">{status}</span>
                    </span>
                    <span className="mt-0.5 block truncate text-[10px] text-muted">
                      {node.type} · {relationships.incoming.length} in · {relationships.outgoing.length} out · {validation}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="px-1 py-2 text-xs text-muted">No nodes yet. Use Add node in the toolbar.</p>
        )}

        <h3 className="px-1 pb-1 pt-4 text-[11px] font-semibold text-muted">Edges ({edges.length})</h3>
        {edges.length ? (
          <ul className="space-y-1">
            {edges.map((edge) => {
              const itemKey = `edge:${edge.id}`;
              const selected = selectedEdgeId === edge.id;
              const validation = (edgeDiagnostics[edge.id] ?? []).some((item) => item.severity === "error")
                ? "validation error"
                : (edgeDiagnostics[edge.id] ?? []).length
                  ? "validation warning"
                  : "valid";
              const from = nodesById[edge.from]?.label ?? edge.from;
              const to = nodesById[edge.to]?.label ?? edge.to;
              return (
                <li key={edge.id}>
                  <button
                    ref={(element) => element ? itemRefs.current.set(itemKey, element) : itemRefs.current.delete(itemKey)}
                    aria-current={selected ? "true" : undefined}
                    aria-label={`${from} to ${to}, condition ${edgeLabel(edge)}, ${validation}`}
                    className={`w-full rounded-md px-2.5 py-2 text-left transition ${
                      selected ? "bg-indigo-50 text-indigo-700" : "text-slate-700 hover:bg-slate-50 hover:text-ink"
                    }`}
                    type="button"
                    onClick={() => onSelectEdge(edge.id)}
                    onFocus={() => onSelectEdge(edge.id)}
                    onKeyDown={(event) => {
                      if (handleCommonKeyDown(event, itemKey)) return;
                      if (event.key === "Enter") {
                        event.preventDefault();
                        onOpenEdge(edge.id);
                      } else if (event.key === "Delete" || event.key === "Backspace") {
                        event.preventDefault();
                        focusMutationResult(onDeleteEdge(edge.id));
                      }
                    }}
                  >
                    <span className="flex items-center gap-1.5 text-xs font-medium">
                      <span className="truncate">{from}</span>
                      <ArrowRight aria-hidden="true" className="shrink-0" size={12} />
                      <span className="truncate">{to}</span>
                    </span>
                    <span className="mt-0.5 flex items-center gap-1 text-[10px] text-muted">
                      <GitBranch aria-hidden="true" size={10} /> {edgeLabel(edge)} · {validation}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="px-1 py-2 text-xs text-muted">No connections.</p>
        )}
      </div>
    </nav>
  );
}
