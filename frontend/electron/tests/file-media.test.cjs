const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createMediaPreviews } = require("../media-preview.cjs");
const { movePath, copyPath } = require("../safe-files.cjs");

function fixture(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "rem-file-media-")));
  t.after(() => fs.rmSync(root, { force: true, recursive: true }));
  return root;
}
const authorize = { authorizeSource() {}, authorizeDestination() {} };

test("moves files and complete folders between projects without replacing existing files", async t => {
  const root = fixture(t), a = path.join(root, "a"), b = path.join(root, "b");
  fs.mkdirSync(a); fs.mkdirSync(b);
  fs.writeFileSync(path.join(a, "notes.txt"), "original");
  await movePath(path.join(a, "notes.txt"), path.join(b, "notes.txt"), authorize);
  assert.equal(fs.existsSync(path.join(a, "notes.txt")), false);
  assert.equal(fs.readFileSync(path.join(b, "notes.txt"), "utf8"), "original");
  fs.writeFileSync(path.join(a, "notes.txt"), "keep");
  await assert.rejects(movePath(path.join(a, "notes.txt"), path.join(b, "notes.txt"), authorize), /already exists/);
  assert.equal(fs.readFileSync(path.join(a, "notes.txt"), "utf8"), "keep");
  fs.mkdirSync(path.join(a, "folder", "nested"), { recursive: true });
  fs.writeFileSync(path.join(a, "folder", "nested", "image.png"), Buffer.from([0, 1, 2, 255]));
  await assert.rejects(movePath(path.join(a, "folder"), path.join(a, "folder", "child"), authorize), /into itself/);
  await movePath(path.join(a, "folder"), path.join(b, "folder"), authorize);
  assert.deepEqual(fs.readFileSync(path.join(b, "folder", "nested", "image.png")), Buffer.from([0, 1, 2, 255]));
});

test("cross-volume moves keep the original when copying fails", async t => {
  const root = fixture(t), source = path.join(root, "source"), destination = path.join(root, "destination");
  fs.writeFileSync(source, "keep the original");
  const rename = fs.promises.rename;
  const open = fs.promises.open;
  fs.promises.rename = async () => { throw Object.assign(new Error("Cross-device"), { code: "EXDEV" }); };
  try {
    fs.promises.open = async (target, flags, ...options) => {
      if (path.basename(target) === "destination" && (flags & fs.constants.O_CREAT)) throw new Error("Copy denied");
      return open(target, flags, ...options);
    };
    await assert.rejects(movePath(source, destination, authorize), /Copy denied/);
    assert.equal(fs.readFileSync(source, "utf8"), "keep the original");
    fs.promises.open = open;
    await movePath(source, destination, authorize);
    assert.equal(fs.existsSync(source), false);
    assert.equal(fs.readFileSync(destination, "utf8"), "keep the original");
  } finally { fs.promises.rename = rename; fs.promises.open = open; }
});

test("cross-volume moves retain originals changed after the copy, including nested files", async t => {
  const root = fixture(t), source = path.join(root, "source"), destination = path.join(root, "destination");
  fs.mkdirSync(source); fs.writeFileSync(path.join(source, "nested.txt"), "original");
  const rename = fs.promises.rename, open = fs.promises.open;
  fs.promises.rename = async () => { throw Object.assign(new Error("Cross-device"), { code: "EXDEV" }); };
  fs.promises.open = async (...args) => {
    const handle = await open(...args);
    if (path.basename(args[0]) === "nested.txt" && (args[1] & fs.constants.O_CREAT)) {
      const close = handle.close.bind(handle);
      handle.close = async () => { await close(); fs.writeFileSync(path.join(source, "nested.txt"), "changed after copy"); };
    }
    return handle;
  };
  try {
    await assert.rejects(movePath(source, destination, authorize), /Source changed while moving/);
    assert.equal(fs.readFileSync(path.join(source, "nested.txt"), "utf8"), "changed after copy");
    assert.equal(fs.readFileSync(path.join(destination, "nested.txt"), "utf8"), "original");
  } finally { fs.promises.rename = rename; fs.promises.open = open; }
});

test("media previews stream full and seek ranges, reject invalid ranges and expire with their owner", async t => {
  const root = fixture(t), file = path.join(root, "movie.mp4");
  fs.writeFileSync(file, "0123456789");
  const previews = createMediaPreviews(), preview = previews.open(file, 7);
  const request = (range, method = "GET") => previews.handle(new Request(preview.url, { method, headers: range ? { range } : {} }));
  const full = await request();
  assert.equal(full.status, 200); assert.equal(full.headers.get("content-type"), "video/mp4");
  assert.equal(await full.text(), "0123456789");
  for (const [range, text, contentRange] of [["bytes=2-5", "2345", "bytes 2-5/10"], ["bytes=7-", "789", "bytes 7-9/10"], ["bytes=-3", "789", "bytes 7-9/10"]]) {
    const response = await request(range);
    assert.equal(response.status, 206); assert.equal(response.headers.get("content-range"), contentRange);
    assert.equal(await response.text(), text);
  }
  for (const range of ["bytes=100-", "bytes=5-3", "bytes=-0", "bytes=0-2,4-6"]) assert.equal((await request(range)).status, 416);
  const head = await request(undefined, "HEAD"); assert.equal(head.headers.get("content-length"), "10"); assert.equal(await head.text(), "");
  previews.close(preview.id, 8); assert.equal((await request(undefined, "HEAD")).status, 200);
  previews.closeOwner(7); assert.equal((await request()).status, 404);
});

test("media streams reject replaced files and paths that were never opened", async t => {
  const root = fixture(t), file = path.join(root, "song.wav");
  fs.writeFileSync(file, "audio");
  const previews = createMediaPreviews(), preview = previews.open(file, 1);
  fs.renameSync(file, `${file}.old`); fs.writeFileSync(file, "replacement");
  assert.equal((await previews.handle(new Request(preview.url))).status, 404);
  assert.equal((await previews.handle(new Request(`raticode-media://preview/${encodeURIComponent(file)}`))).status, 404);
});

test("explicit replacement copies and moves files and whole folders", async t => {
  const root = fixture(t), source = path.join(root, "source"), destination = path.join(root, "destination");
  fs.writeFileSync(source, "incoming"); fs.writeFileSync(destination, "old");
  await copyPath(source, destination, { ...authorize, replace: true });
  assert.equal(fs.readFileSync(source, "utf8"), "incoming");
  assert.equal(fs.readFileSync(destination, "utf8"), "incoming");
  fs.writeFileSync(source, "moved");
  await movePath(source, destination, { ...authorize, replace: true });
  assert.equal(fs.existsSync(source), false);
  assert.equal(fs.readFileSync(destination, "utf8"), "moved");
  fs.mkdirSync(source); fs.writeFileSync(path.join(source, "new.txt"), "nested");
  await copyPath(source, destination, { ...authorize, replace: true });
  assert.equal(fs.readFileSync(path.join(destination, "new.txt"), "utf8"), "nested");
  fs.writeFileSync(path.join(destination, "old.txt"), "discard");
  await movePath(source, destination, { ...authorize, replace: true });
  assert.equal(fs.existsSync(source), false);
  assert.deepEqual(fs.readdirSync(destination), ["new.txt"]);
  assert.deepEqual(fs.readdirSync(root), ["destination"]);
});

test("failed replacement retains source and destination and rejects replacing itself or linked files", async t => {
  const root = fixture(t), source = path.join(root, "source"), destination = path.join(root, "destination");
  fs.writeFileSync(source, "incoming"); fs.writeFileSync(destination, "old");
  const options = { ...authorize, replace: true };
  await assert.rejects(copyPath(source, source, options), /replace itself/);
  fs.symlinkSync(destination, path.join(root, "link"));
  await assert.rejects(copyPath(source, path.join(root, "link"), options), /linked/);
  const open = fs.promises.open;
  fs.promises.open = async (target, flags, ...args) => {
    if (path.basename(target).endsWith(".incoming") && (flags & fs.constants.O_CREAT)) throw new Error("Copy denied");
    return open(target, flags, ...args);
  };
  try {
    await assert.rejects(movePath(source, destination, options), /Copy denied/);
    assert.equal(fs.readFileSync(source, "utf8"), "incoming");
    assert.equal(fs.readFileSync(destination, "utf8"), "old");
    assert.equal(fs.readdirSync(root).some(name => name.startsWith(".raticode-transfer-")), false);
  } finally { fs.promises.open = open; }
});

test("a failed replacement installation restores the old destination", async t => {
  const root = fixture(t), source = path.join(root, "source"), destination = path.join(root, "destination");
  fs.writeFileSync(source, "incoming"); fs.writeFileSync(destination, "old");
  const rename = fs.promises.rename;
  fs.promises.rename = async (from, to) => {
    if (path.basename(from).endsWith(".incoming")) throw new Error("Install denied");
    return rename(from, to);
  };
  try {
    await assert.rejects(movePath(source, destination, { ...authorize, replace: true }), /Install denied/);
    assert.equal(fs.readFileSync(source, "utf8"), "incoming");
    assert.equal(fs.readFileSync(destination, "utf8"), "old");
    assert.deepEqual(fs.readdirSync(root), ["destination", "source"]);
  } finally { fs.promises.rename = rename; }
});

test("replacement preserves destination folders edited while the incoming files are copied", async t => {
  const root = fixture(t), source = path.join(root, "source"), destination = path.join(root, "destination");
  fs.mkdirSync(source); fs.mkdirSync(destination);
  fs.writeFileSync(path.join(source, "new.txt"), "incoming");
  fs.writeFileSync(path.join(destination, "old.txt"), "original");
  const open = fs.promises.open;
  fs.promises.open = async (...args) => {
    const handle = await open(...args);
    if (path.basename(args[0]) === "new.txt" && (args[1] & fs.constants.O_CREAT)) {
      const close = handle.close.bind(handle);
      handle.close = async () => {
        await close();
        // Editing a child leaves its parent directory's size and mtime alone.
        fs.writeFileSync(path.join(destination, "old.txt"), "edited during copy");
      };
    }
    return handle;
  };
  try {
    await assert.rejects(movePath(source, destination, { ...authorize, replace: true }), /Destination changed/);
    assert.equal(fs.readFileSync(path.join(destination, "old.txt"), "utf8"), "edited during copy");
    assert.equal(fs.readFileSync(path.join(source, "new.txt"), "utf8"), "incoming");
    assert.deepEqual(fs.readdirSync(root), ["destination", "source"]);
  } finally { fs.promises.open = open; }
});
