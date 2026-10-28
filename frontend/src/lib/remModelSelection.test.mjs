import assert from "node:assert/strict";
import { test } from "node:test";
import { previousRemModelSelection, remModelSelection, remModelSwitchBanner } from "./remModelSelection.js";

test("Rem compares submitted settings and labels provider defaults honestly", () => {
  const selection = remModelSelection("codex", "sol", "", { models: [{ id: "sol", defaultEffort: "high" }] });
  assert.equal(remModelSwitchBanner(undefined, selection, "Codex"), null);
  assert.equal(remModelSwitchBanner({ ...selection }, selection, "Codex"), null);
  assert.equal(remModelSwitchBanner({ ...selection, effort: "medium" }, selection, "Codex"), "switched to Codex - sol - high");
  assert.equal(remModelSwitchBanner(selection, remModelSelection("cursor", "", ""), "Cursor"), "switched to Cursor - CLI default - default effort");
});

test("Rem finds the last submitted selection beyond a page of tool output", async () => {
  const selection = { provider: "codex", model: "sol", effort: "high" };
  const pagesRead = [];
  const repository = {
    durable: true,
    async page(id, before) {
      assert.equal(id, "thread");
      pagesRead.push(before);
      return before === Infinity
        ? { messages: [{ role: "assistant", body: "Tools" }], before: 40, hasMore: true }
        : { messages: [{ role: "user", remModelSelection: selection }], before: 0, hasMore: false };
    },
  };
  assert.deepEqual(await previousRemModelSelection(repository, "thread", [{ role: "assistant" }]), selection);
  assert.deepEqual(pagesRead, [Infinity, 40]);
});

test("an edited turn compares against the preceding turn, including unloaded history", async () => {
  const previous = { provider: "codex", model: "sol", effort: "high" };
  const current = { provider: "codex", model: "astra", effort: "high" };
  const history = [{ id: "first", role: "user", remModelSelection: previous }, { id: "edit", role: "user", remModelSelection: current }];
  assert.deepEqual(await previousRemModelSelection({ durable: false }, "thread", history, "edit"), previous);
  assert.equal(await previousRemModelSelection({ durable: false }, "thread", [{ role: "user" }]), undefined);
  assert.deepEqual(await previousRemModelSelection({
    durable: true,
    async around(id, messageId, limit) {
      assert.deepEqual([id, messageId, limit], ["thread", "edit", 1]);
      return { before: 42 };
    },
    async page(id, before) {
      assert.equal(before, 42);
      return { messages: [history[0]], hasMore: false };
    },
  }, "thread", [history[1]], "edit"), previous);
});
