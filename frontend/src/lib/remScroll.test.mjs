import assert from "node:assert/strict";
import { test } from "node:test";
import { FOLLOW_ZONE, HOLD_PLACE_MS, followsAfterScroll, gapToEnd, readingPlace, restoreReadingPlace, wheelLeavesEnd } from "./remScroll.js";

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

// A chat of rows laid out top to bottom, scrolled by scrollTop, inside a 500px view at y=100.
function chat(heights) {
  const stream = { scrollTop: 0, rows: [] };
  let y = 0;
  for (const [index, height] of heights.entries()) {
    const row = { id: `m${index}`, top: y, height };
    stream.rows.push(row);
    y += height;
  }
  const box = row => ({ top: 100 + row.top - stream.scrollTop, bottom: 100 + row.top + row.height - stream.scrollTop });
  stream.getBoundingClientRect = () => ({ top: 100, bottom: 600 });
  stream.querySelectorAll = () => stream.rows.map(row => ({ getAttribute: () => row.id, getBoundingClientRect: () => box(row) }));
  // Rows added above shift every row below them down.
  stream.prepend = heights => {
    const added = heights.reduce((sum, height) => sum + height, 0);
    for (const row of stream.rows) row.top += added;
    stream.rows.unshift(...heights.map((height, index) => ({ id: `p${index}`, top: heights.slice(0, index).reduce((sum, h) => sum + h, 0), height })));
  };
  return stream;
}

test("Rem's chat reads its place from the first message whose top is in view", () => {
  const stream = chat([120, 120, 120, 120, 120, 120]);
  stream.scrollTop = 150;
  // m1 is cut off at the top (120..240 shows 150..240); m2 starts in view, 90px down.
  assert.deepEqual(readingPlace(stream, 1000), { id: "m2", offset: 90, until: 1000 + HOLD_PLACE_MS });
  // A message taller than the view: the one cut off at the top is all there is.
  const tall = chat([2000, 120]);
  tall.scrollTop = 300;
  assert.equal(readingPlace(tall, 0).id, "m0");
  assert.equal(readingPlace(chat([]), 0), null);
  assert.equal(readingPlace(null), null);
});

test("Earlier messages added above leave the message you are reading where it was", () => {
  const stream = chat([120, 120, 120, 120, 120, 120]);
  stream.scrollTop = 150;
  const place = readingPlace(stream);
  stream.prepend([80, 300, 45]);
  restoreReadingPlace(stream, place);
  assert.equal(stream.scrollTop, 150 + 425);
  assert.equal(readingPlace(stream).offset, place.offset);
  // A row above that lays out at its real height later is corrected the same way.
  stream.rows[1].height += 60;
  for (const row of stream.rows.slice(2)) row.top += 60;
  restoreReadingPlace(stream, place);
  assert.equal(stream.scrollTop, 150 + 425 + 60);
  // A place whose message is gone moves nothing.
  restoreReadingPlace(stream, { id: "gone", offset: 0 });
  assert.equal(stream.scrollTop, 635);
});
