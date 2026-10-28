import { useSyncExternalStore } from "react";
import { brainRequest } from "./secondBrainApi.js";
import { EMPTY_FILTERS } from "./secondBrain.js";

// One shared library state for the sidebar and workspace. Each brain keeps its
// own view state so switching brains restores the query, tabs and selection.
// Edits publish optimistically and are serialized to the persistent library service.

export const DEFAULT_SEARCH = Object.freeze({ text: "", filters: EMPTY_FILTERS, mode: "exact", searchIn: "all", match: "all", sort: "relevance", savedId: "" });
const newView = () => ({ section: "search", collectionId: "", reportId: "", search: { ...DEFAULT_SEARCH }, tabs: [], activeDocId: "", focus: null, selection: [] });
let sequence = 0;
export const uid = prefix => `${prefix}-${Date.now().toString(36)}-${(++sequence).toString(36)}`;

function initialState() {
  return { loaded: false, brains: [], activeBrainId: "", documents: [], collections: [], savedSearches: [], reports: [], health: [], jobs: [], citations: [], views: {}, tasks: [], dialog: null, toast: null };
}
function seededState(sample) {
  return {
    loaded: true,
    brains: structuredClone(sample.SAMPLE_BRAINS),
    activeBrainId: sample.SAMPLE_BRAINS[0].id,
    documents: structuredClone(sample.SAMPLE_DOCUMENTS),
    collections: structuredClone(sample.SAMPLE_COLLECTIONS),
    savedSearches: structuredClone(sample.SAMPLE_SAVED_SEARCHES),
    reports: structuredClone(sample.SAMPLE_REPORTS),
    health: structuredClone(sample.SAMPLE_HEALTH),
    jobs: structuredClone(sample.SAMPLE_JOBS),
    citations: [
      { id: "cit-1", brainId: "brain-research", docId: "doc-event-journal", blockId: "ej-2", quote: "Readers never infer order from wall-clock timestamps", note: "Key ordering rule", version: "3a9f2c1", createdAt: new Date(Date.now() - 4 * 86400000).toISOString() },
      { id: "cit-2", brainId: "brain-research", docId: "doc-fts5", blockId: "fts-2", quote: "render match spans as escaped text", note: "", version: "c40e118", createdAt: new Date(Date.now() - 9 * 86400000).toISOString() },
      { id: "cit-3", brainId: "brain-research", docId: "doc-rrf", blockId: "rrf-2", quote: "The constant k = 60 was fixed during a pilot investigation", note: "Evaluate on our own corpus", version: "e2b7d45", createdAt: new Date(Date.now() - 20 * 86400000).toISOString() },
    ],
  };
}

let state = initialState();
let loading = null;
let fixture = false;
let queue = Promise.resolve();
let pending = 0;
let poll = null;
let preferencesTimer;
let researchProvider = { id: "codex", model: "cli-default" };
const listeners = new Set();
function emit() { for (const listener of listeners) listener(); }
function set(next) {
  const before = state;
  state = typeof next === "function" ? next(state) : { ...state, ...next };
  emit();
  if (!fixture && before.loaded && state.loaded) persistChanges(before, state);
}
function persistChanges(before, after) {
  for (const kind of ["documents", "collections", "savedSearches", "reports", "citations", "jobs"]) {
    if (before[kind] === after[kind]) continue;
    const old = new Map(before[kind].map(item => [item.id, item]));
    const next = new Map(after[kind].map(item => [item.id, item]));
    for (const [id, item] of next) if (JSON.stringify(old.get(id)) !== JSON.stringify(item)) {
      enqueue("mutate", { kind, id, item: structuredClone(kind === "documents" ? Object.fromEntries(Object.entries(item).filter(([key]) => ["id", "brainId", "title", "author", "tags", "project", "reading", "dates", "source", "inbox", "excluded", "encoding", "ignoredIssues"].includes(key))) : item) });
    }
    for (const id of old.keys()) if (!next.has(id)) enqueue("mutate", { kind, id, delete: true });
  }
  if (before.brains !== after.brains) for (const brain of after.brains) {
    const old = before.brains.find(item => item.id === brain.id);
    if (old) {
      const patch = Object.fromEntries(Object.entries(brain).filter(([key, value]) => ["name", "root", "settings", "lastOpened"].includes(key) && JSON.stringify(old[key]) !== JSON.stringify(value)));
      if (Object.keys(patch).length) enqueue("update_brain", { id: brain.id, patch });
    }
  }
  if (before.activeBrainId !== after.activeBrainId || before.views !== after.views) {
    clearTimeout(preferencesTimer);
    preferencesTimer = setTimeout(() => enqueue("preferences", { activeBrainId: state.activeBrainId, views: state.views }), 350);
  }
}
function enqueue(action, params) {
  pending++;
  queue = queue.then(() => brainRequest(action, params)).catch(error => {
    state = { ...state, toast: { id: uid("error"), message: error.message, tone: "danger" } }; emit();
  }).finally(() => { pending--; if (!pending) void refreshLibrary(); });
  return queue;
}
export function flushLibraryWrites() { return queue; }
export function setResearchProvider(provider) { researchProvider = provider; }
export function isSampleLibrary() { return fixture; }
export function researchProviderSettings() { return researchProvider; }
export async function refreshLibrary({ initial = false } = {}) {
  if (fixture || (pending && !initial)) return;
  try {
    const snapshot = await brainRequest();
    if (pending) return;
    const priorTasks = new Map(state.tasks.map(item => [item.id, item.state]));
    const activeBrainId = (initial ? snapshot.activeBrainId : state.activeBrainId) || snapshot.brains[0]?.id || "";
    state = { ...state, ...snapshot, activeBrainId: snapshot.brains.some(item => item.id === activeBrainId) ? activeBrainId : snapshot.brains[0]?.id || "", views: initial ? snapshot.views || {} : state.views, loaded: true, error: null };
    for (const task of state.tasks) if (task.state === "done" && priorTasks.get(task.id) === "running") {
      if (task.result?.reportId) state = { ...state, views: { ...state.views, [task.brainId]: { ...viewFor(state, task.brainId), section: "reports", reportId: task.result.reportId } } };
      if (task.result?.brainId) state = { ...state, activeBrainId: task.result.brainId };
    }
    emit();
  } catch (error) {
    state = { ...state, loaded: true, error: error.message }; emit();
  }
}
export function subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); }
export const getState = () => state;
export function useSecondBrain() { return useSyncExternalStore(subscribe, getState, getState); }
export function resetSecondBrainStore() { clearInterval(poll); clearTimeout(preferencesTimer); poll = null; state = initialState(); loading = null; fixture = false; emit(); }
// Explicit fixtures for tests and the visual harness. Production never seeds a sample library.
export async function loadSampleLibrary() {
  fixture = true;
  const sample = await import("./secondBrainSample.js");
  set(current => ({ ...current, ...seededState(sample) }));
}
// Loads the library once; callers render a loading state until it resolves.
export function ensureLibrary() {
  if (state.loaded) return Promise.resolve();
  loading ||= refreshLibrary({ initial: true }).then(() => {
    if (!fixture && !poll) poll = setInterval(() => { if (listeners.size && (typeof document === "undefined" || !document.hidden)) void refreshLibrary(); }, 2500);
    poll?.unref?.();
  });
  return loading;
}

export function viewFor(current, brainId = current.activeBrainId) { return current.views[brainId] || newView(); }
function updateView(patch, brainId = state.activeBrainId) {
  set(current => {
    const view = viewFor(current, brainId);
    return { ...current, views: { ...current.views, [brainId]: { ...view, ...(typeof patch === "function" ? patch(view) : patch) } } };
  });
}

export function libraryFor(current, brainId = current.activeBrainId) {
  const documents = current.documents.filter(doc => doc.brainId === brainId);
  const citedBy = {};
  const reportCitations = {};
  for (const report of current.reports) for (const citation of report.citations) {
    (citedBy[citation.docId] ||= []).includes(report.id) || citedBy[citation.docId].push(report.id);
    (reportCitations[citation.docId] ||= []).push(citation);
  }
  for (const doc of documents) for (const target of doc.cites || []) (citedBy[target] ||= []).includes(doc.id) || citedBy[target].push(doc.id);
  const collectionMembers = {};
  for (const collection of current.collections.filter(item => item.brainId === brainId)) {
    const members = [...new Set(collection.sections.flatMap(section => section.items.map(item => item.docId)))];
    collectionMembers[collection.id] = members;
    collectionMembers[collection.name.toLowerCase()] = members;
  }
  const citationsByDoc = {};
  for (const citation of current.citations.filter(item => item.brainId === brainId)) (citationsByDoc[citation.docId] ||= []).push(citation);
  return { documents, citedBy, collectionMembers, citationsByDoc, reportCitations, brainNames: Object.fromEntries(current.brains.map(brain => [brain.id, brain.name])) };
}
export const documentById = (current, id) => current.documents.find(doc => doc.id === id);
export const blockById = (doc, blockId) => doc?.blocks?.find(block => block.id === blockId);

export function notify(message, tone = "success") { set({ toast: { id: uid("toast"), message, tone } }); }
export function dismissToast() { set({ toast: null }); }
export function dismissError() { set({ error: null }); }

export function setActiveBrain(brainId) {
  set(current => ({ ...current, activeBrainId: brainId, brains: current.brains.map(brain => brain.id === brainId && brain.state === "available" ? { ...brain, lastOpened: new Date().toISOString() } : brain) }));
}
export function navigate(section, extra = {}) { updateView({ section, ...extra }); }
export function updateSearch(patch) { updateView(view => ({ search: { ...view.search, ...patch, savedId: patch.savedId ?? (patch.text !== undefined || patch.filters ? "" : view.search.savedId) } })); }
export function resetSearch() { updateView({ search: { ...DEFAULT_SEARCH } }); }

export function applySavedSearch(id) {
  const saved = state.savedSearches.find(item => item.id === id);
  if (!saved) return;
  updateView({ section: "search", search: { ...DEFAULT_SEARCH, text: saved.text || "", filters: { ...EMPTY_FILTERS, ...saved.filters }, mode: saved.mode || "exact", searchIn: saved.searchIn || "all", match: saved.match || "all", sort: saved.sort || "relevance", savedId: id } });
}
export function saveSearch(name) {
  const view = viewFor(state);
  const saved = { id: uid("view"), brainId: state.activeBrainId, name, text: view.search.text, filters: view.search.filters, mode: view.search.mode, searchIn: view.search.searchIn, match: view.search.match, sort: view.search.sort };
  set(current => ({ ...current, savedSearches: [...current.savedSearches, saved] }));
  updateView(current => ({ search: { ...current.search, savedId: saved.id } }));
  notify(`Saved search “${name}”.`);
}
export function deleteSavedSearch(id) { set(current => ({ ...current, savedSearches: current.savedSearches.filter(item => item.id !== id) })); }

export function openDocument(docId, { blockId = "", quote = "", preview = true, brainId } = {}) {
  const doc = documentById(state, docId);
  if (!doc) return;
  if (brainId || doc.brainId !== state.activeBrainId) setActiveBrain(doc.brainId);
  updateView(view => {
    const existing = view.tabs.find(tab => tab.docId === docId);
    let tabs = view.tabs;
    if (!existing) tabs = preview ? [...view.tabs.filter(tab => !tab.preview), { docId, preview: true }] : [...view.tabs, { docId, preview: false }];
    else if (!preview && existing.preview) tabs = view.tabs.map(tab => tab.docId === docId ? { ...tab, preview: false } : tab);
    return { section: "search", tabs, activeDocId: docId, focus: { docId, blockId, quote, request: Date.now() } };
  }, doc.brainId);
  set(current => ({ ...current, documents: current.documents.map(item => item.id === docId ? { ...item, reading: { ...item.reading, read: true } } : item) }));
}
export function pinTab(docId) { updateView(view => ({ tabs: view.tabs.map(tab => tab.docId === docId ? { ...tab, preview: false } : tab) })); }
export function closeTab(docId) {
  updateView(view => {
    const index = view.tabs.findIndex(tab => tab.docId === docId);
    const tabs = view.tabs.filter(tab => tab.docId !== docId);
    return { tabs, activeDocId: view.activeDocId === docId ? (tabs[Math.min(index, tabs.length - 1)]?.docId || "") : view.activeDocId };
  });
}
export function activateTab(docId) { updateView({ activeDocId: docId }); }
export function toggleSelection(docId, force) { updateView(view => ({ selection: (force ?? !view.selection.includes(docId)) ? [...new Set([...view.selection, docId])] : view.selection.filter(id => id !== docId) })); }
export function clearSelection() { updateView({ selection: [] }); }

export function updateDocument(docId, patch) { set(current => ({ ...current, documents: current.documents.map(doc => doc.id === docId ? { ...doc, ...(typeof patch === "function" ? patch(doc) : patch) } : doc) })); }
export function removeDocument(docId) { set(current => ({ ...current, documents: current.documents.filter(doc => doc.id !== docId), health: current.health.filter(issue => issue.docId !== docId) })); }
export function toggleReading(docId, key) { updateDocument(docId, doc => ({ reading: { ...doc.reading, [key]: !doc.reading?.[key] } })); }

export function saveCitation({ docId, blockId, quote, note = "" }) {
  const doc = documentById(state, docId);
  const citation = { id: uid("cit"), brainId: doc.brainId, docId, blockId, quote, note, version: doc.version, createdAt: new Date().toISOString() };
  set(current => ({ ...current, citations: [...current.citations, citation] }));
  updateDocument(docId, item => ({ annotations: (item.annotations || 0) + 1 }));
  notify("Citation saved.");
  return citation;
}
export function removeCitation(id) { set(current => ({ ...current, citations: current.citations.filter(item => item.id !== id) })); }

export function createCollection(name, items = []) {
  const collection = { id: uid("col"), brainId: state.activeBrainId, name, brief: "", updated: new Date().toISOString(), sections: [{ id: uid("sec"), title: "Evidence", items }], questions: [] };
  set(current => ({ ...current, collections: [...current.collections, collection] }));
  return collection;
}
export function updateCollection(id, patch) {
  set(current => ({ ...current, collections: current.collections.map(item => item.id === id ? { ...item, ...(typeof patch === "function" ? patch(item) : patch), updated: new Date().toISOString() } : item) }));
}
export function deleteCollection(id) { set(current => ({ ...current, collections: current.collections.filter(item => item.id !== id) })); }
export function evidenceItem(docId, blockId = "", note = "") {
  const doc = documentById(state, docId);
  return { id: uid("it"), kind: blockId ? "passage" : "document", docId, blockId, version: doc?.version || "", note };
}
export function addToCollection(collectionId, items, sectionId) {
  const list = [].concat(items);
  updateCollection(collectionId, collection => {
    const target = sectionId || collection.sections[0]?.id;
    const known = new Set(collection.sections.flatMap(section => section.items.map(item => `${item.docId}:${item.blockId || ""}`)));
    const fresh = list.filter(item => !known.has(`${item.docId}:${item.blockId || ""}`));
    const sections = collection.sections.length ? collection.sections : [{ id: uid("sec"), title: "Evidence", items: [] }];
    return { sections: sections.map(section => section.id === (target || sections[0].id) ? { ...section, items: [...section.items, ...fresh] } : section) };
  });
  const collection = state.collections.find(item => item.id === collectionId);
  notify(`Added ${list.length === 1 ? (list[0].blockId ? "passage" : "document") : `${list.length} items`} to “${collection?.name}”.`);
}

export function createBrain(params) {
  if (fixture) {
    const existing = state.brains.find(brain => brain.root === params.root.replace(/\/+$/, ""));
    if (existing) { setActiveBrain(existing.id); return existing; }
    const brain = { id: uid("brain"), ...params, state: "available", lastOpened: new Date().toISOString(), color: "indigo", index: { state: "ready", progress: 100, documents: 0, passages: 0, pending: 0, failed: 0, generation: 1, asOf: new Date().toISOString() }, settings: { metadata: "portable", semantic: false, allowCloud: false, formats: {} } };
    set(current => ({ ...current, brains: [...current.brains, brain], activeBrainId: brain.id })); return brain;
  }
  return (async () => { try {
    const brain = await brainRequest("open", params);
    state = { ...state, activeBrainId: brain.id };
    await refreshLibrary();
    return brain;
  } catch (error) { notify(error.message, "danger"); return null; } })();
}
export function updateBrain(id, patch) { set(current => ({ ...current, brains: current.brains.map(brain => brain.id === id ? { ...brain, ...(typeof patch === "function" ? patch(brain) : patch) } : brain) })); }
export function removeBrain(id) {
  if (!fixture) enqueue("remove", { id });
  set(current => {
    const brains = current.brains.filter(brain => brain.id !== id);
    const views = { ...current.views }; delete views[id];
    return { ...current, brains, views, activeBrainId: current.activeBrainId === id ? (brains.find(brain => brain.state === "available") || brains[0])?.id || "" : current.activeBrainId };
  });
}
// The legacy single-root setting becomes the first registry entry rather than a new brain.
export async function adoptLegacyRoot(root) {
  if (!root || fixture || state.brains.some(brain => brain.root === root.replace(/\/+$/, ""))) return;
  await createBrain({ root, metadata: "portable" });
}

export function openDialog(kind, props = {}) { set({ dialog: { kind, ...props } }); }
export function closeDialog() { set({ dialog: null }); }

// Task state and progress come from durable backend receipts.
export async function startTask({ kind, label, brainId = state.activeBrainId, ...params }) {
  if (fixture) { notify("This visual fixture does not execute backend operations.", "info"); return null; }
  try {
    await flushLibraryWrites();
    const task = await brainRequest("task", { ...params, kind, label, brainId, provider: params.provider || researchProvider });
    state = { ...state, tasks: [...state.tasks.filter(item => item.id !== task.id), task] }; emit();
    return task.id;
  } catch (error) { notify(error.message, "danger"); return null; }
}
export function cancelTask(id) { if (!fixture) enqueue("cancel", { id }); }
export function dismissTask(id) { if (!fixture) enqueue("dismiss", { id }); set(current => ({ ...current, tasks: current.tasks.filter(task => task.id !== id) })); }

export function addReport(report) { set(current => ({ ...current, reports: [report, ...current.reports] })); }
export function updateReport(id, patch) { set(current => ({ ...current, reports: current.reports.map(report => report.id === id ? { ...report, ...(typeof patch === "function" ? patch(report) : patch) } : report) })); }
export function updateClaim(reportId, claimId, patch) { updateReport(reportId, report => ({ claims: report.claims.map(claim => claim.id === claimId ? { ...claim, ...patch } : claim) })); }
export function updateReportCitation(reportId, citationId, patch) { updateReport(reportId, report => ({ citations: report.citations.map(citation => citation.id === citationId ? { ...citation, ...patch } : citation) })); }

export function resolveHealth(ids) { if (!fixture) enqueue("ignore_health", { ids: [].concat(ids) }); const list = new Set([].concat(ids)); set(current => ({ ...current, health: current.health.filter(issue => !list.has(issue.id)) })); }
export function saveJob(job) { job = { ...job, provider: job.provider || researchProvider }; set(current => ({ ...current, jobs: current.jobs.some(item => item.id === job.id) ? current.jobs.map(item => item.id === job.id ? job : item) : [...current.jobs, job] })); }
export function removeJob(id) { set(current => ({ ...current, jobs: current.jobs.filter(job => job.id !== id) })); }
export function captureSource({ title, url, excerpt }) {
  return startTask({ kind: "capture", label: "Capturing source", title, url, excerpt });
}
