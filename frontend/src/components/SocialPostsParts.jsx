import { useId, useState } from "react";
import { AlertTriangle, CheckCircle2, CircleSlash, Eye, EyeOff, Minus, Plus, RefreshCw, X } from "lucide-react";
import { ACCOUNT_STATUS } from "../lib/socialPlatforms.js";
import { normalizeDomain } from "../lib/socialApi.js";

// Small building blocks shared by the Posts board, editor and Accounts page.

// A brand-colored tile with a simple mark, so each platform reads at a glance. The label is
// always next to it or in aria-label, so color is never the only cue.
const MARKS = {
  discord: { mark: "D", color: "#5865f2" },
  x: { mark: "𝕏", color: "#16181c" },
  linkedin: { mark: "in", color: "#0a66c2" },
  facebook: { mark: "f", color: "#1877f2" },
  youtube: { mark: "▶", color: "#e62117" },
  patreon: { mark: "P", color: "#e8505b" },
  buymeacoffee: { mark: "☕", color: "#c88a00" },
  tiktok: { mark: "♪", color: "#111111" },
};

export function PlatformIcon({ platform, size = 28, label }) {
  const item = MARKS[platform] || { mark: (platform || "?").slice(0, 1).toUpperCase(), color: "#4d5d6a" };
  return <span className={`sp-picon sp-picon-${platform}`} style={{ "--picon": item.color, "--picon-size": `${size}px` }}
    role={label ? "img" : undefined} aria-label={label || undefined} aria-hidden={label ? undefined : "true"}>{item.mark}</span>;
}

export function AccountStatus({ status }) {
  const info = ACCOUNT_STATUS[status] || { label: status, tone: "todo" };
  const Icon = { done: CheckCircle2, review: RefreshCw, blocked: AlertTriangle, todo: CircleSlash }[info.tone] || CircleSlash;
  return <span className={`sp-pill tone-${info.tone}`}><Icon size={12} aria-hidden="true" />{info.label}</span>;
}

// A labelled on/off switch. The description sits under the label so it reads as one setting.
export function Switch({ label, description, checked, disabled, onChange }) {
  const id = useId();
  return <div className="sp-switch-row">
    <div className="sp-switch-text">
      <label htmlFor={id}>{label}</label>
      {description ? <p id={`${id}-d`}>{description}</p> : null}
    </div>
    <input id={id} type="checkbox" role="switch" className="sp-switch" checked={Boolean(checked)} disabled={disabled}
      aria-describedby={description ? `${id}-d` : undefined} onChange={event => onChange(event.target.checked)} />
  </div>;
}

// − [ n ] +. Typing keeps a draft until blur or Enter; the buttons commit straight away.
export function Stepper({ label, value, min = 0, max = 10_000, step = 1, unit, disabled, onCommit }) {
  const [draft, setDraft] = useState(null);
  const id = useId();
  const clamp = number => Math.min(max, Math.max(min, number));
  const commit = () => {
    if (draft == null) return;
    const number = Number.parseInt(draft, 10);
    setDraft(null);
    if (Number.isFinite(number) && clamp(number) !== value) onCommit(clamp(number));
  };
  const bump = delta => { setDraft(null); const next = clamp((Number(value) || 0) + delta); if (next !== value) onCommit(next); };
  return <div className="sp-stepper">
    <label htmlFor={id}>{label}</label>
    <div className="sp-stepper-box">
      <button type="button" aria-label={`Fewer: ${label}`} disabled={disabled || value <= min} onClick={() => bump(-step)}><Minus size={13} aria-hidden="true" /></button>
      <input id={id} inputMode="numeric" value={draft ?? String(value ?? "")} disabled={disabled}
        onFocus={() => setDraft(String(value ?? ""))} onChange={event => setDraft(event.target.value.replace(/[^\d]/g, ""))} onBlur={commit}
        onKeyDown={event => {
          if (event.key === "Enter") { event.preventDefault(); commit(); }
          if (event.key === "ArrowUp") { event.preventDefault(); bump(step); }
          if (event.key === "ArrowDown") { event.preventDefault(); bump(-step); }
        }} />
      <button type="button" aria-label={`More: ${label}`} disabled={disabled || value >= max} onClick={() => bump(step)}><Plus size={13} aria-hidden="true" /></button>
    </div>
    {unit ? <span className="sp-stepper-unit">{unit}</span> : null}
  </div>;
}

// Domains as removable chips. Enter, comma, space or leaving the field adds what's typed.
export function DomainInput({ label, values, disabled, onChange }) {
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState("");
  const id = useId();
  const add = () => {
    const raw = draft.trim();
    if (!raw) return;
    const domain = normalizeDomain(raw);
    if (!domain) { setProblem(`“${raw}” doesn't look like a website address, such as example.com.`); return; }
    setDraft("");
    setProblem("");
    if (!values.includes(domain)) onChange([...values, domain]);
  };
  return <div className="sp-tags-field">
    <div className={`sp-tags ${problem ? "invalid" : ""}`} onClick={event => event.currentTarget.querySelector("input")?.focus()}>
      <ul aria-label={label}>
        {values.map(value => <li key={value} className="sp-tag">
          <span>{value}</span>
          <button type="button" aria-label={`Remove ${value}`} disabled={disabled} onClick={() => onChange(values.filter(item => item !== value))}><X size={11} aria-hidden="true" /></button>
        </li>)}
      </ul>
      <input id={id} aria-label={`Add to ${label.toLowerCase()}`} value={draft} disabled={disabled} placeholder={values.length ? "Add another" : "example.com"}
        aria-invalid={problem ? "true" : undefined} aria-describedby={problem ? `${id}-e` : undefined}
        onChange={event => { setDraft(event.target.value); setProblem(""); }} onBlur={add}
        onKeyDown={event => {
          if (["Enter", ",", " "].includes(event.key)) { event.preventDefault(); add(); }
          if (event.key === "Backspace" && !draft && values.length) onChange(values.slice(0, -1));
        }} />
    </div>
    {problem ? <p id={`${id}-e`} className="sp-field-error" role="alert">{problem}</p> : null}
  </div>;
}

// One single-line field in the Connect form. Secrets are masked with a Show button.
export function LoginField({ id: fixedId, field, value, error, disabled, onChange, onBlur, inputRef }) {
  const [shown, setShown] = useState(false);
  const generated = useId();
  const id = fixedId || generated;
  const masked = field.kind === "secret" && !shown;
  return <div className="sp-login-field">
    <label htmlFor={id}>{field.label}{field.required ? null : <span className="sp-optional"> (optional)</span>}</label>
    <div className={`sp-login-input ${error ? "invalid" : ""}`}>
      <input id={id} ref={inputRef} type={masked ? "password" : field.kind === "url" ? "url" : "text"} autoComplete="off" spellCheck={false}
        value={value || ""} disabled={disabled} placeholder={field.kind === "url" ? "https://" : ""}
        aria-invalid={error ? "true" : undefined} aria-describedby={`${id}-h`}
        onChange={event => onChange(event.target.value)} onBlur={onBlur} />
      {field.kind === "secret" ? <button type="button" className="sp-reveal" aria-pressed={shown} aria-label={shown ? `Hide ${field.label.toLowerCase()}` : `Show ${field.label.toLowerCase()}`}
        onClick={() => setShown(current => !current)}>{shown ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}</button> : null}
    </div>
    <p id={`${id}-h`} className={error ? "sp-field-error" : "sp-field-hint"}>{error || field.hint}</p>
  </div>;
}
