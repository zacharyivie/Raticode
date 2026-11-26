// Run dialog starting values: declared defaults, false for checkboxes, empty text otherwise.
export function initialWorkflowParameters(workflow) {
  const values = {};
  for (const [name, spec] of Object.entries(workflow.inputs ?? {})) {
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

