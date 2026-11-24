// Links and web pages that other apps open with Raticode, once Raticode is the
// computer's default browser (see electron/default-browser.cjs). This decides
// where each one goes; App.jsx does the opening.

export const EXTERNAL_ENGINE_OPTIONS = [
  ["raticode", "Raticode (chromium)"],
  ["gecko", "Raticode (gecko)"],
];

export function externalEngine(value) {
  return value === "gecko" ? "gecko" : "raticode";
}

// Why Raticode (gecko) can't take a link right now, or "" when it can. `status`
// is the main process's gecko("status") answer; null means it couldn't be read.
export function geckoUnavailableReason(status) {
  if (!status || typeof status !== "object") return "Raticode (gecko) didn't answer";
  if (status.problem) return String(status.problem).replace(/[.\s]+$/, "");
  if (status.needsRestart) return "Raticode (gecko) needs Raticode to restart first";
  if (!status.executable) return "Firefox isn't installed";
  return "";
}

// Where one handed-over item opens.
// - A web address opens as a new tab in the chosen browser. Raticode (gecko) is
//   one Firefox window inside one Raticode tab, so when that tab is open the
//   address becomes a Firefox tab in it, and the notice says so. When Gecko
//   can't run here the address opens in Raticode (chromium) and the notice says why.
// - A web-page file opens in Raticode's file preview, which is Raticode
//   (chromium)'s local preview whichever browser is chosen, and is kept out of
//   what Rem sees of the editor.
export function externalOpenPlan(item, { engine = "raticode", geckoStatus = null, geckoTabOpen = false } = {}) {
  if (item?.kind === "file" && typeof item.path === "string" && item.path) {
    return { kind: "file", path: item.path };
  }
  if (item?.kind !== "url" || typeof item.url !== "string" || !/^https?:\/\//i.test(item.url)) return null;
  if (externalEngine(engine) !== "gecko") return { kind: "url", url: item.url, engine: "raticode", notice: "" };
  const reason = geckoUnavailableReason(geckoStatus);
  if (reason) {
    return {
      kind: "url",
      url: item.url,
      engine: "raticode",
      notice: `${reason}, so the link opened in Raticode (chromium) instead.`,
    };
  }
  return {
    kind: "url",
    url: item.url,
    engine: "gecko",
    notice: geckoTabOpen ? "Raticode (gecko) is one Firefox window, so the link opened as a new Firefox tab in it." : "",
  };
}
