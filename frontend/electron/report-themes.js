export const BUILTIN_THEME_IDS = ["auto", "light", "dark", "sepia", "vaporwave", "steam", "carbon", "botanical", "blueprint", "arcade", "sakura", "deep-sea", "solarpunk", "noir", "candy-lab", "cosmic"];
export const DEFAULT_REPORT_THEMES = { enabled: true, selected: "auto", custom: [], generation: { provider: "", model: "", effort: "" } };
export const REPORT_FORMATS = ["md", "html", "slides", "pdf"];
export function reportOutputFormat(memory = {}) {
  const format = memory.reportFormat ?? memory.secondBrainFormat;
  return REPORT_FORMATS.includes(format) ? format : "md";
}

export function normalizeReportThemes(value, legacyTheme = "auto") {
  const config = value && typeof value === "object" ? value : {};
  const custom = [];
  for (const item of Array.isArray(config.custom) ? config.custom : []) {
    if (custom.length >= 24) break;
    if (!item || typeof item.id !== "string" || !/^custom-[a-zA-Z0-9-]{1,80}$/.test(item.id) || custom.some(theme => theme.id === item.id)) continue;
    if (["label", "instructions", "html"].some(key => typeof item[key] !== "string" || !item[key].trim())) continue;
    if (item.label.length > 80 || item.instructions.length > 12000 || item.html.length > 200000) continue;
    custom.push({ id: item.id, label: item.label, instructions: item.instructions, html: item.html });
  }
  const selected = config.selected ?? legacyTheme;
  const string = (key, limit) => typeof config.generation?.[key] === "string" ? config.generation[key].slice(0, limit) : "";
  return { enabled: config.enabled !== false, selected: [...BUILTIN_THEME_IDS, ...custom.map(theme => theme.id)].includes(selected) ? selected : "auto", custom,
    generation: { provider: string("provider", 80), model: string("model", 160), effort: string("effort", 80) } };
}
