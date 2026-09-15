const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createRequire } = require("node:module");
const updaterRequire = createRequire(require.resolve("electron-updater"));
const equal = updaterRequire("lodash.isequal");

test("updater adapter retains Lodash equality semantics", () => {
  assert.equal(equal, require("lodash/isEqual"));
  for (const [left, right, expected] of [
    [{ version: "1.2", files: [{ sha512: "abc", size: 3 }] }, { files: [{ size: 3, sha512: "abc" }], version: "1.2" }, true],
    [{ size: 0 }, { size: -0 }, true],
    [new Number(3), 3, true],
    [[undefined], Array(1), true],
    [{ value: NaN }, { value: NaN }, true],
    [{ sha512: "abc" }, { sha512: "def" }, false],
    [{ size: 3 }, { size: "3" }, false],
    [{ size: undefined }, {}, false],
    [new Date(100), new Date(100), true],
  ]) assert.equal(equal(left, right), expected);
});

test("real updater accepts matching metadata and rejects changed cached updates", async () => {
  const { DownloadedUpdateHelper } = updaterRequire("electron-updater/out/DownloadedUpdateHelper.js");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-updater-"));
  try {
    const file = path.join(root, "update.bin");
    fs.writeFileSync(file, "fixture");
    const helper = new DownloadedUpdateHelper(root);
    const info = { version: "1.2.3", files: [{ url: "update.bin", sha512: "fixture", size: 7 }] };
    const fileInfo = { info: info.files[0], url: new URL("https://example.invalid/update.bin") };
    await helper.setDownloadedFile(file, null, info, fileInfo, "update.bin", false);
    assert.equal(await helper.validateDownloadedPath(file, structuredClone(info), { ...fileInfo, info: structuredClone(fileInfo.info) }, console), file);
    assert.equal(await helper.validateDownloadedPath(file, { ...info, version: "1.2.4" }, fileInfo, console), null);
    assert.equal(await helper.validateDownloadedPath(file, info, { ...fileInfo, info: { ...fileInfo.info, sha512: "changed" } }, console), null);
    fs.unlinkSync(file);
    assert.equal(await helper.validateDownloadedPath(file, info, fileInfo, console), null);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
