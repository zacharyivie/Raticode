import { useEffect, useId, useRef } from "react";

const dialogStack = [];

function focusableElements(root) {
  if (!root) return [];
  const selector = [
    "a[href]",
    "button:not([disabled])",
    "input:not([disabled])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");
  const matches = root.querySelectorAll
    ? [...root.querySelectorAll(selector)]
    : descendantElements(root).filter(isFocusableElement);
  return matches.filter(
    (element) => !element.hidden && element.getAttribute("aria-hidden") !== "true",
  );
}

function descendantElements(root) {
  const descendants = [];
  for (const child of root.childNodes ?? []) {
    if (child.nodeType !== 1) continue;
    descendants.push(child, ...descendantElements(child));
  }
  return descendants;
}

function isFocusableElement(element) {
  if (element.disabled || element.getAttribute("hidden") !== null) return false;
  if (element.getAttribute("tabindex") === "-1") return false;
  if (element.getAttribute("tabindex") !== null) return true;
  if (["BUTTON", "INPUT", "SELECT", "TEXTAREA"].includes(element.tagName)) return true;
  return element.tagName === "A" && element.getAttribute("href") !== null;
}

export function Dialog({
  children,
  description,
  initialFocusRef,
  onClose,
  overlayClassName = "fixed inset-0 z-50 grid place-items-center ui-scrim px-4",
  panelClassName = "",
  panelProps = {},
  title,
}) {
  const generatedId = useId();
  const panelRef = useRef(null);
  const openerRef = useRef(typeof document === "undefined" ? null : document.activeElement);
  const onCloseRef = useRef(onClose);
  const stackEntryRef = useRef({});
  onCloseRef.current = onClose;
  const titleId = `${generatedId}-title`;
  const descriptionId = description ? `${generatedId}-description` : undefined;

  useEffect(() => {
    const panel = panelRef.current;
    const opener = openerRef.current;
    const stackEntry = stackEntryRef.current;
    dialogStack.push(stackEntry);

    const initialFocus =
      initialFocusRef?.current ??
      panel?.querySelector?.("[data-dialog-initial-focus]") ??
      (panel?.contains(document.activeElement) ? document.activeElement : null) ??
      focusableElements(panel)[0] ??
      panel;
    initialFocus?.focus();

    function isTopDialog() {
      return dialogStack.at(-1) === stackEntry;
    }

    function handleKeyDown(event) {
      if (!isTopDialog()) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = focusableElements(panel);
      if (!focusable.length) {
        event.preventDefault();
        panel?.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    function keepFocusInside(event) {
      if (!isTopDialog() || panel?.contains(event.target)) return;
      (focusableElements(panel)[0] ?? panel)?.focus();
    }

    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("focusin", keepFocusInside);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("focusin", keepFocusInside);
      const stackIndex = dialogStack.lastIndexOf(stackEntry);
      if (stackIndex >= 0) dialogStack.splice(stackIndex, 1);
      if (opener?.isConnected !== false) opener?.focus?.();
    };
  }, [initialFocusRef]);

  return (
    <div className={overlayClassName}>
      <div
        {...panelProps}
        ref={panelRef}
        aria-describedby={descriptionId}
        aria-labelledby={titleId}
        aria-modal="true"
        className={panelClassName}
        role="dialog"
        tabIndex={-1}
      >
        <span className="sr-only" id={titleId}>{title}</span>
        {description ? <span className="sr-only" id={descriptionId}>{description}</span> : null}
        {children}
      </div>
    </div>
  );
}
