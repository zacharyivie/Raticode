// How a ticket's patch reads in its sheet: where it lands and whether Accept can land it.

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

// One line about the merge, and whether Accept can land the branch right now.
export function mergeStatus(patch) {
  if (!patch?.branch) return { tone: "idle", text: "", canLand: false };
  if (patch.landed?.branch === patch.branch) return { tone: "done", text: `Landed in ${patch.landed.baseBranch}`, canLand: false, landed: true };
  const base = patch.baseBranch;
  if (!base) return { tone: "warn", text: "Raticode doesn't know which branch this came from, so it can't land it.", canLand: false };
  const state = patch.merge?.state;
  if (state === "landed") return { tone: "done", text: `Already in ${base}`, canLand: true, landed: true };
  if (state === "conflict") return { tone: "bad", text: `Conflicts with ${base} in ${plural(patch.merge.conflicts.length, "file")}`, canLand: false, conflicts: patch.merge.conflicts };
  if (state === "fast_forward") return { tone: "ok", text: `Lands cleanly in ${base} (fast-forward)`, canLand: true };
  if (state === "clean") return { tone: "ok", text: `Merges cleanly into ${base}`, canLand: true };
  return { tone: "idle", text: "", canLand: false };
}

// "3 files, +12 −4" from the full before and after text of each file.
export function patchSize(files = []) {
  let added = 0, removed = 0;
  for (const file of files) {
    if (file.binary) continue;
    const before = lineCounts(file.original), after = lineCounts(file.modified);
    for (const [line, count] of after) added += Math.max(0, count - (before.get(line) || 0));
    for (const [line, count] of before) removed += Math.max(0, count - (after.get(line) || 0));
  }
  return { files: files.length, added, removed };
}

function lineCounts(text) {
  const counts = new Map();
  if (!text) return counts;
  for (const line of text.split("\n")) counts.set(line, (counts.get(line) || 0) + 1);
  return counts;
}

// The Accept button's words: what will actually happen.
export function acceptLabel(patch) {
  const status = mergeStatus(patch);
  if (!patch?.branch || !status.canLand) return "Accept";
  if (status.landed) return "Accept";
  return `Accept and land in ${patch.baseBranch}`;
}

// What the board says after Accept: landed, or the conflict that stopped it.
export function landedMessage(result) {
  if (!result) return "";
  if (result.conflict) return result.message || `${result.branch} conflicts with ${result.baseBranch}; nothing was landed.`;
  if (result.how === "landed") return `${result.branch} was already in ${result.baseBranch}.`;
  return `Landed ${result.branch} in ${result.baseBranch}.`;
}

let nextKey = 0;
export const commentKey = () => `c${++nextKey}`;
