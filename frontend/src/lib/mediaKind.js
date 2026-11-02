// What a file previews as, from its extension. Shared by the editor's media tabs
// and Rem's chat, which must not load the editor to show a picture.
export function isImagePath(path) {
  return [".avif", ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".webp"]
    .some((extension) => String(path ?? "").toLowerCase().endsWith(extension));
}

export function mediaKind(path) {
  if (isImagePath(path)) return "image";
  const extension = String(path || "").split(".").at(-1).toLowerCase();
  if (["mp4", "m4v", "mov", "webm", "ogv", "mkv", "avi"].includes(extension)) return "video";
  if (["mp3", "m4a", "aac", "wav", "ogg", "oga", "opus", "flac", "aif", "aiff"].includes(extension)) return "audio";
  return "";
}
