import { apiUrl } from "./api.js";

// Old TOML workflow files the backend found in the data folder. Raticode no longer
// loads or runs them; each can be converted to a Rattish workflow in a project.
export function legacyTomlFilesFromPayload(payload) {
  const files = Array.isArray(payload?.legacyTomlFiles) ? payload.legacyTomlFiles : [];
  return files
    .filter((file) => file && typeof file.path === "string" && file.path)
    .map((file) => ({ name: String(file.name || file.path.split(/[\\/]/).pop()), path: file.path }));
}

export function convertTomlWorkflowRequest({ path, projectRoot, projectGrantId = "" }) {
  return {
    url: apiUrl("/workflows/convert-toml"),
    options: {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, projectRoot, projectGrantId: projectGrantId || undefined }),
    },
  };
}

// Converts one TOML file into a registered Rattish workflow in projectRoot. The TOML
// file itself is left as it was.
export async function convertTomlWorkflow({ path, projectRoot }) {
  const projectGrantId = window.goferDesktop?.workspace?.pathGrantForApi?.(projectRoot) ?? "";
  const request = convertTomlWorkflowRequest({ path, projectRoot, projectGrantId });
  const response = await fetch(request.url, request.options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload.workflow) {
    throw new Error(payload.error || `Converting the workflow returned ${response.status}`);
  }
  return {
    workflow: payload.workflow,
    notes: Array.isArray(payload.conversion?.notes) ? payload.conversion.notes.map(String) : [],
    unsupported: Array.isArray(payload.conversion?.unsupported) ? payload.conversion.unsupported.map(String) : [],
  };
}
