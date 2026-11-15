import { useEffect } from "react";
import { SwitchControl } from "./SettingControls.jsx";
import { screenBridge, setScreenStatus, useScreenStatus } from "../lib/screenContext.js";
import { voiceShortcutLabel } from "../lib/voiceShortcut.js";

// Settings > Devices: Show Rem your screen. Off by default. Says plainly how this desktop
// takes a screenshot and what Rem keeps.
export default function ScreenContextSettings({ enabled, anywhere, binding, onChange }) {
  const status = useScreenStatus();
  const bridge = screenBridge();
  const label = voiceShortcutLabel(binding);
  const support = status?.support;
  const shortcut = status?.shortcut;

  // Opening Settings asks the desktop app again, including for macOS's permission.
  useEffect(() => {
    if (!bridge?.configure || !enabled) return undefined;
    let cancelled = false;
    void Promise.resolve(bridge.configure({ enabled, shortcut: anywhere === true, binding }))
      .then((next) => { if (!cancelled) setScreenStatus(next); }, () => {});
    return () => { cancelled = true; };
  }, [bridge, enabled, anywhere, binding]);

  return (
    <div className="py-3" data-screen-context={enabled ? support || "pending" : "off"}>
      <div className="flex items-center gap-5">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-ink">Show Rem your screen</p>
          <p className="mt-1 max-w-[54ch] text-[10px] leading-4 text-muted">
            Adds a Screen button to Rem&apos;s message box. When you use it, Raticode takes one screenshot of the screen or window you pick and shows it in the box, so you can remove it before sending. Rem uses it for that one reply; it isn&apos;t saved, and only its name stays in the chat. Nothing is captured in the background, and Rem can&apos;t take a screenshot on its own.
          </p>
        </div>
        <div className="w-52 shrink-0">
          <SwitchControl label="Show Rem your screen" checked={enabled} onChange={(value) => onChange("devices.screenContext", value)} />
        </div>
      </div>
      {enabled ? (
        <div className="mt-2 max-w-[60ch] space-y-1.5 text-[11px] leading-4">
          {!bridge || status?.unavailable ? (
            <p role="status" className="text-amber-700 dark:text-amber-300">Only the Raticode desktop app can take screenshots.</p>
          ) : !status ? (
            <p role="status" className="text-muted">Checking how this desktop shares its screen…</p>
          ) : support === "unsupported" ? (
            <p role="alert" className="text-red-600">Raticode can&apos;t take screenshots on this system.</p>
          ) : support === "portal" ? (
            <p role="status" className="text-muted">On Wayland your desktop decides what apps may see: each time, its screen-sharing dialog asks which screen or window to share. It needs xdg-desktop-portal and PipeWire.</p>
          ) : support === "xwayland" ? (
            <p role="status" className="text-amber-700 dark:text-amber-300">Raticode is running as an X11 app on a Wayland desktop (the Raticode (gecko) browser needs this), so it can only see other X11 apps; screens usually come out black.</p>
          ) : status.permission && !["granted", "unknown"].includes(status.permission) ? (
            <div role="status" className="space-y-1 text-amber-700 dark:text-amber-300">
              <p>macOS hasn&apos;t allowed Raticode to record the screen yet. The first screenshot asks; allow Raticode in System Settings &gt; Privacy &amp; Security &gt; Screen &amp; System Audio Recording, then quit and reopen Raticode.</p>
              {bridge.openPrivacySettings ? <button className="h-7 rounded-md border border-line px-2 text-[11px] text-ink hover:bg-slate-50" type="button" onClick={() => void bridge.openPrivacySettings()}>Open System Settings</button> : null}
            </div>
          ) : (
            <p role="status" className="text-muted">Ready: the Screen button lists your screens and windows to choose from.</p>
          )}
          {anywhere ? (
            shortcut?.error ? <p role="alert" className="text-red-600" data-screen-shortcut-error>{shortcut.error}</p>
              : <p className="text-muted" data-screen-shortcut>
                Press {label || "the Ask Rem about your screen shortcut"} in any app to attach a screenshot {support === "portal" ? "(your desktop asks which screen or window)" : "of the screen your pointer is on"} and start dictating to Rem. The screenshot waits in Rem&apos;s message box for you to check, even with Send after dictation on. Change the key in Settings &gt; Keybindings.
              </p>
          ) : (
            <p className="text-muted">Turn on Dictate to Rem from any app as well to use {label || "the Ask Rem about your screen shortcut"} from other apps.</p>
          )}
        </div>
      ) : null}
    </div>
  );
}
