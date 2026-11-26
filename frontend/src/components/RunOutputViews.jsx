// What a node printed or returned in a run, shown as readable text. Both node
// inspectors use these: LegacyTomlInspector for TOML workflows and
// RattishInspector in DagCanvas for Rattish ones.
import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

// Text a person reads: a string stays as it is, anything else is indented JSON.
function readableRunValue(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

function isCommandOutput(value) {
  return (
    Boolean(value) &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (typeof value.stdout === "string" || typeof value.stderr === "string")
  );
}

// One activation's text: what it printed or returned, and why it failed.
export function attemptTextFields(attempt) {
  return [
    attempt.inputs && Object.keys(attempt.inputs).length
      ? { id: "inputs", label: "Inputs", text: JSON.stringify(attempt.inputs, null, 2) }
      : null,
    attempt.output ? { id: "output", label: "Output", text: attempt.output } : null,
    attempt.stdout ? { id: "stdout", label: "Stdout", text: attempt.stdout } : null,
    attempt.stderr ? { id: "stderr", label: "Stderr", text: attempt.stderr, tone: "error" } : null,
    attempt.error && attempt.error !== attempt.stderr
      ? { id: "error", label: "Error", text: attempt.error, tone: "error" }
      : null,
    attempt.prompt ? { id: "prompt", label: "Prompt", text: attempt.prompt } : null,
  ].filter(Boolean);
}

// A Rattish activation's text, from a node's latest output (data is the node's
// result: stdout and stderr for bash-command and script nodes, the answer for an
// agent node) or from one runNodes[id].attempts entry, whose output is JSON text.
export function rattishRunTextFields({ data, error, output, stderr, stdout }) {
  let result = data;
  if (result === undefined && typeof output === "string") {
    try {
      result = JSON.parse(output);
    } catch {
      result = output;
    }
  }
  const command = isCommandOutput(result);
  const commandStdout = stdout ?? (command ? result.stdout : null);
  const commandStderr = stderr ?? (command ? result.stderr : null);
  const printed = commandStdout != null || commandStderr != null;
  return attemptTextFields({
    output: printed ? "" : readableRunValue(result),
    stdout: commandStdout ? String(commandStdout) : "",
    stderr: commandStderr ? String(commandStderr) : "",
    error: error ? String(error) : "",
  });
}

// Lines shown before the pane scrolls; drag its bottom edge to see more.
const PANE_MIN_LINES = 3;
const PANE_MAX_LINES = 16;
const PANE_LINE_PX = 20;

function paneHeight(text) {
  const lines = String(text).split("\n").length;
  const shown = Math.min(PANE_MAX_LINES, Math.max(PANE_MIN_LINES, lines));
  return shown * PANE_LINE_PX + 16;
}

// Shows one field at a time with a tab per field. Pass selectedFieldId and
// onSelectField to share the choice across viewers; without them it keeps its own.
// A failed activation opens on its Error unless another field was chosen.
export function ScrollableFieldViewer({ fields, onSelectField, selectedFieldId }) {
  const [ownFieldId, setOwnFieldId] = useState(null);
  const [copiedId, setCopiedId] = useState(null);
  const copiedTimer = useRef(null);
  useEffect(() => () => clearTimeout(copiedTimer.current), []);
  if (!fields.length) return null;
  const chosenId = onSelectField ? selectedFieldId : ownFieldId;
  const selectedField =
    fields.find((field) => field.id === chosenId) ??
    fields.find((field) => field.id === "error") ??
    fields[0];
  const errorTone = selectedField.tone === "error";
  const copied = copiedId === selectedField.id;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(selectedField.text);
    } catch {
      return;
    }
    setCopiedId(selectedField.id);
    clearTimeout(copiedTimer.current);
    copiedTimer.current = setTimeout(() => setCopiedId(null), 1500);
  };
  return (
    <div className="mt-2 overflow-hidden rounded-md border border-line bg-white">
      <div className="flex items-center gap-1 border-b border-line bg-slate-50 p-1">
        <div className="flex min-w-0 flex-1 flex-wrap gap-1">
          {fields.map((field) => {
            const selected = field.id === selectedField.id;
            return (
              <button
                key={field.id}
                aria-pressed={selected}
                className={`rounded px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] transition ${
                  selected
                    ? `bg-white shadow-sm ${field.tone === "error" ? "text-red-700" : "text-ink"}`
                    : "text-muted hover:bg-white/70 hover:text-ink"
                }`}
                title={`Show ${field.label}`}
                type="button"
                onClick={() => (onSelectField ?? setOwnFieldId)(field.id)}
              >
                {field.label}
              </button>
            );
          })}
        </div>
        <button
          aria-label={copied ? `${selectedField.label} copied` : `Copy ${selectedField.label}`}
          className="flex shrink-0 items-center gap-1 rounded px-2 py-1 text-[11px] font-medium text-muted transition hover:bg-white hover:text-ink"
          title={copied ? `${selectedField.label} copied` : `Copy ${selectedField.label}`}
          type="button"
          onClick={() => void copy()}
        >
          {copied ? <Check aria-hidden="true" size={12} /> : <Copy aria-hidden="true" size={12} />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <pre
        key={selectedField.id}
        aria-label={selectedField.label}
        className={`workflow-scrollbar max-h-[85vh] min-h-12 resize-y overflow-auto whitespace-pre-wrap break-words p-2 font-mono text-[11px] leading-5 ${
          errorTone ? "bg-red-50 text-red-700" : "bg-white text-slate-700"
        }`}
        style={{ height: paneHeight(selectedField.text) }}
        tabIndex={0}
      >
        {selectedField.text}
      </pre>
    </div>
  );
}

// The full result as JSON, folded away until asked for.
export function RawRunData({ value }) {
  const [open, setOpen] = useState(false);
  if (value === undefined) return null;
  return (
    <div className="rounded-md border border-line">
      <button
        aria-expanded={open}
        className="flex w-full items-center justify-between px-2 py-1.5 text-[11px] font-semibold text-muted transition hover:text-ink"
        type="button"
        onClick={() => setOpen((current) => !current)}
      >
        <span>Raw data</span>
        <span aria-hidden="true">{open ? "Hide" : "Show"}</span>
      </button>
      {open ? (
        <pre className="workflow-scrollbar max-h-72 overflow-auto whitespace-pre-wrap break-words border-t border-line bg-slate-950 p-3 font-mono text-[11px] leading-5 text-slate-100">
          {JSON.stringify(value, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}
