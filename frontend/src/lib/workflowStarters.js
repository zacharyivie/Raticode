// What a starter needs before it can run, in the same words as `rattish starters`.
export function starterNeeds(starter) {
  if (!starter) return "";
  const providers = Array.isArray(starter.providers) ? starter.providers.filter(Boolean) : [];
  const secrets = Array.isArray(starter.secrets) ? starter.secrets.filter(Boolean) : [];
  const needs = [
    ...(starter.needsGitForWindows ? ["Git for Windows"] : []),
    ...providers.map((name) => `the ${name} provider`),
    ...secrets.map((name) => `the secret ${name} when used`),
  ];
  return `Needs: ${needs.length ? needs.join(", ") : "nothing beyond Raticode"}.`;
}

// The hint under "Start from": the summary, what it needs and whether tests come with it.
export function starterHint(starter) {
  if (!starter) return "An empty workflow. Or pick a starter with inputs, a test set and a guide.";
  return [starter.summary, starterNeeds(starter), starter.hasTests ? "Comes with a test set." : ""]
    .filter(Boolean)
    .join(" ");
}

export const GIT_FOR_WINDOWS_URL = "https://git-scm.com/downloads/win";

// The starter's name in the "Start from" list. On Windows without Git for Windows,
// starters whose steps ask for bash say so before you pick them.
export function starterOptionLabel(starter) {
  if (!starter) return "";
  return starter.needsGitForWindows ? `${starter.name} (Needs Git for Windows)` : starter.name;
}

// Whether the apps a starter's model steps call are on this computer, and which installed
// app can call them instead. apps are the provider capabilities, each with status from
// providerStatus; providerApps are the app IDs that can run agent and common-llm-task
// nodes. checking is true until discovery has answered for the starter's own apps.
export function starterProviderChoice(starter, apps, providerApps, remProvider) {
  const wanted = Array.isArray(starter?.providerApps) ? starter.providerApps : [];
  const none = { checking: false, missing: [], options: [], defaultId: "" };
  if (!wanted.length) return none;
  const byId = new Map((apps ?? []).map((app) => [app.id, app]));
  const known = (app) => app && app.status !== "checking";
  const turnedOff = (app) => app.enabled === false;
  const absent = (app) => app.discoveryStatus === "missing";
  if (!wanted.every((id) => known(byId.get(id)))) return { ...none, checking: true };
  const missing = wanted
    .map((id) => byId.get(id))
    .filter((app) => absent(app) || turnedOff(app))
    .map((app) => ({ id: app.id, name: app.displayName || app.id, turnedOff: !absent(app) }));
  if (!missing.length) return none;
  const runnable = new Set(Array.isArray(providerApps) ? providerApps : []);
  const options = (apps ?? [])
    .filter((app) => runnable.has(app.id) && known(app) && !absent(app) && !turnedOff(app))
    .map((app) => ({ id: app.id, name: app.displayName || app.id }));
  const defaultId = options.some((app) => app.id === remProvider) ? remProvider : options[0]?.id ?? "";
  return { checking: false, missing, options, defaultId };
}

// The line beside Needs for an app a starter calls that this computer can't run.
export function missingAppLine(app) {
  return app.turnedOff
    ? `${app.name} is turned off in Settings > Providers.`
    : `${app.name} isn't installed on this computer.`;
}
