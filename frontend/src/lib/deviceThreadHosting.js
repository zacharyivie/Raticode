import { reportThemeContext } from "./reportThemes.js";

// Loaded only while another desktop may run threads here, keeping it out of the app bundle.
export async function syncThreadHosting(peers, { settings, memory, assistant, workspace, projects }, action, sent) {
  const workflow = {
    remThreads: { projects }, remResources: assistant.resources || {},
    remSwarmAccess: { enabled: assistant.swarmAccessEnabled !== false,
      workspaceGrants: Object.fromEntries(projects.filter(p => p.grantId).map(p => [p.root, p.grantId])) },
    remReportTheme: reportThemeContext(memory),
    remBrowser: { target: settings.browser?.agentBrowser || "raticode" },
    remSecondBrain: { enabled: memory.secondBrainEnabled === true, root: memory.secondBrainRoot,
      format: memory.secondBrainFormat, grantId: workspace.pathGrantForApi(memory.secondBrainRoot) },
  };
  const fingerprint = JSON.stringify(workflow);
  for (const peer of peers) {
    if (sent.get(`host:${peer}`) === fingerprint) continue;
    await action({ action: "host_template", device_id: peer, metadata: {}, workflow });
    sent.set(`host:${peer}`, fingerprint);
  }
}
