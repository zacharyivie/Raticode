import { askRemAboutFiles } from "./fileAttachments.js";

export function installRemActions(editor, getContext) {
  const actions = ["ask", "explain"].map(mode => editor.addAction?.({
    id: `raticode.rem.${mode}`,
    label: mode === "ask" ? "Ask Rem" : "Explain with Rem",
    contextMenuGroupId: "9_rem",
    contextMenuOrder: mode === "ask" ? 1 : 2,
    precondition: "editorHasSelection",
    run: current => {
      const selection = current.getSelection();
      const text = current.getModel()?.getValueInRange(selection);
      if (!text) return;
      window.dispatchEvent(new CustomEvent("gofer:rem-context", { detail: {
        ...getContext(), mode, text,
        startLine: selection.startLineNumber, endLine: selection.endLineNumber,
      } }));
    },
  }));
  // The whole file on disk; an older Git version shown in a diff isn't a file to attach.
  if (!getContext().version) actions.push(editor.addAction?.({
    id: "raticode.rem.file",
    label: "Ask Rem about this file",
    contextMenuGroupId: "9_rem",
    contextMenuOrder: 3,
    run: () => askRemAboutFiles([getContext().path]),
  }));
  return { dispose: () => actions.forEach(action => action?.dispose()) };
}
