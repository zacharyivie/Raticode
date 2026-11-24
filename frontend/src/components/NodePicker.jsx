import { useEffect, useId, useMemo, useRef, useState } from "react";
import { BookOpen, Search } from "lucide-react";
import { groupNodeCatalog, nodeCatalogEntries } from "../lib/nodeCatalog.js";

// Searchable list of node types, grouped by what they do. The list, the one-line
// descriptions and the docs links all come from the Rattish contracts in the workflow
// document, so a new node type appears here without a UI change.
export function NodePicker({
  className = "",
  contracts,
  widthClass = "w-[23rem] max-w-[calc(100vw-2rem)]",
  sizeClass = "max-h-[min(30rem,72vh)] shadow-panel",
  currentType,
  label = "Add node",
  placeholder = "Search nodes",
  onClose,
  onPick,
}) {
  const baseId = useId();
  const rootRef = useRef(null);
  const inputRef = useRef(null);
  const listRef = useRef(null);
  const [query, setQuery] = useState("");
  const entries = useMemo(() => nodeCatalogEntries(contracts), [contracts]);
  const groups = useMemo(() => groupNodeCatalog(entries, query), [entries, query]);
  const visible = useMemo(() => groups.flatMap((group) => group.entries), [groups]);
  const [activeType, setActiveType] = useState(currentType ?? null);
  const active =
    visible.find((entry) => entry.nodeType === activeType) ?? visible[0] ?? null;
  const optionId = (nodeType) => `${baseId}-option-${nodeType}`;
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    function closeOnOutsidePointer(event) {
      const root = rootRef.current;
      if (root && event.target && !root.contains(event.target)) onCloseRef.current?.();
    }
    document.addEventListener("pointerdown", closeOnOutsidePointer, true);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer, true);
  }, []);

  const activeNodeType = active?.nodeType;
  useEffect(() => {
    if (!activeNodeType) return;
    const element = globalThis.document?.getElementById?.(`${baseId}-option-${activeNodeType}`);
    element?.scrollIntoView?.({ block: "nearest" });
  }, [activeNodeType, baseId]);

  function move(step) {
    if (!visible.length) return;
    const index = active ? visible.indexOf(active) : -1;
    const next = visible[(index + step + visible.length) % visible.length];
    setActiveType(next.nodeType);
  }

  function handleKeyDown(event) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      move(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Home" && visible.length && !query) {
      event.preventDefault();
      setActiveType(visible[0].nodeType);
    } else if (event.key === "End" && visible.length && !query) {
      event.preventDefault();
      setActiveType(visible.at(-1).nodeType);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (active) onPick?.(active.nodeType);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation?.();
      onClose?.();
    }
  }

  return (
    <div
      ref={rootRef}
      aria-label={label}
      className={`node-picker flex ${sizeClass} ${widthClass} flex-col overflow-hidden rounded-[10px] border border-line bg-white ${className}`}
      data-node-picker="true"
      role="dialog"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation?.();
          onClose?.();
        }
      }}
    >
      <div className="node-picker-search-row flex items-center gap-2 border-b border-line px-3 py-2">
        <Search aria-hidden="true" className="shrink-0 text-muted" size={15} />
        <input
          ref={inputRef}
          aria-activedescendant={active ? optionId(active.nodeType) : undefined}
          aria-autocomplete="list"
          aria-controls={`${baseId}-list`}
          aria-expanded="true"
          aria-label={placeholder}
          className="node-picker-search min-w-0 flex-1 border-0 bg-transparent p-0 text-sm text-ink outline-none placeholder:text-muted focus:ring-0"
          placeholder={placeholder}
          role="combobox"
          spellCheck={false}
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleKeyDown}
        />
      </div>
      <div
        ref={listRef}
        aria-label="Node types"
        className="workflow-scrollbar min-h-0 flex-1 overflow-y-auto p-1"
        id={`${baseId}-list`}
        role="listbox"
      >
        {groups.length ? (
          groups.map((group) => (
            <div key={group.id} aria-labelledby={`${baseId}-group-${group.id}`} role="group">
              <div
                className="px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted"
                id={`${baseId}-group-${group.id}`}
              >
                {group.label}
              </div>
              {group.entries.map((entry) => {
                const isActive = active?.nodeType === entry.nodeType;
                const isCurrent = entry.nodeType === currentType;
                return (
                  <div
                    key={entry.nodeType}
                    className={`group/node-option flex items-center gap-1 rounded-lg ${
                      isActive ? "bg-indigo-50 dark:bg-indigo-500/15" : ""
                    }`}
                    role="presentation"
                  >
                    <div
                      aria-selected={isActive}
                      className="min-w-0 flex-1 cursor-pointer px-2 py-1.5"
                      data-node-type={entry.nodeType}
                      id={optionId(entry.nodeType)}
                      role="option"
                      onClick={() => onPick?.(entry.nodeType)}
                      onPointerMove={() => {
                        if (!isActive) setActiveType(entry.nodeType);
                      }}
                    >
                      <div className="flex items-baseline gap-2">
                        <span className="truncate text-xs font-semibold text-ink">{entry.label}</span>
                        <span className="truncate font-mono text-[10px] text-muted">{entry.nodeType}</span>
                        {isCurrent ? (
                          <span className="ml-auto shrink-0 text-[10px] font-medium text-indigo-700">
                            Current
                          </span>
                        ) : null}
                      </div>
                      {entry.summary ? (
                        <div className="truncate text-[11px] leading-4 text-muted" title={entry.summary}>
                          {entry.summary}
                        </div>
                      ) : null}
                    </div>
                    <a
                      aria-label={`${entry.label} docs`}
                      className={`mr-1 grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted transition hover:bg-white hover:text-indigo-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600 ${
                        isActive ? "opacity-100" : "opacity-60 group-hover/node-option:opacity-100"
                      }`}
                      href={entry.docsUrl}
                      rel="noreferrer"
                      target="_blank"
                      title={`Open the ${entry.label} docs`}
                    >
                      <BookOpen aria-hidden="true" size={14} />
                    </a>
                  </div>
                );
              })}
            </div>
          ))
        ) : (
          <p className="px-3 py-6 text-center text-xs text-muted">
            No node matches &ldquo;{query.trim()}&rdquo;.
          </p>
        )}
      </div>
      <div className="flex items-center gap-3 border-t border-line px-3 py-1.5 text-[10px] text-muted">
        <span>
          <kbd className="font-sans">↑</kbd> <kbd className="font-sans">↓</kbd> choose
        </span>
        <span>
          <kbd className="font-sans">Enter</kbd> {currentType ? "change" : "add"}
        </span>
        <span>
          <kbd className="font-sans">Esc</kbd> close
        </span>
      </div>
    </div>
  );
}
