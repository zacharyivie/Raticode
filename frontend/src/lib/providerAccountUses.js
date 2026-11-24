// Which account each kind of work uses, for one provider's accounts in Settings > Providers.
// Reads only saved settings: Rem's choice (settings.assistant.providerAccounts) and
// self-work's own choice (self-work settings' provider_accounts). Nothing here is stored.

export const REM_USE = "Threads, tasks and Rem’s replies";
export const SELF_WORK_USE = "Self-work";

/**
 * Where a choice points: { kind: "default" | "auto" | "account" | "removed", account }.
 * "" and "@default" are the provider's default sign-in, "auto" is Choose by usage and
 * anything else names an account, which may have been removed since.
 */
function resolve(choice, names) {
  if (!choice || choice === "@default") return { kind: "default", account: "" };
  if (choice === "auto") return { kind: "auto", account: "" };
  return { kind: names.has(choice) ? "account" : "removed", account: choice };
}

/**
 * The uses to show inside one provider's accounts section.
 *
 * remAccounts is Rem's setting by provider; selfWorkAccounts is self-work's own choice by
 * provider, or null when self-work's settings couldn't be read (no Self-work label then).
 * Self-work with no choice of its own ("") follows Rem's setting.
 *
 * Returns { byAccount: { name: [use] }, lines: [use] }: a use naming an existing account
 * sits on that account's row; default sign-in, Choose by usage and a removed account each
 * get a line of their own. Each use is { id, label, kind, account, needsSignIn, provider }.
 */
export function providerAccountUses({ provider, accounts = [], remAccounts, selfWorkAccounts }) {
  const byAccount = {};
  const lines = [];
  if (!provider || !remAccounts || typeof remAccounts !== "object") return { byAccount, lines };
  const mine = accounts.filter(account => account.provider === provider);
  const names = new Set(mine.map(account => account.name));
  const remChoice = typeof remAccounts[provider] === "string" ? remAccounts[provider] : "";
  const uses = [{ id: "rem", label: REM_USE, choice: remChoice }];
  if (selfWorkAccounts && typeof selfWorkAccounts === "object") {
    const own = typeof selfWorkAccounts[provider] === "string" ? selfWorkAccounts[provider] : "";
    uses.push({ id: "self_work", label: SELF_WORK_USE, choice: own || remChoice });
  }
  const resolved = uses.map(({ choice, ...use }) => ({ ...use, provider, ...resolve(choice, names) }));
  // No accounts and nothing chosen: the section's own "no extra accounts" note says it all.
  if (!mine.length && resolved.every(use => use.kind === "default")) return { byAccount, lines };
  for (const use of resolved) {
    if (use.kind === "account") {
      const account = mine.find(item => item.name === use.account);
      const needsSignIn = account.state === "needs_sign_in" || account.needs_sign_in === true;
      (byAccount[use.account] ??= []).push({ ...use, needsSignIn });
    } else lines.push({ ...use, needsSignIn: false });
  }
  return { byAccount, lines };
}

/** A use's label as the row or line shows it, with the existing "needs sign-in" wording. */
export function inUseLabel(use) {
  return use.needsSignIn ? `${use.label} (needs sign-in)` : use.label;
}

/** What a line says after its label: the default sign-in, Choose by usage or a removed account. */
export function inUseLineValue(use) {
  if (use.kind === "auto") return "Choose by usage";
  if (use.kind === "removed") return `${use.account} (removed)`;
  return "Default sign-in";
}

/**
 * Where a use's Change link goes: Settings > Rem, on Rem's Accounts row or on self-work's
 * Accounts, with the provider's own picker focused when it's there.
 */
export function changeLinkTarget(use) {
  if (use.id === "self_work") return {
    category: "assistant",
    selectors: [`#self-work-account-${use.provider}`, '[data-testid="self-work-accounts"]', '[data-testid="self-work-settings"]'],
  };
  return {
    category: "assistant",
    selectors: [`[data-setting-row="providerAccounts"] [data-provider-account="${use.provider}"] select`, '[data-setting-row="providerAccounts"]'],
  };
}

/** The Change link's accessible name, so a screen reader hears which setting it opens. */
export function changeLinkLabel(use, providerLabel) {
  return use.id === "self_work"
    ? `Change the ${providerLabel} account self-work uses`
    : `Change the ${providerLabel} account for threads, tasks and Rem’s replies`;
}
