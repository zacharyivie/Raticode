const fs = require("node:fs");
const crypto = require("node:crypto");
const path = require("node:path");
const { Readable } = require("node:stream");

const MEDIA_TYPES = {
  ".avif": "image/avif", ".bmp": "image/bmp", ".gif": "image/gif", ".ico": "image/x-icon",
  ".jpeg": "image/jpeg", ".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
  ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
  ".ogv": "video/ogg", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
  ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav",
  ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac", ".aif": "audio/aiff", ".aiff": "audio/aiff",
};

function byteRange(value, size) {
  if (!value) return null;
  const match = /^bytes=(\d*)-(\d*)$/.exec(value);
  if (!match || (!match[1] && !match[2]) || !size) throw new Error("Invalid range.");
  const start = match[1] ? Number(match[1]) : Math.max(0, size - Number(match[2]));
  const end = match[1] && match[2] ? Math.min(Number(match[2]), size - 1) : size - 1;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= size || end < start || (!match[1] && Number(match[2]) === 0)) throw new Error("Invalid range.");
  return { start, end };
}

function createMediaPreviews() {
  const previews = new Map();
  return {
    open(targetPath, ownerId) {
      const mimeType = MEDIA_TYPES[path.extname(targetPath).toLowerCase()];
      if (!mimeType) throw new Error("This file type does not have a media preview.");
      const stat = fs.lstatSync(targetPath);
      if (!stat.isFile()) throw new Error("Media previews require an ordinary file.");
      const id = crypto.randomUUID();
      previews.set(id, { targetPath, mimeType, ownerId, dev: stat.dev, ino: stat.ino });
      return { id, url: `raticode-media://preview/${id}`, mimeType };
    },
    close(id, ownerId) {
      if (previews.get(id)?.ownerId === ownerId) previews.delete(id);
    },
    closeOwner(ownerId) {
      for (const [id, preview] of previews) if (preview.ownerId === ownerId) previews.delete(id);
    },
    async handle(request) {
      const url = new URL(request.url);
      const preview = url.hostname === "preview" && previews.get(url.pathname.slice(1));
      if (!preview || url.protocol !== "raticode-media:") return new Response(null, { status: 404 });
      if (!["GET", "HEAD"].includes(request.method)) return new Response(null, { status: 405 });
      let handle;
      try {
        handle = await fs.promises.open(preview.targetPath, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0));
        const stat = await handle.stat();
        if (!stat.isFile() || stat.dev !== preview.dev || stat.ino !== preview.ino) throw new Error("Media file changed.");
        const headers = { "Content-Type": preview.mimeType, "Accept-Ranges": "bytes", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
        let range;
        try { range = byteRange(request.headers.get("range"), stat.size); }
        catch {
          await handle.close(); handle = null;
          return new Response(null, { status: 416, headers: { ...headers, "Content-Range": `bytes */${stat.size}` } });
        }
        headers["Content-Length"] = String(range ? range.end - range.start + 1 : stat.size);
        if (range) headers["Content-Range"] = `bytes ${range.start}-${range.end}/${stat.size}`;
        const status = range ? 206 : 200;
        if (request.method === "HEAD" || !stat.size) {
          await handle.close(); handle = null;
          return new Response(null, { status, headers });
        }
        const stream = handle.createReadStream({ ...(range || {}), autoClose: true });
        handle = null;
        return new Response(Readable.toWeb(stream), { status, headers });
      } catch {
        if (handle) await handle.close();
        return new Response(null, { status: 404 });
      }
    },
  };
}

module.exports = { byteRange, createMediaPreviews, MEDIA_TYPES };
