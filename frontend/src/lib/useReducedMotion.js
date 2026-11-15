import { useEffect, useState } from "react";

function readPreference() {
  const value = globalThis.document?.documentElement?.dataset?.reducedMotion;
  return value === "true" || (value !== "false" && Boolean(globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches));
}

// App resolves the explicit setting and system preference on the root element.
export function useReducedMotion() {
  const [reduced, setReduced] = useState(readPreference);
  useEffect(() => {
    const update = () => setReduced(readPreference());
    // Pages and tests without a full DOM still render; they just don't follow root changes.
    const root = globalThis.document?.documentElement;
    const observer = root && typeof globalThis.MutationObserver === "function" ? new globalThis.MutationObserver(update) : null;
    observer?.observe(root, { attributes: true, attributeFilter: ["data-reduced-motion"] });
    const media = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
    media?.addEventListener("change", update);
    update();
    return () => { observer?.disconnect(); media?.removeEventListener("change", update); };
  }, []);
  return reduced;
}
