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

export function objectiveProgressChange(event) {
  // Delivery resolution also has before/after snapshots, but those are objects.
  if (event.kind !== "objectives_updated" || !Array.isArray(event.payload?.before) || !Array.isArray(event.payload?.after)) return null;
  return { before: milestoneProgress(event.payload.before), after: milestoneProgress(event.payload.after) };
}

export function positiveDraft(value, fallback, minimum = 1) {
  const number = Number(value);
  return value.trim() && Number.isFinite(number) && number >= minimum ? number : fallback;
}

export function newSwarmAgent(orchestrator = false) {
  return { id: crypto.randomUUID(), name: orchestrator ? "Orchestrator" : "", role: orchestrator ? "Break down work, coordinate agents, and maintain progress." : "", provider: "codex", model: "", effort: "", isOrchestrator: orchestrator, allowSteering: false };
}

export function swarmOverview(run, agents = []) {
  const milestones = (run?.objectives || []).flatMap(item => item.milestones || []);
  const remaining = milestones.filter(item => !["accepted", "cancelled"].includes(item.status));
  const counts = { working: 0, idle: 0, queued: 0, retry_wait: 0 };
  const issues = [];
  for (const agent of agents) {
    const state = run?.agentStates?.[agent.id] || {};
    const status = state.state || "idle";
    counts[status] = (counts[status] || 0) + 1;
    if (state.error) issues.push({ title: agent.name, body: state.error, retryAt: state.retryAt });
  }
  if (run?.failureReason) issues.unshift({ title: "Run stopped", body: run.failureReason });
  if (run?.idleDiagnosis?.error) issues.push({ title: "Recovery explanation failed", body: run.idleDiagnosis.error });
  if (run?.cleanup?.error) issues.push({ title: "Cleanup needs attention", body: run.cleanup.error });
  for (const item of remaining.filter(item => item.status === "blocked")) {
    issues.push({ title: item.title, body: item.blocker || "Coordinator needs to unblock this milestone." });
  }
  for (const attempt of run?.attempts || []) {
    if (attempt.state === "uncertain") issues.push({ title: "Interrupted assignment", body: `${attempt.milestoneId || "Coordination"}: review retained effects before retrying.` });
  }
  if (run?.integration?.error) issues.push({ title: "Combined checks", body: run.integration.error });
  const stalled = run?.state === "running" && !counts.working && !counts.queued && !counts.retry_wait;
  const success = run?.state === "completed" && milestones.some(item => item.status === "accepted") && !remaining.length && !issues.length;
  return { milestones, remaining, counts, issues, success, stalled,
    title: success ? "All milestones resolved" : issues.length ? "Needs attention" : run?.state === "completed" ? "Review the run outcome" : run?.state === "paused" ? "Run paused" : run?.state === "stopped" ? "Run stopped" : counts.retry_wait && !counts.working ? "Waiting for providers" : stalled ? "Coordinator recovery needed" : run?.state === "completing" ? "Finishing up" : "Work in progress" };
}
