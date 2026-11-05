import { apiUrl } from "./api.js";

// Voice tidying: dictated words go to a fast model before they show in the message box.
// Every failure gives back the words as heard, so a dictation is never lost.

export async function loadVoiceTidy({ defaultProvider = "", resolve = true, signal } = {}) {
  const query = new URLSearchParams();
  if (!resolve) query.set("resolve", "0");
  if (defaultProvider) query.set("defaultProvider", defaultProvider);
  const search = query.toString();
  const response = await fetch(apiUrl(`/provider/voice-tidy${search ? `?${search}` : ""}`), { signal });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not load voice tidy settings");
  return payload;
}

export async function saveVoiceTidy(changes) {
  const response = await fetch(apiUrl("/provider/voice-tidy"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not save voice tidy settings");
  return payload;
}

// Always resolves: { text, tidied, reason }. A failed request keeps the heard words.
export async function tidyTranscript(text, { signal } = {}) {
  try {
    const response = await fetch(apiUrl("/chat/voice-tidy"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }), signal,
    });
    const payload = await response.json();
    if (!response.ok || typeof payload.text !== "string") {
      return { text, tidied: false, reason: payload.error || "Tidying isn't available right now." };
    }
    return { text: payload.text || text, tidied: payload.tidied === true, reason: payload.reason || "" };
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    return { text, tidied: false, reason: "Couldn't reach the tidying service." };
  }
}
