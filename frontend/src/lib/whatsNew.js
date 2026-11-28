import { useCallback, useEffect, useState } from "react";

// What's new after an update. The last version that ran is kept on this computer; the
// first launch of a newer one shows "Raticode updated to X.Y.Z" until the user dismisses
// it or opens What's new. The dialog reads the CHANGELOG bundled into the app.
const LAST_VERSION_STORAGE_KEY = "raticode.lastRunVersion.v1";
const RELEASES_URL = "https://github.com/zacharyivie/Raticode/releases";

function versionParts(value) {
  const match = String(value || "").trim().match(/^v?(\d+)\.(\d+)\.(\d+)/);
  return match ? match.slice(1, 4).map(Number) : null;
}

function compareVersions(left, right) {
  const a = versionParts(left);
  const b = versionParts(right);
  for (let index = 0; index < 3; index += 1) {
    if (a[index] !== b[index]) return a[index] > b[index] ? 1 : -1;
  }
  return 0;
}

export function releasePageUrl(version) {
  return `${RELEASES_URL}/tag/v${String(version).replace(/^v/, "")}`;
}

// The CHANGELOG's "## X.Y.Z - date" sections after `since` (none: only `version`'s own)
// up to and including `version`, newest first, each with its heading line.
export function changelogSections(markdown, since, version) {
  if (!versionParts(version)) return [];
  const sections = [];
  let current = null;
  for (const line of String(markdown || "").split("\n")) {
    const heading = line.match(/^## (v?\d+\.\d+\.\d+)\b/);
    if (heading || /^#{1,2} /.test(line)) {
      current = heading ? { version: heading[1].replace(/^v/, ""), lines: [line] } : null;
      if (current) sections.push(current);
    } else if (current) {
      current.lines.push(line);
    }
  }
  const lower = versionParts(since) ? since : null;
  return sections
    .filter((section) => compareVersions(section.version, version) <= 0
      && (lower ? compareVersions(section.version, lower) > 0 : compareVersions(section.version, version) === 0))
    .map((section) => ({ version: section.version, markdown: section.lines.join("\n").trim() }));
}

// Versions before What's new kept no "last version" but did keep other state here. Read
// on the first render, before this launch's effects save anything, it tells an update
// from a first install.
const EARLIER_VERSION = "earlier";
function ranBefore() {
  try {
    const storage = window.localStorage;
    for (let index = 0; index < (storage?.length || 0); index += 1) {
      const key = storage.key(index) || "";
      if (key !== LAST_VERSION_STORAGE_KEY && /^(raticode|gofer)\./.test(key)) return true;
    }
  } catch {
    // No storage: treat it as a first install.
  }
  return false;
}

function readLastVersion() {
  try {
    return window.localStorage?.getItem(LAST_VERSION_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

function storeLastVersion(version) {
  try {
    window.localStorage?.setItem(LAST_VERSION_STORAGE_KEY, version);
  } catch {
    // Storage full or unavailable: the notice shows again next launch.
  }
}

// runningVersion is "" until the running version is known.
export function useWhatsNew(runningVersion) {
  // The version that ran before this one, while the "updated" notice shows.
  const [updatedFrom, setUpdatedFrom] = useState("");
  const [dialog, setDialog] = useState(null);
  const [hadEarlierState] = useState(ranBefore);

  useEffect(() => {
    if (!versionParts(runningVersion)) return;
    const stored = readLastVersion();
    // A first launch, or an unreadable value, only records the version. An update from a
    // version that kept no "last version" shows the notice with this version's notes.
    if (versionParts(stored) && compareVersions(runningVersion, stored) > 0) setUpdatedFrom(stored);
    else if (!stored && hadEarlierState) setUpdatedFrom(EARLIER_VERSION);
    else if (stored !== runningVersion) storeLastVersion(runningVersion);
  }, [hadEarlierState, runningVersion]);

  const acknowledge = useCallback(() => {
    storeLastVersion(runningVersion);
    setUpdatedFrom("");
  }, [runningVersion]);

  // Help › What's New shows the running version's notes, or everything since the last
  // version that ran while the notice is still up.
  const openWhatsNew = useCallback(() => {
    if (!versionParts(runningVersion)) return;
    setDialog({ since: versionParts(updatedFrom) ? updatedFrom : "", version: runningVersion });
    if (updatedFrom) acknowledge();
  }, [acknowledge, runningVersion, updatedFrom]);

  return {
    updatedNotice: updatedFrom ? { version: runningVersion } : null,
    dismissUpdatedNotice: acknowledge,
    openWhatsNew,
    whatsNewDialog: dialog,
    closeWhatsNew: useCallback(() => setDialog(null), []),
  };
}
