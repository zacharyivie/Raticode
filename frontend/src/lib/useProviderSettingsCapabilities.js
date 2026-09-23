import { useCallback, useEffect, useRef, useState } from "react";
import { apiUrl } from "./api";

// Names only: availability, preferences and model catalogs come from the host.
const PROVIDERS = [
  ["codex", "Codex"], ["claude_code", "Claude Code"], ["cursor", "Cursor"],
  ["copilot", "GitHub Copilot"], ["opencode", "OpenCode"],
  ["antigravity", "Antigravity"], ["grok", "Grok"],
];
const snapshots = new WeakMap();

function cachedProviders() {
  const cached = snapshots.get(window);
  if (cached?.host === apiUrl("/")) return cached.providers;
  return PROVIDERS.map(([id, displayName]) => ({
    id, displayName, models: [], discoveryStatus: "pending", settingsPending: true,
  }));
}

export function useProviderSettingsCapabilities(open) {
  const [capabilities, setCapabilities] = useState(cachedProviders);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requestRef = useRef(0);
  const abortRef = useRef(null);

  const load = useCallback(async (refresh = true) => {
    const requestId = ++requestRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const host = apiUrl("/");
    const current = () => requestRef.current === requestId && !controller.signal.aborted && host === apiUrl("/");
    let providers = cachedProviders();
    const publish = () => {
      if (!current()) return;
      snapshots.set(window, { host, providers: providers.map(provider => ({ ...provider, refreshing: false })) });
      setCapabilities(providers);
    };
    const read = async suffix => {
      const response = await fetch(apiUrl(`/provider/capabilities${suffix}`), { signal: controller.signal });
      if (!response.ok) throw new Error("Could not refresh provider information");
      const payload = await response.json();
      if (!Array.isArray(payload.providers)) throw new Error("Invalid provider response");
      return payload.providers;
    };
    setLoading(true);
    setError("");
    try {
      // This reads preferences and stale catalogs without launching any CLIs.
      providers = await read("?snapshot=1");
      if (!current()) return;
      providers = providers.map(provider => ({ ...provider, refreshing: true }));
      publish();
      await Promise.all(providers.map(async ({ id }) => {
        let patch;
        try {
          const result = await read(`?provider=${encodeURIComponent(id)}${refresh ? "&refresh=1" : ""}`);
          patch = result.find(provider => provider.id === id);
          if (!patch) throw new Error("Provider missing from discovery response");
        } catch (failure) {
          if (!current()) return;
          patch = { refreshError: failure.message || "Could not refresh this provider" };
        }
        if (!current()) return;
        providers = providers.map(provider => provider.id === id
          ? { ...provider, ...patch, refreshing: false } : provider);
        publish();
      }));
    } catch (failure) {
      if (current()) setError(failure.message || "Could not refresh providers");
    } finally {
      if (current()) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    void load();
    const reload = event => { void load(event.detail?.refresh === true); };
    window.addEventListener("raticode:providers-changed", reload);
    return () => {
      abortRef.current?.abort();
      window.removeEventListener("raticode:providers-changed", reload);
    };
  }, [open, load]);

  return { capabilities, loading, error, refresh: () => load(true) };
}
