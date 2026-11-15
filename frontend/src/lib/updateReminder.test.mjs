import assert from "node:assert/strict";
import test from "node:test";

import { updateReminderText } from "./updateReminder.js";

test("the update reminder names the new and the running version", () => {
  assert.equal(
    updateReminderText({ available: true, currentVersion: "0.5.4", info: { version: "0.5.6" }, reminder: { version: "0.5.5", day: "2026-11-13" } }),
    "Raticode 0.5.6 is available. You have 0.5.4.",
  );
  assert.equal(updateReminderText({ available: true, reminder: { version: "0.5.5" } }), "Raticode 0.5.5 is available.");
});

test("no reminder once the update is installed or the reminder is dismissed", () => {
  assert.equal(updateReminderText({ available: false, reminder: { version: "0.5.5" } }), "");
  assert.equal(updateReminderText({ available: true, reminder: null, info: { version: "0.5.5" } }), "");
  assert.equal(updateReminderText(null), "");
});
