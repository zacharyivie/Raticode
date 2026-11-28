/* global __dirname, process, console, clearTimeout, setTimeout, document, window, MouseEvent */
// Find in the terminal with the real xterm and @xterm/addon-search: print known lines,
// search them from the find bar and check what the addon selected.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
const { app, BrowserWindow } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-dev-shm-usage");
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-setuid-sandbox");
process.chdir(path.resolve(__dirname, ".."));
// Keep Electron's caches out of the developer's own Electron folder.
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-test-terminal-find-"));
process.once("exit", () => fs.rmSync(userData, { recursive: true, force: true }));
app.setPath("userData", userData);
const deadline = setTimeout(() => { console.error("Timed out"); app.exit(1); }, 60000);
let server;
app.whenReady().then(async () => {
  const { createServer } = await import("vite");
  server = await createServer({
    cacheDir: testViteCacheDir("terminal-find"),
    configFile: false,
    logLevel: "error",
    root: path.resolve(__dirname, ".."),
    server: { host: "127.0.0.1", port: 0 },
    plugins: [(await import("@vitejs/plugin-react")).default(), { name: "terminal-find-fixture", configureServer(dev) {
      dev.middlewares.use(async (req, res, next) => {
        if (req.url !== "/") return next();
        res.setHeader("Content-Type", "text/html");
        res.end(await dev.transformIndexHtml("/", '<html><body><div id="root"></div><script type="module" src="/browser-tests/terminal-fixture.jsx"></script></body></html>'));
      });
    } }],
  });
  await server.listen();
  const win = new BrowserWindow({ width: 1000, height: 520, show: true, webPreferences: { sandbox: false, contextIsolation: true } });
  const errors = [];
  win.webContents.on("console-message", ({ level, message }) => { if (level === "error") errors.push(message); });
  await win.loadURL(server.resolvedUrls.local[0]);
  win.focus();
  win.webContents.focus();
  const evaluate = fn => win.webContents.executeJavaScript(`Promise.resolve((${fn})())`);
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const waitFor = async (fn, message) => {
    for (let i = 0; i < 100; i++) {
      if (await evaluate(fn)) return;
      await pause(50);
    }
    const seen = await evaluate(() => ({ focus: document.activeElement?.getAttribute("aria-label") || document.activeElement?.className, shellInput: window.shellInput, pressed: window.keysPressed, keys: window.keysReachingWindow }));
    assert.fail(`${message}: ${JSON.stringify(seen)}`);
  };
  // What the addon selected in the terminal, and what the find bar shows.
  const state = () => evaluate(() => {
    const terminal = window.terminals.at(-1);
    const bar = document.querySelector('[role="search"][aria-label="Find in terminal"]');
    return {
      barOpen: Boolean(bar),
      count: bar?.querySelector("[aria-live]")?.textContent ?? null,
      terminalFocused: Boolean(document.activeElement?.classList.contains("xterm-helper-textarea")),
      selection: terminal.getSelection(),
      start: terminal.getSelectionPosition()?.start ?? null,
    };
  });
  // Trusted key presses through the DevTools protocol, sent to whatever has focus as the
  // keyboard would. (sendInputEvent delivers nothing on the headless platform.)
  win.webContents.debugger.attach("1.3");
  // A hidden test window never gets the system's focus; let the page keep it anyway.
  await win.webContents.debugger.sendCommand("Emulation.setFocusEmulationEnabled", { enabled: true });
  const KEYS = { Enter: ["Enter", 13, "\r"], Escape: ["Escape", 27, ""], F: ["KeyF", 70, ""] };
  const press = async (key, modifiers = []) => {
    const [code, windowsVirtualKeyCode, text] = KEYS[key];
    const bits = modifiers.reduce((sum, name) => sum + { alt: 1, control: 2, meta: 4, shift: 8 }[name], 0);
    const name = key === "F" && !modifiers.includes("shift") ? "f" : key;
    const event = { code, key: name, modifiers: bits, windowsVirtualKeyCode };
    await win.webContents.debugger.sendCommand("Input.dispatchKeyEvent", { ...event, type: text ? "keyDown" : "rawKeyDown", ...(text ? { text } : {}) });
    await win.webContents.debugger.sendCommand("Input.dispatchKeyEvent", { ...event, type: "keyUp" });
    await pause(50);
  };
  const typeInFind = text => evaluate(new Function(`
    const input = document.querySelector('input[aria-label="Find in terminal"]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, ${JSON.stringify(text)});
    input.dispatchEvent(new Event("input", { bubbles: true }));
  `));

  await waitFor(() => window.sessionReady(), "The terminal session must start");
  await evaluate(() => window.printInTerminal("alpha one\r\nbravo needle two\r\ncharlie three\r\nNeedle again\r\n"));
  await evaluate(() => document.querySelector(".xterm-helper-textarea").focus());

  // Plain Ctrl+F still belongs to the shell (readline's forward-char).
  await press("F", ["control"]);
  await waitFor(() => window.shellInput.includes("\x06"), "Ctrl+F must reach the shell");
  assert.equal((await state()).barOpen, false, "Ctrl+F alone doesn't open Find");

  // Ctrl+Shift+F in the terminal opens the find bar and doesn't reach project search.
  await evaluate(() => { window.keysReachingWindow = []; window.shellInput = []; });
  await press("F", ["control", "shift"]);
  await waitFor(() => document.activeElement?.getAttribute("aria-label") === "Find in terminal", "Ctrl+Shift+F must open the find bar and focus its text field");
  assert.deepEqual(await evaluate(() => window.keysReachingWindow), [], "The terminal keeps Ctrl+Shift+F from project search");
  assert.deepEqual(await evaluate(() => window.shellInput), [], "Find must not send the key to the shell");

  await typeInFind("needle");
  await waitFor(() => window.terminals.at(-1).getSelection() === "needle", "Typing must select the first match");
  let now = await state();
  assert.deepEqual(now.start, { x: 6, y: 1 }, "The addon selects 'needle' on the second line");
  await waitFor(() => document.querySelector('[role="search"] [aria-live]').textContent === "1 of 2", "The bar shows the addon's count, ignoring case by default");

  await press("Enter");
  await waitFor(() => window.terminals.at(-1).getSelectionPosition()?.start.y === 3, "Enter must go to the next match");
  now = await state();
  assert.equal(now.selection, "Needle");
  assert.equal(now.count, "2 of 2");

  await press("Enter");
  await waitFor(() => window.terminals.at(-1).getSelectionPosition()?.start.y === 1, "Next wraps from the last match to the first");
  await press("Enter", ["shift"]);
  await waitFor(() => window.terminals.at(-1).getSelectionPosition()?.start.y === 3, "Shift+Enter wraps from the first match to the last");
  await press("Enter", ["shift"]);
  await waitFor(() => window.terminals.at(-1).getSelectionPosition()?.start.y === 1, "Shift+Enter must go to the previous match");

  await evaluate(() => document.querySelector('button[aria-label="Next match"]').click());
  await waitFor(() => window.terminals.at(-1).getSelectionPosition()?.start.y === 3, "Next must go to the next match");
  await evaluate(() => document.querySelector('button[aria-label="Previous match"]').click());
  await waitFor(() => window.terminals.at(-1).getSelectionPosition()?.start.y === 1, "Previous must go to the previous match");

  await evaluate(() => document.querySelector('button[aria-label="Match case"]').click());
  await waitFor(() => document.querySelector('[role="search"] [aria-live]').textContent === "1 of 1", "Match case must skip 'Needle'");
  assert.equal(await evaluate(() => document.querySelector('button[aria-label="Match case"]').getAttribute("aria-pressed")), "true");
  await typeInFind("missing words");
  await waitFor(() => document.querySelector('[role="search"] [aria-live]').textContent === "No results", "A term that isn't there says so");
  await typeInFind("needle");
  await waitFor(() => window.terminals.at(-1).getSelection() === "needle", "The match is selected again");

  // Escape closes the bar and puts focus back in the terminal.
  await press("Escape");
  await waitFor(() => !document.querySelector('[role="search"]'), "Escape must close the find bar");
  now = await state();
  assert.equal(now.terminalFocused, true, "Escape returns focus to the terminal");

  // The right-click menu has Find too, and it starts from a one-line selection.
  await evaluate(() => window.terminals.at(-1).select(0, 2, 7));
  await evaluate(() => document.querySelector(".terminal-host").dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 200, clientY: 120 })));
  await waitFor(() => [...document.querySelectorAll('[role="menuitem"]')].some(item => item.textContent.startsWith("Find")), "The terminal's menu must list Find");
  assert.match(await evaluate(() => [...document.querySelectorAll('[role="menuitem"]')].find(item => item.textContent.startsWith("Find")).textContent), /Ctrl\+Shift\+F$/);
  await evaluate(() => [...document.querySelectorAll('[role="menuitem"]')].find(item => item.textContent.startsWith("Find")).click());
  await waitFor(() => document.activeElement?.getAttribute("aria-label") === "Find in terminal", "Find from the menu must focus the find bar");
  assert.equal(await evaluate(() => document.querySelector('input[aria-label="Find in terminal"]').value), "charlie");
  await evaluate(() => document.querySelector('button[aria-label="Close find"]').click());
  await waitFor(() => !document.querySelector('[role="search"]') && document.activeElement?.classList.contains("xterm-helper-textarea"), "Close must close the bar and focus the terminal");

  assert.deepEqual(errors, []);
  console.log("Terminal Find: shortcut, count, Next, Previous, Match case, Escape and the right-click menu passed.");
  win.destroy(); await server.close(); clearTimeout(deadline); app.quit();
}).catch(async error => { console.error(error); if (server) await server.close(); app.exit(1); });
