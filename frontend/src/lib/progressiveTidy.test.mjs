import assert from "node:assert/strict";
import test from "node:test";

import { ProgressiveTidy, TIDY_PAUSE_MS, TIDY_ROUND_GAP_MS, TIDY_ROUND_WORDS, countWords, hasVoice } from "./progressiveTidy.js";

const words = Array.from({ length: 60 }, (_, index) => `w${index + 1}`);
const said = (count) => words.slice(0, count).join(" ");

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
  assert.equal(tidy.next(said(28), 5000), said(28));
  // Nothing more is due while it is out.
  assert.equal(tidy.waitMs(said(50), 20000), null);
  tidy.answered(said(28), { text: "W1 to w28.", tidied: true });
  assert.equal(tidy.shown, "W1 to w28.");
  // The next round counts 15 more words and 5 more seconds from that request.
  assert.equal(tidy.due(said(42), 20000), false);
  assert.equal(tidy.due(said(43), 9999), false);
  assert.equal(tidy.due(said(43), 10000), true);
  assert.equal(tidy.next(said(43), 10000), said(43));
  // A failed round changes nothing shown and still counts toward the next.
  assert.equal(tidy.answered(said(43), { text: said(43), tidied: false, reason: "slow" }), false);
  assert.equal(tidy.shown, "W1 to w28.");
  assert.equal(tidy.due(said(57), 60000), false);
  assert.equal(tidy.ready(said(28)), "W1 to w28.");
  assert.equal(tidy.ready(said(43)), null);
});

test("a pause asks directly, below both thresholds, but never for empty or punctuation-only input", () => {
  const tidy = new ProgressiveTidy(0);
  assert.equal(tidy.next("", 100), null);
  assert.equal(tidy.next(" ... ! ", 100), null);
  assert.equal(tidy.due(". , ! ".repeat(30), 60000), false);
  assert.equal(tidy.next("just three words", 1000), "just three words");
  assert.equal(tidy.next("just three words more", 1200), null, "one request at a time");
  assert.equal(countWords("book a room ... !"), 3);
  assert.equal(hasVoice(" - "), false);
});

test("a dropped request can be asked again and its late answer is ignored", () => {
  const tidy = new ProgressiveTidy(0);
  tidy.next("one", 0);
  tidy.dropped("one");
  assert.equal(tidy.next("one two", 10), "one two");
  assert.equal(tidy.answered("one", { text: "One.", tidied: true }), false);
  assert.equal(tidy.answered("one two", { text: "One, two.", tidied: true }), true);
  assert.equal(tidy.shown, "One, two.");
});

test("words whose round failed aren't asked again until more is said, so an instant failure can't loop", () => {
  const tidy = new ProgressiveTidy(0);
  assert.equal(tidy.next("check the build", 0), "check the build");
  tidy.answered("check the build", { text: "check the build", tidied: false, reason: "Tidying is off." });
  assert.equal(tidy.next("check the build", 10), null);
  assert.equal(tidy.next("check the build now", 20), "check the build now");
});
