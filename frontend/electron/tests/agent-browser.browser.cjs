// Drive real webview tabs through the agent browser controller: on screen,
// behind another tab, and fully hidden, with two agents working at once.
const assert = require("node:assert/strict");
const http = require("node:http");
const { app, BrowserWindow } = require("electron");
const { createAgentBrowserController } = require("../agent-browser.cjs");
const { normalizeBrowserUrl } = require("../browser-utils.cjs");

app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
const timer = setTimeout(() => finish(new Error("Agent browser regression timed out")), 60000);

const PAGE = `<!doctype html><title>Form</title>
<h1>Order form</h1>
<p>Plain <b>bold</b> text block.</p>
<label for=name>Name</label><input id=name value="old">
<select id=size aria-label="Size"><option value=s>Small</option><option value=l>Large</option></select>
<div contenteditable=true aria-label="Notes"></div>
<button id=go onclick="document.title=['sent',document.getElementById('name').value,document.getElementById('size').value,document.querySelector('[contenteditable]').innerText.trim()].join(':')">Send</button>
<a href="/next">Next page</a>
<input id=keys aria-label="Keys" onkeydown="if(event.key==='Enter')document.title='enter:'+this.value">
<button hidden>Hidden button</button>`;

function finish(error) {
  clearTimeout(timer);
  if (error) console.error(error);
  else console.log("Agent browser regression passed.");
  app.exit(error ? 1 : 0);
}

app.whenReady().then(async () => {
  const server = http.createServer((request, response) => {
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
  const controller = createAgentBrowserController({
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
  });
  const call = (agent, action, extra = {}) => controller.handle({ agent, action, ...extra });

  // Three agents open tabs in parallel: on screen, behind, and hidden.
  const agents = ["agent-screen-0001", "agent-behind-0002", "agent-hidden-0003"];
  const opened = [];
  for (const agent of agents) opened.push(await call(agent, "open", { url: base }));
  assert.deepEqual(opened.map((tab) => tab.title), ["Form", "Form", "Form"]);

  await Promise.all(agents.map(async (agent) => {
    const { snapshot } = await call(agent, "snapshot");
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
    assert.deepEqual(await call(agent, "evaluate", { expression: "Promise.resolve(innerWidth > 300)" }), {
      tab: opened[agents.indexOf(agent)].tab, result: "true",
    });
  }));

  // Hidden tabs still lay out at a desktop size.
  assert.equal((await call(agents[2], "evaluate", { expression: "innerWidth" })).result, "1280");

  // Screenshots need a laid-out view, which includes tabs behind the active one.
  for (const agent of agents.slice(0, 2)) {
    const shot = await call(agent, "screenshot");
    assert.ok(shot.width > 300 && shot.image.length > 1000, agent);
  }
  await assert.rejects(call(agents[2], "screenshot"), /not on screen/);

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
  server.close();
}).then(() => finish(), finish);
