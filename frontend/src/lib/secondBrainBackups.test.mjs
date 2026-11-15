import assert from "node:assert/strict";
import { test } from "node:test";
import { DEFAULT_BACKUP, archiveLabel, backupSettings, packageFit, scheduleLabel } from "./secondBrainBackups.js";

test("brains saved before backups existed get the off-by-default settings", () => {
  assert.deepEqual(backupSettings({ id: "b" }), DEFAULT_BACKUP);
  assert.equal(backupSettings({ backup: { keep: 3 } }).keep, 3);
  assert.equal(DEFAULT_BACKUP.conversations, false);
});

test("the schedule reads as a plain sentence", () => {
  assert.equal(scheduleLabel(DEFAULT_BACKUP), "Off");
  assert.equal(scheduleLabel({ ...DEFAULT_BACKUP, enabled: true }), "Every day at 03:00");
  assert.equal(scheduleLabel({ ...DEFAULT_BACKUP, enabled: true, frequency: "weekly", weekday: 4, time: "21:30" }), "Every Friday at 21:30");
});

test("the conversation archive decides whether a package fits the 1 GB limit", () => {
  const limit = 1024 ** 3;
  const estimate = { total: 1.068 * limit, conversations: 1.034 * limit, limit, archives: ["Rem Conversations"] };
  assert.equal(packageFit(estimate, true).fits, false);
  const without = packageFit(estimate, false);
  assert.equal(without.fits, true);
  assert.ok(Math.abs(without.size - 0.034 * limit) < 1);
  assert.equal(packageFit(null, true), null);
  assert.match(archiveLabel(estimate), /^Include Rem’s conversation archive \(1\.0\d? GB\)$/);
  assert.equal(archiveLabel({ ...estimate, conversations: 0 }), "Include Rem’s conversation archive");
});
