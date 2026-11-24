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
