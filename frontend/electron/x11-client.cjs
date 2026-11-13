// A small X11 protocol client, enough to place one program's window inside
// another. Raticode speaks the protocol directly so the desktop app needs no
// native module and no extra system tools.
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");

const EVENT = {
  ButtonPress: 4, FocusIn: 9, FocusOut: 10, CreateNotify: 16, DestroyNotify: 17, UnmapNotify: 18, MapNotify: 19,
  ReparentNotify: 21, ConfigureNotify: 22, PropertyNotify: 28,
};
const MASK = { SubstructureNotify: 1 << 19, StructureNotify: 1 << 17, PropertyChange: 1 << 22, FocusChange: 1 << 21, ButtonPress: 1 << 2 };
const REPLAY_POINTER = 2;
const REVERT_TO_PARENT = 2;
const ANY_BUTTON = 0;
const ANY_MODIFIER = 0x8000;
const GRAB_SYNC = 0;
const GRAB_ASYNC = 1;

function pad4(length) {
  return (4 - (length % 4)) % 4;
}

// ":1", ":1.0", "unix:1", "/tmp/.X11-unix/X1". Remote displays aren't used:
// the browser has to run beside Raticode anyway.
function parseDisplay(display) {
  const match = /^(?:unix)?:(\d+)(?:\.\d+)?$/.exec(String(display || ""));
  if (match) return { number: match[1], socket: `/tmp/.X11-unix/X${match[1]}` };
  const socket = /^\/.*\/X(\d+)(?:\.\d+)?$/.exec(String(display || ""));
  return socket ? { number: socket[1], socket: String(display).replace(/\.\d+$/, "") } : null;
}

// The cookie from an Xauthority file for this display, if it has one.
function readAuthority(number, { env = process.env, home = os.homedir(), hostname = os.hostname(), read = fs.readFileSync } = {}) {
  const file = env.XAUTHORITY || path.join(home, ".Xauthority");
  let data;
  try { data = read(file); } catch { return null; }
  let offset = 0;
  const field = () => {
    const length = data.readUInt16BE(offset);
    const value = data.subarray(offset + 2, offset + 2 + length);
    offset += 2 + length;
    return value;
  };
  let fallback = null;
  try {
    while (offset < data.length) {
      const family = data.readUInt16BE(offset);
      offset += 2;
      const address = field().toString();
      const display = field().toString();
      const name = field().toString();
      const secret = field();
      if (name !== "MIT-MAGIC-COOKIE-1" || (display && display !== number)) continue;
      if (family === 65535) fallback ||= { name, data: Buffer.from(secret) };
      if (family === 256 && address === hostname) return { name, data: Buffer.from(secret) };
      if (family === 256) fallback ||= { name, data: Buffer.from(secret) };
    }
  } catch {
    // A short or damaged file: use whatever was found before it.
  }
  return fallback;
}

// SHAPE extension request numbers and values.
const SHAPE_RECTANGLES = 1;
const SHAPE_MASK = 2;
const SHAPE_SET = 0;
const SHAPE_SUBTRACT = 3;
const SHAPE_BOUNDING = 0;

class X11Client extends EventEmitter {
  constructor(options = {}) {
    super();
    this.options = options;
    this.pending = new Map();
    this.sequence = 0;
    this.buffer = Buffer.alloc(0);
    this.atoms = new Map();
    this.closed = false;
    this.ready = false;
  }

  connect() {
    const display = parseDisplay(this.options.display ?? process.env.DISPLAY);
    if (!display) return Promise.reject(new Error(`Raticode can't use the display "${this.options.display ?? process.env.DISPLAY ?? ""}".`));
    return new Promise((resolve, reject) => {
      const socket = net.connect(display.socket);
      this.socket = socket;
      this.setup = { resolve, reject };
      socket.once("error", (error) => {
        this.setup?.reject(error);
        this.setup = null;
        this.emit("closed", error);
      });
      socket.on("close", () => this.closeFromServer());
      socket.on("data", (chunk) => this.receive(chunk));
      socket.once("connect", () => {
        const auth = readAuthority(display.number, this.options) || { name: "", data: Buffer.alloc(0) };
        const name = Buffer.from(auth.name);
        const header = Buffer.alloc(12);
        header.write("l", 0);
        header.writeUInt16LE(11, 2);
        header.writeUInt16LE(name.length, 6);
        header.writeUInt16LE(auth.data.length, 8);
        socket.write(Buffer.concat([header, name, Buffer.alloc(pad4(name.length)), auth.data, Buffer.alloc(pad4(auth.data.length))]));
      });
    });
  }

  closeFromServer() {
    if (this.closed) return;
    this.closed = true;
    const error = new Error("The display connection closed.");
    for (const { reject } of this.pending.values()) reject(error);
    this.pending.clear();
    this.setup?.reject(error);
    this.setup = null;
    this.emit("closed");
  }

  close() {
    this.closed = true;
    this.socket?.destroy();
    this.pending.clear();
  }

  receive(chunk) {
    this.buffer = this.buffer.length ? Buffer.concat([this.buffer, chunk]) : chunk;
    if (this.setup && !this.parseSetup()) return;
    while (this.buffer.length >= 32) {
      const type = this.buffer[0] & 0x7f;
      const length = type === 1 ? 32 + this.buffer.readUInt32LE(4) * 4 : 32;
      if (this.buffer.length < length) return;
      const packet = this.buffer.subarray(0, length);
      this.buffer = this.buffer.subarray(length);
      if (type === 0) this.handleError(packet);
      else if (type === 1) this.handleReply(packet);
      else this.emit("event", { type, sent: Boolean(packet[0] & 0x80), data: packet });
    }
  }

  parseSetup() {
    if (this.buffer.length < 8) return false;
    const total = 8 + this.buffer.readUInt16LE(6) * 4;
    if (this.buffer.length < total) return false;
    const reply = this.buffer.subarray(0, total);
    this.buffer = this.buffer.subarray(total);
    const { resolve, reject } = this.setup;
    this.setup = null;
    if (reply[0] !== 1) {
      const reason = reply.subarray(8, 8 + reply[1]).toString() || "the display refused the connection";
      reject(new Error(reason));
      return false;
    }
    const body = reply.subarray(8);
    this.ridBase = body.readUInt32LE(4);
    this.ridMask = body.readUInt32LE(8);
    this.maxRequest = body.readUInt16LE(18);
    const vendor = body.readUInt16LE(16);
    const screens = body[20];
    const formats = body[21];
    let offset = 32 + vendor + pad4(vendor) + formats * 8;
    if (!screens) { reject(new Error("The display has no screens.")); return false; }
    this.root = body.readUInt32LE(offset);
    this.screenWidth = body.readUInt16LE(offset + 20);
    this.screenHeight = body.readUInt16LE(offset + 22);
    offset += 32;
    this.ready = true;
    resolve(this);
    return true;
  }

  handleError(packet) {
    const sequence = packet.readUInt16LE(2);
    const pending = this.pending.get(sequence);
    const code = packet[1];
    if (pending) {
      this.pending.delete(sequence);
      pending.reject(Object.assign(new Error(`X11 error ${code}`), { code }));
      return;
    }
    // Requests with no reply fail here, usually because a window has closed.
    this.emit("x11-error", { code, sequence, major: packet[10] });
  }

  handleReply(packet) {
    const pending = this.pending.get(packet.readUInt16LE(2));
    if (!pending) return;
    this.pending.delete(packet.readUInt16LE(2));
    pending.resolve(packet);
  }

  send(opcode, detail, body = Buffer.alloc(0)) {
    if (this.closed || !this.ready) throw new Error("The display connection is closed.");
    const header = Buffer.alloc(4);
    header[0] = opcode;
    header[1] = detail;
    header.writeUInt16LE((4 + body.length + pad4(body.length)) / 4, 2);
    this.socket.write(Buffer.concat([header, body, Buffer.alloc(pad4(body.length))]));
    this.sequence = (this.sequence + 1) & 0xffff;
    return this.sequence;
  }

  request(opcode, detail, body) {
    return new Promise((resolve, reject) => {
      try {
        const sequence = this.send(opcode, detail, body);
        this.pending.set(sequence, { resolve, reject });
      } catch (error) {
        reject(error);
      }
    });
  }

  // Waits until the server has handled everything sent so far.
  sync() {
    return this.request(43, 0);
  }

  static words(...values) {
    const body = Buffer.alloc(values.length * 4);
    values.forEach((value, index) => body.writeUInt32LE(value >>> 0, index * 4));
    return body;
  }

  async atom(name) {
    if (this.atoms.has(name)) return this.atoms.get(name);
    const text = Buffer.from(name);
    const body = Buffer.alloc(4 + text.length + pad4(text.length));
    body.writeUInt16LE(text.length, 0);
    text.copy(body, 4);
    const reply = await this.request(16, 0, body);
    const atom = reply.readUInt32LE(8);
    this.atoms.set(name, atom);
    return atom;
  }

  selectInput(window, mask) {
    return this.send(2, 0, X11Client.words(window, 1 << 11, mask));
  }

  mapWindow(window) { return this.send(8, 0, X11Client.words(window)); }
  unmapWindow(window) { return this.send(10, 0, X11Client.words(window)); }

  reparentWindow(window, parent, x, y) {
    const body = Buffer.alloc(12);
    body.writeUInt32LE(window, 0);
    body.writeUInt32LE(parent, 4);
    body.writeInt16LE(x, 8);
    body.writeInt16LE(y, 10);
    return this.send(7, 0, body);
  }

  configureWindow(window, { x, y, width, height, raise = false }) {
    let mask = 0;
    const values = [];
    if (x !== undefined) { mask |= 1; values.push(x); }
    if (y !== undefined) { mask |= 2; values.push(y); }
    if (width !== undefined) { mask |= 4; values.push(width); }
    if (height !== undefined) { mask |= 8; values.push(height); }
    if (raise) { mask |= 64; values.push(0); }
    const body = Buffer.alloc(8 + values.length * 4);
    body.writeUInt32LE(window, 0);
    body.writeUInt16LE(mask, 4);
    values.forEach((value, index) => body.writeInt32LE(value, 8 + index * 4));
    return this.send(12, 0, body);
  }

  // Tells a window its position after a change it can't see itself (its parent
  // moved), as a window manager does. Without it, menus open in the wrong place.
  sendConfigureNotify(window, { x, y, width, height }) {
    const event = Buffer.alloc(32);
    event[0] = EVENT.ConfigureNotify;
    event.writeUInt32LE(window, 4);
    event.writeUInt32LE(window, 8);
    event.writeInt16LE(x, 16);
    event.writeInt16LE(y, 18);
    event.writeUInt16LE(width, 20);
    event.writeUInt16LE(height, 22);
    return this.send(25, 0, Buffer.concat([X11Client.words(window, MASK.StructureNotify), event]));
  }

  setInputFocus(window) {
    return this.send(42, REVERT_TO_PARENT, X11Client.words(window, 0));
  }

  async inputFocus() {
    return (await this.request(43, 0)).readUInt32LE(8);
  }

  // Lets Raticode hear a click on the window just before the window does, as a
  // window manager does for click-to-focus. allowClick must be called each time.
  grabClicks(window) {
    const body = Buffer.alloc(20);
    body.writeUInt32LE(window, 0);
    body.writeUInt16LE(MASK.ButtonPress, 4);
    body[6] = GRAB_SYNC;
    body[7] = GRAB_ASYNC;
    body[16] = ANY_BUTTON;
    body.writeUInt16LE(ANY_MODIFIER, 18);
    return this.send(28, 1, body);
  }

  allowClick() {
    return this.send(35, REPLAY_POINTER, X11Client.words(0));
  }

  async queryTree(window) {
    const reply = await this.request(15, 0, X11Client.words(window));
    const count = reply.readUInt16LE(16);
    return {
      root: reply.readUInt32LE(8),
      parent: reply.readUInt32LE(12),
      children: Array.from({ length: count }, (_, index) => reply.readUInt32LE(32 + index * 4)),
    };
  }

  async geometry(window) {
    const reply = await this.request(14, 0, X11Client.words(window));
    return { x: reply.readInt16LE(12), y: reply.readInt16LE(14), width: reply.readUInt16LE(16), height: reply.readUInt16LE(18) };
  }

  async attributes(window) {
    const reply = await this.request(3, 0, X11Client.words(window));
    return { mapState: reply[26], overrideRedirect: Boolean(reply[27]), class: reply.readUInt16LE(14) };
  }

  async translate(window, target, x = 0, y = 0) {
    const body = Buffer.alloc(12);
    body.writeUInt32LE(window, 0);
    body.writeUInt32LE(target, 4);
    body.writeInt16LE(x, 8);
    body.writeInt16LE(y, 10);
    const reply = await this.request(40, 0, body);
    return { x: reply.readInt16LE(12), y: reply.readInt16LE(14) };
  }

  async property(window, name, limit = 4096) {
    const atom = await this.atom(name);
    const reply = await this.request(20, 0, X11Client.words(window, atom, 0, 0, limit));
    const length = reply.readUInt32LE(16) * (reply[1] / 8);
    return { format: reply[1], type: reply.readUInt32LE(8), value: reply.subarray(32, 32 + length) };
  }

  async cardinal(window, name) {
    const { format, value } = await this.property(window, name, 1);
    return format === 32 && value.length >= 4 ? value.readUInt32LE(0) : 0;
  }

  async text(window, name) {
    return (await this.property(window, name)).value.toString("utf8");
  }

  async atoms32(window, name) {
    const { format, value } = await this.property(window, name, 64);
    if (format !== 32) return [];
    return Array.from({ length: value.length / 4 }, (_, index) => value.readUInt32LE(index * 4));
  }

  // BGRA pixels of part of a window, for Raticode's real-display checks.
  async image(window, x, y, width, height) {
    const body = Buffer.alloc(16);
    body.writeUInt32LE(window, 0);
    body.writeInt16LE(x, 4);
    body.writeInt16LE(y, 6);
    body.writeUInt16LE(width, 8);
    body.writeUInt16LE(height, 10);
    body.writeUInt32LE(0xffffffff, 12);
    const reply = await this.request(73, 2, body);
    return { width, height, data: Buffer.from(reply.subarray(32)) };
  }

  async extension(name) {
    const text = Buffer.from(name);
    const body = Buffer.alloc(4 + text.length + pad4(text.length));
    body.writeUInt16LE(text.length, 0);
    text.copy(body, 4);
    const reply = await this.request(98, 0, body);
    return reply[8] ? reply[9] : 0;
  }

  // SHAPE: cuts rectangles out of a window, so what Raticode draws over the
  // Gecko tab (a menu) shows through a hole instead of the whole page hiding.
  // rects are { x, y, width, height } relative to the window.
  shapeWindow(major, window, width, height, rects) {
    const encode = (list) => {
      const body = Buffer.alloc(list.length * 8);
      list.forEach((rect, index) => {
        body.writeInt16LE(rect.x, index * 8);
        body.writeInt16LE(rect.y, index * 8 + 2);
        body.writeUInt16LE(rect.width, index * 8 + 4);
        body.writeUInt16LE(rect.height, index * 8 + 6);
      });
      return body;
    };
    const request = (operation, list) => {
      const head = Buffer.alloc(12);
      head[0] = operation;
      head[1] = SHAPE_BOUNDING;
      head[2] = 0;
      head.writeUInt32LE(window, 4);
      return this.send(major, SHAPE_RECTANGLES, Buffer.concat([head, encode(list)]));
    };
    request(SHAPE_SET, [{ x: 0, y: 0, width, height }]);
    if (rects.length) request(SHAPE_SUBTRACT, rects);
  }

  // Back to the window's own rectangle.
  unshapeWindow(major, window) {
    const body = Buffer.alloc(16);
    body[0] = SHAPE_SET;
    body[1] = SHAPE_BOUNDING;
    body.writeUInt32LE(window, 4);
    body.writeUInt32LE(0, 12);
    return this.send(major, SHAPE_MASK, body);
  }

  // XTEST: real pointer and key input, for Raticode's real-display checks.
  fakeInput(major, type, detail, x = 0, y = 0) {
    const body = Buffer.alloc(32);
    body[0] = type;
    body[1] = detail;
    body.writeUInt32LE(0, 4);
    body.writeUInt32LE(this.root, 8);
    body.writeInt16LE(x, 20);
    body.writeInt16LE(y, 22);
    return this.send(major, 2, body);
  }
}

module.exports = { EVENT, MASK, X11Client, parseDisplay, readAuthority };
