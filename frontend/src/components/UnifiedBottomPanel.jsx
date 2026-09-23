import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Activity, AlertCircle, AlertTriangle, ChevronDown, History, Terminal as TerminalIcon } from "lucide-react";

const RunTimelinePanel = lazy(() => import("./DagCanvas.jsx").then(module => ({ default: module.RunTimelinePanel })));
import { DEFAULT_APP_SETTINGS, formatKeybinding, matchesCommand, settingBinding } from "../lib/settings.js";

import RunSummary from "./RunSummary.jsx";
import { workflowRunSummary } from "../lib/workflowRuns.js";

const TerminalWorkspace = lazy(() => import("./TerminalWorkspace.jsx"));
import { bottomPanelTabForShortcut, clamp } from "../lib/terminalWorkspace.js";
export * from "../lib/terminalWorkspace.js";

const PANEL_MIN_HEIGHT = 140;
const PANEL_MAX_HEIGHT = 480;

export default function UnifiedBottomPanel({
  diagnostics = [],
  onRevealDiagnostic,
  onSettingChange,
  projectRoot = "",
  settings = DEFAULT_APP_SETTINGS,
  theme = "light",
  timelineProps,
  runsProps = {},
}) {
  const hasExplicitPanelSelectionRef = useRef(false);
  const [activeTab, setActiveTab] = useState("timeline");
  const [collapsed, setCollapsed] = useState(true);
  const [height, setHeight] = useState(settings.layout.bottomPanelHeight);
  const [newTerminalRequest, setNewTerminalRequest] = useState(0);
  const [terminalMounted, setTerminalMounted] = useState(false);
  const [runsMounted, setRunsMounted] = useState(false);
  const [timelineMounted, setTimelineMounted] = useState(false);
  useEffect(() => { if (activeTab === "timeline" && !collapsed) setTimelineMounted(true); }, [activeTab, collapsed]);

  const selectTab = useCallback((tab) => {
    hasExplicitPanelSelectionRef.current = true;
    setActiveTab(tab);
    setCollapsed(false);
    if (tab === "runs") setRunsMounted(true);
    if (tab === "terminal") setTerminalMounted(true);
  }, []);

  const handleTabClick = useCallback((tab) => {
    if (tab === activeTab && !collapsed) {
      setCollapsed(true);
      return;
    }
    selectTab(tab);
  }, [activeTab, collapsed, selectTab]);

  useEffect(() => {
    function togglePanel() {
      if (collapsed) {
        const targetTab = bottomPanelTabForShortcut(
          activeTab,
          hasExplicitPanelSelectionRef.current,
        );
        setActiveTab(targetTab);
        if (targetTab === "terminal") setTerminalMounted(true);
      }
      setCollapsed((current) => !current);
    }

    function newTerminal() {
      selectTab("terminal");
      setNewTerminalRequest((current) => current + 1);
    }

    function handleKeyDown(event) {
      if (matchesCommand(event, settings, "terminal.new") && !event.repeat) {
        event.preventDefault();
        event.stopPropagation();
        newTerminal();
        return;
      }
      if (!matchesCommand(event, settings, "panel.toggle") || event.repeat) return;
      event.preventDefault();
      event.stopPropagation();
      togglePanel();
    }

    function handleExternalToggle(event) {
      if (event.detail?.tab) {
        if (activeTab === event.detail.tab && !collapsed && !event.detail.open) {
          setCollapsed(true);
        } else {
          selectTab(event.detail.tab);
        }
        return;
      }
      togglePanel();
    }

    window.addEventListener("gofer:new-terminal", newTerminal);
    window.addEventListener("keydown", handleKeyDown, true);
    window.addEventListener("gofer:toggle-bottom-panel", handleExternalToggle);
    return () => {
      window.removeEventListener("gofer:new-terminal", newTerminal);
      window.removeEventListener("keydown", handleKeyDown, true);
      window.removeEventListener("gofer:toggle-bottom-panel", handleExternalToggle);
    };
  }, [activeTab, collapsed, selectTab, settings]);

  useEffect(() => {
    setHeight(settings.layout.bottomPanelHeight);
  }, [settings.layout.bottomPanelHeight]);

  function startResize(event) {
    if (collapsed) return;
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = height;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    let nextHeight = startHeight;
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";

    function handlePointerMove(moveEvent) {
      nextHeight = clamp(startHeight + startY - moveEvent.clientY, PANEL_MIN_HEIGHT, PANEL_MAX_HEIGHT);
      setHeight(nextHeight);
    }

    function handlePointerUp() {
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      onSettingChange?.("layout.bottomPanelHeight", nextHeight);
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
  }

  function resizeWithKeyboard(event) {
    if (!["ArrowUp", "ArrowDown", "Home", "End", "Enter"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Home") {
      setHeight(PANEL_MIN_HEIGHT);
      onSettingChange?.("layout.bottomPanelHeight", PANEL_MIN_HEIGHT);
      return;
    }
    if (event.key === "End") {
      setHeight(PANEL_MAX_HEIGHT);
      onSettingChange?.("layout.bottomPanelHeight", PANEL_MAX_HEIGHT);
      return;
    }
    if (event.key === "Enter") {
      setHeight(DEFAULT_APP_SETTINGS.layout.bottomPanelHeight);
      onSettingChange?.("layout.bottomPanelHeight", DEFAULT_APP_SETTINGS.layout.bottomPanelHeight);
      return;
    }
    const step = event.shiftKey ? 40 : 10;
    const nextHeight = clamp(
      height + (event.key === "ArrowUp" ? step : -step),
      PANEL_MIN_HEIGHT,
      PANEL_MAX_HEIGHT,
    );
    setHeight(nextHeight);
    onSettingChange?.("layout.bottomPanelHeight", nextHeight);
  }

  const runSummary = workflowRunSummary(runsProps.records ?? []);
  const errorCount = diagnostics.filter((diagnostic) => diagnostic.severity === "error").length;

  return (
    <section
      aria-label="Bottom panel"
      className="relative z-30 shrink-0 overflow-hidden border-t border-line bg-white text-ink transition-[height] duration-150 ease-out"
      style={{ height: collapsed ? 36 : height }}
    >
      {!collapsed ? (
        <div
          aria-label="Resize bottom panel"
          aria-orientation="horizontal"
          aria-valuemax={PANEL_MAX_HEIGHT}
          aria-valuemin={PANEL_MIN_HEIGHT}
          aria-valuenow={height}
          className="absolute left-0 top-[-3px] z-30 h-1.5 w-full cursor-row-resize transition-colors hover:bg-brand/40"
          role="separator"
          tabIndex={0}
          title="Resize bottom panel"
          onKeyDown={resizeWithKeyboard}
          onPointerDown={startResize}
        />
      ) : null}

      <div
        aria-label="Bottom panel views"
        className="flex h-9 cursor-pointer items-stretch border-b border-line bg-[#f9fbfd]"
        role="tablist"
        title={`${collapsed ? "Expand" : "Collapse"} bottom panel`}
        onClick={(event) => {
          if (event.target.closest?.("button")) return;
          setCollapsed((current) => !current);
        }}
      >
        <PanelTab
          active={activeTab === "problems"}
          icon={AlertTriangle}
          label="Problems"
          onClick={() => handleTabClick("problems")}
        >
          {diagnostics.length ? (
            <span className={errorCount ? "text-red-700" : "text-amber-700"}>
              {diagnostics.length}
            </span>
          ) : null}
        </PanelTab>
        <PanelTab
          active={activeTab === "timeline"}
          icon={History}
          label="Run Timeline"
          onClick={() => handleTabClick("timeline")}
        />
        <PanelTab
          active={activeTab === "runs"}
          icon={Activity}
          label="Runs"
          onClick={() => handleTabClick("runs")}
        >
          {runSummary.active.length ? <span>{runSummary.active.length} active</span> : null}
          {runSummary.unread.length ? <span className="text-brand">· {runSummary.unread.length} unread</span> : null}
          {runSummary.disconnected.length ? <span aria-label="Some run status is out of date" title="Some run status is out of date">!</span> : null}
        </PanelTab>
        <PanelTab
          active={activeTab === "terminal"}
          icon={TerminalIcon}
          label="Terminal"
          onClick={() => handleTabClick("terminal")}
        />
        <div className="flex-1" />
        <button
          aria-label={collapsed ? "Expand bottom panel" : "Collapse bottom panel"}
          className="grid w-9 place-items-center text-muted transition hover:bg-slate-100 hover:text-ink"
          title={`${collapsed ? "Expand" : "Collapse"} bottom panel (${formatKeybinding(settingBinding(settings, "panel.toggle"))})`}
          type="button"
          onClick={() => setCollapsed((current) => !current)}
        >
          <ChevronDown className={`transition-transform ${collapsed ? "rotate-180" : ""}`} size={15} />
        </button>
      </div>

      <div className="h-[calc(100%-36px)] min-h-0">
        <div className={activeTab === "problems" ? "h-full" : "hidden"} role="tabpanel">
          <ProblemsPanel diagnostics={diagnostics} onRevealDiagnostic={onRevealDiagnostic} />
        </div>
        <div className={activeTab === "timeline" ? "h-full" : "hidden"} role="tabpanel">
          {timelineMounted ? <Suspense fallback={<p role="status" className="p-3 text-xs">Loading run history...</p>}><RunTimelinePanel {...timelineProps} collapsed={false} embedded height={height - 36} /></Suspense> : null}
        </div>
        {runsMounted ? <div className={activeTab === "runs" ? "h-full" : "hidden"} role="tabpanel" aria-label="Runs">
          <RunSummary {...runsProps} embedded onClose={() => setCollapsed(true)} />
        </div> : null}
        {terminalMounted ? (
          <div className={activeTab === "terminal" ? "h-full" : "hidden"} role="tabpanel">
            <Suspense fallback={<p role="status" className="p-3 text-xs text-muted">Loading terminal...</p>}><TerminalWorkspace
              active={!collapsed && activeTab === "terminal"}
              projectRoot={projectRoot}
              newTerminalRequest={newTerminalRequest}
              settings={settings}
              theme={theme}
            /></Suspense>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function PanelTab({ active, children, icon: Icon, label, onClick }) {
  return (
    <button
      aria-selected={active}
      className={`relative flex h-9 items-center gap-1.5 px-3 text-[11px] font-semibold transition-colors ${active ? "bg-white text-ink" : "text-muted hover:bg-slate-50 hover:text-ink"}`}
      role="tab"
      type="button"
      onClick={onClick}
    >
      <Icon aria-hidden="true" size={13} />
      {label}
      {children}
      {active ? <span className="absolute inset-x-2 top-0 h-0.5 bg-brand" /> : null}
    </button>
  );
}

function ProblemsPanel({ diagnostics, onRevealDiagnostic }) {
  if (!diagnostics.length) {
    return (
      <div className="grid h-full place-items-center px-6 text-xs text-muted">
        No problems in workflow.rattish.
      </div>
    );
  }

  return (
    <div className="workflow-scrollbar h-full overflow-y-auto py-1">
      {diagnostics.map((diagnostic, index) => {
        const warning = diagnostic.severity === "warning";
        const Icon = warning ? AlertTriangle : AlertCircle;
        const line = diagnostic.span?.start?.line ?? 1;
        return (
          <button
            key={`${diagnostic.code}-${diagnostic.span?.start?.offset ?? 0}-${index}`}
            className="flex w-full items-start gap-2 px-3 py-2 text-left text-[11px] leading-4 transition hover:bg-slate-50"
            type="button"
            onClick={() => onRevealDiagnostic?.(diagnostic)}
          >
            <Icon aria-hidden="true" className={`mt-0.5 shrink-0 ${warning ? "text-amber-600" : "text-red-600"}`} size={13} />
            <span className="min-w-0 flex-1 text-ink">
              {diagnostic.message}
              {diagnostic.code ? <span className="ml-2 font-mono text-[10px] text-muted">{diagnostic.code}</span> : null}
            </span>
            <span className="shrink-0 font-mono text-[10px] text-muted">workflow.rattish:{line}</span>
          </button>
        );
      })}
    </div>
  );
}
