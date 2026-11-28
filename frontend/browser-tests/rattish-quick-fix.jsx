// A real Monaco editor on a Rattish file with one "Did you mean" problem, for
// rattish-quick-fix.browser.cjs.
import { loadRattishMonaco } from "../src/lib/monaco.js";
import { applyDiagnosticReplacement, rattishCodeActions, setRattishMarkers } from "../src/lib/rattishQuickFixes.js";
import "../src/styles/index.css";

const source = "Rattish: 1\nWorkflow:\n  name: Quick fix\nNode greet:\n  type: bash-command\n  command: echo hi\n  to: sumary\n"
  + "Node summary:\n  type: bash-command\n  command: echo done\n  needs: greet\n";
const start = new TextEncoder().encode(source.slice(0, source.indexOf("sumary"))).length;
const problem = {
  code: "RATTISH_UNRESOLVED_NODE",
  severity: "error",
  message: "Route target 'sumary' does not name a Node.",
  span: { start: { offset: start, line: 7, column: 7 }, end: { offset: start + 6, line: 7, column: 13 } },
  details: { source_node: "greet", target: "sumary", replacements: ["summary"] },
  suggestions: ["Did you mean 'summary'?"],
};

const monaco = loadRattishMonaco();
const container = document.createElement("div");
container.style.cssText = "width: 900px; height: 500px;";
document.body.appendChild(container);
const model = monaco.editor.createModel(source, "rattish", monaco.Uri.file("/fixture/workflow.rattish"));
const editor = monaco.editor.create(container, { model, editContext: false, automaticLayout: false });
setRattishMarkers(monaco, model, [problem]);

window.quickFix = {
  text: () => model.getValue(),
  focusName() { editor.focus(); editor.setPosition({ lineNumber: 7, column: 9 }); return editor.hasTextFocus(); },
  titles: () => rattishCodeActions(model, monaco.editor.getModelMarkers({ resource: model.uri })).map((action) => action.title),
  run: (command) => editor.trigger("test", command, null),
  // The Problems button's path: the same edit through the editor API.
  applyFromProblems: () => applyDiagnosticReplacement(monaco, editor, problem, "summary", "sumary"),
};
window.quickFixReady = true;
