import React, { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import CodeWorkspace from "../src/components/CodeWorkspace.jsx";
import CodeFileExplorer from "../src/components/CodeFileExplorer.jsx";
import IntegratedBrowser from "../src/components/IntegratedBrowser.jsx";
import { ApplicationMenus } from "../src/pages/App.jsx";
import { DEFAULT_APP_SETTINGS, updateSetting } from "../src/lib/settings.js";
import { runFocusedEdit } from "../src/lib/focusedEditing.js";
import { flushCodeDrafts } from "../src/lib/codeEditorSessions.js";
import "../src/styles/index.css";
Object.defineProperty(navigator, "platform", { configurable: true, value: "MacIntel" });
Object.defineProperty(navigator, "userAgent", { configurable: true, value: navigator.userAgent.replace(/X11; Linux x86_64/, "Macintosh; Intel Mac OS X 10_15_7") });
window.monacoFixture = (await import("../src/lib/monaco.js")).loadRattishMonaco();
window.flushDrafts = flushCodeDrafts;
window.fixtureReady = false;
function Fixture() {
  const ref = useRef(null);
  const [path, setPath] = useState("/fixture/sample.ts");
  const [settings, setSettings] = useState(() => updateSetting(DEFAULT_APP_SETTINGS, "general.autosave", false));
  const [fileState, setFileState] = useState(null);
  window.setFixturePath = setPath;
  window.setFixtureSetting = (name, value) => setSettings(current => updateSetting(current, name, value));
  window.runFixtureCommand = command => ref.current?.runCommand(command);
  window.fileStateFixture = fileState;
  return <div className="flex h-screen flex-col">
    <div className="flex h-10 shrink-0"><ApplicationMenus activeCodeDocument={fileState} activeCodePath={path} settings={settings} view="code" onAction={action => { if (!runFocusedEdit(action)) ref.current?.runCommand(action); }} recentProjectRoots={["/fixture"]} /></div>
    <input aria-label="Composer fixture" className="h-8 border" defaultValue="composer text" />
    <div className="flex min-h-0 flex-1">
      <div className="w-64 shrink-0"><CodeFileExplorer hideProjectSelector sidebarView="files" workflow={{ projectRoot: "/fixture" }} settings={settings} /></div>
      <div className="flex min-w-0 flex-1"><CodeWorkspace ref={ref} active activePath={path} openPaths={[path]} settings={settings} theme="light" onActiveDocumentStateChange={setFileState} /></div>
    </div>
    <div className="flex h-64 shrink-0"><IntegratedBrowser active clientId="fixture-browser" initialUrl={`${location.origin}/page`} /></div>
  </div>;
}
createRoot(document.getElementById("root")).render(<Fixture />);
window.fixtureReady = true;
