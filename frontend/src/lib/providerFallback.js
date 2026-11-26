// Whether a run's model steps switched to a fallback provider. The runs list already
// carries each switch's sentence as the fallback's line (gofer.ui.workflow_runs).

// Whether a run's fallbacks include a real switch, not only skipped accounts.
export function switchedProvider(fallbacks) {
  return Array.isArray(fallbacks) && fallbacks.some((item) => item && !item.skipped);
}
