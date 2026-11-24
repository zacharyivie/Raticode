// The Studio's Profile menus. An account profile reads as an account with its sign-in
// state, "work · Claude Code account · Needs sign-in", from the provider accounts list
// Settings › Providers uses (/api/provider/accounts). Other profiles, such as API-key
// ones, read as before: "name (model)".

/** A workflow provider ID ("claude-code") as a profile's subscription ("claude_code"). */
export function profileSubscription(provider) {
  return typeof provider === "string" ? provider.replaceAll("-", "_") : provider;
}

function signInLabel(account) {
  if (!account) return "";
  if (account.needs_sign_in || account.state === "needs_sign_in") return "Needs sign-in";
  if (account.state === "signed_in") return "Signed in";
  if (account.state === "unavailable") return "CLI not found";
  return "Not checked";
}

function providerName(profile, account, providers) {
  const provider = (Array.isArray(providers) ? providers : []).find(item => item?.id === profile.subscription);
  if (provider?.label) return provider.label;
  // The accounts list labels each account "Claude Code · work".
  const suffix = ` · ${profile.name}`;
  if (typeof account?.label === "string" && account.label.endsWith(suffix)) return account.label.slice(0, -suffix.length);
  return "";
}

/** One profile's menu label. accounts and providers come from /api/provider/accounts. */
export function profileOptionLabel(profile, { accounts = [], providers = [] } = {}) {
  if (!profile?.account_id) return profile?.model ? `${profile.name} (${profile.model})` : String(profile?.name ?? "");
  const account = (Array.isArray(accounts) ? accounts : []).find(
    item => item?.name === profile.name && (!item.provider || item.provider === profile.subscription),
  );
  const state = signInLabel(account);
  const provider = providerName(profile, account, providers);
  return [profile.name, provider ? `${provider} account` : "Account", state].filter(Boolean).join(" · ");
}

/**
 * The [value, label] options of a Profile menu: None, then the provider's profiles.
 * With current, a profile the list doesn't have stays chosen and reads "Not found".
 */
export function profileSelectOptions(providerProfiles = [], subscription, { accounts = [], providers = [], current } = {}) {
  const options = [["", "None"]];
  (Array.isArray(providerProfiles) ? providerProfiles : [])
    .filter(profile => !subscription || profile.subscription === subscription)
    .forEach(profile => options.push([profile.name, profileOptionLabel(profile, { accounts, providers })]));
  if (current && !options.some(([value]) => value === current)) options.push([current, `${current} · Not found`]);
  return options;
}
