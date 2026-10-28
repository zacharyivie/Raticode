const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const policy = require("../browser-policy.cjs");

test("each kind of content gets its own profile", () => {
  assert.deepEqual(policy.PARTITIONS, {
    user: "persist:raticode-browser",
    rem: "persist:raticode-rem",
    preview: "raticode-preview",
  });
  // Previews never persist cookies or storage.
  assert.equal(policy.PARTITIONS.preview.startsWith("persist:"), false);
});

test("site patterns normalize and match subdomains only", () => {
  assert.equal(policy.normalizeSitePattern("https://GitHub.com/settings"), "github.com");
  assert.equal(policy.normalizeSitePattern("*.example.com"), "example.com");
  assert.equal(policy.normalizeSitePattern("user:pass@example.com"), "");
  assert.equal(policy.normalizeSitePattern("  "), "");
  assert.deepEqual(policy.normalizeSiteList("github.com, https://gitlab.com\nGITHUB.com bad..host"), ["github.com", "gitlab.com"]);
  assert.equal(policy.hostMatches("api.github.com", "github.com"), true);
  assert.equal(policy.hostMatches("github.com", "github.com"), true);
  assert.equal(policy.hostMatches("evilgithub.com", "github.com"), false);
  assert.equal(policy.cookieMatchesSite(".github.com", "github.com"), true);
  assert.equal(policy.cookieMatchesSite("gist.github.com", "github.com"), true);
  // A site receives cookies its parent domain sets.
  assert.equal(policy.cookieMatchesSite(".google.com", "mail.google.com"), true);
  assert.equal(policy.cookieMatchesSite("example.org", "github.com"), false);
});

test("security state reflects transport and certificate exceptions", () => {
  assert.equal(policy.securityState({ url: "https://example.com/" }).level, "secure");
  assert.equal(policy.securityState({ url: "https://example.com/", certificateException: true }).level, "warning");
  assert.equal(policy.securityState({ url: "https://example.com/", certificateError: { host: "example.com" } }).level, "error");
  assert.deepEqual(policy.securityState({ url: "http://example.com/" }), { level: "insecure", label: "Not secure" });
  assert.equal(policy.securityState({ url: "http://localhost:5173/" }).level, "local");
  assert.equal(policy.securityState({ url: "http://app.localhost/" }).level, "local");
  assert.equal(policy.securityState({ url: "file:///tmp/report.html" }).level, "local");
  assert.equal(policy.securityState({ url: "about:blank" }).level, "internal");
});

test("zoom steps through Chromium's levels and clamps", () => {
  assert.equal(policy.nextZoomLevel(1, 1), 1.1);
  assert.equal(policy.nextZoomLevel(1, -1), 0.9);
  assert.equal(policy.nextZoomLevel(1.05, 1), 1.1);
  assert.equal(policy.nextZoomLevel(5, 1), 5);
  assert.equal(policy.nextZoomLevel(0.25, -1), 0.25);
  assert.equal(policy.clampZoom(9), 5);
  assert.equal(policy.clampZoom(Number.NaN), 1);
});

test("downloads get safe, unique names and dangerous types are flagged", () => {
  assert.equal(policy.sanitizeFilename("../../etc/passwd"), "passwd");
  assert.equal(policy.sanitizeFilename("a<b>:c?.txt"), "a_b__c_.txt");
  assert.equal(policy.sanitizeFilename("CON.txt"), "download");
  assert.equal(policy.sanitizeFilename("..."), "download");
  const taken = new Set([path.join("/d", "report.pdf"), path.join("/d", "report (1).pdf")]);
  assert.equal(policy.uniqueDownloadPath("/d", "report.pdf", (candidate) => taken.has(candidate)), path.join("/d", "report (2).pdf"));
  assert.equal(policy.uniqueDownloadPath("/d", "notes", () => false), path.join("/d", "notes"));
  for (const name of ["setup.exe", "run.SH", "x.AppImage", "macro.docm", "tool.jar"]) assert.equal(policy.isDangerousDownload(name), true, name);
  for (const name of ["photo.png", "report.pdf", "archive.zip", "data.csv"]) assert.equal(policy.isDangerousDownload(name), false, name);
});

test("popups need a user gesture and sized popups become windows", () => {
  const now = 100000;
  const recent = now - 1000;
  assert.deepEqual(policy.popupDecision({ url: "https://example.com/", now, lastUserInputAt: 0 }), { kind: "blocked" });
  assert.deepEqual(policy.popupDecision({ url: "https://example.com/", now, lastUserInputAt: 0, popupsAllowed: true }), { kind: "tab", background: false });
  assert.deepEqual(policy.popupDecision({ url: "https://example.com/", now, lastUserInputAt: recent }), { kind: "tab", background: false });
  assert.deepEqual(policy.popupDecision({ url: "https://example.com/", disposition: "background-tab", now, lastUserInputAt: recent }), { kind: "tab", background: true });
  assert.deepEqual(
    policy.popupDecision({ url: "https://accounts.example.com/oauth", features: "width=480,height=640", now, lastUserInputAt: recent }),
    { kind: "window", width: 480, height: 640 },
  );
  // Sign-in libraries often open a blank popup, then navigate it.
  assert.equal(policy.popupDecision({ url: "about:blank", now, lastUserInputAt: recent }).kind, "window");
  assert.equal(policy.popupDecision({ url: "https://example.com/", features: "noopener", now, lastUserInputAt: recent }).kind, "tab");
  assert.deepEqual(policy.popupDecision({ url: "data:text/html,<h1>x", now, lastUserInputAt: recent }), { kind: "deny" });
  assert.deepEqual(policy.popupDecision({ url: "javascript:alert(1)", now, lastUserInputAt: recent }), { kind: "deny" });
  // Rem never opens windows; it follows links in its own tab.
  assert.deepEqual(policy.popupDecision({ url: "https://example.com/", agent: true }), { kind: "same-tab" });
  assert.deepEqual(policy.popupDecision({ url: "about:blank", agent: true }), { kind: "deny" });
  assert.deepEqual(policy.parseWindowFeatures("width=99999,height=10"), { popup: true, width: 1600, height: 200 });
});

test("permissions prompt per site, remember decisions and deny Rem", () => {
  const media = { mediaTypes: ["video", "audio"] };
  assert.deepEqual(policy.permissionKeys("media", media), ["camera", "microphone"]);
  assert.equal(policy.permissionDecision({ permission: "media", details: media }), "prompt");
  assert.equal(policy.permissionDecision({ permission: "media", details: media, stored: { camera: "allow", microphone: "allow" } }), "allow");
  assert.equal(policy.permissionDecision({ permission: "media", details: media, stored: { camera: "allow" } }), "prompt");
  assert.equal(policy.permissionDecision({ permission: "media", details: media, stored: { camera: "allow", microphone: "block" } }), "deny");
  assert.equal(policy.permissionDecision({ permission: "notifications" }), "prompt");
  assert.equal(policy.permissionDecision({ permission: "clipboard-sanitized-write" }), "allow");
  assert.equal(policy.permissionDecision({ permission: "fullscreen" }), "allow");
  assert.equal(policy.permissionDecision({ permission: "hid" }), "deny");
  assert.equal(policy.permissionDecision({ permission: "notifications", agent: true }), "deny");
  assert.equal(policy.permissionDecision({ permission: "fullscreen", agent: true }), "deny");
  assert.equal(policy.permissionDecision({ permission: "clipboard-sanitized-write", agent: true }), "allow");
  assert.equal(policy.permissionDecision({ permission: "openExternal", details: { externalURL: "mailto:a@example.com" } }), "prompt");
  assert.equal(policy.permissionDecision({ permission: "openExternal", details: { externalURL: "mailto:a@example.com" }, stored: { "openExternal:mailto:": "allow" } }), "allow");
  // Handlers abused for code execution are never offered.
  assert.equal(policy.permissionDecision({ permission: "openExternal", details: { externalURL: "ms-msdt:/id" } }), "deny");
  assert.equal(policy.permissionDecision({ permission: "openExternal", details: { externalURL: "file:///etc/passwd" } }), "deny");
});

test("load errors are explained in plain language", () => {
  assert.equal(policy.classifyLoadError(-106).kind, "offline");
  assert.equal(policy.classifyLoadError(-105).kind, "dns");
  assert.equal(policy.classifyLoadError(-7).kind, "timeout");
  assert.equal(policy.classifyLoadError(-102).kind, "refused");
  assert.equal(policy.classifyLoadError(-202).kind, "certificate");
  assert.equal(policy.classifyLoadError(-999, "ERR_SOMETHING").detail, "ERR_SOMETHING");
  assert.equal(policy.isCertificateErrorCode(-201), true);
  assert.equal(policy.isCertificateErrorCode(-105), false);
});

test("the user agent drops embedder tokens", () => {
  const ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) raticode/0.3.9 Chrome/146.0.7680.216 Electron/41.10.7 Safari/537.36";
  assert.equal(policy.userAgentWithoutEmbedderTokens(ua), "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.7680.216 Safari/537.36");
  // App names with spaces, such as a dev build, and any order of tokens.
  const dev = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Raticode Dev/0.3.9 Chrome/156.0.8078.49 Electron/45.1.0 Safari/537.36";
  assert.equal(policy.userAgentWithoutEmbedderTokens(dev), "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.8078.49 Safari/537.36");
  assert.equal(policy.userAgentWithoutEmbedderTokens(dev, { keepElectron: true }), "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.8078.49 Electron/45.1.0 Safari/537.36");
  assert.equal(policy.userAgentWithoutEmbedderTokens("Mozilla/5.0 Chrome/1.0 gofer-flow-ui/2 Electron/3 Safari/537.36"), "Mozilla/5.0 Chrome/1.0 Safari/537.36");
});

test("browser policy from the studio is sanitized", () => {
  const value = policy.sanitizeBrowserPolicy({
    searchUrl: "javascript:{query}",
    remSignInSites: ["GitHub.com", "", "github.com"],
    remConfirm: "sometimes",
    remEvaluate: false,
    remDownloads: "allow",
  });
  assert.equal(value.searchUrl, policy.DEFAULT_BROWSER_POLICY.searchUrl);
  assert.deepEqual(value.remSignInSites, ["github.com"]);
  assert.equal(value.remConfirm, "signed-in");
  assert.equal(value.remEvaluate, false);
  assert.equal(value.remDownloads, "allow");
  assert.equal(policy.sanitizeBrowserPolicy(null).remDownloads, "block");
});

test("Rem's site lists gate navigation", () => {
  assert.equal(policy.remSiteAllowed("https://example.com/", {}), true);
  assert.equal(policy.remSiteAllowed("file:///etc/passwd", {}), false);
  assert.equal(policy.remSiteAllowed("about:blank", { remAllowedSites: ["x.com"] }), true);
  assert.equal(policy.remSiteAllowed("https://docs.x.com/", { remAllowedSites: ["x.com"] }), true);
  assert.equal(policy.remSiteAllowed("https://y.com/", { remAllowedSites: ["x.com"] }), false);
  assert.equal(policy.remSiteAllowed("https://pay.x.com/", { remAllowedSites: ["x.com"], remBlockedSites: ["pay.x.com"] }), false);
  assert.equal(policy.remNeedsConfirmation({ policy: { remConfirm: "signed-in", remSignInSites: ["x.com"] }, url: "https://x.com/", consequential: true }), true);
});

test("pages cannot flood the studio with tabs", () => {
  const session = {};
  let allowed = 0;
  for (let index = 0; index < 25; index += 1) if (policy.allowPageTab(session, 1000)) allowed += 1;
  assert.equal(allowed, 10);
  assert.equal(policy.allowPageTab(session, 1000 + 10001), true);
});
