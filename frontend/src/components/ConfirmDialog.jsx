import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Dialog } from "./Dialog.jsx";

// The modal form of the Organizations ConfirmButton: the same props and the same
// "Keep as is", so a question reads alike inline and in a dialog. The confirm
// button names the action, and the cancel button starts focused so Enter changes nothing.
export function ConfirmDialog({ label, confirmLabel, cancelLabel = "Keep as is", message, danger = false, onConfirm, onCancel }) {
  const messageId = useId();
  return (
    <Dialog
      onClose={onCancel}
      overlayClassName="fixed inset-0 z-[100] grid place-items-center ui-scrim px-4"
      panelClassName="ui-dialog w-full max-w-md p-4"
      panelProps={{ "aria-describedby": messageId, "data-testid": "confirm-dialog" }}
      title={`Confirm: ${label || confirmLabel}`}
    >
      <p id={messageId} className="whitespace-pre-line break-words text-sm text-strong">{message}</p>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className="ui-button" data-dialog-initial-focus onClick={onCancel}>{cancelLabel}</button>
        <button type="button" className={`ui-button ${danger ? "ui-button--danger" : "ui-button--primary"}`} onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </Dialog>
  );
}

// Asks in a ConfirmDialog and resolves true only when the user presses the
// confirm button. Render the returned element once in the component that asks:
//   const [confirm, confirmDialog] = useConfirm();
//   if (!await confirm({ message, confirmLabel: "Delete thread", danger: true })) return;
// A newer question replaces an open one, which then answers false.
export function useConfirm() {
  const [request, setRequest] = useState(null);
  const pendingRef = useRef(null);
  const countRef = useRef(0);

  const settle = useCallback((answer) => {
    const pending = pendingRef.current;
    if (!pending) return;
    pendingRef.current = null;
    setRequest(null);
    pending.resolve(answer);
  }, []);

  const confirm = useCallback((options) => new Promise((resolve) => {
    pendingRef.current?.resolve(false);
    countRef.current += 1;
    const next = { ...options, key: countRef.current, resolve };
    pendingRef.current = next;
    setRequest(next);
  }), []);

  // A question still open when the component goes away keeps things as they are.
  useEffect(() => () => {
    pendingRef.current?.resolve(false);
    pendingRef.current = null;
  }, []);

  const dialog = request
    ? <ConfirmDialog
      key={request.key}
      label={request.label}
      confirmLabel={request.confirmLabel}
      cancelLabel={request.cancelLabel}
      message={request.message}
      danger={request.danger}
      onConfirm={() => settle(true)}
      onCancel={() => settle(false)}
    />
    : null;
  return [confirm, dialog];
}

// Keeps what someone typed in a dialog. A press outside closes the dialog only while
// nothing in it changed, and only when the press starts and ends on the overlay, so a
// text selection dragged out of the panel never closes it. Escape (`requestClose`) on a
// changed dialog asks first, with "Keep editing" focused. Spread `outsidePress` on the
// overlay and render `confirmDialog` outside the panel, so its keys don't reach the panel.
export function useDiscardGuard({ changed, onClose, message }) {
  const [confirm, confirmDialog] = useConfirm();
  const pressedOverlayRef = useRef(false);
  const isChanged = () => Boolean(typeof changed === "function" ? changed() : changed);
  const requestClose = async () => {
    if (isChanged() && !await confirm({ message, confirmLabel: "Discard", cancelLabel: "Keep editing", danger: true })) return;
    onClose();
  };
  const outsidePress = {
    onPointerDown: event => { pressedOverlayRef.current = event.target === event.currentTarget; },
    onClick: event => {
      const pressed = pressedOverlayRef.current;
      pressedOverlayRef.current = false;
      if (pressed && event.target === event.currentTarget && !isChanged()) onClose();
    },
  };
  return { requestClose, outsidePress, confirmDialog };
}
