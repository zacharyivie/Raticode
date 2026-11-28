// Builds the node picker's list from the Rattish contracts the backend sends with each
// workflow document (document.nodeContracts). A new contract with a catalog entry shows up
// in the right group without any change here; one without a catalog lands in "Other".

const NODE_CATEGORIES = [
  { id: "code", label: "Run code" },
  { id: "files", label: "Files" },
  { id: "ai", label: "AI" },
  { id: "web", label: "Web" },
  { id: "approvals", label: "Approvals and notices" },
  { id: "flow", label: "Flow control" },
  { id: "workflows", label: "Other workflows" },
];

const OTHER_CATEGORY = { id: "other", label: "Other" };
const DOCS_ORIGIN = "https://raticode.dev";

export function nodeTypeLabel(nodeType) {
  const words = String(nodeType || "")
    .replaceAll("_", "-")
    .split("-")
    .filter(Boolean);
  if (!words.length) return "";
  const known = { llm: "LLM", mcp: "MCP", http: "HTTP", ai: "AI" };
  return words
    .map((word, index) => known[word] ?? (index === 0 ? word[0].toUpperCase() + word.slice(1) : word))
    .join(" ");
}

function categoryLabel(id) {
  return String(id || "")
    .split(/[-_]/)
    .filter(Boolean)
    .map((part, index) => (index === 0 ? part[0].toUpperCase() + part.slice(1) : part))
    .join(" ");
}

export function nodeCatalogEntries(contracts = []) {
  return (contracts ?? [])
    .filter((contract) => contract?.nodeType)
    .map((contract) => ({
      nodeType: contract.nodeType,
      label: nodeTypeLabel(contract.nodeType),
      category: contract.category || OTHER_CATEGORY.id,
      summary: contract.summary || "",
      docsUrl: safeDocsUrl(contract.docsUrl, contract.nodeType),
    }));
}

// Only links on the documentation site are shown, whatever a contract says.
function safeDocsUrl(value, nodeType) {
  const fallback = `${DOCS_ORIGIN}/docs/nodes/${encodeURIComponent(nodeType)}`;
  if (typeof value !== "string") return fallback;
  try {
    const url = new URL(value);
    return url.origin === DOCS_ORIGIN ? url.href : fallback;
  } catch {
    return fallback;
  }
}

function matchesQuery(entry, categoryName, terms) {
  if (!terms.length) return true;
  const haystack = [entry.nodeType, entry.label, entry.summary, categoryName]
    .join(" ")
    .toLowerCase();
  return terms.every((term) => haystack.includes(term));
}

function rank(entry, query) {
  if (!query) return 0;
  const label = entry.label.toLowerCase();
  if (entry.nodeType === query || label === query) return 0;
  if (entry.nodeType.startsWith(query) || label.startsWith(query)) return 1;
  if (label.split(" ").some((word) => word.startsWith(query))) return 2;
  return 3;
}

// Groups in a fixed order, each sorted by how well it matches the search, then by name.
export function groupNodeCatalog(entries, query = "") {
  const normalized = String(query || "").trim().toLowerCase();
  const terms = normalized.split(/\s+/).filter(Boolean);
  const known = new Map(NODE_CATEGORIES.map((category) => [category.id, category]));
  const extra = [...new Set(entries.map((entry) => entry.category))]
    .filter((id) => !known.has(id) && id !== OTHER_CATEGORY.id)
    .sort()
    .map((id) => ({ id, label: categoryLabel(id) }));
  const categories = [...NODE_CATEGORIES, ...extra, OTHER_CATEGORY];
  return categories
    .map((category) => ({
      ...category,
      entries: entries
        .filter((entry) => entry.category === category.id && matchesQuery(entry, category.label, terms))
        .sort(
          (left, right) =>
            rank(left, normalized) - rank(right, normalized) || left.label.localeCompare(right.label),
        ),
    }))
    .filter((category) => category.entries.length);
}

// Where a new node goes: right of the selected node, or below the lowest node if the
// spot is taken, so it never lands on top of another card.
export function pickedNodePosition(nodes = [], selectedId, { gapX = 330, gapY = 154 } = {}) {
  const selected = nodes.find((node) => node.id === selectedId);
  if (!selected) {
    if (!nodes.length) return { x: 120, y: 120 };
    const lowest = Math.max(...nodes.map((node) => node.y ?? 0));
    const left = Math.min(...nodes.map((node) => node.x ?? 0));
    return { x: left, y: lowest + gapY };
  }
  const x = (selected.x ?? 0) + gapX;
  let y = selected.y ?? 0;
  const taken = (candidateY) =>
    nodes.some(
      (node) => Math.abs((node.x ?? 0) - x) < gapX / 2 && Math.abs((node.y ?? 0) - candidateY) < gapY / 2,
    );
  while (taken(y)) y += gapY;
  return { x, y };
}

export function nextNodeId(nodes = [], nodeType = "node") {
  const used = new Set(nodes.map((node) => String(node.id).toLowerCase()));
  const base = String(nodeType || "node").toLowerCase();
  let index = 1;
  while (used.has(`${base}-${index}`)) index += 1;
  return `${base}-${index}`;
}
