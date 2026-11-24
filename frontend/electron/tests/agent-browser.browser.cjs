// Drive real webview tabs through the agent browser controller: on screen,
// behind another tab, and fully hidden, with two agents working at once.
require("./background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const http = require("node:http");
const { app, BrowserWindow, WebContentsView, nativeImage } = require("electron");
const { createAgentBrowserController } = require("../agent-browser.cjs");
const { normalizeBrowserUrl } = require("../browser-utils.cjs");
const { checkSlowFramePage, createSlowFramePages } = require("./slow-frame-page.cjs");
const { checkHiddenTextPages, pageText, serveHiddenTextPages } = require("./hidden-text-pages.cjs");

app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
const timer = setTimeout(() => finish(new Error("Agent browser regression timed out")), 120000);

const PAGE = `<!doctype html><title>Form</title>
<h1>Order form</h1>
<p>Plain <b>bold</b> text block.</p>
<label for=name>Name</label><input id=name value="old">
<select id=size aria-label="Size"><option value=s>Small</option><option value=l>Large</option></select>
<div contenteditable=true aria-label="Notes"></div>
<button id=go onclick="document.title=['sent',document.getElementById('name').value,document.getElementById('size').value,document.querySelector('[contenteditable]').innerText.trim()].join(':')">Send</button>
<a href="/next">Next page</a>
<input id=keys aria-label="Keys" onkeydown="if(event.key==='Enter')document.title='enter:'+this.value">
<button hidden>Hidden button</button>
<form id=search onsubmit="event.preventDefault();document.title='searched:'+this.q.value"><input name=q aria-label="Query"><button>Search</button></form>
<textarea aria-label="Body" onkeydown="if(event.key==='Enter')document.title='newline:'+this.value"></textarea>
<button id=delete onclick="document.title='deleted'">Delete account</button>
<div style="position:relative"><button id=under onclick="document.title='under'">Under</button>
<iframe srcdoc="<p>ad</p>" style="position:absolute;left:0;top:0;width:200px;height:60px;border:0"></iframe></div>`;

function finish(error) {
  clearTimeout(timer);
  if (error) console.error(error);
  else console.log("Agent browser regression passed.");
  app.exit(error ? 1 : 0);
}

app.whenReady().then(async () => {
  const slowPages = createSlowFramePages();
  const server = http.createServer((request, response) => {
    if (slowPages.serve(request, response)) return;
    if (serveHiddenTextPages(request, response)) return;
    response.setHeader("Content-Type", "text/html");
    response.end(request.url === "/next" ? "<title>Next</title><p>Arrived</p>" : PAGE);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const window = new BrowserWindow({
    width: 1200, height: 800, show: true,
    webPreferences: { webviewTag: true, contextIsolation: true },
  });
  // The user keeps typing in the studio while agents work.
  await window.loadURL("data:text/html,<body style='margin:0'><input id=user autofocus>"
    + "<div id=screen style='position:fixed;inset:0;top:40px'></div>"
    + "<div id=behind style='position:fixed;inset:0;opacity:0'></div>"
    + "<div id=hidden style='display:none'></div></body>");

  const sessions = new Map();
  const hosts = ["screen", "behind", "hidden"];
  const tabs = {
    openTab: ({ agent }) => {
      const id = `tab-${sessions.size + 1}`;
      const host = hosts[sessions.size % hosts.length];
      const session = { id, agent: { id: agent }, host, viewSize: host === "hidden" ? { width: 0, height: 0 } : { width: 1200, height: 800 } };
      sessions.set(id, session);
      const attached = new Promise((resolve) => {
        const listener = (_event, contents) => {
          if (contents.getType() !== "webview") return;
          app.removeListener("web-contents-created", listener);
          contents.once("dom-ready", () => { session.contents = contents; resolve(); });
        };
        app.on("web-contents-created", listener);
      });
      void window.webContents.executeJavaScript(`(() => {
        const view = document.createElement("webview");
        view.src = "about:blank";
        view.style.cssText = "width:100%;height:100%";
        document.getElementById(${JSON.stringify(host)}).appendChild(view);
      })()`);
      void attached;
      return session;
    },
    agentTabs: (agent) => [...sessions.values()].filter((item) => item.agent.id === agent && !item.closed),
    closeTab: (session) => { session.closed = true; sessions.delete(session.id); session.contents?.close?.(); },
    normalizeUrl: normalizeBrowserUrl,
  };
  const controller = createAgentBrowserController(tabs);
  const call = (agent, action, extra = {}) => controller.handle({ agent, action, ...extra });

  // Three agents open tabs in parallel: on screen, behind, and hidden.
  const agents = ["agent-screen-0001", "agent-behind-0002", "agent-hidden-0003"];
  const opened = [];
  for (const agent of agents) opened.push(await call(agent, "open", { url: base }));
  assert.deepEqual(opened.map((tab) => tab.title), ["Form", "Form", "Form"]);

  await Promise.all(agents.map(async (agent) => {
    const snapshot = pageText((await call(agent, "snapshot")).snapshot, base);
    assert.match(snapshot, /^Page URL: http:\/\/127\.0\.0\.1:\d+\/$/m);
    assert.match(snapshot, /- heading "Order form" \[level=1\]/);
    assert.match(snapshot, /- text: "Plain bold text block\."/);
    assert.match(snapshot, /- link "Next page" \[url=.*\/next\] \[ref=e\d+\]/);
    assert.doesNotMatch(snapshot, /Hidden button/);
    const ref = (pattern) => snapshot.match(pattern)[1];
    await call(agent, "type", { ref: ref(/- textbox "Name" value="old" \[ref=(e\d+)\]/), text: agent });
    await call(agent, "select", { ref: ref(/- combobox "Size" .*\[ref=(e\d+)\]/), values: ["Large"] });
    await call(agent, "type", { ref: ref(/- textbox "Notes" \[ref=(e\d+)\]/), text: "fragile" });
    const sent = await call(agent, "click", { ref: ref(/- button "Send" \[ref=(e\d+)\]/) });
    assert.equal(sent.title, `sent:${agent}:l:fragile`, agent);
    await call(agent, "type", { selector: "#keys", text: "kx", submit: false });
    assert.equal((await call(agent, "press", { key: "Backspace" })).focused, "input#keys");
    await call(agent, "press", { key: "Enter" });
    assert.equal((await call(agent, "tabs")).tabs[0].title, "enter:k");
    assert.equal((await call(agent, "press", { key: "Shift+Tab" })).focused, "a");
    const evaluated = await call(agent, "evaluate", { expression: "Promise.resolve(innerWidth > 300)" });
    assert.equal(evaluated.tab, opened[agents.indexOf(agent)].tab);
    assert.equal(pageText(evaluated.result, base), "true");
  }));

  // Hidden tabs still lay out at a desktop size.
  assert.equal(pageText((await call(agents[2], "evaluate", { expression: "innerWidth" })).result, base), "1280");

  // Screenshots need a laid-out view, which includes tabs behind the active one.
  for (const agent of agents.slice(0, 2)) {
    const shot = await call(agent, "screenshot");
    assert.ok(shot.width > 300 && shot.image.length > 1000, agent);
  }
  // A <webview> in display:none has no size and no picture at all.
  await assert.rejects(call(agents[2], "screenshot"), /no image to capture/);

  // Raticode (chromium) tabs are WebContentsViews. One that isn't shown keeps
  // its size; once Rem uses it, background throttling is off, so it keeps
  // drawing and Rem gets a real picture while it stays hidden.
  const hiddenView = new WebContentsView();
  window.contentView.addChildView(hiddenView);
  hiddenView.setBounds({ x: 0, y: 0, width: 1280, height: 800 });
  hiddenView.setVisible(false);
  // As createView does: every view starts on about:blank.
  await hiddenView.webContents.loadURL("about:blank");
  const viewSessions = [];
  const viewController = createAgentBrowserController({
    openTab: ({ agent }) => {
      // As browser-sessions.cjs records a view that isn't placed on screen.
      const session = { id: "view-1", agent: { id: agent }, contents: hiddenView.webContents, view: hiddenView, viewShown: false, viewSize: { width: 1280, height: 800 } };
      viewSessions.push(session);
      return session;
    },
    agentTabs: () => viewSessions,
    closeTab: () => {},
    normalizeUrl: normalizeBrowserUrl,
  });
  // Before Rem uses it, a hidden view has no picture ("Current display
  // surface not available for capture").
  const plain = await hiddenView.webContents.capturePage().then((image) => (image.isEmpty() ? "empty" : "image"), (error) => error.message);
  assert.notEqual(plain, "image", "capturePage now works on hidden views; hiddenCapture may be unneeded");
  await viewController.handle({ agent: "agent-view-0004", action: "open", url: base });
  // Whatever had focus before keeps it: the capture never shows or focuses the view.
  const focusedWindow = BrowserWindow.getFocusedWindow()?.id ?? null;
  const pageFocused = window.webContents.isFocused();
  const viewFocused = hiddenView.webContents.isFocused();
  const viewShot = await viewController.handle({ agent: "agent-view-0004", action: "screenshot" });
  assert.equal(viewShot.width, 1280);
  assert.equal(viewShot.height, 800);
  // A real picture of the page, not a blank one: the form's text is drawn.
  const picture = nativeImage.createFromBuffer(Buffer.from(viewShot.image, "base64"));
  assert.equal(picture.isEmpty(), false);
  const bitmap = picture.toBitmap();
  let dark = 0;
  for (let index = 0; index < bitmap.length; index += 4) if (bitmap[index] < 128 && bitmap[index + 1] < 128 && bitmap[index + 2] < 128) dark += 1;
  assert.ok(dark > 100, `only ${dark} dark pixels in the screenshot`);
  assert.equal(hiddenView.getVisible(), false);
  assert.equal(hiddenView.webContents.isFocused(), viewFocused);
  assert.equal(BrowserWindow.getFocusedWindow()?.id ?? null, focusedWindow);
  assert.equal(window.webContents.isFocused(), pageFocused);
  window.contentView.removeChildView(hiddenView);
  hiddenView.webContents.close();

  await assert.rejects(call(agents[0], "click", { ref: "e999" }), /Take a new snapshot/);
  await assert.rejects(call(agents[0], "navigate", { url: "file:///etc/passwd" }), /http and https/);
  await assert.rejects(call(agents[0], "evaluate", { expression: "missing.value" }), /missing is not defined/);

  const { snapshot } = await call(agents[0], "snapshot");
  await call(agents[0], "click", { ref: snapshot.match(/- link "Next page" .*\[ref=(e\d+)\]/)[1] });
  assert.equal((await call(agents[0], "wait", { text: "Arrived", time: 5 })).title, "Next");
  assert.equal((await call(agents[0], "back")).title, "Form");
  await assert.rejects(call(agents[0], "wait", { text: "Never shown", time: 0.5 }), /did not appear/);

  // Agents cannot see each other's tabs, and the user's field kept focus.
  await assert.rejects(call(agents[1], "snapshot", { tab: opened[0].tab }), /another task/);
  assert.equal(await window.webContents.executeJavaScript("document.activeElement.id"), "user");
  await call(agents[0], "close");
  await assert.rejects(call(agents[0], "snapshot"), /browser_open first/);

  // With approval on for every site, Enter still never asks, but a click on
  // a consequential control does, and a declined click never reaches the page.
  const asked = [];
  const approvals = createAgentBrowserController({
    ...tabs,
    policy: () => ({ remConfirm: "always", remSignInSites: [] }),
    confirm: async (_session, details) => { asked.push(`${details.action}:${details.label}`); return false; },
  });
  const careful = "agent-careful-0004";
  const ask = (action, extra = {}) => approvals.handle({ agent: careful, action, ...extra });
  await ask("open", { url: base });
  await ask("type", { selector: "#search input", text: "cats", submit: true });
  assert.equal((await ask("tabs")).tabs[0].title, "searched:cats");
  await ask("type", { selector: "textarea", text: "line" });
  await ask("press", { key: "Enter" });
  assert.equal((await ask("tabs")).tabs[0].title, "newline:line");
  assert.deepEqual(asked, []);
  await assert.rejects(ask("click", { selector: "#delete" }), /did not approve "Delete account"/);
  assert.deepEqual(asked, ["click:Delete account"]);
  assert.equal((await ask("tabs")).tabs[0].title, "newline:line");
  await ask("close");

  // Real input (Firefox tabs) would land on a frame covering the element, so
  // such a click is refused before any input is sent.
  const native = [];
  const framed = "agent-framed-0005";
  await call(framed, "open", { url: base });
  const framedTab = [...sessions.values()].find((item) => item.agent.id === framed);
  framedTab.contents.nativeInput = { click: async (input) => { native.push(input); } };
  await assert.rejects(call(framed, "click", { selector: "#under" }), /frame from another page covers/);
  assert.deepEqual(native, []);
  await call(framed, "click", { selector: "#delete" });
  assert.equal(native.length, 1);
  await call(framed, "close");

  // Text a page keeps off screen stays out of Rem's snapshot, in a tab on
  // screen, behind another tab and in a hidden one, and what Rem reads is
  // labelled page content. Three tabs in a row land on each host once,
  // however many tabs the steps above opened.
  const tricksters = ["agent-tricks-0007", "agent-tricks-0008", "agent-tricks-0009"];
  for (const agent of tricksters) await call(agent, "open", { url: `${base}/hidden/kept` });
  const trickHosts = tricksters.map((agent) => [...sessions.values()].find((item) => item.agent.id === agent).host);
  assert.deepEqual([...trickHosts].sort(), ["behind", "hidden", "screen"]);
  for (const [index, agent] of tricksters.entries()) {
    await checkHiddenTextPages((action, extra) => call(agent, action, extra), base, `${trickHosts[index]} Raticode tab`);
    await call(agent, "close");
  }

  // A frame that never finishes doesn't hold up Rem's actions.
  await checkSlowFramePage((action, extra) => call("agent-slow-0006", action, extra), base, "Raticode tab");
  slowPages.stop();
  server.close();
}).then(() => finish(), finish);
