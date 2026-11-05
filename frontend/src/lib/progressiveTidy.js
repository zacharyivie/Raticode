// Tidies a dictation while the user is still talking, as voice mode on the phone does, so
// the words show up tidied as they come instead of in one block when listening stops.
//
// While the speaker keeps talking, a round is due once TIDY_ROUND_WORDS new words have built
// up and TIDY_ROUND_GAP_MS have passed since the last round. Both have to hold, with no upper
// limit: a fast talker's round just carries more words, and a slow one isn't cut up every few
// words. A pause of TIDY_PAUSE_MS sends whatever is left. Every round sends everything said so
// far in this dictation, so the model can mend a sentence the last round cut in half. One
// request is out at a time;
// words said meanwhile wait for its answer. A failed answer changes nothing on screen, and
// the words stay safe in the dictation for the final tidy. Pure, so it is unit tested.

export const TIDY_ROUND_WORDS = 15;
export const TIDY_ROUND_GAP_MS = 5000;
export const TIDY_PAUSE_MS = 2000;

/** Actual words, not empty or punctuation-only recognizer output; only these are tidied. */
export function hasVoice(text) {
  return /[\p{L}\p{N}]/u.test(String(text ?? ""));
}

export function countWords(text) {
  return String(text ?? "").split(/\s+/).filter(hasVoice).length;
}

export class ProgressiveTidy {
  constructor(startedAt = 0) {
    // The heard words the shown text tidies, and that tidied text.
    this.tidiedFrom = null;
    this.tidied = "";
    // The words of the request that is out, if one is.
    this.asking = null;
    // How many words the last request carried, answered or not.
    this.askedWords = 0;
    // When the last request went, or listening began.
    this.askedAt = startedAt;
    // Words whose round just failed; asked again only once more is said, so a desktop that
    // fails at once can't be asked in a loop.
    this.failedOn = null;
  }

  /** The words to send now, or null when there is nothing new or a request is out. */
  next(heard, now) {
    const words = String(heard ?? "").trim();
    if (!hasVoice(words) || words === this.tidiedFrom || words === this.failedOn || this.asking !== null) return null;
    this.asking = words;
    this.askedWords = countWords(words);
    this.askedAt = now;
    return words;
  }

  /**
   * While talking: ms until a round is due, 0 when due now, or null while fewer than
   * TIDY_ROUND_WORDS new words have been said or a request is out. A pause sends regardless.
   */
  waitMs(heard, now) {
    if (this.asking !== null || !hasVoice(heard) || countWords(heard) - this.askedWords < TIDY_ROUND_WORDS) return null;
    return Math.max(0, this.askedAt + TIDY_ROUND_GAP_MS - now);
  }

  due(heard, now) {
    return this.waitMs(heard, now) === 0;
  }

  /** An answer came for words. Returns whether the shown text changed. */
  answered(words, result) {
    // A late answer to a dropped request, with a newer one out, is stale.
    if (this.asking !== null && this.asking !== words) return false;
    this.asking = null;
    const text = String(result?.text ?? "").trim();
    if (!result?.tidied || !text) {
      this.failedOn = words;
      return false;
    }
    this.tidiedFrom = words;
    const changed = this.tidied !== text;
    this.tidied = text;
    return changed;
  }

  /** The request was dropped, such as when listening ended. */
  dropped(words) {
    if (this.asking === words) this.asking = null;
  }

  /** The tidied words for exactly heard, when the last answer already covers everything said. */
  ready(heard) {
    return this.tidiedFrom !== null && this.tidiedFrom === String(heard ?? "").trim() ? this.tidied : null;
  }

  /** The tidied words so far; empty until a round has come back. */
  get shown() {
    return this.tidied;
  }
}
