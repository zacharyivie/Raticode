import { apiUrl } from "./api.js";

export async function generationRequest(path, body) {
  const response = await fetch(apiUrl(path), body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Could not load Rem generation jobs.");
  return result;
}
export function startGenerationJob(kind, request, selection) {
  return generationRequest("/generation-jobs", { kind, ...selection, projectRoot: request.projectRoot,
    branch: request.branch, description: request.description, attachments: request.attachments, screenshot: request.screenshot,
    grantId: request.projectRoot ? window.goferDesktop?.workspace?.pathGrantForApi?.(request.projectRoot) : undefined });
}
export async function latestGenerationJob(kind, projectRoot = "", branch = "") {
  const query = new URLSearchParams({ kind, projectRoot, branch });
  if (projectRoot) query.set("grantId", window.goferDesktop?.workspace?.pathGrantForApi?.(projectRoot) || "");
  return (await generationRequest(`/generation-jobs?${query}`)).jobs?.[0] || null;
}
export function dismissGenerationJob(id) { return generationRequest("/generation-jobs/dismiss", { id }); }
export function jobIsRunning(job) { return ["queued", "running"].includes(job?.status); }
