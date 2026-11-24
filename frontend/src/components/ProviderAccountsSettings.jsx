import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { FolderOpen, MoreHorizontal, Plus } from "lucide-react";
import { apiUrl } from "../lib/api";
import { changeLinkLabel, inUseLabel, inUseLineValue } from "../lib/providerAccountUses.js";

// Provider accounts: each one is its own config folder of a provider's CLI (Claude
// Code, Codex), signed in once with that CLI's own sign-in. Raticode never sees it.
// Settings > Providers shows each provider's accounts inside that provider's section.
export async function providerAccountsRequest(body) {
  const response = await fetch(apiUrl("/provider/accounts"), body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : undefined);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not update accounts");
  return payload;
}

function stateLabel(state, provider) {
  if (state === "signed_in") return "Signed in";
  if (state === "needs_sign_in") return "Needs sign-in";
  if (state === "unavailable") return `${provider?.label || "The CLI"} not found`;
  return "Unknown";
}

/** "a", "a and b", "a, b and c". */
function listWords(items) {
  if (items.length < 2) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

/** Every provider's accounts, read once for the whole Providers page. */
export function useProviderAccounts() {
  const [overview, setOverview] = useState(null);
  const [error, setError] = useState("");
  // The provider whose account change failed, so only its section shows the error.
  const [errorProvider, setErrorProvider] = useState("");
  const [busy, setBusy] = useState("");
  const load = useCallback(async (refresh = false) => {
    try {
      setOverview(await fetch(apiUrl(`/provider/accounts${refresh ? "?refresh=1" : ""}`)).then(async response => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "Could not read accounts");
        return payload;
      }));
      setError("");
      setErrorProvider("");
    } catch (failure) { setError(failure.message || "Could not read accounts"); setErrorProvider(""); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const waiting = (overview?.accounts ?? []).some(account => account.sign_in?.state === "waiting");
  useEffect(() => {
    if (!waiting) return undefined;
    const timer = window.setInterval(() => { void load(); }, 2000);
    return () => window.clearInterval(timer);
  }, [waiting, load]);
  const act = useCallback(async (body, label) => {
    setBusy(label);
    setError("");
    try {
      const payload = await providerAccountsRequest(body);
      setOverview(payload);
      window.dispatchEvent(new CustomEvent("raticode:providers-changed"));
      return payload;
    } catch (failure) {
      setError(failure.message || "Could not update accounts");
      setErrorProvider(body?.provider || "");
      return null;
    }
    finally { setBusy(""); }
  }, []);
  return { overview, error, errorProvider, busy, load, act, providers: overview?.providers ?? [] };
}

/**
 * Every provider's accounts on their own, for a provider that supports accounts but
 * has no section on the Providers page (Settings > Providers puts the rest inside
 * each provider).
 */
export default function ProviderAccountsSettings({ accountsState, providerIds, usesFor, onChangeUse }) {
  const own = useProviderAccounts();
  const state = accountsState ?? own;
  const providers = state.providers.filter(provider => !providerIds || providerIds.includes(provider.id));
  return <>
    {providers.map(provider => <section key={provider.id} className="space-y-2 border-t border-line pt-3">
      <h4 className="text-xs font-semibold">{provider.label}</h4>
      <ProviderAccounts provider={provider} accountsState={state} uses={usesFor?.(provider.id)} onChangeUse={onChangeUse} />
    </section>)}
    {!accountsState && state.error && !state.errorProvider ? <p role="alert" className="text-xs text-red-600">{state.error}</p> : null}
  </>;
}

/**
 * One provider's accounts and its Add account button, inside that provider's settings.
 * uses (providerAccountUses) labels the account each kind of work runs on; onChangeUse
 * opens the setting a label's Change link names.
 */
export function ProviderAccounts({ provider, accountsState, uses, onChangeUse }) {
  const { overview, busy, load, act, error, errorProvider } = accountsState;
  const [adding, setAdding] = useState(false);
  const accounts = (overview?.accounts ?? []).filter(account => account.provider === provider.id);
  const warnings = (overview?.warnings ?? []).filter(warning => (warning.provider ?? "claude_code") === provider.id);
  const titleId = `provider-accounts-${provider.id}`;
  function remove(account) {
    if (!window.confirm(`Sign out "${account.name}" and delete its ${provider.label} folder, including its conversation history?`)) return;
    void act({ action: "remove", provider: provider.id, name: account.name }, `remove:${account.name}`).then(payload => {
      if (payload) window.dispatchEvent(new CustomEvent("raticode:provider-account-renamed", { detail: { provider: provider.id, from: account.name, to: "" } }));
    });
  }
  return <div className="space-y-2 rounded border border-line p-2" data-accounts-provider={provider.id} aria-labelledby={titleId} role="group">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h5 id={titleId} className="text-xs font-semibold text-ink">{provider.label} accounts</h5>
      <span className="flex gap-3">
        {accounts.length ? <button type="button" className="text-xs font-semibold text-brand disabled:opacity-50" disabled={Boolean(busy)} onClick={() => load(true)}>Check sign-ins</button> : null}
        {!adding ? <button type="button" className="inline-flex items-center gap-1 text-xs font-semibold text-brand disabled:opacity-50" disabled={Boolean(busy)}
          aria-label={`Add ${provider.label} account`} onClick={() => setAdding(true)}><Plus size={13} aria-hidden="true" />Add account</button> : null}
      </span>
    </div>
    {warnings.map(warning => <p key={warning.accounts.join(",")} role="alert" className="text-xs text-amber-700">{warning.message}</p>)}
    {uses?.lines?.length ? <ul className="space-y-1 text-xs" aria-label={`${provider.label} accounts in use`}>
      {uses.lines.map(use => <li key={use.id} className="flex flex-wrap items-baseline gap-x-2" data-account-use={use.id} data-use-kind={use.kind}>
        <span className="text-muted">{inUseLabel(use)}:</span>
        <span className={use.kind === "removed" ? "font-semibold text-amber-700" : "font-semibold text-ink"}>{inUseLineValue(use)}</span>
        <ChangeUse use={use} provider={provider} onChangeUse={onChangeUse} />
      </li>)}
    </ul> : null}
    {accounts.length ? <ul className="space-y-2">
      {accounts.map(account => <AccountRow key={account.name} account={account} provider={provider} busy={busy} act={act} remove={remove}
        uses={uses?.byAccount?.[account.name] ?? []} onChangeUse={onChangeUse} />)}
    </ul> : !adding ? <p className="text-xs text-muted">No extra {provider.label} accounts.{uses?.lines?.length ? "" : ` Threads and tasks use your default ${provider.label} sign-in.`} Add an account to keep another sign-in in its own folder and let Rem switch between them.</p> : null}
    {adding ? <AddAccountForm provider={provider} busy={busy} act={act} close={() => setAdding(false)} /> : null}
    {error && errorProvider === provider.id ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
  </div>;
}

/** A use label's Change link: opens Settings > Rem where that choice is made. */
function ChangeUse({ use, provider, onChangeUse }) {
  if (!onChangeUse) return null;
  return <button type="button" className="font-semibold text-brand" aria-label={changeLinkLabel(use, provider.label)}
    onClick={() => onChangeUse(use)}>Change</button>;
}

/** Choose a folder to copy setup from: typed, or picked with the desktop's folder picker. */
function SetupFolderField({ provider, value, onChange, disabled, label }) {
  const selectPath = window.goferDesktop?.workspace?.selectPath;
  async function browse() {
    const chosen = await selectPath({ directoryOnly: true, currentPath: value || provider.setup_folder || "" });
    if (chosen) onChange(chosen);
  }
  return <div className="flex items-end gap-2">
    <label className="min-w-0 flex-1 text-xs text-muted">{label}
      <input aria-label={`${provider.label} setup folder`} className="mt-1 block w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink"
        placeholder={provider.setup_folder || "Folder to copy from"} value={value} disabled={disabled} onChange={event => onChange(event.target.value)} />
    </label>
    {selectPath ? <button type="button" className="inline-flex items-center gap-1 pb-1.5 text-xs font-semibold text-brand disabled:opacity-50" disabled={disabled}
      aria-label={`Choose ${provider.label} setup folder`} onClick={() => void browse()}><FolderOpen size={13} aria-hidden="true" />Choose folder…</button> : null}
  </div>;
}

function AddAccountForm({ provider, busy, act, close }) {
  const [name, setName] = useState("");
  const [copySetup, setCopySetup] = useState(false);
  const [setupFrom, setSetupFrom] = useState("");
  const [signIn, setSignIn] = useState(true);
  const disabled = Boolean(busy);
  async function add(event) {
    event.preventDefault();
    if (!name.trim()) return;
    const body = { action: "create", provider: provider.id, name: name.trim(), copySetup, signIn };
    if (copySetup && setupFrom.trim()) body.setupFrom = setupFrom.trim();
    if (await act(body, `create:${provider.id}`)) close();
  }
  return <form className="space-y-2 rounded border border-dashed border-line p-2" onSubmit={add} aria-label={`New ${provider.label} account`}>
    <p className="text-xs text-muted">Raticode keeps this account in its own private folder. You sign in once, in your browser; after that, switching accounts never asks again. Raticode never sees or stores the sign-in.</p>
    <label className="block text-xs text-muted">Account name
      <input aria-label={`New ${provider.label} account name`} className="mt-1 block w-48 rounded border border-line bg-white px-2 py-1.5 text-xs text-ink" placeholder="work" value={name}
        autoFocus onChange={event => setName(event.target.value)} disabled={disabled} />
    </label>
    {provider.can_copy_setup ? <div className="space-y-2">
      <label className="flex items-center gap-1.5 text-xs text-muted">
        <input type="checkbox" checked={copySetup} onChange={event => setCopySetup(event.target.checked)} disabled={disabled} />
        Import my {provider.setup || "shared setup"}
      </label>
      {copySetup ? <SetupFolderField provider={provider} value={setupFrom} onChange={setSetupFrom} disabled={disabled}
        label={`Copy from (empty uses ${provider.setup_folder || "the provider's own folder"})`} /> : null}
    </div> : null}
    <label className="flex items-center gap-1.5 text-xs text-muted">
      <input type="checkbox" checked={signIn} onChange={event => setSignIn(event.target.checked)} disabled={disabled} />
      Sign in now
    </label>
    <div className="flex gap-2">
      <button type="submit" className="rounded border border-line px-2 py-1.5 text-xs font-semibold text-brand disabled:opacity-50" disabled={disabled || !name.trim()}>{signIn ? "Add and sign in" : "Add account"}</button>
      <button type="button" className="rounded px-2 py-1.5 text-xs font-semibold text-muted disabled:opacity-50" disabled={disabled} onClick={close}>Cancel</button>
    </div>
  </form>;
}

/** The menu items focus can move to: the ones on the page and not disabled. */
const enabledOptions = nodes => nodes.filter(node => node && !node.disabled);

/**
 * An account's ⋯ menu. Enter or Space on the button opens it (ArrowDown and ArrowUp
 * too), the arrow keys, Home and End move between items, and Escape closes it and puts
 * focus back on the button. A danger item (Remove) sits last, below a divider.
 * data-settings-keep-open keeps Escape from closing the whole Settings panel.
 */
function AccountActionsMenu({ label, items }) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const itemRefs = useRef([]);
  const focusAt = useRef("first");
  const close = useCallback(refocus => { setOpen(false); if (refocus) triggerRef.current?.focus(); }, []);
  useEffect(() => {
    if (!open) return undefined;
    const options = enabledOptions(itemRefs.current);
    const first = focusAt.current === "last" ? options.at(-1) : options[0];
    (first ?? menuRef.current)?.focus();
    const press = event => { if (!anchorRef.current?.contains(event.target)) close(false); };
    document.addEventListener("pointerdown", press, true);
    return () => document.removeEventListener("pointerdown", press, true);
  }, [open, close]);
  function openAt(where) { focusAt.current = where; setOpen(true); }
  function onTriggerKeyDown(event) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); openAt(event.key === "ArrowUp" ? "last" : "first"); }
    else if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); close(true); }
  }
  function onMenuKeyDown(event) {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); return; }
    if (event.key === "Tab") { close(false); return; }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const options = enabledOptions(itemRefs.current);
    if (!options.length) return;
    const index = options.indexOf(document.activeElement);
    const next = event.key === "Home" ? 0
      : event.key === "End" || (event.key === "ArrowUp" && index < 0) ? options.length - 1
        : (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
    options[next].focus();
  }
  return <div ref={anchorRef} className="relative" data-settings-keep-open={open || undefined}>
    <button ref={triggerRef} type="button" className="ui-icon-button h-6 w-6" aria-haspopup="menu" aria-expanded={open}
      aria-label={label} title={label} onClick={() => open ? close(false) : openAt("first")} onKeyDown={onTriggerKeyDown}>
      <MoreHorizontal size={15} aria-hidden="true" />
    </button>
    {open ? <div ref={menuRef} role="menu" aria-label={label} tabIndex={-1} className="ui-popover absolute right-0 top-full z-30 mt-1 w-44 p-1 outline-none"
      data-settings-keep-open onKeyDown={onMenuKeyDown}>
      {items.map((item, index) => <Fragment key={item.label}>
        {item.danger && index > 0 ? <div role="separator" className="my-1 border-t border-line" /> : null}
        <button ref={node => { itemRefs.current[index] = node; }} type="button" role="menuitem" className="ui-menu-item" tabIndex={-1}
          data-danger={item.danger || undefined} disabled={item.disabled} onClick={() => { close(true); item.onSelect(); }}>{item.label}</button>
      </Fragment>)}
    </div> : null}
  </div>;
}

function AccountRow({ account, provider, busy, act, remove, uses, onChangeUse }) {
  const [mode, setMode] = useState("");
  const [newName, setNewName] = useState(account.name);
  const [setupFrom, setSetupFrom] = useState("");
  // What the last Import setup or Copy my setup again copied: { items, again }.
  const [copied, setCopied] = useState(null);
  const setupChanges = provider.can_copy_setup ? account.setup_changes ?? [] : [];
  const signIn = account.sign_in;
  const waiting = signIn?.state === "waiting";
  const needsSignIn = account.state === "needs_sign_in";
  const label = stateLabel(account.state, provider);
  const target = { provider: provider.id, name: account.name };
  const disabled = Boolean(busy);
  async function rename(event) {
    event.preventDefault();
    const next = newName.trim();
    if (!next || next === account.name) { setMode(""); return; }
    if (await act({ action: "rename", ...target, newName: next }, `rename:${account.name}`)) {
      window.dispatchEvent(new CustomEvent("raticode:provider-account-renamed", { detail: { provider: provider.id, from: account.name, to: next } }));
      setMode("");
    }
  }
  async function importSetup(event) {
    event.preventDefault();
    const body = { action: "import_setup", ...target };
    if (setupFrom.trim()) body.setupFrom = setupFrom.trim();
    const payload = await act(body, `import:${account.name}`);
    if (payload) { setCopied({ items: payload.copied ?? [], again: false }); setMode(""); }
  }
  async function copySetupAgain() {
    const payload = await act({ action: "copy_setup", ...target }, `copy_setup:${account.name}`);
    if (payload) setCopied({ items: payload.copied ?? [], again: true });
  }
  // Only the one action the account needs right now stays on the row; the rest are in ⋯.
  const actions = [
    !waiting && !needsSignIn ? { label: "Sign in again", disabled, onSelect: () => act({ action: "sign_in", ...target }, `sign_in:${account.name}`) } : null,
    { label: "Rename", disabled, onSelect: () => { setNewName(account.name); setMode("rename"); } },
    provider.can_copy_setup ? { label: "Import setup", disabled, onSelect: () => { setCopied(null); setMode("import"); } } : null,
    { label: "Remove", disabled, danger: true, onSelect: () => remove(account) },
  ].filter(Boolean);
  return <li className="rounded border border-line p-2 text-xs" data-account={account.name}>
    <div className="flex items-center justify-between gap-2">
      <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <strong className="text-ink">{account.name}</strong>
        <span className={`rounded border px-1.5 py-0.5 ${needsSignIn ? "border-amber-500 text-amber-700" : "border-line text-muted"}`} aria-label={`Status: ${label}`}>{label}</span>
        {account.plan ? <span className="text-muted">{account.plan}</span> : null}
        {account.email ? <span className="max-w-full truncate text-muted" title={account.email}>{account.email}</span> : null}
      </span>
      <span className="flex shrink-0 items-center gap-2">
        {waiting
          ? <button type="button" className="font-semibold text-brand" onClick={() => act({ action: "cancel_sign_in", ...target }, `cancel:${account.name}`)}>Cancel sign-in</button>
          : needsSignIn ? <button type="button" className="font-semibold text-brand disabled:opacity-50" disabled={disabled}
            onClick={() => act({ action: "sign_in", ...target }, `sign_in:${account.name}`)}>Sign in</button> : null}
        <AccountActionsMenu label={`Actions for ${account.name}`} items={actions} />
      </span>
    </div>
    {uses.length ? <ul className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1" aria-label={`${account.name} is used for`}>
      {uses.map(use => <li key={use.id} className="flex items-center gap-1.5" data-account-use={use.id}>
        <span className={`rounded px-1.5 py-0.5 font-medium ${use.needsSignIn ? "border border-amber-500 text-amber-700" : "bg-brand/10 text-brand"}`}>{inUseLabel(use)}</span>
        <ChangeUse use={use} provider={provider} onChangeUse={onChangeUse} />
      </li>)}
    </ul> : null}
    {needsSignIn && account.reason && !waiting ? <p className="mt-1 text-amber-700">{account.reason} Rem skips this account until it's signed in.</p> : null}
    {account.duplicate_of?.length ? <p className="mt-1 text-amber-700">Same {provider.label} sign-in as {account.duplicate_of.join(", ")}.</p> : null}
    {waiting ? <p role="status" className="mt-1 text-muted">Approve the sign-in in your browser. {signIn.url
      ? <>If no browser opened, <a className="font-semibold text-brand" href={signIn.url} target="_blank" rel="noreferrer">open the sign-in page</a>.</>
      : `If no browser opened, run \`Raticode provider account login ${account.name} --provider ${provider.id}${provider.device_sign_in ? " --device-auth" : ""}\` in a terminal.`}</p> : null}
    {signIn && !waiting && signIn.state !== "signed_in" && signIn.reason ? <p role="alert" className="mt-1 text-red-600">{signIn.reason}</p> : null}
    {mode === "rename" ? <form className="mt-2 flex items-end gap-2" onSubmit={rename}>
      <label className="text-xs text-muted">New name
        <input aria-label={`New name for ${account.name}`} className="mt-1 block w-40 rounded border border-line bg-white px-2 py-1.5 text-xs text-ink" value={newName}
          autoFocus onChange={event => setNewName(event.target.value)} disabled={disabled} />
      </label>
      <button type="submit" className="pb-1.5 font-semibold text-brand disabled:opacity-50" disabled={disabled || !newName.trim()}>Save</button>
      <button type="button" className="pb-1.5 font-semibold text-muted" onClick={() => setMode("")}>Cancel</button>
    </form> : null}
    {mode === "import" ? <form className="mt-2 space-y-2" onSubmit={importSetup}>
      <p className="text-muted">Copies your {provider.setup || "shared setup"} into this account. Anything the account already has stays as it is, and no sign-in is ever copied.</p>
      <SetupFolderField provider={provider} value={setupFrom} onChange={setSetupFrom} disabled={disabled}
        label={`Copy from (empty uses ${provider.setup_folder || "the provider's own folder"})`} />
      <div className="flex gap-3">
        <button type="submit" className="font-semibold text-brand disabled:opacity-50" disabled={disabled}>Import</button>
        <button type="button" className="font-semibold text-muted" onClick={() => setMode("")}>Cancel</button>
      </div>
    </form> : null}
    {setupChanges.length && mode !== "import" ? <p className="mt-1 text-muted" data-setup-changes>
      Differs from your setup in {listWords(setupChanges)}.{" "}
      <button type="button" className="font-semibold text-brand disabled:opacity-50" disabled={disabled}
        aria-label={`Copy my setup again into ${account.name}`} onClick={() => void copySetupAgain()}>Copy my setup again</button>
    </p> : null}
    {copied ? <p role="status" className="mt-1 text-muted">{copied.items.length
      ? `Copied ${copied.items.join(", ")}${copied.again ? " again" : ""}.`
      : copied.again ? "Nothing to copy: the account already matches your setup." : "Nothing new to copy: the account already has everything there."}</p> : null}
  </li>;
}
