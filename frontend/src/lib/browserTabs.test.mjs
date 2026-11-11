import assert from "node:assert/strict";
import test from "node:test";
import { browserTabEngine, browserTabOpening, geckoTabPath, viewedBrowserMedia, withBrowserTabEngines } from "./browserTabs.js";
import { WEB_BROWSER_OPTIONS, controlledEngine, engineBadge, engineLabel, newTabEngine, webBrowserEngine } from "./browserEngines.js";
import { mediaForBar } from "./browserMedia.js";

test("the integrated browsers are Raticode (chromium) and Raticode (gecko), and stored values don't change", () => {
  assert.deepEqual(WEB_BROWSER_OPTIONS, [["raticode", "Raticode (chromium)"], ["gecko", "Raticode (gecko)"], ["firefox", "Firefox"], ["chrome", "Chrome"], ["edge", "Edge"]]);
  assert.equal(engineLabel("raticode"), "Raticode (chromium)");
  assert.equal(engineLabel("gecko"), "Raticode (gecko)");
  assert.equal(engineLabel("chrome"), "Chrome");
  assert.equal(engineLabel("netscape"), "Raticode (chromium)");
  assert.equal(engineBadge("raticode"), "chromium");
  assert.equal(engineBadge("gecko"), "gecko");
  assert.equal(engineBadge("edge"), "Edge");
  // Saved choices from before the rename keep meaning the same browser.
  for (const value of ["raticode", "gecko", "firefox", "chrome", "edge"]) assert.equal(webBrowserEngine(value), value);
  assert.equal(controlledEngine("gecko"), "raticode");
});

test("a new tab opens in the browser chosen for it, the default otherwise, and private tabs in Raticode (chromium)", () => {
  assert.equal(newTabEngine("", "edge"), "edge");
  assert.equal(newTabEngine("chrome", "edge"), "chrome");
  assert.equal(newTabEngine("gecko", "raticode"), "gecko");
  assert.equal(newTabEngine("chrome", "edge", { privateTab: true }), "raticode");
  assert.equal(newTabEngine("bogus", "bogus"), "raticode");
});

test("tabs saved before per-tab browsers keep the browser that showed them", () => {
  const paths = ["a", "b", "rem", "private"];
  const tabs = {
    a: { url: "https://a.test/" },
    b: { url: "https://b.test/", engine: "chrome" },
    rem: { url: "about:blank", agentSessionId: "s1", agentEngine: "edge" },
    private: { url: "https://p.test/", private: true },
  };
  const migrated = withBrowserTabEngines(paths, tabs, "firefox");
  assert.deepEqual(migrated.paths, paths);
  assert.equal(migrated.tabs.a.engine, "firefox");
  assert.equal(migrated.tabs.b.engine, "chrome");
  assert.equal(migrated.tabs.rem.engine, "edge");
  assert.equal(migrated.tabs.private.engine, "raticode");
  assert.equal(migrated.tabs.b, tabs.b, "tabs that already name their browser are kept as they are");
  assert.equal(browserTabEngine({ agentSessionId: "x" }, "gecko"), "raticode", "Rem never ran in Gecko");
});

test("one Raticode (gecko) tab is kept, and the other Gecko tabs' pages open inside it", () => {
  const paths = ["g1", "c", "g2", "g3", "g4"];
  const tabs = {
    g1: { url: "https://music.test/" },
    c: { url: "https://docs.test/", engine: "raticode" },
    g2: { url: "https://news.test/" },
    g3: { url: "about:blank" },
    g4: { url: "https://music.test/" },
  };
  const migrated = withBrowserTabEngines(paths, tabs, "gecko");
  assert.deepEqual(migrated.paths, ["g1", "c"]);
  assert.equal(migrated.tabs.g1.engine, "gecko");
  assert.deepEqual(migrated.tabs.g1.pendingUrls, ["https://news.test/"]);
  assert.equal(geckoTabPath(migrated.paths, migrated.tabs), "g1");
});

test("opening Raticode (gecko) again reuses its tab; other browsers always get a new tab", () => {
  const paths = ["g", "c"];
  const tabs = { g: { engine: "gecko" }, c: { engine: "chrome" } };
  assert.deepEqual(browserTabOpening({ paths, tabs, engine: "gecko" }), { engine: "gecko", reuse: "g" });
  assert.deepEqual(browserTabOpening({ paths, tabs, defaultEngine: "gecko" }), { engine: "gecko", reuse: "g" });
  assert.deepEqual(browserTabOpening({ paths, tabs, engine: "chrome" }), { engine: "chrome", reuse: "" });
  assert.deepEqual(browserTabOpening({ paths: ["c"], tabs, engine: "gecko" }), { engine: "gecko", reuse: "" });
  assert.deepEqual(browserTabOpening({ paths, tabs, engine: "gecko", privateTab: true }), { engine: "raticode", reuse: "" });
});

test("the media bar hides Gecko's player only while the Gecko tab is on screen", () => {
  const tabs = { g: { engine: "gecko" }, c: { engine: "chrome" } };
  const gecko = { id: "gecko", playing: true, startedAt: 5, title: "Song in Gecko" };
  const chrome = { id: "s1", clientId: "c", playing: true, startedAt: 9, title: "Video in Chrome" };
  // Viewing the Chrome tab: its own player is in view, Gecko's music is not.
  assert.equal(mediaForBar([chrome], gecko, viewedBrowserMedia(["c"], tabs)).id, "gecko");
  // Viewing the Gecko tab: Firefox's player is in view, the bar speaks for Chrome.
  assert.deepEqual(viewedBrowserMedia(["g"], tabs), ["g", "gecko"]);
  assert.equal(mediaForBar([chrome], gecko, viewedBrowserMedia(["g"], tabs)).id, "s1");
  // Split view with both: nothing to show.
  assert.equal(mediaForBar([chrome], gecko, viewedBrowserMedia(["g", "c"], tabs)), null);
  // Elsewhere in the app, both remain reachable.
  assert.equal(mediaForBar([chrome], gecko, viewedBrowserMedia([], tabs)).id, "s1");
});
