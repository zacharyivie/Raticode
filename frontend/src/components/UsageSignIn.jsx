import { useState } from "react";
import { Check, Copy, LogIn, RefreshCw } from "lucide-react";
import { useProviderSignIn } from "./ProviderModelEffortFields.jsx";

/** Sign in to a provider's default account from Settings > Usage. */
export default function UsageSignIn({ provider, name, help, checking, onCheck }) {
  const { pending, starting, error, authenticate } = useProviderSignIn(provider);
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(help.command);
      setCopied(true);
    } catch { setCopied(false); }
  }
  const message = pending ? `Finish signing in to ${name} in your browser. Usage updates when you are done.`
    : help.browser ? `Sign in to ${name} so Raticode can read your remaining allowance.`
      : help.command ? <>Open a terminal and run <code>{help.command}</code> to sign in to {name}, then check again.</>
        : `Sign in to ${name} using this profile's own settings, then check again.`;
  return <div className="usage-sign-in">
    <p role={pending ? "status" : undefined}>{message}</p>
    <div className="usage-sign-in-actions">
      {help.browser ? <button type="button" className="usage-sign-in-primary" disabled={starting} onClick={() => authenticate(pending ? "cancel" : "start")}>
        {pending ? null : <LogIn size={13} aria-hidden="true" />}{starting ? "Opening sign-in…" : pending ? "Cancel sign-in" : `Sign in to ${name}`}
      </button> : null}
      {!help.browser && help.command ? <button type="button" className="usage-refresh" onClick={copy}>
        {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}{copied ? "Copied" : "Copy command"}
      </button> : null}
      <button type="button" className="usage-refresh" disabled={checking} onClick={onCheck}>
        <RefreshCw size={13} aria-hidden="true" />{checking ? "Checking…" : "Check again"}
      </button>
    </div>
    {help.browser && help.command && !pending ? <p className="usage-sign-in-alt">If no browser opens, run <code>{help.command}</code> in a terminal.</p> : null}
    {error ? <p role="alert" className="usage-error">{error}</p> : null}
  </div>;
}
