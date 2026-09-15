import assert from "node:assert/strict";
import { test } from "node:test";
import compatibilityData from "../../electron/brand-compat.json" with { type: "json" };
import compatibility from "./brandCompat.js";
import { loadAppSettings, SETTINGS_STORAGE_KEY } from "./settings.js";

for (const legacy of compatibilityData.previousBrands) {
test("previous preferences migrate without overwriting current preferences", () => {
  const oldKey = `${legacy.brand}.settings.v1`;
  const values = new Map([[oldKey, JSON.stringify({
    browser: { homepage: `${legacy.brand}://home` },
    editor: { fontSize: 18 },
  })]]);
  const storage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  const settings = loadAppSettings(storage);
  assert.equal(settings.editor.fontSize, 18);
  assert.equal(settings.browser.homepage, "raticode://home");
  assert.ok(values.has(SETTINGS_STORAGE_KEY));
  assert.ok(values.has(oldKey));
  values.set(SETTINGS_STORAGE_KEY, JSON.stringify({ editor: { fontSize: 16 } }));
  assert.equal(loadAppSettings(storage).editor.fontSize, 16);
});

test("previous settings remain readable when storage rejects migration writes", () => {
  const storage = {
    getItem: (key) => key === `${legacy.brand}.textZoom.v1` ? "120" : null,
    setItem() { throw new Error("Storage is full"); },
  };
  assert.equal(compatibility.readBrandedStorage(storage, "raticode.textZoom.v1"), "120");
});

test("bundle chooser accepts current and previous extensions, with exact suffix checks", () => {
  for (const name of ["review.raticode", `review.${legacy.brand.toUpperCase()}`]) {
    assert.equal(compatibility.isWorkflowBundle({ name }), true);
  }
  for (const name of ["review.zip", "review.raticode.exe", "", null]) {
    assert.equal(compatibility.isWorkflowBundle({ name }), false);
  }
});

}
