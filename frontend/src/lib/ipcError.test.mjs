import assert from "node:assert/strict";
import test from "node:test";
import { ipcErrorMessage } from "./ipcError.js";

test("ipcErrorMessage drops Electron's remote-method prefix and keeps the message", () => {
  assert.equal(ipcErrorMessage(new Error("Error invoking remote method 'gofer-browser:library': Error: Chrome was not found.")), "Chrome was not found.");
  assert.equal(ipcErrorMessage(new Error("Error invoking remote method 'gecko': Profile is locked")), "Profile is locked");
  assert.equal(ipcErrorMessage("Error invoking remote method 'x': Error: plain string"), "plain string");
  assert.equal(ipcErrorMessage(new Error("Nothing to strip")), "Nothing to strip");
});

test("ipcErrorMessage uses the caller's fallback when the failure has no message", () => {
  assert.equal(ipcErrorMessage(null, "Raticode (gecko) could not start."), "Raticode (gecko) could not start.");
  assert.equal(ipcErrorMessage(new Error(""), "Raticode (gecko) could not start."), "Raticode (gecko) could not start.");
  assert.equal(ipcErrorMessage(undefined), "That didn't work.");
});
