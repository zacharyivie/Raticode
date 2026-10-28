import assert from "node:assert/strict";
import test from "node:test";
import { trackPointerDrag } from "./paneResize.js";

function fakeTarget() {
  const listeners = new Map();
  return {
    listeners,
    addEventListener(type, listener) {
      listeners.set(type, [...(listeners.get(type) ?? []), listener]);
    },
    removeEventListener(type, listener) {
      listeners.set(type, (listeners.get(type) ?? []).filter((item) => item !== listener));
    },
    dispatch(type, event = {}) {
      for (const listener of listeners.get(type) ?? []) listener(event);
    },
    count(type) {
      return listeners.get(type)?.length ?? 0;
    },
  };
}

function withFakeDom(run) {
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  const frames = [];
  const fakeWindow = Object.assign(fakeTarget(), {
    cancelAnimationFrame(id) { frames[id - 1] = null; },
    requestAnimationFrame(callback) { frames.push(callback); return frames.length; },
  });
  globalThis.window = fakeWindow;
  globalThis.document = { body: { style: { cursor: "auto", userSelect: "text" } } };
  const runFrames = () => {
    const pending = frames.splice(0);
    for (const callback of pending) callback?.();
  };
  try {
    return run({ body: globalThis.document.body, runFrames, window: fakeWindow });
  } finally {
    globalThis.window = previousWindow;
    globalThis.document = previousDocument;
  }
}

function pointerDown(handle, overrides = {}) {
  return {
    button: 0,
    clientX: 100,
    clientY: 100,
    currentTarget: handle,
    pointerId: 7,
    preventDefault() {},
    ...overrides,
  };
}

function fakeHandle() {
  const handle = fakeTarget();
  handle.captured = new Set();
  handle.setPointerCapture = (id) => handle.captured.add(id);
  handle.hasPointerCapture = (id) => handle.captured.has(id);
  handle.releasePointerCapture = (id) => handle.captured.delete(id);
  return handle;
}

test("pane drags apply at most one move per frame and finish once", () => {
  withFakeDom(({ body, runFrames, window }) => {
    const handle = fakeHandle();
    const moves = [];
    let ends = 0;
    assert.equal(trackPointerDrag(pointerDown(handle), {
      cursor: "col-resize",
      onEnd: () => { ends += 1; },
      onMove: (point) => moves.push(point.clientX),
    }), true);

    assert.equal(body.style.cursor, "col-resize");
    assert.equal(body.style.userSelect, "none");
    assert.ok(handle.captured.has(7));

    for (const clientX of [110, 120, 130]) window.dispatch("pointermove", { clientX, clientY: 0, pointerId: 7 });
    window.dispatch("pointermove", { clientX: 999, clientY: 0, pointerId: 8 });
    assert.deepEqual(moves, []);
    runFrames();
    assert.deepEqual(moves, [130]);

    window.dispatch("pointermove", { clientX: 140, clientY: 0, pointerId: 7 });
    window.dispatch("pointerup", { pointerId: 7 });
    handle.dispatch("lostpointercapture");
    runFrames();

    assert.deepEqual(moves, [130, 140]);
    assert.equal(ends, 1);
    assert.equal(body.style.cursor, "auto");
    assert.equal(body.style.userSelect, "text");
    assert.equal(handle.captured.size, 0);
    for (const type of ["pointermove", "pointerup", "pointercancel", "blur"]) assert.equal(window.count(type), 0);
    assert.equal(handle.count("lostpointercapture"), 0);
  });
});

test("pane drags end when the pointer is cancelled or capture is lost", () => {
  for (const [target, type] of [["window", "pointercancel"], ["window", "blur"], ["handle", "lostpointercapture"]]) {
    withFakeDom(({ window }) => {
      const handle = fakeHandle();
      let ends = 0;
      trackPointerDrag(pointerDown(handle), { cursor: "row-resize", onEnd: () => { ends += 1; }, onMove() {} });
      (target === "window" ? window : handle).dispatch(type, { pointerId: 7 });
      assert.equal(ends, 1, type);
      assert.equal(window.count("pointermove"), 0, type);
    });
  }
});

test("pane drags ignore secondary mouse buttons", () => {
  withFakeDom(({ body, window }) => {
    const handle = fakeHandle();
    assert.equal(trackPointerDrag(pointerDown(handle, { button: 2 }), { cursor: "col-resize", onMove() {} }), false);
    assert.equal(body.style.cursor, "auto");
    assert.equal(window.count("pointermove"), 0);
    assert.equal(handle.captured.size, 0);
  });
});
