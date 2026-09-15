import legacy from "../../electron/brand-compat.json" with { type: "json" };

const LEGACY_BRANDS = legacy.previousBrands.map((item) => item.brand);
const BUNDLE_ACCEPT = [".raticode", ...LEGACY_BRANDS.map((brand) => `.${brand}`)].join(",");

function isWorkflowBundle(file) {
  const name = file?.name?.toLowerCase?.() || "";
  return BUNDLE_ACCEPT.split(",").some((extension) => name.endsWith(extension));
}

function readBrandedStorage(storage, key) {
  const current = storage?.getItem(key);
  if (current != null) return current;
  const previous = LEGACY_BRANDS.map((brand) => storage?.getItem(key.replace(/^raticode\./, `${brand}.`)))
    .find((value) => value != null) ?? null;
  if (previous != null) {
    // Reads still work when browser policy or quota prevents persisting migration.
    try { storage?.setItem(key, previous); } catch { /* Retain the original value. */ }
  }
  return previous;
}

function canonicalHomeUrl(url) {
  return LEGACY_BRANDS.some((brand) => url === `${brand}://home`) ? "raticode://home" : url;
}

export default { BUNDLE_ACCEPT, isWorkflowBundle, readBrandedStorage, canonicalHomeUrl };
