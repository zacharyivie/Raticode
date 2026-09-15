const legacyBrand = require("./brand-compat.json");
const RATICODE_HOME_URL = "raticode://home";

function normalizeBrowserUrl(value) {
  const input = String(value ?? "").trim();
  if (!input || input === "about:blank") return "about:blank";
  if (input === RATICODE_HOME_URL || legacyBrand.previousBrands.some((item) => input === `${item.brand}://home`)) return RATICODE_HOME_URL;
  if (/^https?:\/\//i.test(input)) return new URL(input).toString();
  if (/^(?:localhost|127\.0\.0\.1|\[?::1\]?)(?::\d+)?(?:[/?#]|$)/i.test(input)) {
    return new URL(`http://${input}`).toString();
  }
  if (/^[\w.-]+\.[a-z]{2,}(?::\d+)?(?:[/?#]|$)/i.test(input)) {
    return new URL(`https://${input}`).toString();
  }
  if (/^[a-z][a-z\d+.-]*:/i.test(input)) {
    throw new Error("Enter an http:// or https:// address.");
  }
  return `https://www.google.com/search?q=${encodeURIComponent(input)}`;
}

function browserShortcutAction(input = {}, platform = process.platform, openBrowserBinding = "Mod+Alt+Slash") {
  if (input.type !== "keyDown" || input.isAutoRepeat) return "";
  const key = String(input.key ?? "").toLowerCase();
  const primary = platform === "darwin" ? input.meta : input.control;
  if (matchesBrowserBinding(input, openBrowserBinding, platform)) return "open-browser";
  if (primary && !input.alt && !input.shift && key === ",") return "settings-open";
  if (primary && !input.alt && !input.shift && key === "o") return "file-open";
  if (primary && !input.alt && !input.shift && key === "`") return "panel-toggle";
  if (input.control && !input.alt && !input.meta && !input.shift && key === "b") return "project-pane-toggle";
  if (input.control && !input.alt && !input.meta && !input.shift && key === "l") return "assistant-pane-toggle";
  if (input.alt && !input.control && !input.meta && key === "d") return "focus-location";
  if (primary && key === "r") return "reload";
  if (primary && key === "w") return "close";
  if (input.control && !input.alt && !input.meta && key === "tab") {
    return input.shift ? "previous-tab" : "next-tab";
  }
  if (primary && !input.alt && !input.shift && key === "t") return "new-tab";
  if (input.alt && key === "left") return "back";
  if (input.alt && key === "right") return "forward";
  if (primary && (key === "+" || key === "=")) return "zoom-in";
  if (primary && key === "-") return "zoom-out";
  if (primary && key === "0") return "zoom-reset";
  return "";
}

function browserWheelZoomAction(input = {}) {
  if (input.type !== "mouseWheel") return "";
  const modifiers = new Set(
    Array.isArray(input.modifiers)
      ? input.modifiers.map((modifier) => String(modifier).toLowerCase())
      : [],
  );
  const primaryModifier = input.control === true
    || input.meta === true
    || modifiers.has("control")
    || modifiers.has("ctrl")
    || modifiers.has("meta")
    || modifiers.has("command")
    || modifiers.has("cmd");
  if (!primaryModifier || input.alt === true || modifiers.has("alt")
    || !Number.isFinite(input.deltaY) || input.deltaY === 0) return "";
  return input.deltaY < 0 ? "zoom-in" : "zoom-out";
}

function browserApplicationShortcutAction(session, input = {}, bindings = {}, platform = process.platform) {
  if (input.type !== "keyDown" || input.isAutoRepeat) return "";
  const pending = session.applicationChord;
  session.applicationChord = null;
  if (pending && pending.deadline > Date.now()) {
    if (matchesBrowserBinding(input, pending.binding, platform)) {
      return `command:${pending.commandId}`;
    }
  }
  for (const [commandId, binding] of Object.entries(bindings)) {
    const segments = String(binding ?? "").split(/\s+/).filter(Boolean);
    if (segments.length === 2 && matchesBrowserBinding(input, segments[0], platform)) {
      session.applicationChord = {
        binding: segments[1],
        commandId,
        deadline: Date.now() + 1500,
      };
      return "chord-pending";
    }
    if (segments.length === 1 && matchesBrowserBinding(input, segments[0], platform)) {
      return `command:${commandId}`;
    }
  }
  return "";
}

function browserSessionShortcutAction(
  session,
  input = {},
  platform = process.platform,
) {
  const browserAction = browserShortcutAction(input, platform, session.openBrowserBinding);
  if (["close", "new-tab"].includes(browserAction)) return browserAction;
  return browserApplicationShortcutAction(
    session,
    input,
    session.applicationKeybindings,
    platform,
  ) || browserProjectChordAction(session, input, platform) || browserAction;
}

function browserCommandRequiresOwnerFocus(action) {
  return [
    "close",
    "edit-local-html",
    "focus-location",
    "new-tab",
    "next-tab",
    "previous-tab",
  ].includes(action);
}

function browserProjectChordAction(session, input = {}, platform = process.platform) {
  if (input.type !== "keyDown" || input.isAutoRepeat) return "";
  const key = String(input.key ?? "").toLowerCase();
  const primary = platform === "darwin" ? input.meta : input.control;
  const plainPrimary = primary && !input.alt && !input.shift
    && (platform === "darwin" || !input.meta);
  if (session.projectChordDeadline > Date.now()) {
    session.projectChordDeadline = 0;
    return plainPrimary && key === "o" ? "project-open" : "";
  }
  session.projectChordDeadline = 0;
  if (plainPrimary && key === "k") {
    session.projectChordDeadline = Date.now() + 1500;
    return "chord-pending";
  }
  return "";
}

function browserLoadUrl(value) {
  return value === RATICODE_HOME_URL ? raticodeHomeDataUrl() : value;
}

function browserContentZoomFactor(ownerZoomFactor = 1, pageZoomFactor = 1) {
  const ownerScale = Number.isFinite(ownerZoomFactor) && ownerZoomFactor > 0
    ? ownerZoomFactor
    : 1;
  const pageScale = Number.isFinite(pageZoomFactor) && pageZoomFactor > 0
    ? pageZoomFactor
    : 1;
  return clamp(ownerScale * pageScale, 0.5, 3);
}

function clamp(value, minimum, maximum) {
  return Math.min(Math.max(value, minimum), maximum);
}

function raticodeHomeDataUrl() {
  const html = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
  <title>Raticode</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg:#f6f6f8; --bg-2:#fff; --surface:#fff; --ink:#151517; --muted:#6b6b76; --line:#e6e6ea;
      --accent:#6552e0; --accent-2:#151521; --accent-soft:#eeecff; --glow:rgba(101,82,224,.16); --dot:rgba(21,21,33,.08);
    }
    * { box-sizing:border-box; }
    body {
      margin:0; min-height:100vh; color:var(--ink); overflow-x:hidden; position:relative;
      font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
      background-image: radial-gradient(var(--dot) 1px, transparent 1px);
      background-size: 24px 24px;
      background-color: var(--bg);
    }
    .mark-field { position:absolute; top:-9%; right:-7%; z-index:0; width:min(64vw,760px); pointer-events:none;
      filter:blur(.2px); opacity:.06; transform:rotate(-11deg); }
    @media (max-width:760px) { .mark-field { display:none; } }
    main { position:relative; z-index:1; width:min(1040px,calc(100% - 48px)); margin:0 auto; padding:clamp(56px,10vh,108px) 0 64px; }
    .brand { display:flex; align-items:center; gap:18px; }
    .brand svg { width:56px; height:56px; border-radius:14px; box-shadow:0 14px 30px -12px rgba(21,21,33,.45); flex:none; }
    .brand-text { display:flex; flex-direction:column; gap:4px; }
    .brand-text span { font-size:19px; font-weight:750; letter-spacing:-.02em; }
    .brand-text small { font-size:11px; font-weight:600; letter-spacing:.11em; text-transform:uppercase; color:var(--muted); }
    h1 { max-width:520px; margin:36px 0 16px; font-size:clamp(34px,5vw,54px); line-height:1.06; letter-spacing:-.035em; font-weight:700; }
    h1 .grad { background:linear-gradient(115deg,var(--accent-2),var(--accent) 70%); -webkit-background-clip:text; background-clip:text; color:transparent; }
    .lede { max-width:440px; margin:0; color:var(--muted); font-size:clamp(15px,1.6vw,17px); line-height:1.65; }
    .hero { display:grid; grid-template-columns:1.05fr .95fr; gap:20px 64px; align-items:start; margin-top:8px; }
    .command { display:flex; align-items:center; gap:14px; margin-top:32px; padding-left:16px; border-left:2px solid var(--accent-soft); }
    .command p { margin:0; color:var(--muted); font-size:12.5px; }
    kbd { flex:none; border:1px solid var(--line); border-radius:7px; background:var(--accent-soft); color:var(--accent); padding:6px 10px; font:650 12px/1 ui-monospace,SFMono-Regular,Consolas,monospace; }
    .timeline { list-style:none; margin:6px 0 0; padding:0; position:relative; display:flex; flex-direction:column; gap:30px; }
    .timeline::before { content:""; position:absolute; top:6px; bottom:6px; left:19px; width:1px; background:linear-gradient(var(--line),transparent 92%); }
    .timeline li { position:relative; display:flex; gap:16px; }
    .timeline .icon { position:relative; z-index:1; display:grid; flex:none; place-items:center; width:40px; height:40px; border-radius:11px; background:var(--surface); border:1px solid var(--line); color:var(--accent); }
    .timeline .icon svg { width:18px; height:18px; }
    .timeline h2 { margin:7px 0 5px; font-size:14.5px; font-weight:650; }
    .timeline p { margin:0; color:var(--muted); font-size:13px; line-height:1.55; }
    @media (max-width:760px) { main { width:min(100% - 32px,880px); padding-top:48px; } .hero { grid-template-columns:1fr; } h1 { max-width:none; } .lede { max-width:none; } }
    @media (prefers-color-scheme:dark) {
      :root { --bg:#0c0c10; --bg-2:#131318; --surface:#16161c; --ink:#f4f4f6; --muted:#9a9aa5; --line:#26262e; --accent:#a99bff; --accent-2:#f0eeff; --accent-soft:#221f3a; --dot:rgba(240,238,255,.06); }
      .mark-field { opacity:.09; }
    }
  </style>
</head>
<body>
  <svg class="mark-field" viewBox="0 0 512 512" aria-hidden="true" focusable="false">
    <rect width="512" height="512" rx="104" fill="#151521"/>
    <path d="m256 66 166 96v188l-166 96L90 350V162Z" fill="#151521" stroke="#9A8CFF" stroke-width="18"/>
    <path d="m168 190 88-51 88 51v103l-88 75-88-75Z" fill="#9A8CFF"/>
    <path d="m168 190-34-78 95 43m115 35 34-78-95 43" fill="#F0EEFF"/>
    <circle cx="219" cy="242" r="12" fill="#151521"/><circle cx="293" cy="242" r="12" fill="#151521"/>
    <path d="m256 276 25 20-25 20-25-20Z" fill="#F0EEFF"/><path d="m204 305-65 24m169-24 65 24" stroke="#F0EEFF" stroke-width="11" stroke-linecap="round"/>
  </svg>
  <main>
    <div class="brand">
      <svg viewBox="0 0 512 512" role="img" aria-labelledby="brandmark-title" width="56" height="56">
        <title id="brandmark-title">Raticode</title>
        <rect width="512" height="512" rx="104" fill="#151521"/>
        <path d="m256 66 166 96v188l-166 96L90 350V162Z" fill="#151521" stroke="#9A8CFF" stroke-width="18"/>
        <path d="m168 190 88-51 88 51v103l-88 75-88-75Z" fill="#9A8CFF"/>
        <path d="m168 190-34-78 95 43m115 35 34-78-95 43" fill="#F0EEFF"/>
        <circle cx="219" cy="242" r="12" fill="#151521"/><circle cx="293" cy="242" r="12" fill="#151521"/>
        <path d="m256 276 25 20-25 20-25-20Z" fill="#F0EEFF"/><path d="m204 305-65 24m169-24 65 24" stroke="#F0EEFF" stroke-width="11" stroke-linecap="round"/>
      </svg>
      <div class="brand-text">
        <span>Raticode</span>
        <small>Local-first workflow studio</small>
      </div>
    </div>
    <div class="hero">
      <div>
        <h1><span class="grad">Workflows that stay on your machine.</span></h1>
        <p class="lede">Raticode is a local workflow studio and CLI for building graph-based automation with shell commands, scripts, HTTP requests, and AI agents.</p>
        <div class="command">
          <kbd>Alt + D</kbd>
          <p>Type a URL or search in the address bar. Your home page is configurable in Browser settings.</p>
        </div>
      </div>
      <ol class="timeline" aria-label="Raticode capabilities">
        <li>
          <div class="icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="6" r="2.4"/><circle cx="19" cy="6" r="2.4"/><circle cx="12" cy="18" r="2.4"/><path d="M7 7.5 10 16M17 7.5 14 16M7.4 6h9.2"/></svg></div>
          <div>
            <h2>Build visually</h2>
            <p>Shape routes, branches, loops, and joins in the graph editor or write the same workflow in Rattish.</p>
          </div>
        </li>
        <li>
          <div class="icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg></div>
          <div>
            <h2>Run locally</h2>
            <p>Keep projects and execution on your computer. No account or hosted backend is required.</p>
          </div>
        </li>
        <li>
          <div class="icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v3M12 18v3M4.5 12h3M16.5 12h3M6.5 6.5l2 2M15.5 15.5l2 2M17.5 6.5l-2 2M8.5 15.5l-2 2"/><circle cx="12" cy="12" r="3"/></svg></div>
          <div>
            <h2>Use agents deliberately</h2>
            <p>Mix deterministic steps with Codex, Claude, or API-backed agents where they earn their place.</p>
          </div>
        </li>
      </ol>
    </div>
  </main>
</body>
</html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

function matchesBrowserBinding(input, binding, platform = process.platform) {
  if (!binding) return false;
  const parts = String(binding).split("+").filter(Boolean);
  const expectedCode = parts.at(-1);
  const modifiers = new Set(parts.slice(0, -1));
  const mac = platform === "darwin";
  const primary = mac ? Boolean(input.meta) : Boolean(input.control);
  if (modifiers.has("Mod") && !primary) return false;
  const explicitControl = Boolean(input.control && !(!mac && modifiers.has("Mod")));
  const explicitMeta = Boolean(input.meta && !(mac && modifiers.has("Mod")));
  if (modifiers.has("Ctrl") !== explicitControl) return false;
  if (modifiers.has("Meta") !== explicitMeta) return false;
  if (modifiers.has("Alt") !== Boolean(input.alt)) return false;
  if (modifiers.has("Shift") !== Boolean(input.shift)) return false;
  return browserInputCode(input) === expectedCode;
}

function browserInputCode(input) {
  if (input.code) return input.code;
  const key = String(input.key ?? "");
  if (/^[a-z]$/i.test(key)) return `Key${key.toUpperCase()}`;
  if (/^\d$/.test(key)) return `Digit${key}`;
  return { ",": "Comma", "/": "Slash", "`": "Backquote", "=": "Equal", "-": "Minus" }[key] ?? key;
}

module.exports = {
  RATICODE_HOME_URL,
  browserApplicationShortcutAction,
  browserCommandRequiresOwnerFocus,
  browserContentZoomFactor,
  browserLoadUrl,
  browserProjectChordAction,
  browserSessionShortcutAction,
  browserShortcutAction,
  browserWheelZoomAction,
  matchesBrowserBinding,
  normalizeBrowserUrl,
  raticodeHomeDataUrl,
};
