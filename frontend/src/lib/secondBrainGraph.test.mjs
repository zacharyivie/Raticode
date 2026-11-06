import assert from "node:assert/strict";
import { test } from "node:test";
import { basisLabel, bubbleRadius, connectionItems, egoLayout, fuseResults, isSuggestion, mergedProject, projectDocuments, scalePoints } from "./secondBrainGraph.js";

const result = id => ({ doc: { id }, matchKind: "content", passages: [] });

test("combined search ranks a document found both ways above single matches", () => {
  const fused = fuseResults([result("a"), result("c")], [result("b"), result("c")]);
  assert.equal(fused[0].doc.id, "c");
  assert.deepEqual(fused[0].matchedBy, ["content", "meaning"]);
  assert.deepEqual(fused.map(item => item.doc.id).sort(), ["a", "b", "c"]);
  // Ties keep the exact list first, so a word match is never pushed below a meaning match.
  assert.deepEqual(fuseResults([result("a")], [result("b")]).map(item => item.doc.id), ["a", "b"]);
});

test("suggestions and evidence never share a label", () => {
  assert.equal(isSuggestion("meaning"), true);
  assert.equal(isSuggestion("words"), true);
  assert.equal(isSuggestion("observed"), false);
  assert.equal(isSuggestion("written"), false);
  assert.notEqual(basisLabel("asserted"), basisLabel("observed"));
  assert.equal(basisLabel("nonsense"), "Unknown basis");
});

test("topic coordinates fit inside the drawing with room for labels", () => {
  const placed = scalePoints([{ x: -1, y: 0 }, { x: 1, y: 2 }], 900, 540, 90);
  assert.deepEqual(placed.map(point => [point.px, point.py]), [[90, 90], [810, 450]]);
  assert.deepEqual(scalePoints([{ x: 5, y: 5 }], 900, 540).map(point => [point.px, point.py]), [[450, 270]]);
  assert.ok(bubbleRadius(1, 100) < bubbleRadius(100, 100));
});

test("the ego graph shows at most 14 neighbours and counts the rest", () => {
  const items = Array.from({ length: 20 }, (_, index) => ({ key: String(index), basis: "written" }));
  const layout = egoLayout({ title: "Doc" }, items);
  assert.equal(layout.nodes.length, 14);
  assert.equal(layout.hidden, 6);
});

test("connection items keep written links, uses and suggestions apart", () => {
  const items = connectionItems({
    links: { cites: ["a"], citedBy: ["b"] },
    usedBy: [{ kind: "ticket", basis: "observed", ticket: "LAB-9", title: "Use notes", taskId: "t" }],
    similar: [{ docId: "c", basis: "words", score: 0.3 }, { docId: "d", basis: "words", revision: true }],
  }, id => `Title ${id}`);
  assert.deepEqual(items.map(item => [item.relation, item.basis]), [["Links to", "written"], ["Linked from", "written"], ["Read by ticket", "observed"], ["Similar", "words"]]);
  assert.equal(items[2].title, "LAB-9 Use notes");
});

test("project pages split reports from notes and list newest first", () => {
  const documents = [
    { id: "r1", dates: { modified: "2026-01-01" } },
    { id: "r2", dates: { modified: "2026-02-01" } },
    { id: "n1", dates: { modified: "2026-03-01" } },
    { id: "x", dates: { modified: "2026-03-01" } },
  ];
  const assignments = { r1: { projectId: "p", kind: "reports" }, r2: { projectId: "p", kind: "reports" }, n1: { projectId: "p", kind: "knowledge" }, x: { projectId: "q", kind: "reports" } };
  const { reports, knowledge } = projectDocuments({ assignments }, documents, "p");
  assert.deepEqual(reports.map(row => row.doc.id), ["r2", "r1"]);
  assert.deepEqual(knowledge.map(row => row.doc.id), ["n1"]);
});

test("merging keeps the absorbed project's names and folders", () => {
  let next = 0;
  const merged = mergedProject({ id: "folder:raticode", name: "Raticode", aliases: [], roots: ["/r"], saved: false }, { id: "folder:taskurotta", name: "Taskurotta", aliases: ["Gofer Flow"], roots: ["/t"] }, "brain", prefix => `${prefix}-${++next}`);
  assert.equal(merged.id, "project-1");
  assert.equal(merged.name, "Raticode");
  assert.deepEqual(merged.aliases, ["Taskurotta", "Gofer Flow"]);
  assert.deepEqual(merged.roots, ["/r", "/t"]);
  const saved = mergedProject({ id: "project-9", name: "Raticode", aliases: ["Old"], roots: [], saved: true }, { name: "Raticode" }, "brain", () => "unused");
  assert.equal(saved.id, "project-9");
  assert.deepEqual(saved.aliases, ["Old"]);
});
