import test from "node:test";
import assert from "node:assert/strict";
import { layoutHistoryGraph, filterHistory, commitLabels, commitStats } from "./gitHistoryGraph.js";

const commits = [
  { hash: "merge", subject: "match merge", parents: ["local", "remote"] },
  { hash: "remote", subject: "remote work", author: "Ada", parents: ["base"], labels: [{ name: "origin/main", type: "remote" }] },
  { hash: "local", subject: "local work", parents: ["base"] },
  { hash: "base", subject: "match base", parents: [] },
];
test("merge and diverging lanes join at the common ancestor without duplicate nodes", () => {
  const { rows, width } = layoutHistoryGraph(commits);
  assert.equal(rows.length, 4);
  assert.equal(width, 32);
  assert.equal(rows[0].segments.length, 2);
  assert.notEqual(rows[1].lane, rows[2].lane);
  assert.ok(rows[2].segments.some(edge => !edge.full && !edge.top && edge.from !== edge.to));
  assert.equal(rows[3].segments.filter(edge => !edge.top).length, 0);
  for (const row of rows) assert.ok(row.segments.every(edge => Number.isInteger(edge.to)));
});
test("search preserves ancestry through hidden merges and searches refs, authors and hashes", () => {
  const filtered = filterHistory(commits, "match");
  assert.deepEqual(filtered.map(commit => commit.hash), ["merge", "base"]);
  assert.deepEqual(filtered[0].parents, ["base"]);
  assert.equal(filterHistory(commits, "origin/main Ada")[0].hash, "remote");
  assert.equal(filterHistory(commits, "LOCAL")[0].hash, "local");
  assert.equal(filterHistory(commits, "absent").length, 0);
  assert.equal(filterHistory(commits, "  "), commits);
  assert.deepEqual(commits[0].parents, ["local", "remote"]);
});
test("disconnected roots and missing parents remain separate", () => {
  const { rows } = layoutHistoryGraph([{ hash: "a", parents: ["older"] }, { hash: "b", parents: [] }]);
  assert.notEqual(rows[0].lane, rows[1].lane);
  assert.ok(rows[1].segments.some(edge => edge.full));
  assert.deepEqual(layoutHistoryGraph([]).rows, []);
  assert.equal(commitLabels({ refs: "HEAD -> feature/topic, origin/topic, tag: v1" })[0].current, true);
});

test("commit line counts describe exactly what git reported", () => {
  assert.deepEqual(commitStats({ insertions: 17, deletions: 3, binaryFiles: 1, parents: ["a"] }), {
    insertions: 17, deletions: 3, binaryFiles: 1, lines: true, label: "17 insertions, 3 deletions, 1 binary file without line counts",
  });
  assert.equal(commitStats({ insertions: 1, deletions: 1, binaryFiles: 0 }).label, "1 insertion, 1 deletion");
  const empty = commitStats({ insertions: 0, deletions: 0, binaryFiles: 0 });
  assert.equal(empty.lines, true, "A commit without changed lines still shows +0 -0");
  const binaryOnly = commitStats({ insertions: 0, deletions: 0, binaryFiles: 2 });
  assert.equal(binaryOnly.lines, false, "Binary-only commits must not claim zero changed lines");
  assert.equal(binaryOnly.label, "0 insertions, 0 deletions, 2 binary files without line counts");
  assert.match(commitStats({ insertions: 4, deletions: 2, binaryFiles: 0, parents: ["a", "b"] }).label, /compared with the first parent$/);
  for (const missing of [{}, { insertions: 5 }, { insertions: 5, deletions: null }, { insertions: "5", deletions: 1 }, { insertions: -1, deletions: 0 }, { insertions: 1.5, deletions: 0 }]) {
    assert.equal(commitStats(missing), null, "Missing or invalid counts must show nothing rather than +0");
  }
});
