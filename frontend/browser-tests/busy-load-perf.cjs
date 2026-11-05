/* global CustomEvent, PerformanceObserver, URL, document, localStorage, process, setTimeout, window */
// GOFER_LOAD_PERF=1 (see studio.browser.cjs) reproduces a busy desktop: 84 workflows,
// a long Rem history in the side pane and five tasks whose threads grow on every
// poll, as when several workers run at once. It measures the window's CPU per
// second, main-thread long tasks and requests per endpoint, then records a CPU
// profile and lists where the window's time went.
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const WORKFLOWS = Number(process.env.GOFER_LOAD_PERF_WORKFLOWS || 84);
const TASKS = Number(process.env.GOFER_LOAD_PERF_TASKS || 5);
const THREAD_MESSAGES = Number(process.env.GOFER_LOAD_PERF_THREAD_MESSAGES || 1000);
const SECONDS = Number(process.env.GOFER_LOAD_PERF_SECONDS || 30);

async function busyLoadFixture(frontendRoot) {
  const { remHomeScene } = await import(pathToFileURL(path.join(frontendRoot, "src/lib/remHomeSample.js")).href);
  const scene = remHomeScene("busy");
  const messages = Array.from({ length: 70 }, (_, copy) => scene.messages.map(message => ({ ...message, id: `${message.id}-${copy}` }))).flat();
  const template = scene.tasks.find(task => task.active_run);
  const tasks = Array.from({ length: TASKS }, (_, index) => ({
    ...template,
    id: `busy-task-${index}`,
    title: `Busy task ${index + 1}`,
    active_run: { ...template.active_run, id: `busy-run-${index}`, state: "executing", thread_id: `busy-thread-${index}`, verification_thread_id: null },
  }));
  const threads = Object.fromEntries(tasks.map((task, index) => [task.active_run.thread_id, { index, count: THREAD_MESSAGES }]));
  const workflows = Array.from({ length: WORKFLOWS }, (_, index) => ({
    agents: {}, edges: [], nodes: [], parameters: {}, tags: ["ready"], status: "Ready", readOnly: true,
    id: `busy-workflow-${index}`, name: `Busy workflow ${index}`, sourceFormat: "rattish",
    projectName: "workspace", projectRoot: "/workspace", workflowRoot: `/workspace/.raticode/busy-workflow-${index}`,
    sourcePath: `/workspace/.raticode/busy-workflow-${index}/workflow.rattish`,
    latestRun: { id: "run-1", status: "success" },
  }));
  return { conversation: scene.conversation, messages, tasks, threads, workflows, polls: 0, requests: {}, phoneBytes: 0, exchanges: 0 };
}

const body = (thread, index) => `Step ${index} for thread ${thread}. ` + "Checked the change, ran the focused tests and read the output. ".repeat(14);

function threadPayload(id, thread) {
  return {
    metadata: { id, title: `Busy task ${thread.index + 1}`, managedRunId: `busy-run-${thread.index}`, managedRole: "execute", updatedAt: new Date().toISOString() },
    messages: Array.from({ length: thread.count }, (_, index) => ({ id: `${id}-m${index}`, role: index % 9 ? "assistant" : "user", kind: index % 9 ? "thought" : undefined, body: body(id, index) })),
    running: true,
  };
}

// A paired phone that shares desktop threads, as on the user's desktop
// (GOFER_LOAD_PERF_PHONE=0 leaves it out). The fake backend answers like the real
// one: an exchange stores the thread and returns it.
const PHONE = process.env.GOFER_LOAD_PERF_PHONE !== "0";

function routeDevices(state, request, response, json) {
  if (request?.method !== "POST") {
    json(response, PHONE ? { workspace_peers: ["phone-1"], peers: [{ device_id: "phone-1", role: "controller", state: "active", name: "Phone" }] } : {});
    return;
  }
  let raw = "";
  request.on("data", chunk => { raw += chunk; });
  request.on("end", () => {
    state.phoneBytes += raw.length;
    const body = JSON.parse(raw || "{}");
    if (body.action === "workspace_exchange") {
      state.exchanges += 1;
      const id = body.metadata?.id;
      json(response, { thread_id: `wire-${id}`, sync_token: `s-${state.exchanges}`, revision: (body.revision || 0) + 1, metadata: body.metadata, messages: body.messages || [] });
      return;
    }
    if (body.action === "workspace_poll") { json(response, { threads: [] }); return; }
    if (body.action === "settings_exchange") { json(response, { changes: [] }); return; }
    json(response, {});
  });
}

// Returns true when it answered the request.
function routeBusyLoad(state, url, response, json, request) {
  const category = url.pathname.replace(/\/busy-(workflow|thread)-\d+/, "/:id");
  state.requests[category] = (state.requests[category] || 0) + 1;
  const known = request?.headers?.["x-gofer-known"] || url.searchParams.get("known");
  if (url.pathname === "/api/devices") { routeDevices(state, request, response, json); return true; }
  if (url.pathname === "/api/workflows") { json(response, { dataDir: "/workspace", promptAgentIds: [], workflows: state.workflows }); return true; }
  if (url.pathname === "/api/queue") { json(response, { runs: [] }); return true; }
  if (/^\/api\/workflows\/[^/]+\/logs$/.test(url.pathname)) {
    const runs = url.searchParams.get("status") === "running" ? [] : [
      { id: "run-1", status: "success", startedAt: "2026-11-04T10:00:00Z", finishedAt: "2026-11-04T10:01:00Z" },
      { id: "run-0", status: "error", startedAt: "2026-11-03T10:00:00Z", finishedAt: "2026-11-03T10:01:00Z" },
    ];
    json(response, { runs, pagination: { offset: 0, limit: 100, total: runs.length } });
    return true;
  }
  if (url.pathname === "/api/rem/conversation") {
    json(response, known === "c1" ? { unchanged: true, version: "c1", context_ready: true } : { version: "c1", conversation: state.conversation, messages: state.messages, context_ready: true });
    return true;
  }
  if (url.pathname === "/api/rem/tasks") {
    state.polls += 1;
    // Each running task reports progress on every poll, and its thread grows by one entry.
    for (const thread of Object.values(state.threads)) thread.count += 1;
    json(response, { version: `t${state.polls}`, tasks: state.tasks.map(task => ({ ...task, active_run: { ...task.active_run, phase_label: `Step ${state.polls}` } })) });
    return true;
  }
  if (url.pathname.startsWith("/api/rem/threads/")) {
    const id = decodeURIComponent(url.pathname.split("/").at(-1));
    const thread = state.threads[id];
    if (!thread) { json(response, { metadata: { id }, messages: [] }); return true; }
    const version = `${id}-${thread.count}`;
    json(response, known === version ? { unchanged: true, version } : { ...threadPayload(id, thread), version });
    return true;
  }
  if (url.pathname.startsWith("/api/rem/")) { json(response, {}); return true; }
  return false;
}

async function measureBusyLoad({ windowRef, evaluate, waitFor, state }) {
  await evaluate(() => {
    const settings = JSON.parse(localStorage.getItem("raticode.settings.v1") || "{}");
    localStorage.setItem("raticode.settings.v1", JSON.stringify({ ...settings, layout: { ...settings.layout, assistantPaneContent: "rem" } }));
  });
  await windowRef.reload();
  await waitFor(() => evaluate(() => document.querySelectorAll("[data-rem-pane] .rem-msg").length > 50), 50, "Rem history in the side pane");
  if (process.env.GOFER_LOAD_PERF_OPEN_THREAD === "1") {
    // Watching one running task's thread, as when following a worker.
    await new Promise(resolve => setTimeout(resolve, 3000));
    await evaluate(() => window.dispatchEvent(new CustomEvent("gofer:rem-open-thread", { detail: { threadId: "busy-thread-0" } })));
  }
  await new Promise(resolve => setTimeout(resolve, 6000));
  const pid = windowRef.webContents.getOSProcessId();
  // Percent of one core from /proc; Electron's own figure is spread over all cores.
  const fs = require("node:fs");
  const ticks = () => { const fields = fs.readFileSync(`/proc/${pid}/stat`, "utf8").split(") ")[1].split(" "); return Number(fields[11]) + Number(fields[12]); };
  let last = ticks();
  const rendererCpu = () => { const now = ticks(); const used = now - last; last = now; return used; };
  await evaluate(() => {
    window.__busyLongTasks = [];
    window.__busyObserver = new PerformanceObserver(list => window.__busyLongTasks.push(...list.getEntries().map(entry => entry.duration)));
    window.__busyObserver.observe({ type: "longtask" });
  });
  const before = { ...state.requests };
  const phoneBefore = { bytes: state.phoneBytes, exchanges: state.exchanges };
  const perSecond = [];
  for (let second = 0; second < SECONDS; second += 1) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    perSecond.push(Math.round(rendererCpu()));
  }
  const longTasks = await evaluate(() => { window.__busyObserver.disconnect(); return window.__busyLongTasks; });
  const requests = Object.fromEntries(Object.entries(state.requests).map(([key, count]) => [key, count - (before[key] || 0)]).filter(([, count]) => count > 0).sort((a, b) => b[1] - a[1]));
  const sorted = [...perSecond].sort((a, b) => a - b);
  const result = {
    seconds: SECONDS, workflows: WORKFLOWS, tasks: TASKS, threadMessages: THREAD_MESSAGES,
    rendererCpu: { average: Math.round(perSecond.reduce((sum, value) => sum + value, 0) / perSecond.length), p95: sorted[Math.floor(sorted.length * 0.95) - 1], max: sorted.at(-1), perSecond },
    longTasks: { count: longTasks.length, totalMs: Math.round(longTasks.reduce((sum, value) => sum + value, 0)), longestMs: Math.round(Math.max(0, ...longTasks)), over100ms: longTasks.filter(value => value > 100).length },
    requestsPerSecond: Math.round(Object.values(requests).reduce((sum, value) => sum + value, 0) / SECONDS * 10) / 10,
    requests,
    phone: PHONE ? { exchanges: state.exchanges - phoneBefore.exchanges, megabytesSent: Math.round((state.phoneBytes - phoneBefore.bytes) / 1e5) / 10 } : null,
  };
  if (process.env.GOFER_LOAD_PERF_PROFILE !== "0") result.profile = await profile(windowRef);
  return result;
}

// Self time per function over 15 s, from Chromium's sampling profiler.
async function profile(windowRef) {
  const debug = windowRef.webContents.debugger;
  debug.attach("1.3");
  await debug.sendCommand("Profiler.enable");
  await debug.sendCommand("Profiler.setSamplingInterval", { interval: 1000 });
  await debug.sendCommand("Profiler.start");
  await new Promise(resolve => setTimeout(resolve, 15000));
  const { profile: data } = await debug.sendCommand("Profiler.stop");
  debug.detach();
  const nodes = new Map(data.nodes.map(node => [node.id, node]));
  const self = new Map();
  const deltas = data.timeDeltas || [];
  data.samples.forEach((id, index) => {
    const frame = nodes.get(id).callFrame;
    const key = `${frame.functionName || "(anonymous)"} ${path.basename(frame.url || "")}:${frame.lineNumber + 1}`;
    self.set(key, (self.get(key) || 0) + (deltas[index] || 0) / 1000);
  });
  const total = [...self.values()].reduce((sum, value) => sum + value, 0);
  const idle = ["(idle) :0", "(program) :0", "(garbage collector) :0"].map(key => [key, Math.round(self.get(key) || 0)]);
  return {
    seconds: 15,
    busyMs: Math.round(total - (self.get("(idle) :0") || 0)),
    special: Object.fromEntries(idle),
    top: [...self.entries()].filter(([key]) => !key.startsWith("(idle)")).sort((a, b) => b[1] - a[1]).slice(0, 25).map(([key, ms]) => [key, Math.round(ms)]),
  };
}

// GOFER_LOAD_PERF_CROSS_ORIGIN=1 serves the API from its own port with a bearer
// token, as the desktop app does, so every API call is cross-origin and needs a
// CORS preflight unless the answer may be cached. GOFER_LOAD_PERF_MAX_AGE sets
// Access-Control-Max-Age on preflight answers.
async function startApiServer(state, fallback) {
  const http = require("node:http");
  const maxAge = process.env.GOFER_LOAD_PERF_MAX_AGE;
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    const origin = request.headers.origin;
    if (origin) response.setHeader("Access-Control-Allow-Origin", origin);
    response.setHeader("Vary", "Origin");
    response.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Gofer-Desktop-Grant-Secret, X-Gofer-UI-Token, X-Gofer-Webhook-Token, X-Gofer-Known");
    if (request.method === "OPTIONS") {
      state.requests.OPTIONS = (state.requests.OPTIONS || 0) + 1;
      response.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS");
      if (maxAge) response.setHeader("Access-Control-Max-Age", maxAge);
      response.writeHead(204).end();
      return;
    }
    const json = (res, payload) => { res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify(payload)); };
    if (!routeBusyLoad(state, url, response, json, request)) fallback(url, response, request);
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}/api` };
}

module.exports = { busyLoadFixture, routeBusyLoad, measureBusyLoad, startApiServer };
