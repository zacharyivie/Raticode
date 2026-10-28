import test from "node:test";
import assert from "node:assert/strict";
import {
  addressWithCompletion,
  downloadStatus,
  formatBytes,
  formatZoom,
  historyMenuEntries,
  pendingRemApprovals,
  relativeTime,
  suggestionRows,
  trimHistoryForSession,
} from "./browserUi.js";
import { browserChromeShortcutAction } from "./browserShortcuts.js";

test("sizes and zoom levels read naturally", () => {
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(25 * 1024 * 1024), "25 MB");
  assert.equal(formatBytes(-1), "");
  assert.equal(formatZoom(1.25), "125%");
  assert.equal(formatZoom(undefined), "100%");
});

test("download status explains each state", () => {
  assert.deepEqual(downloadStatus({ state: "progressing", receivedBytes: 512, totalBytes: 1024 }), { percent: 50, label: "512 B of 1.0 KB" });
  assert.match(downloadStatus({ state: "progressing", paused: true, receivedBytes: 1 }).label, /^Paused/);
  assert.match(downloadStatus({ state: "quarantined" }).label, /harm your computer/);
  assert.equal(downloadStatus({ state: "interrupted" }).label, "Failed — interrupted");
});

test("inline completion keeps the typed prefix and selects the rest", () => {
  assert.deepEqual(addressWithCompletion("Gi", "github.com/"), { value: "Github.com/", selectionStart: 2, selectionEnd: 11 });
  assert.deepEqual(addressWithCompletion("gx", "github.com/"), { value: "gx", selectionStart: 2, selectionEnd: 2 });
  assert.deepEqual(addressWithCompletion("github.com/", "github.com/"), { value: "github.com/", selectionStart: 11, selectionEnd: 11 });
});

test("suggestions put the Enter action first, then tabs, then history", () => {
  const rows = suggestionRows({
    text: "docs",
    openTabs: [
      { path: "tab-a", url: "https://docs.example.com/", title: "Docs" },
      { path: "current", url: "https://docs.example.com/self", title: "Docs here" },
      { path: "file", url: "file:///docs.html", title: "Docs file" },
    ],
    suggestions: [
      { url: "https://docs.example.com/", title: "Docs", kind: "history" },
      { url: "https://example.com/docs", title: "", bookmark: true },
    ],
    currentPath: "current",
  });
  assert.deepEqual(rows.map((row) => [row.kind, row.value]), [
    ["search", "docs"],
    ["tab", "https://docs.example.com/"],
    ["bookmark", "https://example.com/docs"],
  ]);
  assert.equal(suggestionRows({ text: "example.com" })[0].kind, "navigate");
  assert.deepEqual(suggestionRows({ text: "  " }), []);
});

test("approvals are listed per tab for the strip and banner", () => {
  assert.deepEqual(pendingRemApprovals({
    a: { prompts: [{ kind: "permission", requestId: "p" }, { kind: "rem-confirm", requestId: "r", label: "Send", agentLabel: "Rem" }] },
    b: {},
  }), [{ path: "a", requestId: "r", label: "Send", agentLabel: "Rem" }]);
});

test("saved history is bounded and keeps the active page", () => {
  const entries = Array.from({ length: 40 }, (_value, index) => ({ url: `https://e.test/${index}`, title: `${index}`, pageState: "x".repeat(10) }));
  const trimmed = trimHistoryForSession({ entries, index: 30 });
  assert.equal(trimmed.entries.length, 25);
  assert.equal(trimmed.entries[trimmed.index].url, "https://e.test/30");
  const large = trimHistoryForSession({ entries: [{ url: "https://e.test/", pageState: "x".repeat(20000) }], index: 0 });
  assert.equal("pageState" in large.entries[0], false);
  assert.equal(trimHistoryForSession({ entries: [{ url: "about:blank" }], index: 0 }), null);
  assert.equal(trimHistoryForSession(null), null);
});

test("back and forward menus list the nearest pages first", () => {
  const history = { index: 2, entries: [{ url: "a" }, { url: "b" }, { url: "c" }, { url: "d" }] };
  assert.deepEqual(historyMenuEntries(history, -1).map((entry) => entry.index), [1, 0]);
  assert.deepEqual(historyMenuEntries(history, 1).map((entry) => entry.index), [3]);
});

test("relative times stay short", () => {
  assert.equal(relativeTime(1000, 1000), "just now");
  assert.equal(relativeTime(0, 5 * 60 * 1000), "5 min ago");
  assert.equal(relativeTime(0, 26 * 60 * 60 * 1000), "yesterday");
});

test("browser chrome takes standard keys only while focus is inside it", () => {
  const event = (patch) => ({ key: "", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, target: null, ...patch });
  assert.equal(browserChromeShortcutAction(event({ ctrlKey: true, key: "l" }), "linux"), "");
  assert.equal(browserChromeShortcutAction(event({ ctrlKey: true, key: "l" }), "linux", { scoped: true }), "focus-location");
  assert.equal(browserChromeShortcutAction(event({ key: "F5" }), "linux", { scoped: true }), "reload");
  assert.equal(browserChromeShortcutAction(event({ ctrlKey: true, shiftKey: true, key: "R" }), "linux", { scoped: true }), "reload-hard");
  assert.equal(browserChromeShortcutAction(event({ key: "F12" }), "linux", { scoped: true }), "toggle-devtools");
  assert.equal(browserChromeShortcutAction(event({ metaKey: true, key: "p" }), "darwin", { scoped: true }), "print");
  assert.equal(browserChromeShortcutAction(event({ ctrlKey: true, key: "=" }), "linux", { scoped: true }), "zoom-in");
  assert.equal(browserChromeShortcutAction(event({ ctrlKey: true, shiftKey: true, key: "T" }), "linux", { scoped: true }), "reopen-closed-tab");
});
