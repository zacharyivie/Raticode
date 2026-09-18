import { pathKey, samePath } from "./workspacePaths.js";
import { shareInFlight, startPolling } from "./refresh.js";

export const RECENT_PROJECT_REFRESH_MS = 60_000;
export const WORKFLOW_DISCOVERY_MS = 30_000;
export const DOCTOR_REFRESH_MS = 60_000;

// Keep validation local to an App instance. Reordering recent roots should not
// repeat filesystem and Git work for every other project.
export function createRecentProjectValidator({ now = Date.now, maxEntries = 64 } = {}) {
  const entries = new Map();
  const keyFor = (root, selected) => JSON.stringify([pathKey(root), pathKey(selected)]);
  const put = (key, entry) => {
    entries.delete(key);
    entries.set(key, entry);
    while (entries.size > maxEntries) entries.delete(entries.keys().next().value);
  };
  return {
    remember(mainProjectRoot, selectedProjectRoot) {
      put(keyFor(mainProjectRoot, selectedProjectRoot), {
        expires: now() + RECENT_PROJECT_REFRESH_MS,
        value: Promise.resolve({ mainProjectRoot, selectedProjectRoot }),
      });
    },
    validate(projectRoot, selectedProjectRoot, workspace, resolveMainRoot) {
      const key = keyFor(projectRoot, selectedProjectRoot);
      return shareInFlight(`recent-path:${key}`, async () => {
        const check = async (root, renew = true) => {
          try {
            if (renew && !entries.has(keyFor(projectRoot, root))) await workspace.trustProjectRoot?.(root);
            return Boolean((await workspace.getPathInfo(root))?.isDirectory);
          } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            if (/does not exist|no such file|not a directory/i.test(message)) return false;
            throw error;
          }
        };
        try {
          // Filesystem existence must stay fresh even while Git metadata is cached.
          let selected = selectedProjectRoot;
          if (!await check(selected)) {
            entries.delete(key);
            if (samePath(selected, projectRoot) || !await check(projectRoot)) return null;
            selected = projectRoot;
          }
          const cached = entries.get(keyFor(projectRoot, selected));
          if (cached && cached.expires > now()) {
            const result = await cached.value;
            if (!samePath(result.mainProjectRoot, selected) && !await check(result.mainProjectRoot, false)) {
              entries.delete(key);
              return { mainProjectRoot: selected, selectedProjectRoot: selected };
            }
            return result;
          }
          const payload = await shareInFlight(`recent-worktrees:${pathKey(selected)}`,
            () => workspace.gitWorktrees?.(selected));
          const result = { mainProjectRoot: resolveMainRoot(payload, projectRoot), selectedProjectRoot: selected };
          put(keyFor(projectRoot, selected), { expires: now() + RECENT_PROJECT_REFRESH_MS, value: result });
          return result;
        } catch {
          // A temporary access or Git failure is not evidence of deletion.
          return { mainProjectRoot: projectRoot, selectedProjectRoot };
        }
      });
    },
  };
}

export function startWorkspacePolling({ refreshLive, discover, doctor }, options = {}) {
  const stops = [
    startPolling(refreshLive, options),
    startPolling(discover, { ...options, interval: WORKFLOW_DISCOVERY_MS }),
    startPolling(doctor, { ...options, interval: DOCTOR_REFRESH_MS }),
  ];
  return () => stops.forEach((stop) => stop());
}
