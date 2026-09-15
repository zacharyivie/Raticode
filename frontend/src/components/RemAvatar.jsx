import { useEffect, useId, useRef, useState } from "react";
import seated from "../assets/rem-options/display/indigo-android-open-224.png";
import seated3x from "../assets/rem-options/display/indigo-android-open-336.png";
import closed from "../assets/rem-options/display/indigo-android-224.png";
import closed3x from "../assets/rem-options/display/indigo-android-336.png";
import waving from "../assets/rem-options/display/indigo-android-wave-224.png";
import waving3x from "../assets/rem-options/display/indigo-android-wave-336.png";
import sleeping from "../assets/rem-options/display/indigo-android-sleep-224.png";
import sleeping3x from "../assets/rem-options/display/indigo-android-sleep-336.png";

export default function RemAvatar({ visible = true, animated = true, reducedMotion = "system" }) {
  const backgroundFilterId = `rem-background-${useId()}`;
  const spriteStyle = { filter: `url(#${backgroundFilterId})` };
  const root = useRef(null);
  const [loaded, setLoaded] = useState({});
  const [inView, setInView] = useState(true);
  const [pageVisible, setPageVisible] = useState(true);
  const [systemReduced, setSystemReduced] = useState(false);
  const [pose, setPose] = useState("seated");
  const [blinking, setBlinking] = useState(false);
  const ready = loaded.seated && loaded.closed && loaded.waving;
  const reduce = reducedMotion === "on" || (reducedMotion === "system" && systemReduced);
  const motion = animated && !reduce;
  const playing = visible && motion && pageVisible && inView && ready && pose !== "sleeping";

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const syncMotion = () => setSystemReduced(media?.matches ?? false);
    const syncVisibility = () => setPageVisible(document.visibilityState !== "hidden");
    syncMotion();
    syncVisibility();
    media?.addEventListener("change", syncMotion);
    document.addEventListener("visibilitychange", syncVisibility);
    const observer = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver(
      ([entry]) => setInView(entry.isIntersecting),
    );
    if (root.current) observer?.observe(root.current);
    return () => {
      media?.removeEventListener("change", syncMotion);
      document.removeEventListener("visibilitychange", syncVisibility);
      observer?.disconnect();
    };
  }, []);

  useEffect(() => {
    // Mounting the home screen and reopening the pane start a new session.
    // Viewport, document visibility, and motion preferences only pause playback.
    if (!visible || !ready) return undefined;
    setPose("waving");
    const greetingTimer = window.setTimeout(() => setPose("seated"), 900);
    const sleepTimer = window.setTimeout(() => setPose("sleeping"), 10 * 60 * 1000);
    return () => {
      window.clearTimeout(greetingTimer);
      window.clearTimeout(sleepTimer);
    };
  }, [visible, ready]);

  useEffect(() => {
    setBlinking(false);
    if (!playing || pose !== "seated") return undefined;
    let timer;
    const nextBlink = () => {
      timer = window.setTimeout(() => {
        setBlinking(true);
        timer = window.setTimeout(() => {
          setBlinking(false);
          nextBlink();
        }, 160);
      }, 3000 + Math.random() * 2500);
    };
    nextBlink();
    return () => window.clearTimeout(timer);
  }, [playing, pose]);

  return (
    <div ref={root} aria-hidden="true" className="rem-avatar" data-motion={motion} data-animated={Boolean(playing)} data-pose={pose === "sleeping" ? pose : playing ? pose : "seated"} data-blinking={Boolean(playing && blinking)}>
      {/* Key out the sprites' near-black backdrop before compositing each pose.
          Character outlines are brighter than this narrow alpha ramp. Keeping
          the original sprites preserves registration of the eyelid overlay. */}
      <svg width="0" height="0" className="rem-avatar-filter" focusable="false">
        <defs>
          <filter id={backgroundFilterId} colorInterpolationFilters="sRGB" x="0" y="0" width="100%" height="100%">
            <feColorMatrix in="SourceGraphic" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  10.625 10.625 10.625 0 -3.75" result="foreground" />
            <feComposite in="SourceGraphic" in2="foreground" operator="in" />
          </filter>
        </defs>
      </svg>
      <img alt="" style={spriteStyle} className="rem-avatar-seated" draggable={false} height={112} width={112} src={seated} srcSet={`${seated} 2x, ${seated3x} 3x`} onLoad={() => setLoaded((value) => ({ ...value, seated: true }))} />
      {/* Only the eyelid changes during a blink, keeping the body pixel-still. */}
      <img alt="" style={spriteStyle} className="rem-avatar-eyelid" draggable={false} height={112} width={112} src={closed} srcSet={`${closed} 2x, ${closed3x} 3x`} onLoad={() => setLoaded((value) => ({ ...value, closed: true }))} />
      <img alt="" style={spriteStyle} className="rem-avatar-wave" draggable={false} height={112} width={112} src={waving} srcSet={`${waving} 2x, ${waving3x} 3x`} onLoad={() => setLoaded((value) => ({ ...value, waving: true }))} />
      <img alt="" style={spriteStyle} className="rem-avatar-sleep" draggable={false} height={112} width={112} src={sleeping} srcSet={`${sleeping} 2x, ${sleeping3x} 3x`} />
    </div>
  );
}
