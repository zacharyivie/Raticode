import { useEffect, useRef, useState } from "react";
import { DEFAULT_REM_RESOURCES } from "../lib/remResources.js";

export { DEFAULT_REM_RESOURCES } from "../lib/remResources.js";

// Keep editable strings untouched until blur, including partially entered paths and URLs.
function DraftField({ label, value, onCommit, placeholder, multiline = false }) {
  const [draft, setDraft] = useState(value || "");
  const focused = useRef(false);
  useEffect(() => { if (!focused.current) setDraft(value || ""); }, [value]);
  const Input = multiline ? "textarea" : "input";
  return <Input aria-label={label} className="h-8 min-w-0 flex-1 rounded border border-line bg-white px-2 text-xs text-ink focus-visible:outline" placeholder={placeholder} value={draft} onChange={(event) => setDraft(event.target.value)} onFocus={() => { focused.current = true; }} onBlur={() => { focused.current = false; onCommit(draft); }} onKeyDown={(event) => { if (event.key === "Enter" && !multiline) { event.preventDefault(); event.currentTarget.blur(); } }} />;
}

export default function RemResources({ value = DEFAULT_REM_RESOURCES, onChange }) {
  const config = { ...DEFAULT_REM_RESOURCES, ...value };
  const update = (patch) => onChange({ ...config, ...patch });
  const updateItem = (key, index, patch) => update({ [key]: config[key].map((item, position) => position === index ? { ...item, ...patch } : item) });
  return <div className="space-y-3 text-xs text-ink">
    {remResourceError(config) ? <p role="status" className="text-xs text-red-700">{remResourceError(config)}</p> : null}
    <fieldset className="flex flex-wrap gap-3">
      <legend className="mb-2 font-semibold">Tools</legend>
      <label className="flex items-center gap-1.5"><input type="checkbox" checked={config.shell} onChange={(event) => update({ shell: event.target.checked })} />Run commands</label>
      <label className="flex items-center gap-1.5"><input type="checkbox" checked={config.web} onChange={(event) => update({ web: event.target.checked })} />Search the web</label>
    </fieldset>
    <fieldset className="space-y-2">
      <legend className="mb-1 font-semibold">Skills</legend>
      <p className="text-[11px] leading-4 text-muted">Add skill folders. Rem reads instructions when needed. Provider-installed skills keep their own defaults.</p>
      {config.skills.map((skill, index) => <div key={index} className="flex items-center gap-1">
        <input aria-label={`Enable skill ${index + 1}`} type="checkbox" checked={skill.enabled !== false} onChange={(event) => updateItem("skills", index, { enabled: event.target.checked })} />
        <DraftField label={`Skill ${index + 1} path`} value={skill.path} placeholder="/path/to/skill" onCommit={(path) => updateItem("skills", index, { path })} />
        <button aria-label={`Remove skill ${index + 1}`} className="h-8 px-2 text-muted hover:text-red-700" type="button" onClick={() => update({ skills: config.skills.filter((_, i) => i !== index) })}>×</button>
      </div>)}
      <button className="rounded border border-line px-2 py-1.5 hover:bg-slate-50" type="button" onClick={() => update({ skills: [...config.skills, { path: "", enabled: true }] })}>Add skill</button>
    </fieldset>
    <fieldset className="space-y-2">
      <legend className="mb-1 font-semibold">MCP servers</legend>
      <p className="text-[11px] leading-4 text-muted">Connect HTTP servers or local programs. Use provider login or environment configuration for credentials.</p>
      {config.mcpServers.map((server, index) => <div key={index} className="space-y-1 border-b border-line pb-2">
        <div className="flex items-center gap-1">
          <input aria-label={`Enable server ${index + 1}`} type="checkbox" checked={server.enabled !== false} onChange={(event) => updateItem("mcpServers", index, { enabled: event.target.checked })} />
          <DraftField label={`Server ${index + 1} name`} value={server.name} placeholder="server-name" onCommit={(name) => updateItem("mcpServers", index, { name })} />
          <button aria-label={`Remove server ${index + 1}`} className="h-8 px-2 text-muted hover:text-red-700" type="button" onClick={() => update({ mcpServers: config.mcpServers.filter((_, i) => i !== index) })}>×</button>
        </div>
        <label className="flex items-center gap-2 text-[11px] text-muted">Connection
          <select aria-label={`Server ${index + 1} connection`} className="h-8 rounded border border-line bg-white px-2 text-ink" value={server.type || "http"} onChange={(event) => updateItem("mcpServers", index, { type: event.target.value })}><option value="http">HTTP endpoint</option><option value="stdio">Local program</option></select>
        </label>
        {server.type === "stdio" ? <>
          <div className="flex"><DraftField label={`Server ${index + 1} executable`} value={server.command} placeholder="Executable path or command" onCommit={(command) => updateItem("mcpServers", index, { command })} /></div>
          <div className="flex"><DraftField label={`Server ${index + 1} arguments`} value={(server.args || []).join("\n")} placeholder="One argument per line" multiline onCommit={(args) => updateItem("mcpServers", index, { args: args ? args.split("\n") : [] })} /></div>
        </> : <div className="flex"><DraftField label={`Server ${index + 1} URL`} value={server.url} placeholder="https://example.com/mcp" onCommit={(url) => updateItem("mcpServers", index, { url })} /></div>}
      </div>)}
      <button className="rounded border border-line px-2 py-1.5 hover:bg-slate-50" type="button" onClick={() => update({ mcpServers: [...config.mcpServers, { name: "", url: "", enabled: true }] })}>Add MCP server</button>
    </fieldset>
  </div>;
}

export function remResourceError(config = DEFAULT_REM_RESOURCES) {
  for (const skill of config.skills || []) {
    if (!skill.path?.trim()) return "Enter a folder for each skill, or remove the empty row.";
  }
  const names = new Set();
  for (const server of config.mcpServers || []) {
    if (!/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(server.name || "")) return "Give each MCP server a name using letters, digits, hyphens, or underscores.";
    if (names.has(server.name)) return "MCP server names must be unique.";
    names.add(server.name);
    if (server.type === "stdio") {
      if (!server.command?.trim()) return "Enter an executable for each local MCP server.";
      continue;
    }
    try {
      const url = new URL(server.url);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.hash) throw new Error();
    } catch { return "Enter an HTTP or HTTPS server URL without credentials or fragments."; }
  }
  return "";
}
