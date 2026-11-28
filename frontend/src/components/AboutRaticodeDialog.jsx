import { Check, Clipboard } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { appDetails, formatAppDetails, loadAppDetails } from "../lib/issueReport.js";
import { Dialog } from "./Dialog.jsx";
import RaticodeMark from "./RaticodeMark.jsx";

// Help › About Raticode: the same details Report a Problem puts in an issue.
export function AboutRaticodeDialog({ onClose }) {
  const [details, setDetails] = useState(() => appDetails());
  const [copyState, setCopyState] = useState("idle");
  const timerRef = useRef(null);

  useEffect(() => {
    let current = true;
    void loadAppDetails().then((loaded) => { if (current) setDetails(loaded); });
    return () => { current = false; window.clearTimeout(timerRef.current); };
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(formatAppDetails(details));
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
    window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setCopyState("idle"), 1800);
  }

  const rows = [["Version", details.version], ["OS", details.os], ["Architecture", details.arch], ["Electron", details.electron]];
  return (
    <Dialog
      onClose={onClose}
      overlayClassName="fixed inset-0 z-[100] grid place-items-center ui-scrim px-4"
      panelClassName="ui-dialog w-full max-w-sm p-4"
      panelProps={{ "data-testid": "about-raticode" }}
      title="About Raticode"
    >
      <div className="flex items-center gap-2.5">
        <RaticodeMark className="h-8 w-8" />
        <h2 className="text-[15px] font-semibold text-ink">Raticode</h2>
      </div>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[13px]">
        {rows.map(([label, value]) => (
          <div className="contents" key={label}>
            <dt className="text-muted">{label}</dt>
            <dd className="min-w-0 break-words font-mono text-[12px] text-ink">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className="ui-button" onClick={() => void copy()}>
          {copyState === "copied" ? <Check aria-hidden="true" size={13} /> : <Clipboard aria-hidden="true" size={13} />}
          {copyState === "copied" ? "Copied" : copyState === "failed" ? "Copy failed" : "Copy"}
        </button>
        <button type="button" className="ui-button ui-button--primary" data-dialog-initial-focus onClick={onClose}>Close</button>
      </div>
      <div aria-live="polite" className="sr-only" role="status">
        {copyState === "copied" ? "Copied Raticode details" : copyState === "failed" ? "Could not copy Raticode details" : null}
      </div>
    </Dialog>
  );
}
