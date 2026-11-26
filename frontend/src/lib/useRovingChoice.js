import { useRef } from "react";

// Arrow-key steps for each orientation. Radio groups take all four arrows (WAI-ARIA APG);
// a horizontal tab list takes Left and Right, a vertical one Up and Down.
const STEPS = {
  both: { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 },
  horizontal: { ArrowLeft: -1, ArrowRight: 1 },
  vertical: { ArrowUp: -1, ArrowDown: 1 },
};

/**
 * Keyboard model for a radio group or a tab list: one Tab stop, arrows to move.
 *
 * The selected item has tabIndex 0 and the rest -1 (the first item when nothing is
 * selected). Arrow keys move to the next or previous item and wrap, Home and End go to the
 * ends, and the item focused is also chosen through `onSelect` (APG automatic activation).
 *
 * Returns `itemProps(key)`: spread it on each radio or tab. `keys` lists the items in the
 * order they appear, without any that aren't rendered. While `busy`, the keys do nothing,
 * for a group that saves each choice and mustn't take another until the save ends.
 */
export function useRovingChoice({ keys, selected, onSelect, orientation = "both", busy = false }) {
  const elements = useRef(new Map());
  const steps = STEPS[orientation] || STEPS.both;
  const stop = keys.includes(selected) ? selected : keys[0];
  return function itemProps(key) {
    return {
      ref: element => {
        if (element) elements.current.set(key, element);
        else elements.current.delete(key);
      },
      tabIndex: key === stop ? 0 : -1,
      onKeyDown: event => {
        if (busy || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
        const index = keys.indexOf(key);
        const step = steps[event.key];
        if (index < 0 || (!step && event.key !== "Home" && event.key !== "End")) return;
        event.preventDefault();
        const target = event.key === "Home" ? 0 : event.key === "End" ? keys.length - 1 : (index + step + keys.length) % keys.length;
        const next = keys[target];
        if (next !== selected) onSelect(next);
        elements.current.get(next)?.focus();
      },
    };
  };
}
