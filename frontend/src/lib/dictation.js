// The words of one dictation across the recognition sessions it takes. Pausing ends a
// session and resuming starts a new one; earlier words stay, and a session cut short
// keeps the words it had. The same model as Raticode Mobile's DictationText.

/** No new words for this long pauses listening, so the microphone isn't left on unattended. Words stay. */
export const QUIET_MS = 45_000;

export function joinWords(...parts) {
  return parts.map((part) => String(part ?? "").trim()).filter(Boolean).join(" ");
}

export class DictationText {
  constructor(keep = "") {
    this.committed = String(keep).trim();
    this.current = "";
  }

  /** Everything heard so far, including the open session's words. */
  get text() {
    return joinWords(this.committed, this.current);
  }

  /** The open session's words so far. */
  partial(words) {
    this.current = String(words ?? "").trim();
  }

  /** A session's final words. An empty final keeps the words heard before it. */
  result(words) {
    this.committed = joinWords(this.committed, String(words ?? "").trim() || this.current);
    this.current = "";
  }

  /** A session ended without final words: keep what it had. */
  interrupted() {
    this.committed = joinWords(this.committed, this.current);
    this.current = "";
  }
}
