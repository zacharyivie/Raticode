/** What the once-a-day reminder says, or "" when there is nothing to remind about. */
export function updateReminderText(updateState) {
  const reminder = updateState?.reminder;
  if (!reminder || !updateState?.available) return "";
  const latest = updateState.info?.version || reminder.version;
  const current = updateState.currentVersion ? ` You have ${updateState.currentVersion}.` : "";
  return `Raticode ${latest} is available.${current}`;
}
