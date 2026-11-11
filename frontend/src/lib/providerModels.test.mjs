import assert from "node:assert/strict";
import test from "node:test";
import { menuModels } from "./providerModels.js";

const provider = {
  models: [
    { id: "claude-haiku-5-5", displayName: "Haiku 5.5", listed: true },
    { id: "sonnet[1m]", displayName: "Sonnet 5.5 (1M context)", listed: true },
    { id: "haiku", displayName: "Haiku alias (Haiku 5.5)", listed: false },
    { id: "gpt", displayName: "From a provider that doesn't send the field" },
  ],
};

test("menus leave out aliases of models that are already listed", () => {
  assert.deepEqual(menuModels(provider).map(model => model.id), ["claude-haiku-5-5", "sonnet[1m]", "gpt"]);
});

test("a saved alias stays visible where it is selected", () => {
  assert.deepEqual(menuModels(provider, "haiku").map(model => model.id), ["claude-haiku-5-5", "sonnet[1m]", "haiku", "gpt"]);
});

test("a missing provider has no models", () => {
  assert.deepEqual(menuModels(undefined, "haiku"), []);
});
