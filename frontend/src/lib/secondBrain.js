// Second Brain library vocabulary and query semantics shared by the sidebar,
// workspace and Rem request builder. Search here runs over the in-memory
// indexed library snapshot; provider retrieval adds genuine semantic results.

export const DOCUMENT_TYPES = [["note", "Note"], ["report", "Report"], ["text", "Text"], ["html", "Web page"], ["pdf", "PDF"], ["capture", "Web capture"]];
export const DATE_FIELDS = [["modified", "Modified"], ["added", "Added"], ["published", "Published"], ["captured", "Captured"], ["generated", "Report generated"]];
export const READING_STATES = [["unread", "Unread"], ["starred", "Starred"], ["reviewed", "Reviewed"], ["highlighted", "Highlighted"], ["annotated", "Has annotations"]];
export const ORIGINS = [["authored", "User-authored"], ["imported", "Imported"], ["generated", "Generated"]];
export const EXTRACTION_STATES = [["ready", "Ready"], ["pending", "Pending extraction"], ["ocr", "Needs OCR"], ["failed", "Extraction failed"]];
export const CITATION_FILTERS = [["has-source", "Has source URL or DOI"], ["cites", "Cites other documents"], ["cited", "Cited by a report"], ["needs-review", "Citations needing review"]];
export const SEARCH_MODES = [["exact", "Exact"], ["semantic", "Related meaning"], ["hybrid", "Combined"]];
export const SEARCH_IN = [["all", "Title and content"], ["title", "Title only"], ["content", "Content only"], ["citations", "Saved citation text"]];
export const MATCH_MODES = [["all", "All words"], ["any", "Any word"], ["phrase", "Exact phrase"]];
export const SORTS = [["relevance", "Relevance"], ["title", "Title"], ["modified", "Modified"], ["added", "Added"], ["published", "Published"]];
export const CITATION_STATES = {
  found: { label: "Passage found", tone: "success" },
  changed: { label: "Source changed", tone: "warning" },
  relocate: { label: "Location needs review", tone: "warning" },
  unavailable: { label: "Source unavailable", tone: "danger" },
  unreviewed: { label: "Support not reviewed", tone: "neutral" },
};
export const SUPPORT_LABELS = [["quoted", "Quoted"], ["paraphrased", "Paraphrased"], ["inferred", "Inferred"], ["disputed", "Disputed"], ["unsupported", "Unsupported"]];

export const EMPTY_FILTERS = Object.freeze({
  brains: [], folder: "", subfolders: true, collection: "",
  types: [], tags: [], requireAllTags: false,
  authors: [], domains: [], projects: [],
  dateField: "modified", from: "", to: "", includeUnknownDates: false,
  reading: [], origins: [], extraction: [], citations: [],
  includeGenerated: true,
});

const FIELD_ALIASES = { type: "types", tag: "tags", author: "authors", domain: "domains", project: "projects", folder: "folder", in: "collection", state: "reading", origin: "origins", brain: "brains", modified: "date", added: "date", published: "date", captured: "date", generated: "date" };
export const QUERY_FIELDS = Object.keys(FIELD_ALIASES);
const label = (pairs, id) => pairs.find(([value]) => value === id)?.[1] || id;
export const typeLabel = id => label(DOCUMENT_TYPES, id);
export const dateFieldLabel = id => label(DATE_FIELDS, id);
export const readingLabel = id => label(READING_STATES, id);
export const originLabel = id => label(ORIGINS, id);
export const extractionLabel = id => label(EXTRACTION_STATES, id);
export const citationFilterLabel = id => label(CITATION_FILTERS, id);

// Accepts YYYY, YYYY-MM or YYYY-MM-DD and returns an inclusive ISO day range.
export function parseDateBound(value, end = false) {
  const text = String(value || "").trim();
  if (!text) return "";
  const match = /^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$/.exec(text);
  if (!match) return null;
  const [, year, month, day] = match;
  if (month && (+month < 1 || +month > 12)) return null;
  if (day) {
    const date = new Date(`${year}-${month}-${day}T00:00:00Z`);
    return Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== `${year}-${month}-${day}` ? null : `${year}-${month}-${day}`;
  }
  if (month) return end ? new Date(Date.UTC(+year, +month, 0)).toISOString().slice(0, 10) : `${year}-${month}-01`;
  return end ? `${year}-12-31` : `${year}-01-01`;
}

// Text syntax: words, "phrases", -exclusions, OR, and field:value clauses.
// Invalid clauses are reported with their position and are never silently dropped.
export function parseQuery(text = "") {
  const result = { terms: [], phrases: [], exclude: [], any: false, filters: {}, date: null, clauses: [], errors: [] };
  const source = String(text);
  const pattern = /(-?)(?:([a-z]+):)?(?:"([^"]*)("?)|(\S+))/gi;
  let match;
  while ((match = pattern.exec(source))) {
    const [raw, negated, field, quoted, closed, bare] = match;
    const start = match.index, end = start + raw.length;
    const value = quoted !== undefined ? quoted : bare;
    if (quoted !== undefined && !closed) { result.errors.push({ start, end, text: raw, message: "Close the quotation mark to search for this phrase." }); continue; }
    if (!field && !negated && bare === "OR") { result.any = true; continue; }
    if (field) {
      const key = FIELD_ALIASES[field.toLowerCase()];
      if (!key) { result.errors.push({ start, end, text: raw, message: `“${field}:” is not a search field. Try ${QUERY_FIELDS.slice(0, 5).join(":, ")}:.` }); continue; }
      if (!value) { result.errors.push({ start, end, text: raw, message: `Add a value after “${field}:”.` }); continue; }
      if (key === "date") {
        const [from, to = from] = value.split("..");
        const range = { field: field.toLowerCase(), from: parseDateBound(from), to: parseDateBound(to, true) };
        if (range.from === null || range.to === null || (!range.from && !range.to)) { result.errors.push({ start, end, text: raw, message: `“${value}” is not a date range. Use 2026, 2026-03 or 2025..2026-06.` }); continue; }
        result.date = range;
      } else if (key === "folder" || key === "collection") {
        result.filters[key] = value;
      } else {
        result.filters[key] = [...(result.filters[key] || []), value.toLowerCase()];
      }
      result.clauses.push({ start, end, field: key, value, negated: Boolean(negated) });
      continue;
    }
    if (!value) continue;
    if (negated) result.exclude.push(value.toLowerCase());
    else if (quoted !== undefined) result.phrases.push(value.toLowerCase());
    else result.terms.push(value.toLowerCase());
  }
  return result;
}

const quote = value => /\s/.test(value) ? `"${value}"` : value;
// Produces the text form of the visual filters so either form can be learned and saved.
export function queryText(text, filters = EMPTY_FILTERS) {
  const parts = [String(text || "").trim()];
  for (const value of filters.types || []) parts.push(`type:${value}`);
  for (const value of filters.tags || []) parts.push(`tag:${quote(value)}`);
  for (const value of filters.authors || []) parts.push(`author:${quote(value)}`);
  for (const value of filters.domains || []) parts.push(`domain:${value}`);
  for (const value of filters.reading || []) parts.push(`state:${value}`);
  for (const value of filters.origins || []) parts.push(`origin:${value}`);
  if (filters.folder) parts.push(`folder:${quote(filters.folder)}`);
  if (filters.collection) parts.push(`in:${quote(filters.collection)}`);
  if (filters.from || filters.to) parts.push(`${filters.dateField || "modified"}:${filters.from || ""}..${filters.to || ""}`.replace(/:(\d[\d-]*)\.\.\1$/, ":$1"));
  return parts.filter(Boolean).join(" ");
}

export function activeFilterCount(filters = EMPTY_FILTERS) {
  return ["types", "tags", "authors", "domains", "projects", "reading", "origins", "extraction", "citations"].reduce((total, key) => total + (filters[key]?.length || 0), 0)
    + (filters.folder ? 1 : 0) + (filters.collection ? 1 : 0) + (filters.from || filters.to ? 1 : 0) + (filters.brains?.length ? 1 : 0) + (filters.includeGenerated === false ? 1 : 0);
}

const within = (date, from, to) => Boolean(date) && (!from || date >= from) && (!to || date.slice(0, 10) <= to);
const lower = value => String(value || "").toLowerCase();
function documentDate(doc, field) { return field === "generated" ? (doc.type === "report" ? doc.dates?.added : "") : doc.dates?.[field] || ""; }
function readingMatches(doc, state) {
  if (state === "unread") return !doc.reading?.read;
  if (state === "starred") return Boolean(doc.reading?.starred);
  if (state === "reviewed") return Boolean(doc.reading?.reviewed);
  if (state === "highlighted" || state === "annotated") return (doc.annotations || 0) > 0;
  return true;
}
function citationMatches(doc, kind, library) {
  if (kind === "has-source") return Boolean(doc.source?.url || doc.source?.doi);
  if (kind === "cites") return (doc.cites || []).length > 0;
  if (kind === "cited") return library.reportCitations ? (library.reportCitations[doc.id] || []).length > 0 : (library.citedBy?.[doc.id] || []).length > 0;
  if (kind === "needs-review") return Boolean(doc.citationReview) || (library.reportCitations?.[doc.id] || []).some(citation => citation.state !== "found");
  return true;
}

// Filters use AND across families and OR within a family, except when every tag is required.
export function matchesFilters(doc, filters = EMPTY_FILTERS, parsed = parseQuery(""), library = {}) {
  const merged = { ...filters };
  for (const [key, values] of Object.entries(parsed.filters)) merged[key] = Array.isArray(values) ? [...(filters[key] || []), ...values] : values;
  const inSet = (values, value) => !values?.length || values.some(item => lower(item) === lower(value));
  if (merged.brains?.length && !merged.brains.some(value => lower(value) === lower(doc.brainId) || lower(value) === lower(library.brainNames?.[doc.brainId]))) return false;
  if (merged.folder) {
    const folder = lower(merged.folder).replace(/\/+$/, "");
    const docFolder = lower(doc.folder);
    if (!(docFolder === folder || (merged.subfolders !== false && docFolder.startsWith(`${folder}/`)))) return false;
  }
  if (merged.collection && !(library.collectionMembers?.[merged.collection] || library.collectionMembers?.[lower(merged.collection)] || []).includes(doc.id)) return false;
  if (!inSet(merged.types, doc.type)) return false;
  if (merged.tags?.length) {
    const tags = (doc.tags || []).map(lower);
    const wanted = merged.tags.map(lower);
    if (merged.requireAllTags ? !wanted.every(tag => tags.includes(tag)) : !wanted.some(tag => tags.includes(tag))) return false;
  }
  if (merged.authors?.length && !merged.authors.some(author => lower(doc.author).includes(lower(author)))) return false;
  if (merged.domains?.length && !inSet(merged.domains, doc.source?.domain || "")) return false;
  if (merged.projects?.length && !inSet(merged.projects, doc.project || "")) return false;
  if (merged.origins?.length && !inSet(merged.origins, doc.origin)) return false;
  if (merged.extraction?.length && !inSet(merged.extraction, doc.extraction)) return false;
  if (merged.reading?.length && !merged.reading.every(state => readingMatches(doc, state))) return false;
  if (merged.citations?.length && !merged.citations.every(kind => citationMatches(doc, kind, library))) return false;
  if (merged.includeGenerated === false && doc.origin === "generated") return false;
  const range = parsed.date || ((merged.from || merged.to) ? { field: merged.dateField || "modified", from: parseDateBound(merged.from) || "", to: parseDateBound(merged.to, true) || "" } : null);
  if (range) {
    const date = documentDate(doc, range.field);
    // Missing metadata stays unknown; modification time never stands in for a publication date.
    if (!date) return Boolean(merged.includeUnknownDates);
    if (!within(date, range.from, range.to)) return false;
  }
  return true;
}

export function highlightSegments(text, needles = []) {
  const terms = [...new Set(needles.filter(Boolean).map(lower))].sort((a, b) => b.length - a.length);
  if (!terms.length) return [{ text, match: false }];
  const escaped = terms.map(term => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(${escaped.join("|")})`, "gi");
  return String(text).split(pattern).filter(part => part !== "").map(part => ({ text: part, match: terms.includes(lower(part)) }));
}

function textMatches(haystack, parsed, match) {
  const text = lower(haystack);
  const words = parsed.terms;
  if (parsed.exclude.some(word => text.includes(word))) return false;
  if (!parsed.phrases.every(phrase => text.includes(phrase))) return false;
  if (match === "phrase" && words.length) return text.includes(words.join(" "));
  if (!words.length) return true;
  return match === "any" || parsed.any ? words.some(word => text.includes(word)) : words.every(word => text.includes(word));
}

// Returns grouped document results with passage previews and a plain match explanation.
export function searchLibrary(documents, { text = "", filters = EMPTY_FILTERS, mode = "exact", searchIn = "all", match = "all", sort = "relevance" } = {}, library = {}) {
  const parsed = parseQuery(text);
  const needles = [...parsed.terms, ...parsed.phrases];
  const hasText = needles.length > 0 || parsed.exclude.length > 0;
  const results = [];
  for (const doc of documents) {
    if (!matchesFilters(doc, filters, parsed, library)) continue;
    if (!hasText) { results.push({ doc, score: 0, matchKind: "filter", passages: (doc.blocks || []).slice(0, 1).map(block => ({ block, segments: [{ text: block.text, match: false }] })), passageCount: 0 }); continue; }
    if (doc.extraction !== "ready" && searchIn !== "title") continue;
    const titleHit = searchIn !== "content" && searchIn !== "citations" && textMatches(`${doc.title} ${doc.path}`, parsed, match);
    const sources = searchIn === "citations" ? (library.citationsByDoc?.[doc.id] || []).map(citation => ({ id: citation.blockId, heading: "Saved citation", text: citation.quote })) : searchIn === "title" ? [] : doc.blocks || [];
    const exact = sources.filter(block => textMatches(`${block.heading || ""} ${block.text}`, parsed, match));
    let semantic = [];
    if (mode !== "exact" && !exact.length && !titleHit && parsed.terms.length) {
      const concepts = (doc.concepts || []).map(lower);
      if (parsed.terms.some(term => concepts.some(concept => concept.includes(term) || term.includes(concept)))) semantic = sources.slice(0, 1);
    }
    if (!titleHit && !exact.length && !semantic.length) continue;
    const passages = (exact.length ? exact : semantic).map(block => ({ block, segments: highlightSegments(block.text, exact.length ? needles : []) }));
    const matchKind = exact.length ? (parsed.phrases.length ? "phrase" : "content") : titleHit ? "title" : "meaning";
    const score = (titleHit ? 4 : 0) + exact.length * 2 + (semantic.length ? 1 : 0) + (doc.reading?.starred ? 0.25 : 0);
    results.push({ doc, score, matchKind, passages: passages.slice(0, 3), passageCount: passages.length });
  }
  const by = {
    relevance: (a, b) => b.score - a.score || lower(a.doc.title).localeCompare(lower(b.doc.title)),
    title: (a, b) => lower(a.doc.title).localeCompare(lower(b.doc.title)),
    modified: (a, b) => (b.doc.dates?.modified || "").localeCompare(a.doc.dates?.modified || ""),
    added: (a, b) => (b.doc.dates?.added || "").localeCompare(a.doc.dates?.added || ""),
    // Unknown publication dates sort last instead of borrowing another date.
    published: (a, b) => (b.doc.dates?.published || "0").localeCompare(a.doc.dates?.published || "0"),
  };
  return { parsed, results: results.sort(by[sort] || by.relevance) };
}

export const MATCH_LABELS = { phrase: "Exact phrase", content: "Matched words", title: "Title or path", meaning: "Related meaning", filter: "Matches filters" };

// Facet counts reflect the other active filters so users can see the effect of each choice.
export function facetCounts(documents, key, accessor, options = {}, library = {}) {
  const { filters = EMPTY_FILTERS, text = "" } = options;
  const counts = new Map();
  const parsed = parseQuery(text);
  for (const doc of documents) {
    if (!matchesFilters(doc, { ...filters, [key]: [] }, { ...parsed, filters: { ...parsed.filters, [key]: undefined } }, library)) continue;
    for (const value of [].concat(accessor(doc) || [])) counts.set(value, (counts.get(value) || 0) + 1);
  }
  return counts;
}

// Explains the active query in plain language, joining families with "and".
export function describeQuery({ text = "", filters = EMPTY_FILTERS, mode = "exact", searchIn = "all", match = "all", brainName = "this brain", brainNames = {} } = {}) {
  const parsed = parseQuery(text);
  const parts = [];
  const scope = filters.brains?.length > 1 ? `${filters.brains.length} brains (${filters.brains.map(id => brainNames[id] || id).join(", ")})` : filters.brains?.length === 1 ? brainNames[filters.brains[0]] || "one brain" : brainName;
  const where = { all: "title or content", title: "title", content: "content", citations: "saved citations" }[searchIn];
  if (parsed.phrases.length) parts.push(`the ${where} contains ${parsed.phrases.map(phrase => `“${phrase}”`).join(" and ")}`);
  if (parsed.terms.length) {
    const joiner = match === "any" || parsed.any ? " or " : " and ";
    parts.push(match === "phrase" ? `the ${where} contains “${parsed.terms.join(" ")}”` : `the ${where} mentions ${parsed.terms.join(joiner)}${mode === "semantic" ? " or a related idea" : mode === "hybrid" ? ", including related ideas" : ""}`);
  }
  if (parsed.exclude.length) parts.push(`does not mention ${parsed.exclude.join(" or ")}`);
  const types = [...(filters.types || []), ...(parsed.filters.types || [])];
  if (types.length) parts.push(`type is ${types.map(typeLabel).join(" or ")}`);
  const tags = [...(filters.tags || []), ...(parsed.filters.tags || [])];
  if (tags.length) parts.push(`tagged ${tags.join(filters.requireAllTags ? " and " : " or ")}`);
  const authors = [...(filters.authors || []), ...(parsed.filters.authors || [])];
  if (authors.length) parts.push(`by ${authors.join(" or ")}`);
  if (filters.folder || parsed.filters.folder) parts.push(`in ${filters.folder || parsed.filters.folder}${filters.subfolders === false ? "" : " and subfolders"}`);
  if (filters.collection || parsed.filters.collection) parts.push(`in the collection ${filters.collection || parsed.filters.collection}`);
  const range = parsed.date || (filters.from || filters.to ? { field: filters.dateField, from: filters.from, to: filters.to } : null);
  if (range) parts.push(`${dateFieldLabel(range.field).toLowerCase()} ${range.from && range.to ? `between ${range.from} and ${range.to}` : range.from ? `on or after ${range.from}` : `on or before ${range.to}`}${filters.includeUnknownDates ? " (or unknown)" : ""}`);
  const reading = [...(filters.reading || []), ...(parsed.filters.reading || [])];
  if (reading.length) parts.push(`marked ${reading.map(readingLabel).map(lower).join(" and ")}`);
  const origins = [...(filters.origins || []), ...(parsed.filters.origins || [])];
  if (origins.length) parts.push(`origin is ${origins.map(originLabel).map(lower).join(" or ")}`);
  if (filters.extraction?.length) parts.push(`extraction is ${filters.extraction.map(extractionLabel).map(lower).join(" or ")}`);
  if (filters.citations?.length) parts.push(filters.citations.map(citationFilterLabel).map(lower).join(" and "));
  if (filters.includeGenerated === false) parts.push("excluding generated reports and summaries");
  return parts.length ? `Documents in ${scope} where ${parts.join(", and ")}.` : `All documents in ${scope}.`;
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "Unknown size";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

export function formatDay(iso, { relative = false, now = new Date() } = {}) {
  if (!iso) return "Unknown";
  const date = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  if (Number.isNaN(date.getTime())) return "Unknown";
  if (relative) {
    const days = Math.round((new Date(now.toDateString()) - new Date(date.toDateString())) / 86400000);
    if (days === 0) return "Today";
    if (days === 1) return "Yesterday";
    if (days > 1 && days < 7) return `${days} days ago`;
  }
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

// Date and minute, without seconds: "Oct 5, 2026, 4:22 PM".
export function formatTime(iso) {
  const date = new Date(iso);
  return iso && !Number.isNaN(date.getTime()) ? date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "Unknown";
}

export function formatCitation(doc, block, style = "short") {
  const author = doc.author || "Unknown author";
  const year = doc.dates?.published?.slice(0, 4) || "n.d.";
  const locator = block ? (doc.type === "pdf" ? `p. ${block.page ?? "?"}` : `§ ${block.heading}`) : "";
  if (style === "short") return `${author}, ${year}${locator ? `, ${locator}` : ""}`;
  return `${author} (${year}). ${doc.title}.${doc.source?.url ? ` ${doc.source.url}` : ""}${locator ? ` ${locator}.` : ""}`;
}

// A rough planning estimate; providers report actual usage after a turn.
export const estimateTokens = text => Math.ceil(String(text || "").length / 4);

// Word-level diff for comparing a cited passage with its current text.
export function wordDiff(before = "", after = "") {
  const a = String(before).split(/(\s+)/).filter(Boolean), b = String(after).split(/(\s+)/).filter(Boolean);
  const table = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) for (let j = b.length - 1; j >= 0; j -= 1) table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  const parts = [];
  const push = (kind, text) => { const last = parts.at(-1); if (last?.kind === kind) last.text += text; else parts.push({ kind, text }); };
  let i = 0, j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { push("same", a[i]); i += 1; j += 1; }
    else if (table[i + 1][j] >= table[i][j + 1]) { push("removed", a[i]); i += 1; }
    else { push("added", b[j]); j += 1; }
  }
  while (i < a.length) push("removed", a[i++]);
  while (j < b.length) push("added", b[j++]);
  return parts;
}

export const HEALTH_KINDS = [
  ["link", "Broken links"], ["metadata", "Missing metadata"], ["extraction", "Extraction problems"], ["duplicate", "Exact duplicates"], ["citation", "Ambiguous citations"],
];


export const RECIPES = [
  ["summarize-new", "Summarize newly added documents", "Write a short cited summary for each new document."],
  ["digest", "Prepare a research digest", "Collect recent additions into one cited digest."],
  ["refresh-comparison", "Refresh a comparison when sources change", "Draft a revised comparison for review when cited sources change."],
];
