import { useEffect, useState } from "react";
import { Brain, Check, Copy, Undo2 } from "lucide-react";
import { formatWhen } from "../lib/remHome.js";
import { addRemMemory, forgetRemMemory, loadRemMemories, saveRemMemory, setRemMemoryMode, undoRemMemory } from "../lib/remMemory.js";

const MODES = [
  { value: "on", label: "On", hint: "Rem uses what it remembers and saves new facts and preferences you state." },
  { value: "paused", label: "Paused", hint: "Rem uses what it remembers but saves nothing new. Asking it to correct or forget something still works." },
  { value: "off", label: "Off", hint: "Rem neither uses nor saves memories. Nothing here is deleted." },
];

// What Rem remembers between conversations. Each fact can be corrected or
// forgotten without clearing the rest of Rem's history.
export default function RemMemoryPanel({ onClose, now = new Date() }) {
  const [memories, setMemories] = useState(null);
  const [mode, setMode] = useState("on");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const show = result => { setMemories(result.memories); setMode(result.mode); };
  useEffect(() => {
    let current = true;
    loadRemMemories().then(result => { if (current) show(result); }, failure => { if (current) setError(failure.message); });
    return () => { current = false; };
  }, []);
  async function apply(action, after) {
    setBusy(true);
    setError("");
    try {
      show(await action());
      setEditing(null);
      after?.();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }
  async function copyAll() {
    const text = (memories || []).map(memory => `- ${memory.text}`).join("\n");
    try { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 2000); } catch { setError("Couldn't copy to the clipboard."); }
  }
  const hint = MODES.find(item => item.value === mode)?.hint;
  return <div className="rem-clear-confirm rem-memory-panel" role="dialog" aria-labelledby="rem-memory-title">
    <h3 id="rem-memory-title">What Rem remembers</h3>
    <p>Rem saves facts and preferences you state so it doesn’t have to ask again, and you can ask it in chat to change or forget one. Each message sends these to the chat model’s provider, so Rem never keeps passwords, keys or tokens.</p>
    <fieldset className="rem-memory-mode" disabled={busy || memories === null}>
      <legend>Memory</legend>
      <div role="radiogroup" aria-label="Memory">
        {MODES.map(item => <label key={item.value} data-checked={mode === item.value || undefined}>
          <input type="radio" name="rem-memory-mode" value={item.value} checked={mode === item.value} onChange={() => apply(() => setRemMemoryMode(item.value))} />
          {item.label}
        </label>)}
      </div>
      {hint ? <small>{hint}</small> : null}
    </fieldset>
    {error ? <p role="alert" className="rem-memory-error">{error}</p> : null}
    {memories === null && !error ? <p role="status">Loading…</p> : null}
    {memories?.length === 0 ? <p>Rem hasn’t saved anything yet.</p> : null}
    {memories?.length ? <ul className="rem-memory-list">
      {memories.map(memory => <li key={memory.id}>
        {editing?.id === memory.id ? <>
          <textarea aria-label="Memory text" value={editing.text} maxLength={2000} rows={3} disabled={busy} onChange={event => setEditing({ id: memory.id, text: event.target.value })} />
          <div>
            <button type="button" disabled={busy || !editing.text.trim()} onClick={() => apply(() => saveRemMemory(memory.id, editing.text))}>Save</button>
            <button type="button" disabled={busy} onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </> : <>
          <span>{memory.text}</span>
          <small>{[formatWhen(memory.created_at, now, { past: true }), memory.added_by === "user" ? "added by you" : "", memory.edited_at ? "edited" : ""].filter(Boolean).join(" · ")}</small>
          <div>
            <button type="button" disabled={busy} onClick={() => setEditing({ id: memory.id, text: memory.text })}>Edit</button>
            <button type="button" data-danger="true" disabled={busy} onClick={() => apply(() => forgetRemMemory(memory.id))}>Forget</button>
          </div>
        </>}
      </li>)}
    </ul> : null}
    <div className="rem-memory-add">
      <textarea aria-label="Something for Rem to remember" placeholder="Add something for Rem to remember" value={draft} maxLength={2000} rows={2} disabled={busy || memories === null} onChange={event => setDraft(event.target.value)} />
      <button type="button" disabled={busy || !draft.trim()} onClick={() => apply(() => addRemMemory(draft), () => setDraft(""))}>Add</button>
    </div>
    <div>
      <button type="button" disabled={!memories?.length} onClick={copyAll} title="Copy every memory as a list">
        {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}<span aria-live="polite">{copied ? "Copied" : "Copy all"}</span>
      </button>
      <button type="button" onClick={onClose}>Done</button>
    </div>
  </div>;
}

const quoted = entry => entry.removed ? "a memory you later forgot" : `“${entry.text}”`;

// Under a reply: what it saved, changed or forgot, with Undo. The phone shows the same note.
export function MemoryNote({ messageId, memory }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!memory) return null;
  const lines = [
    ...(memory.saved || []).map(entry => ({ key: `s-${entry.id}`, text: `Saved to memory: ${quoted(entry)}` })),
    ...(memory.updated || []).map(entry => ({ key: `u-${entry.id}`, text: `Updated memory: ${quoted(entry)}` })),
    ...(memory.forgotten || []).map(entry => ({ key: `f-${entry.id}`, text: `Forgot: ${quoted(entry)}` })),
  ];
  if (memory.refused) lines.push({ key: "refused", text: memory.refused === 1 ? "Didn’t save 1 item that looked like a password, key or token." : `Didn’t save ${memory.refused} items that looked like passwords, keys or tokens.` });
  if (!lines.length) return null;
  const undoable = (memory.saved?.length || memory.updated?.length || memory.forgotten?.length) && !memory.undone_at;
  const undo = async () => {
    setBusy(true);
    setError("");
    try { await undoRemMemory(messageId); } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  };
  return <div className="rem-memory-note" data-undone={memory.undone_at ? true : undefined}>
    <Brain size={13} aria-hidden="true" />
    <ul>{lines.map(line => <li key={line.key}>{line.text}</li>)}</ul>
    {memory.undone_at ? <small>Undone</small> : undoable ? <button type="button" disabled={busy} onClick={undo}><Undo2 size={12} aria-hidden="true" />Undo</button> : null}
    {error ? <p role="alert">{error}</p> : null}
  </div>;
}
