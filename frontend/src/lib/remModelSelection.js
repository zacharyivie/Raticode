// Record the selection on submitted user messages, never on picker changes.
// Keeping the divider with its message preserves it through paging and forks.
// account is the thread's own account choice for the provider (threadAccounts.js),
// left out when the thread follows Rem's account setting.
export function remModelSelection(provider, model, effort, capability, account = "") {
  const modelCapability = capability?.models?.find(item => item.id === model);
  return { provider, model: model || "cli-default", effort: effort || modelCapability?.defaultEffort || "", ...(account ? { account } : {}) };
}

function accountPart(account) {
  if (account === "@default") return "default sign-in";
  return account || "Rem’s account setting";
}

export function remModelSwitchBanner(previous, selection, providerName) {
  const accountChanged = (previous?.account || "") !== (selection.account || "");
  if (!previous || (!accountChanged && ["provider", "model", "effort"].every(key => previous[key] === selection[key]))) return null;
  const account = selection.account || accountChanged ? ` - ${accountPart(selection.account)}` : "";
  return `switched to ${providerName || selection.provider} - ${selection.model === "cli-default" ? "CLI default" : selection.model} - ${selection.effort || "default effort"}${account}`;
}

export async function previousRemModelSelection(repository, threadId, messages, editedMessageId = null) {
  const end = editedMessageId ? messages.findIndex(message => message.id === editedMessageId) : messages.length;
  const previous = messages.slice(0, end).findLast(message => message.role === "user");
  if (previous) return previous.remModelSelection;
  if (!repository.durable) return undefined;

  // The loaded window may contain only tool output. Read older pages until the
  // preceding user message is found, without loading the entire conversation.
  let before = editedMessageId ? (await repository.around(threadId, editedMessageId, 1)).before : Infinity;
  while (true) {
    const page = await repository.page(threadId, before);
    const user = page.messages.findLast(message => message.role === "user");
    if (user) return user.remModelSelection;
    if (!page.hasMore) return undefined;
    before = page.before;
  }
}
