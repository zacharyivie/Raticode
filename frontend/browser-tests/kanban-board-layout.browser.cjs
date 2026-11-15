/* global __dirname, console, process, setTimeout, clearTimeout */
// The Global board's and an organization's board columns in a real Chromium layout. Every card stays inside its own
// column, long text wraps, and the board scrolls sideways when the columns don't fit. A
// card's bottom row (person, token usage, Auto-approved) used to set the width of its
// whole column's cards, so Done's cards painted over Blocked's.
// Isolated: a fake board served by Vite, no backend, no real data, no visible window.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
const { app, BrowserWindow } = require("electron");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-kanban-layout-"));
app.setPath("userData", profile);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const timer = setTimeout(() => { console.error("Kanban board layout test timed out"); app.exit(1); }, 120000);
const shots = process.env.KANBAN_LAYOUT_SCREENSHOTS || "";

const longWord = "Supercalifragilisticexpialidocious".repeat(4);
function ticket(n, status, extras = {}) {
  return { ticket_id: `t-${status}-${n}`, scope: "rem", organization_id: "", organization_name: "", key: `REM-${n}`,
    title: `Back up Rem's database and start safely when it's damaged, number ${n}`, description: "", status, priority: "medium",
    assignee_id: "rem", assignee: "Rem", proposed: false, auto_approved: true, proposal_reason: "", duplicate_of: "", created_by: "self_work",
    created_at: "2026-11-14T11:00:00Z", updated_at: "2026-11-14T11:00:00Z", revision: 1, running: false, paused: "", block_reason: "", summary: "",
    type: "develop", project_dir: "/work/Raticode", project: "Raticode", backlog_score: 0, skip_reason: "", self_work_scope: "assigned",
    run_id: "", thread_id: "", branch: "", landed: false, waits_for: [], waiting: false, wait_notice: "",
    usage: { calls: 12, total_tokens: 14_600_000 + n * 1000, unknown_calls: n % 3 === 0 ? 2 : 0, partial_calls: 0 }, ...extras };
}
const tickets = [
  ...Array.from({ length: 53 }, (_, i) => ticket(100 + i, "done", i === 0 ? { title: `An unbroken ${longWord} title` } : {})),
  ...Array.from({ length: 14 }, (_, i) => ticket(50 + i, "blocked", { project: "second-brain-with-a-long-project-name",
    block_reason: `The review left these checks unresolved: requested maintenance issue within ${longWord} scope. Run relevant regression tests and required checks` })),
  ticket(1, "todo", { auto_approved: false, paused: "user", waits_for: [{ ticket_id: "t-x", key: "REM-9", title: `Waits on ${longWord}`, status: "todo" }] }),
  ticket(2, "in_progress", { running: true }),
  ticket(3, "backlog", { proposed: true, auto_approved: false }),
  ticket(4, "in_review", { skip_reason: `Rem left this out because ${longWord}` }),
];
const fixture = {
  version: "v1",
  board: { tickets, organizations: [], counts: {}, earlier: 0,
    participants: [{ participant_id: "rem", kind: "rem", name: "Rem", title: "Coordinator", organization_id: "", organization_name: "", projects: ["/work/Raticode"], assignable: ["/work/Raticode"], unavailable: [], hidden_projects: 0, revision: 1 }],
    projects: [{ root: "/work/Raticode", name: "Raticode", available: true, organization_name: "" }] },
  self_work: null,
};
// An organization's board: the same columns and cards, with long flags and project names.
const orgTickets = {};
["backlog", "todo", "running", "review", "done"].forEach(column => { for (let i = 0; i < 6; i += 1) {
  const id = `o-${column}-${i}`;
  orgTickets[id] = { id, key: `ORG-${column.length}${i}`, title: i ? `Ship the onboarding checklist for ${column} ${i}` : `Unbroken ${longWord}`, column,
    project: "p1", epicId: "", assignee: "w1", flag: column === "done" || column === "running" ? "" : `Needs ${longWord} before it can go on`, claims: [], used: [{}, {}] };
} });
const orgModel = { tickets: orgTickets, epics: {}, milestones: {}, runtime: { state: "paused", archived: false }, organization: {},
  projects: { p1: { id: "p1", name: `A project called ${longWord}`, color: "#7c3aed" } },
  workers: { w1: { id: "w1", name: "Ada Lovelace-Byron Worker With A Long Name", color: "#0ea5e9" } } };
let vite;

// Where each column and card sits, and anything that spills out of a card or column.
const MEASURE = `(() => {
  const r = el => { const b = el.getBoundingClientRect(); return { left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: b.width }; };
  const wrap = document.querySelector('.oh-board-wrap');
  const columns = [...document.querySelectorAll('.oh-board > .col')].map(col => ({
    label: col.getAttribute('aria-label'), box: r(col), header: r(col.querySelector('header')),
    cards: [...col.querySelectorAll('.oh-card')].map(card => ({ key: card.querySelector('.oh-key').textContent, box: r(card),
      spill: card.scrollWidth - card.clientWidth,
      // Text spilling out of a row can hide in the card's padding, so each row is measured too.
      rows: [...card.children].filter(row => row.getClientRects().length && !row.classList.contains('livebar')).map(row => ({ name: row.className, spill: row.scrollWidth - row.clientWidth })),
      parts: [...card.querySelectorAll('*')].filter(el => el.getClientRects().length && !el.closest('.livebar')).map(el => ({ tag: el.className?.baseVal ?? el.className, box: r(el) })) })),
  }));
  return { columns, wrap: { scrollWidth: wrap.scrollWidth, clientWidth: wrap.clientWidth }, page: document.documentElement.scrollWidth - document.documentElement.clientWidth };
})()`;

function check(layout, width, board, count) {
  const where = `on ${board} at ${width} px`;
  assert.equal(layout.columns.length, count, `${count} columns ${where}`);
  layout.columns.forEach((col, index) => {
    const next = layout.columns[index + 1];
    if (next) assert.ok(col.box.right <= next.box.left + 0.5, `${col.label} overlaps ${next.label} ${where}`);
    for (const card of col.cards) {
      assert.ok(card.box.left >= col.box.left - 0.5 && card.box.right <= col.box.right + 0.5,
        `${card.key} in ${col.label} is ${Math.round(card.box.width)} px wide and spills past its column (${Math.round(col.box.left)}-${Math.round(col.box.right)}) ${where}`);
      assert.ok(card.spill <= 1, `${card.key} in ${col.label} overflows inside itself by ${card.spill} px ${where}`);
      for (const row of card.rows) {
        assert.ok(row.spill <= 1, `${row.name} in ${card.key} (${col.label}) overflows by ${row.spill} px ${where}`);
      }
      for (const part of card.parts) {
        assert.ok(part.box.right <= card.box.right + 0.5 && part.box.left >= card.box.left - 0.5,
          `${part.tag} in ${card.key} (${col.label}) pokes out of its card ${where}`);
      }
    }
  });
  assert.equal(layout.page, 0, `the page itself doesn't scroll sideways ${where}`);
}

async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root: path.join(__dirname, ".."), logLevel: "error", cacheDir: testViteCacheDir("kanban-layout"),
    server: { host: "127.0.0.1", port: 0 }, plugins: [{
      name: "kanban-board-layout-test",
      resolveId(id) { if (id === "/kanban-entry.jsx") return id; },
      load(id) {
        if (id !== "/kanban-entry.jsx") return;
        return `import React from "react"; import { createRoot } from "react-dom/client";
          import KanbanWorkspace from "/src/components/KanbanWorkspace.jsx"; import { IssuesView } from "/src/components/OrganizationViews.jsx";
          import { ORGANIZATION_BOARD_FILTERS } from "/src/lib/boardFilters.js"; import "/src/styles/index.css";
          document.documentElement.classList.add("dark");
          const h = React.createElement;
          const organization = () => h("section", { className: "oh" }, h("div", { className: "oh-main" }, h("div", { className: "oh-view fill" },
            h(IssuesView, { model: ${JSON.stringify(orgModel)}, filters: ORGANIZATION_BOARD_FILTERS, setFilters() {}, busy: false, onOpen() {}, onMove() {}, onQuickAdd() {} }))));
          createRoot(document.getElementById("root")).render(h("div", { style: { display: "flex", height: "100vh" } },
            location.search === "?organization" ? h(organization) : h(KanbanWorkspace)));`;
      },
      configureServer(server) {
        server.middlewares.use(async (request, response, next) => {
          if (request.url.split("?")[0] === "/kanban-test") {
            response.setHeader("Content-Type", "text/html");
            response.end(await server.transformIndexHtml("/kanban-test", `<html><body class="bg-canvas" style="margin:0"><div id="root"></div><script type="module" src="/kanban-entry.jsx"></script></body></html>`));
            return;
          }
          if (!request.url.startsWith("/api/")) return next();
          response.setHeader("Content-Type", "application/json");
          response.end(JSON.stringify(request.url.split("?")[0] === "/api/rem/kanban" ? fixture : {}));
        });
      },
    }] });
  await vite.listen();
  // About the width of the user's screenshot, then a laptop and a wide screen.
  const win = new BrowserWindow({ width: 730, height: 720, show: true, webPreferences: { partition: "kanban-board-layout", contextIsolation: true } });
  const evaluate = source => win.webContents.executeJavaScript(source, true);
  const wait = async (source, ms = 20000) => {
    const deadline = Date.now() + ms;
    while (!(await evaluate(source))) { if (Date.now() > deadline) throw new Error(`Missing UI condition: ${source}`); await new Promise(r => setTimeout(r, 50)); }
  };
  const origin = `http://127.0.0.1:${vite.httpServer.address().port}/kanban-test`;
  const boards = [
    { name: "the Global board", url: origin, cards: tickets.length, columns: 6, narrow: "Blocked", wide: "Done", wideCount: 53, narrowCount: 14, usage: ".kb-usage" },
    { name: "an organization's board", url: `${origin}?organization`, cards: 30, columns: 5, narrow: "Review", wide: "Done", wideCount: 6, narrowCount: 6, usage: ".row3 > span:last-child" },
  ];
  for (const board of boards) {
    await win.loadURL(board.url);
    await wait(`document.querySelectorAll('.oh-board .oh-card').length === ${board.cards}`);
    for (const width of [730, 1280, 1920]) {
      win.setContentSize(width, 720);
      await wait(`window.innerWidth === ${width}`);
      await new Promise(r => setTimeout(r, 150));
      const layout = await evaluate(MEASURE);
      if (shots) {
        await evaluate(`document.querySelector('.oh-board-wrap').scrollLeft = 1e6`);
        await new Promise(r => setTimeout(r, 100));
        fs.writeFileSync(path.join(shots, `${board.columns === 6 ? "global" : "organization"}-${width}.png`), (await win.webContents.capturePage()).toPNG());
        await evaluate(`document.querySelector('.oh-board-wrap').scrollLeft = 0`);
      }
      check(layout, width, board.name, board.columns);
      const where = `on ${board.name} at ${width} px`;
      const wide = layout.columns.find(col => col.label === board.wide);
      const narrow = layout.columns.find(col => col.label === board.narrow);
      assert.equal(wide.cards.length, board.wideCount);
      assert.equal(narrow.cards.length, board.narrowCount);
      // Done's cards line up under Done's header, not shifted towards its neighbour.
      assert.ok(Math.abs(wide.cards[0].box.left - wide.header.left) < 12, `${board.wide}'s cards line up with its header ${where}`);
      // An unbroken title wraps over several lines inside the column.
      assert.ok(wide.cards[0].box.bottom - wide.cards[0].box.top > 90, `a long unbroken title wraps ${where}`);
      if (width === 730) {
        assert.ok(layout.wrap.scrollWidth > layout.wrap.clientWidth + 100, `the board scrolls sideways when its columns don't fit ${where}`);
        await evaluate(`document.querySelector('.oh-board-wrap').scrollLeft = 1e6`);
        assert.ok(await evaluate(`document.querySelector('.oh-board-wrap').scrollLeft`) > 100, `scrolling sideways reaches the last column ${where}`);
      }
      // The right end of a card's bottom row belongs to that card, not to a card from the next column painted over it.
      const hit = await evaluate(`(() => { const col = [...document.querySelectorAll('.oh-board > .col')].find(c => c.getAttribute('aria-label') === ${JSON.stringify(board.narrow)});
        col.scrollIntoView({ inline: 'nearest' }); const part = col.querySelector('.oh-card ${board.usage}'); const b = part.getBoundingClientRect();
        const el = document.elementFromPoint(b.right - 2, b.top + b.height / 2); return el?.closest('.col')?.getAttribute('aria-label') || ''; })()`);
      assert.equal(hit, board.narrow, `the end of a ${board.narrow} card's bottom row is visible, not covered, ${where}`);
      await evaluate(`document.querySelector('.oh-board-wrap').scrollLeft = 0`);
    }
  }
  console.log("Board layout: on the Global board and an organization's board, cards stay in their columns, long text wraps and the board scrolls sideways at 730, 1280 and 1920 px.");
}

main().then(() => { clearTimeout(timer); return vite?.close(); }).then(() => { fs.rmSync(profile, { recursive: true, force: true }); app.exit(0); }).catch(async (error) => {
  console.error(error);
  await vite?.close().catch(() => {});
  fs.rmSync(profile, { recursive: true, force: true });
  app.exit(1);
});
