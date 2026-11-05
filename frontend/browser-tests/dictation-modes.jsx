import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import ChatComposer from "../src/components/ChatComposer.jsx";
import { setDictationMode } from "../src/lib/voiceShortcut.js";
import "../src/styles/index.css";

// A stand-in backend: Chromium's fake microphone supplies real audio, and these
// answers stand in for speech recognition and the tidying model.
const log = { chunks: 0, finished: 0, tidy: [], tidyOut: 0 };
// ?slowTidy=ms makes the tidying model take that long, to check listening goes on meanwhile.
const slowTidy = Number(new URLSearchParams(location.search).get("slowTidy") || 0);
window.dictationLog = log;
const realFetch = window.fetch.bind(window);
window.fetch = async (input, options = {}) => {
  const url = String(input?.url ?? input);
  const body = options.body ? JSON.parse(options.body) : {};
  const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
  if (url.includes("/api/provider/voice-tidy")) return json({ enabled: true });
  if (url.includes("/api/chat/transcribe/start")) return json({ sessionId: "a".repeat(32) }, 201);
  if (url.includes("/api/chat/transcribe/chunk")) { log.chunks += 1; return json({ text: "book a room" }); }
  if (url.includes("/api/chat/transcribe/finish")) { log.finished += 1; return json({ text: "book a room for friday" }); }
  if (url.includes("/api/chat/transcribe/cancel")) return json({ cancelled: true });
  if (url.includes("/api/chat/voice-tidy")) {
    log.tidy.push(body.text);
    log.tidyOut += 1;
    if (slowTidy) await new Promise((resolve) => setTimeout(resolve, slowTidy));
    log.tidyOut -= 1;
    return json({ text: "Book a room for Friday.", tidied: true, reason: "" });
  }
  return realFetch(input, options);
};
setDictationMode(new URLSearchParams(location.search).get("mode") || "toggle");

function Fixture() {
  const [draft, setDraft] = useState("Note:");
  window.fixtureDraft = () => draft;
  return (
    <main style={{ padding: 40, width: 560 }}>
      <ChatComposer draft={draft} onDraftChange={setDraft} attachments={[]} onAttachmentsChange={() => {}} onSend={() => {}} onStop={() => {}} />
    </main>
  );
}
createRoot(document.getElementById("root")).render(<Fixture />);
