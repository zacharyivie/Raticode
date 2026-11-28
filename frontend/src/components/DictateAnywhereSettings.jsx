import { useEffect, useState } from "react";
import { SwitchControl } from "./SettingControls.jsx";
import { setAnywhereStatus, useAnywhereStatus, voiceShortcutLabel } from "../lib/voiceShortcut.js";
import { clockTime } from "../lib/timeText.js";

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

  async function askAgain() {
    try {
      setAnywhereStatus(await bridge.retry());
    } catch { /* The status stays as it was. */ }
  }

  const support = status?.support;
  const lastPressed = status?.lastPressedAt ? clockTime(status.lastPressedAt) : "";
  const desktop = support === "desktop" && status && !status.error ? desktopStatus(status, label) : null;
  const showCommand = Boolean(status?.command) && (Boolean(status?.error) || Boolean(desktop?.command));
  const shownState = !enabled ? "off"
    : status?.registered ? "registered"
      : status?.error ? "failed"
        : desktop ? desktop.state
          : "pending";
  return (
    <div className="py-3" data-dictate-anywhere={shownState}>
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
          ) : desktop ? (
            <p role={desktop.tone === "muted" ? "status" : "alert"} className={desktop.tone === "muted" ? "text-muted" : "text-amber-700 dark:text-amber-300"} data-dictate-anywhere-route={status.route || ""}>
              {desktop.text}
            </p>
          ) : (
            <p role="status" className="text-muted">Ready: {label} works in every app.</p>
          )}
          {desktop?.retry && bridge?.retry ? (
            <button className="h-7 rounded-md border border-line px-2 text-[11px] text-ink hover:bg-slate-50" type="button" onClick={() => void askAgain()}>Ask my desktop again</button>
          ) : null}
          {status?.enabled && status?.registered ? (
            <p className="text-muted" data-dictate-anywhere-last>{lastPressed ? `Last pressed at ${lastPressed}.` : "Not pressed yet."}</p>
          ) : null}
          {showCommand ? (
            <div className="space-y-1">
              <p className="text-muted">Command for a custom shortcut in your desktop&apos;s keyboard settings (Raticode must be running):</p>
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

// What a Wayland desktop did with the shortcut, in words: { state, tone, text, command, retry }.
// route: kde, portal, gnome or manual. Only "manual", a failure or a declined prompt shows the
// command to bind by hand.
export function desktopStatus(status, label) {
  const key = label || "the shortcut";
  const trigger = status.trigger || key;
  const state = status.state || "working";
  if (state === "registered") {
    if (status.route === "kde") {
      return {
        state,
        tone: "muted",
        text: status.elsewhere
          ? `Ready: KDE uses ${trigger} for this, as set in System Settings > Keyboard > Shortcuts > Raticode. Changing the key in Settings > Keybindings replaces it.`
          : `Ready: ${trigger} works in every app. Raticode added it to KDE's shortcuts, where you can also change it (System Settings > Keyboard > Shortcuts > Raticode). Turning this off removes it.`,
      };
    }
    if (status.route === "gnome") {
      return { state, tone: "muted", text: `Ready: ${key} works in every app. Raticode added it to GNOME's custom shortcuts (Settings > Keyboard > Keyboard Shortcuts) and removes it when you turn this off.` };
    }
    return { state, tone: "muted", text: `Ready: ${trigger} works in every app. Your desktop added the shortcut; you can change its key in your desktop's shortcut settings.` };
  }
  if (state === "approval") {
    return { state, tone: "warn", text: `Your desktop is asking whether Raticode may use ${key} in every app. Answer its prompt to finish.` };
  }
  if (state === "declined") {
    return { state, tone: "warn", text: `Your desktop's request to add ${key} was declined, so it only works while Raticode is in front.`, retry: true, command: true };
  }
  if (state === "unassigned") {
    return { state, tone: "warn", text: status.message || "Your desktop added the shortcut without a key. Choose one in your desktop's shortcut settings." };
  }
  if (state === "manual") {
    return { state, tone: "warn", text: `Your desktop doesn't let apps add shortcuts. To use ${key} from any app, add a shortcut in its keyboard settings that runs the command below.`, command: true };
  }
  if (state === "failed") {
    return { state, tone: "warn", text: `Raticode couldn't add the shortcut to your desktop${status.message ? ` (${status.message.replace(/\.$/, "")})` : ""}. You can bind the command below to ${key} in your desktop's keyboard settings instead.`, command: true };
  }
  return { state: "pending", tone: "muted", text: `Asking your desktop to add ${key}…` };
}
