import { startGenerationJob } from "./generationJobs.js";
import { apiUrl } from "./api.js";

import { normalizeReportThemes, reportOutputFormat } from "../../electron/report-themes.js";
export { reportOutputFormat } from "../../electron/report-themes.js";
export { DEFAULT_REPORT_THEMES, normalizeReportThemes } from "../../electron/report-themes.js";

export function reportThemeContext(memory = {}) {
  const config = normalizeReportThemes(memory.reportThemes, memory.secondBrainTheme);
  return { enabled: config.enabled, theme: config.selected, format: reportOutputFormat(memory),
    ...(config.custom.find(theme => theme.id === config.selected) ? { instructions: config.custom.find(theme => theme.id === config.selected).instructions } : {}) };
}

// Generated documents have no script, network, navigation, or parent access.
export function reportPreviewDocument(html) {
  return `<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'">${html}`;
}

export async function generateReportTheme(request, selection) {
  const response = await fetch(apiUrl("/report-themes/generate"), {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" }, signal: request.signal,
    body: JSON.stringify({ ...selection, description: request.description, screenshot: request.screenshot, attachments: request.attachments }),
  });
  if (response.ok && response.headers?.get("Content-Type")?.includes("application/x-ndjson")) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        request.signal?.throwIfAborted();
        buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
        if (done && buffer) buffer += "\n";
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          const event = JSON.parse(line);
          if (event.type === "error") throw new Error(event.error || "Rem could not generate the theme.");
          if (event.type === "progress") request.onProgress?.(event.text);
          if (event.type === "final") return event.theme;
        }
        if (done) throw new Error("Connection to Rem ended before the theme was ready. Try again.");
      }
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Rem could not generate the theme.");
  return result;
}

export function reportThemeGenerationSelection(generation, current) {
  return generation?.provider
    ? { provider: generation.provider, model: generation.model || "cli-default", effort: generation.effort,
        permissionMode: ["grok", "antigravity"].includes(generation.provider) ? "cli-managed" : undefined }
    : current;
}

export function requestReportTheme({ generation, ...options }) {
  if (generation?.provider) return options.background
    ? startGenerationJob("theme", options, reportThemeGenerationSelection(generation))
    : generateReportTheme(options, reportThemeGenerationSelection(generation));
  return new Promise((resolve, reject) => {
    const detail = { ...options, generation, resolve, reject, handled: false };
    window.dispatchEvent(new CustomEvent("gofer:rem-report-theme", { detail }));
    if (!detail.handled) reject(new Error("Open Rem to generate a theme with its current provider."));
  });
}
