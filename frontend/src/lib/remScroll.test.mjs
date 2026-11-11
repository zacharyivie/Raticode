import assert from "node:assert/strict";
import { test } from "node:test";
import { FOLLOW_ZONE, followsAfterScroll, gapToEnd, wheelLeavesEnd } from "./remScroll.js";

test("the first frames of a wheel turn up from the bottom stop Rem's chat following", () => {
  // A smooth wheel turn moves a few pixels per frame; every one of these frames is
  // inside the follow zone, and none of them may keep the chat following.
  let following = true;
  let last = { top: 1000, gap: 0 };
  for (const top of [995, 988, 978, 965, 951, 937]) {
    const position = { top, gap: 1000 - top };
    following = followsAfterScroll(following, position, last);
    assert.equal(following, false, `following at ${position.gap}px from the end`);
    last = position;
  }
});

test("Rem's chat follows again at the end, or when you move down into the follow zone", () => {
  assert.equal(followsAfterScroll(false, { top: 1000, gap: 0 }, { top: 900, gap: 100 }), true);
  assert.equal(followsAfterScroll(false, { top: 1000.6, gap: 0.6 }, null), true);
  assert.equal(followsAfterScroll(false, { top: 950, gap: 50 }, { top: 900, gap: 100 }), true);
  // Moving down while still far from the end doesn't.
  assert.equal(followsAfterScroll(false, { top: 700, gap: 300 }, { top: 600, gap: 400 }), false);
});

test("Rem's chat keeps its choice when the browser holds your place as a message resizes", () => {
  // A message above grew: the top moves down by the same amount, the gap is unchanged.
  assert.equal(followsAfterScroll(false, { top: 1040, gap: 30 }, { top: 1000, gap: 30 }), false);
  assert.equal(followsAfterScroll(true, { top: 1040, gap: 0 }, { top: 1000, gap: 0 }), true);
  // Text arrived below while you read inside the follow zone: still reading.
  assert.equal(followsAfterScroll(false, { top: 1000, gap: 60 }, { top: 1000, gap: 30 }), false);
  // Scrolled far away by any means, such as dragging the scroll bar or a jump to a message.
  assert.equal(followsAfterScroll(true, { top: 400, gap: FOLLOW_ZONE + 20 }, null), false);
});

test("the end of Rem's chat is measured from the bottom of what you see", () => {
  assert.equal(gapToEnd({ scrollHeight: 2000, scrollTop: 1400, clientHeight: 500 }), 100);
});

function node(props) {
  return { scrollTop: 0, scrollHeight: 100, clientHeight: 100, parentElement: null, ...props };
}

test("a wheel turn up over Rem's chat stops following before the chat moves", () => {
  const stream = node({ scrollTop: 900, scrollHeight: 1500, clientHeight: 600 });
  const text = node({ parentElement: node({ parentElement: stream }) });
  assert.equal(wheelLeavesEnd({ deltaY: -100, target: text }, stream), true);
  // Down, sideways, zooming, or a chat with nowhere to go up don't.
  assert.equal(wheelLeavesEnd({ deltaY: 100, target: text }, stream), false);
  assert.equal(wheelLeavesEnd({ deltaY: 0, deltaX: -40, target: text }, stream), false);
  assert.equal(wheelLeavesEnd({ deltaY: -100, ctrlKey: true, target: text }, stream), false);
  assert.equal(wheelLeavesEnd({ deltaY: -100, target: text }, node({ scrollTop: 0, scrollHeight: 400, clientHeight: 600 })), false);
});

test("a wheel turn up taken by a scrolling block inside a message leaves the chat following", () => {
  const stream = node({ scrollTop: 900, scrollHeight: 1500, clientHeight: 600 });
  const block = node({ scrollTop: 40, scrollHeight: 400, clientHeight: 200, parentElement: stream, overflowY: "auto" });
  const line = node({ parentElement: block });
  const original = globalThis.getComputedStyle;
  globalThis.getComputedStyle = element => ({ overflowY: element.overflowY || "visible" });
  try {
    assert.equal(wheelLeavesEnd({ deltaY: -100, target: line }, stream), false);
    // At its own top, the block passes the turn on to the chat.
    block.scrollTop = 0;
    assert.equal(wheelLeavesEnd({ deltaY: -100, target: line }, stream), true);
    // A block that only clips (overflow hidden) never takes the wheel.
    block.scrollTop = 40;
    block.overflowY = "hidden";
    assert.equal(wheelLeavesEnd({ deltaY: -100, target: line }, stream), true);
  } finally {
    globalThis.getComputedStyle = original;
  }
});
