import assert from "node:assert/strict";
import { test } from "node:test";
import { normalizeAppSettings } from "./settings.js";
import { normalizeReportThemes, reportThemeContext, reportPreviewDocument, generateReportTheme, reportThemeGenerationSelection } from "./reportThemes.js";

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
