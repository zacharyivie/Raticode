import { useEffect, useRef } from "react";

const ENABLED_ITEM = "[role=menuitem]:not([disabled])";

// Keyboard behavior for a role="menu" list: focus starts on the first enabled item, ArrowUp,
// ArrowDown, Home and End move between items, and Escape closes the menu with
// { restoreFocus: true } so the caller can put focus back on what opened it.
// Returns the menu's onKeyDown handler. A press outside the menu (or outside its parent, with
// includeParent) closes it too unless closeOnOutsidePress is false, for menus that already
// close on outside presses their own way. When focusKey changes, as when items finish
// loading, focus moves to the first item unless it is already inside the menu.
export function usePopupMenu(menuRef, onClose, { includeParent = false, closeOnOutsidePress = true, focusKey } = {}) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const menu = menuRef.current;
    if (!menu || menu.contains(menu.ownerDocument?.activeElement)) return;
    menu.querySelector(ENABLED_ITEM)?.focus();
  }, [menuRef, focusKey]);
  useEffect(() => {
    if (!closeOnOutsidePress) return undefined;
    const close = (event) => {
      const inside = includeParent ? menuRef.current?.parentElement : menuRef.current;
      if (!inside?.contains(event.target)) onCloseRef.current();
    };
    // The window the menu is in: Raticode's, or the Browser's own window.
    const view = menuRef.current?.ownerDocument?.defaultView || window;
    view.addEventListener("pointerdown", close, true);
    return () => view.removeEventListener("pointerdown", close, true);
  }, [menuRef, includeParent, closeOnOutsidePress]);
  return (event) => {
    const enabled = [...(menuRef.current?.querySelectorAll(ENABLED_ITEM) || [])];
    if (!enabled.length) return;
    const index = enabled.indexOf((menuRef.current?.ownerDocument || document).activeElement);
    const next = {
      ArrowDown: (index + 1) % enabled.length,
      ArrowUp: index <= 0 ? enabled.length - 1 : index - 1,
      Home: 0,
      End: enabled.length - 1,
    }[event.key];
    if (next !== undefined) { event.preventDefault(); enabled[next].focus(); return; }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onCloseRef.current({ restoreFocus: true });
    }
  };
}

// A role="menu" element with usePopupMenu's keyboard behavior. It leaves outside presses to the
// caller, so a menu keeps the mouse behavior it had. Render it only while the menu is open.
export function PopupMenu({ menuRef, onClose, focusKey, children, ...props }) {
  const ownRef = useRef(null);
  const ref = menuRef || ownRef;
  const handleKeyDown = usePopupMenu(ref, onClose, { closeOnOutsidePress: false, focusKey });
  return <div ref={ref} role="menu" {...props} onKeyDown={handleKeyDown}>{children}</div>;
}

// Where focus goes back to when a menu opened by `event` closes with Escape. A right-click or
// the context-menu key lands on a row that holds buttons, so it is the focused control in that
// row, else the row's own menu button, else whatever had focus when the menu opened.
export function menuTrigger(event) {
  const area = event?.currentTarget;
  if (!area) return null;
  if (area.tagName === "BUTTON") return area;
  const doc = area.ownerDocument;
  const active = doc?.activeElement && doc.activeElement !== doc.body ? doc.activeElement : null;
  if (active && area.contains(active)) return active;
  return area.querySelector?.('[aria-haspopup="menu"]') || active;
}
