import React, { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { TextCodeEditor } from "../src/components/CodeWorkspace.jsx";
import { loadRattishMonaco } from "../src/lib/monaco.js";
import "../src/styles/index.css";

const metrics = { updates: 0, gitCalls: 0, saved: "" };
const text = "a".repeat(100) + "\n";
const content = text.repeat(20000);
window.goferDesktop = {
  workspace: { gitFileBaseline: async () => { metrics.gitCalls++; return { tracked: false, changed: false }; } },
  textFiles: { read: async () => ({ content }), write: async ({ content: value }) => { metrics.saved = value; } },
};
function Fixture() {
  const ref = useRef(null);
  const [active, setActive] = useState(true);
  window.editorPerf = { ...window.editorPerf, ref, metrics, hide: () => setActive(false) };
  return <div style={{ height: 700, display: "flex" }}><TextCodeEditor ref={ref} path="/generated/performance.txt" active={active} autosaveEnabled={false} theme="light" onStateChange={(state) => {
    metrics.updates++;
    metrics.dirty = state.dirty;
    if (!state.loading) {
      const monaco = loadRattishMonaco();
      window.editorPerf.model = monaco.editor.getModels()[0];
      window.editorPerf.editor = monaco.editor.getEditors()[0];
      window.perfReady = true;
    }
  }} /></div>;
}
createRoot(document.getElementById("root")).render(<Fixture />);
