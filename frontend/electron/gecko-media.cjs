// What Gecko is playing, for the media bar. Gecko is real Firefox with no
// control port, so Raticode cannot read its pages. Firefox publishes the media
// it is playing to the desktop over MPRIS, the D-Bus interface media keys use,
// and Raticode reads that with busctl. A name on the bus is only believed when
// the bus says Gecko's own process holds it, so the user's everyday Firefox is
// never read or controlled.
//
// busctl's JSON is used because titles come from web pages; gdbus prints them in
// a text format that would need parsing.
const { execFile } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { fileURLToPath } = require("node:url");
const { COMMANDS, describeMedia, nextMediaState } = require("./browser-media.cjs");

const GECKO_MEDIA_ID = "gecko";
const BUS = ["org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus"];
const PLAYER_PATH = "/org/mpris/MediaPlayer2";
const PLAYER = "org.mpris.MediaPlayer2.Player";
const PROPERTIES = "org.freedesktop.DBus.Properties";
const FIREFOX_NAME = /^org\.mpris\.MediaPlayer2\.firefox\.[A-Za-z0-9_.]{1,100}$/;
const METHODS = { "play-pause": "PlayPause", previous: "Previous", next: "Next" };
// Looking for a player while nothing plays, and following one that does.
const SEARCH_MS = 2000;
const POLL_MS = 1000;
const CALL_TIMEOUT_MS = 2000;
// Base64 of this fits the bar's limit on embedded pictures.
const MAX_ARTWORK_BYTES = 192 * 1024;

function busctl(args, env) {
  return new Promise((resolve, reject) => {
    execFile("busctl", ["--user", "--json=short", "call", ...args], { env, timeout: CALL_TIMEOUT_MS, maxBuffer: 512 * 1024 }, (error, stdout) => {
      if (error) { reject(error); return; }
      try {
        resolve(stdout.trim() ? JSON.parse(stdout) : null);
      } catch (cause) {
        reject(cause);
      }
    });
  });
}

function imageType(bytes) {
  if (bytes.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return "image/png";
  if (bytes.subarray(0, 3).equals(Buffer.from([0xff, 0xd8, 0xff]))) return "image/jpeg";
  if (bytes.subarray(0, 4).toString("latin1") === "GIF8") return "image/gif";
  if (bytes.subarray(0, 4).toString("latin1") === "RIFF" && bytes.subarray(8, 12).toString("latin1") === "WEBP") return "image/webp";
  return "";
}

// Firefox saves a page's artwork in a folder of its own and names the file.
// The studio can't load local files, so a small picture is passed as data.
async function readArtworkFile(url) {
  let file;
  try {
    file = fileURLToPath(url);
  } catch {
    return "";
  }
  if (path.basename(path.dirname(file)) !== "firefox-mpris") return "";
  try {
    const details = await fs.promises.lstat(file);
    if (!details.isFile() || details.size > MAX_ARTWORK_BYTES) return "";
    const bytes = await fs.promises.readFile(file);
    const type = imageType(bytes);
    return type ? `data:${type};base64,${bytes.toString("base64")}` : "";
  } catch {
    return "";
  }
}

// The reading the bar's shared rules expect, from Firefox's player properties.
// Null unless something is playing or paused.
function readingFrom(reply, artwork) {
  const properties = reply?.data?.[0];
  const status = properties?.PlaybackStatus?.data;
  if (status !== "Playing" && status !== "Paused") return null;
  const metadata = properties.Metadata?.data || {};
  const artists = metadata["xesam:artist"]?.data;
  return {
    hasElement: true,
    playing: status === "Playing",
    playbackState: status.toLowerCase(),
    title: String(metadata["xesam:title"]?.data || ""),
    artist: Array.isArray(artists) ? artists.filter(Boolean).join(", ") : "",
    artwork,
    poster: "",
    url: String(metadata["xesam:url"]?.data || ""),
    actions: {
      play: properties.CanPlay?.data === true,
      pause: properties.CanPause?.data === true,
      previous: properties.CanGoPrevious?.data === true,
      next: properties.CanGoNext?.data === true,
    },
  };
}

function watchGeckoMedia({
  pid,
  title = () => "",
  onChange = () => {},
  env = process.env,
  log = () => {},
  run = (args) => busctl(args, env),
  readArtwork = readArtworkFile,
  now = () => Date.now(),
  setTimer = setTimeout,
  clearTimer = clearTimeout,
}) {
  let running = false;
  let generation = 0;
  let name = "";
  let state = null;
  let timer = null;
  let busy = false;
  let readAgain = false;
  let artwork = { url: "", data: "" };
  let lastSnapshot = "null";
  let warned = false;
  const followUps = new Set();

  async function heldByGecko(candidate) {
    const reply = await run([...BUS, "GetConnectionUnixProcessID", "s", candidate]);
    return Boolean(pid()) && reply?.data?.[0] === pid();
  }

  async function findPlayer() {
    const reply = await run([...BUS, "ListNames"]);
    const names = Array.isArray(reply?.data?.[0]) ? reply.data[0] : [];
    for (const candidate of names.filter((item) => FIREFOX_NAME.test(String(item)))) {
      // A player can close between the two questions.
      if (await heldByGecko(candidate).catch(() => false)) return candidate;
    }
    return "";
  }

  async function artworkFor(url) {
    const value = String(url || "");
    if (value !== artwork.url) artwork = { url: value, data: /^file:/i.test(value) ? await readArtwork(value) : value };
    return artwork.data;
  }

  function snapshot() {
    if (!running || !state?.shown) return null;
    const actions = state.reading?.actions || {};
    return {
      ...describeMedia(state, { title: title(), url: state.reading?.url || "" }),
      id: GECKO_MEDIA_ID,
      canPlayPause: (state.playing ? actions.pause : actions.play) === true,
    };
  }

  function publish() {
    const key = JSON.stringify(snapshot());
    if (key === lastSnapshot) return;
    lastSnapshot = key;
    onChange();
  }

  function schedule() {
    clearTimer(timer);
    timer = running ? setTimer(read, state ? POLL_MS : SEARCH_MS) : null;
    timer?.unref?.();
  }

  async function read() {
    if (!running) return;
    if (busy) { readAgain = true; return; }
    busy = true;
    const started = generation;
    let reading = null;
    try {
      name ||= await findPlayer();
      if (name) {
        const reply = await run([name, PLAYER_PATH, PROPERTIES, "GetAll", "s", PLAYER]);
        reading = readingFrom(reply, await artworkFor(reply?.data?.[0]?.Metadata?.data?.["mpris:artUrl"]?.data));
      }
    } catch (error) {
      name = "";
      if (error?.code === "ENOENT" && !warned) {
        warned = true;
        log("warn", "The media bar can't follow Gecko without busctl, which comes with systemd.");
      }
    }
    busy = false;
    if (!running) return;
    // If Firefox restarted meanwhile, this answer is about the old one, but the
    // new one still has to be followed.
    if (started === generation) {
      state = nextMediaState(state, reading, { now: now() });
      publish();
    }
    schedule();
    if (readAgain) {
      readAgain = false;
      void read();
    }
  }

  function clearFollowUps() {
    for (const followUp of followUps) clearTimer(followUp);
    followUps.clear();
  }

  function stop() {
    running = false;
    generation += 1;
    readAgain = false;
    name = "";
    state = null;
    artwork = { url: "", data: "" };
    clearTimer(timer);
    timer = null;
    clearFollowUps();
    publish();
  }

  return {
    start() {
      stop();
      running = true;
      void read();
    },
    stop,
    snapshot,
    async control(command) {
      if (!COMMANDS.has(command)) throw new Error(`Unknown media command: ${command}`);
      const actions = state?.reading?.actions || {};
      const allowed = { previous: actions.previous, next: actions.next, "play-pause": state?.playing ? actions.pause : actions.play }[command];
      if (!running || !state?.shown || !name || allowed !== true) return { handled: false };
      try {
        if (!await heldByGecko(name)) {
          name = "";
          return { handled: false };
        }
        await run([name, PLAYER_PATH, PLAYER, METHODS[command]]);
      } catch {
        return { handled: false };
      }
      for (const delay of [150, 600]) {
        const followUp = setTimer(() => { followUps.delete(followUp); void read(); }, delay);
        followUps.add(followUp);
      }
      return { handled: true };
    },
  };
}

module.exports = { GECKO_MEDIA_ID, MAX_ARTWORK_BYTES, readArtworkFile, readingFrom, watchGeckoMedia };
