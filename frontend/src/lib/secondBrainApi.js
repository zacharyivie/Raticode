import { apiUrl } from "./api.js";
import { selectThreadBrains } from "./remResources.js";

export async function brainRequest(action = "snapshot", params = {}) {
  const workspace = window.goferDesktop?.workspace;
  const paths = [];
  if (action === "open") paths.push([params.root, Boolean(params.create)]);
  if (action === "update_brain" && params.patch?.root) paths.push([params.patch.root, false]);
  if (action === "import_preview") paths.push([params.path, false]);
  if (action === "task") {
    if (params.kind === "add-files") paths.push(...(params.paths || []).map(path => [path, false]));
    if (["import", "bibliography"].includes(params.kind)) paths.push([params.path, Boolean(params.export)]);
    if (["import", "export"].includes(params.kind)) paths.push([params.destination, true]);
  }
  const workspaceGrants = {};
  for (const [path, newPath] of paths) {
    if (!path) continue;
    const parent = path.replace(/[\\/]+$/, "").replace(/[\\/][^\\/]+$/, "") || "/";
    await workspace?.trustProjectRoot?.(newPath ? parent : path);
    workspaceGrants[path] = workspace?.pathGrantForApi?.(path) || workspace?.pathGrantForApi?.(parent);
  }
  const response = await fetch(apiUrl("/second-brains"), action === "snapshot" ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, params, workspaceGrants }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not access the Second Brain library.");
  if (payload.result === undefined) throw new Error("The Second Brain service returned an invalid response.");
  return payload.result;
}

// `legacy` is the Settings > Memory configuration, which also turns Second Brain on.
export async function threadBrainConfig(resources, legacy) {
  if (!legacy.enabled) return { enabled: false };
  let library;
  try {
    library = await brainRequest();
  } catch (error) {
    // Without the library a brain turned off here cannot be honored.
    if (resources.disabledSecondBrains?.length || resources.secondBrains?.length) throw error;
    return legacy;
  }
  const selection = selectThreadBrains(resources, library.brains, legacy);
  if (!selection.enabled) return { enabled: false };
  if (!selection.brains.length) return legacy;
  const brains = selection.brains;
  const workspaceGrants = {};
  for (const brain of brains) {
    await window.goferDesktop?.workspace?.trustProjectRoot?.(brain.root);
    workspaceGrants[brain.root] = window.goferDesktop?.workspace?.pathGrantForApi?.(brain.root);
  }
  return { ...legacy, enabled: true, brainIds: brains.map(brain => brain.id), root: brains[0].root, workspaceGrants };
}
