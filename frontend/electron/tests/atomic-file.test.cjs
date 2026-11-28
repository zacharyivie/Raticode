const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { writeFileAtomicSync } = require("../atomic-file.cjs");

const ELECTRON = path.join(__dirname, "..");

function tempRoot(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-atomic-file-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

// Every renameSync call in an electron/*.cjs file or main.js whose source looks like a
// temp file: a name with "tmp" or "temp" in it, or a variable set from one.
function tempRenames(file) {
  const source = fs.readFileSync(file, "utf8");
  const found = [];
  for (const match of source.matchAll(/renameSync\(\s*([^,]+),/g)) {
    const argument = match[1].trim();
    const declared = /^[A-Za-z_$][\w$]*$/.test(argument)
      ? source.match(new RegExp(`(?:const|let|var)\\s+${argument.replace(/\$/g, "\\$")}\\s*=([^;\\n]*)`))?.[1] || ""
      : "";
    if (/te?mp/i.test(argument) || /\.tmp|tmp`|temp/i.test(declared)) {
      const line = source.slice(0, match.index).split("\n").length;
      found.push(`${path.basename(file)}:${line}: renameSync(${argument}, ...)`);
    }
  }
  return found;
}

test("temp-file renames only happen in atomic-file.cjs", () => {
  const files = fs.readdirSync(ELECTRON)
    .filter((name) => (name.endsWith(".cjs") || name === "main.js") && !name.endsWith(".test.cjs") && name !== "atomic-file.cjs")
    .map((name) => path.join(ELECTRON, name));
  assert.ok(files.some((file) => file.endsWith("main.js")));
  assert.deepEqual(files.flatMap(tempRenames), [], "Write the file with writeFileAtomicSync from atomic-file.cjs instead.");
});

test("the temp-rename scan catches the shapes the old writers used", (t) => {
  const root = tempRoot(t);
  const file = path.join(root, "writer.cjs");
  fs.writeFileSync(file, [
    "const temporary = `${file}.${crypto.randomUUID()}.tmp`;",
    "fs.renameSync(temporary, file);",
    "fs.renameSync(`${file}.tmp`, file);",
    "fs.renameSync(temp, readme);",
    "fs.renameSync(source, target);",
  ].join("\n"));
  assert.deepEqual(tempRenames(file), [
    "writer.cjs:2: renameSync(temporary, ...)",
    "writer.cjs:3: renameSync(`${file}.tmp`, ...)",
    "writer.cjs:4: renameSync(temp, ...)",
  ]);
});

test("writeFileAtomicSync replaces the file, readable only by its owner, and leaves no temp file", (t) => {
  const root = tempRoot(t);
  const file = path.join(root, "settings.json");
  fs.writeFileSync(file, "old", { mode: 0o644 });
  writeFileAtomicSync(file, "new", { syncDirectory: true });
  assert.equal(fs.readFileSync(file, "utf8"), "new");
  if (process.platform !== "win32") assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  writeFileAtomicSync(file, Buffer.from("quick"), { durable: false, mode: 0o640 });
  assert.equal(fs.readFileSync(file, "utf8"), "quick");
  if (process.platform !== "win32") assert.equal(fs.statSync(file).mode & 0o777, 0o640 & ~process.umask());
  assert.deepEqual(fs.readdirSync(root), ["settings.json"]);
});

test("durable writes fsync the data, and syncDirectory also fsyncs the folder", (t) => {
  const root = tempRoot(t);
  const file = path.join(root, "data.json");
  const sync = t.mock.method(fs, "fsyncSync");
  writeFileAtomicSync(file, "a", { durable: false });
  assert.equal(sync.mock.callCount(), 0);
  writeFileAtomicSync(file, "b");
  assert.equal(sync.mock.callCount(), 1);
  writeFileAtomicSync(file, "c", { syncDirectory: true });
  assert.equal(sync.mock.callCount(), process.platform === "win32" ? 2 : 3);
});

test("a link or file already at the temp name is never followed or overwritten", { skip: process.platform === "win32" }, (t) => {
  const root = tempRoot(t);
  const file = path.join(root, "settings.json");
  const outside = path.join(root, "outside.txt");
  fs.writeFileSync(outside, "private");
  fs.writeFileSync(file, "old");
  const uuid = "00000000-0000-4000-8000-000000000000";
  t.mock.method(crypto, "randomUUID", () => uuid);
  fs.symlinkSync(outside, `${file}.${uuid}.tmp`);
  assert.throws(() => writeFileAtomicSync(file, "new"), { code: "EEXIST" });
  assert.equal(fs.readFileSync(outside, "utf8"), "private");
  assert.equal(fs.readFileSync(file, "utf8"), "old");
  // The planted link belongs to someone else, so the failed write leaves it alone too.
  assert.equal(fs.lstatSync(`${file}.${uuid}.tmp`).isSymbolicLink(), true);
});

test("a failed write keeps the old file and removes its temp file", (t) => {
  const root = tempRoot(t);
  const file = path.join(root, "settings.json");
  fs.writeFileSync(file, "old");
  t.mock.method(fs, "renameSync", () => { throw Object.assign(new Error("disk failure"), { code: "EIO" }); });
  assert.throws(() => writeFileAtomicSync(file, "new"), /disk failure/);
  assert.equal(fs.readFileSync(file, "utf8"), "old");
  assert.deepEqual(fs.readdirSync(root), ["settings.json"]);
});
