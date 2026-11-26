import { apiJson } from "./api.js";

// The Posts plugin's backend calls and the pure helpers its dashboard uses. Approving
// and publishing always send the content hash of the version on screen, so the backend
// refuses if the post changed after the user read it.

const request = (path, options = {}) => apiJson(path, { ...options, failure: status => `Posts request failed (${status}).` });
const postPath = id => `/social/posts/${encodeURIComponent(id)}`;
const variantPath = (id, variantId) => `${postPath(id)}/variants/${encodeURIComponent(variantId)}`;

export const loadSocial = () => request("/social");
export const createPost = body => request("/social/posts", { method: "POST", body: body ?? {} });
export const editPost = (id, body) => request(`${postPath(id)}/edit`, { method: "POST", body: body ?? {} });
export const approvePost = (post, { publishNow = false } = {}) => request(`${postPath(post.id)}/approve`, { method: "POST", body: { content_hash: post.content_hash, publish_now: publishNow } });
export const declinePost = (id, note = "") => request(`${postPath(id)}/decline`, { method: "POST", body: { note } });
export const requestChanges = (id, note = "") => request(`${postPath(id)}/request-changes`, { method: "POST", body: { note } });
export const cancelPost = id => request(`${postPath(id)}/cancel`, { method: "POST", body: {} });
export const publishVariant = (post, variantId) => request(`${variantPath(post.id, variantId)}/publish`, { method: "POST", body: { content_hash: post.content_hash } });
export const markPosted = (post, variantId, url) => request(`${variantPath(post.id, variantId)}/mark-posted`, { method: "POST", body: { url } });
export const setPaused = paused => request("/social/pause", { method: "POST", body: { paused } });
export const saveSocialSettings = body => request("/social/settings", { method: "PUT", body: body ?? {} });
export const connectAccount = body => request("/social/accounts", { method: "POST", body: body ?? {} });
export const updateAccount = (id, body) => request(`/social/accounts/${encodeURIComponent(id)}`, { method: "PATCH", body: body ?? {} });
export const disconnectAccount = id => request(`/social/accounts/${encodeURIComponent(id)}`, { method: "DELETE" });
// Browser sign-in through a loopback listener on this computer (gofer.social.signin).
const signInPath = id => `/social/accounts/sign-in/${encodeURIComponent(id)}`;
export const startSignIn = body => request("/social/accounts/sign-in", { method: "POST", body: body ?? {} });
export const signInStatus = id => request(signInPath(id));
export const reopenSignIn = id => request(`${signInPath(id)}/open`, { method: "POST", body: {} });
export const chooseSignInPage = (id, pageId) => request(`${signInPath(id)}/page`, { method: "POST", body: { page_id: pageId } });
export const cancelSignIn = id => request(signInPath(id), { method: "DELETE" });
export const checkAccount = id => request(`/social/accounts/${encodeURIComponent(id)}/check`, { method: "POST", body: {} });
export const saveTemplate = (body, id) => request(id ? `/social/templates/${encodeURIComponent(id)}` : "/social/templates", { method: id ? "PUT" : "POST", body: body ?? {} });
export const deleteTemplate = id => request(`/social/templates/${encodeURIComponent(id)}`, { method: "DELETE" });
// The backend's next good times for these accounts or manual platforms, in a time zone. It
// skips times close to other planned posts on the same accounts (gofer.social.timing).
export const suggestTimes = (targets, timeZone) => request(`/social/times?${new URLSearchParams({ target: targets.join(","), tz: timeZone || "" })}`);

export const LANES = Object.freeze([
  ["review", "Needs review"],
  ["scheduled", "Scheduled"],
  ["due", "Due now"],
  ["published", "Published"],
  ["problems", "Problems"],
]);

const REVIEW = new Set(["draft", "proposed", "changes_requested"]);
const CLOSED = new Set(["declined", "cancelled", "expired"]);
const PROBLEM_POSTS = new Set(["failed", "missed", "needs_attention", "partly_published"]);
const PROBLEM_VARIANTS = new Set(["failed", "missed", "needs_reconnect", "outcome_unknown", "held"]);
const DONE_VARIANTS = new Set(["published", "posted_by_you"]);

// Which lane a post belongs to, or "closed" for declined, cancelled and expired posts.
export function laneFor(post) {
  const state = post?.state || "proposed";
  const variants = post?.variants || [];
  if (REVIEW.has(state)) return "review";
  if (CLOSED.has(state)) return "closed";
  if (PROBLEM_POSTS.has(state) || variants.some(variant => PROBLEM_VARIANTS.has(variant.state))) return "problems";
  if (state === "due" || variants.some(variant => variant.state === "due")) return "due";
  if (state === "published" || (variants.length && variants.every(variant => DONE_VARIANTS.has(variant.state) || variant.state === "cancelled"))) return "published";
  return "scheduled";
}

export function groupPosts(posts = []) {
  const lanes = { review: [], scheduled: [], due: [], published: [], problems: [], closed: [] };
  for (const post of posts) lanes[laneFor(post)].push(post);
  const time = post => Date.parse(post.scheduled_at || post.updated_at || post.created_at || "") || 0;
  lanes.review.sort((a, b) => time(a) - time(b));
  lanes.scheduled.sort((a, b) => time(a) - time(b));
  lanes.due.sort((a, b) => time(a) - time(b));
  lanes.problems.sort((a, b) => time(b) - time(a));
  lanes.published.sort((a, b) => time(b) - time(a));
  lanes.closed.sort((a, b) => time(b) - time(a));
  return lanes;
}

// "Tue 18 Nov, 10:00" in the user's locale and time zone; "As soon as approved" when unset.
export function formatWhen(iso, { locale, timeZone } = {}) {
  if (!iso) return "As soon as approved";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Unknown time";
  return new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone }).format(date);
}

// A datetime-local input value ("2026-11-18T10:00") for an ISO time, in local time.
export function toLocalInput(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = value => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// The ISO UTC time for a datetime-local value, or null when it is empty or invalid.
export function fromLocalInput(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

// X counts every link as 23 characters; every other platform counts characters.
const URL_PATTERN = /https?:\/\/\S+/gi;
export function countChars(text = "", platform = "") {
  if (platform === "x") {
    const links = text.match(URL_PATTERN) || [];
    return Array.from(text.replace(URL_PATTERN, "")).length + links.length * 23;
  }
  return Array.from(text).length;
}

export function charCounter(variant, platform) {
  const limit = variant?.limit ?? platform?.text_limit ?? null;
  const text = variant?.link && !(variant.text || "").includes(variant.link) && variant.platform !== "linkedin" && variant.platform !== "facebook"
    ? `${variant.text || ""}\n${variant.link}` : variant?.text || "";
  const count = countChars(text, variant?.platform);
  return { count, limit, over: limit != null && count > limit };
}

// Each URL in the text and link, split so the domain can be highlighted.
export function linkParts(variant) {
  const urls = new Set();
  for (const match of (variant?.text || "").match(URL_PATTERN) || []) urls.add(match.replace(/[.,;:!?)\]]+$/, ""));
  if (variant?.link) urls.add(variant.link);
  return [...urls].map(url => {
    try {
      const parsed = new URL(url);
      const start = url.indexOf(parsed.host);
      return { url, before: url.slice(0, start), host: parsed.host, after: url.slice(start + parsed.host.length) };
    } catch {
      return { url, before: url, host: "", after: "" };
    }
  });
}

// Who proposed a post, in words.
export function sourceLabel(createdBy = "") {
  if (!createdBy || createdBy === "user") return "You";
  if (createdBy === "cli") return "Command line";
  if (createdBy === "rem") return "Rem";
  if (createdBy.startsWith("task:")) return "A Rem task";
  if (createdBy.startsWith("workflow:")) return `Workflow ${createdBy.slice("workflow:".length).split("@")[0]}`;
  return createdBy;
}

// A small line diff: each line of the new text marked same, added or removed.
export function lineDiff(before = "", after = "") {
  const a = before.split("\n");
  const b = after.split("\n");
  const table = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const out = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { out.push({ kind: "same", text: a[i] }); i += 1; j += 1; }
    else if (table[i + 1][j] >= table[i][j + 1]) { out.push({ kind: "removed", text: a[i] }); i += 1; }
    else { out.push({ kind: "added", text: b[j] }); j += 1; }
  }
  while (i < a.length) out.push({ kind: "removed", text: a[i++] });
  while (j < b.length) out.push({ kind: "added", text: b[j++] });
  return out;
}

// What goes out on each of the next seven days, for the week strip.
export function weekStrip(posts = [], now = new Date()) {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const days = Array.from({ length: 7 }, (_, index) => {
    const day = new Date(start);
    day.setDate(start.getDate() + index);
    return { date: day, items: [] };
  });
  for (const post of posts) {
    if (!post.scheduled_at || laneFor(post) === "closed" || laneFor(post) === "published") continue;
    const when = new Date(post.scheduled_at);
    const index = Math.floor((new Date(when.getFullYear(), when.getMonth(), when.getDate()) - start) / 86400000);
    if (index >= 0 && index < 7) days[index].items.push(post);
  }
  return days;
}

// "LinkedIn: reconnect in 9 days" and similar, or "" when nothing needs saying.
export function accountNotice(account) {
  if (!account) return "";
  const label = account.platform_label || account.platform;
  if (account.status === "needs_reconnect") return `${label}: reconnect now`;
  if (account.status === "disabled") return `${label}: turned off`;
  if (account.status === "reconnect_soon" || (account.expires_in_days != null && account.expires_in_days <= 14)) {
    const days = account.expires_in_days ?? 0;
    return `${label}: reconnect in ${days} ${days === 1 ? "day" : "days"}`;
  }
  return "";
}

// Which actions the dashboard offers for one variant in its current state.
export function variantActions(variant) {
  const state = variant?.state;
  if (variant?.delivery === "manual") return state === "posted_by_you" || state === "cancelled" || state === "pending" ? [] : ["copy", "open", "mark-posted"];
  if (state === "due") return ["publish"];
  if (state === "outcome_unknown") return ["it-posted", "send-again"];
  if (["missed", "failed", "held"].includes(state)) return ["publish-now"];
  return [];
}

// Every platform option a variant carries, in words, so the review card shows all of what
// will be sent. The video title is shown above the text, so it isn't repeated here. An
// option this list doesn't know is still shown under its own name rather than hidden.
const OPTION_LABELS = Object.freeze({
  article_title: "Link card title",
  visibility: "Who can see it",
  thread_id: "Discord thread",
});
const VISIBILITY_LABELS = Object.freeze({ PUBLIC: "Anyone", CONNECTIONS: "Connections only" });

export function optionLines(variant) {
  const options = variant?.options || {};
  return Object.keys(options).filter(key => key !== "title" && options[key] !== "" && options[key] != null).sort().map(key => {
    const value = String(options[key]);
    return { key, label: OPTION_LABELS[key] || key, value: key === "visibility" ? VISIBILITY_LABELS[value] || value : value };
  });
}

// --- Time zones -------------------------------------------------------------------------
// The schedule picker takes a wall-clock time in a chosen zone ("9:00 in Denver"). These turn
// that into UTC and back without a date library.

export function localTimeZone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; }
}

function zoneOffsetMs(ms, timeZone) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(new Date(ms)).map(part => [part.type, part.value]));
  const asUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour % 24, +parts.minute, +parts.second);
  return asUtc - Math.floor(ms / 1000) * 1000;
}

// "2026-11-18T10:00" for an ISO time as the clock reads in timeZone.
export function zonedInput(iso, timeZone = localTimeZone()) {
  if (!iso) return "";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  try {
    const shifted = new Date(ms + zoneOffsetMs(ms, timeZone));
    return shifted.toISOString().slice(0, 16);
  } catch {
    return toLocalInput(iso);
  }
}

// The ISO UTC time for a datetime-local value read in timeZone, or null when empty or invalid.
export function zonedToIso(value, timeZone = localTimeZone()) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value || "");
  if (!match) return null;
  const wall = Date.UTC(+match[1], +match[2] - 1, +match[3], +match[4], +match[5]);
  try {
    let guess = wall - zoneOffsetMs(wall, timeZone);
    guess = wall - zoneOffsetMs(guess, timeZone);
    return new Date(guess).toISOString();
  } catch {
    return fromLocalInput(value);
  }
}

// Every zone the runtime knows, with the chosen and local ones always present.
export function timeZoneChoices(...always) {
  let zones;
  try { zones = Intl.supportedValuesOf?.("timeZone") || []; } catch { zones = []; }
  return [...new Set([...always.filter(Boolean), localTimeZone(), "UTC", ...zones])].sort((a, b) => a.localeCompare(b));
}

// --- Late window ------------------------------------------------------------------------

export const LATE_WINDOW_CHOICES = Object.freeze([15, 30, 60, 120, 360, 1440]);
export const MAX_LATE_WINDOW = 7 * 24 * 60;

// "15 minutes", "1 hour", "2 hours 30 minutes", "1 day".
export function durationLabel(minutes) {
  const total = Math.max(0, Math.round(Number(minutes) || 0));
  const days = Math.floor(total / 1440);
  const hours = Math.floor((total % 1440) / 60);
  const mins = total % 60;
  const part = (n, word) => (n ? `${n} ${word}${n === 1 ? "" : "s"}` : "");
  return [part(days, "day"), part(hours, "hour"), part(mins, "minute")].filter(Boolean).join(" ") || "0 minutes";
}

// --- Trusted link sites -----------------------------------------------------------------

// "https://www.Example.com/path" -> "www.example.com"; "" when it isn't a domain.
export function normalizeDomain(input = "") {
  let value = String(input).trim().toLowerCase();
  value = value.replace(/^[a-z][a-z0-9+.-]*:\/\//, "").replace(/[/?#].*$/, "").replace(/:\d+$/, "").replace(/^\.+|\.+$/g, "");
  if (!/^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(value) && !/^xn--/.test(value)) return "";
  return value;
}

// --- Best-time suggestions ------------------------------------------------------------
// The times come from the backend (suggestTimes), so the editor, Rem and the CLI agree.

// The target keys suggestTimes takes for an editor's variants: account ids, or the platform
// for a site you paste into yourself.
export function timeTargets(variants = []) {
  return [...new Set(variants.map(item => item.account_id || item.platform).filter(Boolean))];
}

// "Tue 10:00 AM" for a suggested slot, in the post's time zone.
export function slotLabel(iso, timeZone) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "numeric", minute: "2-digit", timeZone: timeZone || undefined }).format(date);
}

// Quick picks for the schedule: in an hour, tomorrow morning, next Monday morning.
export function quickTimes(now = new Date(), timeZone = localTimeZone()) {
  const today = zonedInput(now.toISOString(), timeZone).slice(0, 10);
  const [year, month, day] = today.split("-").map(Number);
  const at = (offset, hour) => zonedToIso(`${new Date(Date.UTC(year, month - 1, day + offset)).toISOString().slice(0, 10)}T${String(hour).padStart(2, "0")}:00`, timeZone);
  const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay();
  const toMonday = ((8 - weekday) % 7) || 7;
  const inHour = new Date(Math.ceil((now.getTime() + 3600_000) / 900_000) * 900_000).toISOString();
  return [
    { label: "In an hour", at: inHour },
    { label: "Tomorrow, 9 AM", at: at(1, 9) },
    { label: "Monday, 9 AM", at: at(toMonday, 9) },
  ];
}

// --- Power-user helpers -----------------------------------------------------------------

// The editor's starting values for a copy of a post: same accounts, text, links, options and
// stored media, no time, and a fresh review. The copy is a new proposal by the user.
export function duplicateDraft(post) {
  return {
    title: post?.title ? `Copy of ${post.title}`.slice(0, 200) : "",
    scheduled_at: null,
    timezone: post?.timezone || localTimeZone(),
    late_window_minutes: post?.late_window_minutes,
    rationale: post?.rationale || "",
    variants: (post?.variants || []).map(item => ({
      account_id: item.account_id || null, platform: item.platform, text: item.text || "", link: item.link || "",
      options: { ...(item.options || {}) }, media: (item.media || []).filter(media => media.sha256).map(media => ({ sha256: media.sha256, name: media.name })),
    })),
  };
}

// Whether a post in Needs review can be approved together with others. Posts with warnings
// or text over a platform's limit need their own look and their own click.
export function bulkApprovable(post, platforms = []) {
  if (!post || !REVIEW.has(post.state) || !post.variants?.length) return false;
  if (post.flags?.length) return false;
  return !post.variants.some(variant => charCounter(variant, platforms.find(item => item.id === variant.platform)).over);
}

// Shown on cards and in lists: what a post's accounts are, in a few words.
export function audienceLabel(post) {
  const names = (post?.variants || []).map(item => item.platform_label || item.platform);
  return [...new Set(names)].join(", ");
}

// --- "See more" folds -------------------------------------------------------------------
// Where a feed cuts a post with "see more": the platform's fold_chars or fold_lines from the
// backend, whichever comes first (gofer.social.models.fold_at). Returns the text readers see
// before tapping and the rest; rest is "" when the whole text shows.
export function foldText(text = "", platform = {}) {
  const value = String(text || "");
  const cuts = [];
  if (platform?.fold_chars && value.length > platform.fold_chars) cuts.push(platform.fold_chars);
  if (platform?.fold_lines) {
    const lines = value.replace(/\n+$/, "").split("\n");
    if (lines.length > platform.fold_lines) cuts.push(lines.slice(0, platform.fold_lines).join("\n").length);
  }
  if (!cuts.length) return { shown: value, rest: "" };
  const at = Math.min(...cuts);
  return { shown: value.slice(0, at), rest: value.slice(at) };
}

// --- Templates --------------------------------------------------------------------------
// Blanks are written in braces: {version}, {what changed}. The same rule as the backend
// (gofer.social.templates.BLANK), so "{}" and code like "{ x }" are left alone.
const BLANK = /\{([A-Za-z][\w -]{0,39})\}/g;

export function templateBlanks(texts = []) {
  const found = [];
  for (const text of texts) {
    for (const match of String(text || "").matchAll(BLANK)) if (!found.includes(match[1])) found.push(match[1]);
  }
  return found;
}

export function fillBlanks(text = "", values = {}) {
  return String(text || "").replace(BLANK, (whole, name) => (String(values[name] ?? "").trim() ? String(values[name]).trim() : whole));
}

// The editor's starting values from a template with its blanks filled in. Each platform goes
// to the user's matching account, or to the site itself when you paste into it; platforms
// with no account are listed in skipped so the editor can say so.
export function templateDraft(template, values = {}, accounts = [], platforms = []) {
  const variants = [];
  const skipped = [];
  for (const item of template?.variants || []) {
    const platform = platforms.find(entry => entry.id === item.platform);
    const account = item.account_id ? accounts.find(entry => entry.id === item.account_id)
      : accounts.find(entry => entry.platform === item.platform && entry.enabled !== false) || accounts.find(entry => entry.platform === item.platform);
    if (!account && platform?.api !== false) { skipped.push(platform?.label || item.platform); continue; }
    if (variants.some(entry => (entry.account_id || entry.platform) === (account?.id || item.platform))) continue;
    variants.push({ account_id: account?.id || null, platform: item.platform, text: fillBlanks(item.text, values), link: fillBlanks(item.link || "", values), options: {}, media: [] });
  }
  return {
    draft: { title: fillBlanks(template?.title || "", values), scheduled_at: null, timezone: localTimeZone(), rationale: "", variants },
    skipped,
  };
}

// A template body from the editor's form: the title and each platform's text and link, as typed.
export function templateBody(name, form) {
  return {
    name: String(name || "").trim(),
    title: form?.title || "",
    variants: (form?.variants || []).map(item => ({ platform: item.platform, text: item.text || "", ...(item.link ? { link: item.link } : {}) })),
  };
}
