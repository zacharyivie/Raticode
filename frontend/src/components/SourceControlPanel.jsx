import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown, ArrowUp, Check, Copy, Eraser, FolderOpen, FolderPlus, GitBranch, GitCommitHorizontal, GitMerge,
  Minus, MoreHorizontal, Plus, RefreshCw, Trash2, X,
} from "lucide-react";
import { filterHistory, layoutHistoryGraph } from "../lib/gitHistoryGraph.js";
import { mainWorktreePath } from "../lib/useSourceControl.js";
import { timeAgo } from "../lib/timeText.js";
import { pathKey as normalizeWorkspacePath, joinWorkspacePath, samePath, workspaceBasename } from "../lib/workspacePaths.js";
import { Dialog } from "./Dialog.jsx";
import GitHistoryLane, { GitHistoryRefs, GitHistoryStats } from "./GitHistoryLane.jsx";
import GitIntegrationControls from "./GitIntegrationControls.jsx";
import HistoryBranchFilter from "./HistoryBranchFilter.jsx";
import RemActionIcon from "./RemActionIcon.jsx";
import WorktreeContextMenu, { historyOperations, integrationOperations } from "./WorktreeContextMenu.jsx";

// The letter sourceControlStatus gives a change, in words. A rename or copy also
// comes back as A, with the path it came from.
const CHANGE_WORDS = { M: "Modified", A: "Added", D: "Deleted", U: "Untracked", "!": "Conflict" };

function changeDescription(entry) {
  const word = CHANGE_WORDS[entry.status] || entry.status;
  if (entry.status === "!") return `${word}: unresolved index conflict. Stage the working file to resolve.`;
  if (!entry.originalPath) return word;
  const moved = entry.indexStatus === "C" || entry.worktreeStatus === "C" ? "copied" : "renamed";
  if (entry.status === "A") return `${moved === "copied" ? "Copied" : "Renamed"} from ${entry.originalPath}`;
  return `${word}, ${moved} from ${entry.originalPath}`;
}

// The project sidebar's Source control view: Changes, History and Branches, the
// commit message box, and the menus and dialogs they open. useSourceControl holds
// its state and runs the Git actions.
export default function SourceControlPanel({ state, rootPath, activeFilePath, sidebarView, confirm, onOpenFile, onSelectProject }) {
  const {
    sourceControl, loadSourceControlStatus, loadGitPanels, changeSourceControl, gitBusy, gitOperationBusy,
    gitStatusPending, gitOperationRef, setIntegrationBusy, integrationChanged, gitError, setGitError,
    gitNotice, setGitNotice, blockedBranch, setBlockedBranch, remote, setRemote, sourceTab, setSourceTab,
    stagedCount, commitMessage, editCommitMessage, generatingMessage, loadingCommitJob,
    generateCommitMessage, gitHistory, setGitHistory, historyQuery, setHistoryQuery, historyRefs,
    setHistoryRefs, historyLimit, setHistoryLimit, expandedCommits, setExpandedCommits, copiedCommitHash,
    copyCommitHash, historyMenu, setHistoryMenu, openHistoryMenu, historyAction, branchRequest,
    setBranchRequest, branchDraft, setBranchDraft, worktrees, worktreeMenu, setWorktreeMenu, openBranchMenu,
    deleteBranch, createBranchWorktree, integrationSource, setIntegrationSource, integrationRequest,
    setIntegrationRequest, worktreeFormOpen, setWorktreeFormOpen, worktreeStartPoint, setWorktreeStartPoint,
    worktreeBranch, setWorktreeBranch, worktreeFolder, worktreeCreateBranch, setWorktreeCreateBranch,
    chooseWorktreeFolder, createWorktree, worktreeRemoval, setWorktreeRemoval, worktreeRemovalError,
    setWorktreeRemovalError, removeWorktree, branchCleanup, setBranchCleanup, previewBranchCleanup,
    saveCleanupKeep, runBranchCleanup,
  } = state;
  const historyGraph = useMemo(() => layoutHistoryGraph(filterHistory(gitHistory.commits, historyQuery)), [gitHistory.commits, historyQuery]);
  // The pinned scrollbar under the graph column mirrors the list's horizontal scroll.
  const historyListRef = useRef(null);
  const historyScrollbarRef = useRef(null);
  const mirrorHistoryScroll = (from, to) => {
    if (from.current && to.current && Math.abs(to.current.scrollLeft - from.current.scrollLeft) >= 1) to.current.scrollLeft = from.current.scrollLeft;
  };
  return (
    <>
      <section id="sidebar-panel-source-control" role="tabpanel" aria-labelledby="sidebar-tab-source-control" hidden={sidebarView !== "source-control"} className={`scm-panel min-h-0 min-w-0 flex-1 flex-col bg-white text-ink ${sidebarView === "source-control" ? "flex" : "hidden"}`}>
        <div className="ui-panel-header">
          <span className="ui-section-title">Source control</span>
          <div className="flex shrink-0 items-center gap-1">
            {sourceControl.active && (sourceControl.ahead != null || sourceControl.remotes?.length) ? <>
              <button aria-label="Pull" title="Pull" className="grid h-7 w-7 place-items-center rounded text-muted hover:bg-slate-100 focus-visible:outline disabled:opacity-40" disabled={gitBusy || sourceControl.ahead == null} type="button" onClick={() => void changeSourceControl("pull")}><ArrowDown aria-hidden="true" size={13} /></button>
              {sourceControl.ahead != null ? <button aria-label="Push" title="Push" className="grid h-7 w-7 place-items-center rounded text-muted hover:bg-slate-100 focus-visible:outline disabled:opacity-40" disabled={gitBusy} type="button" onClick={() => void changeSourceControl("push")}><ArrowUp aria-hidden="true" size={13} /></button> : null}
            </> : null}
          <button aria-label="Refresh source control" className="grid h-7 w-7 place-items-center rounded text-muted hover:bg-slate-100" disabled={gitBusy || gitHistory.loading || worktrees.loading} type="button" onClick={() => { void loadSourceControlStatus(); void loadGitPanels(); }}><RefreshCw size={13} /></button>
          </div>
        </div>
          {sourceControl.active ? <div className="shrink-0 space-y-2 border-b border-line px-3 pb-3">
            <p className="truncate text-[11px] text-muted" title={rootPath}>{workspaceBasename(rootPath)}</p>
                <p aria-label="Current branch" className="flex items-center gap-2 px-1 text-xs">
                  <GitBranch aria-hidden="true" size={13} />
                  <span className="truncate">{sourceControl.branch || "Detached HEAD"}</span>
                </p>
                <p className="px-1 text-[11px] text-muted">{sourceControl.ahead == null ? "Local branch" : `${sourceControl.ahead} ahead · ${sourceControl.behind} behind`}</p>
                {sourceControl.ahead == null && sourceControl.remotes?.length ? <div className="flex flex-wrap gap-1 px-1">
                    <select aria-label="Publish remote" className="min-w-0 flex-1 rounded border border-line bg-white text-[11px]" value={remote || sourceControl.remotes?.[0] || ""} onChange={(event) => setRemote(event.target.value)}>
                      {!sourceControl.remotes?.length ? <option value="">No remote configured</option> : sourceControl.remotes.map((name) => <option key={name}>{name}</option>)}
                    </select>
                    <button type="button" className="rounded border border-line px-2 py-1 text-[11px] disabled:opacity-40" disabled={gitBusy || !sourceControl.remotes?.length} onClick={() => void changeSourceControl("publish", remote || sourceControl.remotes[0])}>Publish</button>
                </div> : null}

          </div> : null}
          <div role="tablist" aria-label="Source control views" className="flex shrink-0 gap-3 border-b border-line px-3">
            {["changes", "history", "branches"].map((tab, index, tabs) => <button key={tab} id={`scm-tab-${tab}`} role="tab" aria-selected={sourceTab === tab} aria-controls="scm-content" tabIndex={sourceTab === tab ? 0 : -1} className={`min-w-0 border-b-2 py-2.5 text-[11px] ${sourceTab === tab ? "border-brand font-semibold text-ink" : "border-transparent text-muted hover:text-ink"}`} type="button" onClick={() => setSourceTab(tab)} onKeyDown={(event) => {
              const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
              if (next < 0) return;
              event.preventDefault(); setSourceTab(tabs[next]); document.getElementById(`scm-tab-${tabs[next]}`)?.focus();
            }}>{tab === "changes" ? "Changes" : tab === "history" ? "History" : "Branches"}</button>)}
          </div>
          <div id="scm-content" role="tabpanel" aria-labelledby={`scm-tab-${sourceTab}`} tabIndex={0} className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
            {gitError || blockedBranch ? <div role="alert" className="scm-notice mb-3 rounded p-3 text-xs">
              <div className="flex items-start gap-2"><strong className="min-w-0 flex-1">{blockedBranch ? "Changes would be overwritten" : sourceControlErrorSummary(gitError)}</strong><button aria-label="Dismiss Git error" type="button" onClick={() => { setGitError(""); setBlockedBranch(""); }}><X size={13} /></button></div>
              {blockedBranch ? <><p className="my-2 break-words">Commit your changes or stash them before switching to {blockedBranch}.</p><div className="flex flex-wrap gap-2"><button className="rounded border border-current px-2 py-1" disabled={gitBusy} type="button" onClick={() => void changeSourceControl("stash-switch", blockedBranch)}>Stash &amp; switch</button><button type="button" disabled={gitBusy} onClick={() => { setBlockedBranch(""); setGitError(""); setGitNotice(""); }}>Stay on {sourceControl.branch || "this branch"}</button></div></> : null}
              {gitError ? <details className="mt-2"><summary className="cursor-pointer">Technical details</summary><pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words text-[11px]">{gitError}</pre></details> : null}
            </div> : null}
            {gitNotice && !blockedBranch ? <p role="status" className="mb-3 px-1 text-xs text-muted">{gitNotice}</p> : null}
            {sourceControl.operation || sourceControl.entries.some(entry => entry.status === "!") ? <section aria-label="Merge conflicts" className="mb-4 space-y-2 rounded border border-line p-3 text-xs">
              <strong>{sourceControl.operation ? `${sourceControl.operation === "rebase" ? "Rebase" : "Merge"} paused` : "Unresolved conflicts"}</strong>
              <p>The ! entry in Staged is an unresolved index conflict. Rem can resolve and stage the fixes, commit them, and continue until the rebase or merge is complete.</p>
              <div className="flex flex-wrap gap-2"><button className="rounded border border-line px-2 py-1.5" type="button" onClick={() => window.dispatchEvent(new CustomEvent("gofer:rem-context", { detail: { mode: "conflicts", projectRoot: rootPath, text: sourceControl.entries.filter(entry => entry.status === "!").map(entry => entry.path).join("\n") } }))}>Resolve conflicts with Rem</button>
              {sourceControl.operation ? <><button className="rounded border border-line px-2 py-1.5" type="button" disabled={gitBusy || sourceControl.entries.some(entry => entry.status === "!")} onClick={() => void changeSourceControl(`${sourceControl.operation}-continue`)}>Continue {sourceControl.operation}</button><button className="rounded border border-line px-2 py-1.5" type="button" disabled={gitBusy} onClick={async () => { const operation = sourceControl.operation; if (await confirm({ confirmLabel: `Abort ${operation}`, danger: true, message: `Abort this ${operation}? Conflict resolution edits will be discarded.` })) void changeSourceControl(`${operation}-abort`); }}>Abort {sourceControl.operation}</button></> : null}</div>
            </section> : null}
            {!sourceControl.active ? <p className="px-2 py-6 text-xs text-muted">This project is not a Git repository.</p> : <>
              {sourceTab === "changes" ? sourceControl.entries.length ? <>
                {[["staged", "Staged"], ["unstaged", "Unstaged"]].map(([group, title]) => {
                  const entries = sourceControl.entries.filter((entry) => entry[group] || (group === "staged" && entry.status === "!"));
                  if (!entries.length) return null;
                  return <details key={group} open aria-label={title} className="mb-5">
                    <summary className="cursor-pointer px-1 py-1 text-[11px] font-semibold text-ink">
                      <span className="inline-flex w-[calc(100%-1rem)] items-center gap-1 align-middle">
                        <span className="min-w-0 flex-1">{title} · {entries.length}</span>
                        <button aria-label={group === "staged" ? "Unstage all changes" : "Stage all changes"} title={group === "staged" ? "Unstage all changes" : "Stage all changes"} className="grid h-7 w-7 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 hover:text-ink focus-visible:outline disabled:opacity-40" disabled={gitBusy || !entries.some(entry => group !== "staged" || entry.status !== "!")} type="button" onClick={(event) => { event.preventDefault(); event.stopPropagation(); void changeSourceControl(group === "staged" ? "unstage" : "stage", entries.filter(entry => group !== "staged" || entry.status !== "!").map((entry) => entry.path)); }}>{group === "staged" ? <Minus size={12} /> : <Plus size={12} />}</button>
                        <button aria-label={`Discard all ${group} changes`} title={`Discard all ${group} changes`} className="grid h-7 w-7 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 hover:text-red-700 focus-visible:outline disabled:opacity-40" disabled={gitBusy || !entries.length || entries.some(entry => entry.status === "!")} type="button" onClick={(event) => { event.preventDefault(); event.stopPropagation(); void changeSourceControl(group === "staged" ? "revert-staged" : "revert", entries.filter(entry => group !== "staged" || entry.status !== "!").map((entry) => entry.path)); }}><Trash2 size={12} /></button>
                      </span>
                    </summary>
                    {entries.map((entry) => <div key={entry.path} className={`scm-file flex min-h-12 items-center gap-1 rounded px-2 ${samePath(activeFilePath, joinWorkspacePath(rootPath, entry.path)) ? "bg-indigo-50" : "hover:bg-slate-50"}`}>
                      <span aria-hidden="true" title={changeDescription(entry)} className="w-3 shrink-0 text-[10px] text-muted">{entry.status}</span>
                      <button className="min-w-0 flex-1 truncate text-left text-[11px] text-ink" title={entry.path} type="button" onClick={() => onOpenFile?.(joinWorkspacePath(rootPath, entry.path), { diff: true, gitGroup: entry.status === "!" ? "unstaged" : group })}><span className="block truncate text-xs font-medium">{workspaceBasename(entry.path)}</span><span className="block truncate text-[11px] text-muted">{entry.path.includes("/") ? entry.path.slice(0, entry.path.lastIndexOf("/")) : ""}</span><span className="sr-only">, {changeDescription(entry)}</span></button>
                      <button aria-label={`${group === "staged" ? "Unstage" : "Stage"} ${entry.path}`} title={group === "staged" ? "Unstage change" : "Stage change"} className="grid h-7 w-7 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 focus-visible:outline disabled:opacity-40" disabled={gitBusy || (group === "staged" && entry.status === "!")} type="button" onClick={() => void changeSourceControl(group === "staged" ? "unstage" : "stage", entry.path)}>{group === "staged" ? <Minus size={12} /> : <Plus size={12} />}</button>
                      <button aria-label={`Discard ${group} changes to ${entry.path}`} title="Discard changes" className="grid h-7 w-7 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 hover:text-red-700 focus-visible:outline disabled:opacity-40" disabled={gitBusy || entry.status === "!"} type="button" onClick={() => void changeSourceControl(group === "staged" ? "revert-staged" : "revert", entry.path)}><Trash2 size={12} /></button>
                    </div>)}
                  </details>;
                })}
              </> : <div className="px-3 py-10 text-center"><Check className="mx-auto mb-3 text-muted" size={20} /><strong className="text-sm font-semibold">Working tree clean</strong><p className="mt-1 text-xs text-muted">No uncommitted changes.</p></div> : null}
              {sourceTab === "branches" ? <>
            <div className="flex h-7 items-center justify-between">
              <span className="text-[10px] font-semibold text-ink">Worktrees</span>
              <span className="flex items-center">
                <button aria-label="Clean up branches" title="Delete outdated branches and their worktrees" className="grid h-6 w-6 place-items-center rounded text-muted hover:bg-slate-100 hover:text-ink disabled:opacity-40" type="button" disabled={gitBusy} onClick={() => void previewBranchCleanup()}><Eraser size={12} /></button>
                <button aria-label="Add worktree" className="grid h-6 w-6 place-items-center rounded text-muted hover:bg-slate-100 hover:text-ink" type="button" onClick={() => { setWorktreeStartPoint(""); setWorktreeFormOpen((current) => !current); }}><FolderPlus size={12} /></button>
              </span>
            </div>
            {worktreeFormOpen ? (
              <form className="mb-2 space-y-1.5 rounded-md bg-slate-50 p-2" onSubmit={createWorktree}>
                {worktreeStartPoint ? <p className="text-xs text-muted">Starting at {worktreeStartPoint.slice(0, 8)}</p> : null}
                <input aria-label="Worktree branch" className="h-7 w-full rounded border border-line bg-white px-2 text-[11px] outline-none focus:border-indigo-500" placeholder="Branch name" required value={worktreeBranch} onChange={(event) => setWorktreeBranch(event.target.value)} />
                <button className="flex h-7 w-full items-center gap-1.5 rounded border border-line bg-white px-2 text-left text-[11px] text-muted hover:text-ink" type="button" onClick={chooseWorktreeFolder}><FolderOpen size={12} /><span className="min-w-0 flex-1 truncate">{worktreeFolder || "Choose an empty folder"}</span></button>
                <label className="flex items-center gap-1.5 text-[10px] text-muted"><input checked={worktreeCreateBranch} disabled={Boolean(worktreeStartPoint)} type="checkbox" onChange={(event) => setWorktreeCreateBranch(event.target.checked)} />Create a new branch</label>
                <button className="h-7 w-full rounded bg-brand text-[11px] font-semibold text-white disabled:opacity-40" disabled={!worktreeBranch.trim() || !worktreeFolder} type="submit">Add worktree</button>
              </form>
            ) : null}
            {worktrees.loading && !worktrees.items.length ? <p className="py-2 text-[11px] text-muted">Loading worktrees...</p> : null}
            {worktrees.items.filter((worktree) => !worktree.missing && !worktree.prunable).map((worktree) => {
              const activeWorktree = normalizeWorkspacePath(worktree.path) === normalizeWorkspacePath(rootPath);
              return (
                <div
                  key={worktree.path}
                  className="group relative flex min-h-8 items-center gap-1 rounded px-1.5 hover:bg-slate-50"
                  onContextMenu={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    if (gitBusy || !worktree.branch) return;
                    setWorktreeMenu({ source: worktree.branch, x: event.clientX, y: event.clientY, trigger: event.currentTarget.querySelector("button") });
                  }}
                  onKeyDown={(event) => {
                    if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
                    event.preventDefault();
                    event.stopPropagation();
                    if (gitBusy || !worktree.branch) return;
                    const rect = event.currentTarget.getBoundingClientRect();
                    setWorktreeMenu({ source: worktree.branch, x: rect.left, y: rect.bottom, trigger: event.target });
                  }}
                >
                  {activeWorktree ? <span aria-hidden="true" className="absolute inset-y-1 left-0 w-px bg-brand" /> : null}
                  <GitBranch className="shrink-0 text-muted" size={12} />
                  <button
                    aria-current={activeWorktree ? "page" : undefined}
                    className="min-w-0 flex-1 py-1 text-left"
                    title={worktree.path}
                    type="button"
                    onClick={() => onSelectProject?.(worktree.path, {
                      mainProjectRoot: mainWorktreePath(worktrees.items, rootPath),
                    })}
                  >
                    <span className="block truncate text-[11px] font-medium text-ink">{worktree.branch || "Detached HEAD"}</span>
                    <span className="block truncate text-[11px] text-muted">
                      {worktree.path}
                    </span>
                  </button>
                  {worktree.branch ? <button aria-label={`Integrate ${worktree.branch} worktree`} title="Merge or rebase worktree" className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand disabled:opacity-40" disabled={gitBusy} type="button" onClick={() => { setIntegrationRequest(null); setIntegrationSource(worktree.branch); }}><GitMerge aria-hidden="true" size={13} /></button> : null}
                  {activeWorktree ? <span className="text-[10px] text-muted">Current</span> : null}
                  {!activeWorktree ? <button aria-label={`Remove ${worktree.branch || "detached"} worktree`} className="grid h-6 w-6 place-items-center rounded text-muted opacity-0 hover:bg-red-50 hover:text-red-700 focus:opacity-100 group-hover:opacity-100" type="button" disabled={gitBusy} onClick={() => { setWorktreeRemovalError(""); setWorktreeRemoval(worktree); }}><Trash2 size={11} /></button> : null}
                </div>
              );
            })}

            <section aria-label="Branches" className="mt-4 border-t border-line pt-3">
              <h3 className="mb-1 text-[10px] font-semibold text-ink">Branches</h3>
              {!sourceControl.branches?.length ? <p className="py-2 text-[11px] text-muted">No local branches yet.</p> : null}
              {(sourceControl.branches || []).map(branch => {
                const current = branch === sourceControl.branch;
                const checkedOut = current || worktrees.items.some(item => item.branch === branch);
                return <div key={branch} aria-label={`Branch ${branch}`} tabIndex={0} className="relative flex min-h-8 items-center gap-1.5 rounded px-1.5 text-[11px] hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand"
                  onContextMenu={event => openBranchMenu(event, branch)}
                  onKeyDown={event => { if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) openBranchMenu(event, branch); }}>
                  <GitBranch aria-hidden="true" className="shrink-0 text-muted" size={12} />
                  {current ? <span aria-hidden="true" className="absolute inset-y-1 left-0 w-px bg-brand" /> : null}
                  <button type="button" aria-label={`Switch to branch ${branch}`} aria-current={current ? "page" : undefined} disabled={gitBusy || worktrees.loading || current} className="min-w-0 flex-1 truncate py-1 text-left text-ink focus-visible:outline disabled:cursor-default" title={branch} onClick={() => {
                    const worktree = worktrees.items.find(item => item.branch === branch && !item.missing && !item.prunable);
                    if (worktree) onSelectProject?.(worktree.path, { mainProjectRoot: mainWorktreePath(worktrees.items, rootPath) });
                    else void changeSourceControl("switch", branch);
                  }}>{branch}</button>
                  {checkedOut ? <span className="shrink-0 text-[10px] text-muted">{current ? "Current" : "Worktree"}</span> : null}
                  <button type="button" aria-label={`Actions for branch ${branch}`} title="Branch actions" disabled={gitBusy} className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 hover:text-ink focus-visible:outline disabled:opacity-40" onClick={event => openBranchMenu(event, branch)}><MoreHorizontal size={13} /></button>
                  <button type="button" aria-label={`Delete branch ${branch}`} title={checkedOut ? "Cannot delete a checked-out branch" : `Delete branch ${branch}`} disabled={gitBusy || worktrees.loading || checkedOut} className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted hover:text-red-700 focus-visible:outline disabled:opacity-40" onClick={() => void deleteBranch(branch)}><Trash2 size={12} /></button>
                </div>;
              })}
            </section>

                <GitIntegrationControls key={JSON.stringify([rootPath, integrationSource, integrationRequest])} rootPath={rootPath} sourceControl={sourceControl} worktrees={worktrees.items} source={integrationSource} request={integrationRequest} onSourceChange={setIntegrationSource} onBusy={setIntegrationBusy} disabled={gitBusy} onSelectProject={onSelectProject} onChanged={integrationChanged} />
              </> : null}
              {sourceTab === "history" ? <>
            <div className="flex h-7 items-center gap-1.5 pb-1 text-[10px] font-semibold text-ink">
              <GitCommitHorizontal size={12} />
              <span className="flex-1">Commit history</span>
              <button
                aria-label="Refresh commit history"
                className="grid h-6 w-6 place-items-center rounded text-muted outline-none hover:bg-slate-100 hover:text-ink focus-visible:bg-slate-100 focus-visible:text-ink disabled:cursor-default disabled:opacity-70"
                disabled={gitHistory.loading || worktrees.loading}
                title="Refresh commit history"
                type="button"
                onClick={() => void loadGitPanels()}
              >
                <RefreshCw className={gitHistory.loading || worktrees.loading ? "animate-spin" : ""} size={12} />
              </button>
            </div>
            <div className="mb-2 space-y-1.5">
              <HistoryBranchFilter refs={gitHistory.refs || []} selection={historyRefs} onChange={selection => { if (selection === historyRefs) return; setHistoryRefs(selection); setHistoryLimit(100); setGitHistory(current => ({ ...current, commits: [], loading: true })); }} />
              <input aria-label="Filter commit history" type="search" placeholder="Filter message, author, branch, or ID" className="h-7 w-full rounded border border-line bg-white px-2 text-[11px] text-ink placeholder:text-muted focus-visible:outline-brand" value={historyQuery} onChange={event => setHistoryQuery(event.target.value)} />
            </div>
            {gitHistory.loading && !gitHistory.commits.length ? <p className="py-2 text-[11px] text-muted">Loading history...</p> : null}
            {!gitHistory.loading && !gitHistory.active ? <p className="py-2 text-[11px] text-muted">This project is not a Git repository.</p> : null}
            {!gitHistory.loading && gitHistory.active && !gitHistory.commits.length ? <p className="py-6 text-center text-xs text-muted">{historyRefs?.length === 0 ? "Select branches to show their history." : historyRefs ? "No commits in this selection." : "No commits yet."}</p> : null}
            {!gitHistory.loading && gitHistory.commits.length > 0 && !historyGraph.rows.length ? <p className="py-4 text-xs text-muted">No loaded commits match. Clear the filter or load more history.</p> : null}
            <p className="mb-2 text-[10px] leading-4 text-muted">Newer commits are above their ancestors. Lines meet at shared commits.</p>
            <div className="scm-history-scroll" style={{ "--scm-graph-width": `${historyGraph.width}px` }}>
            <div ref={historyListRef} role="list" aria-label="Branch history graph" className="scm-history-graph" onScroll={() => mirrorHistoryScroll(historyListRef, historyScrollbarRef)}>
            {historyGraph.rows.map((row) => {
              const { commit } = row;
              const isExpanded = expandedCommits.has(commit.hash);
              const isCopied = copiedCommitHash === commit.hash;
              return (
                <div key={commit.hash} role="listitem" className="scm-history-row">
                <div className="scm-history-text">
                <div onContextMenu={event => openHistoryMenu(event, commit)} onKeyDown={event => { if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) openHistoryMenu(event, commit); }} className={`min-w-0 rounded-md transition-colors ${isExpanded ? "bg-slate-50" : "hover:bg-slate-50"}`}>
                  <div className="flex items-center rounded-md">
                    <button
                      aria-expanded={isExpanded}
                      className="flex min-w-0 flex-1 items-center rounded-md px-1.5 py-1.5 text-left outline-none focus-visible:bg-slate-100"
                      type="button"
                      onClick={() => setExpandedCommits((current) => {
                        const next = new Set(current);
                        if (isExpanded) next.delete(commit.hash);
                        else next.add(commit.hash);
                        return next;
                      })}
                    >
                      <span className="min-w-0 flex-1">
                        <span className={`block text-[11px] font-medium leading-4 text-ink ${isExpanded ? "whitespace-pre-wrap break-words" : "truncate"}`}>{isExpanded ? (commit.message || commit.subject) : commit.subject}</span>
                        <GitHistoryRefs commit={commit} />
                        <span className="scm-history-meta text-[9px] leading-3 text-muted">
                          <span className="scm-history-author" title={commit.author}>{commit.author}</span>
                          <span className="scm-history-when">
                            <span aria-hidden="true">·</span>
                            <span className="scm-history-time">{timeAgo(commit.authoredAt)}</span>
                          </span>
                          <span className="scm-history-id">
                            <span className="scm-history-hash font-mono">{commit.shortHash}</span>
                            <GitHistoryStats commit={commit} />
                          </span>
                        </span>
                      </span>
                    </button>
                    <button
                      aria-label={isCopied ? `Copied commit ID ${commit.shortHash}` : `Copy commit ID ${commit.shortHash}`}
                      className="mt-0.5 grid h-6 w-5 shrink-0 self-start place-items-center rounded text-muted outline-none hover:bg-slate-200 hover:text-ink focus-visible:bg-slate-200 focus-visible:text-ink dark:hover:bg-white/10 dark:focus-visible:bg-white/10"
                      title={isCopied ? "Commit ID copied" : "Copy commit ID"}
                      type="button"
                      onClick={() => void copyCommitHash(commit)}
                    >
                      {isCopied ? <Check size={11} /> : <Copy size={11} />}
                    </button>
                  </div>
                </div>
                </div>
                  <GitHistoryLane row={row} width={historyGraph.width} />
                </div>
              );
            })}
            </div>
            <div ref={historyScrollbarRef} aria-hidden="true" tabIndex={-1} className="scm-history-scrollbar" onScroll={() => mirrorHistoryScroll(historyScrollbarRef, historyListRef)}><div /></div>
            </div>
            {gitHistory.hasMore && historyLimit < 5000 ? <button type="button" className="my-2 w-full rounded border border-line py-1 text-[11px] text-muted hover:text-ink disabled:opacity-50" disabled={gitHistory.loading} onClick={() => setHistoryLimit(limit => limit + 100)}>Load more history</button> : null}
            {gitHistory.hasMore && historyLimit >= 5000 ? <p className="py-2 text-[11px] text-muted">Showing 5,000 commits. Select a branch to narrow history.</p> : null}

              </> : null}
            </>}
          </div>
          {sourceControl.active && sourceTab === "changes" && sourceControl.entries.length > 0 ? (
                <form className="scm-composer shrink-0 space-y-2 border-t border-line bg-slate-50 p-3" onSubmit={(event) => { event.preventDefault(); void changeSourceControl("commit", commitMessage); }}>
                  <label className="block text-xs font-semibold" htmlFor="scm-commit-message">Commit message</label>
                  <div className="relative">
                  <button type="button" aria-label="Generate commit message with Rem" aria-busy={generatingMessage} title={generatingMessage ? "Rem is drafting your commit message" : "Generate a commit message with Rem"} className="rem-action-button absolute right-1 top-1 grid h-8 w-8 place-items-center rounded border border-line bg-canvas text-ink hover:text-brand focus-visible:outline-brand disabled:opacity-40" disabled={gitBusy || loadingCommitJob || generatingMessage || !stagedCount || sourceControl.entries.some(entry => entry.status === "!")} onClick={() => void generateCommitMessage()}><RemActionIcon /><span className="sr-only">{generatingMessage ? "Generating…" : "Generate with Rem"}</span></button>
                  <textarea id="scm-commit-message" aria-label="Commit message" placeholder="Describe your changes…" rows={2} className="scm-commit-message w-full pr-11 resize-none rounded border border-line bg-white px-2 py-1 text-xs focus-visible:outline" value={commitMessage} onChange={(event) => editCommitMessage(event.target.value)} onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && commitMessage.trim() && !gitOperationBusy && stagedCount) { event.preventDefault(); void changeSourceControl("commit", commitMessage); } }} />
                  </div>
                  <button type="submit" className="h-8 w-full rounded bg-brand text-[11px] font-semibold text-white disabled:opacity-40" disabled={loadingCommitJob || generatingMessage || gitStatusPending || gitOperationBusy || !commitMessage.trim() || !stagedCount || sourceControl.entries.some(entry => entry.status === "!")}>{gitOperationBusy ? "Working…" : `Commit ${stagedCount} staged file${stagedCount === 1 ? "" : "s"}`}</button>
                  <p className="text-[11px] text-muted">Only staged files will be committed. Unresolved conflicts block commits.</p>
                </form>

          ) : null}
      </section>

      {worktreeMenu && sidebarView === "source-control" && sourceTab === "branches" ? <WorktreeContextMenu
        {...worktreeMenu}
        operations={worktreeMenu.branchMenu ? [...integrationOperations, ["worktree-branch", "Create worktree from branch", worktrees.loading || worktreeMenu.source === sourceControl.branch || worktrees.items.some(item => item.branch === worktreeMenu.source)], ["branch-delete", "Delete branch", worktrees.loading || worktreeMenu.source === sourceControl.branch || worktrees.items.some(item => item.branch === worktreeMenu.source)]] : integrationOperations}
        disabled={gitBusy}
        onClose={() => setWorktreeMenu(null)}
        onSelect={(kind, target) => {
          if (kind === "branch-delete" || kind === "worktree-branch") {
            const branch = worktreeMenu.source;
            setWorktreeMenu(null);
            if (kind === "branch-delete") void deleteBranch(branch);
            else void createBranchWorktree(branch);
            return;
          }
          setIntegrationSource(worktreeMenu.source);
          setIntegrationRequest({ kind, target });
          setWorktreeMenu(null);
        }}
      /> : null}
      {branchRequest ? <Dialog title="Checkout new branch" onClose={() => { if (!gitBusy) setBranchRequest(null); }} panelClassName="w-full max-w-sm rounded-lg border border-line bg-white p-4 shadow-panel">
        <form className="space-y-3" onSubmit={async event => { event.preventDefault(); if (!branchDraft.trim() || gitBusy) return; if (await changeSourceControl("branch-commit", { hash: branchRequest.hash, branch: branchDraft.trim() })) setBranchRequest(null); }}>
          <h3 className="text-sm font-semibold">Checkout new branch</h3>
          <p className="text-xs text-muted">Start at {branchRequest.hash.slice(0, 8)}</p>
          <label className="block text-xs">Branch name<input autoFocus aria-label="New branch name" className="mt-1 h-9 w-full rounded border border-line bg-canvas px-2 text-sm focus-visible:outline" value={branchDraft} onChange={event => setBranchDraft(event.target.value)} /></label>
          {gitError ? <p role="alert" className="text-xs text-red-700">{gitError}</p> : null}
          <div className="flex justify-end gap-2"><button type="button" disabled={gitBusy} onClick={() => setBranchRequest(null)} className="rounded border border-line px-3 py-2 text-xs">Cancel</button><button type="submit" disabled={gitBusy || !branchDraft.trim()} className="rounded bg-brand px-3 py-2 text-xs text-white disabled:opacity-40">Checkout branch</button></div>
        </form>
      </Dialog> : null}
      {historyMenu && sidebarView === "source-control" && sourceTab === "history" ? <WorktreeContextMenu {...historyMenu} operations={historyOperations} disabled={gitBusy} onClose={() => setHistoryMenu(null)} onSelect={kind => void historyAction(kind)} /> : null}

      {worktreeRemoval ? (
        <Dialog
          title={worktreeRemoval.requiresForce ? "Discard changes and remove worktree?" : "Remove worktree?"}
          onClose={() => { if (!gitOperationRef.current) setWorktreeRemoval(null); }}
          panelClassName="w-full max-w-md rounded-xl border border-line bg-white p-5 text-ink"
        >
          <h2 className="text-sm font-semibold">{worktreeRemoval.requiresForce ? "Discard changes and remove worktree?" : "Remove worktree?"}</h2>
          <p className="mt-3 break-all text-xs text-muted">{worktreeRemoval.path}</p>
          <p className="mt-3 text-xs">{worktreeRemoval.requiresForce ? "This worktree has uncommitted changes. Removing it will permanently discard all uncommitted changes and untracked files, and delete its folder. The branch will be kept." : "This removes the worktree and its folder. The branch will be kept."}</p>
          {worktreeRemovalError ? <p role="alert" className="mt-3 break-words text-xs text-red-600">{worktreeRemovalError}</p> : null}
          <div className="mt-5 flex justify-end gap-2">
            <button type="button" className="rounded border border-line px-3 py-2 text-xs" disabled={gitOperationBusy} onClick={() => setWorktreeRemoval(null)}>Cancel</button>
            <button aria-label={worktreeRemoval.requiresForce ? "Discard changes and remove" : "Confirm worktree removal"} type="button" className="rounded bg-red-600 px-3 py-2 text-xs text-white disabled:opacity-50" disabled={gitBusy} onClick={() => removeWorktree(worktreeRemoval)}>{gitOperationBusy ? "Removing…" : worktreeRemoval.requiresForce ? "Discard changes and remove" : "Remove worktree"}</button>
          </div>
        </Dialog>
      ) : null}

      {branchCleanup ? (
        <Dialog
          title="Clean up branches?"
          onClose={() => { if (!gitOperationRef.current) setBranchCleanup(null); }}
          panelClassName="w-full max-w-lg rounded-xl border border-line bg-white p-5 text-ink"
        >
          <h2 className="text-sm font-semibold">Clean up branches?</h2>
          {branchCleanup.loading ? <p role="status" className="mt-3 text-xs text-muted">Checking branches…</p> : null}
          {branchCleanup.plan ? <>
            <p className="mt-3 text-xs">{branchCleanup.plan.remove.length ? "These branches have no commits of their own. A branch you always keep, or a newer branch, already contains everything on them. Their worktree folders will be deleted too." : "There are no outdated branches to clean up."}</p>
            {branchCleanup.plan.remove.length ? <ul aria-label="Branches to delete" className="mt-3 max-h-60 space-y-1.5 overflow-y-auto text-xs">
              {branchCleanup.plan.remove.map(item => <li key={item.branch} className="rounded bg-slate-50 px-2 py-1.5">
                <span className="block break-all font-medium">{item.branch}</span>
                <span className="block break-all text-[11px] text-muted">Already in {item.newer}{item.worktree ? ` · ${item.worktree}` : ""}</span>
              </li>)}
            </ul> : null}
            {branchCleanup.plan.kept.length ? <>
              <h3 className="mt-4 text-xs font-semibold">Kept</h3>
              <ul aria-label="Outdated branches kept" className="mt-1 space-y-1 text-[11px] text-muted">
                {branchCleanup.plan.kept.map(item => <li key={item.branch} className="break-all">{item.branch} · {item.reason}</li>)}
              </ul>
            </> : null}
          </> : null}
          {branchCleanup.plan ? <section aria-label="Always keep" className="mt-4 space-y-2 border-t border-line pt-3">
            <h3 className="text-xs font-semibold">Always keep</h3>
            <p className="text-[11px] text-muted">Branch names or patterns such as release/*, separated by commas. Stored in Git config, not committed.</p>
            <CleanupKeepField label="This project" patterns={branchCleanup.plan.keepPatterns?.project || []} disabled={gitBusy || branchCleanup.saving} onSave={patterns => saveCleanupKeep("project", patterns)} />
            <CleanupKeepField label="All projects" patterns={branchCleanup.plan.keepPatterns?.user || []} disabled={gitBusy || branchCleanup.saving} onSave={patterns => saveCleanupKeep("user", patterns)} />
          </section> : null}
          {branchCleanup.error ? <p role="alert" className="mt-3 whitespace-pre-wrap break-words text-xs text-red-600">{branchCleanup.error}</p> : null}
          <div className="mt-5 flex justify-end gap-2">
            <button type="button" className="rounded border border-line px-3 py-2 text-xs" disabled={gitOperationBusy} onClick={() => setBranchCleanup(null)}>{branchCleanup.plan?.remove.length ? "Cancel" : "Close"}</button>
            {branchCleanup.plan?.remove.length ? <button type="button" className="rounded bg-red-600 px-3 py-2 text-xs text-white disabled:opacity-50" disabled={gitBusy || branchCleanup.saving} onClick={() => void runBranchCleanup()}>{gitOperationBusy ? "Deleting…" : `Delete ${branchCleanup.plan.remove.length} branch${branchCleanup.plan.remove.length === 1 ? "" : "es"}`}</button> : null}
          </div>
        </Dialog>
      ) : null}
    </>
  );
}

function CleanupKeepField({ label, patterns, disabled, onSave }) {
  const saved = patterns.join(", ");
  const [draft, setDraft] = useState(saved);
  useEffect(() => { setDraft(saved); }, [saved]);
  const parsed = draft.split(/[\s,]+/).filter(Boolean);
  const changed = parsed.join(", ") !== saved;
  const save = () => { if (changed && !disabled) onSave(parsed); };
  return <div className="flex items-center gap-2 text-[11px]">
    <span className="w-20 shrink-0 text-muted">{label}</span>
    <input aria-label={`Always keep in ${label.toLowerCase()}`} className="h-7 min-w-0 flex-1 rounded border border-line bg-white px-2 text-[11px] outline-none focus:border-indigo-500" placeholder="dev, main" value={draft} disabled={disabled} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); save(); } }} />
    <button type="button" aria-label={`Save branches always kept in ${label.toLowerCase()}`} className="h-7 shrink-0 rounded border border-line px-2 text-[11px] disabled:opacity-40" disabled={disabled || !changed} onClick={save}>Save</button>
  </div>;
}

function sourceControlErrorSummary(message) {
  if (/^(Commit-message generation failed|Could not check commit-message generation):/i.test(message)) return message;
  if (/unsaved editor/i.test(message)) return "Save your editor changes and try again.";
  if (/conflict|unmerged/i.test(message)) return "Resolve the Git conflicts before continuing.";
  if (/authentication|permission denied|could not read Username/i.test(message)) return "Git authentication failed. Check your remote access and try again.";
  if (/restart the desktop/i.test(message)) return "Restart the desktop app to enable Git actions.";
  if (/network|could not resolve|unable to access/i.test(message)) return "Could not reach the remote. Check your connection and try again.";
  return "Git could not complete this action. Review the details and try again.";
}
