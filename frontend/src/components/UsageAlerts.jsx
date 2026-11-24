import { useEffect, useId, useRef, useState } from "react";
import { BellRing } from "lucide-react";
import { apiUrl } from "../lib/api.js";
import { SwitchControl } from "./SettingControls.jsx";

const LEVEL_FIELDS = [["First alert", 0], ["Second alert", 1]];

// Settings > Usage: the alerts sent when an account nears its 5-hour or weekly limit.
// The backend sends them; this only shows and saves the switch and the two levels.
export default function UsageAlerts() {
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const request = useRef(null);
  const id = useId();
  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    void (async () => {
      try {
        const response = await fetch(apiUrl("/usage/alerts"), { signal: controller.signal });
        const data = await response.json();
        if (!response.ok || typeof data.enabled !== "boolean") throw new Error(data.error || "Could not load limit alerts.");
        if (!controller.signal.aborted) setSettings(data);
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure.message || "Could not load limit alerts.");
      }
    })();
    return () => request.current?.abort();
  }, []);

  async function save(change) {
    if (!settings || saving) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setSaving(true);
    setError("");
    try {
      const response = await fetch(apiUrl("/usage/alerts"), {
        method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" },
        body: JSON.stringify(change),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not save limit alerts.");
      if (!controller.signal.aborted) setSettings(data);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message || "Could not save limit alerts.");
    } finally {
      if (!controller.signal.aborted) setSaving(false);
    }
  }

  function saveLevel(index, value) {
    const next = [...settings.thresholds];
    next[index] = value;
    if (next[0] === settings.thresholds[0] && next[1] === settings.thresholds[1]) return;
    void save({ thresholds: next });
  }

  return <section className="usage-alerts" aria-labelledby={`${id}-title`}>
    <div className="usage-alerts-head">
      <BellRing aria-hidden="true" size={15} className="usage-alerts-icon" />
      <div>
        <h3 id={`${id}-title`}>Limit alerts</h3>
        <p id={`${id}-help`}>A desktop and phone notification when an account reaches these levels of its 5-hour or weekly allowance. Each level alerts once until the window resets.</p>
      </div>
      {settings ? <SwitchControl checked={settings.enabled} disabled={saving} label="Limit alerts" onChange={enabled => save({ enabled })} /> : null}
    </div>
    {error ? <p className="usage-error" role="alert">{error}</p> : null}
    {settings ? <div className="usage-alert-levels" data-off={settings.enabled ? undefined : ""}>
      {LEVEL_FIELDS.map(([label, index]) => <LevelField
        key={label} id={`${id}-level-${index}`} label={label} value={settings.thresholds[index]}
        disabled={!settings.enabled || saving} onCommit={value => saveLevel(index, value)}
      />)}
    </div> : null}
  </section>;
}

// Keeps what is typed until blur or Enter; a cleared or non-number entry goes back to the saved level.
function LevelField({ id, label, value, disabled, onCommit }) {
  const [draft, setDraft] = useState(String(value));
  const focused = useRef(false);
  const cancelled = useRef(false);
  useEffect(() => { if (!focused.current) setDraft(String(value)); }, [value]);
  function commit() {
    focused.current = false;
    const parsed = Number(draft);
    if (cancelled.current || draft.trim() === "" || !Number.isInteger(parsed) || parsed < 1 || parsed > 100) {
      setDraft(String(value));
      return;
    }
    setDraft(String(parsed));
    onCommit(parsed);
  }
  return <label className="usage-alert-level" htmlFor={id}>
    <span>{label}</span>
    <span className="usage-alert-input">
      <input id={id} inputMode="numeric" value={draft} disabled={disabled} aria-describedby={`${id}-unit`}
        onFocus={() => { focused.current = true; cancelled.current = false; }} onBlur={commit}
        onChange={event => setDraft(event.target.value)}
        onKeyDown={event => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") { cancelled.current = true; event.currentTarget.blur(); }
        }} />
      <span id={`${id}-unit`}>% used</span>
    </span>
  </label>;
}
