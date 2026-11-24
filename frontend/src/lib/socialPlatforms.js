// What the Posts Connect flow shows for each platform: plain-language steps, the fields it
// asks for and how each is checked before anything is sent. Logins go to the backend once,
// which keeps them in the system keychain; nothing here stores them.

export const DISCORD_WEBHOOK = /^https:\/\/(?:(?:canary|ptb)\.)?discord(?:app)?\.com\/api(?:\/v\d+)?\/webhooks\/\d+\/[\w-]+$/;

// Each field: [key, label, kind, required, hint]. kind is "url", "secret" or "text".
// "options" fields go into the account's settings instead of the keychain.
export const PLATFORM_GUIDES = Object.freeze({
  discord: {
    tagline: "Posts to a channel automatically",
    namePlaceholder: "Community #announcements",
    steps: [
      "In Discord, open the channel you want to post to and choose Edit Channel.",
      "Go to Integrations, then Webhooks, and click New Webhook.",
      "Click Copy Webhook URL and paste it below.",
    ],
    tip: "Try a private test channel first. Posts never ping @everyone, roles or people unless you allow it.",
    fields: [["webhook_url", "Webhook URL", "url", true, "Starts with https://discord.com/api/webhooks/"]],
    docs: "https://discord.com/developers/docs/resources/webhook",
  },
  x: {
    tagline: "Posts automatically · X charges per post",
    namePlaceholder: "@yourhandle",
    tip: "X bills you per post, and more for posts with links. Set a spending limit in X's console too.",
    // The first method is the default. API keys need no sign-in page or callback address.
    methods: [
      {
        id: "api_keys",
        label: "API keys",
        note: "Recommended. Four values from X's developer console; no sign-in page or callback address.",
        steps: [
          "In the X developer console, create an app and buy post credits.",
          "In the app's User authentication settings, set App permissions to Read and write. If X asks for a callback URI and website, http://127.0.0.1 and your own site will do; Raticode doesn't use them.",
          "Open Keys and tokens. Copy the API key and secret, generate the access token and secret for your account (generate them again if you changed permissions), and paste all four below.",
        ],
        fields: [
          ["api_key", "API key", "text", true, "Also called the consumer key"],
          ["api_secret", "API secret", "secret", true, "Also called the consumer secret"],
          ["access_token", "Access token", "secret", true, "Usually starts with your account's number and a dash"],
          ["access_token_secret", "Access token secret", "secret", true, ""],
        ],
        docs: "https://docs.x.com/fundamentals/developer-apps",
      },
      {
        id: "oauth2",
        label: "OAuth 2.0 token",
        note: "For a token from X's OAuth 2.0 sign-in, which you run yourself. X's console doesn't generate this token.",
        steps: [
          "In the app's User authentication settings, turn on OAuth 2.0 and add a callback URL. X needs an exact match; for a tool on your computer use http://127.0.0.1 with a port, not localhost.",
          "Run X's OAuth 2.0 sign-in (authorization code with PKCE) with the tweet.read, tweet.write, users.read and offline.access permissions, for example with an OAuth tool or script.",
          "Paste the access token it returns, and the refresh token with the app's client ID so Raticode can renew the token. Add the client secret only if your app is a confidential client.",
        ],
        fields: [
          ["access_token", "Access token", "secret", true, ""],
          ["refresh_token", "Refresh token", "secret", false, "Lets Raticode renew the login when it expires"],
          ["client_id", "Client ID", "text", false, "Needed with the refresh token"],
          ["client_secret", "Client secret", "secret", false, "Only if your app is a confidential client"],
        ],
        docs: "https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code",
      },
    ],
  },
  linkedin: {
    tagline: "You tap Publish at the scheduled time",
    namePlaceholder: "Your name",
    steps: [
      "Create an app in the LinkedIn Developer Portal.",
      "Add the Share on LinkedIn and Sign in with LinkedIn using OpenID Connect products.",
      "Open the Token Generator, choose the openid, profile and w_member_social permissions, and paste the token below.",
    ],
    tip: "LinkedIn logins last 60 days. Raticode reminds you 14 days before you need to paste a new one.",
    fields: [["access_token", "Access token", "secret", true, ""]],
    docs: "https://www.linkedin.com/developers/apps",
  },
  facebook: {
    tagline: "Posts to a Page you manage",
    namePlaceholder: "Your Page name",
    steps: [
      "Create a Meta app with the pages_manage_posts and pages_read_engagement permissions.",
      "In Graph API Explorer, generate a user token for your app with those permissions, extend it in the Access Token Debugger, then request me/accounts and copy your Page's access_token. A Page token from an extended user token doesn't expire.",
      "Paste the token and your Page ID below. The Page ID is under your Page's About > Page transparency.",
    ],
    tip: "Make a test post and check that a logged-out visitor can see it before you rely on it.",
    fields: [
      ["page_access_token", "Page access token", "secret", true, ""],
      ["page_id", "Page ID", "text", true, "Numbers only", "options"],
    ],
    docs: "https://developers.facebook.com/docs/pages-api",
  },
  youtube: {
    tagline: "Uploads videos with a title and description",
    namePlaceholder: "Your channel",
    steps: [
      "In Google Cloud, create a project and turn on the YouTube Data API v3.",
      "Create an OAuth client of type Web application, add https://developers.google.com/oauthplayground as an authorized redirect URI, and set the consent screen to In production, so the login lasts.",
      "In Google's OAuth 2.0 Playground, open the settings, tick Use your own OAuth credentials and enter your client. Authorize the youtube.upload permission, exchange the code for tokens, and paste the refresh token, client ID and client secret below.",
    ],
    tip: "Videos up to 256 MB. Titles up to 100 characters.",
    fields: [
      ["refresh_token", "Refresh token", "secret", true, ""],
      ["client_id", "Client ID", "text", true, ""],
      ["client_secret", "Client secret", "secret", true, ""],
      ["access_token", "Access token", "secret", false, "Optional: Raticode gets a fresh one from the refresh token"],
    ],
    docs: "https://developers.google.com/youtube/v3",
  },
});

export const CONNECT_ORDER = Object.freeze(["discord", "x", "linkedin", "facebook", "youtube"]);

// The ways to connect one platform, the default first. Most platforms have one way;
// X has API keys and an OAuth 2.0 token. A one-way platform's method id is "default".
export function guideMethods(platform) {
  const guide = PLATFORM_GUIDES[platform];
  if (!guide) return [];
  return guide.methods || [{ id: "default", label: "", note: "", steps: guide.steps, fields: guide.fields, docs: guide.docs }];
}

export function defaultMethod(platform) {
  return guideMethods(platform)[0]?.id;
}

function methodOf(platform, method) {
  const methods = guideMethods(platform);
  return methods.find(item => item.id === method) || methods[0];
}

export function guideSteps(platform, method) {
  return methodOf(platform, method)?.steps || [];
}

export function guideDocs(platform, method) {
  return methodOf(platform, method)?.docs || PLATFORM_GUIDES[platform]?.docs || "";
}

// Field objects for one platform's Connect form, for one way of connecting.
export function guideFields(platform, method) {
  return (methodOf(platform, method)?.fields || []).map(([key, label, kind, required, hint = "", target = "secrets"]) => ({ key, label, kind, required, hint, target }));
}

// A message per field that needs fixing, or an empty object when the form can be sent.
// Replacing a login (reconnect) keeps the saved values for fields left empty, but only
// when it keeps the same method; switching X's method starts from nothing.
export function validateConnect(platform, values = {}, { replacing = false, method } = {}) {
  const chosen = methodOf(platform, method)?.id;
  const errors = {};
  for (const field of guideFields(platform, chosen)) {
    const value = String(values[field.key] ?? "").trim();
    if (!value) {
      if (field.required && !(replacing && field.target === "secrets")) errors[field.key] = `Paste the ${field.label.charAt(0).toLowerCase()}${field.label.slice(1)}.`;
      continue;
    }
    if (field.kind === "url" && platform === "discord" && !DISCORD_WEBHOOK.test(value)) {
      errors[field.key] = "That isn't a Discord webhook address. It should start with https://discord.com/api/webhooks/.";
    } else if (field.kind !== "url" && /\s/.test(value)) {
      errors[field.key] = "This shouldn't contain spaces. Check that you copied all of it and nothing else.";
    } else if (field.key === "page_id" && !/^\d+$/.test(value)) {
      errors[field.key] = "A Page ID is only numbers.";
    }
  }
  if (platform === "x" && chosen === "oauth2" && String(values.refresh_token ?? "").trim() && !String(values.client_id ?? "").trim()) {
    errors.client_id = "Add the client ID so Raticode can use the refresh token.";
  }
  return errors;
}

// The body for POST /social/accounts, with empty fields left out. Only the chosen
// method's fields are sent, and X's body names the method as auth.
export function connectBody(platform, name, values = {}, { id, privacy, method } = {}) {
  const chosen = methodOf(platform, method)?.id;
  const secrets = {};
  const options = {};
  for (const field of guideFields(platform, chosen)) {
    const value = String(values[field.key] ?? "").trim();
    if (!value) continue;
    if (field.target === "options") options[field.key] = value;
    else secrets[field.key] = value;
  }
  if (platform === "youtube" && privacy) options.privacy = privacy;
  return {
    ...(id ? { id } : {}),
    platform,
    name: String(name || "").trim(),
    ...(PLATFORM_GUIDES[platform]?.methods ? { auth: chosen } : {}),
    secrets,
    options,
  };
}

// How an account was connected, in words, for platforms with more than one way.
export function methodLabel(platform, auth, signedIn = false) {
  if (signedIn) return platform === "x" ? "OAuth 2.0 sign-in" : "Signed in";
  const methods = PLATFORM_GUIDES[platform]?.methods;
  if (!methods) return "";
  return (methods.find(item => item.id === (auth || "oauth2")) || methods[0]).label;
}

// One-click sign-in through the system browser. Raticode listens on this computer's
// loopback address for a few minutes, so no website or redirect server is needed. Every
// platform still needs the user's own developer app; Raticode ships no client ID or secret.
// The redirect address comes from the backend's platform info (sign_in), so the steps can
// show the exact text to register. Discord posts through a webhook and has no sign-in.
export const SIGN_IN_GUIDES = Object.freeze({
  youtube: {
    button: "Sign in with Google",
    note: "Recommended. Sign in on Google's own page in your browser; nothing to copy back.",
    steps: () => [
      "In Google Cloud, create a project, turn on the YouTube Data API v3 and set up the OAuth consent screen. Publish it (In production) so the sign-in lasts; in Testing, Google ends it after 7 days.",
      "Create an OAuth client ID of type Desktop app. Desktop clients accept Raticode's local address on any port, so there's no redirect URL to add.",
      "Paste its client ID and client secret below and click Sign in with Google. Allow access in your browser, then come back here.",
    ],
    fields: [
      ["client_id", "Client ID", "text", true, "Ends in .apps.googleusercontent.com"],
      ["client_secret", "Client secret", "secret", true, "Google gives Desktop clients one too"],
    ],
    docs: "https://developers.google.com/identity/protocols/oauth2/native-app",
  },
  x: {
    button: "Sign in with X",
    note: "OAuth 2.0 without pasting tokens. Needs a callback URL in your X app; API keys don't.",
    steps: redirect => [
      "In the X developer console, open your app's User authentication settings, turn on OAuth 2.0, choose Native App (or Web App if you want a client secret) and set App permissions to Read and write.",
      `Add ${redirect} as a callback URL. X needs this exact address: 127.0.0.1, not localhost, with the same port.`,
      "Paste the app's OAuth 2.0 client ID below, and the client secret if X gave you one, then click Sign in with X.",
    ],
    fields: [
      ["client_id", "Client ID", "text", true, "The OAuth 2.0 client ID, not the API key"],
      ["client_secret", "Client secret", "secret", false, "Only for a Web App (confidential client)"],
      ["port", "Port", "port", true, "Change it only if another program uses it, and register the new address too"],
    ],
    docs: "https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code",
  },
  linkedin: {
    button: "Sign in with LinkedIn",
    note: "Recommended. Sign in on LinkedIn's page instead of copying a token; logins still last 60 days.",
    steps: redirect => [
      "In the LinkedIn Developer Portal, create an app and add the Share on LinkedIn and Sign in with LinkedIn using OpenID Connect products.",
      `On the app's Auth tab, add ${redirect} under Authorized redirect URLs.`,
      "Paste the client ID and primary client secret below, then click Sign in with LinkedIn. Raticode reminds you 14 days before the 60 days are up, and signing in again is one click.",
    ],
    fields: [
      ["client_id", "Client ID", "text", true, ""],
      ["client_secret", "Client secret", "secret", true, "The primary client secret on the Auth tab"],
      ["port", "Port", "port", true, "Change it only if another program uses it, and register the new address too"],
    ],
    docs: "https://learn.microsoft.com/linkedin/shared/authentication/authorization-code-flow",
  },
  facebook: {
    button: "Sign in with Facebook",
    note: "Recommended. Sign in on Facebook's page; Raticode finds your Page and keeps a token that doesn't expire.",
    steps: redirect => [
      "Create a Meta app with Facebook Login, and request pages_show_list, pages_read_engagement and pages_manage_posts. People with a role on the app can use them without App Review.",
      `In Facebook Login settings, add ${redirect} to Valid OAuth Redirect URIs. Meta takes localhost addresses while the app is in development mode.`,
      "Paste the App ID and App secret below and click Sign in with Facebook. If you manage more than one Page, Raticode asks which one to post to.",
    ],
    fields: [
      ["client_id", "App ID", "text", true, "Numbers only"],
      ["client_secret", "App secret", "secret", true, "Under App settings > Basic"],
      ["page_id", "Page ID", "text", false, "Optional: leave empty to choose after signing in"],
      ["port", "Port", "port", true, "Change it only if another program uses it, and register the new address too"],
    ],
    docs: "https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow",
  },
});

export const NO_SIGN_IN_NOTE = "Discord posts through a channel webhook, so there's no account to sign in to. Paste the webhook URL instead.";

// The ways the Connect panel offers, in order, with the default first. YouTube, LinkedIn
// and Facebook default to signing in; X keeps API keys first and signing in second.
export function connectChoices(platform) {
  const paste = guideMethods(platform).map(method => ({
    id: method.id,
    label: method.id === "default" ? "Paste a token" : method.id === "oauth2" ? "Paste OAuth 2.0 token" : method.label,
    signIn: false,
  }));
  const guide = SIGN_IN_GUIDES[platform];
  if (!guide) return paste;
  const signIn = { id: "signin", label: platform === "x" ? "Sign in (OAuth 2.0)" : "Sign in", signIn: true };
  return platform === "x" ? [paste[0], signIn, ...paste.slice(1)] : [signIn, ...paste];
}

export function defaultChoice(platform) {
  return connectChoices(platform)[0]?.id;
}

// The address to register in the platform's app. Google Desktop clients take any port.
export function signInRedirect(info, port) {
  if (!info?.available) return "";
  if (!info.fixed_port) return `http://${info.redirect_host}:<any port>${info.callback_path}`;
  return `http://${info.redirect_host}:${port || info.default_port}${info.callback_path}`;
}

export function signInSteps(platform, redirect) {
  return SIGN_IN_GUIDES[platform]?.steps(redirect) || [];
}

export function signInFields(platform, info) {
  return (SIGN_IN_GUIDES[platform]?.fields || [])
    .filter(([key]) => key !== "port" || info?.fixed_port !== false)
    .map(([key, label, kind, required, hint = ""]) => ({ key, label, kind, required, hint, target: "signin" }));
}

// A message per field to fix before signing in. Signing in again leaves the client ID and
// secret empty to reuse the saved ones.
export function validateSignIn(platform, values = {}, { replacing = false, info } = {}) {
  const errors = {};
  for (const field of signInFields(platform, info)) {
    const value = String(values[field.key] ?? "").trim();
    if (!value) {
      const saved = replacing && (field.key === "client_id" || field.key === "client_secret");
      if (field.required && !saved && field.key !== "port") errors[field.key] = `Paste the ${field.label.charAt(0).toLowerCase()}${field.label.slice(1)}.`;
      continue;
    }
    if (/\s/.test(value)) errors[field.key] = "This shouldn't contain spaces. Check that you copied all of it and nothing else.";
    else if (field.key === "port" && !(/^\d+$/.test(value) && Number(value) >= 1024 && Number(value) <= 65535)) errors[field.key] = "Choose a port from 1024 to 65535.";
    else if (field.key === "page_id" && !/^\d+$/.test(value)) errors[field.key] = "A Page ID is only numbers.";
  }
  return errors;
}

// The body for POST /social/accounts/sign-in, with empty fields left out.
export function signInBody(platform, name, values = {}, { id, privacy } = {}) {
  const body = { platform, name: String(name || "").trim() };
  if (id) body.id = id;
  for (const key of ["client_id", "client_secret", "page_id"]) {
    const value = String(values[key] ?? "").trim();
    if (value) body[key] = value;
  }
  const port = String(values.port ?? "").trim();
  if (port) body.port = Number(port);
  if (platform === "youtube" && privacy) body.options = { privacy };
  return body;
}

// Plain words for a sign-in's progress.
export function signInMessage(session, label) {
  if (!session) return "";
  if (session.status === "waiting") return session.opened
    ? `Finish signing in on ${label}'s page in your browser, then come back here.`
    : `Raticode couldn't open your browser. Copy the sign-in link and open it in a browser on this computer.`;
  if (session.status === "exchanging") return `${label} sent you back. Saving the sign-in to your keychain…`;
  if (session.status === "choose_page") return "Choose the Page Raticode should post to.";
  return session.error || "";
}

// Plain words for an account's status. The icon and words carry it, not only the color.
export const ACCOUNT_STATUS = Object.freeze({
  connected: { label: "Connected", tone: "done" },
  reconnect_soon: { label: "Reconnect soon", tone: "review" },
  needs_reconnect: { label: "Needs reconnect", tone: "blocked" },
  disabled: { label: "Off", tone: "todo" },
});
