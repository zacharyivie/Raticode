const fs = require("node:fs");
const path = require("node:path");

// Feature previews the backend reads at startup. A stored choice wins over the
// inherited environment, so developers who launch with the variable keep it
// until they choose otherwise in Settings.
const BACKEND_FEATURES = Object.freeze({
  organizations: "RATICODE_EXPERIMENTAL_ORGANIZATIONS",
  "device-network": "RATICODE_DEVICE_EXPERIMENTAL_NETWORK",
});

function readFeatureChoices(file) {
  let stored;
  try { stored = JSON.parse(fs.readFileSync(file, "utf8")); }
  catch (error) { if (error.code === "ENOENT" || error instanceof SyntaxError) return {}; throw error; }
  const choices = {};
  for (const id of Object.keys(BACKEND_FEATURES)) {
    if (typeof stored?.[id] === "boolean") choices[id] = stored[id];
  }
  return choices;
}

function writeFeatureChoice(file, id, enabled) {
  if (!Object.hasOwn(BACKEND_FEATURES, id)) throw new Error("Unknown feature preview.");
  if (typeof enabled !== "boolean") throw new Error("Choose on or off.");
  const choices = { ...readFeatureChoices(file), [id]: enabled };
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${JSON.stringify(choices, null, 2)}\n`, "utf8");
  return choices;
}

function featureStates(choices, env) {
  return Object.fromEntries(Object.entries(BACKEND_FEATURES).map(([id, variable]) => [
    id, typeof choices[id] === "boolean" ? choices[id] : env[variable] === "1",
  ]));
}

function featureEnv(choices, env) {
  const next = { ...env };
  for (const [id, variable] of Object.entries(BACKEND_FEATURES)) {
    if (choices[id] === true) next[variable] = "1";
    else if (choices[id] === false) delete next[variable];
  }
  return next;
}

module.exports = { BACKEND_FEATURES, featureEnv, featureStates, readFeatureChoices, writeFeatureChoice };
