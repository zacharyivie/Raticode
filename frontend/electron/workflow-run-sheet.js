// The Run sheet's page. Text goes in with textContent and the textarea's value only.
const { document } = globalThis;
const form = document.getElementById("sheet");
const title = document.getElementById("title");
const label = document.getElementById("label");
const field = document.getElementById("text");
const caution = document.getElementById("caution");
const cut = document.getElementById("cut");
const error = document.getElementById("error");
const run = document.getElementById("run");
const cancel = document.getElementById("cancel");
let shownId = "";
let busy = false;

function submit() {
  if (busy) return;
  globalThis.workflowRunSheet.run(field.value);
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  submit();
});
cancel.addEventListener("click", () => globalThis.workflowRunSheet.cancel());
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    globalThis.workflowRunSheet.cancel();
  }
});
field.addEventListener("keydown", (event) => {
  // Enter runs; Shift+Enter keeps a new line. Composing text (IME) is left alone.
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submit();
  }
});

globalThis.workflowRunSheet.onState((state) => {
  if (!state) return;
  busy = state.busy === true;
  title.textContent = state.workflowName || "Workflow";
  title.title = title.textContent;
  label.textContent = state.inputLabel || "Text";
  caution.textContent = state.caution || "";
  // A new sheet replaces the text; an update (busy, an error) keeps what was typed.
  if (state.id !== shownId) {
    shownId = state.id;
    field.value = state.text || "";
    field.focus();
    field.setSelectionRange(field.value.length, field.value.length);
  } else if (state.refill && !field.value) {
    // The clipboard could only be read once the sheet was in front.
    field.value = state.text || "";
  }
  cut.hidden = state.cut !== true;
  cut.textContent = state.cut ? "The clipboard held more text than a run takes; only its start is here." : "";
  error.hidden = !state.error;
  error.textContent = state.error || "";
  run.disabled = busy;
  field.readOnly = busy;
  run.textContent = busy ? "Starting…" : "Run";
});
