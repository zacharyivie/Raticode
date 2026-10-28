import { lazy, Suspense, useId, useState } from "react";
import { AppWindow, BookOpen, FolderOpen, Globe, Network, Pencil, Plug, Plus, Search, Terminal, Trash2 } from "lucide-react";
import { DEFAULT_REM_RESOURCES, remResourceError } from "../lib/remResources.js";
import { editRemLibrary, skillKey, useRemLibrary } from "../lib/remLibrary.js";
import { IconAction, ToolRow, ToolSection } from "./RemToolRows.jsx";
const SecondBrainResources = lazy(() => import("./SecondBrainResources.jsx"));

export { DEFAULT_REM_RESOURCES, remResourceError, remToolSummary } from "../lib/remResources.js";

const ORIGINS = { raticode: "Raticode", agents: "Agents", claude: "Claude", codex: "Codex", added: "Folder" };
const SEARCH_THRESHOLD = 8;
const baseName = (path = "") => skillKey(path).split(/[\\/]/).pop() || path;
const serverDetail = (server) => server.description || (server.type === "stdio"
  ? [server.command, ...(server.args || [])].join(" ")
  : server.url);
const serverLink = (server) => ({ name: server.name, type: server.type || "http", url: server.url || "", command: server.command || "", args: server.args || [], enabled: true, library: true });

// Thread selections reference Raticode's shared library. Entries that exist only
// in this selection (older threads, or an unavailable library) remain editable.
export function remToolRows(config, library) {
  const librarySkills = library.skills || [];
  const libraryServers = library.mcpServers || [];
  const skillKeys = new Set(librarySkills.map((skill) => skillKey(skill.path)));
  const serverNames = new Set(libraryServers.map((server) => server.name));
  const skills = [
    ...librarySkills.map((skill) => ({ kind: "library", skill, selected: config.skills.find((item) => skillKey(item.path) === skillKey(skill.path)) })),
    ...config.skills.filter((item) => !skillKeys.has(skillKey(item.path))).map((item) => ({ kind: "thread", skill: item, selected: item })),
  ];
  const servers = [
    ...libraryServers.map((server) => ({ kind: "library", server, selected: config.mcpServers.find((item) => item.name === server.name) })),
    ...config.mcpServers.filter((item) => !serverNames.has(item.name)).map((item) => ({ kind: serverRowKind(item, library), server: item, selected: item })),
  ];
  return { skills, servers };
}

function serverRowKind(item, library) {
  if (!item.library) return "thread";
  // Until the library loads, show linked servers from their saved copy.
  if (!library.loaded) return "library";
  return library.error ? "thread" : "removed";
}

export default function RemResources({ value = DEFAULT_REM_RESOURCES, onChange, live = false, memory }) {
  const config = { ...DEFAULT_REM_RESOURCES, ...value };
  const library = useRemLibrary({ live });
  const libraryReady = library.loaded && !library.error;
  const [query, setQuery] = useState("");
  const [form, setForm] = useState(null);
  const [failure, setFailure] = useState("");
  const update = (patch) => onChange({ ...config, ...patch });
  const { skills, servers } = remToolRows(config, library);
  const needle = query.trim().toLowerCase();
  const matches = (...fields) => !needle || fields.some((field) => String(field || "").toLowerCase().includes(needle));
  const visibleSkills = skills.filter(({ skill }) => matches(skill.name, skill.description, skill.path));
  const visibleServers = servers.filter(({ server }) => matches(server.name, server.description, server.url, server.command));
  const issue = remResourceError(config);

  const setSkill = (row, on) => {
    if (row.kind === "thread") {
      update({ skills: config.skills.map((item) => item === row.selected ? { ...item, enabled: on } : item) });
    } else if (on) {
      update({ skills: row.selected ? config.skills.map((item) => item === row.selected ? { ...item, enabled: true } : item) : [...config.skills, { path: row.skill.path, enabled: true }] });
    } else {
      update({ skills: config.skills.filter((item) => item !== row.selected) });
    }
  };
  const setServer = (row, on) => {
    if (row.kind !== "library") {
      update({ mcpServers: config.mcpServers.map((item) => item === row.selected ? { ...item, enabled: on } : item) });
    } else if (on) {
      const link = serverLink(row.server);
      update({ mcpServers: row.selected ? config.mcpServers.map((item) => item === row.selected ? link : item) : [...config.mcpServers, link] });
    } else {
      update({ mcpServers: config.mcpServers.filter((item) => item !== row.selected) });
    }
  };

  const addSkill = async (path) => {
    if (libraryReady) await editRemLibrary({ action: "add_skill", path });
    if (!config.skills.some((item) => skillKey(item.path) === skillKey(path))) update({ skills: [...config.skills, { path, enabled: true }] });
    setForm(null);
  };
  const saveServer = async (server, original) => {
    const others = config.mcpServers.filter((item) => item !== original?.selected);
    if (libraryReady) {
      await editRemLibrary({ action: "save_mcp", server, replace: original?.kind === "library" ? original.server.name : undefined });
      // Adding turns the server on here; editing keeps this thread's choice.
      const keep = !original || original.selected;
      update({ mcpServers: [...others.filter((item) => item.name !== server.name), ...(keep ? [serverLink(server)] : [])] });
    } else {
      const issue = remResourceError({ mcpServers: [...others, server] });
      if (issue) throw new Error(issue);
      const definition = { name: server.name, type: server.type, url: server.url || "", command: server.command || "", args: server.args || [] };
      update({ mcpServers: [...others, { ...definition, enabled: original?.selected?.enabled ?? true }] });
    }
    setForm(null);
  };
  const removeServer = async (row) => {
    if (row.kind === "library") await editRemLibrary({ action: "remove_mcp", name: row.server.name });
    update({ mcpServers: config.mcpServers.filter((item) => item !== row.selected) });
  };
  const removeSkill = async (row) => {
    if (row.kind === "library" && row.skill.registered) await editRemLibrary({ action: "remove_skill", path: row.skill.path });
    update({ skills: config.skills.filter((item) => item !== row.selected) });
  };
  const run = (action) => (...args) => {
    setFailure("");
    return action(...args).catch((error) => {
      if (form) setForm((current) => current && { ...current, error: error.message });
      else setFailure(error.message);
    });
  };

  const enabledSkills = config.skills.filter((skill) => skill.enabled !== false).length;
  const enabledServers = config.mcpServers.filter((server) => server.enabled !== false).length;

  return <div className="rem-tools">
    {issue || failure ? <p role="alert" className="rem-tools__alert">{issue || failure}</p> : null}
    {skills.length + servers.length > SEARCH_THRESHOLD ? <label className="rem-tools__search">
      <Search aria-hidden="true" size={14} />
      <input aria-label="Filter skills and MCP servers" placeholder="Filter skills and servers" value={query} onChange={(event) => setQuery(event.target.value)} />
    </label> : null}

    <ToolSection title="Built-in">
      <ToolRow icon={Terminal} title="Run commands" detail="Shell commands, scripts and tests" checked={config.shell} onChange={(shell) => update({ shell })} />
      <ToolRow icon={Globe} title="Search the web" detail="Look up and read web pages" checked={config.web} onChange={(web) => update({ web })} />
      <ToolRow icon={Network} title="Subagents" detail="Hand parts of a task to helper agents" checked={config.agents} onChange={(agents) => update({ agents })} />
      <ToolRow icon={AppWindow} title="Browser" detail="Open and test pages in the browser chosen in Settings > Browser" checked={config.browser} onChange={(browser) => update({ browser })} />
    </ToolSection>

    {/* Settings > Memory turns Second Brain on before brains can be chosen here. */}
    {memory?.secondBrainEnabled ? <Suspense fallback={null}><SecondBrainResources resources={config} legacyRoot={memory.secondBrainRoot} onChange={(disabledSecondBrains) => update({ secondBrains: undefined, disabledSecondBrains })} /></Suspense> : null}

    <ToolSection title="Skills" summary={skills.length ? `${enabledSkills} on` : ""} action={<IconAction label="Add skill" icon={Plus} onClick={() => setForm({ type: "skill" })} />}>
      {form?.type === "skill" ? <SkillForm error={form.error} onCancel={() => setForm(null)} onSubmit={run(addSkill)} /> : null}
      {visibleSkills.map((row) => { const on = row.selected ? row.selected.enabled !== false : false; return <ToolRow
        key={`${row.kind}:${row.skill.path}`}
        icon={BookOpen}
        title={row.kind === "library" ? row.skill.name : baseName(row.skill.path) || "Unnamed skill"}
        detail={row.kind === "library" ? (row.skill.available ? row.skill.description || row.skill.path : "SKILL.md is missing") : row.skill.path}
        // An enabled skill belongs to Rem for every provider; off, it shows the folder it came from.
        badge={row.kind !== "library" ? "This thread" : on ? "Rem" : ORIGINS[row.skill.origin]}
        checked={on}
        disabled={row.kind === "library" && !row.skill.available && !row.selected}
        onChange={(on) => setSkill(row, on)}
        actions={row.kind === "thread" || (row.skill.registered && row.skill.origin === "added") ? <IconAction danger label={`Remove skill ${row.kind === "library" ? row.skill.name : baseName(row.skill.path)}`} icon={Trash2} onClick={run(() => removeSkill(row))} /> : null}
      />; })}
      {!skills.length && form?.type !== "skill" ? <li className="rem-tools__empty">No skills yet. Ask Rem to write one, or add a folder that contains a SKILL.md.</li> : null}
    </ToolSection>

    <ToolSection title="MCP servers" summary={servers.length ? `${enabledServers} on` : ""} action={<IconAction label="Add MCP server" icon={Plus} onClick={() => setForm({ type: "server" })} />}>
      {form?.type === "server" && !form.row ? <ServerForm error={form.error} onCancel={() => setForm(null)} onSubmit={run((server) => saveServer(server))} /> : null}
      {visibleServers.map((row) => form?.type === "server" && form.row === row
        ? <ServerForm key={`edit:${row.server.name}`} initial={row.server} error={form.error} onCancel={() => setForm(null)} onSubmit={run((server) => saveServer(server, row))} />
        : <ToolRow
          key={`${row.kind}:${row.server.name}`}
          icon={Plug}
          title={row.server.name || "Unnamed server"}
          detail={row.kind === "removed" ? "Removed from Raticode. Turn off or remove." : serverDetail(row.server)}
          badge={row.kind === "library" ? (row.server.type === "stdio" ? "Local" : "HTTP") : row.kind === "removed" ? "Removed" : "This thread"}
          checked={row.selected ? row.selected.enabled !== false : false}
          disabled={row.kind === "removed" && row.selected?.enabled === false}
          onChange={(on) => setServer(row, on)}
          actions={<>
            {row.kind !== "removed" ? <IconAction label={`Edit server ${row.server.name}`} icon={Pencil} onClick={() => setForm({ type: "server", row })} /> : null}
            <IconAction danger label={`Remove server ${row.server.name}`} icon={Trash2} onClick={run(() => removeServer(row))} />
          </>}
        />)}
      {!servers.length && form?.type !== "server" ? <li className="rem-tools__empty">No MCP servers yet. Ask Rem to connect one, or add an HTTP endpoint or local program.</li> : null}
    </ToolSection>

    {library.error ? <p className="rem-tools__note">{library.error} Items you add here apply to this selection only.</p> : <p className="rem-tools__note">Rem can set up skills and MCP servers for you. New ones appear here, off until you turn them on.</p>}
  </div>;
}

function FormError({ message }) {
  return message ? <p role="alert" className="rem-tools__form-error">{message}</p> : null;
}

function SkillForm({ error, onCancel, onSubmit }) {
  const id = useId();
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const selectPath = window.goferDesktop?.workspace?.selectPath;
  const submit = async (event) => {
    event.preventDefault();
    if (!path.trim()) return;
    setBusy(true);
    try { await onSubmit(path.trim()); } finally { setBusy(false); }
  };
  return <li className="rem-tools__form-row"><form className="rem-tools__form" onSubmit={submit}>
    <label className="rem-tools__field" htmlFor={`${id}-path`}>Skill folder</label>
    <div className="rem-tools__field-row">
      <input id={`${id}-path`} aria-label="Skill folder" autoFocus value={path} placeholder="/path/to/skill" onChange={(event) => setPath(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") onCancel(); }} />
      {selectPath ? <IconAction label="Browse for skill folder" icon={FolderOpen} onClick={async () => { const chosen = await selectPath({ directoryOnly: true, currentPath: path }); if (chosen) setPath(chosen); }} /> : null}
    </div>
    <p className="rem-tools__field-hint">A folder with a SKILL.md. Rem can use it with any provider.</p>
    <FormError message={error} />
    <div className="rem-tools__form-actions">
      <button type="button" className="rem-tools__button" onClick={onCancel}>Cancel</button>
      <button type="submit" className="rem-tools__button is-primary" disabled={busy || !path.trim()}>Add skill</button>
    </div>
  </form></li>;
}

function ServerForm({ initial, error, onCancel, onSubmit }) {
  const id = useId();
  const [draft, setDraft] = useState(() => ({
    name: initial?.name || "", type: initial?.type || "http", url: initial?.url || "",
    command: initial?.command || "", args: (initial?.args || []).join("\n"), description: initial?.description || "",
  }));
  const [busy, setBusy] = useState(false);
  const set = (patch) => setDraft((current) => ({ ...current, ...patch }));
  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    const server = draft.type === "stdio"
      ? { name: draft.name.trim(), type: "stdio", command: draft.command.trim(), args: draft.args ? draft.args.split("\n").filter((arg) => arg !== "") : [], description: draft.description.trim() }
      : { name: draft.name.trim(), type: "http", url: draft.url.trim(), description: draft.description.trim() };
    try { await onSubmit(server); } finally { setBusy(false); }
  };
  return <li className="rem-tools__form-row"><form className="rem-tools__form" onSubmit={submit} onKeyDown={(event) => { if (event.key === "Escape") onCancel(); }}>
    <label className="rem-tools__field" htmlFor={`${id}-name`}>Name</label>
    <input id={`${id}-name`} aria-label="Server name" autoFocus value={draft.name} placeholder="docs" onChange={(event) => set({ name: event.target.value })} />
    <div className="rem-tools__segmented" role="radiogroup" aria-label="Connection">
      {[["http", "HTTP endpoint"], ["stdio", "Local program"]].map(([type, label]) => <button key={type} type="button" role="radio" aria-checked={draft.type === type} className={draft.type === type ? "is-active" : ""} onClick={() => set({ type })}>{label}</button>)}
    </div>
    {draft.type === "stdio" ? <>
      <label className="rem-tools__field" htmlFor={`${id}-command`}>Program</label>
      <input id={`${id}-command`} aria-label="Server program" value={draft.command} placeholder="npx" onChange={(event) => set({ command: event.target.value })} />
      <label className="rem-tools__field" htmlFor={`${id}-args`}>Arguments, one per line</label>
      <textarea id={`${id}-args`} aria-label="Server arguments" rows={2} value={draft.args} placeholder={"-y\n@modelcontextprotocol/server-filesystem"} onChange={(event) => set({ args: event.target.value })} />
    </> : <>
      <label className="rem-tools__field" htmlFor={`${id}-url`}>URL</label>
      <input id={`${id}-url`} aria-label="Server URL" value={draft.url} placeholder="https://example.com/mcp" onChange={(event) => set({ url: event.target.value })} />
    </>}
    <label className="rem-tools__field" htmlFor={`${id}-description`}>Description <span>optional</span></label>
    <input id={`${id}-description`} aria-label="Server description" value={draft.description} placeholder="What this server provides" onChange={(event) => set({ description: event.target.value })} />
    <p className="rem-tools__field-hint">Credentials come from the program&apos;s environment or the provider&apos;s login, never from here.</p>
    <FormError message={error} />
    <div className="rem-tools__form-actions">
      <button type="button" className="rem-tools__button" onClick={onCancel}>Cancel</button>
      <button type="submit" className="rem-tools__button is-primary" disabled={busy || !draft.name.trim()}>{initial ? "Save" : "Add server"}</button>
    </div>
  </form></li>;
}
