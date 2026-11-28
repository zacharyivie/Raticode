import assert from "node:assert/strict";
import { test } from "node:test";
import { fakeMonacoEditor } from "../../electron/tests/fake-monaco-editor.mjs";
import {
  applyDiagnosticReplacement,
  diagnosticFixes,
  installRattishQuickFixes,
  rattishCodeActions,
  setRattishMarkers,
} from "./rattishQuickFixes.js";

const SOURCE = "Rattish: 1\nWorkflow:\n  name: Résumé\nNode greet:\n  type: bash-command\n  command: echo hi\n  to: sumary\n";
// Offsets are UTF-8 bytes: 'é' twice makes 'sumary' start two bytes after its character index.
const START = Buffer.byteLength(SOURCE.slice(0, SOURCE.indexOf("sumary")));
const routeProblem = {
  code: "RATTISH_UNRESOLVED_NODE",
  severity: "error",
  message: "Route target 'sumary' does not name a Node.",
  span: { start: { offset: START, line: 7, column: 7 }, end: { offset: START + 6, line: 7, column: 13 } },
  details: { source_node: "greet", target: "sumary", replacements: ["summary", "summer"] },
  suggestions: ["Did you mean 'summary' or 'summer'?"],
};

test("a Did you mean problem offers one Change fix per replacement, closest first", () => {
  assert.deepEqual(diagnosticFixes(routeProblem, SOURCE).map((fix) => fix.title), [
    "Change 'sumary' to 'summary'",
    "Change 'sumary' to 'summer'",
  ]);
  // No replacements, a run failure, or text that is no longer one name: no button.
  assert.deepEqual(diagnosticFixes({ ...routeProblem, details: {} }, SOURCE), []);
  assert.deepEqual(diagnosticFixes({ ...routeProblem, runtime: true }, SOURCE), []);
  assert.deepEqual(diagnosticFixes(routeProblem, SOURCE.replace("  to: sumary", "  to: 'x y'")), []);
  assert.deepEqual(diagnosticFixes(routeProblem, undefined), []);
});

test("Quick Fix lists the replacements for the marker and edits only its range", () => {
  const { editor, model, monaco } = fakeMonacoEditor(SOURCE);
  setRattishMarkers(monaco, model, [routeProblem]);
  const [marker] = monaco.editor.markers;
  assert.deepEqual([marker.startLineNumber, marker.startColumn, marker.endColumn], [7, 7, 13]);

  const actions = rattishCodeActions(model, [marker]);
  assert.deepEqual(actions.map((action) => [action.title, action.kind, action.isPreferred]), [
    ["Change 'sumary' to 'summary'", "quickfix", true],
    ["Change 'sumary' to 'summer'", "quickfix", false],
  ]);
  const [edit] = actions[0].edit.edits;
  assert.equal(edit.resource, model.uri);
  assert.equal(edit.versionId, model.getVersionId());
  assert.deepEqual(edit.textEdit, { range: { startLineNumber: 7, startColumn: 7, endLineNumber: 7, endColumn: 13 }, text: "summary" });
  // A marker with no replacements gets nothing.
  assert.deepEqual(rattishCodeActions(model, [{ ...marker, message: "Other." }]), []);

  // Text typed before the name moves it out of the marker's range: no fix offered.
  editor.type(SOURCE.indexOf("  to:"), "  ");
  assert.deepEqual(rattishCodeActions(model, [marker]), []);
});

test("applying a fix is one undoable edit, and a fix that no longer matches changes nothing", () => {
  const { editor, model, monaco } = fakeMonacoEditor(SOURCE);
  setRattishMarkers(monaco, model, [routeProblem]);

  assert.equal(applyDiagnosticReplacement(monaco, editor, routeProblem, "summary"), true);
  assert.match(model.getValue(), /\n {2}to: summary\n$/);
  assert.deepEqual(editor.getSelection(), { startLineNumber: 7, startColumn: 7, endLineNumber: 7, endColumn: 14 });
  editor.undo();
  assert.equal(model.getValue(), SOURCE);

  // Only names the compiler listed are applied.
  assert.equal(applyDiagnosticReplacement(monaco, editor, routeProblem, "anything"), false);
  // After an edit the check hasn't seen, the span covers other text: refuse.
  editor.type(SOURCE.indexOf("  to:"), "#");
  assert.equal(applyDiagnosticReplacement(monaco, editor, routeProblem, "summary"), false);
  assert.match(model.getValue(), /sumary/);
});

test("an editor that never showed the markers applies the fix only over the name the caller saw", () => {
  const { editor, model, monaco } = fakeMonacoEditor(SOURCE);
  assert.equal(applyDiagnosticReplacement(monaco, editor, routeProblem, "summary"), false);
  assert.equal(applyDiagnosticReplacement(monaco, editor, routeProblem, "summary", "greet"), false);
  assert.equal(applyDiagnosticReplacement(monaco, editor, routeProblem, "summary", "sumary"), true);
  assert.match(model.getValue(), /to: summary/);
});

test("the Rattish Quick Fix provider answers with the code actions for the context's markers", () => {
  const { model, monaco } = fakeMonacoEditor(SOURCE);
  let registration;
  monaco.languages = { registerCodeActionProvider: (language, provider, metadata) => { registration = { language, provider, metadata }; return { dispose() {} }; } };
  installRattishQuickFixes(monaco);
  setRattishMarkers(monaco, model, [routeProblem]);
  assert.equal(registration.language, "rattish");
  assert.deepEqual(registration.metadata, { providedCodeActionKinds: ["quickfix"] });
  const list = registration.provider.provideCodeActions(model, null, { markers: monaco.editor.markers });
  assert.equal(list.actions[0].title, "Change 'sumary' to 'summary'");
  assert.equal(typeof list.dispose, "function");
});
