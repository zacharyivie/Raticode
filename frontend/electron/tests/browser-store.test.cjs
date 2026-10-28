const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createBrowserStore, historyKey } = require("../browser-store.cjs");

const DAY = 24 * 60 * 60 * 1000;

function storeAt(time = { value: 10 * DAY }) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "browser-store-"));
  return { directory, time, store: createBrowserStore(directory, { now: () => time.value, writeDelayMs: 5 }) };
}

test("history keeps one row per page and ranks suggestions by use", () => {
  const { store, time } = storeAt();
  store.recordVisit({ url: "https://github.com/raticode#readme", title: "Raticode" });
  store.recordVisit({ url: "https://github.com/raticode", title: "Raticode repo", typed: true });
  store.recordVisit({ url: "https://gitlab.com/", title: "GitLab" });
  store.recordVisit({ url: "file:///etc/passwd" });
  const history = store.searchHistory("");
  assert.deepEqual(history.map((item) => item.url), ["https://gitlab.com/", "https://github.com/raticode"]);
  assert.equal(history[1].visitCount, 2);
  assert.equal(history[1].typedCount, 1);
  assert.equal(history[1].title, "Raticode repo");
  assert.deepEqual(store.searchHistory("repo raticode").map((item) => item.url), ["https://github.com/raticode"]);
  assert.equal(store.suggest("git")[0].url, "https://github.com/raticode");
  assert.equal(store.inlineCompletion("gith"), "github.com/");
  assert.equal(store.inlineCompletion("github.com"), "");
  assert.equal(store.inlineCompletion("two words"), "");
  store.updateTitle("https://gitlab.com/", "GitLab home");
  assert.equal(store.searchHistory("home")[0].url, "https://gitlab.com/");
  time.value += 2 * 60 * 60 * 1000;
  store.recordVisit({ url: "https://example.com/" });
  store.clearHistory("hour");
  assert.equal(store.searchHistory("example").length, 0);
  assert.equal(store.searchHistory("").length, 2);
  store.removeHistory("https://gitlab.com/");
  assert.deepEqual(store.searchHistory("").map((item) => item.url), ["https://github.com/raticode"]);
  store.clearHistory("all");
  assert.equal(store.searchHistory("").length, 0);
});

test("bookmarks are unique web pages and appear in suggestions", () => {
  const { store } = storeAt();
  assert.throws(() => store.addBookmark({ url: "file:///tmp/x.html" }), /web pages/);
  const first = store.addBookmark({ url: "https://docs.example.com/guide#intro", title: "Guide" });
  assert.equal(store.addBookmark({ url: "https://docs.example.com/guide" }).id, first.id);
  assert.equal(store.bookmarkFor("https://docs.example.com/guide#other").id, first.id);
  assert.equal(store.suggest("guide")[0].kind, "bookmark");
  store.updateBookmark(first.id, { title: "  " });
  assert.equal(store.listBookmarks()[0].title, "https://docs.example.com/guide");
  assert.throws(() => store.updateBookmark(first.id, { url: "ftp://x" }), /http/);
  store.removeBookmark(first.id);
  assert.equal(store.listBookmarks().length, 0);
});

test("site settings drop empty values", () => {
  const { store } = storeAt();
  store.setPermission("https://meet.example.com", "camera", "allow");
  store.updateSite("https://meet.example.com", { zoom: 1.25 });
  assert.deepEqual(store.site("https://meet.example.com").permissions, { camera: "allow" });
  store.setPermission("https://meet.example.com", "camera", null);
  store.updateSite("https://meet.example.com", { zoom: null });
  assert.deepEqual(store.site("https://meet.example.com"), {});
  store.updateSite("https://a.example", { popups: "allow" });
  assert.equal(store.listSites().length, 1);
  store.clearSites();
  assert.equal(store.listSites().length, 0);
});

test("files are written privately and reload, with stale downloads interrupted", async () => {
  const { store, directory } = storeAt();
  store.recordVisit({ url: "https://example.com/", title: "Example" });
  store.upsertDownload({ id: "d1", state: "progressing", filename: "a.zip" });
  store.upsertDownload({ id: "d2", state: "completed", filename: "b.zip" });
  store.flush();
  if (process.platform !== "win32") assert.equal(fs.statSync(path.join(directory, "history.json")).mode & 0o777, 0o600);
  const reloaded = createBrowserStore(directory);
  assert.equal(reloaded.searchHistory("example").length, 1);
  assert.deepEqual(reloaded.listDownloads().map((item) => [item.id, item.state]), [["d2", "completed"], ["d1", "interrupted"]]);
  reloaded.clearDownloads();
  assert.equal(reloaded.listDownloads().length, 0);
  // Corrupt files start empty instead of breaking the browser.
  fs.writeFileSync(path.join(directory, "bookmarks.json"), "{nope");
  assert.deepEqual(createBrowserStore(directory).listBookmarks(), []);
});

test("history keys strip fragments and reject other schemes", () => {
  assert.equal(historyKey("https://a.test/x#y"), "https://a.test/x");
  assert.equal(historyKey("about:blank"), "");
  assert.equal(historyKey("not a url"), "");
});
