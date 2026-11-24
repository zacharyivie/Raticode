import assert from "node:assert/strict";
import test from "node:test";
import { NO_DECISIONS, answerChange, applyDecisionChange, cardLine, cardState, queuedCount, queuedLine, checkLine, closedLabel, countNote, decidedLine, decisionPlace, decisionsCount, editChanges, editState, editTitle, evidenceParts, evidenceText, fallbackDraft, groupDecisions, historyEntries, inProject, isRecord, liveDecision, pathWarnings, recordAddChange, recordDraft, RESTORE_NOTE, recordEditChange, recordFileNote, recordReplaceChange, recordSections, removal, replacesLine, sameDecisions, scanSummary, scopeChips, settleEditLocally, splitList, waitingCount, waitingEdits, SCAN_TOPICS, askAgainTitle, currentAnswer, reopenedLine, looseQueued, looseWaiting, planGroups, planLine, planQueuedLine } from "./remDecisions.js";
import { sampleDecisions } from "./remHomeSample.js";

const APP = "/home/me/Projects/app";
const OTHER = "/home/me/Projects/site";
const evidence = [
  { kind: "file", ref: "package.json", excerpt: "\"test\": \"node --test\"" },
  { kind: "command", ref: "npm test", excerpt: "42 passed" },
  { kind: "commit", ref: "abc1234" },
  { kind: "doc", ref: "AGENTS.md" },
];
const items = [
  { id: "D-5", project: APP, project_name: "app", topic: "checks.fast", question: "Which check runs first?", answer: "npm test", recommended: "npm test", why: "It's quick.", evidence, status: "pending", supersedes: "D-1" },
  { id: "D-4", project: OTHER, project_name: "site", topic: "deploy", question: "Where does the site deploy?", answer: "GitHub Pages", recommended: "GitHub Pages", evidence: [], status: "provisional", decided_by: "rem" },
  { id: "D-2", project: null, topic: "style.tone", question: "How formal should replies be?", answer: "Plain and short", recommended: "", evidence: [], status: "confirmed", decided_by: "user", decided_at: "2026-11-18T10:00:00Z" },
  { id: "D-1", project: APP, project_name: "app", topic: "checks.fast", question: "Which check runs first?", answer: "make lint", status: "confirmed", decided_by: "user" },
  { id: "D-0", project: APP, project_name: "app", topic: "old", question: "Old question?", answer: "Old", status: "superseded", superseded_by: "D-1" },
  { id: "D-9", project: OTHER, project_name: "site", topic: "nope", question: "Use a CDN?", answer: "Yes", status: "rejected" },
];
const view = { revision: 7, items, waiting: 1, provisional: 1, confirmed: 2 };

test("the heading counts only what needs the user: waiting and Rem's own auto-mode answers", () => {
  assert.equal(decisionsCount(NO_DECISIONS), "");
  assert.equal(decisionsCount(view), "2 waiting");
  assert.equal(decisionsCount({ items: items.filter(item => item.status === "confirmed") }), "");
  assert.equal(waitingCount(view, { project: APP, all: false }), 1);
});

test("an unchanged poll keeps the same list, and the poll's version isn't part of it", () => {
  const polled = { ...JSON.parse(JSON.stringify(view)), version: "abc" };
  assert.equal(sameDecisions(polled, view), view);
  assert.equal(sameDecisions(undefined, view), view);
  assert.equal(sameDecisions({}, view), view, "a response without items keeps the last list");
  assert.equal(sameDecisions(undefined, undefined), NO_DECISIONS);
  const changed = sameDecisions({ ...view, revision: 8, version: "def" }, view);
  assert.notEqual(changed, view);
  assert.equal("version" in changed, false);
});

test("a project shows its own decisions and those about the user; a worktree counts as its project", () => {
  assert.equal(inProject(items[0], APP), true);
  assert.equal(inProject(items[0], `${APP}/.worktrees/feature`), true);
  assert.equal(inProject(items[0], `${APP}-fork`), false);
  assert.equal(inProject(items[1], APP), false);
  assert.equal(inProject(items[2], APP), true, "a decision about the user applies everywhere");
  assert.equal(inProject(items[1], ""), true, "no project open shows everything");
  const here = groupDecisions(view, { project: APP });
  assert.deepEqual(here.waiting.map(item => item.id), ["D-5"]);
  assert.deepEqual(here.decided.map(item => item.id), ["D-2", "D-1"]);
  assert.deepEqual(here.rules.map(item => item.id), ["D-2", "D-1"]);
  assert.deepEqual(here.records, []);
  assert.deepEqual(here.history.map(item => item.id), ["D-0"]);
  const everywhere = groupDecisions(view, { project: APP, all: true });
  assert.deepEqual(everywhere.waiting.map(item => item.id), ["D-5", "D-4"]);
  assert.deepEqual(everywhere.history.map(item => item.id), ["D-0", "D-9"]);
});

test("evidence reads as one short line: kind and reference, at most three", () => {
  assert.equal(evidenceText(evidence), "file package.json · ran npm test · commit abc1234 · +1 more");
  assert.equal(evidenceText([]), "");
  assert.equal(evidenceText(undefined), "");
  const { parts, more } = evidenceParts(evidence, 2);
  assert.deepEqual(parts.map(part => part.label), ["file", "ran"]);
  assert.equal(parts[1].excerpt, "42 passed");
  assert.equal(more, 2);
});

test("rows name their place, and an opened one says who decided it and when", () => {
  assert.equal(decisionPlace(items[0]), "app");
  assert.equal(decisionPlace(items[2]), "About you");
  assert.equal(decisionPlace({ project: "/srv/repos/tool/" }), "tool");
  assert.equal(decidedLine(items[2]), "You decided on 2026-11-18.");
  assert.equal(decidedLine(items[1]), "Rem decided (auto mode).");
  assert.equal(decidedLine({ ...items[2], source: { via: "carried over" } }), "Raticode carried it over from what it already did on 2026-11-18.");
  assert.equal(decidedLine(items[0]), "", "a request nobody decided yet says nothing");
  assert.equal(closedLabel(items[4]), "Replaced by D-1");
  assert.equal(closedLabel(items[5]), "Turned down");
});

test("the history lists each entry's date, who did it and what, newest first", () => {
  const history = historyEntries({ history: [
    { at: "2026-11-17T09:00:00+00:00", actor: "rem", action: "requested" },
    { at: "2026-11-18T10:00:00+00:00", actor: "user", action: "confirmed", note: "user" },
    { at: "2026-11-19T11:00:00+00:00", actor: "user", action: "edited", note: "Clearer wording" },
    { at: "2026-11-20T12:00:00+00:00", actor: "raticode", action: "kept the approved text", note: "A Rem run changed docs/decisions/0001-x.md; only the user changes decisions." },
    { at: "2026-11-21T13:00:00+00:00", actor: "user", action: "superseded", note: "D-9" },
    { at: "2026-11-21T14:00:00+00:00", actor: "worker", action: "looked twice" },
    { actor: "user" },
  ] });
  assert.deepEqual(history.map(({ date, actor, action, note }) => ({ date, actor, action, note })), [
    { date: "2026-11-21", actor: "Worker", action: "Looked twice", note: "" },
    { date: "2026-11-21", actor: "You", action: "Replaced by D-9", note: "" },
    { date: "2026-11-20", actor: "Raticode", action: "Kept the approved text", note: "A Rem run changed docs/decisions/0001-x.md; only the user changes decisions." },
    { date: "2026-11-19", actor: "You", action: "Edited", note: "Clearer wording" },
    { date: "2026-11-18", actor: "You", action: "Confirmed", note: "" },
    { date: "2026-11-17", actor: "Rem", action: "Asked", note: "" },
  ]);
  assert.equal(new Set(history.map(entry => entry.key)).size, history.length, "each row has its own key");
  assert.deepEqual(historyEntries(items[0]), [], "no history, no rows");
  assert.deepEqual(historyEntries({ history: "nope" }), []);
});

test("a record a Rem run changed says the file differs instead of not in the repo yet", () => {
  const record = { id: "D-6", remember_as: "record", record_path: "docs/decisions/0006-errors.md", record_error: "The landing branch checkout has uncommitted changes." };
  assert.deepEqual(recordFileNote(record), { kind: "missing", flag: "Not in the repo yet", text: "Not in the repo yet: The landing branch checkout has uncommitted changes." });
  const restoring = recordFileNote({ ...record, record_restore: "abc123" });
  assert.equal(restoring.kind, "restore");
  assert.equal(restoring.text, "The file differs from the approved text; Raticode puts it back when the checkout is clean");
  assert.equal(restoring.text, RESTORE_NOTE);
  assert.doesNotMatch(`${restoring.flag} ${restoring.text}`, /Not in the repo yet/);
  assert.equal(recordFileNote({ ...record, record_error: "" }), null);
  assert.equal(recordFileNote({ id: "D-1", record_restore: "abc123" }), null, "only design decisions have a file");
});

test("what the user typed becomes change for a waiting request and edit for a decided one", () => {
  assert.deepEqual(answerChange("  Run make check  ", items[0]), { action: "change", id: "D-5", answer: "Run make check" });
  assert.equal(answerChange("   ", items[0]), null);
  assert.deepEqual(answerChange("Friendly", items[2]), { action: "edit", id: "D-2", answer: "Friendly" });
  assert.equal(answerChange("Plain and short", items[2]), null, "an unchanged answer saves nothing");
  assert.equal(answerChange("x".repeat(1200), items[0]).answer.length, 1000);
});

test("a chat card follows the live list, not the message's snapshot", () => {
  const card = { id: "D-5", topic: "checks.fast", question: "Which check runs first?", recommended: "npm test", status: "pending", evidence: [] };
  assert.equal(liveDecision(view, "D-5"), items[0]);
  assert.equal(liveDecision(view, "D-404"), null);
  assert.equal(liveDecision(view, ""), null);
  assert.equal(cardState(card, items[0], true).state, "open");
  assert.equal(cardState(card, null, false).state, "open", "before the list loads the snapshot stands");
  assert.equal(cardState(card, null, true).state, "gone", "a forgotten decision has no buttons");
  assert.deepEqual(cardState(card, { ...items[0], status: "confirmed", answer: "make check" }, true), { state: "decided", answer: "make check", replaced: "" });
  assert.deepEqual(cardState(card, items[4], true), { state: "decided", answer: "Old", replaced: "D-1" });
  assert.equal(cardState(card, items[5], true).state, "rejected");
  assert.equal(cardState({ title: "Rem couldn't settle this. Here is what it found.", question: "Q?", found: "F", candidate: "", fallback: true }, null, true).state, "fallback");
});

test("a question the user forgot while it waited says they withdrew it", () => {
  const card = { id: "D-5", topic: "checks.fast", question: "Which check runs first?", recommended: "npm test", status: "withdrawn", evidence: [] };
  assert.deepEqual(cardState(card, null, true), { state: "withdrawn" });
  assert.deepEqual(cardState(card, null, false), { state: "withdrawn" }, "even before the list loads");
  assert.equal(cardLine(cardState(card, null, true)), "You withdrew this question");
  // A card the desktop didn't mark, such as one forgotten after it was decided, stays "No longer listed".
  assert.equal(cardLine(cardState({ ...card, status: "pending" }, null, true)), "No longer listed");
  assert.equal(cardLine({ state: "rejected" }), "Turned down");
  assert.equal(cardLine({ state: "queued" }), "Queued behind the questions already waiting for you");
  assert.equal(cardLine({ state: "decided", answer: "npm test" }), "Decided: npm test");
});

test("questions past the three waiting cards show as \"N more queued\", never as cards", () => {
  const queued = [
    { id: "D-7", project: APP, project_name: "app", topic: "t7", question: "Which port?", recommended: "8080", evidence: [], status: "queued" },
    { id: "D-8", project: APP, project_name: "app", topic: "t8", question: "Which region?", recommended: "eu", evidence: [], status: "queued" },
    { id: "D-6", project: OTHER, project_name: "site", topic: "t6", question: "Which host?", recommended: "Pages", evidence: [], status: "queued" },
  ];
  const listed = { ...view, items: [...items, ...queued], queued: 3 };
  const groups = groupDecisions(listed, { project: APP });
  assert.deepEqual(groups.waiting.map(item => item.id), ["D-5"], "a queued question is in no group");
  assert.equal(groups.history.some(item => item.status === "queued"), false);
  assert.equal(waitingCount(listed), 2, "the heading still counts only cards");
  assert.equal(queuedCount(listed, { project: APP }), 2);
  assert.equal(queuedCount(listed, { all: true }), 3);
  assert.equal(queuedLine(queuedCount(listed, { project: APP })), "2 more queued");
  assert.equal(queuedLine(queuedCount(view, { all: true })), "");
  // Its chat card says it is queued, with no buttons, until it moves up.
  const card = { id: "D-7", question: "Which port?", recommended: "8080", status: "queued", evidence: [] };
  assert.deepEqual(cardState(card, queued[0], true), { state: "queued" });
  assert.equal(cardState(card, { ...queued[0], status: "pending" }, true).state, "open");
});

test("answering a fallback card yourself quotes its question in the message box", () => {
  assert.equal(fallbackDraft({ question: "Which branch ships?\nThe release or dev?" }), "> Which branch ships?\n> The release or dev?\n\n");
  assert.equal(fallbackDraft({ question: "  " }), "");
});

test("the design fixtures apply the same changes locally", () => {
  const stamp = "2026-11-20T12:00:00.000Z";
  const used = applyDecisionChange(view, { action: "use", id: "D-5" }, stamp);
  assert.equal(used.revision, 8);
  const d5 = used.items.find(item => item.id === "D-5");
  assert.equal(d5.status, "confirmed");
  assert.equal(d5.decided_by, "user");
  assert.equal(used.items.find(item => item.id === "D-1").status, "superseded", "the decision it replaces closes");
  assert.equal(used.waiting, 0);
  assert.deepEqual(used.items.map(item => item.status), ["provisional", "confirmed", "confirmed", "superseded", "superseded", "rejected"]);
  const changed = applyDecisionChange(view, { action: "change", id: "D-4", answer: "Netlify" }, stamp);
  assert.equal(changed.items.find(item => item.id === "D-4").answer, "Netlify");
  assert.equal(applyDecisionChange(view, { action: "reject", id: "D-5" }).items.find(item => item.id === "D-5").status, "rejected");
  const reopened = applyDecisionChange(view, { action: "reopen", id: "D-2" }, stamp);
  const asked = reopened.items.find(item => item.supersedes === "D-2");
  assert.equal(asked.status, "pending", "Ask again files a new card that waits with the others");
  assert.equal(asked.asked_again, true);
  assert.equal(asked.current_answer, view.items.find(item => item.id === "D-2").answer);
  assert.equal(reopened.items.find(item => item.id === "D-2").status, view.items.find(item => item.id === "D-2").status, "the decision stays in force");
  assert.equal(currentAnswer(asked), asked.current_answer);
  assert.equal(reopenedLine(asked), "You asked again");
  assert.equal(applyDecisionChange(view, { action: "forget", id: "D-2" }).items.some(item => item.id === "D-2"), false);
  assert.equal(applyDecisionChange(view, { action: "edit", id: "D-2", answer: "Warm" }).items.find(item => item.id === "D-2").answer, "Warm");
  assert.equal(applyDecisionChange(view, { action: "use", id: "missing" }), view);
});

test("a written record is retired, not forgotten", () => {
  const stamp = "2026-11-21T12:00:00.000Z";
  const written = { id: "D-9", project: "/p", topic: "adr:db", question: "Which database?", answer: "SQLite", remember_as: "record", record_path: ".raticode/decisions/0009-which-database.md", status: "confirmed", history: [] };
  const unwritten = { ...written, id: "D-10", record_path: undefined };
  const fact = { id: "D-11", project: "/p", topic: "checks.fast", question: "Check?", answer: "make", remember_as: "fact", status: "confirmed" };
  assert.equal(removal(written).label, "Retire");
  assert.match(removal(written).detail, /status: rejected and commits that on the landing branch/);
  assert.equal(removal(unwritten).label, "Forget");
  assert.equal(removal(fact).label, "Forget");
  // Retire is its own action, since it commits the file; a Forget never turns into that commit.
  assert.deepEqual([removal(written).action, removal(unwritten).action, removal(fact).action], ["retire", "forget", "forget"]);
  const view = { revision: 1, items: [written, unwritten, fact] };
  assert.equal(applyDecisionChange(view, { action: "forget", id: "D-9" }, stamp), view, "the backend refuses forget for a written record");
  const after = applyDecisionChange(view, { action: "retire", id: "D-9" }, stamp);
  const retired = after.items.find(item => item.id === "D-9");
  assert.equal(retired.status, "rejected");
  assert.equal(retired.retired_at, stamp);
  assert.equal(closedLabel(retired), "Retired");
  assert.equal(historyEntries(retired)[0].action, "Retired");
  assert.equal(applyDecisionChange(view, { action: "forget", id: "D-10" }).items.some(item => item.id === "D-10"), false, "a record with no file is still deleted");
  assert.equal(closedLabel({ status: "rejected" }), "Turned down");
});

test("the sample decisions show one waiting request and one confirmed answer", () => {
  const sample = sampleDecisions();
  assert.deepEqual(sample.items.map(item => item.status), ["pending", "confirmed"]);
  assert.equal(decisionsCount(sample), "1 waiting");
  assert.ok(sample.items[0].evidence.length > 0, "a recommendation carries evidence");
});

test("Raticode's own check run shows on a decided command", () => {
  assert.equal(checkLine({}), "");
  assert.equal(checkLine({ last_check: { passed: true, ran_at: "2026-11-21T08:00:00+00:00" } }), "Raticode's last run of it passed on 2026-11-21.");
  assert.equal(checkLine({ last_check: { passed: false, exit_code: 2, ran_at: "2026-11-21T08:00:00+00:00" } }), "Raticode's last run of it failed (exit 2) on 2026-11-21.");
  const carried = decidedLine({ id: "D-5", question: "Which branch?", answer: "dev", decided_by: "user", decided_at: "2026-11-20T10:00:00Z", source: { via: "carried over" }, project: "/p/Raticode" });
  assert.equal(carried, "Raticode carried it over from what it already did on 2026-11-20.");
});

test("a project scan's outcome reads as one sentence", () => {
  assert.equal(scanSummary([]), "Nothing new in this project's files.");
  assert.equal(scanSummary([{ topic: "checks.fast", outcome: "turned down before" }, { topic: "x", outcome: "unchecked" }]), "Nothing new in this project's files.");
  assert.equal(scanSummary([{ topic: "checks.fast", outcome: "pending" }, { topic: "branch.integration", outcome: "answered" }]), "Found 2 answers: 1 waiting for you, 1 already decided.");
  assert.equal(scanSummary([{ topic: "checks.fast", outcome: "provisional" }]), "Found 1 answer: 1 decided by Rem for now (auto mode).");
});

const record = {
  id: "D-7", project: APP, project_name: "app", topic: "adr:storage", question: "Where does local data live?", answer: "SQLite in the data folder", recommended: "", why: "One file, no server.",
  remember_as: "record", status: "confirmed", decided_by: "user", record_path: "docs/decisions/0007-storage.md",
  record: { context: "Workers need shared state.", options: "", consequences: "Back up one file.", check: "", applies_to: ["src/store/**"], forbids: ["src/**/*.json"], signals: ["postgres"] },
};
const conflictFix = { id: "D-8", project: APP, topic: "conflict:d-3-d-5", question: "These decisions conflict. Replace them with one?", answer: "Tabs everywhere", recommended: "Tabs everywhere", status: "pending", supersedes_all: ["D-3", "D-5"], conflict: "D-3 says tabs and D-5 says spaces." };

test("design decisions sit apart from how Rem works, and a conflict fix waits with the rest", () => {
  const grouped = groupDecisions({ items: [conflictFix, record, ...items] }, { project: APP });
  assert.deepEqual(grouped.waiting.map(item => item.id), ["D-8", "D-5"]);
  assert.deepEqual(grouped.rules.map(item => item.id), ["D-2", "D-1"]);
  assert.deepEqual(grouped.records.map(item => item.id), ["D-7"]);
  assert.deepEqual(grouped.decided.map(item => item.id), ["D-7", "D-2", "D-1"]);
  assert.equal(isRecord(record), true);
  assert.equal(isRecord(items[2]), false);
  assert.equal(replacesLine(conflictFix), "Replaces D-3, D-5");
  assert.equal(replacesLine(items[0]), "", "a single replacement keeps its own pill");
});

test("a record shows only the parts it has, and its scope as chips", () => {
  assert.deepEqual(recordSections(record).map(part => part.label), ["Context", "Consequences"]);
  assert.deepEqual(recordSections({}), []);
  assert.deepEqual(scopeChips(record).map(chip => chip.label), ["src/store/**", "Never: src/**/*.json"]);
  assert.deepEqual(scopeChips({ record: { applies_to: "nope" } }), []);
  assert.deepEqual(splitList(" a/**, b ,\n a/**,, c "), ["a/**", "b", "c"]);
  assert.equal(splitList(Array.from({ length: 14 }, (_, n) => `p${n}`).join(",")).length, 10);
});

test("record chips show each path's tracked-file count and warn on empty or forbidden matches", () => {
  const counted = { ...record, record: { ...record.record, applies_to: ["src/store/**", "github/workflows/**"], forbids: ["src/**/*.json", "vendor/**"] },
    path_counts: { branch: "dev", commit: "abc123", applies_to: { "src/store/**": 12, "github/workflows/**": 0 }, forbids: { "src/**/*.json": 3, "vendor/**": 0 } } };
  assert.deepEqual(scopeChips(counted).map(({ label, count, warn }) => ({ label, count, warn })), [
    { label: "src/store/**", count: "12 files", warn: false },
    { label: "github/workflows/**", count: "matches no files", warn: true },
    { label: "Never: src/**/*.json", count: "matches 3 files that exist now", warn: true },
    { label: "Never: vendor/**", count: "no files now", warn: false },
  ]);
  assert.deepEqual(scopeChips(record).map(chip => chip.count), ["", ""], "no counts (no landing branch) shows the paths alone");
  assert.deepEqual(countNote("applies", 1), { text: "1 file", warn: false });
  assert.deepEqual(countNote("forbids", 1), { text: "matches 1 file that exists now", warn: true });
  assert.deepEqual(countNote("applies", undefined), { text: "", warn: false });
  assert.deepEqual(pathWarnings(counted.path_counts), [
    "Applies to github/workflows/** matches no files on dev.",
    "Never touch src/**/*.json matches 3 files that exist now.",
  ]);
  assert.deepEqual(pathWarnings(null), [], "paths that can't be counted give no warnings");
});

test("editing a record sends only what changed, with the note", () => {
  const draft = recordDraft(record);
  assert.equal(draft.applies_to, "src/store/**");
  assert.equal(draft.answer, "SQLite in the data folder");
  assert.equal(recordEditChange(draft, record), null, "nothing changed saves nothing");
  assert.equal(recordEditChange({ ...draft, note: "typo" }, record), null, "a note alone saves nothing");
  assert.deepEqual(recordEditChange({ ...draft, consequences: "  Back up one file daily. ", applies_to: "src/store/**, src/db/**", note: " clearer " }, record),
    { action: "edit", id: "D-7", consequences: "Back up one file daily.", applies_to: ["src/store/**", "src/db/**"], note: "clearer" });
  assert.deepEqual(recordEditChange({ ...draft, answer: " " }, record), { error: "The decision can't be empty." });
});

test("replacing a record sends the whole new record; adding one names its project", () => {
  const draft = { ...recordDraft(record), answer: "Postgres", forbids: "", note: "We need many writers" };
  assert.deepEqual(recordReplaceChange(draft, record), {
    action: "replace", id: "D-7", answer: "Postgres", why: "One file, no server.", question: "Where does local data live?",
    context: "Workers need shared state.", options: "", consequences: "Back up one file.", check: "", applies_to: ["src/store/**"], forbids: [], signals: ["postgres"], note: "We need many writers",
  });
  assert.deepEqual(recordReplaceChange({ answer: "" }, record), { error: "Write the new decision first." });
  assert.deepEqual(recordReplaceChange({ answer: "Friendly" }, items[2]), { action: "replace", id: "D-2", answer: "Friendly", why: "" }, "a fact carries no record parts");
  const add = recordAddChange({ ...recordDraft({}), question: "How are errors shown?", answer: "Inline, never in a dialog", applies_to: "src/components/**" }, APP);
  assert.deepEqual(add, { action: "add", project: APP, question: "How are errors shown?", answer: "Inline, never in a dialog", why: "", remember_as: "record", context: "", options: "", consequences: "", check: "", applies_to: ["src/components/**"], forbids: [], signals: [] });
  assert.ok(recordAddChange({ question: "Q", answer: "A" }, "").error);
  assert.ok(recordAddChange({ question: "", answer: "A" }, APP).error);
});

test("the design fixtures replace, add and approve conflict fixes like the backend", () => {
  const start = { revision: 1, items: [conflictFix, record, { id: "D-3", project: APP, status: "confirmed", answer: "Tabs" }, { id: "D-5", project: APP, status: "confirmed", answer: "Spaces" }] };
  const fixed = applyDecisionChange(start, { action: "use", id: "D-8" });
  assert.deepEqual(fixed.items.filter(item => item.status === "superseded").map(item => [item.id, item.superseded_by]), [["D-3", "D-8"], ["D-5", "D-8"]]);
  const replaced = applyDecisionChange(start, recordReplaceChange({ ...recordDraft(record), answer: "Postgres" }, record));
  const fresh = replaced.items.find(item => item.supersedes === "D-7");
  assert.equal(fresh.answer, "Postgres");
  assert.equal(fresh.remember_as, "record");
  assert.deepEqual(fresh.record.applies_to, ["src/store/**"]);
  assert.equal(replaced.items.find(item => item.id === "D-7").superseded_by, fresh.id);
  const added = applyDecisionChange(start, recordAddChange({ question: "Q?", answer: "A" }, APP));
  assert.equal(added.items.find(item => item.question === "Q?").remember_as, "record");
  const edited = applyDecisionChange({ items: [{ ...record, edited_outside: true }] }, { action: "edit", id: "D-7", check: "npm test" });
  assert.equal(edited.items[0].record.check, "npm test");
  assert.equal(edited.items[0].record.context, "Workers need shared state.");
  assert.equal("edited_outside" in edited.items[0], false);
});

const editCard = (fields = {}) => ({
  id: "E-2", decision: "D-7", action: "edit", project: APP, question: "How are workflows written?", status: "waiting",
  changes: [
    { field: "answer", label: "Decision", before: "Rattish only", after: "Rattish; TOML for imports" },
    { field: "consequences", label: "Consequences", before: "", after: "Importers keep TOML" },
  ],
  ...fields,
});

test("a change the user asked for shows each part's before and after, waiting for OK", () => {
  const card = editCard();
  assert.equal(editTitle(card), "Change D-7?");
  assert.equal(editTitle(editCard({ action: "replace" })), "Replace D-7?");
  assert.deepEqual(editChanges(card).map(change => [change.label, change.before, change.after]), [
    ["Decision", "Rattish only", "Rattish; TOML for imports"],
    ["Consequences", "(empty)", "Importers keep TOML"],
  ]);
  assert.deepEqual(editState(card), { waiting: true, line: "" });
});

test("a settled change says what happened instead of showing OK", () => {
  assert.deepEqual(editState(editCard({ status: "applied" })), { waiting: false, line: "Applied to D-7" });
  assert.equal(editState(editCard({ status: "applied", action: "replace", result: "D-9" })).line, "Applied: D-9 replaces D-7");
  assert.equal(editState(editCard({ status: "cancelled" })).line, "Cancelled. Nothing changed.");
  assert.equal(editState(editCard({ status: "replaced", replaced_by: "E-3" })).line, "Replaced by a newer change (E-3)");
  assert.equal(editState({}).waiting, false);
});

test("the Decisions list shows the project's waiting changes, and fixture mode settles them", () => {
  const view = { ...NO_DECISIONS, edits: [editCard(), editCard({ id: "E-4", project: OTHER })] };
  assert.deepEqual(waitingEdits(view, { project: APP }).map(edit => edit.id), ["E-2"]);
  assert.deepEqual(waitingEdits(view, { all: true }).map(edit => edit.id), ["E-2", "E-4"]);
  const state = { messages: [{ id: "reply-1", decision_edits: [editCard()] }, { id: "m" }], decisions: view };
  const settled = settleEditLocally(state, "E-2", "applied");
  assert.equal(settled.messages[0].decision_edits[0].status, "applied");
  assert.deepEqual(settled.decisions.edits.map(edit => edit.id), ["E-4"]);
  assert.equal(settled.messages[1], state.messages[1]);
});

test("Ask again promises a fresh look only where a project scan can give one", () => {
  for (const topic of SCAN_TOPICS) assert.match(askAgainTitle({ topic }), /reads the project's files again/);
  const plain = askAgainTitle({ topic: "deploy.target" });
  assert.doesNotMatch(plain, /fresh|files again/);
  assert.match(plain, /stays in force until you choose/);
  assert.equal(reopenedLine({ asked_again: true, fresh: true, reopened: "You asked again." }), "You asked again. Rem read the project's files again.");
  assert.equal(reopenedLine({ reopened: "AGENTS.md changed since it was decided." }), "Asked again: AGENTS.md changed since it was decided.");
  assert.equal(reopenedLine({}), "");
  assert.equal(currentAnswer({ current_answer: "make" }), "", "only an Ask again card shows a current answer");
  assert.equal(currentAnswer({ asked_again: true, current_answer: "make" }), "make");
});

test("Ask again is queued behind three waiting cards in the project", () => {
  const items = [
    ...[1, 2, 3].map(n => ({ id: `D-${n}`, project: APP, topic: `t${n}`, question: "Q?", status: "pending" })),
    { id: "D-4", project: APP, topic: "checks.fast", question: "Check?", answer: "make", status: "confirmed" },
  ];
  const after = applyDecisionChange({ revision: 1, items }, { action: "reopen", id: "D-4" });
  assert.equal(after.items.find(item => item.supersedes === "D-4").status, "queued");
  assert.equal(after.queued, 1);
  assert.equal(after.items.find(item => item.id === "D-4").status, "confirmed");
  const used = applyDecisionChange(after, { action: "change", id: after.items.find(item => item.supersedes === "D-4").id, answer: "make check" });
  const fresh = used.items.find(item => item.answer === "make check");
  assert.equal(fresh.asked_again, undefined);
  assert.equal(used.items.find(item => item.id === "D-4").status, "superseded");
});

test("plan questions group under the plan title with \"N of M answered\"", () => {
  const planned = [
    { id: "D-20", project: APP, project_name: "app", topic: "plan:store", question: "Where do offline notes live?", recommended: "SQLite", evidence: [], status: "pending", plan: "P-1" },
    { id: "D-21", project: APP, project_name: "app", topic: "plan:merge", question: "How do conflicts merge?", recommended: "Last write wins", evidence: [], status: "pending", plan: "P-1" },
    { id: "D-22", project: APP, project_name: "app", topic: "plan:port", question: "Which sync port?", recommended: "8443", evidence: [], status: "queued", plan: "P-1" },
    { id: "D-23", project: APP, project_name: "app", topic: "plan:errors", question: "Who sees sync errors?", recommended: "The user", evidence: [], status: "queued", plan: "P-1" },
    { id: "D-24", project: APP, project_name: "app", topic: "t24", question: "Which region?", recommended: "eu", evidence: [], status: "queued" },
    { id: "D-18", project: APP, project_name: "app", topic: "plan:metered", question: "Sync on metered data?", answer: "No", status: "confirmed", plan: "P-1" },
    { id: "D-30", project: OTHER, project_name: "site", topic: "plan:x", question: "Site plan question?", recommended: "Yes", evidence: [], status: "pending", plan: "P-2" },
  ];
  const listed = {
    ...view,
    items: [...items, ...planned],
    queued: 3,
    plans: [
      { id: "P-1", title: "Offline sync", goal: "Notes keep working offline", project: APP, status: "asking", answered: 1, total: 5, waiting: 2, queued: 2 },
      { id: "P-2", title: "Site launch", project: OTHER, status: "asking", answered: 0, total: 1, waiting: 1, queued: 0 },
      { id: "P-0", title: "Old plan", project: APP, status: "handed_back", answered: 2, total: 2, waiting: 0, queued: 0 },
    ],
  };
  const groups = planGroups(listed, { project: APP });
  assert.deepEqual(groups.map(group => group.plan.id), ["P-1"], "another project's plan and a handed-back plan aren't shown");
  assert.deepEqual(groups[0].waiting.map(item => item.id), ["D-20", "D-21"]);
  assert.equal(planLine(groups[0].plan), "1 of 5 answered");
  assert.equal(planQueuedLine(groups[0].plan), "2 more come up as you answer");
  assert.equal(planQueuedLine(listed.plans[1]), "");
  // The waiting question that belongs to no plan stays on its own, and only its queue shows under Waiting for you.
  const waiting = groupDecisions(listed, { project: APP }).waiting;
  assert.deepEqual(looseWaiting(waiting, groups).map(item => item.id), ["D-5"]);
  assert.equal(queuedLine(looseQueued(queuedCount(listed, { project: APP }), groups)), "1 more queued");
  // All projects shows both plans.
  assert.deepEqual(planGroups(listed, { all: true }).map(group => group.plan.id), ["P-1", "P-2"]);
  // A desktop without plans groups nothing.
  assert.deepEqual(planGroups({ items: planned }, { all: true }), []);
});

test("the asked line names who asked a waiting question, when, and what waits on it", async () => {
  const { askedLine, askedTitle, shortDay } = await import("./remDecisions.js");
  const run = label => ({ run_id: label, task_id: "t", ticket: label, label });
  const asked = { by: "run", label: "REM-1", ticket: "REM-1", task_id: "t", task_title: "Place work", at: "2026-11-21T12:00:00", waiting: [run("REM-1")], went_on: [], opens: "t" };
  assert.equal(shortDay("2026-11-21T12:00:00"), "21 Nov");
  assert.equal(askedLine({ asked }), "Asked by REM-1 · 21 Nov · REM-1 waits for this");
  assert.equal(askedLine({ asked: { ...asked, waiting: [run("REM-2"), run("REM-3")] } }), "Asked by REM-1 · 21 Nov · REM-2 and REM-3 wait for this");
  assert.equal(askedLine({ asked: { ...asked, waiting: [], went_on: [run("REM-1")] } }), "Asked by REM-1 · 21 Nov · REM-1 went on without it");
  assert.equal(askedLine({ asked: { label: "Project scan", at: "", waiting: [], went_on: [] } }), "Asked by Project scan");
  assert.equal(askedLine({}), "");
  assert.equal(askedTitle({ asked }), "Place work · Open the task");
});
