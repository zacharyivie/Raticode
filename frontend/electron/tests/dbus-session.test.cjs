// The main process's small D-Bus client (dbus-session.cjs): the wire format on its own,
// and a real conversation through a private dbus-daemon when one is installed.
const test = require("node:test");
const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  Variant,
  connect,
  decodeMessage,
  encodeMessage,
  matchRule,
  messageLength,
  sessionBusPath,
  splitSignature,
} = require("../dbus-session.cjs");

test("signatures split into complete types", () => {
  assert.deepEqual(splitSignature("sa{sv}(ii)ao"), ["s", "a{sv}", "(ii)", "ao"]);
  assert.deepEqual(splitSignature("oa(sa{sv})sa{sv}"), ["o", "a(sa{sv})", "s", "a{sv}"]);
  assert.deepEqual(splitSignature(""), []);
  assert.throws(() => splitSignature("a(s"), /Bad D-Bus signature/);
  assert.throws(() => splitSignature("z"), /Bad D-Bus signature/);
});

test("a method call survives encoding and decoding with the portal's types", () => {
  const raw = encodeMessage({
    type: "call",
    serial: 7,
    destination: "org.freedesktop.portal.Desktop",
    path: "/org/freedesktop/portal/desktop",
    interface: "org.freedesktop.portal.GlobalShortcuts",
    member: "BindShortcuts",
    signature: "oa(sa{sv})sa{sv}",
    body: [
      "/org/freedesktop/portal/desktop/session/1_42/raticode",
      [["dictate-to-rem", { description: new Variant("s", "Dictate to Rem"), preferred_trigger: new Variant("s", "CTRL+ALT+space") }]],
      "",
      { handle_token: new Variant("s", "raticode1") },
    ],
  });
  assert.equal(messageLength(raw), raw.length);
  assert.equal(messageLength(raw.subarray(0, 10)), 0);
  const message = decodeMessage(raw);
  assert.equal(message.type, 1);
  assert.equal(message.serial, 7);
  assert.equal(message.member, "BindShortcuts");
  assert.equal(message.interface, "org.freedesktop.portal.GlobalShortcuts");
  assert.deepEqual(message.body, [
    "/org/freedesktop/portal/desktop/session/1_42/raticode",
    [["dictate-to-rem", { description: "Dictate to Rem", preferred_trigger: "CTRL+ALT+space" }]],
    "",
    { handle_token: "raticode1" },
  ]);
});

test("every basic type keeps its value, including 64-bit timestamps and alignment after bytes", () => {
  const raw = encodeMessage({
    type: "signal",
    serial: 1,
    path: "/a",
    interface: "x.y",
    member: "Z",
    signature: "ybnqiuxtdsogbv(yt)ai",
    body: [255, true, -2, 65535, -70000, 4000000000, -5n, 18446744073709551615n, 1.5, "héllo", "/o/p", "a{sv}", false, new Variant("ai", [1, 2]), [1, 99n], []],
  });
  const message = decodeMessage(raw);
  assert.deepEqual(message.body, [255, true, -2, 65535, -70000, 4000000000, -5n, 18446744073709551615n, 1.5, "héllo", "/o/p", "a{sv}", false, [1, 2], [1, 99n], []]);
});

test("strings with a NUL character are refused instead of being cut short", () => {
  assert.throws(() => encodeMessage({ type: "call", serial: 1, path: "/", member: "M", signature: "s", body: ["a\0b"] }), /NUL/);
});

test("big-endian messages from other machines are read too", () => {
  // A hand-built big-endian signal: org.example.Ping("hi") on /p.
  const fields = Buffer.from([
    1, 1, 0x6f, 0, 0, 0, 0, 2, 0x2f, 0x70, 0, 0, 0, 0, 0, 0, // PATH o "/p"
    3, 1, 0x73, 0, 0, 0, 0, 4, 0x50, 0x69, 0x6e, 0x67, 0, 0, 0, 0, // MEMBER s "Ping"
    8, 1, 0x67, 0, 1, 0x73, 0, // SIGNATURE g "s"
  ]);
  const header = Buffer.alloc(16);
  header.write("B", 0, "ascii");
  header[1] = 4;
  header[3] = 1;
  const body = Buffer.from([0, 0, 0, 2, 0x68, 0x69, 0]);
  header.writeUInt32BE(body.length, 4);
  header.writeUInt32BE(3, 8);
  header.writeUInt32BE(fields.length, 12);
  const padded = Buffer.concat([header, fields, Buffer.alloc((8 - ((16 + fields.length) % 8)) % 8)]);
  const raw = Buffer.concat([padded, body]);
  assert.equal(messageLength(raw), raw.length);
  const message = decodeMessage(raw);
  assert.equal(message.path, "/p");
  assert.equal(message.member, "Ping");
  assert.deepEqual(message.body, ["hi"]);
});

test("messages larger than the cap are refused before they are buffered", () => {
  const header = Buffer.alloc(16);
  header.write("l", 0, "ascii");
  header.writeUInt32LE(64 * 1024 * 1024, 4);
  assert.throws(() => messageLength(header), /too large/);
});

test("the session bus address comes from the environment, with the runtime-dir socket as a fallback", () => {
  assert.equal(sessionBusPath({ DBUS_SESSION_BUS_ADDRESS: "unix:path=/run/user/1000/bus,guid=abc" }), "/run/user/1000/bus");
  assert.equal(sessionBusPath({ DBUS_SESSION_BUS_ADDRESS: "tcp:host=x;unix:abstract=/tmp/dbus-1" }), "\0/tmp/dbus-1");
  assert.equal(sessionBusPath({ XDG_RUNTIME_DIR: "/run/user/5" }, (file) => file === "/run/user/5/bus"), "/run/user/5/bus");
  assert.equal(sessionBusPath({}, () => true), "");
  assert.equal(sessionBusPath({ DBUS_SESSION_BUS_ADDRESS: "unix:path=/nonexistent/raticode-test-session-bus" }), "/nonexistent/raticode-test-session-bus");
});

test("match rules name only what was asked for", () => {
  assert.equal(
    matchRule({ sender: "org.freedesktop.portal.Desktop", interface: "org.freedesktop.portal.Request", member: "Response", path: "/r/1" }),
    "type='signal',sender='org.freedesktop.portal.Desktop',interface='org.freedesktop.portal.Request',member='Response',path='/r/1'",
  );
});

test("connecting with no session bus fails plainly", async () => {
  await assert.rejects(connect({ socketPath: "" }), /No D-Bus session bus/);
  await assert.rejects(connect({ socketPath: path.join(os.tmpdir(), "no-bus-here") }), /ENOENT|ECONNREFUSED/);
});

/** Starts a private dbus-daemon, or returns null when it isn't installed. */
async function privateBus(t) {
  if (process.platform !== "linux" || spawnSync("dbus-daemon", ["--version"]).status !== 0) {
    t.skip("dbus-daemon is not installed");
    return null;
  }
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-dbus-"));
  const socketPath = path.join(dir, "bus");
  const daemon = spawn("dbus-daemon", [
    `--config-file=${path.join(__dirname, "test-session-bus.conf")}`,
    `--address=unix:path=${socketPath}`,
    "--nofork",
    "--print-address",
  ], { stdio: ["ignore", "pipe", "inherit"] });
  await new Promise((resolve, reject) => {
    daemon.stdout.once("data", resolve);
    daemon.once("exit", (code) => reject(new Error(`dbus-daemon exited with ${code}`)));
  });
  t.after(() => {
    daemon.kill();
    fs.rmSync(dir, { recursive: true, force: true });
  });
  return socketPath;
}

test("a real dbus-daemon carries calls, replies, errors and matched signals between two connections", async (t) => {
  const socketPath = await privateBus(t);
  if (!socketPath) return;
  const service = await connect({ socketPath });
  const client = await connect({ socketPath });
  t.after(() => { service.close(); client.close(); });
  assert.match(client.name, /^:1\.\d+$/);
  assert.deepEqual(await service.requestName("org.example.Echo"), [1]);
  assert.equal(await client.nameHasOwner("org.example.Echo"), true);
  assert.equal(await client.nameHasOwner("org.example.Nobody"), false);

  service.serve((message) => {
    if (message.member === "Echo") service.reply(message, { signature: "a{sv}t", body: [{ got: new Variant("s", message.body[0]) }, 12345678901234n] });
    else service.reply(message, { error: "org.example.Error.Nope", errorMessage: "Not here." });
  });
  const reply = await client.call({ destination: "org.example.Echo", path: "/echo", interface: "org.example.Echo", member: "Echo", signature: "s", body: ["hello"] });
  assert.deepEqual(reply, [{ got: "hello" }, 12345678901234n]);
  await assert.rejects(
    client.call({ destination: "org.example.Echo", path: "/echo", interface: "org.example.Echo", member: "Other" }),
    (error) => error.dbusName === "org.example.Error.Nope" && error.message === "Not here.",
  );

  // Only signals that match the rule arrive.
  const seen = [];
  client.onSignal((message) => seen.push([message.path, message.member, message.body]));
  await client.addMatch(matchRule({ interface: "org.example.Echo", member: "Rang", path: "/bell" }));
  service.signal({ path: "/elsewhere", interface: "org.example.Echo", member: "Rang", signature: "u", body: [1] });
  service.signal({ path: "/bell", interface: "org.example.Echo", member: "Rang", signature: "u", body: [2] });
  // A round trip after the signals means they have been delivered.
  await client.call({ destination: "org.example.Echo", path: "/echo", interface: "org.example.Echo", member: "Echo", signature: "s", body: ["sync"] });
  assert.deepEqual(seen, [["/bell", "Rang", [2]]]);

  // The other side going away is noticed.
  const closed = new Promise((resolve) => client.onClose(resolve));
  service.close();
  client.close();
  await closed;
  assert.equal(client.closed, true);
  await assert.rejects(client.call({ destination: "org.example.Echo", path: "/", member: "Echo" }), /closed/);
});
