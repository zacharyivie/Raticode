// Links in terminal output: web addresses and file paths with an optional :line[:column].
// Terminal output is untrusted, so only http and https addresses and files that exist
// become links, and they open only on Ctrl+click (Cmd+click on macOS), never on a plain click.
import { pathWithin } from "./workspacePaths.js";

// The terminal asks the app to open a file through this event, so the app's
// outside-project question and editor navigation apply (see App.jsx).
export const TERMINAL_OPEN_FILE_EVENT = "gofer:terminal-open-file";

// Characters that can't be part of an address in terminal output, and trailing ones
// that usually end the sentence around it rather than the address.
const WEB_LINK = /https?:\/\/[^\s"'<>`{}|\\^]+/gi;
const TRAILING_PUNCTUATION = /[.,;:!?'"]+$/;
// A path segment, then more segments, then the line and column as :line:column or as
// (line,column), the form compilers such as tsc and MSBuild print. Paths start at a word
// boundary so the "src/a.py" inside "xsrc/a.py" or a longer path isn't matched again.
// Windows paths may start with a drive (C:\) and use either slash.
const FILE_LINK = /(?<![\w./\\@+~-])((?:[A-Za-z]:[\\/]|\.{1,2}[\\/]|[\\/])?[\w@+-][\w.@+-]*(?:[\\/][\w.@+-]+)*)(?::(\d+)(?::(\d+))?|\((\d+)(?:, ?(\d+))?\))?/g;
// A Python traceback line: File "path", line N. The quotes let the path have spaces.
const TRACEBACK_LINK = /\bFile "([^"<>\n][^"\n]*)", line (\d+)/g;
const WINDOWS_ABSOLUTE = /^[A-Za-z]:[\\/]/;
const MAX_FILE_CANDIDATES = 12;
// The desktop's existence check takes up to 8 paths at once.
const EXISTS_BATCH = 8;
const EXISTS_CACHE_MS = 3000;
const EXISTS_CACHE_SIZE = 200;

function isMacPlatform(platform = globalThis.navigator?.platform ?? "") {
  return /Mac|iPhone|iPad/i.test(platform);
}

function linkModifierLabel(platform) {
  return isMacPlatform(platform) ? "Cmd" : "Ctrl";
}

// Ctrl+click opens a link, or Cmd+click on macOS. A plain click selects text as usual.
function opensLink(event, platform) {
  return isMacPlatform(platform) ? Boolean(event?.metaKey) : Boolean(event?.ctrlKey);
}

function trimWebLink(text) {
  let value = text.replace(TRAILING_PUNCTUATION, "");
  // A closing bracket ends the address only when the address didn't open it.
  for (const [open, close] of [["(", ")"], ["[", "]"]]) {
    while (value.endsWith(close) && value.split(open).length < value.split(close).length) {
      value = value.slice(0, -1).replace(TRAILING_PUNCTUATION, "");
    }
  }
  return value;
}

// http and https addresses in one line of text, as { start, end, url } string offsets.
export function findWebLinks(text) {
  const links = [];
  for (const match of String(text ?? "").matchAll(WEB_LINK)) {
    const url = trimWebLink(match[0]);
    if (/^https?:\/\/[^/?#]/i.test(url)) links.push({ start: match.index, end: match.index + url.length, url });
  }
  return links;
}

function fileLinkCandidate(start, end, path, line, column) {
  return {
    start,
    end,
    path,
    ...(line ? { lineNumber: Number(line) } : {}),
    ...(column ? { column: Number(column) } : {}),
  };
}

// Path-like words with an optional line and column, outside the given web link ranges.
// A Python traceback's File "path", line N is one candidate, the whole phrase. Otherwise
// a word counts as a path when it has a folder separator or a file extension. Whether
// it exists is checked later.
export function findFileLinkCandidates(text, skip = []) {
  const value = String(text ?? "");
  const overlaps = (ranges, start, end) => ranges.some(range => start < range.end && end > range.start);
  const tracebacks = [];
  for (const match of value.matchAll(TRACEBACK_LINK)) {
    const end = match.index + match[0].length;
    if (!overlaps(skip, match.index, end)) tracebacks.push(fileLinkCandidate(match.index, end, match[1], match[2]));
  }
  const candidates = [...tracebacks];
  for (const match of value.matchAll(FILE_LINK)) {
    if (candidates.length >= MAX_FILE_CANDIDATES) break;
    let path = match[1];
    const start = match.index;
    if (overlaps(skip, start, start + match[0].length) || overlaps(tracebacks, start, start + match[0].length)) continue;
    const line = match[2] ?? match[4];
    let length = match[0].length;
    if (!line && path.endsWith(".")) {
      const trimmed = path.replace(/\.+$/, "");
      length -= path.length - trimmed.length;
      path = trimmed;
    }
    if (!path || /^\.{1,2}[\\/]?$/.test(path)) continue;
    if (!/[\\/]/.test(path) && !/\.[A-Za-z][A-Za-z0-9]*$/.test(path)) continue;
    candidates.push(fileLinkCandidate(start, start + length, path, line, match[3] ?? match[5]));
  }
  return candidates.sort((a, b) => a.start - b.start).slice(0, MAX_FILE_CANDIDATES);
}

function normalizedParts(value) {
  const parts = [];
  for (const part of value.split(/[\\/]/)) {
    if (!part || part === ".") continue;
    if (part === "..") parts.pop();
    else parts.push(part);
  }
  return parts;
}

const posixPath = value => `/${normalizedParts(value).join("/")}`;
// C:\ and the rest, with backslashes, as Windows paths are given to the existence check.
const windowsPath = value => `${value.slice(0, 2)}\\${normalizedParts(value.slice(2)).join("\\")}`;

// Where a path in the output can point: itself when absolute, otherwise the terminal's
// current folder first and then the project folder. A folder with a drive letter is a
// Windows folder, where both slashes separate and a path starting with one is on that
// drive. Elsewhere a backslash is part of a name, almost always an escape in output, so
// such a path isn't looked up.
export function fileLinkTargets(path, { cwd = "", projectRoot = "" } = {}) {
  if (WINDOWS_ABSOLUTE.test(path)) return [windowsPath(path)];
  const bases = [cwd, projectRoot].filter(base => typeof base === "string" && base);
  const targets = [];
  for (const base of bases) {
    if (WINDOWS_ABSOLUTE.test(base)) targets.push(windowsPath(/^[\\/]/.test(path) ? `${base.slice(0, 2)}${path}` : `${base}\\${path}`));
    else if (base.startsWith("/") && !path.includes("\\")) targets.push(posixPath(path.startsWith("/") ? path : `${base}/${path}`));
  }
  if (!bases.length && path.startsWith("/") && !path.includes("\\")) targets.push(posixPath(path));
  return [...new Set(targets)];
}

// Which of these absolute paths are files, using the desktop's existence check. It reads
// nothing and grants no access, so hovering over output can't open up a folder.
export function createFileExistenceCheck(missingFiles, now = () => Date.now()) {
  const cache = new Map();
  return async function existingFiles(paths) {
    const time = now();
    const unknown = [...new Set(paths)].filter(path => {
      const cached = cache.get(path);
      return !cached || time - cached.at > EXISTS_CACHE_MS;
    });
    for (let index = 0; index < unknown.length; index += EXISTS_BATCH) {
      const batch = unknown.slice(index, index + EXISTS_BATCH);
      let missing;
      try {
        missing = new Set(await missingFiles(batch));
      } catch {
        missing = new Set(batch);
      }
      for (const path of batch) {
        cache.delete(path);
        cache.set(path, { at: time, exists: !missing.has(path) });
      }
    }
    while (cache.size > EXISTS_CACHE_SIZE) cache.delete(cache.keys().next().value);
    return new Set(paths.filter(path => cache.get(path)?.exists));
  };
}

// The text of a buffer line and the ones wrapped onto it, with the cell each character is in.
function wrappedLineText(buffer, lineIndex) {
  let first = lineIndex;
  while (first > 0 && buffer.getLine(first)?.isWrapped) first -= 1;
  let text = "";
  const cells = [];
  for (let y = first; ; y += 1) {
    const line = buffer.getLine(y);
    if (!line || (y > first && !line.isWrapped)) break;
    for (let x = 0; x < line.length; x += 1) {
      const cell = line.getCell(x);
      const width = cell?.getWidth?.() ?? 1;
      if (width === 0) continue;
      const chars = cell?.getChars?.() || " ";
      for (let unit = 0; unit < chars.length; unit += 1) cells.push({ x, y, width });
      text += chars;
    }
  }
  return { cells, text };
}

// xterm ranges are 1-based and end on the last cell of the link.
function cellRange(cells, start, end) {
  const first = cells[start];
  const last = cells[end - 1];
  return {
    start: { x: first.x + 1, y: first.y + 1 },
    end: { x: last.x + last.width, y: last.y + 1 },
  };
}

function fileLinkLabel(target) {
  if (!target.lineNumber) return target.path;
  return `${target.path}, line ${target.lineNumber}${target.column ? `, column ${target.column}` : ""}`;
}

// An xterm link provider for one terminal. place() gives { cwd, projectRoot }.
export function createTerminalLinkProvider(terminal, {
  existingFiles,
  onHover,
  onLeave,
  openFile,
  openUrl,
  place,
  platform,
}) {
  function link(text, range, label, open) {
    return {
      decorations: { pointerCursor: true, underline: true },
      range,
      text,
      activate(event) {
        if (!opensLink(event, platform)) return;
        event.preventDefault?.();
        open();
      },
      hover(event) { onHover?.(event, `${label}\n${linkModifierLabel(platform)}+click to open`); },
      leave() { onLeave?.(); },
    };
  }

  return {
    provideLinks(bufferLineNumber, callback) {
      const buffer = terminal.buffer.active;
      const { cells, text } = wrappedLineText(buffer, bufferLineNumber - 1);
      const webLinks = findWebLinks(text);
      const links = webLinks.map(found => link(found.url, cellRange(cells, found.start, found.end), found.url, () => openUrl(found.url)));
      const candidates = findFileLinkCandidates(text, webLinks);
      if (!candidates.length || !existingFiles) {
        callback(links.length ? links : undefined);
        return;
      }
      const current = place?.() ?? {};
      const options = candidates.map(candidate => fileLinkTargets(candidate.path, current));
      void existingFiles(options.flat()).then((existing) => {
        candidates.forEach((candidate, index) => {
          const path = options[index].find(target => existing.has(target));
          if (!path) return;
          const target = { path, projectRoot: current.projectRoot || "", ...(candidate.lineNumber ? { lineNumber: candidate.lineNumber } : {}), ...(candidate.column ? { column: candidate.column } : {}) };
          links.push(link(text.slice(candidate.start, candidate.end), cellRange(cells, candidate.start, candidate.end), fileLinkLabel(target), () => openFile(target)));
        });
        callback(links.length ? links : undefined);
      }, () => callback(links.length ? links : undefined));
    },
  };
}

// OSC 8 links a program prints: the same rules, so a plain click or a link that isn't
// http or https does nothing, instead of xterm's default confirm dialog.
export function createTerminalHyperlinkHandler({ onHover, onLeave, openUrl, platform }) {
  const web = url => /^https?:\/\/[^/?#]/i.test(url);
  return {
    activate(event, url) {
      if (!web(url) || !opensLink(event, platform)) return;
      openUrl(url);
    },
    hover(event, url) { if (web(url)) onHover?.(event, `${url}\n${linkModifierLabel(platform)}+click to open`); },
    leave() { onLeave?.(); },
  };
}

// Whether a link's resolved path is inside one of the project folders. Each folder
// counts as given and as resolved, so a project opened through a symlinked folder
// still holds its own files. A resolved path has no symlinks left in it, so being
// under a folder as given means really being in it.
export async function linkInsideProject(resolvedPath, roots, resolve = async path => path) {
  for (const root of roots.filter(Boolean)) {
    if (pathWithin(resolvedPath, root)) return true;
    try {
      if (pathWithin(resolvedPath, await resolve(root))) return true;
    } catch { /* A folder that can't be resolved counts only as given. */ }
  }
  return false;
}

// Opening a file from a terminal link: a file outside the open project, or the folder
// the terminal belongs to, is opened only after the person agrees. The question is
// about where the link really leads: resolve() follows symlinks and grants nothing,
// so a project file that links to ~/.ssh/id_ed25519 asks before anything opens.
export async function openTerminalFileLink(target, { confirm, open, projectRoots = [], resolve = async path => path }) {
  if (!target?.path) return false;
  const path = await resolve(target.path) || target.path;
  const roots = [...projectRoots, target.projectRoot];
  if (!await linkInsideProject(path, roots, resolve) && !await confirm({
    message: path === target.path
      ? `Open "${path}"?\n\nA terminal link points to this file, which is outside the open project.`
      : `Open "${path}"?\n\nA terminal link to "${target.path}" leads to this file, which is outside the open project.`,
    confirmLabel: "Open file",
  })) return false;
  await open(path, {
    fromLink: true,
    resolved: true,
    ...(target.lineNumber ? { lineNumber: target.lineNumber } : {}),
    ...(target.column ? { column: target.column } : {}),
  });
  return true;
}
