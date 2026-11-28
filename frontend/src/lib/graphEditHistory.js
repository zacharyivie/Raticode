// Undo and Redo for graph edits. Each open workflow keeps two stacks of
// { label, sourceBefore, revisionAfter }: the workflow.rattish text from before an
// edit and the revision that edit saved. Node moves are layout metadata and stay out.

export const EMPTY_GRAPH_HISTORY = Object.freeze({ undo: Object.freeze([]), redo: Object.freeze([]) });

const GRAPH_HISTORY_LIMIT = 50;

// The edit that names a change made of several mutations, such as add_node with the
// set_routes that connects it.
const LEADING_KINDS = ["paste_nodes", "add_node", "delete_node", "duplicate_node", "rename_node", "change_node_type"];

function targetName(target) {
  if (target === "workflow" || target?.workflow === true) return "";
  return String(target?.node ?? target ?? "");
}

// "cut node fetch" for one node, "cut 3 nodes" for several.
function nodesLabel(verb, names) {
  return names.length === 1 ? `${verb} node ${names[0]}` : `${verb} ${names.length} nodes`;
}

// "delete node fetch": Undo and Redo buttons read "Undo delete node fetch".
// { cut: true } names a batch of delete_node mutations as a Cut.
export function graphEditLabel(mutations = [], { cut = false } = {}) {
  if (cut) return nodesLabel("cut", mutations.filter((item) => item?.kind === "delete_node").map((item) => String(item.node)));
  const mutation = mutations.find((item) => LEADING_KINDS.includes(item?.kind)) ?? mutations[0];
  const node = String(mutation?.node ?? "");
  switch (mutation?.kind) {
    case "paste_nodes": return nodesLabel("paste", pastedNodeNames(mutation.source));
    case "add_node": return `add node ${node}`;
    case "delete_node": return `delete node ${node}`;
    case "duplicate_node": return `duplicate node ${node}`;
    case "rename_node": return `rename node ${node}`;
    case "change_node_type": return `change type of node ${node}`;
    case "set_needs": return `change needs of node ${node}`;
    case "set_routes": return `change routes of node ${node}`;
    case "set_field":
    case "remove_field": {
      const verb = mutation.kind === "set_field" ? "change" : "remove";
      const target = targetName(mutation.target);
      return target ? `${verb} ${mutation.field} of node ${target}` : `${verb} workflow ${mutation.field}`;
    }
    default: return "graph edit";
  }
}

// The node names declared in pasted workflow.rattish text, before any renaming.
export function pastedNodeNames(source = "") {
  return [...String(source).matchAll(/^node[ \t]+([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)[ \t]*:/gim)].map((match) => match[1]);
}

// The newest entry goes last; the oldest falls off past the limit.
export function pushGraphHistoryEntry(stack, entry) {
  return [...stack, entry].slice(-GRAPH_HISTORY_LIMIT);
}
