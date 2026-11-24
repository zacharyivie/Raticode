import { formatBytes } from "./secondBrain.js";

// Mirrors DEFAULT_BACKUP in src/gofer/ui/brain_backups.py.
export const DEFAULT_BACKUP = { enabled: false, folder: "", frequency: "daily", weekday: 0, time: "03:00", keep: 7, conversations: false };
export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function backupSettings(brain) { return { ...DEFAULT_BACKUP, ...(brain?.backup || {}) }; }

export function scheduleLabel(settings) {
  if (!settings.enabled) return "Off";
  return settings.frequency === "weekly" ? `Every ${WEEKDAYS[settings.weekday]} at ${settings.time}` : `Every day at ${settings.time}`;
}

export function archiveLabel(estimate) {
  return estimate?.conversations ? `Include Rem’s conversation archive (${formatBytes(estimate.conversations)})` : "Include Rem’s conversation archive";
}

/** Package size with or without the conversation archive, and whether it fits the limit. */
export function packageFit(estimate, conversations) {
  if (!estimate) return null;
  const size = conversations ? estimate.total : estimate.total - estimate.conversations;
  return { size, fits: size <= estimate.limit, limit: estimate.limit };
}

/** A backup's files, documents and size, as one line people read. */
export function backupCounts(record) {
  if (!record) return "";
  const count = (value, word) => `${Number(value || 0).toLocaleString()} ${word}${Number(value) === 1 ? "" : "s"}`;
  return [count(record.files, "file"), count(record.documents, "document"), formatBytes(record.size || 0)].join(" · ");
}
