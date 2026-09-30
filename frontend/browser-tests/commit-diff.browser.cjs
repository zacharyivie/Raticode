/* global __dirname, process, console, clearTimeout, window, document, setTimeout */
const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const deadline = setTimeout(() => app.exit(1), 60000);
let server;
app.whenReady().then(async () => {
  const { createServer } = await import("vite");
  server = await createServer({ root: path.resolve(__dirname, ".."), configFile: false,
    server: { host: "127.0.0.1", port: 0 }, plugins: [
      { name: "commit-diff-fixture", configureServer(dev) {
        dev.middlewares.use(async (req, res, next) => {
          if (req.url !== "/") return next();
          res.setHeader("Content-Type", "text/html");
          res.end(await dev.transformIndexHtml("/", '<html><body style="margin:0"><div id="root"></div><script type="module" src="/browser-tests/commit-diff.jsx"></script></body></html>'));
        });
      } }, (await import("@vitejs/plugin-react")).default(),
    ] });
  await server.listen();
  const win = new BrowserWindow({ width: 1100, height: 800, show: true, webPreferences: { contextIsolation: true, sandbox: false } });
  const errors = [];
  win.webContents.on("console-message", ({ level, message }) => { if (level === "error") { errors.push(message); console.error(message); } });
  await win.loadURL(server.resolvedUrls.local[0]);
  const evaluate = async fn => {
    try { return await win.webContents.executeJavaScript(`(${fn})()`); }
    catch (error) { console.error(`Renderer check failed: ${fn}`); throw error; }
  };
  const until = async fn => {
    for (let i = 0; i < 150; i++) {
      if (await evaluate(fn)) return;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    console.error(await evaluate(() => ({ models: window.commitDiffMonaco?.editor.getModels().map(model => ({ uri: model.uri.toString(), disposed: model.isDisposed() })), editors: window.commitDiffMonaco?.editor.getDiffEditors().length, body: document.body.textContent.slice(-1000) })), errors);
    throw new Error(`Timed out: ${fn}`);
  };
  await until(() => window.commitDiffMonaco?.editor.getDiffEditors().length >= 2 && document.querySelectorAll(".view-line .mtk6, .view-line .mtk1").length > 0);
  await evaluate(() => { document.querySelector('[aria-label="Changed files"]').scrollTop = 300; });
  await until(() => window.commitDiffMonaco.editor.getModels().some(model => model.getLanguageId() === "json"));
  await evaluate(() => { document.querySelector('[aria-label="Changed files"]').scrollTop = 0; });
  await new Promise(resolve => setTimeout(resolve, 500));
  const first = await evaluate(() => {
    const monaco = window.commitDiffMonaco;
    const diff = monaco.editor.getDiffEditors().find(item => !item.getModel()?.original.isDisposed() && item.getModel()?.original.getValue().startsWith("def greet"));
    const original = diff.getOriginalEditor(), modified = diff.getModifiedEditor();
    const left = original.getDomNode().getBoundingClientRect(), right = modified.getDomNode().getBoundingClientRect();
    const scroll = document.querySelector('[aria-label="Changed files"]');
    return { sideBySide: left.right <= right.left + 5,
      readOnly: original.getOption(monaco.editor.EditorOption.readOnly) && modified.getOption(monaco.editor.EditorOption.readOnly),
      languages: monaco.editor.getModels().map(model => model.getLanguageId()),
      tokenColors: [...new Set([...original.getDomNode().querySelectorAll(".view-line span")].map(el => window.getComputedStyle(el).color))],
      scrolls: scroll.scrollHeight > scroll.clientHeight,
      original: original.getValue(), modified: modified.getValue(),
      editorFits: original.getScrollHeight() <= original.getLayoutInfo().height + 1,
    };
  });
  assert.ok(first.sideBySide);
  assert.ok(first.readOnly);
  assert.ok(first.languages.includes("python") && first.languages.includes("typescript") && first.languages.includes("json"));
  assert.ok(first.tokenColors.length >= 3, "Python syntax uses distinct colors");
  assert.ok(first.scrolls && first.editorFits, "Commit owns vertical scrolling");
  assert.match(first.original, /return "Hello "/);
  assert.match(first.modified, /Personalized greeting/);
  // Wheel events over Monaco should scroll to the next files, not become trapped.
  const wheelY = await evaluate(() => Math.round(document.querySelector('[aria-label="Changed files"]').getBoundingClientRect().top + 100));
  win.webContents.sendInputEvent({ type: "mouseWheel", x: 450, y: wheelY, deltaY: -450, canScroll: true });
  await until(() => document.querySelector('[aria-label="Changed files"]').scrollTop > 0);
  for (let index = 0; index < 8; index++) {
    await win.webContents.executeJavaScript(`document.querySelectorAll('article[aria-label^="Changed file"]')[${index}].scrollIntoView({ block: "start" })`);
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  await until(() => window.commitDiffMonaco.editor.getDiffEditors().length === 7);
  await until(() => window.commitDiffMonaco.editor.getDiffEditors().every(diff => diff.getLineChanges() !== null));
  const bottom = await evaluate(() => ({ text: document.body.textContent,
    compactHeight: window.commitDiffMonaco.editor.getDiffEditors().find(item => !item.getModel()?.original.isDisposed() && item.getModel()?.original.getValue().startsWith("value_0")).getModifiedEditor().getLayoutInfo().height }));
  assert.match(bottom.text, /Binary file/);
  assert.match(bottom.text, /old.py → new.py/);
  assert.match(bottom.text, /File deleted/);
  assert.match(bottom.text, /File did not exist/);
  assert.ok(bottom.compactHeight < 1500, "Long unchanged regions are folded");
  const input = async (label, value) => {
    await win.webContents.executeJavaScript(`(() => {
      const field = document.querySelector('input[aria-label="' + ${JSON.stringify(label)} + '"]');
      field.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(field, ${JSON.stringify(value)});
      field.dispatchEvent(new Event("input", { bubbles: true }));
    })()`);
  };
  const enter = async (shiftKey = false) => win.webContents.executeJavaScript(`document.querySelector('input[aria-label="Search diff"]').dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", shiftKey: ${shiftKey}, bubbles: true }))`);
  // Type five exclusions at human speed. Partial '*' patterns must not repeatedly
  // destroy and rebuild comparisons that survive the completed filter.
  await evaluate(() => {
    window.filterEditorCreates = 0;
    window.filterCreateListener = window.commitDiffMonaco.editor.onDidCreateEditor(() => window.filterEditorCreates++);
    window.filterStarted = window.performance.now();
    window.filterOriginalModel = window.commitDiffMonaco.editor.getModels().find(model => model.getValue().startsWith("def greet"));
  });
  const exclusions = "*.tsx, *.json, *.js, *.bin, *.sh";
  for (let index = 1; index <= exclusions.length; index++) {
    await input("Files to exclude", exclusions.slice(0, index));
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  await until(() => document.querySelectorAll('article[aria-label^="Changed file"]').length === 3);
  const filterPerformance = await evaluate(() => {
    window.filterCreateListener.dispose();
    return { editorCreates: window.filterEditorCreates, retainedModel: !window.filterOriginalModel.isDisposed(), elapsedMs: Math.round(window.performance.now() - window.filterStarted) };
  });
  console.log("Five typed exclusions:", filterPerformance);
  assert.equal(filterPerformance.editorCreates, 0, "Typing exclusions reuses surviving editors");
  assert.ok(filterPerformance.retainedModel, "An included file keeps its existing model");
  await evaluate(() => document.querySelector('.commit-diff-reset').click());
  await until(() => document.querySelectorAll('article[aria-label^="Changed file"]').length === 8);
  await input("Files to exclude", "*.bin");
  await evaluate(() => document.activeElement.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  assert.ok(await evaluate(() => document.querySelector('[aria-label="Changed files"]').getAttribute("aria-busy") === "false" && document.querySelectorAll('article[aria-label^="Changed file"]').length === 7), "Enter applies without waiting for the debounce");
  await input("Files to exclude", "*");
  await evaluate(() => document.querySelector('.commit-diff-reset').click());
  await new Promise(resolve => setTimeout(resolve, 250));
  assert.equal(await evaluate(() => document.querySelectorAll('article[aria-label^="Changed file"]').length), 8, "Reset cancels pending exclusions");
  await input("Files to include", "*.py");
  await input("Files to exclude", "large.py, old.py");
  await until(() => document.querySelectorAll('article[aria-label^="Changed file"]').length === 1);
  assert.ok(await evaluate(() => Boolean(document.querySelector('article[aria-label="Changed file src/app.py"]'))));
  // Clear and retype while focused: the draft must not snap back or normalize.
  await input("Files to exclude", "");
  assert.equal(await evaluate(() => document.activeElement.value), "");
  await input("Files to exclude", "  large.py, old.py  ");
  await evaluate(() => document.activeElement.blur());
  assert.equal(await evaluate(() => document.querySelector('input[aria-label="Files to exclude"]').value), "  large.py, old.py  ");
  await input("Search diff", "HELLO");
  await until(() => document.querySelectorAll(".commit-diff-match").length >= 2);
  assert.match(await evaluate(() => document.querySelector('.commit-diff-count').textContent), /1 of 8 files/);
  await enter();
  await until(() => document.querySelector('.commit-diff-match-count').textContent === "1 of 2" && document.querySelector('.commit-diff-current-match'));
  assert.equal(await evaluate(() => document.activeElement.getAttribute("aria-label")), "Search diff", "Navigation retains search focus");
  await enter();
  await until(() => document.querySelector('.commit-diff-match-count').textContent === "2 of 2");
  await enter();
  await until(() => document.querySelector('.commit-diff-match-count').textContent === "1 of 2");
  await enter(true);
  await until(() => document.querySelector('.commit-diff-match-count').textContent === "2 of 2");
  await until(() => document.querySelector('.commit-diff-overview i.is-current'));
  assert.ok(await evaluate(() => window.getComputedStyle(document.querySelector('.commit-diff-overview i')).backgroundColor === window.getComputedStyle(document.querySelector('.commit-diff-match')).backgroundColor), "Scrollbar markers use the text highlight color");
  await input("Search diff", "not-in-this-commit");
  await until(() => document.body.textContent.includes("No files match"));
  await enter();
  assert.equal(await evaluate(() => document.querySelector('.commit-diff-match-count').textContent), "0 of 0");
  assert.equal(await evaluate(() => document.querySelectorAll('.commit-diff-overview i').length), 0);
  await until(() => window.commitDiffMonaco.editor.getModels().length === 0);
  await evaluate(() => document.querySelector('.commit-diff-empty button').click());
  await until(() => document.querySelectorAll('article[aria-label^="Changed file"]').length === 8);
  assert.ok(await evaluate(() => [...document.querySelectorAll('.commit-diff-tools input')].every(field => field.value === "")));
  await input("Search diff", "HELLO");
  await enter(true);
  await until(() => document.querySelector('.commit-diff-match-count').textContent === "3 of 3" && document.querySelector('article[aria-label="Changed file new.sh"] .commit-diff-current-match'));
  await evaluate(() => document.querySelector('[aria-label="Next match"]').click());
  await until(() => document.querySelector('.commit-diff-match-count').textContent === "1 of 3" && document.querySelector('article[aria-label="Changed file src/app.py"] .commit-diff-current-match'));
  await input("Search diff", "value_0");
  await until(() => document.querySelectorAll(".commit-diff-match").length >= 2);
  assert.ok(await evaluate(() => window.commitDiffMonaco.editor.getDiffEditors().find(item => !item.getModel()?.original.isDisposed() && item.getModel()?.original.getValue().startsWith("value_0")).getModifiedEditor().getLayoutInfo().height > 1500), "Search reveals unchanged text");
  // A match near the end of an offscreen editor must be scrolled into view.
  await input("Search diff", "value_159");
  await enter();
  await until(() => {
    const hit = document.querySelector('.commit-diff-current-match');
    const scroll = document.querySelector('[aria-label="Changed files"]').getBoundingClientRect();
    const rect = hit?.getBoundingClientRect();
    return rect && rect.top >= scroll.top && rect.bottom <= scroll.bottom;
  });
  await until(() => {
    const marker = document.querySelector('.commit-diff-overview i.is-current');
    return marker && parseFloat(marker.style.top) > document.querySelector('.commit-diff-overview').clientHeight * .85;
  });
  await input("Search diff", "value_");
  await until(() => document.querySelector('.commit-diff-match-count').textContent === "0 of 320");
  await until(() => document.querySelectorAll('.commit-diff-overview i').length > 50);
  assert.ok(await evaluate(() => document.querySelectorAll('.commit-diff-overview i').length <= document.querySelector('.commit-diff-overview').clientHeight), "Markers coalesce by scrollbar pixel");
  if (process.env.GOFER_COMMIT_DIFF_SCREENSHOT) fs.writeFileSync(process.env.GOFER_COMMIT_DIFF_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
  await input("Files to exclude", "*.py");
  await until(() => document.querySelector('.commit-diff-match-count').textContent === "0 of 0");
  assert.equal(await evaluate(() => document.querySelectorAll('.commit-diff-overview i').length), 0, "Exclusions remove search markers");
  await evaluate(() => document.querySelector('.commit-diff-reset').click());
  await input("Search diff", ".py");
  await enter();
  await until(() => document.querySelector('mark.is-current'));
  await input("Search diff", "image.bin");
  await enter();
  await until(() => document.querySelector('mark.is-current')?.textContent === "image.bin");
  await evaluate(() => document.querySelector('.commit-diff-reset').click());
  await until(() => window.commitDiffMonaco.editor.getDiffEditors().some(item => !item.getModel()?.original.isDisposed() && item.getModel()?.original.getValue().startsWith("def greet")));
  await evaluate(() => { document.querySelector('[aria-label="Changed files"]').scrollTop = 0; });
  await new Promise(resolve => setTimeout(resolve, 200));
  win.setSize(600, 800);
  await new Promise(resolve => setTimeout(resolve, 300));
  assert.ok(await evaluate(() => {
    const diff = window.commitDiffMonaco.editor.getDiffEditors().find(item => !item.getModel()?.original.isDisposed() && item.getModel()?.original.getValue().startsWith("def greet"));
    return diff.getOriginalEditor().getDomNode().getBoundingClientRect().right <= diff.getModifiedEditor().getDomNode().getBoundingClientRect().left + 5;
  }), "Narrow tabs retain side-by-side comparisons");
  assert.ok(await evaluate(() => [...document.querySelectorAll('.commit-diff-tools input')].every(field => {
    const rect = field.getBoundingClientRect(); return rect.width > 80 && rect.right <= window.innerWidth;
  })), "Search and filters fit narrow tabs");
  await input("Search diff", "Hello");
  await enter();
  await until(() => document.querySelector('.commit-diff-current-match'));
  await evaluate(() => {
    window.diffDisposals = 0;
    window.expectedDisposals = window.commitDiffMonaco.editor.getEditors().length;
    window.commitDiffMonaco.editor.getEditors().forEach(diff => diff.onDidDispose(() => window.diffDisposals++));
    window.lightTheme();
  });
  await until(() => window.commitDiffMonaco.editor.getModels().length > 0);
  await until(() => document.querySelector('.commit-diff-current-match') && document.querySelector('.commit-diff-overview i'));
  assert.ok(await evaluate(() => window.getComputedStyle(document.querySelector('.commit-diff-overview i')).backgroundColor === window.getComputedStyle(document.querySelector('.commit-diff-match')).backgroundColor), "Light theme markers also match text highlights");
  await evaluate(() => window.closeDiff());
  await until(() => window.commitDiffMonaco.editor.getModels().length === 0 && window.commitDiffMonaco.editor.getEditors().length === 0 && window.diffDisposals === window.expectedDisposals);
  assert.equal(await evaluate(() => window.liveDiffViewModels.size), 0, "Filtering and closing release all diff computations");
  assert.deepEqual(errors, []);
  console.log("Commit diff browser checks passed: search, include/exclude, input drafts, highlights, empty/reset states, syntax colors, revisions, scrolling, narrow layout and disposal.");
  win.destroy();
  await server.close();
  clearTimeout(deadline);
  app.quit();
}).catch(async error => { console.error(error); if (server) await server.close(); app.exit(1); });
