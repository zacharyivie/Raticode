// A temporary folder for one test, removed when that test ends, even when it fails.
//
// tempFolder(prefix, t) makes a folder under os.tmpdir() and removes it in t.after().
// Without t it uses node:test's after(): called inside a running test, that hook
// belongs to the test, so a helper such as a harness() the test calls cleans up too.
// Called at the top of a file, it runs when the file's tests are done.
//
// A real Firefox keeps writing its profile while it shuts down, and can put files back
// into a folder that was just removed. stopBeforeRemoving(folder, engine) stops the
// engine and waits for it (stopAndWait, or else close or stop) before the folder goes.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { after } = require("node:test");

const engines = new Map();

async function stopEngine(engine) {
  if (typeof engine.stopAndWait === "function") await engine.stopAndWait();
  else if (typeof engine.close === "function") await engine.close();
  else if (typeof engine.stop === "function") await engine.stop();
}

function tempFolder(prefix, t) {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  engines.set(folder, []);
  const hook = t && typeof t.after === "function" ? t.after.bind(t) : after;
  hook(async () => {
    const running = engines.get(folder) || [];
    engines.delete(folder);
    try {
      // The last one started stops first, as a finally block would.
      for (const engine of running.reverse()) await stopEngine(engine);
    } finally {
      fs.rmSync(folder, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
    }
  });
  return folder;
}

function stopBeforeRemoving(folder, engine) {
  const running = engines.get(folder);
  if (!running) throw new Error(`${folder} is not a folder from tempFolder() that is still in use`);
  running.push(engine);
  return engine;
}

module.exports = { tempFolder, stopBeforeRemoving };
