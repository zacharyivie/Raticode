import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import CommitDiff from "../src/components/CommitDiff.jsx";
import { loadRattishMonaco } from "../src/lib/monaco.js";
import "../src/styles/index.css";

const hash = "a".repeat(40);
const file = (path, original, modified, extra = {}) => ({ path, oldPath: path, status: "M", oldMode: "100644", newMode: "100644", original, modified, ...extra });
const files = [
  file("src/app.py", 'def greet(name):\n    return "Hello " + name\n', 'def greet(name):\n    # Personalized greeting\n    return f"Hello, {name}!"\n'),
  file("src/app.tsx", 'export const count: number = 1;\n', 'export const count: number = 2;\n'),
  file("config.json", '{"enabled": false}\n', '{"enabled": true}\n'),
  file("new.py", "value = 1\n", "value = 1\n", { oldPath: "old.py", status: "R" }),
  file("removed.js", "const obsolete = true;\n", "", { status: "D" }),
  file("image.bin", null, null, { binary: true }),
  file("new.sh", "", "#!/bin/sh\necho hello\n", { status: "A" }),
  file("large.py", Array.from({ length: 160 }, (_, i) => `value_${i} = ${i}\n`).join(""), Array.from({ length: 160 }, (_, i) => `value_${i} = ${i === 80 ? 900 : i}\n`).join("")),
];
window.goferDesktop = { workspace: { gitRepoAction: async () => ({ hash, parentHash: "b".repeat(40), subject: "Review changed files", files }) } };
window.commitDiffMonaco = loadRattishMonaco();
window.liveDiffViewModels = new Set();
window.commitDiffMonaco.editor.onDidCreateDiffEditor(editor => {
  const createViewModel = editor.createViewModel.bind(editor);
  editor.createViewModel = model => {
    const viewModel = createViewModel(model);
    window.liveDiffViewModels.add(viewModel);
    const dispose = viewModel.dispose.bind(viewModel);
    viewModel.dispose = () => { window.liveDiffViewModels.delete(viewModel); dispose(); };
    return viewModel;
  };
});
function Fixture() {
  const [open, setOpen] = useState(true);
  const [theme, setTheme] = useState("dark");
  useEffect(() => { document.documentElement.classList.toggle("dark", theme === "dark"); }, [theme]);
  window.closeDiff = () => setOpen(false);
  window.lightTheme = () => setTheme("light");
  return <div className={theme === "dark" ? "dark" : ""} style={{ height: "100vh", display: "flex" }}>
    <div className="flex min-w-0 flex-1 bg-canvas text-ink">{open ? <CommitDiff projectRoot="/fixture" hash={hash} theme={theme} editorSettings={{ fontSize: 13, lineHeight: 20, tabSize: 4 }} /> : null}</div>
  </div>;
}
createRoot(document.getElementById("root")).render(<Fixture />);
