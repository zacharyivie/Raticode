import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import CodeWorkspace from "../src/components/CodeWorkspace.jsx";
import CodeFileExplorer from "../src/components/CodeFileExplorer.jsx";
import { applyCodeFilesystemChange } from "../src/lib/codeEditorSessions.js";
import { replacePathPrefix } from "../src/lib/workspacePaths.js";
import "../src/styles/index.css";

const root = new URLSearchParams(location.search).get("root");
function Fixture() {
  const [project, setProject] = useState(`${root}/a`);
  const [paths, setPaths] = useState([]);
  const [activePath, setActivePath] = useState("");
  window.selectFixtureProject = setProject;
  window.openFixtureFiles = openFiles;
  window.fixturePaths = paths;
  function openFiles(incoming) {
    setPaths(current => [...new Set([...current, ...incoming])]);
    setActivePath(incoming.at(-1));
  }
  function changed(change) {
    applyCodeFilesystemChange(change);
    if (change.kind !== "rename") return;
    setPaths(current => current.map(path => replacePathPrefix(path, change.sourcePath, change.path, change.isDirectory)));
    setActivePath(current => replacePathPrefix(current, change.sourcePath, change.path, change.isDirectory));
  }
  return <main className="flex" style={{ height: "100vh" }}>
    <input aria-label="Native drop fixture" type="file" multiple hidden />
    <aside className="w-80 shrink-0"><CodeFileExplorer hideProjectSelector workflow={{ projectRoot: project }} onOpenFile={path => openFiles([path])} onFilesystemChange={changed} /></aside>
    <CodeWorkspace active activePath={activePath} openPaths={paths} onActivePathChange={setActivePath} onOpenPathsChange={setPaths} onClosePath={path => setPaths(current => current.filter(value => value !== path))} onDropPaths={openFiles} />
  </main>;
}
createRoot(document.getElementById("root")).render(<Fixture />);
