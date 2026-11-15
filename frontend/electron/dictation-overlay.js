// Shows the dictation's phase in the overlay. Text goes in with textContent only.
const { document } = globalThis;
const pill = document.querySelector(".pill");
const title = document.getElementById("title");
const detail = document.getElementById("detail");
const stop = document.getElementById("stop");
const open = document.getElementById("open");

stop.addEventListener("click", () => globalThis.dictationOverlay.stop());
open.addEventListener("click", () => globalThis.dictationOverlay.open());

function describe({ phase, message = "", sent = false, label = "", note = "" }) {
  const finish = label ? `Press ${label} again or stop talking to finish` : "Stop talking to finish";
  // A screenshot taken with "Ask Rem about your screen" is named while you talk.
  const again = note ? `${note} ${finish}.` : finish;
  if (phase === "starting") return ["Opening the microphone…", note || "Dictating to Rem", true, false];
  if (phase === "listening") return ["Listening for Rem", again, true, false];
  if (phase === "paused") return ["Paused", again, true, false];
  if (phase === "finishing" || phase === "tidying") return ["Working on your words…", phase === "tidying" ? "Tidying" : "", false, false];
  if (phase === "done") return [sent ? "Sent to Rem" : "Your words are in Rem's message box", message || (sent ? "" : "Open Raticode to edit and send them"), false, !sent];
  if (phase === "error") return ["Dictation stopped", message || "Something went wrong.", false, true];
  return ["Raticode", "", false, false];
}

globalThis.dictationOverlay.onState((state) => {
  const [heading, line, canStop, canOpen] = describe(state || {});
  pill.dataset.phase = state?.phase || "idle";
  title.textContent = heading;
  detail.textContent = line;
  detail.title = line;
  stop.hidden = !canStop;
  open.hidden = !canOpen;
});
