import { useEffect, useRef } from "react";
import { Mic } from "lucide-react";

import { DotMeter, DotMood, drawVoiceDots } from "../lib/voiceDots.js";

function prefersReducedMotion() {
  // The app writes Settings > Appearance > Reduce motion here.
  const setting = document.documentElement?.dataset?.reducedMotion;
  if (setting) return setting === "true";
  return Boolean(globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
}

/**
 * The dotted line from Raticode Mobile's voice mode. level() returns the microphone's
 * loudness from 0 to 1. With onToggle it is the listening control: tapping it pauses or
 * resumes, and words already heard stay either way. Without it, it is decoration.
 */
export default function VoiceDots({ mood, level = () => 0, label = "", title = label, keyshortcuts, onToggle, height = 56 }) {
  const canvasRef = useRef(null);
  const moodRef = useRef(mood);
  const levelRef = useRef(level);
  moodRef.current = mood;
  levelRef.current = level;

  useEffect(() => {
    if (!canvasRef.current?.getContext?.("2d") || typeof globalThis.requestAnimationFrame !== "function") return undefined;
    // Browsers refuse a detached requestAnimationFrame ("Illegal invocation").
    const frame = (callback) => globalThis.requestAnimationFrame(callback);
    const meter = new DotMeter();
    const calm = prefersReducedMotion();
    let last = -1;
    let handle = 0;
    let drawing = false;
    let stopped = false;
    // Never re-enters, even where requestAnimationFrame calls back at once.
    const tick = (time) => {
      if (drawing || stopped) return;
      drawing = true;
      try {
        draw(Number.isFinite(time) ? time : globalThis.performance?.now?.() ?? Date.now());
        handle = frame(tick);
      } finally {
        drawing = false;
      }
    };
    const draw = (now) => {
      const dt = last < 0 ? 16 : Math.max(0, Math.min(100, now - last));
      last = now;
      // The canvas is looked up each frame: it is replaced when the line becomes a button.
      const canvas = canvasRef.current;
      const context = canvas?.getContext?.("2d");
      if (!context) return;
      const width = canvas.clientWidth || 320;
      const tall = canvas.clientHeight || height;
      const scale = globalThis.devicePixelRatio || 1;
      if (canvas.width !== Math.round(width * scale) || canvas.height !== Math.round(tall * scale)) {
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(tall * scale);
      }
      context.setTransform(scale, 0, 0, scale, 0, 0);
      const current = moodRef.current;
      meter.step(dt, current === DotMood.LISTENING ? levelRef.current() : 0, now, calm);
      drawVoiceDots(context, { width, height: tall, meter, mood: current, timeMs: now, calm });
    };
    handle = frame(tick);
    return () => {
      stopped = true;
      globalThis.cancelAnimationFrame?.(handle);
    };
  }, [height]);

  const listening = mood === DotMood.LISTENING;
  const art = (
    <>
      <canvas ref={canvasRef} aria-hidden="true" className="block w-full" style={{ height }} />
      {mood === DotMood.PAUSED ? (
        <Mic aria-hidden="true" className="pointer-events-none absolute bottom-[7px] left-1/2 -translate-x-1/2 text-indigo-500 dark:text-[#E6E2FF]" size={20} />
      ) : null}
    </>
  );
  if (!onToggle) return <div aria-hidden="true" className="relative w-full">{art}</div>;
  return (
    <button
      aria-keyshortcuts={keyshortcuts}
      aria-label={label}
      aria-pressed={listening}
      className="relative block w-full rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
      data-voice-dots={mood}
      title={title}
      type="button"
      onClick={onToggle}
    >
      {art}
    </button>
  );
}
