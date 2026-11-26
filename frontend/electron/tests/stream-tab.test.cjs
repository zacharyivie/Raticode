// The code Chrome, Edge and Firefox tabs share (stream-tab.cjs), run against
// fake browser engines so it needs no real browser: one copy of it, the find
// bar, the input queue and Rem's site rules on a tab that restarts.
const test = require("node:test");
const assert = require("node:assert/strict");
const { StreamTab } = require("../stream-tab.cjs");
const { ChromiumContents } = require("../chromium-contents.cjs");
const { FirefoxContents } = require("../firefox-contents.cjs");

// A DevTools session that answers what ChromiumContents asks while starting,
// and `evaluate` for every Runtime.evaluate.
function fakeChromium(evaluate = () => undefined) {
  const calls = [];
  let started = 0;
  return {
    calls,
    browser: { id: "chrome", name: "Chrome" },
    async createContext() {
      started += 1;
      return { targetId: `target-${started}`, sessionId: `session-${started}`, generation: started, userAgent: "Chrome/1" };
    },
    register() {},
    unregister() {},
    async send(method, params = {}) {
      calls.push([method, params]);
      if (method === "Page.getFrameTree") return { frameTree: { frame: { id: "main", loaderId: "loader" } } };
      if (method === "Page.createIsolatedWorld") return { executionContextId: 7 };
      if (method === "Runtime.evaluate") return { result: { value: evaluate(params.expression) } };
      return {};
    },
  };
}

// A WebDriver BiDi connection that does the same for FirefoxContents.
function fakeFirefox(evaluate = () => undefined) {
  const calls = [];
  let started = 0;
  const remote = (value) => {
    if (value === undefined) return { type: "undefined" };
    if (value === null) return { type: "null" };
    if (typeof value === "object") return { type: "object", value: Object.entries(value).map(([key, item]) => [key, remote(item)]) };
    return { type: typeof value, value };
  };
  return {
    calls,
    async createContext() {
      started += 1;
      return { context: `context-${started}`, generation: started };
    },
    register() {},
    unregister() {},
    async send(method, params = {}) {
      calls.push([method, params]);
      if (method === "script.evaluate") return { type: "success", result: remote(evaluate(params.expression)) };
      return {};
    },
  };
}

const ENGINES = [
  {
    name: "Chrome",
    create: (evaluate) => {
      const engine = fakeChromium(evaluate);
      return { engine, contents: new ChromiumContents({ engine, kind: "rem", platform: "linux" }) };
    },
    guard: "Fetch.enable",
    moves: (calls) => calls.filter(([method, params]) => method === "Input.dispatchMouseEvent" && params.type === "mouseMoved"),
    keys: (calls) => calls.filter(([method]) => method === "Input.dispatchKeyEvent"),
  },
  {
    name: "Firefox",
    create: (evaluate) => {
      const engine = fakeFirefox(evaluate);
      return { engine, contents: new FirefoxContents({ engine, kind: "rem" }) };
    },
    guard: "network.addIntercept",
    moves: (calls) => calls.filter(([method, params]) => method === "input.performActions" && params.actions[0].type === "pointer"),
    keys: (calls) => calls.filter(([method, params]) => method === "input.performActions" && params.actions[0].type === "key"),
  },
];

test("Chrome and Firefox tabs share one copy of the code that needs no browser protocol", () => {
  const shared = ["fail", "scheduleStateRefresh", "setAudioMuted", "settleWaiters", "waitForNavigation", "findInPage", "stopFindInPage", "handleInput", "enqueue", "userInput", "copySelection", "reportCursor", "reportSelect", "chooseOption", "alive", "markDead", "close", "input"];
  for (const name of shared) assert.equal(typeof StreamTab.prototype[name], "function", `StreamTab.${name}`);
  for (const Tab of [ChromiumContents, FirefoxContents]) {
    assert.equal(Object.getPrototypeOf(Tab), StreamTab, `${Tab.name} extends StreamTab`);
    for (const name of shared) assert.ok(!Object.hasOwn(Tab.prototype, name), `${Tab.name} defines its own ${name}`);
  }
});

for (const browser of ENGINES) {
  test(`a ${browser.name} tab that restarts keeps Rem's site rules`, async () => {
    const { engine, contents } = browser.create();
    contents.guardNavigation = true;
    await contents.ready;
    assert.equal(engine.calls.filter(([method]) => method === browser.guard).length, 1);
    contents.markDead("The tab crashed");
    await assert.rejects(contents.alive(), /Reload the tab/);
    await contents.revive();
    assert.equal(engine.calls.filter(([method]) => method === browser.guard).length, 2, "the restarted page has no site rules");
    contents.close();
  });

  test(`a ${browser.name} tab counts find matches and steps through them`, async () => {
    const { contents } = browser.create((expression) => (expression.includes("window.find") ? { matches: 3, found: true } : undefined));
    await contents.ready;
    const found = (options) => new Promise((resolve) => {
      contents.once("found-in-page", (_event, result) => resolve(result));
      contents.findInPage("word", options);
    });
    assert.deepEqual(await found(), { requestId: 1, matches: 3, activeMatchOrdinal: 1, finalUpdate: true });
    assert.equal((await found({ findNext: false })).activeMatchOrdinal, 2);
    assert.equal((await found({ findNext: false, forward: false })).activeMatchOrdinal, 1);
    contents.close();
  });

  test(`a ${browser.name} tab sends only the latest of queued pointer moves, in order with keys`, async () => {
    const { engine, contents } = browser.create();
    await contents.ready;
    contents.setView({ width: 800, height: 600, scale: 1 }, false);
    const events = [];
    contents.on("input-event", (_event, input) => events.push(input.type));
    contents.handleInput([{ t: "move", x: 1, y: 1 }, { t: "move", x: 2, y: 2 }, { t: "move", x: 30, y: 40 }, { t: "keydown", key: "a" }, { t: "keyup", key: "a" }, { t: "keyup", key: "b" }]);
    await contents.inputQueue;
    const moves = browser.moves(engine.calls);
    assert.equal(moves.length, 1);
    assert.match(JSON.stringify(moves[0][1]), /"x":30,"y":40/);
    assert.equal(browser.keys(engine.calls).length, 2, "a key that was never pressed is not released");
    assert.deepEqual(events, ["keyDown"]);
    assert.ok(engine.calls.indexOf(moves[0]) < engine.calls.indexOf(browser.keys(engine.calls)[0]));
    contents.close();
  });
}
