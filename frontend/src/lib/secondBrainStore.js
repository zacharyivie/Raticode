import { useEffect, useState, useSyncExternalStore } from "react";
import { brainRequest } from "./secondBrainApi.js";
import { EMPTY_FILTERS, withFolderRule } from "./secondBrain.js";
import { mergedProject } from "./secondBrainGraph.js";

// One shared library state for the sidebar and workspace. Each brain keeps its
// own view state so switching brains restores the query, tabs and selection.
// Edits publish optimistically and are serialized to the persistent library service.

export const DEFAULT_SEARCH = Object.freeze({ text: "", filters: EMPTY_FILTERS, mode: "exact", searchIn: "all", match: "all", sort: "relevance", savedId: "" });
const newView = () => ({ section: "search", collectionId: "", reportId: "", projectId: "", projectTab: "reports", mapTopicId: "", search: { ...DEFAULT_SEARCH }, tabs: [], activeDocId: "", focus: null, selection: [] });
let sequence = 0;
export const uid = prefix => `${prefix}-${Date.now().toString(36)}-${(++sequence).toString(36)}`;

function initialState() {
  return { loaded: false, passagesLoading: false, brains: [], activeBrainId: "", documents: [], collections: [], savedSearches: [], reports: [], health: [], families: [], series: [], jobs: [], citations: [], projects: [], topics: [], views: {}, tasks: [], dialog: null, toast: null };
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
    projects: [],
    topics: [],
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
// The library revision the state came from. Polls send it and get { unchanged } while it is current.
let revision = "";
let recheckNext = false;
let preferencesTimer;
let researchProvider = { id: "codex", model: "cli-default" };
// The snapshot sends each document's version and passage count, not its passages. They
// live here by document ID with the version they belong to; a refresh fetches only the
// documents whose version this lacks ("passages"), and drops documents that are gone.
const passageCache = new Map();
// Each requested ID mapped to the version it was asked for, so two refreshes don't fetch the same passages.
const passagesAsked = new Map();
// Stored older versions, fetched when a version list or a version dialog opens.
const historyCache = new Map();
const listeners = new Set();
function emit() { for (const listener of listeners) listener(); }
function set(next) {
  const before = state;
  state = typeof next === "function" ? next(state) : { ...state, ...next };
  emit();
  if (!fixture && before.loaded && state.loaded) persistChanges(before, state);
}
function persistChanges(before, after) {
  for (const kind of ["documents", "collections", "savedSearches", "reports", "citations", "jobs", "projects", "topics"]) {
    if (before[kind] === after[kind]) continue;
    const old = new Map(before[kind].map(item => [item.id, item]));
    const next = new Map(after[kind].map(item => [item.id, item]));
    for (const [id, item] of next) if (JSON.stringify(old.get(id)) !== JSON.stringify(item)) {
      enqueue("mutate", { kind, id, item: structuredClone(kind === "documents" ? Object.fromEntries(Object.entries(item).filter(([key]) => ["id", "brainId", "title", "author", "tags", "project", "reading", "dates", "source", "inbox", "excluded", "encoding", "ignoredIssues", "checkBy", "summary"].includes(key))) : item) });
    }
    for (const id of old.keys()) if (!next.has(id)) enqueue("mutate", { kind, id, delete: true });
  }
  if (before.brains !== after.brains) for (const brain of after.brains) {
    const old = before.brains.find(item => item.id === brain.id);
    if (old) {
      const patch = Object.fromEntries(Object.entries(brain).filter(([key, value]) => ["name", "root", "settings", "backup", "lastOpened"].includes(key) && JSON.stringify(old[key]) !== JSON.stringify(value)));
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
    // The optimistic edit never reached the library: the next refresh replaces it in full.
    revision = "";
    state = { ...state, toast: { id: uid("error"), message: error.message, tone: "danger" } }; emit();
  }).finally(() => { pending--; if (!pending) void refreshLibrary(); });
  return queue;
}
export function flushLibraryWrites() { return queue; }
export function setResearchProvider(provider) { researchProvider = provider; }
export function isSampleLibrary() { return fixture; }
// Puts the cached passages on each document. A document whose version the cache lacks keeps
// the passages it had (none on the first load) until its own arrive, marked passagesLoading.
function withPassages(documents) {
  let passagesLoading = false;
  const next = documents.map(doc => {
    const cached = passageCache.get(doc.id);
    if (cached?.version === doc.version) {
      if (doc.blocks === cached.blocks && !doc.passagesLoading) return doc;
      const filled = { ...doc, blocks: cached.blocks };
      delete filled.passagesLoading;
      return filled;
    }
    passagesLoading = true;
    return doc.passagesLoading ? doc : { ...doc, blocks: doc.blocks || cached?.blocks || [], passagesLoading: true };
  });
  return { documents: next, passagesLoading };
}
// A full refresh carries every document's passages and stored versions.
function keepPassages(documents) {
  for (const doc of documents) {
    if (Array.isArray(doc.blocks)) passageCache.set(doc.id, { version: doc.version, blocks: doc.blocks });
    if (Array.isArray(doc.versions)) historyCache.set(doc.id, { version: doc.version, versions: doc.versions });
  }
}
function dropRemoved(documents) {
  const ids = new Set(documents.map(doc => doc.id));
  for (const cache of [passageCache, passagesAsked, historyCache]) for (const id of cache.keys()) if (!ids.has(id)) cache.delete(id);
}
// Fetches, in one request, the passages of every document whose version the cache lacks.
async function fetchPassages() {
  const wanted = state.documents.filter(doc => passageCache.get(doc.id)?.version !== doc.version && passagesAsked.get(doc.id) !== doc.version);
  if (fixture || !wanted.length) return;
  for (const doc of wanted) passagesAsked.set(doc.id, doc.version);
  try {
    const answer = await brainRequest("passages", { ids: wanted.map(doc => doc.id) });
    for (const doc of answer.documents) passageCache.set(doc.id, { version: doc.version, blocks: doc.blocks });
    for (const id of answer.missing) passageCache.delete(id);
  } catch {
    // The view can't search or read without passages: fall back to a full refresh that carries them.
    for (const doc of wanted) if (passagesAsked.get(doc.id) === doc.version) passagesAsked.delete(doc.id);
    await refreshLibrary({ full: true });
    return;
  }
  for (const doc of wanted) if (passagesAsked.get(doc.id) === doc.version) passagesAsked.delete(doc.id);
  state = { ...state, ...withPassages(state.documents) }; emit();
}
// recheck asks the library to run Library health's file checks now instead of within the minute.
// full asks for every document's passages and stored versions in the snapshot itself.
export async function refreshLibrary({ initial = false, recheck = false, full = false } = {}) {
  recheckNext ||= recheck;
  if (fixture || (pending && !initial)) return;
  const asked = recheckNext;
  recheckNext = false;
  try {
    const snapshot = await brainRequest("snapshot", { revision: initial || full ? "" : revision, recheck: asked, passages: full });
    if (pending) return;
    if (snapshot.unchanged) {
      // Nothing changed since the snapshot this window has: keep its state and skip a render.
      revision = snapshot.revision;
      if (state.error) { state = { ...state, error: null }; emit(); }
      await fetchPassages();
      return;
    }
    revision = snapshot.revision || "";
    const documents = snapshot.documents || [];
    if (full) keepPassages(documents);
    dropRemoved(documents);
    const priorTasks = new Map(state.tasks.map(item => [item.id, item.state]));
    const activeBrainId = (initial ? snapshot.activeBrainId : state.activeBrainId) || snapshot.brains[0]?.id || "";
    state = { ...state, ...snapshot, ...withPassages(documents), activeBrainId: snapshot.brains.some(item => item.id === activeBrainId) ? activeBrainId : snapshot.brains[0]?.id || "", views: initial ? snapshot.views || {} : state.views, loaded: true, error: null };
    for (const task of state.tasks) if (task.state === "done" && priorTasks.get(task.id) === "running") {
      if (task.result?.reportId) state = { ...state, views: { ...state.views, [task.brainId]: { ...viewFor(state, task.brainId), section: "reports", reportId: task.result.reportId } } };
      if (task.result?.brainId) state = { ...state, activeBrainId: task.result.brainId };
    }
    emit();
  } catch (error) {
    // After a failed refresh nothing here is trusted: the next one asks for the whole library.
    revision = "";
    recheckNext ||= asked;
    state = { ...state, loaded: true, error: error.message }; emit();
    return;
  }
  await fetchPassages();
}
export function subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); }
export const getState = () => state;
export function useSecondBrain() { return useSyncExternalStore(subscribe, getState, getState); }
export function resetSecondBrainStore() { clearInterval(poll); clearTimeout(preferencesTimer); poll = null; state = initialState(); loading = null; fixture = false; revision = ""; recheckNext = false; passageCache.clear(); passagesAsked.clear(); historyCache.clear(); emit(); }
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
  return { documents, citedBy, collectionMembers, citationsByDoc, reportCitations, brainNames: Object.fromEntries(current.brains.map(brain => [brain.id, brain.name])), folderRules: Object.fromEntries(current.brains.map(brain => [brain.id, brain.settings?.folderRules || {}])) };
}
export const documentById = (current, id) => current.documents.find(doc => doc.id === id);
export const blockById = (doc, blockId) => doc?.blocks?.find(block => block.id === blockId);
// The sample library keeps passages and versions on each document; the library sends counts.
export const passageCount = doc => doc.passageCount ?? doc.blocks?.length ?? 0;
export const historySize = doc => doc.historySize ?? (doc.versions?.length ? new TextEncoder().encode(JSON.stringify(doc.versions)).length : 0);

function cachedHistory(doc) {
  if (fixture) return doc.versions || [];
  const cached = historyCache.get(doc.id);
  if (cached?.version === doc.version) return cached.versions;
  return doc.versionCount === 0 ? [] : null;
}
// A document's stored older versions ({ hash, at, note, blocks }), fetched once per document version.
export async function loadHistory(doc) {
  const cached = cachedHistory(doc);
  if (cached) return cached;
  const answer = await brainRequest("passages", { ids: [doc.id], history: true });
  const item = answer.documents[0];
  if (!item) throw new Error("This document is no longer in the library.");
  historyCache.set(item.id, { version: item.version, versions: item.versions });
  return item.versions;
}
// For the reader's version list and the version dialogs: { versions, loading, error }.
export function useDocumentHistory(doc) {
  const key = doc ? `${doc.id}\u0001${doc.version}` : "";
  const [loaded, setLoaded] = useState({ key: "", versions: [], error: "" });
  const known = doc ? cachedHistory(doc) : [];
  useEffect(() => {
    if (!doc || cachedHistory(doc)) return undefined;
    let active = true;
    loadHistory(doc).then(versions => { if (active) setLoaded({ key, versions, error: "" }); }, error => { if (active) setLoaded({ key, versions: [], error: error.message }); });
    return () => { active = false; };
    // The key holds the document's ID and version, the only inputs that change the result.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  if (known) return { versions: known, loading: false, error: "" };
  if (loaded.key === key) return { versions: loaded.versions, loading: false, error: loaded.error };
  return { versions: [], loading: true, error: "" };
}

// action is an optional { label, run } shown as a button on the notice.
export function notify(message, tone = "success", action = null) { set({ toast: { id: uid("toast"), message, tone, ...(action ? { action } : {}) } }); }
export function dismissToast() { set({ toast: null }); }

// Removes a collection, saved search or research job and offers Undo, which puts the same
// object back at its old place. The usual write path then stores it again under its ID.
function removeWithUndo(kind, id, noun) {
  const index = state[kind].findIndex(item => item.id === id);
  if (index < 0) return;
  const removed = state[kind][index];
  set(current => ({ ...current, [kind]: current[kind].filter(item => item.id !== id) }));
  notify(`Deleted ${noun} “${removed.name}”.`, "success", { label: "Undo", run: () => {
    set(current => current[kind].some(item => item.id === id) ? current : { ...current, [kind]: current[kind].toSpliced(Math.min(index, current[kind].length), 0, removed) });
    dismissToast();
  } });
}
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
export function deleteSavedSearch(id) { removeWithUndo("savedSearches", id, "saved search"); }

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
export function deleteCollection(id) { removeWithUndo("collections", id, "collection"); }
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
// Saves a folder's search rule in the brain's settings; the library re-indexes nothing but
// the files that move in or out of search, and never touches the files.
export function setFolderRule(brainId, folder, rule) {
  updateBrain(brainId, current => ({ settings: { ...current.settings, folderRules: withFolderRule(current.settings?.folderRules || {}, folder, rule) } }));
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

export function updateReport(id, patch) { set(current => ({ ...current, reports: current.reports.map(report => report.id === id ? { ...report, ...(typeof patch === "function" ? patch(report) : patch) } : report) })); }
export function updateClaim(reportId, claimId, patch) { updateReport(reportId, report => ({ claims: report.claims.map(claim => claim.id === claimId ? { ...claim, ...patch } : claim) })); }
export function updateReportCitation(reportId, citationId, patch) { updateReport(reportId, report => ({ citations: report.citations.map(citation => citation.id === citationId ? { ...citation, ...patch } : citation) })); }

export function resolveHealth(ids) { if (!fixture) enqueue("ignore_health", { ids: [].concat(ids) }); const list = new Set([].concat(ids)); set(current => ({ ...current, health: current.health.filter(issue => !list.has(issue.id)) })); }
// Marks olderId as replaced by newerId in the brain's portable metadata. Report files are not changed.
export function confirmReplacement(olderId, newerId) {
  const replacedBy = newerId ? { docId: newerId, at: new Date().toISOString() } : undefined;
  if (!fixture) enqueue(newerId ? "confirm_replacement" : "clear_replacement", newerId ? { olderId, newerId } : { docId: olderId });
  set(current => ({
    ...current,
    documents: current.documents.map(doc => { if (doc.id !== olderId) return doc; const next = { ...doc, replacedBy }; if (!replacedBy) delete next.replacedBy; return next; }),
    health: newerId ? current.health.filter(issue => !(issue.kind === "family" && issue.docId === olderId)) : current.health,
  }));
}
// Marks a detected dated report series "Not a series" (or undoes it) in the brain's portable metadata.
export function markSeries(seriesId, notSeries = true) {
  if (!fixture) enqueue("mark_series", { seriesId, notSeries });
  set(current => ({ ...current, series: (current.series || []).map(item => item.id === seriesId ? { ...item, notSeries } : item) }));
}
export function saveJob(job) { job = { ...job, provider: job.provider || researchProvider }; set(current => ({ ...current, jobs: current.jobs.some(item => item.id === job.id) ? current.jobs.map(item => item.id === job.id ? job : item) : [...current.jobs, job] })); }
export function removeJob(id) { removeWithUndo("jobs", id, "research job"); }
export function captureSource({ title, url, excerpt }) {
  return startTask({ kind: "capture", label: "Capturing source", title, url, excerpt });
}

// Projects and topic names. Merging keeps the absorbed project's name as an alias.
export function saveProject(project) { set(current => ({ ...current, projects: current.projects.some(item => item.id === project.id) ? current.projects.map(item => item.id === project.id ? project : item) : [...current.projects, project] })); }
export function mergeProjects(from, into) {
  const merged = mergedProject(into, from, state.activeBrainId, uid);
  set(current => ({ ...current, projects: [...current.projects.filter(item => item.id !== merged.id && item.id !== from.id), merged] }));
  notify(`“${from.name}” is now part of “${merged.name}”.`);
  return merged;
}
export function setDocumentProject(docId, name) { updateDocument(docId, { project: name }); }
export function nameTopic(topic, name) {
  const record = { id: topic.id, brainId: state.activeBrainId, name, members: topic.members };
  set(current => ({ ...current, topics: name ? [...current.topics.filter(item => item.id !== topic.id), record] : current.topics.filter(item => item.id !== topic.id) }));
}

// The key changes whenever something that affects projects, connections or topics changes.
export function graphKey(current, brainId = current.activeBrainId) {
  const brain = current.brains.find(item => item.id === brainId);
  const projects = JSON.stringify(current.projects.filter(item => item.brainId === brainId));
  const topics = JSON.stringify(current.topics.filter(item => item.brainId === brainId));
  const chosen = current.documents.filter(doc => doc.brainId === brainId && doc.project).map(doc => `${doc.id}=${doc.project}`).join("|");
  return [brainId, brain?.index.generation, brain?.settings.semantic, current.reports.filter(item => item.brainId === brainId).length, projects, topics, chosen].join("\u0001");
}

// Loads one derived view (projects, topics, connections) from the library service.
export function useBrainData(action, params, key, enabled = true) {
  const [result, setResult] = useState({ data: null, error: "", loading: enabled });
  useEffect(() => {
    if (!enabled) return undefined;
    let active = true;
    setResult(previous => ({ ...previous, loading: true, error: "" }));
    const load = fixture
      ? import("./secondBrainSample.js").then(sample => sample.sampleGraphData(action, params))
      : flushLibraryWrites().then(() => brainRequest(action, params));
    load.then(data => { if (active) setResult({ data, error: "", loading: false }); })
      .catch(error => { if (active) setResult({ data: null, error: error.message, loading: false }); });
    return () => { active = false; };
    // The key captures every input that changes the result.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, key, enabled]);
  return result;
}
export async function backfillProvenance(brainId = state.activeBrainId) {
  if (fixture) { notify("This visual fixture does not execute backend operations.", "info"); return null; }
  try {
    const result = await brainRequest("backfill_provenance", { brainId });
    await refreshLibrary();
    notify(result.created || result.updated ? `Found the history of ${result.created + result.updated} report${result.created + result.updated === 1 ? "" : "s"}.` : "Every report with a known history already shows it.");
    return result;
  } catch (error) { notify(error.message, "danger"); return null; }
}
// Read-only: counts the absolute links inside the brain that the repair would make relative.
export async function previewAbsoluteLinks(brainId = state.activeBrainId) {
  if (fixture) return { count: 0, documents: 0, missingTargets: 0, temporary: 0, outside: 0, changes: [] };
  return brainRequest("absolute_links_preview", { brainId });
}
export async function restrictBrainAccess(brainId = state.activeBrainId) {
  if (fixture) { notify("This visual fixture does not execute backend operations.", "info"); return null; }
  try {
    const result = await brainRequest("restrict_access", { brainId });
    await refreshLibrary();
    notify(result.remaining.length ? "Raticode could not make every folder private. Another account may own it; change its permissions in a terminal." : "Only your account can read this brain now.", result.remaining.length ? "danger" : undefined);
    return result;
  } catch (error) { notify(error.message, "danger"); return null; }
}
