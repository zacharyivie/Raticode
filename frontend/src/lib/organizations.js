import { providerPermissionDefault } from "./providerPermissions.js";
import { apiUrl } from "./api.js";

const READS = new Set(["availability", "list", "read", "history", "events", "export", "help", "tasks", "attempts", "routines", "outcomes", "rehearse", "diagnostics", "workflow_preview", "run_events", "audit_verify", "run_input", "knowledge_search", "knowledge_read", "ticket_changes", "repository"]);
export async function organizationRequest(projectRoot = "", action = "list", organizationId, params = {}) {
  projectRoot = projectRoot || "";
  const read = READS.has(action);
  const workspaceGrants = {};
  if (!read) {
    const workspace = window.goferDesktop?.workspace;
    if (projectRoot) await workspace?.trustProjectRoot?.(projectRoot);
    for (const path of [...(params.config?.projectRoots || []), ...(params.config?.projects || []).map(item => item.workspacePath).filter(Boolean)]) {
      await workspace?.trustProjectRoot?.(path);
      workspaceGrants[path] = workspace?.pathGrantForApi?.(path);
    }
    const brain = params.config?.remSecondBrain;
    if (brain?.enabled && brain.root) {
      await workspace?.trustProjectRoot?.(brain.root);
      params = { ...params, config: { ...params.config, remSecondBrain: { ...brain, grantId: workspace?.pathGrantForApi?.(brain.root) } } };
    }
  }
  const query = new URLSearchParams({ projectRoot, action, ...(organizationId ? { organizationId } : {}), ...(read ? Object.fromEntries(Object.entries(params).filter(([, value]) => value != null && value !== "")) : {}) });
  const response = await fetch(apiUrl(`/organizations?${query}`), read ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ projectRoot, action, organizationId, params, workspaceGrants,
      grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(projectRoot) || undefined }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not update the organization. Retry after refreshing.");
  return payload.result;
}

export function historyByDate(history, locale) {
  const buckets = new Map();
  for (const revision of history) {
    const date = new Date(revision.at).toLocaleDateString(locale, { year: "numeric", month: "long", day: "numeric" });
    if (!buckets.has(date)) buckets.set(date, []);
    buckets.get(date).push(revision);
  }
  return [...buckets].map(([date, revisions]) => ({ date, revisions }));
}

export function employeeTree(employees) {
  const result = [];
  const visited = new Set();
  function visit(manager, depth) {
    for (const employee of employees.filter(item => (item.reportsTo || null) === manager)) {
      if (visited.has(employee.id)) continue;
      visited.add(employee.id); result.push({ ...employee, depth }); visit(employee.id, depth + 1);
    }
  }
  visit(null, 0);
  for (const employee of employees) if (!visited.has(employee.id)) result.push({ ...employee, depth: 0 });
  return result;
}

export function reportingHierarchy(employees) {
  const roots = [], parents = [];
  const byId = new Map(employees.map(employee => [employee.id, employee]));
  const local = employees.map(employee => byId.has(employee.reportsTo) ? employee : { ...employee, reportsTo: null });
  for (const employee of employeeTree(local)) {
    const node = { ...employee, reportsTo: byId.get(employee.id).reportsTo, children: [] };
    if (employee.depth === 0) roots.push(node);
    else parents[employee.depth - 1].children.push(node);
    parents[employee.depth] = node;
  }
  return roots;
}

// Team membership follows the manager's reporting chain. Keep cycle-safe for legacy data.
export function teamMembers(employees, team) {
  if (!team.manager) return [];
  const ids = new Set([team.manager]);
  let size;
  do {
    size = ids.size;
    for (const employee of employees) if (ids.has(employee.reportsTo)) ids.add(employee.id);
  } while (size !== ids.size);
  return employees.filter(employee => ids.has(employee.id));
}

export function teamGroups(config) {
  const assigned = new Set();
  const groups = config.teams.map(team => {
    const employees = teamMembers(config.employees, team);
    employees.forEach(employee => assigned.add(employee.id));
    return { team, employees };
  });
  const unassigned = config.employees.filter(employee => !assigned.has(employee.id));
  if (unassigned.length) groups.push({ team: null, employees: unassigned });
  return groups;
}

export function projectPeople(employees, project, tasks) {
  const assigned = new Set(tasks.filter(task => task.project === project.id).map(task => task.assignee));
  return employees.filter(employee => employee.id === project.owner || assigned.has(employee.id)).map(employee => ({
    ...employee,
    assignment: [employee.id === project.owner && "Initiative owner", assigned.has(employee.id) && "Task assignee"].filter(Boolean).join(", "),
  }));
}

export function eligibleManagers(employees, employeeId) {
  const byId = new Map(employees.map(employee => [employee.id, employee]));
  return employees.filter(employee => {
    const seen = new Set([employeeId]);
    let current = employee;
    while (current) {
      if (seen.has(current.id)) return false;
      seen.add(current.id);
      current = byId.get(current.reportsTo);
    }
    return true;
  });
}

export function configurationChanges(before, after, path = "") {
  if (JSON.stringify(before) === JSON.stringify(after)) return [];
  if (Array.isArray(before) && Array.isArray(after) && [...before, ...after].every(item => item && typeof item === "object" && "id" in item)) {
    return configurationChanges(Object.fromEntries(before.map(item => [item.id, item])), Object.fromEntries(after.map(item => [item.id, item])), path);
  }
  if (before && after && !Array.isArray(before) && !Array.isArray(after) && typeof before === "object" && typeof after === "object") {
    return [...new Set([...Object.keys(before), ...Object.keys(after)])].sort().flatMap(key => configurationChanges(before[key], after[key], `${path}/${key}`));
  }
  return [{ path: path || "/", before, after }];
}

export const TASK_STATUSES = ["backlog", "todo", "in_progress", "in_review", "blocked", "done", "cancelled"];
export const TASK_PRIORITIES = ["critical", "high", "medium", "low"];
export const STATUS_LABELS = { backlog: "Backlog", todo: "To do", in_progress: "In progress", in_review: "In review",
  blocked: "Blocked", done: "Done", cancelled: "Cancelled", approval: "Needs approval", attention: "Needs you" };
const GROUP_ORDER = ["approval", "in_progress", "in_review", "todo", "blocked", "backlog", "done", "cancelled"];

export const plural = (count, noun, many = `${noun}s`) => `${count} ${count === 1 ? noun : many}`;
export const needsApproval = task => Boolean(task.approvalRequired && task.approvedRevision == null);
export const isOpenTask = task => !["done", "cancelled"].includes(task.status);

export function initials(name = "") {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  return (words.length > 1 ? words[0][0] + words.at(-1)[0] : words[0].slice(0, 2)).toUpperCase();
}

export function formatUsd(value) {
  if (value == null || !Number.isFinite(value)) return "Unknown";
  return `$${value.toFixed(value > 0 && value < 0.01 ? 4 : 2)}`;
}

// A zero initialized by the ledger is not a reported zero when every turn lacks cost.
export function usageCost(account = {}) {
  if (account.turns > 0 && account.unknownCostTurns >= account.turns) return "Unknown";
  return formatUsd(account.costUsd);
}

export function actorLabel(actor = "", employees = []) {
  if (actor === "user") return "You";
  if (actor === "system") return "Raticode";
  if (actor === "Rem" || actor.startsWith("Rem:")) return "Rem";
  if (actor.startsWith("employee:")) {
    const id = actor.slice("employee:".length);
    return employees.find(employee => employee.id === id)?.name || id;
  }
  return actor || "Unknown";
}

export function employeeStatus(employee, tasks = []) {
  const own = tasks.filter(task => task.assignee === employee.id);
  if (own.some(task => task.claim)) return "working";
  if (own.some(task => ["blocked", "in_review"].includes(task.status))) return "attention";
  return employee.paused ? "paused" : "available";
}

// Codex and the CLI-managed providers don't report dollar cost, so dollar budgets pause them.
const COST_REPORTING = new Set(["claude_code"]);
export const reportsCost = provider => COST_REPORTING.has(provider);
const DANGEROUS_MODES = new Set(["danger-full-access", "bypassPermissions"]);
export const dangerousPermission = mode => DANGEROUS_MODES.has(mode);

// What needs the user right now, for the overview strip.
// Rem's requests expire after a day; older ones can no longer be approved.
export const CONSENT_TTL_MS = 24 * 60 * 60 * 1000;
// Requests waiting on the user. In Auto mode, Rem's review decides the rest (waitingOn "rem").
export function pendingRequests(runtime = {}, now = Date.now()) {
  return Object.values(runtime.consentRequests || {}).filter(request => request.status === "pending" && request.waitingOn !== "rem" && now - Date.parse(request.at) < CONSENT_TTL_MS);
}

// Auto mode: Rem decides the organization's requests (gofer/ui/organization_auto.py).
export const AUTO_MODE_EXPLAINED = "Rem approves, changes or declines this organization's requests instead of you: Rem's own requests, plans and proposals waiting for approval, and finished work waiting for your review. Something dangerous gets declined or made safe first. The organization stops only when something is dangerous and Rem can't make it safe without stopping everything, or when it can't go on without you, such as a missing credential, a sign-in only you can do, missing hardware or no internet.";
export const AUTO_MODE_STAYS_YOURS = "Budget and run-limit increases, git push and folder-isolation policy, and Auto mode itself stay yours: those requests wait for you while work goes on.";
export const AUTO_DECISION_LABEL = { approve: "Approved", modify: "Approved with changes", decline: "Declined", halt: "Stopped for you", left_for_you: "Left for you" };
export function reviewingRequests(runtime = {}, now = Date.now()) {
  return Object.values(runtime.consentRequests || {}).filter(request => request.status === "pending" && request.waitingOn === "rem" && now - Date.parse(request.at) < CONSENT_TTL_MS);
}
export function autoDecisions(runtime = {}, limit = 5) {
  return [...(runtime.autoDecisions || [])].reverse().slice(0, limit);
}
export function decidedBy(request = {}) {
  return request.decidedBy || "you";
}

// Approve all applies requests in this order (gofer/ui/organization_consent.py): changes
// bound to the organization's revision first, oldest revision first; then other changes
// in the order Rem asked; starting the organization, then archiving, last.
const REVISION_BOUND = new Set(["configure", "restore", "resume_all"]);
const LAST = { control: 1, archive: 2 };
export function approvalOrder(requests = []) {
  const rank = request => {
    const expected = request.params?.expectedRevision;
    if (LAST[request.action]) return [2, LAST[request.action], 0];
    if (REVISION_BOUND.has(request.action)) return [0, 0, Number.isInteger(expected) ? expected : 0];
    return [1, 0, 0];
  };
  return [...requests].sort((a, b) => {
    const left = rank(a), right = rank(b);
    for (let i = 0; i < 3; i += 1) if (left[i] !== right[i]) return left[i] - right[i];
    return String(a.at).localeCompare(String(b.at));
  });
}

// Plain words for Approve all's per-request results.
export const APPROVE_RESULT = { approved: "Approved", failed: "Failed", skipped: "Left waiting", declined: "Declined" };
export function approveAllSummary(results = []) {
  const count = status => results.filter(item => item.status === status).length;
  const approved = count("approved"), failed = count("failed"), skipped = count("skipped");
  if (!failed && !skipped) return `Approved all ${plural(approved, "request")}.`;
  return [`Approved ${approved} of ${results.length}`, failed && `${failed} failed`, skipped && `${plural(skipped, "request")} left waiting`].filter(Boolean).join(", ") + ".";
}

// What a run used and produced, as short labels for its record.
export function runProvenance(attempt = {}) {
  return [
    attempt.provider && `${attempt.provider} · ${attempt.model === "cli-default" ? "provider default" : attempt.model}${attempt.effort ? ` · ${attempt.effort}` : ""}`,
    attempt.permissionMode,
    attempt.isolation === "worktree" ? "own worktree" : attempt.isolation === "folder" ? "project folder" : null,
    attempt.headCommit && `ended at ${attempt.headCommit.slice(0, 10)}${attempt.uncommittedChanges ? " + uncommitted changes" : ""}`,
    attempt.inputSha256 && `input ${attempt.inputSha256.slice(0, 12)}`,
  ].filter(Boolean);
}

export function attentionSummary(tasks = [], runtime = {}) {
  const open = tasks.filter(isOpenTask);
  return {
    blocked: open.filter(task => task.status === "blocked").length,
    review: open.filter(task => task.status === "in_review").length,
    approval: open.filter(needsApproval).length,
    requests: pendingRequests(runtime).length,
    running: open.filter(task => task.claim).length,
  };
}

// The current UTC month's company totals.
export function monthUsage(runtime = {}, now = new Date()) {
  const account = runtime.usage?.[now.toISOString().slice(0, 7)]?.company || {};
  return { runs: account.turns || 0, tokens: account.tokens ?? null, cost: account.turns ? usageCost(account) : formatUsd(0) };
}

// One readable line per stored provider event of a run.
export function transcriptEntries(events = []) {
  return events.map((event, index) => {
    const body = event.message?.body ?? event.text ?? event.delta ?? event.error ?? event.command ?? event.summary;
    const label = { thought: "Thinking", final: "Final answer", error: "Error", usage: "Usage", tool: "Tool", tool_call: "Tool", command: "Command" }[event.type]
      || (event.type ? event.type.replaceAll("_", " ").replace(/^./, c => c.toUpperCase()) : "Update");
    const detail = event.type === "usage" ? Object.entries(event.usage || {}).map(([key, value]) => `${key}: ${value}`).join(", ") : body;
    return { key: event.sequence ?? index, label, text: typeof detail === "string" ? detail : JSON.stringify(detail ?? event) };
  });
}

// Approval-gated tasks lead the queue; other groups follow the task lifecycle.
export function taskGroups(tasks) {
  const groups = new Map(GROUP_ORDER.map(id => [id, []]));
  for (const task of tasks) groups.get(needsApproval(task) && isOpenTask(task) ? "approval" : task.status)?.push(task);
  return [...groups].filter(([, items]) => items.length).map(([id, items]) => ({ id, label: STATUS_LABELS[id], tasks: items }));
}

const FIELD_LABELS = {
  name: "Name", slug: "Package slug", description: "Purpose", instructions: "Instructions", goals: "Goals",
  employees: "Employees", projects: "Initiatives", projectRoots: "Project folders", teams: "Teams", title: "Title", role: "Role", reportsTo: "Reports to",
  provider: "Provider", model: "Model", effort: "Effort", permissionMode: "Permission mode", resources: "Tools",
  skills: "Skills", paused: "Paused", heartbeatSeconds: "Heartbeat", memory: "Persistent memory",
  monthlyBudgetUsd: "Monthly budget", monthlyTurnLimit: "Monthly run limit", maxConcurrency: "Concurrent employees",
  maxTaskTurns: "Runs per task", turnTimeoutSeconds: "Run time limit", monthlyTokenLimit: "Monthly token limit",
  maxChildTasksPerTurn: "Tasks one run can create", maxDelegationDepth: "Delegation depth", workspaceIsolation: "Workspace isolation", blockGitPush: "Block git push", pullBacklog: "Pull from Backlog", remSecondBrain: "Second Brain",
  remReportTheme: "Report theme", packageFiles: "Package files", metadata: "Metadata", owner: "Owner",
  manager: "Manager", workspacePath: "Workspace folder", mcpServers: "MCP servers", shell: "Run commands", web: "Search the web",
};
const humanize = key => FIELD_LABELS[key] || key.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ").replace(/^./, c => c.toUpperCase());

// Turns "/employees/mira/heartbeatSeconds" into "Employees › Mira Santos › Heartbeat".
export function changeLabel(path, ...configs) {
  const segments = path.split("/").filter(Boolean);
  if (!segments.length) return "Whole configuration";
  if (segments[0] === "packageFiles") return ["Package files", segments.slice(1).join("/")].filter(Boolean).join(" › ");
  return segments.map((segment, index) => {
    const collection = segments[index - 1];
    if (["employees", "projects", "teams"].includes(collection)) {
      for (const config of configs) {
        const entry = config?.[collection]?.find?.(item => item?.id === segment);
        if (entry?.name) return entry.name;
      }
      return segment;
    }
    return humanize(segment);
  }).join(" › ");
}

function taskTitle(payload, tasks) {
  const title = payload.task?.title || tasks.find(task => task.id === payload.taskId)?.title;
  return title ? `“${title}”` : "a task";
}

// Groups provider stream updates by turn so a single turn reads as one entry.
export function activityEntries(events, tasks = []) {
  const entries = [];
  const turns = new Map();
  for (const event of events) {
    const payload = event.payload || {};
    const base = { key: `event-${event.sequence}`, at: event.at, actor: event.actor, kind: event.kind, events: [event], detail: "" };
    if (event.kind === "turn_event") {
      const existing = turns.get(payload.turnId);
      if (existing) { existing.events.push(event); existing.text = `Shared ${plural(existing.events.length, "update")} on ${taskTitle(payload, tasks)}`; continue; }
      const entry = { ...base, text: `Shared 1 update on ${taskTitle(payload, tasks)}` };
      turns.set(payload.turnId, entry); entries.push(entry); continue;
    }
    const entry = { ...base, text: event.kind.replaceAll("_", " ").replace(/^./, c => c.toUpperCase()) };
    if (event.kind === "configuration_saved") Object.assign(entry, { text: `Saved configuration revision ${payload.revision}`, detail: payload.reason || "" });
    if (event.kind === "configuration_restored") Object.assign(entry, { text: `Restored revision ${payload.restoredFrom} as revision ${payload.revision}`, detail: payload.reason || "" });
    if (event.kind === "organization_control") entry.text = payload.state === "running" ? "Started work" : "Paused work";
    if (event.kind === "runtime_recovered") Object.assign(entry, { text: "Paused after Raticode restarted", detail: payload.reason || "" });
    if (event.kind === "consent_requested") Object.assign(entry, { text: "Asked for your approval", detail: payload.summary || "" });
    if (event.kind === "consent_decided") Object.assign(entry, { text: `${payload.decision === "approve" ? "Approved" : "Declined"} Rem's request${payload.decidedBy && payload.decidedBy !== "you" ? ` (${payload.decidedBy})` : ""}`, detail: payload.error || payload.reason || "" });
    if (event.kind === "auto_decision" && payload.auto) Object.assign(entry, { text: `Rem in Auto mode: ${AUTO_DECISION_LABEL[payload.auto.decision] || payload.auto.decision} “${payload.auto.summary || "a request"}”`, detail: [payload.auto.reason, payload.auto.recommendation && `Recommended: ${payload.auto.recommendation}`, payload.auto.error].filter(Boolean).join(" ") });
    if (event.kind === "auto_mode_halted") Object.assign(entry, { text: "Auto mode stopped the organization", detail: [payload.halt?.reason, payload.halt?.userAction && `To continue: ${payload.halt.userAction}`].filter(Boolean).join(" ") });
    if (event.kind === "auto_mode_set") Object.assign(entry, { text: payload.autoMode ? "Turned Auto mode on" : "Turned Auto mode off", detail: "" });
    if (event.kind === "state_updated") Object.assign(entry, { text: "Updated work automatically", detail: (payload.taskChanges || []).map(change => `“${change.title}”: ${STATUS_LABELS[change.from] || "new"} → ${STATUS_LABELS[change.to] || change.to}`).join("; ") });
    if (event.kind === "backlog_pull") Object.assign(entry, { text: `Pulled ${plural(payload.pulled?.length || 0, "Backlog ticket")} into To do`, detail: (payload.pulled || []).map(item => `${item.key ? `${item.key} ` : ""}“${item.title}” for ${item.employee}`).join("; ") });
    if (event.kind === "consent_checked") Object.assign(entry, { text: "Closed expired requests from Rem", detail: plural(payload.expired?.length || 0, "request") });
    if (event.kind === "organization_archived") entry.text = "Archived the organization";
    if (event.kind === "organization_restored") entry.text = "Restored the organization from the archive";
    if (event.kind === "worktree_created") Object.assign(entry, { text: `Created a worktree for ${taskTitle(payload, tasks)}`, detail: payload.branch ? `Branch ${payload.branch}` : "" });
    if (event.kind === "worktree_released") Object.assign(entry, { text: `Finished the worktree for ${taskTitle(payload, tasks)}`, detail: payload.branch ? `Branch ${payload.branch}` : "" });
    if (event.kind === "dispatch") Object.assign(entry, { actor: payload.employeeId ? `employee:${payload.employeeId}` : event.actor, text: `Started a turn on ${taskTitle(payload, tasks)}` });
    if (event.kind === "turn_finished") {
      if (payload.discarded) Object.assign(entry, { text: "Discarded a turn", detail: payload.reason || "" });
      else if (payload.error) Object.assign(entry, { text: `Turn failed on ${taskTitle(payload, tasks)}`, detail: payload.error, tone: "error" });
      else Object.assign(entry, { text: `Finished a turn on ${taskTitle(payload, tasks)}`, detail: payload.status ? `Now ${STATUS_LABELS[payload.status]?.toLowerCase() || payload.status}` : "" });
    }
    if (event.kind === "task_create") entry.text = `Created ${taskTitle(payload, tasks)}`;
    if (event.kind === "task_update") Object.assign(entry, { text: `Updated ${taskTitle(payload, tasks)}`, detail: payload.task?.status ? `Status: ${STATUS_LABELS[payload.task.status]}` : "" });
    if (event.kind === "comment") Object.assign(entry, { text: `Commented on ${taskTitle(payload, tasks)}`, detail: payload.comment?.body || payload.task?.comments?.at(-1)?.body || "" });
    if (event.kind === "approve") entry.text = `Approved the plan for ${taskTitle(payload, tasks)}`;
    if (event.kind === "memory_updated") entry.text = "Updated working memory";
    entries.push(entry);
  }
  return entries.reverse();
}

export function newEmployee(defaults = {}) {
  return { id: crypto.randomUUID(), name: "", role: "", title: "", instructions: "", reportsTo: null,
    provider: defaults.provider || "codex", model: defaults.model || "cli-default", effort: defaults.effort || null,
    permissionMode: defaults.permissionMode || providerPermissionDefault(defaults.provider || "codex"), paused: false, heartbeatSeconds: 0,
    resources: structuredClone(defaults.resources || { shell: true, web: false, skills: [], mcpServers: [] }), skills: [], memory: "", metadata: {} };
}

export function relativeDay(at, now = new Date()) {
  const day = new Date(at);
  const start = date => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const difference = Math.round((start(now) - start(day)) / 86_400_000);
  return difference === 0 ? "Today" : difference === 1 ? "Yesterday" : "";
}

export function filterWork(tasks, filters = {}) {
  const query = (filters.query || "").trim().toLocaleLowerCase();
  return tasks.filter(task => (!query || `${task.title} ${task.description}`.toLocaleLowerCase().includes(query))
    && (!filters.assignee || task.assignee === filters.assignee)
    && (!filters.project || task.project === filters.project)
    && (!filters.attention || ["blocked", "in_review"].includes(task.status) || (isOpenTask(task) && needsApproval(task))));
}
export function appendActivity(current, incoming) {
  const events = new Map(current.map(event => [event.sequence, event]));
  incoming.forEach(event => events.set(event.sequence, event));
  return [...events.values()].sort((a, b) => a.sequence - b.sequence);
}
