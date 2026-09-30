import test from "node:test";
import assert from "node:assert/strict";
import { filterCommitFiles, indexCommitMatches, searchCommitFiles } from "./commitDiff.js";

const files = [
  { path: "src/app.py", oldPath: "legacy/app.py", original: "removed TEXT", modified: "new text" },
  { path: "src/deep/app.test.py", original: "assert valid", modified: "assert fixed" },
  { path: "config.json", original: "{}", modified: '{"valid": true}' },
  { path: "image.bin", binary: true, original: null, modified: null },
  { path: "literal[1].txt", original: "", modified: " " },
];
const paths = options => filterCommitFiles(files, options).map(file => file.path);

test("match index locates literal text in both revisions with Monaco columns", () => {
  const file = { path: "a.py", original: "😀TEXT\r\ntext text", modified: "text\nTEXT" };
  const matches = indexCommitMatches([file], "TeXt").get(file);
  assert.deepEqual(matches.map(({ side, range }) => [side, range.startLineNumber, range.startColumn, range.endLineNumber, range.endColumn]), [
    ["original", 1, 3, 1, 7], ["original", 2, 1, 2, 5], ["original", 2, 6, 2, 10],
    ["modified", 1, 1, 1, 5], ["modified", 2, 1, 2, 5],
  ]);
  assert.ok(matches.every(match => match.file === file));
  assert.equal(indexCommitMatches(files, "[1].").get(files[4])[0].start, 7);
  assert.equal(indexCommitMatches(files, "").size, 0);
  assert.ok([...indexCommitMatches(files, "missing").values()].every(found => found.length === 0));
});

test("match index includes renamed and binary paths and multiline text", () => {
  assert.equal(indexCommitMatches(files, "legacy").get(files[0])[0].side, "path");
  assert.equal(indexCommitMatches(files, "image").get(files[3])[0].side, "path");
  const file = { path: "a.txt", original: "hello\nworld", modified: "" };
  assert.deepEqual(indexCommitMatches([file], "lo\nwo").get(file)[0].range,
    { startLineNumber: 1, startColumn: 4, endLineNumber: 2, endColumn: 3 });
});

test("commit search finds either revision, renamed paths and binary filenames", () => {
  assert.deepEqual(paths({ query: "REMOVED text" }), ["src/app.py"]);
  assert.deepEqual(paths({ query: "new text" }), ["src/app.py"]);
  assert.deepEqual(paths({ query: "legacy" }), ["src/app.py"]);
  assert.deepEqual(paths({ query: "image" }), ["image.bin"]);
  assert.deepEqual(paths({ query: "missing" }), []);
  assert.deepEqual(paths({}), files.map(file => file.path));
});

test("include and exclude combine with text search; exclusions win on either rename path", () => {
  assert.deepEqual(paths({ include: "src/**, *.json", exclude: "*.test.py", query: "text" }), ["src/app.py"]);
  assert.deepEqual(paths({ include: "src/**", exclude: "legacy/**" }), ["src/deep/app.test.py"]);
  assert.deepEqual(paths({ include: "legacy/**" }), ["src/app.py"]);
  assert.deepEqual(paths({ include: " ,  " }), files.map(file => file.path));
});

test("path globs handle nesting, root files, folders and literal regex punctuation", () => {
  assert.deepEqual(paths({ include: "**/*.json" }), ["config.json"]);
  assert.deepEqual(paths({ include: "src/*.py" }), ["src/app.py"]);
  assert.deepEqual(paths({ include: "./src/" }), ["src/app.py", "src/deep/app.test.py"]);
  assert.deepEqual(paths({ include: "*.py" }), ["src/app.py", "src/deep/app.test.py"]);
  assert.deepEqual(paths({ include: "src/???.py" }), ["src/app.py"]);
  assert.deepEqual(paths({ include: "literal[1].txt" }), ["literal[1].txt"]);
  assert.deepEqual(paths({ include: "[" }), []);
});

test("five exclusions filter cached search results without rereading file contents", () => {
  let contentReads = 0;
  const extensions = ["py", "tsx", "json", "js", "bin", "sh"];
  const largeCommit = Array.from({ length: 1200 }, (_, index) => ({
    path: `src/dir-${index}/file.${extensions[index % extensions.length]}`,
    get original() { contentReads++; return "original matching content"; },
    get modified() { contentReads++; return "modified matching content"; },
  }));
  assert.equal(searchCommitFiles(largeCommit), largeCommit);
  assert.equal(contentReads, 0);
  const searched = searchCommitFiles(largeCommit, "MATCHING");
  assert.equal(searched.length, 1200);
  const readsAfterSearch = contentReads;
  const exclusions = ["*.tsx", "*.json", "*.js", "*.bin", "*.sh"];
  exclusions.forEach((_, index) => {
    const filtered = filterCommitFiles(searched, { exclude: exclusions.slice(0, index + 1).join(", ") });
    assert.equal(filtered.length, 1200 - (index + 1) * 200);
    assert.ok(filtered.every(file => largeCommit.includes(file)), "Stable file identities preserve mounted editors");
  });
  assert.equal(contentReads, readsAfterSearch);
});
