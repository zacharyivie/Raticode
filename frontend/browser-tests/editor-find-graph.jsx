import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import CodeWorkspace from "../src/components/CodeWorkspace.jsx";
import CodeFileExplorer from "../src/components/CodeFileExplorer.jsx";
import { DEFAULT_APP_SETTINGS, updateSetting } from "../src/lib/settings.js";
import "../src/styles/index.css";

// Exercise Raticode's Mac shortcut routing independently of the host OS.
Object.defineProperty(navigator, "platform", { configurable: true, value: "MacIntel" });
const refs = [
  { id: "refs/heads/main", name: "main", type: "local", current: true, upstream: "origin/main", ahead: 1, behind: 0 },
  { id: "refs/remotes/origin/main", name: "origin/main", type: "remote" },
  { id: "refs/tags/v0.3.8", name: "v0.3.8", type: "tag" },
  { id: "refs/heads/coworker", name: "coworker", type: "local" },
  { id: "refs/remotes/origin/coworker", name: "origin/coworker", type: "remote" },
];
const commits = [
  { hash: "merge", shortHash: "abc1201", subject: "Merge editor search changes", parents: ["local", "remote"], labels: [refs[0]], isHead: true },
  { hash: "remote", shortHash: "def2202", subject: "Fix remote sync", parents: ["base"], labels: [refs[1]] },
  { hash: "local", shortHash: "fab3203", subject: "Add configurable Find", message: "Add configurable Find\n\nKeep search within the active editor.", parents: ["base"], labels: [] },
  { hash: "base", shortHash: "dab4204", subject: "Release Raticode", parents: [], labels: [refs[2]] },
].map(commit => ({ author: "Ada", authoredAt: "2026-09-30T12:00:00Z", ...commit }));
let fileExists = true;
let projectExists = true;
let projectGrant = "";
window.generationJobRequests = [];
window.commitPollingError = "";
window.expireFixtureGrant = () => { projectGrant = "expired"; };
window.deleteFixtureProject = () => { projectExists = false; };
window.historyCalls = [];
window.closedFiles = [];
window.unhandled = [];
window.addEventListener("unhandledrejection", event => window.unhandled.push({ message: String(event.reason), stack: event.reason?.stack, phase: window.fixturePhase }));
window.fileWrites = 0;
window.deletedComparisonDiskReads = [];
window.fetch = async url => {
  const query = new URL(url, window.location.href).searchParams;
  if (query.get("kind") === "commit") {
    window.generationJobRequests.push({ root: query.get("projectRoot"), grant: query.get("grantId") });
    const error = window.commitPollingError || (projectGrant !== "registered" ? "Bundle path is outside the approved Raticode desktop roots" : "");
    return { ok: !error, json: async () => error ? { error } : { jobs: [] } };
  }
  return { ok: true, json: async () => ({ jobs: [] }) };
};
window.goferDesktop = {
  workspace: {
    trustProjectRoot: async () => {
      if (!projectExists) throw Object.assign(new Error("Path does not exist: /fixture"), { code: "ENOENT" });
      await new Promise(resolve => setTimeout(resolve, 50));
      projectGrant = "registered";
    },
    pathGrantForApi: () => projectGrant,
    listDirectory: async () => ({ entries: [] }),
    gitStatus: async () => ({ active: projectExists, entries: [], branch: "main" }),
    gitFileBaseline: async () => ({ tracked: false, changed: false }),
    gitHistory: async (_root, options) => {
      window.historyCalls.push(options);
      const tips = new Map([[refs[0].id, "merge"], [refs[1].id, "remote"], [refs[2].id, "base"], [refs[3].id, "local"], [refs[4].id, "local"], ["HEAD", "merge"]]);
      const reachable = new Set();
      function visit(hash) {
        if (reachable.has(hash)) return;
        reachable.add(hash);
        commits.find(commit => commit.hash === hash)?.parents.forEach(visit);
      }
      if (options.refs == null) commits.forEach(commit => visit(commit.hash));
      else options.refs.forEach(ref => visit(tips.get(ref)));
      return { active: true, refs, commits: commits.filter(commit => reachable.has(commit.hash)) };
    },
    getPathInfo: async () => ({ exists: fileExists }),
    gitWorktrees: async () => ({ active: true, worktrees: [] }),
  },
  textFiles: { write: async () => { window.fileWrites++; return {}; }, read: async path => path.endsWith("missing.js") ? { missing: true, content: null } : ({ content: "const findMe = 'search works';\nconsole.log(findMe);\n" }) },
};
function Fixture() {
  const [settings, setSettings] = useState(DEFAULT_APP_SETTINGS);
  const [paths, setPaths] = useState(["/fixture/sample.js"]);
  const [navigation, setNavigation] = useState(null);
  window.deleteFixtureFile = () => {
    window.fixturePhase = "deleting";
    fileExists = false;
    window.dispatchEvent(new CustomEvent("gofer:git-files-changed", { detail: { rootPath: "/fixture" } }));
  };
  window.restoreFixtureFile = () => {
    window.fixturePhase = "restoring";
    fileExists = true;
    window.goferDesktop.textFiles.read = async () => ({ content: "fresh disk content" });
    setPaths(["/fixture/sample.js"]);
  };
  window.openDeletedComparison = () => {
    window.fixturePhase = "comparison";
    window.goferDesktop.workspace.getPathInfo = async path => { window.deletedComparisonDiskReads.push(path); return { exists: false }; };
    window.goferDesktop.textFiles.read = async path => { window.deletedComparisonDiskReads.push(path); return { missing: true, content: null }; };
    window.goferDesktop.workspace.gitFileBaseline = async () => ({ tracked: true, changed: true, deleted: true, content: "original deleted file", modifiedContent: "", hunks: [] });
    setNavigation({ path: "/fixture/deleted.js", diff: true, gitGroup: "unstaged" });
    setPaths(["/fixture/deleted.js"]);
  };
  window.openMissingFile = () => {
    window.goferDesktop.textFiles.read = async () => ({ missing: true, content: null });
    window.goferDesktop.workspace.getPathInfo = undefined;
    setPaths(["/fixture/missing.js"]);
  };
  window.setFindBinding = binding => setSettings(current => updateSetting(current, "keybindings.editor.find", binding));
  return <div style={{ display: "flex", height: "100vh" }}>
    <div style={{ width: 330, flexShrink: 0 }}><CodeFileExplorer sidebarView="source-control" hideProjectSelector workflow={{ projectRoot: "/fixture" }} /></div>
    <div style={{ flex: 1, minWidth: 0, display: "flex" }}><CodeWorkspace active navigationRequest={navigation} activePath={paths[0]} openPaths={paths} onClosePath={path => { window.closedFiles.push(path); setPaths(current => current.filter(item => item !== path)); }} workflow={{ projectRoot: "/fixture" }} theme="light" settings={settings} /></div>
  </div>;
}
createRoot(document.getElementById("root")).render(<Fixture />);
