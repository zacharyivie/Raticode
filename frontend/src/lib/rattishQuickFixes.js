// One-click fixes for Rattish "Did you mean" hints. The compiler lists the other
// spellings in details.replacements only when the diagnostic's span is exactly the
// misspelled name, so a fix swaps that span's text and changes nothing else.
import { diagnosticToMarker, utf8ByteOffsetToTextOffset } from "./rattishRanges.js";

// A misspelled name is one word; anything else means the text moved since the check.
const NAME = /^[A-Za-z0-9_-]+$/;
// Per editor model: the fixable markers and the name each one covered when it was set.
const fixesByModel = new WeakMap();

function diagnosticReplacements(diagnostic) {
  const replacements = diagnostic?.details?.replacements;
  if (!Array.isArray(replacements)) return [];
  return [...new Set(replacements.filter((item) => typeof item === "string" && NAME.test(item)))];
}

function replacementTitle(current, replacement) {
  return current ? `Change '${current}' to '${replacement}'` : `Change to '${replacement}'`;
}

// The name a diagnostic's span covers in `source`, or "" when that text isn't one name.
function diagnosticSpanText(source, diagnostic) {
  const text = String(source ?? "");
  const start = utf8ByteOffsetToTextOffset(text, diagnostic?.span?.start?.offset);
  const end = utf8ByteOffsetToTextOffset(text, diagnostic?.span?.end?.offset);
  const current = text.slice(start, end);
  return NAME.test(current) ? current : "";
}

// The fixes a Problems entry offers, one per replacement, titled like the editor's Quick Fix.
export function diagnosticFixes(diagnostic, source) {
  if (diagnostic?.runtime) return [];
  const current = diagnosticSpanText(source, diagnostic);
  if (!current) return [];
  return diagnosticReplacements(diagnostic)
    .filter((replacement) => replacement !== current)
    .map((replacement) => ({ current, replacement, title: replacementTitle(current, replacement) }));
}

function diagnosticKey(diagnostic) {
  return [diagnostic?.code, diagnostic?.span?.start?.offset, diagnostic?.span?.end?.offset, diagnostic?.message].join("\u0000");
}

function markerRange(marker) {
  const { startLineNumber, startColumn, endLineNumber, endColumn } = marker;
  return { startLineNumber, startColumn, endLineNumber, endColumn };
}

// Shows the diagnostics as editor markers and remembers the ones with fixes. The model
// holds the text they were found in now, so the name each covers is recorded with it.
export function setRattishMarkers(monaco, model, diagnostics = []) {
  const source = model.getValue();
  const fixes = new Map();
  const markers = diagnostics.map((diagnostic) => {
    const marker = diagnosticToMarker(monaco, model, source, diagnostic);
    const replacements = diagnosticReplacements(diagnostic);
    const expected = model.getValueInRange(markerRange(marker));
    if (replacements.length && NAME.test(expected)) fixes.set(diagnosticKey(diagnostic), { expected, marker, replacements });
    return marker;
  });
  fixesByModel.set(model, fixes);
  monaco.editor.setModelMarkers(model, "rattish", markers);
}

// Replaces the diagnostic's name in the editor as one undoable step (Ctrl+Z puts it back).
// `expected` is the name the caller showed. Returns false, changing nothing, when the text
// there is no longer that name, as after an edit the check hasn't caught up with.
export function applyDiagnosticReplacement(monaco, editor, diagnostic, replacement, expected = "") {
  const model = editor?.getModel?.();
  if (!model || !diagnosticReplacements(diagnostic).includes(replacement)) return false;
  const range = markerRange(diagnosticToMarker(monaco, model, model.getValue(), diagnostic));
  const current = model.getValueInRange(range);
  const want = fixesByModel.get(model)?.get(diagnosticKey(diagnostic))?.expected || expected;
  if (!want || current !== want || current === replacement) return false;
  editor.pushUndoStop();
  editor.executeEdits("rattish-quick-fix", [{ range, text: replacement, forceMoveMarkers: true }]);
  editor.pushUndoStop();
  const end = range.startColumn + replacement.length;
  editor.setSelection({ startLineNumber: range.startLineNumber, startColumn: range.startColumn, endLineNumber: range.startLineNumber, endColumn: end });
  editor.revealRangeInCenterIfOutsideViewport?.(editor.getSelection());
  return true;
}

function sameRange(a, b) {
  return a.startLineNumber === b.startLineNumber && a.startColumn === b.startColumn
    && a.endLineNumber === b.endLineNumber && a.endColumn === b.endColumn;
}

// Quick Fix actions (Ctrl+.) for the markers under the cursor.
export function rattishCodeActions(model, markers = []) {
  const fixes = [...(fixesByModel.get(model)?.values() ?? [])];
  const actions = [];
  for (const marker of markers) {
    const fix = fixes.find((entry) => sameRange(entry.marker, marker) && entry.marker.message === marker.message);
    if (!fix || model.getValueInRange(markerRange(marker)) !== fix.expected) continue;
    fix.replacements.filter((replacement) => replacement !== fix.expected).forEach((replacement, index) => {
      actions.push({
        title: replacementTitle(fix.expected, replacement),
        kind: "quickfix",
        diagnostics: [marker],
        isPreferred: index === 0,
        edit: {
          edits: [{
            resource: model.uri,
            textEdit: { range: markerRange(marker), text: replacement },
            versionId: model.getVersionId(),
          }],
        },
      });
    });
  }
  return actions;
}

export function installRattishQuickFixes(monaco) {
  return monaco.languages.registerCodeActionProvider("rattish", {
    provideCodeActions(model, _range, context) {
      return { actions: rattishCodeActions(model, context.markers), dispose() {} };
    },
  }, { providedCodeActionKinds: ["quickfix"] });
}
