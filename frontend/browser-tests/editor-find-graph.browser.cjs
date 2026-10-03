/* global __dirname, process, console, clearTimeout, setTimeout, document, window, navigator, KeyboardEvent, Event */
const { app, BrowserWindow } = require("electron");
const assert = require("node:assert/strict");
const path = require("node:path");
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-dev-shm-usage");
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-setuid-sandbox");
process.chdir(path.resolve(__dirname, ".."));
const deadline = setTimeout(() => app.exit(1), 60000);
let server;
app.whenReady().then(async () => {
  const { createServer } = await import("vite");
  server = await createServer({ root: path.resolve(__dirname, ".."), configFile: false, server: { host: "127.0.0.1", port: 0 }, plugins: [
    (await import("@vitejs/plugin-react")).default(),
    { name: "find-graph-fixture", configureServer(dev) {
      dev.middlewares.use(async (req, res, next) => {
        if (req.url !== "/") return next();
        res.setHeader("Content-Type", "text/html");
        res.end(await dev.transformIndexHtml("/", '<html><body><div id="root"></div><script type="module" src="/browser-tests/editor-find-graph.jsx"></script></body></html>'));
      });
    } },
  ] });
  await server.listen();
  const win = new BrowserWindow({ width: 1280, height: 1000, show: true, webPreferences: { sandbox: false, contextIsolation: true } });
  const errors = [];
  win.webContents.on("console-message", ({ level, message }) => { if (level === "error") errors.push(message); });
  await win.loadURL(server.resolvedUrls.local[0]);
  const evaluate = fn => win.webContents.executeJavaScript(`(${fn})()`);
  const verifyContrast = async () => {
    const { colors, background } = await evaluate(() => {
      const graph = document.querySelector(".scm-history-graph");
      const probe = document.createElement("span"); graph.appendChild(probe);
      const color = variable => { probe.style.color = `var(${variable})`; return window.getComputedStyle(probe).color; };
      const colors = [0, 1, 2, 3].map(index => color(`--scm-lane-${index}`));
      const background = color("--scm-graph-background"); probe.remove();
      return { colors, background };
    });
    const luminance = rgb => rgb.match(/[\d.]+/g).slice(0, 3).map(Number).map(value => {
      const channel = value / 255; return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
    }).reduce((sum, channel, index) => sum + channel * [.2126, .7152, .0722][index], 0);
    for (const color of colors) {
      const levels = [luminance(color), luminance(background)].sort((a, b) => b - a);
      assert.ok((levels[0] + .05) / (levels[1] + .05) >= 3, "Graph lines must have at least 3:1 contrast against their background");
    }
  };
  for (let i = 0; i < 150; i++) {
    if (await evaluate(() => document.querySelector(".monaco-editor textarea"))) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "History").click());
  for (const modifier of ["ctrlKey", "metaKey"]) {
    const opened = await win.webContents.executeJavaScript(`(async () => {
      const editor = document.querySelector('.monaco-editor textarea');
      editor.focus();
      editor.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', code: 'KeyF', ${modifier}: true, bubbles: true, cancelable: true }));
      await new Promise(resolve => setTimeout(resolve, 100));
      return !!document.querySelector('.find-widget.visible');
    })()`);
    if (!opened) console.log(await evaluate(() => ({ find: document.querySelector(".find-widget")?.outerHTML, platform: navigator.platform, errors: document.body.textContent.slice(-1500) })));
    assert.ok(opened, `${modifier}+F must open real Monaco Find on Mac`);
    await evaluate(() => document.querySelector(".find-widget .codicon-widget-close").click());
  }
  await evaluate(() => window.setFindBinding("Alt+KeyG"));
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(await evaluate(async () => {
    const editor = document.querySelector(".monaco-editor textarea");
    editor.dispatchEvent(new KeyboardEvent("keydown", { key: "f", code: "KeyF", ctrlKey: true, bubbles: true, cancelable: true }));
    await new Promise(resolve => setTimeout(resolve, 100));
    return !!document.querySelector(".find-widget.visible");
  }), false, "Changing Find must disable Monaco's original shortcut");
  assert.ok(await evaluate(async () => {
    const editor = document.querySelector(".monaco-editor textarea");
    editor.dispatchEvent(new KeyboardEvent("keydown", { key: "g", code: "KeyG", altKey: true, bubbles: true, cancelable: true }));
    await new Promise(resolve => setTimeout(resolve, 100));
    return !!document.querySelector(".find-widget.visible");
  }), "Custom Find binding must reach Monaco");
  await evaluate(() => document.querySelector(".find-widget .codicon-widget-close").click());
  assert.equal(await evaluate(() => document.querySelectorAll(".scm-history-row").length), 4);
  assert.ok(await evaluate(() => [...document.querySelectorAll(".scm-history-row")].every(row => {
    const lane = row.querySelector(".scm-history-lane").getBoundingClientRect();
    const buttons = [...row.querySelectorAll("button")];
    const header = buttons[0].parentElement.getBoundingClientRect();
    const copy = buttons[1].getBoundingClientRect();
    const icon = buttons[1].querySelector("svg").getBoundingClientRect();
    const message = buttons[0].querySelector(".font-medium").getBoundingClientRect();
    const meta = buttons[0].querySelector(".font-mono");
    const time = meta.previousElementSibling.getBoundingClientRect();
    const hash = meta.getBoundingClientRect();
    return lane.width === 32 && buttons.length === 2
      && Math.abs(icon.y + icon.height / 2 - message.y - message.height / 2) < 1
      && Math.abs(header.right - copy.right) < 1 && hash.left > time.right && Math.abs(hash.y - time.y) < 1;
  })), "History must use compact lanes, put the ID after the time and keep only the copy button at the top right");
  assert.ok(await evaluate(() => [...document.querySelectorAll(".scm-history-lane path")].some(edge => edge.getAttribute("d").includes("C"))));
  await evaluate(() => [...document.querySelectorAll(".scm-history-row button")].find(button => button.textContent.includes("Add configurable Find")).click());
  assert.ok(await evaluate(() => document.body.textContent.includes("Keep search within the active editor.")));
  const bounds = await evaluate(() => [...document.querySelectorAll(".scm-history-lower")].map(svg => svg.getBoundingClientRect().height));
  assert.ok(bounds.every(height => height > 0), "Ancestry must extend through expanded details");
  assert.ok(await evaluate(() => [...document.querySelectorAll(".scm-history-lower")].every(lower => {
    const row = lower.closest(".scm-history-row").getBoundingClientRect();
    const svg = lower.querySelector("svg").getBoundingClientRect();
    return Math.abs(row.bottom - svg.bottom) < 1 && svg.height >= row.height - 19;
  })), "Connecting lines must reach the next commit even after expanding a row");
  assert.ok(await evaluate(() => [...document.querySelectorAll(".scm-history-lane path")].some(edge => edge.getAttribute("stroke-dasharray"))), "Lanes must be distinguishable without relying on color");
  assert.ok(await evaluate(() => document.body.textContent.includes("1 ahead / 0 behind")));
  await evaluate(() => document.querySelector('[aria-label="History branches"]').click());
  const choose = async label => {
    await win.webContents.executeJavaScript(`document.querySelector(${JSON.stringify(`input[aria-label="${label}"]`)}).click()`);
    await new Promise(resolve => setTimeout(resolve, 100));
  };
  const clearBranches = async () => {
    await evaluate(() => [...document.querySelectorAll('[aria-label="Filter history branches"] button')].find(button => button.textContent === "Clear").click());
    await new Promise(resolve => setTimeout(resolve, 100));
  };
  for (const [category, ids, rows] of [
    ["Local branches", ["refs/heads/main", "refs/heads/coworker"], 4],
    ["Remote branches", ["refs/remotes/origin/main", "refs/remotes/origin/coworker"], 3],
    ["Tags", ["refs/tags/v0.3.8"], 1],
  ]) {
    await clearBranches();
    assert.ok(await evaluate(() => document.body.textContent.includes("Select branches to show their history.")));
    await choose(category);
    assert.deepEqual(await evaluate(() => window.historyCalls.at(-1).refs), ids);
    assert.equal(await evaluate(() => document.querySelectorAll(".scm-history-row").length), rows);
  }
  await clearBranches();
  await choose("Local branch coworker");
  await choose("Remote branch origin/main");
  assert.deepEqual(await evaluate(() => window.historyCalls.at(-1).refs), ["refs/heads/coworker", "refs/remotes/origin/main"]);
  assert.equal(await evaluate(() => document.querySelectorAll(".scm-history-row").length), 3);
  assert.ok(await evaluate(() => document.querySelector('input[aria-label="Local branches"]').indeterminate));
  assert.ok(await evaluate(() => document.querySelector('input[aria-label="Remote branches"]').indeterminate));
  await choose("Local branch main");
  assert.equal(await evaluate(() => document.querySelectorAll(".scm-history-row").length), 4);
  await choose("Local branch main");
  assert.equal(await evaluate(() => document.querySelectorAll(".scm-history-row").length), 3);
  assert.equal(await evaluate(() => document.querySelector('[aria-label="History branches"]').getAttribute("aria-expanded")), "true", "The picker must stay open while choosing multiple branches");
  require("node:fs").writeFileSync("/tmp/rem-history-multiselect.png", (await win.webContents.capturePage()).toPNG());
  await evaluate(() => {
    const input = document.querySelector('input[aria-label="Remote branch origin/coworker"]');
    input.focus(); input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  });
  assert.equal(await evaluate(() => document.querySelector('[aria-label="History branches"]').getAttribute("aria-expanded")), "false");
  assert.ok(await evaluate(() => document.activeElement === document.querySelector('[aria-label="History branches"]')));
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "Changes").click());
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "History").click());
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.deepEqual(await evaluate(() => window.historyCalls.at(-1).refs), ["refs/heads/coworker", "refs/remotes/origin/main"], "Returning to history must refresh the selected branches");
  await evaluate(() => document.querySelector('[aria-label="History branches"]').click());
  await evaluate(() => [...document.querySelectorAll('[aria-label="Filter history branches"] button')].find(button => button.textContent === "All").click());
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(await evaluate(() => window.historyCalls.at(-1).refs), null);
  await evaluate(() => document.querySelector('[aria-label="Filter commit history"]').dispatchEvent(new Event("pointerdown", { bubbles: true })));
  assert.equal(await evaluate(() => document.querySelector('[aria-label="History branches"]').getAttribute("aria-expanded")), "false");
  await verifyContrast();
  const screenshot = await win.webContents.capturePage();
  require("node:fs").writeFileSync("/tmp/rem-find-graph.png", screenshot.toPNG());
  await evaluate(() => document.documentElement.classList.add("dark"));
  await new Promise(resolve => setTimeout(resolve, 100));
  await verifyContrast();
  require("node:fs").writeFileSync("/tmp/rem-find-graph-dark.png", (await win.webContents.capturePage()).toPNG());
  win.focus();
  win.webContents.focus();
  await evaluate(() => document.querySelector(".monaco-editor textarea").focus());
  await new Promise(resolve => setTimeout(resolve, 50));
  win.webContents.sendInputEvent({ type: "keyDown", keyCode: "X" });
  win.webContents.sendInputEvent({ type: "char", keyCode: "x" });
  win.webContents.sendInputEvent({ type: "keyUp", keyCode: "X" });
  for (let i = 0; i < 10; i++) {
    if (await evaluate(() => document.querySelector('[aria-label="Unsaved changes"]'))) break;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.ok(await evaluate(() => document.querySelector('[aria-label="Unsaved changes"]')), "Deletion must also clear a dirty editor");
  await evaluate(() => window.deleteFixtureFile());
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.ok(await evaluate(() => document.body.textContent.includes("This file doesn't exist anymore")));
  assert.equal(await evaluate(() => document.querySelectorAll(".monaco-editor").length), 0, "Deleted content must be removed from the editor area");
  require("node:fs").writeFileSync("/tmp/rem-missing-file.png", (await win.webContents.capturePage()).toPNG());
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "Close Tab").click());
  assert.deepEqual(await evaluate(() => window.closedFiles), ["/fixture/sample.js"]);
  await new Promise(resolve => setTimeout(resolve, 1100));
  assert.equal(await evaluate(() => window.fileWrites), 0, "Deleting the file must cancel pending autosave");
  await evaluate(() => window.restoreFixtureFile());
  for (let i = 0; i < 100; i++) {
    if (await evaluate(() => /fresh\s+disk\s+content/.test(document.querySelector(".monaco-editor")?.textContent || ""))) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(await evaluate(() => /fresh\s+disk\s+content/.test(document.querySelector(".monaco-editor")?.textContent || "")), "Reopening a recreated file must read the disk rather than restore a deleted session");
  // Let Monaco finish its delayed cursor highlighting before closing the fresh editor.
  await new Promise(resolve => setTimeout(resolve, 150));
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.getAttribute("aria-label") === "Close sample.js").click());
  await new Promise(resolve => setTimeout(resolve, 100));
  await evaluate(() => window.openMissingFile());
  for (let i = 0; i < 100; i++) {
    if (await evaluate(() => document.body.textContent.includes("This file doesn't exist anymore"))) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(await evaluate(() => document.body.textContent.includes("This file doesn't exist anymore")), "Initial missing read responses must show the missing-file screen");
  assert.equal(await evaluate(() => document.querySelectorAll(".monaco-editor").length), 0);
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "Close Tab").click());
  assert.deepEqual(await evaluate(() => window.closedFiles), ["/fixture/sample.js", "/fixture/sample.js", "/fixture/missing.js"]);
  await evaluate(() => window.openDeletedComparison());
  for (let i = 0; i < 100; i++) {
    if (await evaluate(() => /original\s+deleted\s+file/.test(document.querySelector(".monaco-diff-editor")?.textContent || ""))) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(await evaluate(() => /original\s+deleted\s+file/.test(document.querySelector(".monaco-diff-editor")?.textContent || "")), "Explicit Git comparisons must retain the original version of deleted files");
  assert.equal(await evaluate(() => document.body.textContent.includes("This file doesn't exist anymore")), false);
  assert.ok(await evaluate(() => window.generationJobRequests.length > 0 && window.generationJobRequests.every(request => request.grant === "registered")), "Startup polling must wait for folder registration");
  await evaluate(() => { window.expireFixtureGrant(); window.dispatchEvent(new Event("focus")); });
  await new Promise(resolve => setTimeout(resolve, 250));
  assert.ok(await evaluate(() => window.generationJobRequests.every(request => request.grant === "registered")), "Expired permissions must be renewed before polling");
  await evaluate(() => {
    [...document.querySelectorAll("button")].find(button => button.textContent === "Changes").click();
    window.commitPollingError = "Job storage unavailable";
    window.dispatchEvent(new Event("focus"));
  });
  for (let i = 0; i < 40; i++) {
    if (await evaluate(() => document.querySelector('[aria-label="Dismiss Git error"]'))) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(await evaluate(() => document.body.textContent.includes("Could not check commit-message generation: Job storage unavailable")), "Real polling failures must remain visible");
  await evaluate(() => { window.commitPollingError = ""; window.dispatchEvent(new Event("focus")); });
  for (let i = 0; i < 40; i++) {
    if (await evaluate(() => !document.querySelector('[aria-label="Dismiss Git error"]'))) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.equal(await evaluate(() => !!document.querySelector('[aria-label="Dismiss Git error"]')), false, "A successful empty poll must clear its previous error");
  const requestsBeforeDeletion = await evaluate(() => window.generationJobRequests.length);
  await evaluate(() => { window.deleteFixtureProject(); window.dispatchEvent(new Event("focus")); });
  await new Promise(resolve => setTimeout(resolve, 2200));
  assert.equal(await evaluate(() => window.generationJobRequests.length), requestsBeforeDeletion, "Deleted worktrees must stop requesting commit jobs");
  assert.equal(await evaluate(() => !!document.querySelector('[aria-label="Dismiss Git error"]')), false, "Deleted worktrees must not produce commit polling warnings");
  if (errors.length) console.log(await evaluate(() => window.unhandled));
  assert.deepEqual(errors, []);
  console.log("Mac Find, branch graph, deleted files, commit polling renewal and recovery passed.");
  win.destroy(); await server.close(); clearTimeout(deadline); app.quit();
}).catch(async error => { console.error(error); if (server) await server.close(); app.exit(1); });
