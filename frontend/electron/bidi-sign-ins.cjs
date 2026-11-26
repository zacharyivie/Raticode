// Sign-in sharing and cookie clearing for the two browsers Raticode drives
// through WebDriver BiDi: the windowless Firefox (firefox-engine.cjs) and
// Raticode (gecko) (gecko-control.cjs). Rem's tabs live in a Firefox container
// of their own that starts signed out; the sites the user shares get a copy of
// the user's cookies there. Chrome and Edge do the same over DevTools in
// chromium-engine.cjs.
const { cookieMatchesSite } = require("./browser-policy.cjs");

const USER_COOKIE_RULES = new Set(["clearable", "never"]);

// send: the engine's BiDi send. remUserContext: returns Rem's container, or ""
// while there is none. connected: whether the browser is running.
// browserName: how log messages name the browser ("Firefox", "Gecko").
// userCookies: "clearable" lets clearSiteCookies("user", …) clear the user's
// cookies; "never" leaves them alone and returns 0.
function createBidiSignIns({ send, log = () => {}, remUserContext, connected, browserName, userCookies } = {}) {
  if (typeof send !== "function" || typeof remUserContext !== "function" || typeof connected !== "function") {
    throw new TypeError("createBidiSignIns needs send, remUserContext and connected functions.");
  }
  if (!USER_COOKIE_RULES.has(userCookies)) {
    throw new TypeError("createBidiSignIns needs userCookies set to \"clearable\" or \"never\".");
  }

  function partitionFor(userContext) {
    return { type: "storageKey", userContext };
  }

  // Copies the user's cookies for sites into Rem's container, and removes
  // Rem's copies for sites no longer shared.
  async function mirrorSignIns(sites = [], removed = []) {
    const remContext = remUserContext();
    if (!connected() || !remContext) return;
    const remPartition = partitionFor(remContext);
    if (sites.length) {
      const { cookies = [] } = await send("storage.getCookies", { partition: partitionFor("default") });
      for (const cookie of cookies) {
        if (!sites.some((site) => cookieMatchesSite(cookie.domain, site))) continue;
        const copy = {
          name: cookie.name,
          value: cookie.value,
          domain: cookie.domain,
          path: cookie.path,
          httpOnly: cookie.httpOnly,
          secure: cookie.secure,
          sameSite: cookie.sameSite,
          ...(Number.isFinite(cookie.expiry) ? { expiry: cookie.expiry } : {}),
        };
        await send("storage.setCookie", { cookie: copy, partition: remPartition }).catch((error) => {
          log("warn", `Could not share the sign-in for ${String(cookie.domain).replace(/^\./, "")} with Rem's ${browserName} tabs: ${error.message}`);
        });
      }
    }
    if (removed.length) {
      const { cookies = [] } = await send("storage.getCookies", { partition: remPartition });
      for (const cookie of cookies) {
        if (!removed.some((site) => cookieMatchesSite(cookie.domain, site))) continue;
        if (sites.some((site) => cookieMatchesSite(cookie.domain, site))) continue;
        await send("storage.deleteCookies", { filter: { name: cookie.name, domain: cookie.domain }, partition: remPartition }).catch(() => {});
      }
    }
  }

  // Clears the cookies of one site, or of every site when host is empty
  // (then returns -1). kind is "rem" or "user".
  async function clearSiteCookies(kind, host) {
    if (!connected()) return 0;
    if (kind !== "rem" && userCookies === "never") return 0;
    const userContext = kind === "rem" ? remUserContext() : "default";
    if (!userContext) return 0;
    const partition = partitionFor(userContext);
    if (!host) {
      await send("storage.deleteCookies", { partition });
      return -1;
    }
    const { cookies = [] } = await send("storage.getCookies", { partition });
    let removed = 0;
    for (const cookie of cookies) {
      if (!cookieMatchesSite(cookie.domain, host)) continue;
      await send("storage.deleteCookies", { filter: { name: cookie.name, domain: cookie.domain }, partition }).catch(() => {});
      removed += 1;
    }
    return removed;
  }

  return { clearSiteCookies, mirrorSignIns };
}

module.exports = { createBidiSignIns };
