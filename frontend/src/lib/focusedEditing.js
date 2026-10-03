let editingTarget = null;
export function rememberEditingTarget(target) {
  if (target?.matches?.("input, textarea, [contenteditable=true]") && !target.closest("[role=menu], [aria-label='Application menu']")) editingTarget = target;
}
if (typeof document !== "undefined") document.addEventListener("focusin", event => rememberEditingTarget(event.target));

export function runFocusedEdit(action) {
  const target = editingTarget;
  // Monaco owns its hidden textarea; use the editor dispatcher for its commands.
  if (!target?.isConnected || target.closest(".monaco-editor")) return false;
  target.focus();
  const method = { "edit.undo": "undo", "edit.redo": "redo", "edit.cut": "cut", "edit.copy": "copy", "edit.paste": "paste", "selection.selectAll": "selectAll" }[action];
  if (!method) return false;
  if (window.goferDesktop?.editFocused) void window.goferDesktop.editFocused(method).catch(error => {
    target.dispatchEvent(new CustomEvent("gofer:edit-error", { bubbles: true, detail: error.message }));
  });
  else document.execCommand(method === "selectAll" ? "selectAll" : method);
  return true;
}
