import { useState } from "react";

import { Dialog } from "./Dialog.jsx";

// Asks again for the password inputs a Rattish run didn't save, before Resume or
// Run again. Raticode saves a run's other inputs and trigger events, never these.
export default function SavedInputsPrompt({ action, names, onCancel, onSubmit }) {
  const [values, setValues] = useState(() => Object.fromEntries(names.map((name) => [name, ""])));
  const one = names.length === 1;
  const description = `Raticode doesn't save password inputs, so enter ${one ? "this one" : "these"} again. The run's other inputs and trigger events are reused.`;
  return (
    <Dialog
      description={description}
      onClose={onCancel}
      panelClassName="w-full max-w-[420px] rounded-lg border border-line bg-white shadow-panel"
      title={`${action}: enter ${one ? "a password input" : "password inputs"}`}
    >
      <form
        className="space-y-3 px-5 py-4"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(values);
        }}
      >
        <h2 className="text-base font-semibold">{action}</h2>
        <p className="text-xs text-muted">{description}</p>
        {names.map((name) => (
          <label key={name} className="block text-sm" htmlFor={`saved-input-${name}`}>
            <span className="font-medium">{name}</span>
            <input
              id={`saved-input-${name}`}
              aria-label={name}
              autoComplete="off"
              className="mt-1 w-full rounded-md border border-line bg-white px-3 py-2 text-sm outline-none focus:border-brand"
              type="password"
              value={values[name] ?? ""}
              onChange={(event) => setValues((current) => ({ ...current, [name]: event.target.value }))}
            />
          </label>
        ))}
        <div className="flex justify-end gap-2 pt-1">
          <button className="h-8 rounded-md border border-line px-3 text-xs font-medium" type="button" onClick={onCancel}>
            Cancel
          </button>
          <button className="h-8 rounded-md bg-ink px-3 text-xs font-medium text-white" type="submit">
            {action}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
