import { useEffect, useState } from "react";
import { Check, ShieldCheck, TriangleAlert, Wrench, X } from "lucide-react";
import { needsReview, setupCandidate, setupTitle, setupTone, stepLabel, worktreeSetupApi } from "../lib/worktreeSetup.js";
import "./WorktreeSetup.css";

// What the project's worktree setup did for one run (Rem task, organization ticket or
// swarm agent), with the review that approves a waiting version.
export function WorktreeSetupNotice({ setup, api = worktreeSetupApi }) {
  const [reviewing, setReviewing] = useState(false);
  if (!setup || typeof setup !== "object") return null;
  const tone = setupTone(setup);
  const Icon = tone === "success" ? Check : tone === "danger" ? X : TriangleAlert;
  const steps = Array.isArray(setup.steps) ? setup.steps : [];
  return <section className="wt-setup" data-tone={tone} aria-label="Project setup">
    <div className="wt-setup-line"><Icon size={14} aria-hidden="true" /><strong>{setupTitle(setup)}</strong></div>
    <p className="wt-setup-summary">{setup.summary}</p>
    {steps.length ? <details className="wt-setup-steps">
      <summary>{steps.length === 1 ? "1 step" : `${steps.length} steps`}</summary>
      <ol>{steps.map((step, index) => <li key={index} data-ok={step.ok ? "true" : "false"}>
        <span className="wt-setup-step"><span className="wt-setup-mark">{step.ok ? (step.skipped ? "Skipped" : "Done") : "Failed"}</span><code>{stepLabel(step)}</code></span>
        {step.detail ? <span className="wt-setup-detail">{step.detail}</span> : null}
        {step.output ? <pre className="wt-setup-output" tabIndex={0} aria-label="Command output">{step.output}</pre> : null}
      </li>)}</ol>
    </details> : null}
    {needsReview(setup) && setup.projectPath ? (reviewing
      ? <WorktreeSetupReview project={setup.projectPath} api={api} onClose={() => setReviewing(false)} />
      : <button type="button" className="wt-setup-button" onClick={() => setReviewing(true)}><ShieldCheck size={13} aria-hidden="true" />Review setup</button>) : null}
  </section>;
}

function Effective({ effective }) {
  if (!effective) return null;
  const rows = [
    ["Runs", effective.setup],
    ["Links", effective.link],
    ["Copies", effective.copy],
  ].filter(([, items]) => items?.length);
  if (!rows.length) return <p className="wt-setup-summary">Does nothing on this computer.</p>;
  return <dl className="wt-setup-effective">{rows.map(([label, items]) => <div key={label}>
    <dt>{label}</dt>
    <dd>{items.map(item => <code key={item}>{item}</code>)}</dd>
  </div>)}</dl>;
}

// The project's setup and its approval. Setup runs commands as the user in every new
// worktree, outside agent sandboxes, so the exact text is shown before approving.
export function WorktreeSetupReview({ project, api = worktreeSetupApi, onClose }) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    api.load(project).then(value => { if (live) setStatus(value); }, failure => { if (live) setError(failure.message); });
    return () => { live = false; };
  }, [api, project]);
  async function act(work) {
    setBusy(true);
    setError("");
    try { setStatus(await work()); } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  }
  const candidate = setupCandidate(status);
  return <div className="wt-setup-review" role="group" aria-label="Review project setup">
    {!status && !error ? <p className="wt-setup-summary">Loading the project’s setup…</p> : null}
    {status ? <>
      <p className="wt-setup-summary"><code>{status.configPath}</code> in {status.project}</p>
      {status.state === "invalid" && !candidate ? <p className="wt-setup-error" role="alert">{status.file?.error}</p> : null}
      {candidate ? <>
        <p className="wt-setup-warning">Setup runs these commands as you, outside the agent’s sandbox, in every new worktree of this project. Approve it only if you trust them.</p>
        <Effective effective={candidate.effective} />
        <details className="wt-setup-steps" open={!status.approved}>
          <summary>{status.approved ? "The new version" : "The full setup file"}</summary>
          <pre className="wt-setup-output" tabIndex={0} aria-label="Setup file waiting for approval">{candidate.text}</pre>
        </details>
        {status.approved ? <details className="wt-setup-steps">
          <summary>The version you approved {String(status.approved.approvedAt || "").slice(0, 10)}</summary>
          <pre className="wt-setup-output" tabIndex={0} aria-label="Approved setup file">{status.approved.text}</pre>
        </details> : null}
      </> : status.state === "approved" ? <p className="wt-setup-ok"><Check size={13} aria-hidden="true" />Approved. New worktrees of this project run this setup.</p>
        : status.state === "none" ? <p className="wt-setup-summary">This project has no setup file.</p> : null}
    </> : null}
    {error ? <p className="wt-setup-error" role="alert">{error}</p> : null}
    <div className="wt-setup-actions">
      {candidate ? <button type="button" className="wt-setup-button" data-variant="primary" disabled={busy} onClick={() => act(() => api.approve(project, candidate.sha256))}><ShieldCheck size={13} aria-hidden="true" />Approve this setup</button> : null}
      {status?.approved ? <button type="button" className="wt-setup-button" disabled={busy} onClick={() => act(() => api.revoke(project))}><Wrench size={13} aria-hidden="true" />Stop running setup</button> : null}
      {onClose ? <button type="button" className="wt-setup-button" onClick={onClose}>Close</button> : null}
    </div>
  </div>;
}
