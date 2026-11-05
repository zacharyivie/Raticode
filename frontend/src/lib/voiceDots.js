// The dotted line behind dictation, the same one Raticode Mobile's voice mode shows.
// A row of dots rises into dot bars while you talk and falls back gently; each column's
// top dot hangs for a moment before dropping, like the peak lights on an old hardware
// equalizer. Paused, it dims and makes room for a microphone; while words are tidied, a
// bright dot sweeps back and forth. The motion is kept apart from drawing so it can be
// tested on its own.

export const DOT_ROWS = 6;
const RISE_MS = 45;
const FALL_MS = 170;
const HOLD_MS = 420;
const PEAK_FALL_MS_PER_DOT = 110;

export const DotMood = Object.freeze({ LISTENING: "listening", PAUSED: "paused", THINKING: "thinking" });

// Synthwave across the line: cyan at the left, Raticode's indigo-lilac in the middle, magenta at the right.
const CYAN = [0x3e, 0xe0, 0xff];
const LILAC = [0xa5, 0xa6, 0xff];
const MAGENTA = [0xff, 0x5a, 0xd6];
const RESTING = [0x5e, 0x58, 0x78];

/** A fixed scatter in 0..1 for column n, the same on every run. */
export function noise(n) {
  const s = Math.sin(n * 12.9898 + 78.233) * 43758.547;
  return s - Math.floor(s);
}

function mix(a, b, t) {
  return a.map((value, index) => Math.round(value + (b[index] - value) * t));
}

export function dotColor(x) {
  return x < 0.5 ? mix(CYAN, LILAC, x * 2) : mix(LILAC, MAGENTA, (x - 0.5) * 2);
}

function rgba([r, g, b], alpha) {
  return `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, alpha)).toFixed(3)})`;
}

/** Microphone loudness from 0 to 1, from the RMS of a block of samples. */
export function levelFromSamples(samples) {
  if (!samples?.length) return 0;
  let sum = 0;
  for (const sample of samples) sum += sample * sample;
  const rms = Math.sqrt(sum / samples.length);
  if (rms <= 0) return 0;
  // About -50 dBFS is silence and -10 dBFS is loud speech.
  const db = 20 * Math.log10(rms);
  return Math.max(0, Math.min(1, (db + 50) / 40));
}

export class DotMeter {
  constructor(columns = 0, rows = DOT_ROWS) {
    this.rows = rows;
    this.resize(columns, true);
  }

  get columns() {
    return this.heights.length;
  }

  resize(columns, force = false) {
    if (!force && columns === this.heights.length) return;
    this.heights = new Float32Array(columns);
    this.peaks = new Float32Array(columns);
    this.held = new Float32Array(columns);
  }

  /** Speech is fullest in the middle: a hump across the line, roughened per column so it never looks drawn. */
  shape(i) {
    const n = this.columns;
    const x = n <= 1 ? 0.5 : i / (n - 1);
    return (0.3 + 0.7 * Math.sin(Math.PI * x) ** 1.4) * (0.72 + 0.28 * noise(i));
  }

  /** Each column bobs on its own beat, between about half and full height. */
  sway(i, timeMs) {
    const beat = 1.1 + 1.6 * noise(i * 7 + 3);
    return 0.5 + 0.5 * (0.5 + 0.5 * Math.sin(2 * Math.PI * beat * timeMs / 1000 + 6.3 * noise(i * 13 + 5)));
  }

  /** Moves every column dtMs forward toward level (0 to 1). calm drops the sway when motion is reduced. */
  step(dtMs, level, timeMs, calm = false) {
    const loud = Math.max(0, Math.min(1, level));
    for (let i = 0; i < this.columns; i += 1) {
      const target = loud * this.rows * this.shape(i) * (calm ? 0.8 : this.sway(i, timeMs));
      const h = this.heights[i];
      const rate = Math.min(1, dtMs / (target > h ? RISE_MS : FALL_MS));
      this.heights[i] = Math.max(0, Math.min(this.rows, h + (target - h) * rate));
      if (this.heights[i] >= this.peaks[i]) {
        this.peaks[i] = this.heights[i];
        this.held[i] = 0;
      } else {
        this.held[i] += dtMs;
        if (this.held[i] > HOLD_MS) this.peaks[i] = Math.max(this.heights[i], this.peaks[i] - dtMs / PEAK_FALL_MS_PER_DOT);
      }
    }
  }

  average() {
    if (!this.columns) return 0;
    let sum = 0;
    for (const h of this.heights) sum += h;
    return sum / this.columns;
  }
}

/**
 * Draws one frame. width and height are CSS pixels; the context is already scaled for the
 * screen. Returns nothing; the caller keeps the meter stepping between frames.
 */
export function drawVoiceDots(context, { width, height, meter, mood, timeMs, calm = false }) {
  const gap = 7;
  const dot = 1.6;
  meter.resize(Math.max(1, Math.floor(width / gap)));
  const n = meter.columns;
  const left = (width - (n - 1) * gap) / 2;
  const base = height - 2.5 * gap;
  const micGap = mood === DotMood.PAUSED ? 22 : 0;
  context.clearRect(0, 0, width, height);

  // A low glow on the horizon that swells with the voice: a flat oval inside the line's box.
  if (mood !== DotMood.PAUSED && typeof context.createRadialGradient === "function") {
    const swell = meter.average() / meter.rows;
    const reach = width * 0.48;
    context.save();
    context.translate(width / 2, base);
    context.scale(1, Math.max(0.01, ((height - base) * 0.95) / reach));
    const glow = context.createRadialGradient(0, 0, 0, 0, 0, reach);
    glow.addColorStop(0, rgba(LILAC, 0.12 + 0.25 * swell));
    glow.addColorStop(1, rgba(LILAC, 0));
    context.fillStyle = glow;
    context.beginPath();
    context.arc(0, 0, reach, 0, Math.PI * 2);
    context.fill();
    context.restore();
  }

  const circle = (color, radius, x, y) => {
    context.fillStyle = color;
    context.beginPath();
    context.arc(x, y, radius, 0, Math.PI * 2);
    context.fill();
  };
  const t = timeMs;
  const phase = (t / 1500) % 2;
  const sweep = calm ? 0.5 : phase > 1 ? 2 - phase : phase;
  for (let i = 0; i < n; i += 1) {
    const x = left + i * gap;
    if (micGap > 0 && Math.abs(x - width / 2) < micGap) continue;
    const across = n <= 1 ? 0.5 : i / (n - 1);
    const tint = mood === DotMood.PAUSED ? RESTING : dotColor(across);
    // The resting line is dim, with a slow glint travelling along it so it never looks dead.
    let glow;
    if (mood === DotMood.PAUSED) glow = 0.55;
    else if (mood === DotMood.THINKING) glow = 0.3 + 0.7 * Math.exp(-((((across - sweep) * n) / 3) ** 2));
    else glow = 0.42 + (calm ? 0 : 0.4 * Math.max(0, Math.sin(across * 9 - t / 260)) ** 6);
    const h = meter.heights[i];
    if (h > 0.05) glow = 1;
    circle(rgba(tint, glow), dot, x, base);
    for (let r = 1; r <= Math.ceil(h); r += 1) {
      const alpha = Math.max(0, Math.min(1, h - (r - 1))) * (0.65 + (0.35 * r) / meter.rows);
      circle(rgba(tint, alpha), dot, x, base - r * gap);
    }
    // Reflection under the line, like a neon sign on a wet street.
    for (let r = 1; r <= 2; r += 1) if (h >= r) circle(rgba(tint, 0.22 / r), dot * 0.9, x, base + r * gap);
    const peak = meter.peaks[i];
    if (peak >= 1 && peak - h > 0.6) circle(rgba(mix(tint, [255, 255, 255], 0.55), 1), dot * 1.1, x, base - Math.ceil(peak - 0.5) * gap);
  }
}
