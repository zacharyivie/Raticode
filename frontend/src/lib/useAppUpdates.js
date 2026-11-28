import { useCallback, useEffect, useState } from "react";

// Update checks, the update notice and installing a downloaded update.
export function useAppUpdates({ settings, setTopBarNotice }) {
  const [updateNotice, setUpdateNotice] = useState({ type: "", message: "" });
  const [updateState, setUpdateState] = useState({
    available: false,
    checking: false,
    error: "",
    info: null,
  });

  const checkForUpdates = useCallback(async ({ silent = false } = {}) => {
    if (!window.goferUpdates?.check) return;
    if (!silent) setUpdateNotice({ type: "", message: "" });
    setUpdateState((current) => ({
      ...current,
      checking: true,
      error: silent ? current.error : "",
    }));
    try {
      const info = await window.goferUpdates.check();
      setUpdateState((current) => ({ ...current, ...info, checking: false }));
      const notice = updateCheckNotice(info);
      if (notice.type === "error") throw new Error(notice.message);
      if (!silent) setUpdateNotice(notice);
    } catch (error) {
      setUpdateState((current) => ({
        ...current,
        checking: false,
        error: error instanceof Error ? error.message : "Unable to check for updates",
      }));
      if (!silent) {
        setUpdateNotice({
          type: "error",
          message: error instanceof Error ? error.message : "Unable to check for updates",
        });
      }
    }
  }, []);

  useEffect(() => {
    if (!window.goferUpdates?.onState) return undefined;
    const unsubscribe = window.goferUpdates.onState((nextState) => {
      setUpdateState((current) => ({ ...current, ...nextState }));
    });
    window.goferUpdates.getState?.().then((nextState) => {
      setUpdateState((current) => ({ ...current, ...nextState }));
    }).catch(() => {});
    return unsubscribe;
  }, []);

  // The main process checks on startup and every 10 minutes while this setting is on.
  useEffect(() => {
    const enabled = Boolean(settings.general.checkForUpdates);
    if (window.goferUpdates?.setAutomatic) {
      window.goferUpdates.setAutomatic(enabled).then((nextState) => {
        if (nextState) setUpdateState((current) => ({ ...current, ...nextState }));
      }).catch(() => {});
    } else if (enabled) {
      checkForUpdates({ silent: true });
    }
  }, [checkForUpdates, settings.general.checkForUpdates]);

  async function applyUpdate(update) {
    if (!window.goferUpdates) return;
    try {
      const nextState = update.downloaded
        ? await window.goferUpdates.installDownloaded()
        : await window.goferUpdates.downloadAndInstall();
      setUpdateState((current) => ({ ...current, ...nextState }));
      if (update.supported === false) setTopBarNotice({ type: "info", message: manualUpdateNotice(update) });
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to apply update",
      });
    }
  }

  useEffect(() => {
    if (!updateNotice?.message) return undefined;

    const timeoutId = window.setTimeout(() => {
      setUpdateNotice({ type: "", message: "" });
    }, 3500);

    return () => window.clearTimeout(timeoutId);
  }, [updateNotice?.message]);
  return { updateNotice, updateState, checkForUpdates, applyUpdate };
}

// A failed check must never read as "up to date": the main process returns its
// error in the state instead of rejecting.
export function updateCheckNotice(info) {
  if (!info) return { type: "error", message: "The update check returned no result. Try again." };
  if (info.error) return { type: "error", message: info.error };
  if (info.available) return { type: "success", message: `Raticode ${info.info?.version ?? "update"} is available` };
  if (info.info?.noReleases) return { type: "success", message: "No published Raticode releases yet" };
  return { type: "success", message: "Raticode is up to date" };
}

function opensMacInstaller(updateState) {
  return updateState?.platform === "darwin" && Boolean(updateState.info?.installerUrl);
}

export function manualUpdateNotice(updateState) {
  if (updateState?.managedBy) return `Update Raticode with ${updateState.managedBy}, for example yay -Syu or paru -Syu, then restart it.`;
  const steps = opensMacInstaller(updateState)
    ? "Quit Raticode, open the downloaded DMG, and drag Raticode into Applications to replace the old copy."
    : "Download and install the update from the release page.";
  return updateState?.manualReason ? `${updateState.manualReason} ${steps}` : steps;
}

export function updateButtonLabel(updateState) {
  if (updateState?.managedBy) return `Update ${updateState?.info?.version ?? "available"} with ${updateState.managedBy}`;
  if (updateState?.supported === false) return opensMacInstaller(updateState) ? "Download Mac installer" : "Open update downloads";
  if (updateState?.downloaded) return "Restart to update";
  if (updateState?.verifying) return "Verifying update";
  if (updateState?.downloading) {
    const percent = Math.max(0, Math.min(100, updateState.progress?.percent ?? 0));
    return `Downloading ${Math.round(percent)}%`;
  }
  return `Update ${updateState?.info?.version ?? "available"}`;
}

export function updateButtonTitle(updateState) {
  if (updateState?.managedBy) return `Your system package manager installed Raticode, so ${updateState.managedBy} updates it, for example with yay -Syu or paru -Syu. This opens the release notes.`;
  if (updateState?.supported === false) {
    const reason = updateState.manualReason ? `${updateState.manualReason} ` : "";
    return opensMacInstaller(updateState)
      ? `${reason}Download the Mac installer, quit Raticode, then open the DMG and drag Raticode into Applications. This copy requires manual installation.`
      : `${reason}Open the release page to download and install the update manually`;
  }
  if (updateState?.downloaded) return "Restart Raticode and apply the downloaded update";
  if (updateState?.verifying) return "macOS is checking the downloaded update's signature. Raticode restarts when it's done.";
  if (updateState?.downloading) return "Downloading update";
  return "Download, install, and restart Raticode";
}
