/* global structuredClone, HTMLInputElement, indexedDB, IDBDatabase, localStorage, Storage, Response, ReadableStream, HTMLTextAreaElement, Event, TextEncoder, __dirname, clearTimeout, console, document, getComputedStyle, KeyboardEvent, MouseEvent, process, self, setTimeout, window */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { URL } = require("node:url");
const { app, BrowserWindow } = require("electron");

const frontendRoot = path.resolve(__dirname, "..");
const distRoot = process.env.GOFER_PERF_DIST_ROOT || path.join(frontendRoot, "dist");
const timeout = setTimeout(() => fail(new Error("Browser studio smoke test timed out.")), 30000);

let server;
let windowRef;
const rendererErrors = [];

app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("disable-dev-shm-usage");
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-setuid-sandbox");

process.on("unhandledRejection", fail);
process.on("uncaughtException", fail);
app.whenReady().then(run).catch(fail);

function observeRendererErrors(browserWindow) {
  browserWindow.webContents.on("console-message", (details) => {
    if (details.level === "error") rendererErrors.push(details.message);
  });
}

async function run() {
  const baseUrl = await startServer();
  windowRef = new BrowserWindow({
    width: 1440,
    height: 900,
    // Map the window on Xvfb so Chromium paints Monaco and handles native input.
    show: true,
    webPreferences: {
      // An in-memory partition prevents earlier runs from restoring UI state.
      partition: "studio-browser-test",
      contextIsolation: false,
      nodeIntegration: false,
      preload: path.join(__dirname, "studio-preload-mock.cjs"),
      sandbox: false,
    },
  });

  observeRendererErrors(windowRef);
  const startupAt = process.hrtime.bigint();
  await windowRef.loadURL(baseUrl);
  if (process.env.GOFER_STARTUP_PERF_OUTPUT) {
    windowRef.webContents.debugger.attach("1.3");
    await windowRef.webContents.debugger.sendCommand("Performance.enable");
  }
  windowRef.show();
  windowRef.focus();
  windowRef.webContents.focus();
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[aria-label='Project sidebar views']"))));
  if (process.env.GOFER_EMPTY_WORKSPACE_ONLY === "1") {
    await require("./empty-workspace.browser.cjs")({ windowRef, evaluate, waitFor });
    clearTimeout(timeout);
    assert.deepEqual(rendererErrors, [], "Renderer must not log errors");
    console.log("Browser empty workspace responsiveness regressions passed.");
    await cleanup(0);
    return;
  }
  if (process.env.GOFER_STARTUP_PERF_OUTPUT) {
    const readyMs = Number(process.hrtime.bigint() - startupAt) / 1e6;
    await new Promise(resolve => setTimeout(resolve, 1000));
    const { metrics } = await windowRef.webContents.debugger.sendCommand("Performance.getMetrics");
    const values = Object.fromEntries(metrics.map(item => [item.name, item.value]));
    const result = { readyMs, postLoadScriptCpuMs: values.ScriptDuration * 1000,
      postLoadTaskCpuMs: values.TaskDuration * 1000, jsHeapBytes: values.JSHeapUsedSize,
      processes: app.getAppMetrics().map(item => ({ type: item.type, workingSetKiB: item.memory.workingSetSize })),
      images: await evaluate(() => [...document.querySelectorAll(".rem-avatar img")].map(img => ({ width: img.naturalWidth, height: img.naturalHeight }))),
    };
    fs.writeFileSync(process.env.GOFER_STARTUP_PERF_OUTPUT, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    clearTimeout(timeout);
    await cleanup(0);
    return;
  }
  if (process.env.GOFER_SWARM_ONLY === "1") {
    await exerciseSwarms();
    clearTimeout(timeout);
    assert.deepEqual(rendererErrors, [], "Renderer must not log errors");
    console.log("Browser swarm regressions passed.");
    await cleanup(0);
    return;
  }
  if (process.env.GOFER_TERMINAL_ONLY === "1") {
    await exerciseBottomPanelTerminal();
    clearTimeout(timeout);
    console.log("Browser terminal regression test passed.");
    await cleanup(0);
    return;
  }
  if (process.env.GOFER_MONACO_ONLY === "1") {
    await exerciseMonacoEditor();
    await exercisePackagedMonacoWorker(baseUrl);
    clearTimeout(timeout);
    console.log("Browser Monaco regression test passed.");
    await cleanup(0);
    return;
  }
  if (process.env.GOFER_CHAT_ONLY === "1") {
    await exerciseConversationEfficiency();
    clearTimeout(timeout);
    assert.deepEqual(rendererErrors, [], "Renderer must not log errors");
    console.log("Browser conversation efficiency regressions passed.");
    await cleanup(0);
    return;
  }
  if (process.env.GOFER_REM_ONLY === "1") {
    await exerciseRemAvatar();
    clearTimeout(timeout);
    console.log("Browser Rem avatar regression test passed.");
    await cleanup(0);
    return;
  }
  await waitFor(() => evaluate(() => window.innerWidth >= 1000));
  await exerciseCreateDialog();
  await exerciseDesignRegressions();
  await exerciseKeyboardGraphAndResizers();
  await exerciseMonacoEditor();
  await exercisePackagedMonacoWorker(baseUrl);
  await exerciseSourceControl();
  await exerciseConversationEfficiency();

  clearTimeout(timeout);
  assert.deepEqual(rendererErrors, [], "Renderer must not log errors");
  console.log("Browser studio accessibility smoke test passed.");
  await cleanup(0);
}

async function exerciseConversationEfficiency() {
  await evaluate(() => {
    const threads = Array.from({ length: 8 }, (_, i) => ({ id: `efficiency-${i}`, title: `Efficiency thread ${i}`, updatedAt: new Date().toISOString(), projectRoot: "/workspace", provider: "codex", model: "gpt-5.6-sol" }));
    localStorage.setItem("gofer-flow-chat-threads", JSON.stringify(threads.map(({ id, updatedAt }) => ({ id, updatedAt }))));
    for (const thread of threads) {
      localStorage.setItem(`gofer-flow-chat-thread-meta:${thread.id}`, JSON.stringify(thread));
      localStorage.setItem(`gofer-flow-chat-thread:${thread.id}`, JSON.stringify([{ id: `${thread.id}-initial`, role: "assistant", body: `History for ${thread.title}` }]));
    }
  });
  await windowRef.reload();
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[data-assistant-home]"))));
  await evaluate(() => {
    window.__conversationReads = {};
    window.__conversationWrites = {};
    const read = Storage.prototype.getItem;
    window.__historyTransactions = 0;
    const transaction = IDBDatabase.prototype.transaction;
    IDBDatabase.prototype.transaction = function (stores, mode, ...args) {
      const tx = transaction.call(this, stores, mode, ...args);
      if (mode === "readwrite" && Array.from(stores).includes("messages")) tx.addEventListener("complete", () => { window.__historyTransactions += 1; });
      return tx;
    };
    const write = Storage.prototype.setItem;
    Storage.prototype.getItem = function (key) {
      if (key.startsWith("gofer-flow-chat-thread:")) window.__conversationReads[key] = (window.__conversationReads[key] || 0) + 1;
      return read.call(this, key);
    };
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith("gofer-flow-chat-thread:")) window.__conversationWrites[key] = (window.__conversationWrites[key] || 0) + 1;
      return write.call(this, key, value);
    };
    const fetch = window.fetch;
    window.fetch = (...args) => String(args[0]).includes("/chat/stream")
      ? Promise.resolve(new Response(new ReadableStream({ start(controller) { window.__conversationStream = controller; } })))
      : fetch(...args);
  });
  async function open(index) {
    await evaluate((index) => [...document.querySelectorAll("[data-assistant-home] button")].find((button) => button.textContent.includes(`Efficiency thread ${index}`)).click(), index);
    await waitFor(() => evaluate((index) => document.querySelector("[data-chat-pane]").textContent.includes(`History for Efficiency thread ${index}`), index));
  }
  async function back() {
    await evaluate(() => document.querySelector("button[title='Back to active threads']").click());
    await waitFor(() => evaluate(() => Boolean(document.querySelector("[data-assistant-home]"))));
  }
  for (let i = 0; i < 8; i++) { await open(i); await back(); }
  assert.equal(await evaluate(() => window.__conversationReads["gofer-flow-chat-thread:efficiency-0"]), 1, "Initial visit parses history once");
  await open(0);
  assert.equal(await evaluate(() => window.__conversationReads["gofer-flow-chat-thread:efficiency-0"]), 1, "Revisiting migrated history reads IndexedDB, not legacy localStorage");
  await evaluate(() => {
    const textarea = document.querySelector("[data-chat-composer] textarea");
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(textarea, "Test batched thought stream");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await waitFor(() => evaluate(() => !document.querySelector("button[title='Send message']").disabled));
  await evaluate(() => document.querySelector("button[title='Send message']").click());
  await waitFor(() => evaluate(() => Boolean(window.__conversationStream)));
  await evaluate(() => {
    window.__historyTransactions = 0;
    window.__conversationStream.enqueue(new TextEncoder().encode(JSON.stringify({ type: "compaction", messages: [{ id: "memory", role: "system", kind: "memory", body: "Saved model summary" }] }) + "\n"));
  });
  await waitFor(() => evaluate(() => window.__historyTransactions === 2));
  assert.equal(await evaluate(() => document.querySelector("[data-chat-pane]").textContent.includes("History for Efficiency thread 0")), true, "Compaction retains visible history");
  await evaluate(() => {
    window.__conversationWrites = {};
    window.__historyTransactions = 0;
    window.__conversationStream.enqueue(new TextEncoder().encode(Array.from({ length: 100 }, (_, i) => JSON.stringify({ type: "thought", text: `Thought ${i}` })).join("\n") + "\n"));
  });
  await waitFor(() => evaluate(() => window.__historyTransactions === 1));
  await back();
  await open(1);
  await evaluate(() => {
    window.__conversationStream.enqueue(new TextEncoder().encode(JSON.stringify({ type: "final", message: { body: "Background response complete" } }) + "\n"));
    window.__conversationStream.close();
  });
  await waitFor(() => evaluate(() => window.__historyTransactions === 2));
  await back();
  assert.equal(await evaluate(() => [...document.querySelectorAll("[data-assistant-home] button")].find((button) => button.textContent.includes("Efficiency thread 0")).textContent.includes("Completed")), true);
  await evaluate(() => [...document.querySelectorAll("[data-assistant-home] button")].find(button => button.textContent.includes("Efficiency thread 0")).click());
  await waitFor(() => evaluate(() => document.querySelector("[data-chat-pane]").textContent.includes("Background response complete")));
  const history = await evaluate(async () => {
    const database = await new Promise((resolve, reject) => { const request = indexedDB.open("gofer-flow-conversations"); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
    return new Promise(resolve => { const request = database.transaction("messages").objectStore("messages").getAll(); request.onsuccess = () => { database.close(); resolve(request.result.filter(row => row.threadId === "efficiency-0").sort((a, b) => a.sequence - b.sequence).map(row => row.message)); }; });
  });
  assert.equal(history.filter((message) => message.kind === "thought").length, 100);
  assert.equal(history.at(-1).body, "Background response complete");
  // Search finds the oldest message even though reopening loaded only 40 rows.
  assert.equal(await evaluate(() => document.querySelector("[data-chat-pane]").textContent.includes("History for Efficiency thread 0")), false);
  await evaluate(() => document.querySelector("button[aria-label='Search threads']").click());
  await waitFor(() => evaluate(() => document.activeElement?.getAttribute("aria-label") === "Search all thread history"));
  await evaluate(() => {
    const input = document.querySelector("input[aria-label='Search all thread history']");
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(input, "History for Efficiency thread 0");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await waitFor(() => evaluate(() => document.querySelector("[aria-label='Thread search results']")?.textContent.includes("Efficiency thread 0")));
  await wait(220);
  const searchInputStyle = await evaluate(() => {
    const input = document.querySelector("input[aria-label='Search all thread history']");
    const style = getComputedStyle(input);
    return { outline: style.outlineStyle, color: style.outlineColor, width: style.outlineWidth };
  });
  // Tailwind's outline-none is a transparent solid outline when focus-visible is absent.
  const invisibleOutline = searchInputStyle.outline === "none" || searchInputStyle.width === "0px"
    || searchInputStyle.color === "transparent" || /^rgba\(.*,[ ]*0(?:\.0+)?\)$/.test(searchInputStyle.color);
  assert.equal(invisibleOutline, true, `Search input must not draw an extra outline: ${JSON.stringify(searchInputStyle)}`);
  fs.writeFileSync("/tmp/rem-thread-search.png", (await windowRef.webContents.capturePage()).toPNG());
  await evaluate(() => document.querySelector("input[aria-label='Search all thread history']").dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  assert.equal(await evaluate(() => document.activeElement?.getAttribute("aria-label")), "Search threads");
  // Scroll restoration keeps a visible message at the same viewport position.
  const anchor = await evaluate(() => {
    const scroll = document.querySelector("[data-chat-scroll]");
    scroll.scrollTop = 0;
    const message = scroll.querySelector("[data-history-anchor]");
    const anchor = { id: message?.dataset.historyAnchor, top: message?.getBoundingClientRect().top };
    scroll.dispatchEvent(new Event("scroll", { bubbles: true }));
    return anchor;
  });
  await waitFor(() => evaluate(() => !document.querySelector(".rem-history-loader [role='status']")));
  if (anchor.id) assert.ok(Math.abs(await evaluate(id => document.querySelector('[data-history-anchor="' + id + '"]').getBoundingClientRect().top, anchor.id) - anchor.top) < 3, "Prepending preserves the visible message position");
  for (let i = 0; i < 4; i++) {
    const button = await evaluate(() => [...document.querySelectorAll(".rem-history-loader button")].some(button => button.textContent.includes("Load earlier")));
    if (!button) break;
    await evaluate(() => document.querySelector(".rem-history-loader button").click());
    await waitFor(() => evaluate(() => !document.querySelector(".rem-history-loader [role='status']")));
  }
  assert.equal(await evaluate(() => document.querySelector("[data-chat-pane]").textContent.includes("History for Efficiency thread 0")), true);
  fs.writeFileSync("/tmp/rem-thread-history.png", (await windowRef.webContents.capturePage()).toPNG());
  console.log("Conversation fixture: 8 thread visits; one load per first visit; 100 thoughts in one chunk -> one history write; background completion and unread preserved.");
  await exerciseThreadSearchNavigation();
}

async function exerciseThreadSearchNavigation() {
  await evaluate(() => {
    const thread = { id: "search-navigation", title: "Search navigation", updatedAt: new Date(Date.now() - 11 * 86400000).toISOString(), projectRoot: "/workspace", provider: "codex" };
    const other = { ...thread, id: "search-other", title: "Another thread" };
    localStorage.setItem("gofer-flow-chat-threads", JSON.stringify([thread, other].map(({ id, updatedAt }) => ({ id, updatedAt }))));
    localStorage.setItem(`gofer-flow-chat-thread-meta:${thread.id}`, JSON.stringify(thread));
    localStorage.setItem(`gofer-flow-chat-thread-meta:${other.id}`, JSON.stringify(other));
    localStorage.setItem(`gofer-flow-chat-thread:${other.id}`, JSON.stringify([{ id: "other-0", role: "assistant", body: "Other thread marker" }]));
    const messages = Array.from({ length: 200 }, (_, i) => ({ id: `search-${i}`, role: "assistant", body: `Message ${i}\n\nMore context for this message.` }));
    messages[15].body = "Long message introduction.\n\n".repeat(50) + "Find the **archived needle** here.";
    messages[90] = { id: "search-90", role: "user", body: "Unicode ＮＥＥＤＬＥ and literal [a+b]." };
    messages[130] = { id: "search-130", role: "assistant", kind: "thought", groupId: "search-thoughts", body: "Hidden thought needle", trace: { id: "tool-match", kind: "tool", title: "Read", input: "Hidden thought needle" } };
    messages[145].body = "[Documentation](https://example.com/source-only-match)";
    localStorage.setItem(`gofer-flow-chat-thread:${thread.id}`, JSON.stringify(messages));
  });
  await windowRef.reload();
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[data-assistant-home]"))));
  assert.equal(await evaluate(() => document.querySelector("[data-assistant-home]").textContent.includes("Search navigation")), false, "Archived thread stays out of the active list");
  assert.equal(await evaluate(() => [...document.querySelectorAll("[data-assistant-home] button")].find(button => button.textContent.includes("Archived threads"))?.getAttribute("aria-expanded")), "false");
  const search = async (query, failRead = false) => {
    await evaluate(() => document.querySelector("button[aria-label='Search threads']").click());
    await evaluate(value => {
      const input = document.querySelector("input[aria-label='Search all thread history']");
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }, query);
    await waitFor(() => evaluate(() => Boolean(document.querySelector("[aria-label='Thread search results'] button"))));
    if (failRead) await evaluate(() => {
      const get = window.IDBObjectStore.prototype.get;
      window.IDBObjectStore.prototype.get = function (key) {
        if (this.name === "messages" && key[1] === "search-15") {
          window.IDBObjectStore.prototype.get = get;
          throw new Error("Matching history read failed for test");
        }
        return get.call(this, key);
      };
    });
    await evaluate(() => document.querySelector("[aria-label='Thread search results'] button").click());
    if (failRead) {
      await waitFor(() => evaluate(() => document.querySelector("[data-search-history] [role='alert']")?.textContent.includes("read failed")));
      assert.equal(await evaluate(() => Boolean(document.querySelector('[data-message-id="search-199"]'))), false, "A failed seek must not silently open the latest messages");
      await evaluate(() => [...document.querySelectorAll("[data-search-history] button")].find(button => button.textContent === "Retry").click());
    }
    await waitFor(() => evaluate(() => Boolean(window.CSS.highlights.get("rem-thread-match")?.size)));
    const bounds = await evaluate(() => {
      const range = [...window.CSS.highlights.get("rem-thread-match")][0];
      const rect = range.getBoundingClientRect();
      const scroll = document.querySelector("[data-chat-scroll]").getBoundingClientRect();
      return { text: range.toString(), visible: rect.top >= scroll.top && rect.bottom <= scroll.bottom, count: document.querySelectorAll("[data-search-history] [data-message-id]").length };
    });
    assert.equal(bounds.visible, true, `Search match must be in the viewport: ${query}`);
    assert.ok(bounds.count <= 40, "Opening a search result renders a bounded page");
    return bounds.text;
  };
  assert.equal(await search("archived needle"), "archived needle");
  assert.equal(await evaluate(() => Boolean(document.querySelector("[data-thread-search-match] strong"))), true, "Markdown formatting survives highlighting");
  fs.writeFileSync("/tmp/rem-search-match.png", (await windowRef.webContents.capturePage()).toPNG());
  // Search again while this thread is already open, then return to the same hit.
  assert.equal(await search("unicode needle"), "Unicode ＮＥＥＤＬＥ");
  assert.equal(await search("Hidden thought needle"), "Hidden thought needle");
  assert.equal(await evaluate(() => document.querySelector("[data-thread-search-match] button")?.getAttribute("aria-expanded")), "true");
  const thoughtTop = await evaluate(() => [...window.CSS.highlights.get("rem-thread-match")][0].getBoundingClientRect().top);
  await evaluate(() => [...document.querySelectorAll("[data-search-history] button")].find(button => button.textContent === "Load earlier messages").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector('[data-message-id="search-70"]'))));
  const thoughtTopAfter = await evaluate(() => [...window.CSS.highlights.get("rem-thread-match")][0].getBoundingClientRect().top);
  assert.ok(Math.abs(thoughtTopAfter - thoughtTop) < 3, `Earlier pages preserve the match position: ${thoughtTop} -> ${thoughtTopAfter}`);
  await evaluate(() => document.querySelector("[data-thread-search-match] button").click());
  await evaluate(() => document.querySelector("[data-thread-search-match] button").click());
  await waitFor(() => evaluate(() => Boolean(window.CSS.highlights.get("rem-thread-match")?.size)));
  assert.equal(await search("source-only-match"), "source-only-match");
  assert.equal(await search("Other thread marker"), "Other thread marker");
  assert.equal(await search("archived needle"), "archived needle");
  assert.equal(await search("archived needle", true), "archived needle");
  // A later page must adjoin the search page instead of skipping to recent history.
  await evaluate(() => [...document.querySelectorAll("[data-search-history] button")].find(button => button.textContent === "Load later messages").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector('[data-message-id="search-79"]'))));
  assert.equal(await evaluate(() => Boolean(document.querySelector('[data-message-id="search-199"]'))), false);
  await evaluate(() => [...document.querySelectorAll("[data-chat-pane] button")].find(button => button.textContent === "Latest messages").click());
  await waitFor(() => evaluate(() => !document.querySelector("[data-search-history]") && !window.CSS.highlights.has("rem-thread-match")));
  assert.equal(await evaluate(() => {
    const scroll = document.querySelector("[data-chat-scroll]");
    return scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 3;
  }), true, "Latest messages returns to the bottom");
  // A response completing while the reader is at an old match must not scroll.
  await evaluate(() => {
    const fetch = window.fetch;
    window.fetch = (...args) => String(args[0]).includes("/chat/stream")
      ? Promise.resolve(new Response(new ReadableStream({ start(controller) { window.__searchStream = controller; } }))) : fetch(...args);
    const textarea = document.querySelector("[data-chat-composer] textarea");
    Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set.call(textarea, "Continue this thread");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await waitFor(() => evaluate(() => !document.querySelector("button[title='Send message']").disabled));
  await evaluate(() => document.querySelector("button[title='Send message']").click());
  await waitFor(() => evaluate(() => Boolean(window.__searchStream)));
  await search("archived needle");
  const matchTop = await evaluate(() => [...window.CSS.highlights.get("rem-thread-match")][0].getBoundingClientRect().top);
  await evaluate(() => {
    window.__searchStream.enqueue(new TextEncoder().encode(JSON.stringify({ type: "final", message: { body: "Reply while searching history" } }) + "\n"));
    window.__searchStream.close();
  });
  await waitFor(() => evaluate(() => Boolean(document.querySelector("button[title='Send message']"))));
  assert.ok(Math.abs(await evaluate(() => [...window.CSS.highlights.get("rem-thread-match")][0].getBoundingClientRect().top) - matchTop) < 3, "Live completion must preserve the match position");
  console.log("Search navigation: old and same-thread matches, bounded pages, Unicode, Markdown, expanded thoughts, source-only text and return to latest passed.");
}

async function exerciseRemAvatar() {
  await waitFor(() => evaluate(() => document.querySelector(".rem-avatar")?.dataset.pose === "waving"));
  await waitFor(() => evaluate(() => document.querySelector(".rem-avatar")?.dataset.pose === "seated"));
  await wait(700);
  const metrics = await evaluate(() => {
    const avatar = document.querySelector(".rem-avatar");
    return { width: avatar.offsetWidth, imagesLoaded: [...avatar.querySelectorAll("img")].every((img) => img.complete && img.naturalWidth > 0), waveOpacity: getComputedStyle(avatar.querySelector(".rem-avatar-wave")).opacity };
  });
  assert.deepEqual(metrics, { width: 112, imagesLoaded: true, waveOpacity: "0" });
  fs.writeFileSync("/tmp/raticode-rem-seated.png", (await windowRef.webContents.capturePage()).toPNG());
  await waitFor(() => evaluate(() => document.querySelector(".rem-avatar")?.dataset.blinking === "true"));
  await waitFor(() => evaluate(() => document.querySelector(".rem-avatar")?.dataset.blinking === "false"));
  const toggle = () => evaluate(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "l", code: "KeyL", ctrlKey: true, bubbles: true })));
  await toggle();
  await waitFor(() => evaluate(() => document.querySelector(".rem-avatar")?.dataset.animated === "false"));
  await toggle();
  await waitFor(() => evaluate(() => document.querySelector(".rem-avatar")?.dataset.pose === "waving"));
  assert.deepEqual(await evaluate(() => {
    const wave = document.querySelector(".rem-avatar-wave");
    const style = getComputedStyle(wave);
    return { opacity: style.opacity, transitionDuration: style.transitionDuration };
  }), { opacity: "1", transitionDuration: "0s" });
  fs.writeFileSync("/tmp/raticode-rem-wave.png", (await windowRef.webContents.capturePage()).toPNG());
}

async function exerciseBottomPanelTerminal() {
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[aria-label='Bottom panel']"))));
  await evaluate(() => window.dispatchEvent(new KeyboardEvent("keydown", {
    bubbles: true,
    ctrlKey: true,
    key: "`",
  })));
  await waitFor(() => evaluate(() => Boolean(document.querySelector(".terminal-host .xterm-screen"))));
  assert.equal(
    await evaluate(() => document.querySelector("[aria-label='Bottom panel'] button[role='tab'][aria-selected='true']")?.textContent.trim()),
    "Terminal",
  );
  await waitFor(() => evaluate(() => {
    const panel = document.querySelector("[aria-label='Bottom panel']");
    const expected = Number(panel.querySelector("[aria-label='Resize bottom panel']")?.getAttribute("aria-valuenow"));
    return expected > 36 && panel.getBoundingClientRect().height === expected;
  }));
  await evaluate(() => document.querySelector("button[aria-label='New terminal']").click());
  await waitFor(() => evaluate(() => document.querySelectorAll("button[title^='Close terminal']").length === 2));
  assert.equal(await evaluate(() => window.__goferBridgeCalls
    .filter((call) => call.method === "terminal.create").length), 2);

  await evaluate(() => window.dispatchEvent(new KeyboardEvent("keydown", {
    bubbles: true,
    ctrlKey: true,
    key: "`",
  })));
  await waitFor(() => evaluate(() => document.querySelector("[aria-label='Bottom panel']").getBoundingClientRect().height === 36));
}

async function openRattishWorkflowFile() {
  await evaluate(() => [...document.querySelectorAll("[role='button']")]
    .find((button) => button.textContent.includes("Rattish editor"))
    .parentElement.querySelector("button[title='Workflow actions']").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[role='menu']"))), 25, "workflow actions menu");
  await evaluate(() => [...document.querySelectorAll("[role='menuitem']")]
    .find((button) => button.textContent.trim() === "Edit workflow file").click());
}

async function exerciseMonacoEditor() {
  await waitFor(() => evaluate(() => [...document.querySelectorAll("[role='button']")]
    .some((button) => button.textContent.includes("Rattish editor"))));
  await evaluate(() => [...document.querySelectorAll("[role='button']")]
    .find((button) => button.textContent.includes("Rattish editor")).click());
  await waitFor(() => evaluate(() => [...document.querySelectorAll("article")]
    .some((node) => node.textContent.includes("Prepare"))));
  await evaluate(() => document.querySelector("[data-graph-active='true'] button[title='Run workflow now']").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[role='dialog']"))));
  assert.equal(await evaluate(() => document.querySelector("[role='dialog']").textContent.includes("prepare")), true);
  await evaluate(() => [...document.querySelectorAll("[role='dialog'] button")]
    .find((button) => button.textContent.trim() === "Run workflow").click());
  await waitFor(() => evaluate(() => !document.querySelector("[role='dialog']")));
  await waitFor(() => evaluate(() => [...document.querySelectorAll("[role='button']")]
    .some((button) => button.textContent.includes("Rattish editor") && button.textContent.includes("Success"))));
  await openRattishWorkflowFile();
  await waitFor(() => evaluate(() => Boolean(document.querySelector(".monaco-editor"))));
  assert.equal(await evaluate(() => Boolean(document.querySelector("[aria-label='Search files']"))), false);
  await waitFor(() => evaluate(() => [...document.querySelectorAll(".view-line")]
    .some((line) => line.textContent.includes("Rattish"))));
  await evaluate(() => [...document.querySelectorAll("button[role='tab']")]
    .find((button) => button.closest("[aria-label=\"Editor tabs\"]") && button.textContent.includes("Rattish editor")).click());
  assert.equal(await evaluate(() => Boolean(document.querySelector(".monaco-editor"))), true);
  await waitFor(() => evaluate(() => [...document.querySelectorAll("article")]
    .some((node) => node.textContent.includes("Prepare"))));
  assert.match(await evaluate(() => document.querySelector("[aria-label='Editor tabs'] [aria-selected='true']")?.textContent.trim()), /Rattish editor/);
}

async function exercisePackagedMonacoWorker(baseUrl) {
  const httpWindow = windowRef;
  const packagedWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    show: true,
    webPreferences: {
      partition: "studio-browser-test",
      additionalArguments: [`--gofer-api-base-url=${baseUrl}`],
      contextIsolation: false,
      nodeIntegration: false,
      preload: path.join(__dirname, "studio-preload-mock.cjs"),
      sandbox: false,
    },
  });
  observeRendererErrors(packagedWindow);
  await packagedWindow.loadFile(path.join(distRoot, "index.html"));
  windowRef = packagedWindow;
  if (httpWindow && !httpWindow.isDestroyed()) httpWindow.destroy();
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[aria-label='Project sidebar views']"))));
  await waitFor(() => evaluate(() => [...document.querySelectorAll("[role='button']")]
    .some((button) => button.textContent.includes("Rattish editor"))));
  await evaluate(() => [...document.querySelectorAll("[role='button']")]
    .find((button) => button.textContent.includes("Rattish editor")).click());
  await openRattishWorkflowFile();
  await waitFor(() => evaluate(() => Boolean(document.querySelector(".monaco-editor"))));
  await waitFor(() => evaluate(() => [...document.querySelectorAll(".view-line")]
    .some((line) => /command:\s*echo\s*ready/.test(line.textContent))), 25, "packaged editor to render indented Rattish fields");
  const workerResult = await evaluate(() => {
    try {
      const worker = self.MonacoEnvironment.getWorker();
      worker.terminate();
      return { ok: true };
    } catch (error) {
      return { error: String(error), ok: false };
    }
  });
  assert.equal(workerResult.ok, true, workerResult.error);
}

async function exerciseDesignRegressions() {
  const pickerWidths = await evaluate(() => {
    const textarea = document.querySelector("textarea[placeholder='Message this workflow']");
    const chat = textarea.closest("aside");
    const trigger = chat.querySelector(".model-picker-trigger");
    const provider = trigger.querySelector("[data-model-picker-part='provider']");
    const model = trigger.querySelector("[data-model-picker-part='model']");
    const effort = trigger.querySelector("[data-model-picker-part='effort']");
    const metrics = (segment) => {
      const label = segment.querySelector("[data-picker-label]");
      const segmentRect = segment.getBoundingClientRect();
      const labelRect = label.getBoundingClientRect();
      const style = getComputedStyle(segment);
      return {
        centerDelta: Math.abs(
          (labelRect.left + labelRect.width / 2) - (segmentRect.left + segmentRect.width / 2),
        ),
        color: style.color,
        fontFamily: style.fontFamily,
        fontSize: style.fontSize,
        fontWeight: style.fontWeight,
        fullyVisible: label.scrollWidth <= label.clientWidth,
        width: segmentRect.width,
      };
    };
    return {
      composer: textarea.parentElement.getBoundingClientRect().width,
      effortLeft: effort.getBoundingClientRect().left,
      effortMetrics: metrics(effort),
      effortText: effort.textContent.trim(),
      modelLeft: model.getBoundingClientRect().left,
      modelMetrics: metrics(model),
      modelText: model.textContent.trim(),
      providerLeft: provider.getBoundingClientRect().left,
      providerMetrics: metrics(provider),
      providerText: provider.textContent.trim(),
      trigger: trigger.getBoundingClientRect().width,
    };
  });
  assert.ok(Math.abs(pickerWidths.trigger - pickerWidths.composer) < 1);
  assert.equal(pickerWidths.providerText, "Codex");
  assert.equal(pickerWidths.modelText, "GPT-5.6-Sol");
  assert.equal(pickerWidths.effortText, "Medium");
  assert.ok(pickerWidths.providerLeft < pickerWidths.modelLeft);
  assert.ok(pickerWidths.modelLeft < pickerWidths.effortLeft);
  const typography = ({ color, fontFamily, fontSize, fontWeight }) => ({
    color,
    fontFamily,
    fontSize,
    fontWeight,
  });
  assert.deepEqual(typography(pickerWidths.providerMetrics), typography(pickerWidths.modelMetrics));
  assert.deepEqual(typography(pickerWidths.modelMetrics), typography(pickerWidths.effortMetrics));
  assert.ok(pickerWidths.providerMetrics.centerDelta < 1);
  assert.ok(pickerWidths.modelMetrics.centerDelta < 1);
  assert.ok(pickerWidths.effortMetrics.centerDelta < 1);
  assert.ok(pickerWidths.modelMetrics.width > pickerWidths.providerMetrics.width);
  assert.ok(pickerWidths.providerMetrics.width > pickerWidths.effortMetrics.width);
  assert.equal(pickerWidths.providerMetrics.fullyVisible, true);
  assert.equal(pickerWidths.modelMetrics.fullyVisible, true);
  assert.equal(pickerWidths.effortMetrics.fullyVisible, true);

  await evaluate(() => document.querySelector("[data-picker-trigger='provider']").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[data-picker-menu='provider']"))));
  const providerMenuWidth = await evaluate(() =>
    document.querySelector("[data-picker-menu='provider']").getBoundingClientRect().width,
  );
  assert.ok(
    Math.abs(providerMenuWidth - pickerWidths.composer) <= 20,
    `Provider menu width ${providerMenuWidth} did not match composer width ${pickerWidths.composer}`,
  );
  await evaluate(() => document.querySelector("[data-picker-trigger='provider']").click());

  await evaluate(() => document.querySelector("[data-picker-trigger='model']").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[data-picker-menu='model']"))));
  assert.equal(await evaluate(() => Boolean(document.querySelector("input[placeholder='Search models']"))), false);
  await evaluate(() => document.querySelector("[data-picker-trigger='model']").parentElement.classList.add("dark"));
  await wait(200);
  const darkModelState = await evaluate(() => {
    const trigger = document.querySelector("[data-picker-trigger='model']");
    return {
      background: getComputedStyle(trigger).backgroundColor,
      className: trigger.className,
      expanded: trigger.getAttribute("aria-expanded"),
      parentClassName: trigger.parentElement.className,
    };
  });
  assert.equal(darkModelState.background, "rgb(42, 42, 42)", JSON.stringify(darkModelState));
  await evaluate(() => document.querySelector("[data-picker-trigger='model']").parentElement.classList.remove("dark"));
  await evaluate(() => [...document.querySelectorAll("[data-picker-menu='model'] [role='option']")]
    .find((option) => option.textContent.trim() === "GPT-5.6-Luna").click());
  await waitFor(() => evaluate(() =>
    document.querySelector("[data-picker-trigger='model'] [data-picker-label]").textContent.trim() === "GPT-5.6-Luna"));
  assert.equal(
    await evaluate(() => document.querySelector("[data-picker-trigger='effort'] [data-picker-label]").textContent.trim()),
    "Medium",
  );

  await evaluate(() => document.querySelector("[data-picker-trigger='effort']").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[data-picker-menu='effort']"))));
  const effortMenu = await evaluate(() => {
    const menu = document.querySelector("[data-picker-menu='effort']");
    const options = [...menu.querySelectorAll("[role='option']")];
    return {
      activeLabel: menu.querySelector("[role='option'][aria-selected='true']")?.textContent.trim(),
      fitsWidth: menu.scrollWidth <= menu.clientWidth,
      labels: options.map((option) => option.textContent.trim()),
      optionCount: options.length,
      optionTops: options.map((option) => Math.round(option.getBoundingClientRect().top)),
    };
  });
  assert.equal(effortMenu.optionCount, 5);
  assert.equal(new Set(effortMenu.optionTops).size, 5);
  assert.equal(effortMenu.fitsWidth, true);
  assert.deepEqual(effortMenu.labels, ["Low", "Medium (default)", "High", "X-high", "Max"]);
  assert.equal(effortMenu.activeLabel, "Medium (default)");
  await evaluate(() => document.querySelector("[data-picker-trigger='effort']").click());

  await evaluate(() => document.querySelector("[data-picker-trigger='provider']").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[data-picker-menu='provider']"))));
  await evaluate(() => [...document.querySelectorAll("[data-picker-menu='provider'] [role='option']")]
    .find((option) => option.textContent.includes("Claude Code")).click());
  await waitFor(() => evaluate(() =>
    document.querySelector("[data-picker-trigger='model'] [data-picker-label]").textContent.trim() === "Claude Sonnet 5"));
  assert.equal(
    await evaluate(() => document.querySelector("[data-picker-trigger='effort'] [data-picker-label]").textContent.trim()),
    "High",
  );
  await evaluate(() => document.querySelector("[data-picker-trigger='model']").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[data-picker-menu='model']"))));
  const claudeModels = await evaluate(() =>
    [...document.querySelectorAll("[data-picker-menu='model'] [role='option']")]
      .map((option) => option.textContent.trim()));
  assert.equal(claudeModels.includes("Default"), false);
  assert.equal(claudeModels.includes("Claude Sonnet 5 (default)"), true);
  await evaluate(() => document.querySelector("[data-picker-trigger='model']").click());
  await evaluate(() => document.querySelector("[data-picker-trigger='effort']").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[data-picker-menu='effort']"))));
  assert.equal(
    await evaluate(() => document.querySelector("[data-picker-menu='effort'] [aria-selected='true']").textContent.trim()),
    "High (default)",
  );
  assert.equal(
    await evaluate(() => [...document.querySelectorAll("[data-picker-menu='effort'] [role='option']")]
      .some((option) => option.textContent.trim() === "Default")),
    false,
  );
  await evaluate(() => document.querySelector("[data-picker-trigger='effort']").click());

  assert.equal(await evaluate(() => Boolean(document.querySelector("button[title='New thread']"))), true);
  assert.equal(await evaluate(() => Boolean(document.querySelector("button[title='Active threads']"))), true);
  await evaluate(() => document.querySelector("button[title='New thread']").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("button[title='Back to active threads']"))));
  await evaluate(() => document.querySelector("button[title='Back to active threads']").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[data-assistant-home]"))));
  assert.equal(
    await evaluate(() => document.querySelector("[data-assistant-home] h2")?.textContent.trim()),
    "I'm Rem",
    "Returning from a thread should show Rem's home screen",
  );
  assert.match(
    await evaluate(() => document.querySelector("[data-assistant-home]").textContent),
    /Your coding agent in Raticode\./,
  );
  assert.equal(
    await evaluate(() => document.querySelector("[data-assistant-home] #assistant-home-recent")?.textContent.trim()),
    "Active threads",
  );
  assert.equal(await evaluate(() => Boolean(document.querySelector("button[title='New thread']"))), true);
  assert.equal(await evaluate(() => Boolean(document.querySelector("button[title='Back to active threads']"))), false);
  await evaluate(() => document.querySelector("button[title='Active threads']").click());
  await waitFor(() => evaluate(() => Boolean([...document.querySelectorAll("p")]
    .find((item) => item.textContent.trim() === "Active threads"))));
  await evaluate(() => document.querySelector("button[title='Active threads']").click());

  const composerLayout = await evaluate(() => {
    const composer = document.querySelector("[data-chat-composer]");
    const textarea = composer.querySelector("textarea");
    const send = composer.querySelector("button[title='Send message']");
    const toolbar = send.parentElement;
    const composerRect = composer.getBoundingClientRect();
    const textareaRect = textarea.getBoundingClientRect();
    const sendRect = send.getBoundingClientRect();
    const toolbarRect = toolbar.getBoundingClientRect();
    const inside = (child, parent) => (
      child.left >= parent.left - 1 &&
      child.right <= parent.right + 1 &&
      child.top >= parent.top - 1 &&
      child.bottom <= parent.bottom + 1
    );
    return {
      sendInsideComposer: inside(sendRect, composerRect),
      sendInsideToolbar: inside(sendRect, toolbarRect),
      textareaInsideComposer: inside(textareaRect, composerRect),
      toolbarBelowTextarea: toolbarRect.top >= textareaRect.bottom - 1,
      toolbarInsideComposer: inside(toolbarRect, composerRect),
    };
  });
  assert.equal(composerLayout.textareaInsideComposer, true);
  assert.equal(composerLayout.toolbarInsideComposer, true);
  assert.equal(composerLayout.toolbarBelowTextarea, true);
  assert.equal(composerLayout.sendInsideComposer, true);
  assert.equal(composerLayout.sendInsideToolbar, true);

  await evaluate(() => document.querySelector("button[title='Map']").click());
  await evaluate(() => [...document.querySelectorAll("button")]
    .find((button) => button.textContent.trim() === "Minimap").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[title='Minimap'] > div"))));
  const minimapFill = await evaluate(() => {
    const surface = document.querySelector("[title='Minimap'] > div");
    const container = surface.parentElement;
    return {
      containerHeight: container.clientHeight,
      containerWidth: container.clientWidth,
      surfaceHeight: surface.clientHeight,
      surfaceWidth: surface.clientWidth,
    };
  });
  assert.equal(minimapFill.surfaceWidth, minimapFill.containerWidth);
  assert.equal(minimapFill.surfaceHeight, minimapFill.containerHeight);
  await evaluate(() => [...document.querySelectorAll("button")]
    .find((button) => button.textContent.trim() === "Outline").click());
  await evaluate(() => document.querySelector("button[title='Map']").click());

  await evaluate(() => document.querySelector("summary[title='More graph actions']").click());
  await waitFor(() => evaluate(() => Boolean(
    document.querySelector("details[open] summary[title='More graph actions']"),
  )));
  await evaluate(() => [...document.querySelectorAll("details[open] button")]
    .find((button) => button.textContent.trim() === "Workflow settings").click());
  await waitFor(() => evaluate(() => Boolean(
    document.querySelector("[aria-label='Workflow settings sections']"),
  )), 25, "Workflow settings sections to open");
  assert.deepEqual(
    await evaluate(() => [...document.querySelectorAll("[aria-label='Workflow settings sections'] [role='tab']")]
      .map((tab) => tab.textContent.trim())),
    ["General", "Triggers", "Variables", "Access"],
  );
  await evaluate(() => document.querySelector("button[title='Hide workflow settings and node inspector']").click());
}

async function exerciseKeyboardGraphAndResizers() {
  await evaluate(() => document.querySelector("button[title='Map']").click());
  const initialOutline = await evaluate(() => {
    const outline = document.querySelector("[aria-label='Graph outline']");
    const nodeButtons = [...outline.querySelectorAll("button[aria-label*=', status '")];
    return {
      nodeCount: nodeButtons.length,
      firstDescription: nodeButtons[0]?.getAttribute("aria-label") || "",
    };
  });
  assert.equal(initialOutline.nodeCount, 2);
  assert.match(initialOutline.firstDescription, /incoming.*outgoing.*valid/);

  await evaluate(() => {
    const addNode = document.querySelector("[title='Add node']");
    addNode.focus();
  });
  await pressFocusedKey("Enter");
  await waitFor(() => evaluate(() => Boolean(
    document.querySelector("[aria-label^='New Step 1,']"),
  )));
  assert.equal(
    await evaluate(() => document.activeElement.matches("[aria-label^='New Step 1,']")),
    true,
  );
  await pressFocusedKey("Enter");
  await waitFor(() => evaluate(() => document.activeElement.id === "workflow-inspector"));
  assert.equal(
    await evaluate(() => document.activeElement.id),
    "workflow-inspector",
  );
  assert.deepEqual(
    await evaluate(() => {
      const tablist = document.querySelector("[aria-label='Node inspector sections']");
      return [...tablist.querySelectorAll("[role='tab']")].map((tab) => ({
        label: tab.textContent.trim(),
        selected: tab.getAttribute("aria-selected"),
        tabIndex: tab.getAttribute("tabindex"),
      }));
    }),
    [
      { label: "General", selected: "true", tabIndex: "0" },
      { label: "Action", selected: "false", tabIndex: "-1" },
      { label: "Inputs", selected: "false", tabIndex: "-1" },
      { label: "Run", selected: "false", tabIndex: "-1" },
      { label: "Edges", selected: "false", tabIndex: "-1" },
    ],
  );
  await evaluate(() => {
    document.querySelector("#node-tab-general").focus();
  });
  await pressFocusedKey("ArrowRight");
  await waitFor(() => evaluate(() =>
    document.querySelector("#node-tab-action").getAttribute("aria-selected") === "true",
  ));
  assert.equal(
    await evaluate(() => document.querySelector("#node-tabpanel-general").hidden),
    true,
  );
  await evaluate(() => document.querySelector("#node-tab-general").click());
  await evaluate(() => {
    const labelControl = [...document.querySelectorAll("#workflow-inspector label")]
      .find((label) => label.querySelector("span")?.textContent === "Label")
      ?.querySelector("input");
    labelControl.focus();
    labelControl.select();
  });
  await windowRef.webContents.insertText("Keyboard step");
  await waitFor(() => evaluate(() => Boolean(
    document.querySelector("[aria-label^='Keyboard step,']"),
  )));

  await evaluate(() => {
    document.querySelector("[aria-label^='Run command,']").focus();
  });
  await pressFocusedKey("C");
  assert.match(
    await evaluate(() => document.querySelector("[aria-label='Graph outline']").textContent),
    /Connecting from Run command/,
  );
  await evaluate(() => {
    document.querySelector("[aria-label^='Review output,']").focus();
  });
  await pressFocusedKey("Enter");
  await waitFor(() => evaluate(() => Boolean(
    document.querySelector("[aria-label^='Run command to Review output, condition always']"),
  )));
  assert.equal(
    await evaluate(() => document.activeElement.matches(
      "[aria-label^='Run command to Review output, condition always']",
    )),
    true,
  );
  await pressFocusedKey("Enter");
  await waitFor(() => evaluate(() => document.activeElement.id === "workflow-inspector"));
  assert.equal(await evaluate(() => document.activeElement.id), "workflow-inspector");
  await evaluate(() => {
    const typeControl = [...document.querySelectorAll("#workflow-inspector label")]
      .find((label) => label.querySelector("span")?.textContent === "Type")
      ?.querySelector("select");
    typeControl.focus();
  });
  await pressNativeKey("DOWN");
  await pressNativeKey("DOWN");
  await waitFor(() => evaluate(() => Boolean(
    document.querySelector("[aria-label^='Run command to Review output, condition on failure']"),
  )));
  await evaluate(() => {
    document.querySelector("[aria-label^='Run command to Review output, condition on failure']").focus();
  });
  await pressFocusedKey("Delete");
  await waitFor(() => evaluate(() => !document.querySelector(
    "[aria-label^='Run command to Review output, condition on failure']",
  )));
  assert.equal(
    await evaluate(() => document.activeElement.matches("[aria-label^='Run command,']")),
    true,
  );
  await pressFocusedKey("D", ["control"]);
  await waitFor(() => evaluate(() => Boolean(
    document.querySelector("[aria-label^='Run command copy,']"),
  )));
  assert.equal(
    await evaluate(() => document.activeElement.matches("[aria-label^='Run command copy,']")),
    true,
  );
  await pressFocusedKey("Delete");
  await waitFor(() => evaluate(() => !document.querySelector("[aria-label^='Run command copy,']")));
  assert.equal(
    await evaluate(() => document.activeElement.matches("[aria-label^='Keyboard step,']")),
    true,
  );

  assert.deepEqual(
    await evaluate(() => {
      const separator = document.querySelector("[aria-label='Resize workflow settings and node inspector']");
      return {
        max: separator.getAttribute("aria-valuemax"),
        min: separator.getAttribute("aria-valuemin"),
        now: separator.getAttribute("aria-valuenow"),
        orientation: separator.getAttribute("aria-orientation"),
        role: separator.getAttribute("role"),
      };
    }),
    { max: "520", min: "280", now: "340", orientation: "vertical", role: "separator" },
  );
  await evaluate(() => {
    document.querySelector("[aria-label='Resize workflow settings and node inspector']").focus();
  });
  await pressFocusedKey("ArrowRight");
  await waitFor(() => evaluate(() =>
    document.querySelector("[aria-label='Resize workflow settings and node inspector']")
      .getAttribute("aria-valuenow") === "350",
  ));
  await pressFocusedKey("Enter");
  await waitFor(() => evaluate(() =>
    document.querySelector("[aria-label='Resize workflow settings and node inspector']")
      .getAttribute("aria-valuenow") === "340",
  ));
}

async function exerciseCreateDialog() {
  await evaluate(() => document.querySelector(".studio-sidebar #sidebar-tab-workflows").click());
  await waitFor(() => evaluate(() => document.querySelector(".studio-sidebar #sidebar-panel-workflows").hidden === false));
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[title='New Workflow']"))));
  await evaluate(() => {
    const opener = document.querySelector("[title='New Workflow']");
    opener.focus();
    opener.click();
  });
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[role='dialog']"))));

  const initialState = await evaluate(() => {
    const dialog = document.querySelector("[role='dialog']");
    return {
      activeInside: dialog.contains(document.activeElement),
      describedBy: dialog.getAttribute("aria-describedby"),
      labelledBy: dialog.getAttribute("aria-labelledby"),
      modal: dialog.getAttribute("aria-modal"),
      name: dialog.getAttribute("aria-labelledby")
        ? document.getElementById(dialog.getAttribute("aria-labelledby"))?.textContent
        : "",
    };
  });
  assert.equal(initialState.activeInside, true);
  assert.equal(initialState.modal, "true");
  assert.equal(initialState.name, "New workflow");
  assert.ok(initialState.labelledBy);
  assert.ok(initialState.describedBy);

  const lastControlLabel = await evaluate(() => {
    const dialog = document.querySelector("[role='dialog']");
    const controls = [...dialog.querySelectorAll(
      "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
    )];
    const last = controls.at(-1);
    last.dataset.browserSmokeLast = "true";
    last.focus();
    return last.textContent || last.getAttribute("aria-label") || last.tagName;
  });
  assert.ok(lastControlLabel);
  await sendKey("Tab");
  assert.equal(
    await evaluate(() => {
      const dialog = document.querySelector("[role='dialog']");
      const first = dialog.querySelector(
        "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
      );
      return document.activeElement === first;
    }),
    true,
  );

  await sendKey("Escape");
  await waitFor(() => evaluate(() => !document.querySelector("[role='dialog']")));
  assert.equal(
    await evaluate(() => document.activeElement?.getAttribute("title")),
    "New Workflow",
  );
}

async function sendKey(keyCode) {
  await windowRef.webContents.executeJavaScript(
    `document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: ${JSON.stringify(keyCode)} }))`,
  );
  await wait(40);
}

async function pressFocusedKey(keyCode, modifiers = []) {
  await windowRef.webContents.executeJavaScript(`document.activeElement.dispatchEvent(new KeyboardEvent("keydown", {
    bubbles: true,
    cancelable: true,
    ctrlKey: ${modifiers.includes("control")},
    key: ${JSON.stringify(keyCode)},
    metaKey: ${modifiers.includes("meta")},
    shiftKey: ${modifiers.includes("shift")}
  }))`);
  await wait(60);
}

async function pressNativeKey(keyCode, modifiers = []) {
  windowRef.webContents.sendInputEvent({ type: "keyDown", keyCode, modifiers });
  windowRef.webContents.sendInputEvent({ type: "keyUp", keyCode, modifiers });
  await wait(60);
}

let swarmFixture = {
  id: "swarm-demo", name: "Release team", charter: "Ship a reviewed, tested release.",
  agents: [{ id: "lead", name: "Lead", role: "Coordinate and review work", provider: "codex", model: "gpt-5.6-sol", effort: "high", isOrchestrator: true, allowSteering: false }, { id: "builder", name: "Builder", role: "Implement the frontend", provider: "codex", model: "gpt-5.6-luna", effort: "medium", isOrchestrator: false, allowSteering: false }],
  wakeIntervalSeconds: 60, maxTurns: 100,
  run: { id: "run-1", state: "paused", createdAt: "2026-09-14T12:00:00Z", turnCount: 8, task: "Prepare the release", revision: 1, agentStates: {}, messages: [{ id: "msg1", senderId: "lead", recipientIds: ["builder"], body: "Build the editor and report validation results.", createdAt: "2026-09-12T12:00:00Z", deliveries: [{ agentId: "builder", state: "uncertain", reason: "Restarted before delivery was confirmed" }] }], events: [], objectives: [{ id: "objective1", title: "Release editor", milestones: [{ id: "m1", title: "Plan", weight: 1, ownerId: "lead", status: "accepted", evidence: "Plan reviewed" }, { id: "m2", title: "Build editor", weight: 3, ownerId: "builder", status: "accepted", evidence: "Tests pass" }, { id: "m3", title: "Review", weight: 1, ownerId: "builder", status: "planned" }] }] },
};
swarmFixture.run.agentStates = { builder: { state: "idle", messages: [{ role: "assistant", body: "Editor implementation passes validation." }], traces: [{ title: "Run tests", text: "npm test passed" }] } };
swarmFixture.run.usage = { input_tokens: null, output_tokens: 42 };
swarmFixture.run.workspace = { mode: "git", path: "/workspace/swarm/integration" };
swarmFixture.run.attempts = [{ id: "attempt-1", milestoneId: "m3", ownerId: "builder", state: "uncertain", workspace: { path: "/workspace/swarm/attempt-1" }, result: { passed: false, checks: [{ command: ["python", "-m", "pytest"], exitCode: 1, logPath: "/workspace/swarm/check.log" }] } }];
swarmFixture.run.configuration = { agents: structuredClone(swarmFixture.agents) };
swarmFixture.history = [{ ...structuredClone(swarmFixture.run), id: "prior-run", task: "Previous release", state: "completed" }];
let swarmMutations = [];

async function checkSwarmWidths(layoutSelector, longTextSelector = "") {
  windowRef.setSize(2400, 1000);
  const originalText = await evaluate(selector => [...document.querySelectorAll(selector || ".no-long-text-fixture")].map(element => {
    const text = element.textContent;
    element.textContent = "LongUnbrokenRepositoryOrAgentName".repeat(12);
    return text;
  }), longTextSelector);
  for (const width of [320, 480, 760, 761, 1024, 1920]) {
    await evaluate(width => {
      const pane = document.querySelector(".swarm-workspace");
      pane.style.flex = "none";
      pane.style.width = `${width}px`;
    }, width);
    await wait(60);
    const result = await evaluate(layoutSelector => {
      const pane = document.querySelector(".swarm-workspace");
      const layout = pane.querySelector(layoutSelector);
      const bounds = pane.getBoundingClientRect();
      const scrollContainers = [pane, ...pane.querySelectorAll("*")].filter(element => element.getClientRects().length && !element.closest("[hidden]") && /auto|scroll/.test(getComputedStyle(element).overflowX) && !element.matches("input, textarea"));
      const controls = [...layout.querySelectorAll("input, textarea, select, button")].filter(element => element.getClientRects().length);
      return {
        width: pane.clientWidth,
        layoutWidth: layout.getBoundingClientRect().width,
        overflow: scrollContainers.filter(element => element.scrollWidth > element.clientWidth + 1).map(element => element.className),
        outsideControls: controls.filter(element => {
          const rect = element.getBoundingClientRect();
          return rect.left < bounds.left - 1 || rect.right > bounds.right + 1;
        }).map(element => element.getAttribute("aria-label") || element.textContent),
      };
    }, layoutSelector);
    assert.equal(result.width, width, "Test resizes the swarm pane independently of the window");
    assert.ok(Math.abs(result.layoutWidth - width) <= 16, `${layoutSelector} fills its ${width}px pane`);
    assert.deepEqual(result.overflow, [], `No horizontal scroll containers at ${width}px`);
    assert.deepEqual(result.outsideControls, [], `Controls remain inside the ${width}px pane`);
    if (width === 320 && layoutSelector === ".swarm-setup-layout") {
      assert.equal(await evaluate(() => {
        const buttons = [...document.querySelectorAll(".swarm-setup-agent[open] .model-picker-trigger > button")];
        return buttons.length === 3 && buttons.every(button => button.getBoundingClientRect().height >= 34) && buttons[1].getBoundingClientRect().top >= buttons[0].getBoundingClientRect().bottom;
      }), true, "Narrow model controls stack into readable rows");
    }
  }
  await evaluate(({ selector, texts }) => {
    document.querySelectorAll(selector || ".no-long-text-fixture").forEach((element, index) => { element.textContent = texts[index]; });
    const pane = document.querySelector(".swarm-workspace");
    pane.style.removeProperty("flex");
    pane.style.removeProperty("width");
  }, { selector: longTextSelector, texts: originalText });
  windowRef.setSize(1440, 900);
}

async function exerciseSwarms() {
  await waitFor(() => evaluate(() => [...document.querySelectorAll("[role='button']")].some((button) => button.textContent.includes("Rattish editor"))));
  await evaluate(() => [...document.querySelectorAll("[role='button']")].find((button) => button.textContent.includes("Rattish editor")).click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("#sidebar-tab-swarms"))));
  await evaluate(() => {
    const tab = document.querySelector("#sidebar-tab-workflows");
    tab.focus();
    tab.dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true }));
  });
  assert.equal(await evaluate(() => document.activeElement.id), "sidebar-tab-swarms", "End reaches Swarms from Workflows");
  await evaluate(() => document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true })));
  assert.equal(await evaluate(() => document.activeElement.id), "sidebar-tab-workflows", "Home returns to Workflows");
  const editorTabs = await evaluate(() => document.querySelector("[aria-label='Editor tabs']").textContent);
  await evaluate(() => document.querySelector("#sidebar-tab-swarms").click());
  await waitFor(() => evaluate(() => document.querySelector("#sidebar-panel-swarms").textContent.includes("Release team")));
  await evaluate(() => [...document.querySelectorAll("#sidebar-panel-swarms button")].find((button) => button.textContent.includes("Release team")).click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[aria-label='Message to swarm']"))));
  assert.equal(await evaluate(() => document.querySelector("[aria-label='Agent roster']").textContent.includes("Builder")), true);
  assert.equal(await evaluate(() => Boolean(document.querySelector("[aria-label='Swarm sections']"))), false, "Dashboard replaces section tabs");
  assert.match(await evaluate(() => document.querySelector("[aria-label='Execution and verification']").textContent), /Input tokens: Unknown.*Output tokens: 42/);
  await evaluate(() => document.querySelector("[aria-label='Execution and verification'] summary").click());
  await waitFor(() => evaluate(() => document.querySelector("[aria-label='Execution and verification']").textContent.includes("Exit 1: python -m pytest")));
  await evaluate(() => {
    const input = document.querySelector("[aria-label='Execution and verification'] textarea");
    input.focus();
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, "Prior worker stopped; inspected the saved artifact.");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "Keep output for verification").click());
  await waitFor(() => swarmMutations.some(item => item.path.endsWith("/execution")));
  assert.equal(swarmMutations.at(-1).body.action, "resolve_attempt");
  assert.equal(swarmMutations.at(-1).body.attemptId, "attempt-1");
  await evaluate(() => document.querySelector("[aria-label='Execution and verification'] summary").click());
  assert.equal(await evaluate(() => document.querySelector(".swarm-history").open), false, "Previous runs start collapsed");
  await evaluate(() => {
    const menu = document.querySelector("[aria-label='Lead agent'] .swarm-agent-menu");
    menu.open = true;
    menu.querySelector("button").focus();
    menu.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
  assert.equal(await evaluate(() => document.activeElement.getAttribute("aria-label")), "Options for Lead", "Escape returns focus to the agent menu trigger");
  assert.equal(await evaluate(() => document.querySelector("[aria-label='Lead agent'] .swarm-agent-menu").open), false);

  assert.equal(await evaluate(() => ["Message board", "Run progress", "Agent roster"].every(label => document.querySelector(`[aria-label="${label}"]`).getBoundingClientRect().height > 0)), true, "Board, progress and agents appear together");
  await evaluate(() => document.querySelector("[aria-label='Back to editor']").click());
  await waitFor(() => evaluate(() => !document.querySelector("[aria-label='Swarm workspace']")));
  assert.equal(await evaluate(() => document.querySelector("[aria-label='Editor tabs']").textContent), editorTabs, "Swarm navigation preserves workflow tabs");
  assert.equal(await evaluate(() => document.querySelector("[aria-label='Editor tabs']").getBoundingClientRect().height > 0), true);
  await evaluate(() => [...document.querySelectorAll("#sidebar-panel-swarms button")].find((button) => button.textContent.includes("Release team")).click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[aria-label='Message to swarm']"))));

  await wait(150);
  fs.writeFileSync("/tmp/raticode-swarm-active.png", (await windowRef.webContents.capturePage()).toPNG());
  await evaluate(() => [...document.querySelectorAll("[aria-label='Swarm workspace'] button")].find((button) => button.textContent === "Retry message").click());
  await waitFor(() => swarmMutations.some((item) => item.path.endsWith("/deliveries")));
  assert.deepEqual(swarmMutations.at(-1).body, { messageId: "msg1", agentId: "builder", action: "retry", projectRoot: "/workspace/gofer-flow" });

  await evaluate(() => { document.querySelector(".swarm-objectives").open = true; });
  assert.equal(await evaluate(() => document.querySelector("progress[aria-label='Overall progress']").value), 80);
  await evaluate(() => [...document.querySelectorAll("[aria-label='Swarm workspace'] button")].find((button) => button.textContent === "Edit milestones").click());
  await checkSwarmWidths(".swarm-dashboard", ".swarm-message-content > p");
  await evaluate(() => {
    const input = document.querySelector("[aria-label='Milestone 3 weight']");
    input.focus();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  assert.equal(await evaluate(() => document.querySelector("[aria-label='Milestone 3 weight']").value), "", "Weight can be cleared while focused");
  await evaluate(() => {
    const input = document.querySelector("[aria-label='Milestone 3 weight']");
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "6");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await wait(50);
  await evaluate(() => document.querySelector("[aria-label='Milestone 3 weight']").blur());
  assert.equal(await evaluate(() => document.querySelector("[aria-label='Milestone 3 weight']").value), "6");
  await waitFor(() => evaluate(() => document.querySelector("progress[aria-label='Overall progress']").value === 40));
  await evaluate(() => { document.querySelector(".swarm-objectives").open = false; });
  await evaluate(() => { document.querySelector(".swarm-objectives").open = true; });
  assert.equal(await evaluate(() => document.querySelector("[aria-label='Milestone 3 weight']").value), "6", "Milestone drafts survive collapsing objectives");
  await evaluate(() => [...document.querySelectorAll("[aria-label='Swarm workspace'] button")].find((button) => button.textContent === "Save progress").click());
  await waitFor(() => swarmMutations.some((item) => item.path.endsWith("/objectives")));
  assert.equal(swarmMutations.at(-1).body.objectives[0].milestones[2].weight, 6);
  assert.equal(swarmMutations.at(-1).body.revision, 1);
  await waitFor(() => evaluate(() => !document.querySelector("[aria-label='Milestone 3 weight']")));
  await wait(200);
  await windowRef.webContents.capturePage().then((image) => fs.writeFileSync("/tmp/raticode-swarm-progress.png", image.toPNG()));
  await evaluate(() => { document.querySelector(".swarm-objectives").open = false; });
  await evaluate(() => {
    const input = document.querySelector("[aria-label='Message to swarm']");
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, "Please prioritize review.");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await evaluate(() => [...document.querySelectorAll("[aria-label='Swarm workspace'] button")].find((button) => button.textContent === "Send message").click());
  await waitFor(() => swarmMutations.some((item) => item.path.endsWith("/messages")));
  assert.equal(swarmMutations.at(-1).body.body, "Please prioritize review.");
  await evaluate(() => document.querySelector("button[aria-label='Swarm settings']").click());
  assert.equal(await evaluate(() => document.querySelector(".swarm-setup-footer button").disabled), true, "An active run locks team edits");
  assert.equal(await evaluate(() => document.querySelector("[aria-label='Team details'] input").disabled), true);
  assert.equal(await evaluate(() => document.querySelector("[data-swarm-agent='lead']").disabled), true);
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "Back to dashboard").click());
  await evaluate(() => [...document.querySelectorAll("[aria-label='Swarm workspace'] button")].find((button) => button.textContent === "Stop").click());
  await waitFor(() => swarmMutations.some((item) => item.body.action === "stop"));
  await evaluate(() => document.querySelector("button[aria-label='Swarm settings']").click());
  assert.equal(await evaluate(() => document.querySelector("[aria-label='Team details'] select").value), "lead");
  assert.equal(await evaluate(() => document.querySelector(".swarm-setup-run-options").open), false, "Run limits start collapsed");
  assert.equal(await evaluate(() => [...document.querySelectorAll(".swarm-setup-agent .swarm-setup-advanced")].every(el => !el.open)), true, "Agent advanced settings start collapsed");
  assert.equal(await evaluate(() => document.querySelectorAll(".swarm-setup-agent[open]").length), 1, "Only one agent is expanded");
  await evaluate(() => document.querySelector(".swarm-setup-agent > summary").focus());
  assert.equal(await evaluate(() => document.activeElement.matches(".swarm-setup-agent > summary")), true, "Agent summary can receive focus");
  await pressNativeKey("Return");
  assert.equal(await evaluate(() => document.querySelectorAll(".swarm-setup-agent[open]").length), 0, "Agent summaries support keyboard collapse");
  await pressNativeKey("Return");
  assert.equal(await evaluate(() => document.querySelectorAll(".swarm-setup-agent[open]").length), 1);
  await evaluate(() => {
    document.querySelector(".swarm-setup-run-options").open = true;
    document.querySelector(".swarm-setup-agent[open] .swarm-setup-advanced").open = true;
    document.querySelector(".swarm-setup-agent[open] .swarm-setup-resources").open = true;
  });
  await checkSwarmWidths(".swarm-setup-layout", ".swarm-setup-agent-identity strong");
  await evaluate(() => {
    document.querySelector(".swarm-setup-run-options").open = false;
    document.querySelector(".swarm-setup-agent[open] .swarm-setup-advanced").open = false;
    document.querySelector(".swarm-setup-agent[open] .swarm-setup-resources").open = false;
  });
  await wait(200);
  await windowRef.webContents.capturePage().then((image) => fs.writeFileSync("/tmp/raticode-team-setup-dark.png", image.toPNG()));
  await evaluate(() => { document.querySelector("main").classList.remove("dark"); document.documentElement.classList.remove("dark"); });
  await wait(150);
  fs.writeFileSync("/tmp/raticode-team-setup-light.png", (await windowRef.webContents.capturePage()).toPNG());
  windowRef.setSize(780, 900);
  await wait(150);
  assert.equal(await evaluate(() => { const pane = document.querySelector(".swarm-setup-scroll"); return pane.scrollWidth <= pane.clientWidth; }), true, "Narrow setup does not overflow");
  assert.equal(await evaluate(() => { const footer = document.querySelector(".swarm-setup-footer").getBoundingClientRect(); return footer.bottom <= window.innerHeight && footer.top > 0; }), true, "Save remains visible on narrow screens");
  fs.writeFileSync("/tmp/raticode-team-setup-narrow.png", (await windowRef.webContents.capturePage()).toPNG());
  windowRef.setSize(1440, 900);
  await evaluate(() => document.documentElement.classList.add("dark"));
  await evaluate(() => document.querySelector(".swarm-setup-run-options summary").click());
  for (const [label, value] of [["Concurrent agents", "2"], ["Maximum turns per run", "120"], ["Check interval (seconds)", "90"], ["Maximum run duration (seconds)", "7200"], ["Repair attempts per milestone", "3"], ["Turns without progress before replanning", "8"]]) {
    await evaluate((label) => {
      const input = document.querySelector(`[aria-label="${label}"]`);
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }, label);
    assert.equal(await evaluate(label => document.querySelector(`[aria-label="${label}"]`).value, label), "", "Numeric drafts can be cleared");
    await evaluate(({ label, value }) => {
      const input = document.querySelector(`[aria-label="${label}"]`);
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }, { label, value });
    await evaluate(label => document.querySelector(`[aria-label="${label}"]`).blur(), label);
    assert.equal(await evaluate(label => document.querySelector(`[aria-label="${label}"]`).value, label), value);
  }
  await evaluate(() => document.querySelector(".swarm-setup-run-options summary").click());
  await evaluate(() => document.querySelector("[data-swarm-agent='builder']").closest("details").querySelector("summary").click());
  await evaluate(() => document.querySelector("[data-swarm-agent='builder'] .swarm-setup-advanced summary").click());
  await evaluate(() => document.querySelector("[data-swarm-agent='builder'] input[type='checkbox']").click());
  await evaluate(() => document.querySelector("[data-swarm-agent='lead']").closest("details").querySelector("summary").click());
  await evaluate(() => document.querySelector("[data-swarm-agent='builder']").closest("details").querySelector("summary").click());
  assert.equal(await evaluate(() => document.querySelector("[data-swarm-agent='builder'] input[type='checkbox']").checked), true, "Switching agents preserves drafts");
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "Add agent").click());
  assert.equal(await evaluate(() => document.querySelectorAll(".swarm-setup-agent").length), 3);
  assert.equal(await evaluate(() => document.querySelector(".swarm-setup-agent[open] strong").textContent), "New agent");
  assert.equal(await evaluate(() => document.querySelector(".swarm-setup-footer button").disabled), true, "Incomplete agents cannot be saved");
  await evaluate(() => document.querySelector(".swarm-setup-agent[open] .swarm-setup-remove").click());
  assert.equal(await evaluate(() => document.querySelectorAll(".swarm-setup-agent").length), 2);
  await evaluate(() => {
    const select = document.querySelector("[aria-label='Team details'] select");
    select.value = "builder";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  assert.equal(await evaluate(() => document.querySelectorAll(".swarm-setup-agent .swarm-agent-portrait svg").length), 1, "Changing the orchestrator retains exactly one lead");
  await evaluate(() => {
    const select = document.querySelector("[aria-label='Team details'] select");
    select.value = "lead";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await evaluate(() => document.querySelector(".swarm-setup-footer button").click());
  await waitFor(() => swarmMutations.some(item => item.body.maxTurns === 120));
  assert.equal(swarmMutations.at(-1).body.maxConcurrency, 2);
  assert.equal(swarmMutations.at(-1).body.maxRunSeconds, 7200);
  assert.equal(swarmMutations.at(-1).body.maxRepairAttempts, 3);
  assert.equal(swarmMutations.at(-1).body.stallTurnLimit, 8);
  assert.equal(swarmMutations.at(-1).body.wakeIntervalSeconds, 90);
  assert.equal(swarmMutations.at(-1).body.agents.find(agent => agent.id === "builder").allowSteering, true);
  await waitFor(() => evaluate(() => !document.querySelector(".swarm-setup")));
  await evaluate(() => document.querySelector("[aria-label='Options for Builder']").click());
  await evaluate(() => document.querySelector("[aria-label='Builder agent'] .swarm-agent-menu button").click());
  assert.equal(await evaluate(() => document.querySelector("[aria-label='Agent settings']").textContent.includes("Builder settings")), true);
  assert.equal(await evaluate(() => document.querySelectorAll("[aria-label='Agent settings'] fieldset[data-swarm-agent]").length), 1, "Agent menu opens only that agent's settings");
  await checkSwarmWidths(".swarm-setup-layout", "[aria-label='Agent settings'] .swarm-toolbar h3");
  await evaluate(() => {
    const input = document.querySelector("[aria-label='Agent settings'] textarea");
    input.focus();
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, "Implement and verify the frontend");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.blur();
  });
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "Save agent").click());
  await waitFor(() => swarmMutations.some(item => item.body.agents?.find(agent => agent.id === "builder")?.role === "Implement and verify the frontend"));
  assert.equal(swarmMutations.at(-1).body.agents.find(agent => agent.id === "lead").role, "Coordinate and review work", "Saving one agent preserves teammates");
  await waitFor(() => evaluate(() => !document.querySelector("[aria-label='Agent settings']")));
  await evaluate(() => { document.querySelector("[aria-label='Builder agent'] .swarm-agent-details").open = true; });
  await waitFor(() => evaluate(() => document.querySelector("[aria-label='Builder agent']").textContent.includes("Editor implementation passes validation.")));
  assert.equal(await evaluate(() => document.querySelector("[aria-label='Builder agent']").textContent.includes("Editor implementation passes validation.")), true);
  await evaluate(() => { document.querySelector(".swarm-history").open = true; });
  await waitFor(() => evaluate(() => document.querySelector(".swarm-history").textContent.includes("Previous release")));
  await evaluate(() => document.querySelector(".swarm-history-list button").click());
  await waitFor(() => evaluate(() => document.querySelector(".swarm-run-heading h1").textContent === "Previous release"));
  assert.equal(await evaluate(() => Boolean(document.querySelector("[aria-label='Message to swarm']"))), false, "Archived board cannot send messages");
  assert.equal(await evaluate(() => Boolean(document.querySelector(".swarm-agent-menu"))), false, "Archived agents cannot be configured");
  assert.equal(await evaluate(() => [...document.querySelectorAll("button")].some(button => button.textContent === "Edit milestones")), false, "Archived progress is read only");
  await evaluate(() => { document.querySelector(".swarm-objectives").open = true; });
  await checkSwarmWidths(".swarm-dashboard", ".swarm-objectives h4, .swarm-history-list strong");
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "Return to current run").click());
  await waitFor(() => evaluate(() => document.querySelector(".swarm-run-heading h1").textContent === "Prepare the release"));
  await evaluate(() => { document.querySelector(".swarm-history").open = false; });
  await evaluate(() => { document.querySelector(".swarm-scroll").scrollTop = 0; });
  await wait(100);
  fs.writeFileSync("/tmp/raticode-swarm-dashboard-dark.png", (await windowRef.webContents.capturePage()).toPNG());
  await evaluate(() => { document.querySelector("main").classList.remove("dark"); document.documentElement.classList.remove("dark"); });
  await wait(200);
  fs.writeFileSync("/tmp/raticode-swarm-light.png", (await windowRef.webContents.capturePage()).toPNG());
  windowRef.setSize(1000, 800);
  await wait(200);
  assert.equal(await evaluate(() => { const pane = document.querySelector("[aria-label='Swarm workspace']"); return pane.scrollWidth <= pane.clientWidth; }), true, "Narrow swarm workspace does not overflow horizontally");
  fs.writeFileSync("/tmp/raticode-swarm-narrow.png", (await windowRef.webContents.capturePage()).toPNG());

  // Saved membership and the stopped run's immutable roster may differ.
  swarmFixture.agents = swarmFixture.agents.filter(agent => agent.id !== "builder");
  swarmFixture.run.task = "Review the release. " + "Check the implementation and validation evidence. ".repeat(12);
  swarmFixture.run.configuration.agents[0].role = "Coordinate the team. " + "Review each change and keep the plan up to date. ".repeat(10);
  await evaluate(() => document.querySelector("[aria-label='Back to editor']").click());
  await evaluate(() => [...document.querySelectorAll("#sidebar-panel-swarms button")].find(button => button.textContent.includes("Release team")).click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("[aria-label='Builder agent']"))));
  assert.equal(await evaluate(() => Boolean(document.querySelector("[aria-label='Options for Builder']"))), false, "Removed snapshot agents cannot open empty settings");
  assert.equal(await evaluate(() => document.querySelector(".swarm-run-heading h1").classList.contains("swarm-clamped-text")), true, "Long task prompts keep the dashboard in view");
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "Show full task").click());
  assert.equal(await evaluate(() => document.querySelector(".swarm-run-heading h1").classList.contains("swarm-clamped-text")), false);
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "Show less task").click());
  assert.equal(await evaluate(() => document.querySelector("[aria-label='Lead agent'] .swarm-agent-role").classList.contains("swarm-clamped-text")), true);
  await evaluate(() => { document.querySelector(".swarm-new-run").open = true; });
  await evaluate(() => {
    const input = document.querySelector("#swarm-task");
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, "Review the next release");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "Start run").click());
  await waitFor(() => swarmMutations.some(item => item.path.endsWith("/start") && item.body.task === "Review the next release"));
  await waitFor(() => evaluate(() => document.querySelector(".swarm-run-heading h1").textContent === "Review the next release"));
  assert.equal(await evaluate(() => document.querySelector("[aria-label='Agent roster']").textContent.includes("Builder")), false, "New run uses the saved team");
  assert.equal(await evaluate(() => document.querySelector("progress[aria-label='Overall progress']").value), 0, "New run resets progress");
  assert.equal(await evaluate(() => document.querySelector(".swarm-board-empty").textContent), "No messages yet", "An active run with no messages uses a neutral empty state");
  assert.equal(await evaluate(() => document.querySelector(".swarm-progress-value").textContent), "Not planned", "An unplanned run does not claim measured completion");

  // The first-run dashboard must keep its draft through settings navigation.
  swarmFixture.run = null;
  await evaluate(() => window.dispatchEvent(new Event("gofer:swarms-changed")));
  await evaluate(() => document.querySelector("[aria-label='Back to editor']").click());
  await evaluate(() => [...document.querySelectorAll("#sidebar-panel-swarms button")].find(button => button.textContent.includes("Release team")).click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector("#swarm-task"))));
  assert.equal(await evaluate(() => document.querySelector(".swarm-new-run").open), true);
  assert.equal(await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "Start run").disabled), true);
  assert.equal(await evaluate(() => /mission|Build teamwork|Give your team a task/.test(document.querySelector(".swarm-dashboard").textContent)), false, "The dashboard has no promotional task copy");
  await evaluate(() => {
    const input = document.querySelector("#swarm-task");
    input.focus();
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, "Review the staged changes");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.blur();
  });
  await evaluate(() => document.querySelector("[aria-label='Swarm settings']").click());
  await waitFor(() => evaluate(() => [...document.querySelectorAll("button")].some(button => button.textContent === "Back to dashboard")));
  await evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "Back to dashboard").click());
  assert.equal(await evaluate(() => document.querySelector("#swarm-task").value), "Review the staged changes", "Settings preserve the first task draft");
  assert.equal(await evaluate(() => {
    const board = document.querySelector(".swarm-board-panel").getBoundingClientRect();
    const roster = document.querySelector(".swarm-roster").getBoundingClientRect();
    return board.top < roster.top;
  }), true, "Narrow layout places the board before the roster");
  windowRef.setSize(1800, 1000);
  await evaluate(() => { document.documentElement.classList.add("dark"); document.querySelector(".swarm-scroll").scrollTop = 0; });
  await wait(150);
  fs.writeFileSync("/tmp/raticode-swarm-ready.png", (await windowRef.webContents.capturePage()).toPNG());
  await evaluate(() => document.querySelector("[aria-label='New swarm']").click());
  await waitFor(() => evaluate(() => Boolean(document.querySelector(".swarm-setup-footer"))));
  assert.equal(await evaluate(() => document.querySelector(".swarm-setup-footer button").textContent), "Create swarm");
  await evaluate(() => {
    const input = document.querySelector("[aria-label='Team details'] input");
    input.focus();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "Review team");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await evaluate(() => document.querySelector("[aria-label='Team details'] input").blur());
  await evaluate(() => document.querySelector(".swarm-setup-footer button").click());
  await waitFor(() => swarmMutations.some(item => item.path === "/api/swarms" && item.body.name === "Review team"));
  assert.equal(swarmMutations.at(-1).body.agents.length, 1);
  assert.equal(swarmMutations.at(-1).body.agents[0].isOrchestrator, true);
  assert.equal(swarmMutations.at(-1).body.wakeIntervalSeconds, 60, "Hidden run limits retain their defaults on creation");
}

async function startServer() {
  server = http.createServer((request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    if (url.pathname.startsWith("/api/")) {
      response.setHeader("Access-Control-Allow-Origin", "*");
      if (url.pathname.startsWith("/api/swarms")) {
        if (request.method === "GET") {
          const view = { ...swarmFixture, history: url.searchParams.get("history") === "summary" ? swarmFixture.history.map(({ id, task, state, createdAt }) => ({ id, task, state, createdAt })) : [] };
          json(response, url.pathname === "/api/swarms" ? { swarms: [swarmFixture] } : url.pathname.endsWith("/history") ? { run: swarmFixture.history.find(run => run.id === url.searchParams.get("runId")) } : { swarm: view }); return;
        }
        let body = "";
        request.on("data", (chunk) => { body += chunk; });
        request.on("end", () => {
          const payload = JSON.parse(body);
          swarmMutations.push({ path: url.pathname, body: payload });
          if (url.pathname.endsWith("/start")) {
            swarmFixture.history.push(structuredClone(swarmFixture.run));
            swarmFixture.run = { id: "run-2", task: payload.task, state: "running", configuration: { agents: structuredClone(swarmFixture.agents) }, messages: [], objectives: [], agentStates: {}, revision: 0 };
          }
          if (url.pathname.endsWith("/objectives")) { swarmFixture.run.objectives = payload.objectives; swarmFixture.run.revision += 1; }
          if (request.method === "PUT") { swarmFixture = { ...swarmFixture, ...payload }; }
          if (payload.action === "stop") swarmFixture.run.state = "stopped";
          json(response, { swarm: swarmFixture });
        });
        return;
      }
      if (request.method === "PUT" && url.pathname === "/api/workflows/demo") {
        let body = "";
        request.on("data", (chunk) => { body += chunk; });
        request.on("end", () => json(response, { workflow: { ...workflowFixture(), ...JSON.parse(body) } }));
        return;
      }
      routeApi(url.pathname, response);
      return;
    }

    const requestedPath = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
    const filePath = path.resolve(distRoot, requestedPath);
    if (filePath !== distRoot && !filePath.startsWith(`${distRoot}${path.sep}`)) {
      response.writeHead(404).end();
      return;
    }
    fs.readFile(filePath, (error, data) => {
      if (error) {
        response.writeHead(404).end();
        return;
      }
      const contentTypes = {
        ".css": "text/css",
        ".html": "text/html",
        ".js": "text/javascript",
        ".svg": "image/svg+xml",
      };
      response.writeHead(200, {
        "Content-Type": contentTypes[path.extname(filePath)] || "application/octet-stream",
      });
      response.end(data);
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  return `http://127.0.0.1:${address.port}/`;
}

function routeApi(pathname, response) {
  if (pathname === "/api/workflows") {
    json(response, {
      dataDir: "/workspace",
      promptAgentIds: [],
      workflows: process.env.GOFER_EMPTY_WORKSPACE_ONLY === "1" ? [] : [workflowFixture(), rattishWorkflowFixture()],
    });
    return;
  }
  if (pathname === "/api/provider/capabilities") {
    json(response, {
      providers: [{
        id: "codex",
        displayName: "Codex",
        available: true,
        discoveryStatus: "ready",
        defaultModel: "gpt-5.6-sol",
        models: [{
          id: "gpt-5.6-sol",
          displayName: "GPT-5.6-Sol",
          defaultEffort: "medium",
          efforts: [
            { id: "low", displayName: "Low" },
            { id: "medium", displayName: "Medium" },
            { id: "high", displayName: "High" },
            { id: "xhigh", displayName: "X-high" },
          ],
        }, {
          id: "gpt-5.6-luna",
          displayName: "GPT-5.6-Luna",
          defaultEffort: "medium",
          efforts: [
            { id: "low", displayName: "Low" },
            { id: "medium", displayName: "Medium" },
            { id: "high", displayName: "High" },
            { id: "xhigh", displayName: "X-high" },
            { id: "max", displayName: "Max" },
          ],
        }],
      }, {
        id: "claude_code",
        displayName: "Claude Code",
        available: true,
        discoveryStatus: "ready",
        defaultModel: "claude-sonnet-5",
        models: [{
          id: "claude-sonnet-5",
          displayName: "Claude Sonnet 5",
          defaultEffort: "high",
          efforts: [
            { id: "low", displayName: "Low" },
            { id: "medium", displayName: "Medium" },
            { id: "high", displayName: "High" },
            { id: "xhigh", displayName: "X-high" },
            { id: "max", displayName: "Max" },
          ],
        }, {
          id: "default",
          displayName: "Default",
          defaultEffort: null,
          efforts: [
            { id: "low", displayName: "Low" },
            { id: "medium", displayName: "Medium" },
            { id: "high", displayName: "High" },
            { id: "xhigh", displayName: "X-high" },
            { id: "max", displayName: "Max" },
          ],
        }],
      }],
    });
    return;
  }
  if (pathname === "/api/workflow-templates") {
    json(response, { templates: [] });
    return;
  }
  if (pathname === "/api/workflows/rattish-editor/document") {
    json(response, { document: rattishDocumentFixture() });
    return;
  }
  if (pathname === "/api/workflows/rattish-editor/document/analyze") {
    json(response, { document: rattishDocumentFixture() });
    return;
  }
  if (pathname === "/api/workflows/rattish-editor/document/save") {
    json(response, { document: rattishDocumentFixture() });
    return;
  }
  if (pathname === "/api/workflows/rattish-editor/plan") {
    json(response, {
      plan: {
        blockingDiagnostics: [],
        destructiveActions: ["prepare: command"],
        generations: [{
          index: 0,
          nodes: [{ id: "prepare", detail: "echo ready", sideEffects: ["command"], type: "bash-command" }],
        }],
        kind: "rattish",
        providerRequirements: [],
        requiredSecrets: [],
        runnable: true,
        warnings: [],
      },
    });
    return;
  }
  if (pathname === "/api/workflows/rattish-editor/run") {
    json(response, {
      run: {
        logPath: "/workspace/rattish/run.json",
        logText: "prepare: ready",
        nodeOutputs: { prepare: { data: { stdout: "ready" }, output: "ready", success: true } },
        runEvents: [],
        runNodes: { prepare: { status: "success" } },
        status: "success",
        success: true,
        workflowId: "rattish-editor",
      },
    });
    return;
  }
  if (pathname.endsWith("/logs")) {
    json(response, { runs: [] });
    return;
  }
  if (pathname.endsWith("/approvals")) {
    json(response, { approvals: [] });
    return;
  }
  if (pathname === "/api/doctor") {
    json(response, { errors: [], warnings: [] });
    return;
  }
  json(response, {});
}

function workflowFixture() {
  return {
    agents: {},
    edges: [],
    id: "demo",
    name: "Demo workflow",
    projectRoot: "/workspace/gofer-flow",
    projectName: "gofer-flow",
    nodes: [
      {
        id: "step",
        label: "Run command",
        operation: { command: "echo hello", type: "bash_command", working_dir: "" },
        type: "bash_command",
        x: 80,
        y: 80,
      },
      {
        id: "review",
        label: "Review output",
        operation: { agent_id: "reviewer", prompt: "Review", type: "agent" },
        type: "agent",
        x: 400,
        y: 80,
      },
    ],
    parameters: {},
    sourcePath: "/workspace/demo.toml",
    status: "Ready",
    tags: ["ready"],
  };
}

function rattishWorkflowFixture() {
  return {
    agents: {},
    edges: [],
    id: "rattish-editor",
    name: "Rattish editor",
    nodes: [],
    parameters: {},
    projectName: "gofer-flow",
    projectRoot: "/workspace/gofer-flow",
    readOnly: true,
    sourceFormat: "rattish",
    sourcePath: "/workspace/gofer-flow/.raticode/rattish-editor/workflow.rattish",
    status: "Ready",
    tags: ["ready"],
    workflowRoot: "/workspace/gofer-flow/.raticode/rattish-editor",
  };
}

function rattishDocumentFixture() {
  const source = "Rattish: 1\n\nWorkflow:\n  name: Rattish editor\n\nNode prepare:\n  type: bash-command\n  command: echo ready\n";
  return {
    compilation: { fingerprint: "sha256:test", irVersion: 1, lastValidFingerprint: "sha256:test", state: "valid" },
    diagnostics: [],
    dirty: false,
    graph: {
      edges: [],
      nodes: [{
        configuration: { command: "echo ready" },
        diagnostics: [],
        execution: { allow_fail: false, max_concurrency: 1, retry_count: 0, retry_delay_ms: 0, timeout_ms: null },
        id: "prepare",
        label: "Prepare",
        status: "valid",
        type: "bash-command",
      }],
    },
    invalidRegions: [],
    metadata: { metadataVersion: 1, canvas: { nodes: {}, pan: { x: 0, y: 0 }, zoom: 1 }, editor: { foldedDeclarations: [] } },
    metadataRevision: "sha256:metadata",
    preflight: { diagnostics: [], ready: true },
    projectRoot: "/workspace/gofer-flow",
    runnable: true,
    savedRevision: "sha256:source",
    source,
    sourcePath: "/workspace/gofer-flow/.raticode/rattish-editor/workflow.rattish",
    sourceRevision: "sha256:source",
    workflow: { name: "Rattish editor" },
    workflowId: "rattish-editor",
  };
}

function json(response, payload) {
  response.writeHead(200, { "Content-Type": "application/json" });
  response.end(JSON.stringify(payload));
}

async function evaluate(callback, argument) {
  const result = await windowRef.webContents.executeJavaScript(`(async () => {
    try {
      return { value: await (${callback.toString()})(${JSON.stringify(argument) ?? "undefined"}) };
    } catch (error) {
      return { error: String(error?.stack || error) };
    }
  })()`);
  if (result?.error) throw new Error(result.error);
  return result?.value;
}

async function waitFor(predicate, delay = 25, description = "browser condition") {
  const deadline = Date.now() + 7000;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await wait(delay);
  }
  const pageState = await evaluate(() => ({
    focusedElement: document.activeElement?.outerHTML.slice(0, 1000),
    tabs: [...document.querySelectorAll("[role='tab']")].map((tab) => ({
      label: tab.textContent.trim(),
      selected: tab.getAttribute("aria-selected"),
      disabled: tab.disabled,
    })),
    text: document.body.textContent.slice(-10000),
    editor: [...document.querySelectorAll(".monaco-editor")].map((editor) => ({
      width: editor.clientWidth,
      height: editor.clientHeight,
      text: editor.textContent,
    })),
    bridgeCalls: window.__goferBridgeCalls,
  }));
  throw new Error(`Timed out waiting for ${description}.\nPredicate: ${predicate}\nPage: ${JSON.stringify(pageState, null, 2)}`);
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function cleanup(exitCode) {
  if (windowRef && !windowRef.isDestroyed()) windowRef.destroy();
  if (server) await new Promise((resolve) => server.close(resolve));
  app.exit(exitCode);
}

async function fail(error) {
  clearTimeout(timeout);
  console.error(error);
  await cleanup(1);
}

async function exerciseSourceControl() {
  windowRef.show();
  windowRef.focus();
  windowRef.webContents.focus();
  await waitFor(() => evaluate(() => document.hasFocus()), 25, "source control window focus");
  await evaluate(() => {
    window.goferDesktop.workspace.gitStatus = async () => ({ active: true, branch: "main", branches: ["main", "feature"], remotes: [], entries: Array.from({ length: 24 }, (_, i) => ({ path: `frontend/src/components/Example${i}.jsx`, status: "M", staged: i === 0, unstaged: i !== 0 })) });
    window.goferDesktop.workspace.gitHistory = async () => ({ active: true, commits: [] });
    window.goferDesktop.workspace.gitWorktrees = async () => ({ active: true, worktrees: [{ path: "/repo", branch: "main" }] });
    document.querySelector("[aria-label='Refresh source control']").click();
    document.querySelector("#sidebar-tab-source-control").click();
  });
  await waitFor(() => evaluate(() => Boolean(document.querySelector(".scm-composer"))));
  for (const dark of [false, true]) {
    await windowRef.webContents.executeJavaScript(`document.documentElement.classList.toggle("dark", ${dark})`);
    const bounds = await evaluate(() => {
      const panel = document.querySelector(".scm-panel");
      const content = document.querySelector("#scm-content");
      const composer = document.querySelector(".scm-composer");
      const before = composer.getBoundingClientRect().top;
      content.scrollTop = content.scrollHeight;
      return { width: panel.clientWidth, overflow: panel.scrollWidth > panel.clientWidth, scrolls: content.scrollHeight > content.clientHeight, fixed: composer.getBoundingClientRect().top === before, bottom: composer.getBoundingClientRect().bottom <= panel.getBoundingClientRect().bottom + 1 };
    });
    assert.equal(bounds.overflow, false, JSON.stringify(bounds));
    assert.equal(bounds.scrolls, true);
    assert.equal(bounds.fixed, true);
    assert.equal(bounds.bottom, true);
    await evaluate(() => { document.querySelector("#scm-content").scrollTop = 0; });
    const beforeHover = await evaluate(() => {
      const row = document.querySelector(".scm-file");
      const buttons = [...row.querySelectorAll("button")].slice(1);
      const bounds = buttons.map((button) => {
        const rect = button.getBoundingClientRect();
        return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, display: getComputedStyle(button).display };
      });
      return bounds;
    });
    assert.equal(beforeHover.length, 2);
    assert.ok(beforeHover.every((button) => button.width > 0 && button.display !== "none"));
    windowRef.webContents.sendInputEvent({ type: "mouseMove", x: Math.round(beforeHover[0].x + 14), y: Math.round(beforeHover[0].y + 14) });
    await wait(50);
    const afterHover = await evaluate(() => [...document.querySelector(".scm-file").querySelectorAll("button")].slice(1).map((button) => button.getBoundingClientRect().x));
    assert.deepEqual(afterHover, beforeHover.map((button) => button.x), "Git action buttons must stay in place on hover");
    const screenshot = await windowRef.webContents.capturePage();
    fs.writeFileSync(`/tmp/raticode-source-control-${dark ? "dark" : "light"}.png`, screenshot.toPNG());
  }
  await evaluate(() => document.querySelector("#scm-tab-branches").click());
  for (const dark of [false, true]) {
    windowRef.webContents.sendInputEvent({ type: "mouseMove", x: 10, y: 10 });
    await wait(50);
    await windowRef.webContents.executeJavaScript(`document.documentElement.classList.toggle("dark", ${dark})`);
    await evaluate(() => document.querySelector('[aria-label="Integrate main worktree"]').dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, clientX: 120, clientY: 240 })));
    await waitFor(() => evaluate(() => document.activeElement?.dataset.operation === "merge" && document.activeElement.matches(":focus")), 25, "initial worktree menu focus");
    assert.deepEqual(await evaluate(() => [...document.querySelectorAll("[data-operation]")].map((item) => item.dataset.operation)),
      ["merge", "squash", "ff-only", "no-ff", "rebase"]);
    const merge = await evaluate(() => {
      const item = document.querySelector('[data-operation="merge"]');
      const style = getComputedStyle(item);
      const activeColor = style.getPropertyValue("--color-menu-active").trim().split(/\s+/).join(", ");
      return { background: style.backgroundColor, expectedBackground: `rgb(${activeColor})`, outline: style.outlineStyle };
    });
    assert.equal(merge.background, merge.expectedBackground, "Focused menu item must use the theme's active color");
    assert.equal(merge.outline, "none", "Opening a context menu with the pointer should not paint a keyboard outline");
    const rebaseBounds = await evaluate(() => {
      const rect = document.querySelector('[data-operation="rebase"]').getBoundingClientRect();
      return { x: Math.round(rect.x + 30), y: Math.round(rect.y + 15) };
    });
    windowRef.webContents.sendInputEvent({ type: "mouseMove", ...rebaseBounds });
    await waitFor(() => evaluate(() => document.activeElement?.dataset.operation === "rebase"));
    assert.equal(await evaluate(() => document.querySelector('[data-operation="merge"]').hasAttribute("aria-haspopup")), false);
    await pressNativeKey("Up");
    await waitFor(() => evaluate(() => document.activeElement?.dataset.operation === "no-ff"));
    await pressNativeKey("Home");
    await waitFor(() => evaluate(() => document.activeElement?.dataset.operation === "merge"));
    assert.equal(await evaluate(() => getComputedStyle(document.activeElement).outlineStyle), "solid");
    fs.writeFileSync(`/tmp/raticode-worktree-menu-${dark ? "dark" : "light"}.png`, (await windowRef.webContents.capturePage()).toPNG());
    windowRef.webContents.sendInputEvent({ type: "keyDown", keyCode: "Escape" });
    await waitFor(() => evaluate(() => !document.querySelector('[data-operation="merge"]')));
  }
  await evaluate(() => document.querySelector("#scm-tab-history").click());
  assert.equal(await evaluate(() => Boolean(document.querySelector(".scm-composer"))), false);
  assert.equal(await evaluate(() => document.querySelector("#scm-content").textContent.includes("No commits yet.")), true);
}
