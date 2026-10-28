import { useEffect, useRef, useState } from "react";
import { Code2, Copy, Loader2, MoreVertical, PencilLine, Play, Square, Trash2 } from "lucide-react";
import { formatRunAge } from "../lib/workflowRuns.js";

const WORKFLOW_MENU_SIZE = { width: 200, height: 214 };

// Keeps the fixed-position menu inside the viewport.
function menuPosition(clientX, clientY, viewportWidth = window.innerWidth, viewportHeight = window.innerHeight) {
  return {
    x: Math.max(8, Math.min(Number.isFinite(clientX) ? clientX : 8, viewportWidth - WORKFLOW_MENU_SIZE.width - 8)),
    y: Math.max(8, Math.min(Number.isFinite(clientY) ? clientY : 8, viewportHeight - WORKFLOW_MENU_SIZE.height - 8)),
  };
}

const WORKFLOW_STATUS_PILL = {
  running: "border-blue-200 bg-blue-50 text-blue-700 dark:border-sky-700/70 dark:bg-sky-950/70 dark:text-sky-200",
  success: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800/70 dark:bg-emerald-950/60 dark:text-emerald-200",
  error: "border-red-200 bg-red-50 text-red-700 dark:border-red-800/70 dark:bg-red-950/60 dark:text-red-200",
  warning: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800/70 dark:bg-amber-950/60 dark:text-amber-200",
  muted: "border-line bg-white text-slate-600 dark:bg-transparent dark:text-zinc-300",
};

const WORKFLOW_STATUS_DOT = {
  success: "bg-emerald-500",
  error: "bg-red-500",
  warning: "bg-amber-500",
  muted: "bg-slate-400",
};

export function workflowListSubtitle(listStatus, now = Date.now()) {
  if (listStatus.detail) return listStatus.detail;
  const age = formatRunAge(listStatus.since, now);
  if (!age) return "";
  return listStatus.tone === "running" ? `Started ${age}` : `Last run ${age}`;
}

export default function WorkflowListItem({
  active,
  listStatus,
  onDelete,
  onDuplicate,
  onEditFile,
  onRename,
  onRun,
  onSelect,
  onStop,
  workflow,
}) {
  const menuRef = useRef(null);
  const menuButtonRef = useRef(null);
  const nameInputRef = useRef(null);
  const [menu, setMenu] = useState(null);
  const [renaming, setRenaming] = useState(false);
  const [draftName, setDraftName] = useState(workflow.name);
  const subtitle = workflowListSubtitle(listStatus);
  const invalid = listStatus.key === "invalid";

  useEffect(() => {
    if (!menu) return undefined;
    const close = () => setMenu(null);
    function handlePointerDown(event) {
      if (menuRef.current?.contains(event.target) || menuButtonRef.current?.contains(event.target)) return;
      close();
    }
    function handleKeyDown(event) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close();
      menuButtonRef.current?.focus();
    }
    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("blur", close);
    // The menu is fixed to the viewport, so scrolling the list would strand it.
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    menuRef.current?.querySelector("[role=menuitem]:not(:disabled)")?.focus();
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("blur", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [menu]);

  useEffect(() => {
    if (!renaming) setDraftName(workflow.name);
  }, [renaming, workflow.name]);

  useEffect(() => {
    if (!renaming) return;
    nameInputRef.current?.focus();
    nameInputRef.current?.select();
  }, [renaming]);

  function openMenu(clientX, clientY) {
    setMenu(menuPosition(clientX, clientY));
  }

  function commitRename() {
    const nextName = draftName.trim();
    setRenaming(false);
    if (!nextName) {
      setDraftName(workflow.name);
      return;
    }
    if (nextName !== workflow.name) {
      onRename(nextName);
    }
  }

  function cancelRename() {
    setRenaming(false);
    setDraftName(workflow.name);
  }

  function menuAction(action) {
    return (event) => {
      event.stopPropagation();
      setMenu(null);
      action();
    };
  }

  function moveMenuFocus(event) {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const items = [...(menuRef.current?.querySelectorAll("[role=menuitem]:not(:disabled)") ?? [])];
    const index = items.indexOf(document.activeElement);
    const next = event.key === "Home" ? 0
      : event.key === "End" ? items.length - 1
        : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }

  const itemClass = "flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-ink focus:bg-slate-50 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent dark:hover:bg-[#2a2a2a] dark:focus:bg-[#2a2a2a]";

  return (
    <div
      className={`group relative w-full rounded-lg text-left transition ${
        active
          ? "bg-indigo-50"
          : "bg-transparent hover:bg-slate-100"
      }`}
      data-workflow-status={listStatus.key}
      onContextMenu={(event) => {
        event.preventDefault();
        event.stopPropagation();
        openMenu(event.clientX, event.clientY);
      }}
    >
      <div
        role="button"
        tabIndex={0}
        aria-current={active ? "page" : undefined}
        aria-label={`${workflow.name}, ${listStatus.label}${listStatus.unread ? ", unread result" : ""}`}
        className="w-full rounded-lg py-1.5 pl-2 pr-8 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500"
        onClick={() => {
          if (!renaming) {
            onSelect();
          }
        }}
        onKeyDown={(event) => {
          if (!renaming && event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) {
            event.preventDefault();
            onSelect();
          }
        }}
      >
        <div className="flex min-w-0 items-center gap-2">
          <div className="min-w-0 flex-1">
            {renaming ? (
              <input
                ref={nameInputRef}
                aria-label={`Rename ${workflow.name}`}
                className="w-full rounded-md border border-indigo-300 bg-white px-1.5 py-0.5 text-xs font-medium text-ink outline-none ring-2 ring-indigo-100"
                maxLength={120}
                value={draftName}
                onBlur={commitRename}
                onChange={(event) => setDraftName(event.target.value)}
                onClick={(event) => event.stopPropagation()}
                onKeyDown={(event) => {
                  event.stopPropagation();
                  if (event.key === "Enter") {
                    event.preventDefault();
                    event.currentTarget.blur();
                  }
                  if (event.key === "Escape") {
                    event.preventDefault();
                    cancelRename();
                  }
                }}
              />
            ) : (
              <p className={`flex min-w-0 items-center gap-1.5 text-xs ${active ? "text-indigo-700" : "text-ink"} ${listStatus.unread ? "font-semibold" : "font-medium"}`}>
                <span className="truncate" title={workflow.name}>{workflow.name}</span>
                {listStatus.unread ? <span aria-hidden="true" title="Unread result" className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand" /> : null}
              </p>
            )}
          </div>
          <WorkflowStatusPill listStatus={listStatus} />
        </div>
        {subtitle ? (
          <p
            className={`mt-0.5 truncate text-[10px] leading-4 ${invalid ? "text-red-700 dark:text-red-300" : "text-muted"}`}
            title={subtitle}
          >
            {subtitle}
          </p>
        ) : null}
      </div>
      <div className="absolute right-1 top-1/2 -translate-y-1/2">
        <button
          ref={menuButtonRef}
          aria-expanded={Boolean(menu)}
          aria-haspopup="menu"
          aria-label={`${workflow.name} actions`}
          className={`grid h-7 w-7 place-items-center rounded-md text-muted transition hover:bg-slate-200/70 hover:text-ink focus-visible:opacity-100 group-hover:opacity-100 dark:hover:bg-[#2a2a2a] ${menu || active ? "opacity-100" : "opacity-60"}`}
          title="Workflow actions"
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            if (menu) {
              setMenu(null);
              return;
            }
            const bounds = event.currentTarget.getBoundingClientRect();
            openMenu(bounds.right - WORKFLOW_MENU_SIZE.width, bounds.bottom + 4);
          }}
        >
          <MoreVertical size={14} />
        </button>
      </div>
      {menu ? (
        <div
          ref={menuRef}
          aria-label={`${workflow.name} actions`}
          className="fixed z-[80] rounded-lg border border-line bg-white p-1 shadow-panel"
          role="menu"
          style={{ left: menu.x, top: menu.y, width: WORKFLOW_MENU_SIZE.width }}
          onKeyDown={moveMenuFocus}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <button className={itemClass} role="menuitem" type="button" onClick={menuAction(onEditFile)}>
            <Code2 size={15} />
            Edit workflow file
          </button>
          {onStop ? (
            <button className={itemClass} role="menuitem" type="button" onClick={menuAction(onStop)}>
              <Square size={14} />
              Stop run
            </button>
          ) : (
            <button
              className={itemClass}
              disabled={!onRun}
              role="menuitem"
              title={invalid ? "Fix this workflow's errors before running it." : undefined}
              type="button"
              onClick={menuAction(() => onRun?.())}
            >
              <Play size={15} />
              Run workflow
            </button>
          )}
          <button className={itemClass} role="menuitem" type="button" onClick={menuAction(() => setRenaming(true))}>
            <PencilLine size={15} />
            Rename workflow
          </button>
          <button className={itemClass} role="menuitem" type="button" onClick={menuAction(onDuplicate)}>
            <Copy size={15} />
            Duplicate workflow
          </button>
          <div className="my-1 border-t border-line" />
          <button
            className={`${itemClass} !text-red-700 hover:!bg-red-50 focus:!bg-red-50 dark:hover:!bg-[#3a2424] dark:focus:!bg-[#3a2424]`}
            role="menuitem"
            type="button"
            onClick={menuAction(onDelete)}
          >
            <Trash2 size={15} />
            Delete workflow
          </button>
        </div>
      ) : null}
    </div>
  );
}

function WorkflowStatusPill({ listStatus }) {
  const running = listStatus.tone === "running";
  return (
    <span
      className={`flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-medium leading-none ${WORKFLOW_STATUS_PILL[listStatus.tone] ?? WORKFLOW_STATUS_PILL.muted}`}
      title={listStatus.tone === "error" && listStatus.detail ? listStatus.detail : listStatus.label}
    >
      {running ? (
        <Loader2 aria-hidden="true" size={10} className="animate-spin motion-reduce:animate-none" />
      ) : (
        <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${WORKFLOW_STATUS_DOT[listStatus.tone] ?? WORKFLOW_STATUS_DOT.muted}`} />
      )}
      {listStatus.label}
    </span>
  );
}
