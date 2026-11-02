import { getFileClipboard, setFileClipboard } from "./fileTransfers.js";

export const REM_ATTACH_FILES_EVENT = "gofer:rem-attach-files";

// Reads files from disk as chat attachments.
export async function readFileAttachments(paths, desktop = window.goferDesktop) {
  const read = desktop?.textFiles?.readAttachment;
  if (!read) throw new Error("Restart the desktop app to attach files.");
  return Promise.all(paths.map(async path => {
    const file = await read(path);
    const binary = atob(file.data || "");
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return new File([bytes], file.name, { type: file.type || "application/octet-stream" });
  }));
}

// Sends files to Rem: its chat when the Rem plugin is on, otherwise the open thread.
export function askRemAboutFiles(paths) {
  const files = paths.filter(Boolean);
  if (files.length) window.dispatchEvent(new CustomEvent(REM_ATTACH_FILES_EVENT, { detail: { paths: files } }));
}

// Copying files puts their paths on the system clipboard and remembers them, so
// pasting that same text into a chat attaches the files instead. The file
// explorer can paste them too.
export async function copyFilesForPaste(entries, clipboard = navigator.clipboard) {
  if (!entries.length) return;
  const clipboardText = entries.map(entry => entry.path).join("\n");
  await clipboard.writeText(clipboardText);
  setFileClipboard({ ...entries[0], entries, operation: "copy", clipboardText });
}

export function copiedFilePaths(pastedText, copied = getFileClipboard()) {
  // Windows clipboards may return CRLF line endings.
  if (!pastedText || copied?.operation !== "copy" || copied.clipboardText !== pastedText.replace(/\r\n/g, "\n")) return [];
  return (copied.entries || [copied]).filter(entry => !entry.isDirectory).map(entry => entry.path);
}
