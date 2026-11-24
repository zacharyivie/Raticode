// "Let Rem use Raticode (gecko)" changes three prefs in the user's Gecko
// profile. Firefox copies user.js values into prefs.js, where they outlive the
// user.js line, so turning the setting off must write the old values back.
// Checked against prefs.js fixtures, and against a real headless Firefox with
// a throwaway profile (skipped when Firefox is missing), including a saved
// login that must not land in Rem's tabs.
require("./test-tmp.cjs");
const test = require("node:test");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { findFirefox } = require("../firefox-engine.cjs");
const { prefLine, readPrefs } = require("../firefox-profile.cjs");
const { createGeckoControl, listeningAddress } = require("../gecko-control.cjs");
const { FIREFOX_DEFAULTS, PROFILE_PREFS, REM_CONTROL_PREFS, REM_PREFS_RECORD, remControlPrefLines } = require("../gecko-window.cjs");

const FIREFOX = process.env.RATICODE_SKIP_FIREFOX_TESTS === "1" ? "" : findFirefox();
const AUTOPLAY = "media.block-autoplay-until-in-foreground";
const RECOMMENDED = "remote.prefs.recommended";
const AUTOFILL = "signon.autofillForms";

function profile(prefsText = "") {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-gecko-prefs-"));
  if (prefsText) fs.writeFileSync(path.join(directory, "prefs.js"), prefsText);
  return directory;
}

// The prefs a list of user.js lines sets, read the way Raticode reads prefs.js.
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-gecko-lines-"));
test.after(() => fs.rmSync(scratch, { recursive: true, force: true }));
function parse(lines) {
  const file = path.join(scratch, "user.js");
  fs.writeFileSync(file, lines.join("\n"));
  return readPrefs(file);
}

test("turning Rem control off puts the user's own values back, then stops writing them", (t) => {
  // The user had chosen to let background tabs play; that choice comes back.
  const directory = profile(`user_pref("${AUTOPLAY}", false);\nuser_pref("browser.startup.page", 3);\n`);
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  assert.deepEqual([...parse(remControlPrefLines(directory, true))], Object.entries(REM_CONTROL_PREFS));
  const record = path.join(directory, REM_PREFS_RECORD);
  assert.equal(fs.statSync(record).mode & 0o777, 0o600);
  assert.deepEqual(JSON.parse(fs.readFileSync(record, "utf8")).original, { [AUTOPLAY]: false, [RECOMMENDED]: null, [AUTOFILL]: null });
  // Turning it on again keeps what the user had before the first time.
  fs.writeFileSync(path.join(directory, "prefs.js"), `user_pref("${AUTOPLAY}", false);\nuser_pref("${RECOMMENDED}", false);\nuser_pref("${AUTOFILL}", false);\n`);
  remControlPrefLines(directory, true);
  assert.deepEqual(JSON.parse(fs.readFileSync(record, "utf8")).original, { [AUTOPLAY]: false, [RECOMMENDED]: null, [AUTOFILL]: null });
  // Off: the user's false stays false, and Firefox's defaults come back for the others.
  assert.deepEqual(Object.fromEntries(parse(remControlPrefLines(directory, false))), { [AUTOPLAY]: false, [RECOMMENDED]: true, [AUTOFILL]: true });
  assert.equal(fs.existsSync(record), true);
  // Once prefs.js shows them, the record goes and user.js says nothing more.
  fs.writeFileSync(path.join(directory, "prefs.js"), `user_pref("${AUTOPLAY}", false);\n`);
  assert.deepEqual(remControlPrefLines(directory, false), []);
  assert.equal(fs.existsSync(record), false);
  assert.deepEqual(remControlPrefLines(directory, false), []);
});

test("a profile turned on before saved passwords were covered records the user's own autofill choice", (t) => {
  // The record from then has two prefs, and prefs.js still holds the user's own autofill choice.
  const chosen = profile(`user_pref("${AUTOPLAY}", false);\nuser_pref("${RECOMMENDED}", false);\nuser_pref("${AUTOFILL}", false);\n`);
  const unset = profile(`user_pref("${AUTOPLAY}", false);\nuser_pref("${RECOMMENDED}", false);\n`);
  t.after(() => { for (const directory of [chosen, unset]) fs.rmSync(directory, { recursive: true, force: true }); });
  for (const directory of [chosen, unset]) {
    fs.writeFileSync(path.join(directory, REM_PREFS_RECORD), JSON.stringify({ original: { [AUTOPLAY]: null, [RECOMMENDED]: null } }), { mode: 0o600 });
    assert.deepEqual([...parse(remControlPrefLines(directory, true))], Object.entries(REM_CONTROL_PREFS));
  }
  const original = (directory) => JSON.parse(fs.readFileSync(path.join(directory, REM_PREFS_RECORD), "utf8")).original;
  assert.deepEqual(original(chosen), { [AUTOPLAY]: null, [RECOMMENDED]: null, [AUTOFILL]: false });
  assert.deepEqual(original(unset), { [AUTOPLAY]: null, [RECOMMENDED]: null, [AUTOFILL]: null });
  // Firefox has now copied Rem's values into prefs.js; turning off puts each choice back.
  for (const directory of [chosen, unset]) {
    fs.writeFileSync(path.join(directory, "prefs.js"), Object.entries(REM_CONTROL_PREFS).map(([name, value]) => `user_pref("${name}", ${value});`).join("\n"));
  }
  assert.deepEqual(Object.fromEntries(parse(remControlPrefLines(chosen, false))), { ...FIREFOX_DEFAULTS, [AUTOFILL]: false });
  assert.deepEqual(Object.fromEntries(parse(remControlPrefLines(unset, false))), FIREFOX_DEFAULTS);
});

test("a profile Rem control never touched gets no lines, and a broken record falls back to Firefox's defaults", (t) => {
  const untouched = profile(`user_pref("${AUTOPLAY}", false);\n`);
  const broken = profile(`user_pref("${AUTOPLAY}", false);\n`);
  t.after(() => { for (const directory of [untouched, broken]) fs.rmSync(directory, { recursive: true, force: true }); });
  assert.deepEqual(remControlPrefLines(untouched, false), []);
  fs.writeFileSync(path.join(broken, REM_PREFS_RECORD), JSON.stringify({ original: { [AUTOPLAY]: { evil: 1 } } }));
  assert.deepEqual(Object.fromEntries(parse(remControlPrefLines(broken, false))), FIREFOX_DEFAULTS);
});

// One start of headless Firefox on the profile, as Gecko starts: Raticode
// writes user.js first. --screenshot loads a page and quits cleanly, which is
// when Firefox saves prefs.js.
async function runFirefox(directory, lines) {
  fs.writeFileSync(path.join(directory, "user.js"), lines.join("\n") + "\n");
  const home = path.join(directory, "..", `${path.basename(directory)}-home`);
  fs.mkdirSync(home, { recursive: true });
  const env = { ...process.env, HOME: home, MOZ_HEADLESS: "1", MOZ_CRASHREPORTER_DISABLE: "1", DBUS_SESSION_BUS_ADDRESS: "unix:path=/nonexistent/raticode-test-bus" };
  delete env.DISPLAY;
  delete env.WAYLAND_DISPLAY;
  const child = spawn(FIREFOX, ["--headless", "--no-remote", "--profile", directory, "--screenshot", path.join(directory, "shot.png"), "about:blank"], { env, stdio: "ignore" });
  const code = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill("SIGKILL"); reject(new Error("Firefox did not quit.")); }, 60000);
    child.once("exit", (exitCode) => { clearTimeout(timer); resolve(exitCode); });
  });
  fs.rmSync(home, { recursive: true, force: true });
  return code;
}

test("real Firefox: on, then off, leaves prefs.js as it was and removes the record", { skip: FIREFOX ? false : "Firefox is not installed", timeout: 180000 }, async (t) => {
  const directory = profile();
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  await runFirefox(directory, []);
  const before = readPrefs(path.join(directory, "prefs.js"));
  assert.equal(before.has(AUTOPLAY), false);

  // Firefox sometimes quits from --screenshot without saving prefs.js. Gecko
  // writes the same user.js lines on every start, so the next start catches up;
  // the test starts again the same way, a few times at most.
  let on = new Map();
  for (let start = 0; start < 4 && on.get(AUTOPLAY) !== false; start += 1) {
    await runFirefox(directory, remControlPrefLines(directory, true));
    on = readPrefs(path.join(directory, "prefs.js"));
  }
  assert.equal(on.get(AUTOPLAY), false, "Rem control lets background tabs play");
  assert.equal(on.get(RECOMMENDED), false);
  assert.equal(on.get(AUTOFILL), false, "Rem control stops saved passwords filling in by themselves");

  // The leak this guards against: without the restore, prefs.js kept false here.
  let off = remControlPrefLines(directory, false);
  assert.deepEqual(Object.fromEntries(parse(off)), FIREFOX_DEFAULTS);
  // Until prefs.js shows the old values, each start writes them again. Once it
  // does, the next start drops the record and writes nothing.
  for (let start = 0; start < 4 && off.length; start += 1) {
    await runFirefox(directory, off);
    off = remControlPrefLines(directory, false);
  }
  assert.deepEqual(off, []);
  const restored = readPrefs(path.join(directory, "prefs.js"));
  for (const name of [AUTOPLAY, RECOMMENDED, AUTOFILL]) {
    assert.ok(!restored.has(name) || restored.get(name) === FIREFOX_DEFAULTS[name], `${name} is back to Firefox's default`);
  }
  assert.equal(fs.existsSync(path.join(directory, REM_PREFS_RECORD)), false);
  assert.deepEqual(remControlPrefLines(directory, false), []);
  await runFirefox(directory, []);
  const later = readPrefs(path.join(directory, "prefs.js"));
  assert.ok(!later.has(AUTOPLAY) || later.get(AUTOPLAY) === true);
  assert.ok(!later.has(AUTOFILL) || later.get(AUTOFILL) === true);
});

const SIGN_IN = `<!doctype html><title>Sign in</title><form method=post action=/session>
<input id=u name=username autocomplete=username><input id=p type=password name=password autocomplete=current-password>
<button>Sign in</button></form>`;

// Headless Firefox with Rem's control connection, a saved login for a local
// sign-in page, and Rem's container. System access is only for saving the
// login through Firefox's password manager; Raticode never asks for it.
// Returns what the sign-in form holds in Rem's tab and in the user's tab.
async function signInFields(prefs) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-gecko-logins-"));
  const server = http.createServer((_request, response) => { response.setHeader("Content-Type", "text/html"); response.end(SIGN_IN); });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const site = `http://127.0.0.1:${server.address().port}`;
  const directory = path.join(root, "profile");
  fs.mkdirSync(directory, { mode: 0o700 });
  fs.writeFileSync(path.join(directory, "user.js"), Object.entries({ ...PROFILE_PREFS, ...prefs }).map(([name, value]) => prefLine(name, value)).join("\n"));
  const home = path.join(root, "home");
  fs.mkdirSync(home);
  // Saving the test login needs Firefox's own (chrome) scope. Firefox 158 accepts
  // --remote-allow-system-access alone; the CI runner's Firefox also asks for this variable.
  const env = { ...process.env, HOME: home, MOZ_HEADLESS: "1", MOZ_CRASHREPORTER_DISABLE: "1", MOZ_REMOTE_ALLOW_SYSTEM_ACCESS: "1", DBUS_SESSION_BUS_ADDRESS: "unix:path=/nonexistent/raticode-test-bus" };
  for (const name of ["DISPLAY", "WAYLAND_DISPLAY", ...Object.keys(env).filter((key) => /_proxy$/i.test(key))]) delete env[name];
  const firefox = spawn(FIREFOX, ["--headless", "--new-instance", "--profile", directory, "--remote-debugging-port", "0", "--remote-allow-system-access", "about:blank"], { env, stdio: ["ignore", "ignore", "pipe"] });
  const control = createGeckoControl({ stagingDir: path.join(root, "downloads") });
  control.setHost({ enabled: () => true, available: () => ({ available: true, executable: FIREFOX }), start: async () => { throw new Error("already started"); } });
  try {
    const address = await new Promise((resolve, reject) => {
      let text = "";
      const timer = setTimeout(() => reject(new Error("Firefox did not open its control port.")), 60000);
      firefox.stderr.on("data", (chunk) => {
        text = (text + chunk).slice(-8000);
        const found = listeningAddress(text);
        if (found) { clearTimeout(timer); resolve(found); }
      });
      firefox.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Firefox exited (${code}).`)); });
    });
    await control.attach(address, { kill: () => firefox.kill("SIGKILL") });
    const evaluate = async (context, expression) => (await control.send("script.evaluate", { expression, target: { context }, awaitPromise: true })).result?.value;
    const browserWindow = (await control.send("browsingContext.getTree", { "moz:scope": "chrome" })).contexts.find((item) => item.url.includes("browser.xhtml"));
    const saved = await evaluate(browserWindow.context, `(async () => {
      const LoginInfo = Components.Constructor("@mozilla.org/login-manager/loginInfo;1", Ci.nsILoginInfo, "init");
      await Services.logins.addLoginAsync(new LoginInfo(${JSON.stringify(site)}, ${JSON.stringify(site)}, null, "ada", "s3cret-pw", "username", "password"));
      return (await Services.logins.getAllLogins()).length;
    })()`);
    assert.equal(saved, 1);
    const fields = (context) => evaluate(context, "[document.getElementById('u').value, document.getElementById('p').value].join('|')");
    const settle = () => new Promise((resolve) => setTimeout(resolve, 2000));

    // Rem's tab opens in the background, in its container.
    const remTab = (await control.send("browsingContext.create", { type: "tab", background: true, userContext: control.remUserContext })).context;
    await control.send("browsingContext.navigate", { context: remTab, url: `${site}/sign-in`, wait: "complete" });
    await settle();
    const remHidden = await fields(remTab);
    // Firefox fills a hidden page once it is shown, so the user looks at Rem's tab.
    await control.send("browsingContext.activate", { context: remTab });
    await settle();
    const remShown = await fields(remTab);

    const userTab = (await control.send("browsingContext.getTree", { maxDepth: 0 })).contexts.find((item) => !control.isRemContext(item.userContext)).context;
    await control.send("browsingContext.activate", { context: userTab });
    await control.send("browsingContext.navigate", { context: userTab, url: `${site}/sign-in`, wait: "complete" });
    await settle();
    return { remHidden, remShown, user: await fields(userTab) };
  } finally {
    control.detach({ expected: true });
    const exited = new Promise((resolve) => firefox.once("exit", resolve));
    firefox.kill("SIGTERM");
    await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 8000))]);
    firefox.kill("SIGKILL");
    server.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test("real Firefox: a sign-in page in Rem's container stays empty with a saved login", { skip: FIREFOX ? false : "Firefox is not installed", timeout: 180000 }, async (t) => {
  // Rem's other prefs without this one, as before: Firefox fills the saved
  // login into Rem's tab as soon as it shows, where Rem could read it.
  let before;
  try {
    before = await signInFields({ ...REM_CONTROL_PREFS, [AUTOFILL]: true });
  } catch (error) {
    // Running script in Firefox's own scope over WebDriver BiDi is unfinished in
    // Firefox (bug 1944570) and some releases refuse it. Only this test needs it,
    // to save its login; Raticode never does.
    if (!/privileged scope|System access is required/.test(String(error?.message))) throw error;
    t.skip(`this Firefox can't save the test login: ${error.message}`);
    return;
  }
  assert.deepEqual(before, { remHidden: "|", remShown: "ada|s3cret-pw", user: "ada|s3cret-pw" });

  const after = await signInFields(REM_CONTROL_PREFS);
  assert.deepEqual(after, { remHidden: "|", remShown: "|", user: "|" });
});
