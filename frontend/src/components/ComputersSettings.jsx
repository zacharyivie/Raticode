import { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { apiJson } from "../lib/api.js";
import { NumberControl } from "./SettingControls.jsx";
import { pollWithoutOverlap } from "../lib/statusPolling.js";
import {
  STRATEGY_OPTIONS,
  missingLabel,
  moveInOrder,
  orderedComputers,
  placementBody,
  placementDraft,
  pressureText,
  roomText,
  runningText,
  runningWork,
  stateText,
  withComputer,
  withShared,
  workText,
} from "../lib/computerPlacement.js";

const tones = {
  Online: "bg-success-soft text-success",
  "Back online": "bg-info-soft text-info",
  Offline: "bg-surface-muted text-muted",
};
const iconButton = "grid h-7 w-7 place-items-center rounded-md border border-line text-muted transition hover:bg-slate-50 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500 disabled:cursor-not-allowed disabled:opacity-40";

const send = (path, body) => apiJson(path, { method: "POST", body, failure: "Raticode couldn't save that change." });

function withoutKey(record, key) {
  const next = { ...record };
  delete next[key];
  return next;
}

function Refusal({ children }) {
  return children ? <p role="alert" className="mt-1.5 text-[11px] font-medium text-danger">{children}</p> : null;
}

// The most workers a computer's limit allows, as the backend's MAX_WORKERS.
const MAX_WORKERS = 16;

// Settings > Computers: each computer with its load and the user's order of them,
// then where Rem and each organization run their workers. This computer's row also
// sets its worker limit (assistant.maxWorkers) when onMaxWorkers is given.
export default function ComputersSettings({ maxWorkers = 0, onMaxWorkers } = {}) {
  const [computers, setComputers] = useState(null);
  const [placement, setPlacement] = useState(null);
  const [loadError, setLoadError] = useState("");
  // Unsaved edits by consumer: a manual placement with no computer yet, or one the
  // backend refused, so the user can fix it instead of losing it.
  const [drafts, setDrafts] = useState({});
  // Server refusals by row: "order:<computer ID>" or a consumer ID.
  const [refusals, setRefusals] = useState({});
  const [saving, setSaving] = useState("");

  const load = useCallback(async (signal) => {
    const [listed, placed] = await Promise.all(["/computers", "/computers/placement"].map(
      (path) => apiJson(path, { signal, cache: "no-store", failure: "Computers could not be loaded." }),
    ));
    setComputers(listed.computers ?? []);
    setPlacement(placed);
    setLoadError("");
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const stop = pollWithoutOverlap(load, { interval: 5000, signal: controller.signal, onError: (failure) => setLoadError(failure.message) });
    return () => { controller.abort(); stop(); };
  }, [load]);

  const refuse = (key, message) => setRefusals((current) => ({ ...current, [key]: message }));
  const clear = (key) => setRefusals((current) => withoutKey(current, key));

  async function move(id, step) {
    const order = moveInOrder(placement.order ?? [], id, step);
    if (!order) return;
    const key = `order:${id}`;
    setSaving(key);
    try {
      setPlacement(await send("/computers/placement", { order }));
      clear(key);
    } catch (failure) {
      refuse(key, failure.message);
    } finally {
      setSaving("");
    }
  }

  async function place(consumer, draft) {
    const body = placementBody(consumer.consumer, draft);
    setDrafts((current) => ({ ...current, [consumer.consumer]: draft }));
    if (!body) return;
    setSaving(consumer.consumer);
    try {
      setPlacement(await send("/computers/placement", body));
      setDrafts((current) => withoutKey(current, consumer.consumer));
      clear(consumer.consumer);
    } catch (failure) {
      refuse(consumer.consumer, failure.message);
    } finally {
      setSaving("");
    }
  }

  if (!computers || !placement) {
    return <p className="py-3 text-xs text-muted" role={loadError ? "alert" : "status"}>{loadError || "Loading computers…"}</p>;
  }
  const listed = orderedComputers(computers, placement.order);
  const ids = listed.map((computer) => computer.id);
  const names = Object.fromEntries(listed.map((computer) => [computer.id, computer.name || "Desktop"]));
  return (
    <div className="space-y-6 py-3" data-testid="computers-settings">
      {loadError ? <p role="alert" className="text-[11px] text-danger">{loadError}</p> : null}
      <section aria-labelledby="computers-list-heading">
        <h3 id="computers-list-heading" className="text-xs font-semibold text-ink">Your computers</h3>
        <p className="mt-1 max-w-[60ch] text-[10px] leading-4 text-muted">
          This computer and each desktop paired with it. Work starts on this computer for now. A paired desktop takes work only when you pick Run there on waiting work, or through an organization's fleet execution target. Set this computer's worker limit on its row. Change a paired desktop's limit in Raticode on that desktop.
        </p>
        <ol className="mt-3 divide-y divide-line rounded-lg border border-line">
          {listed.map((computer, index) => {
            const state = stateText(computer);
            const pressure = pressureText(computer);
            const work = workText(computer);
            const name = names[computer.id];
            return (
              <li key={computer.id} className="flex items-start gap-3 px-3 py-2.5" data-computer={computer.id}>
                <span aria-hidden="true" className="mt-0.5 w-4 text-right font-mono text-[11px] text-muted">{index + 1}</span>
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-xs font-medium text-ink">
                    {name}
                    {computer.this_computer ? <span className="text-[10px] font-normal text-muted">This computer</span> : null}
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${tones[state]}`}>{state}</span>
                  </p>
                  <p className="mt-0.5 text-[11px] text-muted" data-testid="computer-running">{runningText(computer)} · {roomText(computer)}</p>
                  {pressure ? <p className="text-[11px] text-muted">{pressure}</p> : null}
                  <RunningWork computer={computer} />
                  {work ? <p className={`mt-0.5 max-w-[60ch] text-[11px] ${work.tone === "warning" ? "text-warning" : "text-muted"}`} data-testid="computer-work">{work.text}</p> : null}
                  {computer.this_computer && onMaxWorkers ? <WorkerLimit computer={computer} value={maxWorkers} onCommit={onMaxWorkers} /> : null}
                  <Refusal>{refusals[`order:${computer.id}`]}</Refusal>
                </div>
                <div className="flex shrink-0 gap-1">
                  <button type="button" className={iconButton} aria-label={`Move ${name} up`} title="Move up" disabled={index === 0 || Boolean(saving)} onClick={() => move(computer.id, -1)}>
                    <ArrowUp aria-hidden="true" size={14} />
                  </button>
                  <button type="button" className={iconButton} aria-label={`Move ${name} down`} title="Move down" disabled={index === listed.length - 1 || Boolean(saving)} onClick={() => move(computer.id, 1)}>
                    <ArrowDown aria-hidden="true" size={14} />
                  </button>
                </div>
              </li>
            );
          })}
        </ol>
      </section>
      <section aria-labelledby="computers-placement-heading">
        <h3 id="computers-placement-heading" className="text-xs font-semibold text-ink">Where work runs</h3>
        <p className="mt-1 max-w-[60ch] text-[10px] leading-4 text-muted">
          A computer can be picked for two organizations, or for Rem and an organization, only when both mark it Shared.
        </p>
        <ul className="mt-3 divide-y divide-line rounded-lg border border-line">
          {placement.consumers.map((consumer) => (
            <ConsumerRow
              key={consumer.consumer}
              consumer={consumer}
              draft={drafts[consumer.consumer] ?? placementDraft(consumer)}
              computers={ids}
              names={names}
              busy={Boolean(saving)}
              refusal={refusals[consumer.consumer]}
              onChange={(draft) => place(consumer, draft)}
            />
          ))}
        </ul>
      </section>
    </div>
  );
}

// This computer's row expands to list what each running worker is. The backend lists
// at most 20, so a longer list says how many more run.
function RunningWork({ computer }) {
  const work = runningWork(computer);
  if (!work.length) return null;
  const more = Math.max(0, (computer.running_threads ?? 0) - work.length);
  return (
    <details className="group mt-1 text-[11px]" data-testid="computer-running-work">
      <summary className="w-fit cursor-pointer rounded text-muted transition hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
        What's running
      </summary>
      <ul className="mt-1 space-y-0.5 border-l border-line pl-2.5">
        {work.map((item, index) => (
          <li key={index} className="flex min-w-0 items-baseline gap-2">
            <span className="min-w-0 truncate text-ink" title={item.label}>{item.label}</span>
            <span className="shrink-0 text-[10px] text-muted">{item.kind}</span>
          </li>
        ))}
        {more ? <li className="text-[10px] text-muted">and {more} more</li> : null}
      </ul>
    </details>
  );
}

// This computer's worker limit: 0 is Auto, which picks a number from the cores and memory.
function WorkerLimit({ computer, onCommit, value }) {  const auto = computer.default_max_threads;
  const picks = auto == null ? "a number" : String(auto);
  return (
    <div className="mt-2 flex items-start gap-3 rounded-md bg-slate-50 px-2.5 py-2" data-setting-row="maxWorkers">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-ink">Worker limit</p>
        <p className="mt-0.5 max-w-[60ch] text-[10px] leading-4 text-muted" data-testid="worker-limit-help">
          How many workers run here at once. It counts Rem tasks, self-work, phone turns, organization workers, swarm turns and work from other desktops. Each idle worker uses about 0.5 GB of memory, and all of them share your provider's usage limits. 0 = Auto, which picks {picks} from this computer's cores and memory.
        </p>
      </div>
      <div className="w-20 shrink-0">
        <NumberControl label="Worker limit" value={value} min={0} max={MAX_WORKERS} suffix={value === 0 ? "Auto" : ""} onCommit={(next) => onCommit(Math.round(next))} />
      </div>
    </div>
  );
}

function ConsumerRow({ busy, computers, consumer, draft, names, onChange, refusal }) {
  const missing = consumer.missing_computers ?? [];
  // Unpaired computers still assigned stay listed, checked, so the user can drop them.
  const choices = [...computers, ...draft.computers.filter((id) => !computers.includes(id))];
  const label = (id) => names[id] ?? missingLabel(id);
  return (
    <li className="px-3 py-2.5" data-consumer={consumer.consumer}>
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-ink">{consumer.name}</p>
          <p className="text-[10px] text-muted">{consumer.kind === "rem" ? "Rem's tasks" : "Organization"}{consumer.saved ? "" : " · default"}</p>
        </div>
        <select
          aria-label={`Where ${consumer.name} runs`}
          className="h-8 w-56 shrink-0 rounded-md border border-line bg-white px-2 text-xs text-ink outline-none transition focus:border-brand disabled:cursor-wait disabled:opacity-50"
          disabled={busy}
          value={draft.strategy}
          onChange={(event) => {
            const strategy = event.target.value;
            onChange(strategy === "manual" ? { ...placementDraft(consumer), strategy } : { strategy, computers: [], shared: [] });
          }}
        >
          {STRATEGY_OPTIONS.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
        </select>
      </div>
      {draft.strategy === "manual" ? (
        <div className="mt-2 space-y-1 rounded-md bg-slate-50 px-2.5 py-2">
          {choices.map((id) => {
            const picked = draft.computers.includes(id);
            return (
              <div key={id} className="flex items-center gap-3 text-xs">
                <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
                  <input type="checkbox" className="accent-indigo-600" checked={picked} disabled={busy} aria-label={`${label(id)} for ${consumer.name}`} onChange={(event) => onChange(withComputer(draft, id, event.target.checked))} />
                  <span className="truncate">{label(id)}</span>
                  {missing.includes(id) ? <span className="text-[10px] font-medium text-warning">missing</span> : null}
                </label>
                {picked ? (
                  <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-[11px] text-muted">
                    <input type="checkbox" className="accent-indigo-600" checked={draft.shared.includes(id)} disabled={busy} aria-label={`Share ${label(id)} for ${consumer.name}`} onChange={(event) => onChange(withShared(draft, id, event.target.checked))} />
                    Shared
                  </label>
                ) : null}
              </div>
            );
          })}
          {draft.computers.length ? null : <p className="text-[11px] text-muted">Pick at least one computer. Nothing is saved until you do.</p>}
        </div>
      ) : null}
      {missing.length ? (
        <p className="mt-1.5 text-[11px] text-warning">
          Missing: {missing.map(label).join(", ")}. No longer paired, so work can't run there. Uncheck it to save this placement again.
        </p>
      ) : null}
      {consumer.notice ? <p className="mt-1.5 max-w-[60ch] text-[11px] text-warning" data-testid="placement-notice">{consumer.notice}</p> : null}
      <Refusal>{refusal}</Refusal>
    </li>
  );
}
