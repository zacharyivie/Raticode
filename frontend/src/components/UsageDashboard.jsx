import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ChevronDown, ExternalLink, RefreshCw } from "lucide-react";
import { apiUrl } from "../lib/api.js";
import {
  allowanceLabel, formatUsageAmount, formatUsageNumber, formatUsageTime,
  remainingPercent, usageDashboardUrl, usageProviderName, usageStatus,
} from "../lib/usage.js";
import "./UsageDashboard.css";
import UsageCredentials from "./UsageCredentials.jsx";

export default function UsageDashboard() {
  const [overview, setOverview] = useState(null);
  const [days, setDays] = useState(30);
  const [view, setView] = useState("allowance");
  const [group, setGroup] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const request = useRef(null);
  const tabs = useRef([]);
  const id = useId();
  const load = useCallback(async (refresh = false, checkAccounts = false) => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError("");
    try {
      const read = async force => {
        const response = await fetch(apiUrl(`/usage/${force ? "refresh" : "overview"}?days=${days}`), {
          signal: controller.signal,
          ...(force ? { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" } : {}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Could not load usage. Try refreshing again.");
        if (!controller.signal.aborted) setOverview(data);
        return data;
      };
      let data = await read(refresh);
      if (checkAccounts && !controller.signal.aborted) data = await read(true);
      for (let attempt = 0; data.refreshing && attempt < 30 && !controller.signal.aborted; attempt += 1) {
        await new Promise(resolve => {
          const finish = () => { clearTimeout(timer); controller.signal.removeEventListener("abort", finish); resolve(); };
          const timer = setTimeout(finish, 1000);
          controller.signal.addEventListener("abort", finish, { once: true });
        });
        if (!controller.signal.aborted) data = await read(true);
      }
      if (data.refreshing && !controller.signal.aborted) setError("Provider usage is still updating. Try refreshing again shortly.");
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message || "Could not load usage. Try refreshing again.");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [days]);
  useEffect(() => {
    void load(false, true);
    const refresh = () => { void load(false, true); };
    window.addEventListener("raticode:providers-changed", refresh);
    return () => {
      request.current?.abort();
      window.removeEventListener("raticode:providers-changed", refresh);
    };
  }, [load]);

  const accounts = (overview?.accounts || []).filter(account => group === "all" || (group === "api") === account.provider?.endsWith("_api"));
  function tabKey(event, index) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? 1 : 1 - index;
    setView(next ? "tokens" : "allowance");
    tabs.current[next]?.focus();
  }
  return <div className="usage-dashboard">
    <div className="usage-title">
      <p>Check your allowance and see what Raticode has used.</p>
      <button className="usage-refresh" type="button" disabled={loading} onClick={() => load(true)}>
        <RefreshCw aria-hidden="true" size={13} />{loading ? "Checking…" : "Refresh usage"}
      </button>
    </div>
    <div role="tablist" aria-label="Usage view" className="usage-tabs">
      {[["allowance", "Remaining allowance"], ["tokens", "Tokens in Raticode"]].map(([key, label], index) => <button
        key={key} ref={element => { tabs.current[index] = element; }} type="button" role="tab"
        id={`${id}-${key}-tab`} aria-selected={view === key} aria-controls={`${id}-${key}-panel`}
        tabIndex={view === key ? 0 : -1} onClick={() => setView(key)} onKeyDown={event => tabKey(event, index)}
      >{label}</button>)}
    </div>
    {error ? <p role="alert" className="usage-error">{error}{overview ? " Showing the last loaded results." : ""}</p> : null}
    {loading ? <p role="status" className="usage-note">{overview ? "Updating usage…" : "Loading provider usage…"}</p> : null}
    <div role="tabpanel" id={`${id}-allowance-panel`} aria-labelledby={`${id}-allowance-tab`} hidden={view !== "allowance"}>
      <div className="usage-filters">
        <span>Provider account allowances</span>
        <label>Show <select value={group} onChange={event => setGroup(event.target.value)} aria-label="Usage account type">
          <option value="all">All accounts</option><option value="cli">Coding apps</option><option value="api">API profiles</option>
        </select></label>
      </div>
      <div className="usage-row-head" aria-hidden="true"><span>Provider / account</span><span>Remaining allowance</span></div>
      {accounts.map(account => <AccountRow key={account.id} account={account} onCredentialsSaved={() => load(true)} />)}
      {!loading && overview && !accounts.length ? <p className="usage-empty">No {group === "api" ? "API profiles" : "accounts"} to show. Add a provider in Settings → Providers.</p> : null}
      <p className="usage-note">Allowances keep each provider&apos;s own units and reset windows. A plan percentage does not tell you how many tokens remain.</p>
    </div>
    <div role="tabpanel" id={`${id}-tokens-panel`} aria-labelledby={`${id}-tokens-tab`} hidden={view !== "tokens"}>
      <div className="usage-filters"><span>All projects on this device</span><label>Period <select value={days} onChange={event => { setOverview(null); setDays(Number(event.target.value)); }} aria-label="Usage period">
        <option value={1}>Last 24 hours</option><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
      </select></label></div>
      {overview ? <TokenActivity activity={overview.activity || {}} trackingStartedAt={overview.tracking_started_at} /> : null}
    </div>
  </div>;
}

function AccountRow({ account, onCredentialsSaved }) {
  const windows = account.windows || [];
  const primary = windows[0];
  const url = usageDashboardUrl(account.dashboard_url);
  const name = usageProviderName(account.provider);
  return <details className="usage-account" data-status={account.status}>
    <summary>
      <span className="usage-identity"><strong>{name}</strong><span>{account.profile || account.label || "Provider account"}</span></span>
      <span className="usage-allowance">
        <strong>{primary ? allowanceLabel(primary) : usageStatus(account.status)}</strong>
        {primary ? <><span>{primary.label}{windows.length > 1 ? ` +${windows.length - 1} more` : ""}</span><AllowanceBar window={primary} /></> : null}
        <span className="usage-freshness">{primary ? `${usageStatus(account.status)}. ` : ""}{account.observed_at ? `Checked ${formatUsageTime(account.observed_at)}` : "No snapshot"}</span>
      </span>
      <ChevronDown size={13} aria-hidden="true" className="usage-chevron" />
    </summary>
    <div className="usage-account-details">
      {account.reason ? <p>{account.reason}</p> : null}
      {windows.map((window, index) => <section className="usage-window" key={`${window.id}:${window.model || ""}:${index}`} aria-label={window.label}>
        <div className="usage-window-title"><h4>{window.label}{window.model ? ` · ${window.model}` : ""}</h4><strong>{allowanceLabel(window)}</strong></div>
        <AllowanceBar window={window} />
        <dl><dt>Used</dt><dd>{formatUsageAmount(window.used, window.unit)}</dd>
          <dt>Limit</dt><dd>{window.unlimited ? "Unlimited" : formatUsageAmount(window.limit, window.unit)}</dd>
          <dt>Resets</dt><dd>{formatUsageTime(window.resets_at)}</dd></dl>
      </section>)}
      <dl><dt>Source</dt><dd>{account.source || "Not reported"}</dd><dt>Last checked</dt><dd>{formatUsageTime(account.observed_at)}</dd></dl>
      {url ? <a href={url} target="_blank" rel="noreferrer">Open {name} usage <ExternalLink aria-hidden="true" size={12} /></a> : null}
      {["claude_code", "cursor", "openai_api", "anthropic_api"].includes(account.provider) ? <UsageCredentials
        key={`${account.provider}:${account.profile || ""}`} provider={account.provider} profile={account.profile || null} onSaved={onCredentialsSaved}
      /> : null}
    </div>
  </details>;
}

function AllowanceBar({ window }) {
  const percent = remainingPercent(window);
  if (percent === null || window.unlimited) return null;
  return <span className={`usage-meter${percent <= 10 ? " usage-meter-low" : ""}`} role="meter" aria-label={`${window.label} remaining`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-valuetext={`${formatUsageNumber(percent)}% remaining`}><span style={{ width: `${percent}%` }} /></span>;
}

function TokenActivity({ activity, trackingStartedAt }) {
  const providers = activity.providers || [];
  return <>
    {!activity.calls ? <div className="usage-empty"><h3>No usage recorded in this period</h3><p>Usage appears here as you work with Rem, workflows, and swarms on this device.</p></div> : <>
      <div className="usage-total"><span>Reported tokens</span><strong>{formatUsageNumber(activity.total_tokens)}</strong><p>{formatUsageNumber(activity.reported_calls)} of {formatUsageNumber(activity.calls)} calls reported tokens. {formatUsageNumber(activity.unknown_calls)} without token data.</p></div>
      {activity.partial_calls > 0 ? <p className="usage-note">{formatUsageNumber(activity.partial_calls)} {activity.partial_calls === 1 ? "call reported only part of its" : "calls reported only part of their"} token usage.</p> : null}
      {activity.estimated_calls > 0 ? <p className="usage-note">{formatUsageNumber(activity.estimated_calls)} {activity.estimated_calls === 1 ? "call has" : "calls have"} estimated usage, {formatUsageNumber(activity.estimated_total_tokens)} estimated tokens. Estimates are excluded from reported totals.</p> : null}
      <div className="usage-table-scroll"><table className="usage-token-table"><caption>Provider token usage</caption><thead><tr><th scope="col">Provider</th><th scope="col">Input</th><th scope="col">Output</th><th scope="col">Total</th></tr></thead><tbody>
        {providers.map((provider, index) => <tr key={`${provider.provider}:${provider.profile || ""}:${index}`}>
          <th scope="row">{usageProviderName(provider.provider)}{provider.profile ? <small>{provider.profile}</small> : null}<small>{formatUsageNumber(provider.reported_calls)} / {formatUsageNumber(provider.calls)} calls reported</small></th>
          <td>{formatUsageNumber(provider.input_tokens)}</td><td>{formatUsageNumber(provider.output_tokens)}</td><td>{formatUsageNumber(provider.total_tokens)}</td>
        </tr>)}
      </tbody><tfoot><tr><th scope="row">Reported total</th><td>{formatUsageNumber(activity.input_tokens)}</td><td>{formatUsageNumber(activity.output_tokens)}</td><td>{formatUsageNumber(activity.total_tokens)}</td></tr></tfoot></table></div>
    </>}
    <p className="usage-note">Only provider-reported tokens are counted. Missing usage stays unknown. Account-wide usage in other apps is outside this history.</p>
    {trackingStartedAt ? <p className="usage-note">Tracking on this device since {formatUsageTime(trackingStartedAt)}.</p> : null}
  </>;
}
