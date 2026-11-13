// Geometry for the Gecko tab. Firefox's window sits above Raticode's page, so
// anything Raticode draws over the tab would be hidden by it. A menu gets a
// hole cut in the window, so the rest of the page stays on screen; the window
// steps aside only for something that covers most of it, like a dialog.

// Window pixels for a CSS rectangle, as X11 counts them.
export function deviceBounds(rect, ratio = 1) {
  const left = Math.round(rect.left * ratio);
  const top = Math.round(rect.top * ratio);
  return {
    x: left,
    y: top,
    width: Math.max(0, Math.round((rect.left + rect.width) * ratio) - left),
    height: Math.max(0, Math.round((rect.top + rect.height) * ratio) - top),
  };
}

// A grid of points just inside the rectangle.
export function coverSamples(rect, columns = 5, rows = 5) {
  const points = [];
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      points.push({
        x: rect.left + 1 + ((rect.width - 2) * column) / Math.max(1, columns - 1),
        y: rect.top + 1 + ((rect.height - 2) * row) / Math.max(1, rows - 1),
      });
    }
  }
  return points;
}

// Whether something other than the slot is on top at any sample point.
// elementAt returns the top element at a point, or null outside the window.
export function isCovered(slot, rect, elementAt, contains = (element) => slot === element || slot.contains?.(element)) {
  return coverSamples(rect).some(({ x, y }) => {
    const element = elementAt(x, y);
    return Boolean(element) && !contains(element);
  });
}

export function sameBounds(a, b) {
  return Boolean(a && b) && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height && a.visible === b.visible
    && JSON.stringify(a.cutouts || []) === JSON.stringify(b.cutouts || []);
}

// The outermost floating element (position fixed or absolute) an element
// belongs to that isn't around the slot itself: the whole menu, not one item.
export function floatingRoot(element, slot, positionOf) {
  let found = null;
  for (let node = element; node; node = node.parentElement) {
    if (node === slot || node.contains?.(slot)) break;
    const position = positionOf(node);
    if (position === "fixed" || position === "absolute") found = node;
  }
  return found;
}

// What covers the slot, as holes to cut in Firefox's window so the page stays
// on screen around them. Returns [] when nothing covers it, and null when the
// window has to step aside instead: something covers most of it (a dialog's
// backdrop), or something that isn't a floating menu covers it.
// extra lists open menus and dialogs, which may sit between the sample points.
export function coverCutouts(slot, rect, { elementAt, positionOf, rectOf, extra = [], ratio = 1, pad = 4, columns = 9, rows = 7, most = 0.6 }) {
  const covers = new Set();
  for (const { x, y } of coverSamples(rect, columns, rows)) {
    const element = elementAt(x, y);
    if (!element || element === slot || slot.contains?.(element)) continue;
    const root = floatingRoot(element, slot, positionOf);
    if (!root) return null;
    covers.add(root);
  }
  for (const element of extra) {
    if (element && element !== slot && !slot.contains?.(element) && !element.contains?.(slot)) covers.add(element);
  }
  const holes = [];
  let area = 0;
  for (const element of covers) {
    const box = rectOf(element);
    const left = Math.max(rect.left, box.left - pad);
    const top = Math.max(rect.top, box.top - pad);
    const right = Math.min(rect.left + rect.width, box.left + box.width + pad);
    const bottom = Math.min(rect.top + rect.height, box.top + box.height + pad);
    if (right <= left || bottom <= top) continue;
    area += (right - left) * (bottom - top);
    holes.push(deviceBounds({ left: left - rect.left, top: top - rect.top, width: right - left, height: bottom - top }, ratio));
  }
  if (area > rect.width * rect.height * most || holes.length > 16) return null;
  return holes;
}
