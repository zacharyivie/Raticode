import { apiUrl } from "./api.js";

export async function swarmRequest(projectRoot, path = "", { method = "GET", query = {}, ...body } = {}) {
  const response = await fetch(apiUrl(`/swarms${path}?${new URLSearchParams({ projectRoot, ...query })}`), {
    method,
    ...(method === "GET" ? {} : {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, projectRoot, grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(projectRoot) || undefined }),
    }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || payload.error || "Could not update swarm. Try again.");
  return payload;
}

export function milestoneProgress(objectives = []) {
  const milestones = objectives.flatMap((objective) => objective.milestones || []).filter((item) => item.status !== "cancelled");
  const totalWeight = milestones.reduce((sum, item) => sum + Number(item.weight || 1), 0);
  const accepted = milestones.filter((item) => item.status === "accepted");
  const acceptedWeight = accepted.reduce((sum, item) => sum + Number(item.weight || 1), 0);
  return { percent: totalWeight ? acceptedWeight / totalWeight * 100 : null, totalWeight, acceptedWeight, acceptedCount: accepted.length, totalCount: milestones.length };
}

export function positiveDraft(value, fallback, minimum = 1) {
  const number = Number(value);
  return value.trim() && Number.isFinite(number) && number >= minimum ? number : fallback;
}

export function newSwarmAgent(orchestrator = false) {
  return { id: crypto.randomUUID(), name: orchestrator ? "Orchestrator" : "", role: orchestrator ? "Break down work, coordinate agents, and maintain progress." : "", provider: "codex", model: "", effort: "", isOrchestrator: orchestrator, allowSteering: false };
}
