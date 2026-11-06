/* global __dirname, process, console, clearTimeout, setTimeout, document, window, navigator, KeyboardEvent, Event */
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
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
  server = await createServer({ cacheDir: testViteCacheDir(), root: path.resolve(__dirname, ".."), configFile: false, server: { host: "127.0.0.1", port: 0 }, plugins: [
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
  // DOM nodes carry React internals that Electron cannot clone; report them as found.
  const evaluate = fn => win.webContents.executeJavaScript(`Promise.resolve((${fn})()).then(value => value instanceof Node || value instanceof NodeList ? true : value)`);
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
  // Author, time, ID and line counts read as one line inside the text column when it fits.
  const metaRows = () => evaluate(() => [...document.querySelectorAll(".scm-history-row")].map(row => {
    const text = row.querySelector(".scm-history-text").getBoundingClientRect();
    const meta = row.querySelector(".scm-history-meta");
    const box = element => element && element.getBoundingClientRect().toJSON();
    const stats = meta.querySelector(".scm-history-stats");
    const hash = meta.querySelector(".scm-history-hash");
    const author = meta.querySelector(".scm-history-author");
    const overflow = [...meta.querySelectorAll("span, svg")].filter(part => part.getBoundingClientRect().right > text.right + 0.5).length;
    const shown = stats && [...stats.children].filter(part => part.getAttribute("aria-hidden") === "true")
      .map(part => part.classList.contains("scm-history-binary") ? `${part.querySelector("svg") ? "binary icon " : ""}${part.textContent}` : part.textContent).join(" ");
    return {
      subject: row.querySelector(".font-medium").textContent, overflow, authorTruncated: author.scrollWidth > author.clientWidth,
      author: box(author), time: box(meta.querySelector(".scm-history-time")), hash: box(hash), stats: box(stats),
      statsAfterHash: !stats || hash.nextElementSibling === stats, shown: shown ?? null,
      title: stats?.title ?? null, spoken: stats?.querySelector(".sr-only").textContent ?? null,
    };
  }));
  const sameLine = (a, b) => Math.abs(a.top + a.height / 2 - b.top - b.height / 2) < 1;
  const assertBesideId = (rows, context) => {
    for (const row of rows) {
      assert.equal(row.overflow, 0, `${context}: ${row.subject} must keep its details inside the text column`);
      assert.ok(row.statsAfterHash, `${context}: line counts must follow the commit ID`);
      if (!row.stats) continue;
      assert.ok(row.stats.width > 0 && sameLine(row.hash, row.stats) && row.stats.left >= row.hash.right && row.stats.left - row.hash.right < 6, `${context}: ${row.subject} must show its line counts right beside the ID ${JSON.stringify(row)}`);
    }
  };
  const assertOneLine = (rows, context) => {
    assertBesideId(rows, context);
    for (const row of rows.filter(item => item.stats)) {
      assert.ok(sameLine(row.author, row.stats) && sameLine(row.time, row.stats), `${context}: ${row.subject} must show author, time, ID and line counts on one line ${JSON.stringify(row)}`);
      assert.ok(row.hash.left >= row.time.right && row.time.left >= row.author.right, `${context}: details must read author, time, ID, then line counts`);
    }
  };
  for (let i = 0; i < 150; i++) {
    if (await evaluate(() => Boolean(document.querySelector(".monaco-editor textarea")))) break;
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
    const time = buttons[0].querySelector(".scm-history-time").getBoundingClientRect();
    const hash = buttons[0].querySelector(".scm-history-hash").getBoundingClientRect();
    return lane.width === 32 && buttons.length === 2
      && Math.abs(icon.y + icon.height / 2 - message.y - message.height / 2) < 1
      && Math.abs(header.right - copy.right) < 1 && hash.left > time.right && Math.abs(hash.y - time.y) < 1;
  })), "History must use compact lanes, put the ID after the time and keep only the copy button at the top right");
  assert.ok(await evaluate(() => {
    const list = document.querySelector(".scm-history-graph");
    const bounds = list.getBoundingClientRect();
    return list.scrollWidth === list.clientWidth && [...document.querySelectorAll(".scm-history-row")].every(row => {
      const text = row.querySelector(".scm-history-text").getBoundingClientRect();
      const lane = row.querySelector(".scm-history-lane").getBoundingClientRect();
      return Math.abs(text.left - bounds.left) < 1 && Math.abs(text.right - lane.left) < 1 && Math.abs(lane.right - bounds.right) < 1;
    });
  }), "A narrow graph must sit at the right of left-aligned text without any horizontal scrolling");
  const meta = await metaRows();
  assertOneLine(meta, "Default sidebar");
  assert.deepEqual(meta.map(row => [row.shown, row.title]), [
    ["+42 -7", "42 insertions, 7 deletions, compared with the first parent"],
    [null, null],
    ["+12 -3 binary icon 1", "12 insertions, 3 deletions, 1 binary file without line counts"],
    ["binary icon 2", "0 insertions, 0 deletions, 2 binary files without line counts"],
  ], "Rows must show git's counts, nothing when counts are unavailable, and no +0 -0 for binary-only commits");
  assert.ok(meta.every(row => row.spoken === row.title), "Screen readers must hear the same counts the row shows");
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
  await evaluate(() => { window.showWideHistory(); document.querySelector('[aria-label="Refresh commit history"]').click(); });
  for (let i = 0; i < 40; i++) {
    if (await evaluate(() => document.querySelectorAll(".scm-history-row").length === 14)) break;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  const wideLayout = () => evaluate(() => {
    const list = document.querySelector(".scm-history-graph");
    const bounds = list.getBoundingClientRect();
    const scrollbar = document.querySelector(".scm-history-scrollbar");
    const rows = [...document.querySelectorAll(".scm-history-row")].map(row => {
      const text = row.querySelector(".scm-history-text").getBoundingClientRect();
      const lane = row.querySelector(".scm-history-lane").getBoundingClientRect();
      const copy = row.querySelector('button[aria-label^="Copy commit ID"]').getBoundingClientRect();
      const subject = row.querySelector(".font-medium").getBoundingClientRect();
      // Hit-testing only works on screen; rows below the window are checked by geometry alone.
      const onScreen = subject.bottom <= window.innerHeight;
      const hit = onScreen && document.elementFromPoint(subject.left + 4, subject.top + subject.height / 2);
      return { text: [text.left, text.right], lane: [lane.left, lane.right, lane.width], copyRight: copy.right, subjectRight: subject.right, textOnTop: onScreen ? Boolean(hit?.closest(".scm-history-text")) : null };
    });
    return { left: bounds.left, right: bounds.right, width: list.clientWidth, scrollWidth: list.scrollWidth, scrollLeft: list.scrollLeft, bar: scrollbar.getBoundingClientRect().toJSON(), barRange: scrollbar.scrollWidth - scrollbar.clientWidth, rows };
  });
  const wide = await wideLayout();
  assert.equal(wide.rows.length, 14);
  assert.ok(wide.scrollWidth > wide.width, "A wide graph must overflow into a horizontal scroll");
  assert.equal(wide.barRange, wide.scrollWidth - wide.width, "The pinned scrollbar must cover exactly the hidden graph");
  assert.ok(Math.abs(wide.bar.left - (wide.left + wide.width * 2 / 3)) < 1 && Math.abs(wide.bar.width - wide.width / 3) < 1, "The pinned scrollbar must sit under the graph third");
  for (const row of wide.rows) {
    assert.ok(Math.abs(row.text[0] - wide.left) < 1 && Math.abs(row.text[1] - row.text[0] - wide.width * 2 / 3) < 1, "Commit text must keep the left two thirds");
    assert.ok(Math.abs(row.lane[0] - row.text[1]) < 1 && row.lane[2] > wide.width / 3, "The graph must start right of the text and continue past the visible third");
    assert.ok(row.copyRight <= row.text[1] + 0.5 && row.subjectRight <= row.text[1] + 0.5 && row.textOnTop !== false, "A wide graph must not cover or push out the commit text");
  }
  assert.ok(wide.rows.filter(row => row.textOnTop).length >= 8, "Most rows must be hit-tested on screen");
  await evaluate(() => { const bar = document.querySelector(".scm-history-scrollbar"); bar.scrollLeft = bar.scrollWidth; bar.dispatchEvent(new Event("scroll")); });
  await new Promise(resolve => setTimeout(resolve, 100));
  const scrolled = await wideLayout();
  assert.equal(scrolled.scrollLeft, wide.scrollWidth - wide.width, "Dragging the pinned scrollbar must scroll the graph");
  for (const row of scrolled.rows) {
    assert.ok(Math.abs(row.text[0] - wide.left) < 1 && row.textOnTop !== false, "Commit text must stay in place while the graph scrolls");
    assert.ok(Math.abs(row.lane[1] - scrolled.right) < 1, "Scrolling to the end must reveal the rest of the graph");
  }
  require("node:fs").writeFileSync("/tmp/rem-history-wide-scrolled.png", (await win.webContents.capturePage()).toPNG());
  await evaluate(() => { const list = document.querySelector(".scm-history-graph"); list.scrollLeft = 0; list.dispatchEvent(new Event("scroll")); });
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(await evaluate(() => document.querySelector(".scm-history-scrollbar").scrollLeft), 0, "Scrolling the graph directly must move the pinned scrollbar too");
  await evaluate(() => [...document.querySelectorAll(".scm-history-row button")].find(button => button.textContent.includes("Parallel branch 3")).click());
  assert.equal(await evaluate(() => [...document.querySelectorAll(".scm-history-row button[aria-expanded]")].find(button => button.textContent.includes("Parallel branch 3")).getAttribute("aria-expanded")), "true", "Commits must still expand beside a wide graph");
  await new Promise(resolve => setTimeout(resolve, 100));
  require("node:fs").writeFileSync("/tmp/rem-history-wide.png", (await win.webContents.capturePage()).toPNG());
  assert.equal((await metaRows()).find(row => row.subject.startsWith("Parallel branch 12")).shown, "+1481472 -1185180 binary icon 2", "Large counts must be shown exactly");
  // The fixture's sidebar includes the activity rail, like the app's 240-420px pane (272px by default).
  for (const width of [420, 330, 272, 240]) {
    await win.webContents.executeJavaScript(`window.setSidebarWidth(${width})`);
    await new Promise(resolve => setTimeout(resolve, 150));
    const rows = await metaRows();
    assert.ok(rows.every(row => row.overflow === 0 && row.statsAfterHash && (!row.stats || row.stats.width > 0)), `${width}px sidebar: details must stay visible inside the text column instead of spilling over the graph`);
    // Ordinary counts keep their place beside the ID; only very large ones may need a line of their own.
    if (width >= 272) assertBesideId(rows.filter(row => !row.shown || row.shown.length <= 10), `${width}px sidebar`);
    if (width === 420) {
      assertOneLine(rows.filter(row => row.subject.startsWith("Merge twelve")), "420px sidebar");
      assert.ok(rows[0].authorTruncated, "A long author name must truncate to make room for the counts");
    }
    if (width === 240) require("node:fs").writeFileSync("/tmp/rem-history-narrow.png", (await win.webContents.capturePage()).toPNG());
  }
  await win.webContents.executeJavaScript("window.setSidebarWidth(330)");
  await new Promise(resolve => setTimeout(resolve, 150));
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
  assert.deepEqual(await evaluate(() => window.deletedComparisonDiskReads), [], "Deleted-file comparisons must use Git content without inspecting or reading the missing disk file");
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
