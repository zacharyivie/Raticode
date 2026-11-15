import assert from "node:assert/strict";
import test from "node:test";

import { addRound, basicTidy, capitalizeFirst, closeDictation, endsOpen, endsSentence, holdStop } from "./dictationPunctuation.js";

// Rounds as Claude Haiku 5.5 answered them for one desktop dictation: each round was cut by
// word count in the middle of a sentence and closed with a full stop, and the next round
// started small because it carried the sentence on.
test("a full stop a round put in mid-sentence is dropped when the next round carries the sentence on", () => {
  const first = holdStop("That last dictation was a little sloppy, and it put in lowercase letters.");
  assert.deepEqual(first, { text: "That last dictation was a little sloppy, and it put in lowercase letters", held: true });
  let box = addRound("", first.text);
  box = addRound(box, "and improper punctuation sometimes.", { held: first.held });
  assert.equal(box, "That last dictation was a little sloppy, and it put in lowercase letters and improper punctuation sometimes.");
});

test("a held full stop goes back in when the next round starts a new sentence", () => {
  const first = holdStop("I hit the button and you start listening no matter what screen I'm on.");
  let box = addRound("", first.text);
  box = addRound(box, "I'd also like Rem to look at my screen.", { held: first.held });
  assert.equal(box, "I hit the button and you start listening no matter what screen I'm on. I'd also like Rem to look at my screen.");
});

test("words after a finished sentence start with a capital even when the model left them small", () => {
  assert.equal(addRound("Check the logs.", "i'd also like the tests run."), "Check the logs. I'd also like the tests run.");
  assert.equal(addRound("Did it pass?", "or did it fail"), "Did it pass? Or did it fail");
  assert.equal(addRound("", "and then stop."), "And then stop.");
  // Mid-sentence, small stays small.
  assert.equal(addRound("The reason was", "the cache we added on Monday."), "The reason was the cache we added on Monday.");
  // A colon or comma doesn't end a sentence.
  assert.equal(addRound("Note:", "book a room."), "Note: book a room.");
});

test("only a plain closing full stop is held back", () => {
  assert.deepEqual(holdStop("Is it done?"), { text: "Is it done?", held: false });
  assert.deepEqual(holdStop("Wait!"), { text: "Wait!", held: false });
  assert.deepEqual(holdStop("And then..."), { text: "And then...", held: false });
  assert.deepEqual(holdStop("Fruit, nuts, etc."), { text: "Fruit, nuts, etc.", held: false });
  assert.deepEqual(holdStop("Ask Dr."), { text: "Ask Dr.", held: false });
  assert.deepEqual(holdStop("It's from the U.S."), { text: "It's from the U.S.", held: false });
  assert.deepEqual(holdStop("Talk to John F."), { text: "Talk to John F.", held: false });
  assert.deepEqual(holdStop("I said no."), { text: "I said no", held: true });
  assert.deepEqual(holdStop("no stop"), { text: "no stop", held: false });
});

test("words as heard get basic capitals, and a held stop goes back in before them", () => {
  assert.equal(basicTidy("  so i think i'm right and i’ve checked  it "), "so I think I'm right and I’ve checked it");
  // The word "i" inside other words is left alone.
  assert.equal(basicTidy("idea in it"), "idea in it");
  assert.equal(addRound("Check the build", "and the tests", { held: true, tidied: false }), "Check the build. And the tests");
  assert.equal(addRound("", "fuck it i'll do it myself", { tidied: false }), "Fuck it I'll do it myself");
});

test("the end of a dictation closes its last sentence", () => {
  assert.equal(closeDictation("Or whatever I've been focused on"), "Or whatever I've been focused on.");
  assert.equal(closeDictation("Done."), "Done.");
  assert.equal(closeDictation("Really?"), "Really?");
  assert.equal(closeDictation('He said "go."'), 'He said "go."');
  assert.equal(closeDictation("Note:"), "Note:");
  assert.equal(closeDictation(""), "");
});

test("sentence ends and starts are found past quotes and brackets", () => {
  assert.equal(endsSentence('She said "yes."'), true);
  assert.equal(endsSentence("(see above.)"), true);
  assert.equal(endsSentence("so then"), false);
  assert.equal(endsOpen("version 2"), true);
  assert.equal(endsOpen("done."), false);
  assert.equal(capitalizeFirst('"what now"'), '"What now"');
  assert.equal(capitalizeFirst("éclair"), "Éclair");
  assert.equal(capitalizeFirst("42 things"), "42 things");
});
