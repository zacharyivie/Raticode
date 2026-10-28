import { shareInFlight } from "./refresh.js";
const DEFAULT_API_BASE_URL = "/api";

export function installGoferApiFetchAuth() {
  if (typeof window === "undefined" || typeof window.fetch !== "function") return;
  if (window.__goferApiFetchAuthInstalled) return;
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    if (shouldBootstrapGoferApiAuth(input, init)) {
      const previousBase = apiUrl("/");
      await ensureGoferApiToken(nativeFetch, { refresh: Boolean(window.goferDesktop?.apiSession) });
      if (typeof input === "string" && input.startsWith(previousBase)) {
        input = apiUrl("/") + input.slice(previousBase.length);
      }
    }
    const request = () => nativeFetch(...withGoferApiAuth(input, init));
    const method = String(init.method || input?.method || "GET").toUpperCase();
    const mutation = !["GET", "HEAD", "OPTIONS"].includes(method) && isGoferApiRequest(input);
    if (mutation) window.__goferApiReadGeneration = (window.__goferApiReadGeneration || 0) + 1;
    const share = method === "GET" && typeof input === "string" && !init.signal && !init.headers && isGoferApiRequest(input);
    const response = share
      ? (await shareInFlight(`api:${window.__goferApiReadGeneration || 0}:${input}:${currentApiToken()}`, request)).clone()
      : await request();
    if (mutation) window.__goferApiReadGeneration = (window.__goferApiReadGeneration || 0) + 1;
    if (response.status === 401 && isGoferApiRequest(input) && window.goferDesktop?.apiSession) {
      const previousToken = currentApiToken();
      const token = await ensureGoferApiToken(nativeFetch, { refresh: true });
      if (token && token !== previousToken) {
        return nativeFetch(...withGoferApiAuth(input, init));
      }
    }
    return response;
  };
  window.__goferApiFetchAuthInstalled = true;
}

export function apiUrl(path) {
  const baseUrl = normalizeApiBaseUrl(window.__goferSessionBaseUrl || window.goferApiBaseUrl || DEFAULT_API_BASE_URL);
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;

  return `${baseUrl}${normalizedPath}`;
}

export function withGoferApiAuth(input, init = {}) {
  const token = currentApiToken();
  if (!token || !isGoferApiRequest(input)) return [input, init];

  const headers = new Headers(init.headers || requestHeaders(input));
  if (!headers.has("Authorization") && !headers.has("X-Gofer-Webhook-Token")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return [input, { ...init, headers }];
}

function currentApiToken() {
  return window.__goferSessionToken || window.goferApiToken || "";
}

function launchCapability() {
  // Fragments are not sent to the server or in HTTP Referer headers.
  const hash = window.location?.hash || "";
  const fragment = new URLSearchParams(hash.replace(/^#/, ""));
  const token = fragment.get("gofer-token") || "";
  if (token) {
    fragment.delete("gofer-token");
    const remaining = fragment.toString();
    window.history?.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${window.location.search}${remaining ? `#${remaining}` : ""}`,
    );
  }
  return token;
}

export async function ensureGoferApiToken(_fetchImpl, { refresh = false } = {}) {
  if (window.__goferApiTokenPromise) return window.__goferApiTokenPromise;
  if (!refresh && currentApiToken()) return currentApiToken();
  window.__goferApiTokenPromise = Promise.resolve()
    .then(async () => {
      const session = await window.goferDesktop?.apiSession?.();
      const token = typeof session?.apiToken === "string" ? session.apiToken : launchCapability();
      if (typeof session?.apiBaseUrl === "string" && session.apiBaseUrl) window.__goferSessionBaseUrl = session.apiBaseUrl;
      if (token) window.__goferSessionToken = token;
      return token;
    })
    .catch(error => {
      if (window.goferDesktop?.apiSession) throw new Error("The desktop backend is unavailable. Retry when it is ready.", { cause: error });
      return "";
    })
    .finally(() => {
      window.__goferApiTokenPromise = undefined;
    });
  return window.__goferApiTokenPromise;
}

function requestHeaders(input) {
  return typeof Request !== "undefined" && input instanceof Request ? input.headers : undefined;
}

function isGoferApiRequest(input) {
  const value = typeof input === "string" ? input : input?.url;
  if (!value) return false;
  const target = new URL(value, window.location?.href || "http://127.0.0.1/");
  const apiBase = new URL(apiUrl("/"), window.location?.href || "http://127.0.0.1/");
  return (
    target.origin === apiBase.origin &&
    target.pathname.startsWith(apiBase.pathname) &&
    !target.pathname.includes("/webhooks/")
  );
}

function shouldBootstrapGoferApiAuth(input) {
  return isGoferApiRequest(input) && (Boolean(window.goferDesktop?.apiSession) || !currentApiToken());
}

function normalizeApiBaseUrl(baseUrl) {
  const normalizedBaseUrl = baseUrl.endsWith("/") ? baseUrl.slice(0, -1) : baseUrl;

  if (isHttpOrigin(normalizedBaseUrl)) {
    return `${normalizedBaseUrl}/api`;
  }

  return normalizedBaseUrl;
}

function isHttpOrigin(value) {
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && url.pathname === "/";
  } catch {
    return false;
  }
}
