const encoder = new TextEncoder();

export function utf8ByteOffsetToTextOffset(source, byteOffset) {
  const target = Math.max(0, Number(byteOffset) || 0);
  let bytes = 0;
  let textOffset = 0;

  for (const character of String(source ?? "")) {
    const nextBytes = bytes + encoder.encode(character).length;
    if (nextBytes > target) break;
    bytes = nextBytes;
    textOffset += character.length;
  }

  return textOffset;
}

export function diagnosticToMarker(monaco, model, source, diagnostic) {
  const startOffset = utf8ByteOffsetToTextOffset(source, diagnostic?.span?.start?.offset);
  const endOffset = utf8ByteOffsetToTextOffset(source, diagnostic?.span?.end?.offset);
  const start = model.getPositionAt(startOffset);
  let end = model.getPositionAt(Math.max(startOffset, endOffset));
  if (end.lineNumber === start.lineNumber && end.column === start.column) {
    const maxColumn = model.getLineMaxColumn(start.lineNumber);
    end = {
      lineNumber: start.lineNumber,
      column: Math.min(maxColumn, start.column + 1),
    };
  }
  return {
    code: diagnostic?.code,
    endColumn: end.column,
    endLineNumber: end.lineNumber,
    message: diagnosticMessage(diagnostic),
    severity: markerSeverity(monaco, diagnostic?.severity),
    source: "Rattish",
    startColumn: start.column,
    startLineNumber: start.lineNumber,
  };
}

// The message plus the compiler's fix hints, such as "Did you mean 'write-file'?",
// one per line so the editor's hover shows them under the error.
export function diagnosticMessage(diagnostic) {
  const message = diagnostic?.message || "Rattish diagnostic";
  const hints = Array.isArray(diagnostic?.suggestions)
    ? diagnostic.suggestions.filter((hint) => typeof hint === "string" && hint.trim())
    : [];
  return hints.length ? [message, ...hints].join("\n") : message;
}

function markerSeverity(monaco, severity) {
  if (severity === "warning") return monaco.MarkerSeverity.Warning;
  if (severity === "info") return monaco.MarkerSeverity.Info;
  return monaco.MarkerSeverity.Error;
}
