import { useEffect, useRef, useState } from "react";
import { ChevronRight, CircleAlert, CircleCheck, CircleX, LoaderCircle } from "lucide-react";
import { ProviderAuthentication, useProviderSignIn } from "./ProviderModelEffortFields.jsx";
import JevProviderSettings from "./JevProviderSettings.jsx";
import LocalModelServerSettings from "./LocalModelServerSettings.jsx";
import ProviderAccountsSettings, { ProviderAccounts, useProviderAccounts } from "./ProviderAccountsSettings.jsx";
import { SwitchControl } from "./SettingControls.jsx";
import { apiUrl } from "../lib/api";
import { menuModels } from "../lib/providerModels.js";
import { checkedLabel } from "../lib/usage.js";
import { providerAccountUses, changeLinkTarget } from "../lib/providerAccountUses.js";
import { request as remRequest } from "../lib/remHomeStore.js";
import { providersCheckedAt } from "../lib/useProviderSettingsCapabilities.js";

// Each status is a word and a symbol, so color is never the only cue.
const STATUSES = {
  ready: { label: "Ready", Icon: CircleCheck, className: "text-emerald-700" },
  needs_sign_in: { label: "Needs sign-in", Icon: CircleAlert, className: "text-amber-700" },
  checking: { label: "Checking", Icon: LoaderCircle, className: "text-muted", spin: true },
  error: { label: "Error", Icon: CircleX, className: "text-red-600" },
};

/** One provider's status for its summary row: ready, needs_sign_in, checking or error. */
export function providerStatus(provider) {
  const status = provider.discoveryStatus;
  if (provider.refreshError && (!status || status === "pending")) return "error";
  if (provider.settingsPending || !status || status === "pending") return "checking";
  if (status === "ready") return "ready";
  if (status === "unauthenticated") return "needs_sign_in";
  return "error";
}

/**
 * Self-work's own account choice by provider, read once from its saved settings, or null
 * when they can't be read (an older backend, or Rem's service is off).
 */
function useSelfWorkAccounts(active) {
  const [choices, setChoices] = useState(null);
  useEffect(() => {
    if (!active) return undefined;
    let live = true;
    remRequest("/kanban").then(payload => {
      const settings = payload?.self_work?.settings;
      if (!live || !settings || typeof settings !== "object") return;
      const saved = settings.provider_accounts;
      setChoices(saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {});
    }).catch(() => {});
    return () => { live = false; };
  }, [active]);
  return choices;
}

/**
 * Settings > Providers. remAccounts is Rem's saved account choice by provider
 * (settings.assistant.providerAccounts); with it, each provider's accounts label the one
 * threads, tasks, Rem's replies and self-work use. onOpenSetting({ category, selectors })
 * takes a label's Change link to that setting.
 */
export default function ProviderSettings({ providerState, remAccounts, onOpenSetting }) {
  const { capabilities, error, loading, refresh } = providerState;
  const accountsState = useProviderAccounts();
  const accountProviders = new Map(accountsState.providers.map(provider => [provider.id, provider]));
  const labelUses = Boolean(remAccounts) && accountsState.providers.length > 0;
  const selfWorkAccounts = useSelfWorkAccounts(labelUses);
  const onChangeUse = onOpenSetting ? use => onOpenSetting(changeLinkTarget(use)) : undefined;
  const usesFor = id => labelUses
    ? providerAccountUses({ provider: id, accounts: accountsState.overview?.accounts ?? [], remAccounts, selfWorkAccounts })
    : null;
  const accountsFor = provider => accountProviders.has(provider.id)
    ? { provider: accountProviders.get(provider.id), state: accountsState, uses: usesFor(provider.id), onChangeUse }
    : null;
  // A provider with accounts but no section of its own still lists its accounts.
  const unlisted = accountsState.providers.map(provider => provider.id).filter(id => !capabilities.some(provider => provider.id === id));
  // An app with a saved override keeps its row even when nothing runs there, so App location can say so.
  const installed = capabilities.filter(provider => provider.discoveryStatus !== "missing" || overrideNotFound(provider));
  const missing = capabilities.filter(provider => provider.discoveryStatus === "missing" && !overrideNotFound(provider));
  const now = useMinuteClock();
  return <div className="space-y-5 py-3">
    <p className="max-w-[60ch] text-xs text-muted">Choose the coding apps Rem and Organizations use, and set up model sources that only workflows use.</p>
    <section aria-labelledby="providers-coding-apps" className="space-y-2" data-provider-part="coding-apps">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <GroupHeading id="providers-coding-apps">Coding apps</GroupHeading>
        <p className="text-xs text-muted">
          <span role="status">{loading ? "Checking providers…" : checkedLabel(providersCheckedAt(capabilities), now)}</span>
          {" · "}
          <button type="button" className="font-semibold text-brand disabled:opacity-60" disabled={loading} onClick={refresh}>Refresh providers</button>
        </p>
      </div>
      {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
      {accountsState.error && !accountsState.errorProvider ? <p role="alert" className="text-xs text-red-600">{accountsState.error}</p> : null}
      {installed.length ? <div className="divide-y divide-line rounded-lg border border-line">
        {installed.map(provider => <ProviderRow key={provider.id} provider={provider} accounts={accountsFor(provider)} />)}
      </div> : null}
      {missing.length ? <NotInstalled providers={missing} accountsFor={accountsFor} /> : null}
      {unlisted.length ? <ProviderAccountsSettings accountsState={accountsState} providerIds={unlisted} usesFor={usesFor} onChangeUse={onChangeUse} /> : null}
    </section>
    <WorkflowOnlySources />
  </div>;
}

function GroupHeading({ id, children }) {
  return <h3 id={id} className="text-[11px] font-semibold uppercase tracking-wide text-muted">{children}</h3>;
}

/** Local model servers and Jev serve workflow nodes only, so they stay folded with a one-line summary. */
function WorkflowOnlySources() {
  const [servers, setServers] = useState(null);
  const [jev, setJev] = useState(null);
  useEffect(() => {
    let live = true;
    const read = path => fetch(apiUrl(path)).then(response => response.ok ? response.json() : Promise.reject(new Error(path)));
    read("/provider/profiles").then(payload => { if (live) setServers((Array.isArray(payload.profiles) ? payload.profiles : []).filter(profile => profile.local)); })
      .catch(() => { if (live) setServers(false); });
    read("/classifiers/jev").then(payload => { if (live) setJev(payload); }).catch(() => { if (live) setJev(false); });
    return () => { live = false; };
  }, []);
  return <section aria-labelledby="providers-workflow-only" className="space-y-2" data-provider-part="workflow-only">
    <GroupHeading id="providers-workflow-only">For workflows only</GroupHeading>
    <div className="divide-y divide-line rounded-lg border border-line">
      <WorkflowSource id="local-model-servers" title="Local model servers" summary={serverSummary(servers)}>
        {labelledBy => <LocalModelServerSettings labelledBy={labelledBy} onProfilesChange={setServers} />}
      </WorkflowSource>
      <WorkflowSource id="classification-models" title="Classification models" summary={jevSummary(jev)}>
        {labelledBy => <JevProviderSettings labelledBy={labelledBy} onStatusChange={setJev} />}
      </WorkflowSource>
    </div>
  </section>;
}

// null while loading, false when it couldn't load.
export function serverSummary(servers) {
  if (servers === null) return "Checking…";
  if (servers === false) return "Couldn't load";
  if (!servers.length) return "No servers";
  return `${servers.length} server${servers.length === 1 ? "" : "s"}`;
}

export function jevSummary(status) {
  if (status === null) return "Checking…";
  if (status === false) return "Couldn't load";
  if (!status.configured) return "Jev: no key";
  return status.enabled === false ? "Jev: key saved, turned off" : "Jev: key saved";
}

function WorkflowSource({ id, title, summary, children }) {
  const [open, setOpen] = useState(false);
  const titleId = `${id}-title`;
  return <section className="px-3 py-2.5" data-workflow-source={id}>
    <h4 className="text-xs">
      <button type="button" aria-expanded={open} aria-controls={`${id}-form`} onClick={() => setOpen(value => !value)}
        className="group flex w-full min-w-0 items-baseline gap-2 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-brand/40">
        <ChevronRight aria-hidden="true" size={14} className={`shrink-0 self-center text-muted transition-transform duration-150 motion-reduce:transition-none ${open ? "rotate-90" : ""}`} />
        <span id={titleId} className="font-semibold text-ink group-hover:text-brand">{title}</span>
        <span className="min-w-0 truncate text-[11px] font-normal text-muted" data-workflow-source-summary>{summary}</span>
      </button>
    </h4>
    {open ? <div id={`${id}-form`} className="ml-[22px] mt-3 border-t border-line pt-3">{children(titleId)}</div> : null}
  </section>;
}

// Re-renders once a minute so "Checked 3 min ago" keeps counting while Settings stays open.
function useMinuteClock() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

/** Saves one provider's settings and tells every provider picker to reload. */
function useProviderSave(provider) {
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  async function save(changes) {
    setSaving(true);
    setError("");
    try {
      const response = await fetch(apiUrl("/provider/settings"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: provider.id, ...changes }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Could not save provider settings");
      window.dispatchEvent(new CustomEvent("raticode:providers-changed"));
      return true;
    } catch (failure) { setError(failure.message || "Could not save provider settings"); return false; }
    finally { setSaving(false); }
  }
  return { error, setError, saving, save };
}

// While a provider updates, its spinner is labelled so it can be announced and found.
function StatusLabel({ status, updating = "" }) {
  const { label, Icon, className, spin } = STATUSES[status];
  const icon = updating && spin ? { role: "status", "aria-label": updating } : { "aria-hidden": "true" };
  return <span className={`inline-flex items-center gap-1 font-semibold ${className}`} data-provider-status={status}>
    <Icon {...icon} size={13} className={spin ? "motion-safe:animate-spin" : ""} />{label}
  </span>;
}

function accountSummary(accounts) {
  if (!accounts.length) return "";
  const waiting = accounts.filter(account => account.state === "needs_sign_in").length;
  const count = `${accounts.length} account${accounts.length === 1 ? "" : "s"}`;
  return waiting ? `${count}, ${waiting} need${waiting === 1 ? "s" : ""} sign-in` : count;
}

function ProviderRow({ provider, accounts }) {
  const [open, setOpen] = useState(false);
  const [enabledDraft, setEnabledDraft] = useState(null);
  const { error, setError, saving, save } = useProviderSave(provider);
  const signIn = useProviderSignIn(provider.id);
  const name = provider.displayName || provider.id;
  const status = providerStatus(provider);
  const enabled = provider.enabled ?? provider.available ?? false;
  const accountList = accounts ? (accounts.state.overview?.accounts ?? []).filter(account => account.provider === provider.id) : [];
  const accountText = accountSummary(accountList);
  const busy = saving || provider.settingsPending;
  const detailsId = `provider-details-${provider.id}`;
  useEffect(() => { setEnabledDraft(null); }, [enabled]);
  async function toggle(next) {
    setEnabledDraft(next);
    if (!await save({ enabled: next })) setEnabledDraft(null);
  }
  function startSignIn() {
    // Apps without a browser sign-in explain how to sign in inside their details.
    if (provider.supportsBrowserLogin) void signIn.authenticate(signIn.pending ? "cancel" : "start");
    else setOpen(true);
  }
  return <section className="px-3 py-2.5" data-provider={provider.id}>
    <div className="flex items-center gap-3">
      <h4 className="min-w-0 flex-1 text-xs">
        <button type="button" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen(value => !value)}
          className="group flex w-full min-w-0 items-start gap-2 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-brand/40">
          <ChevronRight aria-hidden="true" size={14} className={`mt-0.5 shrink-0 text-muted transition-transform duration-150 motion-reduce:transition-none ${open ? "rotate-90" : ""}`} />
          <span className="min-w-0">
            <span className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-semibold text-ink group-hover:text-brand">{name}</span>
              {provider.version ? <span className="text-[10px] tabular-nums text-muted">{provider.version}</span> : null}
            </span>
            <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] font-normal">
              <StatusLabel status={status} updating={provider.refreshing ? `Updating ${name}` : ""} />
              {provider.refreshing && status !== "checking" ? <LoaderCircle role="status" aria-label={`Updating ${name}`} size={12} className="text-muted motion-safe:animate-spin" /> : null}
              {status === "ready" || provider.discoveryStatus === "access_denied" ? <span className="text-muted">Signed in</span> : null}
              {accountText ? <span className={accountList.some(account => account.state === "needs_sign_in") ? "text-amber-700" : "text-muted"}>· {accountText}</span> : null}
            </span>
          </span>
        </button>
      </h4>
      {status === "needs_sign_in" ? <button type="button" disabled={busy || signIn.starting}
        className="shrink-0 rounded-md border border-line px-2.5 py-1 text-xs font-semibold text-brand hover:bg-canvas disabled:opacity-60"
        aria-label={signIn.pending ? `Cancel ${name} sign-in` : `Sign in to ${name}`} onClick={startSignIn}>
        {signIn.pending ? "Cancel sign-in" : "Sign in"}
      </button> : null}
      <span className="shrink-0"><SwitchControl checked={provider.settingsPending ? false : enabledDraft ?? enabled} disabled={busy} label={`${name} enabled`} onChange={toggle} /></span>
    </div>
    <div className="ml-[22px] space-y-1 text-xs empty:hidden">
      {signIn.pending || signIn.starting ? <p role="status" className="flex items-center gap-2 text-muted">
        <LoaderCircle aria-hidden="true" size={13} className="motion-safe:animate-spin" />{signIn.starting ? "Opening sign-in…" : "Complete sign-in in your browser."}
      </p> : null}
      {signIn.error ? <p role="alert" className="text-red-600">{signIn.error}</p> : null}
      {status === "error" && provider.error ? <p className="text-muted">{provider.error}</p> : null}
      {overrideNotFound(provider) ? <p className="text-muted">{name} wasn&apos;t found at its saved location.</p> : null}
      {provider.refreshError ? <p role="alert" className="text-red-600">{provider.refreshError}</p> : null}
      {error ? <p role="alert" className="text-red-600">{error}</p> : null}
    </div>
    {open ? <div id={detailsId} role="region" aria-label={`${name} details`} className="ml-[22px] mt-3 space-y-4 border-t border-line pt-3">
      {provider.discoveryStatus === "missing" ? null : <ModelAccess provider={provider} saving={saving} save={save} />}
      <ModelDefaults provider={provider} saving={saving} save={save} />
      {accounts ? <ProviderAccounts provider={accounts.provider} accountsState={accounts.state} uses={accounts.uses} onChangeUse={accounts.onChangeUse} /> : null}
      <SignInDetails provider={provider} status={status} signIn={signIn} disabled={busy} />
      <AppLocation provider={provider} save={save} saving={busy} setError={setError} />
    </div> : null}
  </section>;
}

function DetailHeading({ children }) {
  return <h5 className="text-[11px] font-semibold text-ink">{children}</h5>;
}

function checkingExecutable(provider) {
  return provider.discoveryStatus === "pending" || Boolean(provider.settingsPending);
}

/** A saved executable override where no app was found. The backend reports this as discoveryStatus "missing". */
function overrideNotFound(provider) {
  return Boolean(provider.executableOverride) && !provider.detected && !checkingExecutable(provider);
}

/** One line for where the app runs from, such as "Automatic · /usr/bin/claude" or "Override · /opt/claude/bin/claude". */
export function appLocationSummary(provider) {
  const override = provider.executableOverride || "";
  const missing = checkingExecutable(provider) ? "checking…" : "not found";
  if (override) return `Override · ${override}${provider.detected || checkingExecutable(provider) ? "" : " · not found"}`;
  return `Automatic · ${provider.detected && provider.executable ? provider.executable : missing}`;
}

/** The executable fields, folded into one closed line until needed, or until a saved override wasn't found. */
function AppLocation({ provider, save, saving, setError }) {
  const overrideMissing = overrideNotFound(provider);
  const [open, setOpen] = useState(overrideMissing);
  const regionId = `provider-app-location-${provider.id}`;
  useEffect(() => { if (overrideMissing) setOpen(true); }, [overrideMissing]);
  const summary = appLocationSummary(provider);
  return <div className="space-y-2" data-app-location={provider.id}>
    <h5 className="text-[11px]">
      <button type="button" aria-expanded={open} aria-controls={regionId} onClick={() => setOpen(value => !value)}
        className="group flex w-full min-w-0 items-baseline gap-2 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-brand/40">
        <ChevronRight aria-hidden="true" size={13} className={`shrink-0 self-center text-muted transition-transform duration-150 motion-reduce:transition-none ${open ? "rotate-90" : ""}`} />
        <span className="shrink-0 font-semibold text-ink group-hover:text-brand">App location</span>
        <span className={`min-w-0 truncate font-normal ${overrideMissing ? "text-amber-700" : "text-muted"}`} title={summary} data-app-location-summary>{summary}</span>
      </button>
    </h5>
    {open ? <div id={regionId} className="ml-[21px]">
      <ExecutableFields provider={provider} save={save} saving={saving} setError={setError} />
    </div> : null}
  </div>;
}

/** Where the app was found, a typed override and the file picker. */
function ExecutableFields({ provider, save, saving, setError, showFound = true }) {
  const [draft, setDraft] = useState(provider.executableOverride || "");
  const focused = useRef(false);
  const name = provider.displayName || provider.id;
  useEffect(() => { if (!focused.current) setDraft(provider.executableOverride || ""); }, [provider.executableOverride]);
  async function browse() {
    try {
      const path = await window.goferDesktop.workspace.selectPath({ fileOnly: true, currentPath: draft || provider.executable || "" });
      if (path) { setDraft(path); await save({ executable: path }); }
    } catch (failure) { setError(failure.message || "Could not open file picker"); }
  }
  return <div className="space-y-1.5">
    {showFound ? <p className="break-all text-xs text-muted">{provider.detected ? `Found: ${provider.executable}` : checkingExecutable(provider) ? "Checking executable…" : "Executable not found"}</p> : null}
    <div className="flex items-end gap-3">
      <label className="min-w-0 flex-1 text-xs text-muted">Executable override
        <input aria-label={`${name} executable`} className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink" placeholder="Automatic discovery" value={draft} disabled={saving}
          onFocus={() => { focused.current = true; }} onChange={event => setDraft(event.target.value)}
          onBlur={() => { focused.current = false; if (draft !== (provider.executableOverride || "")) void save({ executable: draft }); }}
          onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); } }} />
      </label>
      <button type="button" className="shrink-0 pb-1.5 text-xs font-semibold text-brand disabled:opacity-50" aria-label={`Choose ${name} executable`}
        disabled={saving || !window.goferDesktop?.workspace?.selectPath} onClick={browse}>Choose executable…</button>
    </div>
  </div>;
}

/** Sign in again, or how to sign in when the app has no browser sign-in. */
function SignInDetails({ provider, status, signIn, disabled }) {
  const name = provider.displayName || provider.id;
  const refresh = () => window.dispatchEvent(new CustomEvent("raticode:providers-changed", { detail: { refresh: true } }));
  if (provider.discoveryStatus === "missing") return null;
  if (provider.discoveryStatus === "access_denied") return <div className="space-y-1.5"><DetailHeading>Sign-in</DetailHeading><ProviderAuthentication provider={provider} disabled={disabled} /></div>;
  if (!provider.supportsBrowserLogin) {
    if (status !== "needs_sign_in") return null;
    return <div className="space-y-1.5 text-xs text-muted">
      <DetailHeading>Sign-in</DetailHeading>
      <p>{provider.id === "antigravity" ? "Run agy in a terminal to sign in to Antigravity, then refresh providers." : <>Sign in through {name}&apos;s CLI setup, then refresh providers.</>}</p>
      <button type="button" disabled={disabled} className="font-semibold text-brand disabled:opacity-60" onClick={refresh}>Refresh providers</button>
    </div>;
  }
  // The row's Sign in button covers an app that needs sign-in.
  if (status === "needs_sign_in" || status === "checking") return null;
  return <div className="space-y-1.5 text-xs">
    <DetailHeading>Sign-in</DetailHeading>
    <p className="text-muted">Runs {name}&apos;s own sign-in again, for example to switch its default login.</p>
    <button type="button" disabled={disabled || signIn.starting} className="font-semibold text-brand disabled:opacity-60"
      onClick={() => void signIn.authenticate(signIn.pending ? "cancel" : "start")}>{signIn.pending ? "Cancel sign-in" : "Sign in again"}</button>
  </div>;
}

function ModelDefaults({ provider, saving, save }) {
  const models = provider.models ?? [];
  const name = provider.displayName || provider.id;
  const modelOverride = provider.defaultModelOverride || "";
  const effortOverride = provider.defaultEffortOverride || "";
  const defaultModel = models.find(model => model.id === provider.defaultModel) ?? models[0];
  const efforts = defaultModel?.efforts ?? [];
  const canSetDefaults = provider.available && provider.discoveryStatus === "ready" && models.length > 0;
  const deniedDefault = provider.deniedModels?.includes(modelOverride);
  const missingModel = modelOverride && !deniedDefault && !models.some(model => model.id === modelOverride);
  const missingEffort = effortOverride && !efforts.some(effort => effort.id === effortOverride);
  if (!canSetDefaults) return modelOverride || effortOverride
    ? <p className="text-xs text-muted">Saved defaults: {modelOverride || "Provider model"}{effortOverride ? `, ${effortOverride} effort` : ""}</p> : null;
  return <div className="space-y-2">
    <DetailHeading>Default model and effort</DetailHeading>
    <p className="text-xs text-muted">Used when you pick {name} in Raticode.</p>
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="block min-w-0 text-xs text-muted">Default model
        <select aria-label={`${name} default model`} className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink" disabled={saving}
          value={deniedDefault ? "" : modelOverride} onChange={event => save({ defaultModel: event.target.value, defaultEffort: "" })}>
          <option value="">Automatic{provider.defaultModel ? ` (${provider.defaultModel})` : ""}</option>
          {missingModel ? <option value={modelOverride}>{modelOverride} (unavailable)</option> : null}
          {menuModels(provider, modelOverride).map(model => <option key={model.id} value={model.id}>{model.displayName || model.id}</option>)}
        </select>
      </label>
      <label className="block min-w-0 text-xs text-muted">Default effort
        <select aria-label={`${name} default effort`} className="mt-1 w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink" disabled={saving || !efforts.length || Boolean(missingModel)}
          value={effortOverride} onChange={event => save({ defaultEffort: event.target.value })}>
          <option value="">{efforts.length ? `Provider default${defaultModel?.providerDefaultEffort ? ` (${defaultModel.providerDefaultEffort})` : ""}` : "Not supported by this model"}</option>
          {missingEffort ? <option value={effortOverride}>{effortOverride} (unavailable)</option> : null}
          {efforts.map(effort => <option key={effort.id} value={effort.id}>{effort.displayName || effort.id}</option>)}
        </select>
      </label>
    </div>
    {missingModel || missingEffort || deniedDefault ? <p className="text-xs text-muted">A saved default is unavailable. Raticode uses an allowed model until you choose another default.</p> : null}
    {modelOverride || effortOverride ? <button type="button" className="text-xs font-semibold text-brand disabled:opacity-50" disabled={saving}
      onClick={() => save({ defaultModel: "", defaultEffort: "" })}>Reset model and effort defaults</button> : null}
  </div>;
}

/** Apps whose CLI wasn't found, folded into one group that still lets you point at one. */
function NotInstalled({ providers, accountsFor }) {
  const [open, setOpen] = useState(false);
  return <section className="rounded-lg border border-line" data-provider-group="missing">
    <h4 className="text-xs">
      <button type="button" aria-expanded={open} aria-controls="providers-not-installed" onClick={() => setOpen(value => !value)}
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left font-semibold text-muted outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-brand/40">
        <ChevronRight aria-hidden="true" size={14} className={`shrink-0 transition-transform duration-150 motion-reduce:transition-none ${open ? "rotate-90" : ""}`} />
        Not installed ({providers.length})
      </button>
    </h4>
    {open ? <div id="providers-not-installed" className="space-y-3 border-t border-line px-3 pb-3 pt-2">
      <p className="text-xs text-muted">Raticode didn&apos;t find these apps. Install one, or choose its executable if it&apos;s somewhere Raticode doesn&apos;t look.</p>
      <ul className="divide-y divide-line">
        {providers.map(provider => <MissingProvider key={provider.id} provider={provider} accounts={accountsFor(provider)} />)}
      </ul>
    </div> : null}
  </section>;
}

function MissingProvider({ provider, accounts }) {
  const { error, setError, saving, save } = useProviderSave(provider);
  return <li className="space-y-2 py-2.5 first:pt-1" data-provider={provider.id}>
    <p className="text-xs font-semibold text-ink">{provider.displayName || provider.id}</p>
    <ExecutableFields provider={provider} save={save} saving={saving || provider.settingsPending} setError={setError} showFound={false} />
    {error ? <p role="alert" className="text-xs text-red-600">{error}</p> : null}
    {accounts ? <ProviderAccounts provider={accounts.provider} accountsState={accounts.state} uses={accounts.uses} onChangeUse={accounts.onChangeUse} /> : null}
  </li>;
}

// More models than this gets a search field above the list.
const MODEL_SEARCH_THRESHOLD = 8;

function ModelAccess({ provider, saving, save }) {
  const deniedKey = JSON.stringify(provider.deniedModels ?? []);
  const [denied, setDenied] = useState(() => JSON.parse(deniedKey));
  const [query, setQuery] = useState("");
  // A hidden alias that is shown again keeps its row until Settings closes, so it doesn't vanish under the pointer.
  const kept = useRef(new Set());
  useEffect(() => { setDenied(JSON.parse(deniedKey)); }, [deniedKey]);
  for (const id of denied) kept.current.add(id);
  const catalog = provider.discoveredModels ?? provider.models ?? [];
  const rows = catalog.filter(model => model.listed !== false || kept.current.has(model.id));
  for (const id of denied) if (!rows.some(model => model.id === id)) rows.push({ id, displayName: id });
  const shownCount = rows.filter(model => !denied.includes(model.id)).length;
  const disabled = saving || provider.settingsPending;
  const name = provider.displayName || provider.id;
  const needle = query.trim().toLowerCase();
  const visible = needle ? rows.filter(model => `${model.displayName || ""} ${model.id}`.toLowerCase().includes(needle)) : rows;
  async function update(next) {
    if (await save({ deniedModels: next })) setDenied(next);
  }
  const toggle = (id, shown) => update(shown ? denied.filter(other => other !== id) : [...denied, id]);
  return <div className="space-y-2">
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs">
      <span id={`${provider.id}-model-menus`} className="font-semibold text-ink">Show in model menus</span>
      {rows.length ? <span className="text-muted" role="status">{shownCount} of {rows.length} shown</span> : null}
      {rows.length ? <span className="ml-auto flex gap-3">
        <button type="button" className="font-semibold text-brand disabled:opacity-50" disabled={disabled || !denied.length} onClick={() => update([])}>Show all</button>
        <button type="button" className="font-semibold text-brand disabled:opacity-50" disabled={disabled || shownCount === 0}
          onClick={() => update([...new Set([...denied, ...rows.map(model => model.id)])])}>Hide all</button>
      </span> : null}
    </div>
    {rows.length > MODEL_SEARCH_THRESHOLD ? <input type="search" aria-label={`Search ${name} models`} placeholder="Search models"
      className="w-full rounded border border-line bg-white px-2 py-1.5 text-xs text-ink placeholder:text-muted focus-visible:outline-brand"
      value={query} onChange={event => setQuery(event.target.value)} /> : null}
    {rows.length ? <ul aria-labelledby={`${provider.id}-model-menus`} className="max-h-64 divide-y divide-line overflow-y-auto rounded border border-line">
      {visible.map(model => <li key={model.id}>
        <label className="flex cursor-pointer items-center gap-2 px-2 py-1.5 text-xs hover:bg-canvas">
          <input type="checkbox" aria-label={`Show ${model.displayName || model.id} in model menus`} checked={!denied.includes(model.id)} disabled={disabled}
            onChange={event => void toggle(model.id, event.target.checked)} />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-ink">{model.displayName || model.id}</span>
            {model.displayName && model.displayName !== model.id ? <span className="block truncate text-muted">{model.id}</span> : null}
          </span>
        </label>
      </li>)}
      {!visible.length ? <li className="px-2 py-1.5 text-xs text-muted">No models match “{query.trim()}”.</li> : null}
    </ul> : null}
    {!shownCount ? <p className="text-xs text-muted">{rows.length ? "All models are denied. Check a model to show it in model menus." : "No models discovered yet. Refresh providers after signing in."}</p> : null}
  </div>;
}
