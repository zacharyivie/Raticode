import assert from "node:assert/strict";
import test from "node:test";
import { mediaForBar, viewedEditorPaths, viewedMediaClients } from "./browserMedia.js";

const playing = (clientId, startedAt, extra = {}) => ({ id: `session-${clientId}`, clientId, playing: true, startedAt, title: clientId, ...extra });
const paused = (clientId, startedAt) => ({ ...playing(clientId, startedAt), playing: false });

test("the media bar stays away while the playing tab is the one on screen", () => {
  assert.equal(mediaForBar([playing("browser:1", 10)], null, ["browser:1"]), null);
  assert.equal(mediaForBar([playing("browser:1", 10)], null, []).clientId, "browser:1", "on another area it shows");
  assert.equal(mediaForBar([playing("browser:1", 10)], null, ["/project/notes.md"]).clientId, "browser:1", "on another tab it shows");
});

test("with several tabs playing, the bar speaks for one that isn't on screen", () => {
  const sessions = [playing("browser:1", 30), playing("browser:2", 20), paused("browser:3", 40)];
  assert.equal(mediaForBar(sessions, null, []).clientId, "browser:1", "newest playing first");
  assert.equal(mediaForBar(sessions, null, ["browser:1"]).clientId, "browser:2", "the next playing tab");
  assert.equal(mediaForBar(sessions, null, ["browser:1", "browser:2"]).clientId, "browser:3", "a paused tab in its hold keeps Play reachable");
  assert.equal(mediaForBar(sessions, null, ["browser:1", "browser:2", "browser:3"]), null, "split view shows both groups' tabs");
});

test("a paused tab on screen stays hidden and comes back once the user looks away", () => {
  const sessions = [paused("browser:1", 10)];
  assert.equal(mediaForBar(sessions, null, ["browser:1"]), null);
  assert.equal(mediaForBar(sessions, null, ["browser:2"]).playing, false);
});

test("a local page preview is matched by its own session name", () => {
  assert.equal(mediaForBar([playing("html:/project/index.html", 10)], null, ["/project/index.html"]), null);
  assert.ok(viewedMediaClients(["/a.html"]).has("html:/a.html"));
});

test("Gecko's player and sessions without a tab name are never treated as in view", () => {
  const gecko = { id: "gecko", playing: true, startedAt: 5, title: "Firefox song" };
  assert.equal(mediaForBar([], gecko, ["browser:1"]).id, "gecko");
  assert.equal(mediaForBar([{ ...playing("", 9), clientId: "" }], null, [""]).id, "session-");
  assert.equal(mediaForBar([playing("browser:1", 10)], gecko, ["browser:1"]).id, "gecko");
});

test("only the editor's selected tabs count, and only while the editor is shown", () => {
  assert.deepEqual(viewedEditorPaths(true, ["browser:1", ""]), ["browser:1"]);
  assert.deepEqual(viewedEditorPaths(true, ["browser:1", "browser:1"]), ["browser:1"]);
  assert.deepEqual(viewedEditorPaths(false, ["browser:1", "browser:2"]), [], "Rem, the board or another area is in front");
  assert.equal(mediaForBar(null, null, null), null);
});
