import { useEffect, useId, useState } from "react";
import { LogIn, Sparkles } from "lucide-react";
import { checkedLabel, formatUsageNumber, formatUsageTime, remainingPercent, resetCountdown, SIGN_IN_LABELS, signInLabel, usageCards, usageSignIn, usageStatus, weeklyBurndown } from "../lib/usage.js";
import { providerAccountsRequest } from "./ProviderAccountsSettings.jsx";
import UsageSignIn from "./UsageSignIn.jsx";

const TICK_MS = 30_000;
const SIGN_IN_POLL_MS = 2_000;

/**
 * Settings > Usage, top of the page: one card per provider account. Each card shows its
 * own windows and never a combined figure, because accounts can have different limits.
 */
export default function UsageAccountCards({ accounts, remAccount, checking, onCheck }) {
  const [now, setNow] = useState(() => Date.now());
  const id = useId();
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, []);
  const cards = usageCards(accounts);
  if (!cards.length) return null;
  return <section className="usage-glance" aria-labelledby={`${id}-title`}>
    <div className="usage-glance-head">
      <h3 id={`${id}-title`}>Accounts at a glance</h3>
      <span>Each account has its own limits</span>
    </div>
    {remAccount && !remAccount.name && remAccount.why ? <p className="usage-glance-rem">Rem chooses by usage. {remAccount.why}</p> : null}
    <div className="usage-glance-grid">
      {cards.map(card => <AccountCard key={card.id} card={card} now={now} checking={checking} onCheck={onCheck} />)}
    </div>
  </section>;
}

function AccountCard({ card, now, checking, onCheck }) {
  const id = useId();
  const stateLabel = card.signIn ? signInLabel(card.signIn, card.providerName) : usageStatus(card.status);
  return <article className="usage-card" data-provider={card.provider} data-rem-next={card.remNext ? "true" : undefined} data-needs-sign-in={card.needsSignIn ? "true" : undefined} aria-labelledby={`${id}-name`}>
    <header className="usage-card-head">
      <span className="usage-card-name" id={`${id}-name`}>
        <span>{card.providerName}</span>
        <strong>{card.accountName}</strong>
      </span>
      <span className="usage-card-state" data-state={card.needsSignIn ? "needs_sign_in" : card.signIn || card.status}>{card.needsSignIn ? SIGN_IN_LABELS.needs_sign_in : stateLabel}</span>
    </header>
    {card.remNext ? <p className="usage-card-next"><span className="usage-card-badge"><Sparkles size={11} aria-hidden="true" />Rem uses next</span>{card.remNext.why ? <span>{card.remNext.why}</span> : null}</p> : null}
    {card.meters.length ? <div className="usage-card-meters">
      {card.meters.map(meter => <CardMeter key={meter.id} meter={meter} now={now} />)}
    </div> : card.needsSignIn ? null : <p className="usage-card-empty">{card.reason || "No allowance reading yet."}</p>}
    {card.needsSignIn ? null : <WeeklyBurndown account={card.account} />}
    {card.needsSignIn ? card.profile && card.signIn
      ? <AccountSignIn name={card.profile} checking={checking} onCheck={onCheck} />
      : usageSignIn(card.account) ? <UsageSignIn provider={card.provider} name={card.providerName} help={usageSignIn(card.account)} checking={checking} onCheck={onCheck} /> : null : null}
    <footer className="usage-card-foot" title={card.checkedAt ? formatUsageTime(card.checkedAt) : undefined}>{checkedLabel(card.checkedAt, now)}</footer>
  </article>;
}

function CardMeter({ meter, now }) {
  const window = meter.window;
  const left = window ? remainingPercent(window) : null;
  const countdown = window ? resetCountdown(window.resets_at, now) : "";
  return <div className="usage-card-meter">
    <span className="usage-card-meter-label">{meter.label}</span>
    <strong>{left === null ? window?.unlimited ? "Unlimited" : "Not reported" : `${formatUsageNumber(Math.round(left))}% left`}</strong>
    {left === null ? <span className="usage-meter usage-meter-empty" aria-hidden="true" /> : <span
      className={`usage-meter${left <= 10 ? " usage-meter-low" : ""}`} role="meter" aria-label={`${meter.label} remaining`}
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={left} aria-valuetext={`${formatUsageNumber(Math.round(left))}% remaining${countdown ? `, ${countdown}` : ""}`}
    ><span style={{ width: `${left}%` }} /></span>}
    <span className="usage-card-reset">{countdown || (window ? "Reset time not reported" : "No reading yet")}</span>
  </div>;
}

const CHART_W = 240;
const CHART_H = 46;

/**
 * The current weekly window as a small burndown of the provider's own readings, with an
 * even-pace line from the window's start to its reset, and one line about the pace.
 */
function WeeklyBurndown({ account }) {
  const chart = weeklyBurndown(account);
  if (!chart) return null;
  const line = chart.points.map(([x, y], index) => `${index ? "L" : "M"}${(x * CHART_W).toFixed(1)} ${(y * CHART_H).toFixed(1)}`).join(" ");
  const first = chart.points[0];
  const last = chart.points[chart.points.length - 1];
  const area = first ? `${line} L${(last[0] * CHART_W).toFixed(1)} ${CHART_H} L${(first[0] * CHART_W).toFixed(1)} ${CHART_H} Z` : "";
  const summary = `${chart.label} window this week${chart.left === null ? "" : `: ${Math.round(chart.left)}% left`}, from ${chart.count} ${chart.count === 1 ? "reading" : "readings"}. The dashed line is an even pace to the reset.`;
  return <figure className="usage-burndown" data-pace={chart.state}>
    <figcaption className="usage-burndown-head"><span>{chart.label} this window</span><span>{chart.count} {chart.count === 1 ? "reading" : "readings"}</span></figcaption>
    <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} preserveAspectRatio="none" role="img" aria-label={summary}>
      <line className="usage-burndown-grid" x1="0" y1={CHART_H / 2} x2={CHART_W} y2={CHART_H / 2} />
      <line className="usage-burndown-even" x1="0" y1="0" x2={CHART_W} y2={CHART_H} vectorEffect="non-scaling-stroke" />
      {area ? <path className="usage-burndown-area" d={area} /> : null}
      {line ? <path className="usage-burndown-line" d={line} vectorEffect="non-scaling-stroke" /> : null}
    </svg>
    <p className="usage-burndown-pace">{chart.text}</p>
  </figure>;
}

/** A Claude Code account's one-click sign-in, the same one Settings > Providers uses. */
function AccountSignIn({ name, checking, onCheck }) {
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const waiting = state?.state === "waiting";
  useEffect(() => {
    if (!waiting) return undefined;
    const timer = setInterval(async () => {
      try {
        const overview = await providerAccountsRequest();
        const next = overview.accounts?.find(account => account.name === name)?.sign_in || null;
        setState(next);
        if (next?.state !== "waiting") onCheck();
      } catch (failure) { setError(failure.message || "Could not check the sign-in."); }
    }, SIGN_IN_POLL_MS);
    return () => clearInterval(timer);
  }, [waiting, name, onCheck]);
  async function act(action) {
    setBusy(true);
    setError("");
    try {
      const overview = await providerAccountsRequest({ action, name });
      setState(overview.accounts?.find(account => account.name === name)?.sign_in || null);
      window.dispatchEvent(new CustomEvent("raticode:providers-changed"));
    } catch (failure) { setError(failure.message || "Could not start the sign-in."); }
    finally { setBusy(false); }
  }
  return <div className="usage-card-sign-in">
    {waiting ? <p role="status">Approve the sign-in in your browser. {state.url
      ? <>If no browser opened, <a href={state.url} target="_blank" rel="noreferrer">open the sign-in page</a>.</>
      : <>If no browser opened, run <code>Raticode provider account login {name}</code>.</>}</p>
      : <p>Rem skips this account until it&apos;s signed in.</p>}
    <div className="usage-sign-in-actions">
      {waiting
        ? <button type="button" className="usage-refresh" disabled={busy} onClick={() => act("cancel_sign_in")}>Cancel sign-in</button>
        : <button type="button" className="usage-sign-in-primary" disabled={busy || checking} onClick={() => act("sign_in")}><LogIn size={13} aria-hidden="true" />{busy ? "Opening sign-in…" : "Sign in"}</button>}
    </div>
    {state && !waiting && state.state !== "signed_in" && state.reason ? <p role="alert" className="usage-error">{state.reason}</p> : null}
    {error ? <p role="alert" className="usage-error">{error}</p> : null}
  </div>;
}
