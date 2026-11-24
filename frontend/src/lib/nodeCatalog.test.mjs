import assert from "node:assert/strict";
import { test } from "node:test";
import {
  groupNodeCatalog,
  nextNodeId,
  nodeCatalogEntries,
  nodeTypeLabel,
  pickedNodePosition,
} from "./nodeCatalog.js";

const contracts = [
  { nodeType: "agent", category: "ai", summary: "Ask an AI provider to do a task you describe", docsUrl: "https://raticode.dev/docs/nodes/agent" },
  { nodeType: "bash-command", category: "code", summary: "Run one command and capture its output", docsUrl: "https://raticode.dev/docs/nodes/bash-command" },
  { nodeType: "read-file", category: "files", summary: "Read a text file and pass on its content", docsUrl: "https://raticode.dev/docs/nodes/read-file" },
  { nodeType: "write-file", category: "files", summary: "Create, replace or append to a text file", docsUrl: "https://raticode.dev/docs/nodes/write-file" },
  { nodeType: "http-request", category: "web", summary: "Call a web service and pass on its response", docsUrl: "https://raticode.dev/docs/nodes/http-request" },
  { nodeType: "approval-gate", category: "approvals", summary: "Wait for a person to approve or reject", docsUrl: "https://raticode.dev/docs/nodes/approval-gate" },
  { nodeType: "loop", category: "flow", summary: "Run the next steps once for each item", docsUrl: "https://raticode.dev/docs/nodes/loop" },
  { nodeType: "workflow", category: "workflows", summary: "Run another workflow and use its output", docsUrl: "https://raticode.dev/docs/nodes/workflow" },
];

test("groups follow the picker's category order and keep every contract", () => {
  const groups = groupNodeCatalog(nodeCatalogEntries(contracts));
  assert.deepEqual(
    groups.map((group) => group.label),
    ["Run code", "Files", "AI", "Web", "Approvals and notices", "Flow control", "Other workflows"],
  );
  assert.deepEqual(groups[1].entries.map((entry) => entry.label), ["Read file", "Write file"]);
  assert.equal(groups.flatMap((group) => group.entries).length, contracts.length);
});

test("a new contract appears without UI changes, in its own group or under Other", () => {
  const entries = nodeCatalogEntries([
    ...contracts,
    { nodeType: "send-sms", category: "messaging", summary: "Text a phone" },
    { nodeType: "mystery" },
  ]);
  const groups = groupNodeCatalog(entries);
  assert.deepEqual(groups.slice(-2).map((group) => group.label), ["Messaging", "Other"]);
  const mystery = groups.at(-1).entries[0];
  assert.equal(mystery.label, "Mystery");
  assert.equal(mystery.summary, "");
  assert.equal(mystery.docsUrl, "https://raticode.dev/docs/nodes/mystery");
});

test("search matches names, types, summaries and group names, best match first", () => {
  const entries = nodeCatalogEntries(contracts);
  const types = (query) =>
    groupNodeCatalog(entries, query).flatMap((group) => group.entries.map((entry) => entry.nodeType));
  assert.deepEqual(types("file"), ["read-file", "write-file"]);
  assert.deepEqual(types("approve"), ["approval-gate"]);
  assert.deepEqual(types("web"), ["http-request"]);
  assert.deepEqual(types("  RUN  command "), ["bash-command"]);
  assert.deepEqual(types("no such node"), []);
});

test("docs links only point at the documentation site", () => {
  const [entry] = nodeCatalogEntries([
    { nodeType: "agent", docsUrl: "javascript:alert(1)" },
  ]);
  assert.equal(entry.docsUrl, "https://raticode.dev/docs/nodes/agent");
  const [other] = nodeCatalogEntries([{ nodeType: "agent", docsUrl: "https://evil.example/docs" }]);
  assert.equal(other.docsUrl, "https://raticode.dev/docs/nodes/agent");
});

test("labels read naturally", () => {
  assert.equal(nodeTypeLabel("common-llm-task"), "Common LLM task");
  assert.equal(nodeTypeLabel("mcp-tool"), "MCP tool");
  assert.equal(nodeTypeLabel("http-request"), "HTTP request");
  assert.equal(nodeTypeLabel("bash_command"), "Bash command");
});

test("a picked node goes right of the selected node without covering another", () => {
  const nodes = [
    { id: "a", x: 100, y: 100 },
    { id: "b", x: 430, y: 100 },
  ];
  assert.deepEqual(pickedNodePosition(nodes, "a"), { x: 430, y: 254 });
  assert.deepEqual(pickedNodePosition(nodes, "b"), { x: 760, y: 100 });
  assert.deepEqual(pickedNodePosition(nodes, undefined), { x: 100, y: 254 });
  assert.deepEqual(pickedNodePosition([], undefined), { x: 120, y: 120 });
});

test("new node IDs are named after the type and never reuse an ID", () => {
  assert.equal(nextNodeId([{ id: "read-file-1" }, { id: "Read-File-2" }], "read-file"), "read-file-3");
  assert.equal(nextNodeId([], "agent"), "agent-1");
});
