/* global __dirname, process, console, clearTimeout, setTimeout, document, window */
// Links in terminal output with the real xterm: print a web address, a project file with
// a line and a path that doesn't exist, then hover and click them with the real mouse.
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
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-test-terminal-links-"));
process.once("exit", () => fs.rmSync(userData, { recursive: true, force: true }));
app.setPath("userData", userData);
const deadline = setTimeout(() => { console.error("Timed out"); app.exit(1); }, 60000);
let server;
app.whenReady().then(async () => {
  const { createServer } = await import("vite");
  server = await createServer({
    cacheDir: testViteCacheDir("terminal-links"),
    configFile: false,
    logLevel: "error",
    root: path.resolve(__dirname, ".."),
    server: { host: "127.0.0.1", port: 0 },
    plugins: [(await import("@vitejs/plugin-react")).default(), { name: "terminal-links-fixture", configureServer(dev) {
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
  const evaluate = fn => win.webContents.executeJavaScript(`Promise.resolve((${fn})())`);
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const waitFor = async (fn, message) => {
    for (let i = 0; i < 100; i++) {
      if (await evaluate(fn)) return;
      await pause(50);
    }
    assert.fail(message);
  };
  // Trusted mouse input through the DevTools protocol, which xterm's link handling sees
  // as a real pointer. (sendInputEvent delivers nothing on the headless platform.)
  win.webContents.debugger.attach("1.3");
  // The headless platform gives the page a 1x1 viewport; give it the window's size so
  // the pointer lands on the terminal.
  await win.webContents.debugger.sendCommand("Emulation.setDeviceMetricsOverride", { width: 1000, height: 520, deviceScaleFactor: 1, mobile: false });
  // A hidden test window never gets the system's focus; let the page keep it anyway.
  await win.webContents.debugger.sendCommand("Emulation.setFocusEmulationEnabled", { enabled: true });
  const mouse = (type, { x, y }, modifiers = 0) => win.webContents.debugger.sendCommand("Input.dispatchMouseEvent", {
    type, x, y, modifiers, button: type === "mouseMoved" ? "none" : "left", buttons: type === "mousePressed" ? 1 : 0, clickCount: type === "mouseMoved" ? 0 : 1,
  });
  // The middle of a cell, by 1-based column and row, in window coordinates.
  const cell = (column, row) => evaluate(new Function(`
    const terminal = window.terminals.at(-1);
    const box = document.querySelector(".xterm-screen").getBoundingClientRect();
    const width = box.width / terminal.cols, height = box.height / terminal.rows;
    return { x: box.left + (${column} - 0.5) * width, y: box.top + (${row} - 0.5) * height };
  `));
  const tooltip = () => evaluate(() => document.querySelector('[role="tooltip"]')?.textContent ?? null);
  const hover = async (column, row) => {
    await mouse("mouseMoved", await cell(1, 6));
    await pause(50);
    await mouse("mouseMoved", await cell(column, row));
  };
  const click = async (column, row, modifiers = 0) => {
    const point = await cell(column, row);
    await mouse("mouseMoved", point, modifiers);
    // xterm finds the link under the pointer asynchronously (a file's existence check).
    await pause(150);
    await mouse("mousePressed", point, modifiers);
    await mouse("mouseReleased", point, modifiers);
    await pause(100);
  };
  const opened = () => evaluate(() => ({ files: window.openedFiles, urls: window.openedUrls }));
  const CONTROL = 2;

  await waitFor(() => window.sessionReady(), "The terminal session must start");
  await evaluate(() => window.printInTerminal("See https://example.com/x now\r\nError in src/a.py:3 here\r\nmissing.py:4 is gone\r\n"));

  // Hovering names the target; the address starts at column 5 of row 1.
  await hover(10, 1);
  await waitFor(() => document.querySelector('[role="tooltip"]')?.textContent.startsWith("https://example.com/x"), "Hovering a web address must name it");
  assert.equal(await tooltip(), "https://example.com/x\nCtrl+click to open");
  await hover(14, 2);
  await waitFor(() => document.querySelector('[role="tooltip"]')?.textContent.startsWith("/fixture/src/a.py, line 3"), "Hovering a file path must name the file and line");

  // A plain click opens nothing.
  await click(10, 1);
  await click(14, 2);
  assert.deepEqual(await opened(), { files: [], urls: [] }, "A plain click must not open a link");

  // Ctrl+click opens the address in the browser and the file in the editor at its line.
  await click(10, 1, CONTROL);
  await click(14, 2, CONTROL);
  assert.deepEqual(await opened(), {
    files: [{ lineNumber: 3, path: "/fixture/src/a.py", projectRoot: "/fixture" }],
    urls: ["https://example.com/x"],
  });

  // A path that doesn't exist is plain text: no tooltip, and Ctrl+click does nothing.
  await hover(3, 3);
  await pause(300);
  assert.equal(await tooltip(), null, "A missing file must not be a link");
  await click(3, 3, CONTROL);
  assert.equal((await opened()).files.length, 1);

  assert.deepEqual(errors, []);
  console.log("Terminal links: hover, plain click, Ctrl+click and missing files passed.");
  win.destroy(); await server.close(); clearTimeout(deadline); app.quit();
}).catch(async error => { console.error(error); if (server) await server.close(); app.exit(1); });
