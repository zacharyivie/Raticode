const layoutColumnGap = 330;
const layoutRowGap = 154;

export function autoLayoutWorkflow(workflow, options = {}) {
  const nodes = [...(workflow.nodes ?? [])];
  const edges = workflow.edges ?? [];
  if (!nodes.length) return { ...workflow, nodes };

  const columnGap = options.columnGap ?? layoutColumnGap;
  const rowGap = options.rowGap ?? layoutRowGap;
  const startX = options.startX ?? 80;
  const startY = options.startY ?? 80;
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const outgoing = new Map(nodes.map((node) => [node.id, []]));
  const indegree = new Map(nodes.map((node) => [node.id, 0]));

  for (const edge of edges) {
    if (!nodesById.has(edge.from) || !nodesById.has(edge.to)) continue;
    outgoing.get(edge.from).push(edge.to);
    indegree.set(edge.to, indegree.get(edge.to) + 1);
  }

  const layers = new Map(nodes.map((node) => [node.id, 0]));
  const queue = nodes
    .filter((node) => indegree.get(node.id) === 0)
    .sort(compareNodesForLayout);
  const visited = new Set();

  while (queue.length) {
    const node = queue.shift();
    if (visited.has(node.id)) continue;
    visited.add(node.id);

    const targets = [...outgoing.get(node.id)].sort((left, right) =>
      compareNodesForLayout(nodesById.get(left), nodesById.get(right)),
    );
    for (const targetId of targets) {
      layers.set(targetId, Math.max(layers.get(targetId), layers.get(node.id) + 1));
      indegree.set(targetId, indegree.get(targetId) - 1);
      if (indegree.get(targetId) === 0) {
        queue.push(nodesById.get(targetId));
        queue.sort(compareNodesForLayout);
      }
    }
  }

  for (const node of nodes) {
    if (!visited.has(node.id)) {
      const connectedLayer = edges
        .filter((edge) => edge.to === node.id && layers.has(edge.from))
        .map((edge) => layers.get(edge.from) + 1);
      layers.set(node.id, connectedLayer.length ? Math.max(...connectedLayer) : 0);
    }
  }

  const grouped = new Map();
  for (const node of nodes) {
    const layer = layers.get(node.id) ?? 0;
    if (!grouped.has(layer)) grouped.set(layer, []);
    grouped.get(layer).push(node);
  }

  const positioned = new Map();
  for (const layer of [...grouped.keys()].sort((left, right) => left - right)) {
    const layerNodes = grouped.get(layer).sort(compareNodesForLayout);
    layerNodes.forEach((node, row) => {
      positioned.set(node.id, {
        ...node,
        x: startX + layer * columnGap,
        y: startY + row * rowGap,
      });
    });
  }

  return {
    ...workflow,
    nodes: nodes.map((node) => positioned.get(node.id) ?? node),
  };
}

function compareNodesForLayout(left, right) {
  const leftY = Number.isFinite(left?.y) ? left.y : 0;
  const rightY = Number.isFinite(right?.y) ? right.y : 0;
  if (leftY !== rightY) return leftY - rightY;
  return String(left?.id ?? "").localeCompare(String(right?.id ?? ""));
}

