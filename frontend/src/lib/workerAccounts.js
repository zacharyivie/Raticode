// The provider account an organization worker's turns run as. A worker stores an
// account's ID (account_id in /api/provider/accounts), so renaming the account keeps the
// choice; null is the provider's default sign-in.

function stateLabel(account) {
  if (account.needs_sign_in) return "Needs sign-in: runs on the default sign-in until you sign in";
  if (account.state === "signed_in") return "Signed in";
  if (account.state === "unavailable") return "CLI not found";
  return "Not checked";
}

/** The provider's accounts, from the /api/provider/accounts list. */
export function providerAccounts(accounts, provider) {
  return (Array.isArray(accounts) ? accounts : []).filter(account => account?.provider === provider && account.account_id);
}

/**
 * The Account choices for a worker of this provider: the default sign-in, then each
 * account. A saved account that's gone stays listed, saying what its turns do instead.
 */
export function workerAccountOptions(accounts, provider, current) {
  const own = providerAccounts(accounts, provider);
  const options = [
    { id: "", label: "Default sign-in", meta: "The provider’s usual sign-in" },
    ...own.map(account => ({ id: account.account_id, label: account.name, meta: stateLabel(account) })),
  ];
  if (current && !own.some(account => account.account_id === current)) {
    options.push({ id: current, label: "Removed account", meta: "Runs on the default sign-in" });
  }
  return options;
}

/** Whether the worker editor shows the Account choice: the provider has accounts, or one is saved. */
export function showsAccountChoice(accounts, provider, current) {
  return Boolean(current) || providerAccounts(accounts, provider).length > 0;
}

/** The account a worker keeps after switching provider: its own only when it belongs to the new one. */
export function accountAfterProviderChange(accounts, provider, current) {
  if (!current) return null;
  return providerAccounts(accounts, provider).some(account => account.account_id === current) ? current : null;
}

/** The account's name for a worker's facts, or the default sign-in. */
export function workerAccountLabel(accounts, employee) {
  if (!employee?.account) return "Default sign-in";
  const found = providerAccounts(accounts, employee.provider).find(account => account.account_id === employee.account);
  return found ? found.name : "Removed account (runs on the default sign-in)";
}
