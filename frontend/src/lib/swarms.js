import { apiUrl } from "./api.js";

export async function swarmRequest(projectRoot, path = "", { method = "GET", query = {}, workspacePaths = [], ...body } = {}) {
  const response = await fetch(apiUrl(`/swarms${path}?${new URLSearchParams({ projectRoot, ...query })}`), {
    method,
    ...(method === "GET" ? {} : {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, ...(workspacePaths.length ? { workspaceGrants: Object.fromEntries(workspacePaths.map(path => [path, window.goferDesktop?.workspace?.pathGrantForApi?.(path) || ""])) } : {}), projectRoot, grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(projectRoot) || undefined }),
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
  if (run?.failureReason) issues.unshift({ title: "Run failed", body: run.failureReason });
  if (run?.idleDiagnosis?.error) issues.push({ title: "Recovery explanation failed", body: run.idleDiagnosis.error });
  if (run?.cleanup?.error) issues.push({ title: "Cleanup needs attention", body: run.cleanup.error });
  for (const item of remaining.filter(item => item.status === "blocked")) {
    issues.push({ title: item.title, body: item.blocker || "Coordinator needs to unblock this milestone." });
  }
  const titles = new Map(milestones.map(item => [item.id, item.title]));
  for (const attempt of run?.attempts || []) {
    if (attempt.state === "uncertain") issues.push({ title: "Interrupted assignment", body: `${titles.get(attempt.milestoneId) || "Board conversation"}: review retained effects before retrying.` });
  }
  if (run?.integration?.error) issues.push({ title: "Combined checks", body: run.integration.error });
  const stalled = Boolean(run?.replanRequired) || (run?.state === "running" && !counts.working && !counts.queued && !counts.retry_wait);
  const success = run?.state === "completed" && milestones.some(item => item.status === "accepted") && !remaining.length && !issues.length;
  return { milestones, remaining, counts, issues, success, stalled,
    title: success ? "All milestones resolved" : issues.length ? "Needs attention" : run?.state === "completed" ? "Review the run outcome" : run?.replanRequired ? "Stalled: needs a new plan" : run?.state === "paused" ? "Run paused" : run?.state === "stopped" ? "Run stopped" : counts.retry_wait && !counts.working ? "Waiting for providers" : stalled ? "Waiting for the orchestrator" : run?.state === "completing" ? "Finishing up" : "Work in progress" };
}

// Defaults match RUN_LIMITS in src/gofer/ui/swarms.py.
export const RUN_LIMIT_DEFAULTS = { maxTurns: 200, maxRunSeconds: 8 * 3600, maxTurnSeconds: 3600, stallTurnLimit: 6, maxRepairAttempts: 3 };
export const FULL_ACCESS_MODES = new Set(["danger-full-access", "bypassPermissions"]);

export function fullAccessAgents(agents = []) {
  return agents.filter(agent => FULL_ACCESS_MODES.has(agent.permissionMode));
}

const RUN_STATE_LABELS = { ready: "Ready", running: "Running", paused: "Paused", stopping: "Stopping", stopped: "Stopped", completing: "Finishing", completed: "Completed", failed: "Failed" };

export function runStateLabel(state) {
  return RUN_STATE_LABELS[state] || String(state || "Ready").replaceAll("_", " ");
}

// What the sidebar flags without opening the swarm.
export function swarmAttention(run) {
  if (!run) return { waiting: 0, label: "" };
  const waiting = (run.humanInbox || []).filter(item => item.state === "pending").length;
  const label = waiting ? `${waiting} waiting for you` : run.state === "failed" ? "Needs attention" : run.replanRequired ? "Stalled" : run.state === "paused" && run.pauseReason ? "Paused" : "";
  return { waiting, label };
}

// Turns and reported tokens per agent, from the run's attempt records.
export function agentUsage(run, agents = []) {
  const rows = new Map(agents.map(agent => [agent.id, { agent, turns: 0, input: 0, output: 0, unreported: 0 }]));
  for (const attempt of run?.attempts || []) {
    const row = rows.get(attempt.ownerId);
    if (!row || !attempt.startedAt) continue;
    for (const usage of [...(attempt.usageHistory || []), attempt.usage]) {
      row.turns += 1;
      if (Number.isFinite(usage?.input_tokens) || Number.isFinite(usage?.output_tokens)) {
        row.input += Number(usage.input_tokens) || 0;
        row.output += Number(usage.output_tokens) || 0;
      } else row.unreported += 1;
    }
  }
  return [...rows.values()];
}

// One check per line, as command arguments. Double quotes group an argument.
export function parseChecks(text = "") {
  return text.split("\n").map(line => line.trim()).filter(Boolean).map(line => [...line.matchAll(/"([^"]*)"|(\S+)/g)].map(match => match[1] ?? match[2]));
}

export function formatChecks(checks = []) {
  return checks.map(argv => argv.map(arg => /\s/.test(arg) ? `"${arg}"` : arg).join(" ")).join("\n");
}

export function formatMinutes(seconds) {
  const minutes = Math.round((Number(seconds) || 0) / 60);
  return minutes >= 120 ? `${Math.round(minutes / 6) / 10} h` : `${minutes} min`;
}
