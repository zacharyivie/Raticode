// Project names, labels and paths that App and Rem's chat pane share.
import { uniquePaths } from "./workspacePaths.js";

export const PROJECT_LABELS_STORAGE_KEY = "gofer.projectLabels";

export function loadProjectLabels() {
  if (typeof window === "undefined") return {};
  try {
    const parsed = JSON.parse(window.localStorage?.getItem(PROJECT_LABELS_STORAGE_KEY) ?? "{}");
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") return {};
    return Object.fromEntries(
      Object.entries(parsed).filter(
        ([root, label]) => root.trim() && typeof label === "string" && label.trim(),
      ),
    );
  } catch {
    return {};
  }
}

export function projectNameFromPath(pathValue) {
  const parts = String(pathValue).replaceAll("\\", "/").split("/").filter(Boolean);
  return parts.at(-1) || "Unregistered";
}

export function mergeRecentProjects(current = [], additions = []) {
  return uniquePaths([...current, ...additions].map((root) => String(root).trim()).filter(Boolean));
}

export function assistantMarkdownSourcePath(projectRoot) {
  const root = String(projectRoot ?? "").replace(/[\\/]+$/, "");
  if (!root) return "";
  const separator = root.includes("\\") && !root.includes("/") ? "\\" : "/";
  return `${root}${separator}.raticode-assistant.md`;
}
