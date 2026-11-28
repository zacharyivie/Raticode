// Drops the git and app settings tests would otherwise inherit from Raticode, as
// tests/conftest.py does for the Python tests.
//
// Raticode runs agents' commands with its push block
// (url.raticode-push-blocked-ask-the-user://.pushInsteadOf) and the hook that removes
// Co-authored-by lines, both passed as GIT_CONFIG_* git config, and with its own
// GOFER_* and RATICODE_* settings, such as the data folder, command slots and UI
// tokens. A test repository that inherits the git config can't push to its own local
// remote, and a test that inherits the data folder could write to the real one.
//
// The switches a person sets to steer the tests stay: RATICODE_TEST_* (run-tests.cjs,
// test-tmp.cjs, background-display.cjs), RATICODE_SKIP_<BROWSER>_TESTS,
// RATICODE_FIREFOX_NETWORK_TESTS and the browser picks RATICODE_<BROWSER>_PATH.
//
// A test's own child processes are left alone: they inherit the marker and keep any
// setting the test gave them on purpose.
const MARKER = "RATICODE_TEST_ENV_CLEAN";
const KEEP = /^RATICODE_(?:TEST_|SKIP_[A-Z]+_TESTS$|FIREFOX_NETWORK_TESTS$|(?:CHROME|EDGE|FIREFOX)_PATH$)/;

function inheritedSetting(name) {
  // Windows names are case-insensitive.
  const key = name.toUpperCase();
  if (key === "GIT_CONFIG" || key.startsWith("GIT_CONFIG_") || key.startsWith("GOFER_")) return true;
  return key.startsWith("RATICODE_") && !KEEP.test(key);
}

// Removes the inherited settings from env in place, once per run, and returns it.
function dropInheritedSettings(env = process.env) {
  if (env[MARKER] === "1") return env;
  for (const name of Object.keys(env)) {
    if (inheritedSetting(name)) delete env[name];
  }
  env[MARKER] = "1";
  return env;
}

module.exports = { MARKER, dropInheritedSettings, inheritedSetting };
