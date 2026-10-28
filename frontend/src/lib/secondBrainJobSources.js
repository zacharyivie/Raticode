export function defaultJobSourceRules() {
  return { version: 1, location: { kind: "all", id: "", path: "", recursive: true }, query: "", match: "all", time: { mode: "since-success", field: "activity", count: 7, unit: "days" }, tags: [], excludeTags: [], requireAllTags: false, types: [], project: "", unreadOnly: false, starredOnly: false, includeOutputs: false, onlyUnprocessed: false, order: "oldest" };
}

export function jobSourceRules(job, collections = [], searches = []) {
  const defaults = defaultJobSourceRules();
  if (job.sourceRules) return { ...defaults, ...structuredClone(job.sourceRules), location: { ...defaults.location, ...job.sourceRules.location }, time: { ...defaults.time, ...job.sourceRules.time } };
  defaults.time.mode = "all";
  const scope = job.scope || "Inbox folder";
  if (scope === "Inbox folder") defaults.location.kind = "inbox";
  else if (scope === "Documents added in the last 7 days") defaults.time = { mode: "window", field: "added", count: 7, unit: "days" };
  else if (scope.startsWith("Collection: ") || scope.startsWith("Saved search: ")) {
    const collection = scope.startsWith("Collection: ");
    defaults.location.kind = collection ? "collection" : "saved-search";
    defaults.location.id = (collection ? collections : searches).find(item => item.name === scope.split(": ").slice(1).join(": "))?.id || "";
  }
  return defaults;
}

export function jobScopeLabel(rules, collections = [], searches = []) {
  const location = rules.location;
  if (location.kind === "all") return "All documents";
  if (location.kind === "inbox") return "Inbox folder";
  if (location.kind === "folder") return `Folder: ${location.path}`;
  const collection = location.kind === "collection";
  return `${collection ? "Collection" : "Saved search"}: ${(collection ? collections : searches).find(item => item.id === location.id)?.name || "Unavailable"}`;
}

export function jobSourceRulesError(rules, collections, searches) {
  if (["collection", "saved-search"].includes(rules.location.kind) && !rules.location.id) return "Choose a collection or saved search.";
  const items = rules.location.kind === "collection" ? collections : rules.location.kind === "saved-search" ? searches : undefined;
  if (items && !items.some(item => item.id === rules.location.id)) return "The selected collection or saved search is unavailable.";
  if (rules.location.kind === "folder" && (!rules.location.path.trim() || /(^\/|\\|\0|:|(^|\/)\.\.?($|\/))/.test(rules.location.path))) return "Choose a relative folder inside this brain.";
  if (rules.time.mode === "window" && (!Number.isInteger(rules.time.count) || rules.time.count < 1 || rules.time.count > 87600)) return "Enter 1–87600 whole hours or days.";
  return "";
}

export function describeJobSources(rules, collections = [], searches = []) {
  const parts = [jobScopeLabel(rules, collections, searches)];
  if (rules.location.kind === "folder") parts.push(rules.location.recursive ? "including subfolders" : "this folder only");
  if (rules.query) parts.push(`matching ${rules.match === "phrase" ? "the phrase" : rules.match === "any" ? "any words in" : "all words in"} "${rules.query}"`);
  const field = { activity: "added or changed", added: "added", modified: "modified", published: "published", captured: "captured" }[rules.time.field];
  if (rules.time.mode === "since-success") parts.push(`${field} since the last successful run`);
  if (rules.time.mode === "window") parts.push(`${field} in the last ${rules.time.count} ${rules.time.unit}`);
  if (rules.tags.length) parts.push(`with ${rules.requireAllTags ? "all" : "any"} tags: ${rules.tags.join(", ")}`);
  if (rules.excludeTags.length) parts.push(`excluding tags: ${rules.excludeTags.join(", ")}`);
  if (rules.types.length) parts.push(`types: ${rules.types.join(", ")}`);
  if (rules.project) parts.push(`project: ${rules.project}`);
  if (rules.unreadOnly) parts.push("unread only");
  if (rules.starredOnly) parts.push("starred only");
  if (rules.onlyUnprocessed) parts.push("source versions not yet processed by this job");
  if (rules.includeOutputs) parts.push("including previous Second Brain outputs");
  parts.push(`${rules.order} first`);
  return parts.join("; ");
}
