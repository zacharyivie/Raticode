// Pure helpers the app needs at startup. The sync engine itself loads lazily.

// Keep replayed diagnostics next to their originating user message, including
// turns completed before the renderer was opened. Native messages keep their order.
export function orderDeviceMessages(messages) {
  const groups = new Map();
  for (const message of messages) if (message.deviceRequestId) {
    const group = groups.get(message.deviceRequestId) || [];
    group.push(message); groups.set(message.deviceRequestId, group);
  }
  for (const group of groups.values()) group.sort((a, b) => a.deviceSequence - b.deviceSequence);
  const result = [];
  const emitted = new Set();
  const present = new Set(messages.map(message => message.id));
  for (const message of messages) {
    if (message.deviceRequestId) {
      if (present.has(message.deviceRequestId) || emitted.has(message.deviceRequestId)) continue;
      result.push(...groups.get(message.deviceRequestId));
      emitted.add(message.deviceRequestId);
      continue;
    }
    result.push(message);
    if (groups.has(message.id)) result.push(...groups.get(message.id));
  }
  return result;
}

export function mergeDeviceMessages(current, incoming) {
  const byId = new Map(current.map(message => [String(message.id), message]));
  for (const message of incoming) {
    const old = byId.get(String(message.id));
    if (!old || message.deviceRequestId) byId.set(String(message.id), { ...old, ...message });
    else if (message.origin === "phone") byId.set(String(message.id), { ...old, origin: "phone", ...(message.attachments?.length ? { attachments: message.attachments } : {}) });
  }
  return orderDeviceMessages([...byId.values()]);
}

// Phones allowed to use Raticode see these settings. The backend keeps only its
// allowlisted display and default choices; agent tool defaults stay here.
const PHONE_SETTING_SECTIONS = ["appearance", "general", "assistant", "editor", "terminal", "memory"];

export function phoneSettings(settings = {}) {
  const assistant = { ...settings.assistant };
  delete assistant.resources;
  return { appearance: settings.appearance, general: settings.general, assistant, editor: settings.editor,
    terminal: settings.terminal, memory: { reportFormat: settings.memory?.reportFormat } };
}

export function applyPhoneSettings(settings, changes = []) {
  const next = structuredClone(settings);
  for (const change of changes) {
    for (const [key, value] of Object.entries(change.values || {})) {
      const [section, field, ...rest] = key.split(".");
      if (rest.length || !field || !PHONE_SETTING_SECTIONS.includes(section)) continue;
      next[section] = { ...(next[section] || {}), [field]: value };
    }
  }
  return next;
}
