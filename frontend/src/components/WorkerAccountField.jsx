import { showsAccountChoice, workerAccountOptions } from "../lib/workerAccounts.js";

/**
 * The organization worker editor's Account choice: which of the provider's accounts
 * (Settings > Providers) the worker's turns run as, or the default sign-in. Hidden for a
 * provider without accounts, unless the worker still names one.
 */
export default function WorkerAccountField({ accounts, provider, value, onChange, className, labelClassName }) {
  if (!showsAccountChoice(accounts, provider, value)) return null;
  const options = workerAccountOptions(accounts, provider, value);
  const selected = options.find(option => option.id === (value || "")) || options[0];
  return <label className={className}>
    <span className={labelClassName}>Account</span>
    <select aria-label="Account" value={value || ""} onChange={event => onChange(event.target.value || null)}>
      {options.map(option => <option key={option.id || "default"} value={option.id}>{option.label}</option>)}
    </select>
    <small>{selected.meta}.{selected.id && selected.label !== "Removed account" ? " Its runs count toward this account’s usage and limits." : ""}</small>
  </label>;
}
