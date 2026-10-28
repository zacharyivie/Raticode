// Run dialog starting values: declared defaults, false for checkboxes, empty text otherwise.
export function initialWorkflowParameters(workflow) {
  const values = {};
  for (const [name, spec] of Object.entries(workflow.inputs ?? workflow.parameters ?? {})) {
    if (spec.default !== undefined && spec.default !== null) {
      values[name] = spec.default;
    } else if (spec.type === "boolean") {
      values[name] = false;
    } else {
      values[name] = "";
    }
  }
  return values;
}


// Deduplicated filesystem grants with explicit read, write and execute flags.
export function normalizeWorkflowFilesystemAccess(entries = []) {
  const seen = new Set();
  return (entries ?? [])
    .map((entry) => ({
      path: String(entry?.path ?? "").trim(),
      read: entry?.read ?? true,
      write: entry?.write ?? true,
      execute: entry?.execute ?? false,
    }))
    .filter((entry) => {
      const key = entry.path.replace(/\\/g, "/").replace(/\/+$/, "");
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}
