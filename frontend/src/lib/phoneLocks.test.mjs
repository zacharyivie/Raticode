import assert from "node:assert/strict";
import test from "node:test";
import { phoneLockNotice, phoneLocks } from "./phoneLocks.js";

test("locked phones are named from the paired devices list", () => {
  const status = {
    peers: [{ device_id: "p1", name: "Pixel" }, { device_id: "p2", name: null }],
    phone_guard: { enabled: true, locked: [{ device_id: "p1", locked_at: 10, reasons: ["asks for credentials"] }, { device_id: "p2", locked_at: 11 }], flagged: { p1: 1 } },
  };
  assert.deepEqual(phoneLocks(status), [
    { deviceId: "p1", name: "Pixel", lockedAt: 10, reasons: ["asks for credentials"] },
    { deviceId: "p2", name: "Your phone", lockedAt: 11, reasons: [] },
  ]);
  assert.match(phoneLockNotice(phoneLocks(status)), /^2 phones are locked/);
  assert.match(phoneLockNotice(phoneLocks(status).slice(0, 1)), /^Pixel is locked: Raticode stopped a suspicious request from it\./);
});

test("an older desktop without the guard shows no notice", () => {
  assert.deepEqual(phoneLocks({ peers: [] }), []);
  assert.deepEqual(phoneLocks(null), []);
  assert.equal(phoneLockNotice([]), "");
});
