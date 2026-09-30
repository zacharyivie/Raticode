import { providerPermissionDefault } from "./providerPermissions.js";
import { apiUrl } from "./api.js";

const READS = new Set(["availability", "list", "read", "history", "events", "export", "help", "tasks", "attempts", "routines", "outcomes", "rehearse", "diagnostics", "workflow_preview"]);
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
  blocked: "Blocked", done: "Done", cancelled: "Cancelled", approval: "Needs approval" };
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
  if (tasks.some(task => task.assignee === employee.id && task.claim)) return "working";
  return employee.paused ? "paused" : "available";
}

// Approval-gated tasks lead the queue; other groups follow the task lifecycle.
export function taskGroups(tasks) {
  const groups = new Map(GROUP_ORDER.map(id => [id, []]));
  for (const task of tasks) groups.get(needsApproval(task) && isOpenTask(task) ? "approval" : task.status)?.push(task);
  return [...groups].filter(([, items]) => items.length).map(([id, items]) => ({ id, label: STATUS_LABELS[id], tasks: items }));
}

const FIELD_LABELS = {
  name: "Name", slug: "Package slug", description: "Purpose", instructions: "Instructions", goals: "Goals",
  employees: "Employees", projects: "Initiatives", projectRoots: "Raticode projects", teams: "Teams", title: "Title", role: "Role", reportsTo: "Reports to",
  provider: "Provider", model: "Model", effort: "Effort", permissionMode: "Permission mode", resources: "Tools",
  skills: "Skills", paused: "Paused", heartbeatSeconds: "Heartbeat", memory: "Persistent memory",
  monthlyBudgetUsd: "Monthly budget", monthlyTurnLimit: "Monthly turn limit", maxConcurrency: "Concurrent employees",
  maxTaskTurns: "Turns per task", turnTimeoutSeconds: "Turn timeout", remSecondBrain: "Second Brain",
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
