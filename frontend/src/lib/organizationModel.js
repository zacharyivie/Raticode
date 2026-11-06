// Turns an organization read result into the linked model behind the Organizations views:
// projects, milestones, epics, tickets, runs, claims, knowledge and their links.

export const COLUMNS = ["backlog", "todo", "running", "review", "done"];
export const COLUMN_LABELS = { backlog: "Backlog", todo: "To do", running: "In progress", review: "Review", done: "Done" };
export const KINDS = { decision: "Decision", note: "Note", report: "Report", spec: "Spec", finding: "Finding" };
export const EVIDENCE_LABELS = { ok: "Verified", warn: "Source changed", bad: "Failed", none: "No evidence", queued: "Ready to check", running: "Checking", unverified: "Not rerun" };
const PROJECT_COLORS = ["#215879", "#17767b", "#6a4bb0", "#8f5d16", "#2f6fb3", "#a13c36", "#5f6f2a", "#4d5d6a"];
const WORKER_COLORS = ["#6a4bb0", "#215879", "#17767b", "#2f6fb3", "#a13c36", "#8f5d16", "#5f6f2a", "#7a4a7f", "#3b6e5a", "#4d5d6a"];

export function hashColor(id = "", palette = WORKER_COLORS) {
  let hash = 0;
  for (const char of String(id)) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return palette[hash % palette.length];
}

export function initialsOf(name = "") {
  return name.split(/\s+/).filter(Boolean).map(word => word[0]).join("").slice(0, 2).toUpperCase() || "?";
}

// Task status to board column. Blocked work stays in To do, flagged.
export function columnOf(task) {
  return { backlog: "backlog", todo: "todo", blocked: "todo", in_progress: "running", in_review: "review", done: "done" }[task.status] || null;
}

const shortTitle = text => (text.length > 26 ? `${text.slice(0, 24)}…` : text);

export function buildModel(organization) {
  const config = organization?.config || { employees: [], projects: [], milestones: [], epics: [] };
  const runtime = organization?.runtime || { tasks: [] };
  const workers = {};
  for (const employee of config.employees || []) {
    workers[employee.id] = { ...employee, color: hashColor(employee.id), responsibilities: employee.responsibilities || [], persona: employee.persona || "" };
  }
  const projects = {};
  (config.projects || []).forEach((project, index) => {
    projects[project.id] = { ...project, name: project.name || project.id, color: project.color || PROJECT_COLORS[index % PROJECT_COLORS.length], lead: project.owner || null, checks: project.checks || [] };
  });
  const orgMilestones = {};
  const milestones = {};
  for (const milestone of config.milestones || []) {
    if (milestone.projectId) milestones[milestone.id] = milestone;
    else orgMilestones[milestone.id] = milestone;
  }
  const epics = {};
  for (const epic of config.epics || []) epics[epic.id] = epic;

  const claimsByTask = {};
  for (const claim of Object.values(runtime.claims || {})) {
    if (claim.supersededAt) continue;
    (claimsByTask[claim.taskId] ||= []).push(claim);
  }
  for (const list of Object.values(claimsByTask)) list.sort((a, b) => a.at.localeCompare(b.at));

  const runs = {};
  const runsByTask = {};
  for (const attempt of Object.values(runtime.attempts || {})) {
    runs[attempt.id] = attempt;
    (runsByTask[attempt.taskId] ||= []).push(attempt.id);
  }
  for (const list of Object.values(runsByTask)) list.sort((a, b) => (runs[a].startedAt || "").localeCompare(runs[b].startedAt || ""));

  const links = Object.values(runtime.links || {}).filter(link => !link.removed);
  const knowledge = {};
  for (const entry of Object.values(runtime.knowledge || {})) if (!entry.archived) knowledge[entry.id] = entry;

  const tickets = {};
  const byKey = {};
  for (const task of runtime.tasks || []) {
    const column = columnOf(task);
    if (!column) continue;
    const reads = links.filter(link => link.kind === "read" && link.from === `ticket:${task.id}`).map(link => link.to.slice(3));
    const used = [...new Set([...(task.knowledge || []), ...reads])].filter(id => knowledge[id]);
    const ticket = {
      ...task,
      key: task.key || `#${task.number || "?"}`,
      column,
      used,
      observed: new Set(reads),
      claims: claimsByTask[task.id] || [],
      runs: runsByTask[task.id] || [],
      flag: task.status === "blocked" ? task.blockReason || "Blocked, with no reason recorded. Check its conversation, then reopen it." : task.approvalRequired && task.approvedRevision == null && task.status !== "done" ? "Needs your approval before it runs" : "",
    };
    tickets[task.id] = ticket;
    byKey[ticket.key] = task.id;
  }

  const model = { organization, config, runtime, workers, projects, orgMilestones, milestones, epics, tickets, byKey, runs, knowledge, links, claimsByTask };
  return model;
}

export const ticketsOfEpic = (model, epicId) => Object.values(model.tickets).filter(ticket => ticket.epicId === epicId);
export const epicsOfMilestone = (model, milestoneId) => Object.values(model.epics).filter(epic => epic.milestoneId === milestoneId);
export const milestonesOfProject = (model, projectId) => Object.values(model.milestones).filter(milestone => milestone.projectId === projectId);
export const milestonesServing = (model, orgMilestoneId) => Object.values(model.milestones).filter(milestone => milestone.parentId === orgMilestoneId);

const average = values => (values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0);
// Progress counts only accepted (done) tickets.
export function epicProgress(model, epicId) {
  const tickets = ticketsOfEpic(model, epicId);
  return tickets.length ? tickets.filter(ticket => ticket.column === "done").length / tickets.length : 0;
}
export const milestoneProgress = (model, milestoneId) => average(epicsOfMilestone(model, milestoneId).map(epic => epicProgress(model, epic.id)));
export const orgMilestoneProgress = (model, id) => average(milestonesServing(model, id).map(milestone => milestoneProgress(model, milestone.id)));
export function projectProgress(model, projectId) {
  const milestones = milestonesOfProject(model, projectId);
  if (milestones.length) return average(milestones.map(milestone => milestoneProgress(model, milestone.id)));
  const tickets = Object.values(model.tickets).filter(ticket => ticket.project === projectId);
  return tickets.length ? tickets.filter(ticket => ticket.column === "done").length / tickets.length : 0;
}
export const openTickets = (model, projectId) => Object.values(model.tickets).filter(ticket => ticket.column !== "done" && (!projectId || ticket.project === projectId));

export function epicState(model, epicId) {
  const tickets = ticketsOfEpic(model, epicId);
  const progress = epicProgress(model, epicId);
  if (model.epics[epicId]?.status === "done" || (tickets.length && progress === 1)) return "done";
  return tickets.some(ticket => !["backlog", "todo"].includes(ticket.column)) || progress > 0 ? "active" : "planned";
}

export function evidenceState(claim) {
  return claim?.evidence?.state || "none";
}

// Counts for the evidence badges on a card: verified, changed, failed and unproven.
export function claimSummary(claims = []) {
  const summary = { ok: 0, warn: 0, bad: 0, none: 0, pending: 0 };
  for (const claim of claims) {
    const state = evidenceState(claim);
    if (state === "ok") summary.ok += 1;
    else if (state === "warn") summary.warn += 1;
    else if (state === "bad") summary.bad += 1;
    else if (["queued", "running"].includes(state)) summary.pending += 1;
    else summary.none += 1;
  }
  return summary;
}

export function reviewCaution(claims = []) {
  const summary = claimSummary(claims);
  const parts = [summary.bad && `${summary.bad} failed`, summary.warn && `${summary.warn} source changed`, summary.none && `${summary.none} without verified evidence`, summary.pending && `${summary.pending} not checked yet`].filter(Boolean);
  return { summary, text: parts.join(", "), risky: Boolean(summary.bad || summary.warn || summary.none || summary.pending) };
}

export function workerStatus(model, workerId) {
  const worker = model.workers[workerId];
  if (!worker) return ["idle", "Idle"];
  if (worker.paused) return ["paused", "Held"];
  const mine = Object.values(model.tickets).filter(ticket => ticket.assignee === workerId);
  if (mine.some(ticket => ticket.column === "running")) return ["working", "Working"];
  if (mine.some(ticket => ticket.column === "review")) return ["needs", "To review"];
  return ["idle", "Idle"];
}

// What waits on the user: Rem's consent requests, work to review and flagged work.
export function needsYou(model, now = Date.now()) {
  const items = [];
  for (const request of Object.values(model.runtime.consentRequests || {})) {
    if (request.status === "pending" && now - Date.parse(request.at) < 24 * 3600 * 1000) items.push({ type: "request", request });
  }
  // Reviews come before flagged work: they are what moves the plan forward.
  const tickets = Object.values(model.tickets);
  for (const ticket of tickets) if (ticket.column === "review") items.push({ type: "review", ref: `ticket:${ticket.id}` });
  for (const ticket of tickets) if (ticket.column !== "review" && ticket.flag && ticket.column !== "done") items.push({ type: "flag", ref: `ticket:${ticket.id}` });
  // Check-ins that failed three times in a row stop until you resume them.
  for (const [workerId, beat] of Object.entries(model.organization?.diagnostics?.heartbeats || {})) {
    if (beat.stalled && model.workers[workerId]) items.push({ type: "heartbeat", worker: workerId, stall: beat.stalled });
  }
  return items;
}

// Entity lookup for chips, the palette and the side sheet. References read "type:id".
export function entity(model, ref) {
  const index = ref.indexOf(":");
  const type = ref.slice(0, index), id = ref.slice(index + 1);
  switch (type) {
    case "ticket": { const t = model.tickets[id]; return t && { type, id, key: t.key, label: t.title, sub: t.key, color: "var(--k-ticket)" }; }
    case "epic": { const e = model.epics[id]; return e && { type, id, label: e.title, sub: "Epic", color: model.projects[e.projectId]?.color || "var(--accent)" }; }
    case "ms": { const m = model.milestones[id]; return m && { type, id, label: m.title, sub: "Milestone", color: model.projects[m.projectId]?.color || "var(--accent)" }; }
    case "orgms": { const m = model.orgMilestones[id]; return m && { type, id, label: m.title, sub: "Organization milestone", color: "var(--accent)" }; }
    case "project": { const p = model.projects[id]; return p && { type, id, label: p.name, sub: "Project", color: p.color }; }
    case "worker": { const w = model.workers[id]; return w && { type, id, label: w.name, sub: w.title || w.role || "Worker", color: w.color }; }
    case "kb": { const k = model.knowledge[id]; return k && { type, id, label: k.title, sub: KINDS[k.kind] || "Note", color: `var(--k-${k.kind})` }; }
    case "run": { const r = model.runs[id]; const t = r && model.tickets[r.taskId]; return r && { type, id, label: `Run on ${t?.key || "a ticket"}`, sub: "Run", color: "var(--run)" }; }
    case "claim": { const c = Object.values(model.claimsByTask).flat().find(item => item.id === id) || model.runtime.claims?.[id]; return c && { type, id, label: { check: "Check", quote: "Quote", commit: "Commit", file: "File" }[c.evidence?.kind] || "Claim", sub: "Evidence", color: "var(--done)" }; }
    case "new": return { type, id, label: { ticket: "New ticket", epic: "New epic", ms: "New milestone", orgms: "New organization milestone", project: "New project", kb: "New note", worker: "New worker", hours: "Working hours" }[id.split(":")[0]] || "New", sub: "New" };
    default: return null;
  }
}

export function searchEntities(model, query, limit = 12) {
  const refs = [
    ...Object.keys(model.tickets).map(id => `ticket:${id}`), ...Object.keys(model.epics).map(id => `epic:${id}`),
    ...Object.keys(model.milestones).map(id => `ms:${id}`), ...Object.keys(model.orgMilestones).map(id => `orgms:${id}`),
    ...Object.keys(model.projects).map(id => `project:${id}`), ...Object.keys(model.workers).map(id => `worker:${id}`),
    ...Object.keys(model.knowledge).map(id => `kb:${id}`),
  ];
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return refs.map(ref => entity(model, ref)).filter(Boolean)
    .filter(item => words.every(word => `${item.label} ${item.key || ""} ${item.sub}`.toLocaleLowerCase().includes(word)))
    .slice(0, limit);
}

const STOP = new Set("the a an and or of to in on for is are be with by it as at this that from not no only never every each into we you our your".split(" "));
const words = text => new Set(text.toLocaleLowerCase().replace(/[^a-z0-9\s]/g, " ").split(/\s+/).filter(word => word.length > 2 && !STOP.has(word)));

// Similar notes that aren't linked yet. Shown as suggestions; nothing is saved.
export function suggestedLinks(knowledge, existing = [], threshold = 0.2) {
  const entries = Object.values(knowledge);
  const linked = new Set(existing.map(([a, b]) => [a, b].sort().join("|")));
  const bags = new Map(entries.map(entry => [entry.id, words(`${entry.title} ${entry.summary}`)]));
  const result = [];
  for (let i = 0; i < entries.length; i += 1) {
    let best = null;
    for (let j = 0; j < entries.length; j += 1) {
      if (i === j) continue;
      const a = bags.get(entries[i].id), b = bags.get(entries[j].id);
      const shared = [...a].filter(word => b.has(word)).length;
      const score = shared / Math.max(1, Math.min(a.size, b.size));
      if (shared >= 2 && score >= threshold && (!best || score > best.score)) best = { id: entries[j].id, score };
    }
    if (!best) continue;
    const key = [entries[i].id, best.id].sort().join("|");
    if (!linked.has(key)) { linked.add(key); result.push([entries[i].id, best.id, "suggested"]); }
  }
  return result;
}

// All links between knowledge entries: written in the note, asserted by an agent or user.
export function knowledgeEdges(model, { suggested = true } = {}) {
  const edges = [];
  for (const entry of Object.values(model.knowledge)) {
    for (const target of entry.links || []) if (model.knowledge[target]) edges.push([entry.id, target, "written"]);
  }
  for (const link of model.links) {
    if (link.kind !== "asserted") continue;
    const a = link.from.slice(3), b = link.to.slice(3);
    if (model.knowledge[a] && model.knowledge[b]) edges.push([a, b, "asserted", link]);
  }
  return suggested ? [...edges, ...suggestedLinks(model.knowledge, edges)] : edges;
}

export const usedBy = (model, knowledgeId) => Object.values(model.tickets).filter(ticket => ticket.used.includes(knowledgeId));

// A run's stored events as a short list of steps: what it read, edited, ran and claimed.
// Events are the provider trace entries Raticode keeps for each run.
export function runSteps(events = []) {
  const steps = [];
  const seen = new Set();
  const add = (kind, single, many) => {
    const last = steps.at(-1);
    if (last && last.kind === kind && many) { last.count += 1; last.text = many(last.count); return; }
    steps.push({ kind, count: 1, text: single });
  };
  for (const raw of events) {
    const event = raw.event || raw;
    if (event.type === "final") { add("chat", "Finished with a summary"); continue; }
    if (event.type === "error") { add("error", `Stopped: ${String(event.error || "error").slice(0, 140)}`); continue; }
    const trace = event.trace;
    if (!trace || trace.kind !== "tool" || trace.phase === "result" || trace.title === "Tool result") continue;
    if (trace.id) { if (seen.has(trace.id)) continue; seen.add(trace.id); }
    const title = String(trace.title || "").toLowerCase();
    const input = trace.input && typeof trace.input === "object" ? trace.input : {};
    const action = input.action || "";
    const command = String(typeof trace.input === "string" ? trace.input : input.command || trace.detail || "");
    if (title.includes("organization") && action) {
      if (action === "knowledge_read" || action === "knowledge_search") add("read", "Consulted organization knowledge", n => `Consulted organization knowledge ${n} times`);
      else if (action === "claim_add") add("claim", "Posted a claim", n => `Posted ${n} claims`);
      else if (action === "knowledge_save") add("save", "Saved a note", n => `Saved ${n} notes`);
      else if (action === "task_create") add("plan", "Created a ticket", n => `Created ${n} tickets`);
      else add("org", `Used the organization: ${action.replaceAll("_", " ")}`);
    } else if (trace.shell || /^(bash|shell|sh|zsh|powershell)$/.test(title)) {
      if (/\b(test|pytest|jest|vitest|gradlew|npm (run )?test|go test|cargo test|mypy|ruff|eslint)\b/.test(command)) add("test", `Ran ${command.slice(0, 90)}`);
      else if (/\bgit commit\b/.test(command)) add("commit", "Committed changes");
      else add("command", "Ran a command", n => `Ran ${n} commands`);
    } else if (/^(edit|write|multiedit|notebookedit|apply_patch)$/.test(title)) add("edit", "Edited a file", n => `Edited files ${n} times`);
    else if (/^(read|grep|glob|ls|view|search)$/.test(title)) add("look", "Looked through the project", n => `Looked through the project (${n} reads)`);
    else if (/web|fetch/.test(title)) add("web", "Searched the web", n => `Searched the web ${n} times`);
    else add("tool", `Used ${trace.title}`, n => `Used tools ${n} times`);
  }
  return steps;
}

// When a ticket starts, in a sentence: Raticode's own reason for the ticket, put plainly.
// Tones: "run" will start, "wait" waits for something that resolves itself, "you" needs you.
export function startHint(model, ticket) {
  const reason = model.organization?.diagnostics?.taskReasons?.[ticket.id] || "";
  const running = model.runtime.state === "running" && !model.runtime.archived;
  const worker = model.workers[ticket.assignee];
  const name = worker?.name || "Its worker";
  if (ticket.column === "running") return { tone: "run", text: `${name} is working on it now.` };
  if (ticket.column === "review") return { tone: "you", text: "Waiting for your review." };
  if (ticket.column === "done") return null;
  if (ticket.column === "backlog") return { tone: "wait", text: "In Backlog, so it won't run. Move it to To do when it should start." };
  if (reason.startsWith("Off hours until ")) return { tone: "wait", text: `Outside working hours. It starts when they resume, ${zoneTime(reason.slice(16), model.organization?.diagnostics?.hours?.timezone)}.` };
  if (reason.startsWith("Off hours")) return { tone: "you", text: "Outside working hours, and no hours are coming up. Change the working hours to let it run." };
  if (reason.startsWith("Next up: ")) return { tone: "wait", text: `Next up. ${reason.slice(9)}; this one starts right after.` };
  if (reason === "Ready") return running ? { tone: "run", text: `Starts next: ${name} picks it up within a few seconds.` } : { tone: "you", text: "Ready. It starts when you start the organization." };
  if (reason === "Organization is paused") return { tone: "you", text: `It starts when you start the organization${worker ? `; ${name} will pick it up` : ""}.` };
  if (reason === "Organization is archived") return { tone: "you", text: "The organization is archived, so nothing runs." };
  if (reason === "Assign an employee") return { tone: "you", text: "No worker is assigned, so it won't run. Edit the ticket to assign one." };
  if (reason === "Employee is held") return { tone: "you", text: `${name} is held. Release the hold in their details to let it run.` };
  if (reason === "Waiting for plan approval") return { tone: "you", text: "Waiting for you to approve its plan." };
  if (reason.startsWith("Scheduled for ")) return { tone: "wait", text: `Scheduled to start ${formatDate(reason.slice(14))}.` };
  if (reason.startsWith("Queued: ")) return { tone: "wait", text: `${reason.slice(8)}; this one starts after.` };
  if (reason.startsWith("Waiting for monthly run capacity") || reason.startsWith("Monthly ")) return { tone: "you", text: `${reason}. Raise the limit or wait for next month.` };
  const yours = /cancelled|no longer exists|Reassign|Move the ticket|Reopen|reopen|Raise|unavailable|outside|Assign/.test(reason);
  return reason ? { tone: yours ? "you" : "wait", text: reason.endsWith(".") ? reason : `${reason}.` } : null;
}

// Where a dragged card may go. Done always goes through review. Moving a card to In progress
// puts it first in its worker's queue; the run itself starts when the worker is free.
export function moveRule(ticket, column) {
  if (ticket.column === column) return { ok: false };
  if (column === "running") {
    if (ticket.status === "blocked") return { ok: false, reason: "This ticket is blocked. Open it and reopen it first." };
    if (["backlog", "todo"].includes(ticket.column)) return { ok: true, pull: true };
    return { ok: false, reason: ticket.column === "review" ? "Request changes to send it back for another run." : "Reopen it first." };
  }
  if (ticket.column === "running") return { ok: false, reason: "This ticket is running. Pause the organization or wait for the run to finish." };
  if (column === "review") return { ok: false, reason: "Work moves to Review when a run finishes." };
  if (column === "done") return ticket.column === "review" ? { ok: true, review: true } : { ok: false, reason: "Only reviewed work moves to Done. Open the ticket to review it first." };
  if (ticket.status === "done" || ticket.status === "blocked") return { ok: true, reopen: true, status: column };
  return { ok: true, status: column };
}

// Topic groups placed around the middle, largest first, so their outlines don't overlap.
export function topicSpots(topics) {
  const ring = [[0.5, 0.45], [0.16, 0.24], [0.84, 0.24], [0.16, 0.76], [0.84, 0.76], [0.5, 0.86], [0.5, 0.1], [0.33, 0.5], [0.67, 0.5]];
  const spots = {};
  topics.forEach((topic, index) => {
    const [x, y] = ring[index % ring.length];
    const lap = Math.floor(index / ring.length);
    spots[topic] = [Math.min(0.92, x + lap * 0.04), Math.min(0.9, y + lap * 0.04)];
  });
  return spots;
}

export function seeded(seed) {
  let state = seed;
  return () => (state = (state * 16807) % 2147483647) / 2147483647;
}

// Small force layout. Positions persist in `positions` so dragging and re-renders keep them.
export function layoutGraph(nodes, edges, width, height, positions, topicOf) {
  const random = seeded(7);
  const topics = [...new Set(nodes.map(topicOf))].sort((a, b) => nodes.filter(n => topicOf(n) === b).length - nodes.filter(n => topicOf(n) === a).length);
  const spots = topicSpots(topics);
  const centers = Object.fromEntries(topics.map(topic => [topic, [width * spots[topic][0], height * spots[topic][1]]]));
  for (const node of nodes) {
    if (!positions[node.id]) { const [cx, cy] = centers[topicOf(node)]; positions[node.id] = { x: cx + (random() - 0.5) * 80, y: cy + (random() - 0.5) * 80, fixed: false }; }
  }
  for (let iteration = 0; iteration < 220; iteration += 1) {
    const force = Object.fromEntries(nodes.map(node => [node.id, [0, 0]]));
    for (let i = 0; i < nodes.length; i += 1) for (let j = i + 1; j < nodes.length; j += 1) {
      const a = positions[nodes[i].id], b = positions[nodes[j].id];
      const dx = a.x - b.x, dy = a.y - b.y, d2 = Math.max(dx * dx + dy * dy, 25), d = Math.sqrt(d2), push = 3400 / d2;
      force[nodes[i].id][0] += (dx / d) * push; force[nodes[i].id][1] += (dy / d) * push;
      force[nodes[j].id][0] -= (dx / d) * push; force[nodes[j].id][1] -= (dy / d) * push;
    }
    for (const edge of edges) {
      const a = positions[edge.a], b = positions[edge.b];
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.max(Math.hypot(dx, dy), 1), pull = (d - (edge.k === "read" ? 70 : 95)) * 0.025;
      force[edge.a][0] += (dx / d) * pull; force[edge.a][1] += (dy / d) * pull;
      force[edge.b][0] -= (dx / d) * pull; force[edge.b][1] -= (dy / d) * pull;
    }
    for (const node of nodes) { const [cx, cy] = centers[topicOf(node)], p = positions[node.id]; force[node.id][0] += (cx - p.x) * 0.03; force[node.id][1] += (cy - p.y) * 0.03; }
    const cool = 1 - iteration / 240;
    for (const node of nodes) {
      const p = positions[node.id];
      if (p.fixed) continue;
      p.x = Math.max(40, Math.min(width - 40, p.x + Math.max(-8, Math.min(8, force[node.id][0])) * cool));
      p.y = Math.max(30, Math.min(height - 60, p.y + Math.max(-8, Math.min(8, force[node.id][1])) * cool));
    }
  }
  return { topics };
}

export { shortTitle };

export function formatDate(value) {
  if (!value) return "";
  const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T12:00:00` : value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function timeAgo(value, now = Date.now()) {
  const seconds = Math.max(0, (now - Date.parse(value)) / 1000);
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return "now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h`;
  return `${Math.floor(seconds / 86400)} d`;
}

// "just now", "5 min ago", "3 d ago".
export function ago(value, now = Date.now()) {
  const span = timeAgo(value, now);
  return !span ? "" : span === "now" ? "just now" : `${span} ago`;
}

// Recent activity as one line each, with a reference to open.
export function activityFeed(model, events = [], limit = 8) {
  const items = [];
  const seenRuns = new Set();
  for (const event of [...events].reverse()) {
    const payload = event.payload || {};
    const actor = event.actor || "";
    const who = actor.startsWith("employee:") ? actor.slice(9) : actor === "user" ? "you" : actor.startsWith("Rem") ? "Rem" : actor === "system" ? "Raticode" : actor;
    const task = payload.taskId && model.tickets[payload.taskId];
    let line = null;
    if (event.kind === "turn_finished" && task && !seenRuns.has(payload.turnId)) {
      seenRuns.add(payload.turnId);
      line = { who, text: payload.error ? "run failed on" : "finished a run on", ref: `ticket:${task.id}`, bad: Boolean(payload.error) };
    } else if (event.kind === "check_finished" && task) line = { who: "Raticode", text: payload.state === "ok" ? "verified a check on" : "check failed on", ref: `ticket:${task.id}`, bad: payload.state !== "ok" };
    else if (event.kind === "knowledge_save" && model.knowledge[payload.entryId]) line = { who, text: "saved", ref: `kb:${payload.entryId}` };
    else if (event.kind === "knowledge_import" && model.knowledge[payload.entryId]) line = { who, text: "added from the Second Brain", ref: `kb:${payload.entryId}` };
    else if (event.kind === "review" && task) line = { who, text: payload.decision === "accept" ? "accepted" : "sent back", ref: `ticket:${task.id}` };
    else if (event.kind === "task_create" && task) line = { who, text: "created", ref: `ticket:${task.id}` };
    else if (event.kind === "claim_add" && task) continue;
    else if (event.kind === "configuration_saved") line = { who, text: payload.reason ? `saved: ${payload.reason}` : "saved the configuration" };
    else if (event.kind === "configuration_restored") line = { who, text: `restored revision ${payload.restoredFrom}` };
    else if (event.kind === "organization_control") line = { who, text: payload.state === "running" ? "started the organization" : "paused the organization" };
    else if (event.kind === "consent_requested") line = { who: "Rem", text: `asked to ${payload.summary?.replace(/^Change settings to /, "") || "change something"}` };
    else if (event.kind === "consent_decided") line = { who, text: payload.decision === "approve" ? "approved Rem's request" : "declined Rem's request", bad: Boolean(payload.error) };
    else if (event.kind === "task_update" && task && payload.changes?.status) line = { who, text: `moved to ${COLUMN_LABELS[columnOf({ status: payload.changes.status })] || payload.changes.status}`, ref: `ticket:${task.id}` };
    else if (event.kind === "approve" && task) line = { who, text: "approved the plan for", ref: `ticket:${task.id}` };
    else if (event.kind === "reopen" && task) line = { who, text: "reopened", ref: `ticket:${task.id}` };
    else if (event.kind === "state_updated") {
      const change = (payload.taskChanges || []).find(item => model.tickets[item.taskId]);
      if (change) line = { who, text: `moved to ${COLUMN_LABELS[columnOf({ status: change.to })] || change.to}`, ref: `ticket:${change.taskId}` };
    }
    if (line) items.push({ ...line, at: event.at, key: event.sequence });
    if (items.length >= limit) break;
  }
  return items;
}

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function describeHours(hours) {
  if (!hours || hours.mode !== "scheduled") return "24/7";
  const parts = (hours.windows || []).map(window => {
    const days = window.days.join(",");
    const label = days === "0,1,2,3,4" ? "Weekdays" : days === "0,1,2,3,4,5,6" ? "Every day" : days === "5,6" ? "Weekends" : window.days.map(day => DAY_NAMES[day]).join(", ");
    const overnight = window.end <= window.start ? " (overnight)" : "";
    return `${label} ${window.start}–${window.end}${overnight}`;
  });
  return `${parts.join("; ")}, ${hours.timezone}`;
}

// A moment in the organization's time zone, such as "Mon 09:00".
export function zoneTime(value, timezone) {
  if (!value) return "";
  try {
    return new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: timezone }).format(new Date(value));
  } catch { return formatDate(value); }
}

// The header's working-hours state: null when the organization works around the clock.
export function hoursStatus(model) {
  const state = model.organization?.diagnostics?.hours;
  if (!state || state.mode !== "scheduled") return null;
  if (state.open) return { open: true, text: state.closesAt ? `Working hours until ${zoneTime(state.closesAt, state.timezone)}` : "Working hours" };
  return { open: false, text: state.opensAt ? `Off hours until ${zoneTime(state.opensAt, state.timezone)}` : "Off hours" };
}
