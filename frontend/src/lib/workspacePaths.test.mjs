import assert from "node:assert/strict";
import { test } from "node:test";
import { samePath, pathWithin, uniquePaths, pathValue, withPathValue, replacePathPrefix, pathMatchesChange } from "./workspacePaths.js";

test("Windows drive and UNC identities accept equivalent spellings", () => {
  for (const root of ["c:/Users/Alice/repo/", "C:\\Users\\Alice\\repo", "C:/Users//Alice/repo", "\\\\?\\C:\\Users\\Alice\\repo"]) {
    assert.equal(samePath(root, "C:/Users/Alice/repo"), true, root);
  }
  assert.equal(samePath("\\\\server\\share\\repo\\", "//SERVER/share/repo"), true);
  assert.equal(samePath("\\\\?\\UNC\\server\\share\\repo", "//server/share/repo"), true);
  assert.equal(samePath(undefined, ""), false);
});

test("POSIX paths retain case, backslashes, spaces and symlink-sensitive components", () => {
  assert.equal(samePath("/repo/A", "/repo/a"), false);
  assert.equal(samePath("/repo/a\\b", "/repo/a/b"), false);
  assert.equal(samePath("/repo/a ", "/repo/a"), false);
  assert.equal(samePath("/repo/link/../a", "/repo/a"), false);
  assert.equal(samePath("/repo//a/", "/repo/a"), true);
});

test("containment respects directory boundaries and filesystem roots", () => {
  assert.equal(pathWithin("c:\\REPO\\a", "C:/repo/"), true);
  assert.equal(pathWithin("C:/repository/a", "c:/repo"), false);
  assert.equal(pathWithin("/repo/a", "/"), true);
  assert.equal(pathWithin("C:/repo/a", "c:/"), true);
  assert.equal(pathWithin("/repo/a", ""), false);
});

test("recent project identity and keyed preferences preserve original spelling", () => {
  assert.deepEqual(uniquePaths(["C:\\repo", "/repo", "c:/repo/", "/Repo"]), ["C:\\repo", "/repo", "/Repo"]);
  assert.equal(pathValue({ "C:\\repo": "Review" }, "c:/repo/"), "Review");
});

test("rename and delete events match Windows paths without altering descendant case", () => {
  assert.equal(replacePathPrefix("C:\\Repo\\Src\\File.py", "c:/repo/src/", "C:\\Repo\\Lib", true), "C:\\Repo\\Lib\\File.py");
  assert.equal(replacePathPrefix("C:/Repo/File.py", "c:\\repo\\file.py", "C:/Repo/New.py", false), "C:/Repo/New.py");
  assert.equal(replacePathPrefix("/repo/Src/File.py", "/repo/src", "/repo/lib", true), "/repo/Src/File.py");
  assert.equal(pathMatchesChange("C:/Repo/Src/File.py", "c:\\repo\\src", true), true);
  assert.equal(pathMatchesChange("C:/Repo/Src2/File.py", "c:\\repo\\src", true), false);
  assert.equal(replacePathPrefix("/File.py", "/", "/repo", true), "/repo/File.py");
});

test("drive-relative paths remain distinct from drive roots", () => {
  assert.equal(samePath("C:", "C:/"), false);
});

test("updating and clearing a project label removes older equivalent keys", () => {
  const labels = withPathValue({ "C:/Repo": "Old", "/other": "Other" }, "c:/repo/", "New");
  assert.equal(pathValue(labels, "C:/Repo"), "New");
  assert.equal(Object.keys(labels).length, 2);
  assert.deepEqual(withPathValue(labels, "C:/Repo", undefined), { "/other": "Other" });
});
