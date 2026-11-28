// Reading a Rattish document's source and its validation results, for the graph and its
// inspector. Spans are UTF-8 byte offsets into the source.
export function rattishRouteValue(edge, source = "") {
  const mode = edge.mode ?? "unconditional";
  if (mode === "unconditional" || mode === "always") return edge.to;
  if (mode === "otherwise") return { target: edge.to, mode: "otherwise" };
  const predicateSource = edge.predicateSource ?? sourceTextForSpan(source, edge.predicate?.span);
  return {
    target: edge.to,
    mode: "when",
    predicateSource: predicateSource || "succeeded",
  };
}

export function sourceTextForSpan(source, span) {
  if (!source || !span) return "";
  const bytes = new TextEncoder().encode(source);
  return new TextDecoder().decode(bytes.slice(span.start.offset, span.end.offset));
}

export function workflowValidationDiagnostics(workflow) {
  return [
    ...(workflow?.validationDiagnostics ?? []),
    ...(workflow?.validationErrors ?? []),
    ...(workflow?.validationWarnings ?? []),
  ].filter(
    (diagnostic, index, all) =>
      index ===
      all.findIndex(
        (candidate) =>
          candidate.id === diagnostic.id &&
          candidate.subject === diagnostic.subject &&
          candidate.field === diagnostic.field &&
          candidate.message === diagnostic.message,
      ),
  );
}

export function diagnosticsByTarget(diagnostics, targetType) {
  return diagnostics.reduce((grouped, diagnostic) => {
    if (diagnostic.targetType !== targetType || !diagnostic.targetId) return grouped;
    return {
      ...grouped,
      [diagnostic.targetId]: [...(grouped[diagnostic.targetId] ?? []), diagnostic],
    };
  }, {});
}
