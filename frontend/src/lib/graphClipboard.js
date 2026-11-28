// Copy, Cut and Paste for graph nodes. The clipboard holds the nodes' workflow.rattish
// declarations as plain text, so they also paste into a chat, a ticket or the code editor.
// Raticode also remembers where the copied nodes sat relative to each other, next to that
// text, so pasting the same text into any workflow keeps their layout.
import { sourceTextForSpan } from "./rattishSource.js";
import { autoLayoutWorkflow } from "./workflowLayout.js";

// Space between the selection and nodes pasted beside it.
const PASTE_GAP = 80;

let copiedNodes = null;

function normalized(text) {
  return String(text ?? "").replace(/\r\n?/g, "\n").trim();
}

// The selected nodes' declarations in source order. Spans are UTF-8 byte offsets.
export function nodeDeclarationsText(source, nodes) {
  const text = String(source ?? "");
  return [...nodes]
    .sort((left, right) => (left.rattish?.declarationIndex ?? 0) - (right.rattish?.declarationIndex ?? 0))
    .map((node) => sourceTextForSpan(text, node.rattish?.sourceSpan).replace(/\s+$/, ""))
    .filter(Boolean)
    .map((declaration) => `${declaration}\n`)
    .join("\n");
}

// Remember each copied node's place relative to the group's top-left corner, in source order.
export function rememberCopiedNodes(text, nodes) {
  const ordered = [...nodes].sort((left, right) => (left.rattish?.declarationIndex ?? 0) - (right.rattish?.declarationIndex ?? 0));
  const minX = Math.min(...ordered.map((node) => node.x ?? 0));
  const minY = Math.min(...ordered.map((node) => node.y ?? 0));
  copiedNodes = { text: normalized(text), offsets: ordered.map((node) => ({ x: (node.x ?? 0) - minX, y: (node.y ?? 0) - minY })) };
}

// Where the group goes: beside the selection, or centred in the view.
function pasteAnchor({ selectedNodes = [], viewport, size, nodeWidth, groupWidth, groupHeight }) {
  if (selectedNodes.length) {
    return {
      x: Math.max(...selectedNodes.map((node) => node.x ?? 0)) + nodeWidth + PASTE_GAP,
      y: Math.min(...selectedNodes.map((node) => node.y ?? 0)),
    };
  }
  return {
    x: (size.width / 2 - viewport.x) / viewport.scale - groupWidth / 2,
    y: (size.height / 2 - viewport.y) / viewport.scale - groupHeight / 2,
  };
}

// Offsets for the pasted nodes, in the order the backend returned them: the remembered
// layout when the text is what Raticode copied, otherwise an auto-layout of their routes.
function pastedNodeOffsets(text, pastedIds, edges = []) {
  if (copiedNodes && copiedNodes.text === normalized(text) && copiedNodes.offsets.length === pastedIds.length) {
    return copiedNodes.offsets;
  }
  const laidOut = autoLayoutWorkflow(
    { nodes: pastedIds.map((id) => ({ id, label: id, x: 0, y: 0 })), edges },
    { startX: 0, startY: 0 },
  ).nodes;
  return laidOut.map((node) => ({ x: node.x, y: node.y }));
}

// The positions to save for the pasted nodes, keyed by node ID.
export function pastedNodePositions({ text, pastedIds, edges, selectedNodes, viewport, size, nodeWidth, nodeHeight, at = null }) {
  const offsets = pastedNodeOffsets(text, pastedIds, edges);
  const groupWidth = Math.max(...offsets.map((offset) => offset.x)) + nodeWidth;
  const groupHeight = Math.max(...offsets.map((offset) => offset.y)) + nodeHeight;
  const anchor = at ?? pasteAnchor({ selectedNodes, viewport, size, nodeWidth, groupWidth, groupHeight });
  return Object.fromEntries(pastedIds.map((id, index) => [id, {
    x: Math.round(anchor.x + offsets[index].x),
    y: Math.round(anchor.y + offsets[index].y),
  }]));
}

// The desktop app reads the clipboard through its main process; a browser asks directly.
export async function readClipboardText() {
  const read = window.goferDesktop?.clipboard?.readText
    ?? (navigator.clipboard?.readText ? () => navigator.clipboard.readText() : null);
  if (!read) throw new Error("Clipboard access is unavailable.");
  return String((await read()) ?? "");
}
