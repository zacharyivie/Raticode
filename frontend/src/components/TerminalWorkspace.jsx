import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import * as fitAddonPackage from "@xterm/addon-fit";
import * as searchAddonPackage from "@xterm/addon-search";
import * as xtermPackage from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { CaseSensitive, ChevronDown, ChevronUp, FolderPlus, FolderOpen, Pencil, Plus, Terminal as TerminalIcon, Trash2, X } from "lucide-react";


import { askRemAboutTerminalSelection } from "../lib/askRemAboutFailure.js";
import { formatKeybinding, settingBinding } from "../lib/settings.js";
import { createFileExistenceCheck, createTerminalHyperlinkHandler, createTerminalLinkProvider, TERMINAL_OPEN_FILE_EVENT } from "../lib/terminalLinks.js";
import { useReducedMotion } from "../lib/useReducedMotion.js";
import { menuTrigger, PopupMenu } from "./PopupMenu.jsx";

const { FitAddon } = fitAddonPackage;
const { SearchAddon } = searchAddonPackage;
const { Terminal: XTerm } = xtermPackage;

import { projectFolderName, terminalDirectoryFromOsc, terminalProjectGroupId, terminalGroupName, terminalTabGroupId, moveTerminalTabToGroup, terminalTabsAfterDeletingGroup, upsertTerminalGroupDefinition, groupTerminalTabsByProject, shouldCreateInitialTerminal, copyTerminalSelection, handleTerminalClipboardShortcut, terminalWordEraseInput, createDisposableTerminalSession, terminalWorkspaceShortcutAction, terminalFindCountText, terminalFindInitialTerm, TERMINAL_FIND_HIGHLIGHT_LIMIT } from "../lib/terminalWorkspace.js";
export default function TerminalWorkspace({ active, projectRoot, settings, theme, newTerminalRequest = 0 }) {
  const reduceMotion = useReducedMotion();
  const nextTabRef = useRef(1);
  const nextGroupRef = useRef(1);
  const handledNewTerminalRequestRef = useRef(0);
  const initialTerminalCreatedRef = useRef(false);
  const layoutElementsRef = useRef(new Map());
  const layoutRectsRef = useRef(new Map());
  const menuRef = useRef(null);
  const menuTriggerRef = useRef(null);
  const groupRenameCanceledRef = useRef(false);
  const groupRenameInputRef = useRef(null);
  const renameCanceledRef = useRef(false);
  const renameInputRef = useRef(null);
  const [tabs, setTabs] = useState([]);
  const [activeKey, setActiveKey] = useState(null);
  const [collapsedGroups, setCollapsedGroups] = useState({});
  const [draggedKey, setDraggedKey] = useState(null);
  const [dragOverGroupId, setDragOverGroupId] = useState(null);
  const [groupDefinitions, setGroupDefinitions] = useState([]);
  const [groupMenu, setGroupMenu] = useState(null);
  const [groupRenameDraft, setGroupRenameDraft] = useState("");
  const [renamingGroupId, setRenamingGroupId] = useState(null);
  const [tabMenu, setTabMenu] = useState(null);
  const [renamingKey, setRenamingKey] = useState(null);
  const [renameDraft, setRenameDraft] = useState("");
  // Each press of Find counts up, so pressing it again refocuses an open find bar.
  const [findRequest, setFindRequest] = useState({ key: null, count: 0 });

  const applyShellLabel = useCallback((key, label) => {
    setTabs((current) => current.map((item) => (
      item.key === key && !item.customName
        ? { ...item, label: `${label} ${item.number}` }
        : item
    )));
  }, []);

  const applyWorkingDirectory = useCallback((key, currentDirectory) => {
    setTabs((current) => current.map((item) => (
      item.key === key
        ? {
            ...item,
            currentDirectory,
            folderName: projectFolderName(currentDirectory),
          }
        : item
    )));
  }, []);

  const addTerminal = useCallback((targetProjectPath = projectRoot, targetGroupId = null) => {
    const number = nextTabRef.current;
    nextTabRef.current += 1;
    const key = `terminal-${number}-${Date.now()}`;
    const groupId = targetGroupId || terminalProjectGroupId(targetProjectPath);
    setTabs((current) => [
      ...current,
      {
        customName: false,
        cwd: targetProjectPath,
        folderName: projectFolderName(targetProjectPath),
        key,
        label: `Terminal ${number}`,
        number,
        groupId,
        projectPath: targetProjectPath,
      },
    ]);
    setGroupDefinitions((current) => current.map((group) => (
      group.id === groupId ? { ...group, keepEmpty: false } : group
    )));
    setActiveKey(key);
    setCollapsedGroups((current) => {
      return current[groupId] ? { ...current, [groupId]: false } : current;
    });
  }, [projectRoot]);

  const terminalGroups = groupTerminalTabsByProject(tabs, groupDefinitions);

  useLayoutEffect(() => {
    const nextRects = new Map();
    if (reduceMotion) {
      for (const element of layoutElementsRef.current.values()) element?.getAnimations?.().forEach(animation => animation.cancel());
    }
    for (const [key, element] of layoutElementsRef.current) {
      if (!element?.isConnected) continue;
      const nextRect = element.getBoundingClientRect();
      const previousRect = layoutRectsRef.current.get(key);
      nextRects.set(key, nextRect);
      if (reduceMotion || !previousRect || typeof element.animate !== "function") continue;
      const deltaX = previousRect.left - nextRect.left;
      const deltaY = previousRect.top - nextRect.top;
      if (Math.abs(deltaX) < 1 && Math.abs(deltaY) < 1) continue;
      element.animate(
        [
          { transform: `translate3d(${deltaX}px, ${deltaY}px, 0)` },
          { transform: "translate3d(0, 0, 0)" },
        ],
        { duration: 220, easing: "cubic-bezier(0.16, 1, 0.3, 1)" },
      );
    }
    layoutRectsRef.current = nextRects;
  }, [collapsedGroups, groupDefinitions, tabs, reduceMotion]);

  useEffect(() => {
    const pending = newTerminalRequest - handledNewTerminalRequestRef.current;
    handledNewTerminalRequestRef.current = newTerminalRequest;
    if (pending <= 0) return;
    initialTerminalCreatedRef.current = true;
    for (let index = 0; index < pending; index += 1) addTerminal();
  }, [newTerminalRequest, addTerminal]);

  useEffect(() => {
    if (!shouldCreateInitialTerminal(active, tabs.length, initialTerminalCreatedRef.current)) return;
    initialTerminalCreatedRef.current = true;
    addTerminal();
  }, [active, addTerminal, tabs.length]);

  useEffect(() => {
    if (!tabMenu && !groupMenu) return undefined;
    function dismissMenu(event) {
      if (menuRef.current?.contains(event.target)) return;
      setGroupMenu(null);
      setTabMenu(null);
    }
    function dismissWithEscape(event) {
      if (event.key !== "Escape") return;
      setGroupMenu(null);
      setTabMenu(null);
    }
    window.addEventListener("pointerdown", dismissMenu);
    window.addEventListener("keydown", dismissWithEscape);
    return () => {
      window.removeEventListener("pointerdown", dismissMenu);
      window.removeEventListener("keydown", dismissWithEscape);
    };
  }, [groupMenu, tabMenu]);

  useEffect(() => {
    if (!renamingKey) return;
    renameInputRef.current?.focus();
    renameInputRef.current?.select();
  }, [renamingKey]);

  useEffect(() => {
    if (!renamingGroupId) return;
    groupRenameInputRef.current?.focus();
    groupRenameInputRef.current?.select();
  }, [renamingGroupId]);

  function closeTab(key) {
    const index = tabs.findIndex((tab) => tab.key === key);
    const remaining = tabs.filter((tab) => tab.key !== key);
    setTabs(remaining);
    if (activeKey === key) {
      setActiveKey(remaining[Math.min(index, remaining.length - 1)]?.key ?? null);
    }
    setGroupMenu(null);
    setTabMenu(null);
    setRenamingKey((current) => (current === key ? null : current));
  }

  function registerLayoutElement(key, element) {
    if (element) {
      layoutElementsRef.current.set(key, element);
    } else {
      layoutElementsRef.current.delete(key);
    }
  }

  function addGroup() {
    const number = nextGroupRef.current;
    nextGroupRef.current += 1;
    const id = `custom-group-${number}-${Date.now()}`;
    setGroupDefinitions((current) => [
      ...current,
      {
        id,
        keepEmpty: true,
        name: terminalGroupName(number),
        projectPath: projectRoot,
      },
    ]);
    setCollapsedGroups((current) => ({ ...current, [id]: false }));
    setGroupMenu(null);
  }

  function beginGroupRename(group) {
    groupRenameCanceledRef.current = false;
    setGroupRenameDraft(group.name);
    setRenamingGroupId(group.id);
    setGroupMenu(null);
  }

  function commitGroupRename(group) {
    if (groupRenameCanceledRef.current) {
      groupRenameCanceledRef.current = false;
      setRenamingGroupId(null);
      return;
    }
    const name = groupRenameDraft.trim();
    if (name) {
      setGroupDefinitions((current) => upsertTerminalGroupDefinition(current, {
        id: group.id,
        keepEmpty: group.items.length === 0,
        name,
        projectPath: group.projectPath,
      }));
    }
    setRenamingGroupId(null);
  }

  function removeGroup(groupId) {
    const remaining = terminalTabsAfterDeletingGroup(tabs, groupId);
    setTabs(remaining);
    if (!remaining.some((tab) => tab.key === activeKey)) {
      setActiveKey(remaining[0]?.key ?? null);
    }
    setGroupDefinitions((current) => current.filter((group) => group.id !== groupId));
    setCollapsedGroups((current) => {
      const next = { ...current };
      delete next[groupId];
      return next;
    });
    setGroupMenu(null);
    setRenamingGroupId((current) => (current === groupId ? null : current));
  }

  function moveTerminalToGroup(key, targetGroupId) {
    setTabs((current) => moveTerminalTabToGroup(current, key, targetGroupId));
    setGroupDefinitions((current) => current.map((group) => (
      group.id === targetGroupId ? { ...group, keepEmpty: false } : group
    )));
    setCollapsedGroups((current) => ({ ...current, [targetGroupId]: false }));
    setDraggedKey(null);
    setDragOverGroupId(null);
  }

  function openTabMenu(event, key) {
    event.preventDefault();
    event.stopPropagation();
    menuTriggerRef.current = menuTrigger(event);
    setActiveKey(key);
    setGroupMenu(null);
    setTabMenu({
      key,
      left: Math.max(8, Math.min(event.clientX, window.innerWidth - 176)),
      top: Math.max(8, Math.min(event.clientY, window.innerHeight - 84)),
    });
  }

  function openGroupMenu(event, group) {
    event.preventDefault();
    event.stopPropagation();
    menuTriggerRef.current = menuTrigger(event);
    setTabMenu(null);
    setGroupMenu({
      id: group.id,
      itemCount: group.items.length,
      left: Math.max(8, Math.min(event.clientX, window.innerWidth - 176)),
      name: group.name,
      projectPath: group.projectPath,
      top: Math.max(8, Math.min(event.clientY, window.innerHeight - 160)),
    });
  }

  function openTerminalListMenu(event) {
    if (event.target !== event.currentTarget) return;
    event.preventDefault();
    menuTriggerRef.current = menuTrigger(event);
    setTabMenu(null);
    setGroupMenu({
      id: null,
      left: Math.max(8, Math.min(event.clientX, window.innerWidth - 176)),
      name: "Terminals",
      projectPath: projectRoot,
      top: Math.max(8, Math.min(event.clientY, window.innerHeight - 56)),
    });
  }

  // Escape in either menu closes it and puts focus back where the menu was opened.
  function closeMenu(options) {
    setGroupMenu(null);
    setTabMenu(null);
    if (options?.restoreFocus) menuTriggerRef.current?.focus?.();
  }

  function beginRename(key) {
    const tab = tabs.find((item) => item.key === key);
    if (!tab) return;
    renameCanceledRef.current = false;
    setRenameDraft(tab.label);
    setRenamingKey(key);
    setTabMenu(null);
  }

  function commitRename(key) {
    if (renameCanceledRef.current) {
      renameCanceledRef.current = false;
      setRenamingKey(null);
      return;
    }
    const label = renameDraft.trim();
    setTabs((current) => current.map((tab) => (
      tab.key === key && label ? { ...tab, customName: true, label } : tab
    )));
    setRenamingKey(null);
  }

  function handleWorkspaceKeyDown(event) {
    const action = terminalWorkspaceShortcutAction(event, {
      active,
      activeKey,
      renaming: event.target?.classList?.contains("terminal-rename-input"),
      settings,
    });
    if (!action) return;
    event.preventDefault();
    event.stopPropagation();
    if (action === "new") {
      addTerminal();
      return;
    }
    if (action === "find") {
      setFindRequest((current) => ({ key: activeKey, count: current.count + 1 }));
      return;
    }
    closeTab(activeKey);
  }

  return (
    <div data-terminal-workspace="true" className="flex h-full min-h-0 bg-white" onKeyDownCapture={handleWorkspaceKeyDown}>
      <div className="relative min-h-0 min-w-0 flex-1">
        {tabs.map((tab) => (
          <TerminalSession
            key={tab.key}
            active={active && activeKey === tab.key}
            findRequest={findRequest.key === tab.key ? findRequest.count : 0}
            projectRoot={tab.cwd}
            settings={settings}
            tabKey={tab.key}
            theme={theme}
            onCwdChange={applyWorkingDirectory}
            onLabelChange={applyShellLabel}
          />
        ))}
        {!tabs.length ? (
          <div className="grid h-full place-items-center">
            <button className="inline-flex h-8 items-center gap-2 rounded-md border border-line px-3 text-xs font-semibold text-ink hover:bg-slate-50" type="button" onClick={() => addTerminal()}>
              <Plus size={13} />New terminal
            </button>
          </div>
        ) : null}
      </div>

      <aside aria-label="Terminal tabs" className="flex w-52 shrink-0 flex-col border-l border-line bg-slate-50">
        <div className="flex h-8 shrink-0 items-center border-b border-line px-2">
          <span className="min-w-0 flex-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
            Terminals
          </span>
          <button
            aria-label="New terminal"
            className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted transition hover:bg-slate-200/70 hover:text-ink"
            title={`New terminal (${formatKeybinding(settingBinding(settings, "terminal.new"))})`}
            type="button"
            onClick={() => addTerminal()}
          >
            <Plus size={13} />
          </button>
        </div>
        <div
          className="workflow-scrollbar min-h-0 flex-1 overflow-y-auto py-1"
          onContextMenu={openTerminalListMenu}
        >
          {terminalGroups.map((group) => {
            const collapsed = Boolean(collapsedGroups[group.id]);
            return (
              <section
                ref={(element) => registerLayoutElement(`group:${group.id}`, element)}
                key={group.id}
                aria-label={`${group.name} terminals`}
                className={`mb-1 rounded transition-[background-color,box-shadow] duration-150 ${dragOverGroupId === group.id ? "bg-indigo-100/70 shadow-[inset_0_0_0_1px_rgb(99_102_241/0.45)] dark:bg-indigo-950/40" : ""}`}
                onDragEnter={() => {
                  if (!draggedKey || terminalTabGroupId(tabs.find((tab) => tab.key === draggedKey)) === group.id) return;
                  setDragOverGroupId(group.id);
                  if (collapsed) setCollapsedGroups((current) => ({ ...current, [group.id]: false }));
                }}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget)) setDragOverGroupId(null);
                }}
                onDragOver={(event) => {
                  if (!draggedKey || terminalTabGroupId(tabs.find((tab) => tab.key === draggedKey)) === group.id) return;
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "move";
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  const key = draggedKey || event.dataTransfer.getData("text/raticode-terminal");
                  if (key) moveTerminalToGroup(key, group.id);
                }}
              >
                <div
                  className="group/project flex h-7 items-center rounded px-1.5 transition hover:bg-slate-100"
                  title={`${group.name}\n${group.projectPath}\nRight-click for group actions`}
                  onContextMenu={(event) => openGroupMenu(event, group)}
                >
                  {renamingGroupId === group.id ? (
                    <input
                      ref={groupRenameInputRef}
                      aria-label={`Rename ${group.name} terminal group`}
                      className="terminal-rename-input mx-0.5 h-5 min-w-0 flex-1 rounded border border-brand bg-white px-1.5 text-[10px] font-semibold text-ink outline-none ring-1 ring-brand/20"
                      value={groupRenameDraft}
                      onBlur={() => commitGroupRename(group)}
                      onChange={(event) => setGroupRenameDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") event.currentTarget.blur();
                        if (event.key === "Escape") {
                          groupRenameCanceledRef.current = true;
                          event.currentTarget.blur();
                        }
                      }}
                    />
                  ) : (
                    <button
                      aria-expanded={!collapsed}
                      className="flex min-w-0 flex-1 items-center gap-1.5 text-left text-[10px] font-semibold text-ink"
                      type="button"
                      onClick={() => setCollapsedGroups((current) => ({
                        ...current,
                        [group.id]: !current[group.id],
                      }))}
                    >
                      <ChevronDown
                        aria-hidden="true"
                        className={`shrink-0 text-muted transition ${collapsed ? "-rotate-90" : ""}`}
                        size={11}
                      />
                      <FolderOpen aria-hidden="true" className="shrink-0 text-muted" size={11} />
                      <span className="min-w-0 flex-1 truncate">{group.name}</span>
                    </button>
                  )}
                  <span className="text-[9px] font-medium text-muted">{group.items.length}</span>
                </div>
                {!collapsed ? (
                  <div className="ml-3 border-l border-line py-0.5 pl-1">
                    {group.items.map((tab) => (
                      <div
                        ref={(element) => registerLayoutElement(`tab:${tab.key}`, element)}
                        key={tab.key}
                        aria-grabbed={draggedKey === tab.key}
                        className={`group flex h-7 min-w-0 items-center rounded transition-[opacity,background-color,color,box-shadow] duration-150 ${draggedKey === tab.key ? "opacity-40" : "opacity-100"} ${activeKey === tab.key ? "bg-indigo-50 text-indigo-700" : "text-muted hover:bg-slate-100 hover:text-ink"}`}
                        draggable={renamingKey !== tab.key}
                        onDragEnd={() => {
                          setDraggedKey(null);
                          setDragOverGroupId(null);
                        }}
                        onDragStart={(event) => {
                          event.dataTransfer.effectAllowed = "move";
                          event.dataTransfer.setData("text/raticode-terminal", tab.key);
                          setDraggedKey(tab.key);
                          setActiveKey(tab.key);
                        }}
                        onContextMenu={(event) => openTabMenu(event, tab.key)}
                      >
                        {renamingKey === tab.key ? (
                          <input
                            ref={renameInputRef}
                            aria-label={`Rename ${tab.label}`}
                            className="terminal-rename-input mx-1.5 h-5 min-w-0 flex-1 rounded border border-brand bg-white px-1.5 text-[11px] text-ink outline-none ring-1 ring-brand/20"
                            value={renameDraft}
                            onBlur={() => commitRename(tab.key)}
                            onChange={(event) => setRenameDraft(event.target.value)}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") event.currentTarget.blur();
                              if (event.key === "Escape") {
                                renameCanceledRef.current = true;
                                event.currentTarget.blur();
                              }
                            }}
                          />
                        ) : (
                          <button
                            className="flex min-w-0 flex-1 items-center gap-1.5 px-2 text-left text-[11px] font-medium"
                            title={`${tab.label} — ${tab.folderName}`}
                            type="button"
                            onClick={() => setActiveKey(tab.key)}
                            onDoubleClick={() => beginRename(tab.key)}
                          >
                            <TerminalIcon aria-hidden="true" className="shrink-0" size={11} />
                            <span className="min-w-0 flex-1 truncate">{tab.label}</span>
                            <span className="max-w-[4rem] shrink truncate text-[9px] font-normal text-muted">
                              {tab.folderName}
                            </span>
                          </button>
                        )}
                        <button
                          aria-label={`Close ${tab.label}`}
                          className="mr-1 grid h-5 w-5 shrink-0 place-items-center rounded text-muted opacity-0 transition hover:bg-slate-200 hover:text-ink focus:opacity-100 group-hover:opacity-100"
                          title={`Close terminal (${formatKeybinding(settingBinding(settings, "terminal.close"))})`}
                          type="button"
                          onClick={() => closeTab(tab.key)}
                        >
                          <X size={11} />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      </aside>

      {groupMenu ? (
        <PopupMenu
          menuRef={menuRef}
          aria-label={`${groupMenu.name} terminal group actions`}
          className="fixed z-[100] w-44 rounded-md border border-line bg-white p-1 text-[11px] text-ink shadow-lg"
          style={{ left: groupMenu.left, top: groupMenu.top }}
          onClose={closeMenu}
        >
          {groupMenu.id ? (
            <>
              <button
                className="flex h-7 w-full items-center gap-2 rounded px-2 text-left hover:bg-slate-100"
                role="menuitem"
                type="button"
                onClick={() => {
                  addTerminal(groupMenu.projectPath, groupMenu.id);
                  setGroupMenu(null);
                }}
              >
                <Plus size={12} />New terminal
              </button>
              <button
                className="flex h-7 w-full items-center gap-2 rounded px-2 text-left hover:bg-slate-100"
                role="menuitem"
                type="button"
                onClick={() => beginGroupRename(groupMenu)}
              >
                <Pencil size={12} />Rename group
              </button>
            </>
          ) : null}
          <button
            className="flex h-7 w-full items-center gap-2 rounded px-2 text-left hover:bg-slate-100"
            role="menuitem"
            type="button"
            onClick={addGroup}
          >
            <FolderPlus size={12} />Add new group
          </button>
          {groupMenu.id ? (
            <>
              <div className="my-1 border-t border-line" role="separator" />
              <button
                className="flex h-7 w-full items-center gap-2 rounded px-2 text-left text-red-700 hover:bg-red-50 dark:text-red-300"
                role="menuitem"
                type="button"
                onClick={() => removeGroup(groupMenu.id)}
              >
                <Trash2 size={12} />
                {groupMenu.itemCount ? "Delete group and terminals" : "Delete group"}
              </button>
              <p className="truncate px-2 pb-1 pt-1 font-mono text-[9px] text-muted" title={groupMenu.projectPath}>
                {groupMenu.projectPath}
              </p>
            </>
          ) : null}
        </PopupMenu>
      ) : null}

      {tabMenu ? (
        <PopupMenu
          menuRef={menuRef}
          aria-label={`${tabs.find((tab) => tab.key === tabMenu.key)?.label || "Terminal"} actions`}
          className="fixed z-[100] w-40 rounded-md border border-line bg-white p-1 text-[11px] text-ink shadow-lg"
          style={{ left: tabMenu.left, top: tabMenu.top }}
          onClose={closeMenu}
        >
          <button className="flex h-7 w-full items-center rounded px-2 text-left hover:bg-slate-100" role="menuitem" type="button" onClick={() => beginRename(tabMenu.key)}>
            Rename terminal
          </button>
          <button className="flex h-7 w-full items-center rounded px-2 text-left hover:bg-slate-100" role="menuitem" type="button" onClick={() => closeTab(tabMenu.key)}>
            Close terminal
          </button>
        </PopupMenu>
      ) : null}
    </div>
  );
}

function TerminalSession({ active, findRequest = 0, onCwdChange, onLabelChange, projectRoot, settings, tabKey, theme }) {
  const reduced = useReducedMotion();
  const reducedRef = useRef(reduced);
  reducedRef.current = reduced;
  const containerRef = useRef(null);
  const terminalRef = useRef(null);
  const fitAddonRef = useRef(null);
  const searchAddonRef = useRef(null);
  const themeRef = useRef(theme);
  themeRef.current = theme;
  const findInputRef = useRef(null);
  // The find bar: null while closed, otherwise its text and Match case.
  const [find, setFind] = useState(null);
  const findRef = useRef(find);
  findRef.current = find;
  // The addon's last count, { resultIndex, resultCount }, or null before a search.
  const [findResults, setFindResults] = useState(null);
  const [findFocusRequest, setFindFocusRequest] = useState(0);
  const sessionIdRef = useRef("");
  const activeRef = useRef(active);
  const initialThemeRef = useRef(theme);
  const initialSettingsRef = useRef(settings.terminal);
  // The shell and folder, named in what Ask Rem about selection attaches.
  const placeRef = useRef({ shell: "", cwd: projectRoot });
  const contextMenuRef = useRef(null);
  // Right-click menu: where it opens and the text that was selected then.
  const [contextMenu, setContextMenu] = useState(null);
  // The target of the link under the pointer, shown next to it.
  const [linkTip, setLinkTip] = useState(null);

  useEffect(() => {
    if (!active) setLinkTip(null);
  }, [active]);

  useEffect(() => {
    if (!contextMenu) return undefined;
    contextMenuRef.current?.querySelector("button:not(:disabled)")?.focus();
    const dismiss = (event) => { if (!contextMenuRef.current?.contains(event.target)) setContextMenu(null); };
    const escape = (event) => { if (event.key === "Escape") { setContextMenu(null); terminalRef.current?.focus(); } };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", escape);
    window.addEventListener("blur", dismiss);
    return () => {
      window.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("keydown", escape);
      window.removeEventListener("blur", dismiss);
    };
  }, [contextMenu]);

  function openContextMenu(event) {
    const terminal = terminalRef.current;
    if (!terminal) return;
    event.preventDefault();
    const width = 208;
    const height = 140;
    setContextMenu({
      left: Math.max(4, Math.min(event.clientX, window.innerWidth - width - 4)),
      top: Math.max(4, Math.min(event.clientY, window.innerHeight - height - 4)),
      selection: terminal.hasSelection() ? terminal.getSelection() : "",
    });
  }

  function runMenuAction(action) {
    const terminal = terminalRef.current;
    const selection = contextMenu?.selection ?? "";
    setContextMenu(null);
    if (!terminal) return;
    if (action === "copy" && selection) void navigator.clipboard?.writeText(selection).catch(() => {});
    else if (action === "copy") void copyTerminalSelection(terminal).catch(() => {});
    if (action === "select-all") terminal.selectAll();
    // Attaches the selection to Rem's message box; the person writes the question and sends it.
    if (action === "ask") askRemAboutTerminalSelection(selection, placeRef.current);
    if (action === "find") openFind(selection);
    else if (action !== "ask") terminal.focus();
  }

  // Runs the addon on the find bar's text. "type" keeps the current match while the text
  // grows, so each letter doesn't jump to the next one.
  function runFind(direction, state = findRef.current) {
    const addon = searchAddonRef.current;
    if (!addon || !state) return;
    if (!state.term) {
      addon.clearDecorations();
      terminalRef.current?.clearSelection();
      setFindResults(null);
      return;
    }
    const options = { caseSensitive: state.caseSensitive, decorations: terminalFindDecorations(themeRef.current) };
    if (direction === "previous") addon.findPrevious(state.term, options);
    else addon.findNext(state.term, { ...options, incremental: direction === "type" });
  }

  function updateFind(changes) {
    const next = { ...findRef.current, ...changes };
    // The addon saves new options before comparing them with the last ones, so only
    // clearing its cached search makes it recount after Match case changes.
    if (next.caseSensitive !== findRef.current?.caseSensitive) searchAddonRef.current?.clearDecorations();
    findRef.current = next;
    setFind(next);
    runFind("type", next);
  }

  // Opens the bar, or focuses it again when it's open, starting from a one-line selection.
  function openFind(selection) {
    const initial = terminalFindInitialTerm(selection);
    const next = { caseSensitive: false, term: "", ...findRef.current, ...(initial ? { term: initial } : {}) };
    findRef.current = next;
    setFind(next);
    setFindFocusRequest((value) => value + 1);
    if (initial) runFind("type", next);
  }

  function closeFind() {
    findRef.current = null;
    setFind(null);
    setFindResults(null);
    searchAddonRef.current?.clearDecorations();
    terminalRef.current?.focus();
  }

  const openFindRef = useRef(openFind);
  openFindRef.current = openFind;
  const runFindRef = useRef(runFind);
  runFindRef.current = runFind;
  useEffect(() => {
    if (!findRequest) return;
    const terminal = terminalRef.current;
    openFindRef.current(terminal?.hasSelection() ? terminal.getSelection() : "");
  }, [findRequest]);

  useEffect(() => {
    if (!findFocusRequest) return;
    findInputRef.current?.focus();
    findInputRef.current?.select();
  }, [findFocusRequest]);

  useEffect(() => {
    activeRef.current = active;
    if (!active) return;
    window.requestAnimationFrame(() => {
      fitAddonRef.current?.fit();
      terminalRef.current?.focus();
    });
  }, [active]);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) return;
    terminal.options.theme = terminalTheme(theme);
    // Redraw open find highlights in the new theme's colors.
    if (!findRef.current?.term) return;
    searchAddonRef.current?.clearDecorations();
    runFindRef.current("type");
  }, [theme]);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) return;
    terminal.options.cursorBlink = settings.terminal.cursorBlink && !reduced;
    terminal.options.fontSize = settings.terminal.fontSize;
    terminal.options.lineHeight = settings.terminal.lineHeight;
    terminal.options.scrollback = settings.terminal.scrollback;
    fitAddonRef.current?.fit();
  }, [settings.terminal, reduced]);

  useEffect(() => {
    const bridge = window.goferTerminal;
    const container = containerRef.current;
    if (!container) return undefined;

    const terminalSettings = initialSettingsRef.current;
    const platform = globalThis.navigator?.platform ?? "";
    // Opened the same way as a link in Rem's chat.
    const openUrl = (url) => { window.open(url, "_blank", "noopener,noreferrer"); };
    const showLinkTip = (event, text) => setLinkTip({ left: event.clientX, top: event.clientY, text });
    const hideLinkTip = () => setLinkTip(null);
    const terminal = new XTerm({
      // Find's highlights and match count use xterm's decorations, which it still marks
      // as proposed. Nothing the shell prints can call the API; only this code does.
      allowProposedApi: true,
      convertEol: true,
      cursorBlink: terminalSettings.cursorBlink && !reducedRef.current,
      cursorStyle: "block",
      fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
      fontSize: terminalSettings.fontSize,
      lineHeight: terminalSettings.lineHeight,
      linkHandler: createTerminalHyperlinkHandler({ onHover: showLinkTip, onLeave: hideLinkTip, openUrl, platform }),
      scrollback: terminalSettings.scrollback,
      theme: terminalTheme(initialThemeRef.current),
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    const searchAddon = new SearchAddon({ highlightLimit: TERMINAL_FIND_HIGHLIGHT_LIMIT });
    terminal.loadAddon(searchAddon);
    const findResultsDisposable = searchAddon.onDidChangeResults((results) => setFindResults(results));
    terminal.open(container);
    const missingFiles = window.goferDesktop?.workspace?.missingRecentFiles;
    const linkDisposable = terminal.registerLinkProvider(createTerminalLinkProvider(terminal, {
      existingFiles: missingFiles ? createFileExistenceCheck(missingFiles) : null,
      onHover: showLinkTip,
      onLeave: hideLinkTip,
      openFile: (target) => window.dispatchEvent(new CustomEvent(TERMINAL_OPEN_FILE_EVENT, { detail: target })),
      openUrl,
      place: () => ({ cwd: placeRef.current.cwd, projectRoot }),
      platform,
    }));
    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;
    searchAddonRef.current = searchAddon;
    fitAddon.fit();
    const cwdDisposable = terminal.parser.registerOscHandler(633, (data) => {
      const currentDirectory = terminalDirectoryFromOsc(data);
      if (!currentDirectory) return false;
      placeRef.current = { ...placeRef.current, cwd: currentDirectory };
      onCwdChange?.(tabKey, currentDirectory);
      return true;
    });

    if (!bridge) {
      terminal.writeln("\x1b[33mThe terminal is available in the Raticode desktop app.\x1b[0m");
      terminal.options.disableStdin = true;
      return () => {
        cwdDisposable.dispose();
        findResultsDisposable.dispose();
        linkDisposable.dispose();
        terminal.dispose();
        searchAddonRef.current = null;
      };
    }

    const unsubscribeData = bridge.onData((payload) => {
      if (payload?.id === sessionIdRef.current) terminal.write(payload.data ?? "");
    });
    const unsubscribeExit = bridge.onExit((payload) => {
      if (payload?.id !== sessionIdRef.current) return;
      terminal.writeln(`\r\n\x1b[90mProcess exited with code ${payload.exitCode ?? 0}.\x1b[0m`);
      terminal.options.disableStdin = true;
    });
    const inputDisposable = terminal.onData((data) => {
      if (sessionIdRef.current) void bridge.write(sessionIdRef.current, data).catch(() => {});
    });
    terminal.attachCustomKeyEventHandler((event) => {
      const clipboardDecision = handleTerminalClipboardShortcut(event, terminal);
      if (clipboardDecision !== null) return clipboardDecision;
      const wordEraseInput = terminalWordEraseInput(event);
      if (wordEraseInput !== null) {
        if (sessionIdRef.current) {
          void bridge.write(sessionIdRef.current, wordEraseInput).catch(() => {});
        }
        return false;
      }
      if (
        event.type !== "keydown"
        || !event.ctrlKey
        || event.metaKey
        || event.altKey
        || event.shiftKey
        || event.key.toLowerCase() !== "c"
        || terminal.hasSelection()
        || !sessionIdRef.current
      ) {
        return true;
      }
      void bridge.write(sessionIdRef.current, "\x03").catch(() => {});
      return false;
    });
    // Only tell the shell about real grid changes. Pixel-level resizes during a
    // panel drag would otherwise make the shell redraw its prompt every frame.
    let sentSize = "";
    const resizeSession = () => {
      const size = `${terminal.cols}x${terminal.rows}`;
      if (!sessionIdRef.current || size === sentSize) return;
      sentSize = size;
      void bridge.resize(sessionIdRef.current, terminal.cols, terminal.rows).catch(() => {});
    };
    const resizeObserver = new ResizeObserver(() => {
      if (!activeRef.current || !container.offsetWidth || !container.offsetHeight) return;
      fitAddon.fit();
      resizeSession();
    });
    resizeObserver.observe(container);

    const sessionLifecycle = createDisposableTerminalSession(bridge, {
      cols: terminal.cols,
      cwd: projectRoot,
      shell: terminalSettings.shell,
      rows: terminal.rows,
    }, {
      onError(error) {
        terminal.writeln(`\x1b[31mCould not start the terminal. ${error instanceof Error ? error.message : String(error)}\x1b[0m`);
        terminal.options.disableStdin = true;
      },
      onReady(session) {
        sessionIdRef.current = session.id;
        placeRef.current = { shell: session.shell || "", cwd: session.cwd || placeRef.current.cwd };
        onLabelChange?.(tabKey, session.shell);
        onCwdChange?.(tabKey, session.cwd);
        fitAddon.fit();
        resizeSession();
      },
    });

    return () => {
      sessionIdRef.current = "";
      resizeObserver.disconnect();
      inputDisposable.dispose();
      unsubscribeData();
      unsubscribeExit();
      cwdDisposable.dispose();
      findResultsDisposable.dispose();
      linkDisposable.dispose();
      terminal.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
      searchAddonRef.current = null;
      sessionLifecycle.dispose();
    };
  }, [onCwdChange, onLabelChange, projectRoot, tabKey]);

  const hasSelection = Boolean(contextMenu?.selection?.trim());
  const menuItem = "flex h-7 w-full items-center rounded px-2 text-left hover:bg-slate-100 focus-visible:bg-slate-100 focus-visible:outline-none disabled:cursor-default disabled:text-muted disabled:hover:bg-transparent";
  const findButton = "grid h-6 w-6 shrink-0 place-items-center rounded text-muted transition hover:bg-slate-100 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent";
  const findShortcut = formatKeybinding(settingBinding(settings, "terminal.find"));
  return (
    <>
      <div
        ref={containerRef}
        className={`terminal-host absolute inset-0 px-2 py-1 ${active ? "" : "invisible"}`}
        onContextMenu={openContextMenu}
        onPointerDown={(event) => { if (event.button === 0) terminalRef.current?.focus(); }}
      />
      {find && active ? (
        <div
          aria-label="Find in terminal"
          className="absolute right-3 top-1.5 z-10 flex items-center gap-0.5 rounded-md border border-line bg-white p-1 text-[11px] text-ink shadow-md"
          role="search"
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            event.stopPropagation();
            closeFind();
          }}
        >
          <input
            ref={findInputRef}
            aria-label="Find in terminal"
            className="h-6 w-44 rounded border border-line bg-white px-1.5 text-[11px] text-ink outline-none focus:border-brand focus:ring-1 focus:ring-brand/20"
            placeholder="Find"
            spellCheck={false}
            type="text"
            value={find.term}
            onChange={(event) => updateFind({ term: event.target.value })}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              event.preventDefault();
              runFind(event.shiftKey ? "previous" : "next");
            }}
          />
          <button
            aria-label="Match case"
            aria-pressed={find.caseSensitive}
            className={`${findButton} ${find.caseSensitive ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-300 hover:bg-indigo-50 hover:text-indigo-700" : ""}`}
            title="Match case"
            type="button"
            onClick={() => updateFind({ caseSensitive: !find.caseSensitive })}
          >
            <CaseSensitive aria-hidden="true" size={15} />
          </button>
          <span aria-live="polite" className="min-w-[4.75rem] px-1 text-center text-[10px] tabular-nums text-muted">
            {terminalFindCountText(findResults, find.term)}
          </span>
          <button aria-label="Previous match" className={findButton} disabled={!find.term} title="Previous match (Shift+Enter)" type="button" onClick={() => runFind("previous")}>
            <ChevronUp aria-hidden="true" size={14} />
          </button>
          <button aria-label="Next match" className={findButton} disabled={!find.term} title="Next match (Enter)" type="button" onClick={() => runFind("next")}>
            <ChevronDown aria-hidden="true" size={14} />
          </button>
          <button aria-label="Close find" className={findButton} title="Close (Escape)" type="button" onClick={closeFind}>
            <X aria-hidden="true" size={13} />
          </button>
        </div>
      ) : null}
      {linkTip && active ? (
        <div
          className="pointer-events-none fixed z-[100] max-w-md whitespace-pre-line break-all rounded-md border border-line bg-white px-2 py-1 text-[11px] text-ink shadow-lg"
          role="tooltip"
          style={{ left: Math.max(4, Math.min(linkTip.left + 12, window.innerWidth - 320)), top: Math.max(4, Math.min(linkTip.top + 16, window.innerHeight - 56)) }}
        >
          {linkTip.text}
        </div>
      ) : null}
      {contextMenu && active ? (
        <div
          ref={contextMenuRef}
          aria-label="Terminal actions"
          className="fixed z-[100] w-52 rounded-md border border-line bg-white p-1 text-[11px] text-ink shadow-lg"
          role="menu"
          style={{ left: contextMenu.left, top: contextMenu.top }}
          onKeyDown={(event) => {
            if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
            event.preventDefault();
            const items = [...event.currentTarget.querySelectorAll("button:not(:disabled)")];
            const index = items.indexOf(document.activeElement);
            items[(index + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
          }}
        >
          <button className={menuItem} disabled={!hasSelection} role="menuitem" type="button" onClick={() => runMenuAction("copy")}>Copy</button>
          <button className={menuItem} role="menuitem" type="button" onClick={() => runMenuAction("select-all")}>Select all</button>
          <button className={`${menuItem} justify-between gap-3`} role="menuitem" type="button" onClick={() => runMenuAction("find")}>
            <span>Find</span>
            <span className="text-[10px] text-muted">{findShortcut}</span>
          </button>
          <div className="my-1 border-t border-line" role="separator" />
          <button className={menuItem} disabled={!hasSelection} role="menuitem" title={hasSelection ? "Attach the selected text to Rem's message box. Nothing is sent until you send it." : "Select text in the terminal first"} type="button" onClick={() => runMenuAction("ask")}>Ask Rem about selection</button>
        </div>
      ) : null}
    </>
  );
}

// The addon takes only #RRGGBB colors. Matches are amber so they stand apart from the
// blue selection that marks the current match.
function terminalFindDecorations(theme) {
  return theme === "dark"
    ? { activeMatchBackground: "#a16207", activeMatchColorOverviewRuler: "#facc15", matchBackground: "#4d3b12", matchOverviewRuler: "#a16207" }
    : { activeMatchBackground: "#e9b949", activeMatchColorOverviewRuler: "#b7791f", matchBackground: "#f3e2b0", matchOverviewRuler: "#d6a93a" };
}

function terminalTheme(theme) {
  if (theme === "dark") {
    return {
      background: "#18181a",
      black: "#27272a",
      blue: "#818cf8",
      brightBlack: "#71717a",
      brightBlue: "#a5b4fc",
      brightCyan: "#67e8f9",
      brightGreen: "#86efac",
      brightMagenta: "#d8b4fe",
      brightRed: "#fca5a5",
      brightWhite: "#fafafa",
      brightYellow: "#fde68a",
      cursor: "#a5b4fc",
      cyan: "#22d3ee",
      foreground: "#d4d4d8",
      green: "#4ade80",
      magenta: "#c084fc",
      red: "#f87171",
      selectionBackground: "#4338ca88",
      white: "#e4e4e7",
      yellow: "#facc15",
    };
  }
  return {
    background: "#eee9df",
    foreground: "#30343b",
    cursor: "#4f46a5",
    cursorAccent: "#eee9df",
    selectionBackground: "#c4ccf4",
    black: "#30343b",
    red: "#a33343",
    green: "#356344",
    yellow: "#805b1a",
    blue: "#4545a0",
    magenta: "#80508d",
    cyan: "#276770",
    white: "#eee9df",
    brightBlack: "#62635f",
    brightRed: "#ae3547",
    brightGreen: "#3b6b49",
    brightYellow: "#876022",
    brightBlue: "#514bb2",
    brightMagenta: "#885295",
    brightCyan: "#2b6b78",
    brightWhite: "#f4f0e7",
  };
}

