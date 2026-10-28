const test = require("node:test");
const assert = require("node:assert/strict");
const hints = require("../browser-client-hints.cjs");

const CHROME = "156.0.8078.49";
const EMBEDDER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.8078.49 Electron/45.1.0 Safari/537.36";

function linuxHints() {
  return hints.createClientHints(hints.defaultMetadata({ chromeVersion: CHROME, platform: "linux", arch: "x64" }), { embedderUserAgent: EMBEDDER_UA });
}

test("brand lists match Chromium's GREASE algorithm", () => {
  // navigator.userAgentData.brands in Electron 45.1.0 (Chromium 156).
  assert.deepEqual(hints.chromiumBrandList(CHROME), [{ brand: "Not:A-Brand", version: "8" }, { brand: "Chromium", version: "156" }]);
  assert.deepEqual(hints.chromiumBrandList(CHROME, { fullVersion: true }), [
    { brand: "Not:A-Brand", version: "8.0.0.0" },
    { brand: "Chromium", version: "156.0.8078.49" },
  ]);
  // Odd majors put Chromium first; the GREASE brand cycles with the version.
  assert.deepEqual(hints.chromiumBrandList("157.0.1.2"), [{ brand: "Chromium", version: "157" }, { brand: "Not-A.Brand", version: "99" }]);
  assert.deepEqual(hints.chromiumBrandList("124.0.0.0")[0], { brand: "Not-A.Brand", version: "99" });
  assert.equal(hints.serializeBrandList(hints.chromiumBrandList(CHROME)), "\"Not:A-Brand\";v=\"8\", \"Chromium\";v=\"156\"");
  assert.equal(hints.serializeBrandList([{ brand: "A\"B\\C", version: "1" }]), "\"A\\\"B\\\\C\";v=\"1\"");
});

test("the user agent uses Chrome's reduced version format", () => {
  const ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.8078.49 Safari/537.36";
  assert.equal(hints.reducedUserAgent(ua, CHROME), "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36");
  assert.equal(hints.reducedUserAgent(ua, ""), ua);
});

test("Accept-Language follows each regional language with its base language", () => {
  assert.deepEqual(hints.acceptLanguageList(["en-US"]), ["en-US", "en"]);
  assert.deepEqual(hints.acceptLanguageList(["fr_FR.UTF-8", "en-us", "fr"]), ["fr-FR", "fr", "en-US", "en"]);
  assert.deepEqual(hints.acceptLanguageList(["zh-Hant-TW"]), ["zh-Hant-TW", "zh"]);
  assert.deepEqual(hints.acceptLanguageList(["", "not a tag!"]), ["en-US", "en"]);
});

test("only potentially trustworthy URLs get client hints", () => {
  for (const url of ["https://example.com/", "wss://example.com/", "http://localhost:5173/", "http://127.0.0.1/", "http://app.localhost/", "http://[::1]:8080/"]) {
    assert.equal(hints.isPotentiallyTrustworthy(url), true, url);
  }
  for (const url of ["http://example.com/", "http://localhost.evil.test/", "file:///tmp/a.html", "data:text/html,x", "not a url"]) {
    assert.equal(hints.isPotentiallyTrustworthy(url), false, url);
  }
});

test("every secure request gets the low-entropy hints Chromium sends by default", () => {
  const state = linuxHints();
  const headers = state.requestHeaders({
    url: "https://cdn.example.net/app.js",
    firstPartyUrl: "https://example.com/",
    headers: { Accept: "*/*", "Sec-CH-UA": "forged" },
  });
  assert.deepEqual(headers, {
    Accept: "*/*",
    "sec-ch-ua": "\"Not:A-Brand\";v=\"8\", \"Chromium\";v=\"156\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Linux\"",
  });
  const plain = { Accept: "*/*" };
  assert.equal(state.requestHeaders({ url: "http://example.com/", firstPartyUrl: "http://example.com/", headers: plain }), plain);
});

test("high-entropy hints go only to the top-level site that asked for them", () => {
  const state = linuxHints();
  const restart = state.responseNeedsRestart({
    url: "https://accounts.example.com/login",
    method: "GET",
    resourceType: "mainFrame",
    responseHeaders: { "Accept-CH": ["Sec-CH-UA-Full-Version-List, Sec-CH-UA-Arch", "sec-ch-ua-platform-version, Viewport-Width"] },
  });
  assert.equal(restart, false);
  const firstParty = state.requestHeaders({ url: "https://accounts.example.com/api", firstPartyUrl: "https://accounts.example.com/login", headers: {} });
  assert.equal(firstParty["sec-ch-ua-full-version-list"], "\"Not:A-Brand\";v=\"8.0.0.0\", \"Chromium\";v=\"156.0.8078.49\"");
  assert.equal(firstParty["sec-ch-ua-arch"], "\"x86\"");
  assert.equal(firstParty["sec-ch-ua-platform-version"], "\"\"");
  assert.equal(firstParty["viewport-width"], undefined);
  // Embedded in another site, the origin gets only the defaults.
  const thirdParty = state.requestHeaders({ url: "https://accounts.example.com/api", firstPartyUrl: "https://other.test/", headers: {} });
  assert.equal(thirdParty["sec-ch-ua-arch"], undefined);
  // Subframes and non-document responses cannot opt in.
  state.responseNeedsRestart({ url: "https://frame.test/", resourceType: "subFrame", responseHeaders: { "accept-ch": ["Sec-CH-UA-Model"] } });
  assert.equal(state.requestHeaders({ url: "https://frame.test/", firstPartyUrl: "https://frame.test/", headers: {} })["sec-ch-ua-model"], undefined);
  state.clear("https://accounts.example.com");
  assert.equal(state.requestHeaders({ url: "https://accounts.example.com/api", firstPartyUrl: "https://accounts.example.com/", headers: {} })["sec-ch-ua-arch"], undefined);
});

test("Critical-CH restarts a GET navigation once when a needed hint was missing", () => {
  const state = linuxHints();
  const response = {
    url: "https://app.test/",
    method: "GET",
    resourceType: "mainFrame",
    responseHeaders: { "accept-ch": ["Sec-CH-UA-Form-Factors"], "critical-ch": ["Sec-CH-UA-Form-Factors"] },
  };
  assert.equal(state.responseNeedsRestart(response), true);
  // The replayed request carries the hint, so the response is accepted.
  assert.equal(state.requestHeaders({ url: "https://app.test/", firstPartyUrl: "https://app.test/", headers: {} })["sec-ch-ua-form-factors"], "\"Desktop\"");
  assert.equal(state.responseNeedsRestart(response), false);
  // Never replays a form submission.
  assert.equal(linuxHints().responseNeedsRestart({ ...response, method: "POST" }), false);
  // Critical-CH without a matching Accept-CH asks for nothing.
  assert.equal(linuxHints().responseNeedsRestart({ ...response, responseHeaders: { "critical-ch": ["Sec-CH-UA-Arch"] } }), false);
});

test("Google sign-in hosts get Electron's own user agent, other hosts keep theirs", () => {
  const state = linuxHints();
  for (const url of ["https://accounts.google.com/v3/signin/identifier", "https://accounts.google.co.uk/CheckCookie", "https://accounts.google.de/", "https://accounts.youtube.com/accounts/SetSID"]) {
    const headers = state.requestHeaders({ url, firstPartyUrl: url, headers: { "user-agent": "Chrome-shaped" } });
    assert.equal(headers["User-Agent"], EMBEDDER_UA, url);
    assert.equal(headers["user-agent"], undefined, url);
    assert.equal(hints.prefersEmbedderIdentity(url), true, url);
  }
  for (const url of ["https://mail.google.com/", "https://accounts.google.com.evil.test/", "https://evilaccounts.google.com/", "http://accounts.google.com/", "https://github.com/login"]) {
    const headers = state.requestHeaders({ url, firstPartyUrl: url, headers: { "User-Agent": "Chrome-shaped" } });
    assert.equal(headers["User-Agent"] ?? "Chrome-shaped", "Chrome-shaped", url);
    assert.equal(hints.prefersEmbedderIdentity(url), false, url);
  }
  // Without an embedder user agent the header is left alone.
  const plain = hints.createClientHints(hints.defaultMetadata({ chromeVersion: CHROME }));
  assert.equal(plain.requestHeaders({ url: "https://accounts.google.com/", firstPartyUrl: "", headers: { "User-Agent": "x" } })["User-Agent"], "x");
});

test("sampled renderer metadata replaces defaults only with well-formed values", () => {
  const base = hints.defaultMetadata({ chromeVersion: CHROME, platform: "win32", arch: "arm64" });
  assert.equal(base.platform, "Windows");
  assert.equal(base.architecture, "arm");
  assert.equal(base.bitness, "64");
  const merged = hints.mergeMetadata(base, {
    platformVersion: "15.0.0",
    architecture: "x86",
    brands: [{ brand: "Chromium", version: "156" }, { brand: "Not:A-Brand", version: "8" }],
    fullVersionList: "garbage",
    wow64: "yes",
  });
  assert.equal(merged.platformVersion, "15.0.0");
  assert.equal(merged.architecture, "x86");
  assert.deepEqual(merged.brands, [{ brand: "Chromium", version: "156" }, { brand: "Not:A-Brand", version: "8" }]);
  assert.deepEqual(merged.fullVersionList, base.fullVersionList);
  assert.equal(merged.wow64, false);
  assert.equal(hints.mergeMetadata(base, null), base);
  const state = hints.createClientHints(base);
  state.setMetadata(merged);
  assert.equal(state.requestHeaders({ url: "https://a.test/", firstPartyUrl: "", headers: {} })["sec-ch-ua"], "\"Chromium\";v=\"156\", \"Not:A-Brand\";v=\"8\"");
});
