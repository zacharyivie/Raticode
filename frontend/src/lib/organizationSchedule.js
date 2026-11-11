// The organization's work schedule: named focus blocks that repeat daily, weekly, monthly or
// quarterly. The desktop backend (organization_schedule.py) decides which block is on; these
// helpers describe blocks in the same words, check a draft before saving and draw a day.

export const CADENCES = [["daily", "Every day"], ["weekly", "Weekly"], ["monthly", "Monthly"], ["quarterly", "Quarterly"]];
export const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DAY_PLURALS = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"];
export const LONG_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const ORDINALS = [[1, "1st"], [2, "2nd"], [3, "3rd"], [4, "4th"], [-1, "last"]];
const ORDINAL = Object.fromEntries(ORDINALS);
const RANK = { daily: 0, weekly: 1, monthly: 2, quarterly: 3 };
const DAY = 24 * 60;
const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

const minutes = clock => Number(clock.slice(0, 2)) * 60 + Number(clock.slice(3, 5));
// Minutes on the clock; the same start and end is a whole day.
export const blockLength = block => ((minutes(block.end) - minutes(block.start)) % DAY + DAY) % DAY || DAY;

function timeRange(block) {
  if (block.start === block.end) return block.start === "00:00" ? "all day" : `${block.start} for 24 hours`;
  return `${block.start}–${block.end}`;
}

// When a block repeats, in the backend's words: "Every day 08:00–17:00", "Fridays 13:00–17:00".
export function describeBlock(block) {
  if (!CLOCK.test(block.start || "") || !CLOCK.test(block.end || "")) return "Times not set";
  const times = timeRange(block);
  const cadence = block.cadence || "daily";
  if (cadence === "daily") return `Every day ${times}`;
  if (cadence === "weekly") {
    const days = [...(block.days || [])].sort();
    const named = days.length === 1 ? DAY_PLURALS[days[0]] : days.join() === "0,1,2,3,4" ? "Weekdays" : days.join() === "5,6" ? "Weekends" : days.map(d => DAY_NAMES[d]).join(", ");
    return `${named} ${times}`;
  }
  const which = block.monthDay != null ? (Number(block.monthDay) === 31 ? "last day" : `day ${block.monthDay}`) : `${ORDINAL[block.nthWeek]} ${LONG_DAYS[block.weekday]}`;
  if (cadence === "monthly") return `Monthly, ${which}, ${times}`;
  return `Quarterly, ${which} of the ${ORDINAL[block.quarterMonth || 1]} month, ${times}`;
}

// What is wrong with one block, in words, or "".
export function blockProblem(block) {
  const name = block.label?.trim() || "A block";
  if (!block.label?.trim()) return "Give each block a name.";
  if (!CLOCK.test(block.start || "") || !CLOCK.test(block.end || "")) return `${name}: set a start and end time.`;
  const cadence = block.cadence || "daily";
  if (cadence === "weekly" && !(block.days || []).length) return `${name}: choose the weekdays it runs on.`;
  if (cadence === "monthly" || cadence === "quarterly") {
    const byDay = block.monthDay != null, byWeekday = block.nthWeek != null && block.weekday != null;
    if (byDay === byWeekday) return `${name}: choose a day of the month or a weekday such as the first Monday.`;
    if (byDay && !(block.monthDay >= 1 && block.monthDay <= 31)) return `${name}: days of the month are 1 to 31.`;
  }
  if ((block.keywords || []).length > 12) return `${name}: use at most 12 keywords.`;
  return "";
}

// Week minutes a daily or weekly block covers, for the overlap check.
function weekSpans(block) {
  const days = block.cadence === "daily" ? [0, 1, 2, 3, 4, 5, 6] : block.days || [];
  return days.map(day => [day * DAY + minutes(block.start), day * DAY + minutes(block.start) + blockLength(block)]);
}

const overlaps = (a, b) => a.some(([s1, e1]) => b.some(([s2, e2]) => {
  const week = 7 * DAY;
  // Compare across the week's end too, for a Sunday night block.
  return [-week, 0, week].some(shift => s1 < e2 + shift && s2 + shift < e1);
}));

// The first problem with the whole schedule, or "". The backend checks monthly and quarterly
// overlaps as well, over years of calendars.
export function scheduleProblem(blocks) {
  for (const block of blocks) {
    const problem = blockProblem(block);
    if (problem) return problem;
  }
  if (blocks.length > 40) return "A schedule has at most 40 blocks.";
  for (let i = 0; i < blocks.length; i += 1) {
    for (let j = i + 1; j < blocks.length; j += 1) {
      const a = blocks[i], b = blocks[j];
      if (a.cadence !== b.cadence || !["daily", "weekly"].includes(a.cadence) || blockLength(a) !== blockLength(b)) continue;
      if (overlaps(weekSpans(a), weekSpans(b))) return `${a.label} and ${b.label} overlap and neither takes precedence. Change one's times, or its cadence.`;
    }
  }
  return "";
}

// Smaller wins: longer cadence, then the shorter block, then list order.
const precedence = (block, index) => [-RANK[block.cadence || "daily"], blockLength(block), index];
const before = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];

// A day of the daily and weekly blocks, as segments from 00:00 to 24:00 on a weekday (Monday 0).
// Monthly and quarterly blocks are occasional, so they're listed apart instead.
export function daySegments(blocks, weekday) {
  const winners = new Array(DAY).fill(-1);
  const keys = new Array(DAY).fill(null);
  blocks.forEach((block, index) => {
    if (!["daily", "weekly"].includes(block.cadence || "daily") || blockProblem(block)) return;
    const key = precedence(block, index);
    const runs = day => (block.cadence || "daily") === "daily" || (block.days || []).includes(day);
    const cover = (from, to) => { for (let m = Math.max(0, from); m < Math.min(DAY, to); m += 1) if (!keys[m] || before(key, keys[m]) < 0) { keys[m] = key; winners[m] = index; } };
    const start = minutes(block.start), length = blockLength(block);
    if (runs(weekday)) cover(start, start + length);
    if (runs((weekday + 6) % 7)) cover(start - DAY, start - DAY + length);
  });
  const segments = [];
  for (let m = 0; m < DAY; m += 1) {
    const last = segments.at(-1);
    if (last && last.index === winners[m]) last.end = m + 1;
    else segments.push({ start: m, end: m + 1, index: winners[m] });
  }
  return segments.map(s => ({ ...s, block: s.index >= 0 ? blocks[s.index] : null }));
}

// The weekday (Monday 0) and minute of the day right now in a time zone.
export function zoneClock(timezone, now = new Date()) {
  try {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: timezone, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(now).map(p => [p.type, p.value]));
    return { weekday: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(parts.weekday), minute: Number(parts.hour) * 60 + Number(parts.minute) };
  } catch {
    return { weekday: (now.getUTCDay() + 6) % 7, minute: now.getUTCHours() * 60 + now.getUTCMinutes() };
  }
}

export const clockText = minute => `${String(Math.floor(minute / 60) % 24).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;

// Keywords are typed as one line; commit them on blur, not on every keystroke.
export const keywordsText = words => (words || []).join(", ");
export function parseKeywords(text) {
  const seen = new Set();
  return text.split(",").map(word => word.trim()).filter(word => word && !seen.has(word.toLowerCase()) && seen.add(word.toLowerCase())).map(word => word.slice(0, 40));
}

// A portable ID from the name, unique within the schedule.
export function blockId(label, taken) {
  const base = (label || "block").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "block";
  let id = base, n = 2;
  while (taken.includes(id)) id = `${base}-${n++}`;
  return id;
}

// Switching how often a block repeats keeps what still applies and fills in the rest.
export function withCadence(block, cadence) {
  const next = { ...block, cadence };
  if (cadence === "weekly") { next.days = block.days?.length ? block.days : [4]; }
  else delete next.days;
  if (cadence === "monthly" || cadence === "quarterly") {
    if (block.monthDay == null && block.nthWeek == null) Object.assign(next, { nthWeek: 1, weekday: 0 });
    if (cadence === "quarterly") next.quarterMonth = block.quarterMonth || 1;
    else delete next.quarterMonth;
  } else { delete next.monthDay; delete next.nthWeek; delete next.weekday; delete next.quarterMonth; }
  return next;
}

// Only the fields the backend keeps, without empty ones.
export function cleanBlock(block) {
  const fields = ["id", "label", "focus", "cadence", "start", "end", "days", "monthDay", "nthWeek", "weekday", "quarterMonth", "keywords"];
  return Object.fromEntries(fields.filter(key => block[key] != null && !(key === "days" && !block[key].length)).map(key => [key, typeof block[key] === "string" ? block[key].trim() : block[key]]));
}
