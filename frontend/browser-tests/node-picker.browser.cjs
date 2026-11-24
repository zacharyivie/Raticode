/* global __dirname, process, console, clearTimeout, setTimeout, document, window */
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
const { app, BrowserWindow } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-dev-shm-usage");
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-setuid-sandbox");
process.chdir(path.resolve(__dirname, ".."));
const deadline = setTimeout(() => app.exit(1), 90000);
// Set NODE_PICKER_SCREENSHOTS to a folder to keep screenshots of the picker.
const screenshotDir = process.env.NODE_PICKER_SCREENSHOTS || "";

// The installed contracts' catalog entries, shaped like the editor document sends them.
function installedContracts() {
  const root = path.resolve(__dirname, "../../rattish/contracts");
  return fs.readdirSync(root).filter((name) => name.endsWith(".json")).sort().map((name) => {
    const contract = JSON.parse(fs.readFileSync(path.join(root, name), "utf8"));
    return {
      nodeType: contract.node_type,
      configurationSchema: contract.configuration_schema,
      defaults: contract.defaults ?? {},
      category: contract.catalog?.category ?? "other",
      summary: contract.catalog?.summary ?? "",
      docsUrl: `https://raticode.dev${contract.catalog?.docs_path ?? `/docs/nodes/${contract.node_type}`}`,
    };
  });
}

let server;
app.whenReady().then(async () => {
  const contracts = installedContracts();
  const { createServer } = await import("vite");
  server = await createServer({ cacheDir: testViteCacheDir(), root: path.resolve(__dirname, ".."), configFile: false, server: { host: "127.0.0.1", port: 0 }, plugins: [
    (await import("@vitejs/plugin-react")).default(),
    { name: "node-picker-fixture", configureServer(dev) {
      dev.middlewares.use(async (req, res, next) => {
        if (req.url === "/node-picker-contracts.json") {
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify(contracts));
          return;
        }
        if (req.url !== "/") return next();
        res.setHeader("Content-Type", "text/html");
        res.end(await dev.transformIndexHtml("/", '<html><body><div id="root"></div><script type="module" src="/browser-tests/node-picker.jsx"></script></body></html>'));
      });
    } },
  ] });
  await server.listen();
  // Offscreen rendering paints to a bitmap, so input and screenshots work with or without a display.
  const win = new BrowserWindow({ width: 1280, height: 860, show: false, webPreferences: { offscreen: true, sandbox: false, contextIsolation: true } });
  win.webContents.setFrameRate(30);
  const errors = [];
  win.webContents.on("console-message", ({ level, message }) => { if (level === "error") errors.push(message); });
  // External links must not leave the test window.
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  await win.loadURL(server.resolvedUrls.local[0]);
  const evaluate = (fn) => win.webContents.executeJavaScript(`Promise.resolve((${fn})()).then(value => value instanceof Node || value instanceof NodeList ? true : value)`);
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const until = async (fn, message) => {
    for (let i = 0; i < 100; i++) {
      if (await evaluate(fn)) return;
      await wait(50);
    }
    assert.fail(message);
  };
  const key = async (keyCode, modifiers = []) => {
    win.webContents.sendInputEvent({ type: "keyDown", keyCode, modifiers });
    if (keyCode.length === 1) win.webContents.sendInputEvent({ type: "char", keyCode, modifiers });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode, modifiers });
    await wait(60);
  };
  const type = async (text) => { for (const character of text) await key(character); };
  const click = async (selectorFn) => {
    const box = await evaluate(selectorFn);
    assert.ok(box, `Nothing to click for ${selectorFn}`);
    const x = Math.round(box.x + box.width / 2);
    const y = Math.round(box.y + box.height / 2);
    win.webContents.sendInputEvent({ type: "mouseDown", x, y, button: "left", clickCount: 1 });
    win.webContents.sendInputEvent({ type: "mouseUp", x, y, button: "left", clickCount: 1 });
    await wait(120);
  };
  const shot = async (name) => {
    if (!screenshotDir) return;
    fs.mkdirSync(screenshotDir, { recursive: true });
    fs.writeFileSync(path.join(screenshotDir, `${name}.png`), (await win.webContents.capturePage()).toPNG());
  };

  await until(() => document.querySelectorAll("article").length === 2, "The fixture graph must render its two nodes");
  win.focus();
  win.webContents.focus();

  await shot("node-picker-before");
  // Selecting a node focuses the graph, and a real Tab opens the picker on its search box.
  await click(() => [...document.querySelectorAll("article")].find((node) => node.textContent.includes("prepare")).getBoundingClientRect().toJSON());
  const focusState = await evaluate(() => ({
    active: document.activeElement?.getAttribute("data-graph-visualization") ?? document.activeElement?.tagName,
    selected: document.querySelector("[data-node-type-field]") ? "inspector" : "none",
    hasFocus: document.hasFocus(),
    viewport: [window.innerWidth, window.innerHeight],
    nodes: [...document.querySelectorAll("article")].map((node) => node.getBoundingClientRect().toJSON()),
  }));
  assert.equal(focusState.active, "true", `Clicking a node must focus the graph ${JSON.stringify(focusState)}`);
  await key("Tab");
  await until(() => document.querySelector("[data-node-picker]"), "Tab on the graph must open the node picker");
  assert.equal(await evaluate(() => document.activeElement?.getAttribute("role")), "combobox", "The picker must focus its search box");

  const layout = await evaluate(() => {
    const picker = document.querySelector("[data-node-picker]");
    const box = picker.getBoundingClientRect();
    const list = picker.querySelector("[role=listbox]");
    return {
      inside: box.left >= 0 && box.top >= 0 && box.right <= window.innerWidth && box.bottom <= window.innerHeight,
      horizontalScroll: list.scrollWidth > list.clientWidth,
      scrolls: list.scrollHeight > list.clientHeight,
      groups: [...picker.querySelectorAll("[role=group] > div:first-child")].map((heading) => heading.textContent),
      options: [...picker.querySelectorAll("[role=option]")].map((option) => ({
        type: option.getAttribute("data-node-type"),
        summary: option.querySelector(".truncate[title]")?.textContent ?? "",
        docs: option.parentElement.querySelector("a").href,
      })),
    };
  });
  assert.ok(layout.inside, "The picker must fit inside the window");
  assert.equal(layout.horizontalScroll, false, "Picker rows must not scroll sideways");
  assert.ok(layout.scrolls, "A long list scrolls inside the picker");
  assert.deepEqual(layout.groups, ["Run code", "Files", "AI", "Web", "Approvals and notices", "Flow control", "Other workflows"]);
  assert.equal(layout.options.length, contracts.length, "Every installed contract must be listed");
  for (const option of layout.options) {
    assert.ok(option.summary, `${option.type} needs a one-line description`);
    assert.equal(option.docs, `https://raticode.dev/docs/nodes/${option.type}`);
  }
  await shot("node-picker-open");

  await type("web page");
  await until(() => document.querySelectorAll("[role=option]").length === 1, "Searching must narrow the list");
  assert.equal(await evaluate(() => document.querySelector("[role=option]").getAttribute("data-node-type")), "read-web-page");
  await shot("node-picker-search");
  await key("Return");
  await until(() => document.querySelectorAll("article").length === 3, "Enter must add the chosen node");
  assert.deepEqual(await evaluate(() => window.mutations.at(-1)), [
    { kind: "set_routes", node: "prepare", routes: ["review", "read-web-page-1"] },
    { kind: "add_node", node: "read-web-page-1", node_type: "read-web-page" },
  ]);
  await until(() => window.positions["read-web-page-1"], "The new node's position must be saved to metadata");
  const positions = await evaluate(() => window.positions);
  assert.equal(positions["read-web-page-1"].x, positions.prepare.x + 330, "The new node goes right of the selected node");
  assert.notEqual(positions["read-web-page-1"].y, positions.review.y, "The new node must not cover the node already there");
  assert.equal(await evaluate(() => document.activeElement?.getAttribute("data-graph-visualization")), "true", "Focus returns to the graph");
  await shot("node-picker-added");

  // The inspector's Type field opens the same picker, marked with the current type.
  await click(() => document.querySelector("[data-node-type-field]").getBoundingClientRect().toJSON());
  await until(() => document.querySelector("[data-node-picker][aria-label='Change node type']"), "The Type field must open the picker");
  const inspectorLayout = await evaluate(() => {
    const picker = document.querySelector("[data-node-picker]").getBoundingClientRect();
    const inspector = document.getElementById("workflow-inspector")?.getBoundingClientRect() ?? picker;
    return { inside: picker.left >= inspector.left - 1 && picker.right <= inspector.right + 1, current: document.querySelector("[data-node-picker]").textContent.includes("Current") };
  });
  assert.ok(inspectorLayout.inside, "The Type picker must fit inside the inspector");
  assert.ok(inspectorLayout.current, "The Type picker marks the current type");
  await type("loop");
  await shot("node-picker-type-field");
  await key("Return");
  await until(() => window.mutations.at(-1)?.[0]?.kind === "change_node_type", "Choosing a type must change it");
  assert.deepEqual(await evaluate(() => window.mutations.at(-1)), [{ kind: "change_node_type", node: "read-web-page-1", node_type: "loop" }]);

  // Escape closes the picker without a change.
  await click(() => document.querySelector("[title='Add node']").getBoundingClientRect().toJSON());
  await until(() => document.querySelector("[data-node-picker]"), "Add node must open the picker");
  const before = await evaluate(() => window.mutations.length);
  await key("Escape");
  await until(() => !document.querySelector("[data-node-picker]"), "Escape must close the picker");
  assert.equal(await evaluate(() => window.mutations.length), before);

  const unexpected = errors.filter((message) => !/Failed to load resource|fetch/i.test(message));
  assert.deepEqual(unexpected, []);
  console.log("Node picker: Tab, search, grouped contracts, routing, placement and the Type field passed.");
  win.destroy(); await server.close(); clearTimeout(deadline); app.quit();
}).catch(async (error) => { console.error(error); if (server) await server.close(); app.exit(1); });
