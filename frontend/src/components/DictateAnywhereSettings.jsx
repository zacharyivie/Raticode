import { useEffect, useState } from "react";
import { SwitchControl } from "./SettingControls.jsx";
import { setAnywhereStatus, useAnywhereStatus, voiceShortcutLabel } from "../lib/voiceShortcut.js";

// Settings > Devices: the system-wide "Dictate to Rem from any app" shortcut. The desktop
// app registers it; this says plainly whether that worked on this desktop, and on Linux
// gives the command a desktop's own shortcut settings can run instead.
export default function DictateAnywhereSettings({ enabled, binding, onChange }) {
  const status = useAnywhereStatus();
  const [copied, setCopied] = useState(false);
  const label = voiceShortcutLabel(binding);
  const bridge = typeof window === "undefined" ? null : window.goferDesktop?.globalDictation;

  // Opening Settings refreshes the status, including when the shortcut was last pressed.
  useEffect(() => {
    if (!bridge?.configure || !enabled) return undefined;
    let cancelled = false;
    void Promise.resolve(bridge.configure({ enabled, binding, label })).then((next) => { if (!cancelled) setAnywhereStatus(next); }, () => {});
    return () => { cancelled = true; };
  }, [bridge, enabled, binding, label]);

  async function copyCommand() {
    try {
      await navigator.clipboard.writeText(status.command);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  const support = status?.support;
  const lastPressed = status?.lastPressedAt ? new Date(status.lastPressedAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  return (
    <div className="py-3" data-dictate-anywhere={enabled ? (status?.registered ? "registered" : status?.error ? "failed" : "pending") : "off"}>
      <div className="flex items-center gap-5">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-ink">Dictate to Rem from any app</p>
          <p className="mt-1 max-w-[54ch] text-[10px] leading-4 text-muted">
            Press {label || "the shortcut"} in any app, even with Raticode minimized, to start talking to Rem. Press it again, or stop talking for a few seconds, to finish. Your words go to Rem&apos;s message box, tidied and sent according to the settings above. The microphone opens only when you press it, and a small panel shows while Raticode listens. Change the key in Settings &gt; Keybindings.
          </p>
        </div>
        <div className="w-52 shrink-0">
          <SwitchControl label="Dictate to Rem from any app" checked={enabled} onChange={(value) => onChange("devices.dictateAnywhere", value)} />
        </div>
      </div>
      {enabled ? (
        <div className="mt-2 max-w-[60ch] space-y-1.5 text-[11px] leading-4">
          {!bridge || status?.unavailable ? (
            <p role="status" className="text-amber-700 dark:text-amber-300">Only the Raticode desktop app can listen for a shortcut in other apps.</p>
          ) : !status ? (
            <p role="status" className="text-muted">Setting up the shortcut…</p>
          ) : status.error ? (
            <p role="alert" className="text-red-600" data-dictate-anywhere-error>{status.error}</p>
          ) : support === "portal" ? (
            <p role="status" className="text-muted">
              On Wayland your desktop owns shortcuts, so Raticode asks it for {label}. The first time, it may ask you to confirm, or let you pick another key in its own shortcut settings. Some desktops don&apos;t offer this at all. If pressing it does nothing, use the command below.
            </p>
          ) : support === "xwayland" ? (
            <p role="status" className="text-amber-700 dark:text-amber-300">
              Raticode is running as an X11 app on a Wayland desktop (the Raticode (gecko) browser needs this), and most Wayland desktops only pass {label} to X11 apps while one of them is focused. To use it from any app, add a shortcut in your desktop&apos;s settings that runs the command below.
            </p>
          ) : (
            <p role="status" className="text-muted">Ready: {label} works in every app.</p>
          )}
          {status?.enabled && status?.registered ? (
            <p className="text-muted" data-dictate-anywhere-last>{lastPressed ? `Last pressed at ${lastPressed}.` : "Not pressed yet."}</p>
          ) : null}
          {status?.command && (support === "portal" || support === "xwayland" || status.error) ? (
            <div className="space-y-1">
              <p className="text-muted">Command for a custom shortcut in your desktop&apos;s settings (Raticode must be running):</p>
              <div className="flex items-start gap-2">
                <code className="min-w-0 flex-1 select-all break-all rounded-md border border-line bg-slate-50 px-2 py-1 font-mono text-[10px] text-ink dark:bg-slate-900">{status.command}</code>
                <button className="h-7 shrink-0 rounded-md border border-line px-2 text-[11px] text-ink hover:bg-slate-50" type="button" onClick={() => void copyCommand()}>{copied ? "Copied" : "Copy"}</button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
