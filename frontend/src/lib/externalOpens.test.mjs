import assert from "node:assert/strict";
import test from "node:test";
import { EXTERNAL_ENGINE_OPTIONS, externalEngine, externalOpenPlan, geckoUnavailableReason } from "./externalOpens.js";
import { normalizeAppSettings } from "./settings.js";

const link = { kind: "url", url: "https://example.com/" };
const gecko = { available: true, executable: "/usr/bin/firefox", problem: "" };

test("links from other apps open in Raticode (chromium) unless Raticode (gecko) is chosen", () => {
  assert.deepEqual(EXTERNAL_ENGINE_OPTIONS.map(([value]) => value), ["raticode", "gecko"]);
  assert.equal(externalEngine(undefined), "raticode");
  assert.equal(externalEngine("firefox"), "raticode");
  assert.deepEqual(externalOpenPlan(link), { kind: "url", url: "https://example.com/", engine: "raticode", notice: "" });
  assert.deepEqual(externalOpenPlan(link, { engine: "gecko", geckoStatus: gecko }), { kind: "url", url: "https://example.com/", engine: "gecko", notice: "" });
});

test("a second Raticode (gecko) link joins its one Firefox window, and says so", () => {
  const plan = externalOpenPlan(link, { engine: "gecko", geckoStatus: gecko, geckoTabOpen: true });
  assert.equal(plan.engine, "gecko");
  assert.match(plan.notice, /one Firefox window/);
});

test("when Raticode (gecko) can't run, the link opens in Raticode (chromium) with the reason", () => {
  for (const [status, reason] of [
    [null, /didn't answer/],
    [{ problem: "Raticode (gecko) needs Linux.", executable: "" }, /needs Linux, so the link opened in Raticode \(chromium\)/],
    [{ problem: "", needsRestart: true, executable: "/usr/bin/firefox" }, /restart/],
    [{ problem: "", executable: "" }, /Firefox isn't installed/],
  ]) {
    const plan = externalOpenPlan(link, { engine: "gecko", geckoStatus: status });
    assert.equal(plan.engine, "raticode");
    assert.match(plan.notice, reason);
  }
  assert.equal(geckoUnavailableReason(gecko), "");
});

test("web-page files open in the file preview, and anything else is ignored", () => {
  assert.deepEqual(externalOpenPlan({ kind: "file", path: "/home/me/page.html" }, { engine: "gecko", geckoStatus: gecko }), { kind: "file", path: "/home/me/page.html" });
  assert.equal(externalOpenPlan({ kind: "url", url: "javascript:alert(1)" }), null);
  assert.equal(externalOpenPlan({ kind: "file", path: "" }), null);
  assert.equal(externalOpenPlan(null), null);
});

test("the setting defaults to Raticode (chromium) and keeps only the two built-in browsers", () => {
  assert.equal(normalizeAppSettings({}).browser.externalEngine, "raticode");
  assert.equal(normalizeAppSettings({ browser: { externalEngine: "gecko" } }).browser.externalEngine, "gecko");
  assert.equal(normalizeAppSettings({ browser: { externalEngine: "chrome" } }).browser.externalEngine, "raticode");
});
