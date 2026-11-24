import { apiUrl } from "./api";

// Settings > Rem > Remove Co-authored-by lines. The backend's Python check decides
// whether the setting is on and what to remove; a message that never mentions
// co-authored-by can't have such a line, so it skips the round trip.
export async function cleanCommitMessage(message, fetcher = fetch) {
  if (!/co-authored-by/i.test(message)) return message;
  let payload;
  try {
    const response = await fetcher(apiUrl("/provider/commit-message/clean"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }),
    });
    payload = await response.json();
    if (!response.ok) throw new Error(payload?.error || `HTTP ${response.status}`);
  } catch (cause) {
    throw new Error(`Couldn't check the message for Co-authored-by lines, so nothing was committed: ${cause.message}`, { cause });
  }
  if (typeof payload?.message !== "string") throw new Error("Couldn't check the message for Co-authored-by lines, so nothing was committed.");
  return payload.message;
}
