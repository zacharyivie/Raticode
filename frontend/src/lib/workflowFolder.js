// Workflows found outside a project's .raticode folder show their folder, so two with
// the same name (a starter and its copy, say) can be told apart.
export function workflowFolderLabel(workflow) {
  if (workflow?.managed !== false) return "";
  const folder = String(workflow.relativeFolder ?? "").trim();
  return folder === "." ? "Project folder" : folder;
}

// The workflow's folder in its project for the editor breadcrumb, or "" at the top.
export function workflowFolderPath(workflow) {
  const folder = String(workflow?.relativeFolder ?? "").trim();
  if (folder === ".") return "";
  return folder || `.raticode/${workflow?.id ?? ""}`;
}
