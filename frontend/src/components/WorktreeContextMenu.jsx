import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export const integrationOperations = [["merge", "Merge"], ["squash", "Squash merge"], ["ff-only", "Fast-forward only"], ["no-ff", "Merge with a merge commit"], ["rebase", "Rebase"]];
export const historyOperations = [["commit-diff", "Open commit diff"], ["reset-soft", "Soft reset"], ["reset-hard", "Hard reset"], ["branch-commit", "Checkout new branch here"], ["worktree-commit", "Create worktree here"], ["detach-commit", "Detach at commit"]];

export default function WorktreeContextMenu({ source, x, y, trigger, disabled, onClose, onSelect, operations = integrationOperations }) {
  const [keyboardNavigation, setKeyboardNavigation] = useState(false);
  const menuRef = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const width = Math.min(240, window.innerWidth - 16);
  const height = Math.min(operations.length * 32 + 8, window.innerHeight - 16);
  useEffect(() => {
    menuRef.current?.querySelector("button:not(:disabled)")?.focus();
    const dismiss = event => { if (!menuRef.current?.contains(event.target)) closeRef.current(); };
    const resize = () => closeRef.current();
    document.addEventListener("pointerdown", dismiss);
    window.addEventListener("resize", resize);
    return () => { document.removeEventListener("pointerdown", dismiss); window.removeEventListener("resize", resize); };
  }, []);
  function navigate(event) {
    setKeyboardNavigation(true);
    if (event.key === "Escape") { event.preventDefault(); onClose(); trigger?.focus(); return; }
    if (event.key === "Tab") { onClose(); return; }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const items = Array.from(menuRef.current.querySelectorAll("button:not(:disabled)"));
    const index = items.indexOf(event.target);
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }
  return createPortal(<div ref={menuRef} data-keyboard-navigation={keyboardNavigation} onPointerMove={() => setKeyboardNavigation(false)} className="fixed z-[90]" style={{ left: Math.max(8, Math.min(x, window.innerWidth - width - 8)), top: Math.max(8, Math.min(y, window.innerHeight - height - 8)), width }} onKeyDown={navigate} onContextMenu={event => event.preventDefault()}>
    <div role="menu" aria-label={`Actions for ${source}`} className="overflow-y-auto rounded-lg border border-line bg-white p-1 shadow-panel" style={{ maxHeight: window.innerHeight - 16 }}>
      {operations.map(([kind, label, unavailable]) => <button key={kind} type="button" role="menuitem" data-operation={kind} className="worktree-menu-item flex min-h-8 w-full items-center rounded px-2 text-left text-xs text-ink disabled:opacity-40" disabled={disabled || unavailable} onMouseEnter={event => { setKeyboardNavigation(false); event.currentTarget.focus(); }} onClick={() => { onSelect(kind); trigger?.focus(); }}>{label}</button>)}
    </div>
  </div>, document.body);
}
