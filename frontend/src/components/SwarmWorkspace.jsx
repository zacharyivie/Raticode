import { providerPermissionDefault, providerPermissionOptions } from "../lib/providerPermissions.js";
import { useEffect, useId, useRef, useState } from "react";
import { ArrowLeft, ChevronRight, Crown, History, MessagesSquare, MoreHorizontal, Pause, Play, Plus, Send, Settings2, Square, Trash2, X } from "lucide-react";
import { ProviderModelEffortFields, useProviderCapabilities } from "./ProviderModelEffortFields.jsx";
import RemResources, { DEFAULT_REM_RESOURCES, remResourceError } from "./RemResources.jsx";
import { startPolling } from "../lib/refresh.js";
import { swarmOverview, milestoneProgress, objectiveProgressChange, newSwarmAgent, positiveDraft, swarmRequest, RUN_LIMIT_DEFAULTS, FULL_ACCESS_MODES, fullAccessAgents, runStateLabel, agentUsage, parseChecks, formatChecks, formatMinutes } from "../lib/swarms.js";

import "./SwarmWorkspace.css";

const EMPTY_HISTORY = [];
const fieldClass = "ui-input";
const buttonClass = "ui-button ui-button--sm";
const primaryClass = "ui-button ui-button--primary ui-button--sm";

export default function SwarmWorkspace({ rootPath, swarmId, onSelect, onClose, defaults = {}, selectedAgentId, projectPaths = [] }) {
  const [swarm, setSwarm] = useState(null);
  const [editor, setEditor] = useState(swarmId === "new" ? "team" : null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [archiveId, setArchiveId] = useState("");
  const [archive, setArchive] = useState(null);
  const [archiveError, setArchiveError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(swarmId !== "new");
  const [task, setTask] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  useEffect(() => {
    if (swarmId === "new") return undefined;
    let cancelled = false;
    let since = "";
    const stop = startPolling(async () => {
      try {
        const result = await swarmRequest(rootPath, `/${swarmId}`, { query: { since, history: historyOpen ? "summary" : "0" } });
        if (!cancelled) { if (result.swarm?.id) { since = result.swarm.updatedAt; setSwarm(result.swarm); } setLoading(false); }
      } catch (failure) { if (!cancelled) { setError(failure.message); setLoading(false); } }
    }, { interval: 2500, immediate: true });
    return () => { cancelled = true; stop(); };
  }, [rootPath, swarmId, historyOpen]);
  useEffect(() => {
    if (!archiveId) return undefined;
    let cancelled = false;
    setArchiveError("");
    setArchive(null);
    swarmRequest(rootPath, `/${swarmId}/history`, { query: { runId: archiveId } })
      .then(result => { if (!cancelled) setArchive(result.run); })
      .catch(failure => { if (!cancelled) setArchiveError(failure.message); });
    return () => { cancelled = true; };
  }, [rootPath, swarmId, archiveId]);
  async function mutate(suffix, payload, method = "POST") {
    setBusy(true); setError("");
    try {
      const result = await swarmRequest(rootPath, swarmId === "new" ? "" : `/${swarmId}${suffix}`, { method, ...payload, workspacePaths: [...new Set([...(payload.agents || swarm?.agents || []), ...(swarm?.run?.configuration?.agents || [])].map(agent => agent.workspacePath).filter(Boolean))] });
      setSwarm(result.swarm);
      window.dispatchEvent(new CustomEvent("gofer:swarms-changed"));
      if (swarmId === "new") onSelect(result.swarm.id);
      return true;
    } catch (failure) { setError(failure.message); return false; }
    finally { setBusy(false); }
  }
  async function deleteSwarm() {
    setBusy(true); setError("");
    try {
      await swarmRequest(rootPath, `/${swarmId}`, { method: "DELETE" });
      window.dispatchEvent(new CustomEvent("gofer:swarms-changed"));
      onClose();
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  const currentRun = swarm?.run;
  const diagnosing = currentRun?.idleDiagnosis?.state === "working";
  const active = ["running", "paused", "stopping", "completing"].includes(currentRun?.state) || diagnosing;
  const stopping = ["stopping", "completing"].includes(currentRun?.state);
  const history = swarm?.history || EMPTY_HISTORY;
  const run = archiveId ? archive : currentRun;
  const runAgents = run?.configuration?.agents || swarm?.agents || [];
  return <section aria-label="Swarm workspace" className="swarm-workspace flex min-h-0 min-w-0 flex-1 flex-col text-ink">
    <header className="swarm-toolbar">
      <button type="button" aria-label="Close swarm" title="Close swarm" className="swarm-icon-button" onClick={onClose}><ArrowLeft size={16} /></button>
      <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{swarmId === "new" ? "New swarm" : swarm?.name || "Swarm"}</h2>
      {swarm ? <button type="button" aria-label="Swarm settings" title="Swarm settings" className="swarm-icon-button" onClick={() => setEditor("team")}><Settings2 size={16} /></button> : null}
    </header>
    {error ? <div role="alert" className="flex items-start justify-between gap-2 border-b border-line px-4 py-3 text-xs text-red-600 dark:text-red-300"><span>{error}</span><button type="button" aria-label="Dismiss swarm error" onClick={() => setError("")}><X size={14} /></button></div> : null}
    {loading ? <p role="status" className="p-6 text-sm text-muted">Loading swarm...</p> : <>
      {editor ? <section aria-label={editor === "team" ? "Swarm settings" : "Agent settings"} className="flex min-h-0 flex-1 flex-col">
        <div className="swarm-toolbar"><h3 className="flex-1 text-sm font-semibold">{editor === "team" ? "Team setup" : `${swarm?.agents.find(agent => agent.id === editor)?.name || "Agent"} settings`}</h3>{swarm ? <button type="button" className={buttonClass} onClick={() => setEditor(null)}>Back to dashboard</button> : null}</div>
        <SwarmSettings rootPath={rootPath} projectPaths={projectPaths} key={editor} onDelete={swarm ? deleteSwarm : undefined} selectedAgentId={editor === "team" ? null : editor} defaults={defaults} swarm={swarm} busy={busy} active={active} onSave={async definition => { const saved = await mutate("", definition, swarmId === "new" ? "POST" : "PUT"); if (saved) setEditor(null); return saved; }} />
      </section> : null}
      {swarm ? <div hidden={Boolean(editor)} className={`swarm-scroll workflow-scrollbar ${editor ? "hidden" : ""}`}>
        <div className="swarm-dashboard">
            {run ? <HumanInbox key={run.id} run={run} agents={runAgents} readOnly={Boolean(archiveId) || !["running", "paused"].includes(run.state)} busy={busy} onAction={payload => mutate("/execution", payload)} /> : null}

          <section className="swarm-run-heading" aria-label="Run overview">
            <div className="swarm-run-topline"><span className="swarm-run-label">{archiveId ? "Previous run" : run ? "Current run" : "New run"}</span><RunStatus state={run?.state || "ready"} />
              <div className="ml-auto flex flex-wrap gap-2">{archiveId ? <button className={buttonClass} onClick={() => setArchiveId("")}>Return to current run</button> : active ? <><button type="button" disabled={busy || stopping || diagnosing} className={buttonClass} onClick={() => void mutate("/control", { action: currentRun.state === "paused" ? "resume" : "pause" })}>{currentRun.state === "paused" ? <Play size={13} /> : <Pause size={13} />}{currentRun.state === "paused" ? "Resume" : "Pause"}</button><button type="button" disabled={busy || stopping} aria-expanded={confirmStop} className={buttonClass} onClick={() => setConfirmStop(true)}><Square size={12} />Stop…</button></> : null}</div>
            </div>
            {run || archiveId ? <ExpandableText as="h1" text={run?.task || "Loading previous run..."} label="task" limit={180} lines={2} /> : null}

            {run ? <SwarmOverview run={run} agents={runAgents} /> : null}
            {run?.cleanup?.error ? <p className="text-xs text-muted">Retained work is still available. Cleanup error: {run.cleanup.error} {!archiveId ? <button type="button" className={buttonClass} disabled={busy} onClick={() => void mutate("/control", { action: "cleanup" })}>Retry cleanup</button> : null}</p> : null}
            {confirmStop && active && !archiveId ? <div role="alertdialog" aria-label="Stop this run" className="swarm-confirm"><p><strong>Stop this run?</strong> Running agents are cancelled. Unfinished work is archived and the run’s worktrees are removed; the review branch stays. A stopped run can’t be resumed. Use Pause to keep it resumable.</p><div className="flex flex-wrap gap-2"><button type="button" className={buttonClass} onClick={() => setConfirmStop(false)}>Keep running</button><button type="button" className={`${buttonClass} swarm-danger-button`} disabled={busy} onClick={async () => { if (await mutate("/control", { action: "stop" })) setConfirmStop(false); }}>Stop and clean up</button></div></div> : null}
            {run?.pauseReason ? <p role="status" className="mt-3 text-xs leading-5 text-muted">Paused: {run.pauseReason}</p> : null}
            {run?.replanRequired && !archiveId && run.state === "paused" ? <ReplanForm busy={busy} onAction={payload => mutate("/execution", payload)} /> : null}
            {!archiveId && !active ? <details className="swarm-new-run" open={!run || undefined}><summary>Start a new run</summary><form className="swarm-start-form" onSubmit={event => { event.preventDefault(); setReviewing(true); }}><div className="flex items-center justify-between gap-3"><label className="text-xs font-semibold" htmlFor="swarm-task">Task</label>{run ? <button type="button" className="text-xs text-muted hover:text-ink" onClick={event => { event.currentTarget.closest("details").open = false; }}>Cancel</button> : null}</div><textarea id="swarm-task" required rows={2} value={task} onChange={event => setTask(event.target.value)} placeholder="Describe the task…" className={fieldClass} />{reviewing ? <LaunchReview swarm={swarm} busy={busy} onCancel={() => setReviewing(false)} onStart={async confirmFullAccess => { if (await mutate("/start", { task, expectedConfigurationHash: swarm.configurationHash, confirmFullAccess })) { setTask(""); setReviewing(false); } }} /> : <div className="flex justify-end"><button type="submit" disabled={busy || !task.trim()} className={primaryClass}><Play size={13} />Review and start…</button></div>}</form></details> : null}
          </section>
          {archiveError ? <p role="alert" className="text-sm text-red-600 dark:text-red-300">{archiveError}</p> : null}
          {archiveId && !archive ? <p role="status" className="text-sm text-muted">{archiveError ? "Return to the current run or select another previous run." : "Loading archived run..."}</p> : <>
            <section aria-label="Run progress" className="swarm-progress-panel">
              <SwarmProgress key={run?.id || "ready"} readOnly={Boolean(archiveId)} run={run} agents={runAgents} busy={busy} onSave={payload => mutate("/objectives", payload)} />
            </section>
            <div className="swarm-columns">
              <section aria-label="Agent roster" className="swarm-roster">
                <div className="swarm-section-heading"><h2>Agents <span className="swarm-count">{runAgents.length}</span></h2>{!archiveId ? <button type="button" aria-label="Manage team" title="Manage team" className="swarm-icon-button" onClick={() => setEditor("team")}><Plus size={15} /></button> : null}</div>
                <SwarmRoster key={run?.id || "ready"} run={run} agents={runAgents} readOnly={Boolean(archiveId)} selectedAgentId={selectedAgentId} configurableAgents={swarm.agents} onConfigure={setEditor} />
              </section>
              <details aria-label="Message board" className="swarm-board-panel" open><summary className="swarm-section-heading">Messages and steering <span className="swarm-count">{run?.messages?.length || 0}</span></summary>
                <SwarmBoard key={run?.id || "ready"} readOnly={Boolean(archiveId) || Boolean(run && !active)} swarm={{ ...swarm, run }} busy={busy} onResolve={payload => mutate("/deliveries", payload)} onSend={payload => mutate("/messages", payload)} />
              </details>
            </div>
            {run ? <details id="swarm-execution" className="swarm-diagnostics"><summary>Execution details, checks and recovery</summary><SwarmExecution run={run} agents={runAgents} readOnly={Boolean(archiveId) || !active} busy={busy} onAction={payload => mutate("/execution", payload)} /></details> : null}
          </>}
          <details className="swarm-history" open={historyOpen} onToggle={event => setHistoryOpen(event.currentTarget.open)}><summary><History size={14} />Previous runs<ChevronRight className="swarm-history-chevron" size={14} /></summary>{historyOpen ? <div className="swarm-history-list">{!history.length ? <p className="py-4 text-xs text-muted">No previous runs.</p> : history.slice().reverse().map(item => <button key={item.id} type="button" aria-pressed={archiveId === item.id} onClick={() => { setArchiveId(item.id); document.querySelector(".swarm-scroll")?.scrollTo({ top: 0 }); }}><span className="min-w-0 flex-1"><strong className="block break-words font-medium">{item.task}</strong>{item.createdAt ? <time className="mt-1 block text-xs text-muted" dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleDateString()}</time> : null}</span><RunStatus state={item.state} /><ChevronRight size={14} /></button>)}</div> : null}</details>
        </div>
      </div> : null}
    </>}
  </section>;
}

export function SwarmOverview({ run, agents }) {
  const overview = swarmOverview(run, agents);
  const progress = milestoneProgress(run.objectives);
  const terminal = ["completed", "failed", "stopped"].includes(run.state);
  const lead = agents.find(agent => agent.isOrchestrator);
  const finalMessage = terminal ? run.messages?.filter(message => message.senderId === lead?.id).at(-1)?.body : null;
  return <section className="swarm-health" data-success={overview.success || undefined} aria-label="Live overview">
    <h2>{overview.success ? <span aria-hidden="true">✓ </span> : null}{overview.title}</h2>
    <p className="swarm-health-counts"><span>{progress.acceptedCount}/{progress.totalCount} milestones done</span><span>{overview.counts.working} working</span><span>{overview.counts.idle} idle</span>{overview.counts.queued ? <span>{overview.counts.queued} queued</span> : null}{overview.counts.retry_wait ? <span>{overview.counts.retry_wait} awaiting retry</span> : null}</p>
    {overview.success ? <p>The team finished the accepted scope. Review the final summary and combined changes below.</p> : null}
    {overview.stalled ? <p role="status">No agent is working. The coordinator should reconcile blocked work and assign the next step.</p> : null}
    {overview.issues.length ? <div className="swarm-attention" role="alert"><h3>What needs attention</h3>{overview.issues.map((issue, index) => <div key={index}><strong>{issue.title}</strong><p>{issue.body}</p>{issue.retryAt ? <p>{run.state === "paused" ? "Retry waits for Resume." : `Automatic retry at ${new Date(issue.retryAt * 1000).toLocaleTimeString()}.`}</p> : null}</div>)}<a href="#swarm-execution" onClick={() => { const details = document.getElementById("swarm-execution"); if (details) details.open = true; }}>Review checks and recovery</a></div> : null}
    {terminal ? <div className="swarm-outcome"><h3>{overview.success ? "Final summary and next steps" : "Unresolved work and next steps"}</h3>{overview.remaining.length ? <ul>{overview.remaining.map(item => <li key={item.id}>{item.title}: {item.blocker || item.status.replaceAll("_", " ")}</li>)}</ul> : null}{run.idleDiagnosis?.body ? <p>{run.idleDiagnosis.body}</p> : null}{finalMessage ? <ExpandableText text={finalMessage} label="final summary" limit={600} lines={6} /> : <p>No coordinator summary was recorded. Review milestone evidence and execution checks before continuing.</p>}</div> : null}
  </section>;
}

function ExpandableText({ as: Tag = "p", text = "", label, limit, lines, className = "" }) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const long = text.length > limit || text.split("\n").length > lines;
  return <><Tag id={id} className={`${className} ${long && !expanded ? "swarm-clamped-text" : ""}`} style={long && !expanded ? { WebkitLineClamp: lines } : undefined}>{text}</Tag>{long ? <button type="button" className="swarm-expand-text" aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded(value => !value)}>{expanded ? `Show less ${label}` : `Show full ${label}`}</button> : null}</>;
}

function RunStatus({ state }) {
  return <span className="swarm-state" data-state={state}><span aria-hidden="true" />{runStateLabel(state)}</span>;
}

export function SwarmExecution({ run, agents, readOnly, busy, onAction }) {
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState(20);
  const usage = run.usage || {};
  const attempts = run.attempts || [];
  const config = run.configuration || {};
  const titles = new Map((run.objectives || []).flatMap(objective => objective.milestones || []).map(item => [item.id, item.title]));
  const workspaces = [run.workspace, ...Object.values(run.projectWorkspaces || {})].filter(item => item?.path);
  const limit = (used, max) => max ? `${used} of ${max}` : `${used} (no limit)`;
  return <section aria-label="Execution and verification" className="space-y-3 border-t border-line pt-4 text-xs">
    <dl className="swarm-limits">
      <div><dt>Agent turns</dt><dd>{limit(run.turnCount || 0, config.maxTurns)}</dd></div>
      <div><dt>Running time</dt><dd>{config.maxRunSeconds ? `${formatMinutes(run.elapsedSeconds)} of ${formatMinutes(config.maxRunSeconds)}` : formatMinutes(run.elapsedSeconds)}</dd></div>
      <div><dt>Per turn</dt><dd>{config.maxTurnSeconds ? `up to ${formatMinutes(config.maxTurnSeconds)}` : "No limit"}</dd></div>
      <div><dt>Reported tokens</dt><dd>{tokenText(usage.reported_input_tokens ?? usage.input_tokens)} in · {tokenText(usage.reported_output_tokens ?? usage.output_tokens)} out{config.maxTokens ? ` · limit ${config.maxTokens.toLocaleString()}` : ""}</dd></div>
      <div><dt>Stall limit</dt><dd>{config.stallTurnLimit ? `${run.stalledTurns || 0} of ${config.stallTurnLimit} turns` : "None"}</dd></div>
      <div><dt>Repairs per assignment</dt><dd>{config.maxRepairAttempts ?? "No limit"}</dd></div>
    </dl>
    {run.createdAt ? <p className="text-muted">Started {new Date(run.createdAt).toLocaleString()} · {run.workspace?.mode === "git" ? "Isolated Git worktrees" : "One writer at a time"}</p> : null}
    <div className="tr-scroll" role="region" aria-label="Usage by agent" tabIndex={0}><table className="swarm-usage"><caption>Usage by agent</caption><thead><tr><th scope="col">Agent</th><th scope="col">Turns</th><th scope="col">Input tokens</th><th scope="col">Output tokens</th></tr></thead><tbody>{agentUsage(run, agents).map(row => <tr key={row.agent.id}><th scope="row">{row.agent.name}</th><td>{row.turns}</td><td>{row.input.toLocaleString()}{row.unreported ? "*" : ""}</td><td>{row.output.toLocaleString()}{row.unreported ? "*" : ""}</td></tr>)}</tbody></table>{agentUsage(run, agents).some(row => row.unreported) ? <p className="text-muted">* Some turns didn’t report usage, so these totals are low.</p> : null}</div>
    <section aria-label="Results and branches" className="space-y-2">
      <h3 className="font-semibold">Results and branches</h3>
      {workspaces.map(workspace => <div key={workspace.path} className="swarm-result">
        {workspace.projectRoot ? <p className="text-muted">Project: {workspace.projectRoot}</p> : null}
        <p className="break-all">{workspace.mode === "git" ? "Review worktree" : "Workspace"}: <code>{workspace.path}</code> <CopyButton text={workspace.path} /></p>
        {workspace.branch ? <p className="break-all">Review branch: <code>{workspace.branch}</code> <CopyButton text={workspace.branch} /></p> : null}
        {workspace.integration ? <p role="status">Combined checks: {workspace.integration.passed ? "Passed" : "Incomplete"}{workspace.integration.error ? ` · ${workspace.integration.error}` : ""}</p> : null}
        {workspace.excludedFiles?.length ? <p className="text-muted">Left out of the starting snapshot because they look like secrets: {workspace.excludedFiles.join(", ")}</p> : null}
      </div>)}
      {run.integration ? <p role="status">Combined checks: {run.integration.passed ? "Passed" : "Incomplete"}{run.integration.error ? ` · ${run.integration.error}` : ""}</p> : null}
      {run.cleanup?.archives?.length ? <details><summary className="cursor-pointer">Archived unfinished work ({run.cleanup.archives.length})</summary><ul className="mt-2 space-y-1">{run.cleanup.archives.map(path => <li key={path} className="break-all"><code>{path}</code></li>)}</ul></details> : null}
    </section>
    <details open={open} onToggle={event => setOpen(event.currentTarget.open)}><summary className="cursor-pointer font-semibold">Assignment attempts and checks ({attempts.length})</summary>{open ? <div className="mt-3 space-y-4">
      <p className="text-muted">{run.capacityScope || "App-managed turns only."} Failed providers retry after 10 seconds, doubling up to 5 minutes; after 5 failures the run pauses. Provider session recovery and child discovery are unavailable.</p>
      {attempts.slice().reverse().slice(0, shown).map(attempt => <article key={attempt.id} className="space-y-2 border-t border-line pt-3">
        <p className="font-medium">{titles.get(attempt.milestoneId) || (attempt.milestoneId ? `Milestone ${attempt.milestoneId}` : "Board conversation")} · {agents.find(agent => agent.id === attempt.ownerId)?.name || attempt.ownerId} · {attempt.state}{attempt.repairCount ? ` · repair ${attempt.repairCount}` : ""}</p>
        <p className="break-all text-muted">Attempt {attempt.id}{attempt.sessionId ? ` · Session ${attempt.sessionId}` : ""}</p>
        {attempt.workspace?.path ? <p className="break-all text-muted">{attempt.workspace.path}{attempt.workspace.branch ? ` · ${attempt.workspace.branch}` : ""}</p> : null}
        {attempt.result ? <p>Verification: {attempt.result.passed ? "Passed" : "Failed"}{attempt.result.error ? ` · ${attempt.result.error}` : ""}</p> : null}
        {attempt.result?.artifacts?.map(artifact => <p key={artifact.path} className="break-all text-muted">Artifact: {artifact.path} · SHA-256 {artifact.sha256}</p>)}
        {[...(attempt.result?.checks || []), ...(attempt.integration?.checks || [])].map(check => <p key={check.logPath} className="break-all text-muted">Exit {check.exitCode}: {check.command.join(" ")} · Log: {check.logPath}</p>)}
        {attempt.integration?.conflicts?.length ? <p role="alert">Integration conflict: {attempt.integration.conflicts.join(", ")} · Preserved at {attempt.integration.path}</p> : null}
        {!readOnly && attempt.result?.passed && !attempt.integration?.passed && (run.projectWorkspaces?.[attempt.projectRoot] || run.workspace)?.mode === "git" ? <button type="button" className={buttonClass} disabled={busy} onClick={() => onAction({ action: "integrate", milestoneId: attempt.milestoneId, attemptId: attempt.id })}>Integrate and check</button> : null}
        {!readOnly && attempt.state === "uncertain" ? <AttemptReview attempt={attempt} busy={busy} onAction={onAction} /> : null}
      </article>)}
      {attempts.length > shown ? <button type="button" className={buttonClass} onClick={() => setShown(value => value + 20)}>Show 20 older attempts</button> : null}
    </div> : null}</details>
  </section>;
}

function tokenText(value) {
  return Number.isFinite(value) ? value.toLocaleString() : "Unknown";
}

function ReplanForm({ busy, onAction }) {
  const [reason, setReason] = useState("");
  return <form className="swarm-replan space-y-2" onSubmit={async event => { event.preventDefault(); if (await onAction({ action: "replan", reason })) setReason(""); }}>
    <label className="block space-y-1 text-xs">Changed plan<textarea className={fieldClass} required rows={2} value={reason} onChange={event => setReason(event.target.value)} placeholder="What should the team do differently?" /></label>
    <div className="flex items-center justify-between gap-3"><p className="text-xs text-muted">Record the new approach, then resume.</p><button type="submit" className={buttonClass} disabled={busy || !reason.trim()}>Record plan</button></div>
  </form>;
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  return <button type="button" className="swarm-copy" aria-label={`Copy ${text}`} onClick={async () => { try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { setCopied(false); } }}>{copied ? "Copied" : "Copy"}</button>;
}

const PERMISSION_LABELS = { "danger-full-access": "Full access", bypassPermissions: "Full access", "read-only": "Read only", plan: "Read only", "workspace-write": "Worktree only", acceptEdits: "Edits allowed", dontAsk: "Listed tools only", "cli-managed": "CLI managed" };

function AgentAccess({ agent, rootPath }) {
  const full = FULL_ACCESS_MODES.has(agent.permissionMode);
  const project = agent.workspacePath && agent.workspacePath !== rootPath ? agent.workspacePath.split(/[\\/]/).filter(Boolean).at(-1) : "";
  return <p className="swarm-agent-tags"><span className="swarm-agent-tag" data-danger={full || undefined} title={full ? "Runs without a sandbox or approval prompts" : `Permission: ${agent.permissionMode || "provider default"}`}>{full ? "⚠ " : ""}{PERMISSION_LABELS[agent.permissionMode] || "Default access"}</span>{project ? <span className="swarm-agent-tag" title={agent.workspacePath}>{project}</span> : null}</p>;
}

export function LaunchReview({ swarm, busy, onStart, onCancel }) {
  const [acknowledged, setAcknowledged] = useState(false);
  const unrestricted = fullAccessAgents(swarm.agents);
  const limits = { ...RUN_LIMIT_DEFAULTS, ...Object.fromEntries(Object.keys(RUN_LIMIT_DEFAULTS).filter(key => swarm[key]).map(key => [key, swarm[key]])) };
  return <section aria-label="Review before starting" className="swarm-launch-review">
    <h3>Review before starting</h3>
    <ul className="swarm-launch-agents">{swarm.agents.map(agent => <li key={agent.id}><strong>{agent.name}</strong>{agent.isOrchestrator ? " · Orchestrator" : ""} · {agent.provider} {agent.model && agent.model !== "cli-default" ? agent.model : ""} <AgentAccess agent={agent} rootPath={swarm.projectRoot} /></li>)}</ul>
    <p className="text-xs text-muted">Limits: {limits.maxTurns} agent turns, {formatMinutes(limits.maxRunSeconds)} running time, {formatMinutes(limits.maxTurnSeconds)} per turn{swarm.maxTokens ? `, ${swarm.maxTokens.toLocaleString()} tokens` : ""}. The run pauses after {limits.stallTurnLimit} turns without progress. Up to {swarm.maxConcurrency || 3} agents at once.</p>
    <p className="text-xs text-muted">Git: {swarm.gitPermissions?.remote ? "agents can push assignment branches and open pull requests" : swarm.gitPermissions?.local === false ? "managed Git is off" : "local commits only, no pushes"}. Git projects get a review branch named raticode/swarm/… in each repository.</p>
    {!swarm.integrationChecks?.length ? <p className="swarm-warning" role="note">No integration checks are set, so only checks the orchestrator writes gate the combined result. Add some in Team setup → Limits and checks.</p> : null}
    {unrestricted.length ? <label className="swarm-warning swarm-setup-check"><input type="checkbox" aria-label="Confirm full access" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} /><span>{unrestricted.map(agent => agent.name).join(", ")} {unrestricted.length === 1 ? "runs" : "run"} without a sandbox or approval prompts. {unrestricted.length === 1 ? "It" : "They"} can change any file you can, including your original checkouts, and run commands such as git push. I understand.</span></label> : null}
    <div className="flex flex-wrap justify-end gap-2"><button type="button" className={buttonClass} onClick={onCancel}>Back</button><button type="button" className={primaryClass} disabled={busy || (unrestricted.length > 0 && !acknowledged)} onClick={() => void onStart(unrestricted.length > 0 && acknowledged)}><Play size={13} />Start run</button></div>
  </section>;
}

export function HumanInbox({ run, agents, readOnly, busy, onAction }) {
  const items = [...(run.humanInbox || [])].sort((a, b) =>
    Number(b.state === "pending") - Number(a.state === "pending") ||
    String(b.createdAt || "").localeCompare(String(a.createdAt || "")));
  const pending = items.filter(item => item.state === "pending");
  return <section aria-label="Human inbox" className="swarm-inbox">
    <div className="swarm-inbox-heading"><h2>Human inbox</h2><span className="swarm-count">{pending.length} unread</span></div>
    {!pending.length ? <p className="swarm-inbox-empty">No decisions waiting for you.</p> : null}
    {items.length ? <div className="swarm-inbox-list workflow-scrollbar" role="region" aria-label="Inbox issues" tabIndex={0}>
      {items.map(item => <HumanInboxItem key={item.id} item={item} agent={agents.find(agent => agent.id === item.agentId)} busy={Boolean(busy || run.agentStates?.[item.agentId]?.state === "working")} readOnly={readOnly} onAction={onAction} />)}
    </div> : null}
  </section>;
}

const recoveryActions = {
  review: "Keep output for verification",
  retry: "Retry this attempt in its existing workspace",
  dismiss: "Dismiss this attempt",
};

function HumanInboxItem({ item, agent, busy, readOnly, onAction }) {
  const [redirect, setRedirect] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [resolution, setResolution] = useState("review");
  const recovery = item.kind === "recovery";
  const addressed = item.state !== "pending";
  const nextSteps = item.nextSteps || [recovery ? recoveryActions[item.resolution] : "", item.instruction || item.recommendedAction].filter(Boolean).join(". ");
  return <article className="swarm-inbox-item" data-read={addressed || undefined}>
    <div className="swarm-inbox-byline"><span>{agent?.name || item.agentId}</span><span>{addressed ? "Read" : "Unread"}</span></div>
    <h3>Needs Attention:</h3>
    <p>{item.description}</p>
    <h3>{addressed ? "Next steps:" : "Recommended Action:"}</h3>
    <p>{addressed ? nextSteps || "Response recorded." : item.recommendedAction}</p>
    {recovery ? <details className="swarm-inbox-context"><summary>Attempt details</summary>
      {item.workspace?.path ? <p>Workspace: {item.workspace.path}</p> : null}
      <p>Attempt {item.attemptId}{item.milestoneId ? ` · Milestone ${item.milestoneId}` : ""}</p>
    </details> : null}
    {!readOnly && !addressed ? <form onSubmit={event => { event.preventDefault(); if (!busy) void onAction({ action: "human_response", notificationId: item.id, decision: redirect ? "redirect" : "proceed", instruction: redirect ? instruction : "", resolution }); }}>
      {recovery ? <label className="swarm-inbox-resolution">On proceed<select className={fieldClass} value={resolution} onChange={event => setResolution(event.target.value)}>{Object.entries(recoveryActions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label> : null}
      {redirect ? <label className="swarm-inbox-instruction">Additional instruction<textarea autoFocus required className={fieldClass} rows={2} value={instruction} onChange={event => setInstruction(event.target.value)} /></label> : null}
      <div className="swarm-inbox-actions"><button type="submit" className={primaryClass} disabled={busy || (redirect && !instruction.trim())}>{redirect ? "Send instruction" : "Proceed"}</button><button type="button" className={buttonClass} disabled={busy} onClick={() => setRedirect(value => !value)}>{redirect ? "Cancel" : "Do something else"}</button></div>
    </form> : null}
  </article>;
}

function AttemptReview({ attempt, busy, onAction }) {
  const [reason, setReason] = useState("");
  return <form className="space-y-2" onSubmit={event => { event.preventDefault(); void onAction({ action: "resolve_attempt", attemptId: attempt.id, resolution: "review", reason }); }}>
    <label className="block space-y-1">Recovery review<textarea className={fieldClass} required value={reason} onChange={event => setReason(event.target.value)} placeholder="Confirm the prior worker stopped and describe the effects you checked." /></label>
    <button type="submit" className={buttonClass} disabled={busy || !reason.trim()}>Keep output for verification</button>
  </form>;
}

function SwarmRoster({ run, agents, readOnly, selectedAgentId, configurableAgents, onConfigure }) {
  const [expanded, setExpanded] = useState(() => new Set(selectedAgentId ? [selectedAgentId] : []));
  useEffect(() => { if (selectedAgentId) setExpanded(current => new Set([...current, selectedAgentId])); }, [selectedAgentId]);
  return <div>{agents.map(agent => {
    const state = run?.agentStates?.[agent.id];
    const milestone = (run?.objectives || []).flatMap(item => item.milestones || []).find(item => item.id === state?.milestoneId || item.ownerId === agent.id && ["working", "in_review", "blocked"].includes(item.status));
    const digest = run?.digest?.agents?.find(item => item.agentId === agent.id)?.summary;
    return <article key={agent.id} className="swarm-agent" data-orchestrator={agent.isOrchestrator || undefined} aria-label={`${agent.name} agent`}>
      <div className="swarm-agent-identity"><div className="swarm-agent-portrait" aria-hidden="true">{agent.name.trim().slice(0, 2).toUpperCase()}{agent.isOrchestrator ? <Crown size={12} /> : null}</div><div className="min-w-0 flex-1"><h3>{agent.name}</h3><AgentAccess agent={agent} rootPath={run?.workspace?.projectRoot} /></div><RunStatus state={state?.state || "idle"} />{!readOnly && configurableAgents.some(item => item.id === agent.id) ? <AgentMenu agent={agent} onConfigure={onConfigure} /> : null}</div>
      <p className="swarm-agent-now">{milestone && !["accepted", "cancelled"].includes(milestone.status) ? milestone.title : state?.state === "working" ? "Working on board messages" : state?.state === "retry_wait" ? "Waiting for provider retry" : "No active assignment"}</p>
      <details className="swarm-agent-details" open={expanded.has(agent.id)} onToggle={event => { const open = event.currentTarget.open; setExpanded(current => { const next = new Set(current); if (open) next.add(agent.id); else next.delete(agent.id); return next; }); }}><summary>Activity</summary>{expanded.has(agent.id) ? <>{digest ? <p className="py-3">Orchestrator update: {digest}</p> : null}{state?.workspace ? <p className="break-all">{state.workspace}</p> : null}<SwarmAgentActivity run={run} agents={[agent]} selectedAgentId={agent.id} compact /></> : null}</details>
    </article>;
  })}</div>;
}

function AgentMenu({ agent, onConfigure }) {
  const ref = useRef(null);
  useEffect(() => {
    const close = event => { if (!ref.current?.contains(event.target)) ref.current?.removeAttribute("open"); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);
  return <details ref={ref} className="swarm-agent-menu" onKeyDown={event => { if (event.key === "Escape") { ref.current.open = false; ref.current.querySelector("summary").focus(); } }}><summary aria-label={`Options for ${agent.name}`} title={`Options for ${agent.name}`} className="swarm-icon-button"><MoreHorizontal size={18} /></summary><div><button type="button" onClick={() => { ref.current.open = false; onConfigure(agent.id); }}><Settings2 size={14} />Agent settings</button></div></details>;
}

export function PositiveNumberField({ label, value, onChange, min = 1, disabled = false }) {
  const [draft, setDraft] = useState(String(value));
  const [focused, setFocused] = useState(false);
  useEffect(() => { if (!focused) setDraft(String(value)); }, [value, focused]);
  function commit() {
    const next = positiveDraft(draft, value, min);
    setDraft(String(next)); onChange(next); setFocused(false);
  }
  return <label className="block space-y-1.5 text-xs text-muted"><span>{label}</span><input aria-label={label} type="text" inputMode="decimal" className={fieldClass} value={draft} disabled={disabled} onFocus={() => setFocused(true)} onChange={(event) => setDraft(event.target.value)} onBlur={commit} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); } }} /></label>;
}

export function AgentWorkspaceField({ value, rootPath, projectPaths, disabled, onChange }) {
  const paths = [...new Set(projectPaths.filter(path => path && path !== rootPath))];
  if (value && value !== rootPath && !paths.includes(value)) paths.push(value);
  return <label className="swarm-setup-field">Workspace project
    <select className={fieldClass} disabled={disabled} value={value} onChange={event => onChange(event.target.value)}>
      <option value="">Swarm project{rootPath ? ` · ${rootPath}` : ""}</option>
      {value === rootPath ? <option value={rootPath}>{rootPath}</option> : null}
      {paths.map(path => <option key={path} value={path}>{path}</option>)}
    </select>
    <span className="swarm-setup-hint">Works in this repository and coordinates with the whole team. Changes apply to the next run.</span>
  </label>;
}

function limitDraft(swarm) {
  const value = (key) => swarm[key] || RUN_LIMIT_DEFAULTS[key];
  return {
    maxTurns: value("maxTurns"),
    maxRunMinutes: Math.round(value("maxRunSeconds") / 60),
    maxTurnMinutes: Math.round(value("maxTurnSeconds") / 60),
    stallTurnLimit: value("stallTurnLimit"),
    maxRepairAttempts: swarm.maxRepairAttempts ?? RUN_LIMIT_DEFAULTS.maxRepairAttempts,
    maxTokens: swarm.maxTokens ?? null,
    checksText: formatChecks(swarm.integrationChecks || []),
  };
}

function limitPayload(draft) {
  return {
    maxTurns: draft.maxTurns,
    maxRunSeconds: Math.round(draft.maxRunMinutes * 60),
    maxTurnSeconds: Math.round(draft.maxTurnMinutes * 60),
    stallTurnLimit: draft.stallTurnLimit,
    maxRepairAttempts: draft.maxRepairAttempts,
    maxTokens: draft.maxTokens,
    integrationChecks: parseChecks(draft.checksText),
  };
}

export function OptionalNumberField({ label, value, onChange, disabled = false }) {
  const [draft, setDraft] = useState(value == null ? "" : String(value));
  const [focused, setFocused] = useState(false);
  useEffect(() => { if (!focused) setDraft(value == null ? "" : String(value)); }, [value, focused]);
  function commit() {
    const number = Number(draft);
    const next = draft.trim() && Number.isFinite(number) && number > 0 ? Math.round(number) : null;
    setDraft(next == null ? "" : String(next)); onChange(next); setFocused(false);
  }
  return <label className="block space-y-1.5 text-xs text-muted"><span>{label}</span><input aria-label={label} type="text" inputMode="numeric" placeholder="No limit" className={fieldClass} value={draft} disabled={disabled} onFocus={() => setFocused(true)} onChange={event => setDraft(event.target.value)} onBlur={commit} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); commit(); } }} /></label>;
}

export function SwarmSettings({ swarm, active, busy, onSave, onDelete, defaults = {}, selectedAgentId, rootPath = "", projectPaths = [] }) {
  const createAgent = (orchestrator = false) => ({ ...newSwarmAgent(orchestrator), provider: defaults.provider || "codex", model: defaults.model || "", effort: defaults.effort || "", resources: structuredClone(defaults.resources || DEFAULT_REM_RESOURCES) });
  const [draft, setDraft] = useState(() => swarm ? structuredClone({ name: swarm.name, charter: swarm.charter, agents: swarm.agents, wakeIntervalSeconds: swarm.wakeIntervalSeconds, maxConcurrency: swarm.maxConcurrency, gitPermissions: swarm.gitPermissions ?? { local: true, remote: false }, ...limitDraft(swarm) }) : { name: "", charter: "", agents: [createAgent(true)], wakeIntervalSeconds: 60, maxConcurrency: 3, gitPermissions: { local: true, remote: false }, ...limitDraft({}) });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [expandedAgentId, setExpandedAgentId] = useState(selectedAgentId || draft.agents[0]?.id);
  const { capabilities, error, loading, refresh } = useProviderCapabilities();
  const disabled = busy || active;
  function patch(values) { setDraft((current) => ({ ...current, ...values })); }
  function patchAgent(id, values) { setDraft((current) => ({ ...current, agents: current.agents.map((agent) => agent.id === id ? { ...agent, ...values } : agent) })); }
  function setOrchestrator(id) { patch({ agents: draft.agents.map(agent => ({ ...agent, isOrchestrator: agent.id === id })) }); }
  function addAgent() {
    const agent = createAgent();
    patch({ agents: [...draft.agents, agent] });
    setExpandedAgentId(agent.id);
  }
  return <form className="swarm-setup" onInvalidCapture={event => {
    const agent = event.target.closest("[data-swarm-agent]");
    if (agent) setExpandedAgentId(agent.dataset.swarmAgent);
    for (let parent = event.target.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
  }} onSubmit={async (event) => {
    event.preventDefault();
    await onSave({ name: draft.name, charter: draft.charter, agents: draft.agents.map((agent) => {
      const provider = capabilities.find((item) => item.id === agent.provider);
      const model = provider?.models?.find((item) => item.id === (agent.model || provider.defaultModel));
      return { ...agent, model: agent.model || model?.id || "", effort: agent.effort || model?.defaultEffort || "" };
    }), wakeIntervalSeconds: draft.wakeIntervalSeconds, maxConcurrency: draft.maxConcurrency || 3, gitPermissions: draft.gitPermissions, ...limitPayload(draft) });
  }}>
    <div className="swarm-setup-scroll workflow-scrollbar">
      <div className={`swarm-setup-layout ${selectedAgentId ? "swarm-setup-single" : ""}`}>
        {!selectedAgentId ? <section className="swarm-setup-team" aria-label="Team details">
          <h3>Team</h3>
          <label className="swarm-setup-field">Name<input required disabled={disabled} className={fieldClass} value={draft.name} onChange={event => patch({ name: event.target.value })} /></label>
          <label className="swarm-setup-field"><span>Charter <span className="swarm-setup-optional">Optional · the team’s standing purpose</span></span><textarea disabled={disabled} rows={3} className={fieldClass} value={draft.charter} onChange={event => patch({ charter: event.target.value })} /></label>
          <label className="swarm-setup-field">Orchestrator<select disabled={disabled} className={fieldClass} value={draft.agents.find(agent => agent.isOrchestrator)?.id || ""} onChange={event => setOrchestrator(event.target.value)}>{draft.agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name || "Unnamed agent"}</option>)}</select></label>
          <p className="swarm-setup-hint">Plans the work, assigns it to agents, reviews results and tracks progress.</p>
          <fieldset className="swarm-setup-advanced-body" disabled={disabled}>
            <legend className="swarm-setup-field-label">Git permissions</legend>
            <label className="swarm-setup-check"><input type="checkbox" aria-label="Local Git operations" checked={draft.gitPermissions.local} onChange={event => patch({ gitPermissions: { local: event.target.checked, remote: event.target.checked && draft.gitPermissions.remote } })} />Local Git operations</label>
            <p className="swarm-setup-hint">Allow agents to stage and commit in their own worktrees through the managed Git tool.</p>
            <label className="swarm-setup-check"><input type="checkbox" disabled={!draft.gitPermissions.local} aria-label="Remote branches and pull requests" checked={draft.gitPermissions.remote} onChange={event => patch({ gitPermissions: { ...draft.gitPermissions, remote: event.target.checked } })} />Remote branches and pull requests</label>
            <p className="swarm-setup-hint">Allow publishing assignment branches and creating pull requests. Off by default. Changes apply to the next run.</p>
          </fieldset>
          <details className="swarm-setup-advanced swarm-setup-run-options">
            <summary><Settings2 size={14} />Run settings<ChevronRight size={14} /></summary>
            <div className="swarm-setup-advanced-body">
              <PositiveNumberField label="Concurrent agents" value={draft.maxConcurrency || 3} disabled={disabled} onChange={maxConcurrency => patch({ maxConcurrency })} />
              <PositiveNumberField label="Check interval (seconds)" value={draft.wakeIntervalSeconds} min={10} disabled={disabled} onChange={wakeIntervalSeconds => patch({ wakeIntervalSeconds })} />
              <p className="swarm-setup-hint">Git assignments use separate worktrees. Non-Git projects run one agent at a time. Failed agents retry after 10 seconds, doubling up to 5 minutes; after 5 failures in a row the run pauses.</p>
            </div>
          </details>
          <details className="swarm-setup-advanced swarm-setup-run-options" open={!swarm || undefined}>
            <summary><Settings2 size={14} />Limits and checks<ChevronRight size={14} /></summary>
            <div className="swarm-setup-advanced-body">
              <p className="swarm-setup-hint">The run pauses when it reaches a limit. Agents already working finish their turn.</p>
              <PositiveNumberField label="Agent turns per run" value={draft.maxTurns} disabled={disabled} onChange={maxTurns => patch({ maxTurns: Math.round(maxTurns) })} />
              <PositiveNumberField label="Running time (minutes)" value={draft.maxRunMinutes} disabled={disabled} onChange={maxRunMinutes => patch({ maxRunMinutes })} />
              <PositiveNumberField label="One turn (minutes)" value={draft.maxTurnMinutes} disabled={disabled} onChange={maxTurnMinutes => patch({ maxTurnMinutes })} />
              <PositiveNumberField label="Turns without progress before pausing" value={draft.stallTurnLimit} disabled={disabled} onChange={stallTurnLimit => patch({ stallTurnLimit: Math.round(stallTurnLimit) })} />
              <PositiveNumberField label="Repairs per assignment" value={draft.maxRepairAttempts} min={0} disabled={disabled} onChange={maxRepairAttempts => patch({ maxRepairAttempts: Math.round(maxRepairAttempts) })} />
              <OptionalNumberField label="Token limit (reported tokens, optional)" value={draft.maxTokens} disabled={disabled} onChange={maxTokens => patch({ maxTokens })} />
              <label className="swarm-setup-field">Integration checks<textarea disabled={disabled} rows={3} className={`${fieldClass} font-mono`} value={draft.checksText} onChange={event => patch({ checksText: event.target.value })} placeholder={"npm test\nruff check src"} /></label>
              <p className="swarm-setup-hint">One command per line, run in the swarm project’s review worktree before the combined result is accepted. Use double quotes to group an argument. The orchestrator can add checks of its own to milestones, but only these come from you.</p>
            </div>
          </details>
        </section> : null}
        <section className="swarm-setup-agents" aria-label="Agent configuration">
          {!selectedAgentId ? <div className="swarm-setup-heading"><h3>Agents <span className="swarm-count">{draft.agents.length}</span></h3><button type="button" disabled={disabled} className={buttonClass} onClick={addAgent}><Plus size={13} />Add agent</button></div> : null}
          {loading ? <p role="status" className="swarm-setup-hint">Discovering providers...</p> : error ? <p role="alert" className="text-xs text-red-600 dark:text-red-300">{error} <button type="button" className="underline" onClick={refresh}>Retry</button></p> : null}
          <div className="swarm-setup-roster">{draft.agents.filter(agent => !selectedAgentId || agent.id === selectedAgentId).map(agent => <details key={agent.id} className="swarm-setup-agent" open={expandedAgentId === agent.id}>
            <summary onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.currentTarget.click(); } }} onClick={event => { event.preventDefault(); setExpandedAgentId(current => current === agent.id ? null : agent.id); }}>
              <span className="swarm-agent-portrait" aria-hidden="true">{agent.name.trim().slice(0, 2).toUpperCase() || "?"}{agent.isOrchestrator ? <Crown size={12} /> : null}</span>
              <span className="swarm-setup-agent-identity"><strong>{agent.name || "New agent"}</strong><span>{agent.isOrchestrator ? "Orchestrator" : "Worker"}<span className="swarm-setup-provider">{capabilities.find(item => item.id === agent.provider)?.name || agent.provider}</span></span></span>
              <ChevronRight className="swarm-setup-chevron" size={16} />
            </summary>
            <fieldset data-swarm-agent={agent.id} disabled={disabled} className="swarm-setup-agent-body">
              <legend className="sr-only">{agent.name || "New agent"} settings</legend>
              <label className="swarm-setup-field">Name<input required className={fieldClass} value={agent.name} onChange={event => patchAgent(agent.id, { name: event.target.value })} /></label>
              <AgentWorkspaceField value={agent.workspacePath || ""} rootPath={rootPath} projectPaths={projectPaths} disabled={disabled} onChange={workspacePath => patchAgent(agent.id, { workspacePath })} />
              <label className="swarm-setup-field">Role and instructions<textarea required rows={3} className={fieldClass} value={agent.role} onChange={event => patchAgent(agent.id, { role: event.target.value })} /></label>
              <div className="swarm-setup-model"><span className="swarm-setup-field-label">Model</span><ProviderModelEffortFields loading={loading} capabilities={capabilities} provider={agent.provider} model={agent.model} effort={agent.effort} disabled={disabled} onRefresh={refresh} onChange={values => patchAgent(agent.id, { ...values, ...(values.provider && values.provider !== agent.provider ? { permissionMode: providerPermissionDefault(values.provider) } : {}) })} /></div>
              <details className="swarm-setup-advanced">
                <summary>Advanced<ChevronRight size={14} /></summary>
                <div className="swarm-setup-advanced-body">
                  {selectedAgentId ? <label className="swarm-setup-check"><input type="radio" name="orchestrator" checked={agent.isOrchestrator} onChange={() => setOrchestrator(agent.id)} />Team orchestrator</label> : null}
                  <label className="swarm-setup-check"><input type="checkbox" checked={agent.allowSteering} onChange={event => patchAgent(agent.id, { allowSteering: event.target.checked })} />Allow steering</label>
                  <p className="swarm-setup-hint">Deliver new messages while this agent works, when supported.</p>
                  <label className="swarm-setup-field">Permissions<select className={fieldClass} value={agent.permissionMode || providerPermissionDefault(agent.provider)} onChange={event => patchAgent(agent.id, { permissionMode: event.target.value })}>{providerPermissionOptions(agent.provider).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
                  {FULL_ACCESS_MODES.has(agent.permissionMode) ? <p className="swarm-warning" role="note">Runs without a sandbox or approval prompts. This agent can change any file you can, including your original checkouts, and run commands such as git push. You’ll be asked to confirm before each run starts.</p> : null}
                  {agent.permissionMode === "cli-managed" ? <p className="swarm-setup-hint">The CLI controls permissions. Raticode shell and web switches do not restrict its native tools. Configured CLI tools and MCP servers may also be available.</p> : null}
                  <details className="swarm-setup-resources"><summary>Tools and resources</summary><div className="pt-3"><RemResources value={agent.resources || DEFAULT_REM_RESOURCES} onChange={resources => patchAgent(agent.id, { resources })} /></div></details>
                </div>
              </details>
              {!agent.isOrchestrator && !selectedAgentId ? <button type="button" className="swarm-setup-remove" aria-label={`Remove ${agent.name || "agent"}`} onClick={() => { patch({ agents: draft.agents.filter(item => item.id !== agent.id) }); setExpandedAgentId(draft.agents.find(item => item.id !== agent.id)?.id); }}><Trash2 size={13} />Remove agent</button> : null}
              {remResourceError(agent.resources) ? <p role="alert" className="text-xs text-red-600 dark:text-red-300">{remResourceError(agent.resources)}</p> : null}
            </fieldset>
          </details>)}</div>
        </section>
      </div>
    </div>
    <footer className="swarm-setup-footer">{confirmDelete ? <div role="alertdialog" aria-label="Delete this swarm" className="swarm-confirm"><p>Delete <strong>{swarm.name}</strong> and its run history? Review worktrees and branches stay in your repositories.</p><div className="flex gap-2"><button type="button" className={buttonClass} onClick={() => setConfirmDelete(false)}>Keep swarm</button><button type="button" className={`${buttonClass} swarm-danger-button`} disabled={busy} onClick={() => void onDelete?.()}>Delete swarm</button></div></div> : <><p role="status">{active ? "Pause or stop the run to edit this team. Changes apply to the next run." : "Changes apply to the next run."}</p>{swarm && onDelete && !selectedAgentId ? <button type="button" className={`${buttonClass} swarm-danger-button`} disabled={busy || active} onClick={() => setConfirmDelete(true)}><Trash2 size={13} />Delete swarm…</button> : null}<button type="submit" disabled={disabled || !draft.name.trim() || draft.agents.some(agent => !agent.name.trim() || !agent.role.trim() || remResourceError(agent.resources))} className={primaryClass}>{busy ? "Saving..." : selectedAgentId ? "Save agent" : swarm ? "Save changes" : "Create swarm"}</button></>}</footer>
  </form>;
}

function SwarmBoard({ swarm, busy, onSend, onResolve, readOnly = false }) {
  const [body, setBody] = useState("");
  const [recipientId, setRecipientId] = useState("");
  const agents = swarm?.run?.configuration?.agents || swarm?.agents || [];
  const agentName = (id) => id === "user" ? "You" : id === "system" ? "System" : agents.find((agent) => agent.id === id)?.name || id;
  const run = swarm?.run;
  return <div className="flex min-h-0 flex-1 flex-col">
    <div aria-label="Shared message board" className="workflow-scrollbar min-h-0 flex-1 overflow-y-auto px-4" role="log" aria-live="polite">
      {!run?.messages?.length ? <div className="swarm-board-empty"><MessagesSquare size={28} strokeWidth={1} aria-hidden="true" /><p>No messages yet</p></div> : run.messages.map((message) => <article key={message.id} className="swarm-message"><div className="swarm-message-avatar" aria-hidden="true">{agentName(message.senderId).trim().slice(0, 2).toUpperCase()}</div><div className="swarm-message-content"><header className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs"><span className="font-semibold">{agentName(message.senderId)}</span><span className="text-muted">to {message.recipientIds?.length ? message.recipientIds.map(agentName).join(", ") : "Everyone"}</span><time className="ml-auto text-muted" dateTime={message.createdAt}>{new Date(message.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></header><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">{message.body}</p>{message.deliveries?.length ? <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">{message.deliveries.map((delivery) => <span key={delivery.agentId} title={delivery.reason || ""}>{agentName(delivery.agentId)}: {delivery.state}{delivery.reason ? ` · ${delivery.reason}` : ""}{(delivery.state === "uncertain" || (delivery.state === "queued" && run.agentStates?.[delivery.agentId]?.state === "retry_wait" && delivery.executionAttemptId === run.agentStates[delivery.agentId].retryAttemptId)) && !readOnly ? <span className="ml-2 inline-flex flex-wrap gap-2"><button type="button" className="font-semibold underline underline-offset-2" disabled={busy || !["running", "paused"].includes(run.state)} onClick={() => void onResolve({ messageId: message.id, agentId: delivery.agentId, action: "retry" })}>{delivery.state === "uncertain" ? "Retry message" : "Retry now"}</button>{delivery.state === "uncertain" ? <button type="button" className="underline underline-offset-2" disabled={busy} onClick={() => void onResolve({ messageId: message.id, agentId: delivery.agentId, action: "dismiss" })}>Dismiss delivery</button> : null}</span> : null}</span>)}</div> : null}</div></article>)}
    </div>
    {run && !readOnly ? <form className="shrink-0 space-y-2 border-t border-line p-4" onSubmit={async (event) => { event.preventDefault(); if (await onSend({ body, ...(recipientId ? { recipientId } : {}) })) setBody(""); }}><label className="flex items-center gap-2 text-xs text-muted">Send to<select aria-label="Message recipient" className="min-w-0 rounded border border-line bg-white px-2 py-1 text-ink" value={recipientId} onChange={(event) => setRecipientId(event.target.value)}><option value="">Orchestrator</option><option value="all">Everyone</option>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label><textarea aria-label="Message to swarm" required rows={2} className={fieldClass} placeholder="Message…" value={body} onChange={(event) => setBody(event.target.value)} /><div className="flex justify-end"><button type="submit" disabled={busy || !body.trim() || !["running", "paused"].includes(run.state)} className={primaryClass}><Send size={13} />Send message</button></div></form> : null}
  </div>;
}

function ProgressMeter({ objectives, label }) {
  const progress = milestoneProgress(objectives);
  return <div className="swarm-progress-meter"><div className="swarm-progress-caption"><span>{label}</span><span className="swarm-progress-value">{progress.percent === null ? "Not planned" : `${Math.round(progress.percent)}%`}</span></div><progress aria-label={label} max={100} value={progress.percent || 0} />{progress.totalCount ? <p className="swarm-progress-detail" title={`${progress.acceptedWeight} of ${progress.totalWeight} effort points accepted`}>{progress.acceptedCount} / {progress.totalCount} milestones accepted</p> : null}</div>;
}

function MilestoneSnapshot({ objectives }) {
  const milestones = objectives.flatMap(objective => objective.milestones.map(item => ({ ...item, objective: objective.title })));
  const rows = items => items.map(item => <div key={item.id} data-status={item.status}><span aria-hidden="true">{item.status === "accepted" ? "✓" : item.status === "working" ? "●" : item.status === "blocked" ? "!" : "○"}</span><span title={item.objective}>{item.title}</span><RunStatus state={item.status} /></div>);
  return <div className="swarm-milestone-list" aria-label="Milestone status"><section><h3>Milestones</h3>{rows(milestones.slice(0, 6))}</section>{milestones.length > 6 ? <details><summary>Show {milestones.length - 6} more milestones</summary><section>{rows(milestones.slice(6))}</section></details> : null}</div>;
}

function SwarmProgress({ run, agents, busy, onSave, readOnly = false }) {
  const [draft, setDraft] = useState(null);
  const [revision, setRevision] = useState(null);
  const [reason, setReason] = useState("");
  const objectives = draft || run?.objectives || [];
  function beginEdit() { setDraft(structuredClone(run?.objectives || [])); setRevision(run?.revision); }
  function updateObjective(id, values) { setDraft((current) => current.map((item) => item.id === id ? { ...item, ...values } : item)); }
  function updateMilestone(objective, id, values) { updateObjective(objective.id, { milestones: objective.milestones.map((item) => item.id === id ? { ...item, ...values } : item) }); }
  if (!run) return <ProgressMeter objectives={[]} label="Overall progress" />;
  return <div className="swarm-progress-content"><div>
    <ProgressMeter objectives={objectives} label="Overall progress" />
    <MilestoneSnapshot objectives={objectives} />
    <details className="swarm-objectives"><summary>Milestone evidence and editing</summary><div className="space-y-5"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold">Objectives</h3>{draft ? <button type="button" className={buttonClass} onClick={() => setDraft([...draft, { id: crypto.randomUUID(), title: "", acceptanceCriteria: "", milestones: [] }])}><Plus size={13} />Add objective</button> : !readOnly ? <button type="button" className={buttonClass} disabled={busy} onClick={beginEdit}>Edit milestones</button> : null}</div>
    {!objectives.length ? <p className="text-sm leading-6 text-muted">No milestones. Add an objective to plan this run.</p> : null}
    {objectives.map((objective, objectiveIndex) => <section key={objective.id} className="space-y-4 border-b border-line pb-6">
      {draft ? <><label className="block space-y-1.5 text-xs text-muted">Objective {objectiveIndex + 1}<input aria-label={`Objective ${objectiveIndex + 1} title`} className={fieldClass} value={objective.title} onChange={(event) => updateObjective(objective.id, { title: event.target.value })} /></label><label className="block space-y-1.5 text-xs text-muted">Acceptance criteria<textarea className={fieldClass} rows={2} value={objective.acceptanceCriteria || ""} onChange={(event) => updateObjective(objective.id, { acceptanceCriteria: event.target.value })} /></label></> : <><h4 className="text-sm font-semibold">{objective.title}</h4>{objective.acceptanceCriteria ? <p className="text-xs leading-5 text-muted">{objective.acceptanceCriteria}</p> : null}</>}
      <ProgressMeter objectives={[objective]} label={`${objective.title || "Objective"} progress`} />
      {objective.milestones.map((milestone, index) => draft ? <div key={milestone.id} className="space-y-3 border-t border-line pt-4"><label className="block space-y-1.5 text-xs text-muted">Milestone {index + 1}<input className={fieldClass} value={milestone.title} onChange={(event) => updateMilestone(objective, milestone.id, { title: event.target.value })} /></label><div className="swarm-milestone-fields"><PositiveNumberField label={`Milestone ${index + 1} weight`} value={milestone.weight} min={0.01} onChange={(weight) => updateMilestone(objective, milestone.id, { weight })} /><label className="block space-y-1.5 text-xs text-muted">Status<select className={fieldClass} value={milestone.status} onChange={(event) => updateMilestone(objective, milestone.id, { status: event.target.value })}>{["planned", "ready", "working", "in_review", "accepted", "blocked", "cancelled"].map((status) => <option key={status} value={status}>{status.replaceAll("_", " ")}</option>)}</select></label><label className="block space-y-1.5 text-xs text-muted">Owner<select className={fieldClass} value={milestone.ownerId || ""} onChange={(event) => updateMilestone(objective, milestone.id, { ownerId: event.target.value })}><option value="">Unassigned</option>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label></div><label className="block space-y-1.5 text-xs text-muted">Evidence<textarea className={fieldClass} rows={2} value={milestone.evidence || ""} onChange={(event) => updateMilestone(objective, milestone.id, { evidence: event.target.value })} placeholder="Tests, files, or review that demonstrate completion." /></label></div> : <div key={milestone.id} className="flex items-start gap-3 border-t border-line pt-3 text-xs"><span className="min-w-0 flex-1"><span className="block font-medium">{milestone.title}</span><span className="mt-1 block text-muted">{milestone.status.replaceAll("_", " ")}{milestone.ownerId ? ` · ${agents.find((agent) => agent.id === milestone.ownerId)?.name || milestone.ownerId}` : ""}</span>{milestone.evidence ? <span className="mt-2 block whitespace-pre-wrap break-words leading-5 text-muted">{milestone.evidence}</span> : null}</span><span className="shrink-0 tabular-nums text-muted">{milestone.weight} pts</span></div>)}
      {draft ? <button type="button" className={buttonClass} onClick={() => updateObjective(objective.id, { milestones: [...objective.milestones, { id: crypto.randomUUID(), title: "", weight: 1, status: "planned", ownerId: "", evidence: "", acceptanceCriteria: "" }] })}><Plus size={13} />Add milestone</button> : null}
    </section>)}
    {draft ? <div className="space-y-3"><label className="block space-y-1.5 text-xs text-muted">Reason for changes<input className={fieldClass} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Explain scope or estimate changes for the history." /></label><div className="flex gap-2"><button type="button" className={primaryClass} disabled={busy || objectives.some((item) => !item.title.trim() || item.milestones.some((milestone) => !milestone.title.trim()))} onClick={async () => { if (await onSave({ objectives: draft, ...(revision != null ? { revision } : {}), reason })) { setDraft(null); setReason(""); } }}>Save progress</button><button type="button" className={buttonClass} disabled={busy} onClick={() => setDraft(null)}>Cancel</button></div></div> : null}
    <p className="text-xs leading-5 text-muted">Progress counts accepted milestones, weighted by effort.</p>
    <details className="border-t border-line pt-3"><summary className="cursor-pointer text-xs font-semibold">Activity and estimate history</summary><ol className="mt-3 space-y-3">{(run.events || []).slice().reverse().map((event) => { const change = objectiveProgressChange(event); return <li key={event.id} className="text-xs leading-5"><time className="text-muted">{new Date(event.createdAt).toLocaleString()}</time><p>{event.kind.replaceAll("_", " ")} · {agents.find((agent) => agent.id === event.actorId)?.name || event.actorId}</p>{change ? <p className="tabular-nums text-muted">Progress {Math.round(change.before.percent || 0)}% → {Math.round(change.after.percent || 0)}% · {change.before.totalWeight} → {change.after.totalWeight} effort points</p> : null}{event.payload?.reason ? <p className="text-muted">{event.payload.reason}</p> : null}</li>; })}</ol></details></div></details>
  </div></div>;
}


function SwarmAgentActivity({ run, agents, selectedAgentId, compact = false }) {
  const [agentId, setAgentId] = useState(selectedAgentId || agents[0]?.id || "");
  useEffect(() => { if (selectedAgentId) setAgentId(selectedAgentId); }, [selectedAgentId]);
  const state = run?.agentStates?.[agentId];
  const agent = agents.find((item) => item.id === agentId);
  return <div className="workflow-scrollbar min-h-0 flex-1 overflow-y-auto p-4"><div className="mx-auto max-w-3xl space-y-5">
    {!compact ? <label className="block space-y-1.5 text-xs text-muted">Agent<select className={fieldClass} value={agentId} onChange={(event) => setAgentId(event.target.value)}>{agents.map((item) => <option key={item.id} value={item.id}>{item.name}{item.isOrchestrator ? " · Orchestrator" : ""}</option>)}</select></label> : null}
    <div className="space-y-1 text-xs"><p className="font-semibold">{agent?.role}</p><p className="text-muted">{agent?.provider} · {agent?.model || "Default model"} · {agent?.effort || "Default effort"}</p><p className="break-all text-muted">Project: {agent?.workspacePath || "Swarm project"}</p><p role="status" className="text-muted">{state?.state || "Idle"}{state?.activity ? ` · ${state.activity}` : ""}</p>{state?.error ? <p role="alert" className="text-red-600 dark:text-red-300">{state.error}</p> : null}</div>
    <section className="space-y-3"><h3 className="text-sm font-semibold">Recent tool activity</h3>{!state?.traces?.length ? <p className="text-xs text-muted">Tool activity appears here as the agent works.</p> : state.traces.slice().reverse().map((trace, index) => <details key={index} className="border-b border-line pb-3"><summary className="cursor-pointer text-xs">{trace.title || trace.trace?.title || trace.text || "Provider activity"}</summary><pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-3 text-xs leading-5">{trace.body || trace.trace?.body || JSON.stringify(trace, null, 2)}</pre></details>)}</section>
    <section className="space-y-3"><h3 className="text-sm font-semibold">Conversation</h3>{!state?.messages?.length ? <p className="text-xs text-muted">This agent has not started a turn.</p> : state.messages.map((message, index) => <details key={index} open={message.role === "assistant"} className="border-b border-line pb-3"><summary className="cursor-pointer text-xs font-semibold">{message.role === "assistant" ? agent?.name : "Task and shared context"}</summary><p className="mt-2 whitespace-pre-wrap break-words text-xs leading-6">{message.body}</p></details>)}</section>
  </div></div>;
}


export function SwarmDigest({ run, agents }) {
  if (!run) return null;
  const summaries = new Map((run.digest?.agents || []).map(item => [item.agentId, item.summary]));
  const milestones = (run.objectives || []).flatMap(objective => objective.milestones || []);
  return <aside aria-label="Team status" className="swarm-digest">
    <div className="swarm-section-heading"><h2>Team status</h2>{run.digest?.updatedAt ? <time dateTime={run.digest.updatedAt} title={new Date(run.digest.updatedAt).toLocaleString()}>Orchestrator update · {new Date(run.digest.updatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time> : <span>Current assignments</span>}</div>
    <ul>{agents.map(agent => {
      const state = run.agentStates?.[agent.id] || {};
      const assigned = milestones.find(item => item.id === state.milestoneId && !["accepted", "cancelled"].includes(item.status));
      const pending = (run.messages || []).some(message => message.deliveries?.some(item => item.agentId === agent.id && item.state === "queued"));
      const status = state.state === "idle" && pending && run.state === "running" ? "queued" : state.state || "idle";
      const summary = summaries.get(agent.id) || assigned?.title || (status === "working" ? "Reviewing the task and board" : status === "retry_wait" ? "Waiting to retry after a provider error" : "No active assignment");
      return <li key={agent.id}><strong>{agent.name}</strong><span title={summary}>{summary.length > 160 ? `${summary.slice(0, 157)}...` : summary}</span><small>{status.replaceAll("_", " ")}</small></li>;
    })}</ul>
    {run.idleDiagnosis?.body ? <p className="swarm-digest-diagnosis">{run.idleDiagnosis.body}</p> : run.idleDiagnosis?.state === "working" ? <p role="status">Orchestrator is explaining why the run became idle.</p> : null}
  </aside>;
}
