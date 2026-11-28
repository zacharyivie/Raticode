import { useCallback, useEffect, useRef, useState } from "react";
import { hasUnsavedCodeChanges } from "./codeEditorSessions.js";
import { commitDiffPath } from "./commitDiff.js";
import { cleanCommitMessage } from "./commitMessage.js";
import { dismissGenerationJob, jobIsRunning, latestGenerationJob } from "./generationJobs.js";
import { shareInFlight, startPolling } from "./refresh.js";
import { parentWorkspacePath } from "./workspacePaths.js";

// The project sidebar's Source control: Git status (the file tree shows it too),
// Changes, History and Branches, the commit message Rem drafts, and every Git action.
// refreshTree reloads the file tree after an action changes the working tree;
// setExplorerError shows the file tree's error for the actions that report there.
export function useSourceControl({ rootPath, sidebarView, confirm, refreshTree, setExplorerError, onFilesystemChange, onOpenFile, onSelectProject, onRemoveRecentProject }) {
  const gitPanelsLoadingRef = useRef(null);
  const gitStatusLoadingRef = useRef(false);
  const [sourceControl, setSourceControl] = useState({ active: false, entries: [] });
  const [sourceControlRoot, setSourceControlRoot] = useState(null);
  const [gitOperationBusy, setGitBusy] = useState(false);
  const [gitStaging, setGitStaging] = useState(false);
  const pendingStagingRef = useRef(null);
  const gitOperationRef = useRef(false);
  const currentRootRef = useRef(rootPath);
  currentRootRef.current = rootPath;
  const gitStatusPending = sourceControlRoot !== rootPath;
  const gitBusy = gitOperationBusy || gitStaging || gitStatusPending;
  const [branchRequest, setBranchRequest] = useState(null);
  const [branchDraft, setBranchDraft] = useState("");
  useEffect(() => { setHistoryMenu(null); setBranchRequest(null); setWorktreeStartPoint(""); setWorktreeFormOpen(false); setIntegrationRequest(null); setIntegrationSource(""); setWorktreeMenu(null); }, [rootPath]);
  const [historyMenu, setHistoryMenu] = useState(null);
  const [worktreeStartPoint, setWorktreeStartPoint] = useState("");
  const [generatingMessage, setGeneratingMessage] = useState(false);
  const generationRef = useRef(null);
  const [commitMessage, setCommitMessage] = useState("");
  const commitJobRef = useRef(null);
  const draftKey = JSON.stringify([sourceControl.root || rootPath, sourceControl.branch || ""]);
  const draftKeyRef = useRef(draftKey);
  draftKeyRef.current = draftKey;
  const [loadingCommitJob, setLoadingCommitJob] = useState(true);
  useEffect(() => {
    if (sourceControlRoot !== rootPath || !sourceControl.active) return;
    let active = true;
    commitJobRef.current = null; setGeneratingMessage(false); setLoadingCommitJob(true);
    let saved;
    try { saved = JSON.parse(window.localStorage.getItem(`rem-commit-draft:${draftKey}`) || "null"); } catch { /* No stored draft. */ }
    setCommitMessage(saved?.message || ""); setGitNotice(""); setGitError("");
    const stop = startPolling(async () => {
      try {
        const job = await latestGenerationJob("commit", sourceControl.root || rootPath, sourceControl.branch || "");
        if (!active) return;
        setLoadingCommitJob(false);
        setGitError(current => current.startsWith("Could not check commit-message generation:") ? "" : current);
        setGeneratingMessage(jobIsRunning(job));
        if (!job) { if (!generationRef.current) setGitNotice(""); return; }
        if (jobIsRunning(job)) setGitNotice(job.progress || "Rem is drafting your commit message.");
        if (commitJobRef.current?.id === job.id && commitJobRef.current?.status === job.status && commitJobRef.current?.updatedAt === job.updatedAt) return;
        commitJobRef.current = job;
        if (job.error) {
          setGitNotice("");
          setGitError(job.result?.message ? job.error : `Commit-message generation failed: ${job.error}`);
        } else setGitError("");
        if (job.result?.commit) {
          setGitNotice(`Auto-committed ${job.result.commit.slice(0, 8)} on ${job.branch}.`);
          let current;
          try { current = JSON.parse(window.localStorage.getItem(`rem-commit-draft:${draftKey}`) || "null"); } catch { /* No stored draft. */ }
          if (current?.jobId !== job.id) {
            const message = current?.pendingJobId === job.id && current.message !== current.generationDraft ? current.message : "";
            setCommitMessage(message);
            window.localStorage.setItem(`rem-commit-draft:${draftKey}`, JSON.stringify({ jobId: job.id, message }));
          }
        }
        else if (job.result?.message) {
          let current;
          try { current = JSON.parse(window.localStorage.getItem(`rem-commit-draft:${draftKey}`) || "null"); } catch { /* No stored draft. */ }
          if (current?.jobId !== job.id) {
            const message = current?.pendingJobId === job.id && current.message !== current.generationDraft ? current.message : job.result.message;
            setCommitMessage(message);
            window.localStorage.setItem(`rem-commit-draft:${draftKey}`, JSON.stringify({ jobId: job.id, message }));
          }
          if (!job.error) setGitNotice("Rem's commit message is ready.");
        }
      } catch (cause) { if (active) { setLoadingCommitJob(false); setGitNotice(""); setGitError(`Could not check commit-message generation: ${cause.message}`); } }
    }, { immediate: true });
    return () => { active = false; stop(); };
  }, [draftKey, rootPath, sourceControlRoot, sourceControl.active, sourceControl.root, sourceControl.branch]);
  function editCommitMessage(message) {
    setCommitMessage(message);
    let current;
    try { current = JSON.parse(window.localStorage.getItem(`rem-commit-draft:${draftKey}`) || "null"); } catch { /* No stored draft. */ }
    window.localStorage.setItem(`rem-commit-draft:${draftKey}`, JSON.stringify({ ...current, message }));
  }
  const [gitNotice, setGitNotice] = useState("");
  useEffect(() => {
    if (!gitNotice.startsWith("Auto-committed ")) return;
    const timer = window.setTimeout(() => setGitNotice(current => current === gitNotice ? "" : current), 4000);
    return () => window.clearTimeout(timer);
  }, [gitNotice]);
  const [blockedBranch, setBlockedBranch] = useState("");
  const [remote, setRemote] = useState("");
  const [gitError, setGitError] = useState("");
  useEffect(() => { setCommitMessage(""); setBlockedBranch(""); setGitError(""); setGitNotice(""); setRemote(""); }, [rootPath]);
  // Source control opens to History each time it is opened; Changes and Branches are a tab away.
  const [sourceTab, setSourceTab] = useState("history");
  const stagedCount = sourceControl.entries.filter((entry) => entry.staged && entry.status !== "!").length;
  const sourceControlShownRef = useRef(sidebarView === "source-control");
  useEffect(() => {
    const shown = sidebarView === "source-control";
    if (shown && !sourceControlShownRef.current) setSourceTab("history");
    sourceControlShownRef.current = shown;
  }, [sidebarView]);
  const [gitHistory, setGitHistory] = useState({ active: false, commits: [], loading: false });
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyRefs, setHistoryRefs] = useState(null);
  const [historyLimit, setHistoryLimit] = useState(100);
  const historySelectionRef = useRef({ refs: historyRefs, limit: historyLimit });
  historySelectionRef.current = { refs: historyRefs, limit: historyLimit };
  const [expandedCommits, setExpandedCommits] = useState(() => new Set());
  const [copiedCommitHash, setCopiedCommitHash] = useState("");
  const [worktrees, setWorktrees] = useState({ active: false, items: [], loading: false });
  const [integrationSource, setIntegrationSource] = useState("");
  const [integrationRequest, setIntegrationRequest] = useState(null);
  const [worktreeMenu, setWorktreeMenu] = useState(null);
  const [worktreeRemoval, setWorktreeRemoval] = useState(null);
  const [worktreeRemovalError, setWorktreeRemovalError] = useState("");
  const [branchCleanup, setBranchCleanup] = useState(null);
  useEffect(() => { setWorktreeRemoval(null); setWorktreeRemovalError(""); setBranchCleanup(null); }, [rootPath]);
  const [worktreeFormOpen, setWorktreeFormOpen] = useState(false);
  const [worktreeBranch, setWorktreeBranch] = useState("");
  const [worktreeFolder, setWorktreeFolder] = useState("");
  const [worktreeCreateBranch, setWorktreeCreateBranch] = useState(false);

  const loadSourceControlStatus = useCallback(async () => {
    if (!rootPath || pendingStagingRef.current?.rootPath === rootPath || gitStatusLoadingRef.current?.rootPath === rootPath) return;
    const request = { rootPath };
    gitStatusLoadingRef.current = request;
    try {
      const payload = await shareInFlight(`git-status:${rootPath}`, () => window.goferDesktop?.workspace?.gitStatus?.(rootPath));
      if (gitStatusLoadingRef.current !== request || currentRootRef.current !== rootPath) return;
      if (payload?.error) throw new Error(payload.error);
      const next = payload?.active
        ? { ...payload, active: true, entries: payload.entries ?? [] }
        : { active: false, entries: [] };
      setSourceControl((current) => sourceControlSnapshotsEqual(current, next) ? current : next);
      setSourceControlRoot(rootPath);
    } catch (error) {
      if (gitStatusLoadingRef.current !== request || currentRootRef.current !== rootPath || error?.name === "AbortError") return;
      setGitError(error instanceof Error ? error.message : "Unable to load Git status");
      setSourceControl({ active: false, entries: [] });
      setSourceControlRoot(rootPath);
    } finally {
      if (gitStatusLoadingRef.current === request) gitStatusLoadingRef.current = null;
    }
  }, [rootPath]);

  // The file tree keeps the last Git snapshot while it refreshes, and clears it
  // when no project is open.
  const clearSourceControl = useCallback(() => {
    setSourceControl({ active: false, entries: [] });
    setSourceControlRoot(null);
  }, []);

  function openHistoryMenu(event, commit) {
    event.preventDefault();
    if (gitBusy) return;
    const rect = event.currentTarget.getBoundingClientRect();
    setHistoryMenu({ source: commit.shortHash, hash: commit.hash, x: event.clientX ?? rect.left, y: event.clientY ?? rect.bottom, trigger: event.currentTarget.querySelector("button") });
  }

  async function historyAction(kind) {
    const { hash } = historyMenu;
    setHistoryMenu(null);
    if (kind === "commit-diff") {
      onOpenFile?.(commitDiffPath(rootPath, hash));
      return;
    }
    if (kind === "worktree-commit") {
      setWorktreeStartPoint(hash); setWorktreeCreateBranch(true); setWorktreeBranch(""); setWorktreeFolder(""); setWorktreeFormOpen(true); setSourceTab("branches"); return;
    }
    if (kind === "branch-commit") {
      setBranchRequest({ hash }); setBranchDraft(""); return;
    }
    const short = hash.slice(0, 8);
    const question = kind === "reset-hard"
      ? { confirmLabel: "Hard reset", danger: true, message: `Hard reset to ${short}? This moves the current branch and permanently discards tracked staged and unstaged changes. Untracked files obstructing checkout can also be removed.` }
      : kind === "reset-soft"
        ? { confirmLabel: "Soft reset", message: `Soft reset to ${short}? Move the current branch while keeping the index and working files.` }
        : { confirmLabel: "Detach HEAD", message: `Detach HEAD at ${short}? New commits will need a branch to keep them.` };
    if (!await confirm(question)) return;
    await changeSourceControl(kind, { hash });
  }

  async function generateCommitMessage() {
    if (generationRef.current || generatingMessage || loadingCommitJob || gitBusy || !stagedCount) return;
    const key = draftKey;
    generationRef.current = key;
    setGeneratingMessage(true); setGitError(""); setGitNotice("Rem is drafting your commit message.");
    try {
      const job = await new Promise((resolve, reject) => {
        const detail = { background: true, projectRoot: rootPath, branch: sourceControl.branch, resolve, reject, handled: false };
        window.dispatchEvent(new CustomEvent("gofer:rem-commit-message", { detail }));
        if (!detail.handled) reject(new Error("Open Rem to generate a commit message."));
      });
      let current;
      try { current = JSON.parse(window.localStorage.getItem(`rem-commit-draft:${key}`) || "null"); } catch { /* No stored draft. */ }
      window.localStorage.setItem(`rem-commit-draft:${key}`, JSON.stringify({ ...current, message: current?.message ?? commitMessage, pendingJobId: job.id, generationDraft: commitMessage }));
      if (draftKeyRef.current === key && !(commitJobRef.current?.id === job.id && !jobIsRunning(commitJobRef.current))) {
        setGeneratingMessage(jobIsRunning(job));
        setGitNotice(jobIsRunning(job) ? (job.progress || "Rem is drafting your commit message.") : "");
      }
    } catch (cause) { if (draftKeyRef.current === key) { setGitNotice(""); setGitError(`Commit-message generation failed: ${cause.message}`); setGeneratingMessage(false); } }
    finally { if (generationRef.current === key) generationRef.current = null; }
  }

  async function changeSourceControl(action, value) {
    if (action === "commit" && (generationRef.current || generatingMessage || loadingCommitJob)) return;
    if (gitStatusPending || gitOperationRef.current || gitOperationBusy || (pendingStagingRef.current && action !== "commit") || (action === "commit" && (!value?.trim() || !stagedCount || sourceControl.entries.some(entry => entry.status === "!")))) return;
    const changesWorkingTree = (["switch", "stash-switch", "stash-apply", "pull", "reset-soft", "reset-hard", "detach-commit", "branch-commit"].includes(action) || /^(merge|rebase)-/.test(action)) || action.startsWith("revert");
    if ((changesWorkingTree || action === "stage") && hasUnsavedCodeChanges(rootPath)) {
      setGitError("Save your unsaved editor changes before changing the Git working tree.");
      return;
    }
    const bulk = Array.isArray(value);
    if (bulk && !value.length) return;
    if (action === "stage" || action === "unstage") {
      const paths = bulk ? value : [value];
      const selected = new Set(paths);
      gitStatusLoadingRef.current = null;
      setGitStaging(true);
      setGitError("");
      setGitNotice("");
      setSourceControl((current) => ({ ...current, entries: current.entries.map((entry) => selected.has(entry.path)
        ? { ...entry, staged: action === "stage", unstaged: action === "unstage" }
        : entry) }));
      const pending = (async () => {
        try {
          let result;
          for (const filePath of paths) {
            result = await window.goferDesktop?.workspace?.gitFileAction?.(rootPath, filePath, action);
            if (!result) throw new Error("Restart the desktop app to enable Git actions.");
            if (result.error) throw new Error(result.error);
          }
          if (currentRootRef.current === rootPath) setSourceControl(result);
          return true;
        } catch (cause) {
          if (currentRootRef.current === rootPath) {
            setGitError(cause instanceof Error ? cause.message : String(cause));
            // A bulk operation may have partially succeeded. Read the actual index.
            try {
              const actual = await shareInFlight(`git-status:${rootPath}`, () => window.goferDesktop?.workspace?.gitStatus?.(rootPath));
              if (currentRootRef.current === rootPath) setSourceControl(actual ?? { active: false, entries: [] });
            } catch {
              if (currentRootRef.current === rootPath) setSourceControl({ active: false, entries: [] });
            }
          }
          return false;
        }
      })();
      pending.rootPath = rootPath;
      pendingStagingRef.current = pending;
      await pending;
      if (pendingStagingRef.current === pending) pendingStagingRef.current = null;
      setGitStaging(false);
      return;
    }
    if (bulk && action === "revert-staged" && sourceControl.entries.some((entry) => value.includes(entry.path) && entry.unstaged)) {
      setGitError("Some staged files also have unstaged edits. Unstage those files first, or discard their unstaged edits before discarding all staged changes.");
      return;
    }
    const discardTarget = bulk ? `all ${value.length} ${action === "revert-staged" ? "staged" : "unstaged"} files` : value;
    if (action.startsWith("revert") && !await confirm({ confirmLabel: "Discard changes", danger: true, message: `Discard ${action === "revert-staged" ? "staged" : "unstaged"} changes to ${discardTarget}? New files will be moved to the trash.` })) return;
    gitOperationRef.current = true;
    setGitBusy(true);
    if (changesWorkingTree) window.dispatchEvent(new CustomEvent("gofer:git-working-tree-busy", { detail: { rootPath, busy: true } }));
    setGitError("");
    setGitNotice("");
    try {
      if (pendingStagingRef.current && !await pendingStagingRef.current) return;
      if (action === "commit") {
        value = await cleanCommitMessage(value);
        if (!value.trim()) throw new Error("The message had only Co-authored-by lines. Write a message before committing.");
      }
      const bridge = window.goferDesktop?.workspace;
      let result;
      if (bulk) {
        for (const filePath of value) {
          result = await bridge?.gitFileAction?.(rootPath, filePath, action);
          if (!result) throw new Error("Restart the desktop app to enable Git actions.");
          if (result.error) throw new Error(result.error);
        }
      } else result = action === "switch"
        ? await bridge?.gitSwitchBranch?.(rootPath, value)
        : (["commit", "push", "pull", "publish", "stash-switch", "stash-apply", "reset-soft", "reset-hard", "detach-commit", "branch-commit", "branch-delete"].includes(action) || /^(merge|rebase)-/.test(action))
          ? await bridge?.gitRepoAction?.(rootPath, action, value)
          : await bridge?.gitFileAction?.(rootPath, value, action);
      if (!result) throw new Error("Restart the desktop app to enable Git actions.");
      if (action === "branch-delete" && result.branchDeleteUnmerged) {
        if (currentRootRef.current !== rootPath) return;
        if (!await confirm({ confirmLabel: "Delete branch", danger: true, message: `Branch ${value} may have unmerged changes. Delete it anyway? Those commits may be lost.` })) return;
        if (currentRootRef.current !== rootPath) return;
        result = await bridge.gitRepoAction(rootPath, "branch-delete-force", value);
        if (!result) throw new Error("Unable to delete branch.");
      }
      if (result.error) { if (result.active) setSourceControl(result); throw new Error(result.error); }
      setBlockedBranch(result.switchBlocked ? result.requestedBranch : "");
      setGitNotice(result.notice || (action === "commit" ? "Committed staged changes." : ""));
      if (result.switchBlocked) return;
      if (action === "commit") {
        setCommitMessage(""); window.localStorage.removeItem(`rem-commit-draft:${draftKey}`);
        if (commitJobRef.current) {
          try { await dismissGenerationJob(commitJobRef.current.id); }
          catch { setGitNotice("Committed staged changes. Could not clear the saved draft."); }
        }
      }
      setSourceControl(result);
      await Promise.all([refreshTree(), loadGitPanels()]);
      onFilesystemChange?.({ type: "git", rootPath });
      return true;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setGitError(message);
      if (action === "switch" && /would be overwritten/i.test(message)) setBlockedBranch(value);
      if (changesWorkingTree || bulk) { await refreshTree(); onFilesystemChange?.({ type: "git", rootPath }); }
    } finally {
      if (changesWorkingTree) window.dispatchEvent(new CustomEvent("gofer:git-working-tree-busy", { detail: { rootPath, busy: false } }));
      setGitBusy(false);
      gitOperationRef.current = false;
    }
  }

  const loadGitPanels = useCallback(async ({ includeHistory = true } = {}) => {
    if (!rootPath || (gitPanelsLoadingRef.current?.rootPath === rootPath
      && gitPanelsLoadingRef.current?.refs === historyRefs && gitPanelsLoadingRef.current?.limit === historyLimit
      && (!includeHistory || gitPanelsLoadingRef.current.includeHistory))) return;
    const request = { rootPath, refs: historyRefs, limit: historyLimit, includeHistory };
    gitPanelsLoadingRef.current = request;
    if (includeHistory) setGitHistory((current) => ({ ...current, loading: true }));
    if (includeHistory) setWorktrees((current) => ({ ...current, loading: true }));
    try {
      // Both reads start together, but history shows as soon as it arrives: Source control opens to
      // History, and a slow worktree list must not hold the commits back.
      const worktreeResult = Promise.resolve(window.goferDesktop?.workspace?.gitWorktrees?.(rootPath)).then(value => ({ value }), error => ({ error }));
      const historyPayload = includeHistory ? await window.goferDesktop?.workspace?.gitHistory?.(rootPath, { refs: historyRefs, limit: historyLimit }) : null;
      if (gitPanelsLoadingRef.current !== request || currentRootRef.current !== rootPath) return;
      if (historyPayload?.error) throw new Error(historyPayload.error);
      if (includeHistory && historySelectionRef.current.refs === historyRefs && historySelectionRef.current.limit === historyLimit) setGitHistory({ ...historyPayload, active: Boolean(historyPayload?.active), commits: historyPayload?.commits ?? [], loading: false });
      const worktreeRead = await worktreeResult;
      if (worktreeRead.error) throw worktreeRead.error;
      const worktreePayload = worktreeRead.value;
      if (gitPanelsLoadingRef.current !== request || currentRootRef.current !== rootPath) return;
      if (worktreePayload?.error) throw new Error(worktreePayload.error);
      setWorktrees({ active: Boolean(worktreePayload?.active), items: worktreePayload?.worktrees ?? [], loading: false });
    } catch (loadError) {
      if (gitPanelsLoadingRef.current !== request || currentRootRef.current !== rootPath || loadError?.name === "AbortError") return;
      setGitHistory((current) => ({ ...current, loading: false }));
      setWorktrees((current) => ({ ...current, loading: false }));
      setGitError(loadError instanceof Error ? loadError.message : "Unable to load Git information");
    } finally {
      if (gitPanelsLoadingRef.current === request) gitPanelsLoadingRef.current = null;
    }
  }, [rootPath, historyRefs, historyLimit]);

  // Git metadata events cover worktrees and commits from agents, swarms and
  // terminals. Polling stays as a slower safety net while the watch is live.
  const [gitWatchMode, setGitWatchMode] = useState("");
  const gitEventRef = useRef(null);
  gitEventRef.current = (kinds, retry = true) => {
    // A read that started before the change may return the old state. Read once more after it.
    if (retry && (gitStatusLoadingRef.current || gitPanelsLoadingRef.current)) {
      window.setTimeout(() => { if (currentRootRef.current === rootPath) gitEventRef.current?.(kinds, false); }, 500);
    }
    void loadSourceControlStatus();
    if (sidebarView === "source-control" && (kinds.includes("worktrees") || kinds.includes("refs"))) {
      void loadGitPanels({ includeHistory: sourceTab === "history" && kinds.includes("refs") });
    }
  };
  useEffect(() => {
    const watchGit = window.goferDesktop?.workspace?.watchGit;
    setGitWatchMode("");
    if (!rootPath || !watchGit) return undefined;
    const stop = watchGit(rootPath, {
      onReady: (mode) => { if (currentRootRef.current === rootPath) setGitWatchMode(mode); },
      onChange: ({ kinds }) => { if (currentRootRef.current === rootPath) gitEventRef.current?.(kinds); },
    });
    return () => stop?.();
  }, [rootPath]);

  useEffect(() => {
    if (sidebarView === "source-control") void loadGitPanels({ includeHistory: sourceTab === "history" });
  }, [sidebarView, sourceTab, loadGitPanels]);
  useEffect(() => {
    if (sidebarView !== "source-control") return undefined;
    return startPolling(() => loadGitPanels({ includeHistory: false }), { interval: ["watch", "poll"].includes(gitWatchMode) ? 15000 : 2000 });
  }, [sidebarView, loadGitPanels, gitWatchMode]);

  useEffect(() => {
    setHistoryRefs(null);
    setHistoryLimit(100);
    setHistoryQuery("");
    setGitHistory({ active: false, commits: [], loading: false });
    setExpandedCommits(new Set());
    setCopiedCommitHash("");
    setWorktreeMenu(null);
    setIntegrationSource("");
    setIntegrationRequest(null);
  }, [rootPath]);

  useEffect(() => {
    if (!rootPath) return undefined;
    return startPolling(loadSourceControlStatus);
  }, [loadSourceControlStatus, rootPath]);

  async function copyCommitHash(commit) {
    try {
      await navigator.clipboard.writeText(commit.hash.slice(0, 8));
      setCopiedCommitHash(commit.hash);
      window.setTimeout(() => setCopiedCommitHash(""), 1400);
    } catch (copyError) {
      setExplorerError(copyError instanceof Error ? copyError.message : "Unable to copy commit ID");
    }
  }

  function openBranchMenu(event, branch) {
    event.preventDefault();
    event.stopPropagation();
    if (gitBusy) return;
    const rect = event.currentTarget.getBoundingClientRect();
    setWorktreeMenu({ source: branch, branchMenu: true, x: event.clientX || rect.left, y: event.clientY || rect.bottom, trigger: event.currentTarget });
  }

  async function deleteBranch(branch) {
    if (gitBusy || branch === sourceControl.branch || worktrees.items.some(item => item.branch === branch)) return;
    await changeSourceControl("branch-delete", branch);
  }

  async function createBranchWorktree(branch) {
    if (gitBusy || gitOperationRef.current) return;
    gitOperationRef.current = true;
    setGitBusy(true);
    setGitError("");
    try {
      const bridge = window.goferDesktop?.workspace;
      if (!bridge?.selectPath || !bridge?.addWorktree) throw new Error("Restart the desktop app to enable worktree creation.");
      const targetPath = await bridge.selectPath({ currentPath: parentWorkspacePath(rootPath), directoryOnly: true });
      if (!targetPath || currentRootRef.current !== rootPath) return;
      const payload = await bridge.addWorktree({ projectRoot: rootPath, branch, createBranch: false, targetPath });
      if (currentRootRef.current !== rootPath) return;
      if (!payload || payload.error) throw new Error(payload?.error || "Unable to create worktree.");
      setWorktrees({ active: true, items: payload.worktrees ?? [], loading: false });
      setGitNotice(`Created worktree for ${branch}.`);
      onSelectProject?.(payload.createdPath || targetPath, { mainProjectRoot: mainWorktreePath(payload.worktrees, rootPath) });
    } catch (cause) {
      if (currentRootRef.current === rootPath) setGitError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      gitOperationRef.current = false;
      setGitBusy(false);
    }
  }

  async function chooseWorktreeFolder() {
    const selected = await window.goferDesktop?.workspace?.selectPath?.({
      currentPath: parentWorkspacePath(rootPath),
      directoryOnly: true,
    });
    if (selected) setWorktreeFolder(selected);
  }

  async function createWorktree(event) {
    event.preventDefault();
    if (gitBusy) return;
    setExplorerError("");
    try {
      const payload = await window.goferDesktop?.workspace?.addWorktree?.({
        branch: worktreeBranch,
        createBranch: worktreeCreateBranch,
        startPoint: worktreeStartPoint || undefined,
        projectRoot: rootPath,
        targetPath: worktreeFolder,
      });
      setWorktrees({ active: true, items: payload?.worktrees ?? [], loading: false });
      setWorktreeFormOpen(false);
      setWorktreeStartPoint("");
      setWorktreeBranch("");
      setWorktreeFolder("");
      onSelectProject?.(payload?.createdPath || worktreeFolder, {
        mainProjectRoot: mainWorktreePath(payload?.worktrees, rootPath),
      });
    } catch (worktreeError) {
      setExplorerError(worktreeError instanceof Error ? worktreeError.message : "Unable to add worktree");
    }
  }

  async function removeWorktree(worktree) {
    if (gitBusy || gitOperationRef.current) return;
    gitOperationRef.current = true;
    setGitBusy(true);
    setWorktreeRemovalError("");
    try {
      const remove = window.goferDesktop?.workspace?.removeWorktree;
      if (!remove) throw new Error("Worktree removal is only available in the desktop app.");
      const payload = await remove({ projectRoot: rootPath, targetPath: worktree.path, force: worktree.requiresForce === true });
      if (currentRootRef.current !== rootPath) return;
      if (payload?.requiresForce) {
        setWorktreeRemoval({ ...worktree, requiresForce: true });
        return;
      }
      setWorktrees({ active: true, items: payload?.worktrees ?? [], loading: false });
      setWorktreeRemoval(null);
      window.dispatchEvent(new CustomEvent("gofer:git-files-changed", { detail: { rootPath: worktree.path } }));
      onRemoveRecentProject?.(worktree.path);
    } catch (worktreeError) {
      if (currentRootRef.current === rootPath) {
        setWorktreeRemovalError(worktreeError instanceof Error ? worktreeError.message : "Unable to remove worktree");
      }
    } finally {
      gitOperationRef.current = false;
      setGitBusy(false);
    }
  }

  async function previewBranchCleanup() {
    if (gitBusy) return;
    setBranchCleanup({ loading: true, plan: null, error: "" });
    try {
      const result = await window.goferDesktop?.workspace?.gitRepoAction?.(rootPath, "branch-cleanup-preview", {});
      if (!result) throw new Error("Restart the desktop app to enable Git actions.");
      if (result.error) throw new Error(result.error);
      if (currentRootRef.current === rootPath) setBranchCleanup({ loading: false, plan: result, error: "" });
    } catch (cause) {
      if (currentRootRef.current === rootPath) setBranchCleanup({ loading: false, plan: null, error: cause instanceof Error ? cause.message : String(cause) });
    }
  }

  async function saveCleanupKeep(scope, patterns) {
    if (gitBusy || branchCleanup?.saving) return;
    setBranchCleanup(current => ({ ...current, saving: true, error: "" }));
    try {
      const result = await window.goferDesktop?.workspace?.gitRepoAction?.(rootPath, "branch-cleanup-keep", { scope, patterns });
      if (!result) throw new Error("Restart the desktop app to enable Git actions.");
      if (result.error) throw new Error(result.error);
      if (currentRootRef.current === rootPath) setBranchCleanup({ loading: false, plan: result, error: "" });
    } catch (cause) {
      if (currentRootRef.current === rootPath) setBranchCleanup(current => ({ ...current, saving: false, error: cause instanceof Error ? cause.message : String(cause) }));
    }
  }

  async function runBranchCleanup() {
    const plan = branchCleanup?.plan;
    if (gitBusy || gitOperationRef.current || !plan?.remove.length) return;
    if (plan.remove.some(item => item.worktree && hasUnsavedCodeChanges(item.worktree))) {
      setBranchCleanup(current => ({ ...current, error: "Save your editor changes in these worktrees first." }));
      return;
    }
    gitOperationRef.current = true;
    setGitBusy(true);
    setBranchCleanup(current => ({ ...current, error: "" }));
    try {
      const result = await window.goferDesktop?.workspace?.gitRepoAction?.(rootPath, "branch-cleanup", { items: plan.remove });
      if (!result) throw new Error("Restart the desktop app to enable Git actions.");
      if (currentRootRef.current !== rootPath) return;
      if (result.active) setSourceControl(result);
      if (result.error) throw new Error(result.error);
      for (const item of result.cleanup.removed) {
        if (!item.worktree) continue;
        window.dispatchEvent(new CustomEvent("gofer:git-files-changed", { detail: { rootPath: item.worktree } }));
        onRemoveRecentProject?.(item.worktree);
      }
      setGitNotice(result.notice);
      setBranchCleanup(result.cleanup.failed.length ? { loading: false, plan: null, error: result.cleanup.failed.map(item => `${item.branch}: ${item.error}`).join("\n") } : null);
      await loadGitPanels();
      onFilesystemChange?.({ type: "git", rootPath });
    } catch (cause) {
      if (currentRootRef.current === rootPath) setBranchCleanup(current => ({ ...current, error: cause instanceof Error ? cause.message : String(cause) }));
    } finally {
      gitOperationRef.current = false;
      setGitBusy(false);
    }
  }

  // Merge and rebase run in GitIntegrationControls, which reports busy and done here.
  function setIntegrationBusy(busy) {
    gitOperationRef.current = busy;
    setGitBusy(busy);
  }

  async function integrationChanged(result) {
    if (currentRootRef.current !== rootPath) return;
    if (result.active) setSourceControl(result);
    setGitNotice(result.notice || "");
    await refreshTree(); await loadGitPanels();
    onFilesystemChange?.({ type: "git", rootPath });
    if (result.destinationRoot && result.destinationRoot !== rootPath) onFilesystemChange?.({ type: "git", rootPath: result.destinationRoot });
    if (result.conflicts?.length) setSourceTab("changes");
  }

  return {
    sourceControl, loadSourceControlStatus, clearSourceControl, loadGitPanels, changeSourceControl,
    gitBusy, gitOperationBusy, gitStatusPending, setGitBusy, gitOperationRef, setIntegrationBusy, integrationChanged,
    gitError, setGitError, gitNotice, setGitNotice, blockedBranch, setBlockedBranch, remote, setRemote,
    sourceTab, setSourceTab, stagedCount,
    commitMessage, editCommitMessage, generatingMessage, loadingCommitJob, generateCommitMessage,
    gitHistory, setGitHistory, historyQuery, setHistoryQuery, historyRefs, setHistoryRefs, historyLimit, setHistoryLimit,
    expandedCommits, setExpandedCommits, copiedCommitHash, copyCommitHash,
    historyMenu, setHistoryMenu, openHistoryMenu, historyAction, branchRequest, setBranchRequest, branchDraft, setBranchDraft,
    worktrees, worktreeMenu, setWorktreeMenu, openBranchMenu, deleteBranch, createBranchWorktree,
    integrationSource, setIntegrationSource, integrationRequest, setIntegrationRequest,
    worktreeFormOpen, setWorktreeFormOpen, worktreeStartPoint, setWorktreeStartPoint, worktreeBranch, setWorktreeBranch,
    worktreeFolder, worktreeCreateBranch, setWorktreeCreateBranch, chooseWorktreeFolder, createWorktree,
    worktreeRemoval, setWorktreeRemoval, worktreeRemovalError, setWorktreeRemovalError, removeWorktree,
    branchCleanup, setBranchCleanup, previewBranchCleanup, saveCleanupKeep, runBranchCleanup,
  };
}

export function mainWorktreePath(worktrees = [], fallback = "") {
  return String(worktrees[0]?.path || fallback).trim();
}

function sourceControlSnapshotsEqual(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}
