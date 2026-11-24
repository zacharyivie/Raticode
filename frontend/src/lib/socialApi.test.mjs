import assert from "node:assert/strict";
import test from "node:test";
import {
  accountNotice, bulkApprovable, fillBlanks, foldText, slotLabel, templateBlanks, templateBody, templateDraft, timeTargets, charCounter, countChars, duplicateDraft, durationLabel, fromLocalInput, groupPosts, laneFor, lineDiff, linkParts,
  normalizeDomain, optionLines, quickTimes, sourceLabel, timeZoneChoices, toLocalInput, variantActions, weekStrip, zonedInput, zonedToIso,
} from "./socialApi.js";
import {
  CONNECT_ORDER, NO_SIGN_IN_NOTE, SIGN_IN_GUIDES, connectBody, connectChoices, defaultChoice, defaultMethod, guideFields, guideMethods, guideSteps, methodLabel,
  signInBody, signInFields, signInMessage, signInRedirect, signInSteps, validateConnect, validateSignIn,
} from "./socialPlatforms.js";

const post = (state, variants = [], extra = {}) => ({ id: `${state}-${variants.map(v => v.state).join("-")}`, state, variants, ...extra });

test("posts land in the lane their state calls for", () => {
  for (const state of ["draft", "proposed", "changes_requested"]) assert.equal(laneFor(post(state)), "review");
  for (const state of ["approved", "scheduled"]) assert.equal(laneFor(post(state, [{ state: "scheduled" }])), "scheduled");
  assert.equal(laneFor(post("due", [{ state: "due" }])), "due");
  // A due LinkedIn variant puts the post in Due now even while X waits.
  assert.equal(laneFor(post("scheduled", [{ state: "scheduled" }, { state: "due" }])), "due");
  assert.equal(laneFor(post("published", [{ state: "published" }, { state: "posted_by_you" }])), "published");
  for (const state of ["failed", "missed", "needs_attention", "partly_published"]) assert.equal(laneFor(post(state)), "problems");
  for (const variant of ["failed", "missed", "needs_reconnect", "outcome_unknown", "held"]) assert.equal(laneFor(post("scheduled", [{ state: variant }])), "problems");
  for (const state of ["declined", "cancelled", "expired"]) assert.equal(laneFor(post(state)), "closed");
});

test("grouping sorts scheduled posts by time and keeps closed ones apart", () => {
  const lanes = groupPosts([
    post("scheduled", [{ state: "scheduled" }], { id: "late", scheduled_at: "2026-11-20T10:00:00Z" }),
    post("scheduled", [{ state: "scheduled" }], { id: "soon", scheduled_at: "2026-11-18T10:00:00Z" }),
    post("declined", [], { id: "gone" }),
  ]);
  assert.deepEqual(lanes.scheduled.map(item => item.id), ["soon", "late"]);
  assert.deepEqual(lanes.closed.map(item => item.id), ["gone"]);
  assert.deepEqual(lanes.review, []);
});

test("X counts each link as 23 characters; other platforms count characters", () => {
  assert.equal(countChars("hi https://raticode.dev/a/very/long/path/indeed", "x"), 3 + 23);
  assert.equal(countChars("héllo 👋", "discord"), 7);
  const over = charCounter({ platform: "x", text: "a".repeat(270), link: "https://raticode.dev", limit: 280 });
  assert.deepEqual(over, { count: 271 + 23, limit: 280, over: true });
  // LinkedIn and Facebook send the link as a preview, not in the text.
  assert.equal(charCounter({ platform: "linkedin", text: "abc", link: "https://raticode.dev", limit: 3000 }).count, 3);
});

test("links are split so the domain can be highlighted", () => {
  const parts = linkParts({ text: "See https://raticode.dev/docs.", link: "https://evil.example/x" });
  assert.deepEqual(parts.map(part => part.host), ["raticode.dev", "evil.example"]);
  assert.equal(parts[0].before + parts[0].host + parts[0].after, "https://raticode.dev/docs");
});

test("datetime-local values round-trip through UTC", () => {
  const iso = "2026-11-18T15:30:00.000Z";
  assert.equal(fromLocalInput(toLocalInput(iso)), iso);
  assert.equal(fromLocalInput(""), null);
  assert.equal(fromLocalInput("not a date"), null);
  assert.equal(toLocalInput(null), "");
});

test("sources, notices and actions read in words", () => {
  assert.equal(sourceLabel("rem"), "Rem");
  assert.equal(sourceLabel("workflow:release-notes@abc"), "Workflow release-notes");
  assert.equal(sourceLabel("task:123"), "A Rem task");
  assert.equal(sourceLabel("user"), "You");
  assert.equal(accountNotice({ platform_label: "LinkedIn", status: "reconnect_soon", expires_in_days: 9 }), "LinkedIn: reconnect in 9 days");
  assert.equal(accountNotice({ platform_label: "X", status: "needs_reconnect" }), "X: reconnect now");
  assert.equal(accountNotice({ platform_label: "Discord", status: "connected", expires_in_days: null }), "");
  assert.deepEqual(variantActions({ delivery: "tap_to_publish", state: "due" }), ["publish"]);
  assert.deepEqual(variantActions({ delivery: "api", state: "outcome_unknown" }), ["it-posted", "send-again"]);
  assert.deepEqual(variantActions({ delivery: "manual", state: "ready_to_post" }), ["copy", "open", "mark-posted"]);
  assert.deepEqual(variantActions({ delivery: "manual", state: "pending" }), []);
  assert.deepEqual(variantActions({ delivery: "api", state: "scheduled" }), []);
});

test("the diff marks added and removed lines", () => {
  assert.deepEqual(lineDiff("a\nb\nc", "a\nB\nc"), [
    { kind: "same", text: "a" }, { kind: "removed", text: "b" }, { kind: "added", text: "B" }, { kind: "same", text: "c" },
  ]);
});

test("the week strip counts what goes out on each of the next seven days", () => {
  const now = new Date(2026, 10, 16, 9, 0);
  const days = weekStrip([
    post("scheduled", [{ state: "scheduled" }], { scheduled_at: new Date(2026, 10, 17, 10).toISOString() }),
    post("published", [{ state: "published" }], { scheduled_at: new Date(2026, 10, 17, 11).toISOString() }),
    post("scheduled", [{ state: "scheduled" }], { scheduled_at: new Date(2026, 10, 30, 10).toISOString() }),
  ], now);
  assert.equal(days.length, 7);
  assert.deepEqual(days.map(day => day.items.length), [0, 1, 0, 0, 0, 0, 0]);
});

test("a time typed for another time zone turns into the right UTC moment and back", () => {
  assert.equal(zonedToIso("2031-12-01T09:00", "America/Denver"), "2031-12-01T16:00:00.000Z");
  assert.equal(zonedToIso("2031-07-01T09:00", "Europe/Berlin"), "2031-07-01T07:00:00.000Z");
  assert.equal(zonedToIso("2031-07-01T09:00", "UTC"), "2031-07-01T09:00:00.000Z");
  // Across the November daylight-saving change in New York (UTC-4 before, UTC-5 after).
  assert.equal(zonedToIso("2031-11-01T12:00", "America/New_York"), "2031-11-01T16:00:00.000Z");
  assert.equal(zonedToIso("2031-11-03T12:00", "America/New_York"), "2031-11-03T17:00:00.000Z");
  assert.equal(zonedInput("2031-12-01T16:00:00Z", "America/Denver"), "2031-12-01T09:00");
  assert.equal(zonedInput("2031-12-01T16:00:00Z", "Asia/Kolkata"), "2031-12-01T21:30");
  assert.equal(zonedToIso("", "UTC"), null);
  assert.equal(zonedToIso("not a time", "UTC"), null);
  assert.equal(zonedInput(null), "");
  const zones = timeZoneChoices("Mars/Olympus_Mons");
  assert.ok(zones.includes("UTC") && zones.includes("Mars/Olympus_Mons"));
});

test("late limits, durations and trusted sites read in plain words", () => {
  assert.equal(durationLabel(15), "15 minutes");
  assert.equal(durationLabel(60), "1 hour");
  assert.equal(durationLabel(150), "2 hours 30 minutes");
  assert.equal(durationLabel(1440), "1 day");
  assert.equal(normalizeDomain("https://Docs.Example.com/path?q=1"), "docs.example.com");
  assert.equal(normalizeDomain("example.com:8080"), "example.com");
  assert.equal(normalizeDomain(" github.com. "), "github.com");
  assert.equal(normalizeDomain("not a domain"), "");
  assert.equal(normalizeDomain("localhost"), "");
});

test("quick picks are an hour away, tomorrow morning and next Monday morning", () => {
  const now = new Date("2031-12-03T20:00:00Z"); // a Wednesday, 3 PM in New York
  const picks = quickTimes(now, "America/New_York");
  assert.deepEqual(picks.map(item => item.label), ["In an hour", "Tomorrow, 9 AM", "Monday, 9 AM"]);
  assert.equal(picks[1].at, "2031-12-04T14:00:00.000Z");
  assert.equal(picks[2].at, "2031-12-08T14:00:00.000Z");
  assert.ok(Date.parse(picks[0].at) >= now.getTime() + 3600_000);
});

test("a duplicate keeps accounts, text and stored media but drops ids, time and approval", () => {
  const draft = duplicateDraft({
    id: "p1", title: "Launch", state: "published", scheduled_at: "2031-01-01T10:00:00Z", timezone: "America/Denver", content_hash: "h", late_window_minutes: 30,
    variants: [{ id: "v1", account_id: "x-1", platform: "x", text: "Hi", link: "https://raticode.dev", options: { a: 1 }, media: [{ sha256: "a".repeat(64), name: "shot.png", size: 4 }], state: "published", url: "https://x.com/1" }],
  });
  assert.equal(draft.title, "Copy of Launch");
  assert.equal(draft.scheduled_at, null);
  assert.equal(draft.timezone, "America/Denver");
  assert.equal(draft.late_window_minutes, 30);
  assert.deepEqual(draft.variants, [{ account_id: "x-1", platform: "x", text: "Hi", link: "https://raticode.dev", options: { a: 1 }, media: [{ sha256: "a".repeat(64), name: "shot.png" }] }]);
  assert.ok(!("content_hash" in draft) && !("id" in draft));
});

test("only clean posts in review can be approved together", () => {
  const platforms = [{ id: "x", text_limit: 280 }];
  const clean = { state: "proposed", flags: [], variants: [{ platform: "x", text: "ok" }] };
  assert.equal(bulkApprovable(clean, platforms), true);
  assert.equal(bulkApprovable({ ...clean, flags: [{ kind: "link", message: "x" }] }, platforms), false);
  assert.equal(bulkApprovable({ ...clean, variants: [{ platform: "x", text: "a".repeat(281) }] }, platforms), false);
  assert.equal(bulkApprovable({ ...clean, state: "scheduled" }, platforms), false);
  assert.equal(bulkApprovable({ ...clean, variants: [] }, platforms), false);
});

test("the Connect form checks each platform's login before sending it", () => {
  assert.deepEqual(CONNECT_ORDER, ["discord", "x", "linkedin", "facebook", "youtube"]);
  for (const id of CONNECT_ORDER) for (const method of guideMethods(id)) assert.ok(guideSteps(id, method.id).length >= 3, `${id} ${method.id}`);
  assert.match(validateConnect("discord", {}).webhook_url, /Paste the webhook URL/);
  assert.match(validateConnect("discord", { webhook_url: "https://example.com/hook" }).webhook_url, /isn't a Discord webhook/);
  assert.deepEqual(validateConnect("discord", { webhook_url: "https://discord.com/api/webhooks/123/abc-DEF_1" }), {});
  assert.deepEqual(validateConnect("discord", {}, { replacing: true }), {});
  assert.match(validateConnect("x", { access_token: "abc def" }, { method: "oauth2" }).access_token, /spaces/);
  assert.match(validateConnect("x", { access_token: "a", refresh_token: "r" }, { method: "oauth2" }).client_id, /client ID/);
  assert.match(validateConnect("facebook", { page_access_token: "t", page_id: "my-page" }).page_id, /only numbers/);
  // The Page ID is a setting, not a login, so replacing the token still needs it.
  assert.ok(validateConnect("facebook", {}, { replacing: true }).page_id);
  assert.deepEqual(Object.keys(validateConnect("youtube", {})).sort(), ["client_id", "client_secret", "refresh_token"]);
  assert.deepEqual(connectBody("facebook", " Page ", { page_access_token: " t ", page_id: "42" }), { platform: "facebook", name: "Page", secrets: { page_access_token: "t" }, options: { page_id: "42" } });
  assert.deepEqual(connectBody("youtube", "", { refresh_token: "r", client_id: "c", client_secret: "s", access_token: "" }, { id: "youtube-1", privacy: "unlisted" }),
    { id: "youtube-1", platform: "youtube", name: "", secrets: { refresh_token: "r", client_id: "c", client_secret: "s" }, options: { privacy: "unlisted" } });
  assert.ok(guideFields("x").every(field => field.target === "secrets"));
});

test("X connects with API keys by default and keeps OAuth 2.0 as the other way", () => {
  assert.deepEqual(guideMethods("x").map(method => method.id), ["api_keys", "oauth2"]);
  assert.equal(defaultMethod("x"), "api_keys");
  assert.equal(defaultMethod("discord"), "default");
  assert.deepEqual(guideFields("x").map(field => field.key), ["api_key", "api_secret", "access_token", "access_token_secret"]);
  assert.ok(guideFields("x").every(field => field.required));
  assert.deepEqual(guideFields("x", "oauth2").map(field => field.key), ["access_token", "refresh_token", "client_id", "client_secret"]);
  // The guide no longer says to generate an OAuth 2.0 token in the console, which X can't do.
  assert.ok(!guideSteps("x", "oauth2").some(step => /generate your user access token/i.test(step)));
  assert.ok(guideSteps("x").some(step => /Read and write/.test(step)));
  assert.deepEqual(Object.keys(validateConnect("x", {})).sort(), ["access_token", "access_token_secret", "api_key", "api_secret"]);
  assert.deepEqual(Object.keys(validateConnect("x", {}, { method: "oauth2" })), ["access_token"]);
  assert.deepEqual(validateConnect("x", {}, { replacing: true }), {});
  // A refresh token without a client ID only matters for OAuth 2.0.
  assert.deepEqual(validateConnect("x", { api_key: "k", api_secret: "s", access_token: "1-t", access_token_secret: "ts", refresh_token: "r" }), {});
  const keys = { api_key: " k ", api_secret: "s", access_token: "1-t", access_token_secret: "ts", refresh_token: "left over" };
  assert.deepEqual(connectBody("x", "Me", keys),
    { platform: "x", name: "Me", auth: "api_keys", secrets: { api_key: "k", api_secret: "s", access_token: "1-t", access_token_secret: "ts" }, options: {} });
  assert.deepEqual(connectBody("x", "", { access_token: "t", api_key: "k" }, { method: "oauth2", id: "x-1" }),
    { id: "x-1", platform: "x", name: "", auth: "oauth2", secrets: { access_token: "t" }, options: {} });
  assert.equal("auth" in connectBody("discord", "", { webhook_url: "w" }), false);
  assert.equal(methodLabel("x", "api_keys"), "API keys");
  assert.equal(methodLabel("x", undefined), "OAuth 2.0 token");
  assert.equal(methodLabel("discord", undefined), "");
});

test("best-time targets are accounts or pasted-in sites, and slots read in the post's zone", () => {
  assert.deepEqual(timeTargets([{ account_id: "x-1", platform: "x" }, { account_id: null, platform: "patreon" }, { account_id: "x-1", platform: "x" }]), ["x-1", "patreon"]);
  assert.match(slotLabel("2031-12-04T15:00:00Z", "America/New_York"), /10:00/);
  assert.equal(slotLabel("not a time"), "not a time");
});

test("the see-more fold follows the platform's characters or lines, whichever comes first", () => {
  const linkedin = { fold_chars: 210, fold_lines: 3 };
  assert.deepEqual(foldText("short", linkedin), { shown: "short", rest: "" });
  assert.deepEqual(foldText("a".repeat(250), linkedin), { shown: "a".repeat(210), rest: "a".repeat(40) });
  assert.deepEqual(foldText("one\ntwo\nthree\nfour", linkedin), { shown: "one\ntwo\nthree", rest: "\nfour" });
  assert.deepEqual(foldText("one\ntwo\nthree\n\n", linkedin), { shown: "one\ntwo\nthree\n\n", rest: "" });
  assert.deepEqual(foldText("x".repeat(500), { fold_chars: 0 }), { shown: "x".repeat(500), rest: "" });
});

test("template blanks match the backend's rule and fill only what has a value", () => {
  assert.deepEqual(templateBlanks(["{product} {version} is out", "if (x) { return {} }", "{version} {what changed}"]), ["product", "version", "what changed"]);
  assert.equal(fillBlanks("{product} {version} {missing}", { product: "Raticode", version: " 0.6 ", missing: " " }), "Raticode 0.6 {missing}");
});

test("a template draft goes to your accounts, keeps pasted-in sites and names what it skipped", () => {
  const template = { title: "{v} is out", variants: [
    { platform: "x", text: "{v} is out" }, { platform: "linkedin", text: "Big news: {v}" }, { platform: "patreon", text: "Patrons: {v}" },
    { platform: "discord", text: "{v}", link: "https://raticode.dev/{v}" },
  ] };
  const accounts = [{ id: "x-1", platform: "x", enabled: true }, { id: "d-1", platform: "discord", enabled: true }];
  const platforms = [{ id: "x", label: "X", api: true }, { id: "linkedin", label: "LinkedIn", api: true }, { id: "patreon", label: "Patreon", api: false }, { id: "discord", label: "Discord", api: true }];
  const { draft, skipped } = templateDraft(template, { v: "0.6" }, accounts, platforms);
  assert.equal(draft.title, "0.6 is out");
  assert.deepEqual(draft.variants.map(item => [item.account_id, item.platform, item.text]), [["x-1", "x", "0.6 is out"], [null, "patreon", "Patrons: 0.6"], ["d-1", "discord", "0.6"]]);
  assert.equal(draft.variants[2].link, "https://raticode.dev/0.6");
  assert.equal(draft.scheduled_at, null);
  assert.deepEqual(skipped, ["LinkedIn"]);
  assert.deepEqual(templateBody(" Release ", { title: "{v}", variants: [{ platform: "x", text: "{v}", link: "" }] }), { name: "Release", title: "{v}", variants: [{ platform: "x", text: "{v}" }] });
});

test("the review card lists every option a post sends, even ones it doesn't know", () => {
  const lines = optionLines({ options: { title: "Shown above", article_title: "Release notes", visibility: "CONNECTIONS", new_field: "Still shown", empty: "" } });
  assert.deepEqual(lines.map(line => [line.label, line.value]), [
    ["Link card title", "Release notes"], ["new_field", "Still shown"], ["Who can see it", "Connections only"],
  ]);
  assert.deepEqual(optionLines({}), []);
});

test("every OAuth platform signs in through the browser; Discord has no sign-in", () => {
  assert.deepEqual(Object.keys(SIGN_IN_GUIDES).sort(), ["facebook", "linkedin", "x", "youtube"]);
  assert.deepEqual(connectChoices("discord").map(item => item.id), ["default"]);
  assert.match(NO_SIGN_IN_NOTE, /webhook/);
  // X keeps API keys as the default; the others sign in by default and keep the paste way.
  assert.deepEqual(connectChoices("x").map(item => item.id), ["api_keys", "signin", "oauth2"]);
  assert.equal(defaultChoice("x"), "api_keys");
  for (const id of ["youtube", "linkedin", "facebook"]) {
    assert.deepEqual(connectChoices(id).map(item => item.id), ["signin", "default"]);
    assert.equal(defaultChoice(id), "signin");
    assert.equal(connectChoices(id)[1].label, "Paste a token");
  }
  const google = { available: true, fixed_port: false, redirect_host: "127.0.0.1", callback_path: "/callback", default_port: null };
  const x = { available: true, fixed_port: true, redirect_host: "127.0.0.1", callback_path: "/callback", default_port: 47689 };
  const meta = { ...x, redirect_host: "localhost" };
  assert.equal(signInRedirect(x, ""), "http://127.0.0.1:47689/callback");
  assert.equal(signInRedirect(x, "50000"), "http://127.0.0.1:50000/callback");
  assert.equal(signInRedirect(meta, ""), "http://localhost:47689/callback");
  assert.match(signInRedirect(google), /any port/);
  assert.equal(signInRedirect({ available: false }), "");
  // The steps show the exact address to register.
  assert.ok(signInSteps("x", "http://127.0.0.1:47689/callback").some(step => step.includes("http://127.0.0.1:47689/callback")));
  for (const id of Object.keys(SIGN_IN_GUIDES)) assert.equal(signInSteps(id, "r").length, 3, id);
  // Google takes any port, so YouTube asks for none.
  assert.deepEqual(signInFields("youtube", google).map(field => field.key), ["client_id", "client_secret"]);
  assert.deepEqual(signInFields("facebook", meta).map(field => field.key), ["client_id", "client_secret", "page_id", "port"]);
});

test("the sign-in form checks the app's ID, secret and port before starting", () => {
  const x = { available: true, fixed_port: true, redirect_host: "127.0.0.1", callback_path: "/callback", default_port: 47689 };
  assert.deepEqual(Object.keys(validateSignIn("x", { port: "47689" }, { info: x })), ["client_id"]);
  assert.deepEqual(validateSignIn("x", { client_id: "abc", port: "47689" }, { info: x }), {});
  assert.deepEqual(Object.keys(validateSignIn("linkedin", { client_id: "a", port: "47689" }, { info: x })), ["client_secret"]);
  assert.match(validateSignIn("x", { client_id: "a", port: "80" }, { info: x }).port, /1024 to 65535/);
  assert.match(validateSignIn("x", { client_id: "a b", port: "47689" }, { info: x }).client_id, /spaces/);
  assert.match(validateSignIn("facebook", { client_id: "1", client_secret: "s", page_id: "my page", port: "47689" }, { info: x }).page_id, /spaces|numbers/);
  // Signing in again reuses the saved app.
  assert.deepEqual(validateSignIn("linkedin", { port: "47689" }, { replacing: true, info: x }), {});
  assert.deepEqual(signInBody("x", " Me ", { client_id: " c ", client_secret: "", port: "50000" }, { id: "x-1" }),
    { platform: "x", name: "Me", id: "x-1", client_id: "c", port: 50000 });
  assert.deepEqual(signInBody("youtube", "", { client_id: "c", client_secret: "s" }, { privacy: "unlisted" }),
    { platform: "youtube", name: "", client_id: "c", client_secret: "s", options: { privacy: "unlisted" } });
  assert.match(signInMessage({ status: "waiting", opened: true }, "X"), /X's page/);
  assert.match(signInMessage({ status: "waiting", opened: false }, "X"), /couldn't open your browser/);
  assert.equal(signInMessage({ status: "failed", error: "State mismatch" }, "X"), "State mismatch");
  assert.equal(methodLabel("x", "oauth2", true), "OAuth 2.0 sign-in");
  assert.equal(methodLabel("youtube", undefined, true), "Signed in");
});
