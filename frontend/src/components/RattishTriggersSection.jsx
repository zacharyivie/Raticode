import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CalendarClock, Check, Copy, FileSearch, Pencil, Plus, Trash2, Webhook, X } from "lucide-react";
import { startPolling } from "../lib/refresh.js";
import {
  MISSED_OPTIONS,
  OVERLAP_OPTIONS,
  RUNS_ONLY_WHILE_RUNNING,
  draftFromTrigger,
  emptyTriggerDraft,
  formatWhen,
  isProblemStatus,
  schedulerSummary,
  statusLabel,
  triggerBodyFromDraft,
  triggerDetail,
  triggerPolicyText,
  triggerTitle,
  triggersApi,
  webhookCurl,
} from "../lib/rattishTriggers.js";

const KIND_CHOICES = [
  ["cron", "Schedule", CalendarClock],
  ["file", "File change", FileSearch],
  ["webhook", "Webhook", Webhook],
];
const KIND_ICONS = Object.fromEntries(KIND_CHOICES.map(([kind, , Icon]) => [kind, Icon]));
const INPUT_CLASS =
  "mt-1 h-9 w-full rounded-lg border border-line bg-white px-2.5 text-[13px] text-ink outline-none transition focus:border-indigo-500";
const SMALL_BUTTON_CLASS =
  "inline-flex h-7 items-center gap-1 rounded-md border border-line bg-white px-2 text-[11px] font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50";

function FieldLabel({ children, label }) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-muted">{label}</span>
      {children}
    </label>
  );
}

function StatusPill({ status }) {
  const tone = isProblemStatus(status)
    ? "border-amber-500/60 bg-amber-500/10 text-amber-700 dark:text-amber-300"
    : status === "passed"
      ? "border-emerald-500/60 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
      : "border-line bg-slate-100 text-slate-700";
  return (
    <span className={`inline-flex shrink-0 items-center rounded-full border px-1.5 py-px text-[10px] font-semibold ${tone}`}>
      {statusLabel(status)}
    </span>
  );
}

function CopyButton({ label, value }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className={SMALL_BUTTON_CLASS}
      title={label}
      type="button"
      onClick={async () => {
        await navigator.clipboard?.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? <Check size={12} /> : <Copy size={12} />}
      {copied ? "Copied" : label}
    </button>
  );
}

function SchedulerNotice({ scheduler }) {
  const summary = schedulerSummary(scheduler);
  return (
    <div
      className={`rounded-lg border px-3 py-2.5 text-xs leading-5 ${
        summary.running ? "border-line bg-slate-50" : "border-amber-500/60 bg-amber-500/10"
      }`}
      role="status"
    >
      <div className="flex items-start gap-2 font-semibold text-ink">
        <span
          aria-hidden="true"
          className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${summary.running ? "bg-emerald-500" : "bg-amber-500"}`}
        />
        <span>{summary.text}</span>
      </div>
      <p className="mt-1 text-muted">{RUNS_ONLY_WHILE_RUNNING}</p>
      {scheduler?.webhookError ? (
        <p className="mt-1 text-amber-700 dark:text-amber-300">{scheduler.webhookError}</p>
      ) : null}
    </div>
  );
}

function TokenReveal({ reveal, onDismiss }) {
  return (
    <div className="space-y-2 rounded-lg border border-indigo-500/50 bg-indigo-500/5 p-3 text-xs">
      <div className="flex items-start justify-between gap-2">
        <p className="font-semibold text-ink">Save this token now. It can&apos;t be shown again.</p>
        <button aria-label="Dismiss token" className="text-muted hover:text-ink" type="button" onClick={onDismiss}>
          <X size={14} />
        </button>
      </div>
      <code className="block break-all rounded-md bg-slate-950 p-2 font-mono text-[11px] text-slate-100">{reveal.token}</code>
      <div className="flex flex-wrap gap-1.5">
        <CopyButton label="Copy token" value={reveal.token} />
        {reveal.url ? <CopyButton label="Copy curl" value={webhookCurl(reveal.url, reveal.token)} /> : null}
      </div>
      <p className="leading-5 text-muted">
        Send it as an <span className="font-mono">Authorization: Bearer</span> header. Only programs on this computer can reach the webhook.
      </p>
    </div>
  );
}

function CronPreview({ expression, timezone }) {
  const [result, setResult] = useState({ nextRuns: [], error: "" });
  useEffect(() => {
    if (!expression.trim()) {
      setResult({ nextRuns: [], error: "" });
      return undefined;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      triggersApi.previewCron(expression, timezone).then(
        (payload) => !cancelled && setResult({ nextRuns: payload.nextRuns, error: "" }),
        (error) => !cancelled && setResult({ nextRuns: [], error: error.message }),
      );
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [expression, timezone]);
  if (result.error) return <p className="text-xs leading-5 text-amber-700 dark:text-amber-300">{result.error}</p>;
  if (!result.nextRuns.length) return null;
  return (
    <p className="text-xs leading-5 text-muted">
      Next: {result.nextRuns.map(formatWhen).join(", ")}
    </p>
  );
}

function TriggerForm({ defaultTimezone, editing, showInputs, workflowId, onCancel, onSaved }) {
  const [draft, setDraft] = useState(() =>
    editing ? draftFromTrigger(editing) : emptyTriggerDraft("cron", defaultTimezone),
  );
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const update = (patch) => setDraft((current) => ({ ...current, ...patch }));
  const timezones = useMemo(() => Intl.supportedValuesOf?.("timeZone") ?? [], []);

  const save = async () => {
    setError("");
    setSaving(true);
    try {
      const body = triggerBodyFromDraft(draft);
      const payload = editing
        ? await triggersApi.update(editing.id, body)
        : await triggersApi.create(workflowId, draft.kind, body);
      onSaved(payload);
    } catch (saveError) {
      setError(saveError.message);
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3 rounded-lg border border-line bg-slate-50 p-3">
      {editing ? (
        <p className="text-xs font-semibold text-ink">Edit {triggerTitle(editing).toLowerCase()}</p>
      ) : (
        <div aria-label="Trigger type" className="grid grid-cols-3 gap-1" role="radiogroup">
          {KIND_CHOICES.map(([kind, label, Icon]) => (
            <button
              key={kind}
              aria-checked={draft.kind === kind}
              className={`flex h-14 flex-col items-center justify-center gap-1 rounded-lg border text-[11px] font-medium transition ${
                draft.kind === kind
                  ? "border-indigo-500 bg-indigo-500/10 text-ink"
                  : "border-line bg-white text-muted hover:text-ink"
              }`}
              role="radio"
              type="button"
              onClick={() => update({ kind })}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
        </div>
      )}
      <FieldLabel label="Name (optional)">
        <input
          className={INPUT_CLASS}
          value={draft.label}
          onChange={(event) => update({ label: event.target.value })}
        />
      </FieldLabel>
      {draft.kind === "cron" ? (
        <>
          <FieldLabel label="Cron expression">
            <input
              className={`${INPUT_CLASS} font-mono`}
              placeholder="0 9 * * 1-5"
              spellCheck={false}
              value={draft.expression}
              onChange={(event) => update({ expression: event.target.value })}
            />
          </FieldLabel>
          <p className="text-xs leading-5 text-muted">Minute, hour, day of month, month, weekday.</p>
          <FieldLabel label="Time zone">
            <input
              className={INPUT_CLASS}
              list="rattish-trigger-timezones"
              spellCheck={false}
              value={draft.timezone}
              onChange={(event) => update({ timezone: event.target.value })}
            />
          </FieldLabel>
          <datalist id="rattish-trigger-timezones">
            {timezones.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
          <CronPreview expression={draft.expression} timezone={draft.timezone} />
          <FieldLabel label="If a scheduled time is missed">
            <select
              className={INPUT_CLASS}
              value={draft.missed}
              onChange={(event) => update({ missed: event.target.value })}
            >
              {MISSED_OPTIONS.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </FieldLabel>
        </>
      ) : null}
      {draft.kind === "file" ? (
        <>
          <FieldLabel label="File or folder to watch">
            <input
              className={INPUT_CLASS}
              placeholder="inbox or /absolute/path"
              spellCheck={false}
              value={draft.path}
              onChange={(event) => update({ path: event.target.value })}
            />
          </FieldLabel>
          <div className="grid grid-cols-2 gap-2">
            <FieldLabel label="File pattern">
              <input
                className={`${INPUT_CLASS} font-mono`}
                spellCheck={false}
                value={draft.glob}
                onChange={(event) => update({ glob: event.target.value })}
              />
            </FieldLabel>
            <FieldLabel label="Debounce (seconds)">
              <input
                className={INPUT_CLASS}
                inputMode="decimal"
                value={draft.debounceSeconds}
                onChange={(event) => update({ debounceSeconds: event.target.value })}
              />
            </FieldLabel>
          </div>
          <label className="flex items-center justify-between gap-3 rounded-lg border border-line bg-white px-3 py-2">
            <span className="text-[13px] font-medium text-slate-700">Include subfolders</span>
            <input
              checked={draft.recursive}
              className="h-4 w-4 accent-indigo-600"
              type="checkbox"
              onChange={(event) => update({ recursive: event.target.checked })}
            />
          </label>
          <p className="text-xs leading-5 text-muted">
            Starts after the files stay quiet for the debounce time. Changes inside .git, .raticode and node_modules are ignored.
          </p>
        </>
      ) : null}
      {draft.kind === "webhook" ? (
        <p className="text-xs leading-5 text-muted">
          Gets a local URL and a secret token. Only programs on this computer can call it.
        </p>
      ) : null}
      <FieldLabel label="If this workflow is already running">
        <select
          className={INPUT_CLASS}
          value={draft.overlap}
          onChange={(event) => update({ overlap: event.target.value })}
        >
          {OVERLAP_OPTIONS.map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
      </FieldLabel>
      {showInputs ? (
        <FieldLabel label="Workflow inputs (JSON)">
          <textarea
            className={`${INPUT_CLASS} h-20 py-2 font-mono text-[12px]`}
            placeholder='{"topic": "news"}'
            spellCheck={false}
            value={draft.inputs}
            onChange={(event) => update({ inputs: event.target.value })}
          />
        </FieldLabel>
      ) : null}
      {error ? <p className="text-xs leading-5 text-amber-700 dark:text-amber-300" role="alert">{error}</p> : null}
      <div className="flex justify-end gap-1.5">
        <button className={SMALL_BUTTON_CLASS} type="button" onClick={onCancel}>Cancel</button>
        <button
          className="inline-flex h-7 items-center rounded-md bg-indigo-600 px-3 text-[11px] font-semibold text-white transition hover:bg-indigo-700 disabled:opacity-50"
          disabled={saving}
          type="button"
          onClick={save}
        >
          {saving ? "Saving…" : editing ? "Save" : "Add trigger"}
        </button>
      </div>
    </div>
  );
}

function TriggerCard({ scheduler, trigger, onChanged, onEdit, onRevealToken }) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [error, setError] = useState("");
  const Icon = KIND_ICONS[trigger.kind];
  const watchError = scheduler?.watchErrors?.[trigger.id];
  const latest = trigger.latestEvent;

  const act = async (action) => {
    setError("");
    try {
      await action();
      onChanged();
    } catch (actionError) {
      setError(actionError.message);
    }
  };

  return (
    <article aria-label={triggerTitle(trigger)} className="space-y-1.5 rounded-lg border border-line p-3">
      <div className="flex items-center gap-2">
        <Icon className="shrink-0 text-muted" size={14} />
        <h4 className="min-w-0 flex-1 truncate text-[13px] font-semibold text-ink">{triggerTitle(trigger)}</h4>
        <label className="flex shrink-0 items-center gap-1.5 text-[11px] font-medium text-muted">
          {trigger.enabled ? "On" : "Off"}
          <input
            aria-label={`${triggerTitle(trigger)} enabled`}
            checked={trigger.enabled}
            className="h-3.5 w-3.5 accent-indigo-600"
            type="checkbox"
            onChange={(event) => act(() => triggersApi.update(trigger.id, { enabled: event.target.checked }))}
          />
        </label>
      </div>
      <p className="break-all font-mono text-[11px] leading-4 text-muted">{triggerDetail(trigger)}</p>
      <p className="text-[11px] leading-4 text-muted">{triggerPolicyText(trigger)}</p>
      {trigger.nextRunAt ? (
        <p className="text-[11px] leading-4 text-slate-700">Next: {formatWhen(trigger.nextRunAt)}</p>
      ) : null}
      {latest ? (
        <div className="flex items-center gap-1.5 text-[11px] leading-4 text-muted">
          <StatusPill status={latest.status} />
          <span>{formatWhen(latest.at)}</span>
        </div>
      ) : null}
      {watchError ? <p className="text-[11px] leading-4 text-amber-700 dark:text-amber-300">{watchError}</p> : null}
      {error ? <p className="text-[11px] leading-4 text-amber-700 dark:text-amber-300" role="alert">{error}</p> : null}
      <div className="flex flex-wrap gap-1.5 pt-1">
        {trigger.kind === "webhook" ? (
          <>
            <CopyButton label="Copy URL" value={trigger.webhookUrl ?? ""} />
            <button
              className={SMALL_BUTTON_CLASS}
              title="Replace the token. The old one stops working at once."
              type="button"
              onClick={() => act(async () => {
                const payload = await triggersApi.rotateToken(trigger.id);
                onRevealToken({ token: payload.token, url: trigger.webhookUrl });
              })}
            >
              New token
            </button>
          </>
        ) : null}
        <button className={SMALL_BUTTON_CLASS} type="button" onClick={() => onEdit(trigger)}>
          <Pencil size={12} />
          Edit
        </button>
        {confirmingDelete ? (
          <button
            className={`${SMALL_BUTTON_CLASS} border-amber-500/60 text-amber-700 dark:text-amber-300`}
            type="button"
            onClick={() => act(() => triggersApi.remove(trigger.id))}
          >
            Confirm delete
          </button>
        ) : (
          <button className={SMALL_BUTTON_CLASS} type="button" onClick={() => setConfirmingDelete(true)}>
            <Trash2 size={12} />
            Delete
          </button>
        )}
      </div>
    </article>
  );
}

function ActivityList({ events, triggers }) {
  const titles = useMemo(
    () => Object.fromEntries(triggers.map((trigger) => [trigger.id, triggerTitle(trigger)])),
    [triggers],
  );
  const shown = events.slice(0, 12);
  if (!shown.length) return null;
  const problems = events.filter((event) => isProblemStatus(event.status)).length;
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <h4 className="text-xs font-semibold text-ink">Recent activity</h4>
        {problems ? (
          <span className="text-[11px] font-medium text-amber-700 dark:text-amber-300">
            {problems} need{problems === 1 ? "s" : ""} attention
          </span>
        ) : null}
      </div>
      <ul className="space-y-1">
        {shown.map((event) => (
          <li key={event.id} className="rounded-md border border-line px-2 py-1.5 text-[11px] leading-4">
            <div className="flex items-center gap-1.5">
              <StatusPill status={event.status} />
              <span className="min-w-0 flex-1 truncate font-medium text-ink">{titles[event.triggerId] ?? event.triggerId}</span>
              <span className="shrink-0 text-muted">{formatWhen(event.at)}</span>
            </div>
            {event.message ? <p className="mt-1 text-muted">{event.message}</p> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RattishTriggersSection({ active = true, showInputs = false, workflowId }) {
  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [loginError, setLoginError] = useState("");
  const [form, setForm] = useState(null);
  const [reveal, setReveal] = useState(null);
  const generation = useRef(0);

  const refresh = useCallback(async () => {
    if (!workflowId) return;
    const current = ++generation.current;
    try {
      const payload = await triggersApi.load(workflowId);
      if (current === generation.current) {
        setData(payload);
        setLoadError("");
      }
    } catch (error) {
      if (current === generation.current) setLoadError(error.message);
      throw error;
    }
  }, [workflowId]);

  useEffect(() => {
    setData(null);
    setForm(null);
    setReveal(null);
  }, [workflowId]);

  useEffect(() => {
    if (!active || !workflowId) return undefined;
    return startPolling(refresh, { immediate: true, interval: 5000, label: "workflow triggers" });
  }, [active, refresh, workflowId]);

  const toggleLogin = async (enabled) => {
    setLoginError("");
    try {
      const payload = await triggersApi.setStartAtLogin(enabled);
      setData((current) => (current ? { ...current, login: payload.login } : current));
    } catch (error) {
      setLoginError(error.message);
    }
  };

  const triggers = data?.triggers ?? [];
  const saved = (payload) => {
    setForm(null);
    if (payload.token) {
      setReveal({ token: payload.token, url: payload.trigger?.webhookUrl });
    }
    void refresh().catch(() => {});
  };

  return (
    <section aria-label="Triggers" className="space-y-3 rounded-lg border border-line p-3" data-rattish-triggers="true">
      <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-muted">Triggers</h3>
      {loadError && !data ? (
        <p className="text-xs leading-5 text-amber-700 dark:text-amber-300" role="alert">{loadError}</p>
      ) : null}
      <SchedulerNotice scheduler={data?.scheduler} />
      <div>
        <label className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2">
          <span className="text-sm font-medium text-slate-700">Start at login</span>
          <input
            checked={Boolean(data?.login?.enabled)}
            className="h-4 w-4 accent-indigo-600"
            disabled={!data}
            type="checkbox"
            onChange={(event) => toggleLogin(event.target.checked)}
          />
        </label>
        <p className="mt-1.5 text-xs leading-5 text-muted">
          Starts the background scheduler when you log in, so triggers still fire after a reboot. No Raticode window is needed, but the computer has to be on and logged in.
        </p>
        {loginError ? <p className="mt-1 text-xs text-amber-700 dark:text-amber-300" role="alert">{loginError}</p> : null}
      </div>
      {reveal ? <TokenReveal reveal={reveal} onDismiss={() => setReveal(null)} /> : null}
      {triggers.map((trigger) =>
        form?.editing?.id === trigger.id ? (
          <TriggerForm
            key={trigger.id}
            defaultTimezone={data.defaultTimezone}
            editing={trigger}
            showInputs={showInputs}
            workflowId={workflowId}
            onCancel={() => setForm(null)}
            onSaved={saved}
          />
        ) : (
          <TriggerCard
            key={trigger.id}
            scheduler={data?.scheduler}
            trigger={trigger}
            onChanged={() => void refresh().catch(() => {})}
            onEdit={(editing) => setForm({ editing })}
            onRevealToken={setReveal}
          />
        ),
      )}
      {form && !form.editing ? (
        <TriggerForm
          defaultTimezone={data?.defaultTimezone ?? "UTC"}
          showInputs={showInputs}
          workflowId={workflowId}
          onCancel={() => setForm(null)}
          onSaved={saved}
        />
      ) : null}
      {!form ? (
        <button
          className="flex h-9 w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-line text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
          disabled={!data}
          type="button"
          onClick={() => setForm({})}
        >
          <Plus size={14} />
          Add trigger
        </button>
      ) : null}
      <ActivityList events={data?.events ?? []} triggers={triggers} />
    </section>
  );
}
