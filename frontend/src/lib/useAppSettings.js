import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { apiUrl } from "./api.js";
import brandCompat from "./brandCompat.js";
import { reportArchiveError } from "./chatThreads.js";
import { pluginAvailable, withPluginPreview } from "./plugins.js";
import { normalizeReportThemes } from "./reportThemes.js";
import { followRenamedAccount, loadAppSettings, matchesKeybinding, reducedMotionEnabled, resolvedTheme, saveAppSettings, updateSetting } from "./settings.js";
import { clamp as clampNumber } from "./terminalWorkspace.js";
import { browserChromeOwnsShortcut } from "./useBrowserTabs.js";

export const TEXT_ZOOM_STORAGE_KEY = "raticode.textZoom.v1";
export const TEXT_ZOOM_MIN = 80;
export const TEXT_ZOOM_MAX = 150;
const TEXT_ZOOM_STEP = 10;

// App settings, Settings' open state and text zoom. Saves settings, applies the
// theme and zoom, and opens Settings when another part of the app asks.
export function useAppSettings() {
  const [settings, setSettings] = useState(loadAppSettings);
  const [textZoom, setTextZoom] = useState(loadTextZoom);
  useEffect(() => {
    const bridge = window.goferDesktop?.rem;
    if (!bridge) return;
    void bridge.settings().then((memory) => {
      setSettings((current) => ({ ...current, memory: { ...current.memory, ...memory, reportThemes: normalizeReportThemes(memory.reportThemes ?? (memory.secondBrainTheme ? undefined : current.memory.reportThemes), memory.secondBrainTheme) } }));
    }).catch(reportArchiveError);
  }, []);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsCategory, setSettingsCategory] = useState("general");
  const [settingsBrowserSection, setSettingsBrowserSection] = useState("");
  // Selectors for one setting Settings scrolls to when it opens, such as Rem's provider.
  const [settingsReveal, setSettingsReveal] = useState(null);
  useEffect(() => {
    const openProviders = () => { setSettingsCategory("providers"); setSettingsOpen(true); };
    // Firefox and Chrome tabs open Settings > Browser for that browser's settings.
    // A tab's menu names its browser, so Settings shows that browser's section.
    const openBrowser = (event) => { setSettingsCategory("browser"); setSettingsBrowserSection(typeof event?.detail?.engine === "string" ? event.detail.engine : ""); setSettingsOpen(true); };
    // Self-work's settings are in Settings > Rem, reachable with the Kanban board off.
    const openRem = () => { setSettingsCategory("assistant"); setSettingsOpen(true); };
    window.addEventListener("raticode:open-provider-settings", openProviders);
    window.addEventListener("raticode:open-browser-settings", openBrowser);
    window.addEventListener("raticode:open-rem-settings", openRem);
    const openDevices = () => { setSettingsCategory("devices"); setSettingsOpen(true); };
    window.addEventListener("raticode:open-device-settings", openDevices);
    return () => {
      window.removeEventListener("raticode:open-device-settings", openDevices);
      window.removeEventListener("raticode:open-provider-settings", openProviders);
      window.removeEventListener("raticode:open-browser-settings", openBrowser);
      window.removeEventListener("raticode:open-rem-settings", openRem);
    };
  }, []);
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false,
  );
  const theme = resolvedTheme(settings, systemDark);

  const changeSetting = useCallback((path, value) => {
    setSettings((current) => updateSetting(current, path, value));
  }, []);

  // Renaming or removing an account in Settings > Providers keeps Rem's account setting valid.
  useEffect(() => {
    const follow = (event) => setSettings((current) => followRenamedAccount(current, event.detail || {}));
    window.addEventListener("raticode:provider-account-renamed", follow);
    return () => window.removeEventListener("raticode:provider-account-renamed", follow);
  }, []);

  // Organizations also has a backend switch. When the backend has it off, the
  // plugin's preview turns off so the feature appears only in Feature Preview.
  const organizationsShown = pluginAvailable(settings.plugins, "organizations");
  useEffect(() => {
    if (!organizationsShown) return undefined;
    let cancelled = false;
    fetch(apiUrl("/organizations?projectRoot=&action=availability")).then((response) => response.json()).then((payload) => {
      if (cancelled || payload?.result?.enabled !== false) return;
      setSettings((current) => updateSetting(current, "plugins.preview", withPluginPreview(current.plugins, "organizations", false)));
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [organizationsShown]);

  useEffect(() => {
    const timer = window.setTimeout(() => saveAppSettings(settings), 120);
    return () => window.clearTimeout(timer);
  }, [settings]);

  useLayoutEffect(() => {
    const zoomFactor = textZoom / 100;
    const setNativeZoom = window.goferDesktop?.appearance?.setZoomFactor;
    if (setNativeZoom) {
      setNativeZoom(zoomFactor);
      document.documentElement.style.fontSize = "";
      return;
    }
    document.documentElement.style.fontSize = `${textZoom}%`;
  }, [textZoom]);

  useEffect(() => () => {
    window.goferDesktop?.appearance?.setZoomFactor?.(1);
    document.documentElement.style.fontSize = "";
  }, []);

  useEffect(() => {
    try {
      window.localStorage?.setItem(TEXT_ZOOM_STORAGE_KEY, String(textZoom));
    } catch {
      // Text zoom remains available for this session when storage is unavailable.
    }
  }, [textZoom]);

  useEffect(() => {
    function handleTextZoomKeydown(event) {
      // Zoom keys inside the browser chrome zoom the page instead.
      if (browserChromeOwnsShortcut(event)) return;
      if (matchesKeybinding(event, "Mod+Digit0") || matchesKeybinding(event, "Ctrl+Digit0")) {
        if (eventTargetsGraphVisualization(event)) return;
        event.preventDefault();
        setTextZoom(100);
        return;
      }
      const direction = textZoomDirection(event);
      if (!direction) return;
      event.preventDefault();
      if (eventTargetsGraphVisualization(event)) return;
      setTextZoom((current) => nextTextZoom(current, direction));
    }

    function handleTextZoomWheel(event) {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.deltaY === 0) return;
      event.preventDefault();
      if (eventTargetsGraphVisualization(event)) return;
      setTextZoom((current) => nextTextZoom(current, event.deltaY < 0 ? 1 : -1));
    }

    window.addEventListener("keydown", handleTextZoomKeydown, true);
    window.addEventListener("wheel", handleTextZoomWheel, { capture: true, passive: false });
    return () => {
      window.removeEventListener("keydown", handleTextZoomKeydown, true);
      window.removeEventListener("wheel", handleTextZoomWheel, true);
    };
  }, []);

  useEffect(() => {
    const colorScheme = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!colorScheme) return undefined;
    const updateSystemTheme = (event) => setSystemDark(event.matches);
    colorScheme.addEventListener?.("change", updateSystemTheme);
    return () => colorScheme.removeEventListener?.("change", updateSystemTheme);
  }, []);

  useEffect(() => {
    const motionQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const applyMotion = () => {
      if (document.documentElement.dataset) document.documentElement.dataset.reducedMotion = String(reducedMotionEnabled(settings, motionQuery?.matches ?? false));
    };
    applyMotion();
    motionQuery?.addEventListener?.("change", applyMotion);
    document.documentElement.classList?.toggle?.("dark", theme === "dark");
    try {
      window.localStorage?.setItem("gofer-ui-theme", theme);
    } catch {
      // The selected theme still applies for this session.
    }
    return () => motionQuery?.removeEventListener?.("change", applyMotion);
  }, [settings, theme]);
  return { settings, setSettings, textZoom, setTextZoom, settingsOpen, setSettingsOpen, settingsCategory, setSettingsCategory, settingsBrowserSection, setSettingsBrowserSection, settingsReveal, setSettingsReveal, changeSetting, theme };
}

export function loadTextZoom(storage = globalThis.window?.localStorage) {
  try {
    const stored = Number(brandCompat.readBrandedStorage(storage, TEXT_ZOOM_STORAGE_KEY));
    return Number.isFinite(stored) && stored
      ? clampNumber(stored, TEXT_ZOOM_MIN, TEXT_ZOOM_MAX)
      : 100;
  } catch {
    return 100;
  }
}

export function nextTextZoom(current, direction) {
  const value = Number(current);
  const normalized = Number.isFinite(value)
    ? Math.round(value / TEXT_ZOOM_STEP) * TEXT_ZOOM_STEP
    : 100;
  return clampNumber(
    normalized + Math.sign(direction) * TEXT_ZOOM_STEP,
    TEXT_ZOOM_MIN,
    TEXT_ZOOM_MAX,
  );
}

export function textZoomDirection(event) {
  if (
    !(event.ctrlKey || event.metaKey)
    || event.altKey
    || (event.shiftKey && event.key !== "+")
  ) {
    return 0;
  }
  const key = String(event.key ?? "");
  const code = String(event.code ?? "");
  if (key === "+" || key === "=" || code === "Equal" || code === "NumpadAdd") return 1;
  if (key === "-" || key === "_" || code === "Minus" || code === "NumpadSubtract") return -1;
  return 0;
}

export function eventTargetsGraphVisualization(event) {
  let target = event?.target;
  while (target) {
    if (target.getAttribute?.("data-graph-visualization") === "true") return true;
    target = target.parentNode;
  }
  return false;
}
