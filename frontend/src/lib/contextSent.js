// What each Rem turn sends: the size of each part of its prompt (gofer/core/turn_context.py).
// The backend stores character counts only, never the text; tokens are characters / 4.
import { apiUrl } from "./api.js";

export const ESTIMATE_NOTE = "Tokens are estimated as characters ÷ 4.";

export function formatChars(chars) {
  const value = Math.max(0, Number(chars) || 0);
  return `${value.toLocaleString("en-US")} ${value === 1 ? "char" : "chars"}`;
}

// "~1.2k tokens": an estimate, so it is always marked with ~ and kept short.
export function formatTokens(tokens) {
  const value = Math.max(0, Number(tokens) || 0);
  if (value >= 1_000_000) return `~${trim(value / 1_000_000)}M tokens`;
  if (value >= 10_000) return `~${Math.round(value / 1000)}k tokens`;
  if (value >= 1_000) return `~${trim(value / 1000)}k tokens`;
  return `~${value} ${value === 1 ? "token" : "tokens"}`;
}

function trim(value) {
  return value.toFixed(1).replace(/\.0$/, "");
}

// One turn's parts, biggest first, with each bar's width as a share of the biggest part.
// Parts a resumed session kept rather than sent again stay listed, marked as kept.
export function contextRows(turn) {
  const parts = Array.isArray(turn?.parts) ? turn.parts.filter(part => Number(part?.chars) > 0) : [];
  const biggest = Math.max(1, ...parts.map(part => Number(part.chars)));
  return [...parts]
    .sort((a, b) => Number(b.sent !== false) - Number(a.sent !== false) || b.chars - a.chars)
    .map(part => ({
      key: part.key,
      label: part.label || part.key,
      chars: part.chars,
      tokens: part.tokens_estimate ?? Math.ceil(part.chars / 4),
      sent: part.sent !== false,
      // A part the turn reads from a file when it needs it, never sent in its prompt.
      file: part.file === true,
      standing: part.standing !== false && part.key !== "conversation",
      width: Math.max(2, Math.round((part.chars / biggest) * 100)),
    }));
}

// The summary line above a turn's parts.
export function contextSummary(turn) {
  if (!turn) return "";
  const sent = formatTokens(turn.sent_tokens_estimate ?? Math.ceil((turn.sent_chars || 0) / 4));
  const kept = (turn.parts || []).filter(part => part.sent === false && part.file !== true);
  return kept.length ? `${sent} sent · instructions kept from the previous turn` : `${sent} sent`;
}

// Standing parts for Settings > Usage, with bar widths against the biggest total.
// A part of one of Rem's own calls (part.call) counts calls rather than turns.
export function standingRows(standing) {
  const parts = Array.isArray(standing?.parts) ? standing.parts : [];
  const biggest = Math.max(1, ...parts.map(part => Number(part.sent_chars) || 0));
  return parts.map(part => ({
    key: part.id || part.key,
    label: part.label || part.key,
    unit: part.call ? "call" : "turn",
    turns: part.turns || 0,
    sentTurns: part.sent_turns || 0,
    averageChars: part.average_chars || 0,
    averageTokens: part.average_tokens_estimate ?? Math.ceil((part.average_chars || 0) / 4),
    sentTokens: part.sent_tokens_estimate ?? Math.ceil((part.sent_chars || 0) / 4),
    width: Math.max(2, Math.round(((Number(part.sent_chars) || 0) / biggest) * 100)),
  }));
}

// Rem's own calls by kind for Settings > Usage: how many, how big on average, and how
// many were re-asked (asked again because the answer couldn't be used) or still failed.
// ``tokens`` holds the input, cache-read and cache-write tokens its providers reported,
// or null when none of its calls reported any.
export function remCallRows(standing) {
  const kinds = Array.isArray(standing?.rem_calls) ? standing.rem_calls : [];
  const biggest = Math.max(1, ...kinds.map(kind => Number(kind.sent_chars) || 0));
  return kinds.map(kind => ({
    key: kind.call,
    label: kind.label || kind.call,
    calls: kind.calls || 0,
    reasked: kind.reasked || 0,
    failed: kind.failed || 0,
    current: currentRules(kind.current_version),
    averageTokens: kind.average_tokens_estimate ?? Math.ceil((kind.average_chars || 0) / 4),
    largestTokens: kind.max_tokens_estimate ?? Math.ceil((kind.max_chars || 0) / 4),
    sentTokens: kind.sent_tokens_estimate ?? Math.ceil((kind.sent_chars || 0) / 4),
    width: Math.max(2, Math.round(((Number(kind.sent_chars) || 0) / biggest) * 100)),
    tokens: kind.reported_calls ? {
      calls: kind.reported_calls,
      input: kind.input_tokens ?? null,
      cacheRead: kind.cache_read_tokens ?? null,
      cacheWrite: kind.cache_write_tokens ?? null,
    } : null,
  }));
}

// The fixed rules version a kind uses now, with its counts over every kept call, or null
// when its newest call had no fixed rules or came before versions were recorded.
function currentRules(version) {
  if (!version?.version) return null;
  return {
    version: version.version,
    calls: version.calls || 0,
    reasked: version.re_asked || 0,
    failed: version.failed || 0,
    since: version.first_seen || "",
  };
}

// One line for the current rules version's counts, so a rules change shows its effect.
export function currentRulesLine(current) {
  if (!current) return "";
  const date = current.since ? new Date(current.since) : null;
  const since = date && !Number.isNaN(date.getTime())
    ? ` since ${date.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`
    : "";
  const calls = `${current.calls} ${current.calls === 1 ? "call" : "calls"}`;
  return `Current rules${since}: ${current.reasked} re-asked · ${current.failed} failed of ${calls}`;
}

// One line for a kind's reported tokens: the input and how much of it came from or went
// into the provider's cache. A count no call reported is left out.
export function remCallTokensLine(tokens) {
  if (!tokens) return "";
  const parts = [
    tokens.input != null ? `${reportedTokens(tokens.input)} input` : "",
    tokens.cacheRead != null ? `${reportedTokens(tokens.cacheRead)} read from cache` : "",
    tokens.cacheWrite != null ? `${reportedTokens(tokens.cacheWrite)} written to cache` : "",
  ].filter(Boolean);
  return parts.length ? `Reported: ${parts.join(" · ")}` : "";
}

// A token count a provider reported: exact, so unlike formatTokens it has no ~.
function reportedTokens(tokens) {
  const value = Math.max(0, Number(tokens) || 0);
  if (value >= 1_000_000) return `${trim(value / 1_000_000)}M tokens`;
  if (value >= 10_000) return `${Math.round(value / 1000)}k tokens`;
  if (value >= 1_000) return `${trim(value / 1000)}k tokens`;
  return `${value} ${value === 1 ? "token" : "tokens"}`;
}

export async function loadThreadContext(threadId, { limit = 5, signal } = {}) {
  const response = await fetch(apiUrl(`/rem/threads/${encodeURIComponent(threadId)}/context?limit=${limit}`), { signal });
  if (!response.ok) throw new Error(`Couldn't read this thread's context sizes (${response.status}).`);
  return response.json();
}
