import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import DagCanvas from "../src/components/DagCanvas.jsx";
import { rattishGraphWorkflow } from "../src/pages/App.jsx";
import "../src/styles/index.css";

// The node picker in a real browser, fed the installed contracts' catalog entries.
const contracts = await (await fetch("/node-picker-contracts.json")).json();
const graphNode = (id, type) => ({
  id,
  label: id,
  type,
  configuration: type === "bash-command" ? { command: `echo ${id}` } : {},
  execution: { allow_fail: false, max_concurrency: 1, retry_count: 0, retry_delay_ms: 1000 },
  authoredFields: { type: { value: type } },
  bindings: [],
  needs: [],
  diagnostics: [],
});
const initialDocument = {
  workflowId: "picker",
  source: "Rattish: 1\n\nWorkflow:\n  name: Picker\n",
  workflow: { name: "Picker", fields: { name: { value: "Picker" } } },
  metadata: { canvas: { nodes: { prepare: { x: 80, y: 120 }, review: { x: 410, y: 120 } } } },
  nodeContracts: contracts,
  graph: {
    nodes: [graphNode("prepare", "bash-command"), graphNode("review", "agent")],
    edges: [{ id: "prepare:route:0", from: "prepare", to: "review", mode: "unconditional", status: "valid" }],
  },
};
const baseWorkflow = { id: "picker", name: "Picker", sourceFormat: "rattish", nodes: [], edges: [], agents: {} };
window.mutations = [];
window.positions = {};

function Fixture() {
  const [document, setDocument] = useState(initialDocument);
  const workflow = rattishGraphWorkflow(baseWorkflow, document);
  async function mutate(mutations) {
    window.mutations.push(mutations);
    let next = document;
    for (const mutation of mutations) {
      if (mutation.kind === "add_node") {
        next = { ...next, graph: { ...next.graph, nodes: [...next.graph.nodes, graphNode(mutation.node, mutation.node_type)] } };
      } else if (mutation.kind === "set_routes") {
        const edges = next.graph.edges.filter((edge) => edge.from !== mutation.node);
        mutation.routes.forEach((route, index) =>
          edges.push({ id: `${mutation.node}:route:${index}`, from: mutation.node, to: typeof route === "string" ? route : route.target, mode: "unconditional", status: "valid" }),
        );
        next = { ...next, graph: { ...next.graph, edges } };
      } else if (mutation.kind === "change_node_type") {
        next = { ...next, graph: { ...next.graph, nodes: next.graph.nodes.map((node) => (node.id === mutation.node ? graphNode(node.id, mutation.node_type) : node)) } };
      }
    }
    setDocument(next);
    return next;
  }
  function changeWorkflow(nextWorkflow) {
    const nodes = Object.fromEntries(nextWorkflow.nodes.map((node) => [node.id, { x: node.x, y: node.y }]));
    window.positions = nodes;
    setDocument((current) => ({ ...current, metadata: { canvas: { nodes } } }));
  }
  return (
    <div style={{ height: "100vh", display: "flex" }}>
      <DagCanvas
        logState={{ loading: false, error: "", text: "", path: null, runs: [] }}
        approvalState={{ approvals: [], error: "", loading: false }}
        rattishDocument={document}
        runState={{ running: false }}
        workflow={workflow}
        onImportWorkflow={() => {}}
        onLoadLatestLog={() => {}}
        onRattishMutation={mutate}
        onWorkflowChange={changeWorkflow}
      />
    </div>
  );
}

createRoot(window.document.getElementById("root")).render(<Fixture />);
