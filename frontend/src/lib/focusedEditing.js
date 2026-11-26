// The text field the Edit and Selection menu commands act on: the one that had focus
// before a menu took it. Focus moving to anything else outside the menus forgets it, so
// Edit > Undo with the graph in front undoes a graph edit, not typing left long ago.
let editingTarget = null;

function isTextField(target) {
  return Boolean(target?.isContentEditable || ["INPUT", "TEXTAREA"].includes(target?.tagName)
    || target?.getAttribute?.("contenteditable") === "true");
}

function insideMenu(target) {
  for (let node = target; node; node = node.parentNode) {
    if (node.getAttribute?.("role") === "menu" || node.getAttribute?.("aria-label") === "Application menu") return true;
  }
  return false;
}

export function rememberEditingTarget(target) {
  if (insideMenu(target)) return;
  editingTarget = isTextField(target) ? target : null;
}
if (typeof document !== "undefined") document.addEventListener("focusin", event => rememberEditingTarget(event.target));

// A text field, including the code editor's, is still the one Edit commands act on.
export function hasEditingTarget() {
  return Boolean(editingTarget?.isConnected);
}

export function runFocusedEdit(action) {
  const target = editingTarget;
  // Monaco owns its hidden textarea; use the editor dispatcher for its commands.
  if (!target?.isConnected || target.closest?.(".monaco-editor")) return false;
  target.focus();
  const method = { "edit.undo": "undo", "edit.redo": "redo", "edit.cut": "cut", "edit.copy": "copy", "edit.paste": "paste", "selection.selectAll": "selectAll" }[action];
  if (!method) return false;
  if (window.goferDesktop?.editFocused) void window.goferDesktop.editFocused(method).catch(error => {
    target.dispatchEvent(new CustomEvent("gofer:edit-error", { bubbles: true, detail: error.message }));
  });
  else document.execCommand(method === "selectAll" ? "selectAll" : method);
  return true;
}
