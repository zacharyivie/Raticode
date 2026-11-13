import assert from "node:assert/strict";
import { test } from "node:test";
import { repeats, statusLine, visibleComments } from "./ticketConversation.js";

const REPORT = "I ran the permission trial in plan mode. Its done-when isn't met.\n\n| Capability | Result |";
// What older Raticode stored for DESK-2: each status entry copied the comment above it.
const DESK_2 = [
  { actor: "employee:director", at: "2026-11-11T20:05:36.001518+00:00", body: REPORT },
  { actor: "employee:director", at: "2026-11-11T20:05:36.001833+00:00", kind: "status", from: "in_progress", to: "in_review", title: "Work complete, moved to In review", body: "I ran the permission trial in plan mode. Its done-when isn't met." },
  { actor: "user", at: "2026-11-11T20:09:12.379912+00:00", body: "Accepted", decision: "accept" },
  { actor: "user", at: "2026-11-11T20:09:12.380346+00:00", kind: "status", from: "in_review", to: "done", title: "Verified complete, moved to Done", body: "Accepted" },
];

test("a status entry that copied the comment above keeps its headline and drops the copy", () => {
  const view = visibleComments(DESK_2);
  assert.deepEqual(view.map(c => [c.kind || "comment", c.body]), [["comment", REPORT], ["status", ""], ["comment", "Accepted"], ["status", ""]]);
  assert.equal(DESK_2[3].body, "Accepted", "stored entries are not changed");
  assert.deepEqual(statusLine(view[1]), { title: "Work complete, moved to In review", move: "In progress → In review" });
});

test("a clipped recap is a repeat, a status entry's own reason is not", () => {
  assert.equal(repeats("Ported the importer and…", "Ported the importer and ran every test."), true);
  assert.equal(repeats("The last run failed; see the conversation", "Started."), false);
  const view = visibleComments([
    { actor: "employee:mira", at: "2026-01-01T00:00:00Z", body: "Started." },
    { actor: "system", at: "2026-01-01T00:01:00Z", kind: "status", to: "blocked", title: "Blocked", body: "The last run failed; see the conversation" },
    { actor: "system", at: "2026-01-01T00:02:00Z", kind: "status", to: "todo", title: "Unblocked", body: "Started." },
  ]);
  assert.deepEqual(view.map(c => c.body), ["Started.", "The last run failed; see the conversation", "Started."]);
});

test("exact retries are hidden; the same words later, from someone else or as a review are kept", () => {
  const view = visibleComments([
    { actor: "user", at: "2026-01-01T00:00:00Z", body: "Ping" },
    { actor: "user", at: "2026-01-01T00:00:01Z", body: "Ping" },
    { actor: "employee:mira", at: "2026-01-01T00:00:02Z", body: "Ping" },
    { actor: "employee:mira", at: "2026-01-01T01:00:02Z", body: "Ping" },
    { actor: "user", at: "2026-01-01T01:00:03Z", body: "Ping", decision: "accept" },
  ]);
  assert.deepEqual(view.map(c => [c.actor, c.at.slice(11, 19)]), [["user", "00:00:00"], ["employee:mira", "00:00:02"], ["employee:mira", "01:00:02"], ["user", "01:00:03"]]);
  assert.deepEqual(visibleComments(), []);
});
