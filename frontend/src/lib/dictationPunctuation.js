// How tidied dictation rounds join up in the message box. Pure, so it is unit tested.
//
// Rounds are cut by word count and time, not where the speaker ends a sentence, so the model
// often closes a round with a full stop in the middle of a sentence. Once in the box that
// stop can't be taken back (rounds only add to the box), and the next round reads like
// ". and in proper punctuation". So a round's closing full stop is held back. The next
// round, sent with the box's end as the text it continues, decides: a new sentence starts
// with a capital and the stop goes in first; words that carry on the sentence start small
// and the stop is dropped. The end of the dictation always closes its last sentence.
//
// When tidying fails, the words go in as heard with basic capitals and a closing full stop,
// so they never read as one lowercase run.

// Words whose full stop belongs to the word, not the sentence.
const ABBREVIATIONS = new Set([
  "dr", "e.g", "etc", "i.e", "jr", "mr", "mrs", "ms", "prof", "sr", "st", "vs",
]);

/** The text ends a sentence: a closing mark, perhaps inside a quote or bracket. */
export function endsSentence(text) {
  return /[.!?…]["'”’)\]]*$/u.test(String(text ?? "").trimEnd());
}

/** The text ends in the middle of a sentence, on a word or number. */
export function endsOpen(text) {
  return /[\p{L}\p{N}]$/u.test(String(text ?? "").trimEnd());
}

/** The first letter as a capital, past any opening quote or bracket. */
export function capitalizeFirst(text) {
  return String(text ?? "").replace(/^([\s"'“‘([]*)(\p{Ll})/u, (_, lead, letter) => lead + letter.toUpperCase());
}

function startsSmall(text) {
  return /^[\s"'“‘([]*\p{Ll}/u.test(String(text ?? ""));
}

/**
 * A round's text with its closing full stop taken off, and whether one was. Ellipses,
 * question and exclamation marks, abbreviations and initials keep theirs.
 */
export function holdStop(text) {
  const words = String(text ?? "").trim();
  const match = /(^|[^.])\.$/u.exec(words);
  if (!match) return { text: words, held: false };
  const last = words.slice(0, -1).split(/\s+/).at(-1) ?? "";
  const bare = last.toLowerCase().replace(/^["'“‘([]+/u, "");
  if (ABBREVIATIONS.has(bare) || /^\p{L}$/u.test(bare) || /\.\p{L}$/u.test(bare)) return { text: words, held: false };
  return { text: words.slice(0, -1).trimEnd(), held: true };
}

/** Basic tidying for words as heard: the pronoun I as a capital and single spaces. */
export function basicTidy(text) {
  return String(text ?? "")
    .trim()
    .split(/\s+/)
    .map((word) => word.replace(/^i(?=$|['’](?:m|d|ll|ve)$)/u, "I"))
    .join(" ");
}

/**
 * The box with a round's words added at its end.
 * - held: the box's last round had its full stop held back, and the box hasn't changed since.
 * - tidied: the words came from the model. Words as heard have no capitals to go by, so a
 *   held stop goes back in before them, and they get basicTidy.
 */
export function addRound(box, words, { held = false, tidied = true } = {}) {
  let text = String(box ?? "").trimEnd();
  let add = String(words ?? "").trim();
  if (!tidied) add = basicTidy(add);
  if (!add) return text;
  if (held && endsOpen(text) && !(tidied && startsSmall(add))) text += ".";
  if (!text || endsSentence(text)) add = capitalizeFirst(add);
  return text ? `${text} ${add}` : add;
}

/** The box at the end of a dictation: its last sentence closed with a full stop. */
export function closeDictation(box) {
  const text = String(box ?? "").trimEnd();
  return endsOpen(text) ? `${text}.` : text;
}
