// Projects, connections and topic maps for the Second Brain.
// Evidence (what a file says or what Raticode saw happen) and suggestions (similarity)
// are kept apart everywhere: they have different words, styles and line patterns.

export const BASIS = {
  you: { label: "Set by you", kind: "evidence" },
  observed: { label: "Seen by Raticode", kind: "evidence" },
  written: { label: "Written in the file", kind: "evidence" },
  cited: { label: "Cited with a checked quote", kind: "evidence" },
  folder: { label: "From its folder", kind: "evidence" },
  conversation: { label: "Linked in a Rem conversation", kind: "evidence" },
  asserted: { label: "Claimed by an agent", kind: "claim" },
  suggested: { label: "Suggested", kind: "suggestion" },
  meaning: { label: "Similar meaning", kind: "suggestion" },
  words: { label: "Shared words", kind: "suggestion" },
};
export const basisLabel = basis => BASIS[basis]?.label || "Unknown basis";
export const isSuggestion = basis => BASIS[basis]?.kind === "suggestion";

const MAKER_LABELS = { rem_task: "Rem task", rem_thread: "Rem thread", conversation: "Rem conversation", brain_report: "Second Brain report", organization: "Organization" };
export const makerLabel = kind => MAKER_LABELS[kind] || "Unknown";

// Reciprocal rank fusion of documents (k = 60): ranks from exact and meaning search are
// combined without comparing their scores. Documents found both ways rise to the top.
export function fuseResults(exact, related, k = 60) {
  const fused = new Map();
  const add = (list, kind) => list.forEach((result, index) => {
    const entry = fused.get(result.doc.id) || { result, score: 0, kinds: [], first: fused.size };
    entry.score += 1 / (k + index + 1);
    entry.kinds.push(kind);
    fused.set(result.doc.id, entry);
  });
  add(exact, "content");
  add(related, "meaning");
  return [...fused.values()]
    .sort((a, b) => b.score - a.score || a.first - b.first)
    .map(entry => ({ ...entry.result, score: entry.score, matchedBy: entry.kinds }));
}

// Places topic coordinates (about -1..1) inside a drawing area with room for labels.
export function scalePoints(points, width, height, pad = 70) {
  if (!points.length) return [];
  const xs = points.map(point => point.x), ys = points.map(point => point.y);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const spanX = maxX - minX || 1, spanY = maxY - minY || 1;
  return points.map(point => ({
    ...point,
    px: points.length === 1 ? width / 2 : pad + ((point.x - minX) / spanX) * (width - 2 * pad),
    py: points.length === 1 ? height / 2 : pad + ((point.y - minY) / spanY) * (height - 2 * pad),
  }));
}

// A topic bubble's radius grows with the square root of its size, within readable bounds.
export const bubbleRadius = (size, largest) => 18 + 34 * Math.sqrt(size / Math.max(1, largest));

// Ego graph: the document in the middle, related items on a ring, grouped by relation.
export function egoLayout(center, items, width = 520, height = 300) {
  const radius = Math.min(width, height) / 2 - 46;
  const cx = width / 2, cy = height / 2;
  const shown = items.slice(0, 14);
  return {
    center: { ...center, x: cx, y: cy },
    nodes: shown.map((item, index) => {
      const angle = -Math.PI / 2 + (2 * Math.PI * index) / Math.max(1, shown.length);
      return { ...item, x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) };
    }),
    hidden: Math.max(0, items.length - shown.length),
  };
}

// The related items of one document, in the order a reader cares about.
export function connectionItems(connections, documentTitle) {
  if (!connections) return [];
  const items = [];
  for (const id of connections.links?.cites || []) items.push({ key: `cites-${id}`, docId: id, relation: "Links to", basis: "written", title: documentTitle(id) });
  for (const id of connections.links?.citedBy || []) items.push({ key: `cited-${id}`, docId: id, relation: "Linked from", basis: "written", title: documentTitle(id) });
  for (const item of connections.usedBy || []) {
    const title = item.kind === "ticket" || item.kind === "claim" ? `${item.ticket} ${item.title}`.trim() : item.title;
    items.push({ key: `used-${item.kind}-${item.reportId || item.threadId || item.taskId}-${items.length}`, relation: item.kind === "report" ? "Cited by report" : item.kind === "thread" ? "Read by thread" : item.kind === "claim" ? "Quoted by claim" : "Read by ticket", basis: item.basis, title, reportId: item.reportId });
  }
  for (const item of connections.similar || []) if (!item.revision) items.push({ key: `similar-${item.docId}`, docId: item.docId, relation: "Similar", basis: item.basis, title: documentTitle(item.docId), score: item.score });
  return items;
}

// Documents for one project page, split into reports and other knowledge, newest first.
export function projectDocuments(result, documents, projectId) {
  const byId = new Map(documents.map(doc => [doc.id, doc]));
  const rows = Object.entries(result?.assignments || {})
    .filter(([, assignment]) => assignment.projectId === projectId)
    .map(([docId, assignment]) => ({ doc: byId.get(docId), assignment }))
    .filter(row => row.doc);
  const newest = (a, b) => (b.doc.dates?.modified || "").localeCompare(a.doc.dates?.modified || "");
  return {
    reports: rows.filter(row => row.assignment.kind === "reports").sort(newest),
    knowledge: rows.filter(row => row.assignment.kind !== "reports").sort(newest),
  };
}

// A project that absorbs another keeps the other's name as an alias and its folders.
export function mergedProject(into, from, brainId, makeId) {
  const base = into.saved ? into : { id: makeId("project"), brainId, name: into.name, aliases: into.aliases || [], roots: into.roots || [] };
  return {
    id: base.id,
    brainId,
    name: base.name,
    aliases: [...new Set([...(base.aliases || []), from.name, ...(from.aliases || [])])].filter(name => name !== base.name),
    roots: [...new Set([...(base.roots || []), ...(from.roots || [])])],
    color: base.color || "",
  };
}
