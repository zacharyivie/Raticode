import { ChevronRight, Download, FolderOpen, Loader2, Moon, RefreshCw, Settings as SettingsIcon, Sparkles, Sun, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { PALETTE_COMMAND_ID, QUICK_OPEN_COMMAND_ID } from "../lib/commandPalette.js";
import { rememberEditingTarget } from "../lib/focusedEditing.js";
import { loadProjectLabels, mergeRecentProjects, projectNameFromPath } from "../lib/projects.js";
import { DEFAULT_APP_SETTINGS, formatKeybinding, settingBinding } from "../lib/settings.js";
import { TEXT_ZOOM_MAX, TEXT_ZOOM_MIN } from "../lib/useAppSettings.js";
import { updateButtonLabel, updateButtonTitle } from "../lib/useAppUpdates.js";
import { VOICE_COMMAND_ID } from "../lib/voiceShortcut.js";
import { pathValue } from "../lib/workspacePaths.js";

export function TopBar({
  activeCodePath = "",
  workflow,
  view = "graph",
  onGraphToolbarTargetChange,
}) {
  const label = !workflow || (view === "code" && !activeCodePath)
    ? null
    : topBarLabelParts(workflow, view, activeCodePath);
  if (view === "graph" && workflow) {
    return <header className="studio-topbar min-w-0 shrink-0 border-b border-line bg-white px-3 py-2">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-ink" title={label?.fullPath}>{workflow.name}</h2>
      </div>
      <div
        className="mt-1.5 flex min-w-0 items-center [&>[data-toolbar]]:min-w-0 [&>[data-toolbar]]:max-w-full [&>[data-toolbar]]:flex-wrap [&_[data-toolbar-row=secondary]]:flex-wrap"
        data-graph-toolbar-target="true"
        ref={onGraphToolbarTargetChange}
      />
    </header>;
  }
  return (
    <header className="studio-topbar flex h-[54px] shrink-0 items-center justify-between gap-3 border-b border-line bg-white px-4">
      <div
        className={`flex min-w-0 flex-1 items-baseline overflow-hidden ${
          view === "graph" ? "gap-1" : ""
        }`}
        title={label?.fullPath || undefined}
      >
        {label ? (
          <>
            {label.path ? (
              <span
                className={`flex min-w-0 items-baseline text-muted ${
                  view === "graph"
                    ? "shrink-0 text-[11px] leading-4"
                    : "text-xs"
                }`}
              >
                <span className="truncate">{label.path}</span>
                <span className="shrink-0">{label.separator}</span>
              </span>
            ) : null}
            <h2
              className={`min-w-0 truncate font-semibold text-ink dark:text-white ${
                view === "graph"
                  ? "text-xl leading-6"
                  : "max-w-[55%] shrink-0 text-[15px]"
              }`}
            >
              {label.name}
            </h2>
          </>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {view === "graph" && workflow ? (
          <>
            <div
              className="flex shrink-0 items-center gap-2"
              data-graph-toolbar-target="true"
              ref={onGraphToolbarTargetChange}
            />
            <span aria-hidden="true" className="h-5 w-px shrink-0 bg-line" />
          </>
        ) : null}
      </div>
    </header>
  );
}

export function topBarLabelParts(workflow, view = "graph", activeCodePath = "") {
  if (view === "code") {
    return splitTopBarPath(activeCodePath, "No file open");
  }
  const projectRoot = String(workflow?.projectRoot ?? "").replace(/[\\/]+$/, "");
  const projectPath = projectRoot
    ? projectNameFromPath(projectRoot)
    : String(workflow?.projectName || "Unfiled project");
  const workflowTitle = String(workflow?.name || workflow?.id || "Untitled workflow");
  const separator = projectPath.includes("\\") && !projectPath.includes("/") ? "\\" : "/";
  return {
    fullPath: `${projectPath}${separator}${workflowTitle}`,
    name: workflowTitle,
    path: projectPath,
    separator,
  };
}

function splitTopBarPath(path, fallbackName) {
  const fullPath = String(path ?? "").trim();
  if (!fullPath) return { fullPath: fallbackName, name: fallbackName, path: "", separator: "" };
  const separatorIndex = Math.max(fullPath.lastIndexOf("/"), fullPath.lastIndexOf("\\"));
  if (separatorIndex < 0) {
    return { fullPath, name: fullPath, path: "", separator: "" };
  }
  return {
    fullPath,
    name: fullPath.slice(separatorIndex + 1) || fallbackName,
    path: fullPath.slice(0, separatorIndex),
    separator: fullPath[separatorIndex],
  };
}

export function GlobalToolbar({
  projectError = "",
  activeCodeDocument,
  activeCodePath = "",
  assistantPaneVisible = true,
  projectPaneVisible = true,
  recentProjectRoots = [],
  settings = DEFAULT_APP_SETTINGS,
  settingsOpen = false,
  theme,
  updateState,
  updateNotice,
  updatedNotice = null,
  view = "graph",
  onApplyUpdate,
  onCheckForUpdates,
  onDismissUpdatedNotice,
  onOpenWhatsNew,
  onSelectProject,
  onToggleSettings,
  onToggleTheme,
  onMenuAction,
}) {
  const hasUpdateBridge = Boolean(window.goferUpdates?.check);
  const buttonClass = "studio-icon-button grid h-7 w-7 place-items-center rounded-md text-muted transition hover:bg-slate-100 hover:text-ink";
  return (
    <header className="global-toolbar flex h-8 shrink-0 items-center justify-between border-b border-line bg-white px-2" aria-label="Application toolbar">
      <ApplicationMenus
        activeCodeDocument={activeCodeDocument}
        activeCodePath={activeCodePath}
        assistantPaneVisible={assistantPaneVisible}
        projectPaneVisible={projectPaneVisible}
        recentProjectRoots={recentProjectRoots}
        settings={settings}
        view={view}
        onAction={onMenuAction}
        onSelectProject={onSelectProject}
      />
      <div className="flex min-w-0 flex-1 items-center gap-2 px-3">
        {projectError ? <div role="alert" className="flex min-w-0 items-center gap-2 text-xs text-red-700 dark:text-red-300"><span className="truncate" title={projectError}>{projectError}</span><button type="button" className="shrink-0 underline" onClick={() => onMenuAction?.("file.openFolder")}>Open project</button></div> : null}
      </div>
      <div className="flex items-center gap-1">
        {updatedNotice ? (
          <div role="status" aria-label="Raticode updated" className="mr-1 flex h-6 min-w-0 shrink-0 items-center gap-1 rounded-md border border-indigo-200 bg-indigo-50 pl-2 text-xs text-indigo-800 dark:border-indigo-400/30 dark:bg-indigo-400/10 dark:text-indigo-200">
            <Sparkles aria-hidden="true" size={13} className="shrink-0" />
            <span className="truncate">{`Raticode updated to ${updatedNotice.version}`}</span>
            <button type="button" className="shrink-0 rounded px-1.5 font-semibold underline-offset-2 hover:underline" onClick={onOpenWhatsNew}>What&apos;s new</button>
            <button type="button" aria-label="Dismiss update notice" title="Dismiss" className="grid h-6 w-6 shrink-0 place-items-center rounded-r-md opacity-70 hover:opacity-100" onClick={onDismissUpdatedNotice}>
              <X aria-hidden="true" size={13} />
            </button>
          </div>
        ) : null}
        {hasUpdateBridge ? (
          <div className="flex min-w-0 items-center gap-2" aria-label="Application updates">
            {updateNotice?.message ? (
              <span role={updateNotice.type === "error" ? "alert" : "status"} title={updateNotice.message} className={`max-w-[min(28rem,35vw)] truncate text-xs ${updateNotice.type === "error" ? "text-red-700 dark:text-red-300" : "text-muted"}`}>
                {updateNotice.message}
              </span>
            ) : null}
            {updateState?.available ? (
              <button className="inline-flex h-7 shrink-0 items-center gap-2 rounded-md border border-indigo-200 bg-indigo-50 px-3 text-xs font-semibold text-indigo-700 transition hover:bg-indigo-100 disabled:cursor-wait disabled:opacity-70" disabled={Boolean(updateState.downloading || updateState.verifying)} title={updateButtonTitle(updateState)} type="button" onClick={onApplyUpdate}>
                {updateState.downloading || updateState.verifying ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                {updateButtonLabel(updateState)}
              </button>
            ) : null}
            <button className={`${buttonClass} shrink-0`} disabled={Boolean(updateState?.checking || updateState?.downloading || updateState?.verifying)} title={updateState?.error ? `Update check failed: ${updateState.error}` : "Check for updates"} aria-label="Check for updates" type="button" onClick={onCheckForUpdates}>
              <span className="relative">
                <RefreshCw size={16} className={updateState?.checking ? "animate-spin" : ""} />
                {updateState?.error && !updateState?.checking ? <span aria-hidden="true" className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-amber-500" /> : null}
              </span>
            </button>
          </div>
        ) : null}
        <button aria-label="Open settings" aria-expanded={settingsOpen} className={`${buttonClass} ${settingsOpen ? "bg-slate-100 text-ink" : ""}`} title={`Settings (${formatKeybinding(settingBinding(settings, "settings.open"))})`} type="button" onClick={onToggleSettings}>
          <SettingsIcon size={16} />
        </button>
        <button className={buttonClass} title={theme === "dark" ? "Light mode" : "Dark mode"} type="button" onClick={onToggleTheme}>
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </button>
      </div>
    </header>
  );
}

const APPLICATION_MENUS = [
  { label: "File", items: [
    ["file.new", "New File", "Ctrl+N"],
    ["file.open", "Open File...", "Ctrl+O"],
    ["file.quickOpen", "Quick Open...", ""],
    ["file.openFolder", "Open Project...", "project.open"],
    ["file.recentProjects", "Recent Projects"],
    null,
    ["file.save", "Save", "Ctrl+S", "code-document"],
    ["file.close", "Close Editor", "Ctrl+W", "code-path"],
  ] },
  { label: "Edit", items: [
    ["edit.undo", "Undo", "Ctrl+Z"],
    ["edit.redo", "Redo", "Ctrl+Shift+Z"],
    null,
    ["edit.cut", "Cut", "Ctrl+X"],
    ["edit.copy", "Copy", "Ctrl+C"],
    ["edit.paste", "Paste", "Ctrl+V"],
    null,
    ["edit.find", "Find", "Ctrl+F", "code-path"],
    ["edit.replace", "Replace", "Ctrl+H", "code-document"],
    ["edit.toggleLineComment", "Toggle Line Comment", "Ctrl+/", "code-document"],
    ["edit.formatDocument", "Format Document", "Shift+Alt+F", "code-document"],
  ] },
  { label: "Selection", items: [
    ["selection.selectAll", "Select All", "Ctrl+A"],
    ["selection.expand", "Expand Selection", "Shift+Alt+Right", "code-document"],
    ["selection.shrink", "Shrink Selection", "Shift+Alt+Left", "code-document"],
    null,
    ["selection.copyLineUp", "Copy Line Up", "Shift+Alt+Up", "code-document"],
    ["selection.copyLineDown", "Copy Line Down", "Shift+Alt+Down", "code-document"],
    ["selection.moveLineUp", "Move Line Up", "Alt+Up", "code-document"],
    ["selection.moveLineDown", "Move Line Down", "Alt+Down", "code-document"],
    null,
    ["selection.addCursorAbove", "Add Cursor Above", "Ctrl+Alt+Up", "code-document"],
    ["selection.addCursorBelow", "Add Cursor Below", "Ctrl+Alt+Down", "code-document"],
  ] },
  { label: "View", items: [
    ["view.commandPalette", "Command Palette...", ""],
    null,
    ["view.graph", "Workflows", ""],
    ["view.code", "Files", ""],
    null,
    ["view.projectPane", "Project Files", "Ctrl+B", "project-check"],
    ["view.assistantPane", "Rem", "", "assistant-check"],
    ["view.dictate", "Dictate a Message", ""],
    ["view.panel", "Bottom Panel", "panel.toggle"],
    null,
    ["view.zoomIn", "Zoom In", "Ctrl++"],
    ["view.zoomOut", "Zoom Out", "Ctrl+-"],
    ["view.resetZoom", "Reset Zoom", "Ctrl+0"],
  ] },
  { label: "Terminal", items: [["terminal.toggle", "Toggle Terminal", "panel.toggle"]] },
  { label: "Help", items: [
    ["help.documentation", "Documentation", ""],
    ["help.keyboardShortcuts", "Keyboard Shortcuts", ""],
    ["help.troubleshooting", "Troubleshooting", ""],
    null,
    ["help.reportProblem", "Report a Problem...", ""],
    null,
    ["help.whatsNew", "What's New", ""],
    ["help.updates", "Check for Updates...", ""],
    ["help.settings", "Settings", ""],
    null,
    ["help.about", "About Raticode", ""],
  ] },
];

export function ApplicationMenus({
  activeCodeDocument,
  activeCodePath,
  assistantPaneVisible,
  projectPaneVisible,
  recentProjectRoots = [],
  settings,
  view,
  onAction,
  onSelectProject,
}) {
  const [openMenu, setOpenMenu] = useState("");
  const [focusedMenu, setFocusedMenu] = useState("File");
  const containerRef = useRef(null);
  const openerRef = useRef(null);
  const returnFocusRef = useRef(null);
  const menuFocusRef = useRef(false);
  useEffect(() => {
    if (openMenu && menuFocusRef.current) containerRef.current?.querySelector('[role="menu"] button:not(:disabled)')?.focus();
  }, [openMenu]);
  function closeMenu(restore = true) {
    setOpenMenu("");
    if (restore) openerRef.current?.focus();
  }
  function navigateMenu(event) {
    const trigger = event.target.closest?.("[data-menu-trigger]");
    const menu = event.target.closest?.('[role="menu"]');
    const triggers = [...containerRef.current.querySelectorAll("[data-menu-trigger]")];
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMenu(); return; }
    if (event.key === "Tab") { setOpenMenu(""); return; }
    if (trigger && ["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault(); menuFocusRef.current = true; openerRef.current = trigger;
      if (openMenu !== trigger.dataset.menuTrigger) setOpenMenu(trigger.dataset.menuTrigger);
      else containerRef.current.querySelector('[role="menu"] button:not(:disabled)')?.focus();
      return;
    }
    if (menu && ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault(); event.stopPropagation();
      const items = [...menu.querySelectorAll('[role="menuitem"]:not(:disabled)')].filter(item => item.closest('[role="menu"]') === menu);
      const index = items.indexOf(event.target);
      const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowUp" ? -1 : 1) + items.length) % items.length;
      items[next]?.focus(); return;
    }
    if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
      if (menu?.parentElement?.closest('[role="menu"]')) return;
      event.preventDefault();
      const index = triggers.findIndex(item => item.dataset.menuTrigger === (openMenu || trigger?.dataset.menuTrigger));
      const next = triggers[(index + (event.key === "ArrowLeft" ? -1 : 1) + triggers.length) % triggers.length];
      if (!next) return;
      openerRef.current = next;
      next.focus();
      if (openMenu) { menuFocusRef.current = true; setOpenMenu(next.dataset.menuTrigger); }
    }
  }
  useEffect(() => {
    if (!openMenu) return undefined;
    const dismiss = (event) => {
      if (!containerRef.current?.contains(event.target)) setOpenMenu("");
    };
    const escape = (event) => {
      if (event.key === "Escape") closeMenu();
    };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("keydown", escape);
    };
  }, [openMenu]);
  const context = { activeCodeDocument, activeCodePath, assistantPaneVisible, projectPaneVisible, view };
  return (
    <nav aria-label="Application menu" role="menubar" className="flex h-full items-center gap-0.5" ref={containerRef} onKeyDown={navigateMenu}>
      {APPLICATION_MENUS.map((menu) => (
        <div className="relative" key={menu.label}>
          <button
            data-menu-trigger={menu.label}
            role="menuitem"
            tabIndex={focusedMenu === menu.label ? 0 : -1}
            onFocus={() => setFocusedMenu(menu.label)}
            aria-expanded={openMenu === menu.label}
            aria-haspopup="menu"
            className={`rounded px-2 py-1 text-[13px] text-ink outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-brand ${openMenu === menu.label ? "bg-slate-100" : ""}`}
            type="button"
            onPointerDown={() => { returnFocusRef.current = document.activeElement; rememberEditingTarget(document.activeElement); menuFocusRef.current = false; }}
            onClick={event => {
              openerRef.current = event.currentTarget;
              if (event.detail === 0) menuFocusRef.current = true;
              setOpenMenu(current => current === menu.label ? "" : menu.label);
            }}
            onPointerEnter={event => {
              if (!openMenu) return;
              menuFocusRef.current = false;
              openerRef.current = event.currentTarget;
              setFocusedMenu(menu.label);
              setOpenMenu(menu.label);
            }}
          >
            {menu.label}
          </button>
          {openMenu === menu.label ? (
            <div className="absolute left-0 top-[calc(100%+7px)] z-[100] min-w-[250px] rounded-md border border-line bg-white p-1.5 text-ink shadow-[0_10px_28px_rgba(15,23,42,0.18)]" role="menu">
              {menu.items.map((item, index) => item === null ? (
                <div aria-hidden="true" className="my-1 border-t border-line" key={`separator-${index}`} />
              ) : item[0] === "file.recentProjects" ? (
                <RecentProjectsMenuItem
                  key={item[0]}
                  recentProjectRoots={recentProjectRoots}
                  onSelect={(root) => {
                    setOpenMenu("");
                    onSelectProject?.(root);
                  }}
                />
              ) : (
                <ApplicationMenuItem
                  context={context}
                  item={item}
                  key={item[0]}
                  settings={settings}
                  onSelect={() => {
                    setOpenMenu("");
                    returnFocusRef.current?.focus?.();
                    onAction?.(item[0]);
                  }}
                />
              ))}
            </div>
          ) : null}
        </div>
      ))}
    </nav>
  );
}

const APPLICATION_MENU_BINDINGS = {
  "edit.find": "editor.find",
  "file.close": "file.close",
  "file.new": "file.new",
  "file.open": "file.open",
  "file.quickOpen": QUICK_OPEN_COMMAND_ID,
  "file.openFolder": "project.open",
  "file.save": "file.save",
  "help.settings": "settings.open",
  "terminal.toggle": "panel.toggle",
  "view.assistantPane": "view.toggleAssistantPane",
  "view.commandPalette": PALETTE_COMMAND_ID,
  "view.dictate": VOICE_COMMAND_ID,
  "view.code": "view.code",
  "view.graph": "view.graph",
  "view.panel": "panel.toggle",
  "view.projectPane": "view.toggleProjectPane",
};

function ApplicationMenuItem({ context, item, onSelect, settings }) {
  const [action, label, fallbackShortcut, condition] = item;
  const bindingId = APPLICATION_MENU_BINDINGS[action];
  const genericShortcuts = { "edit.undo": "Mod+KeyZ", "edit.redo": "Mod+Shift+KeyZ", "edit.cut": "Mod+KeyX", "edit.copy": "Mod+KeyC", "edit.paste": "Mod+KeyV", "selection.selectAll": "Mod+KeyA" };
  const shortcut = bindingId
    ? formatKeybinding(settingBinding(settings, bindingId))
    : (genericShortcuts[action] ? formatKeybinding(genericShortcuts[action]) : context.activeCodeDocument?.commands?.[action]?.shortcut)
      || (action.startsWith("edit.") || action.startsWith("selection.") ? "" : formatKeybinding(fallbackShortcut.replace("Ctrl", "Mod")));
  const capability = context.activeCodeDocument?.commands?.[action];
  const generalEdit = ["edit.undo", "edit.redo", "edit.cut", "edit.copy", "edit.paste", "selection.selectAll"].includes(action);
  const enabled = (generalEdit || capability?.supported !== false) && (!condition || condition.endsWith("-check")
    || (condition === "code" && context.view === "code")
    || (condition === "code-path" && context.view === "code" && Boolean(context.activeCodePath))
    || (condition === "code-document" && context.view === "code" && Boolean(context.activeCodeDocument)));
  const checked = condition === "graph-check" && context.view === "graph"
    || condition === "code-check" && context.view === "code"
    || condition === "project-check" && context.projectPaneVisible
    || condition === "assistant-check" && context.assistantPaneVisible;
  return (
    <button
      className="flex h-8 w-full items-center gap-3 rounded px-2 text-left text-[12px] hover:bg-slate-100 disabled:text-muted disabled:opacity-45 disabled:hover:bg-transparent"
      disabled={!enabled}
      title={capability?.supported === false ? "Unavailable for this document. No supporting language provider is loaded." : undefined}
      role="menuitem"
      type="button"
      onClick={onSelect}
    >
      <span aria-hidden="true" className="w-3 text-center">{checked ? "✓" : ""}</span>
      <span className="flex-1">{label}</span>
      {shortcut ? <span className="text-[11px] text-muted">{shortcut}</span> : null}
    </button>
  );
}

function RecentProjectsMenuItem({ recentProjectRoots, onSelect }) {
  const [open, setOpen] = useState(false);
  const parentRef = useRef(null);
  const submenuRef = useRef(null);
  const keyboardOpenRef = useRef(false);
  useEffect(() => { if (open && keyboardOpenRef.current) submenuRef.current?.querySelector("button")?.focus(); }, [open]);
  const labels = loadProjectLabels();
  const projects = mergeRecentProjects([], recentProjectRoots).map((root) => ({
    name: pathValue(labels, root)?.trim() || projectNameFromPath(root),
    root,
  }));
  return (
    <div className="relative" onPointerEnter={() => setOpen(true)} onPointerLeave={() => setOpen(false)}>
      <button
        ref={parentRef}
        aria-expanded={open}
        aria-haspopup="menu"
        onKeyDown={event => {
          if (["ArrowRight", "Enter", " "].includes(event.key)) { event.preventDefault(); event.stopPropagation(); keyboardOpenRef.current = true; setOpen(true); }
        }}
        className="flex h-8 w-full items-center gap-3 rounded px-2 text-left text-[12px] hover:bg-slate-100"
        role="menuitem"
        type="button"
        onClick={() => setOpen((current) => !current)}
      >
        <span aria-hidden="true" className="w-3" />
        <span className="flex-1">Recent Projects</span>
        <ChevronRight aria-hidden="true" size={13} />
      </button>
      {open ? (
        <div aria-label="Recent projects" className="absolute left-full top-[-6px] z-[101] min-w-[260px] rounded-md border border-line bg-white p-1.5 shadow-[0_10px_28px_rgba(15,23,42,0.18)]" role="menu" ref={submenuRef} onKeyDown={event => {
          if (["ArrowLeft", "Escape"].includes(event.key)) { event.preventDefault(); event.stopPropagation(); setOpen(false); parentRef.current?.focus(); }
        }}>
          {projects.length ? projects.map((project) => (
            <button className="flex h-8 w-full items-center gap-2 rounded px-2 text-left text-xs hover:bg-slate-100" key={project.root} role="menuitem" title={project.root} type="button" onClick={() => onSelect(project.root)}>
              <FolderOpen aria-hidden="true" className="shrink-0 text-muted" size={13} />
              <span className="min-w-0 flex-1 truncate">{project.name}</span>
                    {project.branch ? <span className="max-w-24 truncate text-[10px] text-muted">{project.branch}</span> : null}
            </button>
          )) : <p className="px-2 py-2 text-xs text-muted">No recent projects</p>}
        </div>
      ) : null}
    </div>
  );
}

// Rem's chat in the side pane, used in place of threads when Settings > Layout
// asks for it. It docks and resizes like the threads pane.
export function RemSidePane({ width, onResizeStart, onResizeKeyDown, children }) {
  return (
    <aside className="studio-rem-pane relative flex h-full min-h-0 shrink-0 flex-col overflow-hidden border-l border-line" data-rem-pane="true" style={{ width }}>
      <div
        aria-label="Resize Rem pane"
        aria-orientation="vertical"
        aria-valuemax={520}
        aria-valuemin={300}
        aria-valuenow={width}
        aria-valuetext={`${width} pixels wide`}
        className="absolute left-[-3px] top-0 z-20 h-full w-1.5 cursor-col-resize transition hover:bg-brand/40"
        role="separator"
        tabIndex={0}
        title="Resize Rem pane"
        onKeyDown={onResizeKeyDown}
        onPointerDown={onResizeStart}
      />
      {children}
    </aside>
  );
}

export function TextZoomBar({ value = 100 }) {
  const progress = ((value - TEXT_ZOOM_MIN) / (TEXT_ZOOM_MAX - TEXT_ZOOM_MIN)) * 100;
  const description = `App zoom ${value}%.`;
  return (
    <div
      aria-label={description}
      aria-valuemax={TEXT_ZOOM_MAX}
      aria-valuemin={TEXT_ZOOM_MIN}
      aria-valuenow={value}
      className="pointer-events-none fixed bottom-1 right-3 z-[100] flex items-center gap-1.5 text-[9px] tabular-nums text-muted opacity-60"
      data-text-zoom
      role="meter"
      title={description}
    >
      <span>Zoom {value}%</span>
      <span aria-hidden="true" className="relative h-px w-14 overflow-hidden bg-line">
        <span
          className="absolute inset-y-0 left-0 bg-current"
          style={{ width: `${progress}%` }}
        />
      </span>
    </div>
  );
}
