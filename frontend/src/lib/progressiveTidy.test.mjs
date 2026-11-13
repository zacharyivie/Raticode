import assert from "node:assert/strict";
import test from "node:test";

import { ProgressiveTidy, TIDY_PAUSE_MS, TIDY_ROUND_GAP_MS, TIDY_ROUND_WORDS, countWords, hasVoice, tailAfter } from "./progressiveTidy.js";

const words = Array.from({ length: 60 }, (_, index) => `w${index + 1}`);
const said = (count) => words.slice(0, count).join(" ");
const between = (from, to) => words.slice(from, to).join(" ");

test("while talking a round needs fifteen new words and five seconds since the last, with no upper limit", () => {
  assert.equal(TIDY_ROUND_WORDS, 15);
  assert.equal(TIDY_ROUND_GAP_MS, 5000);
  assert.ok(TIDY_PAUSE_MS < TIDY_ROUND_GAP_MS);
  const tidy = new ProgressiveTidy(0);
  // Slow speech: five seconds with three words isn't cut off.
  assert.equal(tidy.waitMs(said(3), 5000), null);
  // Fast speech: fifteen words in three seconds wait two more, and words said meanwhile go too.
  assert.equal(tidy.waitMs(said(15), 3000), 2000);
  assert.equal(tidy.due(said(24), 4999), false);
  assert.equal(tidy.due(said(28), 5000), true);
  const first = tidy.next(said(28), 5000);
  assert.deepEqual(first, { upto: said(28), words: said(28) });
  // Nothing more is due while it is out.
  assert.equal(tidy.waitMs(said(50), 20000), null);
  assert.equal(tidy.answered(first, { text: "W1 to w28.", tidied: true }), "W1 to w28.");
  // The next round counts 15 more words and 5 more seconds from that request...
  assert.equal(tidy.due(said(42), 20000), false);
  assert.equal(tidy.due(said(43), 9999), false);
  assert.equal(tidy.due(said(43), 10000), true);
  // ...and carries only the words said since the tidied ones.
  const second = tidy.next(said(43), 10000);
  assert.equal(second.words, between(28, 43));
  // A failed round adds nothing and its words stay for the next round, with the newer ones.
  assert.equal(tidy.answered(second, { text: second.words, tidied: false, reason: "slow" }), null);
  assert.equal(tidy.tail(said(43)), between(28, 43));
  assert.equal(tidy.due(said(57), 60000), false);
  assert.equal(tidy.next(said(58), 60000).words, between(28, 58));
});

test("a pause asks directly, below both thresholds, but never for empty or punctuation-only input", () => {
  const tidy = new ProgressiveTidy(0);
  assert.equal(tidy.next("", 100), null);
  assert.equal(tidy.next(" ... ! ", 100), null);
  assert.equal(tidy.due(". , ! ".repeat(30), 60000), false);
  assert.equal(tidy.next("just three words", 1000).words, "just three words");
  assert.equal(tidy.next("just three words more", 1200), null, "one request at a time");
  assert.equal(countWords("book a room ... !"), 3);
  assert.equal(hasVoice(" - "), false);
});

test("a dropped request can be asked again and its late answer adds nothing", () => {
  const tidy = new ProgressiveTidy(0);
  const dropped = tidy.next("one", 0);
  tidy.dropped(dropped);
  const asked = tidy.next("one two", 10);
  assert.equal(asked.words, "one two");
  assert.equal(tidy.answered(dropped, { text: "One.", tidied: true }), null);
  assert.equal(tidy.answered(asked, { text: "One, two.", tidied: true }), "One, two.");
  assert.equal(tidy.tail("one two"), "");
  // Answering the same request twice adds it once.
  assert.equal(tidy.answered(asked, { text: "One, two.", tidied: true }), null);
});

test("words whose round failed aren't asked again until more is said, so an instant failure can't loop", () => {
  const tidy = new ProgressiveTidy(0);
  const request = tidy.next("check the build", 0);
  tidy.answered(request, { text: "check the build", tidied: false, reason: "Tidying is off." });
  assert.equal(tidy.next("check the build", 10), null);
  assert.equal(tidy.next("check the build now", 20).words, "check the build now");
});

test("rounds only ever send the new words, so a tidied round is never asked about or rewritten again", () => {
  const tidy = new ProgressiveTidy(0);
  const added = [];
  const round = (heard, answer) => {
    const request = tidy.next(heard, 0);
    const text = tidy.answered(request, { text: answer(request.words), tidied: true });
    if (text) added.push(text);
    return request.words;
  };
  const tidyUp = (text) => `${text[0].toUpperCase()}${text.slice(1)}.`;
  assert.equal(round("book a room for friday", tidyUp), "book a room for friday");
  assert.equal(round("book a room for friday and invite the team", tidyUp), "and invite the team");
  assert.equal(round("book a room for friday and invite the team then send the agenda", tidyUp), "then send the agenda");
  assert.deepEqual(added, ["Book a room for friday.", "And invite the team.", "Then send the agenda."]);
});

test("the new words are found after tidied ones the recognizer has since revised", () => {
  // Unchanged, or only grown.
  assert.equal(tailAfter("book a room", "book a room"), "");
  assert.equal(tailAfter("book a room for friday", "book a room"), "for friday");
  assert.equal(tailAfter("book a room", ""), "book a room");
  // A longer word that starts like the last tidied one isn't cut in half.
  assert.equal(tailAfter("book a roomy place", "book a room"), "place");
  // Punctuation and capitals added later to the tidied words.
  assert.equal(tailAfter("Book a room. And invite the team", "book a room"), "And invite the team");
  // A word split in two, or two joined, inside the tidied words.
  assert.equal(tailAfter("to day is nice so we walk", "today is nice"), "so we walk");
  assert.equal(tailAfter("today is nice so we walk", "to day is nice"), "so we walk");
  // Nothing lines up: the words past as many as were tidied.
  assert.equal(tailAfter("alpha beta gamma delta", "one two"), "gamma delta");
  assert.equal(tailAfter("alpha", "one two three"), "");
});
