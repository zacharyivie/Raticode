// How long ago something happened, how long a run took and what date and time something
// happened at, worded the same everywhere in the desktop app.
import { useEffect, useState } from "react";

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

// Seconds from value to now, never negative, or NaN when value isn't a time.
// value is a Date, milliseconds since 1970 or a date string; now is a Date or milliseconds.
function secondsSince(value, now) {
  if (value == null || value === "") return NaN;
  const time = value instanceof Date ? value.getTime() : typeof value === "number" ? value : Date.parse(value);
  return Math.max(0, (Number(now) - time) / 1000);
}

/** "just now", "5 min ago", "3 h ago", "yesterday", "4 days ago", then "Nov 3" ("Nov 3, 2025" in another year). */
export function timeAgo(value, now = Date.now()) {
  const seconds = secondsSince(value, now);
  if (!Number.isFinite(seconds)) return "";
  if (seconds < MINUTE) return "just now";
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)} min ago`;
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)} h ago`;
  const days = Math.floor(seconds / DAY);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return shortDate(Number(now) - seconds * 1000, { now: new Date(Number(now)) });
}

// --- Dates and times ---------------------------------------------------------------------
// Each format is built once per time zone and kept, since building an Intl.DateTimeFormat
// costs far more than using one. timeZone is an IANA name such as "America/Denver" for
// schedule views; leave it out for this computer's zone. An unknown zone falls back to it.

const formatters = new Map();

function formatter(options, timeZone) {
  const key = `${timeZone || ""}|${Object.entries(options).join(";")}`;
  let format = formatters.get(key);
  if (!format) {
    try {
      format = new Intl.DateTimeFormat(undefined, timeZone ? { ...options, timeZone } : options);
    } catch {
      format = formatter(options);
    }
    formatters.set(key, format);
  }
  return format;
}

// Some engines put a narrow no-break space before AM and PM; a plain one reads and copies the same.
const write = (date, options, timeZone) => formatter(options, timeZone).format(date).replace(/[\u202f\u00a0]/g, " ");

// A Date for a Date, milliseconds or a date string, or null when it isn't a time. A bare
// "2026-11-03" means that day where you are, so it is read as local noon rather than UTC midnight.
function toDate(value) {
  if (value == null || value === "") return null;
  const date = value instanceof Date ? value
    : typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00`)
      : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

const YEAR = { year: "numeric" };
const sameYear = (date, now, timeZone) => write(date, YEAR, timeZone) === write(toDate(now) || new Date(), YEAR, timeZone);

function dayOptions(date, { now = new Date(), timeZone, weekday = false } = {}) {
  return {
    ...(weekday ? { weekday: "short" } : {}),
    month: "short",
    day: "numeric",
    ...(sameYear(date, now, timeZone) ? {} : YEAR),
  };
}

const CLOCK = { hour: "numeric", minute: "2-digit" };

/** "Nov 3", or "Nov 3, 2025" in another year; weekday: true gives "Mon, Nov 3". "" for no date. */
export function shortDate(value, options = {}) {
  const date = toDate(value);
  return date ? write(date, dayOptions(date, options), options.timeZone) : "";
}

/** "Nov 3, 2:05 PM", or "Nov 3, 2025, 2:05 PM" in another year. Takes shortDate's options and zoneName. */
export function dateTime(value, options = {}) {
  const date = toDate(value);
  if (!date) return "";
  return write(date, { ...dayOptions(date, options), ...CLOCK, ...(options.zoneName ? { timeZoneName: "short" } : {}) }, options.timeZone);
}

/** "2:05 PM"; zoneName: true gives "2:05 PM MST" for a time in another zone. */
export function clockTime(value, { timeZone, zoneName = false } = {}) {
  const date = toDate(value);
  return date ? write(date, zoneName ? { ...CLOCK, timeZoneName: "short" } : CLOCK, timeZone) : "";
}

/** "Monday, November 3, 2026", for headings and hover text. */
export function longDate(value, { timeZone } = {}) {
  const date = toDate(value);
  return date ? write(date, { weekday: "long", month: "long", day: "numeric", year: "numeric" }, timeZone) : "";
}

/** "Mon", for week strips and schedules. */
export function weekdayName(value, { timeZone } = {}) {
  const date = toDate(value);
  return date ? write(date, { weekday: "short" }, timeZone) : "";
}

/** "Nov", or "Nov 2025" in another year; long: true gives "November 2026". */
export function monthName(value, { now = new Date(), timeZone, long = false } = {}) {
  const date = toDate(value);
  if (!date) return "";
  if (long) return write(date, { month: "long", year: "numeric" }, timeZone);
  return write(date, { month: "short", ...(sameYear(date, now, timeZone) ? {} : YEAR) }, timeZone);
}

/** This computer's time zone, such as "America/Denver", or "UTC" when the system doesn't say. */
export function localTimeZone() {
  try {
    return new Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** The short age for badges and tight rows: "now", "5 min", "3 h", "4 d". */
export function compactAge(value, now = Date.now()) {
  const seconds = secondsSince(value, now);
  if (!Number.isFinite(seconds)) return "";
  if (seconds < MINUTE) return "now";
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)} min`;
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)} h`;
  return `${Math.floor(seconds / DAY)} d`;
}

/** How long something ran: "45s", "5m 3s", "2h 4m". */
export function runLength(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  if (value < MINUTE) return `${value}s`;
  if (value < HOUR) return `${Math.floor(value / MINUTE)}m ${value % MINUTE}s`;
  return `${Math.floor(value / HOUR)}h ${Math.floor((value % HOUR) / MINUTE)}m`;
}

// --- Duration fields ---------------------------------------------------------------------

/** The units a duration field offers, largest last, as [name, seconds]. */
export const DURATION_UNITS = [["minutes", MINUTE], ["hours", HOUR], ["days", DAY]];

/**
 * How a duration field shows saved seconds: in the largest unit that divides them evenly,
 * otherwise in minutes (a fraction for leftover seconds). Blank stays blank.
 */
export function durationParts(seconds) {
  if (seconds == null || String(seconds).trim() === "") return { amount: "", unit: "minutes" };
  const value = Number(seconds);
  if (!Number.isFinite(value)) return { amount: String(seconds), unit: "minutes" };
  const [unit, size] = [...DURATION_UNITS].reverse().find(([, size]) => value !== 0 && value % size === 0) || DURATION_UNITS[0];
  return { amount: String(value / size), unit };
}

/** The seconds a duration field saves, as a string: "" when blank, "NaN" when it isn't a number. */
export function durationSeconds(amount, unit) {
  if (String(amount ?? "").trim() === "") return "";
  const size = DURATION_UNITS.find(([name]) => name === unit)?.[1] ?? MINUTE;
  return String(Math.round(Number(amount) * size));
}

// --- Date and time fields ----------------------------------------------------------------

const pad = value => String(value).padStart(2, "0");

/** A date and time field's value ("2026-11-18T10:00") for an ISO time, in this computer's zone. */
export function toLocalInput(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/**
 * The ISO time for a date and time field's value, with this computer's offset at that
 * moment ("2026-11-18T10:00:00-07:00"), or null when it is empty or invalid.
 */
export function localInputToOffsetIso(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const offset = -date.getTimezoneOffset();
  const zone = `${offset < 0 ? "-" : "+"}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`;
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${zone}`;
}

/** The current time as a Date, refreshed every intervalMs so relative times keep counting. */
export function useMinuteClock(intervalMs = 60_000) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}
