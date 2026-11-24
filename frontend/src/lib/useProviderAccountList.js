import { useCallback, useEffect, useRef, useState } from "react";
import { apiUrl } from "./api.js";

/**
 * Every provider account with its sign-in state and last usage reading, for the
 * thread model picker and the Studio's Profile menus, and the providers that have
 * accounts. Reloads when providers or accounts change anywhere in the app.
 */
export function useProviderAccountList() {
  const [accounts, setAccounts] = useState([]);
  const [providers, setProviders] = useState([]);
  const request = useRef(0);
  const load = useCallback(async () => {
    const current = ++request.current;
    try {
      const response = await fetch(apiUrl("/provider/accounts"));
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Could not read accounts");
      if (current === request.current) {
        setAccounts(Array.isArray(payload.accounts) ? payload.accounts : []);
        setProviders(Array.isArray(payload.providers) ? payload.providers : []);
      }
    } catch {
      // The picker simply shows no Account menu until accounts can be read.
      if (current === request.current) setAccounts([]);
    }
  }, []);
  useEffect(() => {
    void load();
    const reload = () => void load();
    window.addEventListener("raticode:providers-changed", reload);
    return () => {
      request.current += 1;
      window.removeEventListener("raticode:providers-changed", reload);
    };
  }, [load]);
  // While a sign-in started from the picker waits for the browser, check every 2 seconds.
  const waiting = accounts.some(account => account?.sign_in?.state === "waiting");
  useEffect(() => {
    if (!waiting) return undefined;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, [waiting, load]);
  return { accounts, providers, reload: load };
}

/** Starts an account's own sign-in, the same one Settings > Providers runs. */
export async function signInProviderAccount(name) {
  const response = await fetch(apiUrl("/provider/accounts"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "sign_in", name }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not start the sign-in.");
  window.dispatchEvent(new CustomEvent("raticode:providers-changed"));
  return payload;
}
