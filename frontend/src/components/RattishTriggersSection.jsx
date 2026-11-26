import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CalendarClock, CircleAlert, Check, Copy, ExternalLink, FileSearch, Pencil, Plus, ShieldAlert, ShieldCheck, Trash2, Webhook, X } from "lucide-react";
import { startPolling } from "../lib/refresh.js";
import {
  MISSED_OPTIONS,
  OVERLAP_OPTIONS,
  RUNS_ONLY_WHILE_RUNNING,
  UNSANDBOXED_TEXT,
  UNSANDBOXED_TITLE,
  WIDENED_TEXT,
  WIDENED_TITLE,
  approvalNotice,
  approvedLine,
  changedDuringRunLine,
  dailyLimitText,
  draftFromTrigger,
  emptyTriggerDraft,
  fileChangeLabel,
  formatWhen,
  isProblemStatus,
  modelNoticeText,
  modelNoticeTitle,
  modelNotices,
  schedulerSummary,
  statusLabel,
  triggerBodyFromDraft,
  triggerConfirmText,
  triggerDetail,
  triggerPolicyText,
  triggerTitle,
  triggersApi,
  unsandboxedStepText,
  unsandboxedSteps,
  unseenFiles,
  unseenReasonText,
  webhookCurl,
  widenedStepText,
  widenedSteps,
} from "../lib/rattishTriggers.js";
import { SourceDiff } from "./SourceDiff.jsx";
import { useRovingChoice } from "../lib/useRovingChoice.js";

const KIND_CHOICES = [
  ["cron", "Schedule", CalendarClock],
  ["file", "File change", FileSearch],
  ["webhook", "Webhook", Webhook],
];
const KIND_KEYS = KIND_CHOICES.map(([kind]) => kind);
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
        summary.running && !summary.paused ? "border-line bg-slate-50" : "border-amber-500/60 bg-amber-500/10"
      }`}
      role="status"
    >
      <div className="flex items-start gap-2 font-semibold text-ink">
        <span
          aria-hidden="true"
          className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${summary.running && !summary.paused ? "bg-emerald-500" : "bg-amber-500"}`}
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

function UnsandboxedNotice({ steps }) {
  if (!steps.length) return null;
  return (
    <div
      className="space-y-1 rounded-lg border border-amber-500/60 bg-amber-500/10 p-3 text-xs leading-5"
      data-testid="trigger-unsandboxed"
      role="status"
    >
      <p className="flex items-center gap-1.5 font-semibold text-ink">
        <ShieldAlert className="shrink-0 text-amber-700 dark:text-amber-300" size={14} />
        {UNSANDBOXED_TITLE}
      </p>
      <p className="text-amber-800 dark:text-amber-200">{UNSANDBOXED_TEXT}</p>
      <ul className="space-y-0.5 font-mono text-[11px] text-amber-900 dark:text-amber-100">
        {steps.map((step) => (
          <li key={`${step.node}:${step.provider}`}>{unsandboxedStepText(step)}</li>
        ))}
      </ul>
    </div>
  );
}

function WidenedNotice({ steps }) {
  if (!steps.length) return null;
  return (
    <div
      className="space-y-1 rounded-lg border border-amber-500/60 bg-amber-500/10 p-3 text-xs leading-5"
      data-testid="trigger-widened"
      role="status"
    >
      <p className="flex items-center gap-1.5 font-semibold text-ink">
        <ShieldAlert className="shrink-0 text-amber-700 dark:text-amber-300" size={14} />
        {WIDENED_TITLE}
      </p>
      <p className="text-amber-800 dark:text-amber-200">{WIDENED_TEXT}</p>
      <ul className="space-y-0.5 font-mono text-[11px] text-amber-900 dark:text-amber-100">
        {steps.map((step) => (
          <li key={`${step.node}:${step.kind}:${step.path ?? ""}`}>{widenedStepText(step)}</li>
        ))}
      </ul>
    </div>
  );
}

function ModelNotice({ notices }) {
  if (!notices.length) return null;
  return (
    <div
      className="space-y-1 rounded-lg border border-amber-500/60 bg-amber-500/10 p-3 text-xs leading-5"
      data-testid="trigger-model-notice"
      role="status"
    >
      <p className="flex items-center gap-1.5 font-semibold text-ink">
        <CircleAlert className="shrink-0 text-amber-700 dark:text-amber-300" size={14} />
        {modelNoticeTitle(notices)}
      </p>
      <ul className="space-y-0.5 text-amber-900 dark:text-amber-100">
        {notices.map((notice) => (
          <li key={`${notice.node}:${notice.provider}:${notice.model}`}>{modelNoticeText(notice)}</li>
        ))}
      </ul>
    </div>
  );
}

function TriggerForm({ defaultTimezone, editing, showInputs, unsandboxed = [], widened = [], workflowId, onCancel, onSaved }) {
  const [draft, setDraft] = useState(() =>
    editing ? draftFromTrigger(editing) : emptyTriggerDraft("cron", defaultTimezone),
  );
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  // Adding a trigger to a workflow with unsandboxed steps, or steps whose workflow file
  // widens their sandbox, takes a second click.
  const [confirmingUnsandboxed, setConfirmingUnsandboxed] = useState(false);
  const confirmText = triggerConfirmText(unsandboxed, widened);
  const needsConfirmation = !editing && Boolean(confirmText);
  const update = (patch) => setDraft((current) => ({ ...current, ...patch }));
  const kindChoice = useRovingChoice({ keys: KIND_KEYS, selected: draft.kind, onSelect: (kind) => update({ kind }) });
  const timezones = useMemo(() => Intl.supportedValuesOf?.("timeZone") ?? [], []);

  const save = async () => {
    setError("");
    if (needsConfirmation && !confirmingUnsandboxed) {
      setConfirmingUnsandboxed(true);
      return;
    }
    setSaving(true);
    try {
      const body = triggerBodyFromDraft(draft, editing);
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
              {...kindChoice(kind)}
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
      <div className="space-y-1.5">
        <FieldLabel label="Time limit">
          <input
            className={INPUT_CLASS}
            data-testid="trigger-time-limit"
            placeholder="No limit"
            value={draft.maxRunTime}
            onChange={(event) => update({ maxRunTime: event.target.value })}
          />
        </FieldLabel>
        <p className="text-xs leading-5 text-muted">
          A triggered run that takes longer is stopped and shows as failed in this trigger&apos;s history. Use forms like 30m, 2h or 1h30m, or leave it empty for no limit. A shorter timeout in the workflow still applies.
        </p>
      </div>
      <fieldset className="space-y-1.5">
        <legend className="text-xs font-medium text-muted">Daily limit</legend>
        <div className="grid grid-cols-2 gap-2">
          <FieldLabel label="Estimated cost (USD)">
            <input
              className={INPUT_CLASS}
              inputMode="decimal"
              placeholder="No limit"
              value={draft.dailyMaxCostUsd}
              onChange={(event) => update({ dailyMaxCostUsd: event.target.value })}
            />
          </FieldLabel>
          <FieldLabel label="Tokens">
            <input
              className={INPUT_CLASS}
              inputMode="numeric"
              placeholder="No limit"
              value={draft.dailyMaxTokens}
              onChange={(event) => update({ dailyMaxTokens: event.target.value })}
            />
          </FieldLabel>
        </div>
        <p className="text-xs leading-5 text-muted">
          Each run starts capped at what is left of the day. Once it is used up, starts are skipped until midnight
          {draft.kind === "cron" ? " in the time zone above" : " on this computer"}. Runs you start yourself, from the phone or by Rem don&apos;t count.
        </p>
      </fieldset>
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
      <div>
        <label className="flex items-center justify-between gap-3 rounded-lg border border-line bg-white px-3 py-2">
          <span className="text-[13px] font-medium text-slate-700">Wait for a usage limit to reset</span>
          <input
            checked={draft.waitForUsageLimit !== false}
            className="h-4 w-4 accent-indigo-600"
            data-testid="trigger-wait-usage-limit"
            type="checkbox"
            onChange={(event) => update({ waitForUsageLimit: event.target.checked })}
          />
        </label>
        <p className="mt-1.5 text-xs leading-5 text-muted">
          {draft.waitForUsageLimit !== false
            ? "When an agent step hits its provider's usage limit, the run waits for the limit to reset and resumes, without running the steps that passed again. It waits up to 6 times."
            : "Off: a run that hits a provider's usage limit ends failed."}
        </p>
      </div>
      <div>
        <label className="flex items-center justify-between gap-3 rounded-lg border border-line bg-white px-3 py-2">
          <span className="text-[13px] font-medium text-slate-700">Run edits without approval</span>
          <input
            checked={draft.runEditsWithoutApproval}
            className="h-4 w-4 accent-indigo-600"
            type="checkbox"
            onChange={(event) => update({ runEditsWithoutApproval: event.target.checked })}
          />
        </label>
        <p className="mt-1.5 text-xs leading-5 text-muted">
          {draft.runEditsWithoutApproval
            ? "This trigger runs the workflow even after it, or a prompt, script or child workflow it runs, changes. Nobody reviews the edit first."
            : "Off: when the workflow or a file it runs as code changes, this trigger skips its starts until you review the change and approve it."}
        </p>
      </div>
      {error ? <p className="text-xs leading-5 text-amber-700 dark:text-amber-300" role="alert">{error}</p> : null}
      {confirmingUnsandboxed ? (
        <p className="text-xs leading-5 text-amber-800 dark:text-amber-200" role="alert">
          {confirmText}
        </p>
      ) : null}
      <div className="flex justify-end gap-1.5">
        <button className={SMALL_BUTTON_CLASS} type="button" onClick={onCancel}>Cancel</button>
        <button
          className={`inline-flex h-7 items-center rounded-md px-3 text-[11px] font-semibold text-white transition disabled:opacity-50 ${
            confirmingUnsandboxed ? "bg-amber-600 hover:bg-amber-700" : "bg-indigo-600 hover:bg-indigo-700"
          }`}
          disabled={saving}
          type="button"
          onClick={save}
        >
          {saving ? "Saving…" : editing ? "Save" : confirmingUnsandboxed ? "Add anyway" : "Add trigger"}
        </button>
      </div>
    </div>
  );
}

const FILE_NOTES = {
  missing: "The workflow names this file, but it isn't on disk.",
  removed: "The workflow no longer runs this file.",
};

// Opens a changed file in its app, for a change Review changes couldn't show in full.
function OpenFileButton({ file }) {
  const openPath = window.goferDesktop?.workspace?.openPath;
  if (!openPath || !file.absolutePath) return null;
  return (
    <button
      aria-label={`Open ${file.path}`}
      className={SMALL_BUTTON_CLASS}
      type="button"
      onClick={() => void Promise.resolve(openPath(file.absolutePath)).catch(() => {})}
    >
      <ExternalLink size={12} />
      Open
    </button>
  );
}

function ChangedFile({ file }) {
  const note = !file.diff
    ? FILE_NOTES[file.change] ??
      (!file.diffAvailable
        ? "Binary or too large to show. Its contents changed."
        : file.truncated
          ? "Not shown: the diffs above use up what Review changes shows. Open the file to read it."
          : "")
    : "";
  return (
    <div className="overflow-hidden rounded-md border border-line">
      <div className="flex items-center gap-1.5 border-b border-line bg-white px-2 py-1 text-[11px]">
        <span className="shrink-0 font-semibold text-ink">{file.roleLabel}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-muted" title={file.path}>{file.path}</span>
        <span className="shrink-0 rounded-full border border-line px-1.5 text-[10px] font-medium text-slate-700">{fileChangeLabel(file.change)}</span>
      </div>
      {note ? (
        <p className="bg-slate-50 px-3 py-2 text-[11px] leading-4 text-muted">{note}</p>
      ) : (
        <SourceDiff className="max-h-64" diff={file.diff} label={`Changes to ${file.path}`} />
      )}
      {file.truncated && file.diff ? <p className="border-t border-line px-2 py-1 text-[11px] text-muted">The diff was cut short. Open the file to read the rest.</p> : null}
      {file.warning ? (
        <p className="flex items-start gap-1.5 border-t border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] leading-4 text-amber-800 dark:text-amber-200" role="note">
          <ShieldAlert aria-hidden="true" className="mt-px shrink-0" size={12} />
          <span>
            {file.warning}
            {file.unpinned?.length ? <span className="font-mono"> ({file.unpinned.join(", ")})</span> : null}
          </span>
        </p>
      ) : null}
    </div>
  );
}

// The second question before approving changes that weren't shown in full.
function UnseenConfirm({ approving, files, onBack, onConfirm }) {
  const unpinned = files.filter((file) => file.reason === "unpinned").length;
  const notShown = files.length - unpinned;
  return (
    <div
      aria-label="Approve changes you haven't seen?"
      className="space-y-2 rounded-md border border-amber-500/60 bg-amber-500/10 p-2.5"
      role="alertdialog"
    >
      <p className="text-[11px] font-medium leading-4 text-amber-800 dark:text-amber-200">
        {notShown
          ? `Review changes couldn't show all of ${notShown === 1 ? "this file's change" : `these ${notShown} files' changes`}. Open ${notShown === 1 ? "it" : "them"} to read what changed before you approve. `
          : ""}
        {unpinned
          ? `${unpinned === 1 ? "This MCP server runs a package" : `These ${unpinned} MCP servers run packages`} with no exact version, so each run fetches the newest release, which nobody has reviewed. Pin a version in Rem's MCP server list (pkg@1.2.3 or pkg==1.2.3) to stop that.`
          : ""}
      </p>
      <ul className="space-y-1">
        {files.map((file) => (
          <li key={file.path} className="flex items-center gap-1.5 rounded-md border border-line bg-white px-2 py-1 text-[11px]">
            <span className="min-w-0 flex-1">
              <span className="block truncate font-mono text-ink" title={file.path}>{file.path}</span>
              <span className="block text-muted">
                {unseenReasonText(file.reason)}
                {file.packages?.length ? <span className="font-mono"> ({file.packages.join(", ")})</span> : null}
              </span>
            </span>
            <OpenFileButton file={file} />
          </li>
        ))}
      </ul>
      <div className="flex justify-end gap-1.5">
        <button className={SMALL_BUTTON_CLASS} type="button" onClick={onBack}>Back</button>
        <button
          className="inline-flex h-7 items-center gap-1 rounded-md bg-amber-600 px-3 text-[11px] font-semibold text-white transition hover:bg-amber-700 disabled:opacity-50"
          disabled={approving}
          type="button"
          onClick={onConfirm}
        >
          <ShieldCheck size={12} />
          {approving ? "Approving…" : notShown ? "Approve without seeing all" : "Approve anyway"}
        </button>
      </div>
    </div>
  );
}

// Review changes: each file that differs from the approved version, then Approve this
// version, which approves exactly what is shown here. When a diff was cut or a file can't
// be shown, it asks a second time and names those files.
// Rem's Switch over and workflow shortcuts reuse it: ``source`` loads the changes and
// approves them in its own way, ``intro`` replaces the approval line, ``approveLabel``
// names the button and ``subject`` says what the approval is for.
export function ChangesReview({ afterEdit = false, trigger, source = null, intro = "", approveLabel = "Approve this version", subject = "trigger", onApproved, onClose }) {
  const [changes, setChanges] = useState(null);
  const [error, setError] = useState("");
  const [approving, setApproving] = useState(false);
  const [unseen, setUnseen] = useState(null);

  // A reload after a refused approval keeps the refusal showing above the new diff.
  const load = useCallback(async () => {
    try {
      const payload = source ? { changes: await source.changes() } : await triggersApi.changes(trigger.id);
      setChanges(payload.changes);
      setUnseen(null);
    } catch (loadError) {
      setError(loadError.message);
    }
  }, [source, trigger?.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const approve = async (acceptUnseen = false) => {
    const notShown = unseenFiles(changes);
    if (!acceptUnseen && notShown.length) {
      setUnseen(notShown);
      return;
    }
    setApproving(true);
    setError("");
    try {
      await (source ? source.approve(changes.fingerprint, { acceptUnseen }) : triggersApi.approve(trigger.id, changes.fingerprint, { acceptUnseen }));
      onApproved();
    } catch (approveError) {
      setApproving(false);
      if (approveError.unseen?.length && !acceptUnseen) {
        setUnseen(approveError.unseen);
        return;
      }
      setError(approveError.message);
      void load();
    }
  };

  return (
    <div aria-label="Workflow changes" className="space-y-2 rounded-lg border border-line bg-slate-50 p-2.5" role="region">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[11px] leading-4 text-muted">
          {!changes ? "Loading the changes…" : intro || approvedLine(changes) || `No version of this workflow was approved for this ${subject} yet.`}
        </p>
        <button aria-label="Close the changes" className="shrink-0 text-muted hover:text-ink" type="button" onClick={onClose}>
          <X size={14} />
        </button>
      </div>
      {afterEdit ? (
        <p className="text-[11px] leading-4 text-slate-700">
          Saved. Saving doesn't approve changes waiting for review, so the trigger skips its starts until you approve them here.
        </p>
      ) : null}
      {changes?.changedDuringRun ? (
        <p className="text-[11px] leading-4 text-amber-700 dark:text-amber-300" role="status">
          {changedDuringRunLine(changes.changedDuringRun)}
        </p>
      ) : null}
      {changes?.files?.map((file) => <ChangedFile key={`${file.change}:${file.path}`} file={file} />)}
      {changes && !changes.files?.length ? (
        <p className="text-[11px] leading-4 text-muted">
          {changes.state === "approved"
            ? "Nothing changed since you approved it."
            : changes.changedDuringRun
              ? "No file differs from the approved version now."
              : "No file's text differs, but the workflow now uses its files differently."}
        </p>
      ) : null}
      {changes?.truncated ? (
        <p className="text-[11px] leading-4 text-muted">Some diffs were cut short. Open the files to see all of them.</p>
      ) : null}
      {error ? <p className="text-[11px] leading-4 text-amber-700 dark:text-amber-300" role="alert">{error}</p> : null}
      {unseen ? (
        <UnseenConfirm approving={approving} files={unseen} onBack={() => setUnseen(null)} onConfirm={() => void approve(true)} />
      ) : (
        <div className="flex justify-end gap-1.5">
          <button className={SMALL_BUTTON_CLASS} type="button" onClick={onClose}>Not now</button>
          <button
            className="inline-flex h-7 items-center gap-1 rounded-md bg-indigo-600 px-3 text-[11px] font-semibold text-white transition hover:bg-indigo-700 disabled:opacity-50"
            disabled={!changes || approving || changes.state === "approved"}
            type="button"
            onClick={() => void approve()}
          >
            <ShieldCheck size={12} />
            {approving ? "Approving…" : approveLabel}
          </button>
        </div>
      )}
    </div>
  );
}

// reviewing is "" when closed, "open" from Review changes, or "edit" when the Triggers
// section opened it after an edit or turning the trigger on.
function ApprovalNotice({ reviewing, trigger, onChanged, onReview }) {
  const notice = approvalNotice(trigger);
  if (!notice) return null;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 rounded-md border border-amber-500/60 bg-amber-500/10 px-2 py-1.5 text-[11px] leading-4" role="status">
        <span className="min-w-0 flex-1 font-medium text-amber-800 dark:text-amber-200">
          {notice}. Triggered starts are skipped until you approve it.
        </span>
        {!reviewing ? (
          <button className={SMALL_BUTTON_CLASS} type="button" onClick={() => onReview("open")}>
            Review changes
          </button>
        ) : null}
      </div>
      {reviewing ? (
        <ChangesReview
          afterEdit={reviewing === "edit"}
          trigger={trigger}
          onApproved={() => {
            onReview("");
            onChanged();
          }}
          onClose={() => onReview("")}
        />
      ) : null}
    </div>
  );
}

function TriggerCard({ reviewing, scheduler, trigger, onChanged, onEdit, onReview, onRevealToken }) {
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
            onChange={(event) => {
              const enabled = event.target.checked;
              void act(async () => {
                const payload = await triggersApi.update(trigger.id, { enabled });
                // Turning a trigger on approves nothing, so show what is waiting.
                if (enabled && payload?.trigger && approvalNotice(payload.trigger)) onReview("edit");
              });
            }}
          />
        </label>
      </div>
      <p className="break-all font-mono text-[11px] leading-4 text-muted">{triggerDetail(trigger)}</p>
      <p className="text-[11px] leading-4 text-muted">{triggerPolicyText(trigger)}</p>
      {trigger.enabled && scheduler?.paused ? (
        <p className="text-[11px] leading-4 text-amber-700 dark:text-amber-300">Paused with all triggers</p>
      ) : trigger.nextRunAt ? (
        <p className="text-[11px] leading-4 text-slate-700">Next: {formatWhen(trigger.nextRunAt)}</p>
      ) : null}
      {trigger.dailyLimit ? (
        <p
          className={`text-[11px] leading-4 ${trigger.dailyLimit.reached ? "text-amber-700 dark:text-amber-300" : "text-slate-700"}`}
          data-testid="trigger-daily-limit"
        >
          {dailyLimitText(trigger.dailyLimit)}
        </p>
      ) : null}
      {latest ? (
        <div className="flex items-center gap-1.5 text-[11px] leading-4 text-muted">
          <StatusPill status={latest.status} />
          <span>{formatWhen(latest.at)}</span>
        </div>
      ) : null}
      {trigger.runEditsWithoutApproval ? (
        <p className="text-[11px] leading-4 text-muted">Runs edits without approval</p>
      ) : null}
      <ApprovalNotice reviewing={reviewing} trigger={trigger} onChanged={onChanged} onReview={onReview} />
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
  const [warnings, setWarnings] = useState([]);
  // The trigger whose Review changes is open, and how it was opened.
  const [review, setReview] = useState(null);
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
    setWarnings([]);
    setReview(null);
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
  const unsandboxed = unsandboxedSteps(data);
  const widened = widenedSteps(data);
  const saved = (payload) => {
    setForm(null);
    setWarnings(payload.warnings ?? []);
    if (payload.token) {
      setReveal({ token: payload.token, url: payload.trigger?.webhookUrl });
    }
    // An edit approves nothing: changes waiting for review open right away.
    if (payload.trigger && approvalNotice(payload.trigger)) {
      setReview({ id: payload.trigger.id, mode: "edit" });
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
      <UnsandboxedNotice steps={unsandboxed} />
      <WidenedNotice steps={widened} />
      <ModelNotice notices={modelNotices(data)} />
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
      {warnings.length ? (
        <div className="space-y-1 rounded-lg border border-amber-500/60 bg-amber-500/10 p-3 text-xs leading-5" role="status">
          <div className="flex items-start justify-between gap-2">
            <p className="font-semibold text-ink">Saved. Check the daily cost limit:</p>
            <button aria-label="Dismiss warning" className="text-muted hover:text-ink" type="button" onClick={() => setWarnings([])}>
              <X size={14} />
            </button>
          </div>
          {warnings.map((warning) => (
            <p key={warning} className="text-amber-800 dark:text-amber-200">{warning}</p>
          ))}
        </div>
      ) : null}
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
            reviewing={review?.id === trigger.id ? review.mode : ""}
            scheduler={data?.scheduler}
            trigger={trigger}
            onChanged={() => void refresh().catch(() => {})}
            onEdit={(editing) => setForm({ editing })}
            onReview={(mode) => setReview(mode ? { id: trigger.id, mode } : null)}
            onRevealToken={setReveal}
          />
        ),
      )}
      {form && !form.editing ? (
        <TriggerForm
          defaultTimezone={data?.defaultTimezone ?? "UTC"}
          showInputs={showInputs}
          unsandboxed={unsandboxed}
          widened={widened}
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
