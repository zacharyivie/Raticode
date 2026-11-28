import { useLayoutEffect, useRef } from "react";
import { Loader2 } from "lucide-react";

// A spinner that never jumps. A CSS animation starts over whenever its element is moved in
// the page, as when a running thread's new reply sorts its row to the top, or mounted
// again, as when a task changes group; the wheel then snaps back to its first angle. Each
// render puts the animation back in step with the page's clock (start time 0), before the
// browser paints, so a moved or new spinner carries on at the same angle as every other.
// The animation stays on the compositor. `className` brings the animation (rem-spin or
// animate-spin) and its reduced-motion rule, which leaves nothing to keep in step.
export default function SteadySpinner(props) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    for (const animation of ref.current?.getAnimations?.() ?? []) {
      if (animation.startTime !== 0) animation.startTime = 0;
    }
  });
  return <Loader2 ref={ref} {...props} />;
}
