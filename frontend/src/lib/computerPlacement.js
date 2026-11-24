// Settings > Computers: the user's order of computers and where Rem and each
// organization run their workers. The backend keeps both
// (gofer.core.placement_settings); these helpers only shape what the section sends
// and shows.

import { usageProviderName } from "./usage.js";

export const STRATEGY_OPTIONS = Object.freeze([
  ["overflow", "Fill one computer, then the next"],
  ["balanced", "Spread evenly"],
  ["manual", "Only computers I pick"],
]);

// Computers from GET /api/computers in the user's order from GET
// /api/computers/placement. A computer the order doesn't name comes last.
export function orderedComputers(computers = [], order = []) {
  const place = new Map(order.map((id, index) => [id, index]));
  return computers
    .map((computer, index) => ({ computer, index }))
    .sort((a, b) => (place.get(a.computer.id) ?? order.length + a.index) - (place.get(b.computer.id) ?? order.length + b.index))
    .map(({ computer }) => computer);
}

// The order after moving one computer up (step -1) or down (step 1), or null at an end.
export function moveInOrder(ids, id, step) {
  const from = ids.indexOf(id);
  const to = from + step;
  if (from < 0 || to < 0 || to >= ids.length) return null;
  const next = [...ids];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

// One consumer's placement as the section edits it: its strategy, assigned computers
// and the assigned computers it shares.
export function placementDraft(consumer) {
  return {
    strategy: consumer?.strategy || "overflow",
    computers: [...(consumer?.computers ?? [])],
    shared: [...(consumer?.shared ?? [])],
  };
}

// Picks or drops one computer. A dropped computer stops being shared too.
export function withComputer(draft, id, on) {
  const computers = on ? [...new Set([...draft.computers, id])] : draft.computers.filter((item) => item !== id);
  return { ...draft, computers, shared: draft.shared.filter((item) => computers.includes(item)) };
}

export function withShared(draft, id, on) {
  if (on && !draft.computers.includes(id)) return draft;
  return { ...draft, shared: on ? [...new Set([...draft.shared, id])] : draft.shared.filter((item) => item !== id) };
}

// The POST /api/computers/placement body for a draft, or null while a manual
// placement has no computer yet (the backend would refuse it).
export function placementBody(consumerId, draft) {
  if (draft.strategy !== "manual") return { consumer: consumerId, strategy: draft.strategy };
  if (!draft.computers.length) return null;
  const body = { consumer: consumerId, strategy: "manual", computers: draft.computers };
  return draft.shared.length ? { ...body, shared: draft.shared } : body;
}

export function stateText(computer) {
  if (computer.state !== "online") return "Offline";
  return computer.rejoining ? "Back online" : "Online";
}

// Each kind of worker in words, one and many, in the order equal counts are listed
// (gofer.core.worker_claims.KIND_WORDS).
export const KIND_WORDS = Object.freeze({
  rem: ["Rem task", "Rem tasks"],
  phone: ["phone turn", "phone turns"],
  organizations: ["organization worker", "organization workers"],
  swarms: ["swarm turn", "swarm turns"],
});

// A computer's running workers by kind in words, most first, such as
// "2 organization workers, 1 Rem task, 1 swarm turn". Empty when it has no counts (an
// older paired desktop shares only its total) or they don't add up to its total.
export function runningBreakdown(computer) {
  const kinds = computer.state === "online" ? computer.running_by_kind : null;
  if (!kinds || typeof kinds !== "object") return "";
  const order = Object.keys(KIND_WORDS);
  const counts = order
    .map((kind) => [kind, kinds[kind]])
    .filter(([, count]) => Number.isInteger(count) && count > 0);
  const total = Object.values(kinds).reduce((sum, count) => sum + (Number.isInteger(count) ? count : 0), 0);
  if (total !== (computer.running_threads ?? 0)) return "";
  return counts
    .sort((a, b) => b[1] - a[1] || order.indexOf(a[0]) - order.indexOf(b[0]))
    .map(([kind, count]) => `${count} ${KIND_WORDS[kind][count === 1 ? 0 : 1]}`)
    .join(", ");
}

export function runningText(computer) {
  const running = computer.state === "online" ? computer.running_threads ?? 0 : 0;
  const words = runningBreakdown(computer);
  const load = computer.max_threads == null ? `${running} running, limit not reported` : `${running} of ${computer.max_threads} running`;
  return words ? `${load}: ${words}` : load;
}

// What each of this computer's workers is, oldest first: { kind, label } with the kind
// in words. Paired desktops share counts only, so theirs is always empty.
export function runningWork(computer) {
  if (!computer.this_computer || computer.state !== "online" || !Array.isArray(computer.running_work)) return [];
  return computer.running_work
    .filter((work) => work && typeof work.label === "string")
    .map((work) => ({ kind: KIND_WORDS[work.kind]?.[0] ?? "worker", label: work.label }));
}

// How many new workers fit now, and why it is lower than the limit when it is.
export function roomText(computer) {
  if (computer.state !== "online") return "No room while offline";
  if (computer.capacity == null) return "Room unknown";
  if (computer.memory_hold) return `No room: waiting for free memory (${computer.memory_hold})`;
  if (computer.rejoining) return `Room for ${computer.capacity}, one at a time after coming back`;
  const lowered = computer.effective_threads != null && computer.max_threads != null && computer.effective_threads < computer.max_threads;
  return lowered ? `Room for ${computer.capacity}, pressure lowers its limit to ${computer.effective_threads}` : `Room for ${computer.capacity}`;
}

export function pressureText(computer) {
  if (computer.state !== "online") return "";
  const parts = [["CPU", computer.cpu_pressure], ["memory", computer.memory_pressure]]
    .filter(([, value]) => typeof value === "number")
    .map(([label, value]) => `${label} ${Math.round(value)}%`);
  return parts.length ? `Pressure: ${parts.join(", ")}` : "";
}

// A short label for a computer that is assigned but no longer paired.
export function missingLabel(id) {
  return `Unpaired computer ${String(id).slice(0, 8)}`;
}

// What a paired desktop says about running this computer's work there: the reason
// none can run there (its own words from GET /api/computers, naming the computer), or
// the projects and providers it shares. Offline and an unknown limit already show in
// the row, so they add nothing here. Null for this computer.
export function workText(computer) {
  if (computer.this_computer || computer.state !== "online" || computer.max_threads == null) return null;
  if (computer.work_reason) return { tone: "warning", text: computer.work_reason };
  const catalog = computer.catalog ?? {};
  const projects = (catalog.projects ?? []).map((project) => project.name);
  const providers = (catalog.providers ?? []).map(usageProviderName);
  const shared = [
    `Shares ${projects.length === 1 ? "1 project" : `${projects.length} projects`} with this computer: ${projects.join(", ")}`,
    providers.length ? `allows ${providers.join(", ")}` : "allows no provider",
  ].join("; ");
  return { tone: "muted", text: `${shared}. Each start also needs the same repository open there. Work doesn't start on other computers yet.` };
}
