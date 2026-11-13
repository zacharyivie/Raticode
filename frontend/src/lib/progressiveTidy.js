// Tidies a dictation while the user is still talking, as voice mode on the phone does, so
// the words show up tidied as they come instead of in one block when listening stops.
//
// While the speaker keeps talking, a round is due once TIDY_ROUND_WORDS new words have built
// up and TIDY_ROUND_GAP_MS have passed since the last round. Both have to hold, with no upper
// limit: a fast talker's round just carries more words, and a slow one isn't cut up every few
// words. A pause of TIDY_PAUSE_MS sends whatever is left.
//
// Each round sends only the words said since the last tidied round (the tail), and its answer
// is added to the end of the message box. Text already tidied is never asked about again, so
// a later round can't rewrite it, and a long dictation never grows past what one request can
// tidy. One request is out at a time; words said meanwhile wait for its answer. A failed
// round adds nothing: its words stay in the tail, the next round asks about them again with
// whatever was said since, and stopping adds whatever is left, tidied or as heard. Pure, so it
// is unit tested.

export const TIDY_ROUND_WORDS = 15;
export const TIDY_ROUND_GAP_MS = 5000;
export const TIDY_PAUSE_MS = 2000;
// How far the recognizer may still move the end of the words already tidied (a word it split
// or joined) and still be lined up with them.
const ALIGN_WORDS = 6;

/** Actual words, not empty or punctuation-only recognizer output; only these are tidied. */
export function hasVoice(text) {
  return /[\p{L}\p{N}]/u.test(String(text ?? ""));
}

export function countWords(text) {
  return String(text ?? "").split(/\s+/).filter(hasVoice).length;
}

function bare(word) {
  return String(word).toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
}

/**
 * The words of heard said after done, a version of heard's start that was already tidied. The
 * recognizer may still have revised done's last words (punctuation added, a word split or
 * joined), so its last words are lined up with heard rather than its exact text.
 */
export function tailAfter(heard, done) {
  const text = String(heard ?? "").trim();
  const before = String(done ?? "").trim();
  if (!before) return text;
  if (text === before) return "";
  if (text.startsWith(before) && /\s/.test(text[before.length])) return text.slice(before.length).trim();
  const words = text.split(/\s+/);
  const doneWords = before.split(/\s+/);
  // The last three tidied words, or the last two when one of the three was revised.
  for (const size of doneWords.length > 2 ? [3, 2] : [doneWords.length]) {
    const key = doneWords.slice(-size).map(bare);
    const lineUp = (at) => at >= size && at <= words.length && key.every((word, index) => bare(words[at - size + index]) === word);
    for (let shift = 0; shift <= ALIGN_WORDS; shift += 1) {
      for (const at of new Set([doneWords.length + shift, doneWords.length - shift])) {
        if (lineUp(at)) return words.slice(at).join(" ");
      }
    }
  }
  // No match: the words past as many as were tidied are the new ones.
  return words.slice(Math.min(doneWords.length, words.length)).join(" ");
}

export class ProgressiveTidy {
  constructor(startedAt = 0) {
    // Everything heard up to the end of the last tidied round: its words are in the box.
    this.done = "";
    // The request that is out, if one is: the heard words it covers and the tail it sent.
    this.asking = null;
    // How many words had been heard when the last request went, answered or not.
    this.askedWords = 0;
    // When the last request went, or listening began.
    this.askedAt = startedAt;
    // A tail whose round just failed; asked again only once more is said, so a desktop that
    // fails at once can't be asked in a loop.
    this.failedOn = null;
  }

  /** The words heard since the last tidied round: not in the box yet. */
  tail(heard) {
    return tailAfter(heard, this.done);
  }

  /** The request to send now ({ upto, words }), or null when nothing new was said or one is out. */
  next(heard, now) {
    if (this.asking !== null) return null;
    const upto = String(heard ?? "").trim();
    const words = this.tail(upto);
    if (!hasVoice(words) || words === this.failedOn) return null;
    this.asking = { upto, words };
    this.askedWords = countWords(upto);
    this.askedAt = now;
    return this.asking;
  }

  /**
   * While talking: ms until a round is due, 0 when due now, or null while fewer than
   * TIDY_ROUND_WORDS new words have been said or a request is out. A pause sends regardless.
   */
  waitMs(heard, now) {
    if (this.asking !== null || !hasVoice(this.tail(heard)) || countWords(heard) - this.askedWords < TIDY_ROUND_WORDS) return null;
    return Math.max(0, this.askedAt + TIDY_ROUND_GAP_MS - now);
  }

  due(heard, now) {
    return this.waitMs(heard, now) === 0;
  }

  /**
   * An answer came for request. Returns the text to add to the box, or null: the round
   * failed (its words stay in the tail) or the answer is for a request no longer out.
   */
  answered(request, result) {
    if (this.asking !== request) return null;
    this.asking = null;
    const text = String(result?.text ?? "").trim();
    if (!result?.tidied || !text) {
      this.failedOn = request.words;
      return null;
    }
    this.done = request.upto;
    this.failedOn = null;
    return text;
  }

  /** The words of request go into the box as heard, such as when stopping and tidying failed. */
  kept(request) {
    if (this.asking === request) this.asking = null;
    this.done = request.upto;
  }

  /** The request was dropped, such as when listening ended. */
  dropped(request) {
    if (this.asking === request) this.asking = null;
  }
}
