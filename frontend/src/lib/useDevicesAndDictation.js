import { useEffect, useRef, useState } from "react";
import { controlledEngine, webBrowserEngine } from "./browserEngines.js";
import { deletePhoneThread } from "./chatThreads.js";
import { applyPhoneSettings } from "./deviceMessages.js";
import { pluginEnabled } from "./plugins.js";
import { mergeRecentProjects } from "./projects.js";
import { SCREEN_COMMAND_ID, screenFromCapture, setScreenContextEnabled, setScreenStatus } from "./screenContext.js";
import { normalizeAppSettings, settingBinding } from "./settings.js";
import { ANYWHERE_COMMAND_ID, VOICE_COMMAND_ID, onVoicePhase, pickRemComposer, requestAnywhereDictation, setAnywhereStatus, setDictationAutoSend, setDictationMode, setVoiceBinding, stopAnywhereDictation, voiceShortcutLabel } from "./voiceShortcut.js";

// Paired phones and dictation: the phone sync engine, phone locks, Dictate to Rem
// from any app and Show Rem your screen.
export function useDevicesAndDictation({ settings, setSettings, recentProjectRoots, workflows, browserOpenPaths, activeBrowserPath, browserTabs, openIntegratedBrowserRef, setRemMounted }) {
  const deviceSyncContext = useRef({});
  useEffect(() => {
    // The sync engine loads after startup; it is not needed to render the app.
    let stop = null;
    let closed = false;
    import("../lib/deviceWorkspaceSync.js").then(({ startDeviceWorkspaceSync }) => {
      if (!closed) stop = startDeviceWorkspaceSync({ getContext: () => deviceSyncContext.current, deleteThread: deletePhoneThread });
    });
    return () => { closed = true; stop?.(); };
  }, []);
  // Phones the suspicious-request guard locked; only the user unlocks them, in Settings.
  const [phoneLocks, setPhoneLocks] = useState([]);
  useEffect(() => {
    const locked = (event) => setPhoneLocks(current => {
      const next = Array.isArray(event?.detail) ? event.detail : [];
      return JSON.stringify(current) === JSON.stringify(next) ? current : next;
    });
    window.addEventListener("gofer:phone-locked", locked);
    return () => window.removeEventListener("gofer:phone-locked", locked);
  }, []);
  deviceSyncContext.current = { settings, roots: mergeRecentProjects(recentProjectRoots, workflows.map(item => item.projectRoot).filter(Boolean)),
    updateSettings: changes => setSettings(current => normalizeAppSettings(applyPhoneSettings(current, changes))),
    // A paired phone's browser lists these web tabs and can send a page to a new background tab.
    browser: { searchUrl: settings.browser.searchUrl, engine: controlledEngine(settings.browser.engine), tabs: () => phoneBrowserTabs(browserOpenPaths, activeBrowserPath, browserTabs),
      // A page from the phone opens where the phone was told the computer's tabs run.
      open: url => openIntegratedBrowserRef.current?.(url, { newTab: true, background: true, engine: controlledEngine(settings.browser.engine) }) } };

  // Chat boxes show the dictation shortcut in their titles and hints.
  useEffect(() => {
    setVoiceBinding(settingBinding(settings, VOICE_COMMAND_ID));
    setDictationMode(settings.devices?.dictationMode);
    setDictationAutoSend(settings.devices?.dictationAutoSend);
  }, [settings]);

  // Dictate to Rem from any app: the desktop app registers the system-wide shortcut while
  // Settings > Devices has it on, and reports whether it could.
  const anywhereEnabled = settings.devices?.dictateAnywhere === true;
  const anywhereBinding = settingBinding(settings, ANYWHERE_COMMAND_ID);
  // Show Rem your screen: the Screen button in Rem's box, and the "Ask Rem about your screen"
  // shortcut while Dictate to Rem from any app is also on. Off by default.
  const screenEnabled = settings.devices?.screenContext === true;
  const screenBinding = settingBinding(settings, SCREEN_COMMAND_ID);
  useEffect(() => {
    const bridge = window.goferDesktop?.screenContext;
    setScreenContextEnabled(screenEnabled && Boolean(bridge?.configure));
    if (!bridge?.configure) {
      setScreenStatus(screenEnabled ? { unavailable: true } : null);
      return undefined;
    }
    let cancelled = false;
    void Promise.resolve(bridge.configure({ enabled: screenEnabled, shortcut: anywhereEnabled, binding: screenBinding }))
      .then((status) => { if (!cancelled) setScreenStatus(status); }, () => { if (!cancelled) setScreenStatus({ enabled: screenEnabled, error: "Raticode couldn't set up screenshots." }); });
    return () => { cancelled = true; };
  }, [screenEnabled, anywhereEnabled, screenBinding]);
  useEffect(() => {
    const bridge = window.goferDesktop?.globalDictation;
    if (!bridge?.configure) {
      setAnywhereStatus(anywhereEnabled ? { unavailable: true } : null);
      return undefined;
    }
    let cancelled = false;
    void Promise.resolve(bridge.configure({ enabled: anywhereEnabled, binding: anywhereBinding, label: voiceShortcutLabel(anywhereBinding) }))
      .then((status) => { if (!cancelled) setAnywhereStatus(status); }, () => { if (!cancelled) setAnywhereStatus({ enabled: anywhereEnabled, error: "Raticode couldn't set up the shortcut." }); });
    return () => { cancelled = true; };
  }, [anywhereEnabled, anywhereBinding]);
  // The shortcut was pressed in another app: dictate into Rem's chat without bringing the
  // window forward. Rem's chat loads (unseen) if it hasn't been opened yet.
  const remPluginOnForVoice = pluginEnabled(settings.plugins, "rem");
  useEffect(() => {
    const bridge = window.goferDesktop?.globalDictation;
    if (!bridge?.onTrigger) return undefined;
    const report = (state) => { void Promise.resolve(bridge.report?.(state)).catch(() => {}); };
    const stopPhases = onVoicePhase(report);
    const stopTrigger = bridge.onTrigger((message) => {
      // A Wayland desktop answered later (its prompt, or a key changed in its settings).
      if (message?.action === "status") {
        if (message.status && typeof message.status === "object") setAnywhereStatus(message.status);
        return;
      }
      if (message?.action === "stop") {
        stopAnywhereDictation();
        return;
      }
      if (message?.action === "screen") {
        // "Ask Rem about your screen": the screenshot goes in Rem's box, then dictation starts
        // (or carries on). It always waits there to be checked before it is sent.
        if (!remPluginOnForVoice) {
          report({ phase: "error", global: true, message: "Turn on the Rem plugin in Settings > Plugins to show Rem your screen." });
          return;
        }
        const screen = screenFromCapture(message.capture, { fromShortcut: true });
        if (!screen) {
          report({ phase: "error", global: true, message: "The screenshot is too large to send to Rem." });
          return;
        }
        void import("../lib/remComposer.js").then(({ setRemScreen }) => {
          setRemScreen(screen);
          if (pickRemComposer()?.dictating()) return;
          return requestAnywhereDictation({ reveal: () => setRemMounted(true) }).then((result) => {
            if (!result.ok) report({ phase: "error", global: true, message: `${result.message} The screenshot is waiting in Rem's message box.` });
          });
        });
        return;
      }
      if (message?.action !== "toggle") return;
      if (!remPluginOnForVoice) {
        report({ phase: "error", global: true, message: "Turn on the Rem plugin in Settings > Plugins to dictate to Rem." });
        return;
      }
      void requestAnywhereDictation({ reveal: () => setRemMounted(true) }).then((result) => {
        if (!result.ok) report({ phase: "error", global: true, message: result.message });
      });
    });
    return () => { stopPhases(); stopTrigger?.(); };
  }, [remPluginOnForVoice, setRemMounted]);
  return phoneLocks;
}

// Browser tabs as the paired-device sync publishes them. The backend keeps only web
// addresses; previews, local files and blank tabs are dropped there.
export function phoneBrowserTabs(paths, activePath, browserTabs) {
  return paths.filter(path => browserTabs[path] && !browserTabs[path].private).map(path => {
    const tab = browserTabs[path];
    return { id: path, title: String(tab.title || ""), url: String(tab.url || ""), active: path === activePath,
      // The browser this tab runs in; phones that predate per-tab browsers ignore it.
      engine: webBrowserEngine(tab.agentSessionId ? tab.agentEngine || tab.engine : tab.engine),
      ...(tab.agentSessionId ? { owner: "rem", label: tab.agentLabel || "Rem" } : { owner: "you" }) };
  });
}
