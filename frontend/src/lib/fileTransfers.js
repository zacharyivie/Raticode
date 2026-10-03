import { pathWithin, samePath } from "./workspacePaths.js";

export const FILE_DRAG_TYPE = "application/x-raticode-files";
const CLIPBOARD_KEY = "raticode-file-clipboard";
const listeners = new Set();
let clipboard = null;

export function getFileClipboard() { return clipboard; }
export function subscribeFileClipboard(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
export function setFileClipboard(value) {
  clipboard = value;
  try {
    if (value) window.localStorage.setItem(CLIPBOARD_KEY, JSON.stringify(value));
    else window.localStorage.removeItem(CLIPBOARD_KEY);
  } catch { /* The in-memory clipboard still works when storage is unavailable. */ }
  listeners.forEach(listener => listener());
}
if (typeof window !== "undefined") {
  try { clipboard = JSON.parse(window.localStorage.getItem(CLIPBOARD_KEY) || "null"); } catch { /* No saved clipboard. */ }
  window.addEventListener?.("storage", event => {
    if (event.key !== CLIPBOARD_KEY) return;
    try { clipboard = JSON.parse(event.newValue || "null"); } catch { clipboard = null; }
    listeners.forEach(listener => listener());
  });
}

export function containsFileTransfer(transfer) {
  return Array.from(transfer?.types || []).some(type => type === FILE_DRAG_TYPE || type === "Files");
}
export function beginFileDrag(event, entries) {
  event.dataTransfer.effectAllowed = "copyMove";
  event.dataTransfer.setData(FILE_DRAG_TYPE, JSON.stringify(Array.isArray(entries) ? entries : [entries]));
}
export async function droppedFileEntries(transfer, desktop = window.goferDesktop) {
  const internal = transfer?.getData?.(FILE_DRAG_TYPE);
  if (internal) {
    const entries = JSON.parse(internal);
    if (!Array.isArray(entries) || entries.some(entry => !entry?.path || !entry?.name)) throw new Error("Invalid file drop.");
    return { entries, internal: true };
  }
  // Resolve native Files before the first await; drag data is only readable during drop.
  const paths = Array.from(transfer?.files || []).map(file => desktop?.getDroppedFilePath?.(file)).filter(Boolean);
  if (!paths.length) throw new Error("File drops require the desktop app.");
  const entries = [];
  for (const path of paths) {
    const info = await desktop.workspace.getPathInfo(path);
    if (!info?.exists) throw new Error(`File no longer exists: ${path}`);
    entries.push({ path, name: path.replace(/[\\/]+$/, "").split(/[\\/]/).at(-1), isDirectory: info.isDirectory, isFile: info.isFile });
  }
  return { entries, internal: false };
}

export function transferName(entry, directory, existingNames, operation, conflictAction = operation === "copy" ? "keep-both" : "cancel") {
  if (entry.isDirectory && pathWithin(directory, entry.path)) throw new Error("A folder cannot be placed inside itself.");
  const separator = directory.includes("\\") && !directory.includes("/") ? "\\" : "/";
  const destination = `${directory.replace(/[\\/]+$/, "")}${separator}${entry.name}`;
  if (operation === "move") {
    if (samePath(entry.path, destination)) return null;
    if (existingNames.has(entry.name) && conflictAction === "cancel") throw new Error(`${entry.name} already exists.`);
  }
  if (!existingNames.has(entry.name) || conflictAction === "replace") return entry.name;
  const dot = entry.isDirectory ? -1 : entry.name.lastIndexOf(".");
  const base = dot > 0 ? entry.name.slice(0, dot) : entry.name;
  const extension = dot > 0 ? entry.name.slice(dot) : "";
  let name = `${base} copy${extension}`;
  for (let i = 2; existingNames.has(name); i++) name = `${base} copy ${i}${extension}`;
  return name;
}

// A selected folder already carries its descendants during a transfer.
export function topLevelTransferEntries(entries) {
  return entries.filter((entry, index) => !entries.some((other, otherIndex) => otherIndex !== index
    && (samePath(entry.path, other.path) ? otherIndex < index : other.isDirectory && pathWithin(entry.path, other.path))));
}

export function clipboardEntries(value) {
  return value?.entries || (value?.path ? [value] : []);
}
