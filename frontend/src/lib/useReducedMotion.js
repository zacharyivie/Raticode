import { useEffect, useState } from "react";

function readPreference() {
  const value = globalThis.document?.documentElement?.dataset.reducedMotion;
  return value === "true" || (value !== "false" && Boolean(globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches));
}

// App resolves the explicit setting and system preference on the root element.
export function useReducedMotion() {
  const [reduced, setReduced] = useState(readPreference);
  useEffect(() => {
    const update = () => setReduced(readPreference());
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-reduced-motion"] });
    const media = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
    media?.addEventListener("change", update);
    update();
    return () => { observer.disconnect(); media?.removeEventListener("change", update); };
  }, []);
  return reduced;
}
