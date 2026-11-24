import { useEffect, useState, useSyncExternalStore } from "react";
import { Check, ChevronDown, ChevronRight, FileText, ListChecks, MoreHorizontal, Pencil, Plus, Replace, RotateCcw, Scale, ScanSearch, Trash2 } from "lucide-react";
import { changeRemDecisions, countRemDecisionPaths, getState, openTaskSheet, scanRemDecisions, settleRemDecisionEdit, subscribe, suggestMessage } from "../lib/remHomeStore.js";
import { DECISION_ANSWER_MAX, RECORD_SECTIONS, answerChange, cardLine, cardState, checkLine, closedLabel, decidedLine, decisionAnswer, decisionPlace, decisionsCount, editChanges, editState, editTitle, evidenceParts, fallbackDraft, groupDecisions, historyEntries, inProject, isRecord, liveDecision, looseQueued, looseWaiting, pathWarnings, planGroups, planLine, planQueuedLine, queuedCount, queuedLine, recordAddChange, recordDraft, recordEditChange, recordFileNote, recordReplaceChange, recordSections, removal, replacesLine, scopeChips, splitList, waitingCount, waitingEdits, askAgainTitle, askedLine, askedTitle, currentAnswer, reopenedLine } from "../lib/remDecisions.js";
import { RemMenu } from "./RemHomeParts.jsx";
import "./RemDecisions.css";

const OPEN_KEY = "raticode.rem.decisionsOpen";
const DECIDED_SHOWN = 5;

// The section starts open while something waits, unless the user folded it before.
function storedOpen(waiting) {
  try {
    const stored = window.localStorage?.getItem(OPEN_KEY);
    return stored ? stored === "open" : waiting > 0;
  } catch { return waiting > 0; }
}

// What the recommendation rests on, in one line: kind and reference, at most three.
// Each piece's excerpt shows on hover.
function EvidenceLine({ evidence }) {
  const { parts, more } = evidenceParts(evidence);
  if (!parts.length) return null;
  return <p className="rem-decision-evidence" aria-label="Evidence">
    {parts.map((part, index) => <span key={`${part.kind}-${index}`} title={part.excerpt ? `${part.ref}\n${part.excerpt}` : part.ref}>
      <b>{part.label}</b> <span>{part.ref}</span>
    </span>)}
    {more ? <span className="rem-decision-more">+{more} more</span> : null}
  </p>;
}

// The user's own answer (or a new answer for a decided one). What's typed stays as typed
// until Save or Enter; Escape or Cancel leaves without saving. Shift+Enter starts a new line.
function AnswerForm({ item, initial = "", label, onDone }) {
  const [draft, setDraft] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async event => {
    event?.preventDefault();
    const change = answerChange(draft, item);
    if (!change) { if (!draft.trim()) setError("Write your answer first."); else onDone(); return; }
    setBusy(true);
    const result = await changeRemDecisions(change);
    setBusy(false);
    if (result.ok) onDone();
    else setError(result.message);
  };
  return <form className="rem-decision-form" onSubmit={save} onKeyDown={event => {
    if (event.key === "Escape") { event.stopPropagation(); onDone(); }
  }}>
    <textarea aria-label={label} rows={2} maxLength={DECISION_ANSWER_MAX} value={draft} autoFocus placeholder="Your answer"
      onChange={event => { setDraft(event.target.value); if (error) setError(""); }}
      onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent?.isComposing) void save(event); }} />
    {error ? <p className="rem-decision-error" role="alert">{error}</p> : null}
    <div className="rem-decision-form-actions">
      <button type="submit" disabled={busy || !draft.trim()}>Save</button>
      <button type="button" onClick={onDone}>Cancel</button>
    </div>
  </form>;
}

// Use this / Change… / Turn down for a request that waits (or Rem's auto-mode answer).
function OpenActions({ item, onChange, onError, quietReject = true }) {
  const [busy, setBusy] = useState(false);
  const run = async change => {
    setBusy(true);
    onError("");
    const result = await changeRemDecisions(change);
    setBusy(false);
    if (!result.ok) onError(result.message);
  };
  const canUse = Boolean(item.recommended || item.answer);
  // An Ask again card: turning it down keeps the answer in force, so it says that.
  const keep = Boolean(item.asked_again);
  return <div className="rem-decision-actions">
    {canUse ? <button type="button" data-primary="true" disabled={busy} onClick={() => void run({ action: "use", id: item.id })}
      title={item.status === "provisional" ? "Keep Rem's answer as your decision" : "Take Rem's recommendation"}><Check size={13} aria-hidden="true" />Use this</button> : null}
    <button type="button" disabled={busy} onClick={onChange}>{canUse ? "Change…" : keep ? "New answer…" : "Answer…"}</button>
    {quietReject || keep ? <button type="button" className="rem-decision-quiet" disabled={busy} onClick={() => void run({ action: "reject", id: item.id })}
      title={keep ? "Keep the current answer and close this question." : "Turn this request down. Rem won't use it."}>{keep ? "Keep current answer" : "Turn down"}</button> : null}
  </div>;
}

// Pills above a waiting question: Rem's auto-mode answer, a proposal to replace a decision.
function DecisionTags({ item, place }) {
  const tags = [];
  if (place) tags.push(<span key="place" className="rem-decision-place">{place}</span>);
  if (item.status === "provisional") tags.push(<span key="auto" className="rem-decision-pill" data-tone="auto" title="Self-work auto mode is on, so Rem applied its own recommendation. It stays in force until you confirm or change it.">Rem decided (auto mode)</span>);
  if (item.supersedes) tags.push(<span key="replace" className="rem-decision-pill" title={`Your decision ${item.supersedes} stays in force until you choose here.`}>Would replace {item.supersedes}</span>);
  const replaces = replacesLine(item);
  if (replaces) tags.push(<span key="replaces" className="rem-decision-pill" data-tone="conflict" title="These decisions stay in force until you choose here. Use this replaces all of them with the one below.">{replaces}</span>);
  if (isRecord(item)) tags.push(<span key="record" className="rem-decision-pill" data-tone="record" title="A design decision Raticode writes as a file in the project's repository.">Design decision</span>);
  return tags.length ? <div className="rem-decision-tags">{tags}</div> : null;
}

// Where a record applies (paths it is about, paths it rules out) and its written parts,
// each folded so the decision itself stays the first thing read.
function RecordParts({ item }) {
  const chips = scopeChips(item);
  const sections = recordSections(item);
  if (!chips.length && !sections.length) return null;
  return <>
    {chips.length ? <ul className="rem-decision-scope" aria-label="Where it applies">
      {chips.map(chip => <li key={`${chip.kind}-${chip.glob}`} data-kind={chip.kind} data-warn={chip.warn || undefined}
        title={[chip.kind === "forbids" ? `A change must never add or modify ${chip.glob} while this is in force.` : `Changes to ${chip.glob} are checked against this decision.`,
          chip.count && item.path_counts?.branch ? `Tracked files on ${item.path_counts.branch}: ${chip.count}.` : ""].filter(Boolean).join("\n")}>
        <span>{chip.label}</span>{chip.count ? <small className="rem-decision-scope-count">{chip.count}</small> : null}</li>)}
    </ul> : null}
    {sections.map(part => <details key={part.key} className="rem-decision-fold">
      <summary>{part.label}</summary>
      <p>{part.text}</p>
    </details>)}
  </>;
}

// Every piece of evidence with its excerpt, for an opened decision.
function EvidenceList({ evidence }) {
  const { parts } = evidenceParts(evidence, 10);
  if (!parts.length) return null;
  return <ul className="rem-decision-evidence-list" aria-label="Evidence">
    {parts.map((part, index) => <li key={`${part.kind}-${index}`}>
      <b>{part.label}</b> <span className="rem-decision-evidence-ref">{part.ref}</span>
      {part.excerpt ? <small>{part.excerpt}</small> : null}
    </li>)}
  </ul>;
}

// What an opened decision rests on: who decided it and when, its evidence, Raticode's last
// run of its check, and its history folded (date, who, what, and the note).
function DecisionFacts({ item }) {
  const decided = decidedLine(item);
  const history = historyEntries(item);
  return <>
    {decided ? <p className="rem-decision-decided">{decided}</p> : null}
    <EvidenceList evidence={item.evidence} />
    {item.last_check?.ran_at ? <p className="rem-decision-check" data-passed={item.last_check.passed ? "true" : "false"}>{checkLine(item)}</p> : null}
    {history.length ? <details className="rem-decision-fold rem-decision-history">
      <summary>History <small>{history.length}</small></summary>
      <ol aria-label="History">
        {history.map(entry => <li key={entry.key}>
          <time dateTime={entry.date}>{entry.date}</time>
          <span><b>{entry.actor}</b> {entry.action}{entry.note ? <small>{entry.note}</small> : null}</span>
        </li>)}
      </ol>
    </details> : null}
  </>;
}

// Who asked a waiting question, when, and what waits on it. Pressing it opens the task's
// sheet when a task asked or waits.
function AskedLine({ item }) {
  const line = askedLine(item);
  if (!line) return null;
  const opens = item.asked.opens;
  const waits = item.asked.waiting?.length ? "true" : undefined;
  const title = askedTitle(item) || undefined;
  return opens ? <button type="button" className="rem-decision-asked" data-waits={waits} title={title} onClick={() => openTaskSheet(opens)}>{line}</button>
    : <p className="rem-decision-asked" data-waits={waits} title={title}>{line}</p>;
}

function WaitingItem({ item, showPlace }) {
  const [changing, setChanging] = useState(false);
  const [error, setError] = useState("");
  const answer = decisionAnswer(item);
  const fix = Boolean(replacesLine(item));
  return <li className="rem-decision" data-status={item.status}>
    <DecisionTags item={item} place={showPlace ? decisionPlace(item) : ""} />
    <p className="rem-decision-question" title={`${item.id} · ${item.topic}`}>{item.question}</p>
    <AskedLine item={item} />
    {item.conflict ? <p className="rem-decision-conflict"><b>Conflict:</b> {item.conflict}</p> : null}
    {reopenedLine(item) ? <p className="rem-decision-reopened">{reopenedLine(item)}</p> : null}
    {currentAnswer(item) ? <p className="rem-decision-current"><small>Current answer</small> <span>{currentAnswer(item)}</span></p> : null}
    {answer ? <div className="rem-decision-answer">
      <small>{fix ? "Rem's fix" : item.status === "provisional" ? "Rem chose" : "Rem recommends"}</small>
      <strong>{answer}</strong>
      {item.why ? <span className="rem-decision-why">{item.why}</span> : null}
    </div> : item.tried ? <p className="rem-decision-tried"><b>Rem tried:</b> {item.tried}</p> : null}
    {isRecord(item) ? <RecordParts item={item} /> : null}
    <EvidenceLine evidence={item.evidence} />
    {changing ? <AnswerForm item={item} label={`Your answer: ${item.question}`} onDone={() => setChanging(false)} />
      : <OpenActions item={item} onChange={() => { setError(""); setChanging(true); }} onError={setError} />}
    {error && !changing ? <p className="rem-decision-error" role="alert">{error}</p> : null}
  </li>;
}

// Plan with me: the plan's title and how many of its questions are answered, then the ones
// waiting now. The rest come up, a few at a time, as these are answered.
function PlanGroup({ group, showPlace }) {
  const { plan, waiting } = group;
  const queued = planQueuedLine(plan);
  const done = plan.total ? Math.round((100 * (plan.answered || 0)) / plan.total) : 0;
  return <li className="rem-decision-plan" aria-label={`Plan: ${plan.title}`}>
    <div className="rem-decision-plan-head" title={plan.goal || plan.title}>
      <ListChecks size={13} aria-hidden="true" />
      <strong>{plan.title}</strong>
      <small>{planLine(plan)}</small>
    </div>
    <span className="rem-decision-plan-bar" aria-hidden="true"><span style={{ width: `${done}%` }} /></span>
    <ul className="rem-decisions-waiting">
      {waiting.map(item => <WaitingItem key={item.id} item={item} showPlace={showPlace} />)}
    </ul>
    {queued ? <p className="rem-decisions-queued">{queued}</p> : null}
  </li>;
}

// How Rem works here: one line that opens into who decided it, why and its history.
function DecidedRow({ item, showPlace }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState("");
  const [error, setError] = useState("");
  const act = async change => {
    setMode("");
    const result = await changeRemDecisions(change);
    setError(result.ok ? "" : result.message);
  };
  if (mode === "edit") {
    return <li className="rem-decision-row" data-editing="true">
      <p className="rem-decision-question">{item.question}</p>
      <AnswerForm item={item} initial={item.answer || ""} label={`New answer: ${item.question}`} onDone={() => setMode("")} />
    </li>;
  }
  const remove = removal(item);
  if (mode === "forget") {
    return <li className="rem-decision-row" data-confirming="true">
      <p>{remove.label} <strong>{item.question}</strong>? {remove.detail}</p>
      <div className="rem-task-confirm">
        <button type="button" data-danger="true" autoFocus onClick={() => void act({ action: remove.action, id: item.id })}>{remove.label}</button>
        <button type="button" onClick={() => setMode("")}>Keep</button>
      </div>
    </li>;
  }
  const place = showPlace ? decisionPlace(item) : "";
  return <li className="rem-decision-rule" data-open={open || undefined}>
    <div className="rem-decision-row">
      <button type="button" className="rem-decision-row-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={11} aria-hidden="true" /> : <ChevronRight size={11} aria-hidden="true" />}
        <span className="rem-decision-line">
          <span className="rem-decision-line-text"><span>{item.question}</span> <span aria-hidden="true">→</span> <strong>{decisionAnswer(item)}</strong></span>
          {place || item.decided_by === "rem" ? <small>{[place, item.decided_by === "rem" ? "Rem decided" : ""].filter(Boolean).join(" · ")}</small> : null}
          {item.last_check?.ran_at && !open ? <small className="rem-decision-check" data-passed={item.last_check.passed ? "true" : "false"}>{checkLine(item)}</small> : null}
        </span>
      </button>
      <RemMenu label={`${item.question} actions`} className="rem-decision-menu" trigger={<MoreHorizontal size={14} />} items={[
        { label: "Edit answer", icon: Pencil, onSelect: () => { setError(""); setMode("edit"); } },
        { label: "Ask again", icon: RotateCcw, title: askAgainTitle(item), onSelect: () => void act({ action: "reopen", id: item.id }) },
        { label: remove.label, icon: Trash2, danger: true, onSelect: () => setMode("forget") },
      ]} />
    </div>
    {error ? <p className="rem-decision-error" role="alert">{error}</p> : null}
    {open ? <div className="rem-decision-record-detail">
      <p className="rem-decision-place">{item.id}{place ? ` · ${place}` : ""}</p>
      {item.why ? <p className="rem-decision-why">{item.why}</p> : null}
      <DecisionFacts item={item} />
    </div> : null}
  </li>;
}

// A replaced or turned-down decision: struck through, and opens into its history.
function HistoryRow({ item, showPlace }) {
  const [open, setOpen] = useState(false);
  return <li className="rem-decision-rule" data-open={open || undefined}>
    <button type="button" className="rem-decision-row rem-decision-row-toggle" data-closed="true" aria-expanded={open} onClick={() => setOpen(!open)}>
      {open ? <ChevronDown size={11} aria-hidden="true" /> : <ChevronRight size={11} aria-hidden="true" />}
      <span className="rem-decision-line">
        <span className="rem-decision-line-text"><span>{item.question}</span>{decisionAnswer(item) ? <> <span aria-hidden="true">→</span> <span>{decisionAnswer(item)}</span></> : null}</span>
        <small>{[closedLabel(item), showPlace ? decisionPlace(item) : ""].filter(Boolean).join(" · ")}</small>
      </span>
    </button>
    {open ? <div className="rem-decision-record-detail">
      <p className="rem-decision-place">{item.id}</p>
      {item.why ? <p className="rem-decision-why">{item.why}</p> : null}
      <DecisionFacts item={item} />
    </div> : null}
  </li>;
}

const FORM_TITLES = Object.freeze({ edit: "Edit wording", replace: "Replace with a new decision", add: "Add a design decision" });
const SAVE_LABELS = Object.freeze({ edit: "Save", replace: "Replace", add: "Add" });

const COUNT_DELAY_MS = 400;

// The typed paths' warnings, counted on the landing branch a moment after typing stops: an
// Applies to path that matches no files, a Never touch path that matches files. Saving is
// never blocked by them.
function usePathWarnings(target, appliesText, forbidsText) {
  const [warnings, setWarnings] = useState([]);
  const key = target ? JSON.stringify(target) : "";
  useEffect(() => {
    const appliesTo = splitList(appliesText);
    const forbids = splitList(forbidsText);
    if (!key || (!appliesTo.length && !forbids.length)) { setWarnings([]); return undefined; }
    let live = true;
    const timer = setTimeout(() => {
      void countRemDecisionPaths(JSON.parse(key), appliesTo, forbids).then(counts => { if (live) setWarnings(pathWarnings(counts)); });
    }, COUNT_DELAY_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [key, appliesText, forbidsText]);
  return warnings;
}

// A record's parts as plain fields: edit its wording, replace it with a new decision, or add
// one. Every field keeps what was typed (lists stay comma-separated text) until Save; Escape
// or Cancel leaves without saving.
function RecordForm({ item, mode, project = "", onDone }) {
  const [draft, setDraft] = useState(() => recordDraft(item || {}));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const warnings = usePathWarnings(mode === "add" ? (project ? { project } : null) : item?.id ? { id: item.id } : null, draft.applies_to, draft.forbids);
  const set = key => event => { const value = event.target.value; setDraft(current => ({ ...current, [key]: value })); if (error) setError(""); };
  const save = async event => {
    event?.preventDefault();
    const change = mode === "add" ? recordAddChange(draft, project) : mode === "replace" ? recordReplaceChange(draft, item) : recordEditChange(draft, item);
    if (!change) { onDone(); return; }
    if (change.error) { setError(change.error); return; }
    setBusy(true);
    const result = await changeRemDecisions(change);
    setBusy(false);
    if (result.ok) onDone();
    else setError(result.message);
  };
  const field = (key, label, { rows = 0, max, hint = "" } = {}) => <label key={key} className="rem-decision-field">
    <span>{label}{hint ? <small> {hint}</small> : null}</span>
    {rows ? <textarea aria-label={label} rows={rows} maxLength={max} value={draft[key]} onChange={set(key)} />
      : <input aria-label={label} type="text" maxLength={max} value={draft[key]} onChange={set(key)} />}
  </label>;
  return <form className="rem-decision-form rem-decision-record-form" aria-label={FORM_TITLES[mode]} onSubmit={save} onKeyDown={event => {
    if (event.key === "Escape") { event.stopPropagation(); onDone(); }
  }}>
    <p className="rem-decision-form-title">{FORM_TITLES[mode]}{item?.id && mode !== "add" ? <small> {item.id}</small> : null}</p>
    {field("question", "What it's about", { max: 500 })}
    {field("answer", mode === "replace" ? "New decision" : "Decision", { rows: 2, max: DECISION_ANSWER_MAX })}
    {field("why", "Why", { rows: 2, max: 300 })}
    {RECORD_SECTIONS.map(({ key, label, max }) => field(key, label, { rows: 2, max }))}
    {field("applies_to", "Applies to", { hint: "paths, comma-separated" })}
    {field("forbids", "Never touch", { hint: "paths, comma-separated" })}
    {warnings.length ? <ul className="rem-decision-path-warnings" aria-label="Path warnings">
      {warnings.map(line => <li key={line}>{line}</li>)}
    </ul> : null}
    {field("signals", "Signal words", { hint: "comma-separated" })}
    {mode !== "add" ? field("note", "Note for the history", { max: 300, hint: "optional" }) : null}
    {error ? <p className="rem-decision-error" role="alert">{error}</p> : null}
    <div className="rem-decision-form-actions">
      <button type="submit" disabled={busy}>{SAVE_LABELS[mode]}</button>
      <button type="button" onClick={onDone}>Cancel</button>
    </div>
  </form>;
}

// A design decision in force: one line, and its record unfolded on click.
function RecordRow({ item, showPlace }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState("");
  const [error, setError] = useState("");
  const act = async change => {
    setMode("");
    const result = await changeRemDecisions(change);
    setError(result.ok ? "" : result.message);
  };
  const place = showPlace ? decisionPlace(item) : "";
  const remove = removal(item);
  const fileNote = recordFileNote(item);
  const flags = [place, item.decided_by === "rem" ? "Rem decided" : "", fileNote?.flag || "", item.edited_outside ? "Edited outside Raticode" : ""].filter(Boolean);
  return <li className="rem-decision-record" data-open={open || undefined}>
    <button type="button" className="rem-decision-record-toggle" aria-expanded={open} onClick={() => setOpen(!open)} title={`${item.id} · ${item.question}`}>
      {open ? <ChevronDown size={11} aria-hidden="true" /> : <ChevronRight size={11} aria-hidden="true" />}
      <span className="rem-decision-line">
        <span className="rem-decision-line-text"><strong>{decisionAnswer(item)}</strong></span>
        {flags.length ? <small>{flags.join(" · ")}</small> : null}
      </span>
    </button>
    {open ? <div className="rem-decision-record-detail">
      <div className="rem-decision-tags">
        <span className="rem-decision-pill" data-tone="confirmed">In force</span>
        <span className="rem-decision-place">{item.id}{item.decided_by === "rem" ? " · Rem decided" : ""}</span>
      </div>
      <p className="rem-decision-about">{item.question}</p>
      <p className="rem-decision-sentence">{decisionAnswer(item)}</p>
      {item.why ? <p className="rem-decision-why">{item.why}</p> : null}
      <RecordParts item={item} />
      {item.record_path ? <p className="rem-decision-file" title={item.record_commit ? `Commit ${item.record_commit}` : item.record_path}>
        <FileText size={12} aria-hidden="true" /><span>{item.record_path}</span>
      </p> : null}
      {item.edited_outside ? <p className="rem-decision-note">Edited outside Raticode. This shows the file's wording now.</p> : null}
      {fileNote ? <p className="rem-decision-note" data-tone="attention" data-kind={fileNote.kind}>{fileNote.text}</p> : null}
      <DecisionFacts item={item} />
      {mode === "edit" || mode === "replace" ? <RecordForm item={item} mode={mode} onDone={() => setMode("")} />
        : mode === "forget" ? <div className="rem-decision-confirm">
          <p>{remove.ask} {remove.detail}</p>
          <div className="rem-task-confirm">
            <button type="button" data-danger="true" autoFocus onClick={() => void act({ action: remove.action, id: item.id })}>{remove.label}</button>
            <button type="button" onClick={() => setMode("")}>Keep</button>
          </div>
        </div>
        : <div className="rem-decision-actions">
          <button type="button" onClick={() => { setError(""); setMode("edit"); }} title="Fix the wording of this decision. It stays the same decision."><Pencil size={12} aria-hidden="true" />Edit wording</button>
          <button type="button" onClick={() => { setError(""); setMode("replace"); }} title="Decide differently. This one closes and links to the new one."><Replace size={12} aria-hidden="true" />Replace with…</button>
          <button type="button" className="rem-decision-quiet" onClick={() => void act({ action: "reopen", id: item.id })} title={askAgainTitle(item)}>Ask again</button>
          <button type="button" className="rem-decision-quiet" data-tight="true" onClick={() => setMode("forget")} title={remove.label === "Retire" ? "Close this decision. Raticode rewrites its file with status: rejected and commits that on the landing branch." : undefined}>{remove.label}</button>
        </div>}
      {error ? <p className="rem-decision-error" role="alert">{error}</p> : null}
    </div> : null}
  </li>;
}

// A small fold inside the section (Decided past the first few, History).
function SubHeading({ label, count, open, onToggle }) {
  if (!onToggle) return <h4 className="rem-decisions-sub"><span>{label}</span><small>{count}</small></h4>;
  return <h4 className="rem-decisions-sub"><button type="button" aria-expanded={open} onClick={onToggle}>
    {open ? <ChevronDown size={11} aria-hidden="true" /> : <ChevronRight size={11} aria-hidden="true" />}<span>{label}</span><small>{count}</small>
  </button></h4>;
}

// Rem's decisions in the side pane, under Objectives: what waits for the user first, then
// the answers in force (how Rem works here, then the project's design decisions), then
// closed ones folded away. With a project open it lists that project's decisions (and
// those about the user); All projects lists every one.
export default function RemDecisions({ decisions, project = "" }) {
  const waitingAll = waitingCount(decisions);
  const [open, setOpen] = useState(() => storedOpen(waitingAll));
  const [all, setAll] = useState(false);
  const [showAllRules, setShowAllRules] = useState(false);
  const [showAllRecords, setShowAllRecords] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const scoped = Boolean(project) && !all;
  const groups = groupDecisions(decisions, { project, all: !scoped });
  const edits = waitingEdits(decisions, { project, all: !scoped });
  const elsewhere = scoped ? waitingAll - groups.waiting.length : 0;
  const plans = planGroups(decisions, { project, all: !scoped });
  const loose = looseWaiting(groups.waiting, plans);
  const queued = queuedLine(looseQueued(queuedCount(decisions, { project, all: !scoped }), plans));
  const hasOthers = Boolean(project) && (decisions?.items || []).some(item => !inProject(item, project));
  const toggle = next => {
    setOpen(next);
    try { window.localStorage?.setItem(OPEN_KEY, next ? "open" : "closed"); } catch { /* the choice just isn't kept */ }
  };
  const count = decisionsCount(decisions);
  const [scan, setScan] = useState({ busy: false, message: "", failed: false });
  const lookAgain = async () => {
    setScan({ busy: true, message: "", failed: false });
    const result = await scanRemDecisions(project);
    setScan({ busy: false, message: result.message, failed: !result.ok });
  };
  const rules = showAllRules ? groups.rules : groups.rules.slice(0, DECIDED_SHOWN);
  const records = showAllRecords ? groups.records : groups.records.slice(0, DECIDED_SHOWN);
  const showPlace = !scoped;
  const more = (list, shown, setShown) => list.length > DECIDED_SHOWN ? <button type="button" className="rem-decisions-more" onClick={() => setShown(!shown)}>
    {shown ? "Show fewer" : `Show all ${list.length}`}
  </button> : null;
  return <section className="rem-side-group rem-side-decisions" aria-label="Decisions">
    <div className="rem-side-group-head">
      <button type="button" className="rem-side-group-heading" aria-expanded={open} onClick={() => toggle(!open)}
        title="Questions Rem brought you, each with one recommended answer, and the answers Rem keeps using so it never asks twice.">
        {open ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
        <span>Decisions</span><small data-waiting={count ? "true" : undefined}>{count}</small>
      </button>
      {project && (hasOthers || all) ? <button type="button" className="rem-decisions-scope" aria-pressed={all} onClick={() => setAll(!all)}
        title={all ? "Show only this project's decisions" : "Show decisions for every project"}>{all ? "This project" : "All projects"}</button> : null}
    </div>
    {open ? <div className="rem-decisions">
      {groups.waiting.length || edits.length || queued || plans.length ? <>
        <SubHeading label="Waiting for you" count={groups.waiting.length + edits.length} />
        <ul className="rem-decisions-waiting">
          {edits.map(edit => <li key={edit.id}><DecisionEditCard card={edit} /></li>)}
          {loose.map(item => <WaitingItem key={item.id} item={item} showPlace={showPlace} />)}
          {plans.map(group => <PlanGroup key={group.plan.id} group={group} showPlace={showPlace} />)}
        </ul>
        {queued ? <p className="rem-decisions-queued" title="Each gets its card, oldest first, when one of the questions above is answered, turned down or forgotten.">{queued}</p> : null}
      </> : null}
      {elsewhere > 0 ? <button type="button" className="rem-decisions-elsewhere" onClick={() => setAll(true)}>
        {elsewhere} more waiting in other projects
      </button> : null}
      {groups.rules.length ? <>
        <SubHeading label="How Rem works here" count={groups.rules.length} />
        <ul className="rem-decisions-list">{rules.map(item => <DecidedRow key={item.id} item={item} showPlace={showPlace} />)}</ul>
        {more(groups.rules, showAllRules, setShowAllRules)}
      </> : null}
      {groups.records.length ? <>
        <SubHeading label="Design decisions" count={groups.records.length} />
        <ul className="rem-decisions-list">{records.map(item => <RecordRow key={item.id} item={item} showPlace={showPlace} />)}</ul>
        {more(groups.records, showAllRecords, setShowAllRecords)}
      </> : null}
      {adding ? <RecordForm mode="add" project={project} onDone={() => setAdding(false)} /> : null}
      {groups.history.length ? <>
        <SubHeading label="History" count={groups.history.length} open={historyOpen} onToggle={() => setHistoryOpen(!historyOpen)} />
        {historyOpen ? <ul className="rem-decisions-list">{groups.history.map(item => <HistoryRow key={item.id} item={item} showPlace={showPlace} />)}</ul> : null}
      </> : null}
      {!groups.waiting.length && !groups.decided.length && !groups.history.length && !elsewhere
        ? <p className="rem-decisions-empty">Nothing decided for this project yet.</p> : null}
      {project ? <div className="rem-decisions-scan">
        <div className="rem-decisions-tools">
          <button type="button" disabled={scan.busy} onClick={() => void lookAgain()}
            title="Read this project's CI files, agent docs, package and build files and git history for its check command, landing branch and decision folder. Nothing runs, and decisions in force stay as they are.">
            <ScanSearch size={12} aria-hidden="true" />{scan.busy ? "Looking…" : "Look for answers in this project"}
          </button>
          {!adding ? <button type="button" onClick={() => setAdding(true)}
            title="Write down a design decision for this project. Raticode keeps it as a file in the repository, and workers keep to it.">
            <Plus size={12} aria-hidden="true" />Add a record
          </button> : null}
        </div>
        {scan.message ? <small role="status" data-failed={scan.failed ? "true" : undefined}>{scan.message}</small> : null}
      </div> : null}
    </div> : null}
  </section>;
}

const decisionsOf = () => getState().decisions;
const decisionsLoaded = () => getState().decisionsLoaded;

// The question a task update brings, in Rem's chat. The message holds a snapshot; the live
// list says whether it still waits, so Use this and Change… show only while it does.
export function DecisionCard({ card }) {
  const decisions = useSyncExternalStore(subscribe, decisionsOf, decisionsOf);
  const loaded = useSyncExternalStore(subscribe, decisionsLoaded, decisionsLoaded);
  const [changing, setChanging] = useState(false);
  const [error, setError] = useState("");
  const view = cardState(card, liveDecision(decisions, card?.id), loaded);
  if (view.state === "fallback") {
    return <div className="rem-decision-card" data-fallback="true">
      <p className="rem-decision-card-title"><Scale size={13} aria-hidden="true" /><strong>{card.title || "Rem couldn't settle this. Here is what it found."}</strong></p>
      <p className="rem-decision-question">{card.question}</p>
      {card.found ? <div className="rem-decision-found"><small>What it found</small><p>{card.found}</p></div> : null}
      {card.candidate ? <p className="rem-decision-candidate"><small>A possible answer, not checked:</small> {card.candidate}</p> : null}
      <div className="rem-decision-actions">
        <button type="button" onClick={() => suggestMessage(fallbackDraft(card))} title="Write your answer in the message box">Answer it yourself…</button>
      </div>
    </div>;
  }
  if (view.state !== "open") {
    const line = cardLine(view);
    return <div className="rem-decision-card" data-settled={view.state}>
      <p className="rem-decision-question">{card.question}</p>
      <p className="rem-decision-settled">{view.state === "decided" ? <Check size={13} aria-hidden="true" /> : null}<span>{line}</span>
        {view.replaced ? <small> · replaced by {view.replaced}</small> : null}</p>
    </div>;
  }
  const item = view.item;
  const answer = decisionAnswer(item);
  return <div className="rem-decision-card" data-status={item.status}>
    <p className="rem-decision-card-title"><Scale size={13} aria-hidden="true" /><strong>{item.status === "provisional" ? "Rem decided this for now" : "Rem needs your decision"}</strong><span className="rem-decision-id">{item.id}</span></p>
    <DecisionTags item={item} place="" />
    <p className="rem-decision-question">{item.question}</p>
    {item.conflict ? <p className="rem-decision-conflict"><b>Conflict:</b> {item.conflict}</p> : null}
    {reopenedLine(item) ? <p className="rem-decision-reopened">{reopenedLine(item)}</p> : null}
    {currentAnswer(item) ? <p className="rem-decision-current"><small>Current answer</small> <span>{currentAnswer(item)}</span></p> : null}
    {answer ? <p className="rem-decision-recommends"><small>{replacesLine(item) ? "Rem's fix:" : item.status === "provisional" ? "Rem chose:" : "Rem recommends:"}</small> <strong>{answer}</strong></p>
      : item.tried ? <p className="rem-decision-tried"><b>Rem tried:</b> {item.tried}</p> : null}
    {item.why ? <p className="rem-decision-why">{item.why}</p> : null}
    <EvidenceLine evidence={item.evidence} />
    {changing ? <AnswerForm item={item} label={`Your answer: ${item.question}`} onDone={() => setChanging(false)} />
      : <OpenActions item={item} quietReject={false} onChange={() => { setError(""); setChanging(true); }} onError={setError} />}
    {error && !changing ? <p className="rem-decision-error" role="alert">{error}</p> : null}
  </div>;
}

// A change the user asked Rem for in chat: each part's before and after, applied only on OK.
// Under Rem's reply it stays as a record of what was pressed; in the Decisions list it shows
// while it waits.
export function DecisionEditCard({ card }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  if (!card?.id) return null;
  const state = editState(card);
  const press = async ok => {
    setBusy(ok ? "ok" : "cancel");
    setError("");
    const result = await settleRemDecisionEdit(card.id, ok);
    setBusy("");
    if (!result.ok) setError(result.message);
  };
  return <div className="rem-decision-card rem-decision-edit" data-waiting={state.waiting || undefined} data-settled={state.waiting ? undefined : card.status}>
    <p className="rem-decision-card-title"><Pencil size={13} aria-hidden="true" /><strong>{editTitle(card)}</strong><span className="rem-decision-id">{card.id}</span></p>
    {card.question ? <p className="rem-decision-question">{card.question}{card.project_name ? <small className="rem-decision-edit-place"> · {card.project_name}</small> : null}</p> : null}
    <dl className="rem-decision-edit-changes">
      {editChanges(card).map(change => <div key={change.field} className="rem-decision-edit-change">
        <dt>{change.label}</dt>
        <dd className="rem-decision-edit-before"><span className="rem-decision-edit-side">Before</span><span>{change.before}</span></dd>
        <dd className="rem-decision-edit-after"><span className="rem-decision-edit-side">After</span><span>{change.after}</span></dd>
      </div>)}
    </dl>
    {state.waiting ? <div className="rem-decision-actions">
      <button type="button" data-primary="true" disabled={Boolean(busy)} onClick={() => void press(true)}
        title="Make this change. A decision record's file follows on the landing branch."><Check size={13} aria-hidden="true" />{busy === "ok" ? "Applying…" : "OK"}</button>
      <button type="button" disabled={Boolean(busy)} onClick={() => void press(false)} title="Leave the decision as it is">Cancel</button>
    </div> : <p className="rem-decision-settled">{card.status === "applied" ? <Check size={13} aria-hidden="true" /> : null}<span>{state.line}</span></p>}
    {error ? <p className="rem-decision-error" role="alert">{error}</p> : null}
  </div>;
}
