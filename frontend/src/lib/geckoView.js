// Geometry for the Gecko tab. Firefox's window sits above Raticode's page, so
// anything Raticode draws over the tab (a menu, a dialog) would be hidden by it;
// the window steps aside while something covers it.

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
  return Boolean(a && b) && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height && a.visible === b.visible;
}
