// Monaco 0.56+ exposes its ESM files as "monaco-editor/<path>" and moved the
// CSS, HTML, JSON and TypeScript language features to languages/features/*.
import TsWorker from "monaco-editor/languages/features/typescript/ts.worker?worker";
import CssWorker from "monaco-editor/languages/features/css/css.worker?worker";
import HtmlWorker from "monaco-editor/languages/features/html/html.worker?worker";
import JsonWorker from "monaco-editor/languages/features/json/json.worker?worker";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";
import { getJavaScriptWorker, getTypeScriptWorker } from "monaco-editor/languages/features/typescript/register";
import "monaco-editor/languages/features/css/register";
import "monaco-editor/languages/features/html/register";
import { getWorker as getJsonWorker } from "monaco-editor/languages/features/json/register";
import * as monaco from "monaco-editor/editor/editor.api";
import "monaco-editor/languages/definitions/register.all";
import "monaco-editor/editor/contrib/smartSelect/browser/smartSelect";
import "monaco-editor/editor/contrib/gotoSymbol/browser/goToCommands";
import "monaco-editor/editor/contrib/parameterHints/browser/parameterHints";
import "monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching";
import "monaco-editor/editor/contrib/caretOperations/browser/caretOperations";
import "monaco-editor/editor/contrib/clipboard/browser/clipboard";
// Quick Fix (Ctrl+.), the light bulb and the hover's "Quick Fix..." link.
import "monaco-editor/editor/contrib/codeAction/browser/codeActionContributions";
import "monaco-editor/editor/contrib/codelens/browser/codelensController";
import "monaco-editor/editor/contrib/comment/browser/comment";
import "monaco-editor/editor/contrib/contextmenu/browser/contextmenu";
import "monaco-editor/editor/contrib/find/browser/findController";
import "monaco-editor/editor/contrib/folding/browser/folding";
import "monaco-editor/editor/contrib/format/browser/formatActions";
import "monaco-editor/editor/contrib/gotoError/browser/gotoError";
import "monaco-editor/editor/contrib/hover/browser/hoverContribution";
import "monaco-editor/editor/contrib/linesOperations/browser/linesOperations";
import "monaco-editor/editor/contrib/multicursor/browser/multicursor";
import "monaco-editor/editor/contrib/suggest/browser/suggestController";
import "monaco-editor/editor/contrib/wordHighlighter/browser/wordHighlighter";
import "monaco-editor/editor/contrib/wordOperations/browser/wordOperations";
import { installRattishQuickFixes } from "./rattishQuickFixes.js";

if (typeof self !== "undefined") {
  self.MonacoEnvironment = {
    ...self.MonacoEnvironment,
    getWorker(_moduleId, label) {
      if (["javascript", "typescript"].includes(label)) return new TsWorker();
      if (["css", "scss", "less"].includes(label)) return new CssWorker();
      if (["html", "handlebars", "razor"].includes(label)) return new HtmlWorker();
      return label === "json" ? new JsonWorker() : new EditorWorker();
    },
  };
}

export async function prepareLanguageServices(model) {
  const language = model.getLanguageId();
  const workerFactory = {
    // CSS and HTML expose no worker getter; their providers load on first use.
    typescript: getTypeScriptWorker,
    javascript: getJavaScriptWorker,
    json: getJsonWorker,
  }[language];
  if (workerFactory) {
    // The encounter-language event may register the mode on the next turn.
    for (let attempt = 0; attempt < 20; attempt++) {
      try { const worker = await workerFactory(); await worker(model.uri); return; }
      catch (error) {
        if (!/not registered/i.test(String(error)) || attempt === 19) throw error;
        await new Promise(resolve => setTimeout(resolve, 25));
      }
    }
  }
}

let registered = false;

export function loadRattishMonaco() {
  if (!registered) {
    registerRattishLanguage();
    registered = true;
  }
  return monaco;
}

function registerRattishLanguage() {
  monaco.languages.register({
    id: "rattish",
    aliases: ["Rattish", "rattish"],
    extensions: [".rattish", ".rad"],
  });
  installRattishQuickFixes(monaco);
  monaco.languages.setLanguageConfiguration("rattish", {
    comments: { lineComment: "#" },
    indentationRules: {
      increaseIndentPattern: /^\s*(?:Workflow|Node\s+[A-Za-z][A-Za-z0-9-]*|with|needs|to|inputs|outputs|environment)\s*:\s*(?:#.*)?$/i,
      decreaseIndentPattern: /^\s*(?:Rattish|Radish|Workflow|Node\s+[A-Za-z][A-Za-z0-9-]*)\b/i,
    },
    onEnterRules: [
      {
        beforeText: /^\s*(?:Workflow|Node\s+[A-Za-z][A-Za-z0-9-]*|with|needs|to|inputs|outputs|environment)\s*:\s*(?:#.*)?$/i,
        action: { indentAction: monaco.languages.IndentAction.Indent },
      },
    ],
  });
  monaco.languages.setMonarchTokensProvider("rattish", {
    defaultToken: "",
    ignoreCase: true,
    tokenizer: {
      root: [
        [/^(\s*)(Rattish|Radish)(\s*:)/, ["", "keyword.rattish", "delimiter"]],
        [/^(\s*)(Workflow)(\s*:)/, ["", "keyword.rattish", "delimiter"]],
        [/^(\s*)(Node)(\s+)([A-Za-z][A-Za-z0-9-]*)(\s*:)/, ["", "keyword.rattish", "", "type.identifier", "delimiter"]],
        [/#.*$/, "comment"],
        [/\{\{[^}]+\}\}/, "variable.predefined"],
        [/"(?:\\.|[^"\\])*"/, "string"],
        [/\b(?:true|false|null)\b/, "constant"],
        [/\b\d+(?:\.\d+)?(?:ms|s|m|h|d)?\b/, "number"],
        [/\b(?:and|or|not|contains|matches|exists|is)\b/, "keyword.operator"],
        [/(?:==|!=|<=|>=|<|>)/, "operator"],
        [/\b(?:agent|bash-command|python-script|prompt-file|file|folder|open-resource|http-request|read-web-page|mcp-tool|jev|notification|approval-gate|local-search|local-vectorize|common-llm-task|rem-prompt|rem-task|loop|break|workflow)\b/, "type"],
        [/^(\s*)([A-Za-z][A-Za-z0-9-]*)(\s*:)/, ["", "attribute.name", "delimiter"]],
        [/[{},]/, "delimiter"],
      ],
    },
  });
  monaco.editor.defineTheme("gofer-rattish-light", {
    base: "vs",
    inherit: true,
    colors: {
      "editor.background": "#eee9df",
      "editor.foreground": "#30343b",
      "editorLineNumber.foreground": "#62635f",
      "editor.selectionBackground": "#c4ccf4",
      "editorWidget.background": "#f4f0e7",
      "editorWidget.border": "#b6b3aa",
      "editorSuggestWidget.background": "#f4f0e7",
      "editorSuggestWidget.selectedBackground": "#c4ccf4",
      "editorHoverWidget.background": "#f4f0e7",
      "editorHoverWidget.border": "#b6b3aa",
      "editor.lineHighlightBackground": "#4f46e512",
      "editorGutter.background": "#e4dfd5",
      "editorLineNumber.activeForeground": "#4338ca",
      "minimap.background": "#e4dfd5",
      "editorOverviewRuler.background": "#e4dfd5",
    },
    rules: [
      { token: "keyword.rattish", foreground: "6D28D9", fontStyle: "bold" },
      { token: "type.identifier", foreground: "18181B", fontStyle: "bold" },
      { token: "attribute.name", foreground: "1D4ED8" },
      { token: "type", foreground: "0D6F8A", fontStyle: "bold" },
      { token: "variable.predefined", foreground: "0D6F8A" },
      { token: "string", foreground: "137537" },
      { token: "number", foreground: "92400E" },
      { token: "keyword.operator", foreground: "7C3AED", fontStyle: "bold" },
      { token: "operator", foreground: "7C3AED" },
      { token: "comment", foreground: "62635F", fontStyle: "italic" },
    ],
  });
  monaco.editor.defineTheme("gofer-rattish-dark", {
    base: "vs-dark",
    inherit: true,
    colors: {
      "editor.background": "#19191b",
      "editor.lineHighlightBackground": "#6366F124",
      "editorGutter.background": "#19191b",
    },
    rules: [
      { token: "keyword.rattish", foreground: "C4B5FD", fontStyle: "bold" },
      { token: "type.identifier", foreground: "F4F4F5", fontStyle: "bold" },
      { token: "attribute.name", foreground: "93C5FD" },
      { token: "type", foreground: "67E8F9", fontStyle: "bold" },
      { token: "variable.predefined", foreground: "67E8F9" },
      { token: "string", foreground: "86EFAC" },
      { token: "number", foreground: "FCD34D" },
      { token: "keyword.operator", foreground: "C4B5FD", fontStyle: "bold" },
      { token: "operator", foreground: "C4B5FD" },
      { token: "comment", foreground: "71717A", fontStyle: "italic" },
    ],
  });
}
