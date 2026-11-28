import { apiJson } from "./api.js";

function generationRequest(path, body) {
  return apiJson(path, { method: body === undefined ? undefined : "POST", body, failure: "Could not load Rem generation jobs." });
}
export async function startGenerationJob(kind, request, selection) {
  if (request.projectRoot) await window.goferDesktop?.workspace?.trustProjectRoot?.(request.projectRoot);
  return generationRequest("/generation-jobs", { kind, ...selection, projectRoot: request.projectRoot,
    branch: request.branch, description: request.description, attachments: request.attachments, screenshot: request.screenshot,
    grantId: request.projectRoot ? window.goferDesktop?.workspace?.pathGrantForApi?.(request.projectRoot) : undefined });
}
export async function latestGenerationJob(kind, projectRoot = "", branch = "") {
  if (kind === "commit" && !projectRoot) return null;
  if (projectRoot) {
    try {
      // Registration must finish before reading the cached grant, including on
      // startup and after the backend's folder grant expires or is reset.
      await window.goferDesktop?.workspace?.trustProjectRoot?.(projectRoot);
    } catch (error) {
      if (["ENOENT", "ENOTDIR"].includes(error.code)) return null;
      throw error;
    }
  }
  const query = new URLSearchParams({ kind, projectRoot, branch });
  if (projectRoot) query.set("grantId", window.goferDesktop?.workspace?.pathGrantForApi?.(projectRoot) || "");
  return (await generationRequest(`/generation-jobs?${query}`)).jobs?.[0] || null;
}
export function dismissGenerationJob(id) { return generationRequest("/generation-jobs/dismiss", { id }); }
export function jobIsRunning(job) { return ["queued", "running"].includes(job?.status); }
