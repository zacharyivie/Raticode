// A small client for the D-Bus session bus, used by the main process on Linux to ask the
// desktop for a system-wide shortcut (see linux-shortcuts.cjs). Electron has no D-Bus API,
// and the desktop's portal ties a shortcut to the connection that asked for it, so the
// connection has to live inside Raticode for as long as the shortcut does.
//
// It speaks just enough of the protocol for that: EXTERNAL authentication over a Unix
// socket, method calls and their replies, and signals picked by match rules. No file
// descriptors, no introspection, no serving of objects (tests use serve() for that).
//
// Values: s/o/g are strings, y/n/q/i/u/h and d are numbers, x/t are BigInts, b is a
// boolean, arrays are arrays, a{..} is a plain object, structs are arrays, and a variant
// is written as new Variant(signature, value) and read back as its plain value.

const net = require("node:net");
const fs = require("node:fs");
const path = require("node:path");

const MAX_MESSAGE = 4 * 1024 * 1024;
const TYPES = { call: 1, return: 2, error: 3, signal: 4 };
const FIELD = { path: 1, interface: 2, member: 3, errorName: 4, replySerial: 5, destination: 6, sender: 7, signature: 8 };
const FIELD_SIG = { 1: "o", 2: "s", 3: "s", 4: "s", 5: "u", 6: "s", 7: "s", 8: "g" };
const NO_REPLY = 0x1;

class Variant {
  constructor(signature, value) {
    this.signature = signature;
    this.value = value;
  }
}

class DBusError extends Error {
  constructor(name, message) {
    super(message || name);
    this.name = "DBusError";
    this.dbusName = name;
  }
}

// --- Signatures -------------------------------------------------------------------------

/** Splits a signature into its complete types: "sa{sv}(ii)" -> ["s", "a{sv}", "(ii)"]. */
function splitSignature(signature) {
  const types = [];
  let index = 0;
  while (index < signature.length) {
    const end = typeEnd(signature, index);
    types.push(signature.slice(index, end));
    index = end;
  }
  return types;
}

function typeEnd(signature, start) {
  const code = signature[start];
  if (code === "a") return typeEnd(signature, start + 1);
  if (code === "(" || code === "{") {
    const close = code === "(" ? ")" : "}";
    let index = start + 1;
    while (signature[index] !== close) {
      if (index >= signature.length) throw new Error(`Bad D-Bus signature "${signature}".`);
      index = typeEnd(signature, index);
    }
    return index + 1;
  }
  if (!"ybnqiuxtdsogvh".includes(code ?? "")) throw new Error(`Bad D-Bus signature "${signature}".`);
  return start + 1;
}

function alignmentOf(type) {
  switch (type[0]) {
    case "y": case "g": case "v": return 1;
    case "n": case "q": return 2;
    case "x": case "t": case "d": case "(": case "{": return 8;
    default: return 4;
  }
}

// --- Writing ----------------------------------------------------------------------------

class Writer {
  constructor() {
    this.chunks = [];
    this.length = 0;
  }

  push(buffer) {
    this.chunks.push(buffer);
    this.length += buffer.length;
  }

  align(n) {
    const pad = (n - (this.length % n)) % n;
    if (pad) this.push(Buffer.alloc(pad));
  }

  number(bytes, write) {
    this.align(bytes);
    const buffer = Buffer.alloc(bytes);
    write(buffer);
    this.push(buffer);
  }

  string(text, lengthBytes = 4) {
    const bytes = Buffer.from(String(text), "utf8");
    if (bytes.includes(0)) throw new Error("D-Bus strings can't contain a NUL character.");
    if (lengthBytes === 1) {
      if (bytes.length > 255) throw new Error("D-Bus signature too long.");
      this.push(Buffer.from([bytes.length]));
    } else {
      this.number(4, (buffer) => buffer.writeUInt32LE(bytes.length));
    }
    this.push(bytes);
    this.push(Buffer.alloc(1));
  }

  value(type, value) {
    switch (type[0]) {
      case "y": this.push(Buffer.from([Number(value) & 0xff])); return;
      case "b": this.number(4, (buffer) => buffer.writeUInt32LE(value ? 1 : 0)); return;
      case "n": this.number(2, (buffer) => buffer.writeInt16LE(Number(value))); return;
      case "q": this.number(2, (buffer) => buffer.writeUInt16LE(Number(value))); return;
      case "i": this.number(4, (buffer) => buffer.writeInt32LE(Number(value))); return;
      case "u": case "h": this.number(4, (buffer) => buffer.writeUInt32LE(Number(value))); return;
      case "x": this.number(8, (buffer) => buffer.writeBigInt64LE(BigInt(value))); return;
      case "t": this.number(8, (buffer) => buffer.writeBigUInt64LE(BigInt(value))); return;
      case "d": this.number(8, (buffer) => buffer.writeDoubleLE(Number(value))); return;
      case "s": case "o": this.string(value); return;
      case "g": this.string(value, 1); return;
      case "v": {
        if (!(value instanceof Variant)) throw new Error("A D-Bus variant needs new Variant(signature, value).");
        this.string(value.signature, 1);
        this.value(value.signature, value.value);
        return;
      }
      case "(": {
        this.align(8);
        const fields = splitSignature(type.slice(1, -1));
        fields.forEach((field, index) => this.value(field, value[index]));
        return;
      }
      case "a": {
        const element = type.slice(1);
        this.align(4);
        const lengthAt = this.chunks.length;
        this.push(Buffer.alloc(4));
        this.align(alignmentOf(element));
        const start = this.length;
        if (element[0] === "{") {
          const [keyType, valueType] = splitSignature(element.slice(1, -1));
          const entries = value instanceof Map ? [...value.entries()] : Object.entries(value ?? {});
          for (const [key, entryValue] of entries) {
            this.align(8);
            this.value(keyType, key);
            this.value(valueType, entryValue);
          }
        } else {
          for (const item of value ?? []) this.value(element, item);
        }
        this.chunks[lengthAt].writeUInt32LE(this.length - start);
        return;
      }
      default:
        throw new Error(`Can't write D-Bus type "${type}".`);
    }
  }

  toBuffer() {
    return Buffer.concat(this.chunks, this.length);
  }
}

// --- Reading ----------------------------------------------------------------------------

class Reader {
  constructor(buffer, little = true, offset = 0) {
    this.buffer = buffer;
    this.little = little;
    this.offset = offset;
  }

  align(n) {
    this.offset += (n - (this.offset % n)) % n;
  }

  need(bytes) {
    if (this.offset + bytes > this.buffer.length) throw new Error("D-Bus message ended early.");
  }

  int(bytes, signed) {
    this.align(bytes);
    this.need(bytes);
    const { buffer, offset, little } = this;
    this.offset += bytes;
    if (bytes === 1) return buffer[offset];
    if (bytes === 2) return signed ? (little ? buffer.readInt16LE(offset) : buffer.readInt16BE(offset)) : (little ? buffer.readUInt16LE(offset) : buffer.readUInt16BE(offset));
    if (bytes === 4) return signed ? (little ? buffer.readInt32LE(offset) : buffer.readInt32BE(offset)) : (little ? buffer.readUInt32LE(offset) : buffer.readUInt32BE(offset));
    return signed ? (little ? buffer.readBigInt64LE(offset) : buffer.readBigInt64BE(offset)) : (little ? buffer.readBigUInt64LE(offset) : buffer.readBigUInt64BE(offset));
  }

  string(lengthBytes = 4) {
    const length = lengthBytes === 1 ? this.int(1) : this.int(4);
    this.need(length + 1);
    const text = this.buffer.toString("utf8", this.offset, this.offset + length);
    this.offset += length + 1;
    return text;
  }

  value(type) {
    switch (type[0]) {
      case "y": return this.int(1);
      case "b": return this.int(4) !== 0;
      case "n": return this.int(2, true);
      case "q": return this.int(2);
      case "i": return this.int(4, true);
      case "u": case "h": return this.int(4);
      case "x": return this.int(8, true);
      case "t": return this.int(8);
      case "d": {
        this.align(8);
        this.need(8);
        const value = this.little ? this.buffer.readDoubleLE(this.offset) : this.buffer.readDoubleBE(this.offset);
        this.offset += 8;
        return value;
      }
      case "s": case "o": return this.string();
      case "g": return this.string(1);
      case "v": {
        const signature = this.string(1);
        return this.value(signature);
      }
      case "(": {
        this.align(8);
        return splitSignature(type.slice(1, -1)).map((field) => this.value(field));
      }
      case "a": {
        const element = type.slice(1);
        const length = this.int(4);
        if (length > MAX_MESSAGE) throw new Error("D-Bus array too long.");
        this.align(alignmentOf(element));
        const end = this.offset + length;
        this.need(length);
        if (element[0] === "{") {
          const [keyType, valueType] = splitSignature(element.slice(1, -1));
          const result = {};
          while (this.offset < end) {
            this.align(8);
            const key = this.value(keyType);
            result[String(key)] = this.value(valueType);
          }
          return result;
        }
        const items = [];
        while (this.offset < end) items.push(this.value(element));
        return items;
      }
      default:
        throw new Error(`Can't read D-Bus type "${type}".`);
    }
  }
}

// --- Messages ---------------------------------------------------------------------------

/** Builds one message. fields: { path, interface, member, destination, errorName, replySerial, sender }. */
function encodeMessage({ type, flags = 0, serial, signature = "", body = [], ...fields }) {
  const bodyWriter = new Writer();
  splitSignature(signature).forEach((field, index) => bodyWriter.value(field, body[index]));
  const bodyBuffer = bodyWriter.toBuffer();
  const headerFields = [];
  for (const [name, code] of Object.entries(FIELD)) {
    if (name === "signature") continue;
    if (fields[name] !== undefined && fields[name] !== "") headerFields.push([code, new Variant(FIELD_SIG[code], fields[name])]);
  }
  if (signature) headerFields.push([FIELD.signature, new Variant("g", signature)]);
  const header = new Writer();
  header.push(Buffer.from([0x6c, TYPES[type] ?? type, flags, 1]));
  header.value("u", bodyBuffer.length);
  header.value("u", serial);
  header.value("a(yv)", headerFields);
  header.align(8);
  return Buffer.concat([header.toBuffer(), bodyBuffer]);
}

/** Bytes needed for the message starting at buffer[0], or 0 if the header isn't in yet. */
function messageLength(buffer) {
  if (buffer.length < 16) return 0;
  const little = buffer[0] === 0x6c;
  if (!little && buffer[0] !== 0x42) throw new Error("Not a D-Bus message.");
  const bodyLength = little ? buffer.readUInt32LE(4) : buffer.readUInt32BE(4);
  const fieldsLength = little ? buffer.readUInt32LE(12) : buffer.readUInt32BE(12);
  const headerLength = 16 + fieldsLength + ((8 - ((16 + fieldsLength) % 8)) % 8);
  const total = headerLength + bodyLength;
  if (total > MAX_MESSAGE) throw new Error("D-Bus message too large.");
  return total;
}

function decodeMessage(buffer) {
  const little = buffer[0] === 0x6c;
  const reader = new Reader(buffer, little, 1);
  const type = reader.int(1);
  const flags = reader.int(1);
  reader.int(1);
  reader.int(4);
  const serial = reader.int(4);
  const message = { type, flags, serial, signature: "", body: [] };
  const names = Object.fromEntries(Object.entries(FIELD).map(([name, code]) => [code, name]));
  for (const [code, value] of reader.value("a(yv)")) {
    if (names[code]) message[names[code]] = value;
  }
  reader.align(8);
  if (message.signature) message.body = splitSignature(message.signature).map((field) => reader.value(field));
  return message;
}

// --- Connections ------------------------------------------------------------------------

/** Where the session bus listens, from DBUS_SESSION_BUS_ADDRESS or $XDG_RUNTIME_DIR/bus. */
function sessionBusPath(env = process.env, exists = fs.existsSync) {
  for (const address of String(env.DBUS_SESSION_BUS_ADDRESS || "").split(";")) {
    const [transport, rest = ""] = address.split(/:(.*)/s);
    if (transport !== "unix") continue;
    const params = Object.fromEntries(rest.split(",").map((pair) => pair.split(/=(.*)/s)).filter(([key]) => key));
    if (params.path) return decodeURIComponent(params.path);
    if (params.abstract) return `\0${decodeURIComponent(params.abstract)}`;
  }
  if (!env.DBUS_SESSION_BUS_ADDRESS && env.XDG_RUNTIME_DIR) {
    const fallback = path.join(env.XDG_RUNTIME_DIR, "bus");
    if (exists(fallback)) return fallback;
  }
  return "";
}

/**
 * Connects to the bus at socketPath (default: the session bus) and says Hello.
 * Resolves to a connection: { name, call, signal, addMatch, removeMatch, onSignal,
 * onClose, close, closed }.
 */
function connect({ socketPath = sessionBusPath(), uid = process.getuid?.() ?? 0, timeoutMs = 5000 } = {}) {
  if (!socketPath) return Promise.reject(new Error("No D-Bus session bus in this session."));
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(socketPath);
    let authenticated = false;
    let pending = Buffer.alloc(0);
    let serial = 0;
    const replies = new Map();
    const signalHandlers = new Set();
    const serveHandlers = new Set();
    const closeHandlers = new Set();
    let closed = false;
    const timer = setTimeout(() => fail(new Error("The D-Bus session bus didn't answer.")), timeoutMs);

    function fail(error) {
      clearTimeout(timer);
      socket.destroy();
      reject(error);
    }

    function finish(error) {
      if (closed) return;
      closed = true;
      for (const { reject: rejectReply, timer: replyTimer } of replies.values()) {
        clearTimeout(replyTimer);
        rejectReply(error ?? new Error("The D-Bus connection closed."));
      }
      replies.clear();
      for (const handler of closeHandlers) {
        try { handler(error); } catch { /* A close handler's own problem. */ }
      }
    }

    function send(message) {
      if (closed) throw new Error("The D-Bus connection is closed.");
      serial += 1;
      socket.write(encodeMessage({ ...message, serial }));
      return serial;
    }

    function call({ destination, path: objectPath, interface: iface, member, signature = "", body = [], timeoutMs: callTimeout = 30000, noReply = false }) {
      return new Promise((resolveCall, rejectCall) => {
        let id;
        try {
          id = send({ type: "call", flags: noReply ? NO_REPLY : 0, destination, path: objectPath, interface: iface, member, signature, body });
        } catch (error) {
          rejectCall(error);
          return;
        }
        if (noReply) {
          resolveCall([]);
          return;
        }
        const replyTimer = setTimeout(() => {
          replies.delete(id);
          rejectCall(new DBusError("org.freedesktop.DBus.Error.NoReply", `${member} didn't answer in time.`));
        }, callTimeout);
        replies.set(id, { resolve: resolveCall, reject: rejectCall, timer: replyTimer });
      });
    }

    function handle(message) {
      if (message.type === TYPES.return || message.type === TYPES.error) {
        const waiting = replies.get(message.replySerial);
        if (!waiting) return;
        replies.delete(message.replySerial);
        clearTimeout(waiting.timer);
        if (message.type === TYPES.return) waiting.resolve(message.body);
        else waiting.reject(new DBusError(message.errorName, typeof message.body[0] === "string" ? message.body[0] : ""));
        return;
      }
      if (message.type === TYPES.call) {
        for (const handler of serveHandlers) {
          try { handler(message); } catch { /* A test double's own problem. */ }
        }
        return;
      }
      if (message.type === TYPES.signal) {
        for (const handler of signalHandlers) {
          try { handler(message); } catch { /* One handler's problem doesn't stop the others. */ }
        }
      }
    }

    function readMessages() {
      for (;;) {
        const length = messageLength(pending);
        if (!length || pending.length < length) return;
        const raw = pending.subarray(0, length);
        pending = pending.subarray(length);
        handle(decodeMessage(raw));
      }
    }

    socket.on("connect", () => {
      const id = Buffer.from(String(uid), "ascii").toString("hex");
      socket.write(Buffer.concat([Buffer.from([0]), Buffer.from(`AUTH EXTERNAL ${id}\r\n`, "ascii")]));
    });

    socket.on("data", (chunk) => {
      pending = Buffer.concat([pending, chunk]);
      if (!authenticated) {
        const end = pending.indexOf("\r\n");
        if (end < 0) return;
        const line = pending.subarray(0, end).toString("ascii");
        pending = pending.subarray(end + 2);
        if (!line.startsWith("OK ")) {
          fail(new Error(`The D-Bus session bus refused Raticode (${line.slice(0, 60)}).`));
          return;
        }
        authenticated = true;
        socket.write("BEGIN\r\n");
        call({ destination: "org.freedesktop.DBus", path: "/org/freedesktop/DBus", interface: "org.freedesktop.DBus", member: "Hello" })
          .then(([name]) => {
            clearTimeout(timer);
            resolve(connection(name));
          }, fail);
      }
      try {
        readMessages();
      } catch (error) {
        socket.destroy(error);
      }
    });

    socket.on("error", (error) => {
      if (!authenticated) fail(error);
      finish(error);
    });
    socket.on("close", () => finish(null));

    function busCall(member, signature, body) {
      return call({ destination: "org.freedesktop.DBus", path: "/org/freedesktop/DBus", interface: "org.freedesktop.DBus", member, signature, body });
    }

    function connection(name) {
      return {
        name,
        call,
        /** Sends a signal (tests use this to play a desktop). */
        signal: ({ path: objectPath, interface: iface, member, signature = "", body = [], destination }) =>
          send({ type: "signal", path: objectPath, interface: iface, member, signature, body, destination }),
        /** Replies to a method call received through serve(). */
        reply: (message, { signature = "", body = [], error = "", errorMessage = "" } = {}) => {
          if (error) send({ type: "error", replySerial: message.serial, destination: message.sender, errorName: error, signature: "s", body: [errorMessage || error] });
          else send({ type: "return", replySerial: message.serial, destination: message.sender, signature, body });
        },
        addMatch: (rule) => busCall("AddMatch", "s", [rule]),
        removeMatch: (rule) => busCall("RemoveMatch", "s", [rule]).catch(() => {}),
        requestName: (wanted) => busCall("RequestName", "su", [wanted, 4]),
        nameHasOwner: (wanted) => busCall("NameHasOwner", "s", [wanted]).then(([owned]) => owned === true),
        onSignal(handler) {
          signalHandlers.add(handler);
          return () => signalHandlers.delete(handler);
        },
        /** Method calls addressed to this connection (tests use this to play a desktop). */
        serve(handler) {
          serveHandlers.add(handler);
          return () => serveHandlers.delete(handler);
        },
        onClose(handler) {
          closeHandlers.add(handler);
          return () => closeHandlers.delete(handler);
        },
        get closed() { return closed; },
        close() {
          socket.end();
          socket.destroy();
          finish(null);
        },
      };
    }

  });
}

/** A match rule string for a signal. */
function matchRule({ sender, path: objectPath, interface: iface, member }) {
  const parts = ["type='signal'"];
  if (sender) parts.push(`sender='${sender}'`);
  if (iface) parts.push(`interface='${iface}'`);
  if (member) parts.push(`member='${member}'`);
  if (objectPath) parts.push(`path='${objectPath}'`);
  return parts.join(",");
}

module.exports = {
  DBusError,
  MAX_MESSAGE,
  TYPES,
  Variant,
  connect,
  decodeMessage,
  encodeMessage,
  matchRule,
  messageLength,
  sessionBusPath,
  splitSignature,
};
