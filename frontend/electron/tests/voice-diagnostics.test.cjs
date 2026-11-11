const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const { createSystemMicrophoneGate, describeSpeechHelper } = require("../voice-diagnostics.cjs");
const policy = require("../studio-policy.cjs");

function fakePreferences(status, answer = true) {
  const asked = [];
  return {
    asked,
    getMediaAccessStatus: () => status,
    async askForMediaAccess(type) {
      asked.push(type);
      status = answer ? "granted" : "denied";
      return answer;
    },
  };
}

function helperFixture(t, mode = 0o755) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-speech-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const helper = path.join(dir, "raticode-speech");
  fs.writeFileSync(helper, "");
  fs.chmodSync(helper, mode);
  return helper;
}

test("the speech helper is described by what is at its path", (t) => {
  const helper = helperFixture(t);
  assert.equal(describeSpeechHelper(helper), `present at ${helper}`);
  assert.equal(describeSpeechHelper(`${helper}-gone`), `missing at ${helper}-gone`);
  assert.equal(describeSpeechHelper(path.dirname(helper)), `not a file at ${path.dirname(helper)}`);
  assert.equal(describeSpeechHelper(""), "no path");
  if (process.getuid?.() !== 0) {
    fs.chmodSync(helper, 0o644);
    assert.equal(describeSpeechHelper(helper), `not executable at ${helper}`);
  }
});

test("only macOS has a microphone gate", () => {
  assert.equal(createSystemMicrophoneGate({ platform: "linux", systemPreferences: fakePreferences("granted") }), null);
  assert.equal(createSystemMicrophoneGate({ platform: "darwin", systemPreferences: {} }), null);
});

test("macOS asks once, and a refused microphone is refused and logged with where to fix it", async (t) => {
  const helper = helperFixture(t);
  const logs = [];
  const log = (level, message) => logs.push([level, message]);

  const granted = fakePreferences("granted");
  const allow = createSystemMicrophoneGate({ platform: "darwin", systemPreferences: granted, helperPath: () => helper, log });
  assert.equal(await allow(), true);
  assert.equal(await allow(), true);
  assert.deepEqual(granted.asked, []);
  // The same answer is logged once.
  assert.deepEqual(logs, [["info", `Dictation: macOS microphone access for Raticode is granted; speech helper present at ${helper}`]]);

  logs.length = 0;
  const first = fakePreferences("not-determined", false);
  const ask = createSystemMicrophoneGate({ platform: "darwin", systemPreferences: first, helperPath: `${helper}-gone`, log });
  assert.equal(await ask(), false);
  assert.deepEqual(first.asked, ["microphone"]);
  assert.equal(logs[0][0], "warn");
  assert.match(logs[0][1], /is denied \(asked; it was not-determined\); speech helper missing at .*-gone\./);
  assert.match(logs[0][1], /System Settings > Privacy & Security > Microphone/);

  logs.length = 0;
  const restricted = createSystemMicrophoneGate({ platform: "darwin", systemPreferences: fakePreferences("restricted"), helperPath: helper, log });
  assert.equal(await restricted(), false);
  assert.equal(logs[0][0], "warn");
  // An unknown state isn't a refusal.
  assert.equal(await createSystemMicrophoneGate({ platform: "darwin", systemPreferences: fakePreferences("unknown"), log })(), true);
});

test("the studio's microphone request waits for macOS, and a failed check never blocks it", async () => {
  const fakeSession = () => ({
    setPermissionRequestHandler(fn) { this.request = fn; },
    setPermissionCheckHandler(fn) { this.check = fn; },
    setDevicePermissionHandler(fn) { this.device = fn; },
  });
  const indexPath = path.resolve(__dirname, "../../dist/index.html");
  const contents = { getURL: () => pathToFileURL(indexPath).href };
  const options = { indexPath, isProduction: true, devServerUrl: "http://127.0.0.1:5173" };
  const ask = (systemMicrophone, permission = "media", details = { mediaTypes: ["audio"], requestingUrl: contents.getURL() }) => {
    const studio = fakeSession();
    policy.installPermissionPolicy(fakeSession(), studio, () => contents, { ...options, systemMicrophone });
    return new Promise((resolve) => studio.request(contents, permission, resolve, details));
  };
  assert.equal(await ask(async () => true), true);
  assert.equal(await ask(async () => false), false);
  assert.equal(await ask(async () => { throw new Error("broken"); }), true);
  assert.equal(await ask(undefined), true);
  // The policy still decides first: video is never asked about.
  let consulted = false;
  assert.equal(await ask(async () => { consulted = true; return true; }, "media", { mediaTypes: ["video"] }), false);
  assert.equal(consulted, false);
  assert.equal(await ask(async () => { consulted = true; return true; }, "clipboard-sanitized-write", {}), true);
  assert.equal(consulted, false);
});
