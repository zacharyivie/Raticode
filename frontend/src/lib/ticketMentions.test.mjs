import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { chatRows, selfWorkBatchLine } from "./remHome.js";
import { findRequestedTicket } from "./remKanban.js";
import { cardTickets, remarkTicketMentions, splitTicketText, taskTickets, ticketKeys, ticketStatusLabel } from "./ticketMentions.js";

const index = {
  "REM-5": { ticket_id: "t5", key: "REM-5", title: "Fix sync", status: "in_progress", scope: "rem" },
  "AGE-18": { ticket_id: "o18", key: "AGE-18", title: "Agent eval", status: "done", scope: "organization" },
};

function render(markdown, tickets = index) {
  return renderToStaticMarkup(h(ReactMarkdown, {
    remarkPlugins: [remarkGfm, remarkTicketMentions(tickets)],
    components: { "ticket-pill": ({ node }) => h("button", { "data-key": node.properties.dataTicketKey }, tickets[node.properties.dataTicketKey].title) },
  }, markdown));
}

test("ticket keys are whole words, as the backend reads them", () => {
  assert.deepEqual(ticketKeys("See REM-5. (AGE-18), REM-5 again and **REM-12**"), ["REM-5", "AGE-18", "REM-12"]);
  assert.deepEqual(ticketKeys("xREM-5 REM-5a REM-5-2 a/REM-5 REM-5.2 rem-5 REM-0 R-5 REM- #REM-5 me@REM-5"), []);
  assert.deepEqual(ticketKeys(""), []);
  assert.deepEqual(ticketKeys(null), []);
});

test("only keys of tickets on the board become pills; code, links and unknown keys stay text", () => {
  const html = render([
    "Working on REM-5 and AGE-18; REM-99 and UTF-8 aren't tickets.",
    "",
    "Code `REM-5` and [a link about REM-5](https://example.com/REM-5) stay as written.",
    "",
    "```",
    "git checkout REM-5",
    "```",
  ].join("\n"));
  assert.match(html, /Working on <button data-key="REM-5">Fix sync<\/button> and <button data-key="AGE-18">Agent eval<\/button>; REM-99 and UTF-8 aren/);
  assert.match(html, /<code>REM-5<\/code>/);
  assert.match(html, /<a href="https:\/\/example.com\/REM-5">a link about REM-5<\/a>/);
  assert.match(html, /<pre><code>git checkout REM-5\n<\/code><\/pre>/);
  assert.equal(html.match(/<button/g).length, 2);
  // With no tickets known, nothing changes.
  assert.equal(render("REM-5", {}), "<p>REM-5</p>");
});

test("text splits around known keys only", () => {
  assert.deepEqual(splitTicketText("REM-5, REM-6.", index), [{ key: "REM-5" }, ", REM-6."]);
  assert.deepEqual(splitTicketText("plain", index), ["plain"]);
  assert.equal(ticketStatusLabel("in_progress"), "In progress");
  assert.equal(ticketStatusLabel("unknown"), "");
});

test("a card lists the live run's tickets, else the ones saved with the message", () => {
  const saved = { tickets: [{ ticket_id: "t5", key: "REM-5", title: "Old", role: "worked" }] };
  assert.deepEqual(cardTickets(saved, null).map(t => t.title), ["Old"]);
  assert.deepEqual(cardTickets(saved, { tickets: [{ ticket_id: "t5", key: "REM-5", title: "New" }] }).map(t => t.title), ["New"]);
  assert.deepEqual(cardTickets(saved, { tickets: [] }).map(t => t.title), ["Old"]);
  assert.deepEqual(cardTickets({ tickets: [{ title: "no key" }, null] }), []);
  const task = {
    active_run: { tickets: [{ ticket_id: "a", key: "REM-1" }] },
    last_run: { tickets: [{ ticket_id: "a", key: "REM-1" }, { ticket_id: "b", key: "REM-2" }] },
    recent_runs: [{ tickets: [{ ticket_id: "c", key: "REM-3" }] }],
  };
  assert.deepEqual(taskTickets(task).map(t => t.key), ["REM-1", "REM-2", "REM-3"]);
});

test("cards one self-work pass started show as one row; others stay single", () => {
  const card = (id, batch, kind = "ticket", extra = {}) => ({ id, role: "rem", work: { run_id: id, self_work: kind }, self_work: { kind, batch }, ...extra });
  const rows = chatRows([
    { id: "u", role: "user" },
    card("a", "p1"),
    card("b", "p1", "research"),
    card("c", "p1", "triage"),
    card("d", "p2"),
    { id: "e", role: "rem", kind: "task_update", work: { run_id: "a" }, self_work: { batch: "p1" } },
    card("f", "p3"),
  ]);
  assert.deepEqual(rows.map(row => row.batch ? row.messages.map(m => m.id) : row.message.id), ["u", ["a", "b", "c"], "d", "e", "f"]);
  assert.equal(selfWorkBatchLine(rows[1].messages), "Started 3 things while you were away: 1 ticket, 1 backlog ranking and 1 research run.");
  assert.equal(selfWorkBatchLine([card("x", "p"), card("y", "p")]), "Started 2 things while you were away: 2 tickets.");
});

test("a pill opens its ticket on the board, or says why it can't", () => {
  const board = { tickets: [{ ticket_id: "t5", key: "REM-5" }], earlier: 0 };
  assert.equal(findRequestedTicket(board, { ticket_id: "t5", key: "REM-5" }).ticket.key, "REM-5");
  // A key that moved to another ticket ID (say, the board was rebuilt) still finds it by key.
  assert.equal(findRequestedTicket(board, { ticket_id: "gone", key: "REM-5" }).ticket.key, "REM-5");
  assert.equal(findRequestedTicket(board, { ticket_id: "x", key: "REM-9" }).missing, "REM-9 isn't on the board anymore.");
  assert.match(findRequestedTicket({ ...board, earlier: 3 }, { ticket_id: "x", key: "REM-9" }).missing, /older than the board shows/);
  assert.deepEqual(findRequestedTicket(null, { key: "REM-5" }), { ticket: null, missing: "" });
});
