// A small stand-in for a Monaco editor and its text model, for tests that edit text
// through the editor API without loading Monaco. Positions are 1-based like Monaco's,
// and undo() steps back to the text before the last group of edits.
export function fakeMonacoEditor(text) {
  let value = text;
  let version = 1;
  let selection = null;
  const undoStack = [];
  let groupOpen = false;
  const lines = () => value.split("\n");
  const offsetAt = ({ lineNumber, column }) => lines().slice(0, lineNumber - 1).reduce((sum, line) => sum + line.length + 1, 0) + column - 1;
  const model = {
    uri: { toString: () => "file:///fixture/workflow.rattish" },
    getValue: () => value,
    getVersionId: () => version,
    getLineMaxColumn: (lineNumber) => (lines()[lineNumber - 1] ?? "").length + 1,
    getPositionAt(offset) {
      const before = value.slice(0, Math.max(0, Math.min(offset, value.length))).split("\n");
      return { lineNumber: before.length, column: before.at(-1).length + 1 };
    },
    getValueInRange: (range) => value.slice(
      offsetAt({ lineNumber: range.startLineNumber, column: range.startColumn }),
      offsetAt({ lineNumber: range.endLineNumber, column: range.endColumn }),
    ),
  };
  const monaco = {
    MarkerSeverity: { Warning: 4, Info: 2, Error: 8 },
    editor: { markers: [], setModelMarkers(_model, _owner, markers) { monaco.editor.markers = markers; } },
  };
  const editor = {
    getModel: () => model,
    pushUndoStop() { groupOpen = false; },
    executeEdits(_source, edits) {
      if (!groupOpen) undoStack.push(value);
      groupOpen = true;
      for (const { range, text: replacement } of edits) {
        const start = offsetAt({ lineNumber: range.startLineNumber, column: range.startColumn });
        const end = offsetAt({ lineNumber: range.endLineNumber, column: range.endColumn });
        value = value.slice(0, start) + replacement + value.slice(end);
        version += 1;
      }
      return true;
    },
    setSelection(range) { selection = range; },
    getSelection: () => selection,
    undo() { value = undoStack.pop() ?? value; groupOpen = false; version += 1; },
    // Typing elsewhere, as a person would between a check and a click.
    type(offset, typed) { value = value.slice(0, offset) + typed + value.slice(offset); version += 1; },
  };
  return { editor, model, monaco };
}
