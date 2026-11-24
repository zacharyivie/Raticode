// A thread's own account, chosen in the model picker's Account menu. Each thread keeps
// one choice per provider in accountsByProvider, so switching providers and back keeps
// it. No entry means Rem's setting (Settings > Rem > Accounts) decides.

// The provider's default sign-in. Account names start with a letter or digit, so no
// account can be called this; the backend reads it as "no account".
export const DEFAULT_SIGN_IN = "@default";
export const REM_SETTING = "";

export function threadAccountChoice(thread, provider) {
  const value = thread?.accountsByProvider?.[provider];
  return typeof value === "string" ? value : REM_SETTING;
}

/** The thread's choices with this provider's set to choice; "" removes it. */
export function withThreadAccount(accountsByProvider, provider, choice) {
  const next = { ...(accountsByProvider && typeof accountsByProvider === "object" ? accountsByProvider : {}) };
  if (choice) next[provider] = choice;
  else delete next[provider];
  return next;
}

/** What a turn sends as providerProfile: the thread's choice, else Rem's setting. */
export function turnAccount({ provider, threadAccount, providerAccounts, claudeAccount }) {
  if (threadAccount) return threadAccount;
  const setting = providerAccounts && typeof providerAccounts === "object" ? providerAccounts[provider] : undefined;
  return setting ?? (provider === "claude_code" ? claudeAccount : undefined);
}

function settingLabel(setting) {
  if (setting === "auto") return "Choose by usage";
  return setting || "Default sign-in";
}

/** "Week 42% · 5h 10% used, 2h ago" from an account's last usage reading, or "". */
export function readingLabel(reading, now = Date.now()) {
  if (!reading || typeof reading !== "object") return "";
  const parts = [];
  if (typeof reading.seven_day === "number") parts.push(`Week ${Math.round(reading.seven_day)}%`);
  if (typeof reading.five_hour === "number") parts.push(`5h ${Math.round(reading.five_hour)}%`);
  if (!parts.length) return "";
  const observed = Date.parse(reading.observed_at || "");
  const age = Number.isFinite(observed) ? ageLabel(now - observed) : "";
  return `${parts.join(" · ")} used${age ? `, ${age}` : ""}`;
}

function ageLabel(ms) {
  if (ms < 0) return "";
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)} days ago`;
}

/**
 * The Account menu for one provider, or null when the provider has no accounts.
 * accounts is the /api/provider/accounts list; remSetting is Rem's setting for it.
 */
export function accountMenu({ provider, accounts, remSetting = "", choice = REM_SETTING, now = Date.now() }) {
  const own = (Array.isArray(accounts) ? accounts : []).filter(account => account?.provider === provider);
  if (!own.length) return null;
  const options = [
    { id: REM_SETTING, label: "Rem’s setting", meta: settingLabel(remSetting) },
    { id: DEFAULT_SIGN_IN, label: "Default sign-in", meta: "The provider’s usual sign-in" },
    ...own.map(account => {
      const needsSignIn = Boolean(account.needs_sign_in);
      const state = needsSignIn ? "Needs sign-in" : account.state === "signed_in" ? "Signed in" : account.state === "unavailable" ? "CLI not found" : "Not checked";
      const reading = readingLabel(account.last_reading, now);
      return {
        id: account.name,
        label: account.name,
        meta: reading && !needsSignIn ? `${state} · ${reading}` : state,
        needsSignIn,
        disabled: needsSignIn,
      };
    }),
  ];
  const known = options.some(option => option.id === choice);
  if (!known) options.push({ id: choice, label: choice, meta: "Removed", disabled: true });
  const selected = options.find(option => option.id === choice);
  return {
    options,
    choice,
    // The picker's label: the account a turn runs as.
    label: choice === REM_SETTING ? `Rem’s setting · ${settingLabel(remSetting)}` : choice === DEFAULT_SIGN_IN ? "Default sign-in" : selected?.label || choice,
  };
}

/** The account part of the switch divider, such as "work" or "Default sign-in". */
export function accountSwitchLabel(choice, remSetting = "") {
  if (choice === DEFAULT_SIGN_IN) return "Default sign-in";
  if (!choice) return `Rem’s setting (${settingLabel(remSetting)})`;
  return choice;
}

/** The warning shown after changing a started thread's account. */
export function accountChangeWarning(providerName, label) {
  return `Your next message starts a new ${providerName} session on ${label}, as switching providers does. Rem gives it the conversation so far.`;
}
