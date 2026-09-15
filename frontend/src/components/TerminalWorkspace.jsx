import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import * as fitAddonPackage from "@xterm/addon-fit";
import * as xtermPackage from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { ChevronDown, FolderPlus, FolderOpen, Pencil, Plus, Terminal as TerminalIcon, Trash2, X } from "lucide-react";


const { FitAddon } = fitAddonPackage;
const { Terminal: XTerm } = xtermPackage;

import { projectFolderName, terminalDirectoryFromOsc, terminalProjectGroupId, terminalGroupName, terminalTabGroupId, moveTerminalTabToGroup, terminalTabsAfterDeletingGroup, upsertTerminalGroupDefinition, groupTerminalTabsByProject, shouldCreateInitialTerminal, handleTerminalClipboardShortcut, terminalWordEraseInput, createDisposableTerminalSession, terminalWorkspaceShortcutAction } from "../lib/terminalWorkspace.js";
export default function TerminalWorkspace({ active, projectRoot, settings, theme }) {
  const nextTabRef = useRef(1);
  const nextGroupRef = useRef(1);
  const initialTerminalCreatedRef = useRef(false);
  const layoutElementsRef = useRef(new Map());
  const layoutRectsRef = useRef(new Map());
  const menuRef = useRef(null);
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
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
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
  }, [collapsedGroups, groupDefinitions, tabs]);

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
    setTabMenu(null);
    setGroupMenu({
      id: null,
      left: Math.max(8, Math.min(event.clientX, window.innerWidth - 176)),
      name: "Terminals",
      projectPath: projectRoot,
      top: Math.max(8, Math.min(event.clientY, window.innerHeight - 56)),
    });
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
    closeTab(activeKey);
  }

  return (
    <div data-terminal-workspace="true" className="flex h-full min-h-0 bg-white" onKeyDownCapture={handleWorkspaceKeyDown}>
      <div className="relative min-h-0 min-w-0 flex-1">
        {tabs.map((tab) => (
          <TerminalSession
            key={tab.key}
            active={active && activeKey === tab.key}
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
            title="New terminal (Ctrl+T)"
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
                          title="Close terminal (Ctrl+W)"
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
        <div
          ref={menuRef}
          aria-label={`${groupMenu.name} terminal group actions`}
          className="fixed z-[100] w-44 rounded-md border border-line bg-white p-1 text-[11px] text-ink shadow-lg"
          role="menu"
          style={{ left: groupMenu.left, top: groupMenu.top }}
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
        </div>
      ) : null}

      {tabMenu ? (
        <div
          ref={menuRef}
          className="fixed z-[100] w-40 rounded-md border border-line bg-white p-1 text-[11px] text-ink shadow-lg"
          role="menu"
          style={{ left: tabMenu.left, top: tabMenu.top }}
        >
          <button className="flex h-7 w-full items-center rounded px-2 text-left hover:bg-slate-100" role="menuitem" type="button" onClick={() => beginRename(tabMenu.key)}>
            Rename terminal
          </button>
          <button className="flex h-7 w-full items-center rounded px-2 text-left hover:bg-slate-100" role="menuitem" type="button" onClick={() => closeTab(tabMenu.key)}>
            Close terminal
          </button>
        </div>
      ) : null}
    </div>
  );
}

function TerminalSession({ active, onCwdChange, onLabelChange, projectRoot, settings, tabKey, theme }) {
  const containerRef = useRef(null);
  const terminalRef = useRef(null);
  const fitAddonRef = useRef(null);
  const sessionIdRef = useRef("");
  const activeRef = useRef(active);
  const initialThemeRef = useRef(theme);
  const initialSettingsRef = useRef(settings.terminal);

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
  }, [theme]);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) return;
    terminal.options.cursorBlink = settings.terminal.cursorBlink;
    terminal.options.fontSize = settings.terminal.fontSize;
    terminal.options.lineHeight = settings.terminal.lineHeight;
    terminal.options.scrollback = settings.terminal.scrollback;
    fitAddonRef.current?.fit();
  }, [settings.terminal]);

  useEffect(() => {
    const bridge = window.goferTerminal;
    const container = containerRef.current;
    if (!container) return undefined;

    const terminalSettings = initialSettingsRef.current;
    const terminal = new XTerm({
      allowProposedApi: false,
      convertEol: true,
      cursorBlink: terminalSettings.cursorBlink,
      cursorStyle: "block",
      fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
      fontSize: terminalSettings.fontSize,
      lineHeight: terminalSettings.lineHeight,
      scrollback: terminalSettings.scrollback,
      theme: terminalTheme(initialThemeRef.current),
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(container);
    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;
    fitAddon.fit();
    const cwdDisposable = terminal.parser.registerOscHandler(633, (data) => {
      const currentDirectory = terminalDirectoryFromOsc(data);
      if (!currentDirectory) return false;
      onCwdChange?.(tabKey, currentDirectory);
      return true;
    });

    if (!bridge) {
      terminal.writeln("\x1b[33mThe terminal is available in the Raticode desktop app.\x1b[0m");
      terminal.options.disableStdin = true;
      return () => {
        cwdDisposable.dispose();
        terminal.dispose();
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
    const resizeObserver = new ResizeObserver(() => {
      if (!activeRef.current || !container.offsetWidth || !container.offsetHeight) return;
      fitAddon.fit();
      if (sessionIdRef.current) {
        void bridge.resize(sessionIdRef.current, terminal.cols, terminal.rows).catch(() => {});
      }
    });
    resizeObserver.observe(container);

    const sessionLifecycle = createDisposableTerminalSession(bridge, {
      cols: terminal.cols,
      cwd: projectRoot,
      rows: terminal.rows,
    }, {
      onError(error) {
        terminal.writeln(`\x1b[31mCould not start the terminal. ${error instanceof Error ? error.message : String(error)}\x1b[0m`);
        terminal.options.disableStdin = true;
      },
      onReady(session) {
        sessionIdRef.current = session.id;
        onLabelChange?.(tabKey, session.shell);
        onCwdChange?.(tabKey, session.cwd);
        fitAddon.fit();
        void bridge.resize(session.id, terminal.cols, terminal.rows).catch(() => {});
      },
    });

    return () => {
      sessionIdRef.current = "";
      resizeObserver.disconnect();
      inputDisposable.dispose();
      unsubscribeData();
      unsubscribeExit();
      cwdDisposable.dispose();
      terminal.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
      sessionLifecycle.dispose();
    };
  }, [onCwdChange, onLabelChange, projectRoot, tabKey]);

  return (
    <div
      ref={containerRef}
      className={`terminal-host absolute inset-0 px-2 py-1 ${active ? "" : "invisible"}`}
      onPointerDown={() => terminalRef.current?.focus()}
    />
  );
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

