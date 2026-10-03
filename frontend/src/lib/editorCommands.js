export const EDITOR_COMMAND_IDS = {
  "edit.undo": "undo",
  "edit.redo": "redo",
  "edit.cut": "editor.action.clipboardCutAction",
  "edit.copy": "editor.action.clipboardCopyAction",
  "edit.paste": "editor.action.clipboardPasteAction",
  "edit.find": "actions.find",
  "edit.replace": "editor.action.startFindReplaceAction",
  "selection.selectAll": "editor.action.selectAll",
  "selection.expand": "editor.action.smartSelect.expand",
  "selection.shrink": "editor.action.smartSelect.shrink",
  "selection.copyLineUp": "editor.action.copyLinesUpAction",
  "selection.copyLineDown": "editor.action.copyLinesDownAction",
  "selection.moveLineUp": "editor.action.moveLinesUpAction",
  "selection.moveLineDown": "editor.action.moveLinesDownAction",
  "selection.addCursorAbove": "editor.action.insertCursorAbove",
  "selection.addCursorBelow": "editor.action.insertCursorBelow",
  "edit.toggleLineComment": "editor.action.commentLine",
  "edit.formatDocument": "editor.action.formatDocument",
};

export function editorCommandMetadata(editor) {
  return Object.fromEntries(Object.entries(EDITOR_COMMAND_IDS).map(([command, id]) => [command, {
    supported: ["undo", "redo"].includes(id) || Boolean(editor.getAction(id)?.isSupported()),
    shortcut: editor._standaloneKeybindingService?.lookupKeybinding(id)?.getLabel() || "",
  }]));
}

export async function pasteIntoEditor(editor, readText, readOnly = false) {
  if (!readText) throw new Error("Clipboard access is unavailable.");
  if (readOnly) throw new Error("This document is read only.");
  const text = await readText();
  if (!text || !editor.getModel() || editor.getModel().isDisposed()) return;
  editor.focus();
  editor.pushUndoStop();
  editor.executeEdits("raticode-paste", editor.getSelections().map(range => ({ range, text, forceMoveMarkers: true })));
  editor.pushUndoStop();
}
