// Downloads in Raticode's own browser tabs, end to end on real Electron: files a
// page builds itself (blob: and data: links, as Open WebUI's "Download" does),
// files from a server, private tabs, and Save link as… on a blob. Rem's refusal
// is covered in browser-sessions.test.cjs. Runs on a hidden display with its
// own profile and Downloads folder.
require("./background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const electron = require("electron");
const { app, BrowserWindow } = electron;
const { createBrowserSessions } = require("../browser-sessions.cjs");
const { createBrowserStore } = require("../browser-store.cjs");

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-browser-downloads-test-"));
const downloadsDir = path.join(profile, "Downloads");
fs.mkdirSync(downloadsDir);
app.setPath("userData", profile);
app.setPath("downloads", downloadsDir);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const timer = setTimeout(() => finish(new Error("Browser downloads test timed out")), 90000);

const HTML = "<!doctype html><title>Chat</title><p>Exported chat</p>";
// Each case is one way pages hand the user a file they built in the page.
const PAGE = `<!doctype html><title>Downloads</title><body>
<script>
const html = ${JSON.stringify(HTML)};
function link(href, name, extra = {}) {
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  Object.assign(a, extra);
  return a;
}
window.cases = {
  // FileSaver.js, which Open WebUI uses: a detached link, clicked on the next
  // task, its blob revoked 40 seconds later.
  fileSaver(name) {
    const a = link(URL.createObjectURL(new Blob([html], { type: "text/html" })), name, { rel: "noopener" });
    setTimeout(() => URL.revokeObjectURL(a.href), 40000);
    setTimeout(() => a.dispatchEvent(new MouseEvent("click")), 0);
  },
  // Pages that revoke the blob straight after clicking.
  revokeNow(name) {
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    const a = link(url, name);
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
  newTab(name) {
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    link(url, name, { target: "_blank" }).click();
  },
  dataUrl(name) {
    link("data:text/html;charset=utf-8," + encodeURIComponent(html), name).click();
  },
  server(name) {
    link("/file.html", name).click();
  },
};
</script>`;

let server;
function finish(error) {
  clearTimeout(timer);
  server?.close();
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* test-tmp removes the run folder. */ }
  if (error) console.error(error);
  else console.log("Browser downloads regression passed.");
  app.exit(error ? 1 : 0);
}

async function waitFor(check, label, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = check();
    if (value) return value;
    if (Date.now() > deadline) throw new Error(`Timed out waiting for ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

app.whenReady().then(async () => {
  server = http.createServer((request, response) => {
    if (request.url === "/file.html") {
      response.setHeader("Content-Type", "text/html");
      response.end(HTML);
      return;
    }
    response.setHeader("Content-Type", "text/html");
    response.end(PAGE);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}/`;

  const window = new BrowserWindow({ width: 1000, height: 700, show: true, webPreferences: { webviewTag: true, contextIsolation: true } });
  const store = createBrowserStore(path.join(profile, "browser"), { writeDelayMs: 0 });
  const browsers = createBrowserSessions({
    app,
    Menu: electron.Menu,
    clipboard: electron.clipboard,
    dialog: electron.dialog,
    shell: electron.shell,
    session: electron.session,
    webContents: electron.webContents,
    getMainWindow: () => window,
    resolvePath: () => { throw new Error("No files in this test."); },
    store,
    quarantineDir: path.join(profile, "browser", "quarantine"),
  });
  browsers.install();
  window.webContents.on("will-attach-webview", (event, webPreferences, params) => {
    if (!browsers.isPendingAttach(window.webContents.id, params)) event.preventDefault();
    webPreferences.preload = path.join(__dirname, "..", "browser-preload.cjs");
    webPreferences.contextIsolation = true;
    webPreferences.sandbox = true;
  });
  window.webContents.on("did-attach-webview", (_event, guest) => browsers.hardenGuest(guest));
  await window.loadURL("data:text/html,<body style='margin:0'>");
  const event = { sender: window.webContents };

  async function openTab(options) {
    const state = browsers.createBrowser(event, options);
    const attached = new Promise((resolve) => window.webContents.once("did-attach-webview", (_e, guest) => resolve(guest)));
    await window.webContents.executeJavaScript(`(() => {
      const view = document.createElement("webview");
      view.partition = ${JSON.stringify(state.partition)};
      view.src = ${JSON.stringify(state.src)};
      view.style.cssText = "width:100%;height:300px";
      document.body.append(view);
    })()`);
    const guest = await attached;
    browsers.browserAction(event, { action: "adopt", id: state.id, webContentsId: guest.id });
    if (guest.isLoading() || guest.getURL() !== base) await new Promise((resolve) => guest.once("did-finish-load", resolve));
    return { id: state.id, guest };
  }

  // The page's own click, with the user's click behind it.
  async function trigger(guest, name, filename) {
    await guest.executeJavaScript(`cases.${name}(${JSON.stringify(filename)})`, true);
  }

  async function downloaded(filename) {
    const entry = await waitFor(() => store.listDownloads().find((item) => item.filename === filename && item.state !== "progressing"), `${filename} to finish`);
    assert.equal(entry.state, "completed", `${filename}: ${entry.state}`);
    assert.equal(entry.path, path.join(downloadsDir, filename));
    assert.equal(fs.readFileSync(entry.path, "utf8"), HTML, filename);
    return entry;
  }

  const user = await openTab({ url: base });
  for (const [name, filename] of [
    ["server", "server.html"],
    ["fileSaver", "file-saver.html"],
    ["revokeNow", "revoke-now.html"],
    ["newTab", "new-tab.html"],
    ["dataUrl", "data-url.html"],
  ]) {
    await trigger(user.guest, name, filename);
    const entry = await downloaded(filename);
    assert.ok(!entry.private, filename);
  }
  // A page's file never leaves its own tab for a new one.
  assert.equal(user.guest.getURL(), base);

  // Private tabs download too, and their list entries stay out of the saved list.
  const privateTab = await openTab({ url: base, private: true });
  await trigger(privateTab.guest, "fileSaver", "private.html");
  const privateEntry = await waitFor(() => store.listDownloads().find((item) => item.filename === "private.html" && item.state === "completed"), "the private download");
  assert.equal(privateEntry.private, true);
  assert.equal(fs.readFileSync(path.join(downloadsDir, "private.html"), "utf8"), HTML);
  store.flush();
  const saved = JSON.parse(fs.readFileSync(path.join(profile, "browser", "downloads.json"), "utf8"));
  assert.ok(!JSON.stringify(saved).includes("private.html"), "a private download was written to disk");

  // Save link as… on a page's own file: Raticode asks Chromium for the blob
  // the page still holds, by its address.
  const kept = await user.guest.executeJavaScript(`URL.createObjectURL(new Blob([${JSON.stringify(HTML)}], { type: "text/html" }))`);
  assert.match(kept, /^blob:http:\/\/127\.0\.0\.1:\d+\//);
  user.guest.downloadURL(kept);
  const linkEntry = await waitFor(() => store.listDownloads().find((item) => item.url === kept && item.state !== "progressing"), "the saved blob link");
  assert.equal(linkEntry.state, "completed");
  assert.equal(fs.readFileSync(linkEntry.path, "utf8"), HTML);

  finish();
}).catch(finish);
