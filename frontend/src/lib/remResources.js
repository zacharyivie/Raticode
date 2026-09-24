export const DEFAULT_REM_RESOURCES = { shell: true, web: true, skills: [], mcpServers: [] };

// Capture a complete, independent configuration before starting a provider turn.
// Explicit false values must survive defaults and later composer edits.
export function snapshotRemResources(resources) {
  return structuredClone({ ...DEFAULT_REM_RESOURCES, ...resources });
}
