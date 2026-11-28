// The Rattish inspector lives in components/RattishInspector.jsx and the source helpers
// the graph and the inspector share live in lib/rattishSource.js. These tests run the
// helpers and keep DagCanvas.jsx from growing back past the size it had once the
// inspector moved out.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { parse } from "@babel/parser";

import { nodeDeclarationsText } from "./graphClipboard.js";
import { diagnosticsByTarget, rattishRouteValue, sourceTextForSpan, workflowValidationDiagnostics } from "./rattishSource.js";

const SRC = path.resolve(import.meta.dirname, "..");
const CANVAS_FILE = path.join(SRC, "components/DagCanvas.jsx");
const INSPECTOR_FILE = path.join(SRC, "components/RattishInspector.jsx");
// Measured right after the inspector moved out.
const CANVAS_MAX_LINES = 3563;
const MOVED = [
  "handleInspectorTabKeyDown", "RattishInspector", "RattishLatestActivation", "rattishEventAttempt",
  "RattishActivationRow", "RattishProfileField", "RattishFallbackProfiles", "RattishContractField",
  "SHELL_BASH_HELP", "rattishNodeHelp", "RemTaskField", "McpServerField", "mcpToolsQuery",
  "mcpArgumentSummary", "McpToolField", "RattishBindingSummary", "humanizeRattishField",
  "rattishDurationFromMilliseconds", "parseRattishIdentifierDraft", "updateRattishRoute",
  "HealthDiagnosticList", "NodeTypeField", "ProviderAccountsContext",
];
const SHARED = ["rattishRouteValue", "workflowValidationDiagnostics", "diagnosticsByTarget", "sourceTextForSpan"];

function program(file) {
  return parse(fs.readFileSync(file, "utf8"), { sourceType: "module", plugins: ["jsx"] }).program;
}

// Names a module declares at top level, exported or not.
function declaredNames(body) {
  return body.flatMap((statement) => {
    const node = statement.type === "ExportNamedDeclaration" || statement.type === "ExportDefaultDeclaration"
      ? statement.declaration
      : statement;
    if (!node) return [];
    if (node.type === "VariableDeclaration") return node.declarations.map((declaration) => declaration.id.name);
    return node.id?.name ? [node.id.name] : [];
  });
}

// Names a module passes on from another module with `export ... from`.
function reexportedNames(body) {
  return body
    .filter((statement) => statement.type === "ExportNamedDeclaration" && statement.source)
    .flatMap((statement) => statement.specifiers.map((specifier) => specifier.exported.name));
}

test("the graph canvas keeps the Rattish inspector and its fields out", () => {
  const source = fs.readFileSync(CANVAS_FILE, "utf8");
  const lines = source.split("\n").length - (source.endsWith("\n") ? 1 : 0);
  assert.ok(lines <= CANVAS_MAX_LINES, `DagCanvas.jsx has ${lines} lines, over ${CANVAS_MAX_LINES}. Put new inspector code in RattishInspector.jsx.`);
  const body = program(CANVAS_FILE).body;
  const declared = declaredNames(body);
  assert.deepEqual([...MOVED, ...SHARED].filter((name) => declared.includes(name)), []);
  assert.deepEqual(reexportedNames(body).filter((name) => MOVED.includes(name) || SHARED.includes(name)), []);
  assert.match(source, /import \{ ProviderAccountsContext, RattishInspector \} from "\.\/RattishInspector\.jsx";/);
});

test("the inspector declares what moved and shares the source helpers without passing them on", () => {
  const body = program(INSPECTOR_FILE).body;
  const declared = declaredNames(body);
  assert.deepEqual(MOVED.filter((name) => !declared.includes(name)), []);
  assert.deepEqual(SHARED.filter((name) => declared.includes(name)), []);
  assert.deepEqual(reexportedNames(body), []);
});

test("graph copy reads node declarations through sourceTextForSpan", () => {
  const clipboard = fs.readFileSync(path.join(SRC, "lib/graphClipboard.js"), "utf8");
  assert.match(clipboard, /import \{ sourceTextForSpan \} from "\.\/rattishSource\.js";/);
  assert.doesNotMatch(clipboard, /TextDecoder/);
});

test("source spans are UTF-8 byte offsets", () => {
  const source = 'node café: bash-command {\n  command: "echo ☕"\n}\nnode next: bash-command {}\n';
  const bytes = new TextEncoder().encode(source);
  // "é" and "☕" take more bytes than characters, so a character index would cut the wrong place.
  const secondStart = new TextEncoder().encode(source.slice(0, source.indexOf("node next"))).length;
  assert.notEqual(secondStart, source.indexOf("node next"));
  const first = { start: { offset: 0 }, end: { offset: secondStart - 1 } };
  const second = { start: { offset: secondStart }, end: { offset: bytes.length } };
  assert.equal(sourceTextForSpan(source, first), 'node café: bash-command {\n  command: "echo ☕"\n}');
  assert.equal(sourceTextForSpan(source, second), "node next: bash-command {}\n");
  assert.equal(sourceTextForSpan("", first), "");
  assert.equal(sourceTextForSpan(source, null), "");

  const nodes = [
    { rattish: { declarationIndex: 1, sourceSpan: second } },
    { rattish: { declarationIndex: 0, sourceSpan: first } },
    { rattish: { declarationIndex: 2 } },
  ];
  assert.equal(
    nodeDeclarationsText(source, nodes),
    'node café: bash-command {\n  command: "echo ☕"\n}\n\nnode next: bash-command {}\n',
  );
});

test("route values and diagnostics keep their shapes", () => {
  const source = "next when node.a.output.ok == true";
  const predicate = { span: { start: { offset: 10 }, end: { offset: source.length } } };
  assert.equal(rattishRouteValue({ to: "b" }), "b");
  assert.equal(rattishRouteValue({ to: "b", mode: "always" }), "b");
  assert.deepEqual(rattishRouteValue({ to: "b", mode: "otherwise" }), { target: "b", mode: "otherwise" });
  assert.deepEqual(rattishRouteValue({ to: "b", mode: "when", predicate }, source), {
    target: "b",
    mode: "when",
    predicateSource: "node.a.output.ok == true",
  });
  assert.deepEqual(rattishRouteValue({ to: "b", mode: "when" }), { target: "b", mode: "when", predicateSource: "succeeded" });

  const duplicate = { id: "x", subject: "a", field: "f", message: "Bad", targetType: "node", targetId: "a" };
  const diagnostics = workflowValidationDiagnostics({
    validationDiagnostics: [duplicate],
    validationErrors: [{ ...duplicate }],
    validationWarnings: [{ id: "y", message: "Hmm", targetType: "edge", targetId: "e1" }],
  });
  assert.equal(diagnostics.length, 2);
  assert.deepEqual(Object.keys(diagnosticsByTarget(diagnostics, "node")), ["a"]);
  assert.deepEqual(Object.keys(diagnosticsByTarget(diagnostics, "edge")), ["e1"]);
});
