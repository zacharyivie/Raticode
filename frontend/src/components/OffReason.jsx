import { useId } from "react";

// A button that is off for a reason stays in the Tab and arrow-key order
// (aria-disabled, not disabled) and says why in a muted line tied to it with
// aria-describedby, so the reason is readable without hovering.
// Pass the reason text when the button is off and "" (or nothing) when it is on.
export function useOffReason(reason) {
  const id = useId();
  const off = Boolean(reason);
  return {
    off,
    props: off ? { "aria-disabled": "true", "aria-describedby": id } : {},
    // Wraps a click handler so it does nothing while the button is off.
    guard: (handler) => (event) => {
      if (off) {
        event?.preventDefault?.();
        event?.stopPropagation?.();
        return;
      }
      handler?.(event);
    },
    line: off ? <OffReasonLine id={id} reason={reason} /> : null,
  };
}

function OffReasonLine({ id, reason }) {
  return (
    <p className="text-[11px] leading-4 text-muted" data-off-reason="" id={id}>
      {reason}
    </p>
  );
}
