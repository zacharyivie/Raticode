import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Check, CheckCircle2, Copy, ExternalLink, KeyRound, Link2, Loader2, LogIn, Pause, Pencil, Plug, Timer, TriangleAlert } from "lucide-react";
import {
  LATE_WINDOW_CHOICES, MAX_LATE_WINDOW, accountNotice, cancelSignIn, checkAccount, chooseSignInPage, connectAccount, disconnectAccount, durationLabel,
  reopenSignIn, saveSocialSettings, setPaused, signInStatus, startSignIn, updateAccount,
} from "../lib/socialApi.js";
import {
  CONNECT_ORDER, NO_SIGN_IN_NOTE, PLATFORM_GUIDES, SIGN_IN_GUIDES, connectBody, connectChoices, defaultChoice, defaultMethod, guideDocs, guideFields, guideMethods, guideSteps, methodLabel,
  signInBody, signInFields, signInMessage, signInRedirect, signInSteps, validateConnect, validateSignIn,
} from "../lib/socialPlatforms.js";
import { dateTime } from "../lib/timeText.js";
import { AccountStatus, DomainInput, LoginField, PlatformIcon, Stepper, Switch } from "./SocialPostsParts.jsx";
import { useRovingChoice } from "../lib/useRovingChoice.js";

// Posts > Accounts: connected accounts as cards, a guided Connect panel beside them, and the
// publishing rules underneath. `attempt` runs one backend call and returns { ok, value, error }
// without the page-wide error banner, so each part can show its own result.
export default function SocialAccounts({ data, attempt, busy, openLink, connectFor, onConnectHandled }) {
  const [connect, setConnect] = useState(() => ({ platform: connectFor || "", account: null }));
  const panel = useRef(null);
  const platforms = data.platforms || [];
  const label = id => platforms.find(item => item.id === id)?.label || id;

  useEffect(() => {
    if (!connectFor) return;
    setConnect({ platform: connectFor, account: null });
    onConnectHandled?.();
  }, [connectFor, onConnectHandled]);

  const startConnect = (platform, account = null) => {
    setConnect({ platform, account });
    window.requestAnimationFrame?.(() => panel.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" }));
  };

  return <div className="sp-view sp-accounts">
    <div className="sp-acc-layout">
      <section className="sp-acc-main" aria-labelledby="sp-accounts-h">
        <header className="sp-section-head">
          <div>
            <h2 id="sp-accounts-h">Your accounts</h2>
            <p className="sp-lede"><KeyRound size={13} aria-hidden="true" />Logins stay in your system keychain under “Raticode social accounts”. Rem, workflows and the phone can't read them.</p>
          </div>
          {data.accounts.length ? <span className="sp-count-badge">{data.accounts.length} connected</span> : null}
        </header>
        {data.accounts.length ? <ul className="sp-acc-grid">
          {data.accounts.map(account => <li key={account.id}>
            <AccountCard account={account} label={label(account.platform)} attempt={attempt} busy={busy} onReconnect={() => startConnect(account.platform, account)} />
          </li>)}
        </ul> : <EmptyAccounts onPick={platform => startConnect(platform)} />}
      </section>
      <aside className="sp-acc-side" ref={panel} aria-label="Connect an account">
        <ConnectPanel key={`${connect.platform}:${connect.account?.id || ""}`} platform={connect.platform} account={connect.account} label={label}
          info={platforms.find(item => item.id === connect.platform)?.sign_in} attempt={attempt} busy={busy} openLink={openLink}
          onPick={platform => setConnect({ platform, account: null })} onBack={() => setConnect({ platform: "", account: null })} />
      </aside>
    </div>
    <PublishingRules data={data} attempt={attempt} busy={busy} />
  </div>;
}

function EmptyAccounts({ onPick }) {
  return <div className="sp-empty-state">
    <div className="sp-empty-icons" aria-hidden="true">{CONNECT_ORDER.map(id => <PlatformIcon key={id} platform={id} size={34} />)}</div>
    <h3>Connect your first account</h3>
    <p>Raticode posts only what you approve, at the time you choose. Discord is the quickest start: a webhook into a private test channel takes about a minute.</p>
    <button type="button" className="oh-btn primary" onClick={() => onPick("discord")}><Plug size={14} aria-hidden="true" />Connect Discord</button>
  </div>;
}

function AccountCard({ account, label, attempt, busy, onReconnect }) {
  const [result, setResult] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [renaming, setRenaming] = useState(null);
  const [testing, setTesting] = useState(false);
  const update = body => attempt(() => updateAccount(account.id, body)).then(outcome => { if (!outcome.ok) setResult({ ok: false, message: outcome.error }); });
  const test = async () => {
    setTesting(true);
    const outcome = await attempt(() => checkAccount(account.id));
    setTesting(false);
    setResult(outcome.ok
      ? { ok: true, message: outcome.value?.remote_name ? `Works. ${label} knows this login as “${outcome.value.remote_name}”.` : `Works. ${label} accepted the login.` }
      : { ok: false, message: outcome.error });
  };
  const rename = () => {
    const name = (renaming || "").trim();
    setRenaming(null);
    if (name && name !== account.name) update({ name });
  };
  const notice = accountNotice(account);
  return <article className={`sp-acc-card st-${account.status}`} aria-label={`${label}: ${account.name}`}>
    <header className="sp-acc-head">
      <PlatformIcon platform={account.platform} size={36} />
      <div className="sp-acc-title">
        {renaming != null ? <input className="sp-input sp-rename" aria-label="Account name" value={renaming} autoFocus
          onChange={event => setRenaming(event.target.value)} onBlur={rename}
          onKeyDown={event => { if (event.key === "Enter") rename(); if (event.key === "Escape") setRenaming(null); }} />
          : <h3>{account.name}<button type="button" className="sp-icon-btn" aria-label={`Rename ${account.name}`} onClick={() => setRenaming(account.name)}><Pencil size={12} aria-hidden="true" /></button></h3>}
        <p>{label}{account.remote_name ? ` · ${account.remote_name}` : ""}{methodLabel(account.platform, account.auth, account.signed_in) ? ` · ${methodLabel(account.platform, account.auth, account.signed_in)}` : ""}</p>
      </div>
      <AccountStatus status={account.status} />
    </header>
    {notice && account.status !== "disabled" ? <p className="sp-acc-notice"><TriangleAlert size={12} aria-hidden="true" />{account.status === "needs_reconnect" ? "This login stopped working. Reconnect to keep posting." : `Reconnect in ${account.expires_in_days ?? 0} days to keep posting.`}</p> : null}
    {account.check_error && !result ? <p className="sp-acc-notice"><TriangleAlert size={12} aria-hidden="true" />Last test: {account.check_error}</p> : null}
    <p className="sp-acc-meta">{account.last_posted_at ? `Last post ${dateTime(account.last_posted_at, { weekday: true })}` : "No posts yet"}</p>
    <div className="sp-acc-settings">
      <Switch label="Posting on" description="Off holds its posts until you turn it back on." checked={account.enabled} disabled={busy} onChange={enabled => update({ enabled })} />
      <Switch label="Only my own posts" description="Rem, tasks and workflows can't propose posts for this account." checked={account.quiet} disabled={busy} onChange={quiet => update({ quiet })} />
      {account.platform === "linkedin" ? <Switch label="Send without a tap" description="Off: you tap Publish at the time. LinkedIn's API terms bar automated posting, so leave this off unless you're sure."
        checked={account.options?.send_without_tap === true} disabled={busy} onChange={value => update({ options: { send_without_tap: value } })} /> : null}
      {account.platform === "youtube" ? <div className="sp-choice-row">
        <span id={`${account.id}-privacy`}>New videos are</span>
        <div className="oh-seg" role="group" aria-labelledby={`${account.id}-privacy`}>
          {[["public", "Public"], ["unlisted", "Unlisted"]].map(([value, text]) => <button key={value} type="button" aria-pressed={(account.options?.privacy || "public") === value} disabled={busy}
            onClick={() => update({ options: { privacy: value } })}>{text}</button>)}
        </div>
      </div> : null}
      {account.platform === "discord" ? <div className="sp-choice-row">
        <span id={`${account.id}-mentions`}>Posts may ping</span>
        <div className="sp-chips" role="group" aria-labelledby={`${account.id}-mentions`}>
          {[["users", "People"], ["roles", "Roles"], ["everyone", "@everyone"]].map(([value, text]) => {
            const on = (account.options?.allowed_mentions || []).includes(value);
            return <button key={value} type="button" className="sp-chip-btn" aria-pressed={on} disabled={busy}
              onClick={() => update({ options: { allowed_mentions: on ? account.options.allowed_mentions.filter(item => item !== value) : [...(account.options?.allowed_mentions || []), value] } })}>
              {on ? <Check size={11} aria-hidden="true" /> : null}{text}</button>;
          })}
        </div>
      </div> : null}
      <div className="sp-limits" role="group" aria-label="Posting limits">
        <Stepper label="Posts a day, at most" value={account.daily_cap} disabled={busy} onCommit={value => update({ daily_cap: value })} />
        <Stepper label="Posts a month, at most" value={account.monthly_cap} disabled={busy} onCommit={value => update({ monthly_cap: value })} />
      </div>
    </div>
    {result ? <p className={`sp-result ${result.ok ? "ok" : "bad"}`} role="status">
      {result.ok ? <CheckCircle2 size={13} aria-hidden="true" /> : <TriangleAlert size={13} aria-hidden="true" />}{result.message}</p> : null}
    {confirming ? <div className="sp-confirm" role="group" aria-label="Confirm disconnect">
      <p>Disconnect {account.name}? Its login is deleted from your keychain. Posts scheduled for it will fail at their time; duplicate them to another account first.</p>
      <div className="sp-actions">
        <button type="button" className="oh-btn small danger" disabled={busy} onClick={async () => { const outcome = await attempt(() => disconnectAccount(account.id)); if (!outcome.ok) { setConfirming(false); setResult({ ok: false, message: outcome.error }); } }}>Disconnect</button>
        <button type="button" className="oh-btn small ghost" onClick={() => setConfirming(false)}>Keep it</button>
      </div>
    </div> : <footer className="sp-actions">
      <button type="button" className="oh-btn small" disabled={busy || testing} onClick={test}>{testing ? <Loader2 size={13} className="sp-spin" aria-hidden="true" /> : <Plug size={13} aria-hidden="true" />}{testing ? "Testing…" : "Test"}</button>
      {account.signed_in && SIGN_IN_GUIDES[account.platform] && account.status !== "connected" && account.status !== "disabled"
        ? <button type="button" className="oh-btn small primary" disabled={busy} onClick={onReconnect}><LogIn size={13} aria-hidden="true" />Sign in again</button>
        : <button type="button" className="oh-btn small" disabled={busy} onClick={onReconnect}><KeyRound size={13} aria-hidden="true" />Replace login</button>}
      <span className="grow" />
      <button type="button" className="oh-btn small ghost sp-danger-text" disabled={busy} onClick={() => setConfirming(true)}>Disconnect</button>
    </footer>}
  </article>;
}

// Choose a platform, follow its steps, paste the login, then Raticode saves and tests it.
function ConnectPanel({ platform, account, label, info, attempt, busy, openLink, onPick, onBack }) {
  const replacing = Boolean(account);
  const guide = PLATFORM_GUIDES[platform];
  const methods = guideMethods(platform);
  const choices = connectChoices(platform);
  // Replacing keeps the account's own way of connecting; a new account starts on the default.
  const [method, setMethod] = useState(() => {
    if (!account) return defaultChoice(platform);
    if (account.signed_in && SIGN_IN_GUIDES[platform]) return "signin";
    if (account.auth && methods.some(item => item.id === account.auth)) return account.auth;
    return platform === "x" ? "oauth2" : defaultMethod(platform);
  });
  const signingIn = method === "signin" && Boolean(SIGN_IN_GUIDES[platform]) && info?.available !== false;
  const chosen = methods.find(item => item.id === method) || methods[0];
  const fields = guideFields(platform, chosen?.id);
  const [name, setName] = useState(account?.name || "");
  const [values, setValues] = useState(() => (account?.options?.page_id ? { page_id: account.options.page_id } : {}));
  const [touched, setTouched] = useState({});
  const [submitted, setSubmitted] = useState(false);
  const [privacy, setPrivacy] = useState(account?.options?.privacy || "public");
  const [phase, setPhase] = useState({ step: "form" });
  const firstField = useRef(null);
  useEffect(() => { if (platform) firstField.current?.focus(); }, [platform]);

  if (!guide) {
    return <div className="sp-connect">
      <h2>Connect an account</h2>
      <p className="sp-lede">Pick where your posts should go. Each one takes a login you create on that platform; Raticode walks you through it.</p>
      <ul className="sp-platform-picker" aria-label="Platforms">
        {CONNECT_ORDER.map(id => <li key={id}><button type="button" className="sp-platform-tile" onClick={() => onPick(id)}>
          <PlatformIcon platform={id} size={32} />
          <span><strong>{label(id)}</strong><small>{PLATFORM_GUIDES[id].tagline}</small></span>
        </button></li>)}
      </ul>
    </div>;
  }

  // After a failed test the login is already saved, so a retry replaces it like Reconnect.
  const savedId = account?.id || phase.account?.id;
  const savedMethod = phase.account?.auth || account?.auth || (savedId && platform === "x" ? "oauth2" : chosen?.id);
  const errors = validateConnect(platform, values, { replacing: Boolean(savedId) && savedMethod === chosen?.id, method: chosen?.id });
  const shown = key => (submitted || touched[key]) ? errors[key] : "";
  const submit = async event => {
    event.preventDefault();
    setSubmitted(true);
    if (Object.keys(errors).length) {
      const first = fields.find(field => errors[field.key]);
      document.getElementById?.(`sp-login-${first?.key}`)?.focus?.();
      return;
    }
    setPhase(current => ({ step: "saving", account: current.account }));
    const saved = await attempt(() => connectAccount(connectBody(platform, name, values, { id: savedId, privacy, method: chosen?.id })));
    if (!saved.ok) { setPhase(current => ({ ...current, step: current.step === "saving" && current.account ? "test-failed" : "form", error: saved.error })); return; }
    setValues(current => Object.fromEntries(Object.entries(current).filter(([key]) => fields.find(field => field.key === key)?.target === "options")));
    setPhase({ step: "testing", account: saved.value });
    const checked = await attempt(() => checkAccount(saved.value.id));
    setPhase(checked.ok ? { step: "done", account: checked.value || saved.value } : { step: "test-failed", account: saved.value, error: checked.error });
  };

  if (phase.step === "done") {
    return <div className="sp-connect sp-connect-done" role="status">
      <CheckCircle2 size={34} aria-hidden="true" className="sp-done-icon" />
      <h2>{phase.account?.name || label(platform)} is connected</h2>
      <p>{phase.account?.remote_name ? `${label(platform)} confirmed the login belongs to “${phase.account.remote_name}”.` : `${label(platform)} accepted the login.`} You can now pick it when you write a post.</p>
      <div className="sp-actions"><button type="button" className="oh-btn" onClick={onBack}>Connect another account</button></div>
    </div>;
  }

  const working = phase.step === "saving" || phase.step === "testing";
  const pick = id => { setMethod(id); setValues(current => Object.fromEntries(Object.entries(current).filter(([key]) => key === "page_id"))); setTouched({}); setSubmitted(false); };
  const head = <>
    <button type="button" className="sp-back" onClick={onBack}><ArrowLeft size={13} aria-hidden="true" />All platforms</button>
    <header className="sp-connect-head">
      <PlatformIcon platform={platform} size={36} />
      <div>
        <h2 id="sp-connect-h">{replacing ? `${signingIn ? "Sign in again for" : "Replace the login for"} ${account.name}` : `Connect ${label(platform)}`}</h2>
        <p>{guide.tagline}</p>
      </div>
    </header>
    {choices.length > 1 ? <div className="sp-choice-row sp-method-row">
      <span id="sp-connect-method">Connect with</span>
      <div className="oh-seg" role="group" aria-labelledby="sp-connect-method">
        {choices.map(item => <button key={item.id} type="button" aria-pressed={(signingIn ? "signin" : chosen?.id) === item.id} disabled={working}
          onClick={() => pick(item.id)}>{item.signIn ? <LogIn size={12} aria-hidden="true" /> : null}{item.label}</button>)}
      </div>
    </div> : null}
  </>;
  if (signingIn) {
    return <SignInForm platform={platform} account={account} label={label(platform)} info={info} attempt={attempt} busy={busy} openLink={openLink} onBack={onBack} head={head} />;
  }
  return <form className="sp-connect" onSubmit={submit} noValidate aria-labelledby="sp-connect-h">
    {head}
    {chosen?.note ? <p className="sp-field-hint sp-method-note">{chosen.note}</p> : null}
    {platform === "discord" ? <p className="sp-field-hint sp-method-note">{NO_SIGN_IN_NOTE}</p> : null}
    <ol className="sp-steps">{guideSteps(platform, chosen?.id).map(step => <li key={step}>{step}</li>)}</ol>
    {guide.tip ? <p className="sp-tip">{guide.tip}</p> : null}
    {guideDocs(platform, chosen?.id) ? <button type="button" className="sp-link-btn" onClick={() => openLink(guideDocs(platform, chosen?.id))}><ExternalLink size={12} aria-hidden="true" />{label(platform)} developer docs</button> : null}
    <div className="sp-login-fields">
      {replacing ? null : <div className="sp-login-field">
        <label htmlFor="sp-connect-name">Name it<span className="sp-optional"> (optional)</span></label>
        <div className="sp-login-input"><input id="sp-connect-name" ref={firstField} value={name} maxLength={80} placeholder={guide.namePlaceholder} onChange={event => setName(event.target.value)} /></div>
        <p className="sp-field-hint">Only you see this, on cards and in the post editor.</p>
      </div>}
      {fields.map((field, index) => <LoginField key={field.key} id={`sp-login-${field.key}`} field={field} value={values[field.key]} error={shown(field.key)} disabled={working}
        inputRef={replacing && index === 0 ? firstField : undefined}
        onChange={value => setValues(current => ({ ...current, [field.key]: value }))} onBlur={() => setTouched(current => ({ ...current, [field.key]: true }))} />)}
      {platform === "youtube" ? <div className="sp-choice-row">
        <span id="sp-connect-privacy">New videos are</span>
        <div className="oh-seg" role="group" aria-labelledby="sp-connect-privacy">
          {[["public", "Public"], ["unlisted", "Unlisted"]].map(([value, text]) => <button key={value} type="button" aria-pressed={privacy === value} onClick={() => setPrivacy(value)}>{text}</button>)}
        </div>
      </div> : null}
      {savedId ? <p className="sp-field-hint">Fields you leave empty keep their saved values.</p> : null}
    </div>
    {phase.error && phase.step === "form" ? <p className="sp-result bad" role="alert"><TriangleAlert size={13} aria-hidden="true" />{phase.error}</p> : null}
    {phase.step === "test-failed" ? <div className="sp-result bad" role="alert">
      <TriangleAlert size={13} aria-hidden="true" />
      <div><strong>Saved, but the test didn't pass.</strong> {phase.error} Paste a corrected login above and save again, test again, or fix it later from the account's card.</div>
    </div> : null}
    {phase.step === "test-failed" ? <div className="sp-actions">
      <button type="button" className="oh-btn small" disabled={busy} onClick={async () => {
        setPhase(current => ({ ...current, step: "testing" }));
        const checked = await attempt(() => checkAccount(savedId));
        setPhase(current => (checked.ok ? { step: "done", account: checked.value || current.account } : { ...current, step: "test-failed", error: checked.error }));
      }}><Plug size={13} aria-hidden="true" />Test again</button>
    </div> : null}
    <div className="sp-actions">
      <button type="submit" className="oh-btn primary" disabled={busy || working}>
        {working ? <Loader2 size={14} className="sp-spin" aria-hidden="true" /> : <Plug size={14} aria-hidden="true" />}
        {phase.step === "saving" ? "Saving to your keychain…" : phase.step === "testing" ? `Testing with ${label(platform)}…` : replacing || phase.step === "test-failed" ? "Save and test" : "Connect and test"}
      </button>
      <button type="button" className="oh-btn ghost" onClick={onBack}>Cancel</button>
    </div>
    <p className="sp-field-hint">Testing asks {label(platform)} who the login belongs to. It doesn't post anything.</p>
  </form>;
}

// Sign in through the system browser: Raticode listens on this computer's loopback address
// for a few minutes, the platform sends the browser back there, and the backend saves the
// tokens to the keychain. Polls the backend for progress; leaving the panel cancels.
function SignInForm({ platform, account, label, info, attempt, busy, openLink, onBack, head }) {
  const guide = SIGN_IN_GUIDES[platform];
  const replacing = Boolean(account);
  const savedApp = Boolean(account?.signed_in);
  const fields = signInFields(platform, info);
  const [name, setName] = useState(account?.name || "");
  const [values, setValues] = useState(() => ({
    port: String(account?.sign_in_port || info?.default_port || ""),
    ...(account?.options?.page_id ? { page_id: account.options.page_id } : {}),
  }));
  const [touched, setTouched] = useState({});
  const [submitted, setSubmitted] = useState(false);
  const [privacy, setPrivacy] = useState(account?.options?.privacy || "public");
  const [session, setSession] = useState(null);
  const [phase, setPhase] = useState({ step: "form" });
  const [copied, setCopied] = useState(false);
  const [page, setPage] = useState("");
  const live = useRef(null);
  const firstField = useRef(null);
  live.current = session;
  useEffect(() => { firstField.current?.focus(); }, []);
  // Leaving the panel while the browser is still out closes the local listener.
  useEffect(() => () => {
    const current = live.current;
    if (current && (current.status === "waiting" || current.status === "choose_page")) cancelSignIn(current.id).catch(() => {});
  }, []);
  useEffect(() => {
    if (!session || (session.status !== "waiting" && session.status !== "exchanging")) return undefined;
    const timer = window.setTimeout(async () => {
      try {
        setSession(await signInStatus(session.id));
      } catch (failure) {
        setSession(current => ({ ...current, status: "failed", error: failure.message || "Raticode lost track of the sign-in. Start it again." }));
      }
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [session]);
  // Signed in: test the login the way a pasted one is tested.
  const tested = useRef("");
  useEffect(() => {
    if (session?.status !== "done" || !session.account || tested.current === session.id) return;
    tested.current = session.id;
    const account = session.account;
    setPhase({ step: "testing", account });
    attempt(() => checkAccount(account.id)).then(checked => {
      if (live.current?.id === session.id) setPhase(checked.ok ? { step: "done", account: checked.value || account } : { step: "test-failed", account, error: checked.error });
    });
  }, [session, attempt]);

  const redirect = signInRedirect(info, values.port);
  const errors = validateSignIn(platform, values, { replacing: savedApp, info });
  const shown = key => (submitted || touched[key]) ? errors[key] : "";
  const active = session && (session.status === "waiting" || session.status === "exchanging" || session.status === "choose_page");
  const start = async event => {
    event.preventDefault();
    setSubmitted(true);
    if (Object.keys(errors).length) {
      const first = fields.find(field => errors[field.key]);
      document.getElementById?.(`sp-signin-${first?.key}`)?.focus?.();
      return;
    }
    setCopied(false);
    setPhase({ step: "form" });
    setSession({ status: "starting" });
    const started = await attempt(() => startSignIn(signInBody(platform, name, values, { id: account?.id, privacy })));
    setSession(started.ok ? started.value : { status: "failed", error: started.error });
  };
  const cancel = async () => {
    if (!session?.id) return;
    const outcome = await attempt(() => cancelSignIn(session.id));
    setSession(outcome.ok ? outcome.value : { ...session, status: "cancelled", error: "You cancelled the sign-in." });
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(session.authorize_url); setCopied(true); } catch { setCopied(false); }
  };

  if (phase.step === "done") {
    return <div className="sp-connect sp-connect-done" role="status">
      <CheckCircle2 size={34} aria-hidden="true" className="sp-done-icon" />
      <h2>{phase.account?.name || label} is connected</h2>
      <p>{phase.account?.remote_name ? `${label} confirmed you signed in as “${phase.account.remote_name}”.` : `${label} accepted the sign-in.`} You can now pick it when you write a post.</p>
      <div className="sp-actions"><button type="button" className="oh-btn" onClick={onBack}>Connect another account</button></div>
    </div>;
  }

  const ended = session && ["failed", "cancelled", "expired"].includes(session.status);
  return <form className="sp-connect" onSubmit={start} noValidate aria-labelledby="sp-connect-h">
    {head}
    <p className="sp-field-hint sp-method-note">{guide.note}</p>
    <ol className="sp-steps">{signInSteps(platform, redirect).map(step => <li key={step}>{step}</li>)}</ol>
    {info?.fixed_port ? <div className="sp-redirect">
      <span>Redirect address to add in your app</span>
      <code>{redirect}</code>
      <button type="button" className="sp-icon-btn" aria-label="Copy the redirect address" onClick={() => navigator.clipboard?.writeText(redirect).catch(() => {})}><Copy size={12} aria-hidden="true" /></button>
    </div> : null}
    <button type="button" className="sp-link-btn" onClick={() => openLink(guide.docs)}><ExternalLink size={12} aria-hidden="true" />How {label} sign-in works</button>
    <div className="sp-login-fields">
      {replacing ? null : <div className="sp-login-field">
        <label htmlFor="sp-signin-name">Name it<span className="sp-optional"> (optional)</span></label>
        <div className="sp-login-input"><input id="sp-signin-name" ref={firstField} value={name} maxLength={80} placeholder={PLATFORM_GUIDES[platform].namePlaceholder} disabled={active} onChange={event => setName(event.target.value)} /></div>
        <p className="sp-field-hint">Only you see this, on cards and in the post editor.</p>
      </div>}
      {fields.map((field, index) => <LoginField key={field.key} id={`sp-signin-${field.key}`} field={{ ...field, required: field.required && !(savedApp && field.key !== "port") }}
        value={values[field.key]} error={shown(field.key)} disabled={active}
        inputRef={replacing && index === 0 ? firstField : undefined}
        onChange={value => setValues(current => ({ ...current, [field.key]: value }))} onBlur={() => setTouched(current => ({ ...current, [field.key]: true }))} />)}
      {platform === "youtube" ? <div className="sp-choice-row">
        <span id="sp-signin-privacy">New videos are</span>
        <div className="oh-seg" role="group" aria-labelledby="sp-signin-privacy">
          {[["public", "Public"], ["unlisted", "Unlisted"]].map(([value, text]) => <button key={value} type="button" aria-pressed={privacy === value} disabled={active} onClick={() => setPrivacy(value)}>{text}</button>)}
        </div>
      </div> : null}
      {savedApp ? <p className="sp-field-hint">Leave the client ID and secret empty to use the ones saved with this account.</p> : null}
    </div>
    {session && session.status !== "starting" && !ended && session.status !== "done" ? <div className="sp-signin-wait" role="status" aria-live="polite">
      <p>{session.status === "choose_page" ? null : <Loader2 size={14} className="sp-spin" aria-hidden="true" />}{signInMessage(session, info?.label || label)}
        {session.status === "waiting" ? <span className="sp-field-hint"> {Math.floor(session.seconds_left / 60)}:{String(session.seconds_left % 60).padStart(2, "0")} left</span> : null}</p>
      {session.status === "choose_page" ? <fieldset className="sp-page-pick">
        <legend>Pages you manage</legend>
        {session.pages.map(item => <label key={item.id}><input type="radio" name="sp-page" value={item.id} checked={page === item.id} onChange={() => setPage(item.id)} />{item.name} <span className="sp-field-hint">{item.id}</span></label>)}
      </fieldset> : null}
      <div className="sp-actions">
        {session.status === "choose_page" ? <button type="button" className="oh-btn small primary" disabled={!page || busy} onClick={async () => {
          const chosen = await attempt(() => chooseSignInPage(session.id, page));
          setSession(chosen.ok ? chosen.value : { ...session, status: "failed", error: chosen.error });
        }}>Use this Page</button> : null}
        {session.status === "waiting" ? <>
          <button type="button" className="oh-btn small" disabled={busy} onClick={async () => { const again = await attempt(() => reopenSignIn(session.id)); if (again.ok) setSession(again.value); }}><ExternalLink size={13} aria-hidden="true" />Open the page again</button>
          <button type="button" className="oh-btn small" onClick={copy}><Copy size={13} aria-hidden="true" />{copied ? "Link copied" : "Copy sign-in link"}</button>
        </> : null}
        <button type="button" className="oh-btn small ghost" disabled={session.status === "exchanging"} onClick={cancel}>Cancel sign-in</button>
      </div>
    </div> : null}
    {ended ? <p className="sp-result bad" role="alert"><TriangleAlert size={13} aria-hidden="true" />{session.error || "The sign-in stopped."}</p> : null}
    {phase.step === "test-failed" ? <div className="sp-result bad" role="alert">
      <TriangleAlert size={13} aria-hidden="true" />
      <div><strong>Signed in and saved, but the test didn't pass.</strong> {phase.error} Test it again from the account's card.</div>
    </div> : null}
    {phase.step === "testing" ? <p className="sp-field-hint" role="status"><Loader2 size={13} className="sp-spin" aria-hidden="true" />Testing with {label}…</p> : null}
    {active || phase.step === "testing" ? null : <div className="sp-actions">
      <button type="submit" className="oh-btn primary" disabled={busy || session?.status === "starting"}>
        {session?.status === "starting" ? <Loader2 size={14} className="sp-spin" aria-hidden="true" /> : <LogIn size={14} aria-hidden="true" />}
        {ended ? "Try again" : guide.button}
      </button>
      <button type="button" className="oh-btn ghost" onClick={onBack}>Cancel</button>
    </div>}
    <p className="sp-field-hint">Your password goes only to {info?.label || label}'s page in your browser. Raticode receives a one-time code on this computer, checks it, and keeps the result in your keychain. It doesn't post anything.</p>
  </form>;
}

// Plain-language settings for every post: trusted link sites, how late is too late, pause.
function PublishingRules({ data, attempt, busy }) {
  const [saved, setSaved] = useState("");
  const [problem, setProblem] = useState("");
  const late = data.settings?.late_window_minutes ?? 120;
  const allowlist = data.settings?.link_allowlist || [];
  const custom = !LATE_WINDOW_CHOICES.includes(late);
  const save = async (body, what) => {
    setSaved("");
    setProblem("");
    const outcome = await attempt(() => saveSocialSettings(body));
    if (outcome.ok) setSaved(what); else setProblem(outcome.error);
  };
  const pickLate = minutes => save({ late_window_minutes: minutes }, "Late limit");
  // Not disabled while a save runs: that would drop keyboard focus out of the group.
  const lateChoice = useRovingChoice({ keys: LATE_WINDOW_CHOICES, selected: late, onSelect: pickLate, busy });
  return <section className="sp-rules" aria-labelledby="sp-rules-h">
    <header className="sp-section-head">
      <div>
        <h2 id="sp-rules-h">Publishing rules</h2>
        <p className="sp-lede">These apply to every post and save as you change them.</p>
      </div>
      <span className="sp-saved" role="status" aria-live="polite">{saved ? <><Check size={12} aria-hidden="true" />{saved} saved</> : problem ? <span className="sp-field-error">{problem}</span> : null}</span>
    </header>
    <div className="sp-rules-grid">
      <div className="sp-rule">
        <h3><Link2 size={14} aria-hidden="true" />Trusted link sites</h3>
        <p>Links to these sites go through quietly. A link to any other site gets a “check this link” warning on the post, so you look before approving.</p>
        <DomainInput label="Trusted sites" values={allowlist} disabled={busy} onChange={next => save({ link_allowlist: next }, "Trusted sites")} />
      </div>
      <div className="sp-rule">
        <h3><Timer size={14} aria-hidden="true" />If a post runs late</h3>
        <p>If your computer was asleep or Raticode was closed at post time, still send it if it's no more than this late. After that it waits in Problems as Missed, so nothing goes out hours late by surprise.</p>
        <div className="sp-chips" role="radiogroup" aria-label="Still post when late by up to">
          {LATE_WINDOW_CHOICES.map(minutes => <button key={minutes} type="button" role="radio" aria-checked={late === minutes} className="sp-chip-btn" aria-disabled={busy || undefined}
            onClick={() => !busy && late !== minutes && pickLate(minutes)} {...lateChoice(minutes)}>{durationLabel(minutes)}</button>)}
        </div>
        <Stepper label={custom ? "Custom, in minutes" : "Or exactly, in minutes"} value={late} min={1} max={MAX_LATE_WINDOW} step={15} disabled={busy}
          onCommit={minutes => save({ late_window_minutes: minutes }, "Late limit")} />
        <p className="sp-field-hint">Each post can change this in its editor.</p>
      </div>
      <div className="sp-rule">
        <h3><Pause size={14} aria-hidden="true" />Pause everything</h3>
        <p>Stops every send and every Publish reminder. Times that pass while paused are skipped, not caught up later.</p>
        <Switch label={data.paused ? "Publishing is paused" : "Publishing is on"} checked={!data.paused} disabled={busy}
          onChange={async on => {
            setSaved(""); setProblem("");
            const outcome = await attempt(() => setPaused(!on));
            if (outcome.ok) setSaved(on ? "Publishing on" : "Pause"); else setProblem(outcome.error);
          }} />
      </div>
    </div>
  </section>;
}
