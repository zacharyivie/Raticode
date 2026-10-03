import assert from "node:assert/strict";
import { test } from "node:test";
import { normalizeAppSettings } from "./settings.js";
import { exportReportTheme, importReportTheme, MAX_REPORT_THEME_FILE_BYTES, normalizeReportThemes, reportThemeContext, reportPreviewDocument, generateReportTheme, reportThemeGenerationSelection } from "./reportThemes.js";

const companyTheme = { id: "custom-company", label: "Company / 海", instructions: "Use navy ink and our company colors. 海", html: "<!doctype html><html><body>Company preview 海</body></html>" };

test("theme files round-trip all design content without copying local settings or IDs", () => {
  const { filename, content } = exportReportTheme({ ...companyTheme, provider: "codex", private: "local-only" });
  assert.equal(filename, "Company.raticode-theme.json");
  assert.deepEqual(JSON.parse(content), { format: "raticode-report-theme", version: 1, theme: { label: companyTheme.label, instructions: companyTheme.instructions, html: companyTheme.html } });
  const current = { enabled: false, selected: companyTheme.id, custom: [companyTheme], generation: { provider: "claude_code", model: "local-model", effort: "high" } };
  const imported = importReportTheme(content, current);
  assert.equal(imported.enabled, false);
  assert.deepEqual(imported.generation, current.generation);
  assert.deepEqual(imported.custom[0], companyTheme);
  assert.equal(current.custom.length, 1);
  assert.notEqual(imported.selected, companyTheme.id);
  assert.deepEqual(imported.custom[1], { ...companyTheme, id: imported.selected });
  assert.deepEqual(normalizeReportThemes(imported), imported);
  const again = importReportTheme("\uFEFF" + content, imported);
  assert.equal(again.custom.length, 3);
  assert.notEqual(again.selected, imported.selected);
});

test("theme imports discard unrelated settings and supplied IDs", () => {
  const file = JSON.parse(exportReportTheme(companyTheme).content);
  file.theme.id = "custom-company";
  file.theme.generation = { provider: "codex" };
  file.enabled = true;
  const imported = importReportTheme(JSON.stringify(file), { enabled: false });
  assert.equal(imported.enabled, false);
  assert.deepEqual(imported.custom[0], { ...companyTheme, id: imported.selected });
  assert.deepEqual(imported.generation, { provider: "", model: "", effort: "" });
});

test("theme files reject malformed JSON, wrong formats, versions, and oversized files", () => {
  for (const content of ["not json", "null", "[]", "{}", JSON.stringify({ format: "another-app", version: 1, theme: companyTheme }), JSON.stringify({ format: "raticode-report-theme", version: 2, theme: companyTheme })]) {
    assert.throws(() => importReportTheme(content, {}), /valid JSON|Unsupported theme/);
  }
  assert.throws(() => importReportTheme("x".repeat(MAX_REPORT_THEME_FILE_BYTES + 1), {}), /2 MB/);
  assert.throws(() => importReportTheme("海".repeat(MAX_REPORT_THEME_FILE_BYTES / 2), {}), /2 MB/);
});

test("theme files require bounded, nonempty names, instructions, and previews", () => {
  for (const [field, limit] of [["label", 80], ["instructions", 12000], ["html", 200000]]) {
    for (const value of [undefined, null, 123, "", "   ", "x".repeat(limit + 1)]) {
      const theme = { ...companyTheme, [field]: value };
      assert.throws(() => exportReportTheme(theme), /must be nonempty text/);
      assert.throws(() => importReportTheme(JSON.stringify({ format: "raticode-report-theme", version: 1, theme }), {}), /must be nonempty text/);
    }
  }
  const theme = { ...companyTheme, label: "海".repeat(80), instructions: "海".repeat(12000), html: "海".repeat(200000) };
  assert.equal(exportReportTheme(theme).filename, "report-theme.raticode-theme.json");
  assert.equal(importReportTheme(exportReportTheme(theme).content, {}).custom[0].html, theme.html);
});

test("theme imports reject a full gallery without dropping saved themes", () => {
  const current = { custom: Array.from({ length: 24 }, (_, index) => ({ ...companyTheme, id: `custom-${index}` })) };
  assert.throws(() => importReportTheme(exportReportTheme(companyTheme).content, current), /24 custom/);
  assert.equal(current.custom.length, 24);
});

test("legacy themes migrate independently of knowledge enablement", () => {
  const settings = normalizeAppSettings({ memory: { secondBrainEnabled: false, secondBrainTheme: "blueprint" } });
  assert.equal(settings.memory.reportThemes.selected, "blueprint");
  assert.deepEqual(reportThemeContext(settings.memory), { enabled: true, theme: "blueprint", format: "md" });
  assert.equal(normalizeReportThemes({ selected: "missing" }).selected, "auto");
});

test("custom themes and provider settings survive normalization", () => {
  const config = { enabled: false, selected: "custom-ocean", custom: [{ id: "custom-ocean", label: "Ocean", instructions: "Navy ink", html: "<html>Demo</html>" }], generation: { provider: "codex", model: "chosen", effort: "high" } };
  assert.deepEqual(normalizeReportThemes(config), config);
  assert.deepEqual(reportThemeContext({ reportThemes: config }), { enabled: false, theme: "custom-ocean", format: "md", instructions: "Navy ink" });
  assert.ok(reportPreviewDocument(config.custom[0].html).includes("default-src 'none'"));
});

test("theme generation sends resolved provider, model, effort and screenshot", async () => {
  const oldWindow = globalThis.window, oldFetch = globalThis.fetch;
  globalThis.window = {};
  const screenshot = { name: "example.png", type: "image/png", data: "test" };
  globalThis.fetch = async (url, options) => {
    assert.equal(url, "/api/report-themes/generate");
    assert.deepEqual(JSON.parse(options.body), { provider: "codex", model: "chosen", effort: "high", description: "Ocean", screenshot });
    return { ok: true, json: async () => ({ label: "Ocean" }) };
  };
  try { assert.deepEqual(await generateReportTheme({ description: "Ocean", screenshot }, { provider: "codex", model: "chosen", effort: "high" }), { label: "Ocean" }); }
  finally { globalThis.window = oldWindow; globalThis.fetch = oldFetch; }
});


test("theme generation inherits all current choices and clears them for an override", () => {
  const current = { provider: "codex", model: "astra", effort: "high", permissionMode: "read-only" };
  assert.deepEqual(reportThemeGenerationSelection({}, current), current);
  assert.deepEqual(reportThemeGenerationSelection({ provider: "claude_code", model: "sonnet", effort: "low" }, current), { provider: "claude_code", model: "sonnet", effort: "low", permissionMode: undefined });
});

test("report output migrates legacy preferences and stays independent of knowledge and themes", () => {
  assert.equal(normalizeAppSettings({ memory: { secondBrainFormat: "html" } }).memory.reportFormat, "html");
  assert.equal(normalizeAppSettings({ memory: { reportFormat: "unknown" } }).memory.reportFormat, "md");
  for (const format of ["md", "html", "slides", "pdf"]) {
    const settings = normalizeAppSettings({ memory: { reportFormat: format, secondBrainEnabled: false, reportThemes: { enabled: false } } });
    assert.equal(settings.memory.reportFormat, format);
    assert.equal(reportThemeContext(settings.memory).format, format);
    assert.equal(reportThemeContext(settings.memory).enabled, false);
  }
});


test("theme stream handles fragmented progress and UTF-8, then returns only the draft", async () => {
  const oldWindow = globalThis.window, oldFetch = globalThis.fetch;
  globalThis.window = {};
  const draft = { label: "Ocean", html: "<html>Demo</html>", instructions: "Navy" };
  const bytes = new TextEncoder().encode([
    {type: "heartbeat"}, {type: "progress", text: "Palette 🌊"},
    {type: "progress", text: "Composing"}, {type: "final", theme: draft},
  ].map(event => JSON.stringify(event)).join("\n"));
  globalThis.fetch = async (_, options) => {
    assert.equal(options.headers.Accept, "application/x-ndjson");
    return new Response(new ReadableStream({ start(controller) {
      for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
      controller.close();
    }}), {headers: {"Content-Type": "application/x-ndjson"}});
  };
  const progress = [];
  try {
    assert.deepEqual(await generateReportTheme({onProgress: text => progress.push(text)}, {}), draft);
    assert.deepEqual(progress, ["Palette 🌊", "Composing"]);
  } finally { globalThis.window = oldWindow; globalThis.fetch = oldFetch; }
});

for (const [name, events, message] of [
  ["provider error", [{type:"error",error:"Provider unavailable"}], /Provider unavailable/],
  ["incomplete stream", [{type:"progress",text:"Working"}], /ended before the theme/],
]) test(`theme generation reports ${name}`, async () => {
  const oldWindow = globalThis.window, oldFetch = globalThis.fetch;
  globalThis.window = {};
  globalThis.fetch = async () => new Response(events.map(event => JSON.stringify(event)).join("\n"), {headers:{"Content-Type":"application/x-ndjson"}});
  try { await assert.rejects(generateReportTheme({}, {}), message); }
  finally { globalThis.window = oldWindow; globalThis.fetch = oldFetch; }
});

test("aborted theme requests do not deliver late progress or a preview", async () => {
  const oldWindow = globalThis.window, oldFetch = globalThis.fetch;
  globalThis.window = {};
  const controller = new AbortController();
  globalThis.fetch = async (_, options) => {
    assert.equal(options.signal, controller.signal);
    controller.abort();
    return new Response('{"type":"progress","text":"Too late"}\n', {headers:{"Content-Type":"application/x-ndjson"}});
  };
  try {
    await assert.rejects(generateReportTheme({signal:controller.signal, onProgress: () => assert.fail("Late progress")}, {}), {name:"AbortError"});
  } finally { globalThis.window = oldWindow; globalThis.fetch = oldFetch; }
});
