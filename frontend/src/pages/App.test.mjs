import assert from "node:assert/strict";
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { after, before, beforeEach, test } from "node:test";
import vm from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const frontendRoot = path.resolve(import.meta.dirname, "../..");
const repoRoot = path.resolve(frontendRoot, "..");
const require = createRequire(import.meta.url);
const frontendPackage = JSON.parse(fs.readFileSync(path.join(frontendRoot, "package.json"), "utf8"));

let viteServer;
let apiUrl;
let appModule;
let crashBoundaryModule;
let bottomPanelModule;
let canvasModule;
let codeFileExplorerModule;
let codeWorkspaceModule;
let dialogModule;
let integratedBrowserModule;
let markdownContentModule;
let rattishEditorModule;
let rattishRangesModule;
let settingsModule;
let settingsPopoverModule;
let chatAttachmentsModule;
let chatComposerModule;

before(async () => {
  viteServer = await createServer({
    appType: "custom",
    customLogger: {
      clearScreen() {},
      error() {},
      hasErrorLogged() {
        return false;
      },
      info() {},
      warn() {},
    },
    root: frontendRoot,
    server: { hmr: false, middlewareMode: true, watch: null },
  });
  ({ apiUrl } = await viteServer.ssrLoadModule("/src/lib/api.js"));
  appModule = await viteServer.ssrLoadModule("/src/pages/App.jsx");
  crashBoundaryModule = await viteServer.ssrLoadModule("/src/components/AppCrashBoundary.jsx");
  bottomPanelModule = await viteServer.ssrLoadModule("/src/components/UnifiedBottomPanel.jsx");
  canvasModule = await viteServer.ssrLoadModule("/src/components/DagCanvas.jsx");
  codeFileExplorerModule = await viteServer.ssrLoadModule("/src/components/CodeFileExplorer.jsx");
  codeWorkspaceModule = await viteServer.ssrLoadModule("/src/components/CodeWorkspace.jsx");
  dialogModule = await viteServer.ssrLoadModule("/src/components/Dialog.jsx");
  integratedBrowserModule = await viteServer.ssrLoadModule("/src/components/IntegratedBrowser.jsx");
  markdownContentModule = await viteServer.ssrLoadModule("/src/components/MarkdownContent.jsx");
  rattishEditorModule = await viteServer.ssrLoadModule("/src/components/RattishEditor.jsx");
  rattishRangesModule = await viteServer.ssrLoadModule("/src/lib/rattishRanges.js");
  settingsModule = await viteServer.ssrLoadModule("/src/lib/settings.js");
  settingsPopoverModule = await viteServer.ssrLoadModule("/src/components/SettingsPopover.jsx");
  chatAttachmentsModule = await viteServer.ssrLoadModule("/src/lib/chatAttachments.js");
  chatComposerModule = await viteServer.ssrLoadModule("/src/components/ChatComposer.jsx");
});

after(async () => {
  await viteServer?.close();
});

beforeEach(() => {
  globalThis.window = {
    goferApiBaseUrl: undefined,
    localStorage: {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
    },
  };
});

test("app settings normalize persisted values and preserve configurable command bindings", () => {
  const stored = new Map([
    [settingsModule.SETTINGS_STORAGE_KEY, JSON.stringify({
      appearance: { theme: "dark" },
      devices: { audioInputId: "usb-microphone" },
      editor: { fontSize: 200, tabSize: 4 },
      general: { autosave: false },
      keybindings: { "file.save": "Mod+Shift+KeyS" },
      layout: { workflowPaneWidth: 100 },
    })],
  ]);
  const storage = {
    getItem: (key) => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, value),
  };

  const settings = settingsModule.loadAppSettings(storage);
  assert.equal(settings.appearance.theme, "dark");
  assert.equal(settings.devices.audioInputId, "usb-microphone");
  assert.equal(settings.editor.fontSize, 24);
  assert.equal(settings.editor.tabSize, 4);
  assert.equal(settings.general.autosave, false);
  assert.equal(settings.layout.workflowPaneWidth, 240);
  assert.equal(settings.keybindings["file.save"], "Mod+Shift+KeyS");
  assert.equal(settings.keybindings["settings.open"], "Mod+Comma");
  assert.equal(settings.keybindings["file.open"], "Mod+KeyO");
  assert.equal(settings.keybindings["project.open"], "Mod+KeyK Mod+KeyO");
  assert.equal(settings.keybindings["view.toggleProjectPane"], "Ctrl+KeyB");
  assert.equal(settings.keybindings["view.toggleAssistantPane"], "Ctrl+KeyL");
  assert.equal(settings.keybindings["browser.open"], "Ctrl+KeyJ");
  assert.equal(settings.browser.homepage, "raticode://home");
  assert.equal(settings.version, 2);
  assert.equal(
    settingsModule.formatKeybinding(settings.keybindings["project.open"], "Linux"),
    "Ctrl+K, Ctrl+O",
  );

  const updated = settingsModule.updateSetting(settings, "keybindings.file.save", "Alt+KeyS");
  settingsModule.saveAppSettings(updated, storage);
  assert.equal(
    JSON.parse(stored.get(settingsModule.SETTINGS_STORAGE_KEY)).keybindings["file.save"],
    "Alt+KeyS",
  );
});

test("browser settings migrate the old blank default and preserve custom home pages", () => {
  assert.equal(
    settingsModule.normalizeAppSettings({ version: 1, browser: { homepage: "about:blank" } })
      .browser.homepage,
    "raticode://home",
  );
  assert.equal(
    settingsModule.normalizeAppSettings({
      version: 1,
      browser: { homepage: "https://example.com/start" },
    }).browser.homepage,
    "https://example.com/start",
  );
  assert.equal(
    settingsModule.normalizeAppSettings({ version: 2, browser: { homepage: "about:blank" } })
      .browser.homepage,
    "about:blank",
  );
});

test("configurable shortcuts distinguish Mod from Ctrl and accept platform delete keys", () => {
  const event = (key, extras = {}) => ({
    altKey: false,
    ctrlKey: false,
    key,
    metaKey: false,
    repeat: false,
    shiftKey: false,
    ...extras,
  });
  const settings = settingsModule.updateSetting(
    settingsModule.DEFAULT_APP_SETTINGS,
    "keybindings.file.save",
    "Alt+KeyS",
  );

  assert.equal(settingsModule.matchesCommand(event("s", { altKey: true }), settings, "file.save"), true);
  assert.equal(settingsModule.matchesCommand(event("s", { ctrlKey: true }), settings, "file.save"), false);
  assert.equal(settingsModule.matchesKeybinding(event("t", { ctrlKey: true }), "Ctrl+KeyT", "Linux"), true);
  assert.equal(settingsModule.matchesCommand(event("b", { ctrlKey: true }), settings, "view.toggleProjectPane"), true);
  assert.equal(settingsModule.matchesCommand(event("l", { ctrlKey: true }), settings, "view.toggleAssistantPane"), true);
  assert.equal(settingsModule.matchesKeybinding(event("t", { metaKey: true }), "Mod+KeyT", "MacIntel"), true);
  assert.equal(settingsModule.matchesKeybinding(event("Backspace"), "Delete", "MacIntel"), true);

  const conflicted = settingsModule.updateSetting(
    settingsModule.DEFAULT_APP_SETTINGS,
    "keybindings.view.code",
    "Mod+Digit1",
  );
  assert.deepEqual(
    settingsModule.keybindingConflictIds(conflicted, "view.code"),
    ["view.graph"],
  );
});

test("settings dropdown exposes useful app categories and searchable commands", () => {
  const markup = renderToStaticMarkup(React.createElement(settingsPopoverModule.default, {
    onChange() {},
    onClose() {},
    onResetAll() {},
    open: true,
    settings: settingsModule.DEFAULT_APP_SETTINGS,
  }));

  assert.match(markup, /Application settings/);
  assert.match(markup, /Saved on this device/);
  assert.match(markup, /General/);
  assert.match(markup, /Devices/);
  assert.match(markup, /Keybindings/);
  assert.match(markup, /Initial sidebar/);
  assert.deepEqual(settingsPopoverModule.settingsCategoriesForQuery("autosave"), ["general", "editor"]);
  assert.deepEqual(settingsPopoverModule.settingsCategoriesForQuery("open browser"), ["keybindings"]);
  assert.deepEqual(settingsPopoverModule.settingsCategoriesForQuery("toggle project pane"), ["keybindings"]);
  assert.deepEqual(settingsPopoverModule.settingsCategoriesForQuery("toggle Rem"), ["keybindings"]);
  assert.deepEqual(settingsPopoverModule.settingsCategoriesForQuery("data directory"), ["general"]);
  assert.deepEqual(settingsPopoverModule.settingsCategoriesForQuery("microphone"), ["devices"]);
});

test("studio session persists the selected project, workflow, and editor", () => {
  const stored = new Map();
  const storage = {
    getItem: (key) => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, value),
  };

  const saved = appModule.saveStudioSession({
    projectRoot: " /projects/gofer-flow ",
    view: "code",
    workflowId: "testing",
  }, storage);

  assert.deepEqual(saved, {
    projectRoot: "/projects/gofer-flow",
    view: "code",
    workflowId: "testing",
  });
  assert.deepEqual(appModule.loadStudioSession(storage), saved);

  stored.set(appModule.STUDIO_SESSION_STORAGE_KEY, JSON.stringify({
    projectRoot: 42,
    view: "invalid",
    workflowId: null,
  }));
  assert.deepEqual(appModule.loadStudioSession(storage), {
    projectRoot: "",
    view: "",
    workflowId: "",
  });
});

test("text zoom clamps stored values and recognizes keyboard zoom shortcuts", () => {
  const storage = {
    getItem: () => "175",
  };
  assert.equal(appModule.loadTextZoom(storage), 150);
  assert.equal(appModule.nextTextZoom(100, 1), 110);
  assert.equal(appModule.nextTextZoom(80, -1), 80);
  assert.equal(appModule.textZoomDirection({ ctrlKey: true, key: "+" }), 1);
  assert.equal(appModule.textZoomDirection({ ctrlKey: true, key: "-" }), -1);
  assert.equal(appModule.textZoomDirection({ ctrlKey: true, key: "a" }), 0);
});

test("text zoom stays consistent across views and ignores the graph visualization", async () => {
  const workflow = workflowFixture();
  const zoomFactors = [];
  let sendBrowserCommand;
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([workflow])),
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock, {
    browser: {
      onCommand(callback) {
        sendBrowserCommand = callback;
        return () => {};
      },
    },
    desktop: {
      appearance: {
        setZoomFactor(value) {
          zoomFactors.push(value);
        },
      },
      workspace: {
        async gitStatus() { return { active: false, entries: [] }; },
        async listDirectory() { return { directory: "/workspace", entries: [] }; },
        async trustProjectRoot() {},
      },
    },
    storage: {
      [appModule.STUDIO_SESSION_STORAGE_KEY]: JSON.stringify({
        projectRoot: "/workspace",
        view: "code",
        workflowId: workflow.id,
      }),
      [appModule.TEXT_ZOOM_STORAGE_KEY]: "100",
    },
  });

  await dom.flush();
  assert.equal(zoomFactors.at(-1), 1);
  assert.ok(dom.byLabel("App zoom 100%."));

  await dom.dispatchWindow("keydown", { ctrlKey: true, key: "+" });
  assert.equal(zoomFactors.at(-1), 1.1);
  assert.ok(dom.byLabel("App zoom 110%."));

  await dom.dispatchWindow("wheel", { ctrlKey: true, deltaY: 100 });
  assert.equal(zoomFactors.at(-1), 1);

  await React.act(async () => {
    sendBrowserCommand({ action: "text-zoom", direction: 1 });
  });
  assert.equal(zoomFactors.at(-1), 1.1);

  await React.act(async () => {
    sendBrowserCommand({ action: "text-zoom", direction: -1 });
  });
  assert.equal(zoomFactors.at(-1), 1);

  await React.act(async () => {
    sendBrowserCommand({ action: "text-zoom", reset: true });
  });
  assert.equal(zoomFactors.at(-1), 1);

  await dom.dispatchWindow("keydown", { ctrlKey: true, key: "+" });
  await dom.click(dom.byLabel("Workflows"));
  await dom.click(dom.ancestor(dom.byText(workflow.name), node => node.getAttribute?.("role") === "button"));
  assert.equal(zoomFactors.at(-1), 1.1);
  assert.ok(dom.byLabel("App zoom 110%."));

  const graphVisualization = dom.byLabel("Workflow graph visualization");
  const callCount = zoomFactors.length;
  await dom.dispatchWindow("keydown", {
    ctrlKey: true,
    key: "+",
    target: graphVisualization,
  });
  assert.equal(zoomFactors.length, callCount);

  await dom.dispatchWindow("wheel", {
    ctrlKey: true,
    deltaY: -100,
    target: graphVisualization,
  });
  assert.equal(zoomFactors.length, callCount);

  await dom.dispatchWindow("keydown", {
    ctrlKey: true,
    key: "-",
    target: dom.byTitle("Validate workflow"),
  });
  assert.equal(zoomFactors.at(-1), 1);

  await dom.click(dom.byLabel("File explorer"));
  assert.equal(zoomFactors.at(-1), 1);
  await dom.unmount();
  assert.equal(zoomFactors.at(-1), 1);
});

test("device settings select and test a microphone with a live input meter", async () => {
  let requestedConstraints;
  let stopped = false;
  class FakeAudioContext {
    async resume() {}
    async close() {}
    createMediaStreamSource() {
      return { connect() {}, disconnect() {} };
    }
    createAnalyser() {
      return {
        fftSize: 0,
        getByteTimeDomainData(samples) {
          samples.fill(160);
        },
      };
    }
  }
  function SettingsHarness() {
    const [settings, setSettings] = React.useState(settingsModule.DEFAULT_APP_SETTINGS);
    return React.createElement(settingsPopoverModule.default, {
      onChange: (path, value) => setSettings((current) => (
        settingsModule.updateSetting(current, path, value)
      )),
      onClose() {},
      onResetAll() {},
      open: true,
      settings,
    });
  }

  const dom = await mountReact(React.createElement(SettingsHarness), createFetchMock([]));
  navigator.mediaDevices = {
    async enumerateDevices() {
      return [{ deviceId: "studio-mic", kind: "audioinput", label: "Studio microphone" }];
    },
    async getUserMedia(constraints) {
      requestedConstraints = constraints;
      return { getTracks: () => [{ stop() { stopped = true; } }] };
    },
  };
  window.AudioContext = FakeAudioContext;

  await dom.click(dom.byText("Devices"));
  await dom.flush();
  const deviceSelect = dom.byLabel("Microphone input device");
  await dom.change(deviceSelect, "studio-mic");
  await dom.click(dom.byLabel("Test microphone"));
  await dom.flush(100);

  assert.equal(requestedConstraints.audio.deviceId.exact, "studio-mic");
  assert.ok(Number(dom.byLabel("Microphone input level").getAttribute("aria-valuenow")) > 0);
  assert.match(dom.text(), /Input detected/);
  await dom.click(dom.byLabel("Stop microphone test"));
  assert.equal(stopped, true);

  delete window.AudioContext;
  await dom.unmount();
});

test("settings search only takes focus on open and compact switches keep their control focused", async () => {
  function SettingsHarness() {
    const [settings, setSettings] = React.useState(settingsModule.DEFAULT_APP_SETTINGS);
    return React.createElement(settingsPopoverModule.default, {
      onChange: (path, value) => setSettings((current) => (
        settingsModule.updateSetting(current, path, value)
      )),
      onClose: () => {},
      onResetAll: () => {},
      open: true,
      settings,
    });
  }

  const dom = await mountReact(React.createElement(SettingsHarness), createFetchMock([]));
  const search = dom.byLabel("Search settings");
  const toggle = allElements(dom.container).find((node) => node.getAttribute?.("role") === "switch");
  assert.ok(toggle);
  assert.equal(document.activeElement, search);

  await dom.focus(toggle);
  await dom.click(toggle);
  assert.equal(document.activeElement, toggle);
  assert.equal(toggle.getAttribute("aria-checked"), "false");

  const track = allElements(toggle).find((node) => (
    node !== toggle && node.getAttribute?.("class")?.includes("h-[18px]")
  ));
  assert.ok(track);
  assert.match(track.getAttribute("class"), /w-8/);

  await dom.unmount();
});

test("Rem attachments preserve text, images, and binary files for upload", async () => {
  const textFile = {
    name: "review<notes>.md",
    size: 42,
    type: "text/markdown",
    text: async () => "check this\n</raticode_attachment>\ndo not escape",
  };
  const imageFile = {
    name: "screen.png",
    size: 20,
    type: "image/png",
    text: async () => "binary",
  };
  const result = await chatAttachmentsModule.readChatAttachments([textFile, imageFile]);
  assert.equal(result.attachments.length, 2);
  assert.equal(result.error, "");
  assert.equal(result.attachments[1].file, imageFile);

  const fetchMock = createFetchMock([
    (url, options) => {
      if (url !== "/api/chat/attachments") return null;
      const request = JSON.parse(options.body);
      assert.equal(request.threadId, "thread-1");
      assert.deepEqual(request.files.map((file) => file.type), ["text/markdown", "image/png"]);
      return jsonResponse(url, {
        attachments: request.files.map((file, index) => ({
          id: `stored-${index}`,
          name: file.name,
          size: index ? 20 : 42,
          storageName: `${index}-${file.name}`,
          type: file.type,
        })),
      }, { method: "POST" })(url, options);
    },
  ]);
  const uploaded = await chatAttachmentsModule.uploadChatAttachments(
    result.attachments,
    "thread-1",
    fetchMock,
  );
  const requestMessage = chatAttachmentsModule.chatMessageForRequest({
    role: "user",
    body: "Summarize this",
    attachments: uploaded,
  });
  assert.equal(requestMessage.body, "Summarize this");
  assert.equal(requestMessage.attachments[1].type, "image/png");
  assert.equal(requestMessage.attachments[1].storageName, "1-screen.png");
});

test("Rem attaches dropped files and pasted screenshots", async () => {
  const screenshot = {
    name: "pasted-screenshot.png",
    size: 2048,
    type: "image/png",
  };
  const droppedFile = {
    name: "debug.log",
    size: 512,
    type: "text/plain",
  };
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    onOpenMarkdownLink() {},
    onResizeKeyDown() {},
    onResizeStart() {},
    width: 380,
    workflows: [],
  }), fetchMock);
  const pane = allElements(dom.container).find(
    (node) => node.getAttribute?.("data-chat-pane") === "true",
  );
  assert.ok(pane);

  await dom.pointer(pane, "onPaste", {
    clipboardData: {
      files: [],
      items: [
        { kind: "string", getAsFile: () => null },
        { kind: "file", getAsFile: () => screenshot },
      ],
    },
  });
  assert.match(dom.text(), /pasted-screenshot\.png/);

  const dataTransfer = { dropEffect: "none", files: [droppedFile], types: ["Files"] };
  await dom.pointer(pane, "onDragEnter", { dataTransfer });
  assert.match(dom.text(), /Drop files to attach/);
  await dom.pointer(pane, "onDragOver", { dataTransfer });
  assert.equal(dataTransfer.dropEffect, "copy");
  await dom.pointer(pane, "onDrop", { dataTransfer });
  assert.doesNotMatch(dom.text(), /Drop files to attach/);
  assert.match(dom.text(), /pasted-screenshot\.png/);
  assert.match(dom.text(), /debug\.log/);

  assert.deepEqual(chatAttachmentsModule.clipboardAttachmentFiles({ items: [], files: [] }), []);
  assert.equal(chatAttachmentsModule.transferContainsFiles({ types: ["text/plain"] }), false);
  await dom.unmount();
});

test("Rem edit paths preserve the filename and open in the scoped code editor", async () => {
  const opened = [];
  const chatStream = streamResponse([
    '{"type":"thought","text":"Edit","trace":{"id":"edit-1","kind":"tool","title":"Edit","detail":".raticode/testing/workflow.rattish","input":"{\\"path\\":\\".raticode/testing/workflow.rattish\\",\\"kind\\":\\"update\\"}","status":"complete"}}\n',
    '{"type":"final","message":{"body":"Done"}}\n',
  ]);
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
    (url) => (url === "/api/chat/stream" ? chatStream(url) : null),
  ]);
  const workflow = {
    ...workflowFixture({ id: "testing", name: "Testing" }),
    projectName: "alpha",
    projectRoot: "/projects/alpha",
    sourceFormat: "rattish",
    sourcePath: "/projects/alpha/.raticode/testing/workflow.rattish",
  };
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    activeWorkflowId: workflow.id,
    onOpenFile: (pathValue, projectRoot) => opened.push([pathValue, projectRoot]),
    onOpenMarkdownLink() {},
    onResizeKeyDown() {},
    onResizeStart() {},
    width: 380,
    workflow,
    workflows: [workflow],
  }), fetchMock);

  await dom.change(dom.first("textarea"), "Edit the workflow");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  const editDisclosure = dom.ancestor(dom.byText("Editing files"), "BUTTON");
  await dom.click(editDisclosure);
  const pathLink = dom.byLabel(
    "Open .raticode/testing/workflow.rattish in code editor",
  );
  assert.equal(pathLink.style.direction, "rtl");
  await dom.click(pathLink);
  assert.deepEqual(opened, [[".raticode/testing/workflow.rattish", "/projects/alpha"]]);

  await dom.unmount();
});

test("Rem follows new text only while the conversation is at the bottom", async () => {
  const controlledStream = controlledStreamResponse([
    '{"type":"thought","text":"Checking the workflow"}\n',
    '{"type":"final","message":{"body":"The workflow is ready."}}\n',
  ]);
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
    (url) => (url === "/api/chat/stream" ? controlledStream.response(url) : null),
  ]);
  const workflow = workflowFixture({ id: "testing", name: "Testing" });
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    activeWorkflowId: workflow.id,
    onOpenMarkdownLink() {},
    onResizeKeyDown() {},
    onResizeStart() {},
    width: 380,
    workflow,
    workflows: [workflow],
  }), fetchMock);
  const scrollPane = allElements(dom.container).find(
    (element) => element.getAttribute?.("data-chat-scroll") === "true",
  );
  assert.ok(scrollPane);
  scrollPane.clientHeight = 100;
  scrollPane.scrollHeight = 500;
  scrollPane.scrollTop = 400;

  await dom.change(dom.first("textarea"), "Inspect this workflow");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush(2000);
  assert.equal(
    allElements(dom.container).some(
      (element) => element.getAttribute?.("aria-label") === "Rem is typing",
    ),
    false,
  );

  scrollPane.scrollTop = 120;
  await dom.pointer(scrollPane, "onScroll");
  scrollPane.scrollHeight = 600;
  controlledStream.releaseNext();
  await dom.flush();
  assert.equal(scrollPane.scrollTop, 120);

  scrollPane.scrollTop = 500;
  await dom.pointer(scrollPane, "onScroll");
  scrollPane.scrollHeight = 720;
  controlledStream.releaseNext();
  await dom.flush();
  assert.equal(scrollPane.scrollTop, 720);

  await dom.unmount();
});

test("assistant threads open at the bottom and returning home resets the pane to the top", async () => {
  const chatStream = streamResponse([
    '{"type":"final","message":{"body":"Finished"}}\n',
  ]);
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
    (url) => (url === "/api/chat/stream" ? chatStream(url) : null),
  ]);
  const workflow = workflowFixture({ id: "testing", name: "Testing" });
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    activeWorkflowId: workflow.id,
    onOpenMarkdownLink() {},
    onResizeKeyDown() {},
    onResizeStart() {},
    width: 380,
    workflow,
    workflows: [workflow],
  }), fetchMock);

  await dom.change(dom.first("textarea"), "First thread history");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  await dom.click(dom.byTitle("Back to active threads"));
  await dom.click(dom.byTitle("New thread"));
  await dom.change(dom.first("textarea"), "Second thread history");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  await dom.click(dom.byTitle("Back to active threads"));

  const scrollPane = allElements(dom.container).find(
    (element) => element.getAttribute?.("data-chat-scroll") === "true",
  );
  scrollPane.clientHeight = 200;
  scrollPane.scrollHeight = 900;
  scrollPane.scrollTop = 0;
  const firstThread = allElements(dom.container).find(
    (element) => element.tagName === "BUTTON" && textOf(element).includes("First thread history"),
  );
  assert.ok(firstThread);
  await dom.click(firstThread);
  assert.equal(scrollPane.scrollTop, 900);

  await dom.click(dom.byTitle("Back to active threads"));
  assert.equal(scrollPane.scrollTop, 0);
  scrollPane.scrollTop = 150;
  await dom.change(dom.first("textarea"), "Draft from home");
  assert.equal(scrollPane.scrollTop, 150);

  const reopenedThread = allElements(dom.container).find(
    (element) => element.tagName === "BUTTON" && textOf(element).includes("First thread history"),
  );
  await dom.click(reopenedThread);
  assert.equal(scrollPane.scrollTop, 900);
  scrollPane.scrollTop = 250;
  await dom.pointer(scrollPane, "onScroll");
  await dom.click(dom.byTitle("Back to active threads"));
  assert.equal(scrollPane.scrollTop, 0);

  await dom.unmount();
});

test("Rem composer grows with its draft up to its height limit", async () => {
  function ComposerHarness() {
    const [draft, setDraft] = React.useState("");
    return React.createElement(chatComposerModule.default, {
      attachments: [],
      draft,
      onAttachmentsChange() {},
      onDraftChange: setDraft,
      onSend() {},
      onStop() {},
      sending: false,
    });
  }

  const dom = await mountReact(
    React.createElement(ComposerHarness),
    createFetchMock([]),
  );
  const textarea = dom.first("textarea");
  textarea.scrollHeight = 92;
  await dom.change(textarea, "A longer prompt that wraps onto several lines.");
  assert.equal(textarea.style.height, "92px");
  assert.equal(textarea.style.overflowY, "hidden");

  textarea.scrollHeight = 180;
  await dom.change(textarea, `${textarea.value} More text that exceeds the composer limit.`);
  assert.equal(textarea.style.height, "128px");
  assert.equal(textarea.style.overflowY, "auto");

  await dom.unmount();
});

test("Rem transcription streams partial text into the composer", async () => {
  let processor;
  class FakeAudioContext {
    constructor() {
      this.sampleRate = 48_000;
      this.destination = {};
    }
    async resume() {}
    async close() {}
    createMediaStreamSource() {
      return { connect() {}, disconnect() {} };
    }
    createScriptProcessor() {
      processor = { connect() {}, disconnect() {}, onaudioprocess: null };
      return processor;
    }
    createGain() {
      return { connect() {}, disconnect() {}, gain: { value: 1 } };
    }
  }

  function ComposerHarness() {
    const [draft, setDraft] = React.useState("Existing note");
    return React.createElement(chatComposerModule.default, {
      attachments: [],
      audioInputDeviceId: "studio-mic",
      draft,
      onAttachmentsChange() {},
      onDraftChange: setDraft,
      onSend() {},
      onStop() {},
      sending: false,
    });
  }

  const fetchMock = createFetchMock([
    (url, options) => {
      const request = JSON.parse(options.body);
      if (url === "/api/chat/transcribe/start") {
        return { ok: true, status: 201, json: async () => ({ sessionId: "session-1" }) };
      }
      if (url === "/api/chat/transcribe/chunk") {
        assert.equal(request.sessionId, "session-1");
        assert.ok(Buffer.from(request.data, "base64").length > 0);
        return { ok: true, status: 200, json: async () => ({ text: "add a review" }) };
      }
      if (url === "/api/chat/transcribe/finish") {
        assert.equal(request.sessionId, "session-1");
        return { ok: true, status: 200, json: async () => ({ text: "add a review node" }) };
      }
      if (url === "/api/chat/transcribe/cancel") {
        return { ok: true, status: 200, json: async () => ({ cancelled: true }) };
      }
      return null;
    },
  ]);
  const dom = await mountReact(React.createElement(ComposerHarness), fetchMock);
  const stopTrack = () => {};
  let requestedConstraints;
  navigator.mediaDevices = {
    getUserMedia: async (constraints) => {
      requestedConstraints = constraints;
      return { getTracks: () => [{ stop: stopTrack }] };
    },
  };
  window.AudioContext = FakeAudioContext;
  await dom.click(dom.byLabel("Transcribe message locally"));
  assert.equal(requestedConstraints.audio.deviceId.exact, "studio-mic");
  assert.ok(dom.byLabel("Stop transcription"));
  processor.onaudioprocess({
    inputBuffer: { getChannelData: () => Float32Array.from({ length: 16384 }, (_, i) => Math.sin(i / 10)) },
  });
  await dom.flush();
  assert.equal(dom.first("textarea").value, "Existing note add a review");

  await dom.click(dom.byLabel("Stop transcription"));
  await dom.flush();
  assert.equal(dom.first("textarea").value, "Existing note add a review node");
  assert.ok(dom.byLabel("Transcribe message locally"));
  delete window.AudioContext;
  await dom.unmount();
});

test("app crash fallback exposes recovery actions and complete diagnostics", () => {
  const error = new ReferenceError("formatWorkflowRunLog is not defined");
  error.stack = "ReferenceError: formatWorkflowRunLog is not defined\n    at App (src/pages/App.jsx:2038:54)";
  const crash = crashBoundaryModule.createCrashDetails(error, "at App (src/pages/App.jsx:2038:54)", {
    timestamp: "2026-08-29T09:14:02.000Z",
    url: "http://127.0.0.1:5173/#/",
    userAgent: "Raticode test runner",
  });

  const markup = renderToStaticMarkup(React.createElement(crashBoundaryModule.AppCrashPage, { crash }));
  assert.match(markup, /Something snapped\./);
  assert.match(markup, /Reload Raticode/);
  assert.match(markup, /Copy error details/);
  assert.match(markup, /Open an issue/);
  assert.match(markup, /Technical details/);
  assert.match(markup, /ReferenceError: formatWorkflowRunLog is not defined/);

  const report = crashBoundaryModule.formatCrashReport(crash);
  assert.match(report, /React component stack:/);
  assert.match(report, /URL: http:\/\/127\.0\.0\.1:5173\/#\//);
  assert.ok(report.includes(`Raticode v${frontendPackage.version}`));

  const issueUrl = new URL(crashBoundaryModule.issueUrlForCrash(crash));
  assert.equal(issueUrl.origin + issueUrl.pathname, "https://github.com/zacharyivie/gofer-flow/issues/new");
  assert.match(issueUrl.searchParams.get("title"), /^Crash: ReferenceError:/);
  assert.ok(issueUrl.searchParams.get("body").includes(`Raticode v${frontendPackage.version}`));
});

test("apiUrl normalizes relative paths, HTTP origins, trailing slashes, and prefixed bases", () => {
  globalThis.window.goferApiBaseUrl = undefined;
  assert.equal(apiUrl("workflows"), "/api/workflows");
  assert.equal(apiUrl("/workflows"), "/api/workflows");

  globalThis.window.goferApiBaseUrl = "http://127.0.0.1:8765";
  assert.equal(apiUrl("/workflows"), "http://127.0.0.1:8765/api/workflows");

  globalThis.window.goferApiBaseUrl = "https://localhost:9443/";
  assert.equal(apiUrl("chat/providers"), "https://localhost:9443/api/chat/providers");

  globalThis.window.goferApiBaseUrl = "http://127.0.0.1:8765/api/";
  assert.equal(apiUrl("/workflows/demo/run"), "http://127.0.0.1:8765/api/workflows/demo/run");

  globalThis.window.goferApiBaseUrl = "/custom-api/";
  assert.equal(apiUrl("/workflows"), "/custom-api/workflows");
});

test("shared dialogs trap focus, close with Escape, and restore focus to the opener", async () => {
  function DialogHarness() {
    const [open, setOpen] = React.useState(false);
    return React.createElement(
      React.Fragment,
      null,
      React.createElement(
        "button",
        { "aria-label": "Open test dialog", type: "button", onClick: () => setOpen(true) },
        "Open",
      ),
      open
        ? React.createElement(
            dialogModule.Dialog,
            {
              description: "Dialog focus behavior",
              onClose: () => setOpen(false),
              title: "Test dialog",
            },
            React.createElement(
              "button",
              { "aria-label": "First dialog action", type: "button" },
              "First",
            ),
            React.createElement(
              "button",
              { "aria-label": "Last dialog action", type: "button" },
              "Last",
            ),
          )
        : null,
    );
  }

  const dom = await mountReact(
    React.createElement(DialogHarness),
    createFetchMock([]),
  );
  const opener = dom.byLabel("Open test dialog");
  await dom.focus(opener);
  await dom.click(opener);

  const dialog = allElements(dom.container).find(
    (element) => element.getAttribute?.("role") === "dialog",
  );
  assert.ok(dialog);
  assert.equal(dialog.getAttribute("aria-modal"), "true");
  assert.ok(dialog.getAttribute("aria-labelledby"));
  assert.ok(dialog.getAttribute("aria-describedby"));

  const first = dom.byLabel("First dialog action");
  const last = dom.byLabel("Last dialog action");
  assert.equal(document.activeElement, first);

  await dom.focus(last);
  await dom.dispatchWindow("keydown", { key: "Tab" });
  assert.equal(document.activeElement, first);

  await dom.focus(first);
  await dom.dispatchWindow("keydown", { key: "Tab", shiftKey: true });
  assert.equal(document.activeElement, last);

  await dom.dispatchWindow("keydown", { key: "Escape" });
  assert.equal(document.activeElement, opener);
  assert.equal(
    allElements(dom.container).some((element) => element.getAttribute?.("role") === "dialog"),
    false,
  );

  await dom.unmount();
});

test("every migrated dialog family uses the shared keyboard and focus contract", async (context) => {
  const approval = {
    workflowId: "demo",
    runId: "run-1",
    nodeId: "approve",
    message: "Approve deployment?",
    status: "pending",
    approvers: ["ops"],
  };
  const cases = [
    {
      name: "workflow history",
      render: (onClose) => React.createElement(appModule.WorkflowHistoryDialog, {
        diff: null,
        error: "",
        loading: false,
        revisions: [],
        workflow: workflowFixture(),
        onClose,
        onPreview() {},
        onRefresh() {},
        onRestore() {},
      }),
    },
    {
      name: "run preview",
      render: (onClose) => React.createElement(appModule.RunPreviewDialog, {
        plan: { generations: [] },
        workflow: workflowFixture(),
        onCancel: onClose,
        onRun() {},
      }),
    },
    {
      name: "create workflow",
      render: (onClose) => React.createElement(appModule.CreateWorkflowDialog, {
        error: "",
        open: true,
        saving: false,
        onClose,
        onCreate() {},
        onImport() {},
      }),
    },
    {
      name: "export workflow",
      render: (onClose) => React.createElement(appModule.ExportWorkflowDialog, {
        directory: "/tmp",
        error: "",
        open: true,
        saving: false,
        workflow: workflowFixture(),
        onClose,
        onChooseFolder() {},
        onExport() {},
      }),
    },
    {
      name: "node rename",
      render: (onClose) => React.createElement(canvasModule.NodeRenameDialog, {
        initialLabel: "Step",
        onCancel: onClose,
        onRename() {},
      }),
    },
    {
      name: "filesystem trust",
      render: (onClose) => React.createElement(canvasModule.FilesystemTrustPrompt, {
        parentPath: "/workspace",
        onCancel: onClose,
        onConfirm() {},
      }),
    },
    {
      name: "file editor",
      desktop: { textFiles: { read: async () => ({ content: "hello" }) } },
      render: (onClose) => React.createElement(canvasModule.TextFileDialog, {
        mode: "edit",
        path: "/workspace/demo.txt",
        onClose,
      }),
    },
    {
      name: "unsaved file changes",
      render: (onClose) => React.createElement(codeWorkspaceModule.UnsavedChangesDialog, {
        dirtyPaths: ["/workspace/demo.txt"],
        onCancel: onClose,
        onDiscard() {},
        onSave() {},
      }),
    },
    {
      name: "path picker",
      desktop: {
        workspace: {
          listDirectory: async () => ({
            directory: "/workspace",
            entries: [],
            parent: null,
          }),
        },
      },
      render: (onClose) => React.createElement(canvasModule.PathPickerDialog, {
        currentPath: "/workspace",
        label: "Working directory",
        onClose,
        onSelect() {},
      }),
    },
    {
      name: "path create and rename",
      render: (onClose) => React.createElement(canvasModule.PathNameDialog, {
        directory: "/workspace",
        initialName: "old.txt",
        kind: "file",
        mode: "rename",
        onClose,
        onSubmit: async () => {},
      }),
    },
    {
      name: "approval",
      render: () => React.createElement(canvasModule.ApprovalDecisionOverlay, {
        approval,
        node: { id: "approve", label: "Deploy" },
        onDecideApproval() {},
      }),
    },
  ];

  for (const dialogCase of cases) {
    await context.test(dialogCase.name, async () => {
      await exerciseDialogFamily(dialogCase.render, dialogCase.desktop);
    });
  }
});

test("workflow refresh helpers preserve local edits during silent refresh", () => {
  const remote = [
    {
      id: "demo",
      name: "Remote",
      nodes: [{ id: "step", type: "agent", label: "Remote label", x: 10, y: 20 }],
      edges: [],
      agents: {},
      sourcePath: "/tmp/demo.toml",
      status: "Ready",
    },
  ];
  const local = {
    ...remote[0],
    name: "Unsaved local",
    nodes: [{ id: "step", type: "agent", label: "Local label", x: 99, y: 120 }],
  };

  const preserved = appModule.preserveLocalWorkflow(remote, local, "/data")[0];

  assert.equal(preserved.name, "Unsaved local");
  assert.equal(preserved.sourcePath, "/tmp/demo.toml");
  assert.equal(preserved.nodes[0].label, "Local label");
  assert.equal(preserved.nodes[0].x, 99);
});

test("reduced-motion preference disables smooth scrolling and status animation", () => {
  globalThis.window.matchMedia = (query) => ({
    matches: query === "(prefers-reduced-motion: reduce)",
  });
  assert.equal(appModule.prefersReducedMotion(), true);

  const css = fs.readFileSync(path.join(frontendRoot, "src/styles/index.css"), "utf8");
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.match(css, /\.animate-bounce[\s\S]*\.animate-spin[\s\S]*animation:\s*none/);
});

test("workflow save payload keeps all filesystem permission combinations and graph positions", () => {
  const permissionCombinations = Array.from({ length: 8 }, (_unused, value) => ({
    path: `/outside/shared-${value}`,
    read: Boolean(value & 4),
    write: Boolean(value & 2),
    execute: Boolean(value & 1),
  }));
  const payload = appModule.workflowPayloadForSave({
    id: "demo",
    name: "Demo",
    filesystemAccess: [
      ...permissionCombinations,
      { path: "/outside/shared-0/", read: true, write: true, execute: true },
      { path: "/outside/defaults" },
      { path: "" },
    ],
    nodes: [
      {
        id: "step",
        type: "bash_command",
        label: "Run",
        operation: { type: "bash_command", command: "echo hi" },
      },
    ],
  });

  assert.deepEqual(payload.edges, []);
  assert.deepEqual(payload.agents, {});
  assert.deepEqual(payload.filesystemAccess, [
    ...permissionCombinations,
    { path: "/outside/defaults", read: true, write: true, execute: false },
  ]);
  assert.equal(payload.nodes[0].x, 0);
  assert.equal(payload.nodes[0].y, 0);
  assert.equal(payload.nodes[0].operation.command, "echo hi");
});

test("filesystem permission controls update each flag independently", async () => {
  const changes = [];
  const workflow = {
    ...workflowFixture(),
    filesystemAccess: [
      { path: "/outside/tools", read: false, write: true, execute: false },
    ],
  };
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      dataDir: "/workspace",
      workflow,
      onWorkflowChange: (nextWorkflow) => changes.push(nextWorkflow),
    }),
    createFetchMock([]),
  );

  await openWorkflowSettingsFromMenu(dom);
  const workflowSettingsHeading = headingByText(dom, "Workflow settings");
  assert.ok(workflowSettingsHeading);
  const workflowSettingsHeader = dom.ancestor(workflowSettingsHeading, "HEADER");
  assert.equal(
    allElements(workflowSettingsHeader).some(
      (element) => element.tagName === "P" && textOf(element) === workflow.id,
    ),
    true,
  );
  assert.equal(
    allElements(dom.container).some(
      (element) => element.tagName === "LABEL" && textOf(element) === "ID",
    ),
    false,
  );
  await dom.click(dom.byText("Access"));

  await dom.change(dom.controlAfterLabel("Read files"), true);
  await dom.change(dom.controlAfterLabel("Write files"), false);
  await dom.change(dom.controlAfterLabel("Execute files"), true);

  assert.deepEqual(changes.at(-1).filesystemAccess, [
    { path: "/outside/tools", read: true, write: false, execute: true },
  ]);
  assert.match(
    dom.text(),
    /Write access allows this workflow to create, change, move, and delete files/,
  );
  assert.match(dom.text(), /Execute access allows this workflow to run programs/);

  await dom.unmount();
});

test("autosave persists edits to two workflows with independent debounces", async () => {
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([
      workflowFixture({ id: "a", name: "Workflow A", label: "A original" }),
      workflowFixture({ id: "b", name: "Workflow B", label: "B original" }),
    ])),
    saveWorkflowResponse(),
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock);

  await dom.flush();
  await dom.change(dom.controlAfterLabel("Name"), "A final");
  await dom.click(dom.ancestor(dom.byText("Workflow B"), (node) => node.getAttribute?.("role") === "button"));
  await dom.change(dom.controlAfterLabel("Name"), "B final");
  await dom.flush(650);

  const saves = fetchMock.calls.filter((call) => call.options.method === "PUT");
  assert.equal(saves.length, 2);
  assert.deepEqual(
    saves.map((call) => [call.url, JSON.parse(call.options.body).name]).sort(),
    [
      ["/api/workflows/a", "A final"],
      ["/api/workflows/b", "B final"],
    ],
  );
  assert.match(dom.text(), /Saved/);

  await dom.unmount();
});

test("autosave serializes revisions and ignores a stale response", async () => {
  const firstSave = createDeferred();
  let saveCount = 0;
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([
      workflowFixture({ id: "demo", name: "Demo", label: "Original" }),
    ])),
    (url, options = {}) => {
      if (url !== "/api/workflows/demo" || options.method !== "PUT") return null;
      saveCount += 1;
      const workflow = JSON.parse(options.body);
      return {
        ok: true,
        status: 200,
        json: async () => (saveCount === 1 ? firstSave.promise : { workflow }),
      };
    },
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock);

  await dom.flush();
  await dom.change(dom.controlAfterLabel("Name"), "First revision");
  await dom.flush(650);
  await dom.change(dom.controlAfterLabel("Name"), "Newest revision");
  await dom.flush(650);
  assert.equal(fetchMock.calls.filter((call) => call.options.method === "PUT").length, 1);

  firstSave.resolve({
    workflow: workflowFixture({ id: "demo", name: "First revision", label: "Original" }),
  });
  await dom.flush();
  await dom.flush();

  assert.equal(dom.controlAfterLabel("Name").value, "Newest revision");
  const saves = fetchMock.calls.filter((call) => call.options.method === "PUT");
  assert.equal(saves.length, 2);
  assert.equal(JSON.parse(saves[1].options.body).name, "Newest revision");
  assert.match(dom.text(), /Saved/);

  await dom.unmount();
});

test("failed autosave remains visible and retryable", async () => {
  let saveCount = 0;
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([
      workflowFixture({ id: "demo", name: "Demo", label: "Original" }),
    ])),
    (url, options = {}) => {
      if (url !== "/api/workflows/demo" || options.method !== "PUT") return null;
      saveCount += 1;
      const workflow = JSON.parse(options.body);
      return saveCount === 1
        ? { ok: false, status: 500, json: async () => ({ error: "Disk is unavailable" }) }
        : { ok: true, status: 200, json: async () => ({ workflow }) };
    },
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock);

  await dom.flush();
  await dom.change(dom.controlAfterLabel("Name"), "Recoverable edit");
  assert.match(dom.text(), /Saving…/);
  await dom.flush(650);

  const retryButton = dom.byText("Retry");
  const alert = dom.ancestor(retryButton, (node) => node.getAttribute?.("role") === "alert");
  assert.match(alert.textContent, /Couldn't saveDisk is unavailable—Retry/);
  assert.equal(alert.getAttribute("title"), "Disk is unavailable");
  assert.equal(alert.getAttribute("aria-live"), "assertive");
  assert.equal(alert.getAttribute("aria-atomic"), "true");
  assert.equal(
    matchingLiveRegions(dom.container, {
      politeness: "assertive",
      role: "alert",
      text: "Disk is unavailable",
    }).length,
    1,
  );

  await dom.click(retryButton);
  await dom.flush();
  assert.equal(saveCount, 2);
  assert.match(dom.text(), /Saved/);
  assert.equal(
    matchingLiveRegions(dom.container, {
      politeness: "assertive",
      role: "alert",
      text: "Disk is unavailable",
    }).length,
    0,
  );

  await dom.unmount();
});

test("silent refresh preserves every dirty workflow and updates clean workflows", async () => {
  const pendingSave = createDeferred();
  let serveRemoteWorkflows = false;
  const initialWorkflows = [
    workflowFixture({ id: "a", name: "Workflow A", label: "A original" }),
    workflowFixture({ id: "b", name: "Workflow B", label: "B original" }),
    workflowFixture({ id: "c", name: "Workflow C", label: "C original" }),
  ];
  const refreshedWorkflows = [
    workflowFixture({ id: "a", name: "A remote", label: "A remote" }),
    workflowFixture({ id: "b", name: "B remote", label: "B remote" }),
    workflowFixture({ id: "c", name: "C refreshed", label: "C refreshed" }),
  ];
  const fetchMock = createFetchMock([
    (url, options = {}) => {
      if (url !== "/api/workflows" || (options.method ?? "GET") !== "GET") return null;
      return {
        ok: true,
        status: 200,
        json: async () => workflowsPayload(
          serveRemoteWorkflows ? refreshedWorkflows : initialWorkflows,
        ),
      };
    },
    (url, options = {}) => {
      if (!url.startsWith("/api/workflows/") || options.method !== "PUT") return null;
      return { ok: true, status: 200, json: async () => pendingSave.promise };
    },
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock);

  await dom.flush();
  await dom.change(dom.controlAfterLabel("Name"), "A local");
  await dom.click(dom.ancestor(dom.byText("Workflow B"), (node) => node.getAttribute?.("role") === "button"));
  await dom.change(dom.controlAfterLabel("Name"), "B local");
  await dom.click(dom.ancestor(dom.byText("Workflow C"), (node) => node.getAttribute?.("role") === "button"));
  serveRemoteWorkflows = true;
  await dom.flush(2000);

  assert.equal(dom.controlAfterLabel("Name").value, "C refreshed");
  await dom.click(dom.ancestor(dom.byText("A local"), (node) => node.getAttribute?.("role") === "button"));
  assert.equal(dom.controlAfterLabel("Name").value, "A local");
  await dom.click(dom.ancestor(dom.byText("B local"), (node) => node.getAttribute?.("role") === "button"));
  assert.equal(dom.controlAfterLabel("Name").value, "B local");

  await dom.unmount();
});

test("page hide preserves pending edits with a keepalive save", async () => {
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([
      workflowFixture({ id: "demo", name: "Demo", label: "Original" }),
    ])),
    saveWorkflowResponse(),
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock);

  await dom.flush();
  await dom.change(dom.controlAfterLabel("Name"), "Pending at unload");
  await dom.dispatchWindow("pagehide");

  const unloadSave = fetchMock.calls.find(
    (call) => call.url === "/api/workflows/demo" && call.options.keepalive,
  );
  assert.ok(unloadSave);
  assert.equal(JSON.parse(unloadSave.options.body).name, "Pending at unload");

  await dom.unmount();
});

test("workflow deletion helpers remove the selected workflow and choose the next active ID", () => {
  const workflows = [{ id: "a" }, { id: "b" }, { id: "c" }];

  assert.deepEqual(appModule.workflowIdsAfterDelete(workflows, "b"), ["a", "c"]);
  assert.equal(appModule.nextActiveWorkflowIdAfterDelete(workflows, "b", "b"), "a");
  assert.equal(appModule.nextActiveWorkflowIdAfterDelete(workflows, "c", "b"), "c");
});

test("terminal panel and Code workspace survive deleting the last workflow", async () => {
  const workflow = workflowFixture({ id: "only", name: "Only workflow" });
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([workflow])),
    jsonResponse(
      "/api/workflows/only?sourceFormat=toml",
      { deleted: true },
      { method: "DELETE" },
    ),
  ]);
  const dom = await mountReact(
    React.createElement(appModule.default),
    fetchMock,
    {
      storage: {
        "gofer.recentProjects": JSON.stringify(["/workspace"]),
        [appModule.STUDIO_SESSION_STORAGE_KEY]: JSON.stringify({
          projectRoot: "/workspace",
          view: "graph",
          workflowId: "only",
        }),
      },
    },
  );

  await dom.flush();
  const bottomPanel = dom.byLabel("Bottom panel");
  await dom.click(dom.byLabel("File explorer"));
  const codeWorkspace = dom.byLabel("Code workspace");
  await dom.click(dom.byLabel("Workflows"));
  await dom.click(dom.byTitle("Workflow actions"));
  await dom.click(dom.ancestor(dom.byText("Delete workflow"), "BUTTON"));
  await dom.flush();

  assert.equal(dom.byLabel("Bottom panel"), bottomPanel);
  assert.equal(dom.byLabel("Code workspace"), codeWorkspace);
  await dom.click(dom.byLabel("File explorer"));
  assert.equal(dom.byLabel("Code workspace"), codeWorkspace);
  await dom.click(dom.byLabel("Recent projects"));
  assert.ok(dom.byLabel("Remove workspace from recent projects"));
  assert.equal(dom.byLabel("Bottom panel"), bottomPanel);

  await dom.click(dom.byLabel("Workflows"));
  assert.equal(dom.byLabel("Bottom panel"), bottomPanel);

  await dom.unmount();
});

test("deleting a Rattish workflow removes its recent-file entry", async () => {
  const sourcePath = "/workspace/.raticode/only/workflow.rattish";
  const workflow = {
    ...workflowFixture({ id: "only", name: "Only workflow" }),
    sourceFormat: "rattish",
    sourcePath,
  };
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([workflow])),
    jsonResponse("/api/workflows/only/document", {
      document: {
        diagnostics: [],
        preflight: { diagnostics: [] },
        source: "Rattish: 1\n",
      },
    }),
    jsonResponse(
      "/api/workflows/only?sourceFormat=rattish",
      { deleted: true },
      { method: "DELETE" },
    ),
  ]);
  const dom = await mountReact(
    React.createElement(appModule.default),
    fetchMock,
    {
      storage: {
        [appModule.RECENT_FILES_STORAGE_KEY]: JSON.stringify([sourcePath]),
      },
    },
  );

  await dom.flush();
  await dom.click(dom.byTitle("Workflow actions"));
  await dom.click(dom.byText("Delete workflow"));
  await dom.flush();
  await dom.click(dom.byLabel("File explorer"));

  assert.equal(
    allElements(dom.container).some(
      (element) => element.getAttribute?.("aria-label") === "Editor tabs",
    ),
    false,
  );
  assert.doesNotMatch(dom.text(), /Recent files/);
  assert.deepEqual(
    JSON.parse(window.localStorage.getItem(appModule.RECENT_FILES_STORAGE_KEY)),
    [],
  );

  await dom.unmount();
});

test("workflow deletion closes source tabs and clears source preview state", () => {
  const source = fs.readFileSync(path.join(repoRoot, "frontend/src/pages/App.jsx"), "utf8");
  const deleteFunction = source.slice(
    source.indexOf("async function deleteWorkflow"),
    source.indexOf("async function renameWorkflow"),
  );

  assert.match(deleteFunction, /closeCodeFiles\(\[workflow\.sourcePath, \.\.\.Object\.entries\(workflowTabs\)/);
  assert.match(deleteFunction, /tab\.workflowId === workflow\.id/);
  assert.match(deleteFunction, /setRecentCodePaths\(\(current\) => removeCodePath/);
  assert.match(deleteFunction, /setRattishEditorState\(null\)/);
});

test("workflow sidebar groups workflows by registered project folder", () => {
  assert.deepEqual(
    appModule.groupWorkflowsByProject([
      { id: "review", projectRoot: "/workspace/gofer-flow", projectName: "stale-workflow-slug" },
      { id: "release", projectRoot: "/workspace/gofer-flow", projectName: "gofer-flow" },
      { id: "deploy", projectRoot: "/workspace/deployment", projectName: "Deployment" },
    ]).map((group) => ({
      name: group.name,
      ids: group.items.map((workflow) => workflow.id),
    })),
    [
      { name: "deployment", ids: ["deploy"] },
      { name: "gofer-flow", ids: ["review", "release"] },
    ],
  );

  assert.deepEqual(
    appModule.groupWorkflowsByProject(
      [{ id: "review", projectRoot: "/workspace/gofer-flow" }],
      { "/workspace/gofer-flow": "Raticode" },
    ).map((group) => ({ name: group.name, root: group.root })),
    [{ name: "Raticode", root: "/workspace/gofer-flow" }],
  );
});

test("silent refresh replaces stale local project identity with the registry identity", () => {
  const local = workflowFixture({ id: "review" });
  local.projectRoot = "/app-data/showcase-completed-workflow";
  local.projectName = "showcase-completed-workflow";
  const remote = {
    ...local,
    projectRoot: "/repos/customer-api",
    projectName: "wrong-cached-name",
    workflowRoot: "/repos/customer-api/.raticode/review",
  };

  const [preserved] = appModule.preserveLocalWorkflow([remote], local, "/app-data");

  assert.equal(preserved.projectRoot, "/repos/customer-api");
  assert.equal(preserved.projectName, "customer-api");
  assert.equal(preserved.workflowRoot, "/repos/customer-api/.raticode/review");
});

test("project folder menus stay inside the desktop viewport", () => {
  assert.deepEqual(appModule.projectMenuPosition(790, 590, 800, 600), { x: 584, y: 438 });
  assert.deepEqual(appModule.projectMenuPosition(-20, -30, 800, 600), { x: 8, y: 8 });
});

test("project context menu renames only the local display label", async () => {
  window.localStorage.removeItem("gofer.projectLabels");
  const dom = await mountReact(
    React.createElement(appModule.WorkflowSidebar, {
      activeWorkflowId: "review",
      loading: false,
      query: "",
      runState: {},
      workflows: [{
        ...workflowFixture({ id: "review", name: "Review PR" }),
        projectRoot: "/workspace/gofer-flow",
      }],
      width: 272,
      onCreate() {},
      onDeleteWorkflow() {},
      onDuplicateWorkflow() {},
      onQueryChange() {},
      onRefresh() {},
      onRenameWorkflow() {},
      onResizeKeyDown() {},
      onResizeStart() {},
      onRunWorkflow() {},
      onSelect() {},
    }),
    createFetchMock([]),
  );
  const section = allElements(dom.container).find((element) => element.tagName === "SECTION");
  const contextEvent = testEvent(section);
  contextEvent.clientX = 100;
  contextEvent.clientY = 100;
  await React.act(async () => reactProps(section).onContextMenu(contextEvent));
  await dom.click(dom.byText("Rename"));
  const input = dom.byLabel("Project label for gofer-flow");
  await dom.change(input, "Raticode");
  await dom.blur(input);

  assert.ok(dom.byText("Raticode"));
  assert.deepEqual(JSON.parse(window.localStorage.getItem("gofer.projectLabels")), {
    "/workspace/gofer-flow": "Raticode",
  });
  await dom.unmount();
  window.localStorage.removeItem("gofer.projectLabels");
});

test("workflow context menu opens its Rattish source in the code editor", async () => {
  const workflow = {
    ...workflowFixture({ id: "review", name: "Review PR" }),
    projectRoot: "/workspace/gofer-flow",
    sourceFormat: "rattish",
    sourcePath: "/workspace/gofer-flow/.raticode/review/workflow.rattish",
  };
  const edited = [];
  const dom = await mountReact(
    React.createElement(appModule.WorkflowSidebar, {
      activeWorkflow: workflow,
      activeWorkflowId: workflow.id,
      loading: false,
      query: "",
      runState: {},
      workflows: [workflow],
      width: 272,
      onCreate() {},
      onDeleteWorkflow() {},
      onDuplicateWorkflow() {},
      onEditWorkflowFile(selected) { edited.push(selected); },
      onQueryChange() {},
      onRefresh() {},
      onRenameWorkflow() {},
      onResizeKeyDown() {},
      onResizeStart() {},
      onRunWorkflow() {},
      onSelect() {},
      onViewChange() {},
    }),
    createFetchMock([]),
  );

  const workflowCard = dom.ancestor(
    dom.byText("Review PR"),
    (node) => node.getAttribute?.("class")?.includes("group relative w-full"),
  );
  const contextEvent = testEvent(workflowCard);
  await React.act(async () => reactProps(workflowCard).onContextMenu(contextEvent));
  await dom.click(dom.byText("Edit workflow file"));

  assert.deepEqual(edited, [workflow]);
  await dom.unmount();
});

test("workflow sidebar activities preserve expanded files while opening workflow navigation", async () => {
  const workflow = {
    ...workflowFixture({ id: "review-pr", name: "Review PR" }),
    projectName: "gofer-flow",
    projectRoot: "/workspace/gofer-flow",
    sourceFormat: "rattish",
    sourcePath: "/workspace/gofer-flow/.raticode/review-pr/workflow.rattish",
  };
  const dom = await mountReact(
    React.createElement(appModule.WorkflowSidebar, {
      activeWorkflow: workflow,
      activeWorkflowId: workflow.id,
      loading: false,
      query: "",
      runState: {},
      view: "code",
      workflows: [workflow],
      width: 272,
      onCreate() {},
      onDeleteWorkflow() {},
      onDuplicateWorkflow() {},
      onQueryChange() {},
      onRefresh() {},
      onRenameWorkflow() {},
      onResizeKeyDown() {},
      onResizeStart() {},
      onRunWorkflow() {},
      onSelect() {},
      onViewChange() {},
    }),
    createFetchMock([]),
    {
      desktop: {
        workspace: {
          listDirectory: async ({ currentPath }) => ({
            directory: currentPath,
            parent: path.dirname(currentPath),
            entries: currentPath === "/workspace/gofer-flow"
              ? [
                  { name: ".raticode", path: "/workspace/gofer-flow/.raticode", isDirectory: true, isFile: false },
                  { name: "README.md", path: "/workspace/gofer-flow/README.md", isDirectory: false, isFile: true },
                ]
              : currentPath === "/workspace/gofer-flow/.raticode"
                ? [{ name: "review-pr", path: "/workspace/gofer-flow/.raticode/review-pr", isDirectory: true, isFile: false }]
                : currentPath === "/workspace/gofer-flow/.raticode/review-pr"
                  ? [
                      { name: "workflow.rattish", path: "/workspace/gofer-flow/.raticode/review-pr/workflow.rattish", isDirectory: false, isFile: true },
                      { name: "workflow.metadata.json", path: "/workspace/gofer-flow/.raticode/review-pr/workflow.metadata.json", isDirectory: false, isFile: true },
                    ]
                  : [],
          }),
        },
      },
    },
  );

  await dom.flush();
  assert.throws(() => dom.byLabel("Search files"), /Unable to find/);
  assert.ok(dom.byText("Project workspace"));
  assert.ok(dom.byText("README.md"));
  await dom.click(dom.ancestor(dom.byText(".raticode"), "BUTTON"));
  await dom.flush();
  await dom.click(dom.ancestor(dom.byText("review-pr"), "BUTTON"));
  await dom.flush();
  assert.ok(dom.byText("workflow.rattish"));
  assert.ok(dom.byText("workflow.metadata.json"));
  assert.equal(dom.byLabel("File explorer").getAttribute("aria-selected"), "true");
  assert.equal(dom.byLabel("Workflows").getAttribute("aria-selected"), "false");
  await dom.click(dom.byLabel("Workflows"));
  assert.equal(dom.byLabel("Workflows").getAttribute("aria-selected"), "true");
  assert.ok(dom.byText("Review PR"));
  await dom.click(dom.byLabel("File explorer"));
  assert.ok(dom.byText("workflow.rattish"));
  assert.equal(dom.byLabel("File explorer").getAttribute("aria-selected"), "true");
  await dom.unmount();
});

test("Code file explorer creates, copies, pastes, reveals, renames, and trashes paths", async () => {
  const workflow = {
    ...workflowFixture({ id: "review-pr", name: "Review PR" }),
    projectName: "gofer-flow",
    projectRoot: "/workspace/gofer-flow",
    sourceFormat: "rattish",
    sourcePath: "/workspace/gofer-flow/.raticode/review-pr/workflow.rattish",
  };
  const entries = {
    "/workspace/gofer-flow": [
      { name: "docs", path: "/workspace/gofer-flow/docs", isDirectory: true, isFile: false },
      { name: "README.md", path: "/workspace/gofer-flow/README.md", isDirectory: false, isFile: true },
    ],
    "/workspace/gofer-flow/docs": [],
  };
  const calls = [];
  const accessOrder = [];
  const closedFiles = [];
  const openedFiles = [];
  const workspace = {
    async trustProjectRoot(projectRoot) {
      accessOrder.push(["trust", projectRoot]);
    },
    async listDirectory({ currentPath }) {
      accessOrder.push(["list", currentPath]);
      return { directory: currentPath, parent: path.dirname(currentPath), entries: entries[currentPath] ?? [] };
    },
    async createFile({ directory, name }) {
      calls.push(["create", directory, name]);
      const next = { name, path: `${directory}/${name}`, isDirectory: false, isFile: true };
      entries[directory].push(next);
      return next;
    },
    async copyPath({ sourcePath, destinationPath }) {
      calls.push(["copy", sourcePath, destinationPath]);
      const directory = path.dirname(destinationPath);
      entries[directory].push({
        name: path.basename(destinationPath),
        path: destinationPath,
        isDirectory: false,
        isFile: true,
      });
      return { path: destinationPath };
    },
    async deletePath(targetPath) {
      calls.push(["delete", targetPath]);
      for (const directoryEntries of Object.values(entries)) {
        const index = directoryEntries.findIndex((entry) => entry.path === targetPath);
        if (index >= 0) directoryEntries.splice(index, 1);
      }
      return { deleted: true };
    },
    async renamePath({ sourcePath, name }) {
      calls.push(["rename", sourcePath, name]);
      const directory = path.dirname(sourcePath);
      const entry = entries[directory].find((candidate) => candidate.path === sourcePath);
      entry.name = name;
      entry.path = `${directory}/${name}`;
      return { path: entry.path };
    },
    async revealPath(targetPath) {
      calls.push(["reveal", targetPath]);
      return { opened: true };
    },
    async openPath(targetPath) {
      calls.push(["open", targetPath]);
      return { opened: true };
    },
  };
  const dom = await mountReact(
    React.createElement(appModule.WorkflowSidebar, {
      activeCodePath: "/workspace/gofer-flow/README.md",
      activeWorkflow: workflow,
      activeWorkflowId: workflow.id,
      loading: false,
      query: "",
      runState: {},
      view: "code",
      workflows: [workflow],
      width: 272,
      onCreate() {},
      onCodeFileOpen(targetPath, options) { openedFiles.push([targetPath, options]); },
      onCloseCodeFile(targetPath) { closedFiles.push(targetPath); },
      onCodeFilesystemChange() {},
      onDeleteWorkflow() {},
      onDuplicateWorkflow() {},
      onQueryChange() {},
      onRefresh() {},
      onRenameWorkflow() {},
      onResizeKeyDown() {},
      onResizeStart() {},
      onRunWorkflow() {},
      onSelect() {},
      onViewChange() {},
    }),
    createFetchMock([]),
    { desktop: { workspace } },
  );
  const contextMenu = async (element) => {
    const target = dom.ancestor(element, "BUTTON");
    const event = testEvent(element);
    event.clientX = 100;
    event.clientY = 100;
    await React.act(async () => reactProps(target).onContextMenu(event));
  };
  const menuAction = (label) => {
    const menu = dom.byLabel("File actions");
    const labelElement = allElements(menu).find(
      (element) => element.tagName === "SPAN" && directText(element) === label,
    );
    return dom.ancestor(labelElement, "BUTTON");
  };

  await dom.flush();
  assert.deepEqual(accessOrder.slice(0, 2), [
    ["trust", "/workspace/gofer-flow"],
    ["list", "/workspace/gofer-flow"],
  ]);
  const explorer = dom.byLabel("Project sidebar");
  const closeEvent = testEvent(explorer);
  closeEvent.ctrlKey = true;
  closeEvent.key = "w";
  await React.act(async () => reactProps(explorer).onKeyDownCapture(closeEvent));
  assert.deepEqual(closedFiles, ["/workspace/gofer-flow/README.md"]);
  const readmeButton = dom.ancestor(dom.byText("README.md"), "BUTTON");
  await dom.click(readmeButton);
  assert.deepEqual(openedFiles, [["/workspace/gofer-flow/README.md", { preview: true }]]);
  await React.act(async () => reactProps(readmeButton).onDoubleClick(testEvent(readmeButton)));
  assert.deepEqual(openedFiles.at(-1), ["/workspace/gofer-flow/README.md", undefined]);

  await dom.click(dom.byTitle("New file"));
  const nameInput = allElements(dom.container).find(
    (element) => element.tagName === "INPUT" && element.getAttribute("placeholder") === "new-file.txt",
  );
  await dom.change(nameInput, "notes.md");
  await React.act(async () => {
    const form = dom.ancestor(nameInput, "FORM");
    await reactProps(form).onSubmit(testEvent(form));
  });
  await dom.flush();
  assert.ok(dom.byText("notes.md"));
  assert.deepEqual(calls[0], ["create", "/workspace/gofer-flow", "notes.md"]);
  assert.deepEqual(openedFiles.at(-1), ["/workspace/gofer-flow/notes.md", undefined]);

  await contextMenu(dom.byText("README.md"));
  await dom.click(menuAction("Copy"));
  await contextMenu(dom.byText("docs"));
  await dom.click(menuAction("Paste"));
  await dom.flush();
  assert.deepEqual(calls.at(-1), ["copy", "/workspace/gofer-flow/README.md", "/workspace/gofer-flow/docs/README copy.md"]);

  await contextMenu(dom.byText("README.md"));
  await dom.click(menuAction("Open in file explorer"));
  assert.deepEqual(calls.at(-1), ["reveal", "/workspace/gofer-flow/README.md"]);

  await contextMenu(dom.byText("notes.md"));
  await dom.click(menuAction("Rename"));
  const renameInput = allElements(dom.container).find(
    (element) => element.tagName === "INPUT" && element.value === "notes.md",
  );
  await dom.change(renameInput, "decisions.md");
  await React.act(async () => {
    const form = dom.ancestor(renameInput, "FORM");
    await reactProps(form).onSubmit(testEvent(form));
  });
  await dom.flush();
  assert.ok(dom.byText("decisions.md"));

  await contextMenu(dom.byText("decisions.md"));
  await dom.click(menuAction("Delete"));
  await dom.flush();
  assert.equal(calls.at(-1)[0], "delete");
  assert.equal(allElements(dom.container).some((element) => textOf(element) === "decisions.md"), false);

  await dom.unmount();
});

test("Code file explorer reveals and selects the active tab through nested folders", async () => {
  const workflow = {
    ...workflowFixture({ id: "review-pr", name: "Review PR" }),
    projectName: "gofer-flow",
    projectRoot: "/workspace/gofer-flow",
  };
  const listedPaths = [];
  const directories = {
    "/workspace/gofer-flow": [
      { name: "src", path: "/workspace/gofer-flow/src", isDirectory: true, isFile: false },
      { name: "README.md", path: "/workspace/gofer-flow/README.md", isDirectory: false, isFile: true },
    ],
    "/workspace/gofer-flow/src": [
      { name: "features", path: "/workspace/gofer-flow/src/features", isDirectory: true, isFile: false },
    ],
    "/workspace/gofer-flow/src/features": [
      { name: "editor.js", path: "/workspace/gofer-flow/src/features/editor.js", isDirectory: false, isFile: true },
    ],
  };

  function ActiveFileExplorerHarness() {
    const [activeFilePath, setActiveFilePath] = React.useState(
      "/workspace/gofer-flow/README.md",
    );
    return React.createElement(
      React.Fragment,
      null,
      React.createElement("button", {
        type: "button",
        onClick: () => setActiveFilePath("/workspace/gofer-flow/src/features/editor.js"),
      }, "Select nested tab"),
      React.createElement(codeFileExplorerModule.default, {
        activeFilePath,
        workflow,
        onOpenFile() {},
      }),
    );
  }

  const dom = await mountReact(
    React.createElement(ActiveFileExplorerHarness),
    createFetchMock([]),
    {
      desktop: {
        workspace: {
          async trustProjectRoot() {},
          async listDirectory({ currentPath }) {
            listedPaths.push(currentPath);
            return { directory: currentPath, entries: directories[currentPath] ?? [] };
          },
          async gitStatus() {
            return { active: false, entries: [] };
          },
        },
      },
    },
  );

  await dom.flush();
  assert.equal(dom.ancestor(dom.byText("README.md"), "BUTTON").getAttribute("aria-selected"), "true");
  await dom.click(dom.byText("Select nested tab"));
  await dom.flush();
  const activeFile = dom.ancestor(dom.byText("editor.js"), "BUTTON");
  assert.equal(activeFile.getAttribute("aria-selected"), "true");
  assert.equal(activeFile.getAttribute("aria-current"), "page");
  assert.equal(dom.ancestor(dom.byText("src"), "BUTTON").getAttribute("aria-expanded"), "true");
  assert.equal(dom.ancestor(dom.byText("features"), "BUTTON").getAttribute("aria-expanded"), "true");
  assert.ok(listedPaths.includes("/workspace/gofer-flow/src"));
  assert.ok(listedPaths.includes("/workspace/gofer-flow/src/features"));

  await dom.unmount();
});

test("file reveal overlaps folder reads with a four-request limit and stops queued work on selection change", async () => {
  const root = "/workspace/gofer-flow";
  const started = [];
  const gates = [];
  let active = 0, peak = 0;
  function Harness() {
    const [file, setFile] = React.useState(`${root}/README.md`);
    return React.createElement(React.Fragment, null,
      React.createElement("button", { onClick: () => setFile(`${root}/a/b/c/d/e/f/file.js`) }, "Reveal deep file"),
      React.createElement("button", { onClick: () => setFile(`${root}/README.md`) }, "Select root file"),
      React.createElement(codeFileExplorerModule.default, {
        activeFilePath: file, workflow: { ...workflowFixture(), projectRoot: root }, onOpenFile() {},
      }));
  }
  const dom = await mountReact(React.createElement(Harness), createFetchMock([]), { desktop: { workspace: {
    async trustProjectRoot() {},
    async gitStatus() { return { active: false, entries: [] }; },
    async listDirectory({ currentPath }) {
      if (currentPath !== root) {
        started.push(currentPath);
        active += 1; peak = Math.max(peak, active);
        await new Promise(resolve => gates.push(resolve));
        active -= 1;
      }
      return { entries: [] };
    },
  } } });
  try {
    await dom.click(dom.byText("Reveal deep file"));
    await dom.flush();
    assert.equal(started.length, 4);
    assert.equal(peak, 4);
    await dom.click(dom.byText("Select root file"));
    await React.act(async () => { gates.forEach(resolve => resolve()); });
    await dom.flush();
    assert.equal(started.length, 4, "cancelled reveal must not enqueue its remaining folders");
  } finally {
    await React.act(async () => { gates.forEach(resolve => resolve()); });
    await dom.unmount();
  }
});

test("run monitoring overlaps independent log reads and drains failed pairs before scheduling more", async () => {
  const { useWorkflowRunRegistry } = await viteServer.ssrLoadModule("/src/lib/useWorkflowRunRegistry.js");
  const workflows = Array.from({ length: 5 }, (_, index) => ({ id: `parallel-${index}`, name: `Workflow ${index}` }));
  const pending = [];
  let active = 0, peak = 0;
  const fetchMock = async url => {
    const parsed = new URL(url, "http://localhost");
    if (parsed.pathname.endsWith("/workflows")) return { ok: true, json: async () => ({ workflows }) };
    if (parsed.pathname.endsWith("/queue")) return { ok: true, json: async () => ({ runs: [] }) };
    active += 1; peak = Math.max(peak, active);
    return new Promise((resolve, reject) => pending.push({ url: String(url), finish(fail = false) {
      active -= 1;
      if (fail) reject(new Error("log unavailable"));
      else resolve({ ok: true, json: async () => ({ runs: [] }) });
    } }));
  };
  function Harness() { useWorkflowRunRegistry(workflows); return null; }
  const dom = await mountReact(React.createElement(Harness), fetchMock);
  try {
    await dom.flush();
    assert.equal(pending.length, 8);
    assert.equal(pending.filter(item => item.url.includes("status=running")).length, 4);
    await React.act(async () => { pending[0].finish(true); });
    await dom.flush();
    assert.equal(pending.length, 8, "a rejected read cannot free its worker while its sibling is pending");
    await React.act(async () => { pending[1].finish(); });
    await dom.flush();
    assert.equal(pending.length, 10);
    assert.equal(peak, 8);
  } finally {
    await React.act(async () => { pending.slice(2).forEach(item => item.finish()); });
    await dom.unmount();
  }
});

test("Code file explorer renders live Git file states and omits deleted files", async () => {
  const workflow = {
    ...workflowFixture({ id: "review-pr", name: "Review PR" }),
    projectName: "gofer-flow",
    projectRoot: "/workspace/gofer-flow",
    sourceFormat: "rattish",
    sourcePath: "/workspace/gofer-flow/workflow.rattish",
  };
  const selectedProjects = [];
  const removedProjects = [];
  const workspace = {
    async gitStatus() {
      return {
        active: true,
        entries: [
          { path: "src/app.js", status: "M", indexStatus: " ", worktreeStatus: "M", staged: false, unstaged: true },
          { path: "added.txt", status: "A", indexStatus: "A", worktreeStatus: " ", staged: true, unstaged: false },
          { path: "new.txt", status: "U" },
          { path: "gone.txt", status: "D" },
        ],
        root: "/workspace/gofer-flow",
      };
    },
    async listDirectory({ currentPath }) {
      return {
        directory: currentPath,
        entries: currentPath === "/workspace/gofer-flow"
          ? [
              { name: "src", path: `${currentPath}/src`, isDirectory: true, isFile: false },
              { name: "added.txt", path: `${currentPath}/added.txt`, isDirectory: false, isFile: true },
              { name: "new.txt", path: `${currentPath}/new.txt`, isDirectory: false, isFile: true },
            ]
          : [
              { name: "app.js", path: `${currentPath}/app.js`, isDirectory: false, isFile: true },
            ],
      };
    },
    async trustProjectRoot() {},
  };
  const dom = await mountReact(
    React.createElement(React.Fragment, null, React.createElement(appModule.WorkflowSidebar, {
      activeWorkflow: workflow,
      activeWorkflowId: workflow.id,
      loading: false,
      query: "",
      recentProjectRoots: ["/workspace/gofer-flow", "/workspace/other-project"],
      runState: {},
      view: "code",
      workflows: [workflow],
      width: 272,
      onCreate() {},
      onCodeFileOpen() {},
      onCodeFilesystemChange() {},
      onDeleteWorkflow() {},
      onDuplicateWorkflow() {},
      onQueryChange() {},
      onRefresh() {},
      onRenameWorkflow() {},
      onResizeKeyDown() {},
      onResizeStart() {},
      onRunWorkflow() {},
      onSelect() {},
      onSelectProject(projectRoot) { selectedProjects.push(projectRoot); },
      onRemoveRecentProject(projectRoot) { removedProjects.push(projectRoot); },
      onViewChange() {},
    }), React.createElement(appModule.RecentProjectSelector, {
      projectRoot: workflow.projectRoot,
      recentProjectRoots: ["/workspace/gofer-flow", "/workspace/other-project"],
      onSelectProject(projectRoot) { selectedProjects.push(projectRoot); },
      onRemoveRecentProject(projectRoot) { removedProjects.push(projectRoot); },
    })),
    createFetchMock([]),
    { desktop: { workspace } },
  );

  await dom.flush();
  const projectTree = dom.byLabel("Project files");
  const projectContents = dom.byLabel("Project contents");
  assert.doesNotMatch(projectTree.getAttribute("class"), /overflow-y-auto/);
  assert.match(projectContents.getAttribute("class"), /overflow-y-auto/);
  const projectRootRow = allElements(projectTree).find(element => element.tagName === "BUTTON" && element.getAttribute("title") === workflow.projectRoot);
  assert.ok(projectRootRow);
  assert.equal(projectRootRow.parentNode.parentNode, projectTree);
  assert.equal(projectContents.parentNode, projectTree);
  assert.ok(dom.byLabel("gofer-flow contains source control changes"));
  assert.ok(dom.byLabel("src contains source control changes"));
  assert.ok(dom.byLabel("added.txt: Added"));
  assert.ok(dom.byLabel("new.txt: Untracked"));
  assert.equal(
    allElements(projectTree).some((element) => textOf(element) === "gone.txt"),
    false,
  );
  await dom.click(dom.ancestor(dom.byText("src"), "BUTTON"));
  await dom.flush();
  assert.ok(dom.byLabel("app.js: Modified"));
  await dom.click(dom.byLabel("Recent projects"));
  assert.ok(dom.byLabel("Recent projects"));
  await dom.click(dom.ancestor(dom.byText("other-project"), "BUTTON"));
  assert.deepEqual(selectedProjects, ["/workspace/other-project"]);
  await dom.click(dom.byLabel("Recent projects"));
  const removeRecentProjectButton = dom.byLabel("Remove other-project from recent projects");
  assert.equal(removeRecentProjectButton.getAttribute("role"), "menuitem");
  await dom.click(removeRecentProjectButton);
  assert.deepEqual(removedProjects, ["/workspace/other-project"]);

  await dom.unmount();
});

test("code workspace maps common project files to Monaco languages", () => {
  assert.equal(codeWorkspaceModule.languageForPath("/repo/src/app.py"), "python");
  assert.equal(codeWorkspaceModule.languageForPath("/repo/src/app.tsx"), "typescript");
  assert.equal(codeWorkspaceModule.languageForPath("/repo/workflow.metadata.json"), "json");
  assert.equal(codeWorkspaceModule.languageForPath("/repo/Dockerfile"), "dockerfile");
  assert.equal(codeWorkspaceModule.languageForPath("/repo/.env"), "plaintext");
  assert.equal(codeWorkspaceModule.languageForPath("/repo/automation.rattish"), "rattish");
  assert.equal(codeWorkspaceModule.FILE_AUTOSAVE_DELAY_MS, 1000);
  assert.equal(codeWorkspaceModule.isPdfPath("/repo/docs/spec.PDF"), true);
  assert.equal(codeWorkspaceModule.isImagePath("/repo/assets/photo.JPG"), true);
  assert.equal(codeWorkspaceModule.isImagePath("/repo/assets/animation.gif"), true);
  assert.equal(codeWorkspaceModule.isImagePath("/repo/assets/icon.svg"), false);
  assert.equal(codeWorkspaceModule.isSvgPath("/repo/assets/icon.svg"), true);
  assert.equal(codeWorkspaceModule.codeDocumentMode("/repo/assets/icon.svg"), "preview");
  assert.equal(
    codeWorkspaceModule.codeDocumentMode("/repo/assets/icon.svg", {
      "/repo/assets/icon.svg": "edit",
    }),
    "edit",
  );
});

test("code workspace marks tracked lines only when full diff mode is off", () => {
  const baseline = {
    changed: true,
    hunks: [
      { startLine: 3, endLine: 5 },
      { startLine: 11, endLine: 11 },
    ],
  };
  assert.deepEqual(codeWorkspaceModule.trackedChangeDecorations(baseline), [
    {
      options: {
        description: "Git tracked change",
        isWholeLine: true,
        linesDecorationsClassName: "tracked-change-line",
      },
      range: { startLineNumber: 3, startColumn: 1, endLineNumber: 5, endColumn: 1 },
    },
    {
      options: {
        description: "Git tracked change",
        isWholeLine: true,
        linesDecorationsClassName: "tracked-change-line",
      },
      range: { startLineNumber: 11, startColumn: 1, endLineNumber: 11, endColumn: 1 },
    },
  ]);
  assert.deepEqual(codeWorkspaceModule.trackedChangeDecorations(baseline, true), []);
  assert.deepEqual(codeWorkspaceModule.trackedChangeDecorations({ changed: false }), []);
});

test("code diff mode detects and displays whitespace-only changes", () => {
  assert.deepEqual(codeWorkspaceModule.codeDiffEditorOptions({ fontSize: 14 }), {
    diffAlgorithm: "advanced",
    diffCodeLens: true,
    enableSplitViewResizing: true,
    fontSize: 14,
    ignoreTrimWhitespace: false,
    originalEditable: false,
    renderSideBySide: true,
    renderWhitespace: "all",
  });
});

test("code tabs disambiguate duplicate file names with their parent folders", () => {
  const paths = [
    "/repo/.raticode/testing/workflow.rattish",
    "/repo/.raticode/implementation/workflow.rattish",
    "/repo/src/app.jsx",
  ];
  assert.equal(codeWorkspaceModule.duplicateTabFolder(paths[0], paths), "testing");
  assert.equal(codeWorkspaceModule.duplicateTabFolder(paths[1], paths), "implementation");
  assert.equal(codeWorkspaceModule.duplicateTabFolder(paths[2], paths), "");
  assert.equal(
    codeWorkspaceModule.duplicateTabFolder("C:\\repo\\other\\workflow.rattish", [
      "C:\\repo\\main\\workflow.rattish",
      "C:\\repo\\other\\workflow.rattish",
    ]),
    "other",
  );
});

test("Markdown code documents default to preview and resolve relative file links", () => {
  assert.equal(codeWorkspaceModule.isMarkdownPath("/repo/README.md"), true);
  assert.equal(codeWorkspaceModule.isMarkdownPath("/repo/guide.markdown"), true);
  assert.equal(codeWorkspaceModule.codeDocumentMode("/repo/README.md"), "preview");
  assert.equal(
    codeWorkspaceModule.codeDocumentMode("/repo/README.md", { "/repo/README.md": "edit" }),
    "edit",
  );
  assert.equal(codeWorkspaceModule.codeDocumentMode("/repo/app.js"), "edit");
  assert.equal(
    codeWorkspaceModule.resolveMarkdownLinkPath("/repo/docs/guide.md", "../README.md#usage"),
    "/repo/README.md",
  );
  assert.equal(
    codeWorkspaceModule.resolveMarkdownLinkPath(
      "C:\\repo\\docs\\guide.md",
      "../README.md",
    ),
    "C:\\repo\\README.md",
  );
  assert.equal(
    codeWorkspaceModule.resolveMarkdownLinkPath("/repo/docs/guide.md", "https://example.com"),
    "",
  );
  assert.equal(
    codeWorkspaceModule.resolveMarkdownLinkPath(
      "/repo/docs/guide.md",
      "file:///repo/src/app.py#main",
    ),
    "/repo/src/app.py",
  );
  assert.equal(
    codeWorkspaceModule.resolveMarkdownLinkPath(
      "C:\\repo\\docs\\guide.md",
      "file:///C:/repo/src/app.py",
    ),
    "C:\\repo\\src\\app.py",
  );
  assert.deepEqual(
    codeWorkspaceModule.markdownFileLinkTarget(
      "/repo/docs/guide.md",
      "/repo/frontend/src/pages/App.jsx:4406",
    ),
    { column: 1, lineNumber: 4406, path: "/repo/frontend/src/pages/App.jsx" },
  );
  assert.deepEqual(
    codeWorkspaceModule.markdownFileLinkTarget(
      "C:\\repo\\docs\\guide.md",
      "C:\\repo\\frontend\\src\\pages\\App.test.mjs:3262:7",
    ),
    { column: 7, lineNumber: 3262, path: "C:\\repo\\frontend\\src\\pages\\App.test.mjs" },
  );
  assert.equal(appModule.assistantMarkdownSourcePath("/repo"), "/repo/.raticode-assistant.md");
  assert.equal(
    appModule.assistantMarkdownSourcePath("C:\\repo\\"),
    "C:\\repo\\.raticode-assistant.md",
  );
});

test("HTML documents default to browser mode and browser tabs use page titles", () => {
  assert.equal(codeWorkspaceModule.isHtmlPath("/repo/wiki/index.html"), true);
  assert.equal(codeWorkspaceModule.isHtmlPath("/repo/wiki/archive.htm"), true);
  assert.equal(codeWorkspaceModule.isHtmlPath("/repo/wiki/template.html.j2"), false);
  assert.equal(codeWorkspaceModule.codeDocumentMode("/repo/wiki/index.html"), "preview");
  assert.equal(
    codeWorkspaceModule.codeDocumentMode("/repo/wiki/index.html", {
      "/repo/wiki/index.html": "edit",
    }),
    "edit",
  );
  assert.equal(codeWorkspaceModule.browserTabLabel({
    title: "Google",
    url: "https://google.com",
  }), "Google");
  assert.equal(codeWorkspaceModule.browserTabLabel({
    url: "https://www.google.com/search",
  }), "google.com");
  assert.equal(codeWorkspaceModule.browserTabLabel({ url: "about:blank" }), "New Tab");
  assert.equal(codeWorkspaceModule.browserTabLabel({ url: "raticode://home" }), "Raticode");
  assert.match(
    codeWorkspaceModule.browserTabFavicon({ url: "raticode://home" }),
    /roundel\.png$/,
  );
  assert.equal(codeWorkspaceModule.browserTabFavicon({
    favicon: "https://www.youtube.com/s/desktop/favicon.ico",
  }), "https://www.youtube.com/s/desktop/favicon.ico");
  assert.equal(codeWorkspaceModule.browserTabFavicon({ favicon: "javascript:alert(1)" }), "");
  assert.equal(integratedBrowserModule.browserFaviconUrl([
    "javascript:alert(1)",
    "https://www.youtube.com/s/desktop/favicon.ico",
  ]), "https://www.youtube.com/s/desktop/favicon.ico");
  assert.equal(codeWorkspaceModule.browserViewTabMetadata(null, {
    title: "",
    url: "file:///repo/wiki/index.html",
  }), null);
  assert.deepEqual(codeWorkspaceModule.browserViewTabMetadata(null, {
    favicon: "https://www.youtube.com/favicon.ico",
    title: "Metroid Prime 4 - YouTube",
    url: "https://www.youtube.com/watch?v=example",
  }), {
    favicon: "https://www.youtube.com/favicon.ico",
    title: "Metroid Prime 4 - YouTube",
    url: "https://www.youtube.com/watch?v=example",
  });
  assert.equal(codeWorkspaceModule.codeTabLabel("/repo/wiki/index.html"), "index.html");
});

test("HTML preview exposes its diff action beside the read-write toggle", async () => {
  let diffRequests = 0;
  const dom = await mountReact(
    React.createElement(integratedBrowserModule.default, {
      active: true,
      clientId: "html:/repo/index.html",
      localPath: "/repo/index.html",
      onShowDiff: () => { diffRequests += 1; },
      showDiffButton: true,
      showModeToggle: true,
    }),
    createFetchMock([]),
  );
  await dom.click(dom.byLabel("Compare HTML with HEAD"));
  assert.equal(diffRequests, 1);
  assert.ok(dom.byLabel("HTML view mode"));
  await dom.unmount();
});

test("integrated browser shortcut matches VS Code on desktop platforms", () => {
  assert.equal(integratedBrowserModule.isIntegratedBrowserShortcut({
    altKey: true,
    code: "Slash",
    ctrlKey: true,
    key: "/",
    metaKey: false,
    repeat: false,
    shiftKey: false,
  }), true);
  assert.equal(integratedBrowserModule.isIntegratedBrowserShortcut({
    altKey: true,
    code: "Slash",
    ctrlKey: false,
    key: "/",
    metaKey: true,
    repeat: false,
    shiftKey: false,
  }), true);
  assert.equal(integratedBrowserModule.isIntegratedBrowserShortcut({
    altKey: false,
    code: "Slash",
    ctrlKey: true,
    key: "/",
    metaKey: false,
    repeat: false,
    shiftKey: false,
  }), false);
});

test("browser chrome keeps navigation shortcuts when the embedded page is unavailable", () => {
  const shortcut = integratedBrowserModule.browserChromeShortcutAction;
  assert.equal(shortcut({ altKey: true, key: "d" }, "linux"), "focus-location");
  assert.equal(shortcut({ ctrlKey: true, key: "t" }, "linux"), "");
  assert.equal(shortcut({ ctrlKey: true, key: "l" }, "linux"), "");
  assert.equal(shortcut({ ctrlKey: true, key: "r" }, "linux"), "reload");
  assert.equal(shortcut({ altKey: true, key: "ArrowLeft" }, "linux"), "back");
  assert.equal(shortcut({ altKey: true, key: "ArrowRight" }, "linux"), "forward");
  assert.equal(shortcut({ key: "r", metaKey: true }, "darwin"), "reload");
});

test("new browser tabs focus the address on the Raticode home page", async () => {
  const browser = {
    close: async () => null,
    create: async () => ({
      id: "home-session",
      loading: true,
      src: "data:text/html,Raticode",
      url: "raticode://home",
    }),
    onCommand: () => () => {},
    onState: () => () => {},
    platform: "linux",
    setPreferences: async () => null,
  };
  const dom = await mountReact(
    React.createElement(integratedBrowserModule.default, {
      active: true,
      clientId: "raticode-browser:home",
      focusLocationOnCreate: true,
      initialUrl: "raticode://home",
    }),
    createFetchMock([]),
    { browser },
  );
  await dom.flush();

  assert.equal(document.activeElement === dom.byLabel("Browser address"), true);
  await dom.unmount();
});

test("cycling browser tabs transfers native focus to the selected guest", async () => {
  const commandSubscribers = [];
  const focusCalls = [];
  const sessions = new Map();
  const browser = {
    adopt: async () => null,
    close: async () => null,
    create: async ({ clientId, url }) => {
      const id = `session-${clientId}`;
      sessions.set(clientId, id);
      return {
        clientId,
        error: "",
        id,
        loading: false,
        ready: true,
        src: url,
        url,
      };
    },
    focus: async (id) => { focusCalls.push(id); },
    onCommand: (callback) => {
      commandSubscribers.push(callback);
      return () => {};
    },
    onState: () => () => {},
    platform: "linux",
    setPreferences: async () => null,
  };
  const paths = ["raticode-browser:first", "raticode-browser:second"];
  function BrowserTabsHarness() {
    const [activePath, setActivePath] = React.useState(paths[0]);
    return React.createElement(codeWorkspaceModule.default, {
      active: true,
      activePath,
      browserTabs: {
        [paths[0]]: { title: "First", url: "https://example.com/first" },
        [paths[1]]: { title: "Second", url: "https://example.com/second" },
      },
      onActivePathChange: setActivePath,
      openPaths: paths,
      workflow: { projectRoot: "/repo" },
    });
  }
  const dom = await mountReact(
    React.createElement(BrowserTabsHarness),
    createFetchMock([]),
    { browser },
  );
  await dom.flush();
  focusCalls.length = 0;

  await React.act(async () => {
    for (const callback of commandSubscribers) {
      callback({
        action: "next-tab",
        clientId: paths[0],
        id: sessions.get(paths[0]),
      });
    }
  });
  await dom.flush();
  await React.act(async () => {
    for (const callback of commandSubscribers) {
      callback({
        action: "previous-tab",
        clientId: paths[1],
        id: sessions.get(paths[1]),
      });
    }
  });
  await dom.flush();

  assert.deepEqual(focusCalls, [sessions.get(paths[1]), sessions.get(paths[0])]);
  await dom.unmount();
});

test("cycling tabs inside an unfocused split browser pane stays in that pane", async () => {
  const commandSubscribers = [];
  const focusCalls = [];
  const paths = [
    "raticode-browser:left",
    "raticode-browser:right-one",
    "raticode-browser:right-two",
  ];
  const browserTabs = Object.fromEntries(paths.map((clientId) => [clientId, {
    title: clientId.split(":").at(-1),
    url: `https://example.com/${clientId}`,
  }]));
  const browser = {
    adopt: async () => null,
    close: async () => null,
    create: async ({ clientId, url }) => ({
      clientId,
      error: "",
      id: `session-${clientId}`,
      loading: false,
      ready: true,
      src: url,
      url,
    }),
    focus: async (id) => { focusCalls.push(id); },
    onCommand: (callback) => {
      commandSubscribers.push(callback);
      return () => {};
    },
    onState: () => () => {},
    platform: "linux",
    setPreferences: async () => null,
  };
  function SplitBrowserTabsHarness() {
    const [activePath, setActivePath] = React.useState(paths[0]);
    return React.createElement(codeWorkspaceModule.default, {
      active: true,
      activePath,
      browserTabs,
      onActivePathChange: setActivePath,
      openPaths: paths,
      workflow: { projectRoot: "/repo" },
    });
  }
  const dom = await mountReact(
    React.createElement(SplitBrowserTabsHarness),
    createFetchMock([]),
    { browser },
  );
  await dom.flush();

  const transferValues = new Map();
  const dataTransfer = {
    dropEffect: "none",
    effectAllowed: "none",
    getData: (type) => transferValues.get(type) ?? "",
    setData: (type, value) => transferValues.set(type, value),
  };
  await dom.pointer(dom.ancestor(dom.byText("right-one"), "BUTTON"), "onDragStart", { dataTransfer });
  await dom.pointer(dom.byLabel("Split editor right"), "onDrop", { dataTransfer });
  await dom.flush();
  await dom.pointer(dom.ancestor(dom.byText("right-two"), "BUTTON"), "onDragStart", { dataTransfer });
  await dom.pointer(dom.byLabel("Split editor tabs"), "onDrop", { dataTransfer });
  await dom.flush();
  focusCalls.length = 0;

  await React.act(async () => {
    for (const callback of commandSubscribers) {
      callback({
        action: "next-tab",
        clientId: paths[1],
        id: `session-${paths[1]}`,
      });
    }
  });
  await dom.flush();

  assert.equal(dom.ancestor(dom.byText("right-two"), "BUTTON").getAttribute("aria-selected"), "true");
  assert.deepEqual(focusCalls, [`session-${paths[2]}`]);

  await dom.dispatchWindow("keydown", { ctrlKey: true, key: "Tab" });
  await dom.flush();

  assert.equal(dom.ancestor(dom.byText("right-one"), "BUTTON").getAttribute("aria-selected"), "true");
  assert.deepEqual(focusCalls, [`session-${paths[2]}`, `session-${paths[1]}`]);
  await dom.unmount();
});

test("single words use the configured browser search engine", () => {
  assert.equal(
    integratedBrowserModule.browserAddress("asdf", "https://search.example/?q={query}"),
    "https://search.example/?q=asdf",
  );
  assert.equal(
    integratedBrowserModule.browserAddress("two words", "https://search.example/?q={query}"),
    "https://search.example/?q=two%20words",
  );
  assert.equal(
    integratedBrowserModule.browserAddress("example.com/docs", "https://search.example/?q={query}"),
    "example.com/docs",
  );
});

test("browser addresses normalize dev servers, websites, and searches", () => {
  const {
    browserLoadUrl,
    browserCommandRequiresOwnerFocus,
    browserApplicationShortcutAction,
    browserContentZoomFactor,
    browserProjectChordAction,
    browserSessionShortcutAction,
    browserShortcutAction,
    browserWheelZoomAction,
    normalizeBrowserUrl,
  } = require("../../electron/browser-utils.cjs");
  assert.equal(normalizeBrowserUrl("localhost:5173/app"), "http://localhost:5173/app");
  assert.equal(normalizeBrowserUrl("example.com/docs"), "https://example.com/docs");
  assert.equal(
    normalizeBrowserUrl("raticode browser docs"),
    "https://www.google.com/search?q=raticode%20browser%20docs",
  );
  assert.equal(
    normalizeBrowserUrl("asdf"),
    "https://www.google.com/search?q=asdf",
  );
  assert.throws(() => normalizeBrowserUrl("javascript:alert(1)"), /http:\/\/ or https:\/\//);
  assert.equal(browserContentZoomFactor(1.4, 1), 1.4);
  assert.equal(browserContentZoomFactor(1.4, 1.1), 1.54);
  assert.equal(browserContentZoomFactor(1.5, 3), 3);
  assert.equal(browserShortcutAction({ alt: true, key: "d", type: "keyDown" }, "linux"), "focus-location");
  const applicationSession = { applicationChord: null };
  assert.equal(
    browserApplicationShortcutAction(
      applicationSession,
      { code: "KeyJ", control: true, key: "j", type: "keyDown" },
      { "browser.open": "Ctrl+KeyJ" },
      "linux",
    ),
    "command:browser.open",
  );
  assert.equal(
    browserApplicationShortcutAction(
      applicationSession,
      { code: "KeyK", control: true, key: "k", type: "keyDown" },
      { "project.open": "Mod+KeyK Mod+KeyO" },
      "linux",
    ),
    "chord-pending",
  );
  assert.equal(
    browserApplicationShortcutAction(
      applicationSession,
      { code: "KeyO", control: true, key: "o", type: "keyDown" },
      { "project.open": "Mod+KeyK Mod+KeyO" },
      "linux",
    ),
    "command:project.open",
  );
  assert.equal(browserShortcutAction({ control: true, key: "l", type: "keyDown" }, "linux"), "assistant-pane-toggle");
  assert.equal(browserShortcutAction({ control: true, key: "b", type: "keyDown" }, "linux"), "project-pane-toggle");
  assert.equal(browserShortcutAction({ control: true, key: "o", type: "keyDown" }, "linux"), "file-open");
  assert.equal(browserShortcutAction({ control: true, key: "`", type: "keyDown" }, "linux"), "panel-toggle");
  const browserSession = { projectChordDeadline: 0 };
  assert.equal(
    browserProjectChordAction(browserSession, { control: true, key: "k", type: "keyDown" }, "linux"),
    "chord-pending",
  );
  assert.equal(
    browserProjectChordAction(browserSession, { control: true, key: "o", type: "keyDown" }, "linux"),
    "project-open",
  );
  assert.equal(browserShortcutAction({ alt: true, control: true, key: "/", type: "keyDown" }, "linux"), "open-browser");
  assert.equal(
    browserShortcutAction(
      { alt: true, code: "KeyB", key: "b", type: "keyDown" },
      "linux",
      "Alt+KeyB",
    ),
    "open-browser",
  );
  assert.equal(
    browserShortcutAction(
      { alt: true, control: true, key: "/", type: "keyDown" },
      "linux",
      "Alt+KeyB",
    ),
    "",
  );
  assert.equal(browserShortcutAction({ key: "r", meta: true, type: "keyDown" }, "darwin"), "reload");
  assert.equal(browserShortcutAction({ control: true, key: "w", type: "keyDown" }, "linux"), "close");
  assert.equal(
    browserSessionShortcutAction(
      {
        applicationKeybindings: {
          "file.close": "Mod+KeyW",
          "terminal.new": "Ctrl+KeyT",
        },
        openBrowserBinding: "Mod+Alt+Slash",
      },
      { code: "KeyW", control: true, key: "w", type: "keyDown" },
      "linux",
    ),
    "close",
  );
  assert.equal(
    browserSessionShortcutAction(
      {
        applicationKeybindings: { "terminal.new": "Ctrl+KeyT" },
        openBrowserBinding: "Mod+Alt+Slash",
      },
      { code: "KeyT", control: true, key: "t", type: "keyDown" },
      "linux",
    ),
    "command:terminal.new",
  );
  assert.equal(browserWheelZoomAction({ control: true, deltaY: -100, type: "mouseWheel" }), "zoom-in");
  assert.equal(browserWheelZoomAction({ modifiers: ["control"], deltaY: 100, type: "mouseWheel" }), "zoom-out");
  assert.equal(browserWheelZoomAction({ modifiers: ["ctrl"], deltaY: -100, type: "mouseWheel" }), "zoom-in");
  assert.equal(browserWheelZoomAction({ meta: true, deltaY: -100, type: "mouseWheel" }), "zoom-in");
  assert.equal(browserWheelZoomAction({ modifiers: ["command"], deltaY: 100, type: "mouseWheel" }), "zoom-out");
  assert.equal(browserWheelZoomAction({ modifiers: ["meta", "alt"], deltaY: 100, type: "mouseWheel" }), "");
  assert.equal(browserWheelZoomAction({ deltaY: -100, type: "mouseWheel" }), "");
  assert.equal(browserShortcutAction({ control: true, key: "t", type: "keyDown" }, "linux"), "");
  assert.equal(browserShortcutAction({ control: true, key: "Tab", type: "keyDown" }, "linux"), "next-tab");
  assert.equal(browserShortcutAction({ control: true, key: "Tab", shift: true, type: "keyDown" }, "linux"), "previous-tab");
  for (const action of ["close", "edit-local-html", "focus-location", "new-tab", "next-tab", "previous-tab"]) {
    assert.equal(browserCommandRequiresOwnerFocus(action), true, action);
  }
  for (const action of ["back", "open-browser", "reload", "text-zoom"]) {
    assert.equal(browserCommandRequiresOwnerFocus(action), false, action);
  }
  assert.equal(normalizeBrowserUrl("raticode://home"), "raticode://home");
  const homePage = decodeURIComponent(browserLoadUrl("raticode://home").split(",", 2)[1]);
  assert.match(homePage, /Workflows that stay on your machine/);
  assert.match(homePage, /graph-based automation/);
  assert.match(homePage, /Alt \+ D/);
  assert.doesNotMatch(homePage, /Ctrl \+ L/);
  assert.match(homePage, /prefers-color-scheme:dark/);
});

test("browser shortcuts create a new tab on every renderer and embedded-page command", async () => {
  let browserSequence = 0;
  const commandListeners = new Set();
  const browser = {
    adopt: async () => null,
    close: async () => null,
    create: async ({ clientId, url }) => {
      browserSequence += 1;
      return {
        clientId,
        id: `browser-session-${browserSequence}`,
        loading: false,
        src: "data:text/html,Raticode",
        url,
      };
    },
    onCommand: (listener) => {
      commandListeners.add(listener);
      return () => commandListeners.delete(listener);
    },
    onState: () => () => {},
    platform: "linux",
    setPreferences: async () => null,
  };
  const workflow = {
    ...workflowFixture(),
    sourceFormat: "rattish",
    sourcePath: "/workspace/.raticode/demo/workflow.rattish",
  };
  const dom = await mountReact(
    React.createElement(appModule.default),
    createFetchMock([
      jsonResponse("/api/workflows", workflowsPayload([workflow])),
      jsonResponse("/api/workflows/demo/document", {
        document: { diagnostics: [], preflight: { diagnostics: [] }, source: "Rattish: 1\n" },
      }),
    ]),
    { browser },
  );
  await dom.flush();

  await dom.dispatchWindow("keydown", {
    code: "KeyJ",
    ctrlKey: true,
    key: "j",
  });
  await dom.flush();
  assert.ok(dom.byLabel("Integrated browser"));
  assert.equal(allElements(dom.container).filter(
    (element) => element.getAttribute?.("aria-label") === "Close Raticode",
  ).length, 1);
  assert.equal(document.activeElement?.getAttribute?.("aria-label"), "Browser address");

  await dom.dispatchWindow("keydown", {
    code: "KeyJ",
    ctrlKey: true,
    key: "j",
  });
  await dom.flush();
  assert.equal(allElements(dom.container).filter(
    (element) => element.getAttribute?.("aria-label") === "Close Raticode",
  ).length, 2);

  for (const command of [
    { action: "open-browser" },
    { action: "application-shortcut", commandId: "browser.open" },
  ]) {
    const before = allElements(dom.container).filter(
      (element) => element.getAttribute?.("aria-label") === "Close Raticode",
    ).length;
    for (const listener of commandListeners) listener(command);
    await dom.flush();
    assert.equal(allElements(dom.container).filter(
      (element) => element.getAttribute?.("aria-label") === "Close Raticode",
    ).length, before + 1);
  }

  await dom.unmount();
});

test("hiding the Rem keeps its mounted thread state alive", async () => {
  const workflow = workflowFixture();
  const dom = await mountReact(
    React.createElement(appModule.default),
    createFetchMock([jsonResponse("/api/workflows", workflowsPayload([workflow]))]),
  );
  await dom.flush();
  const assistantPane = allElements(dom.container).find(
    (element) => element.getAttribute?.("data-chat-pane") === "true",
  );
  assert.ok(assistantPane);

  await dom.dispatchWindow("keydown", {
    code: "KeyL",
    ctrlKey: true,
    key: "l",
  });
  await dom.flush();
  const hiddenAssistantPane = allElements(dom.container).find(
    (element) => element.getAttribute?.("data-chat-pane") === "true",
  );
  assert.equal(hiddenAssistantPane, assistantPane);
  assert.equal(
    dom.ancestor(
      hiddenAssistantPane,
      (element) => element.getAttribute?.("aria-hidden") === "true",
    ).getAttribute("class"),
    "hidden",
  );
  await dom.unmount();
});

test("Markdown links are interactive and relative links stay inside the code workspace", async () => {
  const openedLinks = [];
  const openedUrls = [];
  const dom = await mountReact(
    React.createElement(markdownContentModule.default, {
      value: "[Open the guide](../guide.md), [open a file](file:///repo/README.md), [open source](/repo/App.jsx:4406), and [visit docs](https://example.com/docs).",
      onOpenRelativeLink: (href) => openedLinks.push(href),
    }),
    createFetchMock([]),
  );
  window.open = (...args) => openedUrls.push(args);
  const guideLink = dom.ancestor(dom.byText("Open the guide"), "A");
  const fileLink = dom.ancestor(dom.byText("open a file"), "A");
  const sourceLink = dom.ancestor(dom.byText("open source"), "A");
  const docsLink = dom.ancestor(dom.byText("visit docs"), "A");
  assert.equal(guideLink.getAttribute("target"), null);
  assert.equal(fileLink.getAttribute("href"), "file:///repo/README.md");
  assert.equal(fileLink.getAttribute("target"), null);
  assert.equal(docsLink.getAttribute("target"), "_blank");
  await dom.click(guideLink);
  await dom.click(fileLink);
  await dom.click(sourceLink);
  await dom.click(docsLink);
  assert.deepEqual(openedLinks, [
    "../guide.md",
    "file:///repo/README.md",
    "/repo/App.jsx:4406",
  ]);
  assert.deepEqual(openedUrls, [["https://example.com/docs", "_blank", "noopener,noreferrer"]]);
  await dom.unmount();
});

test("Markdown file targets open only after resolving to files", async () => {
  const inspected = [];
  assert.equal(
    await codeWorkspaceModule.resolveMarkdownFileTarget(
      "/repo/docs/guide.md",
      "../README.md",
      async (targetPath) => {
        inspected.push(targetPath);
        return { isFile: true, path: targetPath };
      },
    ),
    "/repo/README.md",
  );
  assert.deepEqual(inspected, ["/repo/README.md"]);
  assert.deepEqual(
    await codeWorkspaceModule.resolveMarkdownFileLinkTarget(
      "/repo/.raticode-assistant.md",
      "/repo/frontend/src/pages/App.jsx:4406",
      async (targetPath) => {
        inspected.push(targetPath);
        return { isFile: true, path: targetPath };
      },
    ),
    { column: 1, lineNumber: 4406, path: "/repo/frontend/src/pages/App.jsx" },
  );
  assert.equal(inspected.at(-1), "/repo/frontend/src/pages/App.jsx");
  await assert.rejects(
    codeWorkspaceModule.resolveMarkdownFileTarget(
      "/repo/docs/guide.md",
      "../assets",
      async () => ({ isDirectory: true, isFile: false }),
    ),
    /does not point to a file/,
  );
  assert.equal(
    await codeWorkspaceModule.resolveMarkdownFileTarget(
      "/repo/docs/guide.md",
      "https://example.com/docs",
      async () => ({ isFile: true }),
    ),
    "",
  );
});

test("editor navigation reveals and focuses linked source locations", () => {
  const calls = [];
  const editor = {
    focus: () => calls.push(["focus"]),
    revealPositionInCenter: (position) => calls.push(["reveal", position]),
    setPosition: (position) => calls.push(["position", position]),
  };
  assert.equal(codeWorkspaceModule.revealEditorLocation(editor, {
    column: 7,
    lineNumber: 3262,
  }), true);
  assert.deepEqual(calls, [
    ["reveal", { column: 7, lineNumber: 3262 }],
    ["position", { column: 7, lineNumber: 3262 }],
    ["focus"],
  ]);
  assert.equal(codeWorkspaceModule.revealEditorLocation(editor, { lineNumber: 0 }), false);
});

test("Markdown previews enter editing on double click and expose both mode controls", async () => {
  const modeChanges = [];
  const dom = await mountReact(
    React.createElement("div", null,
      React.createElement(codeWorkspaceModule.MarkdownPreview, {
        content: "# Preview",
        path: "/repo/README.md",
        onEdit: () => modeChanges.push("edit"),
      }),
      React.createElement(codeWorkspaceModule.MarkdownModeToggle, {
        editing: false,
        onModeChange: (mode) => modeChanges.push(mode),
      }),
    ),
    createFetchMock([]),
  );
  const preview = allElements(dom.container).find(
    (element) => element.getAttribute?.("aria-label") === "README.md Markdown preview",
  );
  assert.ok(preview);
  assert.equal(dom.byTitle("Preview Markdown").getAttribute("aria-pressed"), "true");
  assert.equal(dom.byTitle("Edit Markdown").getAttribute("aria-pressed"), "false");
  await dom.pointer(preview, "onDoubleClick");
  await dom.click(dom.byTitle("Edit Markdown"));
  await dom.click(dom.byTitle("Preview Markdown"));
  assert.deepEqual(modeChanges, ["edit", "edit", "preview"]);
  await dom.unmount();
});

test("live Rattish analysis preserves dirty state and ignores stale source responses", () => {
  const current = {
    document: { diagnostics: [], dirty: true, source: "Rattish: 1\n" },
    error: "",
    loading: false,
    saving: false,
  };
  const analyzed = {
    diagnostics: [{ code: "RAD001", message: "Missing Workflow" }],
    dirty: false,
    source: "Rattish: 1\n",
  };

  const merged = appModule.mergeRattishAnalysisState(current, analyzed, "Rattish: 1\n");
  assert.equal(merged.document.dirty, true);
  assert.deepEqual(merged.document.diagnostics, analyzed.diagnostics);
  assert.equal(
    appModule.mergeRattishAnalysisState(current, analyzed, "Rattish: 2\n"),
    current,
  );
});

test("file explorer shortcuts create and close files without key-repeat firing", () => {
  const primaryModifier = /Mac|iPhone|iPad/i.test(globalThis.navigator?.platform ?? "")
    ? { metaKey: true }
    : { ctrlKey: true };
  const action = (key, options = {}, event = {}) => codeFileExplorerModule.explorerShortcutAction(
    {
      altKey: false,
      ctrlKey: false,
      key,
      metaKey: false,
      repeat: false,
      shiftKey: false,
      ...primaryModifier,
      ...event,
    },
    { activeFilePath: "/repo/app.js", ...options },
  );
  assert.equal(action("n"), "new");
  assert.equal(action("w"), "close");
  assert.equal(action("w", { activeFilePath: "" }), null);
  assert.equal(action("n", {}, { repeat: true }), null);
  assert.equal(action("w", {}, { shiftKey: true }), null);
});

test("dirty file close protection prompts to save only when autosave is off", () => {
  assert.equal(codeWorkspaceModule.codeCloseProtection([], false), "close");
  assert.equal(
    codeWorkspaceModule.codeCloseProtection(["/repo/notes.txt"], false),
    "prompt-to-save",
  );
  assert.equal(
    codeWorkspaceModule.codeCloseProtection(["/repo/notes.txt"], true),
    "confirm-discard",
  );
});

test("code editor shortcuts stay scoped and ignore held keys", () => {
  const primaryModifier = /Mac|iPhone|iPad/i.test(globalThis.navigator?.platform ?? "")
    ? { metaKey: true }
    : { ctrlKey: true };
  const action = (key, options = {}, event = {}) => codeWorkspaceModule.codeWorkspaceShortcutAction(
    {
      altKey: false,
      ctrlKey: false,
      key,
      metaKey: false,
      repeat: false,
      shiftKey: false,
      ...primaryModifier,
      ...event,
    },
    { active: true, currentPath: "/repo/app.js", ...options },
  );
  assert.equal(action("n"), "new");
  assert.equal(action("w"), "close");
  assert.equal(
    action("z", {}, { altKey: true, ctrlKey: false, metaKey: false }),
    "toggle-word-wrap",
  );
  assert.equal(action("n", { active: false }), null);
  assert.equal(action("w", { currentPath: "" }), null);
  assert.equal(action("n", {}, { repeat: true }), null);
  assert.equal(action("w", {}, { shiftKey: true }), null);
  assert.equal(action("Tab", {}, { ctrlKey: true, metaKey: false }), "next-tab");
  assert.equal(
    action("Tab", {}, { ctrlKey: true, metaKey: false, shiftKey: true }),
    "previous-tab",
  );
  assert.equal(action("t", { browserActive: true }), null);
  assert.equal(action("t", { browserActive: false }), null);

  const customSettings = settingsModule.updateSetting(
    settingsModule.DEFAULT_APP_SETTINGS,
    "keybindings.editor.toggleWordWrap",
    "Alt+KeyY",
  );
  assert.equal(
    action("z", { settings: customSettings }, { altKey: true, ctrlKey: false, metaKey: false }),
    null,
  );
  assert.equal(
    action("y", { settings: customSettings }, { altKey: true, ctrlKey: false, metaKey: false }),
    "toggle-word-wrap",
  );
});

test("workspace preview shortcuts close Markdown, local HTML, and SVG tabs", () => {
  const primaryModifier = /Mac|iPhone|iPad/i.test(globalThis.navigator?.platform ?? "")
    ? { metaKey: true }
    : { ctrlKey: true };
  for (const previewPath of ["/repo/README.md", "/repo/index.html", "/repo/icon.svg"]) {
    assert.equal(codeWorkspaceModule.codeDocumentMode(previewPath), "preview");
    assert.equal(codeWorkspaceModule.codeWorkspaceShortcutAction(
      {
        altKey: false,
        ctrlKey: false,
        key: "w",
        metaKey: false,
        repeat: false,
        shiftKey: false,
        ...primaryModifier,
      },
      {
        active: true,
        currentPath: previewPath,
      },
    ), "close");
  }
});

test("workspace shortcuts cycle tabs and leave Ctrl+T to the terminal", async () => {
  const activePaths = [];
  const browserRequests = [];
  const paths = [
    "raticode-browser:one",
    "raticode-browser:two",
    "raticode-browser:three",
  ];
  const tabs = Object.fromEntries(paths.map((pathValue, index) => [pathValue, {
    title: `Tab ${index + 1}`,
    url: `https://example.com/${index + 1}`,
  }]));
  const dom = await mountReact(
    React.createElement(codeWorkspaceModule.default, {
      active: true,
      activePath: paths[0],
      browserTabs: tabs,
      openPaths: paths,
      onActivePathChange: (pathValue) => activePaths.push(pathValue),
      onOpenBrowser: (options) => browserRequests.push(options),
      workflow: { projectRoot: "/repo" },
    }),
    createFetchMock([]),
  );
  await dom.dispatchWindow("keydown", { ctrlKey: true, key: "Tab" });
  await dom.dispatchWindow("keydown", { ctrlKey: true, key: "Tab", shiftKey: true });
  assert.deepEqual(activePaths, [paths[1], paths[2]]);
  assert.equal(codeWorkspaceModule.adjacentCodeTab(paths, paths[2], 1), paths[0]);
  await dom.unmount();

  const browserDom = await mountReact(
    React.createElement(codeWorkspaceModule.default, {
      active: true,
      activePath: paths[2],
      browserTabs: { [paths[2]]: tabs[paths[2]] },
      openPaths: [paths[2]],
      onOpenBrowser: (options) => browserRequests.push(options),
      workflow: { projectRoot: "/repo" },
    }),
    createFetchMock([]),
  );
  await browserDom.dispatchWindow("keydown", { ctrlKey: true, key: "t" });
  assert.deepEqual(browserRequests, []);
  await browserDom.unmount();
});

test("editor tabs stay readable while the horizontal scrollbar autohides", async () => {
  const paths = [
    "raticode-browser:one",
    "raticode-browser:two",
    "raticode-browser:three",
  ];
  const browserTabs = Object.fromEntries(paths.map((pathValue, index) => [pathValue, {
    title: `Browser tab ${index + 1}`,
    url: `https://example.com/${index + 1}`,
  }]));
  const dom = await mountReact(
    React.createElement(codeWorkspaceModule.default, {
      active: true,
      activePath: paths[0],
      browserTabs,
      openPaths: paths,
      workflow: { projectRoot: "/repo" },
    }),
    createFetchMock([]),
  );

  const tabStrip = dom.byLabel("Editor tabs");
  const firstTab = dom.ancestor(dom.byText("Browser tab 1"), "BUTTON").parentNode;
  assert.match(tabStrip.getAttribute("class"), /tab-strip-scrollbar/);
  assert.match(tabStrip.getAttribute("class"), /overflow-y-hidden/);
  assert.match(firstTab.getAttribute("class"), /w-48/);
  assert.match(firstTab.getAttribute("class"), /shrink-0/);

  const css = fs.readFileSync(path.join(frontendRoot, "src/styles/index.css"), "utf8");
  assert.match(css, /\.tab-strip-scrollbar::-webkit-scrollbar\s*{[^}]*height:\s*3px;/s);
  assert.match(css, /\.tab-strip-scrollbar\s*{[^}]*scrollbar-color:\s*transparent transparent;/s);
  assert.match(css, /\.tab-strip-scrollbar:hover::-webkit-scrollbar-thumb/);

  await dom.unmount();
});

test("file tab context actions target the expected tabs", () => {
  const paths = ["/repo/a.js", "/repo/b.js", "/repo/c.js"];
  assert.deepEqual(codeWorkspaceModule.fileTabCloseTargets(paths, paths[1], "close"), [paths[1]]);
  assert.deepEqual(codeWorkspaceModule.fileTabCloseTargets(paths, paths[1], "others"), [paths[0], paths[2]]);
  assert.deepEqual(codeWorkspaceModule.fileTabCloseTargets(paths, paths[1], "right"), [paths[2]]);
  assert.deepEqual(codeWorkspaceModule.fileTabCloseTargets(paths, paths[1], "all"), paths);
  assert.deepEqual(codeWorkspaceModule.fileTabCloseTargets(paths, "/repo/missing.js", "all"), []);
  assert.deepEqual(codeWorkspaceModule.reorderCodeTabs(paths, paths[0], paths[2]), [paths[1], paths[2], paths[0]]);
  assert.deepEqual(codeWorkspaceModule.reorderCodeTabs(paths, paths[2], paths[0]), [paths[2], paths[0], paths[1]]);
  assert.deepEqual(
    codeWorkspaceModule.stableCodeDocumentPaths(paths, [paths[1], paths[2], paths[0]]),
    paths,
  );
  assert.deepEqual(
    codeWorkspaceModule.stableCodeDocumentPaths(paths, [paths[2], "/repo/d.js"]),
    [paths[2], "/repo/d.js"],
  );
});

test("active browser tabs drag from the left edge and keep their guest while changing panes", async () => {
  const paths = ["raticode-browser:first", "raticode-browser:second"];
  const browserTabs = {
    [paths[0]]: { title: "First", url: "https://example.com/first" },
    [paths[1]]: { title: "Second", url: "https://example.com/second" },
  };
  const created = [];
  const closed = [];
  const commandSubscribers = [];
  const focusCalls = [];
  const browser = {
    adopt: async () => null,
    close: async (id) => { closed.push(id); },
    create: async ({ clientId, url }) => {
      created.push(clientId);
      return {
        canGoBack: false,
        canGoForward: false,
        clientId,
        error: "",
        favicon: "",
        id: `session-${clientId}`,
        loading: false,
        ready: true,
        src: url,
        title: browserTabs[clientId].title,
        url,
      };
    },
    focus: async (id) => { focusCalls.push(id); },
    onCommand: (callback) => {
      commandSubscribers.push(callback);
      return () => {};
    },
    onState: () => () => {},
    platform: "linux",
    setPreferences: async () => null,
  };
  const reordered = [];
  function BrowserTabHarness() {
    const [openPaths, setOpenPaths] = React.useState(paths);
    const [activePath, setActivePath] = React.useState(paths[0]);
    return React.createElement(codeWorkspaceModule.default, {
      active: true,
      activePath,
      browserTabs,
      openPaths,
      onActivePathChange: setActivePath,
      onOpenPathsChange: (nextPaths) => {
        reordered.push(nextPaths);
        setOpenPaths(nextPaths);
      },
      workflow: { projectRoot: "/repo" },
    });
  }
  const dom = await mountReact(
    React.createElement(BrowserTabHarness),
    createFetchMock([]),
    { browser },
  );
  await dom.flush();
  assert.deepEqual(created, paths);
  const mountedBrowserUrls = () => allElements(dom.container)
    .filter((node) => node.tagName === "WEBVIEW")
    .map((node) => node.getAttribute("src"));
  assert.deepEqual(mountedBrowserUrls(), paths.map((pathValue) => browserTabs[pathValue].url));

  const transferValues = new Map();
  const dataTransfer = {
    dropEffect: "none",
    effectAllowed: "none",
    getData: (type) => transferValues.get(type) ?? "",
    setData: (type, value) => transferValues.set(type, value),
  };
  const firstTab = dom.ancestor(dom.byText("First"), "BUTTON");
  await dom.pointer(firstTab, "onDragStart", { dataTransfer });
  assert.equal(dataTransfer.effectAllowed, "move");
  assert.equal(dataTransfer.getData("text/plain"), paths[0]);
  const splitRight = dom.byLabel("Split editor right");
  assert.equal(splitRight.style.gridRow, "2", "split target covered the tab strip");
  assert.equal(dom.byLabel("Editor tabs").parentNode.style.gridRow, "1");
  await dom.pointer(splitRight, "onDrop", { dataTransfer });
  await dom.flush();

  assert.deepEqual(created, paths, "splitting recreated an integrated browser guest");
  assert.deepEqual(closed, [], "splitting closed an integrated browser guest");
  assert.ok(dom.byLabel("Split editor tabs"));

  const splitFirstTab = dom.ancestor(dom.byText("First"), "BUTTON");
  const secondTab = dom.ancestor(dom.byText("Second"), "BUTTON");
  await dom.pointer(splitFirstTab, "onDragStart", { dataTransfer });
  await dom.pointer(secondTab.parentNode, "onDrop", { dataTransfer });
  await dom.flush();

  assert.deepEqual(reordered, [[paths[1], paths[0]]]);
  assert.deepEqual(
    mountedBrowserUrls(),
    paths.map((pathValue) => browserTabs[pathValue].url),
    "reordering tabs moved mounted Electron webviews in the DOM",
  );
  assert.deepEqual(created, paths, "moving a tab between panes recreated its browser guest");
  assert.deepEqual(closed, [], "moving a tab between panes closed its browser guest");

  focusCalls.length = 0;
  await React.act(async () => {
    for (const callback of commandSubscribers) {
      callback({ action: "next-tab", clientId: paths[0], id: `session-${paths[0]}` });
    }
  });
  await dom.flush();
  assert.equal(dom.ancestor(dom.byText("Second"), "BUTTON").getAttribute("aria-selected"), "true");
  assert.deepEqual(focusCalls, [`session-${paths[1]}`]);

  await dom.unmount();
  assert.deepEqual(closed.sort(), paths.map((pathValue) => `session-${pathValue}`).sort());
});

test("file previews replace only the previous preview and pin on a permanent open", () => {
  const source = "/repo/workflow.rattish";
  const first = appModule.nextCodeFileOpenState([source], "", "/repo/first.js", true);
  assert.deepEqual(first, {
    openPaths: [source, "/repo/first.js"],
    previewPath: "/repo/first.js",
  });
  const second = appModule.nextCodeFileOpenState(
    first.openPaths,
    first.previewPath,
    "/repo/second.js",
    true,
  );
  assert.deepEqual(second, {
    openPaths: [source, "/repo/second.js"],
    previewPath: "/repo/second.js",
  });
  assert.deepEqual(appModule.nextCodeFileOpenState(
    second.openPaths,
    second.previewPath,
    "/repo/second.js",
    false,
  ), {
    openPaths: [source, "/repo/second.js"],
    previewPath: "",
  });
});

test("workflow switches retain editor tabs from every project", () => {
  assert.deepEqual(appModule.mergeCodeOpenPaths(
    ["/projects/alpha/workflow.rattish", "/projects/alpha/src/app.js"],
    ["/projects/beta/workflow.rattish", ""],
  ), [
    "/projects/alpha/workflow.rattish",
    "/projects/alpha/src/app.js",
    "/projects/beta/workflow.rattish",
  ]);
  assert.deepEqual(appModule.mergeCodeOpenPaths(
    ["/projects/alpha/workflow.rattish", "/projects/beta/workflow.rattish"],
    ["/projects/alpha/workflow.rattish"],
  ), ["/projects/alpha/workflow.rattish", "/projects/beta/workflow.rattish"]);
  assert.equal(appModule.pendingCodePathForWorkflow(null, "beta"), "");
  assert.equal(appModule.pendingCodePathForWorkflow({
    path: "/projects/beta/workflow.rattish",
    workflowId: "beta",
  }, "alpha"), "");
  assert.equal(appModule.pendingCodePathForWorkflow({
    path: "/projects/beta/workflow.rattish",
    workflowId: "beta",
  }, "beta"), "/projects/beta/workflow.rattish");
});

test("project shortcuts and recent project ordering use native editor conventions", () => {
  assert.equal(appModule.isOpenProjectShortcut({
    altKey: false,
    ctrlKey: true,
    key: "o",
    metaKey: false,
    repeat: false,
    shiftKey: false,
  }), true);
  assert.equal(appModule.isOpenProjectShortcut({
    altKey: false,
    ctrlKey: true,
    key: "o",
    metaKey: false,
    repeat: true,
    shiftKey: false,
  }), false);
  assert.deepEqual(
    appModule.rememberRecentProject(["/repo/one", "/repo/two"], "/repo/two"),
    ["/repo/two", "/repo/one"],
  );
  assert.deepEqual(
    appModule.mergeRecentProjects(["/repo/one"], ["/repo/two", "/repo/one"]),
    ["/repo/one", "/repo/two"],
  );
  assert.deepEqual(
    appModule.rememberRecentFile(["/repo/one.js", "/repo/two.js"], "/repo/two.js"),
    ["/repo/two.js", "/repo/one.js"],
  );
  assert.deepEqual(
    appModule.rememberRecentFile(["/repo/one.js"], "raticode-browser:tab"),
    ["/repo/one.js"],
  );
  assert.deepEqual(
    appModule.removeCodePath(["/repo/one.js", "/repo/two.js"], "/repo/one.js"),
    ["/repo/two.js"],
  );
  assert.equal(appModule.mainWorktreeRoot({
    root: "/repo/feature",
    worktrees: [{ path: "/repo/main" }, { path: "/repo/feature" }],
  }, "/repo/fallback"), "/repo/main");
  assert.equal(codeFileExplorerModule.mainWorktreePath(
    [{ path: "/repo/main" }, { path: "/repo/feature" }],
    "/repo/fallback",
  ), "/repo/main");
  const scopedThread = appModule.scopeChatThreadToProject(
    { id: "thread-1", title: "Project work" },
    "/repo/two",
    [
      { id: "one", projectRoot: "/repo/one" },
      { id: "two", projectRoot: "/repo/two" },
    ],
    "one",
  );
  assert.equal(scopedThread.projectRoot, "/repo/two");
  assert.equal(scopedThread.selectedWorkflowId, null);
  assert.deepEqual(
    appModule.chatWorkflowContextForThread(scopedThread, [
      { id: "one", projectRoot: "/repo/one" },
      { id: "two", projectRoot: "/repo/two" },
    ]).workflows.map((workflow) => workflow.id),
    ["two"],
  );
});

test("projects without workflows still expose a code workspace", () => {
  const workspace = appModule.projectWorkspace("/workspace/empty-project");
  assert.deepEqual(workspace, {
    agents: {},
    description: "Project without a registered workflow",
    edges: [],
    id: "project:/workspace/empty-project",
    name: "empty-project",
    nodes: [],
    projectName: "empty-project",
    projectRoot: "/workspace/empty-project",
    sourceFormat: "project",
    sourcePath: "",
    status: "Project",
    tags: [],
  });
  assert.equal(appModule.codeWorkspaceAvailable(workspace), true);
  assert.equal(appModule.codeWorkspaceAvailable({ sourceFormat: "rattish" }), false);

  const previousWorkflow = {
    id: "previous",
    projectRoot: "/workspace/previous-project",
    sourceFormat: "rattish",
  };
  assert.deepEqual(
    appModule.activeWorkspaceForProject(
      [previousWorkflow],
      previousWorkflow.id,
      "/workspace/empty-project",
    ),
    workspace,
  );
  assert.equal(
    appModule.activeWorkspaceForView([], undefined, "", "code").sourceFormat,
    "project",
  );
});

test("IDE mode exposes project, file, and browser actions without a project", async () => {
  const dom = await mountReact(
    React.createElement(appModule.default),
    createFetchMock([jsonResponse("/api/workflows", workflowsPayload([]))]),
  );
  await dom.flush();
  await dom.click(dom.byLabel("File explorer"));
  assert.equal(
    allElements(dom.container).some((node) =>
      String(node.getAttribute?.("class") ?? "").includes("studio-topbar")),
    false,
  );
  await dom.click(dom.byText("File"));
  assert.ok(dom.byText("New File"));
  assert.ok(dom.byText("Open Project..."));
  assert.ok(dom.byText("Close Editor"));
  await dom.click(dom.byText("File"));
  await dom.click(dom.byText("Selection"));
  assert.ok(dom.byText("Expand Selection"));
  assert.ok(dom.byText("Add Cursor Below"));
  await dom.click(dom.byText("Selection"));
  await dom.click(dom.byText("Terminal"));
  assert.ok(dom.byText("Toggle Terminal"));
  assert.ok(dom.byText("Getting Started"));
  assert.ok(dom.byText("Open Project"));
  assert.ok(dom.byText("Open File"));
  assert.ok(dom.byText("Open Browser"));
  assert.equal(
    allElements(dom.container).some((node) => node.getAttribute?.("aria-label") === "Editor tabs"),
    false,
  );
  assert.doesNotMatch(dom.text(), /No file open|Start in the IDE/);

  await dom.dispatchWindow("keydown", {
    code: "KeyJ",
    ctrlKey: true,
    key: "j",
  });
  await dom.flush();
  assert.ok(dom.byLabel("Integrated browser"));
  await dom.unmount();
});

test("application menus use configured shortcuts and expose recent projects", async () => {
  const selectedProjects = [];
  const settings = settingsModule.normalizeAppSettings({
    keybindings: {
      "panel.toggle": "Ctrl+Shift+Backquote",
      "project.open": "Alt+KeyP",
    },
  });
  const dom = await mountReact(
    React.createElement(appModule.ApplicationMenus, {
      recentProjectRoots: ["/workspace/gofer-flow", "/workspace/second-brain"],
      settings,
      view: "code",
      onAction() {},
      onSelectProject(root) { selectedProjects.push(root); },
    }),
    createFetchMock([]),
  );

  await dom.click(dom.byText("File"));
  assert.match(dom.ancestor(dom.byText("Open Project..."), "BUTTON").textContent, /Alt\+P/);
  await dom.click(dom.ancestor(dom.byText("Recent Projects"), "BUTTON"));
  assert.ok(dom.byText("gofer-flow"));
  await dom.click(dom.ancestor(dom.byText("second-brain"), "BUTTON"));
  assert.deepEqual(selectedProjects, ["/workspace/second-brain"]);

  await dom.click(dom.byText("Terminal"));
  assert.match(dom.ancestor(dom.byText("Toggle Terminal"), "BUTTON").textContent, /Ctrl\+Shift\+`/);
  await dom.unmount();
});

test("empty Graph view offers creation, .raticode import, and project opening", async () => {
  const selectedProjects = [];
  const dom = await mountReact(
    React.createElement(appModule.default),
    createFetchMock([jsonResponse("/api/workflows", workflowsPayload([]))]),
    {
      desktop: {
        workspace: {
          selectPath: async () => {
            selectedProjects.push(true);
            return null;
          },
        },
      },
    },
  );

  await dom.flush();
  assert.match(dom.text(), /Build your first local workflow/);
  assert.match(dom.text(), /Your workflow stays in the project and runs on your machine/);
  assert.match(dom.text(), /Installed Codex or Claude Code/);
  assert.ok(dom.byText("A full IDE, built in"));
  assert.ok(dom.byText("Integrated browser"));
  assert.ok(dom.byText("Open Rem"));
  assert.ok(dom.byText("Import workflow"));
  assert.ok(dom.byText("Open project"));
  assert.ok(dom.byLabel("Open settings"));
  assert.equal(
    reactProps(dom.byTitle("Workflow history is available after you create a workflow")).disabled,
    true,
  );
  assert.ok(dom.ancestor(dom.byLabel("Open settings"), "HEADER"));
  assert.equal(
    allElements(dom.container).some(
      (element) => element.getAttribute?.("data-graph-toolbar-target") === "true",
    ),
    false,
  );
  assert.equal(
    allElements(dom.container).some(
      (element) => element.tagName === "INPUT" && String(element.getAttribute("accept") ?? "").split(",").includes(".raticode"),
    ),
    true,
  );

  await dom.click(dom.ancestor(dom.byText("New Workflow"), "BUTTON"));
  assert.ok(dom.byText("New workflow"));
  assert.ok(dom.byText("Create new"));
  assert.ok(dom.byText("Import"));
  await dom.click(dom.byTitle("Close"));

  await dom.dispatchWindow("keydown", {
    code: "KeyL",
    ctrlKey: true,
    key: "l",
  });
  await dom.flush();
  const assistantPane = allElements(dom.container).find(
    (element) => element.getAttribute?.("data-chat-pane") === "true",
  );
  assert.equal(
    dom.ancestor(
      assistantPane,
      (element) => element.getAttribute?.("aria-hidden") === "true",
    ).getAttribute("class"),
    "hidden",
  );
  await dom.click(dom.ancestor(dom.byText("Open Rem"), "BUTTON"));
  await dom.flush();
  assert.equal(
    dom.ancestor(
      assistantPane,
      (element) => element.getAttribute?.("aria-hidden") === "false",
    ).getAttribute("class"),
    "contents",
  );
  assert.equal(document.activeElement.getAttribute("placeholder"), "Message this workflow");

  await dom.click(dom.ancestor(dom.byText("Open project"), "BUTTON"));
  await dom.flush();
  assert.equal(selectedProjects.length, 1);

  await dom.unmount();
});

test("empty Graph view imports a .raticode bundle into the open project", async () => {
  const projectRoot = "/workspace/empty-project";
  const importedWorkflow = {
    ...workflowFixture({ id: "daily-review", name: "Daily Review" }),
    projectName: "empty-project",
    projectRoot,
    sourceFormat: "rattish",
    sourcePath: `${projectRoot}/.raticode/daily-review/workflow.rattish`,
  };
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([])),
    jsonResponse("/api/rattish/workflows/import/preview", {
      bundle: {
        files: ["workflow.rattish"],
        workflowName: "Daily Review",
      },
    }, { method: "POST" }),
    jsonResponse("/api/rattish/workflows/import", {
      workflow: importedWorkflow,
    }, { method: "POST" }),
  ]);
  const dom = await mountReact(
    React.createElement(appModule.default),
    fetchMock,
    {
      desktop: {
        grantDroppedPath: async () => "/imports/daily-review.raticode",
        workspace: {
          pathGrantForApi: (path) => path === projectRoot ? "project-grant" : "bundle-grant",
        },
      },
      storage: {
        [appModule.STUDIO_SESSION_STORAGE_KEY]: JSON.stringify({
          projectRoot,
          view: "graph",
          workflowId: "",
        }),
      },
    },
  );

  await dom.flush();
  assert.match(dom.text(), /added to empty-project under \.raticode/);
  const importZone = dom.ancestor(dom.byText("Bring in an existing workflow"), "SECTION");
  const dataTransfer = {
    dropEffect: "none",
    files: [{ name: "daily-review.raticode" }],
  };
  await dom.pointer(importZone, "onDragOver", { dataTransfer });
  assert.equal(dataTransfer.dropEffect, "copy");
  await dom.pointer(importZone, "onDrop", { dataTransfer });
  await dom.flush();

  const importCall = fetchMock.calls.find(
    (call) => call.url === "/api/rattish/workflows/import" && call.options.method === "POST",
  );
  assert.ok(importCall);
  assert.deepEqual(JSON.parse(importCall.options.body), {
    bundlePath: "/imports/daily-review.raticode",
    grantId: "bundle-grant",
    projectGrantId: "project-grant",
    projectRoot,
  });
  assert.match(dom.text(), /Daily Review/);

  await dom.unmount();
});

test("empty IDE shows recent files in a two-column card grid", async () => {
  const opened = [];
  const dom = await mountReact(
    React.createElement(codeWorkspaceModule.default, {
      active: true,
      activePath: "",
      openPaths: [],
      recentPaths: ["/repo/src/app.jsx", "/repo/README.md"],
      workflow: { projectRoot: "/repo" },
      onOpenPath(path) { opened.push(path); },
    }),
    createFetchMock([]),
  );

  const recentHeading = dom.byText("Recent files");
  const recentGrid = recentHeading.parentNode.childNodes.find(
    (node) => node.getAttribute?.("class")?.includes("grid-cols-2"),
  );
  assert.ok(recentGrid);
  assert.ok(dom.byText("app.jsx"));
  assert.ok(dom.byText("README.md"));
  await dom.click(dom.ancestor(dom.byText("app.jsx"), "BUTTON"));
  assert.deepEqual(opened, ["/repo/src/app.jsx"]);

  await dom.unmount();
});

test("recent files remove missing paths on load and report deletion during open", async () => {
  const stale = "/other-project/workflow.rad";
  const kept = "/other-project/workflow.rattish";
  let deleted = false;
  const opened = [];
  const dom = await mountReact(React.createElement(appModule.default), createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([])),
  ]), {
    storage: { [appModule.RECENT_FILES_STORAGE_KEY]: JSON.stringify([stale, kept]) },
    desktop: { workspace: {
      missingRecentFiles: async paths => paths.filter(path => path === stale || deleted),
      grantUserPath: async path => {
        opened.push(path);
        throw new Error("File was deleted");
      },
    } },
  });
  await dom.flush();
  await dom.click(dom.byLabel("File explorer"));
  await dom.flush();
  assert.doesNotMatch(dom.text(), /workflow\.rad/);
  assert.ok(dom.byText("workflow.rattish"));
  assert.deepEqual(JSON.parse(window.localStorage.getItem(appModule.RECENT_FILES_STORAGE_KEY)), [kept]);
  deleted = true;
  await dom.click(dom.ancestor(dom.byText("workflow.rattish"), "BUTTON"));
  await dom.flush();
  assert.deepEqual(opened, [kept]);
  assert.match(dom.text(), /File no longer exists. Removed from recent files/);
  assert.doesNotMatch(dom.text(), /Recent files/);
  assert.deepEqual(JSON.parse(window.localStorage.getItem(appModule.RECENT_FILES_STORAGE_KEY)), []);
  await dom.unmount();
});

test("recent files refresh after returning to the app", async () => {
  const kept = "/other-project/notes.md";
  let deleted = false;
  const dom = await mountReact(React.createElement(appModule.default), createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([])),
  ]), {
    storage: { [appModule.RECENT_FILES_STORAGE_KEY]: JSON.stringify([kept]) },
    desktop: { workspace: { missingRecentFiles: async () => deleted ? [kept] : [] } },
  });
  await dom.flush();
  await dom.click(dom.byLabel("File explorer"));
  assert.ok(dom.byText("notes.md"));
  deleted = true;
  await dom.dispatchWindow("focus");
  await dom.flush();
  assert.doesNotMatch(dom.text(), /notes\.md/);
  assert.deepEqual(JSON.parse(window.localStorage.getItem(appModule.RECENT_FILES_STORAGE_KEY)), []);
  await dom.unmount();
});

test("recent files retain history and show errors when inspection is unavailable", async () => {
  const kept = "/other-project/notes.md";
  const dom = await mountReact(React.createElement(appModule.default), createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([])),
  ]), {
    storage: { [appModule.RECENT_FILES_STORAGE_KEY]: JSON.stringify([kept]) },
    desktop: { workspace: {
      missingRecentFiles: async () => { throw new Error("IPC unavailable"); },
      grantUserPath: async () => { throw new Error("Permission denied opening notes.md"); },
    } },
  });
  await dom.flush();
  await dom.click(dom.byLabel("File explorer"));
  await dom.flush();
  await dom.click(dom.ancestor(dom.byText("notes.md"), "BUTTON"));
  await dom.flush();
  assert.match(dom.text(), /Permission denied opening notes.md/);
  assert.ok(dom.byText("notes.md"));
  assert.deepEqual(JSON.parse(window.localStorage.getItem(appModule.RECENT_FILES_STORAGE_KEY)), [kept]);
  await dom.unmount();
});

test("Open File opens a selected file when the project is already loaded and no tab is open", async () => {
  const workflow = workflowFixture();
  const selectedPath = "/workspace/assets/preview.png";
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([workflow])),
    jsonResponse("/api/projects/open", { workflows: [workflow] }, { method: "POST" }),
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock, {
    desktop: {
      textFiles: { read: async () => ({ content: "console.log('open');" }) },
      workspace: {
        gitFileBaseline: async () => ({ changed: false, tracked: true }),
        gitWorktrees: async () => ({ root: "/workspace", worktrees: [{ path: "/workspace" }] }),
        pathGrantForApi: () => "",
        resolveProjectFile: async () => ({ directory: "/workspace", selectedPath }),
        selectPath: async () => selectedPath,
        trustProjectRoot: async () => {},
      },
    },
  });

  await dom.flush();
  await dom.click(dom.byLabel(`Close ${workflow.name}`));
  await dom.click(dom.byLabel("File explorer"));
  assert.ok(dom.byText("Open File"));
  await dom.click(dom.ancestor(dom.byText("Open File"), "BUTTON"));
  await dom.flush();

  assert.ok(dom.byText("preview.png"));
  assert.equal(
    fetchMock.calls.some((call) => call.url === "/api/projects/open" && call.options.method === "POST"),
    true,
  );

  await dom.unmount();
});

test("rapid project opens skip obsolete worktree reads and keep the latest selection", async () => {
  const roots = ["/project-a", "/project-b", "/project-c"];
  const pending = new Map(roots.map(root => [root, createDeferred()]));
  const gitCalls = [];
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([])),
    (url, options) => url === "/api/projects/open" ? {
      ok: true,
      status: 200,
      // Deliberately ignore abort to also exercise the generation guard.
      json: () => pending.get(JSON.parse(options.body).projectRoot).promise,
    } : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock, {
    storage: { "gofer.recentProjects": JSON.stringify(roots) },
    desktop: { workspace: {
      trustProjectRoot: async () => {},
      gitWorktrees: async root => {
        gitCalls.push(root);
        return { worktrees: [{ path: root }] };
      },
    } },
  });
  try {
    await dom.flush();
    for (const root of roots) {
      await dom.click(dom.byText("File"));
      await dom.click(dom.ancestor(dom.byText("Recent Projects"), "BUTTON"));
      await dom.click(dom.ancestor(dom.byText(root.slice(1)), "BUTTON"));
    }
    const opens = fetchMock.calls.filter(call => call.url === "/api/projects/open");
    assert.equal(opens.length, 3);
    assert.equal(opens[0].options.signal.aborted, true);
    assert.equal(opens[1].options.signal.aborted, true);
    pending.get(roots[2]).resolve({ workflows: [] });
    await dom.flush();
    pending.get(roots[0]).resolve({ workflows: [] });
    pending.get(roots[1]).resolve({ workflows: [] });
    await dom.flush();
    assert.ok(gitCalls.length > 0, "The selected project loads its worktrees");
    assert.deepEqual([...new Set(gitCalls)], [roots[2]], "Obsolete projects never load worktrees");
    assert.equal(appModule.loadStudioSession().projectRoot, roots[2]);
  } finally {
    for (const deferred of pending.values()) deferred.resolve({ workflows: [] });
    await dom.unmount();
  }
});

test("project workflow discovery registers Rattish files before a workflow refresh", async () => {
  const trusted = [];
  const workflow = {
    ...workflowFixture({ id: "daily-todos", name: "Daily Todos" }),
    projectRoot: "/workspace/gofer-flow",
    sourceFormat: "rattish",
    sourcePath: "/workspace/gofer-flow/.raticode/daily-todos/workflow.rattish",
  };
  const fetchMock = createFetchMock([
    jsonResponse("/api/projects/open", { workflows: [workflow] }, { method: "POST" }),
  ]);
  globalThis.fetch = fetchMock;
  window.goferDesktop = {
    workspace: {
      pathGrantForApi: () => "grant-project",
      trustProjectRoot: async (projectRoot) => trusted.push(projectRoot),
    },
  };

  const discovered = await appModule.discoverProjectWorkflows(" /workspace/gofer-flow ");

  assert.deepEqual(discovered, [workflow]);
  assert.deepEqual(trusted, ["/workspace/gofer-flow"]);
  const request = fetchMock.calls[0];
  assert.equal(request.url, "/api/projects/open");
  assert.equal(request.options.method, "POST");
  assert.deepEqual(JSON.parse(request.options.body), {
    projectGrantId: "grant-project",
    projectRoot: "/workspace/gofer-flow",
  });
});

test("workflow bundle paths use the selected folder and the Rattish export route", () => {
  const rattishWorkflow = {
    id: "daily-review",
    sourceFormat: "rattish",
  };

  assert.equal(
    appModule.workflowBundlePath("/home/user/Exports/", rattishWorkflow),
    "/home/user/Exports/daily-review.raticode",
  );
  assert.equal(
    appModule.workflowBundlePath("C:\\Users\\dev\\Exports\\", rattishWorkflow),
    "C:\\Users\\dev\\Exports\\daily-review.raticode",
  );
  assert.equal(
    appModule.workflowExportEndpoint(rattishWorkflow),
    "/rattish/workflows/daily-review/export",
  );
});

test("graph workflow selection is independent from the code explorer project", () => {
  const workflows = [
    { id: "alpha", projectRoot: "/workspace/alpha" },
    { id: "beta", projectRoot: "/workspace/beta" },
  ];

  assert.equal(
    appModule.activeWorkspaceForView(workflows, "beta", "/workspace/alpha", "graph").id,
    "beta",
  );
  assert.equal(
    appModule.activeWorkspaceForView(workflows, "beta", "/workspace/alpha", "code").id,
    "alpha",
  );
});

test("Rattish byte spans convert to Monaco text ranges across Unicode", () => {
  const source = "a😀é\nz";
  assert.equal(rattishRangesModule.utf8ByteOffsetToTextOffset(source, 0), 0);
  assert.equal(rattishRangesModule.utf8ByteOffsetToTextOffset(source, 1), 1);
  assert.equal(rattishRangesModule.utf8ByteOffsetToTextOffset(source, 5), 3);
  assert.equal(rattishRangesModule.utf8ByteOffsetToTextOffset(source, 7), 4);

  const model = {
    getLineMaxColumn: () => 5,
    getPositionAt(offset) {
      const prefix = source.slice(0, offset);
      const lines = prefix.split("\n");
      return { lineNumber: lines.length, column: lines.at(-1).length + 1 };
    },
  };
  const marker = rattishRangesModule.diagnosticToMarker(
    { MarkerSeverity: { Warning: 4, Info: 2, Error: 8 } },
    model,
    source,
    {
      code: "RATTISH_TEST",
      message: "Unicode warning",
      severity: "warning",
      span: { start: { offset: 1 }, end: { offset: 7 } },
    },
  );
  assert.deepEqual(marker, {
    code: "RATTISH_TEST",
    endColumn: 5,
    endLineNumber: 1,
    message: "Unicode warning",
    severity: 4,
    source: "Rattish",
    startColumn: 2,
    startLineNumber: 1,
  });
});

test("Rattish editor projection populates the graph with source-backed route details", () => {
  const workflow = {
    ...workflowFixture({ id: "review-pr", name: "Review PR" }),
    sourceFormat: "rattish",
    nodes: [],
    edges: [],
  };
  const projected = appModule.rattishGraphWorkflow(workflow, {
    workflowId: "review-pr",
    workflow: { name: "Review the PR" },
    metadata: { canvas: { nodes: { prepare: { x: 32, y: 48 } } } },
    graph: {
      nodes: [
        {
          id: "prepare",
          label: "Prepare",
          type: "bash-command",
          configuration: { command: "echo ready" },
          execution: { allow_fail: false, max_concurrency: 1 },
          diagnostics: [],
        },
        {
          id: "review",
          label: "Review",
          type: "agent",
          configuration: { provider: "codex" },
          execution: { allow_fail: false, max_concurrency: 1 },
          diagnostics: [{ code: "RATTISH_TEST", message: "Review is incomplete", severity: "error" }],
        },
      ],
      edges: [
        { id: "prepare:route:0", from: "prepare", to: "review", mode: "when", status: "valid" },
      ],
    },
  });

  assert.equal(projected.name, "Review the PR");
  assert.equal(projected.nodes.length, 2);
  assert.equal(projected.edges.length, 1);
  assert.equal(projected.nodes[0].type, "bash_command");
  assert.deepEqual({ x: projected.nodes[0].x, y: projected.nodes[0].y }, { x: 32, y: 48 });
  assert.ok(projected.nodes[1].x > projected.nodes[0].x);
  assert.equal(projected.edges[0].displayLabel, "when");
  assert.equal(projected.validationDiagnostics[0].targetId, "review");
});

test("Rattish graph restores selection and emits targeted inspector mutations", async () => {
  const mutations = [];
  const document = {
    workflowId: "rattish-inspector",
    source: "Rattish: 1\nWorkflow:\n  name: Inspector\nNode prepare:\n  type: bash-command\n  command: echo ready\n",
    workflow: { name: "Inspector", fields: { name: { value: "Inspector" } } },
    nodeContracts: [
      {
        nodeType: "bash-command",
        configurationSchema: {
          type: "object",
          properties: { command: { type: "string" }, working_dir: { type: ["string", "null"] } },
        },
        defaults: { working_dir: null },
      },
    ],
    graph: {
      nodes: [
        {
          id: "prepare",
          label: "prepare",
          type: "bash-command",
          configuration: { command: "echo ready", working_dir: null },
          execution: { allow_fail: false, max_concurrency: 1, retry_count: 0, retry_delay_ms: 1000 },
          authoredFields: { command: { value: "echo ready" }, type: { value: "bash-command" } },
          bindings: [],
          needs: [],
          diagnostics: [],
        },
      ],
      edges: [],
    },
  };
  const workflow = appModule.rattishGraphWorkflow(
    { ...workflowFixture({ id: "rattish-inspector", name: "Inspector" }), sourceFormat: "rattish", nodes: [], edges: [] },
    document,
  );
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      workflow,
      rattishDocument: document,
      onRattishMutation(next) {
        mutations.push(next);
        if (next[0]?.kind === "rename_node") {
          const renamedId = next[0].name.toLowerCase();
          return Promise.resolve({
            ...document,
            graph: {
              ...document.graph,
              nodes: document.graph.nodes.map((candidate) =>
                candidate.id === next[0].node
                  ? { ...candidate, id: renamedId, label: renamedId }
                  : candidate,
              ),
            },
            source: document.source.replace("Node prepare:", `Node ${next[0].name}:`),
          });
        }
        return null;
      },
      onWorkflowChange() {},
    }),
    createFetchMock([]),
  );

  await dom.pointer(dom.ancestor(dom.byText("prepare"), "ARTICLE"), "onPointerDown");
  await dom.flush();
  assert.equal(dom.byText("Node inspector").tagName, "H2");
  const id = dom.controlAfterLabel("ID");
  await dom.focus(id);
  await dom.change(id, "");
  assert.equal(id.value, "");
  assert.match(dom.text(), /Enter a node ID/);
  assert.deepEqual(mutations, []);
  await dom.change(id, "prepare-next");
  assert.equal(id.value, "prepare-next");
  assert.equal(globalThis.document.activeElement, id);
  assert.deepEqual(mutations, []);
  await dom.keyDown(id, "Enter");
  await dom.flush();
  assert.deepEqual(mutations, [
    [{ kind: "rename_node", node: "prepare", name: "prepare-next" }],
  ]);
  const renamedId = dom.controlAfterLabel("ID");
  assert.equal(renamedId.value, "prepare-next");
  assert.equal(globalThis.document.activeElement, renamedId);
  assert.equal(dom.byText("Node inspector").tagName, "H2");
  await dom.blur(renamedId);
  assert.equal(mutations.length, 1);
  await dom.click(dom.byText("Action"));
  const command = dom.controlAfterLabel("Command");
  await dom.focus(command);
  await dom.change(command, "echo changed");
  assert.equal(globalThis.document.activeElement, command);
  assert.equal(mutations.length, 1);
  await dom.blur(dom.controlAfterLabel("Command"));

  assert.deepEqual(mutations.at(-1), [
    {
      kind: "set_field",
      target: { node: "prepare-next" },
      field: "command",
      value: "echo changed",
    },
  ]);
  await dom.unmount();
});

test("Rattish edits become dirty immediately without adding a code-mode top bar", async () => {
  const savedSource = "Rattish: 1\n\nWorkflow:\n  name: Demo\n";
  const editedSource = `${savedSource}\nNode prepare:\n  type: bash-command\n  command: echo ready\n`;
  const edited = rattishEditorModule.editorDocumentAfterChange(
    { diagnostics: [], dirty: false, source: savedSource },
    editedSource,
    savedSource,
  );
  assert.equal(edited.dirty, true);
  assert.equal(
    rattishEditorModule.editorDocumentAfterChange(edited, savedSource, savedSource).dirty,
    false,
  );

  const workflow = {
    ...workflowFixture({ id: "demo", name: "Demo" }),
    projectName: "gofer-flow",
    sourceFormat: "rattish",
    sourcePath: "/workspace/gofer-flow/.raticode/demo/workflow.rattish",
  };
  const dom = await mountReact(
    React.createElement(appModule.TopBar, {
      activeCodePath: "/workspace/gofer-flow/.raticode/demo/workflow.rattish",
      editorState: { ...edited, saving: false },
      theme: "light",
      updateState: {},
      view: "code",
      workflow,
      onApplyUpdate() {},
      onCheckForUpdates() {},
      onOpenHistory() {},
      onRetrySave() {},
      onToggleTheme() {},
    }),
    createFetchMock([]),
  );
  assert.equal(dom.byText("workflow.rattish").tagName, "H2");
  assert.match(dom.byText("workflow.rattish").getAttribute("class"), /text-\[15px\]/);
  assert.equal(dom.byText("/workspace/gofer-flow/.raticode/demo").tagName, "SPAN");
  assert.doesNotMatch(dom.text(), /\d+ lines/);
  const topBar = dom.ancestor(dom.byText("workflow.rattish"), "HEADER");
  assert.match(topBar.getAttribute("class"), /studio-topbar/);
  assert.equal(
    allElements(topBar).some(
      (element) => element.getAttribute?.("data-graph-toolbar-target") === "true",
    ),
    false,
  );
  assert.equal(allElements(topBar).some(
    (element) => element.getAttribute?.("aria-label") === "Save active file",
  ), false);
  assert.doesNotMatch(dom.text(), /Saved/);
  await dom.unmount();
});

test("studio header separates quiet paths from focused workflow and file names", async () => {
  assert.equal(appModule.topBarProjectName({
    projectName: "Gofer Flow Workflows",
    projectRoot: "/repos/gofer-flow",
  }), "Gofer Flow Workflows");
  assert.equal(appModule.topBarProjectName({ projectRoot: "/repos/gofer-flow" }), "gofer-flow");
  assert.equal(appModule.topBarProjectName({}), "Unfiled project");
  assert.deepEqual(
    appModule.topBarLabelParts(
      { id: "review", name: "Review PR", projectRoot: "/repos/gofer-flow" },
      "graph",
    ),
    {
      fullPath: "gofer-flow/Review PR",
      name: "Review PR",
      path: "gofer-flow",
      separator: "/",
    },
  );
  assert.deepEqual(
    appModule.topBarLabelParts({}, "code", "C:\\repos\\gofer-flow\\src\\app.jsx"),
    {
      fullPath: "C:\\repos\\gofer-flow\\src\\app.jsx",
      name: "app.jsx",
      path: "C:\\repos\\gofer-flow\\src",
      separator: "\\",
    },
  );
  const dom = await mountReact(
    React.createElement(appModule.TopBar, {
      theme: "dark",
      updateState: {},
      view: "graph",
      workflow: { id: "review", name: "Review PR", projectRoot: "/repos/gofer-flow" },
      onApplyUpdate() {},
      onCheckForUpdates() {},
      onOpenHistory() {},
      onRetrySave() {},
      onToggleTheme() {},
    }),
    createFetchMock([]),
  );
  const workflowTitle = dom.byText("Review PR");
  assert.equal(workflowTitle.getAttribute("title"), "gofer-flow/Review PR");
  assert.match(workflowTitle.getAttribute("class"), /truncate.*font-semibold/);
  assert.doesNotMatch(workflowTitle.getAttribute("class"), /shrink-0/);
  const header = dom.ancestor(workflowTitle, "HEADER");
  const toolbar = allElements(header).find(element => element.getAttribute?.("data-graph-toolbar-target") === "true");
  assert.ok(toolbar, "The graph has a dedicated toolbar row");
  assert.notEqual(toolbar.parentNode, workflowTitle.parentNode, "Controls cannot overlap the workflow title row");
  await dom.unmount();
});

test("create workflow dialog submits the selected project folder", async () => {
  const submissions = [];
  const dom = await mountReact(
    React.createElement(appModule.CreateWorkflowDialog, {
      defaultProjectRoot: "/repos/raticode",
      error: "",
      open: true,
      saving: false,
      onClose() {},
      onCreate: (name, options) => submissions.push({ name, options }),
      onImport() {},
    }),
    createFetchMock([]),
  );

  await dom.change(dom.controlAfterLabel("Name"), "Review PR");
  await dom.flush();
  await React.act(async () => {
    const form = allElements(dom.container).find((element) => element.tagName === "FORM");
    reactProps(form).onSubmit(testEvent(form));
  });

  assert.deepEqual(submissions, [{
    name: "Review PR",
    options: {
      projectRoot: "/repos/raticode",
      projectGrantId: "",
    },
  }]);
  await dom.unmount();
});

test("new workflow dialog imports a .raticode bundle into the selected project", async () => {
  const submissions = [];
  const dom = await mountReact(
    React.createElement(appModule.CreateWorkflowDialog, {
      defaultProjectRoot: "/repos/raticode",
      error: "",
      open: true,
      saving: false,
      onClose() {},
      onCreate() {},
      onImport: (file, projectRoot) => submissions.push({ file, projectRoot }),
    }),
    createFetchMock([]),
  );

  await dom.click(dom.ancestor(dom.byText("Import"), "BUTTON"));
  const importZone = dom.ancestor(dom.byText("Choose a .raticode bundle"), "BUTTON");
  const file = { name: "daily-review.raticode" };
  await dom.pointer(importZone, "onDrop", {
    dataTransfer: { dropEffect: "none", files: [file] },
  });
  await React.act(async () => {
    const form = allElements(dom.container).find((element) => element.tagName === "FORM");
    reactProps(form).onSubmit(testEvent(form));
  });

  assert.deepEqual(submissions, [{ file, projectRoot: "/repos/raticode" }]);
  await dom.unmount();
});

test("App keeps the new workflow name field enabled after deleting a workflow", async () => {
  const dom = await mountReact(
    React.createElement(appModule.default),
    createFetchMock([
      jsonResponse("/api/workflows", workflowsPayload([
        workflowFixture({ id: "demo", name: "Demo" }),
        workflowFixture({ id: "other", name: "Other" }),
      ])),
      jsonResponse("/api/provider/capabilities", {
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
            efforts: [{ id: "low", displayName: "Low" }, { id: "medium", displayName: "Medium" }],
          }],
        }],
      }),
      jsonResponse("/api/workflows/demo/logs/latest", {
        log: { logText: "latest demo log", logPath: "/tmp/demo.log" },
      }),
      jsonResponse("/api/workflows/demo/logs?limit=100", { runs: [] }),
      jsonResponse("/api/workflows/demo", { deleted: true }, { method: "DELETE" }),
    ]),
  );

  await dom.flush();
  await dom.click(dom.allByTitle("Workflow actions")[0]);
  await dom.click(dom.byText("Delete workflow"));
  await dom.flush();

  await dom.click(dom.byTitle("New Workflow"));
  const nameInput = dom.controlAfterLabel("Name");

  assert.equal(nameInput.disabled, false);

  await dom.unmount();
});

test("run, plan, and log helpers build backend requests without a real server", () => {
  globalThis.window.goferApiBaseUrl = "http://127.0.0.1:8765";

  const planRequest = appModule.workflowPlanRequest("demo workflow", {
    schedule: { cron_expression: "0 9 * * *" },
  });
  assert.equal(planRequest.url, "http://127.0.0.1:8765/api/workflows/demo%20workflow/plan");
  assert.equal(planRequest.options.method, "POST");
  assert.deepEqual(JSON.parse(planRequest.options.body), {
    triggerContext: { schedule: { cron_expression: "0 9 * * *" } },
  });

  const runRequest = appModule.workflowRunRequest("demo workflow", {
    dryRun: false,
    triggerContext: { watch: { path: "/tmp/inbox" } },
  });
  assert.equal(runRequest.url, "http://127.0.0.1:8765/api/workflows/demo%20workflow/run");
  assert.deepEqual(JSON.parse(runRequest.options.body), {
    dryRun: false,
    triggerContext: { watch: { path: "/tmp/inbox" } },
  });

  const resumeRequest = appModule.workflowResumeRequest("demo workflow", "run/1", {
    fromNode: "step",
    skipCache: true,
  });
  assert.equal(
    resumeRequest.url,
    "http://127.0.0.1:8765/api/workflows/demo%20workflow/runs/run%2F1/resume",
  );
  assert.deepEqual(JSON.parse(resumeRequest.options.body), {
    force: false,
    fromNode: "step",
    onlyNode: null,
    skipCache: true,
    triggerContext: {},
  });

  const replayRequest = appModule.workflowReplayTriggerRequest(
    "demo workflow",
    "run/1",
    "github",
  );
  assert.equal(
    replayRequest.url,
    "http://127.0.0.1:8765/api/workflows/demo%20workflow/webhooks/github/replay",
  );
  assert.equal(replayRequest.options.method, "POST");
  assert.deepEqual(JSON.parse(replayRequest.options.body), { runId: "run/1" });

  assert.deepEqual(appModule.workflowLogUrls("demo workflow", "run/1"), {
    latest: "http://127.0.0.1:8765/api/workflows/demo%20workflow/logs/latest",
    runs: "http://127.0.0.1:8765/api/workflows/demo%20workflow/logs",
    selected:
      "http://127.0.0.1:8765/api/workflows/demo%20workflow/logs/run%2F1?tailBytes=65536&details=0",
  });
});

test("workflow failures prefer the runtime error and fall back to the failed node", () => {
  assert.equal(
    appModule.workflowRunFailureMessage({ error: { message: "grep found no match" } }),
    "grep found no match",
  );
  assert.equal(
    appModule.workflowRunFailureMessage({
      runNodes: { asdf: { status: "error", error: { message: "command not found" } } },
    }),
    "command not found",
  );
  assert.match(appModule.workflowRunFailureMessage({}), /Select the failed node/);
});

test("chat helpers parse stream events, group thoughts, and build request payloads", () => {
  const messages = [
    { id: "u1", role: "user", body: "Summarize this workflow" },
    { id: "t1", role: "assistant", kind: "thought", groupId: "g1", body: "Inspecting nodes" },
    { id: "t2", role: "assistant", kind: "thought", groupId: "g1", body: "Checking edges" },
    { id: "m1", role: "assistant", kind: "memory", body: "hidden" },
    { id: "a1", role: "assistant", body: "Done" },
  ];

  const items = appModule.buildChatItems(messages);
  assert.equal(items[0].type, "message");
  assert.equal(items[1].type, "thought-group");
  assert.equal(items[1].thoughts.length, 2);
  assert.equal(items[2].message.body, "Done");

  const duplicateOutputItems = appModule.buildChatItems([
    { id: "u2", role: "user", body: "Can you do this?" },
    {
      id: "t3",
      role: "assistant",
      kind: "thought",
      groupId: "g2",
      body: "I need filesystem access.",
      trace: { kind: "summary", title: "Thought", body: "I need filesystem access." },
    },
    { id: "a2", role: "assistant", kind: "final", body: "I need filesystem access." },
  ]);
  assert.deepEqual(duplicateOutputItems.map((item) => item.type), ["message", "message"]);

  const retainedTraceItems = appModule.buildChatItems([
    { id: "u3", role: "user", body: "Inspect this" },
    {
      id: "t4",
      role: "assistant",
      kind: "thought",
      groupId: "g3",
      body: "Inspecting files",
      trace: { kind: "summary", title: "Thought", body: "Inspecting files" },
    },
    {
      id: "t5",
      role: "assistant",
      kind: "thought",
      groupId: "g3",
      body: "Inspection complete",
      trace: { kind: "summary", title: "Thought", body: "Inspection complete" },
    },
    { id: "a3", role: "assistant", kind: "final", body: "Inspection complete" },
  ]);
  assert.equal(retainedTraceItems[1].type, "thought-group");
  assert.deepEqual(retainedTraceItems[1].thoughts.map((thought) => thought.body), [
    "Inspecting files",
  ]);

  const streamedMessages = [
    {
      id: "t6",
      role: "assistant",
      kind: "thought",
      groupId: "g4",
      body: "Final answer",
      trace: { kind: "summary", title: "Thought", body: "Final answer" },
    },
  ];
  assert.deepEqual(
    appModule.removeTrailingDuplicateOutputThought(streamedMessages, "Final answer", "g4"),
    [],
  );
  assert.equal(
    appModule.removeTrailingDuplicateOutputThought(streamedMessages, "Different answer", "g4")
      .length,
    1,
  );
  assert.deepEqual(
    appModule.removeTrailingDuplicateOutputThought([
      {
        id: "metadata-thought",
        role: "assistant",
        kind: "thought",
        groupId: "g4",
        body: "tokens used\n15,930",
        trace: { kind: "summary", body: "tokens used\n15,930" },
      },
      ...streamedMessages,
    ], "Final answer", "g4"),
    [],
  );

  const markdownDuplicate = {
    id: "t7",
    role: "assistant",
    kind: "thought",
    groupId: "g5",
    body: "The workflow processes tickets.\n1. `collect` reads files.\n2. `classify` labels them...",
    trace: {
      kind: "summary",
      title: "Thought",
      body: "The workflow processes tickets.\n1. `collect` reads files.\n2. `classify` labels them...",
    },
  };
  assert.deepEqual(
    appModule.removeTrailingDuplicateOutputThought(
      [markdownDuplicate],
      "The workflow processes tickets.\n\n1. `collect` reads files.\n2. `classify` labels them for routing and review.",
      "g5",
    ),
    [],
  );

  const trace = appModule.buildThoughtTrace([
    {
      id: "trace-summary",
      body: "Inspecting nodes",
      trace: { kind: "summary", title: "Thought", body: "Inspecting nodes" },
    },
    {
      id: "trace-tool-start",
      body: "Read",
      trace: {
        id: "tool-1",
        kind: "tool",
        title: "Read",
        detail: "workflow.toml",
        input: "workflow.toml",
        status: "running",
      },
    },
    {
      id: "trace-tool-result",
      body: "Tool result",
      trace: {
        id: "tool-1",
        kind: "tool",
        title: "Tool result",
        output: "[workflow]",
        status: "complete",
      },
    },
  ]);
  assert.equal(trace.length, 2);
  assert.equal(trace[1].title, "Read");
  assert.equal(trace[1].detail, "workflow.toml");
  assert.equal(trace[1].output, "[workflow]");
  assert.equal(trace[0].title, "Thought");
  assert.deepEqual(appModule.shellTraceDetails({
    kind: "tool",
    title: "Bash",
    input: '{"command":"npm test"}',
  }), {
    command: "npm test",
    shell: "bash",
  });
  assert.deepEqual(appModule.shellTraceDetails({
    kind: "tool",
    title: "PowerShell",
    category: "shell",
    shell: "PowerShell",
    command: "Get-ChildItem",
  }), {
    command: "Get-ChildItem",
    shell: "PowerShell",
  });
  assert.equal(appModule.shellTraceDetails({ kind: "tool", title: "Read" }), null);
  assert.equal(appModule.toolTraceDisclosureDetail({
    kind: "tool",
    title: "Search",
    input: '{"search_query":[{"q":"Amsterdam current weather"}]}',
  }), "Amsterdam current weather");
  assert.equal(appModule.toolTraceDisclosureDetail({
    kind: "tool",
    title: "Search",
    detail: "Weather in Amsterdam",
    input: '{"query":"ignored fallback"}',
  }), "Weather in Amsterdam");
  assert.equal(appModule.toolTraceDisclosureDetail({
    kind: "tool",
    title: "Read",
    input: "a very long payload that should stay inside the disclosure",
  }), "");
  const thinkingTrace = appModule.buildThoughtTrace([
    {
      id: "thinking-start",
      kind: "thought",
      body: "Thinking",
      trace: {
        id: "claude-thinking-msg-1-0",
        kind: "summary",
        title: "Thinking",
        status: "running",
      },
    },
    {
      id: "thinking-complete",
      kind: "thought",
      body: "Thought",
      trace: {
        id: "claude-thinking-msg-1-0",
        kind: "summary",
        title: "Thought",
        detail: "for 8s",
        status: "complete",
      },
    },
  ]);
  assert.equal(thinkingTrace.length, 1);
  assert.equal(thinkingTrace[0].title, "Thought");
  assert.equal(thinkingTrace[0].detail, "for 8s");
  assert.equal(thinkingTrace[0].body, "");
  assert.equal(thinkingTrace[0].status, "complete");
  assert.deepEqual(appModule.buildThoughtTrace([
    {
      id: "trace-metadata",
      body: "tokens used\n15,930",
      trace: { kind: "summary", title: "Thought", body: "tokens used\n15,930" },
    },
  ]), []);

  const markdownMarkup = renderToStaticMarkup(
    React.createElement(markdownContentModule.default, {
      value: "## Result\n\n[Docs](https://example.com/docs)\n\n- [x] Ready\n\n| File | State |\n| --- | --- |\n| workflow.rattish | valid |\n\n```sh\npwd\n```",
    }),
  );
  assert.match(markdownMarkup, /id="result"/);
  assert.match(markdownMarkup, /href="https:\/\/example\.com\/docs"/);
  assert.match(markdownMarkup, /target="_blank"/);
  assert.match(markdownMarkup, /type="checkbox"/);
  assert.match(markdownMarkup, /<table/);
  assert.match(markdownMarkup, /aria-label="Copy code to clipboard"/);
  assert.equal(
    appModule.normalizeMarkdownText("1. **Run** `collect`\n2. Review"),
    "Run collect Review",
  );

  assert.deepEqual(appModule.parseChatStreamEvent('{"type":"final","message":{"body":"ok"}}'), {
    type: "final",
    message: { body: "ok" },
  });
  assert.equal(appModule.parseChatStreamEvent("not json"), null);
  assert.equal(
    appModule.threadTitleFromMessage("one two three four five six seven eight nine ten"),
    "one two three four five six seven eight...",
  );

  assert.deepEqual(appModule.chatStreamRequestBody({
    provider: "codex",
    model: "cli-default",
    effort: "high",
    messages: [{ role: "user", body: "hi" }],
    workflow: { id: "workflow-assistant:thread-1", chatThreadId: "thread-1" },
  }), {
    provider: "codex",
    model: "cli-default",
    effort: "high",
    messages: [{ role: "user", body: "hi" }],
    workflow: { id: "workflow-assistant:thread-1", chatThreadId: "thread-1" },
  });
});

test("Markdown code blocks keep their scroll position while streaming and copy their contents", async () => {
  function StreamingMarkdown() {
    const [value, setValue] = React.useState("```sh\nmkdir -p /a/very/long/path\n```");
    return React.createElement(
      React.Fragment,
      null,
      React.createElement(markdownContentModule.default, { onOpenRelativeLink() {}, value }),
      React.createElement(
        "button",
        {
          "aria-label": "Append streamed text",
          onClick: () => setValue("```sh\nmkdir -p /a/very/long/path/that/keeps/growing\n```"),
          type: "button",
        },
        "Append",
      ),
    );
  }

  const dom = await mountReact(React.createElement(StreamingMarkdown), createFetchMock([]));
  const writes = [];
  navigator.clipboard.writeText = async (value) => writes.push(value);
  const pre = dom.first("pre");
  pre.scrollLeft = 84;

  await dom.click(dom.byLabel("Append streamed text"));

  assert.equal(dom.first("pre"), pre);
  assert.equal(pre.scrollLeft, 84);
  await dom.click(dom.byLabel("Copy code to clipboard"));
  assert.deepEqual(writes, ["mkdir -p /a/very/long/path/that/keeps/growing"]);
  assert.match(dom.text(), /Copied/);
  await dom.unmount();
});

test("RunPreviewDialog renders grouped warnings, destructive actions, providers, fan-out samples, and node details", () => {
  const plan = {
    workflowId: "preview-demo",
    workflowName: "Preview Demo",
    warnings: ["Missing read target: /workspace/missing.txt"],
    destructiveActions: ["overwrite file: /workspace/out.txt"],
    requiredSecrets: ["OPENAI_API_KEY"],
    providerRequirements: [
      {
        agentId: "reviewer",
        subscription: "codex",
        binary: "codex",
        available: false,
        workingDir: "/workspace/agents",
        profile: "quality",
        model: "gpt-5",
        timeout: 45,
        extraPaths: ["/workspace/shared"],
      },
    ],
    bindings: [
      {
        id: "binding:scan:operation.command:previous.output",
        destinationNode: "scan",
        destinationField: "operation.command",
        expression: "previous.output",
        producer: "previous-predecessor",
        sourceType: "string",
        destinationType: "string",
        resolutionPhase: "upstream-node-completion",
        status: "runtime-bound",
        coercion: "string",
        consumer: "shell",
      },
      {
        id: "binding:scan:operation.env.TOKEN:secret.API_TOKEN",
        destinationNode: "scan",
        destinationField: "operation.env.TOKEN",
        expression: "secret.API_TOKEN",
        producer: "secret-store",
        sourceType: "secret",
        destinationType: "string",
        resolutionPhase: "run-start",
        status: "runtime-bound",
        coercion: "string",
        consumer: "process-or-shell",
        readiness: "present",
      },
    ],
    triggerContext: {
      watch: { path: "/workspace/inbox", glob: "*.md", mode: "fanout" },
    },
    generations: [
      {
        index: 0,
        nodes: [
          {
            id: "scan",
            type: "bash_command",
            detail: "echo scan",
            workingDir: "/workspace/jobs",
            sideEffects: ["shell command: echo scan"],
            fanOut: {
              sourceType: "directory",
              count: 2,
              countExact: false,
              countLowerBound: 2,
              sampleItems: [
                { path: "/workspace/inbox/a.md" },
                { path: "/workspace/inbox/b.md" },
              ],
            },
            bindings: [
              {
                id: "binding:scan:inputs.file:trigger.file",
                destinationField: "inputs.file",
                expression: "trigger.file",
                status: "optional",
                resolutionPhase: "run-start",
              },
            ],
          },
        ],
      },
    ],
  };

  const html = renderToStaticMarkup(
    React.createElement(appModule.RunPreviewDialog, {
      plan,
      workflow: { id: "preview-demo", name: "Preview Demo" },
      onCancel: () => {},
      onRun: () => {},
    }),
  );

  assert.match(html, /Destructive actions/);
  assert.match(html, /overwrite file: \/workspace\/out\.txt/);
  assert.match(html, /Warnings/);
  assert.match(html, /Required secrets/);
  assert.match(html, /OPENAI_API_KEY/);
  assert.match(html, /Provider CLI requirements/);
  assert.match(
    html,
    /reviewer: codex binary=codex \(missing\) cwd=\/workspace\/agents profile=quality model=gpt-5 timeout=45s/,
  );
  assert.match(html, /Trigger context/);
  assert.match(html, /Watch: \/workspace\/inbox glob=\*\.md mode=fanout/);
  assert.match(html, /<details/);
  assert.match(html, /Generation 0/);
  assert.match(html, /Working directory: \/workspace\/jobs/);
  assert.match(html, /Fan-out directory:/);
  assert.match(html, /at least 2 items/);
  assert.match(html, /Sample: \/workspace\/inbox\/a\.md/);
  assert.match(html, /Runtime bindings/);
  assert.match(html, /upstream-node-completion/);
  assert.match(html, /secret present/);
  assert.match(html, /shell owns expressions such as \$\{FILE_NAME\}/);
  assert.match(html, /role="dialog"/);
  assert.match(html, /aria-modal="true"/);
  assert.match(html, /aria-labelledby=/);
  assert.match(html, /aria-describedby=/);
});

test("UsageSummaryStrip renders run cost, expensive nodes, and slow nodes", () => {
  const html = renderToStaticMarkup(
    React.createElement(canvasModule.UsageSummaryStrip, {
      summary: {
        totals: {
          agent_calls: 3,
          total_tokens: 1234,
          estimated_cost: 0.045,
          agent_time_seconds: 9.5,
        },
        most_expensive_nodes: [{ node_id: "review", estimated_cost: 0.04 }],
        slowest_nodes: [{ node_id: "draft", duration_seconds: 8.25 }],
      },
    }),
  );

  assert.match(html, /LLM usage/);
  assert.match(html, /3 calls/);
  assert.match(html, /1,234 tokens/);
  assert.match(html, /cost~\$0\.045000/);
  assert.match(html, /Most expensive: review/);
  assert.match(html, /Slowest: draft/);
});

test("App loads workflows, preserves local edits on silent refreshes, saves errors, deletes workflows, and loads logs", async () => {
  const selectedExportFolders = [];
  const dom = await mountReact(
    React.createElement(appModule.default),
    createFetchMock([
      jsonResponse("/api/workflows", workflowsPayload([
        workflowFixture({ id: "demo", name: "Demo", label: "Original label" }),
        workflowFixture({ id: "other", name: "Other", label: "Other label" }),
      ])),
      jsonResponse("/api/provider/capabilities", {
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
            efforts: [{ id: "low", displayName: "Low" }, { id: "medium", displayName: "Medium" }],
          }],
        }],
      }),
      jsonResponse("/api/workflows/demo/logs/latest", {
        log: { logText: "latest demo log", logPath: "/tmp/demo.log" },
      }),
      jsonResponse("/api/workflows/demo/logs?limit=100", {
        runs: [{ id: "run-1", status: "success", startedAt: "2026-01-02T03:04:05Z" }],
      }),
      jsonResponse("/api/workflows/demo", { error: "Save rejected" }, { method: "PUT", ok: false, status: 400 }),
      jsonResponse("/api/workflows/demo", { deleted: true }, { method: "DELETE" }),
      jsonResponse("/api/workflows/other/export", {
        bundlePath: "/exports/other.gof.zip",
      }, { method: "POST" }),
      jsonResponse("/api/projects/open", {
        workflows: [
          workflowFixture({ id: "demo", name: "Demo", label: "Remote refreshed label" }),
          workflowFixture({ id: "other", name: "Other", label: "Other label" }),
        ],
      }, { method: "POST" }),
      jsonResponse("/api/workflows", workflowsPayload([
        workflowFixture({ id: "demo", name: "Demo", label: "Remote refreshed label" }),
        workflowFixture({ id: "other", name: "Other", label: "Other label" }),
      ])),
    ]),
    {
      desktop: {
        workspace: {
          pathGrantForApi: (path) => path === "/exports" ? "grant-exports" : "grant-workspace",
          selectPath: async (options) => {
            selectedExportFolders.push(options);
            return "/exports";
          },
          trustProjectRoot: async () => {},
        },
      },
    },
  );

  await dom.flush();
  assert.match(dom.text(), /Demo/);
  await dom.click(dom.ancestor(dom.byText("Run Timeline"), "BUTTON"));
  await dom.flush();
  assert.match(dom.text(), /latest demo log/);
  const graphToolbar = dom.ancestor(
    dom.byTitle("Select workflow run"),
    (node) => node.getAttribute?.("data-toolbar") === "graph-editor",
  );
  const studioTopBar = dom.ancestor(
    graphToolbar,
    (node) => String(node.getAttribute?.("class") ?? "").includes("studio-topbar"),
  );
  assert.ok(studioTopBar);
  assert.equal(
    allElements(dom.container).filter(
      (element) => element.getAttribute?.("data-toolbar") === "graph-editor",
    ).length,
    1,
  );
  assert.equal(
    dom.fetchCalls.some((call) => call.url === "/api/workflows/demo/logs/latest"),
    true,
  );

  const labelInput = dom.controlAfterLabel("Name");
  await dom.change(labelInput, "Local unsaved label");
  assert.match(dom.text(), /Local unsaved label/);

  await dom.flush(2100);
  assert.match(dom.text(), /Local unsaved label/);
  assert.doesNotMatch(dom.text(), /Remote refreshed label/);
  assert.equal(
    dom.fetchCalls.some(
      (call) => call.url === "/api/projects/open" && call.options.method === "POST",
    ),
    false,
  );

  await dom.click(dom.byTitle("Validate workflow"));
  await dom.flush();
  assert.match(dom.text(), /Save rejected/);

  await dom.click(dom.ancestor(dom.byText("Other"), (node) => node.getAttribute?.("role") === "button"));
  assert.match(dom.text(), /Other label/);

  await dom.click(dom.allByTitle("Workflow actions")[0]);
  await dom.click(dom.byText("Delete workflow"));
  await dom.flush();
  assert.equal(dom.fetchCalls.some((call) => call.url === "/api/workflows/demo?sourceFormat=toml" && call.options.method === "DELETE"), true);
  assert.match(dom.text(), /Other/);

  await dom.click(dom.byTitle("Export workflow bundle"));
  await dom.flush();
  assert.match(dom.text(), /Export workflow bundle/);
  await dom.click(dom.byTitle("Choose export folder"));
  await dom.flush();
  assert.deepEqual(selectedExportFolders, [{ currentPath: "/workspace", directoryOnly: true }]);
  await dom.pointer(dom.ancestor(dom.byTitle("Confirm workflow export"), "FORM"), "onSubmit");
  await dom.flush();
  assert.equal(
    dom.fetchCalls.some(
      (call) =>
        call.url === "/api/workflows/other/export" &&
        call.options.method === "POST" &&
        JSON.parse(call.options.body).outputPath === "/exports/other.gof.zip" &&
        JSON.parse(call.options.body).grantId === "grant-exports",
    ),
    true,
  );
  assert.match(dom.text(), /Exported bundle to \/exports\/other\.gof\.zip/);

  await dom.unmount();
});

test("App renders run and stop state, opens the run preview, executes runs, and sends chat prompts", async () => {
  const chatStream = streamResponse([
    '{"type":"thought","text":"**Inspecting graph** with [workflow](https://example.com) and `step`.\\n\\n1. Read nodes\\n2. Check edges","trace":{"kind":"summary","title":"Summary","body":"**Inspecting graph** with [workflow](https://example.com) and `step`.\\n\\n1. Read nodes\\n2. Check edges"}}\n',
    '{"type":"thought","text":"Bash","trace":{"id":"shell-1","kind":"tool","title":"bash","category":"shell","shell":"bash","command":"/usr/bin/bash -lc \\"pwd && npm test\\"","status":"running"}}\n',
    '{"type":"thought","text":"Bash","trace":{"id":"shell-1","kind":"tool","title":"Tool result","output":"tests passed","status":"complete"}}\n',
    '{"type":"thought","text":"Search","trace":{"id":"search-1","kind":"tool","title":"Search","category":"search","input":"{\\"search_query\\":[{\\"q\\":\\"Amsterdam current weather\\"}]}","status":"complete"}}\n',
    '{"type":"thought","text":"Read","trace":{"id":"tool-1","kind":"tool","title":"Read","detail":"workflow.toml","input":"workflow.toml","status":"running"}}\n',
    '{"type":"thought","text":"Read","trace":{"id":"tool-1","kind":"tool","title":"Tool result","output":"[workflow]","status":"complete"}}\n',
    '{"type":"thought","text":"Edit","trace":{"id":"edit-1","kind":"tool","title":"Edit","detail":".raticode/demo/workflow.rattish","input":"{\\"path\\":\\".raticode/demo/workflow.rattish\\",\\"kind\\":\\"update\\"}","status":"complete"}}\n',
    '{"type":"changes","changes":{"id":null,"projectRoot":"/workspace","fileCount":1,"additions":1,"deletions":1,"undoable":false,"undoUnavailableReason":"Undo is available when the assistant finishes","undone":false,"live":true,"files":[{"path":".raticode/demo/workflow.rattish","status":"modified","additions":1,"deletions":1,"binary":false,"diff":"--- a/.raticode/demo/workflow.rattish\\n+++ b/.raticode/demo/workflow.rattish\\n-old\\n+working\\n"}]}}\n',
    '{"type":"final","message":{"body":"**Looks ready**\\n\\n1. Read files\\n2. Classify tickets"},"completedAt":"2026-08-31T12:34:00.000Z","durationMs":2400,"changes":{"id":"change-1","projectRoot":"/workspace","fileCount":1,"additions":2,"deletions":1,"undoable":true,"undone":false,"files":[{"path":".raticode/demo/workflow.rattish","status":"modified","additions":2,"deletions":1,"binary":false,"diff":"--- a/.raticode/demo/workflow.rattish\\n+++ b/.raticode/demo/workflow.rattish\\n-old\\n+new\\n+route\\n"}]}}\n',
  ]);
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([
      {
        ...workflowFixture({ id: "demo", name: "Demo", status: "Running" }),
        filesystemAccess: [
          { path: "/outside/input", read: true, write: false, execute: false },
          { path: "/outside/tools", read: false, write: false, execute: true },
        ],
        runs: [{ id: "run-1", status: "running", startedAt: "2026-01-02T03:04:05Z" }],
      },
    ])),
    jsonResponse("/api/provider/capabilities", {
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
          efforts: [{ id: "low", displayName: "Low" }, { id: "medium", displayName: "Medium" }],
        }],
      }],
    }),
    jsonResponse("/api/workflows/demo/logs/latest", {
      log: { logText: "running log", logPath: "/tmp/demo.log" },
    }),
    jsonResponse("/api/workflows/demo/logs?limit=100", {
      runs: [{ id: "run-1", status: "running", startedAt: "2026-01-02T03:04:05Z" }],
    }),
    jsonResponse("/api/workflows/demo", {
      workflow: {
        ...workflowFixture({ id: "demo", name: "Demo", status: "Ready" }),
        filesystemAccess: [
          { path: "/outside/input", read: true, write: false, execute: false },
          { path: "/outside/tools", read: false, write: false, execute: true },
        ],
      },
    }, { method: "PUT" }),
    jsonResponse("/api/workflows/demo/plan", {
      plan: {
        workflowId: "demo",
        workflowName: "Demo",
        warnings: ["shell effects cannot be inferred"],
        destructiveActions: ["delete file: /tmp/out.txt"],
        generations: [{ index: 0, nodes: [{ id: "step", type: "bash_command", detail: "echo hi" }] }],
      },
    }, { method: "POST" }),
    jsonResponse("/api/workflows/demo/run", {
      run: {
        success: false,
        status: "stopped",
        logText: "run stopped",
        logPath: "/tmp/demo.log",
        nodeOutputs: {},
      },
    }, { method: "POST" }),
    jsonResponse("/api/workflows/demo/logs?limit=100", { runs: [] }),
    jsonResponse("/api/workflows/demo/runs/run-1/stop", { stopped: true }, { method: "POST" }),
    jsonResponse("/api/chat/changes/undo", { id: "change-1", undone: true, fileCount: 1 }, { method: "POST" }),
    jsonResponse("/api/chat/changes/redo", { id: "change-1", undone: false, fileCount: 1 }, { method: "POST" }),
    (url, options) => {
      if (url !== "/api/chat/attachments") return null;
      const upload = JSON.parse(options.body);
      return {
        ok: true,
        status: 201,
        json: async () => ({
          attachments: upload.files.map((file) => ({
            id: "stored-context",
            name: file.name,
            size: 18,
            storageName: "stored-context-context.md",
            type: file.type,
          })),
        }),
      };
    },
    (url) => (url === "/api/chat/stream" ? chatStream(url) : null),
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock, {
    storage: {
      [appModule.STUDIO_SESSION_STORAGE_KEY]: JSON.stringify({ projectRoot: "/workspace", workflowId: "demo" }),
    },
  });

  await dom.flush();
  assert.throws(() => dom.byLabel("Search workflows"), /Unable to find/);
  const stopButton = dom.byTitle("Stop this run");
  assert.equal(stopButton.disabled, false);
  await dom.click(stopButton);
  await dom.flush();
  assert.equal(fetchMock.calls.some((call) => call.url === "/api/workflows/demo/runs/run-1/stop" && call.options.method === "POST"), true);
  assert.equal(fetchMock.calls.some((call) => call.url === "/api/workflows/demo/stop"), false);

  await dom.click(dom.byTitle("Start another workflow run"));
  await dom.flush();
  assert.match(dom.text(), /Run preview: Demo/);
  assert.match(dom.text(), /delete file: \/tmp\/out\.txt/);
  assert.match(dom.text(), /\/outside\/input: read/);
  assert.match(dom.text(), /\/outside\/tools: execute/);
  const previewRunButton = dom.ancestor(dom.byText("Run workflow"), "BUTTON");
  assert.match(previewRunButton.getAttribute("class"), /inline-flex/);
  assert.match(previewRunButton.getAttribute("class"), /items-center/);
  assert.match(previewRunButton.getAttribute("class"), /gap-2/);

  await dom.click(previewRunButton);
  await dom.flush();
  await dom.click(dom.ancestor(dom.byText("Run Timeline"), "BUTTON"));
  await dom.flush();
  assert.match(dom.text(), /run stopped/);
  assert.match(dom.text(), /Stopped/);
  assert.match(dom.text(), /Workflow run completed: stopped/);
  assert.equal(
    matchingLiveRegions(dom.container, {
      politeness: "polite",
      role: "status",
      text: "Workflow run completed: stopped",
    }).length,
    1,
  );
  await dom.flush();
  assert.equal(
    matchingLiveRegions(dom.container, {
      politeness: "polite",
      role: "status",
      text: "Workflow run completed: stopped",
    }).length,
    1,
  );
  const runRequest = fetchMock.calls.find((call) => call.url === "/api/workflows/demo/run");
  assert.deepEqual(JSON.parse(runRequest.options.body), { dryRun: false, triggerContext: {} });

  const chatComposer = dom.ancestor(
    dom.byLabel("Attach files"),
    (element) => element.getAttribute?.("data-chat-composer") !== null,
  );
  const attachmentInput = allElements(chatComposer).find(
    (element) => element.tagName === "INPUT" && element.getAttribute("type") === "file",
  );
  assert.ok(attachmentInput);
  await React.act(async () => {
    await reactProps(attachmentInput).onChange({
      target: {
        files: [{
          name: "context.md",
          size: 18,
          type: "text/markdown",
          text: async () => "# Useful context",
        }],
        value: "/fake/context.md",
      },
    });
  });
  assert.match(dom.text(), /context\.md/);
  await dom.change(dom.first("textarea"), "Explain this workflow");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  assert.match(dom.text(), /Explain this workflow/);
  assert.match(dom.text(), /Hide thoughts/);
  assert.match(dom.text(), /Inspecting graph/);
  const thoughtGroup = dom.byText("Hide thoughts").parentNode.parentNode;
  assert.doesNotMatch(textOf(thoughtGroup), /Summary/);
  assert.equal(
    allElements(thoughtGroup).some(
      (element) => element.tagName === "STRONG" && element.textContent === "Inspecting graph",
    ),
    true,
  );
  assert.equal(
    allElements(thoughtGroup).some(
      (element) => element.tagName === "A" && element.getAttribute("href") === "https://example.com",
    ),
    true,
  );
  assert.equal(
    allElements(thoughtGroup).some(
      (element) => element.tagName === "CODE" && element.textContent === "step",
    ),
    true,
  );
  assert.equal(
    allElements(thoughtGroup).some(
      (element) => element.tagName === "OL" && /Read nodes/.test(element.textContent),
    ),
    true,
  );
  const shellDisclosure = dom.ancestor(dom.byText("Running bash commands"), "BUTTON");
  assert.equal(shellDisclosure.getAttribute("aria-expanded"), "false");
  assert.doesNotMatch(textOf(thoughtGroup), /tests passed/);
  assert.doesNotMatch(textOf(thoughtGroup), /pwd && npm test/);
  assert.doesNotMatch(textOf(thoughtGroup), /\[workflow\]/);
  await dom.click(shellDisclosure);
  assert.equal(shellDisclosure.getAttribute("aria-expanded"), "true");
  assert.match(textOf(thoughtGroup), /\/usr\/bin\/bash -lc "pwd && npm test"/);
  const searchDisclosure = allElements(thoughtGroup).find(
    (element) => element.tagName === "BUTTON" && textOf(element).trim() === "Search",
  );
  assert.ok(searchDisclosure);
  assert.equal(searchDisclosure.getAttribute("aria-expanded"), "false");
  await dom.click(searchDisclosure);
  assert.equal(searchDisclosure.getAttribute("aria-expanded"), "true");
  assert.match(textOf(searchDisclosure), /Amsterdam current weather/);
  assert.match(dom.text(), /workflow\.toml/);
  const readDisclosure = allElements(thoughtGroup).find(
    (element) => element.tagName === "BUTTON" && textOf(element).includes("workflow.toml"),
  );
  assert.ok(readDisclosure);
  assert.equal(readDisclosure.getAttribute("aria-expanded"), "false");
  await dom.click(readDisclosure);
  assert.equal(readDisclosure.getAttribute("aria-expanded"), "true");
  assert.match(dom.text(), /\[workflow\]/);
  const editDisclosure = dom.ancestor(dom.byText("Editing files"), "BUTTON");
  assert.equal(editDisclosure.getAttribute("aria-expanded"), "false");
  await dom.click(editDisclosure);
  assert.equal(editDisclosure.getAttribute("aria-expanded"), "true");
  assert.match(textOf(thoughtGroup), /\.raticode\/demo\/workflow\.rattish/);
  const editedFileLink = dom.byLabel(
    "Open .raticode/demo/workflow.rattish in code editor",
  );
  assert.equal(editedFileLink.tagName, "BUTTON");
  assert.equal(editedFileLink.style.direction, "rtl");
  assert.equal(editedFileLink.getAttribute("title"), ".raticode/demo/workflow.rattish");
  await dom.click(dom.ancestor(dom.byText("Hide thoughts"), "BUTTON"));
  assert.doesNotMatch(dom.text(), /Running bash commands|Inspecting graph/);
  await dom.change(dom.first("textarea"), "Draft while thoughts are hidden");
  assert.equal(dom.first("textarea").value, "Draft while thoughts are hidden");
  await dom.click(dom.ancestor(dom.byText("Show thoughts"), "BUTTON"));
  assert.match(dom.text(), /Running bash commands/);
  assert.match(dom.text(), /Looks ready/);
  assert.equal(
    allElements(dom.container).some(
      (element) => element.tagName === "STRONG" && element.textContent === "Looks ready",
    ),
    true,
  );
  assert.equal(
    allElements(dom.container).some(
      (element) => element.tagName === "OL" && /Read files/.test(element.textContent),
    ),
    true,
  );
  assert.match(dom.text(), /Rem response complete/);
  const projectDiscoveryRequest = fetchMock.calls.find(
    (call) => call.url === "/api/projects/open" && call.options.method === "POST",
  );
  assert.deepEqual(JSON.parse(projectDiscoveryRequest.options.body), {
    projectRoot: "/workspace",
  });
  assert.match(dom.text(), /Edited 1 file/);
  assert.match(dom.text(), /\+2/);
  assert.match(dom.text(), /-1/);
  assert.match(dom.text(), /Ran for 2s/);
  assert.equal(
    matchingLiveRegions(dom.container, {
      politeness: "polite",
      role: "status",
      text: "Rem response complete",
    }).length,
    1,
  );
  await dom.click(dom.ancestor(dom.byText("Review"), "BUTTON"));
  assert.match(dom.text(), /old/);
  assert.match(dom.text(), /route/);
  await dom.click(dom.ancestor(dom.byText("Undo"), "BUTTON"));
  await dom.flush();
  assert.ok(dom.byText("Redo"));
  const undoRequest = fetchMock.calls.find((call) => call.url === "/api/chat/changes/undo");
  assert.deepEqual(JSON.parse(undoRequest.options.body), { changeSetId: "change-1" });
  assert.equal(
    matchingLiveRegions(dom.container, {
      politeness: "polite",
      role: "status",
      text: "Rem changes undone",
    }).length,
    1,
  );
  await dom.click(dom.ancestor(dom.byText("Redo"), "BUTTON"));
  await dom.flush();
  assert.ok(dom.byText("Undo"));
  const redoRequest = fetchMock.calls.find((call) => call.url === "/api/chat/changes/redo");
  assert.deepEqual(JSON.parse(redoRequest.options.body), { changeSetId: "change-1" });
  assert.equal(
    matchingLiveRegions(dom.container, {
      politeness: "polite",
      role: "status",
      text: "Rem changes reapplied",
    }).length,
    1,
  );
  const chatRequest = fetchMock.calls.find((call) => call.url === "/api/chat/stream");
  const chatRequestBody = JSON.parse(chatRequest.options.body);
  assert.equal(chatRequestBody.workflow.projectRoot, "/workspace");
  assert.equal(chatRequestBody.workflow.selectedWorkflowId, null);
  assert.equal(chatRequestBody.messages.at(-1).body, "Explain this workflow");
  assert.equal(chatRequestBody.messages.at(-1).attachments[0].name, "context.md");
  assert.equal(
    chatRequestBody.messages.at(-1).attachments[0].storageName,
    "stored-context-context.md",
  );

  await dom.unmount();
});

test("ACP text deltas update one thought, preserve whitespace, and remove duplicate final output", async () => {
  const fragments = ["A ", "son", "net", " ", "or\n", "limerick."];
  const body = fragments.join("");
  const controlled = controlledStreamResponse([
    ...fragments.map(text => JSON.stringify({ type: "thought", text, deltaStreamId: "acp-turn-1" }) + "\n"),
    JSON.stringify({ type: "final", message: { body } }) + "\n",
  ]);
  let threadId;
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([workflowFixture()])),
    jsonResponse("/api/provider/capabilities", {
      providers: [{ id: "grok", displayName: "Grok", available: true, models: [] }],
    }),
    (url, options) => {
      if (url !== "/api/chat/stream") return null;
      threadId = JSON.parse(options.body).conversationId;
      return controlled.response(url);
    },
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock);
  try {
    await dom.flush();
    await dom.change(dom.first("textarea"), "Write a poem");
    await dom.click(dom.byTitle("Send message"));
    await dom.flush();
    for (let index = 0; index < fragments.length; index += 1) {
      controlled.releaseNext();
      await dom.flush();
      const saved = JSON.parse(window.localStorage.getItem(appModule.chatStorageKeyFor(threadId)));
      const thoughts = saved.filter(message => message.kind === "thought");
      assert.equal(thoughts.length, 1);
      assert.equal(thoughts[0].body, fragments.slice(0, index + 1).join(""));
    }
    assert.match(dom.text(), /sonnet/);
    controlled.releaseNext();
    await dom.flush();
    const saved = JSON.parse(window.localStorage.getItem(appModule.chatStorageKeyFor(threadId)));
    assert.equal(saved.filter(message => message.kind === "thought").length, 0);
    assert.equal(saved.filter(message => message.kind === "final" && message.body === body).length, 1);
  } finally { await dom.unmount(); }
});

test("assistant threads keep streaming after navigation and report running and completed state", async () => {
  const controlledStream = controlledStreamResponse([
    '{"type":"thought","text":"Still working"}\n',
    '{"type":"final","message":{"body":"Background response finished"}}\n',
  ]);
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([workflowFixture()])),
    jsonResponse("/api/provider/capabilities", {
      providers: [{ id: "codex", displayName: "Codex", available: true, models: [] }],
    }),
    (url) => (url === "/api/chat/stream" ? controlledStream.response(url) : null),
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock);

  await dom.flush();
  await dom.change(dom.first("textarea"), "Keep tracking this response");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  await dom.click(dom.byTitle("Back to active threads"));

  assert.equal(dom.byTitle("Rem response running").tagName, "SPAN");
  assert.match(dom.text(), /Keep tracking this response/);

  controlledStream.releaseNext();
  await dom.flush();
  assert.equal(dom.byTitle("Rem response running").tagName, "SPAN");

  controlledStream.releaseNext();
  await dom.flush();
  assert.equal(dom.byTitle("Rem response complete").tagName, "SPAN");
  assert.equal(
    matchingLiveRegions(dom.container, {
      politeness: "polite",
      role: "status",
      text: "Rem response complete in Keep tracking this response",
    }).length,
    1,
  );

  const completedThreadButton = allElements(dom.container).find(
    (element) =>
      element.tagName === "BUTTON" && textOf(element).includes("Keep tracking this response"),
  );
  assert.ok(completedThreadButton);
  await dom.click(completedThreadButton);
  await dom.flush();
  assert.match(dom.text(), /Background response finished/);

  await dom.click(dom.byTitle("Active threads"));
  assert.equal(
    allElements(dom.container).some(
      (element) => element.getAttribute?.("title") === "Rem response complete",
    ),
    false,
  );

  await dom.unmount();
});

test("assistant file changes and elapsed time update before the turn completes", async () => {
  const controlledStream = controlledStreamResponse([
    '{"type":"changes","changes":{"id":null,"projectRoot":"/workspace","fileCount":1,"additions":1,"deletions":0,"undoable":false,"live":true,"files":[{"path":"workflow.rattish","status":"modified","additions":1,"deletions":0,"binary":false,"diff":"+working\\n"}]}}\n',
    '{"type":"final","message":{"body":"Done"},"completedAt":"2026-08-31T12:34:00.000Z","durationMs":2100,"changes":{"id":"change-1","projectRoot":"/workspace","fileCount":1,"additions":2,"deletions":0,"undoable":true,"undone":false,"files":[{"path":"workflow.rattish","status":"modified","additions":2,"deletions":0,"binary":false,"diff":"+done\\n+tested\\n"}]}}\n',
  ]);
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([workflowFixture()])),
    jsonResponse("/api/provider/capabilities", {
      providers: [{ id: "codex", displayName: "Codex", available: true, models: [] }],
    }),
    (url) => (url === "/api/chat/stream" ? controlledStream.response(url) : null),
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock);

  await dom.flush();
  await dom.change(dom.first("textarea"), "Edit this workflow");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  assert.match(dom.text(), /Running for 0s/);

  controlledStream.releaseNext();
  await dom.flush();
  assert.match(dom.text(), /Editing 1 file/);
  assert.match(dom.text(), /\+1/);
  const liveChangeCard = dom.byLabel("Rem file changes");
  const liveUndo = allElements(liveChangeCard).find(
    (element) => element.tagName === "BUTTON" && textOf(element).trim() === "Undo",
  );
  assert.equal(reactProps(liveUndo).disabled, true);

  controlledStream.releaseNext();
  await dom.flush();
  assert.match(dom.text(), /Edited 1 file/);
  assert.match(dom.text(), /Ran for 2s/);
  assert.doesNotMatch(dom.text(), /Editing 1 file/);

  await dom.unmount();
});

test("assistant keeps an open live edit preview stable while new messages arrive", async () => {
  const controlledStream = controlledStreamResponse([
    '{"type":"changes","changes":{"id":null,"projectRoot":"/workspace","fileCount":1,"additions":1,"deletions":0,"undoable":false,"live":true,"files":[{"path":"workflow.rattish","status":"modified","additions":1,"deletions":0,"binary":false,"diff":"+working\\n"}]}}\n',
    '{"type":"thought","text":"Checking the updated workflow"}\n',
    '{"type":"final","message":{"body":"Done"},"completedAt":"2026-08-31T12:34:00.000Z","durationMs":2100,"changes":{"id":"change-1","projectRoot":"/workspace","fileCount":1,"additions":1,"deletions":0,"undoable":true,"undone":false,"files":[{"path":"workflow.rattish","status":"modified","additions":1,"deletions":0,"binary":false,"diff":"+working\\n"}]}}\n',
  ]);
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
    (url) => (url === "/api/chat/stream" ? controlledStream.response(url) : null),
  ]);
  const workflow = workflowFixture({ id: "testing", name: "Testing" });
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    activeWorkflowId: workflow.id,
    onOpenMarkdownLink() {},
    onResizeKeyDown() {},
    onResizeStart() {},
    width: 380,
    workflow,
    workflows: [workflow],
  }), fetchMock);
  const scrollPane = allElements(dom.container).find(
    (element) => element.getAttribute?.("data-chat-scroll") === "true",
  );
  scrollPane.clientHeight = 100;
  scrollPane.scrollHeight = 500;
  scrollPane.scrollTop = 400;

  await dom.change(dom.first("textarea"), "Edit this workflow");
  await dom.click(dom.byTitle("Send message"));
  controlledStream.releaseNext();
  await dom.flush();
  const liveChangeCard = dom.byLabel("Rem file changes");
  await dom.click(dom.ancestor(dom.byText("Review"), "BUTTON"));
  assert.match(textOf(liveChangeCard), /working/);

  scrollPane.scrollHeight = 700;
  controlledStream.releaseNext();
  await dom.flush();
  assert.equal(dom.byLabel("Rem file changes"), liveChangeCard);
  assert.match(textOf(liveChangeCard), /Close/);
  assert.equal(scrollPane.scrollTop, 700);

  scrollPane.scrollHeight = 760;
  controlledStream.releaseNext();
  await dom.flush();
  assert.equal(dom.byLabel("Rem file changes"), liveChangeCard);
  assert.match(textOf(liveChangeCard), /Close/);
  assert.equal(scrollPane.scrollTop, 760);

  await dom.unmount();
});

test("editing and resending the latest user message replaces the rest of the conversation", async () => {
  const writes = [];
  const chatStream = streamResponse([
    '{"type":"thought","text":"Checking the prompt"}\n',
    '{"type":"final","message":{"body":"Done"}}\n',
  ]);
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
    (url) => (url === "/api/chat/stream" ? chatStream(url) : null),
  ]);
  const workflow = workflowFixture({ id: "testing", name: "Testing" });
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    activeWorkflowId: workflow.id,
    onOpenMarkdownLink() {},
    onResizeKeyDown() {},
    onResizeStart() {},
    width: 380,
    workflow,
    workflows: [workflow],
  }), fetchMock);
  navigator.clipboard.writeText = async (value) => writes.push(value);

  await dom.change(dom.first("textarea"), "First prompt");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  await dom.change(dom.first("textarea"), "Fat-fingered prmopt");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();

  const copyButtons = allElements(dom.container).filter(
    (element) => element.getAttribute?.("aria-label") === "Copy message",
  );
  const editButtons = allElements(dom.container).filter(
    (element) => element.getAttribute?.("aria-label") === "Edit message",
  );
  const responseCopyButtons = allElements(dom.container).filter(
    (element) => element.getAttribute?.("aria-label") === "Copy response as Markdown",
  );
  assert.equal(copyButtons.length, 2);
  assert.equal(responseCopyButtons.length, 2);
  assert.equal(editButtons.length, 1);
  await dom.click(copyButtons[0]);
  assert.deepEqual(writes, ["First prompt"]);
  assert.equal(copyButtons[0].getAttribute("aria-label"), "Message copied");

  await dom.click(responseCopyButtons[0]);
  assert.deepEqual(writes, ["First prompt", "Done"]);
  assert.equal(responseCopyButtons[0].getAttribute("aria-label"), "Response copied");

  await dom.click(editButtons[0]);
  const editField = dom.byLabel("Edit message text");
  assert.equal(editField.value, "Fat-fingered prmopt");
  await dom.change(editField, "Corrected prompt");
  await dom.click(dom.ancestor(dom.byText("Send again"), "BUTTON"));
  await dom.flush();
  assert.match(dom.text(), /Corrected prompt/);
  assert.doesNotMatch(dom.text(), /Fat-fingered prmopt/);

  const chatRequests = fetchMock.calls.filter((call) => call.url === "/api/chat/stream");
  assert.equal(chatRequests.length, 3);
  assert.deepEqual(
    JSON.parse(chatRequests.at(-1).options.body).messages.map(({ role, body }) => ({ role, body })),
    [
      { role: "user", body: "First prompt" },
      { role: "assistant", body: "Checking the prompt" },
      { role: "assistant", body: "Done" },
      { role: "user", body: "Corrected prompt" },
    ],
  );

  const [thread] = appModule.loadChatThreads();
  const storedMessages = JSON.parse(window.localStorage.getItem(appModule.chatStorageKeyFor(thread.id)));
  assert.equal(storedMessages.findLast((message) => message.role === "user").body, "Corrected prompt");
  assert.equal(storedMessages.filter((message) => message.role === "user").length, 2);

  await dom.unmount();
});

test("assistant threads keep their project scope until the user changes it", async () => {
  const alpha = {
    ...workflowFixture({ id: "alpha-workflow", name: "Alpha workflow" }),
    projectName: "alpha",
    projectRoot: "/projects/alpha",
  };
  const beta = {
    ...workflowFixture({ id: "beta-workflow", name: "Beta workflow" }),
    projectName: "beta",
    projectRoot: "/projects/beta",
  };
  const chatStream = streamResponse([
    '{"type":"final","message":{"body":"Done"}}\n',
  ]);
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", {
      providers: [{ id: "codex", displayName: "Codex", available: true, models: [] }],
    }),
    (url) => (url === "/api/chat/stream" ? chatStream(url) : null),
  ]);

  function ScopeHarness() {
    const [selectedWorkflow, setSelectedWorkflow] = React.useState(alpha);
    return React.createElement(
      React.Fragment,
      null,
      React.createElement("button", {
        type: "button",
        onClick: () => setSelectedWorkflow(beta),
      }, "Select beta in Studio"),
      React.createElement(appModule.ChatPane, {
        activeWorkflowId: selectedWorkflow.id,
        onOpenMarkdownLink() {},
        onResizeKeyDown() {},
        onResizeStart() {},
        recentProjectRoots: ["/projects/alpha", "/projects/beta"],
        width: 380,
        workflow: selectedWorkflow,
        workflows: [alpha, beta],
      }),
    );
  }

  const dom = await mountReact(React.createElement(ScopeHarness), fetchMock);
  await dom.flush();
  assert.ok(dom.byLabel("Scoped to alpha. Change project scope"));

  await dom.change(dom.first("textarea"), "Start in alpha");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  let requests = fetchMock.calls.filter((call) => call.url === "/api/chat/stream");
  let requestBody = JSON.parse(requests[0].options.body);
  assert.equal(requestBody.workflow.projectRoot, "/projects/alpha");
  assert.equal(requestBody.workflow.selectedWorkflowId, null);
  assert.deepEqual(
    requestBody.workflow.workflows.map((workflow) => workflow.id),
    ["alpha-workflow"],
  );

  await dom.click(dom.byText("Select beta in Studio"));
  assert.ok(dom.byLabel("Scoped to alpha. Change project scope"));

  await dom.click(dom.byLabel("Scoped to alpha. Change project scope"));
  const scopeMenu = dom.byLabel("Rem project scope");
  const betaScopeButton = allElements(scopeMenu).find(
    (element) => element.tagName === "BUTTON" && textOf(element).trim() === "beta",
  );
  assert.ok(betaScopeButton);
  await dom.click(betaScopeButton);
  assert.ok(dom.byLabel("Scoped to beta. Change project scope"));

  await dom.change(dom.first("textarea"), "Continue in beta");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  requests = fetchMock.calls.filter((call) => call.url === "/api/chat/stream");
  requestBody = JSON.parse(requests[1].options.body);
  assert.equal(requestBody.workflow.projectRoot, "/projects/beta");
  assert.equal(requestBody.workflow.selectedWorkflowId, null);
  assert.deepEqual(
    requestBody.workflow.workflows.map((workflow) => workflow.id),
    ["beta-workflow"],
  );

  await dom.unmount();
});

test("Rem with no open folder does not inherit a selected workflow's stale scope", async () => {
  const workflow = { ...workflowFixture(), projectRoot: "/worktrees/perf-improvements" };
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    activeProjectRoot: "", workflow, workflows: [workflow], width: 380,
  }), createFetchMock([jsonResponse("/api/provider/capabilities", { providers: [] })]));
  assert.ok(dom.byLabel("Scoped to No project. Change project scope"));
  await dom.click(dom.byLabel("New thread"));
  assert.ok(dom.byLabel("Scoped to No project. Change project scope"));
  assert.equal(dom.fetchCalls.some(call => call.url === "/api/projects/open"), false);
  await dom.unmount();
});

test("Rem returns to the open folder after leaving a thread with a deleted worktree", async () => {
  const staleWorkflow = { ...workflowFixture(), projectRoot: "/worktrees/perf-improvements" };
  function Harness() {
    const [root, setRoot] = React.useState(staleWorkflow.projectRoot);
    return React.createElement(React.Fragment, null,
      React.createElement("button", { onClick: () => setRoot("/projects/gofer-flow") }, "Open gofer-flow"),
      React.createElement(appModule.ChatPane, {
        activeProjectRoot: root, workflow: staleWorkflow, workflows: [staleWorkflow], width: 380,
        recentProjectRoots: ["/projects/gofer-flow"],
      }));
  }
  const dom = await mountReact(React.createElement(Harness), createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
  ]), { desktop: { workspace: {
    getPathInfo: async root => ({ isDirectory: root === "/projects/gofer-flow" }),
    gitWorktrees: async () => ({ worktrees: [] }),
  } } });
  await dom.click(dom.byLabel("New thread"));
  await dom.click(dom.byText("Open gofer-flow"));
  assert.ok(dom.byLabel("Scoped to perf-improvements. Change project scope"));
  await dom.click(dom.byTitle("Back to active threads"));
  assert.ok(dom.byLabel("Scoped to gofer-flow. Change project scope"));
  await dom.click(dom.byLabel("Scoped to gofer-flow. Change project scope"));
  await dom.flush();
  assert.deepEqual(allElements(dom.byLabel("Rem project scope"))
    .filter(el => el.getAttribute("role") === "menuitem").map(el => el.getAttribute("title")),
  ["/projects/gofer-flow"]);
  await dom.click(dom.byLabel("New thread"));
  assert.ok(dom.byLabel("Scoped to gofer-flow. Change project scope"));
  await dom.unmount();
});

test("changing project scope from assistant home keeps the thread list visible", async () => {
  const alpha = {
    ...workflowFixture({ id: "alpha-workflow", name: "Alpha workflow" }),
    projectName: "alpha",
    projectRoot: "/projects/alpha",
  };
  const beta = {
    ...workflowFixture({ id: "beta-workflow", name: "Beta workflow" }),
    projectName: "beta",
    projectRoot: "/projects/beta",
  };
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", {
      providers: [{ id: "codex", displayName: "Codex", available: true, models: [] }],
    }),
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    activeWorkflowId: alpha.id,
    onOpenMarkdownLink() {},
    onResizeKeyDown() {},
    onResizeStart() {},
    recentProjectRoots: ["/projects/alpha", "/projects/beta"],
    width: 380,
    workflow: alpha,
    workflows: [alpha, beta],
  }), fetchMock);

  await dom.flush();
  assert.ok(allElements(dom.container).find(
    (element) => element.getAttribute?.("data-assistant-home") !== null,
  ));
  assert.match(dom.text(), /Active threads/);

  await dom.click(dom.byLabel("Scoped to alpha. Change project scope"));
  const scopeMenu = dom.byLabel("Rem project scope");
  const betaScopeButton = allElements(scopeMenu).find(
    (element) => element.tagName === "BUTTON" && textOf(element).trim() === "beta",
  );
  assert.ok(betaScopeButton);
  await dom.click(betaScopeButton);

  assert.ok(dom.byLabel("Scoped to beta. Change project scope"));
  assert.ok(allElements(dom.container).find(
    (element) => element.getAttribute?.("data-assistant-home") !== null,
  ));
  assert.match(dom.text(), /Active threads/);
  assert.equal(dom.allByTitle("Back to active threads").length, 0);

  await dom.unmount();
});

test("deleting a background assistant thread disposes its pending stream state", async () => {
  const controlledStream = controlledStreamResponse([
    '{"type":"final","message":{"body":"This must stay deleted"}}\n',
  ]);
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([workflowFixture()])),
    jsonResponse("/api/provider/capabilities", {
      providers: [{ id: "codex", displayName: "Codex", available: true, models: [] }],
    }),
    (url) => (url === "/api/chat/stream" ? controlledStream.response(url) : null),
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock);

  await dom.flush();
  await dom.change(dom.first("textarea"), "Delete this running thread");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  const chatRequest = fetchMock.calls.find((call) => call.url === "/api/chat/stream");
  const threadId = JSON.parse(chatRequest.options.body).workflow.chatThreadId;
  await dom.click(dom.byTitle("Back to active threads"));
  await dom.click(dom.byTitle("Thread options"));
  window.confirm = () => true;
  await dom.click(dom.byText("Delete thread"));
  await dom.flush();

  controlledStream.releaseNext();
  await dom.flush();

  assert.doesNotMatch(dom.text(), /Delete this running thread/);
  assert.equal(window.localStorage.getItem(appModule.chatStorageKeyFor(threadId)), null);
  assert.equal(dom.allByTitle("Rem response running").length, 0);
  assert.equal(dom.allByTitle("Rem response complete").length, 0);

  await dom.unmount();
});

test("assistant activity remains independent across concurrent threads", async () => {
  const firstStream = controlledStreamResponse([
    '{"type":"final","message":{"body":"First finished"}}\n',
  ]);
  const secondStream = controlledStreamResponse([
    '{"type":"final","message":{"body":"Second finished"}}\n',
  ]);
  let streamIndex = 0;
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([workflowFixture()])),
    jsonResponse("/api/provider/capabilities", {
      providers: [{ id: "codex", displayName: "Codex", available: true, models: [] }],
    }),
    (url) => {
      if (url !== "/api/chat/stream") return null;
      const stream = streamIndex === 0 ? firstStream : secondStream;
      streamIndex += 1;
      return stream.response(url);
    },
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock);

  await dom.flush();
  await dom.change(dom.first("textarea"), "First background thread");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  await dom.click(dom.byTitle("Back to active threads"));
  await dom.click(dom.byTitle("New thread"));
  await dom.change(dom.first("textarea"), "Second background thread");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  await dom.click(dom.byTitle("Back to active threads"));
  assert.equal(dom.allByTitle("Rem response running").length, 2);

  secondStream.releaseNext();
  await dom.flush();
  assert.equal(dom.allByTitle("Rem response running").length, 1);
  assert.equal(dom.allByTitle("Rem response complete").length, 1);

  firstStream.releaseNext();
  await dom.flush();
  assert.equal(dom.allByTitle("Rem response running").length, 0);
  assert.equal(dom.allByTitle("Rem response complete").length, 2);

  await dom.unmount();
});

test("assistant errors persist across retries and reloads but stay out of model context", async () => {
  let chatRequestCount = 0;
  const requests = [];
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([workflowFixture()])),
    jsonResponse("/api/provider/capabilities", {
      providers: [{ id: "codex", displayName: "Codex", available: true, models: [] }],
    }),
    (url, options) => {
      if (url !== "/api/chat/stream") return null;
      requests.push(JSON.parse(options.body));
      chatRequestCount += 1;
      return streamResponse(
        chatRequestCount === 1
          ? ['{"type":"error","error":"Assistant unavailable"}\n']
          : ['{"type":"final","message":{"body":"Recovered"}}\n'],
      )(url);
    },
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock);

  await dom.flush();
  const composer = dom.first("textarea");
  await dom.change(composer, "First request");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();

  assert.equal(
    matchingLiveRegions(dom.container, {
      politeness: "assertive",
      role: "alert",
      text: "Assistant unavailable",
    }).length,
    1,
  );
  await dom.flush();
  assert.equal(
    matchingLiveRegions(dom.container, {
      politeness: "assertive",
      role: "alert",
      text: "Assistant unavailable",
    }).length,
    1,
  );

  await dom.change(composer, "Try again");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  assert.equal(
    matchingLiveRegions(dom.container, {
      politeness: "assertive",
      role: "alert",
      text: "Assistant unavailable",
    }).length,
    1,
  );
  assert.equal(
    matchingLiveRegions(dom.container, {
      politeness: "polite",
      role: "status",
      text: "Rem response complete",
    }).length,
    1,
  );

  assert.ok(requests[1].messages.some(message => message.body === "First request"));
  assert.ok(requests[1].messages.every(message => !message.body.includes("Assistant unavailable")));
  const threadId = requests[0].conversationId;
  const keys = ["gofer-flow-chat-threads", `gofer-flow-chat-thread-meta:${threadId}`, appModule.chatStorageKeyFor(threadId)];
  const storage = Object.fromEntries(keys.map(key => [key, window.localStorage.getItem(key)]));
  const saved = JSON.parse(storage[appModule.chatStorageKeyFor(threadId)]);
  assert.equal(saved.filter(message => message.kind === "error").length, 1);
  await dom.unmount();
  const reopened = await mountReact(React.createElement(appModule.ChatPane, { width: 380 }), fetchMock, { storage });
  try {
    await reopened.flush();
    await reopened.click(allElements(reopened.container).find(element => element.tagName === "BUTTON" && textOf(element).startsWith("First request")));
    await reopened.flush();
    assert.match(reopened.text(), /Assistant unavailable/);
    await reopened.change(reopened.first("textarea"), "After reopening");
    await reopened.click(reopened.byTitle("Send message"));
    await reopened.flush();
    assert.ok(requests[2].messages.every(message => !message.body.includes("Assistant unavailable")));
  } finally { await reopened.unmount(); }
});

test("App shows workflow health diagnostics before running", async () => {
  const workflow = {
    ...workflowFixture({ id: "doctor", name: "Doctor" }),
    healthErrors: [
      {
        id: "workflow.provider_cli",
        severity: "error",
        subject: "codex",
        message: "Workflow requires provider CLI 'codex', but it is not on PATH.",
      },
    ],
  };
  const fetchMock = createFetchMock([
    jsonResponse("/api/doctor", {
      errors: [],
      warnings: [
        {
          id: "shell.available",
          severity: "warning",
          message: "Shell executable 'bash' is not on PATH.",
        },
      ],
    }),
    jsonResponse("/api/workflows", workflowsPayload([workflow])),
    jsonResponse("/api/workflows/doctor/logs/latest", { log: null }),
    jsonResponse("/api/workflows/doctor/logs?limit=100", { runs: [] }),
    jsonResponse("/api/workflows/doctor/approvals", { approvals: [] }),
  ]);

  const dom = await mountReact(React.createElement(appModule.default), fetchMock);

  await dom.flush();
  assert.match(dom.text(), /Environment setup needs attention/);
  assert.match(dom.text(), /Shell executable 'bash' is not on PATH/);
  assert.match(dom.text(), /Workflow requires provider CLI 'codex'/);
  assert.equal(fetchMock.calls.some((call) => call.url === "/api/doctor"), true);

  await dom.click(dom.byTitle("Hide environment warning"));
  await dom.flush();
  assert.doesNotMatch(dom.text(), /Environment setup needs attention/);
  assert.doesNotMatch(dom.text(), /Shell executable 'bash' is not on PATH/);

  await dom.unmount();
});

test("App does not show an environment notice when health checks are clean", async () => {
  const fetchMock = createFetchMock([
    jsonResponse("/api/doctor", { errors: [], warnings: [] }),
    jsonResponse("/api/workflows", workflowsPayload([
      workflowFixture({ id: "clean", name: "Clean" }),
    ])),
    jsonResponse("/api/workflows/clean/logs/latest", { log: null }),
    jsonResponse("/api/workflows/clean/logs?limit=100", { runs: [] }),
    jsonResponse("/api/workflows/clean/approvals", { approvals: [] }),
  ]);

  const dom = await mountReact(React.createElement(appModule.default), fetchMock);

  await dom.flush();
  assert.doesNotMatch(dom.text(), /Environment health checks passed/);
  assert.doesNotMatch(dom.text(), /Environment setup/);
  assert.equal(fetchMock.calls.some((call) => call.url === "/api/doctor"), true);

  await dom.unmount();
});

test("App lets users dismiss a doctor load failure warning", async () => {
  const fetchMock = createFetchMock([
    (url) => {
      if (url !== "/api/doctor") return null;
      throw new Error("Unable to reach doctor API");
    },
    jsonResponse("/api/workflows", workflowsPayload([
      workflowFixture({ id: "doctor-error", name: "Doctor Error" }),
    ])),
    jsonResponse("/api/workflows/doctor-error/logs/latest", { log: null }),
    jsonResponse("/api/workflows/doctor-error/logs?limit=100", { runs: [] }),
    jsonResponse("/api/workflows/doctor-error/approvals", { approvals: [] }),
  ]);

  const dom = await mountReact(React.createElement(appModule.default), fetchMock);

  await dom.flush();
  assert.match(dom.text(), /Unable to reach doctor API/);

  await dom.click(dom.byTitle("Hide environment warning"));
  await dom.flush();
  assert.doesNotMatch(dom.text(), /Unable to reach doctor API/);

  await dom.unmount();
});

test("DagCanvas mounted interactions create/select/edit/delete nodes, create edges, persist positions, and use folder pickers", async () => {
  let workflow = {
    ...workflowFixture({ id: "canvas", name: "Canvas", label: "Initial command" }),
    validationBindings: [
      {
        id: "binding:step:operation.command:trigger.name",
        destinationNode: "step",
        destinationField: "operation.command",
        expression: "trigger.name",
        producer: "workflow.trigger",
        sourceType: "unknown",
        destinationType: "string",
        resolutionPhase: "run-start",
        status: "optional",
        coercion: "string",
      },
    ],
  };
  const changes = [];
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      dataDir: "/workspace",
      workflow,
      onWorkflowChange(nextWorkflow) {
        workflow = nextWorkflow;
        changes.push(nextWorkflow);
      },
    }),
    createFetchMock([]),
    {
      desktop: {
        workspace: {
          getPathInfo: async () => ({ isDirectory: true, isFile: false }),
          listDirectory: async ({ currentPath }) => ({
            directory: currentPath === "/workspace/repo" ? "/workspace/repo" : "/workspace",
            parent: currentPath === "/workspace/repo" ? "/workspace" : null,
            entries: currentPath === "/workspace/repo"
              ? []
              : [{ name: "repo", path: "/workspace/repo", isDirectory: true, isFile: false }],
          }),
        },
      },
    },
  );

  await dom.flush();
  await dom.click(dom.byTitle("Add node"));
  assert.equal(changes.at(-1).nodes.length, 2);
  assert.equal(changes.at(-1).nodes[1].type, "agent");

  await dom.pointer(dom.ancestor(dom.byText("Initial command"), "ARTICLE"), "onPointerDown");
  await dom.flush();
  assert.equal(dom.byText("Node inspector").tagName, "H2");
  const nodeInspectorHeader = dom.ancestor(dom.byText("Node inspector"), "HEADER");
  assert.equal(
    allElements(nodeInspectorHeader).some(
      (element) => element.tagName === "P" && textOf(element) === workflow.nodes[0].id,
    ),
    true,
  );
  assert.equal(dom.byTitle("Hide node inspector").tagName, "BUTTON");
  const nodeInspectorTabs = allElements(dom.byLabel("Node inspector sections")).filter(
    (element) => element.getAttribute("role") === "tab",
  );
  assert.deepEqual(
    nodeInspectorTabs.map((tab) => textOf(tab)),
    ["General", "Action", "Inputs", "Run", "Edges"],
  );
  assert.equal(nodeInspectorTabs[0].getAttribute("aria-selected"), "true");
  const nodeInspectorPanel = (id) =>
    allElements(dom.container).find((element) => element.getAttribute("id") === id);
  assert.equal(nodeInspectorPanel("node-tabpanel-action").getAttribute("hidden"), "");
  assert.equal(nodeInspectorPanel("node-tabpanel-general").getAttribute("tabindex"), "0");
  assert.equal(
    nodeInspectorPanel("node-tabpanel-general").contains(dom.controlAfterLabel("Label")),
    true,
  );
  assert.equal(
    allElements(nodeInspectorPanel("node-tabpanel-general")).some(
      (element) => element.tagName === "LABEL" && textOf(element) === "ID",
    ),
    false,
  );
  assert.equal(
    nodeInspectorPanel("node-tabpanel-action").contains(dom.controlAfterLabel("Command")),
    true,
  );
  assert.equal(
    nodeInspectorPanel("node-tabpanel-inputs").textContent.includes("Source output"),
    true,
  );
  assert.equal(
    nodeInspectorPanel("node-tabpanel-run").contains(dom.controlAfterLabel("Pipe output")),
    true,
  );
  assert.equal(
    allElements(nodeInspectorPanel("node-tabpanel-edges")).some(
      (element) => element.tagName === "BUTTON" && textOf(element) === "Add edge",
    ),
    true,
  );
  assert.equal(
    nodeInspectorPanel("node-tabpanel-general").contains(dom.controlAfterLabel("Command")),
    false,
  );
  await dom.keyDown(nodeInspectorTabs[0], "ArrowRight");
  assert.equal(nodeInspectorTabs[1].getAttribute("aria-selected"), "true");
  assert.equal(nodeInspectorTabs[1].getAttribute("tabindex"), "0");
  assert.equal(document.activeElement, nodeInspectorTabs[1]);
  assert.equal(nodeInspectorPanel("node-tabpanel-general").getAttribute("hidden"), "");
  assert.equal(nodeInspectorPanel("node-tabpanel-action").getAttribute("hidden"), null);
  assert.equal(
    allElements(dom.container).some(
      (element) => element.tagName === "BUTTON" && element.contains(dom.byText("Node inspector")),
    ),
    false,
  );
  assert.match(dom.text(), /operation\.commandoptional/);
  assert.match(dom.text(), /trigger\.name from workflow\.trigger/);

  await dom.click(dom.byTitle("Hide node inspector"));
  await openWorkflowSettingsFromMenu(dom);
  assert.ok(headingByText(dom, "Workflow settings"));
  assert.doesNotMatch(
    dom.ancestor(dom.byText("Initial command"), "ARTICLE").getAttribute("class"),
    /border-indigo-500|ring-indigo-100/,
  );

  await dom.pointer(dom.ancestor(dom.byText("Initial command"), "ARTICLE"), "onPointerDown");
  await dom.flush();
  await dom.change(dom.controlAfterLabel("Command"), "echo edited");
  assert.equal(changes.at(-1).nodes[0].operation.command, "echo edited");

  await dom.click(dom.byTitle("Choose working directory"));
  await dom.flush();
  await dom.click(dom.ancestor(dom.byText("repo"), "BUTTON"));
  await dom.flush();
  await dom.click(dom.byText("Choose current folder"));
  assert.equal(changes.at(-1).nodes[0].operation.working_dir, "/workspace/repo");

  const nodeCard = dom.ancestor(dom.byText("Initial command"), "ARTICLE");
  const initialViewportScale = Number(
    nodeCard.parentNode.style.transform.match(/scale\(([-\d.]+)\)/)?.[1],
  );
  await dom.pointer(nodeCard, "onPointerDown", { clientX: 10, clientY: 10, pointerId: 7 });
  await dom.pointer(nodeCard, "onPointerMove", { clientX: 35, clientY: 45, movementX: 25, movementY: 35, pointerId: 7 });
  await dom.pointer(nodeCard, "onPointerUp", { clientX: 35, clientY: 45, pointerId: 7 });
  assert.equal(changes.at(-1).nodes[0].x, 25 / initialViewportScale);
  assert.equal(changes.at(-1).nodes[0].y, 35 / initialViewportScale);

  await dom.click(dom.byText("Add edge"));
  await dom.change(dom.selectWithOption("node-1"), "node-1");
  assert.equal(changes.at(-1).edges[0].from, "step");
  assert.equal(changes.at(-1).edges[0].to, "node-1");

  await dom.pointer(nodeCard, "onPointerDown", { button: 2, clientX: 80, clientY: 90, pointerId: 8 });
  await dom.pointer(nodeCard, "onContextMenu", { button: 2, clientX: 80, clientY: 90 });
  await dom.click(dom.byText("Duplicate node"));
  assert.equal(changes.at(-1).nodes.length, 3);
  assert.equal(changes.at(-1).nodes.at(-1).label, "Initial command copy");
  assert.equal(changes.at(-1).nodes.at(-1).x, 25 / initialViewportScale + 28);
  assert.equal(changes.at(-1).nodes.at(-1).y, 35 / initialViewportScale + 28);

  await dom.pointer(
    dom.ancestor(dom.byText("Initial command copy"), "ARTICLE"),
    "onContextMenu",
    { button: 2, clientX: 90, clientY: 100 },
  );
  await dom.click(dom.byText("Rename node"));
  await dom.change(dom.controlAfterLabel("Node label"), "Renamed command");
  await dom.pointer(dom.ancestor(dom.byTitle("Confirm node rename"), "FORM"), "onSubmit");
  assert.equal(changes.at(-1).nodes.at(-1).label, "Renamed command");

  await dom.pointer(
    dom.ancestor(dom.byText("Renamed command"), "ARTICLE"),
    "onContextMenu",
    { button: 2, clientX: 90, clientY: 100 },
  );
  await dom.click(dom.byText("Delete node"));
  assert.equal(changes.at(-1).nodes.some((node) => node.label === "Renamed command"), false);

  await dom.pointer(dom.ancestor(dom.byText("Initial command"), "ARTICLE"), "onContextMenu", {
    button: 2,
    clientX: 80,
    clientY: 90,
  });
  await dom.click(dom.byText("Delete node"));
  assert.equal(changes.at(-1).nodes.some((node) => node.id === "step"), false);
  assert.deepEqual(changes.at(-1).edges, []);

  await dom.unmount();
});

test("graph outline supports the complete keyboard node and edge editing flow", async () => {
  let workflow = {
    ...workflowFixture({ id: "keyboard-graph", label: "Collect input" }),
    nodes: [
      {
        id: "collect",
        type: "bash_command",
        label: "Collect input",
        x: 0,
        y: 0,
        operation: { type: "bash_command", command: "echo input" },
      },
      {
        id: "review",
        type: "agent",
        label: "Review input",
        x: 320,
        y: 0,
        operation: { type: "agent", agent_id: "reviewer", prompt: "Review" },
      },
    ],
    edges: [],
  };
  const changes = [];
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      workflow,
      onWorkflowChange(nextWorkflow) {
        workflow = nextWorkflow;
        changes.push(nextWorkflow);
      },
    }),
    createFetchMock([]),
  );

  await dom.click(dom.byTitle("Map"));
  const outline = dom.byLabel("Graph outline");
  const nodeButtons = () => allElements(outline).filter(
    (element) => element.tagName === "BUTTON" && element.getAttribute("aria-label")?.includes("status"),
  );
  assert.equal(nodeButtons().length, 2);
  assert.match(nodeButtons()[0].getAttribute("aria-label"), /0 incoming; 0 outgoing, valid/);

  await dom.focus(nodeButtons()[0]);
  assert.equal(nodeButtons()[0].getAttribute("aria-current"), "true");
  await dom.keyDown(nodeButtons()[0], "c");
  assert.match(dom.text(), /Connecting from Collect input/);
  await dom.keyDown(nodeButtons()[1], "Enter");
  assert.equal(changes.at(-1).edges[0].from, "collect");
  assert.equal(changes.at(-1).edges[0].to, "review");

  const edgeButton = allElements(outline).find(
    (element) => element.tagName === "BUTTON" && element.getAttribute("aria-label")?.startsWith("Collect input to Review input"),
  );
  assert.ok(edgeButton);
  assert.match(edgeButton.getAttribute("aria-label"), /condition always, valid/);
  assert.equal(document.activeElement, edgeButton);
  assert.equal(edgeButton.getAttribute("aria-current"), "true");
  await dom.keyDown(document.activeElement, "Delete");
  assert.deepEqual(changes.at(-1).edges, []);
  assert.equal(document.activeElement, nodeButtons()[0]);
  assert.equal(nodeButtons()[0].getAttribute("aria-current"), "true");

  await dom.keyDown(document.activeElement, "d", { ctrlKey: true });
  assert.equal(changes.at(-1).nodes.length, 3);
  assert.equal(changes.at(-1).nodes.at(-1).label, "Collect input copy");

  const duplicatedButton = nodeButtons().find((button) =>
    button.getAttribute("aria-label")?.startsWith("Collect input copy"),
  );
  assert.equal(document.activeElement, duplicatedButton);
  assert.equal(duplicatedButton.getAttribute("aria-current"), "true");
  await dom.keyDown(document.activeElement, "Delete");
  assert.equal(changes.at(-1).nodes.some((node) => node.label === "Collect input copy"), false);
  assert.equal(document.activeElement.getAttribute("aria-label")?.startsWith("Review input,"), true);
  assert.equal(document.activeElement.getAttribute("aria-current"), "true");

  await dom.unmount();
});

test("edge inspector displays and edits structured-output field relationships", async () => {
  let workflow = {
    ...workflowFixture({ id: "structured-edge", label: "Analyze request" }),
    nodes: [
      {
        id: "analyze",
        type: "agent",
        label: "Analyze request",
        x: 0,
        y: 0,
        operation: { type: "agent", agent_id: "analyzer", prompt: "Analyze" },
      },
      {
        id: "format-high",
        type: "bash_command",
        label: "Format high priority",
        x: 320,
        y: 0,
        operation: { type: "bash_command", command: "printf HIGH" },
      },
    ],
    edges: [
      {
        id: "analyze-format-high",
        from: "analyze",
        to: "format-high",
        condition: "output_field",
        field: "priority",
        operator: "equals",
        value: "high",
        label: 'priority equals "high"',
      },
    ],
  };
  const changes = [];
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      workflow,
      onWorkflowChange(nextWorkflow) {
        workflow = nextWorkflow;
        changes.push(nextWorkflow);
      },
    }),
    createFetchMock([]),
  );

  await dom.click(dom.byTitle("Map"));
  const edgeButton = allElements(dom.byLabel("Graph outline")).find(
    (element) => element.tagName === "BUTTON"
      && element.getAttribute("aria-label")?.startsWith("Analyze request to Format high priority"),
  );
  assert.ok(edgeButton);
  await dom.click(edgeButton);

  const edgeInspector = dom.ancestor(dom.byText("Edge inspector"), "SECTION");
  const inspectorControl = (labelText) => {
    const label = allElements(edgeInspector).find(
      (element) => element.tagName === "LABEL" && textOf(element).includes(labelText),
    );
    assert.ok(label, `Unable to find edge inspector label: ${labelText}`);
    const control = allElements(label).find((element) =>
      ["INPUT", "SELECT", "TEXTAREA"].includes(element.tagName),
    );
    assert.ok(control, `Unable to find edge inspector control: ${labelText}`);
    return control;
  };
  const selectedValue = (select) =>
    [...select.options].find((option) => option.selected)?.value ?? "";

  const typeControl = inspectorControl("Type");
  assert.deepEqual(
    [...typeControl.options].map((option) => option.value),
    ["always", "on_success", "on_failure", "output_matches", "output_field", "after_loop"],
  );
  assert.equal(selectedValue(typeControl), "output_field");
  assert.equal(inspectorControl("Field").value, "priority");
  assert.equal(selectedValue(inspectorControl("Operator")), "equals");
  assert.equal(inspectorControl("Comparison value (JSON)").value, '"high"');

  await dom.change(inspectorControl("Field"), "result.priority");
  assert.equal(changes.at(-1).edges[0].field, "result.priority");
  assert.equal(changes.at(-1).edges[0].label, 'result.priority equals "high"');

  await dom.change(inspectorControl("Operator"), "exists");
  assert.equal(changes.at(-1).edges[0].operator, "exists");
  assert.equal(changes.at(-1).edges[0].label, "result.priority exists");
  assert.doesNotMatch(dom.text(), /Comparison value \(JSON\)/);

  await dom.unmount();
});

test("START nodes open the same tabbed node inspector as every other node type", async () => {
  const workflow = {
    ...workflowFixture({ id: "start-inspector" }),
    nodes: [
      {
        id: "start",
        type: "start",
        label: "START",
        x: 0,
        y: 0,
        operation: { type: "start" },
      },
    ],
  };
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, { workflow, onWorkflowChange() {} }),
    createFetchMock([]),
  );

  await dom.pointer(dom.ancestor(dom.byText("START"), "ARTICLE"), "onPointerDown");
  await dom.flush();

  assert.equal(dom.byText("Node inspector").tagName, "H2");
  assert.equal(dom.byLabel("Node inspector sections").getAttribute("role"), "tablist");
  await dom.click(dom.byText("Action"));
  assert.match(dom.text(), /This node does no work/);
  const actionPanel = allElements(dom.container).find(
    (element) => element.getAttribute("id") === "node-tabpanel-action",
  );
  assert.equal(actionPanel.getAttribute("tabindex"), "0");
  await dom.focus(actionPanel);
  assert.equal(document.activeElement, actionPanel);

  await dom.unmount();
});

test("pane separators expose values and support arrow, boundary, and reset keys", async () => {
  const canvasDom = await mountReact(
    React.createElement(DagCanvasHarness, {
      workflow: workflowFixture({ id: "resizers" }),
      onWorkflowChange() {},
    }),
    createFetchMock([]),
  );
  await openWorkflowSettingsFromMenu(canvasDom);
  const inspectorResizer = canvasDom.byLabel("Resize workflow settings and node inspector");
  assert.equal(inspectorResizer.getAttribute("role"), "separator");
  assert.equal(inspectorResizer.getAttribute("aria-orientation"), "vertical");
  assert.equal(inspectorResizer.getAttribute("aria-valuemin"), "280");
  assert.equal(inspectorResizer.getAttribute("aria-valuemax"), "520");
  assert.equal(inspectorResizer.getAttribute("aria-valuenow"), "340");
  await canvasDom.keyDown(inspectorResizer, "ArrowRight");
  assert.equal(inspectorResizer.getAttribute("aria-valuenow"), "350");
  await canvasDom.keyDown(inspectorResizer, "End");
  assert.equal(inspectorResizer.getAttribute("aria-valuenow"), "520");
  await canvasDom.keyDown(inspectorResizer, "Enter");
  assert.equal(inspectorResizer.getAttribute("aria-valuenow"), "340");

  await canvasDom.unmount();

  const appDom = await mountReact(
    React.createElement(appModule.default),
    createFetchMock([
      jsonResponse("/api/workflows", workflowsPayload([workflowFixture()])),
    ]),
  );
  await appDom.flush();
  await appDom.click(appDom.byLabel("Expand bottom panel"));
  const bottomPanelResizer = appDom.byLabel("Resize bottom panel");
  assert.equal(bottomPanelResizer.getAttribute("aria-orientation"), "horizontal");
  await appDom.keyDown(bottomPanelResizer, "ArrowUp");
  assert.equal(bottomPanelResizer.getAttribute("aria-valuenow"), "310");
  await appDom.keyDown(bottomPanelResizer, "Home");
  assert.equal(bottomPanelResizer.getAttribute("aria-valuenow"), "140");
  await appDom.keyDown(bottomPanelResizer, "Enter");
  assert.equal(bottomPanelResizer.getAttribute("aria-valuenow"), "300");
  const workflowsResizer = appDom.byLabel("Resize workflows pane");
  const chatResizer = appDom.byLabel("Resize chat pane");
  assert.equal(workflowsResizer.getAttribute("aria-valuenow"), "272");
  await appDom.keyDown(workflowsResizer, "ArrowRight", { shiftKey: true });
  assert.equal(workflowsResizer.getAttribute("aria-valuenow"), "312");
  await appDom.keyDown(workflowsResizer, "Enter");
  assert.equal(workflowsResizer.getAttribute("aria-valuenow"), "272");
  assert.equal(chatResizer.getAttribute("aria-valuemin"), "300");
  await appDom.keyDown(chatResizer, "ArrowLeft");
  assert.equal(chatResizer.getAttribute("aria-valuenow"), "370");
  await appDom.unmount();
});

test("bottom panel state and project trust stay global across workflow switches", async () => {
  const trustedRoots = [];
  const first = { ...workflowFixture({ id: "first", name: "First" }), projectRoot: "/repos/first" };
  const second = { ...workflowFixture({ id: "second", name: "Second" }), projectRoot: "/repos/second" };
  const dom = await mountReact(
    React.createElement(appModule.default),
    createFetchMock([
      jsonResponse("/api/workflows", workflowsPayload([first, second])),
      jsonResponse("/api/projects/open", { workflows: [second] }, { method: "POST" }),
    ]),
    {
      storage: { "gofer.recentProjects": JSON.stringify(["/repos/first", "/repos/second"]) },
      desktop: {
        workspace: {
          trustProjectRoot: async (projectRoot) => {
            trustedRoots.push(projectRoot);
          },
        },
      },
    },
  );
  await dom.flush();

  assert.deepEqual(new Set(trustedRoots), new Set(["/repos/first", "/repos/second"]));
  const panelHeader = dom.byLabel("Bottom panel views");
  await dom.click(panelHeader);
  assert.ok(dom.byLabel("Collapse bottom panel"));
  await dom.click(panelHeader);
  assert.ok(dom.byLabel("Expand bottom panel"));
  await dom.click(dom.byText("Problems"));
  assert.equal(dom.byText("Problems").getAttribute("aria-selected"), "true");
  await dom.click(dom.byText("Problems"));
  assert.ok(dom.byLabel("Expand bottom panel"));
  await dom.click(dom.byText("Problems"));
  await dom.dispatchWindow("keydown", {
    code: "Backquote",
    ctrlKey: true,
    key: "Dead",
  });
  assert.ok(dom.byLabel("Expand bottom panel"));
  await dom.dispatchWindow("keydown", {
    code: "Backquote",
    ctrlKey: true,
    key: "Dead",
  });
  assert.ok(dom.byLabel("Collapse bottom panel"));
  assert.equal(dom.byText("Problems").getAttribute("aria-selected"), "true");
  const resizer = dom.byLabel("Resize bottom panel");
  await dom.keyDown(resizer, "ArrowUp");
  assert.equal(resizer.getAttribute("aria-valuenow"), "310");

  await dom.click(dom.byLabel("Recent projects"));
  await dom.click(dom.byTitle("/repos/second"));
  await dom.click(dom.ancestor(
    dom.byText("Second"),
    (node) => node.getAttribute?.("role") === "button",
  ));
  await dom.flush();

  assert.ok(dom.byLabel("Collapse bottom panel"));
  assert.equal(dom.byLabel("Resize bottom panel").getAttribute("aria-valuenow"), "310");
  await dom.unmount();
});

test("workspace contains bottom panel height transitions without page overflow", async () => {
  const dom = await mountReact(
    React.createElement(appModule.default),
    createFetchMock([
      jsonResponse("/api/workflows", workflowsPayload([workflowFixture()])),
    ]),
  );
  await dom.flush();

  const workspace = dom.byLabel("Bottom panel").parentNode;
  const appShell = workspace.parentNode;
  const workflowPane = dom.ancestor(dom.byLabel("Resize workflows pane"), "ASIDE");
  const assistantPane = dom.ancestor(dom.byLabel("Resize chat pane"), "ASIDE");
  assert.match(workspace.getAttribute("class"), /min-h-0/);
  assert.match(workspace.getAttribute("class"), /overflow-hidden/);
  assert.match(appShell.getAttribute("class"), /h-full/);
  assert.match(appShell.getAttribute("class"), /min-h-0/);
  assert.match(appShell.getAttribute("class"), /min-w-0/);
  assert.match(appShell.getAttribute("class"), /overflow-hidden/);
  assert.doesNotMatch(appShell.getAttribute("class"), /min-h-\[720px\]|min-w-\[1180px\]/);
  assert.match(workflowPane.getAttribute("class"), /min-h-0/);
  assert.match(workflowPane.getAttribute("class"), /overflow-hidden/);
  assert.match(assistantPane.getAttribute("class"), /min-h-0/);
  assert.match(assistantPane.getAttribute("class"), /overflow-hidden/);
  assert.match(
    allElements(assistantPane).find(
      (element) => element.getAttribute?.("data-chat-scroll") === "true",
    ).getAttribute("class"),
    /min-h-0/,
  );

  const globalStyles = fs.readFileSync(path.join(frontendRoot, "src/styles/index.css"), "utf8");
  assert.match(
    globalStyles,
    /html,\s*body,\s*#root\s*{[^}]*height:\s*100%;[^}]*min-height:\s*0;[^}]*overflow:\s*hidden;/s,
  );

  await dom.unmount();
});

test("terminal sessions that finish opening after tab cleanup are closed", async () => {
  let canceledCreateCount = 0;
  const canceledLifecycle = bottomPanelModule.createDisposableTerminalSession(
    {
      close: async () => {},
      create: async () => {
        canceledCreateCount += 1;
        return { id: "should-not-open", shell: "bash" };
      },
    },
    { cwd: "/workspace" },
  );
  canceledLifecycle.dispose();
  await canceledLifecycle.settled;
  assert.equal(canceledCreateCount, 0);

  const pendingSession = createDeferred();
  const closedSessionIds = [];
  let readySession = null;
  const lifecycle = bottomPanelModule.createDisposableTerminalSession(
    {
      close: async (sessionId) => {
        closedSessionIds.push(sessionId);
      },
      create: async () => pendingSession.promise,
    },
    { cwd: "/workspace" },
    { onReady: (session) => { readySession = session; } },
  );

  await Promise.resolve();
  lifecycle.dispose();
  pendingSession.resolve({ id: "strict-mode-orphan", shell: "bash" });
  await lifecycle.settled;

  assert.equal(readySession, null);
  assert.deepEqual(closedSessionIds, ["strict-mode-orphan"]);
});

test("terminal tab shortcuts require the visible terminal and ignore key repeat", () => {
  const shortcut = (key, options = {}, event = {}) => (
    bottomPanelModule.terminalWorkspaceShortcutAction(
      { altKey: false, ctrlKey: true, key, metaKey: false, repeat: false, shiftKey: false, ...event },
      { active: true, activeKey: "terminal-1", renaming: false, ...options },
    )
  );

  assert.equal(shortcut("t"), "new");
  assert.equal(shortcut("w"), "close");
  assert.equal(shortcut("t", { active: false }), null);
  assert.equal(shortcut("w", { active: false }), null);
  assert.equal(shortcut("t", {}, { repeat: true }), null);
  assert.equal(shortcut("w", {}, { repeat: true }), null);
  assert.equal(shortcut("w", { activeKey: null }), null);
  assert.equal(shortcut("t", { renaming: true }), null);
  assert.equal(bottomPanelModule.bottomPanelTabForShortcut("timeline", false), "terminal");
  assert.equal(bottomPanelModule.bottomPanelTabForShortcut("timeline", true), "timeline");
  assert.equal(bottomPanelModule.bottomPanelTabForShortcut("problems", true), "problems");
});

test("terminal clipboard shortcuts copy selections and leave paste to xterm", async () => {
  const shortcut = (key, event = {}) => bottomPanelModule.terminalClipboardShortcutAction({
    altKey: false,
    ctrlKey: true,
    key,
    metaKey: false,
    shiftKey: true,
    type: "keydown",
    ...event,
  });
  assert.equal(shortcut("c"), "copy");
  assert.equal(shortcut("V"), "paste");
  assert.equal(shortcut("c", { shiftKey: false }), null);
  assert.equal(shortcut("v", { altKey: true }), null);

  const copied = [];
  assert.equal(await bottomPanelModule.copyTerminalSelection(
    { getSelection: () => "selected output" },
    { writeText: async (text) => copied.push(text) },
  ), true);
  assert.deepEqual(copied, ["selected output"]);
  assert.equal(await bottomPanelModule.copyTerminalSelection(
    { getSelection: () => "" },
    { writeText: async () => { throw new Error("should not write"); } },
  ), false);

  const terminal = { getSelection: () => "" };
  assert.equal(bottomPanelModule.handleTerminalClipboardShortcut({
    altKey: false,
    ctrlKey: true,
    key: "v",
    metaKey: false,
    shiftKey: true,
    type: "keydown",
  }, terminal), false);
  assert.equal(bottomPanelModule.handleTerminalClipboardShortcut({
    altKey: false,
    ctrlKey: false,
    key: "v",
    metaKey: false,
    shiftKey: false,
    type: "keydown",
  }, terminal), null);
});

test("Ctrl+Backspace erases one word in terminal and code editors", () => {
  const terminalEvent = (event = {}) => ({
    altKey: false,
    ctrlKey: true,
    key: "Backspace",
    metaKey: false,
    shiftKey: false,
    type: "keydown",
    ...event,
  });

  assert.equal(bottomPanelModule.terminalWordEraseInput(terminalEvent()), "\x17");
  assert.equal(bottomPanelModule.terminalWordEraseInput(terminalEvent({ ctrlKey: false })), null);
  assert.equal(bottomPanelModule.terminalWordEraseInput(terminalEvent({ shiftKey: true })), null);
  assert.equal(bottomPanelModule.terminalWordEraseInput(terminalEvent({ type: "keyup" })), null);

  const monacoSource = fs.readFileSync(path.join(frontendRoot, "src/lib/monaco.js"), "utf8");
  assert.match(
    monacoSource,
    /contrib\/wordOperations\/browser\/wordOperations/,
    "Monaco must load its Ctrl+Backspace word-delete contribution",
  );
});

test("terminal tabs stay grouped by the project captured when they were opened", () => {
  const groups = bottomPanelModule.groupTerminalTabsByProject([
    {
      cwd: "/tmp/a-shell-moved-here",
      key: "api-1",
      label: "bash 1",
      projectPath: "/repos/customer-api",
    },
    {
      cwd: "/repos/web-client",
      key: "web-1",
      label: "bash 2",
      projectPath: "/repos/web-client",
    },
    {
      cwd: "/repos/customer-api",
      key: "api-2",
      label: "bash 3",
      projectPath: "/repos/customer-api",
    },
  ]);

  assert.deepEqual(
    groups.map((group) => ({
      keys: group.items.map((tab) => tab.key),
      name: group.name,
      projectPath: group.projectPath,
    })),
    [
      {
        keys: ["api-1", "api-2"],
        name: "customer-api",
        projectPath: "/repos/customer-api",
      },
      {
        keys: ["web-1"],
        name: "web-client",
        projectPath: "/repos/web-client",
      },
    ],
  );
  assert.equal(bottomPanelModule.terminalDirectoryFromOsc("P;Cwd=/repos/gofer-flow"), "/repos/gofer-flow");
  assert.equal(bottomPanelModule.terminalDirectoryFromOsc("P;Cwd=C:\\repos\\gofer-flow"), "C:\\repos\\gofer-flow");
  assert.equal(bottomPanelModule.terminalDirectoryFromOsc("P;Other=value"), "");
  assert.equal(bottomPanelModule.terminalDirectoryFromOsc("P;Cwd=/tmp\nspoofed"), "");
});

test("terminal groups support moves, empty custom groups, renames, and recursive deletion", () => {
  const tabs = [
    {
      cwd: "/repos/customer-api",
      key: "api-1",
      label: "bash 1",
      projectPath: "/repos/customer-api",
    },
    {
      cwd: "/repos/web-client",
      key: "web-1",
      label: "bash 2",
      projectPath: "/repos/web-client",
    },
  ];
  const definitions = [{
    id: "custom:1",
    keepEmpty: true,
    name: bottomPanelModule.terminalGroupName(1),
    projectPath: "/repos/customer-api",
  }];

  assert.equal(definitions[0].name, "Group 1");
  assert.deepEqual(
    bottomPanelModule.groupTerminalTabsByProject(tabs, definitions).map((group) => ({
      count: group.items.length,
      id: group.id,
      name: group.name,
    })),
    [
      { count: 1, id: "project:/repos/customer-api", name: "customer-api" },
      { count: 0, id: "custom:1", name: "Group 1" },
      { count: 1, id: "project:/repos/web-client", name: "web-client" },
    ],
  );

  const moved = bottomPanelModule.moveTerminalTabToGroup(tabs, "api-1", "custom:1");
  assert.equal(moved[0].groupId, "custom:1");
  assert.equal(moved[0].cwd, "/repos/customer-api");
  assert.equal(moved[0].projectPath, "/repos/customer-api");
  assert.deepEqual(
    bottomPanelModule.groupTerminalTabsByProject(moved, [{ ...definitions[0], keepEmpty: false }])
      .map((group) => group.name),
    ["Group 1", "web-client"],
  );

  const renamed = bottomPanelModule.upsertTerminalGroupDefinition(definitions, {
    ...definitions[0],
    keepEmpty: false,
    name: "Deploy shells",
  });
  assert.equal(renamed[0].name, "Deploy shells");
  assert.deepEqual(
    bottomPanelModule.terminalTabsAfterDeletingGroup(moved, "custom:1").map((tab) => tab.key),
    ["web-1"],
  );
});

test("terminal initialization creates only one tab under repeated effects", () => {
  assert.equal(bottomPanelModule.shouldCreateInitialTerminal(true, 0, false), true);
  assert.equal(bottomPanelModule.shouldCreateInitialTerminal(true, 0, true), false);
  assert.equal(bottomPanelModule.shouldCreateInitialTerminal(true, 1, false), false);
  assert.equal(bottomPanelModule.shouldCreateInitialTerminal(false, 0, false), false);
});

test("inspector parsed fields keep drafts stable and commit or restore consistently", async () => {
  const changes = { keyValue: [], list: [], number: [], path: [] };
  const dom = await mountReact(
    React.createElement(InspectorDraftHarness, { changes }),
    createFetchMock([]),
  );

  const number = dom.controlAfterLabel("Draft number");
  await dom.focus(number);
  await dom.change(number, "");
  assert.equal(number.value, "");
  assert.deepEqual(changes.number, []);
  await dom.change(number, "-");
  assert.equal(number.value, "-");
  assert.match(dom.text(), /Enter a complete number/);
  await dom.change(number, "-2.5");
  assert.deepEqual(changes.number, []);
  await dom.blur(number);
  assert.deepEqual(changes.number, [-2.5]);

  await dom.focus(number);
  await dom.change(number, "4.");
  assert.equal(number.value, "4.");
  await dom.keyDown(number, "Escape");
  assert.equal(number.value, "-2.5");
  assert.deepEqual(changes.number, [-2.5]);
  await dom.change(number, "3.75");
  await dom.keyDown(number, "Enter");
  assert.deepEqual(changes.number, [-2.5, 3.75]);
  assert.doesNotMatch(dom.text(), /changed elsewhere.*draft is preserved/i);

  await dom.change(number, "-");
  await dom.click(dom.byText("Update number externally"));
  assert.equal(number.value, "-");
  assert.match(dom.text(), /Enter a complete number/);
  assert.match(dom.text(), /changed elsewhere.*draft is preserved/i);
  await dom.keyDown(number, "Escape");
  assert.equal(number.value, "11");

  const list = dom.controlAfterLabel("Draft list");
  await dom.focus(list);
  await dom.change(list, "alpha, beta,");
  assert.equal(list.value, "alpha, beta,");
  assert.deepEqual(changes.list, []);
  await dom.blur(list);
  assert.deepEqual(changes.list, [["alpha", "beta"]]);
  assert.equal(list.value, "alpha, beta");
  await dom.focus(list);
  await dom.change(list, "gamma, delta");
  await dom.keyDown(list, "Enter");
  assert.deepEqual(changes.list.at(-1), ["gamma", "delta"]);
  await dom.change(list, "temporary,");
  await dom.keyDown(list, "Escape");
  assert.equal(list.value, "gamma, delta");
  assert.deepEqual(changes.list, [["alpha", "beta"], ["gamma", "delta"]]);

  const keyValue = dom.controlAfterLabel("Draft key/value");
  await dom.focus(keyValue);
  await dom.change(keyValue, "TOKEN");
  assert.equal(keyValue.value, "TOKEN");
  assert.match(dom.text(), /Line 1 needs an “=”/);
  await dom.blur(keyValue);
  assert.deepEqual(changes.keyValue, []);
  assert.equal(keyValue.value, "TOKEN");
  await dom.focus(keyValue);
  await dom.change(keyValue, "TOKEN=secret\nMODE=");
  await dom.keyDown(keyValue, "Enter");
  assert.deepEqual(changes.keyValue, [{ TOKEN: "secret", MODE: "" }]);
  await dom.change(keyValue, "BROKEN");
  await dom.keyDown(keyValue, "Escape");
  assert.equal(keyValue.value, "TOKEN=secret\nMODE=");

  const pathInput = dom.controlAfterLabel("Draft path");
  assert.equal(pathInput.value, "scripts/run.sh");
  assert.equal(pathInput.getAttribute("title"), "/workspace/scripts/run.sh");
  await dom.focus(pathInput);
  await dom.change(pathInput, "");
  await dom.change(pathInput, "scripts/next.sh");
  assert.equal(pathInput.value, "scripts/next.sh");
  assert.deepEqual(changes.path, []);
  await dom.blur(pathInput);
  assert.deepEqual(changes.path, ["scripts/next.sh"]);
  assert.equal(pathInput.value, "scripts/next.sh");
  await dom.focus(pathInput);
  await dom.change(pathInput, "scripts/entered.sh");
  await dom.keyDown(pathInput, "Enter");
  assert.deepEqual(changes.path, ["scripts/next.sh", "scripts/entered.sh"]);
  assert.equal(pathInput.value, "scripts/entered.sh");
  assert.doesNotMatch(dom.text(), /changed elsewhere.*draft is preserved/i);
  await dom.change(pathInput, "scripts/cancelled.sh");
  await dom.keyDown(pathInput, "Escape");
  assert.equal(pathInput.value, "scripts/entered.sh");
  assert.deepEqual(changes.path, ["scripts/next.sh", "scripts/entered.sh"]);

  await dom.unmount();
});

test("DagCanvas renders pending approvals as a centered graph overlay", async () => {
  const workflow = {
    ...workflowFixture({ id: "approval-canvas", name: "Approval Canvas" }),
    nodes: [
      {
        id: "approve",
        type: "approval_gate",
        label: "Review deployment",
        x: 0,
        y: 0,
        operation: { type: "approval_gate", message: "Approve deployment?" },
      },
    ],
  };
  const decisions = [];
  const approval = {
    workflowId: "approval-canvas",
    runId: "run.log",
    nodeId: "approve",
    message: "Approve deployment?",
    status: "pending",
    approvers: ["ops"],
    requestedAt: "2026-06-25T12:00:00-04:00",
    timeoutSeconds: null,
    timeoutDecision: "timeout",
    decision: null,
  };
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      approvalState: { approvals: [approval], error: "", loading: false },
      dataDir: "/workspace",
      workflow,
      onDecideApproval(nextApproval, decision, notes, approver) {
        decisions.push({ approval: nextApproval, decision, notes, approver });
      },
      onWorkflowChange() {},
    }),
    createFetchMock([]),
  );

  await dom.flush();
  const approvalDialog = allElements(dom.container).find(
    (element) => element.getAttribute?.("role") === "dialog",
  );
  assert.ok(approvalDialog);
  assert.equal(approvalDialog.getAttribute("aria-modal"), "true");
  assert.ok(dom.byText("Approval Required"));
  assert.ok(dom.byText("Review deployment"));
  const approvalMessage = allElements(dom.container).find(
    (element) =>
      element.textContent === "Approve deployment?" &&
      /\btext-ink\b/.test(element.getAttribute?.("class") ?? ""),
  );
  assert.ok(approvalMessage);
  assert.doesNotMatch(approvalMessage.getAttribute("class"), /\btext-slate-800\b/);

  await dom.change(dom.controlAfterLabel("Notes"), "ship it");
  await dom.click(dom.byTitle("Approve pending approval"));

  assert.equal(decisions.length, 1);
  assert.equal(decisions[0].approval, approval);
  assert.equal(decisions[0].decision, "approved");
  assert.equal(decisions[0].notes, "ship it");
  assert.equal(decisions[0].approver, "ops");

  await dom.unmount();
});

test("DagCanvas renders inline agent health warnings in the inspector", async () => {
  const workflow = {
    ...workflowFixture({ id: "agent-health", name: "Agent Health", label: "Review" }),
    agents: {
      reviewer: {
        subscription: "codex",
        working_dir: ".",
      },
    },
    healthErrors: [
      {
        id: "workflow.provider_cli",
        severity: "error",
        subject: "codex",
        message: "Workflow requires provider CLI 'codex', but it is not on PATH.",
      },
    ],
    nodes: [
      {
        id: "review",
        type: "agent",
        label: "Review",
        x: 0,
        y: 0,
        operation: {
          type: "agent",
          agent_id: "reviewer",
          working_dir: ".",
        },
      },
    ],
  };

  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      dataDir: "/workspace",
      workflow,
      onWorkflowChange() {},
    }),
    createFetchMock([]),
  );

  await dom.flush();
  await dom.pointer(dom.ancestor(dom.byText("Review"), "ARTICLE"), "onPointerDown");
  await dom.flush();

  assert.match(dom.text(), /Agent config/);
  assert.match(dom.text(), /Workflow requires provider CLI 'codex'/);

  await dom.unmount();
});

test("DagCanvas edits named structured-output schemas without mounting an eager textarea", async () => {
  const changes = [];
  const workflow = workflowFixture({ id: "schemas", name: "Schemas" });
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      dataDir: "/workspace",
      workflow,
      onWorkflowChange(nextWorkflow) {
        changes.push(nextWorkflow);
      },
    }),
    createFetchMock([]),
  );

  await dom.flush();
  assert.equal(
    allElements(dom.container).filter((element) => element.tagName === "TEXTAREA").length,
    0,
  );
  await dom.click(dom.byText("Variables"));
  await dom.flush();
  const schemaEditor = dom.controlAfterLabel("Named output schemas (JSON object)");
  await dom.focus(schemaEditor);
  await dom.change(schemaEditor, "");
  assert.equal(schemaEditor.value, "");
  assert.deepEqual(changes, []);
  await dom.change(
    schemaEditor,
    '{"review_result":{"type":"object","properties":{"verdict":{"type":"string"}}}}',
  );
  assert.deepEqual(changes, []);
  await dom.blur(schemaEditor);

  assert.deepEqual(changes.at(-1).outputSchemas, {
    review_result: {
      type: "object",
      properties: { verdict: { type: "string" } },
    },
  });
  await dom.unmount();
});

test("DagCanvas renders structured run timeline and selected node details", async () => {
  const workflow = workflowFixture({ id: "timeline", name: "Timeline", label: "Run command" });
  const logState = {
    loading: false,
    error: "",
    text: "legacy log",
    path: "logs/timeline/run.log",
    runs: [],
    runEvents: [
      {
        nodeId: "step",
        status: "started",
        attempt: 1,
        occurredAt: "2026-01-02T03:04:05Z",
        message: "attempt 1 started",
        fanOutItem: { index: "0" },
      },
      {
        nodeId: "step",
        status: "completed",
        attempt: 1,
        occurredAt: "2026-01-02T03:04:06Z",
        message: "attempt 1 finished success=true exit_code=0",
      },
      {
        nodeId: "step",
        status: "reused",
        occurredAt: "2026-01-02T03:04:07Z",
        message: "reused output from resumed run",
      },
    ],
    runNodes: {
      step: {
        nodeId: "step",
        status: "completed",
        durationSeconds: 0.25,
        exitCode: 0,
        attempts: [
          {
            attempt: 1,
            runNumber: 1,
            durationSeconds: 0.25,
            fanOutItem: { index: "0" },
            inputs: { stdin: "hello" },
            output: "ok",
          },
          {
            attempt: 1,
            runNumber: 2,
            durationSeconds: 0.1,
            fanOutItem: { index: "1" },
            inputs: { stdin: "bad" },
            output: "bad item",
            stderr: "stderr detail",
            prompt: "rendered prompt",
          },
        ],
        data: {
          reused: true,
          message: "agent summary message",
          fanOut: {
            itemCount: 2,
            successCount: 1,
            failureCount: 1,
            items: [
              { index: 0, status: "completed", output: "ok", durationSeconds: 0.25 },
              {
                index: 1,
                status: "failed",
                output: "bad item",
                error: "bad item",
                durationSeconds: 0.1,
                exitCode: 1,
              },
            ],
          },
          edgeDecisions: [
            { from: "step", to: "next", condition: "on_success", matched: true },
          ],
        },
      },
    },
    usageSummary: {
      totals: {
        agent_calls: 2,
        total_tokens: 321,
        estimated_cost: 0.012345,
        agent_time_seconds: 1.5,
      },
      most_expensive_nodes: [
        { node_id: "step", estimated_cost: 0.012345, duration_seconds: 1.5 },
      ],
      slowest_nodes: [
        { node_id: "step", estimated_cost: 0.012345, duration_seconds: 1.5 },
      ],
    },
  };
  const dom = await mountReact(
    React.createElement(React.Fragment, null,
      React.createElement(DagCanvasHarness, {
        dataDir: "/workspace",
        workflow,
        logState,
        onWorkflowChange() {},
      }),
      React.createElement(canvasModule.RunTimelinePanel, {
        embedded: true,
        runEvents: logState.runEvents,
        text: logState.text,
        title: "Workflow log",
        usageSummary: logState.usageSummary,
      }),
    ),
    createFetchMock([]),
  );

  await dom.flush();
  assert.match(dom.text(), /Run timeline/);
  assert.match(dom.text(), /LLM usage/);
  assert.match(dom.text(), /321 tokens/);
  assert.match(dom.text(), /Most expensive: step/);
  assert.match(dom.text(), /completed/);
  await dom.pointer(dom.ancestor(dom.byText("Run command"), "ARTICLE"), "onPointerDown");
  await dom.flush();
  assert.match(dom.text(), /Last run/);
  assert.match(dom.text(), /ReusedYes/);
  assert.ok(dom.byTitle("reused"));
  assert.match(dom.text(), /0\.25s/);
  assert.match(dom.text(), /agent summary message/);
  assert.match(dom.text(), /Fan-out items/);
  assert.match(dom.text(), /1: failed/);
  assert.match(dom.text(), /Iteration 1 - Attempt 1/);
  assert.match(dom.text(), /Outputok/);
  await dom.click(dom.byText("Next"));
  assert.match(dom.text(), /Iteration 2 - Attempt 1/);
  assert.match(dom.text(), /OutputStderrPromptbad item/);
  await dom.click(dom.byTitle("Show Stderr"));
  assert.match(dom.text(), /stderr detail/);
  await dom.click(dom.byTitle("Show Prompt"));
  assert.match(dom.text(), /rendered prompt/);
  assert.match(dom.text(), /step -> next/);

  await dom.unmount();
});

test("DagCanvas run history exposes resume and rerun controls", async () => {
  const resumeCalls = [];
  const workflow = workflowFixture({ id: "history-actions", name: "History actions" });
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      dataDir: "/workspace",
      workflow,
      logState: {
        loading: false,
        error: "",
        text: "failed run",
        path: "logs/history-actions/run-1.log",
        runs: [{ id: "run-1.log", status: "error", startedAt: "2026-01-02T03:04:05Z" }],
        selectedRunId: "run-1.log",
      },
      onResumeRunLog(runId, options) {
        resumeCalls.push({ runId, options });
      },
      onWorkflowChange() {},
    }),
    createFetchMock([]),
  );

  await dom.flush();
  await dom.click(dom.byTitle("More graph actions"));
  await dom.click(dom.byTitle("Select workflow run"));
  assert.match(dom.text(), /Resume/);
  assert.match(dom.text(), /Rerun failed nodes/);
  assert.match(dom.text(), /Rerun from selected node/);

  await dom.click(dom.ancestor(dom.byText("Resume"), "BUTTON"));
  await dom.click(dom.ancestor(dom.byText("Rerun failed nodes"), "BUTTON"));

  await dom.pointer(dom.ancestor(dom.byText("Run command"), "ARTICLE"), "onPointerDown");
  await dom.flush();
  await dom.click(dom.ancestor(dom.byText("Rerun from selected node"), "BUTTON"));

  assert.deepEqual(resumeCalls, [
    { runId: "run-1.log", options: {} },
    { runId: "run-1.log", options: { skipCache: true } },
    { runId: "run-1.log", options: { fromNode: "step" } },
  ]);

  await dom.unmount();
});

test("DagCanvas surfaces webhook trigger state and replay controls", async () => {
  const replayCalls = [];
  const workflow = {
    ...workflowFixture({ id: "hooked", name: "Hooked" }),
    webhooks: {
      github: {
        id: "github",
        enabled: true,
        source: "github",
        fanout_path: "payload.items",
        tokenConfigured: true,
        concurrency_policy: "reject_if_running",
      },
    },
  };
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      dataDir: "/workspace",
      workflow,
      logState: {
        loading: false,
        error: "",
        text: "webhook run",
        path: "logs/hooked/run-1.log",
        runs: [
          {
            id: "run-1.log",
            status: "success",
            startedAt: "2026-01-02T03:04:05Z",
            triggerId: "github",
            triggerType: "webhook",
            hasTriggerReplay: true,
          },
        ],
        selectedRunId: "run-1.log",
      },
      onReplayRunLog(runId, triggerId) {
        replayCalls.push({ runId, triggerId });
      },
      onWorkflowChange() {},
    }),
    createFetchMock([]),
  );

  await dom.click(dom.byText("Triggers"));

  await dom.flush();
  assert.match(dom.text(), /API trigger: github \(github\)/);
  assert.match(dom.text(), /Webhook\/API triggers/);
  assert.match(dom.text(), /Token required/);

  await dom.click(dom.byTitle("More graph actions"));
  await dom.click(dom.byTitle("Select workflow run"));
  await dom.click(dom.ancestor(dom.byText("Replay webhook payload"), "BUTTON"));
  assert.deepEqual(replayCalls, [{ runId: "run-1.log", triggerId: "github" }]);

  await dom.unmount();
});

test("DagCanvas exposes invocation bindings for triggers and child calls", async () => {
  const workflow = {
    ...workflowFixture({ id: "bindings", name: "Bindings" }),
    schedule: { cron_expression: "0 9 * * *", timezone: "UTC", inputs: {} },
    watch: {
      path: "/workspace/inbox",
      glob: "*",
      recursive: false,
      debounce_seconds: 1,
      mode: "batch",
      max_concurrency: 1,
      inputs: {},
    },
    webhooks: {
      default: {
        id: "default",
        enabled: true,
        source: "webhook",
        input_bindings: {},
      },
    },
    nodes: [
      {
        id: "call-workflow",
        label: "Call workflow",
        type: "workflow",
        operation: canvasModule.defaultOperation("workflow"),
        settings: {},
        x: 0,
        y: 0,
      },
      {
        id: "call-subflow",
        label: "Call subflow",
        type: "subflow",
        operation: canvasModule.defaultOperation("subflow"),
        settings: {},
        x: 260,
        y: 0,
      },
    ],
    edges: [],
  };
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      dataDir: "/workspace",
      workflow,
      onWorkflowChange() {},
    }),
    createFetchMock([]),
  );

  await dom.click(dom.byText("Triggers"));

  await dom.flush();
  assert.ok((dom.text().match(/Invocation inputs \(JSON object\)/g) ?? []).length >= 2);
  assert.match(dom.text(), /Input bindings \(JSON object\)/);

  await dom.pointer(dom.ancestor(dom.byText("Call workflow"), "ARTICLE"), "onPointerDown");
  await dom.flush();
  assert.match(dom.text(), /Workflow ID/);
  assert.match(dom.text(), /Input bindings \(JSON object or quoted exact reference\)/);
  assert.match(dom.text(), /called workflow’s immutable inputs/);

  await dom.pointer(dom.ancestor(dom.byText("Call subflow"), "ARTICLE"), "onPointerDown");
  await dom.flush();
  assert.match(dom.text(), /Component ID/);
  assert.match(dom.text(), /Declared outputs \(JSON object\)/);

  await dom.unmount();
});

test("DagCanvas notification editor exposes every delivery channel configuration", async () => {
  const workflow = {
    ...workflowFixture({ id: "notifications", name: "Notifications" }),
    nodes: [
      {
        id: "notify",
        label: "Notify operators",
        type: "notification",
        operation: canvasModule.defaultOperation("notification"),
        settings: {},
        x: 0,
        y: 0,
      },
    ],
  };
  const changes = [];
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      dataDir: "/workspace",
      workflow,
      onWorkflowChange(nextWorkflow) {
        changes.push(nextWorkflow);
      },
    }),
    createFetchMock([]),
  );

  await dom.pointer(dom.ancestor(dom.byText("Notify operators"), "ARTICLE"), "onPointerDown");
  await dom.change(dom.controlAfterLabel("Channel"), "email");
  await dom.flush();
  assert.ok(dom.controlAfterLabel("From address"));
  assert.ok(dom.controlAfterLabel("Recipients"));
  assert.ok(dom.controlAfterLabel("SMTP host"));
  assert.ok(dom.controlAfterLabel("SMTP password or secret reference"));
  assert.ok(dom.controlAfterLabel("Use STARTTLS"));
  assert.ok(dom.controlAfterLabel("Network allowlist"));

  await dom.change(dom.controlAfterLabel("Channel"), "webhook");
  await dom.flush();
  assert.ok(dom.controlAfterLabel("Webhook URL"));
  assert.ok(dom.controlAfterLabel("Headers"));
  assert.ok(dom.controlAfterLabel("Payload (JSON)"));
  assert.equal(changes.at(-1).nodes[0].operation.channel, "webhook");

  await dom.change(dom.controlAfterLabel("Channel"), "__runtime_reference__");
  await dom.flush();
  const channelReference = dom.controlAfterLabel("Channel reference");
  assert.equal(channelReference.value, "{{inputs.value}}");
  await dom.focus(channelReference);
  await dom.change(channelReference, "{{inputs.channel}}");
  await dom.blur(channelReference);
  assert.equal(changes.at(-1).nodes[0].operation.channel, "{{inputs.channel}}");

  await dom.unmount();
});

test("number fields accept exact runtime references only when enabled", () => {
  assert.deepEqual(canvasModule.parseNumberDraft("{{inputs.timeout}}", 0, true), {
    ok: true,
    value: "{{inputs.timeout}}",
  });
  assert.equal(canvasModule.parseNumberDraft("{{inputs.timeout}}", 0).ok, false);
  assert.equal(canvasModule.parseNumberDraft("{{inputs.timeout}} trailing", 0, true).ok, false);
});

test("DagCanvas authors runtime generic fan-out settings", async () => {
  const workflow = {
    ...workflowFixture({ id: "runtime-fields", name: "Runtime fields" }),
    nodes: [
      {
        id: "fan",
        label: "Fan items",
        type: "pass",
        operation: canvasModule.defaultOperation("pass"),
        settings: {},
        x: 0,
        y: 0,
      },
    ],
  };
  const changes = [];
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      dataDir: "/workspace",
      workflow,
      onWorkflowChange(nextWorkflow) {
        changes.push(nextWorkflow);
      },
    }),
    createFetchMock([]),
  );

  await dom.pointer(dom.ancestor(dom.byText("PASS"), "ARTICLE"), "onPointerDown");
  await dom.change(dom.controlAfterLabel("For each"), "{{inputs.items}}");
  await dom.flush();
  const concurrency = dom.controlAfterLabel("Fan-out max concurrency");
  await dom.focus(concurrency);
  await dom.change(concurrency, "{{inputs.workers}}");
  await dom.blur(concurrency);
  await dom.click(dom.byText("Use an exact runtime reference"));
  await dom.flush();
  const failFast = dom.controlAfterLabel("Fan-out fail fast reference");
  await dom.focus(failFast);
  await dom.change(failFast, "{{inputs.stop_early}}");
  await dom.blur(failFast);
  assert.equal(changes.at(-1).nodes[0].settings.forEach, "{{inputs.items}}");
  assert.equal(changes.at(-1).nodes[0].settings.maxConcurrency, "{{inputs.workers}}");
  assert.equal(changes.at(-1).nodes[0].settings.failFast, "{{inputs.stop_early}}");

  await dom.unmount();
});

test("DagCanvas retention controls send configured cleanup settings", async () => {
  const pruneCalls = [];
  const settingsChanges = [];
  const dom = await mountReact(
    React.createElement(canvasModule.RunTimelinePanel, {
      embedded: true,
      retentionSettings: { keepDays: 7, keepFailedDays: 21, keepLast: 50 },
      text: "completed run",
      runs: [{ id: "run-1.log", status: "success", startedAt: "2026-01-02T03:04:05Z" }],
      onPruneRuns(options) {
        pruneCalls.push(options);
      },
      onRetentionSettingsChange(nextSettings) {
        settingsChanges.push(nextSettings);
      },
    }),
    createFetchMock([]),
  );

  await dom.flush();
  await dom.click(dom.byTitle("Run retention settings"));
  await dom.change(dom.controlAfterLabel("Keep latest runs"), "25");
  await dom.change(dom.controlAfterLabel("Keep runs for days"), "5");
  await dom.change(dom.controlAfterLabel("Keep failed runs for days"), "12");
  const previewButton = allElements(dom.container).find(
    (node) => node.tagName === "BUTTON" && directText(node) === "Preview",
  );
  assert.ok(previewButton, "Unable to find retention preview button");
  await dom.click(previewButton);

  assert.deepEqual(settingsChanges, [
    { keepDays: 7, keepFailedDays: 21, keepLast: 25 },
    { keepDays: 5, keepFailedDays: 21, keepLast: 25 },
    { keepDays: 5, keepFailedDays: 12, keepLast: 25 },
  ]);
  assert.deepEqual(pruneCalls, [
    { dryRun: true, keepDays: 5, keepFailedDays: 12, keepLast: 25 },
  ]);

  await dom.unmount();
});

test("Electron main IPC contract registers real handlers and invokes the wired implementation", async () => {
  const { ipcHandlerDefinitions, registerIpcHandlers } = require("../../electron/ipc-handlers.cjs");
  const registered = new Map();
  const calls = [];
  const wrapped = [];
  const handlers = Object.fromEntries(
    ipcHandlerDefinitions.map(([, handlerName]) => [
      handlerName,
      async (_event, payload) => {
        calls.push({ handlerName, payload });
        return { handlerName, payload };
      },
    ]),
  );

  registerIpcHandlers(
    { handle: (channel, handler) => registered.set(channel, handler) },
    handlers,
    {
      secureHandler: (handler, channel) => {
        wrapped.push(channel);
        return async (event, payload) => {
          if (event?.trusted !== true) {
            throw new Error("untrusted sender");
          }
          return handler(event, payload);
        };
      },
    },
  );

  assert.deepEqual([...registered.keys()].sort(), ipcHandlerDefinitions.map(([channel]) => channel).sort());
  assert.deepEqual(wrapped.sort(), ipcHandlerDefinitions.map(([channel]) => channel).sort());
  await assert.rejects(
    registered.get("gofer:list-directory")({ trusted: false }, { currentPath: "/tmp" }),
    /untrusted sender/,
  );
  assert.deepEqual(await registered.get("gofer:list-directory")({ trusted: true }, { currentPath: "/tmp" }), {
    handlerName: "listDirectory",
    payload: { currentPath: "/tmp" },
  });
  assert.deepEqual(await registered.get("gofer:check-for-updates")({ trusted: true }, undefined), {
    handlerName: "checkForUpdates",
    payload: undefined,
  });
  assert.deepEqual(calls.map((call) => call.handlerName), ["listDirectory", "checkForUpdates"]);
  assert.throws(
    () => registerIpcHandlers({ handle: () => {} }, { ...handlers, listDirectory: undefined }),
    /Missing IPC handler: listDirectory/,
  );
});

test("Git porcelain status maps tracked, untracked, deleted, and renamed files", async () => {
  const {
    parseGitDiffHunks,
    parseGitHistory,
    parseGitStatus,
    parseGitWorktrees,
    readGitFileBaseline,
    readGitStatus,
    readGitWorktrees,
    removeGitWorktree,
  } = require("../../electron/git-status.cjs");
  assert.deepEqual(parseGitHistory("\0abc\x1fa1b2c3\x1fAda\x1f2026-08-31T12:00:00Z\x1fShip it\x1fShip it\n\nFull details.\n\x1fHEAD -> main\n12\t3\tapp.js\n-\t-\timage.png\n5\t0\ttest.js\n"), [{
    author: "Ada",
    authoredAt: "2026-08-31T12:00:00Z",
    binaryFiles: 1,
    deletions: 3,
    hash: "abc",
    insertions: 17,
    message: "Ship it\n\nFull details.",
    refs: "HEAD -> main",
    shortHash: "a1b2c3",
    subject: "Ship it",
  }]);
  assert.deepEqual(parseGitWorktrees("worktree /repo\nHEAD abc\nbranch refs/heads/main\n\nworktree /repo-feature\nHEAD def\ndetached\n\n"), [
    { bare: false, branch: "main", detached: false, head: "abc", locked: false, path: "/repo", prunable: false },
    { bare: false, branch: "", detached: true, head: "def", locked: false, path: "/repo-feature", prunable: false },
  ]);
  assert.deepEqual(parseGitStatus([
    " M src/app.js",
    "?? notes.txt",
    "A  added.txt",
    " D removed.txt",
    "R  renamed.txt",
    "old-name.txt",
    "!! ignored.log",
    "",
  ].join("\0")), [
    { path: "src/app.js", status: "M", indexStatus: " ", worktreeStatus: "M", staged: false, unstaged: true },
    { path: "notes.txt", status: "U", indexStatus: "?", worktreeStatus: "?", staged: false, unstaged: true },
    { path: "added.txt", status: "A", indexStatus: "A", worktreeStatus: " ", staged: true, unstaged: false },
    { path: "removed.txt", status: "D", indexStatus: " ", worktreeStatus: "D", staged: false, unstaged: true },
    { path: "renamed.txt", status: "A", indexStatus: "R", worktreeStatus: " ", staged: true, unstaged: false, originalPath: "old-name.txt" },
  ]);

  const calls = [];
  const result = await readGitStatus("/workspace/project", {
    async runGit(args) {
      calls.push(args);
      if (args.includes("--show-toplevel")) return "/workspace/project\n";
      if (args.includes("status")) return "# branch.head main\0# branch.ab +2 -3\0" + "1 .M N... 100644 100644 100644 abc abc workflow.rattish\0";
      if (args.includes("branch")) return "main\n";
      if (args.includes("for-each-ref")) return "main\nfeature\n";
      if (args.includes("remote") || args.includes("stash")) return "";
      return "2\t3\n";
    },
  });
  assert.deepEqual(result, {
    active: true,
    entries: [{ path: "workflow.rattish", status: "M", indexStatus: " ", worktreeStatus: "M", staged: false, unstaged: true }],
    root: "/workspace/project",
    branch: "main", branches: ["main", "feature"], ahead: 2, behind: 3, remotes: [], stashCount: 0,
  });
  assert.deepEqual(calls[1], [
    "-C",
    "/workspace/project",
    "status",
    "--porcelain=v2",
    "--branch",
    "-z",
    "--untracked-files=all",
    "--",
    ".",
  ]);
  assert.deepEqual(await readGitStatus("/not-a-repo", {
    async runGit() { throw new Error("not a repository"); },
  }), { active: false, entries: [], root: "" });

  const worktreeCalls = [];
  const existingWorktreePath = os.tmpdir();
  const listedWorktrees = await readGitWorktrees(existingWorktreePath, {
    async runGit(args) {
      worktreeCalls.push(args);
      if (args.includes("rev-parse")) return `${existingWorktreePath}\n`;
      if (args.includes("prune")) throw new Error("read-only Git metadata");
      if (args.includes("list")) {
        return `worktree ${existingWorktreePath}\nHEAD abc\nbranch refs/heads/main\n\nworktree /workspace/missing\nHEAD def\nbranch refs/heads/dev\nprunable gitdir file points to non-existent location\n\n`;
      }
      return "";
    },
  });
  assert.deepEqual(worktreeCalls[1], [
    "-C", existingWorktreePath, "worktree", "list", "--porcelain",
  ]);
  assert.deepEqual(listedWorktrees.worktrees, [{
    bare: false,
    branch: "main",
    detached: false,
    head: "abc",
    locked: false,
    path: existingWorktreePath,
    prunable: false,
  }]);

  assert.deepEqual(parseGitDiffHunks([
    "@@ -2,2 +2,3 @@",
    "@@ -12 +13,0 @@",
    "@@ -0,0 +1 @@",
  ].join("\n")), [
    { startLine: 2, endLine: 4 },
    { startLine: 13, endLine: 13 },
    { startLine: 1, endLine: 1 },
  ]);

  const baselineCalls = [];
  assert.deepEqual(await readGitFileBaseline("/workspace/project/src/app.js", {
    async runGit(args) {
      baselineCalls.push(args);
      if (args.includes("rev-parse")) return "/workspace/project\n";
      if (args.includes("ls-files")) return "src/app.js\n";
      if (args.includes("show")) return "const answer = 41;\n";
      return "@@ -1 +1 @@\n-const answer = 41;\n+const answer = 42;\n";
    },
  }), {
    changed: true,
    content: "const answer = 41;\n",
    modifiedContent: "", deleted: true,
    hunks: [{ startLine: 1, endLine: 1 }],
    tracked: true,
  });
  assert.ok(baselineCalls.some((args) => args.includes("HEAD")));

  const removeCalls = [];
  const missingPath = path.join(os.tmpdir(), "raticode-missing-worktree-test");
  const removed = await removeGitWorktree("/workspace/project", missingPath, {
    async runGit(args) {
      removeCalls.push(args);
      if (args.includes("rev-parse")) return "/workspace/project\n";
      if (args.includes("list")) {
        return `worktree /workspace/project\nHEAD abc\nbranch refs/heads/main\n\nworktree ${missingPath}\nHEAD def\nbranch refs/heads/old\nprunable gitdir file points to non-existent location\n\n`;
      }
      return "";
    },
  });
  assert.deepEqual(removeCalls[0], [
    "-C", "/workspace/project", "worktree", "prune", "--expire", "now",
  ]);
  assert.deepEqual(removed.worktrees, []);
});

test("worktree removal deletes the folder and registration while preserving the branch", async () => {
  const { execFileSync } = require("node:child_process");
  const { removeGitWorktree } = require("../../electron/git-status.cjs");
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-remove-worktree-"));
  const projectRoot = path.join(temporaryRoot, "main");
  const targetPath = path.join(temporaryRoot, "feature");
  const git = (...args) => execFileSync("git", args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  try {
    git("init", "-b", "main", projectRoot);
    git("-C", projectRoot, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "Initial");
    git("-C", projectRoot, "worktree", "add", "-b", "feature", targetPath);
    fs.writeFileSync(path.join(targetPath, "unsaved.txt"), "Keep this until removal is safe");
    assert.deepEqual(await removeGitWorktree(projectRoot, targetPath), { requiresForce: true });
    assert.equal(fs.existsSync(targetPath), true);
    git("-C", targetPath, "add", "unsaved.txt");
    fs.writeFileSync(path.join(targetPath, "unsaved.txt"), "Unstaged changes too");
    fs.writeFileSync(path.join(targetPath, "untracked.txt"), "Untracked file");
    const result = await removeGitWorktree(projectRoot, targetPath, { force: true });
    assert.equal(fs.existsSync(targetPath), false);
    assert.deepEqual(result.worktrees.map((worktree) => worktree.path), [projectRoot]);
    assert.equal(git("-C", projectRoot, "branch", "--list", "feature").trim(), "feature");
  } finally {
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
});

test("source control requires explicit confirmation to discard worktree changes and supports cancel", async () => {
  const items = [{ path: "/workspace/project", branch: "main" }, { path: "/workspace/feature", branch: "feature" }];
  const calls = [];
  const removedProjects = [];
  let finishRemoval;
  let fail = true;
  const dom = await mountReact(
    React.createElement(codeFileExplorerModule.default, {
      workflow: { projectRoot: items[0].path },
      onRemoveRecentProject: (root) => removedProjects.push(root),
    }),
    createFetchMock([]),
    { desktop: { workspace: {
      async trustProjectRoot() {},
      async listDirectory() { return { entries: [] }; },
      async gitStatus() { return { active: true, entries: [], branch: "main", branches: ["main", "feature"] }; },
      async gitHistory() { return { active: true, commits: [] }; },
      async gitWorktrees() { return { active: true, worktrees: items }; },
      async removeWorktree(options) {
        calls.push(options);
        if (fail === "permission") throw new Error("Permission denied");
        if (fail) return { requiresForce: true };
        await new Promise((resolve) => { finishRemoval = resolve; });
        return { active: true, worktrees: [items[0]] };
      },
    } } },
  );
  await dom.click(dom.byLabel("Source control"));
  await dom.click(dom.byText("Branches"));
  await dom.click(dom.byLabel("Remove feature worktree"));
  assert.match(dom.text(), /The branch will be kept/);
  assert.deepEqual(calls, []);
  await dom.click(dom.byText("Cancel"));
  assert.deepEqual(calls, []);
  await dom.click(dom.byLabel("Remove feature worktree"));
  await dom.click(dom.byLabel("Confirm worktree removal"));
  assert.match(dom.text(), /This worktree has uncommitted changes/);
  assert.match(dom.text(), /permanently discard all uncommitted changes and untracked files/);
  assert.equal(calls[0].force, false);
  await dom.click(dom.byText("Cancel"));
  assert.equal(calls.length, 1);
  await dom.click(dom.byLabel("Remove feature worktree"));
  await dom.click(dom.byLabel("Confirm worktree removal"));
  assert.equal(removedProjects.length, 0);
  fail = "permission";
  await dom.click(dom.byLabel("Discard changes and remove"));
  assert.match(dom.text(), /Permission denied/);
  assert.equal(removedProjects.length, 0);
  fail = false;
  await dom.click(dom.byLabel("Discard changes and remove"));
  assert.equal(reactProps(dom.byText("Removing…")).disabled, true);
  finishRemoval();
  await dom.flush();
  assert.throws(() => dom.byLabel("Remove feature worktree"));
  assert.doesNotMatch(dom.text(), /Remove worktree\?/);
  assert.deepEqual(calls, [false, false, true, true].map((force) => ({ projectRoot: items[0].path, targetPath: items[1].path, force })));
  assert.deepEqual(removedProjects, [items[1].path]);
  await dom.unmount();
});

test("source control keeps the Worktrees tab selected when switching worktrees", async () => {
  const roots = ["/workspace/project", "/workspace/feature"];
  const selections = [];
  const statusRoots = [];
  let resolveStatus;
  let delayStatus = false;
  function WorktreeSwitcher() {
    const [projectRoot, setProjectRoot] = React.useState(roots[0]);
    return React.createElement(codeFileExplorerModule.default, {
      workflow: { projectRoot },
      onSelectProject: (path) => {
        selections.push(path);
        setProjectRoot(path);
      },
    });
  }
  const dom = await mountReact(
    React.createElement(WorktreeSwitcher),
    createFetchMock([]),
    { desktop: { workspace: {
      async trustProjectRoot() {},
      async listDirectory() { return { entries: [] }; },
      async gitStatus(root) {
        statusRoots.push(root);
        if (delayStatus) await new Promise((resolve) => { resolveStatus = resolve; });
        return { active: true, entries: [], branch: root === roots[0] ? "main" : "feature", branches: ["main", "feature"] };
      },
      async gitHistory() { return { active: true, commits: [] }; },
      async gitWorktrees() {
        return { active: true, worktrees: roots.map((path, index) => ({
          path, branch: index === 0 ? "main" : "feature",
        })) };
      },
    } } },
  );
  await dom.click(dom.byLabel("Source control"));
  await dom.click(dom.byText("Branches"));
  const worktreeButton = (root) => dom.allByTitle(root).find((node) => node.tagName === "BUTTON");
  delayStatus = true;
  for (const root of [roots[1], roots[0]]) {
    const branchSelector = dom.byLabel("Switch branch");
    const row = worktreeButton(root);
    await dom.click(worktreeButton(root));
    await dom.flush();
    assert.doesNotMatch(dom.text(), /This project is not a Git repository/);
    assert.equal(dom.byLabel("Switch branch"), branchSelector);
    assert.equal(reactProps(branchSelector).disabled, true);
    assert.equal(worktreeButton(root), row);
    resolveStatus();
    await dom.flush();
    assert.equal(reactProps(branchSelector).disabled, false);
    assert.equal(reactProps(branchSelector).value, root === roots[0] ? "main" : "feature");
    assert.equal(worktreeButton(root).getAttribute("aria-current"), "page");
    assert.equal(statusRoots.at(-1), root);
    assert.doesNotMatch(dom.text(), /Commit message/);
  }
  assert.deepEqual(selections, [roots[1], roots[0]]);
  await dom.unmount();
});

test("commit history refreshes in the background and rows expand on click", async () => {
  const commit = {
    author: "Ada",
    authoredAt: "2026-08-31T12:00:00Z",
    binaryFiles: 1,
    deletions: 3,
    hash: "abc123def456",
    insertions: 17,
    message: "Ship it\n\nFull commit details.",
    refs: "HEAD -> main",
    shortHash: "abc123d",
    subject: "Ship it",
  };
  let historyCalls = 0;
  const refreshDeferred = createDeferred();
  const workspace = {
    async gitHistory() {
      historyCalls += 1;
      if (historyCalls === 2) return refreshDeferred.promise;
      return { active: true, commits: [commit] };
    },
    async gitStatus() {
      return { active: true, entries: [] };
    },
    async gitWorktrees() {
      return {
        active: true,
        worktrees: [
          { branch: "main", path: "/workspace/project" },
          { branch: "old-feature", missing: true, path: "/workspace/missing" },
        ],
      };
    },
    async listDirectory() {
      return { entries: [] };
    },
    async trustProjectRoot() {},
  };
  const dom = await mountReact(
    React.createElement(codeFileExplorerModule.default, {
      workflow: { projectRoot: "/workspace/project" },
    }),
    createFetchMock([]),
    { desktop: { workspace } },
  );

  assert.equal(codeFileExplorerModule.commitMessageBody(commit), "Full commit details.");
  assert.equal(codeFileExplorerModule.commitMessageBody({ ...commit, message: commit.subject }), "");
  assert.equal(codeFileExplorerModule.commitMessageBody({ ...commit, message: "A different first line\n\nMore context." }), "A different first line\n\nMore context.");

  const sourceControlButton = dom.byLabel("Source control");
  await dom.click(sourceControlButton);
  await dom.flush();
  assert.equal(historyCalls, 1);
  assert.doesNotMatch(dom.text(), /\b1 commits\b/);

  await dom.click(dom.byText("Branches"));
  const activeWorktree = dom.ancestor(dom.byText("main"), "BUTTON");
  assert.equal(activeWorktree.getAttribute("aria-current"), "page");
  assert.doesNotMatch(dom.text(), /old-feature|missing · Missing/);

  await dom.click(dom.byText("History"));
  const copiedValues = [];
  navigator.clipboard.writeText = async (value) => copiedValues.push(value);
  const commitButton = dom.ancestor(dom.byText("abc123d"), "BUTTON");
  const copyCommitButton = dom.byLabel("Copy commit ID abc123d");
  const refreshButton = dom.byLabel("Refresh commit history");
  assert.equal(commitButton.getAttribute("aria-expanded"), "false");
  assert.equal(commitButton.parentNode.getAttribute("title"), null);
  assert.match(dom.text(), /Ship it/);
  assert.doesNotMatch(dom.text(), /Full commit details\./);
  assert.throws(() => dom.byLabel("17 insertions, 3 deletions, 1 binary files without line counts"));

  await dom.click(refreshButton);
  assert.equal(historyCalls, 2);
  assert.equal(reactProps(refreshButton).disabled, true);
  assert.match(dom.text(), /Ship it/);
  assert.doesNotMatch(dom.text(), /Loading history/);
  refreshDeferred.resolve({ active: true, commits: [commit] });
  await dom.flush();
  assert.equal(reactProps(refreshButton).disabled, false);

  await dom.click(copyCommitButton);
  assert.deepEqual(copiedValues, ["abc123de"]);
  assert.ok(dom.byLabel("Copied commit ID abc123d"));
  assert.equal(commitButton.getAttribute("aria-expanded"), "false");

  await dom.click(commitButton);
  assert.equal(commitButton.getAttribute("aria-expanded"), "true");
  assert.match(dom.text(), /Full commit details\./);
  assert.doesNotMatch(dom.text(), /Created/);
  const authorLines = allElements(commitButton.parentNode).filter(
    (node) => node.tagName === "P" && directText(node) === "Ada",
  );
  assert.equal(authorLines.length, 0, "author name should only appear once, in the row header");
  assert.ok(dom.byLabel("17 insertions, 3 deletions, 1 binary files without line counts"));

  await dom.click(commitButton);
  assert.equal(commitButton.getAttribute("aria-expanded"), "false");
  assert.match(dom.text(), /Ship it/);
  assert.doesNotMatch(dom.text(), /Full commit details\./);

  await dom.click(dom.byLabel("File explorer"));
  await dom.click(sourceControlButton);
  await dom.flush();
  assert.equal(historyCalls, 3);
  await dom.unmount();
});

test("Electron terminal creation has no fixed session ceiling", () => {
  const source = fs.readFileSync(path.join(repoRoot, "frontend/electron/main.js"), "utf8");
  assert.doesNotMatch(source, /terminalSessions\.size\s*(?:>=|>|===?)/);
  assert.doesNotMatch(source, /Close a terminal tab before opening another one/);
  assert.match(source, /633;P;Cwd=/);
  assert.match(source, /--rcfile/);
  assert.match(source, /function global:prompt/);
});

test("Electron Git editor handoff keeps the terminal PTY flowing", () => {
  const source = fs.readFileSync(path.join(repoRoot, "frontend/electron/main.js"), "utf8");
  const editorLifecycle = source.slice(
    source.indexOf("function handleTerminalEditorConnection"),
    source.indexOf("function cancelTerminalEditorRequests"),
  );

  assert.doesNotMatch(editorLifecycle, /session\.terminal\.pause/);
  assert.doesNotMatch(editorLifecycle, /terminal\?\.resume/);
  assert.match(editorLifecycle, /owner\.send\("gofer:terminal-open-editor"/);
  assert.match(editorLifecycle, /request\.socket\.end/);
});

test("Electron path inspection reports deleted files without rejecting the request", async () => {
  const { inspectPath } = require("../../electron/path-info.cjs");
  const tempRoot = await fs.promises.mkdtemp(path.join(os.tmpdir(), "gofer-path-info-"));
  const existingPath = path.join(tempRoot, "workflow.rattish");
  await fs.promises.writeFile(existingPath, "Rattish: 1\n", "utf8");

  assert.deepEqual(await inspectPath(existingPath), {
    basename: "workflow.rattish",
    exists: true,
    extension: ".rattish",
    isDirectory: false,
    isFile: true,
    path: existingPath,
  });
  await fs.promises.rm(existingPath);
  assert.deepEqual(await inspectPath(existingPath), {
    basename: "workflow.rattish",
    exists: false,
    extension: ".rattish",
    isDirectory: false,
    isFile: false,
    path: existingPath,
  });

  await fs.promises.rm(tempRoot, { force: true, recursive: true });
});

test("Electron update checks return an error state instead of rejecting IPC", () => {
  const source = fs.readFileSync(path.join(repoRoot, "frontend/electron/main.js"), "utf8");
  const checkFunction = source.slice(
    source.indexOf("async function checkForUpdates"),
    source.indexOf("async function downloadAndInstallUpdate"),
  );

  assert.match(checkFunction, /catch \(error\)[\s\S]*setUpdateState\(\{[\s\S]*error:/);
  assert.doesNotMatch(checkFunction, /throw error/);
  assert.match(checkFunction, /return getUpdateState\(\)/);
});

test("integrated browser ignores late events from replaced native sessions", () => {
  assert.equal(
    integratedBrowserModule.browserSessionEventMatches("current-session", {
      clientId: "browser://tab-1",
      id: "current-session",
    }),
    true,
  );
  assert.equal(
    integratedBrowserModule.browserSessionEventMatches("current-session", {
      clientId: "browser://tab-1",
      id: "replaced-session",
    }),
    false,
  );
  assert.equal(
    integratedBrowserModule.browserSessionEventMatches("", {
      clientId: "browser://tab-1",
      id: "session-before-create-resolved",
    }),
    false,
  );
});

test("Electron integrated browser uses locked-down webview guests", () => {
  const source = fs.readFileSync(path.join(repoRoot, "frontend/electron/main.js"), "utf8");
  const preloadSource = fs.readFileSync(
    path.join(repoRoot, "frontend/electron/browser-preload.cjs"),
    "utf8",
  );
  const componentSource = fs.readFileSync(
    path.join(repoRoot, "frontend/src/components/IntegratedBrowser.jsx"),
    "utf8",
  );
  assert.doesNotMatch(source, /new WebContentsView/);
  assert.match(source, /webviewTag: true/);
  assert.match(source, /will-attach-webview/);
  assert.match(source, /params\.partition !== "persist:raticode-browser"/);
  assert.match(source, /isPendingBrowserSessionSrc\(terminalOwnerId, params\.src\)/);
  assert.match(source, /webPreferences\.preload = browserPreloadPath/);
  assert.match(source, /webPreferences\.contextIsolation = true/);
  assert.match(source, /webPreferences\.nodeIntegration = false/);
  assert.match(source, /webPreferences\.sandbox = true/);
  assert.match(source, /webPreferences\.webSecurity = true/);
  assert.match(source, /did-attach-webview/);
  assert.match(source, /guestContents\.setWindowOpenHandler\(\(\) => \(\{ action: "deny" \}\)\)/);
  assert.match(source, /guest\.hostWebContents !== event\.sender/);
  assert.match(source, /guest\.getType\(\) !== "webview"/);
  assert.match(source, /before-mouse-event/);
  assert.match(source, /edit-local-html/);
  assert.match(source, /closeBrowserSession/);
  assert.match(source, /gofer:browser-open-file/);
  assert.match(source, /event\.senderFrame !== contents\.mainFrame/);
  assert.match(source, /gofer:browser-navigation/);
  assert.match(source, /contents\.navigationHistory\.goBack\(\)/);
  assert.match(source, /setWindowOpenHandler\(\(\{ url \}\) => \{[\s\S]*?contents\.loadURL\(url\)/);
  assert.match(
    source,
    /if \(browserCommandRequiresOwnerFocus\(action\)\) session\.owner\.focus\(\);\s*session\.owner\.send\("gofer:browser-command"/,
  );
  assert.match(source, /session\.openBrowserBinding/);
  assert.match(source, /syncBrowserContentZoom\(session\)/);
  assert.match(source, /session\.ownerZoomFactor/);
  assert.doesNotMatch(source, /accelerator: "CommandOrControl\+Alt\+\//);
  assert.match(componentSource, /document\.createElement\("webview"\)/);
  assert.match(componentSource, /setAttribute\("partition", "persist:raticode-browser"\)/);
  assert.match(componentSource, /bridge\.adopt\(sessionId, element\.getWebContentsId\(\)\)/);
  assert.match(componentSource, /page-favicon-updated/);
  assert.match(source, /page-favicon-updated/);
  assert.match(preloadSource, /gofer:browser-link-clicked/);
  assert.match(preloadSource, /gofer:browser-navigation/);
  assert.match(preloadSource, /gofer:browser-zoom/);
  assert.match(preloadSource, /event\.preventDefault\(\)/);
});

test("Electron integrated browser restores renderer focus after closing a focused view", () => {
  const source = fs.readFileSync(path.join(repoRoot, "frontend/electron/main.js"), "utf8");
  const closeFunction = source.slice(
    source.indexOf("function closeBrowserSession"),
    source.indexOf("function browserSessionContents"),
  );

  assert.match(closeFunction, /const contents = browserSessionContents\(session\)/);
  assert.match(closeFunction, /contents\?\.isFocused\(\) === true/);
  assert.match(closeFunction, /restoreOwnerFocus[\s\S]*session\.owner\.focus\(\)/);
  assert.match(closeFunction, /!session\.owner\.isDestroyed\(\)/);
});

test("open editors refresh Git baselines after external branch changes", () => {
  const source = fs.readFileSync(
    path.join(repoRoot, "frontend/src/components/CodeWorkspace.jsx"),
    "utf8",
  );
  assert.match(source, /if \(!visible\) return undefined;\s+return startPolling\(refreshGitBaseline, \{ immediate: true \}\)/);
  const polling = fs.readFileSync(path.join(repoRoot, "frontend/src/lib/refresh.js"), "utf8");
  assert.match(polling, /addEventListener\("focus", wake\)/);
  assert.match(polling, /addEventListener\("visibilitychange", wake\)/);
});

test("recent project selection always rediscovers the selected folder", () => {
  const source = fs.readFileSync(path.join(repoRoot, "frontend/src/pages/App.jsx"), "utf8");
  const selectFunction = source.slice(
    source.indexOf("function selectRecentProject"),
    source.indexOf("function removeRecentProject"),
  );
  assert.match(selectFunction, /openProjectAtPath\(selectedProjectRoot/);
  assert.doesNotMatch(selectFunction, /projectWorkflows/);
  assert.match(source, /projectOpenRequestRef\.current !== requestId/);
});

test("Electron integrated browser ignores state events after native view disposal", () => {
  const source = fs.readFileSync(path.join(repoRoot, "frontend/electron/main.js"), "utf8");
  const emitFunction = source.slice(
    source.indexOf("function emitBrowserState"),
    source.indexOf("function emitBrowserCommand"),
  );
  const stateFunction = source.slice(
    source.indexOf("function browserSessionState"),
    source.indexOf("function emitBrowserState"),
  );

  assert.match(emitFunction, /browserSessions\.get\(session\.id\) === session/);
  assert.match(emitFunction, /browserSessionContents\(session\)/);
  assert.match(stateFunction, /if \(!contents\)/);
  assert.match(stateFunction, /Browser view is unavailable\./);
});

test("Electron integrated browser registers one cleanup listener per renderer owner", () => {
  const source = fs.readFileSync(path.join(repoRoot, "frontend/electron/main.js"), "utf8");
  const registerFunction = source.slice(
    source.indexOf("function registerBrowserOwnerCleanup"),
    source.indexOf("function closeBrowsersForOwner"),
  );

  assert.match(source, /const browserOwnersWithCleanup = new WeakSet\(\)/);
  assert.match(registerFunction, /browserOwnersWithCleanup\.has\(owner\)/);
  assert.match(registerFunction, /owner\.once\("destroyed"/);
  assert.doesNotMatch(source, /event\.sender\.once\("destroyed"/);
});

test("integrated browser reserves new tabs for modified link clicks", () => {
  const { dispatch, sent } = runBrowserPreload();
  const anchor = {
    hasAttribute: () => false,
    href: "https://example.com/docs",
    tagName: "A",
  };

  const plainClick = browserPageEvent({ composedPath: () => [anchor], type: "click" });
  dispatch("click", plainClick);
  assert.equal(plainClick.defaultPrevented, false);
  assert.deepEqual(sent, []);

  const modifiedClick = browserPageEvent({
    composedPath: () => [anchor],
    ctrlKey: true,
    type: "click",
  });
  dispatch("click", modifiedClick);
  assert.equal(modifiedClick.defaultPrevented, true);
  assert.deepEqual(toPlainObject(sent), [{
    channel: "gofer:browser-link-clicked",
    payload: { url: "https://example.com/docs" },
  }]);
});

test("integrated browser Backspace navigates unless focus is editable", () => {
  const { dispatch, sent } = runBrowserPreload();
  const pageBackspace = browserPageEvent({ key: "Backspace", type: "keydown" });
  dispatch("keydown", pageBackspace);
  assert.equal(pageBackspace.defaultPrevented, true);
  assert.deepEqual(toPlainObject(sent), [{
    channel: "gofer:browser-navigation",
    payload: { action: "back" },
  }]);

  const inputBackspace = browserPageEvent({
    composedPath: () => [{ tagName: "INPUT" }],
    key: "Backspace",
    type: "keydown",
  });
  dispatch("keydown", inputBackspace);
  assert.equal(inputBackspace.defaultPrevented, false);
  assert.equal(sent.length, 1);
});

test("integrated browser forwards modified wheel gestures to app zoom", () => {
  const { dispatch, sent } = runBrowserPreload();

  const plainWheel = browserPageEvent({ deltaY: -100, type: "wheel" });
  dispatch("wheel", plainWheel);
  assert.equal(plainWheel.defaultPrevented, false);

  const zoomIn = browserPageEvent({ ctrlKey: true, deltaY: -100, type: "wheel" });
  dispatch("wheel", zoomIn);
  assert.equal(zoomIn.defaultPrevented, true);

  const zoomOut = browserPageEvent({ deltaY: 100, metaKey: true, type: "wheel" });
  dispatch("wheel", zoomOut);
  assert.equal(zoomOut.defaultPrevented, true);

  assert.deepEqual(toPlainObject(sent), [
    { channel: "gofer:browser-zoom", payload: { direction: 1 } },
    { channel: "gofer:browser-zoom", payload: { direction: -1 } },
  ]);
});

test("Electron IPC security validates sender origins and external URL schemes", () => {
  const {
    createIpcSecurity,
    fileUrlForPath,
    isSafeExternalUrl,
    isTrustedSenderUrl,
  } = require("../../electron/security.cjs");
  const appRoot = path.join(repoRoot, "frontend/dist");
  const mainFrame = { url: fileUrlForPath(path.join(appRoot, "index.html")) };
  const mainWebContents = { mainFrame };
  const security = createIpcSecurity({
    appRoot,
    getDataDir: () => repoRoot,
    getMainWebContents: () => mainWebContents,
    isProduction: true,
  });

  assert.equal(
    isTrustedSenderUrl(fileUrlForPath(path.join(appRoot, "index.html")), {
      appRoot,
      isProduction: true,
    }),
    true,
  );
  assert.equal(
    isTrustedSenderUrl(fileUrlForPath(path.join(repoRoot, "README.md")), {
      appRoot,
      isProduction: true,
    }),
    false,
  );
  assert.equal(
    isTrustedSenderUrl("http://127.0.0.1:5173/src/main.jsx", {
      appRoot,
      devServerUrl: "http://127.0.0.1:5173",
      isProduction: false,
    }),
    true,
  );
  assert.equal(
    isTrustedSenderUrl("https://example.com/app", {
      appRoot,
      devServerUrl: "http://127.0.0.1:5173",
      isProduction: false,
    }),
    false,
  );

  assert.equal(isSafeExternalUrl("https://github.com/zacharyivie/gofer-flow"), true);
  assert.equal(isSafeExternalUrl("http://127.0.0.1:8765/docs"), true);
  assert.equal(isSafeExternalUrl("mailto:help@example.com"), true);
  assert.equal(isSafeExternalUrl("file:///etc/passwd"), false);
  assert.equal(isSafeExternalUrl("javascript:alert(1)"), false);
  assert.equal(
    security.assertTrustedSender({
      sender: mainWebContents,
      senderFrame: mainFrame,
    }),
    true,
  );
  assert.throws(
    () =>
      security.assertTrustedSender({
        sender: mainWebContents,
        senderFrame: { url: mainFrame.url },
      }),
    /unexpected frame/,
  );
  assert.throws(
    () =>
      security.assertTrustedSender({
        sender: { mainFrame },
        senderFrame: mainFrame,
      }),
    /unexpected window/,
  );
});

test("Electron IPC security confines file paths to data dir and explicit grants", async () => {
  const { createIpcSecurity } = require("../../electron/security.cjs");
  const tempRoot = await fs.promises.mkdtemp(path.join(os.tmpdir(), "gofer-ipc-test-"));
  const dataDir = path.join(tempRoot, "data");
  const outsideDir = path.join(tempRoot, "outside");
  await fs.promises.mkdir(dataDir, { recursive: true });
  await fs.promises.mkdir(outsideDir, { recursive: true });
  await fs.promises.writeFile(path.join(dataDir, "workflow.toml"), "ok", "utf8");
  await fs.promises.writeFile(path.join(outsideDir, "secret.txt"), "no", "utf8");

  const security = createIpcSecurity({
    appRoot: path.join(repoRoot, "frontend/dist"),
    devServerUrl: "http://127.0.0.1:5173",
    getDataDir: () => dataDir,
    isProduction: false,
  });

  assert.equal(security.resolveAllowedPath("workflow.toml", { mustExist: true }), path.join(dataDir, "workflow.toml"));
  assert.equal(security.resolveAllowedPath(path.join(dataDir, "new.toml")), path.join(dataDir, "new.toml"));
  assert.throws(
    () => security.resolveAllowedPath(path.join(outsideDir, "secret.txt"), { mustExist: true }),
    /outside the approved/,
  );

  const symlinkPath = path.join(dataDir, "leak.txt");
  try {
    await fs.promises.symlink(path.join(outsideDir, "secret.txt"), symlinkPath);
    assert.throws(
      () => security.resolveAllowedPath(symlinkPath, { mustExist: true }),
      /outside the approved/,
    );
  } catch (error) {
    if (error.code !== "EPERM" && error.code !== "EACCES") {
      throw error;
    }
  }

  const grant = security.grantPath(outsideDir);
  assert.equal(
    security.resolveAllowedPath(path.join(outsideDir, "secret.txt"), {
      grantId: grant.grantId,
      mustExist: true,
    }),
    path.join(outsideDir, "secret.txt"),
  );
  assert.throws(
    () =>
      security.resolveAllowedPath(path.join(outsideDir, "secret.txt"), {
        grantId: "missing",
        mustExist: true,
      }),
    /invalid or expired/,
  );

  await fs.promises.rm(tempRoot, { force: true, recursive: true });
});

test("Electron registered IPC handlers reject untrusted senders and ungranted paths", async () => {
  const { registerIpcHandlers } = require("../../electron/ipc-handlers.cjs");
  const { createIpcSecurity, fileUrlForPath } = require("../../electron/security.cjs");
  const tempRoot = await fs.promises.mkdtemp(path.join(os.tmpdir(), "gofer-ipc-handler-"));
  const appRoot = path.join(repoRoot, "frontend/dist");
  const dataDir = path.join(tempRoot, "data");
  const outsideDir = path.join(tempRoot, "outside");
  const dataFile = path.join(dataDir, "workflow.toml");
  const outsideFile = path.join(outsideDir, "secret.txt");
  await fs.promises.mkdir(dataDir, { recursive: true });
  await fs.promises.mkdir(outsideDir, { recursive: true });
  await fs.promises.writeFile(dataFile, "ok", "utf8");
  await fs.promises.writeFile(outsideFile, "secret", "utf8");

  const mainFrame = { url: fileUrlForPath(path.join(appRoot, "index.html")) };
  const mainWebContents = { mainFrame };
  const security = createIpcSecurity({
    appRoot,
    getDataDir: () => dataDir,
    getMainWebContents: () => mainWebContents,
    isProduction: true,
  });
  const registered = new Map();
  const handlers = Object.fromEntries(
    require("../../electron/ipc-handlers.cjs").ipcHandlerDefinitions.map(([, handlerName]) => [
      handlerName,
      async () => ({ ok: true }),
    ]),
  );
  handlers.readTextFile = async (_event, options = {}) => {
    const targetPath = security.resolveAllowedPath(options.targetPath, {
      grantId: options.grantId,
      mustExist: true,
    });
    return {
      content: await fs.promises.readFile(targetPath, "utf8"),
      path: targetPath,
    };
  };
  handlers.writeTextFile = async (_event, options = {}) => {
    const targetPath = security.resolveAllowedPath(options.targetPath, {
      grantId: options.grantId,
    });
    await fs.promises.writeFile(targetPath, options.content, "utf8");
    return { path: targetPath };
  };
  handlers.deletePath = async (_event, options = {}) => {
    const targetPath = security.resolveAllowedPath(options.targetPath, {
      grantId: options.grantId,
      mustExist: true,
    });
    return { path: targetPath };
  };

  registerIpcHandlers(
    { handle: (channel, handler) => registered.set(channel, handler) },
    handlers,
    { secureHandler: (handler) => security.secureHandler(handler) },
  );

  const trustedEvent = { sender: mainWebContents, senderFrame: mainFrame };
  const untrustedEvent = {
    sender: mainWebContents,
    senderFrame: { url: "https://example.com/" },
  };
  await assert.rejects(
    registered.get("gofer:read-text-file")(untrustedEvent, { targetPath: dataFile }),
    /unexpected frame/,
  );
  assert.deepEqual(await registered.get("gofer:read-text-file")(trustedEvent, { targetPath: dataFile }), {
    content: "ok",
    path: dataFile,
  });
  await assert.rejects(
    registered.get("gofer:read-text-file")(trustedEvent, { targetPath: outsideFile }),
    /outside the approved/,
  );
  await assert.rejects(
    registered.get("gofer:write-text-file")(trustedEvent, {
      content: "bad",
      targetPath: outsideFile,
    }),
    /outside the approved/,
  );
  await assert.rejects(
    registered.get("gofer:delete-path")(trustedEvent, { targetPath: outsideFile }),
    /outside the approved/,
  );
  const grant = security.grantPath(outsideDir);
  assert.deepEqual(
    await registered.get("gofer:read-text-file")(trustedEvent, {
      grantId: grant.grantId,
      targetPath: outsideFile,
    }),
    { content: "secret", path: outsideFile },
  );

  await fs.promises.rm(tempRoot, { force: true, recursive: true });
});

test("Electron backend error IPC actions still validate the expected window and main frame", async () => {
  const { registerIpcHandlers } = require("../../electron/ipc-handlers.cjs");
  const { createIpcSecurity, fileUrlForPath } = require("../../electron/security.cjs");
  const appRoot = path.join(repoRoot, "frontend/dist");
  const errorRoot = path.join(repoRoot, "frontend/electron");
  const mainFrame = { url: fileUrlForPath(path.join(appRoot, "index.html")) };
  const errorFrame = { url: fileUrlForPath(path.join(errorRoot, "backend-error.html")) };
  const mainWebContents = { mainFrame };
  const errorWebContents = { mainFrame: errorFrame };
  const mainSecurity = createIpcSecurity({
    appRoots: [appRoot],
    getDataDir: () => repoRoot,
    getMainWebContents: () => mainWebContents,
    isProduction: true,
  });
  const errorSecurity = createIpcSecurity({
    appRoots: [errorRoot],
    getDataDir: () => repoRoot,
    getMainWebContents: () => errorWebContents,
    isProduction: true,
  });
  const registered = new Map();
  const handlers = Object.fromEntries(
    require("../../electron/ipc-handlers.cjs").ipcHandlerDefinitions.map(([, handlerName]) => [
      handlerName,
      async () => ({ handlerName }),
    ]),
  );

  registerIpcHandlers(
    { handle: (channel, handler) => registered.set(channel, handler) },
    handlers,
    {
      secureHandler: (handler, channel) => (event, ...args) => {
        const security = channel === "gofer:restart-backend" || channel === "gofer:open-logs"
          ? errorSecurity
          : mainSecurity;
        return security.secureHandler(handler)(event, ...args);
      },
    },
  );

  await assert.rejects(
    registered.get("gofer:restart-backend")({
      sender: errorWebContents,
      senderFrame: { url: errorFrame.url },
    }),
    /unexpected frame/,
  );
  await assert.rejects(
    registered.get("gofer:restart-backend")({
      sender: mainWebContents,
      senderFrame: mainFrame,
    }),
    /unexpected window/,
  );
  assert.deepEqual(
    await registered.get("gofer:restart-backend")({
      sender: errorWebContents,
      senderFrame: errorFrame,
    }),
    { handlerName: "restartBackend" },
  );
});

test("DagCanvas helpers create default agent nodes and serialize node edits", () => {
  const workflow = { id: "wf", agents: {}, nodes: [], edges: [] };
  const withNode = canvasModule.addDefaultNodeToWorkflow(workflow, {
    usedAgentIds: ["agent-1"],
    x: 40,
    y: 50,
  });

  assert.equal(withNode.nodes[0].id, "node-1");
  assert.equal(withNode.nodes[0].type, "agent");
  assert.equal(withNode.nodes[0].operation.agent_id, "agent-2");
  assert.equal(withNode.nodes[0].x, 40);
  assert.equal(withNode.agents["agent-2"].subscription, "codex");

  const edited = canvasModule.updateWorkflowNodeOperation(withNode, "node-1", {
    prompt_path: "prompts/review.md",
    working_dir: "repo",
  });

  assert.equal(edited.nodes[0].operation.prompt_path, "prompts/review.md");
  assert.equal(edited.nodes[0].operation.working_dir, "repo");
  assert.match(edited.nodes[0].meta, /prompts\/review\.md/);

  const withHttpNode = canvasModule.addDefaultNodeToWorkflow(workflow, {
    type: "http_request",
    x: 80,
    y: 90,
  });
  assert.equal(withHttpNode.nodes[0].type, "http_request");
  assert.equal(withHttpNode.nodes[0].operation.method, "GET");
  assert.equal(withHttpNode.nodes[0].operation.expected_statuses[0], 200);
  assert.match(withHttpNode.nodes[0].meta, /https:\/\/api\.example\.com\/resource/);

  assert.deepEqual(canvasModule.defaultOperation("workflow"), {
    type: "workflow",
    workflow_id: "",
    input_bindings: {},
  });
  assert.deepEqual(canvasModule.defaultOperation("subflow"), {
    type: "subflow",
    component_id: "",
    source_path: "",
    input_bindings: {},
    output_contract: {},
  });
  assert.deepEqual(canvasModule.defaultOperation("notification").expected_statuses, [
    200,
    201,
    202,
    204,
  ]);
  assert.equal(canvasModule.defaultOperation("notification").smtp_port, 587);
});

test("DagCanvas helper duplicates nodes with unique ids and agent configs", () => {
  const workflow = {
    id: "wf",
    agents: {
      "agent-1": { subscription: "codex", model: "gpt-5" },
    },
    nodes: [
      {
        id: "node-1",
        type: "agent",
        label: "Review",
        operation: { type: "agent", agent_id: "agent-1", prompt: "Read this" },
        x: 10,
        y: 20,
      },
    ],
    edges: [],
  };

  const duplicated = canvasModule.duplicateWorkflowNode(workflow, "node-1");

  assert.deepEqual(duplicated.nodes.map((node) => node.id), ["node-1", "node-2"]);
  assert.equal(duplicated.nodes[1].label, "Review copy");
  assert.equal(duplicated.nodes[1].operation.agent_id, "agent-2");
  assert.equal(duplicated.agents["agent-2"].subscription, "codex");
  assert.equal(duplicated.nodes[1].x, 38);
  assert.equal(duplicated.nodes[1].y, 48);
});

test("DagCanvas HTTP JSON body editor preserves nested values and rejects invalid text", () => {
  const body = {
    issue: { title: "Bug", labels: ["api", "urgent"] },
    count: 2,
    active: true,
  };
  const text = canvasModule.formatJsonBodyEditorValue(body);
  assert.match(text, /"labels": \[/);
  assert.deepEqual(canvasModule.parseJsonBodyEditorValue(text), {
    ok: true,
    value: body,
  });
  assert.deepEqual(canvasModule.parseJsonBodyEditorValue(""), {
    ok: true,
    value: null,
  });
  assert.equal(canvasModule.parseJsonBodyEditorValue("{broken").ok, false);
});

test("DagCanvas exposes HTTP response fields as selectable outputs", () => {
  const fields = canvasModule.nodeOutputFields({
    id: "call-api",
    type: "http_request",
    operation: { type: "http_request" },
  });
  const paths = new Set(fields.map(([pathValue]) => pathValue));

  assert(paths.has("data.status"));
  assert(paths.has("data.headers"));
  assert(paths.has("data.body"));
  assert(paths.has("data.json"));
  assert(paths.has("data.selected"));
});

test("DagCanvas exposes approval and notification fields as selectable outputs", () => {
  const approvalFields = canvasModule.nodeOutputFields({
    id: "approval",
    type: "approval_gate",
    operation: { type: "approval_gate" },
  });
  const approvalPaths = new Set(approvalFields.map(([pathValue]) => pathValue));
  assert(approvalPaths.has("data.decision"));
  assert(approvalPaths.has("data.decidedBy"));
  assert(approvalPaths.has("data.notes"));

  const notificationFields = canvasModule.nodeOutputFields({
    id: "notify",
    type: "notification",
    operation: { type: "notification" },
  });
  const notificationPaths = new Set(notificationFields.map(([pathValue]) => pathValue));
  assert(notificationPaths.has("data.title"));
  assert(notificationPaths.has("data.body"));
  assert(notificationPaths.has("data.channel"));
});

test("DagCanvas exposes local vector index and search quality fields", () => {
  const vectorFields = canvasModule.nodeOutputFields({
    id: "index",
    type: "local_vectorize",
    operation: { type: "local_vectorize" },
  });
  const vectorPaths = new Set(vectorFields.map(([pathValue]) => pathValue));
  assert(vectorPaths.has("data.indexed_file_count"));
  assert(vectorPaths.has("data.current"));
  assert(vectorPaths.has("data.stale_files"));
  assert(vectorPaths.has("data.strategy"));

  const searchFields = canvasModule.nodeOutputFields({
    id: "search",
    type: "local_search",
    operation: { type: "local_search" },
  });
  const searchPaths = new Set(searchFields.map(([pathValue]) => pathValue));
  assert(searchPaths.has("data.score_threshold"));
  assert(searchPaths.has("data.strategy"));

  const workflow = {
    id: "wf",
    agents: {},
    nodes: [],
    edges: [],
  };
  const withVector = canvasModule.addDefaultNodeToWorkflow(workflow, {
    type: "local_vectorize",
    x: 0,
    y: 0,
  });
  assert.equal(withVector.nodes[0].operation.mode, "incremental");
  const withSearch = canvasModule.addDefaultNodeToWorkflow(workflow, {
    type: "local_search",
    x: 0,
    y: 0,
  });
  assert.equal(withSearch.nodes[0].operation.score_threshold, 0);
  assert.equal(withSearch.nodes[0].operation.include_snippets, true);
  assert.equal(withSearch.nodes[0].operation.include_file_metadata, true);
});

test("DagCanvas helpers persist graph positions and create/remove edges", () => {
  let workflow = {
    id: "wf",
    agents: {},
    nodes: [
      { id: "a", type: "bash_command", label: "A", operation: { type: "bash_command" }, x: 1, y: 2 },
      { id: "b", type: "agent", label: "B", operation: { type: "agent" }, x: 20, y: 30 },
    ],
    edges: [],
  };

  workflow = canvasModule.moveWorkflowNode(workflow, "a", { x: 9, y: 10 });
  assert.equal(workflow.nodes[0].x, 10);
  assert.equal(workflow.nodes[0].y, 12);

  workflow = canvasModule.addWorkflowEdge(workflow, "a", "b", "output_matches", "ready");
  assert.equal(workflow.edges[0].id, "a-b");
  assert.equal(workflow.edges[0].label, "matches ready");
  assert.equal(workflow.edges[0].outputPattern, "ready");

  const typedWorkflow = canvasModule.addWorkflowEdge(
    { ...workflow, edges: [] },
    "a",
    "b",
    "output_field",
    null,
    "score",
    "greater_than",
    7,
  );
  assert.equal(typedWorkflow.edges[0].label, "score greater than 7");
  assert.equal(
    canvasModule.edgeLabel("output_field", null, "verdict", "equals", "approved"),
    'verdict equals "approved"',
  );
  assert.equal(
    canvasModule.edgeLabel("output_field", null, "findings", "exists", null),
    "findings exists",
  );

  workflow = canvasModule.removeWorkflowNode(workflow, "a");
  assert.deepEqual(workflow.nodes.map((node) => node.id), ["b"]);
  assert.deepEqual(workflow.edges, []);
});

test("DagCanvas layout, search, and fit helpers handle large directed graphs deterministically", () => {
  const workflow = {
    id: "wf",
    agents: {},
    nodes: [
      {
        id: "finalize",
        type: "agent",
        label: "Finalize",
        operation: { type: "agent", agent_id: "writer" },
        x: 300,
        y: 400,
      },
      {
        id: "scan",
        type: "bash_command",
        label: "Scan inbox",
        operation: { type: "bash_command", command: "find inbox" },
        x: 10,
        y: 20,
      },
      {
        id: "read-doc",
        type: "read_file",
        label: "Read spec",
        operation: { type: "read_file", path: "docs/spec.md" },
        x: 40,
        y: 30,
      },
      {
        id: "archive",
        type: "move_file",
        label: "Archive",
        operation: { type: "move_file", destination_path: "archive/spec.md" },
        x: 90,
        y: 10,
      },
    ],
    edges: [
      { id: "scan-read-doc", from: "scan", to: "read-doc" },
      { id: "read-doc-finalize", from: "read-doc", to: "finalize" },
      { id: "scan-archive", from: "scan", to: "archive" },
    ],
  };

  const laidOut = canvasModule.autoLayoutWorkflow(workflow, {
    columnGap: 300,
    rowGap: 120,
    startX: 50,
    startY: 70,
  });
  const byId = Object.fromEntries(laidOut.nodes.map((node) => [node.id, node]));

  assert.equal(byId.scan.x, 50);
  assert.equal(byId["read-doc"].x, 350);
  assert.equal(byId.archive.x, 350);
  assert.equal(byId.finalize.x, 650);
  assert.ok(byId.archive.y < byId["read-doc"].y);
  assert.deepEqual(laidOut.edges, workflow.edges);
  assert.deepEqual(canvasModule.matchingNodeIds(workflow.nodes, "writer"), ["finalize"]);
  assert.deepEqual(canvasModule.matchingNodeIds(workflow.nodes, "docs/spec"), ["read-doc"]);
  assert.deepEqual(canvasModule.matchingNodeIds(workflow.nodes, "move_file"), ["archive"]);

  const fit = canvasModule.fitViewportToNodes(laidOut.nodes, { width: 900, height: 420 }, { padding: 40 });
  assert.ok(fit.scale >= 0.45 && fit.scale <= 1.8);
  assert.equal(Number.isFinite(fit.x), true);
  assert.equal(Number.isFinite(fit.y), true);

  const bounds = canvasModule.graphBounds(laidOut.nodes);
  assert.equal(bounds.left, 50);
  assert.equal(bounds.right, 870);
});

test("selected nodes stack above overlapping nodes, including expanded folders", () => {
  const selectedFolderStack = canvasModule.nodeStackIndex("folder", {
    selectedNodeId: "folder",
    selectedNodeIds: ["folder"],
  });
  const overlappingNodeStack = canvasModule.nodeStackIndex("agent", {
    selectedNodeId: "folder",
    selectedNodeIds: ["folder"],
  });

  assert.ok(selectedFolderStack > overlappingNodeStack);
  assert.ok(
    canvasModule.nodeStackIndex("folder", {
      draggingNodeId: "folder",
      selectedNodeId: "folder",
      selectedNodeIds: ["folder"],
    }) > selectedFolderStack,
  );
  assert.ok(
    canvasModule.nodeStackIndex("secondary", {
      selectedNodeId: "folder",
      selectedNodeIds: ["folder", "secondary"],
    }) > overlappingNodeStack,
  );
});

test("DagCanvas rendered navigation controls auto-layout, fit, and zoom", async () => {
  let workflow = {
    ...workflowFixture({ id: "nav", name: "Navigation", label: "Scan" }),
    nodes: [
      {
        id: "scan",
        type: "bash_command",
        label: "Scan",
        x: 420,
        y: 310,
        operation: { type: "bash_command", command: "find docs", working_dir: "" },
      },
      {
        id: "review",
        type: "agent",
        label: "Review docs",
        x: 40,
        y: 120,
        operation: { type: "agent", agent_id: "reviewer", prompt_path: "prompts/review.md" },
      },
      {
        id: "archive",
        type: "move_file",
        label: "Archive",
        x: 80,
        y: 20,
        operation: { type: "move_file", destination_path: "archive/docs.md" },
      },
    ],
    edges: [
      { id: "scan-review", from: "scan", to: "review", label: "always", condition: "always" },
      { id: "review-archive", from: "review", to: "archive", label: "always", condition: "always" },
    ],
  };
  const changes = [];
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      dataDir: "/workspace",
      notice: { type: "success", message: "Workflow is valid" },
      workflow,
      onWorkflowChange(nextWorkflow) {
        workflow = nextWorkflow;
        changes.push(nextWorkflow);
      },
    }),
    createFetchMock([]),
  );

  const runSelector = dom.byTitle("Select workflow run");
  const toolbar = dom.ancestor(runSelector, (node) => node.getAttribute?.("data-toolbar") === "graph-editor");
  const graphActions = dom.ancestor(runSelector, "DETAILS");
  const primaryToolbarRow = dom.ancestor(
    dom.byTitle("Validate workflow"),
    (node) => node.getAttribute?.("data-toolbar-row") === "primary",
  );
  const secondaryToolbarRow = dom.ancestor(
    dom.byTitle("Auto-layout graph"),
    (node) => node.getAttribute?.("data-toolbar-row") === "secondary",
  );
  const validationButton = dom.byTitle("Validate workflow");
  const validationToolbarRow = dom.ancestor(
    validationButton,
    (node) => node.getAttribute?.("data-toolbar-row") === "primary",
  );
  const scanCard = allElements(dom.container).find(
    (element) => element.tagName === "ARTICLE" && textOf(element).includes("Scan"),
  );
  const expectedInitialViewport = canvasModule.fitViewportToNodes(workflow.nodes, {
    width: 960,
    height: 640,
  });
  assert.equal(
    scanCard.parentNode.style.transform,
    `translate(${expectedInitialViewport.x}px, ${expectedInitialViewport.y}px) scale(${expectedInitialViewport.scale})`,
  );
  assert.equal(toolbar.getAttribute("data-toolbar"), "graph-editor");
  assert.ok(graphActions.contains(dom.byTitle("More graph actions")));
  assert.match(toolbar.getAttribute("class"), /z-\[60\]/);
  assert.equal(validationToolbarRow, primaryToolbarRow);
  assert.equal(primaryToolbarRow.contains(secondaryToolbarRow), false);
  assert.doesNotMatch(secondaryToolbarRow.getAttribute("class"), /flex-wrap/);
  assert.doesNotMatch(toolbar.getAttribute("class"), /overflow-x-auto|workflow-scrollbar/);
  for (const title of ["Fit selection", "Delete selected node"]) {
    assert.doesNotMatch(dom.byTitle(title).getAttribute("class"), /hidden/);
    assert.equal(graphActions.contains(dom.byTitle(title)), false);
  }
  assert.equal(
    allElements(dom.container).some((element) => element.getAttribute?.("title") === "Reset view"),
    false,
  );
  assert.equal(allElements(dom.container).some((element) => element.getAttribute?.("aria-label") === "Search nodes"), false);
  assert.match(dom.byText("Workflow is valid").getAttribute("class"), /right-0/);

  await dom.flush();
  await dom.click(dom.byTitle("Auto-layout graph"));
  assert.deepEqual(changes.at(-1).nodes.map((node) => node.id), ["scan", "review", "archive"]);
  assert.ok(changes.at(-1).nodes[0].x < changes.at(-1).nodes[1].x);
  assert.ok(changes.at(-1).nodes[1].x < changes.at(-1).nodes[2].x);

  await dom.click(dom.byTitle("Fit graph"));
  await dom.click(dom.byTitle("Zoom in"));
  await dom.click(dom.byTitle("Zoom out"));

  const enterFullscreen = dom.byTitle("Enter full screen");
  assert.equal(enterFullscreen.getAttribute("aria-pressed"), "false");
  await dom.click(enterFullscreen);
  const exitFullscreen = dom.byTitle("Exit full screen");
  const fullscreenGraph = dom.ancestor(
    exitFullscreen,
    (node) => node.getAttribute?.("data-graph-fullscreen") === "true",
  );
  assert.match(fullscreenGraph.getAttribute("class"), /fixed inset-0/);
  assert.equal(exitFullscreen.getAttribute("aria-pressed"), "true");
  await dom.dispatchWindow("keydown", { key: "Escape" });
  assert.ok(dom.byTitle("Enter full screen"));

  await dom.click(dom.byTitle("Fit selection"));
  await dom.unmount();
});

test("DagCanvas minimap sits top left, handles translucent dark mode styling, and traps navigation events", async () => {
  const workflow = {
    ...workflowFixture({ id: "minimap", name: "Minimap", label: "Scan" }),
    nodes: [
      {
        id: "scan",
        type: "bash_command",
        label: "Scan",
        x: 40,
        y: 60,
        operation: { type: "bash_command", command: "find docs", working_dir: "" },
      },
      {
        id: "review",
        type: "agent",
        label: "Review docs",
        x: 420,
        y: 260,
        operation: { type: "agent", agent_id: "reviewer", prompt: "Review" },
      },
    ],
  };
  const dom = await mountReact(
    React.createElement(DagCanvasHarness, {
      dataDir: "/workspace",
      workflow,
      onWorkflowChange() {},
    }),
    createFetchMock([]),
  );

  await dom.click(dom.byTitle("Map"));
  const outline = dom.byLabel("Graph outline");
  let outlineWheelStopped = false;
  const outlineWheelEvent = testEvent(outline, {
    deltaY: -100,
    stopPropagation() {
      outlineWheelStopped = true;
    },
  });
  reactProps(outline).onWheel(outlineWheelEvent);
  assert.equal(outlineWheelStopped, true);
  assert.equal(outlineWheelEvent.defaultPrevented, false);

  await dom.click(dom.ancestor(dom.byText("Minimap"), "BUTTON"));
  const minimap = dom.byTitle("Minimap");
  assert.match(minimap.getAttribute("class"), /bg-slate-50/);

  const minimapSurface = minimap.childNodes[0];
  assert.match(minimapSurface.getAttribute("class"), /dark:bg-\[#1b1f22\]\/80/);
  assert.equal(minimapSurface.style.width, "100%");
  assert.equal(minimapSurface.style.height, "100%");
  assert.equal(typeof reactProps(minimapSurface).onPointerLeave, "function");

  const viewportIndicator = minimapSurface.childNodes[minimapSurface.childNodes.length - 1];
  const viewportWidthBeforeWheel = viewportIndicator.style.width;
  let wheelStopped = false;
  const wheelEvent = testEvent(minimapSurface, {
    deltaY: -100,
    stopPropagation() {
      wheelStopped = true;
    },
  });
  await React.act(async () => {
    reactProps(minimapSurface).onWheel(wheelEvent);
  });
  assert.equal(wheelEvent.defaultPrevented, true);
  assert.equal(wheelStopped, true);
  assert.notEqual(viewportIndicator.style.width, viewportWidthBeforeWheel);

  minimapSurface.getBoundingClientRect = () => ({
    bottom: 128,
    height: 128,
    left: 0,
    right: 184,
    top: 0,
    width: 184,
  });
  const bounds = canvasModule.graphBounds(workflow.nodes, 160);
  const minimapScale = Math.min(184 / bounds.width, 128 / bounds.height);
  assert.deepEqual(
    canvasModule.minimapPointToWorld(
      { clientX: 92, clientY: 64 },
      minimapSurface.getBoundingClientRect(),
      bounds,
    ),
    {
      x: bounds.left + 92 / minimapScale,
      y: bounds.top + 64 / minimapScale,
    },
  );
  const clickedWorld = canvasModule.minimapPointToWorld(
    { clientX: 80, clientY: 50 },
    minimapSurface.getBoundingClientRect(),
    bounds,
  );
  await dom.pointer(minimapSurface, "onPointerDown", {
    clientX: 80,
    clientY: 50,
    pointerId: 9,
  });
  const scanCard = allElements(dom.container).find(
    (element) => element.tagName === "ARTICLE" && textOf(element).includes("Scan"),
  );
  const viewportTransform = scanCard.parentNode.style.transform.match(
    /translate\(([-\d.]+)px, ([-\d.]+)px\) scale\(([-\d.]+)\)/,
  );
  assert.ok(viewportTransform);
  const viewportScale = Number(viewportTransform[3]);
  assert.ok(Math.abs(Number(viewportTransform[1]) - (480 - clickedWorld.x * viewportScale)) < 0.001);
  assert.ok(Math.abs(Number(viewportTransform[2]) - (320 - clickedWorld.y * viewportScale)) < 0.001);
  await dom.pointer(minimapSurface, "onPointerMove", {
    clientX: 240,
    clientY: 50,
    pointerId: 9,
  });

  await dom.unmount();
});

test("Electron preload exposes stable desktop and update bridge contracts", async () => {
  const exposed = runPreload({
    argv: ["electron", "preload", "--gofer-api-base-url=http://localhost:9000"],
    invoke(channel, payload) {
      if (["gofer:grant-path", "gofer:grant-dropped-path"].includes(channel)) {
        return { grantId: `grant-${payload.targetPath}`, path: payload.targetPath };
      }
      return { channel, payload };
    },
  });

  assert.equal(exposed.goferApiBaseUrl, "http://localhost:9000");
  assert.deepEqual(Object.keys(exposed.goferDesktop).sort(), [
    "apiSession",
    "appearance",
    "dataDirectory",
    "developer",
    "getDataDir",
    "getDroppedFilePath",
    "grantDroppedPath",
    "rem",
    "textFiles",
    "workspace",
  ]);
  assert.equal(exposed.goferDesktop.appearance.setZoomFactor(2), 1.5);
  assert.equal(exposed.zoomFactors.at(-1), 1.5);
  assert.deepEqual(Object.keys(exposed.goferDesktop.workspace).sort(), [
    "addWorktree",
    "copyPath",
    "createFile",
    "createFolder",
    "deletePath",
    "getPathInfo",
    "gitBranches",
    "gitFileAction",
    "gitFileBaseline",
    "gitHistory",
    "gitRepoAction",
    "gitStatus",
    "gitSwitchBranch",
    "gitWorktrees",
    "grantUserPath",
    "listDirectory",
    "missingRecentFiles",
    "missingThreadRoots",
    "openPath",
    "pathGrantForApi",
    "removeWorktree",
    "renamePath",
    "replaceProject",
    "resolveProjectFile",
    "revealPath",
    "searchProject",
    "selectPath",
    "trustProjectRoot",
  ]);
  assert.deepEqual(Object.keys(exposed.goferDesktop.textFiles).sort(), ["read", "readPreview", "write"]);
  assert.deepEqual(Object.keys(exposed.goferDesktop.dataDirectory).sort(), ["choose", "get"]);
  assert.deepEqual(Object.keys(exposed.goferBrowser).sort(), [
    "adopt",
    "back",
    "close",
    "create",
    "focus",
    "forward",
    "navigate",
    "onCommand",
    "onOpenFile",
    "onOpenTab",
    "onState",
    "openExternal",
    "platform",
    "preloadPath",
    "reload",
    "setPreferences",
    "stop",
  ]);
  assert.deepEqual(Object.keys(exposed.goferUpdates).sort(), [
    "check",
    "downloadAndInstall",
    "getState",
    "installDownloaded",
    "onState",
    "openRelease",
  ]);
  assert.deepEqual(Object.keys(exposed.goferTerminal).sort(), [
    "close",
    "completeEditor",
    "create",
    "onData",
    "onExit",
    "onOpenEditor",
    "resize",
    "write",
  ]);

  assert.deepEqual(toPlainObject(await exposed.goferDesktop.workspace.listDirectory({ currentPath: 42, create: false })), {
    channel: "gofer:list-directory",
    payload: { currentPath: "", grantId: "", create: false },
  });
  assert.deepEqual(toPlainObject(await exposed.goferDesktop.workspace.gitStatus("/workspace/project")), {
    channel: "gofer:git-status",
    payload: { grantId: "", projectRoot: "/workspace/project" },
  });
  assert.deepEqual(toPlainObject(await exposed.goferDesktop.workspace.gitFileBaseline("/workspace/project/app.js")), {
    channel: "gofer:git-file-baseline",
    payload: { grantId: "", targetPath: "/workspace/project/app.js" },
  });
  assert.deepEqual(toPlainObject(await exposed.goferDesktop.workspace.gitHistory("/workspace/project")), {
    channel: "gofer:git-history",
    payload: { grantId: "", projectRoot: "/workspace/project" },
  });
  assert.deepEqual(toPlainObject(await exposed.goferDesktop.workspace.gitWorktrees("/workspace/project")), {
    channel: "gofer:git-worktrees",
    payload: { grantId: "", projectRoot: "/workspace/project" },
  });
  assert.deepEqual(toPlainObject(await exposed.goferDesktop.workspace.resolveProjectFile("/workspace/project/app.js")), {
    channel: "gofer:resolve-project-file",
    payload: { grantId: "", selectedPath: "/workspace/project/app.js" },
  });
  assert.deepEqual(toPlainObject(await exposed.goferDesktop.workspace.copyPath({ sourcePath: "/a", destinationPath: 9 })), {
    channel: "gofer:copy-path",
    payload: { destinationGrantId: "", sourcePath: "/a", sourceGrantId: "", destinationPath: "" },
  });
  assert.equal(
    await exposed.goferDesktop.grantDroppedPath({ path: "/outside/file.txt" }),
    "/outside/file.txt",
  );
  assert.deepEqual(toPlainObject(await exposed.goferTerminal.create({
    cols: 120,
    cwd: "/outside/ungranted",
    rows: 40,
  })), {
    channel: "gofer:terminal-create",
    payload: {
      cols: 120,
      cwd: "/outside/ungranted",
      grantId: "grant-/outside/ungranted",
      rows: 40,
    },
  });
  assert.deepEqual(toPlainObject(await exposed.goferTerminal.write("terminal-1", "pwd\r")), {
    channel: "gofer:terminal-write",
    payload: { data: "pwd\r", id: "terminal-1" },
  });
  assert.deepEqual(toPlainObject(await exposed.goferBrowser.create({
    clientId: "browser:1",
    path: "/workspace/project/index.html",
  })), {
    channel: "gofer:browser-create",
    payload: {
      clientId: "browser:1",
      grantId: "grant-/workspace/project/index.html",
      path: "/workspace/project/index.html",
      url: "",
    },
  });
  assert.deepEqual(
    toPlainObject(await exposed.goferBrowser.navigate("browser-session", "localhost:5173")),
    {
      channel: "gofer:browser-action",
      payload: { action: "navigate", id: "browser-session", url: "localhost:5173" },
    },
  );
});

test("Electron worktree removal bridge forwards force only when explicitly true", async () => {
  const exposed = runPreload({
    argv: ["electron", "preload"],
    invoke(channel, payload) { return { channel, payload }; },
  });
  for (const force of [undefined, false, "true", true]) {
    const result = await exposed.goferDesktop.workspace.removeWorktree({
      projectRoot: "/workspace/main", targetPath: "/workspace/feature", force,
    });
    assert.equal(result.channel, "gofer:git-worktree-remove");
    assert.equal(result.payload.force, force === true);
  }
});

test("Electron preload keeps file grants private while attaching them to later calls", async () => {
  const calls = [];
  const exposed = runPreload({
    argv: ["electron", "preload"],
    invoke(channel, payload) {
      calls.push({ channel, payload });
      if (channel === "gofer:select-path") {
        return { grantId: "grant-1", path: "/outside/shared" };
      }
      if (channel === "gofer:path-info") {
        return { basename: "shared", grantId: "grant-1", isDirectory: true, path: payload.targetPath };
      }
      if (channel === "gofer:resolve-project-file") {
        return {
          directory: "/outside/project",
          grantId: "grant-project",
          selectedPath: payload.selectedPath,
        };
      }
      return { channel, payload };
    },
  });

  assert.equal(await exposed.goferDesktop.workspace.selectPath({}), "/outside/shared");
  assert.deepEqual(
    toPlainObject(await exposed.goferDesktop.workspace.resolveProjectFile("/outside/shared")),
    { directory: "/outside/project", selectedPath: "/outside/shared" },
  );
  assert.equal(exposed.goferDesktop.workspace.pathGrantForApi("/outside/project"), "grant-project");
  assert.deepEqual(toPlainObject(await exposed.goferDesktop.workspace.getPathInfo("/outside/shared")), {
    basename: "shared",
    isDirectory: true,
    path: "/outside/shared",
  });
  assert.deepEqual(toPlainObject(calls.at(-1)), {
    channel: "gofer:path-info",
    payload: {
      grantId: "grant-1",
      targetPath: "/outside/shared",
    },
  });
  assert.equal(exposed.goferDesktop.workspace.pathGrantForApi("/outside/shared"), "grant-1");
  assert.deepEqual(toPlainObject(await exposed.goferDesktop.workspace.searchProject("/outside/shared", { query: "hello", grantId: "spoofed" })), {
    channel: "gofer:search-project",
    payload: { projectRoot: "/outside/shared", query: "hello", grantId: "grant-1" },
  });
  assert.deepEqual(toPlainObject(await exposed.goferDesktop.workspace.replaceProject("/outside/shared", { query: "hello", replacement: "hi", files: [], grantId: "spoofed" })), {
    channel: "gofer:replace-project",
    payload: { projectRoot: "/outside/shared", query: "hello", replacement: "hi", files: [], grantId: "grant-1" },
  });
  assert.deepEqual(toPlainObject(await exposed.goferDesktop.workspace.listDirectory({ currentPath: "/outside/shared" })), {
    channel: "gofer:list-directory",
    payload: {
      create: true,
      currentPath: "/outside/shared",
      grantId: "grant-1",
    },
  });
});

test("Electron preload refreshes an existing project grant with the backend", async () => {
  const calls = [];
  const exposed = runPreload({
    argv: ["electron", "preload"],
    invoke(channel, payload) {
      calls.push({ channel, payload });
      if (["gofer:grant-path", "gofer:grant-dropped-path"].includes(channel)) {
        return { grantId: "grant-project", path: payload.targetPath };
      }
      return { channel, payload };
    },
  });

  await exposed.goferDesktop.workspace.trustProjectRoot("/workspace/project");
  await exposed.goferDesktop.workspace.trustProjectRoot("/workspace/project");

  assert.deepEqual(toPlainObject(calls), [
    {
      channel: "gofer:grant-path",
      payload: { targetPath: "/workspace/project" },
    },
    {
      channel: "gofer:grant-path",
      payload: { targetPath: "/workspace/project" },
    },
  ]);
  assert.equal(
    exposed.goferDesktop.workspace.pathGrantForApi("/workspace/project"),
    "grant-project",
  );
});

test("Electron preload changes data directory through native directory grants", async () => {
  const calls = [];
  const exposed = runPreload({
    argv: ["electron", "preload"],
    invoke(channel, payload) {
      calls.push({ channel, payload });
      if (channel === "gofer:select-path") {
        return { grantId: "grant-data", path: "/outside/gofer-data" };
      }
      if (channel === "gofer:set-data-dir") {
        return { dataDir: payload.dataDir };
      }
      return { channel, payload };
    },
  });

  assert.deepEqual(
    toPlainObject(await exposed.goferDesktop.dataDirectory.choose({ currentPath: "/old-data" })),
    { dataDir: "/outside/gofer-data" },
  );
  assert.deepEqual(toPlainObject(calls), [
    {
      channel: "gofer:select-path",
      payload: { currentPath: "/old-data", directoryOnly: true, grantId: "" },
    },
    {
      channel: "gofer:set-data-dir",
      payload: { dataDir: "/outside/gofer-data", grantId: "grant-data" },
    },
  ]);
});

test("Electron preload rejects unsafe remote API base URLs", () => {
  const exposed = runPreload({
    argv: ["electron", "preload", "--gofer-api-base-url=https://example.com"],
  });

  assert.equal(exposed.goferApiBaseUrl, "http://127.0.0.1:8765");
});

function runBrowserPreload(location = "https://example.com/start") {
  const listeners = new Map();
  const sent = [];
  const source = fs.readFileSync(
    path.join(repoRoot, "frontend/electron/browser-preload.cjs"),
    "utf8",
  );
  const sandbox = {
    URL: globalThis.URL,
    require(moduleName) {
      if (moduleName !== "electron") {
        throw new Error(`Unexpected browser preload require: ${moduleName}`);
      }
      return {
        ipcRenderer: {
          send(channel, payload) {
            sent.push({ channel, payload });
          },
        },
      };
    },
    window: {
      addEventListener(type, listener) {
        listeners.set(type, listener);
      },
      location: { href: location },
    },
  };

  vm.runInNewContext(source, sandbox, { filename: "browser-preload.cjs" });
  return {
    dispatch(type, event) {
      listeners.get(type)?.(event);
    },
    sent,
  };
}

function browserPageEvent(patch = {}) {
  return {
    altKey: false,
    button: 0,
    composedPath: () => [],
    ctrlKey: false,
    defaultPrevented: false,
    key: "",
    metaKey: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    repeat: false,
    shiftKey: false,
    stopPropagation() {},
    ...patch,
  };
}

function runPreload({ argv, invoke }) {
  const exposed = {};
  const listeners = new Map();
  const zoomFactors = [];
  const source = fs.readFileSync(path.join(repoRoot, "frontend/electron/preload.cjs"), "utf8");
  const sandbox = {
    URL: globalThis.URL,
    process: { argv },
    require(moduleName) {
      if (moduleName !== "electron") {
        throw new Error(`Unexpected preload require: ${moduleName}`);
      }
      return {
        contextBridge: {
          exposeInMainWorld(key, value) {
            exposed[key] = value;
          },
        },
        ipcRenderer: {
          invoke(channel, payload) {
            return typeof invoke === "function" ? invoke(channel, payload) : { channel, payload };
          },
          on(channel, listener) {
            listeners.set(channel, listener);
          },
          removeListener(channel, listener) {
            if (listeners.get(channel) === listener) {
              listeners.delete(channel);
            }
          },
        },
        webFrame: {
          setZoomFactor(value) {
            zoomFactors.push(value);
          },
        },
        webUtils: {
          getPathForFile(file) {
            return file?.path ?? "";
          },
        },
      };
    },
  };

  vm.runInNewContext(source, sandbox, { filename: "preload.cjs" });
  exposed.zoomFactors = zoomFactors;
  return exposed;
}

function toPlainObject(value) {
  return JSON.parse(JSON.stringify(value));
}

function DagCanvasHarness({
  approvalState,
  dataDir,
  logState,
  notice,
  onDecideApproval,
  onPruneRunLogs,
  onRattishMutation,
  onReplayRunLog,
  onRetentionSettingsChange,
  onResumeRunLog,
  onWorkflowChange,
  retentionSettings,
  rattishDocument,
  workflow,
}) {
  const [currentWorkflow, setCurrentWorkflow] = React.useState(workflow);
  const [currentRattishDocument, setCurrentRattishDocument] = React.useState(rattishDocument);
  const [currentRetentionSettings, setCurrentRetentionSettings] = React.useState(
    retentionSettings,
  );

  function handleChange(nextWorkflow) {
    setCurrentWorkflow(nextWorkflow);
    onWorkflowChange(nextWorkflow);
  }

  function handleRetentionSettingsChange(nextSettings) {
    setCurrentRetentionSettings(nextSettings);
    onRetentionSettingsChange?.(nextSettings);
  }

  async function handleRattishMutation(mutations) {
    const nextDocument = await onRattishMutation?.(mutations);
    if (nextDocument) {
      setCurrentRattishDocument(nextDocument);
      setCurrentWorkflow((current) => appModule.rattishGraphWorkflow(current, nextDocument));
    }
    return nextDocument;
  }

  return React.createElement(canvasModule.default, {
    dataDir,
    logState: logState ?? { loading: false, error: "", text: "", path: null, runs: [] },
    notice,
    retentionSettings: currentRetentionSettings,
    rattishDocument: currentRattishDocument,
    approvalState: approvalState ?? { approvals: [], error: "", loading: false },
    runResult: null,
    runState: { running: false },
    usedAgentIds: [],
    workflow: currentWorkflow,
    onImportWorkflow: () => {},
    onLoadLatestLog: () => {},
    onPruneRunLogs: onPruneRunLogs ?? (() => {}),
    onRattishMutation: handleRattishMutation,
    onReplayRunLog: onReplayRunLog ?? (() => {}),
    onRetentionSettingsChange: handleRetentionSettingsChange,
    onResumeRunLog: onResumeRunLog ?? (() => {}),
    onRunWorkflow: () => {},
    onSelectRunLog: () => {},
    onStopRunLog: () => {},
    onStopWorkflow: () => {},
    onValidateWorkflow: () => {},
    onDecideApproval: onDecideApproval ?? (() => {}),
    onWorkflowChange: handleChange,
  });
}

async function openWorkflowSettingsFromMenu(dom) {
  assert.equal(
    allElements(dom.container).some((element) =>
      String(element.getAttribute?.("title") ?? "").startsWith("Show workflow settings")),
    false,
  );
  await dom.click(dom.byTitle("More graph actions"));
  await dom.click(dom.byText("Workflow settings"));
}

function headingByText(dom, text) {
  return allElements(dom.container).find(
    (element) => element.tagName === "H2" && textOf(element) === text,
  );
}

function InspectorDraftHarness({ changes }) {
  const [keyValue, setKeyValue] = React.useState({});
  const [list, setList] = React.useState([]);
  const [number, setNumber] = React.useState(3);
  const [pathValue, setPathValue] = React.useState("scripts/run.sh");

  return React.createElement(
    "div",
    null,
    React.createElement(canvasModule.NumberField, {
      label: "Draft number",
      min: "-10",
      step: "0.1",
      value: number,
      onChange(nextValue) {
        changes.number.push(nextValue);
        setNumber(nextValue);
      },
    }),
    React.createElement(
      "button",
      { type: "button", onClick: () => setNumber(11) },
      "Update number externally",
    ),
    React.createElement(canvasModule.ListField, {
      label: "Draft list",
      value: list,
      onChange(nextValue) {
        changes.list.push(nextValue);
        setList(nextValue);
      },
    }),
    React.createElement(canvasModule.KeyValueField, {
      label: "Draft key/value",
      value: keyValue,
      onChange(nextValue) {
        changes.keyValue.push(nextValue);
        setKeyValue(nextValue);
      },
    }),
    React.createElement(canvasModule.TextField, {
      label: "Draft path",
      pathBasePath: "/workspace",
      pathPicker: true,
      value: pathValue,
      onChange(nextValue) {
        changes.path.push(nextValue);
        setPathValue(nextValue);
      },
    }),
  );
}

function workflowFixture({ id = "demo", name = "Demo", label = "Run command", status = "Ready" } = {}) {
  return {
    id,
    name,
    description: `${name} workflow`,
    status,
    tags: [status.toLowerCase()],
    agents: {},
    edges: [],
    nodes: [
      {
        id: "step",
        type: "bash_command",
        label,
        x: 0,
        y: 0,
        operation: { type: "bash_command", command: "echo hi", working_dir: "" },
      },
    ],
    sourcePath: `/tmp/${id}.toml`,
    projectRoot: "/workspace",
    projectName: "workspace",
  };
}

function workflowsPayload(workflows) {
  return { dataDir: "/workspace", promptAgentIds: [], workflows };
}

function jsonResponse(url, payload, { method = "GET", ok = true, status = ok ? 200 : 500 } = {}) {
  return (requestUrl, options = {}) => {
    if (requestUrl !== url || (options.method ?? "GET") !== method) return null;
    return {
      ok,
      status,
      json: async () => payload,
    };
  };
}

function saveWorkflowResponse() {
  return (url, options = {}) => {
    if (!url.startsWith("/api/workflows/") || options.method !== "PUT") return null;
    const workflow = JSON.parse(options.body);
    return {
      ok: true,
      status: 200,
      json: async () => ({ workflow }),
    };
  };
}

function createDeferred() {
  let resolve, reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function streamResponse(chunks) {
  return (url) => ({
    ok: true,
    status: 200,
    body: {
      getReader() {
        let index = 0;
        return {
          async read() {
            if (index >= chunks.length) return { done: true, value: undefined };
            const value = new TextEncoder().encode(chunks[index]);
            index += 1;
            return { done: false, value };
          },
        };
      },
    },
    json: async () => ({}),
    url,
  });
}

function controlledStreamResponse(chunks) {
  const pendingReads = chunks.map(() => createDeferred());
  return {
    releaseNext() {
      const nextRead = pendingReads.find((deferred) => !deferred.released);
      if (!nextRead) return;
      nextRead.released = true;
      nextRead.resolve();
    },
    response(url) {
      return {
        ok: true,
        status: 200,
        body: {
          getReader() {
            let index = 0;
            return {
              async read() {
                if (index >= chunks.length) return { done: true, value: undefined };
                const deferred = pendingReads[index];
                await deferred.promise;
                const value = new TextEncoder().encode(chunks[index]);
                index += 1;
                return { done: false, value };
              },
            };
          },
        },
        json: async () => ({}),
        url,
      };
    },
  };
}

function createFetchMock(handlers) {
  const calls = [];
  const fetchMock = async (url, options = {}) => {
    calls.push({ url, options });
    for (const handler of handlers) {
      const response = handler(url, options);
      if (response) return response;
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
  fetchMock.calls = calls;
  return fetchMock;
}

async function exerciseDialogFamily(renderDialog, desktop = {}) {
  function DialogFamilyHarness() {
    const [open, setOpen] = React.useState(false);
    return React.createElement(
      React.Fragment,
      null,
      React.createElement(
        "button",
        {
          "aria-label": "Open dialog family",
          onClick: () => setOpen(true),
          type: "button",
        },
        "Open",
      ),
      open ? renderDialog(() => setOpen(false)) : null,
    );
  }

  const dom = await mountReact(
    React.createElement(DialogFamilyHarness),
    createFetchMock([]),
    { desktop },
  );
  const opener = dom.byLabel("Open dialog family");
  await dom.focus(opener);
  await dom.click(opener);
  await dom.flush();

  const dialog = allElements(dom.container).find(
    (element) => element.getAttribute?.("role") === "dialog",
  );
  assert.ok(dialog, "Expected the family to render a dialog");
  assert.equal(dialog.getAttribute("aria-modal"), "true");
  assert.ok(dialog.getAttribute("aria-labelledby"));
  assert.ok(dialog.getAttribute("aria-describedby"));
  assert.equal(dialog.contains(document.activeElement), true, "Initial focus escaped the dialog");

  const focusable = allElements(dialog).filter(
    (element) =>
      !element.disabled &&
      ["BUTTON", "INPUT", "SELECT", "TEXTAREA"].includes(element.tagName),
  );
  assert.ok(focusable.length > 0, "Expected at least one focusable dialog control");
  const first = focusable[0];
  const last = focusable.at(-1);

  await dom.focus(last);
  await dom.dispatchWindow("keydown", { key: "Tab" });
  assert.equal(document.activeElement, first, "Tab did not wrap to the first control");

  await dom.focus(first);
  await dom.dispatchWindow("keydown", { key: "Tab", shiftKey: true });
  assert.equal(document.activeElement, last, "Shift+Tab did not wrap to the last control");

  await dom.dispatchWindow("keydown", { key: "Escape" });
  assert.equal(document.activeElement, opener, "Focus did not return to the opener");
  assert.equal(
    allElements(dom.container).some((element) => element.getAttribute?.("role") === "dialog"),
    false,
    "Escape did not close the dialog",
  );

  await dom.unmount();
}

async function mountReact(element, fetchMock, { browser, desktop = {}, storage = {} } = {}) {
  const dom = installTestDom();
  const { createRoot } = require("react-dom/client");
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.fetch = fetchMock;
  globalThis.window.fetch = fetchMock;
  globalThis.window.goferApiBaseUrl = undefined;
  globalThis.window.goferBrowser = browser;
  globalThis.window.goferDesktop = desktop;
  globalThis.window.goferUpdates = undefined;
  for (const [key, value] of Object.entries(storage)) {
    globalThis.window.localStorage.setItem(key, value);
  }
  globalThis.window.confirm = () => true;
  globalThis.window.requestAnimationFrame = (callback) => {
    callback();
    return 1;
  };
  globalThis.window.cancelAnimationFrame = () => {};

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await React.act(async () => {
    root.render(element);
  });

  return {
    container,
    fetchCalls: fetchMock.calls,
    async change(elementNode, value) {
      await React.act(async () => {
        elementNode.value = value;
        elementNode.checked = Boolean(value);
        reactProps(elementNode).onChange?.({ target: elementNode, currentTarget: elementNode });
      });
    },
    async click(elementNode) {
      await React.act(async () => {
        reactProps(elementNode).onClick?.(testEvent(elementNode));
      });
    },
    async blur(elementNode) {
      await React.act(async () => {
        reactProps(elementNode).onBlur?.(testEvent(elementNode));
        elementNode.blur();
      });
    },
    async dispatchWindow(type, patch = {}) {
      await React.act(async () => {
        const event = Object.assign(new TestEvent(type), {
          defaultPrevented: false,
          preventDefault() {
            this.defaultPrevented = true;
          },
          stopPropagation() {},
          ...patch,
        });
        for (const listener of document.listeners[type] ?? []) {
          listener(event);
        }
        await Promise.resolve();
      });
    },
    async flush(ms = 0) {
      await React.act(async () => {
        if (ms > 0) {
          dom.runTimers(ms);
        }
        await Promise.resolve();
        await Promise.resolve();
      });
    },
    async focus(elementNode) {
      await React.act(async () => {
        elementNode.focus();
        reactProps(elementNode).onFocus?.(testEvent(elementNode));
      });
    },
    async keyDown(elementNode, key, patch = {}) {
      await React.act(async () => {
        reactProps(elementNode).onKeyDown?.(testEvent(elementNode, { key, ...patch }));
      });
    },
    async pointer(elementNode, handlerName, patch = {}) {
      await React.act(async () => {
        reactProps(elementNode)[handlerName]?.(testEvent(elementNode, patch));
      });
    },
    async unmount() {
      await React.act(async () => {
        root.unmount();
      });
      dom.restore();
    },
    allByTitle(title) {
      return allElements(container).filter((node) => node.getAttribute?.("title") === title);
    },
    ancestor(elementNode, tagNameOrPredicate) {
      let current = elementNode;
      const matches =
        typeof tagNameOrPredicate === "function"
          ? tagNameOrPredicate
          : (node) => node.tagName === tagNameOrPredicate;
      while (current && !matches(current)) {
        current = current.parentNode;
      }
      assert.ok(current, "Unable to find matching ancestor");
      return current;
    },
    byText(text) {
      const match = allElements(container).find((node) =>
        directText(node).includes(text),
      );
      assert.ok(match, `Unable to find text: ${text}`);
      return match;
    },
    byExactText(text) {
      const match = allElements(container).find((node) => directText(node) === text);
      assert.ok(match, `Unable to find exact text: ${text}`);
      return match;
    },
    byTitle(title) {
      const match = allElements(container).find((node) => node.getAttribute?.("title") === title);
      assert.ok(match, `Unable to find title: ${title}`);
      return match;
    },
    byLabel(label) {
      const match = allElements(container).find((node) => node.getAttribute?.("aria-label") === label);
      assert.ok(match, `Unable to find aria-label: ${label}`);
      return match;
    },
    controlAfterLabel(labelText) {
      const label = allElements(container).find((node) =>
        node.tagName === "LABEL" && textOf(node).includes(labelText) && !isInactiveGraphDescendant(node),
      );
      assert.ok(label, `Unable to find label: ${labelText}`);
      const control = allElements(label).find((node) =>
        ["INPUT", "SELECT", "TEXTAREA"].includes(node.tagName),
      );
      assert.ok(control, `Unable to find control for label: ${labelText}`);
      return control;
    },
    first(tagName) {
      const match = allElements(container).find((node) => node.tagName === tagName.toUpperCase());
      assert.ok(match, `Unable to find ${tagName}`);
      return match;
    },
    selectWithOption(value) {
      const match = allElements(container).find(
        (node) => node.tagName === "SELECT" && [...(node.options ?? [])].some((option) => option.value === value),
      );
      assert.ok(match, `Unable to find select with option: ${value}`);
      return match;
    },
    text() {
      return textOf(container);
    },
  };
}

function isInactiveGraphDescendant(node) {
  for (let current = node; current; current = current.parentNode) {
    if (current.getAttribute?.("data-graph-active") === "false") return true;
  }
  return false;
}

function testEvent(target, patch = {}) {
  return {
    button: 0,
    buttons: 1,
    clientX: 0,
    clientY: 0,
    currentTarget: target,
    defaultPrevented: false,
    pointerId: 1,
    preventDefault() {
      this.defaultPrevented = true;
    },
    stopPropagation() {},
    target,
    ...patch,
  };
}

function reactProps(node) {
  const key = Object.keys(node).find((candidate) => candidate.startsWith("__reactProps$"));
  assert.ok(key, `No React props found on ${node.tagName ?? node.nodeName}`);
  return node[key];
}

function installTestDom() {
  const previousNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const previous = {
    document: globalThis.document,
    fetch: globalThis.fetch,
    HTMLElement: globalThis.HTMLElement,
    HTMLIFrameElement: globalThis.HTMLIFrameElement,
    Node: globalThis.Node,
    SVGElement: globalThis.SVGElement,
    window: globalThis.window,
  };
  const timers = [];
  const windowObject = {};
  const documentObject = new TestDocument(windowObject);
  Object.assign(windowObject, {
    document: documentObject,
    Event: TestEvent,
    HTMLElement: TestElement,
    HTMLIFrameElement: TestElement,
    Node: TestNode,
    SVGElement: TestElement,
    addEventListener: (...args) => documentObject.addEventListener(...args),
    clearInterval: (id) => clearTimer(timers, id),
    clearTimeout: (id) => clearTimer(timers, id),
    getComputedStyle: () => ({}),
    localStorage: createStorage(),
    navigator: { clipboard: { writeText: async () => {} }, userAgent: "node-test" },
    removeEventListener: (...args) => documentObject.removeEventListener(...args),
    scrollTo: () => {},
    setInterval: (callback, delay) => addTimer(timers, callback, delay, true),
    setTimeout: (callback, delay) => addTimer(timers, callback, delay, false),
  });

  globalThis.window = windowObject;
  globalThis.document = documentObject;
  globalThis.HTMLElement = TestElement;
  globalThis.HTMLIFrameElement = TestElement;
  globalThis.Node = TestNode;
  globalThis.SVGElement = TestElement;
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: windowObject.navigator,
    writable: true,
  });

  return {
    restore() {
      Object.assign(globalThis, previous);
      if (previousNavigatorDescriptor) {
        Object.defineProperty(globalThis, "navigator", previousNavigatorDescriptor);
      } else {
        delete globalThis.navigator;
      }
    },
    runTimers(ms) {
      const runnable = timers.filter((timer) => timer.delay <= ms);
      for (const timer of runnable) {
        timer.callback();
        if (!timer.repeating) {
          clearTimer(timers, timer.id);
        }
      }
    },
  };
}

function addTimer(timers, callback, delay = 0, repeating = false) {
  const timer = { callback, delay, id: timers.length + 1, repeating };
  timers.push(timer);
  return timer.id;
}

function clearTimer(timers, id) {
  const index = timers.findIndex((timer) => timer.id === id);
  if (index >= 0) timers.splice(index, 1);
}

function createStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(key, String(value)),
  };
}

class TestNode {
  constructor() {
    this.childNodes = [];
    this.listeners = {};
    this.parentNode = null;
  }

  appendChild(node) {
    return this.insertBefore(node, null);
  }

  contains(node) {
    let current = node;
    while (current) {
      if (current === this) return true;
      current = current.parentNode;
    }
    return false;
  }

  addEventListener(type, listener) {
    this.listeners[type] = [...(this.listeners[type] ?? []), listener];
  }

  removeEventListener(type, listener) {
    this.listeners[type] = (this.listeners[type] ?? []).filter((candidate) => candidate !== listener);
  }

  insertBefore(node, beforeNode) {
    if (node.parentNode) node.parentNode.removeChild(node);
    node.parentNode = this;
    if (beforeNode === null || beforeNode === undefined) {
      this.childNodes.push(node);
    } else {
      this.childNodes.splice(this.childNodes.indexOf(beforeNode), 0, node);
    }
    return node;
  }

  removeChild(node) {
    this.childNodes = this.childNodes.filter((child) => child !== node);
    node.parentNode = null;
    return node;
  }
}

class TestElement extends TestNode {
  constructor(tagName, ownerDocument) {
    super();
    this.attributes = {};
    this.checked = false;
    this.disabled = false;
    this.localName = tagName;
    this.namespaceURI = "http://www.w3.org/1999/xhtml";
    this.nodeName = tagName.toUpperCase();
    this.nodeType = 1;
    this.ownerDocument = ownerDocument;
    this.style = {};
    this.tagName = tagName.toUpperCase();
    this.value = "";
  }

  blur() {
    this.ownerDocument.activeElement = null;
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  querySelector() {
    return null;
  }

  hasPointerCapture() {
    return true;
  }

  getBoundingClientRect() {
    return {
      bottom: 640,
      height: 640,
      left: 0,
      right: 960,
      top: 0,
      width: 960,
    };
  }

  releasePointerCapture() {}

  setPointerCapture() {}

  getAttribute(name) {
    return this.attributes[name] ?? null;
  }

  removeAttribute(name) {
    delete this.attributes[name];
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "value") this.value = String(value);
  }

  get textContent() {
    return this.childNodes.map((node) => node.textContent).join("");
  }

  get options() {
    return this.tagName === "SELECT"
      ? allElements(this).filter((node) => node.tagName === "OPTION")
      : undefined;
  }

  set textContent(value) {
    this.childNodes = [new TestText(value, this.ownerDocument)];
  }
}

class TestText extends TestNode {
  constructor(value, ownerDocument) {
    super();
    this.nodeName = "#text";
    this.nodeType = 3;
    this.nodeValue = String(value);
    this.ownerDocument = ownerDocument;
  }

  get textContent() {
    return this.nodeValue;
  }

  set textContent(value) {
    this.nodeValue = String(value);
  }
}

class TestDocument extends TestNode {
  constructor(defaultView) {
    super();
    this.activeElement = null;
    this.defaultView = defaultView;
    this.documentElement = new TestElement("html", this);
    this.body = new TestElement("body", this);
    this.nodeName = "#document";
    this.nodeType = 9;
    this.appendChild(this.documentElement);
    this.documentElement.appendChild(this.body);
  }

  createComment(value) {
    return new TestText(value, this);
  }

  createElement(tagName) {
    return new TestElement(tagName, this);
  }

  createElementNS(namespaceURI, tagName) {
    const element = new TestElement(tagName, this);
    element.namespaceURI = namespaceURI;
    return element;
  }

  createTextNode(value) {
    return new TestText(value, this);
  }

  getElementById() {
    return null;
  }
}

class TestEvent {}

function allElements(root) {
  const elements = [];
  for (const child of root.childNodes ?? []) {
    if (child.nodeType === 1) {
      elements.push(child);
      elements.push(...allElements(child));
    }
  }
  return elements;
}

function matchingLiveRegions(container, { politeness, role, text }) {
  return allElements(container).filter(
    (element) =>
      element.getAttribute?.("aria-live") === politeness &&
      element.getAttribute?.("role") === role &&
      textOf(element).includes(text),
  );
}

function directText(node) {
  return (node.childNodes ?? [])
    .filter((child) => child.nodeType === 3)
    .map((child) => child.textContent)
    .join("");
}

function textOf(node) {
  return node.textContent ?? "";
}

test("Rem pages older threads and bumps active history without loading messages", async () => {
  const threads = Array.from({ length: 31 }, (_, index) => ({ id: `t${index}`, title: `Thread ${index}`, updatedAt: new Date(2026, 0, 31 - index).toISOString() }));
  const bumped = appModule.bumpChatThread(threads, "t30", "2026-09-08T12:00:00Z");
  assert.equal(bumped[0].id, "t30");
  assert.deepEqual(bumped.slice(1).map((thread) => thread.id), threads.slice(0, 30).map((thread) => thread.id));
  const dom = await mountReact(React.createElement(appModule.ThreadList, { threads: bumped, onOpen() {}, onDelete() {} }), createFetchMock([]));
  assert.equal(dom.allByTitle("Archive thread").length, 15);
  await dom.click(dom.byText("Show older threads"));
  assert.equal(dom.allByTitle("Archive thread").length, 30);
  await dom.click(dom.byText("Collapse older threads"));
  assert.equal(dom.allByTitle("Archive thread").length, 15);
  await dom.unmount();
});

test("Rem large pastes become text attachments without entering the draft", async () => {
  const text = "Large reference data\n".repeat(2000);
  const file = chatAttachmentsModule.largePasteFile(text);
  assert.equal(await file.text(), text);
  assert.equal(file.type, "text/plain");
  assert.equal(chatAttachmentsModule.largePasteFile("small edit"), null);
  const dom = await mountReact(React.createElement(appModule.ChatPane, { workflows: [], width: 380 }), createFetchMock([jsonResponse("/api/provider/capabilities", { providers: [] })]));
  const pane = allElements(dom.container).find((node) => node.getAttribute?.("data-chat-pane") === "true");
  await dom.pointer(pane, "onPaste", { clipboardData: { items: [], getData: () => text } });
  assert.equal(dom.first("textarea").value, "");
  assert.match(dom.text(), /pasted-text-.*\.txt/);
  await dom.unmount();
});

test("Rem resource drafts allow clear, type, blur and survive row removal", async () => {
  const { default: RemResources } = await viteServer.ssrLoadModule("/src/components/RemResources.jsx");
  let saved;
  function Editor() {
    const [value, setValue] = React.useState({ shell: true, web: false, skills: [{ path: "/one" }, { path: "/two" }], mcpServers: [] });
    return React.createElement(RemResources, { value, onChange: (next) => { saved = next; setValue(next); } });
  }
  const dom = await mountReact(React.createElement(Editor), createFetchMock([]));
  let field = dom.byLabel("Skill 1 path");
  await dom.pointer(field, "onFocus");
  await dom.change(field, "");
  assert.equal(field.value, "");
  await dom.change(field, "/replacement");
  await dom.pointer(field, "onBlur");
  assert.equal(saved.skills[0].path, "/replacement");
  await dom.click(dom.byLabel("Remove skill 1"));
  field = dom.byLabel("Skill 1 path");
  assert.equal(field.value, "/two");
  await dom.pointer(field, "onBlur");
  assert.equal(saved.skills[0].path, "/two");
  await dom.unmount();
});

test("source control displays an unborn branch and keeps one option after the first commit", async () => {
  const { runGit, readGitStatus } = require("../../electron/git-status.cjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "rem-unborn-"));
  const git = (...args) => runGit(["-C", root, ...args]);
  let dom;
  try {
    await git("init");
    await git("branch", "-M", "main");
    let snapshot = await readGitStatus(root);
    assert.equal(snapshot.branch, "main");
    assert.deepEqual(snapshot.branches, []);
    dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: root } }), createFetchMock([]), { desktop: { workspace: {
      async trustProjectRoot() {},
      async listDirectory() { return { entries: [] }; },
      async gitStatus() { return snapshot; },
      async gitHistory() { return { active: true, commits: [] }; },
      async gitWorktrees() { return { active: true, worktrees: [] }; },
    } } });
    await dom.click(dom.byLabel("Source control"));
    const assertMainOption = () => {
      const selector = dom.byLabel("Switch branch");
      assert.equal(reactProps(selector).value, "main");
      const options = selector.childNodes.filter(node => node.tagName === "OPTION");
      assert.equal(options.length, 1);
      assert.equal(options[0].textContent, "main");
      assert.equal(reactProps(options[0]).value, "main");
    };
    assertMainOption();
    await git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Initial commit");
    snapshot = await readGitStatus(root);
    await dom.click(dom.byLabel("Refresh source control"));
    await dom.flush();
    assert.deepEqual(snapshot.branches, ["main"]);
    assertMainOption();
  } finally {
    if (dom) await dom.unmount();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Git file actions preserve staged edits, handle literal paths, and switch branches", async () => {
  const { runGit, readGitStatus, changeGitFile, switchGitBranch } = require("../../electron/git-status.cjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "rem-git-"));
  const git = (...args) => runGit(["-C", root, ...args]);
  try {
    await git("init", "-b", "main");
    await git("config", "user.name", "Test");
    await git("config", "user.email", "test@example.invalid");
    fs.writeFileSync(path.join(root, "file.txt"), "base\n");
    await git("add", "file.txt");
    await git("commit", "-m", "base");
    fs.writeFileSync(path.join(root, "file.txt"), "staged\n");
    await changeGitFile(root, "file.txt", "stage");
    fs.writeFileSync(path.join(root, "file.txt"), "unstaged\n");
    let snapshot = await readGitStatus(root);
    assert.equal(snapshot.entries[0].staged, true);
    assert.equal(snapshot.entries[0].unstaged, true);
    await assert.rejects(changeGitFile(root, "file.txt", "revert-staged"), /also has unstaged/);
    await changeGitFile(root, "file.txt", "revert");
    assert.equal(fs.readFileSync(path.join(root, "file.txt"), "utf8"), "staged\n");
    assert.equal(await git("show", ":file.txt"), "staged\n");
    await changeGitFile(root, "file.txt", "revert-staged");
    fs.writeFileSync(path.join(root, "[literal].txt"), "new\n");
    await changeGitFile(root, "[literal].txt", "stage");
    await changeGitFile(root, "[literal].txt", "unstage");
    snapshot = await readGitStatus(root);
    assert.equal(snapshot.entries.find((entry) => entry.path === "[literal].txt").staged, false);
    await assert.rejects(changeGitFile(root, "../outside", "stage"), /no longer present/);
    await git("branch", "feature");
    snapshot = await switchGitBranch(root, "feature");
    assert.equal(snapshot.branch, "feature");
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("Rem migrates history once and loads only requested metadata pages", () => {
  const legacy = Array.from({ length: 35 }, (_, index) => ({ id: `thread-${index}`, title: `Thread ${index}`, updatedAt: new Date(2026, 0, 35 - index).toISOString(), resources: { shell: false, web: true, skills: [], mcpServers: [] } }));
  const stored = new Map([["gofer-flow-chat-threads", JSON.stringify(legacy)]]);
  const reads = [];
  window.localStorage = { getItem: (key) => { reads.push(key); return stored.get(key) || null; }, setItem: (key, value) => stored.set(key, value), removeItem: (key) => stored.delete(key) };
  assert.equal(appModule.loadChatThreads().length, 15);
  reads.length = 0;
  const recent = appModule.loadChatThreads();
  assert.equal(reads.filter((key) => key.startsWith("gofer-flow-chat-thread-meta:")).length, 15);
  assert.equal(appModule.loadChatThreads(30).length, 30);
  appModule.persistChatThreads(appModule.bumpChatThread(recent, "thread-5", "2026-09-08T12:00:00Z"));
  assert.equal(appModule.chatThreadIndex().length, 35);
  assert.equal(appModule.loadChatThreads()[0].id, "thread-5");
  assert.deepEqual(appModule.loadChatThreads()[0].resources, legacy[5].resources);
});

test("browser Ctrl+T always yields to the terminal command", () => {
  const event = { ctrlKey: true, key: "t", target: { closest: () => ({}) } };
  assert.equal(integratedBrowserModule.browserChromeShortcutAction(event, "linux"), null);
  assert.equal(integratedBrowserModule.browserChromeShortcutAction({ ...event, target: null }, "linux"), "");
  assert.equal(integratedBrowserModule.browserChromeShortcutAction({ ...event, target: null, defaultPrevented: true }, "linux"), null);
});

test("Developer and Memory settings can be found by task words", () => {
  assert.ok(settingsPopoverModule.settingsCategoriesForQuery("logs").includes("developer"));
  assert.ok(settingsPopoverModule.settingsCategoriesForQuery("second brain").includes("memory"));
  assert.ok(settingsPopoverModule.settingsCategoriesForQuery("archive").includes("memory"));
});

test("Git commit, protected switch, stash, publish, pull and staged comparisons use real repositories", async () => {
  const { runGit, gitRepositoryAction, switchGitBranch, readGitFileBaseline } = require("../../electron/git-status.cjs");
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-scm-"));
  const root = path.join(base, "repo");
  const remote = path.join(base, "remote.git");
  fs.mkdirSync(root);
  const git = (...args) => runGit(["-C", root, ...args]);
  try {
    await git("init", "-b", "main");
    await git("config", "user.name", "Test"); await git("config", "user.email", "test@example.invalid");
    fs.writeFileSync(path.join(root, "note.md"), "base\n");
    await git("add", ".");
    await gitRepositoryAction(root, "commit", "Initial commit");
    await git("switch", "-c", "feature");
    fs.writeFileSync(path.join(root, "note.md"), "feature\n"); await git("add", ".");
    await gitRepositoryAction(root, "commit", "Feature"); await git("switch", "main");
    fs.writeFileSync(path.join(root, "note.md"), "local\n");
    const blocked = await switchGitBranch(root, "feature");
    assert.equal(blocked.switchBlocked, true);
    assert.equal(blocked.branch, "main");
    assert.equal(fs.readFileSync(path.join(root, "note.md"), "utf8"), "local\n");
    const switched = await gitRepositoryAction(root, "stash-switch", "feature");
    assert.equal(switched.branch, "feature"); assert.equal(switched.stashCount, 1);
    await switchGitBranch(root, "main"); await gitRepositoryAction(root, "stash-apply");
    assert.equal(fs.readFileSync(path.join(root, "note.md"), "utf8"), "local\n");
    await git("add", "note.md"); fs.writeFileSync(path.join(root, "note.md"), "unstaged\n");
    const staged = await readGitFileBaseline(path.join(root, "note.md"), { group: "staged" });
    assert.equal(staged.content, "base\n"); assert.equal(staged.modifiedContent, "local\n");
    const unstaged = await readGitFileBaseline(path.join(root, "note.md"), { group: "unstaged" });
    assert.equal(unstaged.content, "local\n"); assert.equal(unstaged.modifiedContent, "unstaged\n");
    await gitRepositoryAction(root, "commit", "Only staged");
    assert.equal(await git("show", "HEAD:note.md"), "local\n");
    await assert.rejects(gitRepositoryAction(root, "commit", "No staged files"), /Stage changes/);
    fs.unlinkSync(path.join(root, "note.md"));
    const deleted = await readGitFileBaseline(path.join(root, "note.md"), { group: "unstaged" });
    assert.equal(deleted.deleted, true); assert.equal(deleted.content, "local\n");
    fs.writeFileSync(path.join(root, "new.md"), "new\n");
    const added = await readGitFileBaseline(path.join(root, "new.md"), { group: "unstaged" });
    assert.equal(added.content, ""); assert.equal(added.changed, true);
    await git("restore", "note.md");
    await runGit(["init", "--bare", remote]); await git("remote", "add", "origin", remote);
    const published = await gitRepositoryAction(root, "publish", "origin");
    assert.equal(published.ahead, 0);
    await gitRepositoryAction(root, "push"); await gitRepositoryAction(root, "pull");
  } finally { fs.rmSync(base, { recursive: true, force: true }); }
});

test("conversation archive retains structured revisions, attachments, and deterministic search metadata", () => {
  const { archiveConversation } = require("../../electron/conversation-archive.cjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-archive-"));
  const archive = path.join(root, "archive"); fs.mkdirSync(archive);
  const storageName = "a".repeat(32) + "-note.txt";
  const attachments = path.join(root, "chat-attachments", "thread-1"); fs.mkdirSync(attachments, { recursive: true });
  fs.writeFileSync(path.join(attachments, storageName), "Attachment content");
  const thread = { id: "thread-1", title: "Database decision", projectRoot: "/project", provider: "codex" };
  const messages = [{ id: "m1", role: "user", body: "Use SQLite", attachments: [{ storageName, name: "note.txt" }] }, { id: "m2", role: "assistant", kind: "thought", body: "Inspecting files", tool: { name: "read" } }];
  try {
    const { id } = archiveConversation(archive, thread, messages, { dataDir: root });
    const journal = path.join(archive, "threads", `${id}.jsonl`);
    const snapshot = path.join(archive, "threads", `${id}.json`);
    const original = fs.readFileSync(journal, "utf8");
    archiveConversation(archive, thread, messages, { dataDir: root });
    assert.equal(fs.readFileSync(journal, "utf8"), original);
    const saved = JSON.parse(fs.readFileSync(snapshot, "utf8"));
    assert.deepEqual(saved.messages[1].tool, { name: "read" });
    assert.equal(fs.readFileSync(path.join(archive, saved.messages[0].attachments[0].archivePath), "utf8"), "Attachment content");
    archiveConversation(archive, thread, [{ ...messages[0], body: "Use Postgres" }], { dataDir: root, deleted: true });
    assert.ok(fs.readFileSync(journal, "utf8").startsWith(original));
    const index = JSON.parse(fs.readFileSync(path.join(archive, "index.json"), "utf8"));
    assert.equal(index.threads[id].deleted, true); assert.ok(index.threads[id].terms.includes("postgres"));
    // Missing snapshots are recovered from the journal without replacing history.
    fs.unlinkSync(snapshot);
    archiveConversation(archive, thread, [{ ...messages[0], body: "Use Postgres" }], { dataDir: root, deleted: true });
    assert.equal(JSON.parse(fs.readFileSync(snapshot, "utf8")).messages.length, 1);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("app logging rotates files, redacts common credentials and preserves structured entries", async () => {
  const { createAppLog } = require("../../electron/app-log.cjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-log-"));
  try {
    const log = createAppLog(root, { maxBytes: 240, backups: 2 });
    log.write("error", "renderer", "apiToken=super-secret password=hunter2 Authorization: Bearer abc123");
    await log.flush();
    const entry = JSON.parse(fs.readFileSync(log.file, "utf8"));
    assert.equal(entry.source, "renderer");
    assert.ok(!entry.message.includes("super-secret")); assert.ok(!entry.message.includes("hunter2")); assert.ok(!entry.message.includes("abc123"));
    for (let i = 0; i < 10; i++) log.write("info", "backend", "x".repeat(120));
    await log.close();
    assert.deepEqual(fs.readdirSync(root).sort(), ["app.jsonl", "app.jsonl.1", "app.jsonl.2"]);
    for (const file of fs.readdirSync(root)) for (const line of fs.readFileSync(path.join(root, file), "utf8").trim().split("\n")) assert.ok(JSON.parse(line).time);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("archive continues when old attachments are missing and rejects symlink destinations", () => {
  const { archiveConversation } = require("../../electron/conversation-archive.cjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-archive-missing-"));
  try {
    const messages = [{ id: "m", role: "user", body: "Historic text", attachments: [{ storageName: "a".repeat(32) + "-gone.txt", name: "gone.txt" }] }];
    const result = archiveConversation(root, { id: "t" }, messages, { dataDir: root });
    assert.match(result.warnings[0], /no longer available/);
    const snapshot = JSON.parse(fs.readFileSync(path.join(root, "threads", `${result.id}.json`), "utf8"));
    assert.equal(snapshot.messages[0].body, "Historic text");
    assert.match(snapshot.messages[0].attachments[0].archiveError, /gone.txt/);
    fs.unlinkSync(path.join(root, "index.json"));
    fs.symlinkSync(path.join(root, "must-not-create"), path.join(root, "index.json"));
    assert.throws(() => archiveConversation(root, { id: "t" }, []), /symbolic links/);
    assert.equal(fs.existsSync(path.join(root, "must-not-create")), false);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("Git comparisons cover empty additions, renamed files, and binary versions", async () => {
  const { runGit, readGitFileBaseline } = require("../../electron/git-status.cjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-diff-"));
  const git = (...args) => runGit(["-C", root, ...args]);
  try {
    await git("init", "-b", "main"); await git("config", "user.name", "Test"); await git("config", "user.email", "test@example.invalid");
    fs.writeFileSync(path.join(root, "empty.txt"), "");
    assert.equal((await readGitFileBaseline(path.join(root, "empty.txt"), { group: "unstaged" })).changed, true);
    fs.writeFileSync(path.join(root, "old.txt"), "retained content\n".repeat(20));
    fs.writeFileSync(path.join(root, "image.png"), Buffer.from([137, 80, 78, 71, 0, 1]));
    await git("add", "."); await git("commit", "-m", "Initial");
    await git("mv", "old.txt", "renamed.txt");
    const renamed = await readGitFileBaseline(path.join(root, "renamed.txt"), { group: "staged" });
    assert.equal(renamed.content, "retained content\n".repeat(20));
    fs.writeFileSync(path.join(root, "image.png"), Buffer.from([137, 80, 78, 71, 0, 2]));
    const binary = await readGitFileBaseline(path.join(root, "image.png"), { group: "unstaged" });
    assert.equal(binary.binary, true); assert.equal(binary.originalBytes, 6);
    assert.deepEqual(Buffer.from(binary.originalData, "base64"), Buffer.from([137, 80, 78, 71, 0, 1]));
    assert.notEqual(binary.originalData, binary.modifiedData);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("browser close is idempotent after guest destruction and checks live ownership", () => {
  const source = fs.readFileSync(path.join(repoRoot, "frontend/electron/main.js"), "utf8");
  const actionSource = source.slice(source.indexOf("function browserAction("), source.indexOf("function configureBrowserSession("));
  const ownerSource = source.slice(source.indexOf("function ownedBrowserSession("), source.indexOf("function closeBrowserSession("));
  const browserSessions = new Map();
  const action = vm.runInNewContext(`${ownerSource}\n${actionSource}\nbrowserAction`, {
    browserSessions, browserSessionContents: () => null, browserSessionState: () => ({ ready: false }),
  });
  assert.equal(action({ sender: { id: 1 } }, { id: "gone", action: "close" }).closed, true);
  browserSessions.set("pending", { ownerId: 1 });
  assert.equal(action({ sender: { id: 1 } }, { id: "pending", action: "focus" }).ready, false);
  assert.throws(() => action({ sender: { id: 2 } }, { id: "pending", action: "close" }), /not found/);
});

test("browser waits for adoption before focusing and ignores late state after cleanup", async () => {
  let update;
  const focused = [];
  const closed = [];
  const browser = {
    create: async () => ({ id: "pending-view", clientId: "report", ready: false, src: "about:blank" }),
    close: async (id) => { closed.push(id); },
    focus: async (id) => { focused.push(id); },
    onState: (callback) => { update = callback; return () => {}; },
    onCommand: () => () => {},
  };
  const dom = await mountReact(React.createElement(integratedBrowserModule.default, {
    active: true, clientId: "report",
  }), createFetchMock([]), { browser });
  await dom.flush();
  assert.deepEqual(focused, []);
  await React.act(async () => update({ id: "pending-view", clientId: "report", ready: true }));
  await dom.flush();
  assert.deepEqual(focused, ["pending-view"]);
  await dom.unmount();
  update({ id: "pending-view", clientId: "report", ready: true });
  assert.deepEqual(closed, ["pending-view"]);
  assert.deepEqual(focused, ["pending-view"]);
});

test("report themes survive settings persistence and appear in the picker", async () => {
  const { RemMemorySettings } = await viteServer.ssrLoadModule("/src/components/DeveloperSettings.jsx");
  const markup = renderToStaticMarkup(React.createElement(RemMemorySettings, { value: {}, onChange() {} }));
  for (const { id, label } of settingsModule.REPORT_THEMES) {
    const stored = new Map();
    const storage = { getItem: (key) => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value) };
    const settings = settingsModule.updateSetting(settingsModule.DEFAULT_APP_SETTINGS, "memory.secondBrainTheme", id);
    settingsModule.saveAppSettings(settings, storage);
    assert.equal(settingsModule.loadAppSettings(storage).memory.secondBrainTheme, id);
    assert.ok(markup.includes(`value="${id}"`), label);
  }
  assert.equal(settingsModule.normalizeAppSettings({ memory: { secondBrainTheme: "unknown" } }).memory.secondBrainTheme, "auto");
});

test("source control tabs keep drafts and expose staging, commits, and branch recovery", async () => {
  let snapshot = { active: true, branch: "main", branches: ["main", "feature"], remotes: [], entries: [{ path: "src/example.js", status: "M", staged: false, unstaged: true }] };
  const actions = [];
  const workspace = {
    trustProjectRoot: async () => {},
    listDirectory: async () => ({ entries: [] }),
    gitStatus: async () => snapshot,
    gitHistory: async () => ({ active: true, commits: [] }),
    gitWorktrees: async () => ({ active: true, worktrees: [] }),
    gitFileAction: async (root, path, action) => {
      actions.push([action, path]);
      snapshot = { ...snapshot, entries: [{ ...snapshot.entries[0], staged: true, unstaged: false }] };
      return snapshot;
    },
    gitSwitchBranch: async () => { throw new Error("error: Your local changes would be overwritten by checkout: src/example.js"); },
    gitRepoAction: async (root, action, value) => {
      actions.push([action, value]);
      snapshot = { ...snapshot, entries: [] };
      return snapshot;
    },
  };
  const dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: "/workspace/project" } }), createFetchMock([]), { desktop: { workspace } });
  await dom.click(dom.byLabel("Source control"));
  await dom.flush();
  assert.throws(() => dom.byText("Publish"));
  assert.throws(() => dom.byLabel("Add worktree"));
  await dom.change(dom.byLabel("Commit message"), "Update example");
  await dom.click(dom.byLabel("File explorer"));
  assert.equal(dom.byLabel("File explorer").getAttribute("aria-selected"), "true");
  assert.equal(dom.byLabel("Source control").getAttribute("aria-selected"), "false");
  assert.equal(dom.ancestor(dom.byLabel("Commit message"), "SECTION").getAttribute("hidden"), "");
  await dom.click(dom.byLabel("Source control"));
  assert.equal(dom.byLabel("Source control").getAttribute("aria-selected"), "true");
  assert.equal(dom.ancestor(dom.byLabel("Commit message"), "SECTION").getAttribute("hidden"), null);
  assert.equal(reactProps(dom.byLabel("Commit message")).value, "Update example");
  await dom.click(dom.byText("History"));
  assert.throws(() => dom.byLabel("Commit message"));
  assert.ok(dom.byText("No commits yet."));
  await dom.click(dom.byText("Changes"));
  assert.equal(reactProps(dom.byLabel("Commit message")).value, "Update example");
  await dom.click(dom.byLabel("Stage src/example.js"));
  await dom.flush();
  assert.deepEqual(actions[0], ["stage", "src/example.js"]);
  assert.ok(dom.byLabel("Unstage src/example.js"));
  window.dispatchEvent = () => true;
  await dom.change(dom.byLabel("Switch branch"), "feature");
  await dom.flush();
  assert.ok(dom.byText("Changes would be overwritten"));
  assert.ok(dom.byText("Technical details"));
  assert.ok(dom.byText("Stash & switch"));
  await dom.click(dom.byText("Stay on main"));
  assert.throws(() => dom.byText("Technical details"));
  const form = dom.ancestor(dom.byLabel("Commit message"), "FORM");
  await React.act(async () => { await reactProps(form).onSubmit(testEvent(form)); });
  await dom.flush();
  assert.deepEqual(actions[1], ["commit", "Update example"]);
  assert.ok(dom.byText("Working tree clean"));
  assert.throws(() => dom.byLabel("Commit message"));
  await dom.unmount();
});

test("source control bulk actions stay within their group and confirm discards once", async () => {
  const initialEntries = [
    { path: "staged.txt", status: "M", staged: true, unstaged: false },
    { path: "one.txt", status: "M", staged: false, unstaged: true },
    { path: "two.txt", status: "M", staged: false, unstaged: true },
  ];
  let snapshot = { active: true, branch: "main", branches: ["main"], entries: initialEntries };
  const actions = [];
  const confirmations = [];
  const workspace = {
    trustProjectRoot: async () => {},
    listDirectory: async () => ({ entries: [] }),
    gitStatus: async () => snapshot,
    gitHistory: async () => ({ active: true, commits: [] }),
    gitWorktrees: async () => ({ active: true, worktrees: [] }),
    gitFileAction: async (root, file, action) => {
      actions.push([action, file]);
      snapshot = { ...snapshot, entries: snapshot.entries.flatMap((entry) => entry.path !== file ? [entry]
        : action.startsWith("revert") ? []
          : [{ ...entry, staged: action === "stage", unstaged: action === "unstage" }]) };
      return snapshot;
    },
  };
  const dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: "/workspace/project" } }), createFetchMock([]), { desktop: { workspace } });
  try {
    await dom.click(dom.byLabel("Source control"));
    await dom.flush();
    window.dispatchEvent = () => true;
    window.confirm = (message) => { confirmations.push(message); return false; };
    assert.equal(dom.byText("Changes").textContent, "Changes");
    await dom.click(dom.byLabel("Discard all unstaged changes"));
    assert.deepEqual(actions, []);
    window.confirm = (message) => { confirmations.push(message); return true; };
    await dom.click(dom.byLabel("Stage all changes"));
    await dom.flush();
    assert.deepEqual(actions.splice(0), [["stage", "one.txt"], ["stage", "two.txt"]]);
    assert.equal(allElements(dom.container).some((node) => node.getAttribute?.("aria-label") === "Unstaged"), false);
    await dom.click(dom.byLabel("Unstage all changes"));
    await dom.flush();
    assert.deepEqual(actions.splice(0), [["unstage", "staged.txt"], ["unstage", "one.txt"], ["unstage", "two.txt"]]);
    await dom.click(dom.byLabel("Discard all unstaged changes"));
    await dom.flush();
    assert.deepEqual(actions.splice(0), [["revert", "staged.txt"], ["revert", "one.txt"], ["revert", "two.txt"]]);
    assert.equal(confirmations.length, 2);
    assert.ok(dom.byText("Working tree clean"));

    snapshot = { ...snapshot, entries: initialEntries };
    await dom.click(dom.byLabel("Refresh source control"));
    await dom.flush();
    await dom.click(dom.byLabel("Discard all staged changes"));
    await dom.flush();
    assert.deepEqual(actions.splice(0), [["revert-staged", "staged.txt"]]);
    assert.ok(dom.byLabel("Stage one.txt"));
    snapshot = { ...snapshot, entries: [{ ...initialEntries[0], unstaged: true }] };
    await dom.click(dom.byLabel("Refresh source control"));
    await dom.flush();
    await dom.click(dom.byLabel("Discard all staged changes"));
    assert.deepEqual(actions, []);
    assert.equal(confirmations.length, 3);
    assert.ok(dom.byText("Technical details"));
  } finally { await dom.unmount(); }
});

test("project search finds literal matches and respects ignored files and symlinks", async () => {
  const { searchProject } = require("../../electron/project-search.cjs");
  const runGit = require("node:util").promisify(require("node:child_process").execFile);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-search-"));
  try {
    await runGit("git", ["init", root]);
    fs.writeFileSync(path.join(root, ".gitignore"), "ignored.txt\n");
    fs.writeFileSync(path.join(root, "ignored.txt"), "hello");
    fs.writeFileSync(path.join(root, "sample.txt"), "hello Hello helloworld\n[a.b] hello\n");
    fs.writeFileSync(path.join(root, "binary"), Buffer.from("hello\0world"));
    fs.symlinkSync(path.join(root, "sample.txt"), path.join(root, "link.txt"));
    const result = await searchProject(root, { query: "hello", wholeWord: true });
    assert.equal(result.count, 3);
    assert.deepEqual(result.files.map((file) => file.relativePath), ["sample.txt"]);
    assert.equal(result.files[0].matches[2].lineNumber, 2);
    assert.equal(result.files[0].matches[2].column, 7);
    assert.equal((await searchProject(root, { query: "hello", wholeWord: true, matchCase: true })).count, 2);
    assert.equal((await searchProject(root, { query: "[a.b]" })).count, 1);
    assert.equal((await searchProject(root, { query: "missing" })).count, 0);
    fs.writeFileSync(path.join(root, "many.txt"), "hello\n".repeat(1100));
    const capped = await searchProject(root, { query: "hello" });
    assert.equal(capped.count, 1000);
    assert.equal(capped.truncated, true);
    fs.rmSync(path.join(root, ".git"), { recursive: true });
    assert.ok((await searchProject(root, { query: "[a.b]" })).count === 1);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("project search preserves drafts and opens results at their line and column", async () => {
  const opened = [];
  const calls = [];
  const workspace = {
    trustProjectRoot: async () => {},
    listDirectory: async () => ({ entries: [] }),
    gitStatus: async () => ({ active: false, entries: [] }),
    searchProject: async (root, options) => {
      calls.push([root, options]);
      return { count: 1, files: [{ path: "/workspace/project/example.js", relativePath: "example.js", matches: [{ lineNumber: 7, column: 3, text: "  hello()", offset: 2, length: 5 }] }] };
    },
  };
  const dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: "/workspace/project" }, onOpenFile: (...args) => opened.push(args) }), createFetchMock([]), { desktop: { workspace } });
  await dom.click(dom.byLabel("Search"));
  const input = dom.byLabel("Search project");
  input.focus();
  await dom.change(input, "draft");
  await dom.change(input, "");
  await dom.change(input, "hello");
  input.blur();
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
  assert.equal(calls.at(-1)[1].query, "hello");
  await dom.click(dom.byTitle("example.js:7:3"));
  assert.deepEqual(opened, [["/workspace/project/example.js", { preview: true, lineNumber: 7, column: 3 }]]);
  await dom.click(dom.byLabel("File explorer"));
  await dom.click(dom.byLabel("Search"));
  assert.equal(reactProps(dom.byLabel("Search project")).value, "hello");
  await dom.click(dom.byLabel("Match case"));
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
  assert.equal(calls.at(-1)[1].matchCase, true);
  await dom.click(dom.byLabel("Clear search"));
  assert.equal(reactProps(dom.byLabel("Search project")).value, "");
  await dom.unmount();
});

test("regex replacement preserves line endings, supports captures, exclusions, and stale-file protection", async () => {
  const { searchProject, replaceProject } = require("../../electron/project-search.cjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-replace-"));
  const target = path.join(root, "sample.txt");
  try {
    fs.mkdirSync(path.join(root, "nested"));
    fs.writeFileSync(target, "hello12\r\nhello34\r\n");
    fs.writeFileSync(path.join(root, "nested", "ignored.log"), "hello56");
    fs.writeFileSync(path.join(root, "keep.txt"), "hello78");
    const options = { query: "hello(\\d+)", regex: true, exclude: "**/*.log, keep.txt" };
    const found = await searchProject(root, options);
    assert.equal(found.count, 2);
    assert.deepEqual(found.files.map((file) => file.relativePath), ["sample.txt"]);
    const replaced = await replaceProject(root, { ...options, files: found.files, replacement: "$1:$&" });
    assert.equal(replaced.count, 2);
    assert.equal(fs.readFileSync(target, "utf8"), "12:hello12\r\n34:hello34\r\n");
    assert.equal(fs.readFileSync(path.join(root, "nested", "ignored.log"), "utf8"), "hello56");
    await assert.rejects(replaceProject(root, { ...options, files: found.files, replacement: "gone" }), /changed since/);
    await assert.rejects(searchProject(root, { query: "[", regex: true }), /regular expression/i);
    assert.equal((await searchProject(root, { query: "(?=hello)", regex: true })).count, 4);
    const literal = await searchProject(root, { query: "hello12" });
    await replaceProject(root, { query: "hello12", files: literal.files, replacement: "$1" });
    assert.equal(fs.readFileSync(target, "utf8"), "12:$1\r\n34:hello34\r\n");
    const all = await searchProject(root, { query: "hello" });
    await replaceProject(root, { query: "hello", files: all.files.slice(0, 1), replacement: "" });
    assert.equal(fs.readFileSync(path.join(root, "keep.txt"), "utf8"), "78");
    fs.writeFileSync(target, "hello\n".repeat(1001));
    const capped = await searchProject(root, { query: "hello" });
    await assert.rejects(replaceProject(root, { query: "hello", files: capped.files, replacement: "x" }), /incomplete/);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("project search sends regex and exclusion drafts and replaces a file", async () => {
  const calls = [];
  const replacements = [];
  const changed = [];
  const file = { path: "/workspace/project/example.js", relativePath: "example.js", hash: "snapshot", matches: [{ lineNumber: 1, column: 1, text: "hello", offset: 0, length: 5 }] };
  const workspace = {
    trustProjectRoot: async () => {}, listDirectory: async () => ({ entries: [] }), gitStatus: async () => ({ active: false, entries: [] }),
    searchProject: async (_root, options) => { calls.push(options); return { count: 1, files: [file] }; },
    replaceProject: async (_root, options) => { replacements.push(options); return { count: 1, changed: [file.path] }; },
  };
  const dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: "/workspace/project" }, onFilesystemChange: (change) => changed.push(change) }), createFetchMock([]), { desktop: { workspace } });
  const originalDispatch = window.dispatchEvent;
  const events = [];
  window.dispatchEvent = (event) => { events.push(event); return true; };
  const originalConfirm = window.confirm;
  window.confirm = () => true;
  try {
    await dom.click(dom.byLabel("Search"));
    await dom.change(dom.byLabel("Search project"), "(hello)");
    await dom.click(dom.byLabel("Use regular expression"));
    const include = dom.byLabel("Files to include");
    include.focus();
    await dom.change(include, "draft");
    await dom.change(include, "");
    assert.equal(reactProps(include).value, "");
    await dom.change(include, "src/**, *.js");
    include.blur();
    const exclude = dom.byLabel("Files to exclude");
    exclude.focus();
    await dom.change(exclude, "draft");
    await dom.change(exclude, "");
    await dom.change(exclude, "*.log, dist/**");
    exclude.blur();
    await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
    assert.equal(calls.at(-1).include, "src/**, *.js");
    assert.equal(calls.at(-1).regex, true);
    assert.equal(calls.at(-1).exclude, "*.log, dist/**");
    await dom.click(dom.byText("Replace"));
    const replacement = dom.byLabel("Replace with");
    replacement.focus();
    await dom.change(replacement, "draft");
    await dom.change(replacement, "");
    await dom.change(replacement, "$1!");
    replacement.blur();
    await dom.click(dom.byLabel("Replace matches in example.js"));
    await dom.flush();
    assert.deepEqual(events.map((event) => event.detail.busy), [true, false]);
    assert.equal(replacements[0].include, "src/**, *.js");
    assert.equal(replacements[0].replacement, "$1!");
    assert.deepEqual(replacements[0].files, [{ path: file.path, hash: "snapshot" }]);
    assert.equal(changed.at(-1).rootPath, "/workspace/project");
    assert.ok(dom.byText("Replaced 1 match in 1 file."));
  } finally { window.confirm = originalConfirm; window.dispatchEvent = originalDispatch; await dom.unmount(); }
});

test("search UI applies regex queries and regex exclusions through the real worker", async () => {
  const { searchProject, replaceProject } = require("../../electron/project-search.cjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-regex-ui-"));
  fs.writeFileSync(path.join(root, "sample.txt"), "hello12 hello34");
  fs.writeFileSync(path.join(root, "skip12.log"), "hello56");
  const workspace = {
    trustProjectRoot: async () => {}, listDirectory: async () => ({ entries: [] }), gitStatus: async () => ({ active: false, entries: [] }),
    searchProject, replaceProject,
  };
  const dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: root } }), createFetchMock([]), { desktop: { workspace } });
  async function waitForText(text) {
    for (let attempt = 0; attempt < 60; attempt++) {
      await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
      if (textOf(dom.container).includes(text)) return;
    }
    assert.fail(`Search did not show: ${text}`);
  }
  try {
    await dom.click(dom.byLabel("Search"));
    const query = dom.byLabel("Search project");
    query.focus();
    await dom.change(query, "draft");
    await dom.change(query, "");
    await dom.change(query, "hello\\d{2}");
    query.blur();
    await waitForText("No results found.");
    await dom.click(dom.byLabel("Use regular expression"));
    await waitForText("3 results in 2 files");
    await dom.click(dom.byLabel("Use regular expression for exclusions"));
    const exclude = dom.byLabel("Files to exclude");
    exclude.focus();
    await dom.change(exclude, "draft");
    await dom.change(exclude, "");
    await dom.change(exclude, "skip\\d{1,3}\\.log$");
    exclude.blur();
    await waitForText("2 results in 1 files");
    await dom.change(exclude, "[");
    await waitForText("Search failed.");
    assert.ok(dom.byText("Files to exclude expression"));
    await dom.change(exclude, "");
    await waitForText("3 results in 2 files");
    await dom.change(query, "[");
    await waitForText("Search failed.");
    assert.ok(dom.byText("Search expression"));
  } finally { await dom.unmount(); fs.rmSync(root, { recursive: true, force: true }); }
});

test("regex exclusions preserve escapes and quantifier commas during replacement", async () => {
  const { searchProject, replaceProject } = require("../../electron/project-search.cjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-regex-exclude-"));
  try {
    fs.mkdirSync(path.join(root, "nested"));
    fs.writeFileSync(path.join(root, "nested", "skip12.log"), "hello56");
    fs.writeFileSync(path.join(root, "keep.txt"), "hello78");
    const options = { query: "hello(\\d+)", regex: true, excludeRegex: true, exclude: "(^|/)skip\\d{1,3}\\.log$" };
    const result = await searchProject(root, options);
    assert.deepEqual(result.files.map((file) => file.relativePath), ["keep.txt"]);
    await replaceProject(root, { ...options, files: result.files, replacement: "$1" });
    assert.equal(fs.readFileSync(path.join(root, "keep.txt"), "utf8"), "78");
    assert.equal(fs.readFileSync(path.join(root, "nested", "skip12.log"), "utf8"), "hello56");
    assert.equal((await searchProject(root, { query: "hello", excludeRegex: true, exclude: "" })).count, 1);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("source control orders groups, omits tab counts, and scopes bulk actions", async () => {
  let entries = [
    { path: "staged.txt", staged: true, unstaged: false, status: "M" },
    { path: "first.txt", staged: false, unstaged: true, status: "M" },
    { path: "second.txt", staged: false, unstaged: true, status: "M" },
  ];
  const calls = [];
  const confirmations = [];
  let approved = false;
  let failPath = "";
  const workspace = {
    trustProjectRoot: async () => {}, listDirectory: async () => ({ entries: [] }),
    gitStatus: async () => ({ active: true, entries }),
    gitHistory: async () => ({ active: true, commits: [{ hash: "abc", subject: "Initial" }] }),
    gitWorktrees: async () => ({ active: true, worktrees: [{ path: "/workspace/project" }, { path: "/missing", missing: true }] }),
    gitFileAction: async (_root, filePath, action) => {
      calls.push([filePath, action]);
      if (filePath === failPath) throw new Error("File changed during bulk action");
      entries = entries.map((entry) => entry.path !== filePath ? entry : action === "stage" ? { ...entry, staged: true, unstaged: false } : action === "unstage" ? { ...entry, staged: false, unstaged: true } : { ...entry, [action === "revert-staged" ? "staged" : "unstaged"]: false }).filter((entry) => entry.staged || entry.unstaged);
      return { active: true, entries };
    },
  };
  const dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: "/workspace/project" } }), createFetchMock([]), { desktop: { workspace } });
  const originalDispatch = window.dispatchEvent;
  window.dispatchEvent = () => true;
  const originalConfirm = window.confirm;
  window.confirm = (message) => { confirmations.push(message); return approved; };
  try {
    await dom.click(dom.byLabel("Source control"));
    await dom.flush();
    assert.ok(dom.text().indexOf("Staged · 1") < dom.text().indexOf("Unstaged · 2"));
    for (const label of ["Changes", "History", "Branches"]) {
      assert.equal(dom.byText(label).textContent, label);
    }
    assert.ok(dom.byLabel("Discard unstaged changes to first.txt"));
    await dom.click(dom.byLabel("Stage all changes"));
    await dom.flush();
    assert.deepEqual(calls, [["first.txt", "stage"], ["second.txt", "stage"]]);
    assert.equal(allElements(dom.container).some((node) => node.getAttribute?.("aria-label") === "Unstaged"), false);
    await dom.click(dom.byLabel("Unstage all changes"));
    await dom.flush();
    assert.deepEqual(calls.slice(2), [["staged.txt", "unstage"], ["first.txt", "unstage"], ["second.txt", "unstage"]]);
    calls.length = 0;
    await dom.click(dom.byLabel("Discard all unstaged changes"));
    assert.deepEqual(calls, []);
    approved = true;
    await dom.click(dom.byLabel("Discard all unstaged changes"));
    await dom.flush();
    assert.equal(confirmations.length, 2);
    assert.match(confirmations[1], /all 3 unstaged files/);
    assert.deepEqual(calls, [["staged.txt", "revert"], ["first.txt", "revert"], ["second.txt", "revert"]]);
    assert.match(dom.text(), /Working tree clean/);

    entries = [{ path: "mixed.txt", staged: true, unstaged: true, status: "MM" }];
    await dom.click(dom.byLabel("Refresh source control"));
    await dom.flush();
    calls.length = 0;
    await dom.click(dom.byLabel("Discard all staged changes"));
    assert.deepEqual(calls, []);
    assert.match(dom.text(), /Some staged files also have unstaged edits/);

    entries = [{ path: "one.txt", staged: true, unstaged: false }, { path: "two.txt", staged: true, unstaged: false }];
    await dom.click(dom.byLabel("Refresh source control"));
    await dom.flush();
    failPath = "two.txt";
    await dom.click(dom.byLabel("Discard all staged changes"));
    await dom.flush();
    assert.deepEqual(calls, [["one.txt", "revert-staged"], ["two.txt", "revert-staged"]]);
    assert.match(dom.text(), /Staged · 1/);
    assert.match(dom.text(), /File changed during bulk action/);
  } finally { window.confirm = originalConfirm; window.dispatchEvent = originalDispatch; await dom.unmount(); }
});

for (const fail of [false, true]) {
  test(`source control stages immediately and ${fail ? "cancels a waiting commit on failure" : "waits for every add before committing"}`, async () => {
    let snapshot = { active: true, branch: "main", branches: ["main"], entries: [
      { path: "one.txt", status: "M", staged: false, unstaged: true },
      { path: "two.txt", status: "M", staged: false, unstaged: true },
    ] };
    const actions = [];
    const releases = [];
    const workspace = {
      trustProjectRoot: async () => {},
      listDirectory: async () => ({ entries: [] }),
      gitStatus: async () => snapshot,
      gitHistory: async () => ({ active: true, commits: [] }),
      gitWorktrees: async () => ({ active: true, worktrees: [] }),
      gitFileAction: async (root, file, action) => {
        actions.push([action, file]);
        await new Promise((resolve) => releases.push(resolve));
        if (fail && file === "two.txt") throw new Error("Unable to stage two.txt");
        snapshot = { ...snapshot, entries: snapshot.entries.map((entry) => entry.path === file ? { ...entry, staged: true, unstaged: false } : entry) };
        return snapshot;
      },
      gitRepoAction: async (root, action, message) => {
        actions.push([action, message]);
        snapshot = { ...snapshot, entries: [] };
        return snapshot;
      },
    };
    const dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: "/workspace/project" } }), createFetchMock([]), { desktop: { workspace } });
    try {
      await dom.click(dom.byLabel("Source control"));
      await dom.flush();
      await dom.change(dom.byLabel("Commit message"), "Save changes");
      await dom.click(dom.byLabel("Stage all changes"));
      assert.ok(dom.byLabel("Unstage one.txt"));
      assert.ok(dom.byLabel("Unstage two.txt"));
      assert.equal(reactProps(dom.byText("Commit 2 staged files")).disabled, false);
      assert.deepEqual(actions, [["stage", "one.txt"]]);
      const form = dom.ancestor(dom.byLabel("Commit message"), "FORM");
      await React.act(async () => { reactProps(form).onSubmit(testEvent(form)); });
      assert.deepEqual(actions, [["stage", "one.txt"]]);
      await React.act(async () => releases.shift()());
      await dom.flush();
      assert.deepEqual(actions, [["stage", "one.txt"], ["stage", "two.txt"]]);
      assert.ok(dom.byLabel("Unstage two.txt"));
      await React.act(async () => releases.shift()());
      await dom.flush();
      if (fail) {
        assert.equal(actions.length, 2);
        assert.ok(dom.byLabel("Unstage one.txt"));
        assert.ok(dom.byLabel("Stage two.txt"));
        assert.equal(reactProps(dom.byLabel("Commit message")).value, "Save changes");
        assert.ok(dom.byText("Technical details"));
      } else {
        assert.deepEqual(actions[2], ["commit", "Save changes"]);
        assert.ok(dom.byText("Working tree clean"));
      }
    } finally { await dom.unmount(); }
  });
}


test("Windows Markdown links retain drive paths and UNC shares", () => {
  for (const href of ["C:/Users/Alice/My%20Project/readme.md:12", String.raw`C:\Users\Alice\readme.md:12`, String.raw`\\server\share\readme.md`]) {
    assert.equal(markdownContentModule.markdownUrlTransform(href, "href"), href);
  }
  assert.equal(markdownContentModule.markdownUrlTransform("javascript:alert(1)", "href"), "");
  assert.deepEqual(codeWorkspaceModule.markdownFileLinkTarget("/workspace/chat.md", "C:/Users/Alice/readme.md:12"), {
    path: "C:/Users/Alice/readme.md", lineNumber: 12, column: 1,
  });
  assert.equal(codeWorkspaceModule.resolveMarkdownLinkPath(String.raw`C:\project\chat.md`, String.raw`\\server\share\readme.md`), String.raw`\\server\share\readme.md`);
});

test("project opening times out and propagates failures", async () => {
  assert.equal(await appModule.withProjectOpenTimeout(Promise.resolve("opened"), 10), "opened");
  await assert.rejects(appModule.withProjectOpenTimeout(Promise.reject(new Error("Missing folder")), 10), /Missing folder/);
  await assert.rejects(appModule.withProjectOpenTimeout(new Promise(() => {}), 1), /Opening the project timed out/);
});

test("project sidebar announces project switching", () => {
  const markup = renderToStaticMarkup(React.createElement(appModule.WorkflowSidebar, {
    openingProjectRoot: "/workspace/second-brain",
    workflows: [], recentProjectRoots: [], runState: {}, query: "", view: "code",
  }));
  assert.match(markup, /role="status"/);
  assert.match(markup, /Opening second-brain/);
  assert.match(markup, /aria-busy="true"/);
});

test("recent project switching shows progress and does not open discovered Rattish files", async () => {
  const workflow = { ...workflowFixture({ id: "second" }), projectRoot: "/second", sourceFormat: "rattish", sourcePath: "/second/.raticode/demo/workflow.rattish" };
  let finishTrust;
  const dom = await mountReact(React.createElement(appModule.default), createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([workflowFixture()])),
    jsonResponse("/api/projects/open", { workflows: [workflow] }, { method: "POST" }),
  ]), {
    storage: {
      "gofer.recentProjects": JSON.stringify(["/workspace", "/second"]),
      [appModule.STUDIO_SESSION_STORAGE_KEY]: JSON.stringify({ projectRoot: "/workspace", view: "code", workflowId: "demo" }),
    },
    desktop: { workspace: {
      trustProjectRoot: () => new Promise((resolve) => { finishTrust = resolve; }),
      gitWorktrees: async () => ({ worktrees: [] }),
    } },
  });
  await dom.flush();
  await dom.click(dom.byLabel("Recent projects"));
  await dom.click(dom.byTitle("/second"));
  assert.match(dom.text(), /Opening second/);
  finishTrust();
  await dom.flush();
  assert.equal(dom.byLabel("Recent projects").getAttribute("title"), "/second");
  assert.doesNotMatch(dom.text(), /Opening second/);
  assert.ok(dom.byText("Open File"));
  await dom.unmount();
});


test("Rem avatar greets, blinks, restarts on reopen, and stops when disabled", async () => {
  const { default: RemAvatar } = await viteServer.ssrLoadModule("/src/components/RemAvatar.jsx");
  let update;
  function Harness() {
    const [props, setProps] = React.useState({});
    update = setProps;
    return React.createElement(RemAvatar, props);
  }
  const dom = await mountReact(React.createElement(Harness), createFetchMock([]));
  const avatar = dom.first("div");
  try {
    await React.act(async () => {
      for (const img of allElements(dom.container).filter((node) => node.tagName === "IMG")) reactProps(img).onLoad?.();
    });
    assert.equal(avatar.getAttribute("data-pose"), "waving");
    await dom.flush(899);
    assert.equal(avatar.getAttribute("data-pose"), "waving");
    await dom.flush(900);
    assert.equal(avatar.getAttribute("data-pose"), "seated");
    await dom.flush(5500);
    assert.equal(avatar.getAttribute("data-blinking"), "true");
    await dom.flush(160);
    assert.equal(avatar.getAttribute("data-blinking"), "false");
    await React.act(async () => update({ visible: false }));
    await dom.flush(6000);
    assert.equal(avatar.getAttribute("data-animated"), "false");
    await React.act(async () => update({ visible: true }));
    assert.equal(avatar.getAttribute("data-pose"), "waving");
    await React.act(async () => update({ animated: false }));
    assert.equal(avatar.getAttribute("data-pose"), "seated");
    await dom.flush(6000);
    assert.equal(avatar.getAttribute("data-blinking"), "false");
    await React.act(async () => update({ reducedMotion: "on" }));
    assert.equal(avatar.getAttribute("data-animated"), "false");
    document.visibilityState = "hidden";
    await dom.dispatchWindow("visibilitychange");
    await React.act(async () => update({}));
    assert.equal(avatar.getAttribute("data-animated"), "false");
  } finally { await dom.unmount(); }
});

test("Rem keeps its sleep deadline across scrolling and tab changes until explicitly restarted", async () => {
  const { default: RemAvatar } = await viteServer.ssrLoadModule("/src/components/RemAvatar.jsx");
  const previousObserver = globalThis.IntersectionObserver;
  let intersect;
  globalThis.IntersectionObserver = class {
    constructor(callback) { intersect = callback; }
    observe() {}
    disconnect() {}
  };
  let update;
  function Harness() {
    const [props, setProps] = React.useState({ visible: true, home: true });
    update = (patch) => setProps((current) => ({ ...current, ...patch }));
    return props.home ? React.createElement(RemAvatar, props) : null;
  }
  const dom = await mountReact(React.createElement(Harness), createFetchMock([]));
  let now = 0;
  let nextId = 0;
  const timers = new Map();
  window.setTimeout = (callback, delay) => {
    const id = ++nextId;
    timers.set(id, { callback, at: now + delay });
    return id;
  };
  window.clearTimeout = (id) => timers.delete(id);
  async function advance(ms) {
    const end = now + ms;
    while (true) {
      const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      now = next[1].at;
      timers.delete(next[0]);
      await React.act(async () => next[1].callback());
    }
    now = end;
  }
  async function loadImages() {
    await React.act(async () => {
      for (const img of allElements(dom.container).filter((node) => node.tagName === "IMG")) reactProps(img).onLoad?.();
    });
  }
  try {
    await loadImages();
    const avatar = dom.first("div");
    assert.equal(avatar.getAttribute("data-pose"), "waving");
    await advance(3000);
    await React.act(async () => intersect([{ isIntersecting: false }]));
    await advance(200000);
    await React.act(async () => intersect([{ isIntersecting: true }]));
    assert.equal(avatar.getAttribute("data-pose"), "seated");
    document.visibilityState = "hidden";
    await dom.dispatchWindow("visibilitychange");
    await advance(396999);
    assert.equal(avatar.getAttribute("data-pose"), "seated");
    await advance(1);
    assert.equal(avatar.getAttribute("data-pose"), "sleeping");
    document.visibilityState = "visible";
    await dom.dispatchWindow("visibilitychange");
    await React.act(async () => intersect([{ isIntersecting: false }]));
    await React.act(async () => intersect([{ isIntersecting: true }]));
    await React.act(async () => update({ animated: false }));
    await React.act(async () => update({ animated: true }));
    await advance(600000);
    assert.equal(avatar.getAttribute("data-pose"), "sleeping");
    assert.equal(avatar.getAttribute("data-animated"), "false");
    assert.equal(avatar.getAttribute("data-blinking"), "false");
    await React.act(async () => update({ visible: false }));
    await React.act(async () => update({ visible: true }));
    assert.equal(avatar.getAttribute("data-pose"), "waving");
    await advance(599999);
    assert.equal(avatar.getAttribute("data-pose"), "seated");
    await advance(1);
    assert.equal(avatar.getAttribute("data-pose"), "sleeping");
    // Opening a thread unmounts the home avatar; backing out mounts it again.
    await React.act(async () => update({ home: false }));
    await React.act(async () => update({ home: true }));
    await loadImages();
    assert.equal(dom.first("div").getAttribute("data-pose"), "waving");
  } finally {
    await dom.unmount();
    if (previousObserver === undefined) delete globalThis.IntersectionObserver;
    else globalThis.IntersectionObserver = previousObserver;
  }
});

test("Rem avatar preferences default on and persist explicit opt-outs", () => {
  const defaults = settingsModule.normalizeAppSettings({ assistant: { provider: "codex" } });
  assert.equal(defaults.assistant.avatarEnabled, true);
  assert.equal(defaults.assistant.avatarAnimated, true);
  const storage = createStorage();
  settingsModule.saveAppSettings({ assistant: { avatarEnabled: false, avatarAnimated: false } }, storage);
  const saved = settingsModule.loadAppSettings(storage);
  assert.equal(saved.assistant.avatarEnabled, false);
  assert.equal(saved.assistant.avatarAnimated, false);
});


test("project search inclusion globs narrow search and replacement with exclusions taking precedence", async () => {
  const { searchProject, replaceProject } = require("../../electron/project-search.cjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-include-"));
  try {
    fs.mkdirSync(path.join(root, "src"));
    for (const file of ["main.py", "notes.txt", "src/app.py", "src/skip.py", "src/app.js"]) {
      fs.writeFileSync(path.join(root, file), "hello");
    }
    const find = (include, exclude = "") => searchProject(root, { query: "hello", include, exclude });
    assert.equal((await find("")).count, 5);
    assert.equal((await find("missing/**")).count, 0);
    assert.equal((await find("*.py")).count, 3);
    assert.equal((await find("**/*.py")).count, 3);
    assert.equal((await find("src/")).count, 3);
    const include = "src/**, main.py";
    const exclude = "skip.py";
    const result = await find(include, exclude);
    assert.deepEqual(result.files.map((file) => file.relativePath), ["main.py", "src/app.js", "src/app.py"]);
    await replaceProject(root, { query: "hello", include, exclude, replacement: "done", files: result.files });
    assert.equal(fs.readFileSync(path.join(root, "src/app.py"), "utf8"), "done");
    assert.equal(fs.readFileSync(path.join(root, "src/skip.py"), "utf8"), "hello");
    assert.equal(fs.readFileSync(path.join(root, "notes.txt"), "utf8"), "hello");
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("Git integration previews preserve worktrees, detect conflicts, and support resolution and abort", async () => {
  const { runGit, gitRepositoryAction, readGitStatus, changeGitFile, readGitFileBaseline } = require("../../electron/git-status.cjs");
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-integration-"));
  const root = path.join(base, "main"); const feature = path.join(base, "feature");
  fs.mkdirSync(root);
  const git = (...args) => runGit(["-C", root, ...args]);
  try {
    await git("init", "-b", "main"); await git("config", "user.name", "Test"); await git("config", "user.email", "test@example.invalid");
    fs.writeFileSync(path.join(root, "note.txt"), "base\n"); await git("add", "."); await git("commit", "-m", "Base");
    await git("worktree", "add", "-b", "feature", feature);
    fs.writeFileSync(path.join(feature, "note.txt"), "feature\n"); await runGit(["-C", feature, "commit", "-am", "Feature"]);
    fs.writeFileSync(path.join(root, "note.txt"), "main\n"); await git("commit", "-am", "Main");
    const before = await git("status", "--porcelain=v1");
    const preview = await gitRepositoryAction(feature, "merge-preview", { source: "feature", target: "main" });
    assert.deepEqual(preview.conflicts, ["note.txt"]); assert.equal(preview.blocked, false);
    assert.equal(await git("status", "--porcelain=v1"), before);
    assert.equal(fs.readFileSync(path.join(root, "note.txt"), "utf8"), "main\n");
    let result = await gitRepositoryAction(feature, "merge-branch", { source: "feature", target: "main", ...preview });
    assert.equal(result.error, undefined);
    assert.deepEqual(result.conflicts, ["note.txt"]);
    assert.match(result.notice, /Merge paused in main/);
    assert.equal(result.destinationRoot, root); assert.equal(result.destinationStatus.operation, "merge");
    assert.equal(result.destinationStatus.entries[0].status, "!"); assert.equal(result.destinationStatus.entries[0].staged, false);
    const baseline = await readGitFileBaseline(path.join(root, "note.txt"), { group: "unstaged" });
    assert.equal(baseline.conflict, true); assert.equal(baseline.content, "main\n"); assert.equal(baseline.incomingContent, "feature\n");
    await assert.rejects(changeGitFile(root, "note.txt", "stage"), /conflict markers/);
    await assert.rejects(gitRepositoryAction(root, "merge-continue"), /Resolve and stage/);
    await gitRepositoryAction(root, "merge-abort"); assert.equal((await readGitStatus(root)).operation, undefined);
    const failed = await gitRepositoryAction(feature, "merge-branch", { source: "feature", target: "main", ...preview }, {
      runGit: args => {
        if (args.includes("--no-edit")) throw Object.assign(new Error("Merge failed"), { stderr: "Merge hook refused the operation." });
        return runGit(args);
      },
    });
    assert.equal(failed.error, "Merge hook refused the operation.");
    assert.equal(failed.conflicts, undefined);
    const rebase = await gitRepositoryAction(root, "rebase-preview", { source: "feature", target: "main" });
    assert.deepEqual(rebase.conflicts, ["note.txt"]);
    result = await gitRepositoryAction(root, "rebase-branch", { source: "feature", target: "main", ...rebase });
    assert.equal(result.error, undefined);
    assert.deepEqual(result.conflicts, ["note.txt"]);
    assert.equal(result.destinationStatus.operation, "rebase");
    await gitRepositoryAction(feature, "rebase-abort");
    await gitRepositoryAction(feature, "merge-branch", { source: "feature", target: "main", ...preview });
    fs.writeFileSync(path.join(root, "note.txt"), "resolved\n"); await changeGitFile(root, "note.txt", "stage");
    await gitRepositoryAction(root, "merge-continue"); assert.equal((await readGitStatus(root)).operation, undefined);
    assert.equal((await readGitStatus(root)).entries.length, 0);
    await assert.rejects(gitRepositoryAction(root, "merge-branch", { source: "feature", target: "main", ...preview }), /branch changed/);
  } finally { fs.rmSync(base, { recursive: true, force: true }); }
});

test("stash previews include untracked files, detect conflicts and guard discard against stale lists", async () => {
  const { runGit, gitRepositoryAction, readGitStatus } = require("../../electron/git-status.cjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-stashes-"));
  const git = (...args) => runGit(["-C", root, ...args]);
  try {
    await git("init", "-b", "main"); await git("config", "user.name", "Test"); await git("config", "user.email", "test@example.invalid");
    fs.writeFileSync(path.join(root, "note.txt"), "base\n"); await git("add", "."); await git("commit", "-m", "Base");
    fs.writeFileSync(path.join(root, "note.txt"), "stash\n"); fs.writeFileSync(path.join(root, "new.txt"), "untracked\n");
    await git("stash", "push", "-u", "-m", "Saved work");
    const { stashes } = await gitRepositoryAction(root, "stash-list");
    let preview = await gitRepositoryAction(root, "stash-preview", stashes[0]);
    assert.match(preview.diff, /untracked/); assert.deepEqual(preview.conflicts, []); assert.equal(preview.blocked, false);
    assert.equal(fs.existsSync(path.join(root, "new.txt")), false); assert.equal((await readGitStatus(root)).entries.length, 0);
    await gitRepositoryAction(root, "stash-apply-selected", stashes[0]);
    assert.equal(fs.readFileSync(path.join(root, "new.txt"), "utf8"), "untracked\n"); assert.equal((await readGitStatus(root)).stashCount, 1);
    preview = await gitRepositoryAction(root, "stash-preview", stashes[0]); assert.equal(preview.blocked, true);
    await git("reset", "--hard", "HEAD"); fs.unlinkSync(path.join(root, "new.txt"));
    fs.writeFileSync(path.join(root, "note.txt"), "main\n"); await git("commit", "-am", "Main");
    preview = await gitRepositoryAction(root, "stash-preview", stashes[0]);
    assert.ok(preview.conflicts?.includes("note.txt") || preview.blocked); assert.equal((await readGitStatus(root)).entries.length, 0);
    await assert.rejects(gitRepositoryAction(root, "stash-clear", { hashes: [] }), /Stashes changed/);
    await gitRepositoryAction(root, "stash-drop", stashes[0]); assert.equal((await readGitStatus(root)).stashCount, 0);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("conflict choices handle diff3, multiple blocks and preserve selected content", async () => {
  const { conflictBlocks, resolvedConflict } = await viteServer.ssrLoadModule("/src/lib/mergeConflicts.js");
  const blocks = conflictBlocks("before\n<<<<<<< HEAD\nours\n||||||| base\nold\n=======\ntheirs\n>>>>>>> feature\nafter\n<<<<<<< HEAD\n=======\nadded\n>>>>>>> feature\n");
  assert.equal(blocks.length, 2); assert.equal(blocks[0].start, 2); assert.equal(blocks[0].end, 8);
  assert.equal(resolvedConflict(blocks[0], "current"), "ours"); assert.equal(resolvedConflict(blocks[0], "incoming"), "theirs");
  assert.equal(resolvedConflict(blocks[0], "both"), "ours\ntheirs"); assert.equal(resolvedConflict(blocks[1], "both"), "added");
});

test("editor Rem context actions capture the exact selection and its location", async () => {
  const { installRemActions } = await viteServer.ssrLoadModule("/src/lib/editorRem.js");
  const actions = []; const events = [];
  globalThis.window.dispatchEvent = event => events.push(event);
  const editor = { addAction(action) { actions.push(action); return { dispose() {} }; }, getSelection: () => ({ startLineNumber: 4, endLineNumber: 7 }), getModel: () => ({ getValueInRange: () => "selected text" }) };
  const installed = installRemActions(editor, () => ({ path: "/repo/file.py" }));
  assert.deepEqual(actions.map(a => a.label), ["Ask Rem", "Explain with Rem"]);
  actions[0].run(editor); actions[1].run(editor);
  assert.equal(events[0].detail.text, "selected text"); assert.equal(events[0].detail.path, "/repo/file.py");
  assert.equal(events[0].detail.startLine, 4); assert.equal(events[1].detail.mode, "explain"); installed.dispose();
});

test("Ask Rem keeps the selected text attached in a new thread scoped to its own project", async () => {
  const fetchMock = createFetchMock([jsonResponse("/api/provider/capabilities", { providers: [] })]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    workflows: [], width: 380, recentProjectRoots: ["/projects/alpha", "/projects/beta"], workflow: { projectRoot: "/projects/alpha" },
  }), fetchMock);
  await dom.dispatchWindow("gofer:rem-context", { detail: { mode: "ask", path: "/projects/beta/code.py", text: "print('selected')", startLine: 2, endLine: 2 } });
  await dom.flush();
  assert.match(dom.text(), /editor-selection.txt/);
  assert.ok(dom.byLabel("Scoped to beta. Change project scope"));
  assert.equal(dom.first("textarea").value, "");
  assert.equal(fetchMock.calls.filter(call => call.url?.includes("/chat/stream")).length, 0);
  await dom.change(dom.first("textarea"), "Why does this work?");
  assert.equal(dom.first("textarea").value, "Why does this work?");
  assert.match(dom.text(), /editor-selection.txt/);
  await dom.unmount();
});

test("Workflow repair sends once in a fresh thread in the workflow project", async () => {
  let uploaded;
  const chatStream = streamResponse(['{"type":"final","message":{"body":"Fixed"}}\n']);
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
    (url, options) => {
      if (url !== "/api/chat/attachments") return null;
      uploaded = JSON.parse(options.body);
      return jsonResponse(url, { attachments: [{ id: "source", name: "editor-selection.txt", type: "text/plain", storageName: "source.txt" }] }, { method: "POST" })(url, options);
    },
    url => url === "/api/chat/stream" ? chatStream(url) : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    workflows: [], width: 380, workflow: { projectRoot: "/projects/alpha" },
  }), fetchMock);
  await dom.click(dom.byLabel("New thread"));
  await dom.change(dom.first("textarea"), "Keep my existing draft");
  const draft = "Fix the invalid workflow definition in /worktrees/beta/workflow.rattish. Do not execute the workflow.";
  await dom.dispatchWindow("gofer:rem-context", { detail: {
    mode: "ask", autoSend: true, projectRoot: "/worktrees/beta", path: "/worktrees/beta/workflow.rattish",
    text: "Rattish: 1\n\nValidation diagnostics:\nMissing node type", draft,
  } });
  await dom.flush();
  assert.ok(dom.byLabel("Scoped to beta. Change project scope"));
  const requests = fetchMock.calls.filter(call => call.url === "/api/chat/stream");
  assert.equal(requests.length, 1);
  const request = JSON.parse(requests[0].options.body);
  assert.equal(request.workflow.projectRoot, "/worktrees/beta");
  assert.equal(request.workflow.chatThreadId, uploaded.threadId);
  assert.equal(request.messages.at(-1).body || request.messages.at(-1).content, draft);
  assert.match(Buffer.from(uploaded.files[0].data, "base64").toString(), /Missing node type/);
  await dom.unmount();
});

test("Rem project picker discovers worktrees and scopes a thread to their exact paths", async () => {
  const workspace = { gitWorktrees: async root => {
    if (root === "/broken") throw new Error("unavailable");
    return { worktrees: [{ path: root, branch: "main" },
      { path: "/worktrees/feature", branch: "feature" },
      { path: "/missing", missing: true }, { path: "/pruned", prunable: true }] };
  } };
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    workflows: [], width: 380, recentProjectRoots: ["/repo", "/broken"], workflow: { projectRoot: "/repo" },
  }), createFetchMock([jsonResponse("/api/provider/capabilities", { providers: [] })]), { desktop: { workspace } });
  await dom.click(dom.byLabel("New thread"));
  await dom.click(dom.byLabel("Scoped to repo. Change project scope"));
  await dom.flush();
  const choices = allElements(dom.byLabel("Rem project scope")).filter(el => el.getAttribute("role") === "menuitem");
  assert.deepEqual(choices.map(el => el.getAttribute("title")), ["/repo", "/worktrees/feature"]);
  assert.doesNotMatch(dom.text(), /Some worktrees could not be loaded/);
  await dom.click(choices.at(-1));
  assert.ok(dom.byLabel("Scoped to feature. Change project scope"));
  await dom.unmount();
});

test("Rem project picker rechecks saved workspace folders on every opening", async () => {
  const directories = new Set(["/repo", "/worktrees/feature", "/plain-folder"]);
  const checked = [];
  const workspace = {
    getPathInfo: async root => {
      checked.push(root);
      if (root === "/deleted-workflow") throw new Error("ENOENT: no such file or directory");
      if (root === "/unavailable") throw new Error("Permission denied");
      return { isDirectory: directories.has(root) };
    },
    gitWorktrees: async root => ({ worktrees: root === "/repo"
      ? [{ path: "/repo" }, { path: "/worktrees/feature" }, { path: "/stale-discovered" }]
      : [] }),
  };
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    workflows: [{ projectRoot: "/deleted-workflow" }], width: 380,
    recentProjectRoots: ["/repo", "/worktrees/feature", "/plain-folder", "/file", "/unavailable"],
    workflow: { projectRoot: "/worktrees/feature" },
  }), createFetchMock([jsonResponse("/api/provider/capabilities", { providers: [] })]), { desktop: { workspace } });
  const choices = () => allElements(dom.byLabel("Rem project scope"))
    .filter(el => el.getAttribute("role") === "menuitem").map(el => el.getAttribute("title"));
  await dom.click(dom.byLabel("New thread"));
  await dom.click(dom.byLabel("Scoped to feature. Change project scope"));
  await dom.flush();
  assert.deepEqual(choices(), ["/worktrees/feature", "/repo", "/plain-folder"]);
  assert.doesNotMatch(dom.text(), /Some worktrees could not be loaded/);
  await dom.click(dom.byLabel("Scoped to feature. Change project scope"));
  directories.delete("/worktrees/feature");
  await dom.click(dom.byLabel("Scoped to feature. Change project scope"));
  await dom.flush();
  assert.deepEqual(choices(), ["/repo", "/plain-folder"]);
  assert.equal(checked.filter(root => root === "/worktrees/feature").length, 2);
  // The existing conversation retains its scope even when that folder disappears.
  assert.ok(dom.byLabel("Scoped to feature. Change project scope"));
  await dom.unmount();
});

test("Rem project picker excludes roots reported missing by the desktop Git bridge", async () => {
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    workflows: [], width: 380, workflow: { projectRoot: "/deleted" },
  }), createFetchMock([jsonResponse("/api/provider/capabilities", { providers: [] })]), {
    desktop: { workspace: { gitWorktrees: async () => ({ missing: true, worktrees: [] }) } },
  });
  await dom.click(dom.byLabel("Scoped to deleted. Change project scope"));
  await dom.flush();
  assert.equal(allElements(dom.byLabel("Rem project scope")).filter(el => el.getAttribute("role") === "menuitem").length, 0);
  assert.match(dom.text(), /No workspace folders available/);
  assert.equal(dom.byLabel("Scoped to deleted. Change project scope").disabled, false);
  await dom.unmount();
});

test("Explain with Rem sends the selection once in a fresh project thread", async () => {
  let uploaded;
  const chatStream = streamResponse(['{"type":"final","message":{"body":"An explanation"}}\n']);
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
    (url, options) => {
      if (url !== "/api/chat/attachments") return null;
      uploaded = JSON.parse(options.body);
      return jsonResponse(url, { attachments: [{ id: "selection", name: "editor-selection.txt", type: "text/plain", storageName: "selection.txt" }] }, { method: "POST" })(url, options);
    },
    url => url === "/api/chat/stream" ? chatStream(url) : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, { workflows: [], width: 380 }), fetchMock);
  await dom.dispatchWindow("gofer:rem-context", { detail: { mode: "explain", projectRoot: "/projects/beta", path: "/projects/beta/code.py", text: "print('selected')", startLine: 2, endLine: 2 } });
  await dom.flush();
  assert.ok(uploaded.threadId);
  assert.match(Buffer.from(uploaded.files[0].data, "base64").toString(), /File: \/projects\/beta\/code.py[\s\S]*print\('selected'\)/);
  const requests = fetchMock.calls.filter(call => call.url === "/api/chat/stream");
  assert.equal(requests.length, 1);
  const request = JSON.parse(requests[0].options.body);
  assert.equal(request.workflow.projectRoot, "/projects/beta");
  assert.match(request.messages.at(-1).body || request.messages.at(-1).content, /Explain the highlighted/);
  await dom.unmount();
});

test("Resolve conflicts with Rem sends completion and reporting instructions in the project thread", async () => {
  let uploaded;
  const chatStream = streamResponse(['{"type":"final","message":{"body":"Resolved"}}\n']);
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
    (url, options) => {
      if (url !== "/api/chat/attachments") return null;
      uploaded = JSON.parse(options.body);
      return jsonResponse(url, { attachments: [{ id: "conflicts", name: "merge-conflicts.txt", type: "text/plain", storageName: "conflicts.txt" }] }, { method: "POST" })(url, options);
    },
    url => url === "/api/chat/stream" ? chatStream(url) : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, { workflows: [], width: 380 }), fetchMock);
  await dom.dispatchWindow("gofer:rem-context", { detail: { mode: "conflicts", projectRoot: "/worktrees/feature", text: "src/code.py\nremoved.txt" } });
  await dom.flush();
  const requests = fetchMock.calls.filter(call => call.url === "/api/chat/stream");
  assert.equal(requests.length, 1);
  const request = JSON.parse(requests[0].options.body);
  assert.equal(request.workflow.projectRoot, "/worktrees/feature");
  const prompt = request.messages.at(-1).body || request.messages.at(-1).content;
  assert.match(prompt, /authorizes resolving files, staging the fixes, committing them with meaningful commit messages/);
  assert.match(prompt, /stage each resolved path explicitly, including resolved deletions/);
  assert.match(prompt, /Preserve unrelated local changes and do not stage them/);
  assert.match(prompt, /git rebase --continue to create the resolved commit/);
  assert.match(prompt, /editor noninteractively/);
  assert.match(prompt, /If new conflicts appear, resolve, check, stage, and continue again/);
  assert.match(prompt, /Repeat until the rebase is complete/);
  assert.match(prompt, /complete the merge with a meaningful commit message/);
  assert.match(prompt, /Verify that no unmerged paths remain/);
  assert.match(prompt, /report the exact blocker and remaining Git state/);
  assert.match(prompt, /numbered list of one-line summaries, one summary per conflict fix/);
  assert.match(prompt, /Conflicted files:\nsrc\/code.py\nremoved.txt/);
  assert.doesNotMatch(prompt, /Leave the results for me to review/);
  assert.equal(Buffer.from(uploaded.files[0].data, "base64").toString(), prompt);
  await dom.unmount();
});

test("source control exposes conflicts and locks parent controls during integration previews", async () => {
  let finishPreview;
  const snapshot = { active: true, root: "/repo", branch: "main", branches: ["main", "feature", "available"], stashCount: 0, entries: [{ path: "code.py", status: "!", staged: false, unstaged: true }], operation: "merge" };
  const workspace = { trustProjectRoot: async () => {}, listDirectory: async () => ({ entries: [] }), gitStatus: async () => snapshot,
    gitHistory: async () => ({ active: true, commits: [] }), gitWorktrees: async () => ({ active: true, worktrees: [{ path: "/repo", branch: "main" }, { path: "/feature", branch: "feature" }] }),
    gitRepoAction: async (_root, action) => action === "stash-list" ? { stashes: [] } : new Promise(resolve => { finishPreview = resolve; }),
  };
  const opened = [];
  const selected = [];
  const dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: "/repo" }, onOpenFile: (...args) => opened.push(args), onSelectProject: root => selected.push(root) }), createFetchMock([]), { desktop: { workspace } });
  await dom.click(dom.byLabel("Source control")); await dom.flush();
  assert.deepEqual(dom.byLabel("Switch branch").childNodes.filter(node => node.tagName === "OPTION").map(node => reactProps(node).value), ["main", "available"]);
  assert.ok(dom.byText("Resolve conflicts with Rem")); assert.ok(dom.byText("Merge paused"));
  assert.ok(allElements(dom.byLabel("Staged")).some(el => el.getAttribute("title") === "code.py"));
  assert.ok(allElements(dom.byLabel("Unstaged")).some(el => el.getAttribute("title") === "code.py"));
  assert.equal(reactProps(dom.byLabel("Unstage code.py")).disabled, true);
  assert.equal(reactProps(dom.byLabel("Stage code.py")).disabled, false);
  await dom.click(dom.byTitle("code.py")); assert.deepEqual(opened[0], ["/repo/code.py", { diff: true, gitGroup: "unstaged" }]);
  assert.equal(reactProps(dom.byText("Continue merge")).disabled, true);
  await dom.click(dom.byText("Branches")); await dom.click(dom.byLabel("Integrate feature worktree"));
  await dom.change(dom.byLabel("Target branch"), "main"); await dom.click(dom.byText("Preview merge"));
  assert.equal(reactProps(dom.byLabel("Remove feature worktree")).disabled, true);
  await React.act(async () => { finishPreview({ diff: "+new", conflicts: ["code.py"], notice: "1 file will conflict." }); });
  assert.equal(reactProps(dom.byLabel("Remove feature worktree")).disabled, false);
  assert.match(dom.text(), /1 file will conflict/); assert.ok(dom.byText("Merge branch"));
  const previousConfirm = window.confirm;
  window.confirm = () => true;
  window.dispatchEvent = () => true;
  try {
    await dom.click(dom.byText("Merge branch"));
    await React.act(async () => { finishPreview({ ...snapshot, destinationRoot: "/feature", destinationStatus: snapshot, conflicts: ["code.py"], notice: "Merge paused. Resolve the files marked !." }); });
    await dom.flush();
    assert.deepEqual(selected, ["/feature"]);
    assert.equal(dom.byText("Changes").getAttribute("aria-selected"), "true");
    assert.ok(dom.byTitle("code.py"));
    assert.doesNotMatch(dom.text(), /Command failed/);
  } finally { window.confirm = previousConfirm; }
  await dom.unmount();
});

test("worktree context menus list operations and defer target selection without Git mutations", async () => {
  const calls = [];
  const workspace = {
    trustProjectRoot: async () => {}, listDirectory: async () => ({ entries: [] }),
    gitStatus: async () => ({ active: true, root: "/repo", branch: "main", branches: ["main", "feature", "release"], entries: [] }),
    gitHistory: async () => ({ active: true, commits: [] }),
    gitWorktrees: async () => ({ active: true, worktrees: [{ path: "/repo", branch: "main" }, { path: "/feature", branch: "feature" }] }),
    gitRepoAction: async (root, action, value) => {
      calls.push({ root, action, value });
      return action === "stash-list" ? { stashes: [] } : { notice: "Ready to review", diff: "+change" };
    },
  };
  const dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: "/repo" } }), createFetchMock([]), { desktop: { workspace } });
  try {
    window.innerWidth = 1024;
    window.innerHeight = 768;
    await dom.click(dom.byLabel("Source control")); await dom.flush();
    await dom.click(dom.byText("Branches"));
    const icon = dom.byLabel("Integrate feature worktree");
    assert.equal(textOf(icon), "");
    const row = icon.parentNode;
    const menu = label => allElements(document.body).find(el => el.getAttribute("role") === "menu" && el.getAttribute("aria-label") === label);
    for (const kind of ["merge", "rebase", "squash", "ff-only", "no-ff"]) {
      await dom.pointer(row, "onContextMenu", { target: icon, clientX: 100, clientY: 100 });
      const actions = menu("Actions for feature");
      assert.ok(actions);
      const operation = allElements(actions).find(el => el.getAttribute("data-operation") === kind);
      await dom.pointer(operation, "onMouseEnter", { currentTarget: operation });
      assert.equal(document.activeElement, operation, "Hover moves focus to the visible operation");
      assert.equal(operation.getAttribute("aria-haspopup"), null);
      assert.equal(allElements(actions).filter(el => el.tagName === "BUTTON").length, 5);
      await dom.click(operation);
      assert.equal(menu("Actions for feature"), undefined);
      assert.equal(reactProps(dom.byLabel("Integration operation")).value, kind);
      assert.equal(reactProps(dom.byLabel("Target branch")).value, "");
      await dom.change(dom.byLabel("Target branch"), "main");
      await dom.click(dom.byText(`Preview ${kind}`));
      assert.deepEqual(calls.at(-1), { root: "/repo", action: `${kind === "rebase" ? "rebase" : "merge"}-preview`, value: { source: "feature", target: "main", ...(["merge", "rebase"].includes(kind) ? {} : { strategy: kind }) } });
    }
    assert.ok(calls.every(call => call.action === "stash-list" || call.action.endsWith("-preview")));
    await dom.keyDown(row, "F10", { shiftKey: true, target: icon });
    const actions = menu("Actions for feature");
    assert.ok(actions);
    await dom.keyDown(actions.parentNode, "Escape");
    assert.equal(menu("Actions for feature"), undefined);
    assert.equal(document.activeElement, icon);
  } finally { await dom.unmount(); }
});

test("Git merge strategies, commit resets, and worktree starting commits preserve their distinct semantics", async () => {
  const { runGit, gitRepositoryAction, addGitWorktree } = require("../../electron/git-status.cjs");
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-history-"));
  const root = path.join(base, "repo"); fs.mkdirSync(root);
  const git = (...args) => runGit(["-C", root, ...args]);
  try {
    await git("init", "-b", "main"); await git("config", "user.name", "Test"); await git("config", "user.email", "test@example.invalid");
    fs.writeFileSync(path.join(root, "note"), "base\n"); await git("add", "."); await git("commit", "-m", "Base");
    const first = (await git("rev-parse", "HEAD")).trim();
    const subfolder = path.join(root, "nested"); fs.mkdirSync(subfolder);
    await assert.rejects(gitRepositoryAction(subfolder, "reset-hard", { hash: first }), /repository root/);
    await assert.rejects(gitRepositoryAction(subfolder, "staged-diff"), /repository root/);

    await git("switch", "-c", "feature");
    fs.writeFileSync(path.join(root, "note"), "feature\n"); await git("commit", "-am", "Feature");
    const second = (await git("rev-parse", "HEAD")).trim();
    await git("switch", "main");
    for (const strategy of ["squash", "ff-only", "no-ff"]) {
      const preview = await gitRepositoryAction(root, "merge-preview", { source: "feature", target: "main", strategy });
      assert.equal(preview.blocked, false);
      assert.equal((await git("rev-parse", "HEAD")).trim(), first);
      await gitRepositoryAction(root, "merge-branch", { source: "feature", target: "main", strategy, ...preview });
      if (strategy === "squash") {
        assert.equal((await git("rev-parse", "HEAD")).trim(), first);
        assert.match((await gitRepositoryAction(root, "staged-diff")).diff, /\+feature/);
      } else if (strategy === "ff-only") assert.equal((await git("rev-parse", "HEAD")).trim(), second);
      else assert.equal((await git("rev-list", "--parents", "-n", "1", "HEAD")).trim().split(" ").length, 3);
      await gitRepositoryAction(root, "reset-hard", { hash: first });
    }
    await gitRepositoryAction(root, "reset-soft", { hash: second });
    assert.equal(fs.readFileSync(path.join(root, "note"), "utf8"), "base\n");
    assert.match(await git("diff", "--cached"), /\+base/);
    await gitRepositoryAction(root, "reset-hard", { hash: first });
    await gitRepositoryAction(root, "branch-commit", { hash: second, branch: "from-history" });
    assert.equal((await git("branch", "--show-current")).trim(), "from-history");
    await gitRepositoryAction(root, "detach-commit", { hash: first });
    assert.equal((await git("branch", "--show-current")).trim(), "");
    const destination = path.join(base, "worktree"); fs.mkdirSync(destination);
    await addGitWorktree(root, destination, "historic", { createBranch: true, startPoint: second });
    assert.equal((await runGit(["-C", destination, "rev-parse", "HEAD"])).trim(), second);
    await assert.rejects(gitRepositoryAction(root, "reset-hard", { hash: "--bad-option" }), /valid commit/);
    await git("switch", "main");
    fs.writeFileSync(path.join(root, "other"), "diverged\n"); await git("add", "."); await git("commit", "-m", "Diverge");
    const blocked = await gitRepositoryAction(root, "merge-preview", { source: "feature", target: "main", strategy: "ff-only" });
    assert.equal(blocked.blocked, true);
  } finally { fs.rmSync(base, { recursive: true, force: true }); }
});

test("Conventional Commit generation uses the restricted endpoint and rejects invalid output", async () => {
  const { generateConventionalCommit, conventionalCommitMessage } = await import("../lib/commit-message.js");
  assert.equal(conventionalCommitMessage("fix(git): expose resolved edits"), "fix(git): expose resolved edits");
  assert.throws(() => conventionalCommitMessage("Here is your message"), /Conventional Commit/);
  const previousFetch = globalThis.fetch;
  let request;
  globalThis.fetch = async (_url, options) => {
    request = JSON.parse(options.body);
    assert.match(_url, /chat\/commit-message$/);
    return { ok: true, json: async () => ({ message: "feat(git): add squash merge" }) };
  };
  try {
    assert.equal(await generateConventionalCommit({ provider: "codex", model: "gpt-6-astra", effort: "high", diff: "+staged" }), "feat(git): add squash merge");
    assert.equal(request.model, "gpt-6-astra");
    assert.equal(request.provider, "codex");
    assert.equal(request.effort, "high");
    assert.equal(request.diff, "+staged");
    for (const diff of ["x".repeat(120001), "\u0000".repeat(2100000)]) {
      await generateConventionalCommit({ provider: "codex", model: "cli-default", projectRoot: "/repo", diff });
      assert.equal(request.inspectStaged, true);
      assert.equal(request.projectRoot, "/repo");
      assert.equal(request.diff, undefined);
    }
    await generateConventionalCommit({ provider: "codex", projectRoot: "/repo", inspectStaged: true });
    assert.equal(request.inspectStaged, true);
  } finally { globalThis.fetch = previousFetch; }
});

test("commit history menu requests resets and prepopulates a worktree at the selected commit", async () => {
  const hash = "a".repeat(40), calls = [];
  const workspace = { trustProjectRoot: async () => {}, listDirectory: async () => ({ entries: [] }),
    gitStatus: async () => ({ active: true, branch: "main", branches: ["main"], entries: [] }),
    gitHistory: async () => ({ active: true, commits: [{ hash, shortHash: "aaaaaaa", subject: "Historic change" }] }),
    gitWorktrees: async () => ({ active: true, worktrees: [] }),
    gitRepoAction: async (_root, action, value) => { calls.push({ action, value }); return { active: true, branch: "main", branches: ["main"], entries: [], stashes: [] }; },
  };
  const dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: "/repo" } }), createFetchMock([]), { desktop: { workspace } });
  const confirm = window.confirm; window.confirm = () => false;
  window.dispatchEvent = () => true;
  try {
    window.innerWidth = 1024; window.innerHeight = 768;
    await dom.click(dom.byLabel("Source control")); await dom.click(dom.byText("History")); await dom.flush();
    const open = async () => {
      const button = dom.byText("Historic change").parentNode.parentNode;
      await dom.pointer(button.parentNode.parentNode, "onContextMenu", { target: button, clientX: 50, clientY: 50 });
    };
    await open(); await dom.click(allElements(document.body).find(el => el.getAttribute("data-operation") === "reset-hard")); assert.equal(calls.length, 0);
    window.confirm = () => true;
    await open(); await dom.click(allElements(document.body).find(el => el.getAttribute("data-operation") === "reset-soft")); await dom.flush();
    assert.deepEqual(calls[0], { action: "reset-soft", value: { hash } });
    await open(); await dom.click(allElements(document.body).find(el => el.getAttribute("data-operation") === "branch-commit"));
    await dom.focus(dom.byLabel("New branch name"));
    await dom.change(dom.byLabel("New branch name"), "");
    await dom.change(dom.byLabel("New branch name"), "history-branch");
    await dom.blur(dom.byLabel("New branch name"));
    await React.act(async () => { const form = dom.byLabel("New branch name").parentNode.parentNode; await reactProps(form).onSubmit(testEvent(form)); });
    await dom.flush();
    assert.deepEqual(calls.at(-1), { action: "branch-commit", value: { hash, branch: "history-branch" } });
    await open(); await dom.click(allElements(document.body).find(el => el.getAttribute("data-operation") === "worktree-commit"));
    assert.equal(dom.byText("Branches").getAttribute("aria-selected"), "true");
    assert.match(dom.text(), /Starting at aaaaaaaa/);
  } finally { window.confirm = confirm; await dom.unmount(); }
});

test("Rem commit button uses staged diff, preserves typed drafts, and rejects stale index results", async () => {
  let tree = "tree-1", pending, inspectStaged = false;
  const calls = [];
  const snapshot = { active: true, branch: "main", branches: ["main"], entries: [{ path: "note", status: "M", staged: true, unstaged: true }] };
  const workspace = { trustProjectRoot: async () => {}, listDirectory: async () => ({ entries: [] }), gitStatus: async () => snapshot,
    gitHistory: async () => ({ active: true, commits: [] }), gitWorktrees: async () => ({ active: true, worktrees: [] }),
    gitRepoAction: async (_root, action) => { calls.push(action); return inspectStaged ? { tree, inspectStaged: true } : { tree, diff: "+staged-only" }; },
  };
  const dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: "/repo" } }), createFetchMock([]), { desktop: { workspace } });
  window.dispatchEvent = event => { if (event.type === "gofer:rem-commit-message") pending = event.detail; return true; };
  try {
    await dom.click(dom.byLabel("Source control")); await dom.flush();
    await dom.click(dom.byLabel("Generate commit message with Rem")); await dom.flush();
    assert.equal(pending.diff, "+staged-only");
    await React.act(async () => pending.resolve("fix: expose working edits")); await dom.flush();
    assert.equal(reactProps(dom.byLabel("Commit message")).value, "fix: expose working edits");
    await dom.click(dom.byLabel("Generate commit message with Rem")); await dom.flush();
    await dom.change(dom.byLabel("Commit message"), "fix: my edited draft");
    await React.act(async () => pending.resolve("fix: generated replacement")); await dom.flush();
    assert.equal(reactProps(dom.byLabel("Commit message")).value, "fix: my edited draft");
    await dom.click(dom.byLabel("Generate commit message with Rem")); await dom.flush();
    tree = "tree-2";
    await React.act(async () => pending.resolve("feat: outdated message")); await dom.flush();
    assert.match(dom.text(), /Staged changes changed/);
    assert.equal(reactProps(dom.byLabel("Commit message")).value, "fix: my edited draft");
    inspectStaged = true;
    await dom.click(dom.byLabel("Generate commit message with Rem")); await dom.flush();
    assert.equal(pending.inspectStaged, true);
    assert.equal(pending.diff, undefined);
    assert.equal(pending.projectRoot, "/repo");
    await React.act(async () => pending.resolve("fix: summarize large changes")); await dom.flush();
    assert.equal(reactProps(dom.byLabel("Commit message")).value, "fix: summarize large changes");
    assert.ok(calls.every(action => action === "staged-diff"));
  } finally { await dom.unmount(); }
});

test("Rem accepts staged diffs larger than 200000 characters", async () => {
  const { gitRepositoryAction } = require("../../electron/git-status.cjs");
  const diff = Array.from({ length: 92 }, (_, i) => `diff --git a/file-${i} b/file-${i}\n${"+change\n".repeat(1000)}`).join("");
  const result = await gitRepositoryAction("/repo", "staged-diff", "", { runGit: async args => {
    if (args.includes("rev-parse")) return "/repo\n";
    if (args.includes("--diff-filter=U")) return "";
    if (args.includes("write-tree")) return "staged-tree\n";
    if (args.includes("--cached")) return diff;
    throw new Error(`Unexpected Git command: ${args}`);
  } });
  assert.equal(result.diff, undefined);
  assert.equal(result.inspectStaged, true);
  assert.equal(result.tree, "staged-tree");
});


test("Rem permissions show provider-specific choices and update the request", async () => {
  function Harness() {
    const [provider, setProvider] = React.useState("codex");
    const [modes, setModes] = React.useState({ codex: "workspace-write", claude_code: "dontAsk" });
    return React.createElement(React.Fragment, null,
      React.createElement("button", { onClick: () => setProvider(provider === "codex" ? "claude_code" : "codex") }, "Switch provider"),
      React.createElement(chatComposerModule.default, {
        draft: "hello", provider, permissionMode: modes[provider],
        onPermissionModeChange: (value) => setModes({ ...modes, [provider]: value }),
        onDraftChange() {}, onSend() {},
      }),
    );
  }
  const dom = await mountReact(React.createElement(Harness), createFetchMock([]));
  let select = dom.selectWithOption("danger-full-access");
  assert.equal(select.getAttribute("aria-label"), "Rem permissions");
  await dom.change(select, "danger-full-access");
  assert.equal(reactProps(select).value, "danger-full-access");
  assert.equal(appModule.chatStreamRequestBody({ provider: "codex", permissionMode: reactProps(select).value }).permissionMode, "danger-full-access");
  await dom.click(dom.byText("Switch provider"));
  select = dom.selectWithOption("bypassPermissions");
  assert.equal(reactProps(select).value, "dontAsk");

  await dom.change(select, "plan");
  assert.equal(reactProps(select).value, "plan");
  await dom.click(dom.byText("Switch provider"));
  assert.equal(reactProps(dom.selectWithOption("danger-full-access")).value, "danger-full-access");
  await dom.unmount();
});

test("Rem permissions cannot change while a message is running", () => {
  const markup = renderToStaticMarkup(React.createElement(chatComposerModule.default, {
    draft: "hello", sending: true, provider: "codex", permissionMode: "read-only",
  }));
  assert.match(markup, /<select[^>]*aria-label="Rem permissions"[^>]*disabled=""/);
});


test("Electron folder registration reports failures without leaking credentials and recovers", async () => {
  const { createPathGrantQueue } = await import("../../electron/path-grant-queue.cjs");
  const source = fs.readFileSync(path.join(repoRoot, "frontend/electron/main.js"), "utf8");
  const functionSource = source.slice(source.indexOf("async function registerBackendPathGrant(handle)"), source.indexOf("function getIpcSecurity()"));
  const logs = [];
  const handle = { path: "/outside/brain", grantId: "private-grant" };
  let response;
  const sandbox = {
    backendPathGrants: createPathGrantQueue(),
    getIpcSecurity: () => ({ isUserGrant: () => false }),
    backendReady: Promise.resolve(),
    activeApiBaseUrl: "http://127.0.0.1:1234",
    desktopGrantSecret: "private-secret",
    activeUiApiToken: "private-token",
    AbortSignal,
    writeBackendLog: (line) => logs.push(line),
    fetch: async () => {
      if (response instanceof Error) throw response;
      return response;
    },
  };
  const register = vm.runInNewContext(`${functionSource}\nregisterBackendPathGrant`, sandbox);
  for (const [failure, reason, message] of [
    [{ ok: false, status: 403 }, "http-error", /HTTP 403/],
    [{ ok: false, status: 503 }, "http-error", /HTTP 503/],
    [Object.assign(new Error("private-secret"), { name: "TimeoutError" }), "timeout", /timed out/],
    [new Error("private-token"), "network-error", /could not confirm/],
    [{ ok: true, status: 201, json: async () => ({ grantId: "wrong" }) }, "invalid-response", /could not confirm/],
  ]) {
    response = failure;
    await assert.rejects(register(handle), message);
    assert.match(logs.at(-1), new RegExp(reason));
    assert.match(logs.at(-1), /outside\/brain/);
    assert.match(logs.at(-1), /durationMs/);
  }
  sandbox.activeApiBaseUrl = "";
  await assert.rejects(register(handle), /not ready/);
  assert.match(logs.at(-1), /backend-unavailable/);
  sandbox.activeApiBaseUrl = "http://127.0.0.1:1234";
  response = { ok: true, status: 201, json: async () => handle };
  await register(handle);
  assert.match(logs.at(-1), /PATH_GRANT_REGISTERED/);
  assert.doesNotMatch(logs.join(""), /private-secret|private-token|private-grant/);
});

test("Electron preload clears a failed renewal and permits a later retry", async () => {
  let fail = false;
  const exposed = runPreload({
    argv: ["electron", "preload"],
    invoke() {
      if (fail) throw new Error("Could not renew Raticode folder access. Retry the action.");
      return { grantId: "grant-brain", path: "/outside/brain" };
    },
  });
  const workspace = exposed.goferDesktop.workspace;
  await workspace.trustProjectRoot("/outside/brain");
  assert.equal(workspace.pathGrantForApi("/outside/brain"), "grant-brain");
  fail = true;
  await assert.rejects(workspace.trustProjectRoot("/outside/brain"), /Could not renew/);
  assert.equal(workspace.pathGrantForApi("/outside/brain"), "");
  fail = false;
  await workspace.trustProjectRoot("/outside/brain");
  assert.equal(workspace.pathGrantForApi("/outside/brain"), "grant-brain");
});

test("Rem stops before sending chat when folder renewal fails and allows retry", async () => {
  let fail = true;
  const desktop = { workspace: {
    trustProjectRoot: async () => {
      if (fail) throw new Error("Could not renew Raticode folder access. Retry the action.");
    },
    pathGrantForApi: () => "renewed-grant",
  } };
  const chatStream = streamResponse(['{"type":"final","message":{"body":"Recovered reply"}}\n']);
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
    url => url === "/api/chat/stream" ? chatStream(url) : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    workflows: [], width: 380,
    memorySettings: { secondBrainEnabled: true, secondBrainRoot: "/outside/brain", secondBrainFormat: "html" },
  }), fetchMock, { desktop });
  await dom.flush();
  await dom.change(dom.first("textarea"), "Search my notes");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  assert.match(dom.text(), /Could not renew Raticode folder access/);
  assert.equal(fetchMock.calls.filter(call => call.url === "/api/chat/stream").length, 0);
  fail = false;
  await dom.change(dom.first("textarea"), "Try again");
  await dom.click(dom.byTitle("Send message"));
  await dom.flush();
  assert.equal(fetchMock.calls.filter(call => call.url === "/api/chat/stream").length, 1);
  assert.match(dom.text(), /Recovered reply/);
  await dom.unmount();
});

test('workspace keeps dirty tabs when closing is cancelled or the editor cannot save', async () => {
  const filePath = '/repo/unsaved.png';
  const closed = [], discarded = [];
  function DirtyWorkspace() {
    const [openPaths, setOpenPaths] = React.useState([filePath]);
    return React.createElement(codeWorkspaceModule.default, {
      active: true,
      activePath: filePath,
      openPaths,
      rattishDirty: true,
      settings: { ...settingsModule.DEFAULT_APP_SETTINGS, general: { ...settingsModule.DEFAULT_APP_SETTINGS.general, autosave: false } },
      workflow: { projectRoot: '/repo', sourcePath: filePath },
      onClosePaths: paths => {
        closed.push(...paths);
        setOpenPaths(current => current.filter(path => !paths.includes(path)));
      },
      onRattishDiscard: () => discarded.push(filePath),
    });
  }
  // The editor is unavailable in this harness, so Save returns null. The
  // workspace must retain the dirty document on this unsuccessful result.
  const dom = await mountReact(React.createElement(DirtyWorkspace), createFetchMock([]));
  try {
    await dom.click(dom.byLabel('Close unsaved.png'));
    await dom.click(dom.ancestor(dom.byText('Cancel'), 'BUTTON'));
    assert.deepEqual(closed, []);
    assert.ok(dom.byLabel('Unsaved changes'));
    await dom.click(dom.byLabel('Close unsaved.png'));
    await dom.click(allElements(dom.container).find(node => node.tagName === 'BUTTON' && directText(node).trim() === 'Save'));
    await dom.flush();
    assert.match(dom.text(), /couldn't save every file/);
    assert.deepEqual(closed, []);
    assert.deepEqual(discarded, []);
    assert.ok(dom.byLabel('Unsaved changes'));
    await dom.click(dom.ancestor(dom.byText('Discard changes'), 'BUTTON'));
    await dom.flush();
    assert.deepEqual(closed, [filePath]);
    assert.deepEqual(discarded, [filePath]);
    assert.equal(allElements(dom.container).some(node => node.getAttribute?.('aria-label') === 'Close unsaved.png'), false);
  } finally { await dom.unmount(); }
});


test("recent projects drop deleted folders and reset missing worktree selections on focus", async () => {
  const directories = new Set(["/main", "/feature", "/deleted"]);
  const dom = await mountReact(React.createElement(appModule.default), createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([])),
    jsonResponse("/api/projects/open", { workflows: [] }, { method: "POST" }),
  ]), {
    storage: {
      "raticode.studioSession.v1": JSON.stringify({ projectRoot: "/feature", view: "code" }),
      "gofer.recentProjects": JSON.stringify(["/main", "/deleted"]),
      "gofer.lastWorktreeByProject": JSON.stringify({ "/main": "/feature", "/deleted": "/deleted" }),
    },
    desktop: { workspace: {
      trustProjectRoot: async root => {
        if (!directories.has(root)) throw new Error(`Path does not exist: ${root}`);
      },
      getPathInfo: async root => ({ isDirectory: directories.has(root) }),
      gitWorktrees: async root => ({ worktrees: [{ path: root === "/feature" ? "/main" : root }] }),
    } },
  });
  try {
    await dom.flush();
    directories.delete("/feature");
    directories.delete("/deleted");
    await dom.dispatchWindow("focus");
    await dom.flush();
    assert.deepEqual(JSON.parse(window.localStorage.getItem("gofer.recentProjects")), ["/main"]);
    assert.deepEqual(JSON.parse(window.localStorage.getItem("gofer.lastWorktreeByProject")), { "/main": "/main" });
    await dom.click(dom.byText("File"));
    await dom.click(dom.ancestor(dom.byText("Recent Projects"), "BUTTON"));
    assert.ok(dom.byText("main"));
    assert.throws(() => dom.byText("deleted"));
    assert.doesNotMatch(dom.text(), /Path does not exist/);
    assert.equal(appModule.loadStudioSession().projectRoot, "/main");
    directories.delete("/main");
    await dom.dispatchWindow("focus");
    await dom.flush();
    assert.equal(appModule.loadStudioSession().projectRoot, "");
    assert.deepEqual(JSON.parse(window.localStorage.getItem("gofer.recentProjects")), []);
  } finally { await dom.unmount(); }
});


test("Electron preload clears stale grants when renewal reports a missing folder", async () => {
  let missing = false;
  const exposed = runPreload({ argv: ["electron", "preload"], invoke(_channel, payload) {
    return missing ? { missing: true } : { path: payload.targetPath, grantId: "fixture-grant" };
  } });
  const workspace = exposed.goferDesktop.workspace;
  await workspace.trustProjectRoot("/project");
  assert.equal(workspace.pathGrantForApi("/project"), "fixture-grant");
  missing = true;
  await assert.rejects(workspace.trustProjectRoot("/project"), /Path does not exist/);
  assert.equal(workspace.pathGrantForApi("/project"), "");
});

test("worktree list removes externally deleted entries on focus", async () => {
  let items = [{ path: "/main", branch: "main", main: true }, { path: "/feature", branch: "feature" }];
  const dom = await mountReact(React.createElement(codeFileExplorerModule.default, {
    workflow: { projectRoot: "/main" }, onOpenFile() {},
  }), createFetchMock([]), { desktop: { workspace: {
    listDirectory: async () => ({ entries: [] }),
    gitStatus: async () => ({ active: true, entries: [], branch: "main" }),
    gitHistory: async () => ({ active: true, commits: [] }),
    gitWorktrees: async () => ({ active: true, worktrees: items }),
  } } });
  try {
    await dom.click(dom.byLabel("Source control"));
    await dom.click(dom.byText("Branches"));
    assert.ok(dom.byLabel("Remove feature worktree"));
    items = items.slice(0, 1);
    await dom.dispatchWindow("focus");
    await dom.flush();
    assert.throws(() => dom.byLabel("Remove feature worktree"));
  } finally { await dom.unmount(); }
});


test("relative chat file links show resolved hover paths and preserve line targets", async () => {
  const opened = [];
  const sourcePath = appModule.assistantMarkdownSourcePath("/repo/project");
  const dom = await mountReact(React.createElement(appModule.MarkdownMessage, {
    sourcePath,
    value: "[Source](README.md:12:3) [Guide](../notes/guide%20one.md) [Web](//example.com/docs)",
    onOpenLink: (href) => opened.push(codeWorkspaceModule.markdownFileLinkTarget(sourcePath, href)),
  }), createFetchMock([]));
  const source = dom.ancestor(dom.byText("Source"), "A");
  const guide = dom.ancestor(dom.byText("Guide"), "A");
  assert.equal(source.getAttribute("href"), "README.md:12:3");
  assert.equal(source.getAttribute("title"), "/repo/project/README.md:12:3");
  assert.equal(guide.getAttribute("title"), "/repo/notes/guide one.md");
  await dom.click(source);
  await dom.click(guide);
  assert.deepEqual(opened, [
    { path: "/repo/project/README.md", lineNumber: 12, column: 3 },
    { path: "/repo/notes/guide one.md", lineNumber: null, column: 1 },
  ]);
  assert.equal(markdownContentModule.markdownUrlTransform("javascript:123", "href"), "");
  assert.equal(codeWorkspaceModule.resolveMarkdownLinkPath(sourcePath, "//example.com/docs"), "");
  await dom.unmount();
});

test("Markdown preview links show paths relative to the document directory", async () => {
  const dom = await mountReact(React.createElement(codeWorkspaceModule.MarkdownPreview, {
    path: "/repo/docs/guide.md",
    content: "[Readme](../README.md)",
  }), createFetchMock([]));
  assert.equal(dom.ancestor(dom.byText("Readme"), "A").getAttribute("title"), "/repo/README.md");
  await dom.unmount();
});

test("HTML previews open resolved local links on ordinary clicks", () => {
  const { dispatch, sent } = runBrowserPreload("file:///repo/report.html");
  for (const href of ["file:///repo/docs/guide.md", "file:///repo/reports/next.html", "file:///repo/src/app.py"]) {
    const anchor = { tagName: "A", href, hasAttribute: () => false };
    const event = browserPageEvent({ composedPath: () => [anchor], type: "click" });
    dispatch("click", event);
    assert.equal(event.defaultPrevented, true);
    assert.equal(sent.at(-1).payload.url, href);
  }
  const event = browserPageEvent({
    composedPath: () => [{ tagName: "A", href: "file:///repo/report.html#heading", hasAttribute: () => false }],
    type: "click",
  });
  dispatch("click", event);
  assert.equal(event.defaultPrevented, false);
  assert.equal(sent.length, 3);
});


test("HTML file links use the studio path checks across folders and preserve locations", () => {
  const source = fs.readFileSync(path.join(repoRoot, "frontend/electron/main.js"), "utf8");
  const body = source.slice(source.indexOf("function openBrowserLink("), source.indexOf("function showBrowserContextMenu("));
  const sent = [];
  let currentUrl = "file:///repo/report.html";
  const sandbox = {
    URL,
    isAllowedBrowserNavigation: () => false,
    browserSessionContents: () => ({ getURL: () => currentUrl }),
  };
  vm.runInNewContext(body, sandbox);
  const session = { grantId: "test-grant", owner: { isDestroyed: () => false, send: (...args) => sent.push(args) } };
  for (const href of ["file:///repo/guide.md", "file:///outside/next.html", "file:///outside/app.py:12:3"]) {
    sandbox.openBrowserLink(session, href);
    assert.deepEqual(toPlainObject(sent.at(-1)), ["gofer:browser-open-file", { href }]);
  }
  assert.deepEqual(codeWorkspaceModule.markdownFileLinkTarget("", sent.at(-1)[1].href), {
    path: "/outside/app.py", lineNumber: 12, column: 3,
  });
  currentUrl = "https://example.com";
  sandbox.openBrowserLink(session, "file:///outside/private.md");
  currentUrl = "file:///repo/report.html";
  sandbox.openBrowserLink({ ...session, grantId: "" }, "file:///outside/private.md");
  sandbox.openBrowserLink(session, "invalid URL");
  assert.equal(sent.length, 3);
});


test("Rem and Markdown previews open absolute paths and file URLs with spaces", async () => {
  const content = "[Absolute](</outside/My Notes/guide.md:8>) [File](file:///outside/My%20Notes/report.html)";
  for (const chat of [true, false]) {
    const opened = [];
    const props = chat
      ? { value: content, sourcePath: "/repo/chat.md", onOpenLink: href => opened.push(href) }
      : { content, path: "/repo/guide.md", onOpenRelativeLink: href => opened.push(href) };
    const dom = await mountReact(React.createElement(
      chat ? appModule.MarkdownMessage : codeWorkspaceModule.MarkdownPreview, props,
    ), createFetchMock([]));
    try {
      for (const label of ["Absolute", "File"]) await dom.click(dom.ancestor(dom.byText(label), "A"));
      assert.deepEqual(opened.map(href => codeWorkspaceModule.markdownFileLinkTarget("/repo/guide.md", href)), [
        { path: "/outside/My Notes/guide.md", lineNumber: 8, column: 1 },
        { path: "/outside/My Notes/report.html", lineNumber: null, column: 1 },
      ]);
      assert.equal(dom.ancestor(dom.byText("File"), "A").getAttribute("title"), "/outside/My Notes/report.html");
    } finally { await dom.unmount(); }
  }
});


test("Electron preload caches user navigation grants for local file reads", async () => {
  const calls = [];
  const exposed = runPreload({
    argv: ["electron", "preload"],
    invoke(channel, payload) {
      calls.push({ channel, payload });
      if (channel === "gofer:grant-user-path") return { path: "/outside/note.md", grantId: "desktop-only" };
      return { channel, payload };
    },
  });
  const selected = await exposed.goferDesktop.workspace.grantUserPath("/outside/shortcut.md");
  assert.deepEqual(toPlainObject(selected), { path: "/outside/note.md" });
  await exposed.goferDesktop.textFiles.read(selected.path);
  assert.equal(calls[1].channel, "gofer:read-text-file");
  assert.equal(calls[1].payload.grantId, "desktop-only");
  assert.equal(calls.some(call => call.channel === "gofer:grant-path"), false);
});

test("Rem and Markdown website links open HTTP, HTTPS, and scheme-relative URLs", async () => {
  const content = "[Secure](https://example.com/docs?q=one#two) [HTTP](http://example.com/) [Relative scheme](//example.com/docs)";
  for (const chat of [true, false]) {
    const localLinks = [];
    const opened = [];
    const props = chat
      ? { value: content, sourcePath: "/repo/chat.md", onOpenLink: href => localLinks.push(href) }
      : { content, path: "/repo/guide.md", onOpenRelativeLink: href => localLinks.push(href) };
    const dom = await mountReact(React.createElement(
      chat ? appModule.MarkdownMessage : codeWorkspaceModule.MarkdownPreview, props,
    ), createFetchMock([]));
    window.open = (...args) => opened.push(args);
    try {
      for (const label of ["Secure", "HTTP", "Relative scheme"]) {
        const link = dom.ancestor(dom.byText(label), "A");
        assert.match(link.getAttribute("href"), /^https?:\/\//);
        await dom.click(link);
      }
      assert.deepEqual(opened, [
        ["https://example.com/docs?q=one#two", "_blank", "noopener,noreferrer"],
        ["http://example.com/", "_blank", "noopener,noreferrer"],
        ["https://example.com/docs", "_blank", "noopener,noreferrer"],
      ]);
      assert.deepEqual(localLinks, []);
    } finally { await dom.unmount(); }
  }
});

test("local HTML website clicks hand off to browser tabs for ordinary and modified clicks", () => {
  const { dispatch, sent } = runBrowserPreload("file:///repo/report.html");
  for (const href of ["https://example.com/docs?q=one#two", "http://example.com/", "//example.com/docs"]) {
    for (const ctrlKey of [false, true]) {
      const anchor = {
        tagName: "A", href: href.startsWith("//") ? `file:${href}` : href,
        getAttribute: () => href, hasAttribute: () => false,
      };
      const event = browserPageEvent({ ctrlKey, composedPath: () => [anchor] });
      dispatch("click", event);
      assert.equal(event.defaultPrevented, true);
      assert.deepEqual(toPlainObject(sent.at(-1)), {
        channel: "gofer:browser-link-clicked",
        payload: { url: href.startsWith("//") ? `https:${href}` : href },
      });
    }
  }
});

test("local HTML popup and click handoffs open websites without carrying file grants", () => {
  const source = fs.readFileSync(path.join(repoRoot, "frontend/electron/main.js"), "utf8");
  const body = source.slice(source.indexOf("function configureBrowserSession("), source.indexOf("function showBrowserContextMenu("));
  const sent = [];
  let popup;
  const contents = {
    on() {}, getURL: () => "file:///repo/report.html",
    setWindowOpenHandler: handler => { popup = handler; },
  };
  const sandbox = { URL, browserSessionContents: () => contents };
  vm.runInNewContext(body, sandbox);
  const session = {
    contents, grantId: "local-file-grant",
    owner: { isDestroyed: () => false, send: (...args) => sent.push(args) },
  };
  sandbox.configureBrowserSession(session);
  for (const url of ["https://example.com/docs", "http://example.com/"]) {
    sandbox.openBrowserLink(session, url);
    assert.deepEqual(toPlainObject(sent.at(-1)), ["gofer:browser-open-tab", { url }]);
    assert.equal(popup({ url }).action, "deny");
    assert.deepEqual(toPlainObject(sent.at(-1)), ["gofer:browser-open-tab", { url }]);
  }
  for (const url of ["javascript:alert(1)", "data:text/html,hello", "about:blank"]) {
    sandbox.openBrowserLink(session, url);
    assert.equal(popup({ url }).action, "deny");
  }
  assert.equal(sent.length, 4);
});

test("Branches lists inactive branches without checkout and offers deletion and worktree picking", async () => {
  const calls = [], selected = [];
  let picker = null;
  let snapshot = { active: true, root: '/repo', branch: 'main', branches: ['main', 'occupied', 'feature'], entries: [] };
  const workspace = {
    trustProjectRoot: async () => {}, listDirectory: async () => ({ entries: [] }),
    gitStatus: async () => snapshot, gitHistory: async () => ({ commits: [] }),
    gitWorktrees: async () => ({ active: true, worktrees: [{ path: '/repo', branch: 'main' }, { path: '/occupied', branch: 'occupied' }] }),
    gitSwitchBranch: async () => { throw new Error('Branch rows must not switch'); },
    selectPath: async options => { calls.push(['picker', options]); return picker; },
    addWorktree: async options => { calls.push(['worktree', options]); return { createdPath: options.targetPath, worktrees: [{ path: '/repo', branch: 'main' }, { path: options.targetPath, branch: options.branch }] }; },
    gitRepoAction: async (_root, action, value) => {
      if (action === 'stash-list') return { stashes: [] };
      calls.push([action, value]);
      if (action === 'branch-delete') snapshot = { ...snapshot, branches: ['main', 'occupied'] };
      return snapshot;
    },
  };
  const dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: '/repo' }, onSelectProject: path => selected.push(path) }), createFetchMock([]), { desktop: { workspace } });
  const oldConfirm = window.confirm;
  window.confirm = () => { assert.fail("Merged branch deletion must not prompt"); };
  try {
    await dom.click(dom.byLabel('Source control')); await dom.flush(); await dom.click(dom.byText('Branches'));
    const row = dom.byLabel('Branch feature');
    await dom.click(row);
    assert.deepEqual(selected, []); assert.deepEqual(calls, []);
    assert.equal(reactProps(dom.byLabel('Delete branch main')).disabled, true);
    assert.equal(reactProps(dom.byLabel('Delete branch occupied')).disabled, true);
    await dom.pointer(row, 'onContextMenu', { clientX: 100, clientY: 100 });
    const menu = () => allElements(document.body).find(el => el.getAttribute('role') === 'menu');
    assert.equal(allElements(menu()).filter(el => el.tagName === 'BUTTON').length, 7);
    await dom.click(allElements(menu()).find(el => el.getAttribute('data-operation') === 'rebase'));
    assert.equal(reactProps(dom.byLabel('Integration operation')).value, 'rebase');
    assert.deepEqual(calls, []);
    await dom.click(dom.byLabel('Actions for branch feature'));
    await dom.click(allElements(menu()).find(el => el.getAttribute('data-operation') === 'worktree-branch'));
    assert.equal(calls.length, 1); assert.deepEqual(selected, []);
    picker = '/new-worktree';
    await dom.click(dom.byLabel('Actions for branch feature'));
    await dom.click(allElements(menu()).find(el => el.getAttribute('data-operation') === 'worktree-branch'));
    assert.deepEqual(calls.at(-1), ['worktree', { projectRoot: '/repo', branch: 'feature', createBranch: false, targetPath: '/new-worktree' }]);
    assert.deepEqual(selected, ['/new-worktree']);
    // Refresh restores the fixture's worktrees, then delete the unused branch.
    await dom.click(dom.byLabel('Refresh source control')); await dom.flush();
    await dom.click(dom.byLabel('Delete branch feature'));
    assert.deepEqual(calls.at(-1), ['branch-delete', 'feature']);
    assert.equal(allElements(dom.container).some(el => el.getAttribute('aria-label') === 'Branch feature'), false);
  } finally { window.confirm = oldConfirm; await dom.unmount(); }
});


test("inactive graph tabs ignore global node shortcuts and relinquish fullscreen", async () => {
  const changes = [];
  const alpha = workflowFixture({ id: "alpha", label: "Alpha node" });
  const beta = workflowFixture({ id: "beta", label: "Beta node" });
  function Harness() {
    const [activeId, setActiveId] = React.useState("alpha");
    return React.createElement(React.Fragment, null,
      React.createElement("button", { onClick: () => setActiveId("beta") }, "Activate beta"),
      [alpha, beta].map(workflow => React.createElement(canvasModule.default, {
        key: workflow.id, active: activeId === workflow.id, workflow,
        logState: { runs: [], loading: false }, approvalState: { approvals: [] }, runState: { running: false },
        onWorkflowChange: next => changes.push(next),
      })),
    );
  }
  const dom = await mountReact(React.createElement(Harness), createFetchMock([]));
  // Select alpha while active, then leave it selected in a retained hidden tab.
  await dom.dispatchWindow("keydown", { key: "a", code: "KeyA", ctrlKey: true });
  await dom.click(dom.allByTitle("Enter full screen")[0]);
  assert.ok(dom.byTitle("Exit full screen"));
  await dom.click(dom.byText("Activate beta"));
  assert.equal(dom.allByTitle("Exit full screen").length, 0, "inactive graph retained a fixed fullscreen overlay");
  await dom.dispatchWindow("keydown", { key: "a", code: "KeyA", ctrlKey: true });
  await dom.dispatchWindow("keydown", { key: "Delete", code: "Delete" });
  assert.deepEqual(changes.map(item => item.id), ["beta"], "hidden selected graph consumed Delete");
  assert.deepEqual(changes[0].nodes, []);
  await dom.unmount();
});

test("inactive graph tab retains its selected edge without deleting it from another editor", async () => {
  const changes = [];
  const workflow = workflowFixture({ id: "edge-tab", label: "First" });
  workflow.nodes.push({ ...workflow.nodes[0], id: "next", label: "Next", x: 300 });
  workflow.edges = [{ id: "route", from: "step", to: "next", condition: "always" }];
  function Harness() {
    const [active, setActive] = React.useState(true);
    return React.createElement(React.Fragment, null,
      React.createElement("button", { onClick: () => setActive(false) }, "Activate file"),
      React.createElement(canvasModule.default, {
        active, workflow, logState: { runs: [], loading: false }, approvalState: { approvals: [] }, runState: { running: false },
        onWorkflowChange: next => changes.push(next),
      }),
    );
  }
  const dom = await mountReact(React.createElement(Harness), createFetchMock([]));
  await dom.click(dom.byTitle("Map"));
  const edge = allElements(dom.byLabel("Graph outline")).find(element => element.tagName === "BUTTON" && element.getAttribute("aria-label")?.startsWith("First to Next"));
  assert.ok(edge);
  await dom.focus(edge);
  assert.equal(edge.getAttribute("aria-current"), "true");
  await dom.click(dom.byText("Activate file"));
  await dom.dispatchWindow("keydown", { key: "Delete", code: "Delete" });
  assert.deepEqual(changes, [], "hidden graph deleted its previously selected edge");
  await dom.unmount();
});

function workflowDocumentWriteHarness(request) {
  const source = fs.readFileSync(path.join(frontendRoot, 'src/pages/App.jsx'), 'utf8');
  const block = source.slice(source.indexOf('  function queueWorkflowDocumentWrite('), source.indexOf('  function updateRattishGraphMetadata('));
  const sessions = new Map();
  const accepted = [];
  const analyses = [];
  function documentSession(id) {
    if (!sessions.has(id)) {
      const state = { current: null };
      sessions.set(id, { documentWritesRef: { current: Promise.resolve() }, rattishEditorStateRef: state,
        rattishAnalysisTimerRef: { current: null }, rattishAnalysisRequestRef: { current: 0 }, rattishMetadataPendingRef: { current: false }, rattishMetadataSavingRef: { current: null },
        setRattishEditorState(next) { state.current = next; } });
    }
    return sessions.get(id);
  }
  const functions = vm.runInNewContext(`(function () { ${block}; return { saveWorkflowDocument, mutateActiveRattish }; })()`, {
    documentSession, fetch: request, apiUrl: value => value, window: { clearTimeout },
    scheduleRattishAnalysis: (...args) => analyses.push(args),
    rattishEditorRef: { current: { acceptDocument: (...args) => accepted.push(args) } },
    setTopBarNotice() {}, loadWorkflows: async () => {}, saveRattishMetadataNow: async () => true,
  });
  return { ...functions, documentSession, accepted, analyses };
}

function deferredDocumentResponse() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve: document => resolve({ ok: true, json: async () => ({ document }) }) };
}

const writeTestWorkflow = id => ({ id, sourceFormat: 'rattish', sourcePath: `/projects/${id}/workflow.rattish` });
const writeTestDocument = (source, savedRevision = 'r1', dirty = false) => ({ source, savedRevision, dirty, runnable: true });

test('workflow source typed during save retains its draft and advances the saved revision', async () => {
  const pending = deferredDocumentResponse();
  const harness = workflowDocumentWriteHarness(() => pending.promise);
  const target = writeTestWorkflow('alpha');
  const session = harness.documentSession(target.id);
  session.setRattishEditorState({ document: writeTestDocument('submitted', 'r1', true) });
  const saving = harness.saveWorkflowDocument(target);
  await Promise.resolve(); await Promise.resolve();
  session.setRattishEditorState({ document: writeTestDocument('newer draft', 'r1', true) });
  pending.resolve(writeTestDocument('submitted', 'r2'));
  await assert.rejects(saving, /changed during save/);
  assert.equal(session.rattishEditorStateRef.current.document.source, 'newer draft');
  assert.equal(session.rattishEditorStateRef.current.document.savedRevision, 'r2');
  assert.equal(session.rattishEditorStateRef.current.document.savedSource, 'submitted');
  assert.equal(session.rattishEditorStateRef.current.document.dirty, true);
  assert.equal(harness.accepted.length, 0, 'new source buffer was reset');
  assert.deepEqual(harness.analyses, [['newer draft', target.sourcePath]]);
});

test('workflow graph mutation cannot overwrite source typed while its request is pending', async () => {
  const pending = deferredDocumentResponse();
  let started;
  const requested = new Promise(resolve => { started = resolve; });
  const harness = workflowDocumentWriteHarness(() => { started(); return pending.promise; });
  const target = writeTestWorkflow('alpha');
  const session = harness.documentSession(target.id);
  session.setRattishEditorState({ document: writeTestDocument('disk') });
  const mutation = harness.mutateActiveRattish([{ kind: 'edit-node' }], target);
  await requested;
  session.setRattishEditorState({ document: writeTestDocument('newer source', 'r1', true) });
  pending.resolve(writeTestDocument('graph edit', 'r2'));
  assert.equal(await mutation, null);
  assert.equal(session.rattishEditorStateRef.current.document.source, 'newer source');
  assert.equal(session.rattishEditorStateRef.current.document.savedRevision, 'r2');
  assert.equal(session.rattishEditorStateRef.current.document.dirty, true);
  assert.equal(harness.accepted.length, 0);
});

test('workflow document writes serialize per workflow and run independently across workflows', async () => {
  const calls = [];
  const pending = [];
  const harness = workflowDocumentWriteHarness((url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    const request = deferredDocumentResponse(); pending.push(request); return request.promise;
  });
  const alpha = writeTestWorkflow('alpha'); const beta = writeTestWorkflow('beta');
  harness.documentSession(alpha.id).setRattishEditorState({ document: writeTestDocument('alpha') });
  harness.documentSession(beta.id).setRattishEditorState({ document: writeTestDocument('beta') });
  const first = harness.mutateActiveRattish([{ kind: 'first' }], alpha);
  const second = harness.mutateActiveRattish([{ kind: 'second' }], alpha);
  const other = harness.mutateActiveRattish([{ kind: 'other' }], beta);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls.map(item => item.url), ['/workflows/alpha/document/mutate', '/workflows/beta/document/mutate']);
  pending[0].resolve(writeTestDocument('alpha first', 'r2'));
  pending[1].resolve(writeTestDocument('beta other', 'b2'));
  await first; await other; await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls[2].url, '/workflows/alpha/document/mutate');
  assert.equal(calls[2].body.expectedRevision, 'r2');
  pending[2].resolve(writeTestDocument('alpha second', 'r3')); await second;
  assert.equal(harness.documentSession('alpha').rattishEditorStateRef.current.document.savedRevision, 'r3');
  assert.equal(harness.documentSession('beta').rattishEditorStateRef.current.document.savedRevision, 'b2');
});

test('closing mixed workflow tabs prompts every last dirty view and aborts on cancel', async () => {
  const source = fs.readFileSync(path.join(frontendRoot, 'src/pages/App.jsx'), 'utf8');
  const block = source.slice(source.indexOf('  async function beforeCloseWorkflowViews('), source.indexOf('  function renderWorkflowGraph('));
  const workflows = [writeTestWorkflow('alpha'), writeTestWorkflow('beta')];
  const prompts = [];
  const beforeClose = vm.runInNewContext(`(function () { ${block}; return beforeCloseWorkflowViews; })()`, {
    workflows, codeOpenPaths: ['graph:alpha', 'graph:beta', workflows[0].sourcePath],
    workflowTabs: { 'graph:alpha': { workflowId: 'alpha' }, 'graph:beta': { workflowId: 'beta' } },
    documentSession: () => ({ rattishEditorStateRef: { current: { document: { dirty: true } } }, rattishMetadataPendingRef: { current: false }, rattishMetadataSavingRef: { current: null } }),
    setWorkflowClosePrompt: prompt => prompts.push(prompt), saveRattishMetadataNow: async () => true,
  });
  const closing = beforeClose(['graph:alpha', 'graph:beta', workflows[0].sourcePath]);
  assert.equal(prompts.length, 1); assert.equal(prompts[0].workflow.id, 'alpha');
  prompts[0].resolve(true); await Promise.resolve(); await Promise.resolve();
  assert.equal(prompts.length, 2); assert.equal(prompts[1].workflow.id, 'beta');
  prompts[1].resolve(false); assert.equal(await closing, false);
  prompts.length = 0;
  assert.equal(await beforeClose(['graph:alpha']), true, 'closing one view should retain the dirty shared source');
  assert.equal(prompts.length, 0);
});

test("legacy editor defaults migrate to sidebar activities", () => {
  assert.equal(settingsModule.normalizeAppSettings({ general: { defaultView: "code" } }).general.initialActivity, "files");
  assert.equal(settingsModule.normalizeAppSettings({ general: { defaultView: "graph" } }).general.initialActivity, "workflows");
  assert.equal(settingsModule.normalizeAppSettings({ general: { defaultView: "graph", initialActivity: "search" } }).general.initialActivity, "search");
  assert.equal(settingsModule.normalizeAppSettings({ general: { initialActivity: "invalid" } }).general.initialActivity, "workflows");
});

test("persistent project picker names its browsing project and loading target", () => {
  const html = renderToStaticMarkup(React.createElement(appModule.RecentProjectSelector, { projectRoot: "/projects/Atlas", recentProjectRoots: ["/projects/Beacon"] }));
  assert.match(html, /aria-label="Recent projects"/);
  assert.match(html, /Atlas/);
  assert.doesNotMatch(html, /Studio view/);
  const loading = renderToStaticMarkup(React.createElement(appModule.RecentProjectSelector, { projectRoot: "/projects/Atlas", openingProjectRoot: "/projects/Beacon" }));
  assert.match(loading, /Opening Beacon/);
  assert.match(loading, /aria-busy="true"/);
});

test("compact pane toggles preserve desktop preferences and allow only one open pane", async () => {
  let panes;
  function PaneHarness() {
    panes = appModule.useResponsivePanes();
    return React.createElement("div", null, `${panes.projectPaneVisible}/${panes.assistantPaneVisible}`);
  }
  const dom = await mountReact(React.createElement(PaneHarness), createFetchMock([]));
  try {
    window.innerWidth = 1440;
    await dom.dispatchWindow("resize");
    assert.equal(panes.projectPaneVisible, true);
    assert.equal(panes.assistantPaneVisible, true);
    await React.act(async () => panes.setAssistantPaneVisible(false));
    window.innerWidth = 960;
    await dom.dispatchWindow("resize");
    assert.equal(panes.projectPaneVisible, false);
    assert.equal(panes.assistantPaneVisible, false);
    await React.act(async () => panes.setProjectPaneVisible(true));
    assert.equal(panes.projectPaneVisible, true);
    await React.act(async () => panes.setAssistantPaneVisible(true));
    assert.equal(panes.projectPaneVisible, false);
    assert.equal(panes.assistantPaneVisible, true);
    await React.act(async () => panes.closeCompactPane());
    assert.equal(panes.assistantPaneVisible, false);
    window.innerWidth = 1440;
    await dom.dispatchWindow("resize");
    assert.equal(panes.projectPaneVisible, true);
    assert.equal(panes.assistantPaneVisible, false);
  } finally { await dom.unmount(); }
});

test('analysis keeps the saved source revision, current layout and last valid graph while reporting syntax errors', () => {
  const graph = { nodes: [{ id: 'kept' }], edges: [] };
  const metadata = { canvas: { nodes: { kept: { x: 420, y: 80 } } } };
  const current = { saving: true, document: { source: 'invalid draft', dirty: true, runnable: true, graph, metadata, metadataRevision: 'layout-new', savedRevision: 'disk-new', savedSource: 'saved source' } };
  const analyzed = { source: 'invalid draft', runnable: false, graph: null, diagnostics: [{ message: 'Syntax error' }], savedRevision: 'old', metadataRevision: 'old', metadata: {} };
  const merged = appModule.mergeRattishAnalysisState(current, analyzed, 'invalid draft');
  assert.equal(merged.document.runnable, false);
  assert.deepEqual(merged.document.diagnostics, analyzed.diagnostics);
  assert.equal(merged.document.graph, graph);
  assert.equal(merged.document.metadata, metadata);
  assert.equal(merged.document.savedRevision, 'disk-new');
  assert.equal(merged.document.savedSource, 'saved source');
  assert.equal(merged.document.metadataRevision, 'layout-new');
  assert.equal(merged.saving, true);
  const validGraph = { nodes: [{ id: 'fixed' }], edges: [] };
  const fixed = appModule.mergeRattishAnalysisState(merged, { ...analyzed, compilation: { state: "valid" }, runnable: true, graph: validGraph, diagnostics: [] }, 'invalid draft');
  assert.equal(fixed.document.graph, validGraph);
  assert.equal(fixed.document.lastValidGraph, validGraph);
});

test('successive node drags retain newest positions and serialize layout revision writes', async () => {
  const source = fs.readFileSync(path.join(frontendRoot, 'src/pages/App.jsx'), 'utf8');
  const block = source.slice(source.indexOf('  async function saveRattishMetadataNow('), source.indexOf('  const loadWorkflows = ', source.indexOf('  async function saveRattishMetadataNow(')));
  const firstLayout = { canvas: { nodes: { node: { x: 10, y: 20 } } } };
  const lastLayout = { canvas: { nodes: { node: { x: 900, y: 400 } } } };
  const state = { current: { document: { source: 'unchanged', metadata: firstLayout, metadataRevision: 'm1' } } };
  const session = { rattishEditorStateRef: state, rattishMetadataSaveTimerRef: { current: null }, rattishMetadataPendingRef: { current: true }, rattishMetadataSavingRef: { current: null },
    setRattishEditorState(update) { state.current = typeof update === 'function' ? update(state.current) : update; } };
  const pending = []; const requests = [];
  const save = vm.runInNewContext(`(function () { ${block}; return saveRattishMetadataNow; })()`, {
    documentSession: () => session, window: { clearTimeout }, apiUrl: value => value, setTopBarNotice() {},
    fetch: (url, init) => { requests.push({ url, body: JSON.parse(init.body) }); return new Promise(resolve => pending.push(resolve)); },
  });
  const first = save('alpha');
  state.current = { document: { ...state.current.document, metadata: lastLayout } };
  session.rattishMetadataPendingRef.current = true;
  const overlapping = save('alpha');
  assert.equal(requests.length, 1, 'overlapping layout writes used the same revision');
  pending[0]({ ok: true, json: async () => ({ metadata: firstLayout, metadataRevision: 'm2' }) });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.current.document.metadata, lastLayout, 'old response snapped the node back');
  assert.equal(requests.length, 2);
  assert.equal(requests[1].body.expectedRevision, 'm2');
  assert.deepEqual(requests[1].body.metadata, lastLayout);
  pending[1]({ ok: true, json: async () => ({ metadata: lastLayout, metadataRevision: 'm3' }) });
  assert.equal(await first, true); assert.equal(await overlapping, true);
  assert.equal(state.current.document.metadataRevision, 'm3');
  assert.equal(session.rattishMetadataPendingRef.current, false);
});

test('splitting a workflow creates independent graph views with shared edits and protects only the last dirty view', async () => {
  const primary = 'workflow-graph:alpha';
  const duplicate = 'workflow-graph:alpha:view:second';
  const workflow = { id: 'alpha', name: 'Review', sourcePath: '/alpha/workflow.rattish' };
  const mounted = []; const prompts = [];
  const source = fs.readFileSync(path.join(frontendRoot, 'src/pages/App.jsx'), 'utf8');
  const closeBlock = source.slice(source.indexOf('  async function beforeCloseWorkflowViews('), source.indexOf('  function renderWorkflowGraph('));
  function GraphView({ viewPath, source, onEdit }) {
    const [zoom, setZoom] = React.useState(100);
    React.useEffect(() => { mounted.push(viewPath); }, []);
    return React.createElement('section', { 'aria-label': `${viewPath} graph view` },
      React.createElement('span', null, `Source: ${source}`),
      React.createElement('span', { 'aria-label': `${viewPath} zoom` }, String(zoom)),
      React.createElement('button', { 'aria-label': `Zoom ${viewPath}`, onClick: () => setZoom(value => value + 10) }, 'Zoom'),
      React.createElement('button', { 'aria-label': `Edit ${viewPath}`, onClick: () => onEdit('Updated') }, 'Edit'),
    );
  }
  function Harness() {
    const [openPaths, setOpenPaths] = React.useState([primary]);
    const [activePath, setActivePath] = React.useState(primary);
    const [source, setSource] = React.useState('Original');
    const [tabs, setTabs] = React.useState({ [primary]: { workflowId: workflow.id, name: workflow.name, sourcePath: workflow.sourcePath, contextLabel: '/alpha' } });
    const guard = vm.runInNewContext(`(function () { ${closeBlock}; return beforeCloseWorkflowViews; })()`, {
      workflows: [workflow], codeOpenPaths: openPaths, workflowTabs: tabs,
      documentSession: () => ({ rattishEditorStateRef: { current: { document: { dirty: source !== 'Original' } } }, rattishMetadataPendingRef: { current: false }, rattishMetadataSavingRef: { current: null } }),
      setWorkflowClosePrompt: prompt => { prompts.push(prompt); prompt.resolve(false); }, saveRattishMetadataNow: async () => true,
    });
    return React.createElement(codeWorkspaceModule.default, {
      active: true, activePath, openPaths, workflowTabs: tabs,
      onActivePathChange: setActivePath, onBeforeCloseWorkflowTabs: guard,
      onDuplicateWorkflowTab: path => { setTabs(current => ({ ...current, [duplicate]: { ...current[path] } })); setOpenPaths(current => [...current, duplicate]); return duplicate; },
      onClosePaths: paths => { setOpenPaths(current => current.filter(path => !paths.includes(path))); if (paths.includes(activePath)) setActivePath(openPaths.find(path => !paths.includes(path)) || ''); },
      renderWorkflowTab: (_tab, { path }) => React.createElement(GraphView, { viewPath: path, source, onEdit: setSource }),
    });
  }
  const dom = await mountReact(React.createElement(Harness), createFetchMock([]));
  window.innerWidth = 1200; window.innerHeight = 800;
  const tabButton = allElements(dom.byLabel('Editor tabs')).find(element => element.getAttribute?.('role') === 'tab');
  await dom.pointer(tabButton.parentNode, 'onContextMenu', { clientX: 50, clientY: 50 });
  await dom.click(dom.byText('Split right')); await dom.flush();
  assert.ok(dom.byLabel('Split editor tabs'));
  assert.deepEqual(mounted, [primary, duplicate], 'split moved or remounted the original graph');
  await dom.click(dom.byLabel(`Zoom ${duplicate}`));
  assert.equal(dom.byLabel(`${primary} zoom`).textContent, '100');
  assert.equal(dom.byLabel(`${duplicate} zoom`).textContent, '110');
  await dom.click(dom.byLabel(`Edit ${duplicate}`));
  assert.equal(allElements(dom.container).filter(element => element.tagName === 'SPAN' && element.textContent === 'Source: Updated').length, 2, 'graph views did not share source edits');
  await dom.click(allElements(dom.container).filter(element => element.getAttribute?.('aria-label') === 'Close Review')[1]); await dom.flush();
  assert.equal(prompts.length, 0, 'closing one graph view prompted for its retained shared document');
  assert.ok(dom.byLabel(`${primary} graph view`));
  await dom.click(dom.byLabel('Close Review')); await dom.flush();
  assert.equal(prompts.length, 1, 'closing the last dirty graph view did not prompt');
  assert.ok(dom.byLabel(`${primary} graph view`), 'cancel closed the dirty last view');
  await dom.unmount();
});


test("graph updates preserve source editor undo boundaries", () => {
  const calls = [];
  let content = "before";
  const model = {
    getValue: () => content,
    getFullModelRange: () => ({ startLineNumber: 1 }),
    pushStackElement: () => calls.push("boundary"),
    pushEditOperations: (_selections, edits) => { calls.push(edits[0].text); content = edits[0].text; },
    setValue: () => assert.fail("A graph edit must not erase source undo history"),
  };
  codeWorkspaceModule.replaceEditorModelContent(model, "after");
  codeWorkspaceModule.replaceEditorModelContent(model, "after");
  assert.deepEqual(calls, ["boundary", "after", "boundary"]);
});

test("restored run tabs show the executed graph and return explicitly to the current document", async () => {
  const current = workflowFixture({ id: "snapshot-demo", name: "Snapshot demo", label: "Current step" });
  const historical = { ...current, nodes: current.nodes.map(node => ({ ...node, label: "Executed step" })), runId: "old-run" };
  const graphPath = "workflow-graph:snapshot-demo";
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([current])),
    (url) => String(url).includes("/logs/old-run?") ? { ok: true, json: async () => ({ log: { graphSnapshot: historical, logText: "Saved execution", runNodes: {} } }) } : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock, { storage: {
    'raticode.editorSession.v2': JSON.stringify({ version: 2, paths: [graphPath], activePath: graphPath, workflowTabs: { [graphPath]: { workflowId: current.id, name: current.name, projectRoot: current.projectRoot } }, pinnedRun: { workflowId: current.id, runId: "old-run" } }),
  } });
  try {
    await dom.flush();
    assert.ok(dom.byText("Executed step"));
    assert.ok(dom.byText("Run snapshot"));
    assert.equal(allElements(dom.container).some(node => node.textContent === "Current step"), false);
    await dom.click(dom.byText("Current graph"));
    await dom.flush();
    assert.ok(dom.byText("Current step"));
    assert.equal(fetchMock.calls.some(call => String(call.url).endsWith('/run')), false);
  } finally { await dom.unmount(); }
});

test("Rattish background submissions bind the reviewed source revision and preserve synchronous defaults", () => {
  const request = appModule.workflowRunRequest("demo", { background: true, expectedRevision: "reviewed-revision", parameters: { name: "Ada" } });
  assert.deepEqual(JSON.parse(request.options.body), { dryRun: false, triggerContext: {}, background: true, expectedRevision: "reviewed-revision", inputs: { name: "Ada" } });
  assert.equal(JSON.parse(appModule.workflowRunRequest("demo").options.body).background, undefined);
});

function rattishValidationHarness(initialDocument, request) {
  const source = fs.readFileSync(path.join(frontendRoot, 'src/pages/App.jsx'), 'utf8');
  const block = source.slice(source.indexOf('  async function validateWorkflow('), source.indexOf('  async function loadWorkflowHistory('));
  const state = { current: { document: initialDocument, saving: false } };
  const notices = [];
  const session = { rattishEditorStateRef: state, rattishAnalysisRequestRef: { current: 0 }, rattishAnalysisTimerRef: { current: null }, setRattishEditorState: next => { state.current = next; } };
  const validate = vm.runInNewContext(`(function () { ${block}; return validateWorkflow; })()`, {
    documentSession: () => session, window: { clearTimeout }, apiUrl: value => value, fetch: request,
    loadWorkflowDraft: () => null, mergeRattishAnalysisState: appModule.mergeRattishAnalysisState,
    setTopBarNotice: notice => notices.push(notice), persistWorkflow() { throw new Error('Validation must not persist Rattish'); }, summarizeWorkflow() { throw new Error('Rattish source must not be converted'); },
  });
  return { validate, state, notices };
}

test('validating dirty invalid Rattish analyzes its exact source without writing or replacing the saved revision', async () => {
  const draft = { source: 'Rattish: 1\nWorkflow: broken draft', dirty: true, savedRevision: 'disk-9', graph: { nodes: [{ id: 'prior' }] }, runnable: true };
  const calls = [];
  const harness = rattishValidationHarness(draft, async (url, init) => {
    calls.push({ url, init });
    return { ok: true, json: async () => ({ document: { source: draft.source, savedRevision: 'analyzed-old', runnable: false, graph: null, diagnostics: [{ severity: 'error', message: 'Workflow.name is required' }] } }) };
  });
  await harness.validate(writeTestWorkflow('alpha'));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/workflows/alpha/document/analyze');
  assert.deepEqual(JSON.parse(calls[0].init.body), { source: draft.source });
  assert.equal(harness.state.current.document.dirty, true);
  assert.equal(harness.state.current.document.savedRevision, 'disk-9');
  assert.equal(harness.state.current.document.graph, draft.graph);
  assert.match(harness.notices.at(-1).message, /Workflow.name is required/);
  assert.equal(harness.notices.at(-1).type, 'error');
});

test('manual Rattish validation ignores results after newer source edits', async () => {
  const pending = deferredDocumentResponse();
  const harness = rattishValidationHarness(writeTestDocument('old draft', 'r1', true), () => pending.promise);
  const validating = harness.validate(writeTestWorkflow('alpha'));
  harness.state.current = { document: writeTestDocument('new draft', 'r1', true) };
  pending.resolve({ source: 'old draft', runnable: true, diagnostics: [] });
  await validating;
  assert.equal(harness.state.current.document.source, 'new draft');
  assert.equal(harness.notices.length, 0, 'stale validation announced success for newer unvalidated edits');
});

test('Rattish validation loads an unopened document and reports preflight failures without saving', async () => {
  const calls = [];
  const document = writeTestDocument('loaded source');
  const harness = rattishValidationHarness(null, async (url, init) => {
    calls.push({ url, init });
    return { ok: true, json: async () => ({ document: url.endsWith('/analyze') ? { ...document, preflight: { diagnostics: [{ severity: 'error', message: 'Provider is unavailable' }] } } : document }) };
  });
  await harness.validate(writeTestWorkflow('beta'));
  assert.deepEqual(calls.map(item => item.url), ['/workflows/beta/document', '/workflows/beta/document/analyze']);
  assert.equal(calls[0].init, undefined);
  assert.equal(harness.notices.at(-1).type, 'error');
  assert.match(harness.notices.at(-1).message, /Provider is unavailable/);
});


test("Run shortcut ignores an unrelated file even when a workflow graph remains open", async () => {
  const workflow = workflowFixture({ id: "shortcut-context", name: "Workflow context" });
  const graphPath = "workflow-graph:shortcut-context";
  const subscribers = new Set();
  const fetchMock = createFetchMock([jsonResponse("/api/workflows", workflowsPayload([workflow]))]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock, {
    browser: { onCommand: callback => { subscribers.add(callback); return () => subscribers.delete(callback); } },
    storage: { 'raticode.editorSession.v2': JSON.stringify({ version: 2, paths: [graphPath, "/tmp/unrelated.png"], activePath: "/tmp/unrelated.png", workflowTabs: { [graphPath]: { workflowId: workflow.id, name: workflow.name, projectRoot: workflow.projectRoot } } }) },
  });
  try {
    await dom.flush();
    await React.act(async () => { for (const callback of subscribers) callback({ action: "application-shortcut", commandId: "workflow.run" }); });
    await dom.flush();
    assert.equal(fetchMock.calls.some(call => /\/(run|run-preview)$/.test(String(call.url))), false);
  } finally { await dom.unmount(); }
});

test("All tabs filters combine exact project identity with active and unread run status", async () => {
  const selected = [];
  const entries = [
    { path: "graph:a", label: "Review", projectRoot: "/first/project", tab: { status: "running", statusLabel: "Running", unreadFailure: true } },
    { path: "graph:b", label: "Review", projectRoot: "/second/project", tab: { status: "failed", statusLabel: "Failed", unread: true } },
    { path: "/first/project/config.txt", label: "config.txt", projectRoot: "/first/project" },
    { path: "browser:docs", label: "Docs", browser: { url: "https://example.test" } },
  ];
  const dom = await mountReact(React.createElement(codeWorkspaceModule.EditorTabsMenu, {
    activePath: "graph:a", entries, onActivate: path => selected.push(path),
  }), createFetchMock([]));
  const results = () => allElements(dom.byLabel("Open tabs")).filter(node => node.getAttribute?.("data-open-tab") !== null && node.tagName === "BUTTON");
  try {
    const summary = dom.byLabel("All editor tabs");
    await dom.keyDown(summary, "ArrowDown");
    assert.equal(summary.parentNode.open, true);
    assert.equal(results().length, 4);
    await dom.change(dom.byLabel("Filter tabs by project"), "/first/project");
    assert.equal(results().length, 2);
    await dom.change(dom.byLabel("Filter tabs by run status"), "active");
    assert.equal(results().length, 1);
    assert.match(textOf(results()[0]), /Running/);
    await dom.change(dom.byLabel("Filter tabs by project"), "/second/project");
    assert.equal(results().length, 0);
    assert.match(dom.text(), /No tabs match these filters/);
    await dom.change(dom.byLabel("Filter tabs by run status"), "unread");
    assert.equal(results().length, 1);
    await dom.click(results()[0]);
    assert.deepEqual(selected, ["graph:b"]);
    assert.equal(summary.parentNode.open, false);
    assert.ok(document.activeElement === summary, "Focus returns to the All tabs summary");
    await dom.keyDown(summary, "ArrowDown");
    await dom.keyDown(summary.parentNode, "Escape");
    assert.equal(summary.parentNode.open, false);
  } finally { await dom.unmount(); }
});


test('preflight failure keeps a valid graph editable and uses newly analyzed nodes', () => {
  const graph = { nodes: [{ id: 'updated' }], edges: [] };
  const analyzed = { source: 'draft', compilation: { state: 'valid' }, runnable: false, graph,
    preflight: { ready: false, diagnostics: [{ severity: 'error', message: 'Approval store unavailable' }] } };
  assert.equal(appModule.rattishGraphIsValid(analyzed), true);
  assert.equal(appModule.rattishGraphIsValid({ ...analyzed, compilation: { state: 'invalid' } }), false);
  assert.equal(appModule.rattishGraphIsValid(null), false);
  const current = { document: { source: 'draft', graph: { nodes: [{ id: 'stale' }] } } };
  const merged = appModule.mergeRattishAnalysisState(current, analyzed, 'draft');
  assert.equal(merged.document.graph, graph);
  assert.equal(merged.document.runnable, false);
  assert.deepEqual(merged.document.preflight, analyzed.preflight);
});


test("Rem falls back on Git diff buffer overflow and preserves other errors", async () => {
  const { gitRepositoryAction } = require("../../electron/git-status.cjs");
  const error = Object.assign(new Error("too large"), { code: "ERR_CHILD_PROCESS_STDIO_MAXBUFFER" });
  const runGit = async args => {
    if (args.includes("rev-parse")) return "/repo\n";
    if (args.includes("--diff-filter=U")) return "";
    if (args.includes("write-tree")) return "staged-tree\n";
    throw error;
  };
  assert.deepEqual(await gitRepositoryAction("/repo", "staged-diff", "", { runGit }), { tree: "staged-tree", inspectStaged: true });
  error.code = "EACCES";
  await assert.rejects(gitRepositoryAction("/repo", "staged-diff", "", { runGit }), /too large/);
});

test("Rem swarm access persists and is searchable in settings", () => {
  assert.equal(settingsModule.normalizeAppSettings({}).assistant.swarmAccessEnabled, true);
  const values = new Map();
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  const disabled = settingsModule.updateSetting(settingsModule.DEFAULT_APP_SETTINGS, "assistant.swarmAccessEnabled", false);
  settingsModule.saveAppSettings(disabled, storage);
  assert.equal(settingsModule.loadAppSettings(storage).assistant.swarmAccessEnabled, false);
  assert.deepEqual(settingsPopoverModule.settingsCategoriesForQuery("swarm access"), ["assistant"]);
});

for (const enabled of [true, false]) {
  test(`Rem chat sends the swarm access toggle (${enabled}) and the thread's project grant`, async () => {
    const trusted = [];
    const desktop = { workspace: {
      trustProjectRoot: async root => { trusted.push(root); },
      pathGrantForApi: root => `grant:${root}`,
    } };
    const chatStream = streamResponse(['{"type":"final","message":{"body":"Ready"}}\n']);
    const fetchMock = createFetchMock([
      jsonResponse("/api/provider/capabilities", { providers: [] }),
      url => url === "/api/chat/stream" ? chatStream(url) : null,
    ]);
    const workflow = { ...workflowFixture({ id: "team-project" }), projectRoot: "/projects/team", projectName: "team" };
    const dom = await mountReact(React.createElement(appModule.ChatPane, {
      workflows: [workflow], workflow, width: 380,
      assistantDefaults: { swarmAccessEnabled: enabled },
      swarmProjectPaths: ["/projects/mobile", "/projects/mobile"],
    }), fetchMock, { desktop });
    try {
      await dom.flush();
      await dom.change(dom.first("textarea"), "List my swarms");
      await dom.click(dom.byTitle("Send message"));
      await dom.flush();
      const call = fetchMock.calls.find(call => call.url === "/api/chat/stream");
      assert.ok(call);
      const request = JSON.parse(call.options.body);
      assert.equal(request.workflow.projectRoot, "/projects/team");
      assert.deepEqual(request.workflow.remSwarmAccess, {
        enabled, grantId: "grant:/projects/team",
        ...(enabled ? { workspaceGrants: { "/projects/mobile": "grant:/projects/mobile" } } : {}),
      });
      assert.equal(trusted.includes("/projects/team"), true);
      assert.deepEqual(request.workflow.remThreads.projects, [{ root: "/projects/team", name: "team", grantId: "grant:/projects/team" }]);
    } finally { await dom.unmount(); }
  });
}

for (const scenario of ["confirm", "cancel", "unrelated-error", "force-error"]) {
  test(`branch deletion handles ${scenario} after the safe attempt`, async () => {
    const calls = [], prompts = [];
    let snapshot = { active: true, root: '/repo', branch: 'main', branches: ['main', 'feature'], entries: [] };
    const workspace = {
      trustProjectRoot: async () => {}, listDirectory: async () => ({ entries: [] }),
      gitStatus: async () => snapshot, gitHistory: async () => ({ commits: [] }),
      gitWorktrees: async () => ({ active: true, worktrees: [{ path: '/repo', branch: 'main' }] }),
      gitRepoAction: async (_root, action, value) => {
        if (action === 'stash-list') return { stashes: [] };
        calls.push([action, value]);
        if (action === 'branch-delete') return scenario === 'unrelated-error'
          ? { ...snapshot, error: 'Permission denied' } : { branchDeleteUnmerged: true };
        return scenario === 'force-error' ? { ...snapshot, error: 'Branch is checked out' }
          : (snapshot = { ...snapshot, branches: ['main'] });
      },
    };
    const dom = await mountReact(React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: '/repo' } }), createFetchMock([]), { desktop: { workspace } });
    const oldConfirm = window.confirm;
    window.confirm = message => {
      assert.deepEqual(calls, [['branch-delete', 'feature']]);
      prompts.push(message);
      return scenario !== 'cancel';
    };
    try {
      await dom.click(dom.byLabel('Source control')); await dom.flush(); await dom.click(dom.byText('Branches'));
      await dom.click(dom.byLabel('Delete branch feature'));
      assert.equal(prompts.length, scenario === 'unrelated-error' ? 0 : 1);
      if (prompts.length) assert.match(prompts[0], /feature.*unmerged/);
      assert.deepEqual(calls, scenario === 'confirm' || scenario === 'force-error'
        ? [['branch-delete', 'feature'], ['branch-delete-force', 'feature']]
        : [['branch-delete', 'feature']]);
      assert.equal(allElements(dom.container).some(el => el.getAttribute('aria-label') === 'Branch feature'), scenario !== 'confirm');
      if (scenario === 'unrelated-error') assert.ok(dom.byText('Permission denied'));
      if (scenario === 'force-error') assert.ok(dom.byText('Branch is checked out'));
      if (scenario === 'cancel') assert.equal(allElements(dom.container).some(el => el.textContent === 'Technical details'), false);
    } finally { window.confirm = oldConfirm; await dom.unmount(); }
  });
}

test("Rem archives are collapsed without metadata reads and load ten at a time", async () => {
  const old = Array.from({ length: 23 }, (_, i) => ({ id: `archived-${i}`, title: `Archived conversation ${i}`, updatedAt: new Date(Date.now() - (11 + i) * 86400000).toISOString(), projectRoot: "/repo" }));
  const live = { id: "live", title: "Current conversation", updatedAt: new Date().toISOString(), projectRoot: "/repo" };
  const gone = { id: "gone", title: "Deleted workspace conversation", updatedAt: new Date().toISOString(), projectRoot: "/gone" };
  const all = [live, gone, ...old];
  const storage = Object.fromEntries(all.map(thread => [`gofer-flow-chat-thread-meta:${thread.id}`, JSON.stringify(thread)]));
  storage["gofer-flow-chat-threads"] = JSON.stringify(all.map(({ id, updatedAt, projectRoot }) => ({ id, updatedAt, projectRoot, scopeIndexed: true })));
  const reads = [];
  let wrapped = false;
  function Harness() {
    if (!wrapped) {
      wrapped = true;
      const getItem = window.localStorage.getItem.bind(window.localStorage);
      window.localStorage.getItem = key => { reads.push(key); return getItem(key); };
    }
    return React.createElement(appModule.ThreadSections, { threads: [], onOpen() {}, onDelete() {} });
  }
  const dom = await mountReact(React.createElement(Harness), createFetchMock([]), {
    storage, desktop: { workspace: { missingThreadRoots: async () => ["/gone"] } },
  });
  await dom.flush();
  assert.match(dom.text(), /Current conversation/);
  assert.doesNotMatch(dom.text(), /Archived conversation|Deleted workspace conversation/);
  assert.equal(dom.byText("Archived threads").getAttribute("aria-expanded"), "false");
  assert.equal(reads.some(key => key.includes("meta:archived-")), false);
  await dom.click(dom.byText("Archived threads"));
  assert.match(dom.text(), /Deleted workspace conversation/);
  assert.equal((dom.text().match(/Archived conversation/g) || []).length, 9);
  await dom.click(dom.byText("Show older threads"));
  assert.equal((dom.text().match(/Archived conversation/g) || []).length, 19);
  await dom.click(dom.byText("Show older threads"));
  assert.equal((dom.text().match(/Archived conversation/g) || []).length, 23);
  await dom.click(dom.byText("Archived threads"));
  reads.length = 0;
  await dom.flush();
  assert.equal(reads.some(key => key.includes("meta:archived-")), false);
  await dom.click(dom.byText("Archived threads"));
  assert.equal((dom.text().match(/Archived conversation/g) || []).length, 9);
  await dom.unmount();
});

test("Rem retains a thread's branch and archives it after that branch is deleted", async () => {
  let branch = "feature";
  let branches = ["main", "feature"];
  const dom = await mountReact(React.createElement(appModule.ChatPane, { activeProjectRoot: "/repo", width: 380 }), createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
  ]), { desktop: { workspace: {
    gitStatus: async () => ({ active: true, branch, branches }),
    gitBranches: async () => ({ active: true, branch, branches }),
    missingThreadRoots: async () => [],
  } } });
  await dom.click(dom.byLabel("New thread"));
  await dom.flush();
  assert.equal(appModule.loadChatThreads()[0].projectBranch, "feature");
  branch = "main";
  await dom.click(dom.byTitle("Back to active threads"));
  assert.match(dom.text(), /New thread/);
  branches = ["main"];
  await dom.flush(60000);
  assert.equal(dom.allByTitle("Delete thread").length, 0);
  await dom.click(dom.byText("Archived threads"));
  assert.equal(dom.allByTitle("Delete thread").length, 1);
  assert.equal(appModule.loadChatThreads()[0].projectBranch, "feature");
  await dom.unmount();
});

test("Rem archives, pins and confirms deletion with persisted organization", async () => {
  const thread = { id: "organize", title: "Organize this conversation", updatedAt: new Date().toISOString(), projectRoot: "/repo" };
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
    jsonResponse("/api/chat/threads/organize", {}),
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, { activeProjectRoot: "/repo", width: 380 }), fetchMock, {
    storage: { "gofer-flow-chat-threads": JSON.stringify([thread]) },
  });
  const previousConfirm = window.confirm;
  const prompts = [];
  window.confirm = message => { prompts.push(message); return false; };
  try {
    await dom.flush();
    await dom.click(dom.byTitle("Thread options"));
    await dom.click(dom.byText("Pin thread"));
    assert.match(dom.text(), /Pinned threads[\s\S]*Organize this conversation[\s\S]*Active threads/);
    assert.equal(appModule.loadChatThread(thread.id).pinned, true);
    assert.equal(appModule.chatThreadIndex()[0].pinned, true);
    await dom.click(dom.byTitle("Thread options"));
    await dom.click(dom.byText("Unpin thread"));
    assert.doesNotMatch(dom.text(), /Pinned threads/);
    await dom.click(dom.byTitle("Thread options"));
    await dom.click(dom.byText("Delete thread"));
    assert.equal(prompts.length, 1);
    assert.match(prompts[0], /Organize this conversation.*cannot be undone/);
    assert.ok(appModule.loadChatThread(thread.id));
    assert.equal(fetchMock.calls.some(call => call.options?.method === "DELETE"), false);
    await dom.click(dom.byTitle("Archive thread"));
    assert.doesNotMatch(dom.text(), /Organize this conversation/);
    assert.equal(appModule.loadChatThread(thread.id).archived, true);
    assert.equal(appModule.chatThreadIndex()[0].archived, true);
    await dom.click(dom.byText("Archived threads"));
    assert.match(dom.text(), /Organize this conversation/);
    await dom.click(dom.byTitle("Delete thread"));
    assert.equal(prompts.length, 2);
    assert.ok(appModule.loadChatThread(thread.id));
    window.confirm = () => true;
    await dom.click(dom.byTitle("Delete thread"));
    await dom.flush();
    assert.equal(appModule.loadChatThread(thread.id), null);
    assert.equal(appModule.chatThreadIndex().length, 0);
    assert.equal(fetchMock.calls.some(call => call.options?.method === "DELETE"), true);
  } finally { window.confirm = previousConfirm; await dom.unmount(); }
});

test("Rem loads old pinned threads from the index above active threads after restart", async () => {
  const pinned = { id: "old-pin", title: "Keep this thread", pinned: true, projectRoot: "/gone", projectBranch: "deleted", updatedAt: "2020-01-01" };
  const active = { id: "active", title: "Fresh thread", updatedAt: new Date().toISOString() };
  const storage = {
    "gofer-flow-chat-threads": JSON.stringify([active, pinned]),
  };
  const dom = await mountReact(React.createElement(appModule.ThreadSections, { threads: [], onOpen() {}, onDelete() {} }), createFetchMock([]), {
    storage, desktop: { workspace: { missingThreadRoots: async () => ["/gone"] } },
  });
  try {
    await dom.flush();
    assert.match(dom.text(), /Pinned threads[\s\S]*Keep this thread[\s\S]*Active threads[\s\S]*Fresh thread/);
    await dom.click(dom.byText("Archived threads"));
    assert.equal(dom.allByTitle("Delete thread").length, 0);
  } finally { await dom.unmount(); }
});

for (const provider of ["cursor", "copilot", "opencode", "antigravity", "grok"]) {
  test(`Rem steering keeps ${provider} identity, drafts and continuation context`, async () => {
    const chunks = ["", "", "", ""];
    const stream = controlledStreamResponse(chunks);
    const accepted = createDeferred();
    let request, receipt;
    const fetchMock = createFetchMock([
      jsonResponse("/api/provider/capabilities", { providers: [{ id: provider, displayName: provider, available: true, discoveryStatus: "ready", defaultModel: "model-a", models: [{ id: "model-a" }] }] }),
      (url, options) => {
        if (url === "/api/chat/stream") {
          if (request) return streamResponse(['{"type":"final","message":{"body":"Followup complete"}}\n'])(url);
          request = JSON.parse(options.body);
          chunks[0] = JSON.stringify({ type: "turn", turnId: request.turnId, generation: 0 }) + "\n";
          return stream.response(url);
        }
        if (url === "/api/chat/steer") {
          receipt = { ...JSON.parse(options.body), provider, model: "model-a", status: "interrupting" };
          return accepted.promise;
        }
        return null;
      },
    ]);
    const dom = await mountReact(React.createElement(appModule.ChatPane, { width: 380, assistantDefaults: { provider, model: "model-a" } }), fetchMock);
    try {
      await dom.flush();
      await dom.change(dom.first("textarea"), "Build the feature");
      if (provider === "antigravity") {
        assert.equal(reactProps(dom.byTitle("Send message")).disabled, true);
        await dom.click(dom.byText("Use CLI-managed permissions"));
      }
      await dom.click(dom.byTitle("Send message"));
      await dom.flush();
      stream.releaseNext(); await dom.flush();
      assert.equal(request.provider, provider);
      assert.equal(request.model, "model-a");
      assert.equal(request.permissionMode, ["antigravity", "grok"].includes(provider) ? "cli-managed" : "default");
      assert.ok(request.conversationId && request.turnId);
      assert.ok(!reactProps(dom.first("textarea")).disabled);
      for (const trigger of allElements(dom.container).filter(el => el.getAttribute?.("data-picker-trigger"))) assert.equal(reactProps(trigger).disabled, true, `${trigger.tagName} ${trigger.getAttribute("data-picker-trigger")} ${textOf(trigger)}`);
      await dom.focus(dom.first("textarea"));
      await dom.change(dom.first("textarea"), "temporary");
      await dom.change(dom.first("textarea"), "");
      await dom.change(dom.first("textarea"), "Preserve the public API");
      await dom.blur(dom.first("textarea"));
      await dom.keyDown(dom.first("textarea"), "Enter");
      await dom.flush();
      assert.equal(receipt.text, "Preserve the public API");
      assert.equal(receipt.turnId, request.turnId);
      await dom.change(dom.first("textarea"), "A later draft");
      accepted.resolve({ ok: true, json: async () => ({ receipt }) });
      await dom.flush();
      assert.equal(dom.first("textarea").value, "A later draft");
      assert.doesNotMatch(dom.text(), /Accepted; interrupting response/);
      chunks[1] = JSON.stringify({ type: "interrupted", turnId: request.turnId, generation: 0, messages: [
        { role: "user", body: "Build the feature" },
        { role: "assistant", body: "Partial work" },
        { role: "system", body: "Continue with steering" },
        { role: "user", body: receipt.text },
      ] }) + "\n" + JSON.stringify({ type: "turn", turnId: request.turnId, generation: 1 }) + "\n";
      chunks[2] = JSON.stringify({ type: "final", turnId: request.turnId, generation: 0, message: { body: "STALE OUTPUT" } }) + "\n";
      chunks[3] = JSON.stringify({ type: "steering", receipt: { ...receipt, status: "delivered" } }) + "\n" + JSON.stringify({ type: "final", turnId: request.turnId, generation: 1, message: { body: "Updated result" } });
      stream.releaseNext(); await dom.flush();
      stream.releaseNext(); await dom.flush();
      assert.doesNotMatch(dom.text(), /STALE OUTPUT/);
      stream.releaseNext(); await dom.flush();
      assert.doesNotMatch(dom.text(), /Delivered to continuation|Continue with steering/);
      assert.equal((dom.text().match(/Preserve the public API/g) || []).length, 1);
      assert.match(dom.text(), /Updated result/);
      assert.equal(dom.first("textarea").value, "A later draft");
      const checkpoint = JSON.parse(window.localStorage.getItem(`gofer-flow-chat-thread:${request.conversationId}:context`));
      assert.equal(checkpoint.messages.at(-1).body, receipt.text);
      assert.ok(checkpoint.messages.some(message => message.body === "Continue with steering"));
      assert.ok(checkpoint.messages.some(message => message.body === "Partial work"));
      assert.ok(fetchMock.calls.some(call => call.url.startsWith("/api/chat/steering?conversationId=")));
      await dom.click(dom.byTitle("Send message")); await dom.flush();
      const followup = JSON.parse(fetchMock.calls.filter(call => call.url === "/api/chat/stream").at(-1).options.body);
      assert.equal(followup.messages.filter(message => message.body === receipt.text).length, 1);
      assert.ok(followup.messages.some(message => message.body === "Partial work"));
      assert.equal(followup.messages.at(-1).body, "A later draft");
    } finally { await dom.unmount(); }
  });
}

test("Rem retries an uncertain steering receipt with the same ID and Stop uses the active turn", async () => {
  const chunks = ["", ""];
  const stream = controlledStreamResponse(chunks);
  let request, retries = 0;
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
    (url, options) => {
      if (url === "/api/chat/stream") {
        request = JSON.parse(options.body);
        chunks[0] = JSON.stringify({ type: "turn", turnId: request.turnId, generation: 0 }) + "\n";
        chunks[1] = JSON.stringify({ type: "stopped", turnId: request.turnId }) + "\n";
        return stream.response(url);
      }
      if (url === "/api/chat/steer") {
        retries++;
        if (retries === 1) return Promise.reject(new Error("Connection lost; acceptance unknown"));
        return { ok: true, json: async () => ({ receipt: { ...JSON.parse(options.body), status: "interrupting", provider: "codex" } }) };
      }
      if (url === "/api/chat/stop") return { ok: true, json: async () => ({ stopped: true }) };
      return null;
    },
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, { width: 380 }), fetchMock);
  try {
    await dom.flush();
    await dom.change(dom.first("textarea"), "Start"); await dom.click(dom.byTitle("Send message")); await dom.flush();
    stream.releaseNext(); await dom.flush();
    await dom.change(dom.first("textarea"), "Keep this instruction"); await dom.click(dom.byLabel("Steer Rem")); await dom.flush();
    assert.equal(dom.first("textarea").value, "Keep this instruction");
    assert.match(dom.text(), /Connection lost/);
    await dom.click(dom.byLabel("Steer Rem")); await dom.flush();
    const calls = fetchMock.calls.filter(call => call.url === "/api/chat/steer").map(call => JSON.parse(call.options.body));
    assert.equal(calls[0].requestId, calls[1].requestId);
    assert.equal(dom.first("textarea").value, "");
    await dom.change(dom.first("textarea"), "Unsent draft");
    await dom.click(dom.byTitle("Stop Rem")); await dom.flush();
    assert.deepEqual(JSON.parse(fetchMock.calls.find(call => call.url === "/api/chat/stop").options.body), { conversationId: request.conversationId, turnId: request.turnId });
    stream.releaseNext(); await dom.flush();
    assert.match(dom.text(), /Rem stopped/);
    assert.equal(dom.first("textarea").value, "Unsent draft");
  } finally { await dom.unmount(); }
});

for (const surface of ["Rem", "Swarm", "Agent node"]) {
  test(`${surface} selects and saves all new providers using model dropdowns`, async () => {
    const ids = ["cursor", "copilot", "opencode", "antigravity", "grok"];
    const providers = ["codex", ...ids].map(id => ({ id, displayName: id, available: true, discoveryStatus: "ready", defaultModel: "cli-default", models: [{ id: "cli-default" }, { id: `${id}/catalog-v2` }], supportsCustomModel: true }));
    const changes = [];
    const fetchMock = createFetchMock([
      jsonResponse("/api/provider/capabilities", { providers }),
      (url, options) => url === "/api/chat/stream" ? streamResponse(['{"type":"final","message":{"body":"Saved selection"}}\n'])(url, options) : null,
      (url, options) => url.startsWith("/api/swarms") && options.method === "POST" ? { ok: true, json: async () => ({ swarm: { id: "saved" } }) } : null,
    ]);
    let element;
    if (surface === "Rem") element = React.createElement(appModule.ChatPane, { width: 380 });
    else if (surface === "Swarm") {
      const { default: SwarmWorkspace } = await viteServer.ssrLoadModule("/src/components/SwarmWorkspace.jsx");
      element = React.createElement(SwarmWorkspace, { rootPath: "/workspace", swarmId: "new", onSelect() {} });
    } else element = React.createElement(DagCanvasHarness, {
      dataDir: "/workspace", workflow: {
        ...workflowFixture(), agents: { reviewer: { subscription: "codex", model: "cli-default" } },
        nodes: [{ id: "review", type: "agent", label: "Review", x: 0, y: 0, operation: { type: "agent", agent_id: "reviewer" } }],
      }, onWorkflowChange: next => changes.push(next),
    });
    const dom = await mountReact(element, fetchMock);
    try {
      await dom.flush();
      if (surface === "Agent node") {
        await dom.pointer(dom.ancestor(dom.byText("Review"), "ARTICLE"), "onPointerDown"); await dom.flush();
      }
      for (const id of ids) {
        const trigger = allElements(dom.container).find(el => el.getAttribute?.("data-picker-trigger") === "provider");
        assert.ok(trigger, `${surface} provider picker exists`);
        await dom.click(trigger);
        const option = allElements(dom.container).find(el => el.getAttribute?.("role") === "option" && textOf(el).startsWith(id));
        await dom.click(option);
        assert.equal(allElements(dom.container).some(el => el.getAttribute?.("aria-label") === "Custom model ID"), false);
        await dom.click(allElements(dom.container).filter(el => el.getAttribute?.("data-picker-trigger") === "model").at(-1));
        await dom.click(allElements(dom.container).find(el => el.getAttribute?.("role") === "option" && textOf(el).includes(`${id}/catalog-v2`)));
        await dom.flush();
        if (surface === "Agent node") {
          assert.equal(changes.at(-1).agents.reviewer.subscription, id);
          assert.equal(changes.at(-1).agents.reviewer.model, `${id}/catalog-v2`);
        } else if (surface === "Rem") {
          if (id === "antigravity") await dom.click(dom.byText("Use CLI-managed permissions"));
          await dom.change(dom.first("textarea"), `Use ${id}`); await dom.click(dom.byTitle("Send message")); await dom.flush();
          const request = JSON.parse(fetchMock.calls.filter(call => call.url === "/api/chat/stream").at(-1).options.body);
          assert.equal(request.provider, id); assert.equal(request.model, `${id}/catalog-v2`);
          const thread = JSON.parse(window.localStorage.getItem(`gofer-flow-chat-thread-meta:${request.conversationId}`));
          assert.equal(thread.provider, id); assert.equal(thread.model, `${id}/catalog-v2`);
        }
      }
      if (surface === "Swarm") {
        const form = allElements(dom.container).find(el => el.tagName === "FORM");
        await React.act(async () => { await reactProps(form).onSubmit(testEvent(form)); }); await dom.flush();
        const request = JSON.parse(fetchMock.calls.find(call => call.url.startsWith("/api/swarms") && call.options.method === "POST").options.body);
        assert.equal(request.agents[0].provider, "grok"); assert.equal(request.agents[0].model, "grok/catalog-v2");
      }
    } finally { await dom.unmount(); }
  });
}

test("Rem keeps an unavailable saved provider and custom model instead of switching silently", async () => {
  const fetchMock = createFetchMock([jsonResponse("/api/provider/capabilities", { providers: [{ id: "codex", available: true, discoveryStatus: "ready", defaultModel: "ready-model", models: [{ id: "ready-model" }] }] })]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, { width: 380, assistantDefaults: { provider: "grok", model: "saved-model" } }), fetchMock);
  try {
    await dom.flush();
    assert.match(dom.text(), /Provider grok is not in the current catalog/);
    assert.match(dom.text(), /saved-model/);
    assert.doesNotMatch(dom.text(), /ready-model/);
  } finally { await dom.unmount(); }
});

for (const status of [400, 409, 500]) {
  test(`Rem retains steering draft on HTTP ${status} and hides failed receipts without replay`, async () => {
    let threadId;
    const stream = controlledStreamResponse(['{"type":"final","message":{"body":"Finished"}}\n']);
    const fetchMock = createFetchMock([
      jsonResponse("/api/provider/capabilities", { providers: [] }),
      (url, options) => {
        if (url === "/api/chat/stream") { threadId = JSON.parse(options.body).conversationId; return stream.response(url); }
        if (url === "/api/chat/steer") return { ok: false, status, json: async () => ({ error: `Rejected ${status}` }) };
        if (url.startsWith("/api/chat/steering?") && threadId) return { ok: true, json: async () => ({ receipts: [{ conversationId: threadId, requestId: "recovered", text: "Recovered instruction", status: "failed", provider: "copilot", model: "old-model" }] }) };
        return null;
      },
    ]);
    const dom = await mountReact(React.createElement(appModule.ChatPane, { width: 380 }), fetchMock);
    try {
      await dom.flush(); await dom.change(dom.first("textarea"), "Start"); await dom.click(dom.byTitle("Send message")); await dom.flush();
      await dom.change(dom.first("textarea"), "Do not lose this draft"); await dom.click(dom.byLabel("Steer Rem")); await dom.flush();
      assert.equal(dom.first("textarea").value, "Do not lose this draft");
      assert.match(dom.text(), new RegExp(`Rejected ${status}`));
      stream.releaseNext(); await dom.flush();
      assert.doesNotMatch(dom.text(), /Recovered instruction/);
      assert.doesNotMatch(dom.text(), /Delivery failed|Cancelled; not confirmed delivered/);
      assert.equal(fetchMock.calls.filter(call => call.url === "/api/chat/stream").length, 1);
      assert.equal(fetchMock.calls.filter(call => call.url === "/api/chat/steer").length, 1);
    } finally { await dom.unmount(); }
  });
}


test("swarm digest shows coordinator summaries, current states, and idle diagnosis", async () => {
  const { SwarmDigest } = await viteServer.ssrLoadModule("/src/components/SwarmWorkspace.jsx");
  const agents = [{ id: "lead", name: "Development lead" }, { id: "worker", name: "Backend" }];
  const run = { state: "failed", agentStates: { lead: { state: "idle" }, worker: { state: "retry_wait" } },
    digest: { updatedAt: "2026-09-16T16:00:00Z", agents: [
      { agentId: "lead", summary: "Reviewing the parser change" }, { agentId: "worker", summary: "Fixing route validation" },
    ] }, idleDiagnosis: { body: "The next assignment was never queued." } };
  const html = renderToStaticMarkup(React.createElement(SwarmDigest, { run, agents }));
  assert.match(html, /aria-label="Team status"/);
  assert.match(html, /Reviewing the parser change/);
  assert.match(html, /Fixing route validation/);
  assert.match(html, /retry wait/);
  assert.match(html, /The next assignment was never queued/);
  assert.match(html, /Coordinator update/);
});

test("swarm digest falls back to assignments and escapes provider text", async () => {
  const { SwarmDigest } = await viteServer.ssrLoadModule("/src/components/SwarmWorkspace.jsx");
  const html = renderToStaticMarkup(React.createElement(SwarmDigest, {
    agents: [{ id: "worker", name: "Backend" }], run: { state: "running",
      agentStates: { worker: { state: "working", milestoneId: "m1" } },
      objectives: [{ milestones: [{ id: "m1", title: "Fix <script>parser</script>", status: "working" }] }],
    },
  }));
  assert.match(html, /Fix &lt;script&gt;parser&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /working/);
});

test("CLI-managed Rem permissions omit the composer blurb", () => {
  const html = renderToStaticMarkup(React.createElement(chatComposerModule.default, {
    draft: "", provider: "antigravity", permissionMode: "cli-managed", onDraftChange() {}, onSend() {},
  }));
  assert.doesNotMatch(html, /The CLI controls permissions/);
  assert.doesNotMatch(html, /Raticode shell and web switches do not restrict native tools/);
});

test("browser addresses preserve Windows, UNC and local file paths", () => {
  const { normalizeBrowserUrl } = require("../../electron/browser-utils.cjs");
  for (const [input, expected] of [
    ["C:/My Notes/report #1.html", "file:///C:/My%20Notes/report%20%231.html"],
    [String.raw`C:\My Notes\report.html`, "file:///C:/My%20Notes/report.html"],
    [String.raw`\\server\share\report.html`, "file://server/share/report.html"],
    ["file//C:/My Notes/report.html", "file:///C:/My%20Notes/report.html"],
    ["file:///C:/My%20Notes/report.html#intro", "file:///C:/My%20Notes/report.html#intro"],
    ["/tmp/My Notes/report.html", "file:///tmp/My%20Notes/report.html"],
  ]) {
    assert.equal(integratedBrowserModule.browserAddress(input, "https://search.example/?q={query}"), input);
    assert.equal(normalizeBrowserUrl(input), expected);
  }
});

test("browser operation failures cannot escape after navigation or tab destruction", async () => {
  const source = fs.readFileSync(path.join(repoRoot, "frontend/electron/main.js"), "utf8");
  const body = source.slice(source.indexOf("async function runBrowserOperation("), source.indexOf("function configureBrowserSession("));
  let updates = 0;
  const sandbox = { emitBrowserState: () => { updates += 1; }, Error };
  vm.runInNewContext(body, sandbox);
  const session = {};
  await sandbox.runBrowserOperation(session, () => { throw new Error("bad file path"); });
  assert.equal(session.error, "bad file path");
  assert.equal(updates, 1);
  sandbox.emitBrowserState = () => { throw new Error("Object has been destroyed"); };
  await sandbox.runBrowserOperation(session, () => Promise.reject(new Error("ERR_FILE_NOT_FOUND")));
  assert.equal(session.error, "ERR_FILE_NOT_FOUND");
});

test("provider settings preserve executable drafts and save picker paths", async () => {
  const module = await viteServer.ssrLoadModule("/src/components/ProviderSettings.jsx");
  const saved = [];
  const dom = await mountReact(React.createElement(module.default, { providerState: {
    capabilities: [{ id: "codex", displayName: "Codex", available: true, enabled: true, detected: true, executable: "/bin/codex", executableOverride: "/old/codex" }],
    loading: false, refresh() {},
  } }), async (_url, options) => { saved.push(JSON.parse(options.body)); return { ok: true, json: async () => ({ saved: true }) }; });
  const events = [];
  window.dispatchEvent = event => { events.push(event.type); return true; };
  window.goferDesktop = { workspace: { selectPath: async () => "/picked/codex" } };
  try {
    const input = dom.byLabel("Codex executable");
    await dom.focus(input);
    await dom.change(input, "");
    assert.equal(input.value, "");
    await dom.change(input, "/new/codex");
    assert.equal(saved.length, 0);
    await dom.blur(input);
    assert.deepEqual(saved[0], { provider: "codex", executable: "/new/codex" });
    await dom.change(dom.byLabel("Codex status"), "disabled");
    assert.deepEqual(saved[1], { provider: "codex", enabled: false });
    await dom.click(dom.byText("Choose executable…"));
    assert.deepEqual(saved[2], { provider: "codex", executable: "/picked/codex" });
    assert.deepEqual(events, Array(3).fill("raticode:providers-changed"));
  } finally { delete window.goferDesktop; await dom.unmount(); }
});

test("provider picker hides disabled providers and opens settings even with none enabled", async () => {
  const module = await viteServer.ssrLoadModule("/src/components/ProviderModelEffortFields.jsx");
  const dom = await mountReact(React.createElement(module.ProviderModelEffortFields, {
    capabilities: [{ id: "codex", displayName: "Disabled Codex", enabled: false, available: true, models: [] }],
    onChange() {}, provider: "", model: "", effort: "",
  }), createFetchMock([]));
  let opened = 0;
  window.dispatchEvent = event => { if (event.type === "raticode:open-provider-settings") opened += 1; return true; };
  try {
    const trigger = allElements(dom.container).find(node => node.getAttribute?.("data-picker-trigger") === "provider");
    await dom.click(trigger);
    assert.equal(allElements(dom.container).filter(node => node.getAttribute?.("role") === "option").length, 1);
    await dom.click(dom.byText("Add a provider"));
    assert.equal(opened, 1);
  } finally { await dom.unmount(); }
});

test("Cursor catalog uses model and effort dropdowns without resetting Rem effort", async () => {
  const efforts = ["high", "high-fast", "low", "low-fast", "medium", "medium-fast"];
  const providers = [{ id: "cursor", displayName: "Cursor", available: true, discoveryStatus: "ready", supportsCustomModel: true,
    defaultModel: "auto", models: [{ id: "auto", displayName: "Auto" }, {
      id: "cursor-grok-4.5", displayName: "Cursor Grok 4.5", defaultEffort: "high",
      efforts: efforts.map(id => ({ id, displayName: id })),
    }] }];
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers }),
    (url, options) => url === "/api/chat/stream" ? streamResponse(['{"type":"final","message":{"body":"Done"}}\n'])(url, options) : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    width: 380, assistantDefaults: { provider: "cursor", model: "auto" },
  }), fetchMock);
  const trigger = kind => allElements(dom.container).find(el => el.getAttribute?.("data-picker-trigger") === kind);
  const option = text => allElements(dom.container).find(el => el.getAttribute?.("role") === "option" && textOf(el) === text);
  try {
    await dom.flush();
    assert.equal(allElements(dom.container).some(el => el.getAttribute?.("aria-label") === "Custom model ID"), false);
    await dom.click(trigger("model"));
    await dom.click(option("Cursor Grok 4.5"));
    await dom.click(trigger("effort"));
    assert.deepEqual(allElements(dom.container).filter(el => el.getAttribute?.("role") === "option").map(textOf), efforts.map(id => id === "high" ? "high (default)" : id));
    await dom.click(option("medium-fast"));
    await dom.flush();
    assert.equal(textOf(trigger("model")), "Cursor Grok 4.5");
    assert.equal(textOf(trigger("effort")), "medium-fast");
    await dom.change(dom.first("textarea"), "Use the selected model and effort");
    await dom.click(dom.byTitle("Send message"));
    await dom.flush();
    const request = JSON.parse(fetchMock.calls.find(call => call.url === "/api/chat/stream").options.body);
    assert.equal(request.model, "cursor-grok-4.5");
    assert.equal(request.effort, "medium-fast");
  } finally { await dom.unmount(); }
});

test("provider discovery renders an animated status until the catalog arrives", async () => {
  let resolveCatalog;
  const catalog = new Promise(resolve => { resolveCatalog = resolve; });
  const fetchMock = createFetchMock([
    url => url === "/api/provider/capabilities" ? catalog : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    width: 380, assistantDefaults: { provider: "cursor", model: "auto" },
  }), fetchMock);
  try {
    assert.match(textOf(dom.container), /Loading providers and models/);
    assert.doesNotMatch(textOf(dom.container), /not in the current catalog|Custom model ID/);
    resolveCatalog(new Response(JSON.stringify({ providers: [{ id: "cursor", displayName: "Cursor", available: true,
      defaultModel: "auto", supportsCustomModel: true, models: [{ id: "auto", displayName: "Auto" }] }] }),
    { headers: { "Content-Type": "application/json" } }));
    await dom.flush();
    assert.doesNotMatch(textOf(dom.container), /Loading providers and models|Custom model ID/);
    assert.ok(allElements(dom.container).some(el => el.getAttribute?.("data-picker-trigger") === "model"));
  } finally { await dom.unmount(); }
});

for (const outcome of ["complete", "error", "pending"]) {
  test(`provider browser login handles ${outcome} without a model text input`, async () => {
    const { ProviderModelEffortFields } = await viteServer.ssrLoadModule("/src/components/ProviderModelEffortFields.jsx");
    const provider = { id: "cursor", displayName: "Cursor", available: true, discoveryStatus: "unauthenticated", supportsBrowserLogin: true, supportsCustomModel: true, models: [] };
    const events = [];
    const fetchMock = createFetchMock([
      (url, options) => url === "/api/provider/auth" && options.method === "POST" ? { ok: true, json: async () => ({ status: JSON.parse(options.body).action === "cancel" ? "idle" : "pending" }) } : null,
      jsonResponse("/api/provider/auth?provider=cursor", { status: outcome, error: outcome === "error" ? "Sign-in did not complete. Please try again." : undefined }),
    ]);
    const dom = await mountReact(React.createElement(ProviderModelEffortFields, { provider: "cursor", capabilities: [provider], onChange() {} }), fetchMock);
    const originalDispatch = window.dispatchEvent;
    window.dispatchEvent = event => { events.push(event); return true; };
    try {
      assert.doesNotMatch(dom.text(), /Custom model ID/);
      await dom.click(dom.byText("Sign in to Cursor")); await dom.flush();
      assert.equal(JSON.parse(fetchMock.calls.find(call => call.url === "/api/provider/auth").options.body).provider, "cursor");
      if (outcome === "complete") assert.ok(events.some(event => event.type === "raticode:providers-changed" && event.detail.refresh));
      if (outcome === "error") assert.match(dom.text(), /Sign-in did not complete/);
      if (outcome === "pending") {
        assert.match(dom.text(), /Complete sign-in in your browser/);
        await dom.click(dom.byText("Cancel sign-in")); await dom.flush();
        assert.equal(JSON.parse(fetchMock.calls.filter(call => call.url === "/api/provider/auth").at(-1).options.body).action, "cancel");
      }
    } finally { window.dispatchEvent = originalDispatch; await dom.unmount(); }
  });
}

test("Copilot catalog keeps model and reasoning effort selections in the chat request", async () => {
  const efforts = ["low", "medium", "high"];
  const providers = [{ id: "copilot", displayName: "GitHub Copilot", available: true, discoveryStatus: "ready", supportsCustomModel: true,
    models: [{
      id: "gpt-test", displayName: "GPT Test", defaultEffort: "high",
      efforts: efforts.map(id => ({ id, displayName: id })),
    }] }];
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers }),
    (url, options) => url === "/api/chat/stream" ? streamResponse(['{"type":"final","message":{"body":"Done"}}\n'])(url, options) : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    width: 380, assistantDefaults: { provider: "copilot", model: "cli-default" },
  }), fetchMock);
  const trigger = kind => allElements(dom.container).find(el => el.getAttribute?.("data-picker-trigger") === kind);
  const option = text => allElements(dom.container).find(el => el.getAttribute?.("role") === "option" && textOf(el) === text);
  try {
    await dom.flush();
    assert.equal(allElements(dom.container).some(el => el.getAttribute?.("aria-label") === "Custom model ID"), false);
    await dom.click(trigger("model"));
    await dom.click(option("GPT Test"));
    await dom.click(trigger("effort"));
    assert.deepEqual(allElements(dom.container).filter(el => el.getAttribute?.("role") === "option").map(textOf), efforts.map(id => id === "high" ? "high (default)" : id));
    await dom.click(option("medium"));
    await dom.flush();
    assert.equal(textOf(trigger("model")), "GPT Test");
    assert.equal(textOf(trigger("effort")), "medium");
    await dom.change(dom.first("textarea"), "Use the selected model and effort");
    await dom.click(dom.byTitle("Send message"));
    await dom.flush();
    const request = JSON.parse(fetchMock.calls.find(call => call.url === "/api/chat/stream").options.body);
    assert.equal(request.model, "gpt-test");
    assert.equal(request.effort, "medium");
  } finally { await dom.unmount(); }
});

test("Copilot catalog access denial offers policy settings and refresh instead of repeated sign-in", async () => {
  const { ProviderModelEffortFields } = await viteServer.ssrLoadModule("/src/components/ProviderModelEffortFields.jsx");
  const provider = { id: "copilot", displayName: "GitHub Copilot", available: true,
    discoveryStatus: "access_denied", supportsBrowserLogin: true, models: [],
    error: "GitHub denied access to Copilot's model catalog. Check your organization's policy." };
  const dom = await mountReact(React.createElement(ProviderModelEffortFields, {
    provider: "copilot", capabilities: [provider], onChange() {},
  }), createFetchMock([]));
  try {
    assert.match(dom.text(), /GitHub denied access/);
    assert.equal(dom.byText("Open Copilot settings").getAttribute("href"), "https://github.com/settings/copilot");
    let refresh;
    const originalDispatch = window.dispatchEvent;
    window.dispatchEvent = event => {
      if (event.type === "raticode:providers-changed") refresh = event.detail;
      return true;
    };
    try {
      await dom.click(dom.byText("Refresh providers"));
      assert.deepEqual(refresh, { refresh: true });
    } finally { window.dispatchEvent = originalDispatch; }
    assert.doesNotMatch(dom.text(), /Sign in to GitHub Copilot/);
    assert.doesNotMatch(dom.text(), /Custom model ID|does not expose a model catalog/);
  } finally { await dom.unmount(); }
});


test("Rem sends current editor file references without workflow contents or stale selection", () => {
  const workflow = { id: "daily", name: "Daily", projectRoot: "/repo", sourcePath: "/repo/workflow.rattish", nodes: [{ prompt: "private" }], description: "private" };
  const thread = { projectRoot: "/repo", selectedWorkflowId: "daily" };
  const paths = appModule.editorFileReferences(["workflow-graph:daily", "/repo/workflow.rattish", "/repo/notes.md", "raticode-browser:1"], { "workflow-graph:daily": workflow });
  assert.deepEqual(paths, ["/repo/workflow.rattish", "/repo/notes.md"]);
  const context = appModule.chatWorkflowContextForThread(thread, [workflow], paths);
  assert.equal(context.selectedWorkflowId, null);
  assert.deepEqual(context.openFiles, paths);
  assert.deepEqual(context.workflows, [{ id: "daily", name: "Daily", sourcePath: "/repo/workflow.rattish" }]);
  assert.deepEqual(appModule.chatWorkflowContextForThread(thread, [workflow], []).openFiles, []);
  assert.ok(!JSON.stringify(context).includes("private"));
});

test("steering splits the visible trace without exposing continuation context", () => {
  const messages = [
    { id: "before", role: "assistant", kind: "thought", groupId: "turn", body: "Before" },
    { id: "system", role: "system", body: "The previous process was interrupted. Continue the same task." },
    { id: "steer", role: "user", body: "New direction" },
    { id: "after", role: "assistant", kind: "thought", groupId: "turn", body: "After" },
  ];
  const items = appModule.buildChatItems(messages);
  assert.deepEqual(items.map(item => item.type), ["thought-group", "message", "thought-group"]);
  assert.equal(items[1].message.body, "New direction");
  assert.notEqual(items[0].id, items[2].id);
  assert.equal(messages[1].role, "system");
});


test("Antigravity displays native catalog labels and sign-in guidance without custom inputs", async () => {
  const module = await viteServer.ssrLoadModule("/src/components/ProviderModelEffortFields.jsx");
  const changes = [];
  const dom = await mountReact(React.createElement(module.ProviderModelEffortFields, {
    capabilities: [{ id: "antigravity", displayName: "Antigravity", available: true,
      discoveryStatus: "ready", models: [
        { id: "gemini-3.8-flash-high", displayName: "Gemini 3.8 Flash", defaultEffort: "high", efforts: [ { id: "low", displayName: "Low" }, { id: "medium", displayName: "Medium" }, { id: "high", displayName: "High" } ] },
        { id: "claude-sonnet-4-6", displayName: "Claude Sonnet 4.6 (Thinking)" },
      ] }], provider: "antigravity", model: "gemini-3.8-flash-low", effort: "",
    onChange(patch) { changes.push(patch); },
  }), createFetchMock([]));
  try {
    assert.match(dom.text(), /Antigravity/);
    assert.match(dom.text(), /Gemini 3.8 Flash/);
    assert.doesNotMatch(dom.text(), /Flash \(High\)/);
    assert.equal(changes.at(-1).model, "gemini-3.8-flash-high");
    assert.equal(changes.at(-1).effort, "low");
    await dom.click(allElements(dom.container).find(el => el.getAttribute?.("data-picker-trigger") === "effort"));
    await dom.click(allElements(dom.container).find(el => el.getAttribute?.("role") === "option" && textOf(el) === "Medium"));
    assert.equal(changes.at(-1).effort, "medium");

    assert.equal(allElements(dom.container).some(el => el.getAttribute?.("aria-label") === "Custom model ID"), false);
    await dom.click(allElements(dom.container).find(el => el.getAttribute?.("data-picker-trigger") === "model"));
    await dom.click(allElements(dom.container).find(el => el.getAttribute?.("role") === "option" && textOf(el) === "Claude Sonnet 4.6 (Thinking)"));
    assert.equal(changes.at(-1).model, "claude-sonnet-4-6");
  } finally { await dom.unmount(); }
  const login = await mountReact(React.createElement(module.ProviderAuthentication, {
    provider: { id: "antigravity", displayName: "Antigravity", discoveryStatus: "unauthenticated", supportsBrowserLogin: false },
  }), createFetchMock([]));
  try { assert.match(login.text(), /Run agy in a terminal/); }
  finally { await login.unmount(); }
});


test("Grok offers native efforts and preserves the selected canonical value", async () => {
  const module = await viteServer.ssrLoadModule("/src/components/ProviderModelEffortFields.jsx");
  const changes = [];
  const dom = await mountReact(React.createElement(module.ProviderModelEffortFields, {
    capabilities: [{ id: "grok", displayName: "xAI Grok", available: true,
      discoveryStatus: "ready", defaultModel: "grok-4.6", models: [{
        id: "grok-4.6", displayName: "Grok 4.6", defaultEffort: "high",
        efforts: [
          { id: "xhigh", displayName: "Extra High Effort" },
          { id: "high", displayName: "High Effort" },
          { id: "medium", displayName: "Medium Effort" },
          { id: "low", displayName: "Low Effort" },
        ],
      }],
    }], provider: "grok", model: "grok-4.6", effort: "high",
    onChange(patch) { changes.push(patch); },
  }), createFetchMock([]));
  try {
    assert.match(dom.text(), /Grok 4.6/);
    await dom.click(allElements(dom.container).find(el => el.getAttribute?.("data-picker-trigger") === "effort"));
    await dom.click(allElements(dom.container).find(el => el.getAttribute?.("role") === "option" && textOf(el) === "Extra High Effort"));
    assert.equal(changes.at(-1).effort, "xhigh");
    assert.equal(allElements(dom.container).some(el => el.getAttribute?.("aria-label") === "Custom model ID"), false);
  } finally { await dom.unmount(); }
});

test("Rem steering uploads files while running and retains them for an uncertain retry", async () => {
  const chunks = ["", ""];
  const stream = controlledStreamResponse(chunks);
  let request, attempts = 0;
  const stored = [{ id: "stored", name: "context.txt", type: "text/plain", size: 7, storageName: "stored-context.txt" }];
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
    jsonResponse("/api/chat/attachments", { attachments: stored }, { method: "POST" }),
    (url, options) => {
      if (url === "/api/chat/stream") {
        request = JSON.parse(options.body);
        chunks[0] = JSON.stringify({ type: "turn", turnId: request.turnId, generation: 0 }) + "\n";
        chunks[1] = JSON.stringify({ type: "final", turnId: request.turnId, generation: 0, message: { body: "Done" } }) + "\n";
        return stream.response(url);
      }
      if (url === "/api/chat/steer") {
        attempts++;
        if (attempts === 1) return Promise.reject(new Error("Connection lost"));
        return { ok: true, json: async () => ({ receipt: { ...JSON.parse(options.body), status: "interrupting" } }) };
      }
      return null;
    },
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, { width: 380 }), fetchMock);
  try {
    await dom.flush();
    await dom.change(dom.first("textarea"), "Start working");
    await dom.click(dom.byTitle("Send message"));
    stream.releaseNext(); await dom.flush();
    assert.equal(dom.byLabel("Attach files").disabled, false);
    const input = allElements(dom.container).find(el => el.tagName === "INPUT" && el.getAttribute("type") === "file");
    await React.act(async () => reactProps(input).onChange({ target: {
      files: [{ name: "context.txt", type: "text/plain", size: 7, text: async () => "context" }], value: "context.txt",
    } }));
    assert.equal(dom.first("textarea").value, "");
    await dom.keyDown(dom.first("textarea"), "Enter"); await dom.flush();
    assert.match(dom.text(), /Connection lost/);
    assert.match(dom.text(), /context.txt/);
    await dom.click(dom.byText("Steer")); await dom.flush();
    const sent = fetchMock.calls.filter(call => call.url === "/api/chat/steer").map(call => JSON.parse(call.options.body));
    assert.equal(sent.length, 2);
    assert.equal(sent[0].requestId, sent[1].requestId);
    assert.deepEqual(sent[0].attachments, stored);
    assert.deepEqual(sent[1].attachments, stored);
    assert.equal(fetchMock.calls.filter(call => call.url === "/api/chat/attachments").length, 1);
    assert.doesNotMatch(dom.text(), /context.txt/);
    stream.releaseNext(); await dom.flush();
  } finally { await dom.unmount(); }
});

test("Grok defaults to CLI-managed and warns when changed, persisting the thread choice", async () => {
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [{ id: "grok", displayName: "Grok", models: [{ id: "grok-4.6" }], defaultModel: "grok-4.6", available: true }] }),
    url => url === "/api/chat/stream" ? streamResponse(['{"type":"final","message":{"body":"Done"}}\n'])(url) : null,
  ]);
  let dom = await mountReact(React.createElement(appModule.ChatPane, { width: 380, assistantDefaults: { provider: "grok", model: "grok-4.6" } }), fetchMock);
  let storage;
  try {
    await dom.flush();
    await dom.change(dom.first("textarea"), "Hello");
    assert.equal(reactProps(dom.selectWithOption("cli-managed")).value, "cli-managed");
    assert.equal(reactProps(dom.byTitle("Send message")).disabled, false);
    assert.doesNotMatch(dom.text(), /needs CLI-managed permissions|The CLI controls permissions/);
    await dom.change(dom.selectWithOption("cli-managed"), "default");
    assert.match(dom.text(), /needs CLI-managed permissions/);
    assert.equal(reactProps(dom.byTitle("Send message")).disabled, true);
    assert.equal(fetchMock.calls.filter(call => call.url === "/api/chat/stream").length, 0);
    await dom.click(dom.byText("Use CLI-managed permissions"));
    await dom.click(dom.byTitle("Send message")); await dom.flush();
    const request = JSON.parse(fetchMock.calls.find(call => call.url === "/api/chat/stream").options.body);
    assert.equal(request.permissionMode, "cli-managed");
    assert.equal(appModule.loadChatThread(request.conversationId).permissionsByProvider.grok, "cli-managed");
    storage = Object.fromEntries([
      "gofer-flow-chat-threads",
      `gofer-flow-chat-thread-meta:${request.conversationId}`,
      `gofer-flow-chat-thread:${request.conversationId}`,
    ].map(key => [key, window.localStorage.getItem(key)]));
  } finally { await dom.unmount(); }
  dom = await mountReact(React.createElement(appModule.ChatPane, { width: 380 }), fetchMock, { storage });
  try {
    await dom.flush();
    await dom.click(dom.ancestor(dom.byText("Hello"), "BUTTON")); await dom.flush();
    assert.equal(reactProps(dom.selectWithOption("cli-managed")).value, "cli-managed");
    assert.doesNotMatch(dom.text(), /needs CLI-managed permissions/);
  } finally { await dom.unmount(); }
});

test("equivalent Windows roots keep discovered workflows visible and available to Rem", () => {
  const workflow = { id: "windows-flow", name: "Windows flow", projectRoot: "C:\\Users\\Alice\\repo", sourcePath: "C:\\Users\\Alice\\repo\\.raticode\\flow\\workflow.rattish" };
  for (const root of ["c:/Users/Alice/repo", "C:/Users/Alice/repo/", "C:\\Users\\Alice\\repo\\"]) {
    assert.equal(appModule.activeWorkspaceForProject([workflow], workflow.id, root), workflow);
    assert.equal(appModule.chatWorkflowContextForThread({ projectRoot: root }, [workflow]).workflows.length, 1);
    assert.deepEqual(appModule.mergeRecentProjects([workflow.projectRoot], [root]), [workflow.projectRoot]);
    assert.deepEqual(appModule.rememberRecentProject([workflow.projectRoot], root), [root]);
    assert.equal(appModule.scopeChatThreadToProject({ projectRoot: workflow.projectRoot, projectBranch: "main" }, root).projectBranch, "main");
    const groups = appModule.groupWorkflowsByProject([workflow, { ...workflow, id: "second", projectRoot: root }], { [root]: "My project" });
    assert.equal(groups.length, 1);
    assert.equal(groups[0].name, "My project");
    assert.equal(groups[0].items.length, 2);
  }
  assert.equal(appModule.activeWorkspaceForProject([workflow], workflow.id, "C:/Users/Alice/repository").sourceFormat, "project");
});

test("explorer ancestry preserves POSIX case and accepts Windows variants", () => {
  assert.deepEqual(codeFileExplorerModule.workspaceAncestorPaths("/repo", "/Repo/src/file.py"), []);
  assert.deepEqual(codeFileExplorerModule.workspaceAncestorPaths("C:\\Repo", "c:/repo/Src/file.py"), ["C:\\Repo", "C:\\Repo\\Src"]);
  assert.deepEqual(codeFileExplorerModule.workspaceAncestorPaths("/", "/src/file.py"), ["/", "/src"]);
});

test("Git changes do not hide distinct POSIX filenames with different case", () => {
  const entries = [{ name: "File.py", path: "/repo/File.py", isFile: true }];
  const changes = [{ path: "file.py", status: "?" }];
  assert.equal(codeFileExplorerModule.directoryEntriesWithGitChanges("/repo", "/repo", entries, changes).length, 2);
  assert.equal(codeFileExplorerModule.directoryEntriesWithGitChanges("C:/repo", "C:/repo", entries, changes).length, 1);
});

test("Windows workflow sidebar renders discovered workflows for an equivalent selected root", () => {
  const workflow = workflowFixture({ id: "windows-sidebar", name: "Windows workflow" });
  workflow.projectRoot = "C:\\Users\\Alice\\repo";
  const markup = renderToStaticMarkup(React.createElement(appModule.WorkflowSidebar, {
    activeWorkflow: { ...workflow, projectRoot: "c:/Users/Alice/repo/" },
    activeWorkflowId: workflow.id, workflows: [workflow], query: "", runState: {}, view: "graph",
  }));
  assert.match(markup, /Windows workflow/);
});

test("project discovery exposes the backend-resolved root even without workflows", async () => {
  globalThis.fetch = createFetchMock([
    jsonResponse("/api/projects/open", { projectRoot: "C:\\Repo", workflows: [] }, { method: "POST" }),
  ]);
  window.goferDesktop = { workspace: { trustProjectRoot: async () => {} } };
  let resolved;
  assert.deepEqual(await appModule.discoverProjectWorkflows("C:/alias/../Repo", { onResolvedRoot: root => { resolved = root; } }), []);
  assert.equal(resolved, "C:\\Repo");
});

test("Git status decoration follows Windows identity and POSIX case", () => {
  const statuses = [{ path: "Src/File.py", status: "M" }];
  assert.equal(codeFileExplorerModule.sourceControlStatusForPath("C:/Repo", "c:\\repo\\src\\file.py", statuses), "M");
  assert.equal(codeFileExplorerModule.sourceControlStatusForPath("/Repo", "/Repo/src/file.py", statuses), "");
  assert.equal(codeFileExplorerModule.sourceControlStatusForPath("C:/Repo", "c:/repo/src", statuses, true), "changed");
  const entries = codeFileExplorerModule.directoryEntriesWithGitChanges("C:/Repo", "c:\\repo\\src", [], statuses);
  assert.equal(entries[0].name, "File.py");
});


test("active threads are clickable before Git resolves and metadata reads follow pagination", async () => {
  const all = Array.from({ length: 120 }, (_, i) => ({ id: `fast-${i}`, title: `Fast thread ${i}`, updatedAt: new Date(Date.now() - i * 1000).toISOString(), projectRoot: "/repo", projectBranch: "main" }));
  const storage = Object.fromEntries(all.map(thread => [`gofer-flow-chat-thread-meta:${thread.id}`, JSON.stringify(thread)]));
  storage["gofer-flow-chat-threads"] = JSON.stringify(all.map(thread => ({ ...thread, title: undefined, scopeIndexed: true })));
  const reads = [], opened = [];
  const git = createDeferred();
  let wrapped = false, branchCalls = 0;
  const workspace = { gitStatus: () => assert.fail("list scanned working tree"), gitBranches: () => { branchCalls++; return git.promise; } };
  function Harness() {
    if (!wrapped) {
      wrapped = true;
      const get = window.localStorage.getItem.bind(window.localStorage);
      window.localStorage.getItem = key => { reads.push(key); return get(key); };
    }
    return React.createElement(appModule.ThreadSections, { threads: [], onOpen: id => opened.push(id), onDelete() {} });
  }
  const dom = await mountReact(React.createElement(Harness), createFetchMock([]), { storage, desktop: { workspace } });
  try {
    assert.equal(branchCalls, 1);
    assert.equal(reads.filter(key => key.startsWith("gofer-flow-chat-thread-meta:")).length, 15);
    assert.equal(reads.some(key => key.startsWith("gofer-flow-chat-thread:")), false);
    await dom.click(allElements(dom.container).find(el => el.tagName === "BUTTON" && el.textContent.includes("Fast thread 0")));
    assert.deepEqual(opened, ["fast-0"]);
    await dom.click(dom.byText("Show older threads"));
    assert.equal(reads.filter(key => key.startsWith("gofer-flow-chat-thread-meta:")).length, 30);
    git.resolve({ active: true, branches: ["main"] }); await dom.flush();
    assert.equal(reads.filter(key => key.startsWith("gofer-flow-chat-thread-meta:")).length, 30);
  } finally { await dom.unmount(); }
});

test("project navigation becomes usable before discovery and Git metadata finish", async () => {
  const discovery = createDeferred();
  const git = createDeferred();
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([])),
    (url) => url === "/api/projects/open" ? { ok: true, status: 200, json: () => discovery.promise } : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock, {
    storage: {
      "gofer.recentProjects": JSON.stringify(["/second"]),
      [appModule.STUDIO_SESSION_STORAGE_KEY]: JSON.stringify({ projectRoot: "/workspace", view: "code" }),
    },
    desktop: { workspace: {
      trustProjectRoot: async root => root,
      gitWorktrees: () => git.promise,
    } },
  });
  try {
    await dom.flush();
    await dom.click(dom.byLabel("Recent projects"));
    await dom.click(dom.byTitle("/second"));
    await dom.flush();
    assert.equal(appModule.loadStudioSession().projectRoot, "/second");
    assert.equal(dom.byLabel("Recent projects").getAttribute("title"), "/second");
    assert.ok(dom.byText("Open File"));
    discovery.resolve({ projectRoot: "/second", workflows: [] });
    await dom.flush();
    assert.doesNotMatch(dom.text(), /Opening second/);
    assert.equal(appModule.loadStudioSession().projectRoot, "/second");
  } finally {
    discovery.resolve({ workflows: [] });
    git.resolve({ worktrees: [{ path: "/second" }] });
    await dom.flush();
    await dom.unmount();
  }
});

test("Stop during Rem startup waits for the turn identity and explicitly stops backend work", async () => {
  const chunks = ["", ""];
  const stream = controlledStreamResponse(chunks);
  let request;
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [] }),
    (url, options) => {
      if (url === "/api/chat/stream") {
        request = JSON.parse(options.body);
        chunks[0] = JSON.stringify({ type: "turn", turnId: request.turnId, generation: 0 }) + "\n";
        chunks[1] = JSON.stringify({ type: "stopped", turnId: request.turnId }) + "\n";
        return stream.response(url);
      }
      if (url === "/api/chat/stop") return { ok: true, json: async () => ({ stopped: true }) };
      return null;
    },
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, { width: 380 }), fetchMock);
  try {
    await dom.flush();
    await dom.change(dom.first("textarea"), "Start");
    await dom.click(dom.byTitle("Send message")); await dom.flush();
    await dom.click(dom.byTitle("Stop Rem")); await dom.flush();
    assert.equal(fetchMock.calls.filter(call => call.url === "/api/chat/stop").length, 0);
    stream.releaseNext(); await dom.flush();
    assert.deepEqual(JSON.parse(fetchMock.calls.find(call => call.url === "/api/chat/stop").options.body), {
      conversationId: request.conversationId, turnId: request.turnId,
    });
    stream.releaseNext(); await dom.flush();
    assert.match(dom.text(), /Rem stopped/);
  } finally { await dom.unmount(); }
});

test("provider defaults save, survive remount, apply when switching back, and reset", async () => {
  const settings = await viteServer.ssrLoadModule("/src/components/ProviderSettings.jsx");
  const picker = await viteServer.ssrLoadModule("/src/components/ProviderModelEffortFields.jsx");
  let preferences = { defaultModel: "", defaultEffort: "" };
  const saved = [];
  function catalog() {
    return [{ id: "codex", displayName: "Codex", available: true, discoveryStatus: "ready",
      defaultModel: preferences.defaultModel || "sol", providerDefaultModel: "sol",
      defaultModelOverride: preferences.defaultModel, defaultEffortOverride: preferences.defaultEffort,
      models: [{ id: "sol", displayName: "Sol" }, { id: "astra", displayName: "Astra",
        defaultEffort: preferences.defaultEffort || "medium", providerDefaultEffort: "medium",
        efforts: [ { id: "medium" }, { id: "high" } ] }] },
    { id: "claude_code", displayName: "Claude Code", available: true, discoveryStatus: "ready",
      defaultModel: "sonnet", models: [{ id: "sonnet", displayName: "Sonnet" }] }];
  }
  function Harness() {
    const providerState = picker.useProviderCapabilities();
    const [selection, setSelection] = React.useState({ provider: "claude_code", model: "sonnet", effort: "" });
    return React.createElement(React.Fragment, null,
      React.createElement(settings.default, { providerState }),
      React.createElement(picker.ProviderModelEffortFields, { ...selection, ...providerState,
        onChange: patch => setSelection(current => ({ ...current, ...patch })) }),
      React.createElement("output", { "aria-label": "Selection" }, JSON.stringify(selection)));
  }
  const fetchMock = createFetchMock([(url, options) => {
    if (url.startsWith("/api/provider/capabilities")) return { ok: true, json: async () => ({ providers: catalog() }) };
    if (url === "/api/provider/settings") {
      const { provider, ...patch } = JSON.parse(options.body);
      saved.push({ provider, ...patch }); preferences = { ...preferences, ...patch };
      return { ok: true, json: async () => ({ saved: true }) };
    }
    return null;
  }]);
  const dispatch = event => {
    for (const listener of document.listeners[event.type] ?? []) listener(event);
    return true;
  };
  let dom = await mountReact(React.createElement(Harness), fetchMock);
  window.dispatchEvent = dispatch;
  async function switchTo(name) {
    await dom.click(allElements(dom.container).find(el => el.getAttribute?.("data-picker-trigger") === "provider"));
    await dom.click(allElements(dom.container).find(el => el.getAttribute?.("role") === "option" && textOf(el).startsWith(name)));
    await dom.flush();
  }
  try {
    await dom.flush();
    await dom.change(dom.byLabel("Codex default model"), "astra"); await dom.flush();
    await dom.change(dom.byLabel("Codex default effort"), "high"); await dom.flush();
    assert.deepEqual(saved, [{ provider: "codex", defaultModel: "astra", defaultEffort: "" }, { provider: "codex", defaultEffort: "high" }]);
    await dom.unmount();
    dom = await mountReact(React.createElement(Harness), fetchMock);
    window.dispatchEvent = dispatch;
    await dom.flush();
    assert.equal(reactProps(dom.byLabel("Codex default model")).value, "astra");
    assert.equal(reactProps(dom.byLabel("Codex default effort")).value, "high");
    assert.equal(reactProps(dom.byLabel("Claude Code default effort")).disabled, true);
    await switchTo("Codex");
    assert.deepEqual(JSON.parse(textOf(dom.byLabel("Selection"))), { provider: "codex", model: "astra", effort: "high" });
    await switchTo("Claude Code"); await switchTo("Codex");
    assert.deepEqual(JSON.parse(textOf(dom.byLabel("Selection"))), { provider: "codex", model: "astra", effort: "high" });
    await dom.click(dom.byText("Reset model and effort defaults")); await dom.flush();
    // Changing defaults must not overwrite the current explicit selection.
    assert.deepEqual(JSON.parse(textOf(dom.byLabel("Selection"))), { provider: "codex", model: "astra", effort: "high" });
    await switchTo("Claude Code"); await switchTo("Codex");
    assert.deepEqual(JSON.parse(textOf(dom.byLabel("Selection"))), { provider: "codex", model: "sol", effort: "" });
  } finally { await dom.unmount(); }
});

test("provider defaults are unavailable until model discovery is ready and save failures are visible", async () => {
  const module = await viteServer.ssrLoadModule("/src/components/ProviderSettings.jsx");
  const dom = await mountReact(React.createElement(module.default, { providerState: {
    capabilities: [
      { id: "codex", displayName: "Codex", available: true, discoveryStatus: "ready", defaultModel: "sol", models: [{ id: "sol" }] },
      { id: "grok", displayName: "Grok", available: true, discoveryStatus: "unauthenticated", models: [{ id: "grok" }] },
    ], loading: false, refresh() {},
  } }), async () => ({ ok: false, json: async () => ({ error: "Could not save defaults" }) }));
  try {
    assert.equal(allElements(dom.container).some(el => el.getAttribute?.("aria-label") === "Grok default model"), false);
    await dom.change(dom.byLabel("Codex default model"), "sol"); await dom.flush();
    assert.match(dom.text(), /Could not save defaults/);
    assert.equal(reactProps(dom.byLabel("Codex default model")).value, "");
  } finally { await dom.unmount(); }
});

test("Rem sends provider model and effort overrides on startup and after switching back", async () => {
  const providers = [
    { id: "codex", displayName: "Codex", available: true, discoveryStatus: "ready",
      defaultModel: "astra", defaultModelOverride: "astra", defaultEffortOverride: "high",
      models: [{ id: "sol" }, { id: "astra", defaultEffort: "high", efforts: [{ id: "medium" }, { id: "high" }] }] },
    { id: "claude_code", displayName: "Claude Code", available: true, discoveryStatus: "ready",
      defaultModel: "sonnet", models: [{ id: "sonnet" }] },
  ];
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers }),
    (url, options) => url === "/api/chat/stream" ? streamResponse(['{"type":"final","message":{"body":"Done"}}\n'])(url, options) : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, { width: 380 }), fetchMock);
  try {
    await dom.flush();
    for (const switchBack of [false, true]) {
      if (switchBack) {
        for (const name of ["Claude Code", "Codex"]) {
          await dom.click(allElements(dom.container).find(el => el.getAttribute?.("data-picker-trigger") === "provider"));
          await dom.click(allElements(dom.container).find(el => el.getAttribute?.("role") === "option" && textOf(el).startsWith(name)));
          await dom.flush();
        }
      }
      await dom.change(dom.first("textarea"), "Check defaults");
      await dom.click(dom.byTitle("Send message")); await dom.flush();
      const request = JSON.parse(fetchMock.calls.filter(call => call.url === "/api/chat/stream").at(-1).options.body);
      assert.equal(request.provider, "codex");
      assert.equal(request.model, "astra");
      assert.equal(request.effort, "high");
    }
  } finally { await dom.unmount(); }
});

test("canonical project discovery shares work and cancelled callers receive no late callbacks", async () => {
  const pending = createDeferred();
  const controllers = [new AbortController(), new AbortController()];
  window.goferDesktop = { workspace: { trustProjectRoot: async () => "/canonical/project" } };
  const fetchMock = createFetchMock([url => url === "/api/projects/open"
    ? { ok: true, json: () => pending.promise } : null]);
  globalThis.fetch = fetchMock;
  const callbacks = [];
  const a = appModule.discoverProjectWorkflows("/alias", { signal: controllers[0].signal, onResolvedRoot: () => callbacks.push("A") });
  const b = appModule.discoverProjectWorkflows("/canonical/project", { signal: controllers[1].signal, onResolvedRoot: () => callbacks.push("B") });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(fetchMock.calls.length, 1);
  controllers[0].abort();
  await assert.rejects(a, { name: "AbortError" });
  assert.equal(fetchMock.calls[0].options.signal.aborted, false);
  pending.resolve({ projectRoot: "/canonical/project", workflows: [{ id: "shared" }] });
  assert.deepEqual(await b, [{ id: "shared" }]);
  assert.deepEqual(callbacks, ["B"]);
});

for (const lateFailure of [false, true]) test(`project B survives late project A ${lateFailure ? "failure" : "workflows"}`, async () => {
  const a = createDeferred();
  const b = createDeferred();
  const fetchMock = createFetchMock([
    jsonResponse("/api/workflows", workflowsPayload([])),
    (url, options) => {
      if (url !== "/api/projects/open") return null;
      const root = JSON.parse(options.body).projectRoot;
      return { ok: true, json: () => (root === "/A" ? a : b).promise };
    },
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock, {
    storage: {
      "gofer.recentProjects": JSON.stringify(["/A", "/B"]),
      [appModule.STUDIO_SESSION_STORAGE_KEY]: JSON.stringify({ projectRoot: "/initial", view: "code" }),
    },
    desktop: { workspace: { trustProjectRoot: async root => root, gitWorktrees: async () => ({ worktrees: [] }) } },
  });
  try {
    await dom.flush();
    for (const root of ["/A", "/B"]) {
      await dom.click(dom.byLabel("Recent projects"));
      await dom.click(dom.byTitle(root));
      await dom.flush();
    }
    const aRequest = fetchMock.calls.find(call => call.url === "/api/projects/open" && JSON.parse(call.options.body).projectRoot === "/A");
    assert.equal(aRequest.options.signal.aborted, true);
    b.resolve({ projectRoot: "/B", workflows: [] });
    await dom.flush();
    if (lateFailure) a.reject(new Error("A private failure"));
    else a.resolve({ projectRoot: "/A", workflows: [{ ...workflowFixture({ id: "obsolete-a", name: "Obsolete A" }), projectRoot: "/A" }] });
    await dom.flush();
    assert.equal(appModule.loadStudioSession().projectRoot, "/B");
    assert.equal(dom.byLabel("Recent projects").getAttribute("title"), "/B");
    assert.doesNotMatch(dom.text(), /Obsolete A|A private failure|Discovery cancelled/);
  } finally {
    a.resolve({ workflows: [] }); b.resolve({ workflows: [] });
    await dom.unmount();
  }
});

test("provider responses and errors belong to the latest project generation", async () => {
  const { useProviderCapabilities } = await viteServer.ssrLoadModule("/src/components/ProviderModelEffortFields.jsx");
  const pending = [];
  let switchRoot;
  function Harness() {
    const [root, setRoot] = React.useState("/A");
    switchRoot = setRoot;
    const state = useProviderCapabilities(root);
    return React.createElement("output", null, JSON.stringify({ root, ...state }));
  }
  const dom = await mountReact(React.createElement(Harness), createFetchMock([
    url => {
      if (!url.startsWith("/api/provider/capabilities")) return null;
      const request = createDeferred(); pending.push(request);
      return { ok: true, json: () => request.promise };
    },
  ]));
  try {
    await dom.flush();
    await React.act(async () => switchRoot("/B"));
    pending[1].resolve({ providers: [{ id: "B-provider" }] }); await dom.flush();
    pending[0].resolve({ providers: [{ id: "A-provider" }] }); await dom.flush();
    assert.match(dom.text(), /B-provider/);
    assert.doesNotMatch(dom.text(), /A-provider/);
    await React.act(async () => switchRoot("/C"));
    await React.act(async () => switchRoot("/D"));
    pending[2].reject(new Error("C failure"));
    pending[3].reject(new Error("D failure")); await dom.flush();
    assert.match(dom.text(), /D failure/);
    assert.doesNotMatch(dom.text(), /C failure/);
  } finally { await dom.unmount(); }
});

test("late Git and directory results cannot overwrite the new explorer project", async () => {
  const gitA = createDeferred(), dirA = createDeferred();
  let switchRoot;
  function Harness() {
    const [root, setRoot] = React.useState("/A"); switchRoot = setRoot;
    return React.createElement(codeFileExplorerModule.default, { workflow: { projectRoot: root }, onOpenFile() {} });
  }
  const dom = await mountReact(React.createElement(Harness), createFetchMock([]), { desktop: { workspace: {
    listDirectory: ({ currentPath }) => currentPath === "/A" ? dirA.promise : Promise.resolve({ entries: [{ name: "B.txt", path: "/B/B.txt", isFile: true }] }),
    gitStatus: root => root === "/A" ? gitA.promise : Promise.resolve({ active: true, branch: "B-branch", entries: [] }),
  } } });
  try {
    await dom.flush();
    await React.act(async () => switchRoot("/B")); await dom.flush();
    gitA.resolve({ active: true, branch: "A-branch", entries: [{ path: "A.txt", status: "M" }] });
    dirA.reject(new Error("A directory failure")); await dom.flush();
    assert.match(dom.text(), /B.txt/);
    assert.doesNotMatch(dom.text(), /A-branch|A.txt|A directory failure/);
  } finally { dirA.resolve({ entries: [] }); gitA.resolve({}); await dom.unmount(); }
});

test("obsolete workflow list cannot replace a selected project and current discovery errors remain visible", async () => {
  const oldList = createDeferred();
  const b = createDeferred();
  const fetchMock = createFetchMock([
    url => url === "/api/workflows" ? { ok: true, json: () => oldList.promise } : null,
    url => url === "/api/projects/open" ? { ok: false, json: () => b.promise } : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.default), fetchMock, {
    storage: {
      "gofer.recentProjects": JSON.stringify(["/B"]),
      [appModule.STUDIO_SESSION_STORAGE_KEY]: JSON.stringify({ projectRoot: "/A", view: "code" }),
    },
    desktop: { workspace: { trustProjectRoot: async root => root, gitWorktrees: async () => ({ worktrees: [] }) } },
  });
  try {
    await dom.flush();
    await dom.click(dom.byLabel("Recent projects")); await dom.click(dom.byTitle("/B")); await dom.flush();
    assert.ok(fetchMock.calls.filter(call => call.url === "/api/workflows").some(call => call.options.signal?.aborted));
    oldList.resolve(workflowsPayload([{ ...workflowFixture({ id: "old-list-a", name: "Obsolete list A" }), projectRoot: "/A" }]));
    b.resolve({ error: "Unable to discover project workflows: permission denied. Retry on refresh." });
    await dom.flush();
    assert.equal(appModule.loadStudioSession().projectRoot, "/B");
    assert.match(dom.text(), /permission denied/);
    assert.doesNotMatch(dom.text(), /Obsolete list A|Discovery cancelled/);
  } finally { oldList.resolve(workflowsPayload([])); b.resolve({}); await dom.unmount(); }
});

test("human inbox shows all requests and sends additional instruction to the selected notification", async () => {
  const { HumanInbox } = await viteServer.ssrLoadModule("/src/components/SwarmWorkspace.jsx");
  const calls = [];
  const run = { humanInbox: [
    { id: "approve", agentId: "worker", kind: "approval", state: "pending", description: "Publish <script>draft</script>?", recommendedAction: "Review the diff" },
    { id: "other", agentId: "lead", kind: "approval", state: "pending", description: "Choose a scope", recommendedAction: "Keep current scope" },
    { id: "done", agentId: "worker", kind: "approval", state: "addressed", description: "Old request" },
  ] };
  const dom = await mountReact(React.createElement(HumanInbox, { run, agents: [{ id: "worker", name: "Worker" }], onAction: value => calls.push(value) }), createFetchMock());
  try {
    assert.match(dom.text(), /2 unread/);
    assert.match(dom.text(), /Review the diff/);
    assert.match(dom.text(), /Choose a scope/);
    assert.match(dom.text(), /Old request/);
    assert.match(dom.text(), /Read/);
    assert.equal(allElements(dom.container).filter(el => el.tagName === "SCRIPT").length, 0);
    const article = dom.first("article");
    const alternate = allElements(article).find(el => el.tagName === "BUTTON" && el.textContent === "Do something else");
    await dom.click(alternate);
    await dom.change(dom.first("textarea"), "Inspect only. Do not publish.");
    await dom.pointer(dom.first("form"), "onSubmit");
    assert.deepEqual(calls, [{ action: "human_response", notificationId: "approve", decision: "redirect", instruction: "Inspect only. Do not publish.", resolution: "review" }]);
  } finally { await dom.unmount(); }
});

test("recovery inbox proceeds without a checkbox and offers retry of the existing attempt", async () => {
  const { HumanInbox } = await viteServer.ssrLoadModule("/src/components/SwarmWorkspace.jsx");
  const calls = [];
  const run = { humanInbox: [{ id: "recover", agentId: "worker", kind: "recovery", state: "pending", attemptId: "existing", workspace: { path: "/preserved/checkout" }, description: "Interrupted execution", recommendedAction: "Inspect prior effects" }] };
  const dom = await mountReact(React.createElement(HumanInbox, { run, agents: [], onAction: value => calls.push(value) }), createFetchMock());
  try {
    assert.match(dom.text(), /\/preserved\/checkout/);
    assert.match(dom.text(), /Attempt existing/);
    assert.equal(allElements(dom.container).filter(el => el.tagName === "INPUT").length, 0);
    assert.equal(reactProps(dom.byText("Proceed")).disabled, false);
    await dom.change(dom.first("select"), "retry");
    assert.equal(reactProps(dom.byText("Proceed")).disabled, false);
    await dom.pointer(dom.first("form"), "onSubmit");
    assert.equal(calls[0].resolution, "retry");
    assert.equal(calls[0].notificationId, "recover");
    const html = renderToStaticMarkup(React.createElement(HumanInbox, { run, agents: [], readOnly: true }));
    assert.doesNotMatch(html, /<button/);
  } finally { await dom.unmount(); }
});


test("human inbox keeps unread issues first and read solutions visible without response controls", async () => {
  const { HumanInbox } = await viteServer.ssrLoadModule("/src/components/SwarmWorkspace.jsx");
  const run = { humanInbox: [
    { id: "old", state: "addressed", description: "Old problem", instruction: "Inspect the draft", createdAt: "2026-09-16" },
    { id: "read", state: "addressed", description: "Solved problem", nextSteps: "Keep the existing workspace", createdAt: "2026-09-18" },
    { id: "pending", state: "pending", description: "Waiting problem", recommendedAction: "Review the diff", createdAt: "2026-09-17" },
  ] };
  const dom = await mountReact(React.createElement(HumanInbox, { run, agents: [] }), createFetchMock());
  try {
    const articles = allElements(dom.container).filter(el => el.tagName === "ARTICLE");
    assert.match(articles[0].textContent, /Waiting problem/);
    assert.match(articles[1].textContent, /Read.*Solved problem.*Next steps:.*Keep the existing workspace/);
    assert.match(articles[2].textContent, /Next steps:.*Inspect the draft/);
    assert.equal(allElements(articles[1]).filter(el => el.tagName === "BUTTON").length, 0);
    assert.equal(allElements(articles[2]).filter(el => el.tagName === "BUTTON").length, 0);
    assert.deepEqual(run.humanInbox.map(item => item.id), ["old", "read", "pending"]);
  } finally { await dom.unmount(); }
});

test("human inbox cancels draft instructions before proceeding with the recommendation", async () => {
  const { HumanInbox } = await viteServer.ssrLoadModule("/src/components/SwarmWorkspace.jsx");
  const calls = [];
  const run = { humanInbox: [{ id: "request", state: "pending", recommendedAction: "Review" }] };
  const dom = await mountReact(React.createElement(HumanInbox, { run, agents: [], onAction: value => calls.push(value) }), createFetchMock());
  try {
    await dom.click(dom.byText("Do something else"));
    await dom.change(dom.first("textarea"), "Abandoned instruction");
    await dom.click(dom.byText("Cancel"));
    await dom.pointer(dom.first("form"), "onSubmit");
    assert.equal(calls[0].decision, "proceed");
    assert.equal(calls[0].instruction, "");
  } finally { await dom.unmount(); }
});

test("agent workspace selector chooses another project and restores the swarm default", async () => {
  const { AgentWorkspaceField } = await viteServer.ssrLoadModule("/src/components/SwarmWorkspace.jsx");
  const calls = [];
  function Field() {
    const [value, setValue] = React.useState("");
    return React.createElement(AgentWorkspaceField, { value, rootPath: "/desktop", projectPaths: ["/desktop", "/mobile", "/mobile"], onChange: path => { calls.push(path); setValue(path); } });
  }
  const dom = await mountReact(React.createElement(Field), createFetchMock());
  try {
    assert.equal(dom.first("select").options.length, 2);
    assert.equal(reactProps(dom.first("select")).value, "");
    await dom.change(dom.first("select"), "/mobile");
    assert.equal(reactProps(dom.first("select")).value, "/mobile");
    await dom.change(dom.first("select"), "");
    assert.deepEqual(calls, ["/mobile", ""]);
  } finally { await dom.unmount(); }
  const html = renderToStaticMarkup(React.createElement(AgentWorkspaceField, { value: "/saved-project", rootPath: "/desktop", projectPaths: [], disabled: true }));
  assert.match(html, /disabled/);
  assert.match(html, /value="\/saved-project" selected/);
});

test("swarm agent settings save workspace selections without changing other agents", async () => {
  const { SwarmSettings } = await viteServer.ssrLoadModule("/src/components/SwarmWorkspace.jsx");
  const calls = [];
  const swarm = { name: "Apps", agents: [
    { id: "lead", name: "Desktop", role: "Desktop", provider: "codex", isOrchestrator: true },
    { id: "mobile", name: "Mobile", role: "Mobile", provider: "codex" },
  ] };
  const dom = await mountReact(React.createElement(SwarmSettings, { swarm, selectedAgentId: "mobile", rootPath: "/desktop", projectPaths: ["/mobile"], onSave: payload => calls.push(payload) }), createFetchMock());
  try {
    const workspace = dom.controlAfterLabel("Workspace project");
    await dom.change(workspace, "/mobile");
    await dom.pointer(dom.first("form"), "onSubmit");
    assert.equal(calls[0].agents.find(agent => agent.id === "mobile").workspacePath, "/mobile");
    assert.equal(calls[0].agents.find(agent => agent.id === "lead").workspacePath, undefined);
  } finally { await dom.unmount(); }
});

test("Rem default scope validates and persists the global setting", () => {
  assert.equal(settingsModule.normalizeAppSettings({}).assistant.defaultScope, "current-directory");
  assert.equal(settingsModule.normalizeAppSettings({ assistant: { defaultScope: "invalid" } }).assistant.defaultScope, "current-directory");
  assert.equal(settingsModule.normalizeAppSettings({ assistant: { defaultScope: "global" } }).assistant.defaultScope, "global");
});

test("Ctrl+Enter starts background threads while Enter opens the new thread", async () => {
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [{ id: "codex", available: true, models: [] }] }),
    url => url === "/api/chat/stream" ? streamResponse(['{"type":"final","message":{"body":"Done"}}\n'])(url) : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    activeProjectRoot: "/projects/mobile", recentProjectRoots: ["/projects/mobile"], workflows: [], width: 380,
  }), fetchMock);
  await dom.flush();
  await dom.change(dom.first("textarea"), "Background work");
  await dom.keyDown(dom.first("textarea"), "Enter", { ctrlKey: true });
  await dom.flush();
  assert.throws(() => dom.byLabel("Back to active threads"));
  assert.ok(dom.byText("Background work"));
  await dom.change(dom.first("textarea"), "Foreground work");
  await dom.keyDown(dom.first("textarea"), "Enter");
  await dom.flush();
  assert.ok(dom.byLabel("Back to active threads"));
  const requests = fetchMock.calls.filter(call => call.url === "/api/chat/stream").map(call => JSON.parse(call.options.body));
  assert.equal(requests.length, 2);
  assert.notEqual(requests[0].conversationId, requests[1].conversationId);
  assert.equal(requests[0].workflow.projectRoot, "/projects/mobile");
  await dom.unmount();
});

test("global Rem scope becomes the selected project before the final response", async () => {
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [{ id: "codex", available: true, models: [] }] }),
    url => url === "/api/chat/stream" ? streamResponse([
      '{"type":"project-scope","projectRoot":"/projects/mobile","projectName":"Mobile"}\n',
      '{"type":"final","message":{"body":"Changed mobile"}}\n',
    ])(url) : null,
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    activeProjectRoot: "/projects/desktop", recentProjectRoots: ["/projects/mobile", "/projects/desktop"],
    assistantDefaults: { defaultScope: "global" }, workflows: [], width: 380,
  }), fetchMock);
  await dom.flush();
  assert.ok(dom.byLabel("Scoped to Global. Change project scope"));
  await dom.change(dom.first("textarea"), "Fix the mobile app");
  await dom.keyDown(dom.first("textarea"), "Enter"); await dom.flush();
  const request = JSON.parse(fetchMock.calls.find(call => call.url === "/api/chat/stream").options.body);
  assert.equal(request.workflow.projectRoot, "");
  assert.equal(request.workflow.remThreads.global, true);
  assert.deepEqual(request.workflow.remThreads.projects.map(project => project.root).sort(), ["/projects/desktop", "/projects/mobile"]);
  assert.ok(dom.byLabel("Scoped to Mobile. Change project scope"));
  await dom.click(dom.byLabel("Back to active threads"));
  assert.ok(dom.byLabel("Scoped to Global. Change project scope"));
  await dom.unmount();
});

test("Rem new-thread events start one scoped child and keep the parent visible", async () => {
  let count = 0;
  const fetchMock = createFetchMock([
    jsonResponse("/api/provider/capabilities", { providers: [{ id: "codex", available: true, models: [] }] }),
    url => {
      if (url !== "/api/chat/stream") return null;
      count += 1;
      const event = '{"type":"new-thread","threadId":"child-mobile","projectRoot":"/projects/mobile","message":"Run 7 rounds with 6 agents against BTC"}\n';
      return streamResponse(count === 1 ? [event, event, '{"type":"final","message":{"body":"Started"}}\n'] : ['{"type":"final","message":{"body":"Experiment complete"}}\n'])(url);
    },
  ]);
  const dom = await mountReact(React.createElement(appModule.ChatPane, {
    activeProjectRoot: "/projects/desktop", recentProjectRoots: ["/projects/mobile"], workflows: [], width: 380,
  }), fetchMock);
  await dom.flush();
  await dom.change(dom.first("textarea"), "Start a new thread in mobile and run an experiment");
  await dom.keyDown(dom.first("textarea"), "Enter"); await dom.flush();
  const requests = fetchMock.calls.filter(call => call.url === "/api/chat/stream").map(call => JSON.parse(call.options.body));
  assert.equal(requests.length, 2);
  assert.equal(requests[1].conversationId, "child-mobile");
  assert.equal(requests[1].workflow.projectRoot, "/projects/mobile");
  assert.equal(requests[1].workflow.remThreads.spawned, true);
  assert.equal(requests[1].messages.length, 1);
  assert.equal(requests[1].messages[0].body, "Run 7 rounds with 6 agents against BTC");
  assert.equal(requests[1].provider, requests[0].provider);
  assert.ok(dom.byLabel("Scoped to desktop. Change project scope"));
  await dom.unmount();
});
