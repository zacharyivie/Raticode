// What kind of file an attachment is, for its thumbnail and its preview. The phone
// uses the same groups (FileKinds.kt) so a file looks the same on both.
const KINDS = [
  ["image", /^(png|jpe?g|gif|webp|bmp|avif|ico|svg|heic|heif|tiff?)$/],
  ["video", /^(mp4|m4v|mov|webm|ogv|mkv|avi|3gp)$/],
  ["audio", /^(mp3|m4a|aac|wav|ogg|oga|opus|flac|aiff?)$/],
  ["pdf", /^pdf$/],
  ["sheet", /^(csv|tsv|xlsx?|ods|numbers)$/],
  ["code", /^(js|jsx|mjs|cjs|ts|tsx|py|rb|go|rs|java|kt|kts|swift|c|cc|cpp|h|hpp|cs|sh|bash|zsh|fish|sql|css|scss|html?|vue|php|lua|rattish|json|jsonl|ya?ml|toml|xml|diff|patch|ini|cfg|conf|env)$/],
  ["text", /^(txt|md|markdown|log|rst)$/],
  ["archive", /^(zip|tar|gz|tgz|bz2|xz|7z|rar|zst|jar|apk)$/],
  ["document", /^(docx?|odt|rtf|pptx?|odp|key|pages|epub)$/],
];

// Files the preview can show as text, by extension; anything else is checked for text.
const TEXT_SHEETS = new Set(["csv", "tsv"]);

export function fileExtension(name) {
  const base = String(name || "").split(/[\\/]/).at(-1);
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot + 1).toLowerCase() : "";
}

// { kind, label }: kind is one of image, video, audio, pdf, sheet, code, text, archive,
// document or file; label is the short type shown on a tile, such as PDF or ZIP.
export function fileKind(name, type = "") {
  const extension = fileExtension(name);
  const mime = String(type || "").toLowerCase();
  let kind = KINDS.find(([, pattern]) => pattern.test(extension))?.[0] || "";
  if (!kind) {
    if (mime.startsWith("image/")) kind = "image";
    else if (mime.startsWith("video/")) kind = "video";
    else if (mime.startsWith("audio/")) kind = "audio";
    else if (mime === "application/pdf") kind = "pdf";
    else if (mime.startsWith("text/")) kind = "text";
    else kind = "file";
  }
  const label = /^[a-z0-9]{1,5}$/.test(extension) ? extension.toUpperCase() : kind === "file" ? "FILE" : kind.toUpperCase();
  return { kind, label };
}

// How the preview shows a file: media (picture, video or sound), text, or a card that
// offers to open it elsewhere.
export function previewMode(name, type = "") {
  const { kind } = fileKind(name, type);
  if (kind === "image" || kind === "video" || kind === "audio") return kind;
  if (kind === "text" || kind === "code" || TEXT_SHEETS.has(fileExtension(name))) return "text";
  if (kind === "file" && String(type || "").startsWith("text/")) return "text";
  return "other";
}

export const TEXT_PREVIEW_BYTES = 256 * 1024;

// The start of a file as text, or null when it looks binary.
export function previewText(bytes, limit = TEXT_PREVIEW_BYTES) {
  const head = bytes.subarray(0, limit);
  if (head.subarray(0, 8192).includes(0)) return null;
  return { text: new TextDecoder("utf-8", { fatal: false }).decode(head), truncated: bytes.length > limit };
}
