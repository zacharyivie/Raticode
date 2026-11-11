// Whether a Firefox, Chrome or Edge tab is playing sound, for the tab's speaker
// icon and so a background tab playing music isn't unloaded to save memory.
// Raticode's own tabs get this from Electron's audio-state-changed event; the
// windowless browsers have no such event, so each tab checks its page every
// few seconds in a script world the page cannot see.

const SOUND_POLL_MS = 2500;

// Whether [media] can be heard: its volume is up and it has a sound track. An
// element Raticode muted for the tab's mute still counts, as a muted Raticode
// tab still shows its crossed-out speaker. Silent looping videos do not:
// browsers report no sound track, or no sound decoded after the first second.
function heardSource(tabMuted) {
  return `media.volume > 0 && (!media.muted || ${tabMuted === true}) && media.mozHasAudio !== false && !(media.webkitAudioDecodedByteCount === 0 && media.currentTime > 1)`;
}

// A playing audio or video element that can be heard, or, on a page with no
// such element at all, the page's own word through navigator.mediaSession. Only
// elements in the page's document are seen: not sound from inside frames.
function soundExpression(tabMuted) {
  return `(() => { const elements = [...document.querySelectorAll("audio,video")]; return elements.some((media) => !media.paused && !media.ended && ${heardSource(tabMuted)}) || (!elements.length && navigator.mediaSession?.playbackState === "playing"); })()`;
}

// Starts checking [contents] (a FirefoxContents or ChromiumContents) and
// returns a function that checks at once. [ready] says whether the tab has a
// page to ask; [evaluate] runs an expression in the tab's private world.
function watchSound(contents, { ready, evaluate }) {
  let busy = false;
  const check = async () => {
    if (busy || contents.destroyed || contents.dead || !ready()) return;
    busy = true;
    let playing;
    try {
      playing = (await evaluate(soundExpression(contents.muted))) === true;
    } catch {
      return;
    } finally {
      busy = false;
    }
    if (contents.destroyed || playing === (contents.audible === true)) return;
    contents.audible = playing;
    contents.emit("audio-state-changed", { audible: playing });
  };
  const timer = setInterval(() => void check(), SOUND_POLL_MS);
  timer.unref?.();
  contents.once("destroyed", () => clearInterval(timer));
  return { check, stop: () => clearInterval(timer) };
}

module.exports = { SOUND_POLL_MS, heardSource, soundExpression, watchSound };
