import { Check, ChevronDown, Copy, FolderOpen, History, Loader2, MoreVertical, Plus, PencilLine, RefreshCw, X } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { LegacyTomlNotice } from "./LegacyTomlWorkflows.jsx";
import { menuTrigger, PopupMenu } from "./PopupMenu.jsx";
import RaticodeMark from "./RaticodeMark.jsx";
import { openWorkflowRuns } from "../lib/allWorkflowRuns.js";
import { workflowsForFile } from "../lib/fileInputs.js";
import { loadProjectLabels, mergeRecentProjects, PROJECT_LABELS_STORAGE_KEY, projectNameFromPath } from "../lib/projects.js";
import { exactWorkflowRunStopPath, workflowListStatus, workflowRunSummary } from "../lib/workflowRuns.js";
import { pathKey, samePath, pathValue, withPathValue } from "../lib/workspacePaths.js";

const WorkflowListItem = lazy(() => import("./WorkflowListItem.jsx"));
const CodeFileExplorer = lazy(() => import("./CodeFileExplorer.jsx"));

export function WorkflowSidebar({
  mainProjectRoot,
  openingProjectRoot = "",
  paneVisible = true,
  activity,
  onActivityChange,
  activeCodePath,
  activeWorkflow,
  activeWorkflowId,
  loading,
  runRecords = [],
  runStatesById = {},
  settings,
  onSettingsChange,
  workflows,
  view = "graph",
  newFileRequest,
  recentProjectRoots,
  onCodeFileOpen,
  onOpenSwarm,
  selectedSwarmId,
  onOpenOrganization,
  selectedOrganizationId,
  onOpenSecondBrain,
  secondBrainOpen,
  onOpenKanban,
  onOpenPosts,
  kanbanOpen,
  onOpenRemHome,
  remHomeOpen,
  remProjectOptions,
  onShowEditor,
  onOpenBrowser,
  onCloseCodeFile,
  onCodeFilesystemChange,
  onCreate,
  legacyTomlFiles = [],
  onConvertLegacyToml,
  onOpenProject,
  onAddWorkflowShortcut,
  onDeleteWorkflow,
  onDuplicateWorkflow,
  onEditWorkflowFile,
  onRefresh,
  onRenameWorkflow,
  onSelectProject,
  onRemoveRecentProject,
  onResizeKeyDown,
  onResizeStart,
  onRunWorkflow,
  onStopWorkflow,
  onSelect,
  width,
}) {
  const [collapsedGroups, setCollapsedGroups] = useState({});
  const [projectMenu, setProjectMenu] = useState(null);
  const projectMenuTriggerRef = useRef(null);
  const [copiedProjectRoot, setCopiedProjectRoot] = useState("");
  const [projectLabels, setProjectLabels] = useState(loadProjectLabels);
  const [renamingProjectRoot, setRenamingProjectRoot] = useState("");
  const [projectLabelDraft, setProjectLabelDraft] = useState("");
  const workflowGroups = useMemo(
    () => groupWorkflowsByProject((workflows ?? []).filter((item) => !activeWorkflow?.projectRoot || samePath(item.projectRoot, activeWorkflow.projectRoot)), projectLabels),
    [activeWorkflow?.projectRoot, projectLabels, workflows],
  );
  const recentProjects = useMemo(
    () => mergeRecentProjects([], recentProjectRoots ?? []).map((root) => ({
      name: pathValue(projectLabels, root)?.trim() || projectNameFromPath(root),
      root,
    })),
    [projectLabels, recentProjectRoots],
  );
  const [localActivity, setLocalActivity] = useState(() => settings?.general?.initialActivity || (view === "code" ? "files" : "workflows"));
  const selectedActivity = activity || localActivity;
  const selectActivity = (next) => { setLocalActivity(next); onActivityChange?.(next); };

  useEffect(() => {
    try {
      window.localStorage?.setItem(PROJECT_LABELS_STORAGE_KEY, JSON.stringify(projectLabels));
    } catch {
      // Labels still work for this session when browser storage is unavailable.
    }
  }, [projectLabels]);

  async function copyProjectRoot(root) {
    try {
      await navigator.clipboard.writeText(root);
      setCopiedProjectRoot(root);
      window.setTimeout(() => setCopiedProjectRoot(""), 1400);
    } catch {
      // The menu retains the path as a title so it can still be copied manually.
      return;
    }
    setProjectMenu(null);
  }

  async function openProjectRoot(root) {
    await window.goferDesktop?.workspace?.openPath?.(root);
    setProjectMenu(null);
  }

  useEffect(() => {
    if (!projectMenu) return undefined;
    const close = () => setProjectMenu(null);
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setProjectMenu(null);
    };
    window.addEventListener("pointerdown", close);
    window.addEventListener("blur", close);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("blur", close);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [projectMenu]);

  function showProjectMenu(event, group) {
    event.preventDefault();
    event.stopPropagation();
    projectMenuTriggerRef.current = menuTrigger(event);
    setProjectMenu({
      ...projectMenuPosition(event.clientX, event.clientY),
      name: group.name,
      root: group.root,
    });
  }

  function startProjectRename(group) {
    setProjectLabelDraft(group.name);
    setRenamingProjectRoot(group.root);
    setProjectMenu(null);
  }

  function commitProjectRename(root, nextLabel = projectLabelDraft) {
    const label = nextLabel.trim();
    const folderName = projectNameFromPath(root);
    setProjectLabels((current) => {
      return withPathValue(current, root, !label || label === folderName ? undefined : label);
    });
    setRenamingProjectRoot("");
    setProjectLabelDraft("");
  }

  function cancelProjectRename() {
    setRenamingProjectRoot("");
    setProjectLabelDraft("");
  }

  return (
    <aside
      className="studio-sidebar relative flex h-full min-h-0 shrink-0 flex-col overflow-hidden border-r border-line bg-white"
      style={{ width: paneVisible ? width : 41 }}
    >
      <div
        hidden={!paneVisible}
        aria-label="Resize workflows pane"
        aria-orientation="vertical"
        aria-valuemax={420}
        aria-valuemin={240}
        aria-valuenow={width}
        aria-valuetext={`${width} pixels wide`}
        className="absolute right-[-3px] top-0 z-20 h-full w-1.5 cursor-col-resize transition hover:bg-brand/40"
        role="separator"
        tabIndex={paneVisible ? 0 : -1}
        title="Resize workflows pane"
        onKeyDown={onResizeKeyDown}
        onPointerDown={onResizeStart}
      />
      <div className={`px-3.5 pb-2 pt-3.5 ${paneVisible ? "" : "hidden"}`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <RaticodeMark className="h-10 w-10" />
            <div>
              <h1 className="text-[13px] font-semibold leading-tight">Raticode</h1>
              <p className="text-[11px] leading-tight text-muted">
                Project workspace
              </p>
            </div>
          </div>
          <button
            className="studio-icon-button grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
            title="Refresh workflows"
            type="button"
            onClick={onRefresh}
          >
            {loading ? <Loader2 size={18} className="animate-spin" /> : <RefreshCw size={18} />}
          </button>
        </div>


      </div>

      {paneVisible ? <div className="relative z-30 px-3.5 pb-2">
        <RecentProjectSelector
          projectRoot={mainProjectRoot ?? activeWorkflow?.projectRoot ?? ""}
          openingProjectRoot={openingProjectRoot}
          recentProjectRoots={recentProjectRoots}
          onSelectProject={onSelectProject}
          onRemoveRecentProject={onRemoveRecentProject}
          onOpenProject={onOpenProject}
        />
      </div> : null}

      {paneVisible && openingProjectRoot ? (
        <div role="status" className="flex items-center gap-2 px-3.5 py-2 text-xs text-muted">
          <Loader2 aria-hidden="true" className="shrink-0 animate-spin motion-reduce:animate-none" size={14} />
          <span className="truncate" title={openingProjectRoot}>Opening {projectNameFromPath(openingProjectRoot)}...</span>
        </div>
      ) : null}
      <div aria-busy={Boolean(openingProjectRoot)} className="workflow-scrollbar relative min-h-0 flex-1 overflow-hidden pb-3 pt-1">
        <Suspense fallback={<p role="status" className="p-4 text-sm text-muted">Loading explorer...</p>}>
        <CodeFileExplorer
            activeFilePath={activeCodePath}
            fileWorkflows={workflowsForFile(workflows, activeWorkflow?.projectRoot)}
            onRunWorkflowWithFile={(target, input, path) => onRunWorkflow(target, { [input]: path })}
            newFileRequest={newFileRequest}
            recentProjects={recentProjects}
            settings={settings}
            onSettingsChange={onSettingsChange}
            workflow={activeWorkflow}
            sidebarView={selectedActivity}
            onSidebarViewChange={selectActivity}
            paneVisible={paneVisible}
            hideProjectSelector
            onFilesystemChange={onCodeFilesystemChange}
            onCloseActiveFile={onCloseCodeFile}
            onOpenFile={onCodeFileOpen}
            onOpenOrganization={onOpenOrganization}
            selectedOrganizationId={selectedOrganizationId}
            onOpenSecondBrain={onOpenSecondBrain}
            onOpenKanban={onOpenKanban}
            onOpenPosts={onOpenPosts}
            kanbanOpen={kanbanOpen}
            secondBrainOpen={secondBrainOpen}
            onOpenRemHome={onOpenRemHome}
            remHomeOpen={remHomeOpen}
            remProjectOptions={remProjectOptions}
            onShowEditor={onShowEditor}
            onOpenBrowser={onOpenBrowser}
            onOpenSwarm={onOpenSwarm}
            selectedSwarmId={selectedSwarmId}
            onSelectProject={onSelectProject}
            onRemoveRecentProject={onRemoveRecentProject}
            workflowsHeader={
              <div className="flex flex-col gap-1">
                <button
                  className="flex h-9 w-full items-center justify-center gap-2 rounded-lg bg-brand px-3 text-xs font-semibold text-white transition hover:bg-indigo-700"
                  title="New Workflow"
                  type="button"
                  onClick={onCreate}
                >
                  <Plus size={15} />
                  New Workflow
                </button>
                <button
                  className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-xs font-medium text-ink transition hover:bg-slate-100 dark:hover:bg-zinc-800"
                  title="Every workflow's runs, newest first"
                  type="button"
                  onClick={() => openWorkflowRuns()}
                >
                  <History aria-hidden="true" size={14} />
                  All workflow runs
                </button>
                <LegacyTomlNotice files={legacyTomlFiles} onConvert={onConvertLegacyToml} />
              </div>
            }
            workflowsContent={workflowGroups.length ? (
          workflowGroups.map(({ id, name, items, root }) => {
            const collapsed = Boolean(collapsedGroups[id]);
            return (
              <section
                key={id}
                className="mb-1 rounded-lg"
                aria-label={`${name} workflows`}
                onContextMenu={(event) => showProjectMenu(event, { name, root })}
              >
                <div className="group/folder flex h-8 items-center rounded-lg px-1.5 transition hover:bg-slate-100">
                  <button
                    aria-expanded={!collapsed}
                    className="flex shrink-0 items-center gap-1.5 text-left text-xs font-semibold text-ink"
                    type="button"
                    onClick={() =>
                      setCollapsedGroups((current) => ({ ...current, [id]: !current[id] }))
                    }
                  >
                    <ChevronDown
                      aria-hidden="true"
                      className={`shrink-0 text-muted transition ${collapsed ? "-rotate-90" : ""}`}
                      size={13}
                    />
                    <FolderOpen aria-hidden="true" className="shrink-0 text-muted" size={13} />
                  </button>
                  {renamingProjectRoot === root ? (
                    <input
                      autoFocus
                      aria-label={`Project label for ${projectNameFromPath(root)}`}
                      className="ml-1 h-6 min-w-0 flex-1 rounded-md border border-indigo-300 bg-white px-1.5 text-xs font-semibold text-ink outline-none ring-2 ring-indigo-100"
                      maxLength={120}
                      value={projectLabelDraft}
                      onBlur={(event) => commitProjectRename(root, event.currentTarget.value)}
                      onChange={(event) => setProjectLabelDraft(event.target.value)}
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") event.currentTarget.blur();
                        if (event.key === "Escape") {
                          event.preventDefault();
                          cancelProjectRename();
                        }
                      }}
                    />
                  ) : (
                    <button
                      className="min-w-0 flex-1 truncate pl-1 text-left text-xs font-semibold text-ink"
                      title={`${name}\n${root}`}
                      type="button"
                      onClick={() =>
                        setCollapsedGroups((current) => ({ ...current, [id]: !current[id] }))
                      }
                    >
                      {name}
                    </button>
                  )}
                  <span className="text-[10px] font-medium text-muted">{items.length}</span>
                  <button
                    aria-haspopup="menu"
                    className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-muted opacity-0 transition hover:bg-white hover:text-ink group-hover/folder:opacity-100 focus:opacity-100"
                    title={`${name} project actions`}
                    type="button"
                    onClick={(event) => {
                      const bounds = event.currentTarget.getBoundingClientRect();
                      event.stopPropagation();
                      projectMenuTriggerRef.current = menuTrigger(event);
                      setProjectMenu({
                        ...projectMenuPosition(bounds.right, bounds.bottom),
                        name,
                        root,
                      });
                    }}
                  >
                    <MoreVertical size={13} />
                  </button>
                </div>
                {!collapsed ? (
                  <div className="ml-3 space-y-0.5 border-l border-line py-0.5 pl-1.5">
                    {items.map((workflow) => {
                      const listStatus = workflowListStatus(workflow, runRecords, { submitting: Boolean(runStatesById[workflow.id]?.running) });
                      const { active: activeRuns } = workflowRunSummary(runRecords, workflow.id);
                      // Stop acts on exactly one run; several runs are stopped from the Runs panel.
                      const stoppable = activeRuns.length === 1 && Boolean(exactWorkflowRunStopPath(activeRuns[0]));
                      return (
                        <WorkflowListItem
                          key={workflow.id}
                          active={workflow.id === activeWorkflowId}
                          listStatus={listStatus}
                          workflow={workflow}
                          onDelete={() => onDeleteWorkflow(workflow)}
                          onDuplicate={() => onDuplicateWorkflow(workflow)}
                          onAddShortcut={onAddWorkflowShortcut ? () => onAddWorkflowShortcut(workflow) : null}
                          onEditFile={() => onEditWorkflowFile(workflow)}
                          onRename={(name) => onRenameWorkflow(workflow, name)}
                          onRun={["invalid", "submitting"].includes(listStatus.key) ? null : () => onRunWorkflow(workflow)}
                          onStop={stoppable && onStopWorkflow ? () => onStopWorkflow(workflow) : null}
                          onSelect={() => onSelect(workflow.id)}
                        />
                      );
                    })}
                  </div>
                ) : null}
              </section>
            );
          })
        ) : (
          <div className="rounded-[10px] border border-dashed border-line bg-slate-50 p-4 text-xs leading-5 text-muted" role={loading ? "status" : undefined}>
            {loading ? (
              <span className="flex items-center gap-2">
                <Loader2 aria-hidden="true" className="shrink-0 animate-spin motion-reduce:animate-none" size={13} />
                Loading workflows...
              </span>
            ) : (
              <>
                <p>{activeWorkflow?.projectRoot ? "No workflows in this project yet." : "No workflows yet."}</p>
                <button className="mt-2 font-semibold text-indigo-700 underline-offset-2 hover:underline dark:text-indigo-300" type="button" onClick={onCreate}>
                  Create a workflow
                </button>
              </>
            )}
          </div>
        )}
          />
        </Suspense>
        {projectMenu ? (
          <PopupMenu
            aria-label={`${projectMenu.name} project actions`}
            className="fixed z-[80] w-52 rounded-lg border border-line bg-white p-1 shadow-panel"
            style={{ left: projectMenu.x, top: projectMenu.y }}
            onClose={(options) => {
              setProjectMenu(null);
              if (options?.restoreFocus) projectMenuTriggerRef.current?.focus?.();
            }}
            onPointerDown={(event) => event.stopPropagation()}
          >
            <button
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs text-ink hover:bg-slate-50"
              role="menuitem"
              type="button"
              onClick={() => startProjectRename(projectMenu)}
            >
              <PencilLine size={14} /> Rename
            </button>
            <button
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs text-ink hover:bg-slate-50"
              role="menuitem"
              type="button"
              onClick={() => copyProjectRoot(projectMenu.root)}
            >
              {copiedProjectRoot === projectMenu.root ? <Check size={14} /> : <Copy size={14} />}
              {copiedProjectRoot === projectMenu.root ? "Path copied" : "Copy path"}
            </button>
            <button
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs text-ink hover:bg-slate-50"
              role="menuitem"
              type="button"
              onClick={() => openProjectRoot(projectMenu.root)}
            >
              <FolderOpen size={14} /> Open in file explorer
            </button>
            <p className="truncate px-2.5 pb-1.5 pt-1 font-mono text-[10px] text-muted" title={projectMenu.root}>
              {projectMenu.root}
            </p>
          </PopupMenu>
        ) : null}
      </div>
    </aside>
  );
}

export function RecentProjectSelector({ projectRoot = "", openingProjectRoot = "", recentProjectRoots = [], onSelectProject, onRemoveRecentProject, onOpenProject }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const projects = mergeRecentProjects([], recentProjectRoots);
  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => { if (!rootRef.current?.contains(event.target)) setOpen(false); };
    const escape = (event) => { if (event.key === "Escape") { setOpen(false); triggerRef.current?.focus(); } };
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", escape);
    return () => { window.removeEventListener("pointerdown", close); window.removeEventListener("keydown", escape); };
  }, [open]);
  return <div ref={rootRef} className="relative w-full" aria-busy={Boolean(openingProjectRoot)}>
    <button ref={triggerRef} type="button" aria-label="Recent projects" aria-expanded={open} aria-haspopup="menu" title={projectRoot || "Open a project"} className="flex h-9 w-full min-w-0 items-center gap-2 rounded-lg border border-line px-3 text-xs text-ink hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand" onClick={() => setOpen(value => !value)} onKeyDown={event => { if (event.key === "ArrowDown") { event.preventDefault(); setOpen(true); requestAnimationFrame(() => rootRef.current?.querySelector('[role="menuitem"]')?.focus()); } }}>
      {openingProjectRoot ? <Loader2 aria-hidden="true" size={14} className="shrink-0 animate-spin motion-reduce:animate-none" /> : <FolderOpen aria-hidden="true" size={14} className="shrink-0 text-muted" />}
      <span className="min-w-0 flex-1 truncate text-left">{openingProjectRoot ? `Opening ${projectNameFromPath(openingProjectRoot)}...` : projectNameFromPath(projectRoot) || "Open a project"}</span><ChevronDown aria-hidden="true" size={12} />
    </button>
    {open ? <div role="menu" aria-label="Recent projects" className="absolute left-0 top-full z-[90] mt-1 max-h-[min(32rem,calc(100vh-160px))] w-full overflow-y-auto rounded-lg border border-line bg-white p-1 shadow-panel" onKeyDown={event => { if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return; event.preventDefault(); const items = [...event.currentTarget.querySelectorAll('[role="menuitem"]')]; const index = items.indexOf(document.activeElement); items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus(); }}>
      <p className="px-2 py-1.5 text-[10px] font-semibold text-muted">Recent projects</p>
      {projects.map(root => <div className="group flex items-center rounded-md hover:bg-slate-50" key={root}>
        <button type="button" role="menuitem" title={root} className="min-w-0 flex-1 rounded-md px-2 py-2 text-left text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand" onClick={() => { setOpen(false); onSelectProject?.(root); }}><span className="flex items-center gap-2 font-medium text-ink"><span className="truncate">{projectNameFromPath(root)}</span>{samePath(root, projectRoot) ? <Check aria-hidden="true" size={12} /> : null}</span><span className="block truncate pt-0.5 text-[10px] text-muted">{root}</span></button>
        {onRemoveRecentProject ? <button type="button" role="menuitem" aria-label={`Remove ${projectNameFromPath(root)} from recent projects`} className="m-1 grid h-7 w-7 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 focus-visible:outline" onClick={() => onRemoveRecentProject(root)}><X aria-hidden="true" size={13} /></button> : null}
      </div>)}
      {!projects.length ? <p className="px-2 py-2 text-xs text-muted">No recent projects</p> : null}
      {onOpenProject ? <button type="button" role="menuitem" className="mt-1 w-full rounded-md border-t border-line px-2 py-2 text-left text-xs text-ink hover:bg-slate-50 focus-visible:outline" onClick={() => { setOpen(false); onOpenProject(); }}>Open project...</button> : null}
    </div> : null}
  </div>;
}

export function projectMenuPosition(clientX, clientY, viewportWidth = window.innerWidth, viewportHeight = window.innerHeight) {
  const menuWidth = 208;
  const menuHeight = 154;
  const requestedX = Number.isFinite(clientX) ? clientX : 8;
  const requestedY = Number.isFinite(clientY) ? clientY : 8;
  const availableWidth = Number.isFinite(viewportWidth) ? viewportWidth : 1024;
  const availableHeight = Number.isFinite(viewportHeight) ? viewportHeight : 768;
  return {
    x: Math.max(8, Math.min(requestedX, availableWidth - menuWidth - 8)),
    y: Math.max(8, Math.min(requestedY, availableHeight - menuHeight - 8)),
  };
}

export function groupWorkflowsByProject(workflows, projectLabels = {}) {
  const groups = new Map();
  for (const workflow of workflows ?? []) {
    const root = workflow.projectRoot || workflow.sourcePath || "Unregistered";
    const id = `project:${pathKey(root)}`;
    if (!groups.has(id)) {
      groups.set(id, {
        id,
        name: pathValue(projectLabels, root)?.trim() || projectNameFromPath(root),
        defaultName: projectNameFromPath(root),
        root,
        items: [],
      });
    }
    groups.get(id).items.push(workflow);
  }
  return [...groups.values()].sort((left, right) =>
    left.name.localeCompare(right.name) || left.root.localeCompare(right.root));
}
