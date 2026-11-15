import { Download, Loader2, TriangleAlert, X } from "lucide-react";
import { updateReminderText } from "../lib/updateReminder.js";

/**
 * The daily "update Raticode" reminder. There is only ever one: the main process replaces it, and
 * Not now hides it until tomorrow.
 */
export default function UpdateReminderBanner({ updateState, onUpdate, onDismiss }) {
  const text = updateReminderText(updateState);
  if (!text) return null;
  const busy = Boolean(updateState.downloading || updateState.verifying);
  return (
    <div role="status" aria-label="Update reminder" className="flex h-9 shrink-0 items-center gap-3 border-b border-amber-200 bg-amber-50 px-3 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
      <TriangleAlert size={15} className="shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate" title={text}>{text}</span>
      <button type="button" className="inline-flex h-7 shrink-0 items-center gap-2 rounded-md border border-amber-300 bg-white px-3 font-semibold text-amber-900 transition hover:bg-amber-100 disabled:cursor-wait disabled:opacity-70 dark:border-amber-400/40 dark:bg-transparent dark:text-amber-100 dark:hover:bg-amber-500/20" disabled={busy} onClick={onUpdate}>
        {busy ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
        Update Raticode
      </button>
      <button type="button" aria-label="Not now" title="Not now. Raticode reminds you again tomorrow." className="grid h-7 w-7 shrink-0 place-items-center rounded-md transition hover:bg-amber-100 dark:hover:bg-amber-500/20" onClick={onDismiss}>
        <X size={15} />
      </button>
    </div>
  );
}
