/*
THESIS: Settings behave like an editor command center, not a form dumped into a modal.
OWN-WORLD: Raticode's zinc surfaces, indigo selection, compact rows, and restrained floating depth.
STORY: Choose a category, change a value, and see it take effect without leaving the workspace.
FIRST VIEWPORT: A searchable two-column dropdown with categories left and dense setting rows right.
FORM: Operate-mode app popover extending the existing studio chrome.
*/
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  Brain,
  ChartNoAxesCombined,
  Code2,
  Command,
  Globe2,
  GripVertical,
  LayoutPanelLeft,
  MonitorCog,
  Palette,
  PcCase,
  PlugZap,
  Puzzle,
  RotateCcw,
  Search,
  Terminal,
  Wrench,
  X,
} from "lucide-react";

import DeveloperSettings, { RemMemorySettings } from "./DeveloperSettings.jsx";
import RemResources from "./RemResources.jsx";
import ProviderSettings from "./ProviderSettings.jsx";
import CommitMessageSettings from "./CommitMessageSettings.jsx";
import UsageDashboard from "./UsageDashboard.jsx";
import PairedDevices from "./PairedDevices.jsx";
import "./SettingsPopover.css";
import { PLUGIN_ICONS } from "./pluginIcons.js";
import { useProviderSettingsCapabilities } from "../lib/useProviderSettingsCapabilities.js";
import { ARCHIVE_RETENTION_OPTIONS } from "../lib/threadActivity.js";
import { audioInputConstraints, listAudioInputDevices } from "../lib/audioDevices.js";
import {
  DEFAULT_APP_SETTINGS,
  KEYBINDING_COMMANDS,
  CHORD_COMMAND_IDS,
  eventToKeybinding,
  formatKeybinding,
  keybindingConflictIds,
} from "../lib/settings.js";
import {
  DEFAULT_PLUGIN_SETTINGS,
  PLUGIN_DRAG_TYPE,
  SIDEBAR_PLUGINS,
  dropPlacement,
  enabledPlugins,
  movePlugin,
  orderedPlugins,
  pluginEnabled,
  resolvePluginActivity,
  stepPlugin,
  withPluginEnabled,
} from "../lib/plugins.js";

const INITIAL_ACTIVITY_IDS = ["rem", "workflows", "files", "search", "source-control", "organizations", "second-brain"];

const CATEGORIES = [
  { id: "general", label: "General", icon: MonitorCog },
  { id: "devices", label: "Devices", icon: PcCase },
  { id: "appearance", label: "Appearance", icon: Palette },
  { id: "editor", label: "Editor", icon: Code2 },
  { id: "browser", label: "Browser", icon: Globe2 },
  { id: "terminal", label: "Terminal", icon: Terminal },
  { id: "providers", label: "Providers", icon: PlugZap },
  { id: "usage", label: "Usage", icon: ChartNoAxesCombined },
  { id: "assistant", label: "Rem", icon: Bot },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "developer", label: "Developer", icon: Wrench },
  { id: "layout", label: "Layout", icon: LayoutPanelLeft },
  { id: "plugins", label: "Plugins", icon: Puzzle },
  { id: "keybindings", label: "Keybindings", icon: Command },
];

export default function SettingsPopover({
  initialCategory = "general",
  dataDir = "",
  onChange,
  onChooseDataDirectory,
  onClose,
  onResetAll,
  open,
  settings,
}) {
  const panelRef = useRef(null);
  const searchRef = useRef(null);
  const onCloseRef = useRef(onClose);
  const [category, setCategory] = useState(initialCategory);
  const [query, setQuery] = useState("");
  const providerState = useProviderSettingsCapabilities(open);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return undefined;
    const focusFrame = window.requestAnimationFrame(() => searchRef.current?.focus());
    function handlePointerDown(event) {
      if (!panelRef.current?.contains(event.target)) onCloseRef.current?.();
    }
    function handleKeyDown(event) {
      if (event.key === "Escape") onCloseRef.current?.();
    }
    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown, true);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const revealCategory = () => {
      if (window.innerWidth <= 600) panelRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
    };
    const frame = window.requestAnimationFrame(revealCategory);
    window.addEventListener("resize", revealCategory);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", revealCategory);
    };
  }, [category, open]);

  const matchingCategories = useMemo(
    () => settingsCategoriesForQuery(query),
    [query],
  );
  if (!open) return null;

  return (
    <section
      ref={panelRef}
      aria-label="Application settings"
      className="settings-popover fixed right-3 top-[58px] z-[120] flex h-[min(650px,calc(100vh-72px))] w-[min(780px,calc(100vw-24px))] min-h-0 overflow-hidden rounded-xl border border-line bg-white text-ink shadow-panel max-[600px]:flex-col"
      role="dialog"
    >
      <aside className="flex w-44 shrink-0 flex-col border-r border-line bg-slate-50 p-2 max-[600px]:w-full max-[600px]:border-r-0 max-[600px]:border-b">
        <div className="px-2 pb-2 pt-1 max-[600px]:hidden">
          <p className="text-sm font-semibold">Settings</p>
          <p className="mt-0.5 text-[10px] text-muted">Saved on this device</p>
        </div>
        <nav aria-label="Settings categories" className="min-h-0 overflow-y-auto space-y-0.5 max-[600px]:flex max-[600px]:gap-1 max-[600px]:space-y-0 max-[600px]:overflow-x-auto">
          {CATEGORIES.map((item) => {
            const Icon = item.icon;
            const active = item.id === category;
            return (
              <button
                key={item.id}
                aria-current={active ? "page" : undefined}
                className={`flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-xs transition max-[600px]:w-auto max-[600px]:shrink-0 ${
                  active ? "bg-slate-100 font-semibold text-ink" : "text-muted hover:bg-slate-100 hover:text-ink"
                }`}
                type="button"
                onClick={() => {
                  setCategory(item.id);
                  setQuery("");
                }}
              >
                <Icon aria-hidden="true" size={14} />
                {item.label}
              </button>
            );
          })}
        </nav>
        <div className="mt-auto border-t border-line pt-2 max-[600px]:hidden">
          <button
            className="flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-xs text-muted transition hover:bg-slate-100 hover:text-ink"
            type="button"
            onClick={onResetAll}
          >
            <RotateCcw aria-hidden="true" size={14} />
            Reset all settings
          </button>
        </div>
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-4 transition-colors focus-within:border-brand">
          <Search aria-hidden="true" className="text-muted" size={14} />
          <input
            ref={searchRef}
            aria-label="Search settings"
            className="studio-search-input min-w-0 flex-1 bg-transparent text-xs text-ink outline-none placeholder:text-muted"
            placeholder="Search settings"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button
            aria-label="Close settings"
            className="grid h-7 w-7 place-items-center rounded-md text-muted transition hover:bg-slate-100 hover:text-ink"
            type="button"
            onClick={onClose}
          ><X aria-hidden="true" size={14} /></button>
        </div>

        <div className="workflow-scrollbar min-h-0 flex-1 overflow-y-auto px-5 pb-8 pt-4 max-[600px]:px-3">
          {query ? (
            matchingCategories.length ? matchingCategories.map((categoryId) => (
              <SettingsCategory
                key={categoryId}
                category={categoryId}
                query={query}
                providerState={providerState}
                settings={settings}
                dataDir={dataDir}
                onChange={onChange}
                onChooseDataDirectory={onChooseDataDirectory}
              />
            )) : <EmptySettingsSearch query={query} />
          ) : (
            <SettingsCategory
              category={category}
              providerState={providerState}
              settings={settings}
              dataDir={dataDir}
              onChange={onChange}
              onChooseDataDirectory={onChooseDataDirectory}
            />
          )}
        </div>
      </div>
    </section>
  );
}

function SettingsCategory({ category, dataDir, onChange, onChooseDataDirectory, providerState, query = "", settings }) {
  const heading = CATEGORIES.find((item) => item.id === category)?.label ?? "Settings";
  const rows = categoryRows(category, settings, onChange, providerState, {
    dataDir,
    onChooseDataDirectory,
  });
  const normalizedQuery = query.trim().toLowerCase();
  const visibleRows = normalizedQuery
    ? rows.filter((row) => row.searchText.toLowerCase().includes(normalizedQuery))
    : rows;
  if (!visibleRows.length) return null;
  return (
    <section className={query ? "mb-7" : ""}>
      <h2 className="mb-1 text-sm font-semibold">{heading}</h2>
      <div className="divide-y divide-line">
        {visibleRows.map((row) => row.element)}
      </div>
    </section>
  );
}

function categoryRows(category, settings, onChange, providerState, appControls) {
  const row = (key, label, description, element, options = {}) => ({
    element: <SettingRow key={key} label={label} description={description} stacked={options.stacked}>{element}</SettingRow>,
    searchText: `${label} ${description}`,
  });
  if (category === "providers") return [{ searchText: "Providers enabled disabled executable coding harness path apps models allowed denied", element: <ProviderSettings key="providers" providerState={providerState} /> }];
  if (category === "usage") return [{ searchText: "Usage tokens allowance limits remaining quotas credits billing spend accounts providers history refresh", element: <UsageDashboard key="usage" /> }];
  if (category === "developer") return [{ searchText: "Developer diagnostics logs logging app data storage version backend restart tools", element: <DeveloperSettings key="developer" /> }];
  if (category === "memory") return [{ searchText: "Rem memory conversation archive folder Second Brain knowledge notes reports HTML Markdown theme gallery generation provider model effort screenshot", element: <RemMemorySettings audioInputDeviceId={settings.devices.audioInputId} key="memory" value={settings.memory} onChange={onChange} providerState={providerState} /> }];
  if (category === "general") return [
    row("dataDir", "Application data directory", "Stores global Raticode state, run artifacts, and registries.", (
      <PathControl value={appControls.dataDir} onChoose={appControls.onChooseDataDirectory} />
    )),
    row("initialActivity", "Initial sidebar", "Shown on first launch or when the last sidebar cannot be restored.", (
      <SelectControl value={resolvePluginActivity(settings.plugins, settings.general.initialActivity)} onChange={(value) => onChange("general.initialActivity", value)} options={enabledPlugins(settings.plugins).filter((plugin) => INITIAL_ACTIVITY_IDS.includes(plugin.id)).map((plugin) => [plugin.id, plugin.label])} />
    )),
    row("autosave", "Autosave files", "Save file edits after a short delay.", (
      <SwitchControl checked={settings.general.autosave} onChange={(value) => onChange("general.autosave", value)} />
    )),
    row("executionMode", "Default run target", "Start runs locally or send them to the runner queue.", (
      <SelectControl value={settings.general.executionMode} onChange={(value) => onChange("general.executionMode", value)} options={[["local", "Local"], ["remote", "Runner queue"]]} />
    )),
    row("updates", "Check for updates at startup", "Ask the desktop updater for a new release when the app starts.", (
      <SwitchControl checked={settings.general.checkForUpdates} onChange={(value) => onChange("general.checkForUpdates", value)} />
    )),
    row("archivedThreadRetention", "Delete archived threads", "Permanently delete archived Rem threads with no activity for this long. A configured conversation archive keeps a copy.", (
      <SelectControl value={settings.general.archivedThreadRetention} onChange={(value) => onChange("general.archivedThreadRetention", value)} options={ARCHIVE_RETENTION_OPTIONS} />
    )),
  ];
  if (category === "appearance") return [
    row("theme", "Color theme", "Follow the operating system or keep a fixed theme.", (
      <SelectControl value={settings.appearance.theme} onChange={(value) => onChange("appearance.theme", value)} options={[["system", "System"], ["light", "Light"], ["dark", "Dark"]]} />
    )),
    row("motion", "Reduced motion", "Control smooth scrolling and nonessential interface movement.", (
      <SelectControl value={settings.appearance.reducedMotion} onChange={(value) => onChange("appearance.reducedMotion", value)} options={[["system", "System"], ["on", "On"], ["off", "Off"]]} />
    )),
  ];
  if (category === "devices") return [
    row("microphone", "Microphone", "Choose the input used for local transcription and check its signal level.", (
      <MicrophoneControl
        value={settings.devices.audioInputId}
        onChange={(value) => onChange("devices.audioInputId", value)}
      />
    )),
    {
      searchText: "Devices paired devices phone mobile desktop pairing QR trust revoke",
      element: <div key="paired-devices" className="pt-4"><PairedDevices /></div>,
    },
  ];
  if (category === "editor") return [
    row("fontSize", "Font size", "Text size in Rattish and regular code editors.", <NumberControl value={settings.editor.fontSize} min={10} max={24} suffix="px" onCommit={(value) => onChange("editor.fontSize", value)} />),
    row("lineHeight", "Line height", "Vertical spacing between editor lines.", <NumberControl value={settings.editor.lineHeight} min={14} max={40} suffix="px" onCommit={(value) => onChange("editor.lineHeight", value)} />),
    row("tabSize", "Tab size", "Spaces inserted for one indentation level.", <NumberControl value={settings.editor.tabSize} min={1} max={8} onCommit={(value) => onChange("editor.tabSize", value)} />),
    row("wordWrap", "Word wrap", "Wrap long lines instead of scrolling horizontally.", <SwitchControl checked={settings.editor.wordWrap} onChange={(value) => onChange("editor.wordWrap", value)} />),
    row("minimap", "Minimap", "Show the document overview at the right edge of the editor.", <SwitchControl checked={settings.editor.minimap} onChange={(value) => onChange("editor.minimap", value)} />),
    row("autosave", "Autosave delay", "Wait after the last edit before writing the file.", <NumberControl value={settings.editor.autosaveDelay} min={250} max={10000} step={250} suffix="ms" onCommit={(value) => onChange("editor.autosaveDelay", value)} />),
    row("markdown", "Markdown files", "Choose the mode used when a Markdown file opens.", <SelectControl value={settings.editor.markdownDefault} onChange={(value) => onChange("editor.markdownDefault", value)} options={[["preview", "Preview"], ["edit", "Edit"]]} />),
    row("html", "HTML files", "Choose the mode used when a local HTML file opens.", <SelectControl value={settings.editor.htmlDefault} onChange={(value) => onChange("editor.htmlDefault", value)} options={[["preview", "Browse"], ["edit", "Edit"]]} />),
  ];
  if (category === "browser") return [
    row("homepage", "Home page", "Address loaded when you open a new integrated browser tab.", <TextControl value={settings.browser.homepage} placeholder={DEFAULT_APP_SETTINGS.browser.homepage} onCommit={(value) => onChange("browser.homepage", value)} />),
    row("search", "Search URL", "Use {query} where the encoded search terms belong.", <TextControl value={settings.browser.searchUrl} placeholder="https://www.google.com/search?q={query}" onCommit={(value) => onChange("browser.searchUrl", value)} />),
    row("agentBrowser", "Rem's browser", "Where Rem opens pages when its Browser tool is on. Raticode tabs open in the background, so you keep your place, and several tasks can work side by side. External browsers use Playwright and need Node.js.", <SelectControl value={settings.browser.agentBrowser} onChange={(value) => onChange("browser.agentBrowser", value)} options={[["raticode", "Raticode browser tabs"], ["external", "External browser window"], ["external-headless", "External browser, hidden"]]} />),
    row("remSignInSites", "Sites where Rem uses your sign-ins", "Rem browses in its own profile and is signed out everywhere. List sites, such as github.com, whose sign-ins Rem may use. Rem asks before submitting or running scripts on them.", <TextControl value={settings.browser.remSignInSites.join(", ")} placeholder="None" onCommit={(value) => onChange("browser.remSignInSites", value)} />),
    row("remAllowedSites", "Sites Rem may open", "Leave empty to let Rem open any site. Subdomains are included.", <TextControl value={settings.browser.remAllowedSites.join(", ")} placeholder="Any site" onCommit={(value) => onChange("browser.remAllowedSites", value)} />),
    row("remBlockedSites", "Sites Rem may not open", "Always blocked, even when listed above.", <TextControl value={settings.browser.remBlockedSites.join(", ")} placeholder="None" onCommit={(value) => onChange("browser.remBlockedSites", value)} />),
    row("remConfirm", "Approve Rem's browser actions", "Whether Rem must ask before it submits forms, presses buttons such as Send or Delete, uploads files or runs page scripts. Full access lets Rem act without approval prompts.", <SelectControl value={settings.browser.remConfirm} onChange={(value) => onChange("browser.remConfirm", value)} options={[["signed-in", "Ask on signed-in and shared tabs"], ["always", "Ask on every site"], ["never", "Full access, never ask"]]} />),
    row("remEvaluate", "Let Rem run page scripts", "Allows browser_evaluate. Turn off to limit Rem to reading, clicking and typing.", <SwitchControl checked={settings.browser.remEvaluate} onChange={(value) => onChange("browser.remEvaluate", value)} />),
    row("remDownloads", "Downloads started by Rem", "Blocked downloads are reported to Rem instead of saved.", <SelectControl value={settings.browser.remDownloads} onChange={(value) => onChange("browser.remDownloads", value)} options={[["block", "Block"], ["allow", "Save to Downloads"]]} />),
    row("askWhereToSave", "Ask where to save each download", "Otherwise files go to your Downloads folder. Files that can run code always wait for you to keep them.", <SwitchControl checked={settings.browser.askWhereToSave} onChange={(value) => onChange("browser.askWhereToSave", value)} />),
    row("discardAfterMinutes", "Free memory from background tabs", "Tabs you have not viewed for this long pause and reload when you return. Tabs playing audio or controlled by Rem stay open. 0 keeps every tab loaded.", <NumberControl value={settings.browser.discardAfterMinutes} min={0} max={1440} step={5} suffix="min" onCommit={(value) => onChange("browser.discardAfterMinutes", value)} />),
  ];
  if (category === "terminal") return [
    row("shell", "Shell", "Use your account's login shell, or select bash or zsh for new terminals. Windows uses PowerShell.", <SelectControl value={settings.terminal.shell} onChange={value => onChange("terminal.shell", value)} options={[["system", "System login shell"], ["bash", "bash"], ["zsh", "zsh"]]} />),
    row("fontSize", "Font size", "Text size in new and open terminal sessions.", <NumberControl value={settings.terminal.fontSize} min={8} max={28} step={0.5} suffix="px" onCommit={(value) => onChange("terminal.fontSize", value)} />),
    row("lineHeight", "Line height", "Vertical spacing in terminal sessions.", <NumberControl value={settings.terminal.lineHeight} min={1} max={2} step={0.05} onCommit={(value) => onChange("terminal.lineHeight", value)} />),
    row("cursor", "Blinking cursor", "Animate the terminal cursor while it is ready for input.", <SwitchControl checked={settings.terminal.cursorBlink} onChange={(value) => onChange("terminal.cursorBlink", value)} />),
    row("scrollback", "Scrollback lines", "Maximum number of terminal lines retained in memory.", <NumberControl value={settings.terminal.scrollback} min={100} max={100000} step={100} onCommit={(value) => onChange("terminal.scrollback", value)} />),
  ];
  if (category === "assistant") return assistantRows(settings, onChange, row, providerState);
  if (category === "layout") return [
    row("workflowPane", "Project pane width", "Default and current width of the left project pane.", <NumberControl value={settings.layout.workflowPaneWidth} min={240} max={420} suffix="px" onCommit={(value) => onChange("layout.workflowPaneWidth", value)} />),
    row("assistantPane", "Rem pane width", "Default and current width of Rem's chat pane.", <NumberControl value={settings.layout.assistantPaneWidth} min={300} max={520} suffix="px" onCommit={(value) => onChange("layout.assistantPaneWidth", value)} />),
    row("bottomPanel", "Bottom panel height", "Height used for Problems, Run Timeline, Runs, and Terminal.", <NumberControl value={settings.layout.bottomPanelHeight} min={140} max={480} suffix="px" onCommit={(value) => onChange("layout.bottomPanelHeight", value)} />),
    row("inspector", "Graph inspector width", "Width of the node and workflow inspector.", <NumberControl value={settings.layout.graphInspectorWidth} min={280} max={520} suffix="px" onCommit={(value) => onChange("layout.graphInspectorWidth", value)} />),
  ];
  if (category === "plugins") return [
    row("pluginLock", "Lock plugin order", "Keep plugins in place so they cannot be dragged in the sidebar or here.", (
      <SwitchControl checked={settings.plugins.locked} onChange={(value) => onChange("plugins.locked", value)} />
    )),
    {
      searchText: `Plugins sidebar order drag reorder show hide enable disable experimental ${SIDEBAR_PLUGINS.map((plugin) => plugin.label).join(" ")}`,
      element: <PluginSettings key="plugin-list" value={settings.plugins} onChange={onChange} />,
    },
  ];
  if (category === "keybindings") return keybindingRows(settings, onChange);
  return [];
}

function assistantRows(settings, onChange, row, providerState) {
  const { capabilities, loading } = providerState;
  const availableProviders = capabilities.filter((provider) => provider.available && provider.enabled !== false);
  const provider = availableProviders.find((item) => item.id === settings.assistant.provider)
    ?? availableProviders[0];
  const models = provider?.models ?? [];
  const model = models.find((item) => item.id === settings.assistant.model)
    ?? models.find((item) => item.id === provider?.defaultModel)
    ?? models[0];
  return [
    { searchText: "Rem commit messages provider model auto commit changes template format", element: <CommitMessageSettings key="commit-messages" capabilities={capabilities} /> },
    row("defaultScope", "Default scope", "Start threads in the current directory or let Rem choose from open projects.", <SelectControl value={settings.assistant.defaultScope} onChange={(value) => onChange("assistant.defaultScope", value)} options={[["current-directory", "Current directory"], ["global", "Global"]]} />),
    row("avatar", "Show Rem avatar", "Show Rem on the welcome screen of Rem’s home.", <SwitchControl checked={settings.assistant.avatarEnabled} onChange={(value) => onChange("assistant.avatarEnabled", value)} />),
    row("autoHideThoughts", "Auto-hide thoughts", "Collapse thoughts when Rem replies. You can reopen them at any time.", <SwitchControl checked={settings.assistant.autoHideThoughts} onChange={(value) => onChange("assistant.autoHideThoughts", value)} />),
    row("avatarAnimation", "Animate Rem", "Greet you when Rem’s home opens, then blink while seated. Respects reduced motion.", <SwitchControl checked={settings.assistant.avatarAnimated} onChange={(value) => onChange("assistant.avatarAnimated", value)} />),
    row("swarmAccess", "Swarm access", "Let Rem manage project teams and runs when asked. Instructions load on demand. Changes apply to the next message.", <SwitchControl checked={settings.assistant.swarmAccessEnabled} onChange={(value) => onChange("assistant.swarmAccessEnabled", value)} />),
    row("resources", "Rem resources", "Defaults copied into new threads. Each thread can change its own selection.", <RemResources value={settings.assistant.resources} memory={settings.memory} onChange={(value) => onChange("assistant.resources", value)} />, { stacked: true }),
    row("provider", "Default provider", "Used for Rem’s next message and as the default for new tasks and threads.", (
      <SelectControl disabled={loading || !availableProviders.length} value={provider?.id ?? settings.assistant.provider} onChange={(value) => onChange("assistant.provider", value)} options={availableProviders.map((item) => [item.id, item.displayName ?? item.id])} />
    )),
    row("model", "Default model", "Used for Rem’s next message. Existing task and thread selections stay unchanged.", (
      <SelectControl disabled={!models.length} value={model?.id ?? settings.assistant.model} onChange={(value) => onChange("assistant.model", value)} options={models.map((item) => [item.id, item.displayName ?? item.id])} />
    )),
    row("effort", "Default reasoning effort", "Reasoning level for Rem’s next message and new tasks and threads.", (
      <SelectControl disabled={!model?.efforts?.length} value={settings.assistant.effort || model?.defaultEffort || ""} onChange={(value) => onChange("assistant.effort", value)} options={(model?.efforts ?? []).map((item) => [item.id, item.displayName ?? item.id])} />
    )),
  ];
}

function keybindingRows(settings, onChange) {
  return KEYBINDING_COMMANDS.map((command) => {
    const conflicts = keybindingConflictIds(settings, command.id)
      .map((commandId) => KEYBINDING_COMMANDS.find((item) => item.id === commandId)?.label)
      .filter(Boolean);
    return {
      element: (
        <SettingRow key={command.id} label={command.label} description={`${command.group} · ${scopeLabel(command.scope)}`}>
          <KeybindingControl
            binding={settings.keybindings[command.id]}
            command={command}
            conflict={conflicts.join(", ")}
            onChange={(binding) => onChange(`keybindings.${command.id}`, binding)}
          />
        </SettingRow>
      ),
      searchText: `${command.label} ${command.group} ${scopeLabel(command.scope)} ${formatKeybinding(settings.keybindings[command.id])}`,
    };
  });
}

// Stacked rows give list-like controls the full width beneath their label.
function SettingRow({ children, description, label, stacked = false }) {
  if (stacked) return (
    <div className="py-3">
      <p className="text-xs font-semibold text-ink">{label}</p>
      <p className="mt-1 max-w-[54ch] text-[10px] leading-4 text-muted">{description}</p>
      <div className="mt-3 rounded-lg border border-line px-2 py-3">{children}</div>
    </div>
  );
  return (
    <div className="flex min-h-[66px] items-center gap-5 py-3">
      <div className="min-w-0 flex-1">
        <p className="text-xs font-semibold text-ink">{label}</p>
        <p className="mt-1 max-w-[54ch] text-[10px] leading-4 text-muted">{description}</p>
      </div>
      <div className="w-52 shrink-0">{children}</div>
    </div>
  );
}

function PathControl({ onChoose, value }) {
  return (
    <button
      className="flex h-8 w-full items-center gap-2 rounded-md border border-line bg-white px-2 text-left text-xs text-ink transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
      disabled={!onChoose}
      title={value || "Application data directory"}
      type="button"
      onClick={onChoose}
    >
      <span className="min-w-0 flex-1 truncate text-muted">{value || "Desktop app only"}</span>
      <span className="shrink-0 font-semibold text-brand">Choose…</span>
    </button>
  );
}

function SelectControl({ disabled = false, onChange, options, value }) {
  return (
    <select
      className="h-8 w-full rounded-md border border-line bg-white px-2 text-xs text-ink outline-none transition focus:border-brand disabled:cursor-not-allowed disabled:opacity-50"
      disabled={disabled}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      {options.map(([optionValue, label]) => <option key={optionValue} value={optionValue}>{label}</option>)}
    </select>
  );
}

function MicrophoneControl({ onChange, value }) {
  const [devices, setDevices] = useState([]);
  const [error, setError] = useState("");
  const [level, setLevel] = useState(0);
  const [testing, setTesting] = useState(false);
  const testRef = useRef(null);

  const refreshDevices = useCallback(async () => {
    try {
      setDevices(await listAudioInputDevices());
    } catch (refreshError) {
      setError(deviceErrorMessage(refreshError));
    }
  }, []);

  const stopTest = useCallback(() => {
    const current = testRef.current;
    testRef.current = null;
    if (current) {
      window.clearInterval(current.interval);
      current.source.disconnect();
      current.stream.getTracks().forEach((track) => track.stop());
      void current.context.close();
    }
    setTesting(false);
    setLevel(0);
  }, []);

  useEffect(() => {
    void refreshDevices();
    const mediaDevices = navigator.mediaDevices;
    const handleDeviceChange = () => { void refreshDevices(); };
    mediaDevices?.addEventListener?.("devicechange", handleDeviceChange);
    return () => {
      mediaDevices?.removeEventListener?.("devicechange", handleDeviceChange);
      const current = testRef.current;
      testRef.current = null;
      if (current) {
        window.clearInterval(current.interval);
        current.source.disconnect();
        current.stream.getTracks().forEach((track) => track.stop());
        void current.context.close();
      }
    };
  }, [refreshDevices]);

  useEffect(() => {
    if (testRef.current) stopTest();
  }, [stopTest, value]);

  async function startTest() {
    const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
    if (!navigator.mediaDevices?.getUserMedia || !AudioContextConstructor) {
      setError("Microphone testing is not available in this desktop build.");
      return;
    }
    setError("");
    let stream;
    let context;
    let source;
    try {
      stream = await navigator.mediaDevices.getUserMedia(audioInputConstraints(value));
      context = new AudioContextConstructor();
      await context.resume?.();
      source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      const samples = new Uint8Array(analyser.fftSize);
      const sampleLevel = () => {
        analyser.getByteTimeDomainData(samples);
        let energy = 0;
        for (const sample of samples) {
          const amplitude = (sample - 128) / 128;
          energy += amplitude * amplitude;
        }
        setLevel(Math.min(1, Math.sqrt(energy / samples.length) * 4));
      };
      const interval = window.setInterval(sampleLevel, 80);
      testRef.current = { context, interval, source, stream };
      sampleLevel();
      setTesting(true);
      await refreshDevices();
    } catch (startError) {
      source?.disconnect();
      stream?.getTracks().forEach((track) => track.stop());
      if (context) void context.close();
      setError(deviceErrorMessage(startError));
      stopTest();
    }
  }

  const options = [
    ["default", "System default"],
    ...devices.filter((device) => device.id !== "default").map((device) => [device.id, device.label]),
  ];
  if (value !== "default" && !devices.some((device) => device.id === value)) {
    options.push([value, "Unavailable microphone"]);
  }

  return (
    <div className="space-y-2">
      <select
        aria-label="Microphone input device"
        className="h-8 w-full rounded-md border border-line bg-white px-2 text-xs text-ink outline-none transition focus:border-brand"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map(([optionValue, label]) => (
          <option key={optionValue} value={optionValue}>{label}</option>
        ))}
      </select>
      <div className="flex items-center gap-2">
        <button
          aria-label={testing ? "Stop microphone test" : "Test microphone"}
          className={`h-7 shrink-0 rounded-md border px-2 text-[10px] font-semibold transition ${
            testing
              ? "border-red-200 bg-red-50 text-red-700 hover:bg-red-100"
              : "border-line bg-white text-ink hover:bg-slate-50"
          }`}
          type="button"
          onClick={testing ? stopTest : startTest}
        >
          {testing ? "Stop test" : "Test input"}
        </button>
        <div
          aria-label="Microphone input level"
          aria-valuemax={100}
          aria-valuemin={0}
          aria-valuenow={Math.round(level * 100)}
          className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-slate-100"
          role="meter"
        >
          <span
            aria-hidden="true"
            className={`block h-full origin-left rounded-full transition-transform duration-75 ${
              level > 0.03 ? "bg-emerald-500" : "bg-slate-300"
            }`}
            style={{ transform: `scaleX(${Math.max(0.015, level)})` }}
          />
        </div>
      </div>
      {error ? <p className="text-[10px] leading-4 text-red-600" role="alert">{error}</p> : null}
      {testing ? (
        <p aria-live="polite" className="text-[10px] text-muted">
          {level > 0.03 ? "Input detected" : "Listening for input…"}
        </p>
      ) : null}
    </div>
  );
}

function deviceErrorMessage(error) {
  if (error?.name === "NotAllowedError" || error?.name === "SecurityError") {
    return "Microphone access was denied. Allow access in the operating system and try again.";
  }
  if (error?.name === "NotFoundError") return "No microphone is connected.";
  if (error?.name === "OverconstrainedError") {
    return "The selected microphone is unavailable. Choose another input.";
  }
  return error instanceof Error ? error.message : "The microphone could not be opened.";
}

function SwitchControl({ checked, label, onChange }) {
  return (
    <button
      aria-checked={checked}
      aria-label={label}
      className="ml-auto grid h-7 w-10 place-items-center rounded-md outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
      role="switch"
      type="button"
      onClick={() => onChange(!checked)}
    >
      <span
        aria-hidden="true"
        className={`relative h-[18px] w-8 rounded-full border transition-[background-color,border-color] duration-150 ${
          checked ? "border-brand bg-brand" : "border-line bg-slate-100"
        }`}
      >
        <span
          className={`absolute left-0.5 top-0.5 h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-150 ease-out motion-reduce:transition-none ${
            checked ? "translate-x-3.5" : "translate-x-0"
          }`}
        />
      </span>
    </button>
  );
}

function PluginSettings({ onChange, value }) {
  const [drag, setDrag] = useState(null);
  const listRef = useRef(null);
  const plugins = orderedPlugins(value);
  const order = plugins.map((plugin) => plugin.id);
  const locked = value.locked;
  const customized = Object.keys(value.enabled).length > 0
    || order.some((id, index) => id !== DEFAULT_PLUGIN_SETTINGS.order[index]);

  function reorder(next) {
    if (next.some((id, index) => id !== order[index])) onChange("plugins.order", next);
  }

  return (
    <div className="py-3">
      <div className="flex items-start gap-5">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-ink">Sidebar plugins</p>
          <p className="mt-1 max-w-[54ch] text-[10px] leading-4 text-muted">
            Choose which plugins appear in the left sidebar and drag them into order. File explorer is always shown. Experimental plugins are off until you turn them on.
          </p>
        </div>
        <button
          className="h-7 shrink-0 rounded-md border border-line bg-white px-2 text-[10px] font-semibold text-ink transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
          disabled={!customized}
          type="button"
          onClick={() => {
            onChange("plugins.order", DEFAULT_PLUGIN_SETTINGS.order);
            onChange("plugins.enabled", {});
          }}
        >Restore defaults</button>
      </div>
      <ol ref={listRef} aria-label="Sidebar plugins" className="mt-3 divide-y divide-line rounded-lg border border-line">
        {plugins.map((plugin) => {
          const Icon = PLUGIN_ICONS[plugin.id];
          const enabled = pluginEnabled(value, plugin.id);
          const dropTarget = drag && drag.id !== plugin.id && drag.targetId === plugin.id ? drag.placement : "";
          return (
            <li
              key={plugin.id}
              draggable={!locked}
              className={`relative flex items-center gap-2.5 px-2 py-2 ${drag?.id === plugin.id ? "opacity-40" : ""}`}
              onDragStart={(event) => {
                if (locked) { event.preventDefault(); return; }
                event.dataTransfer?.setData(PLUGIN_DRAG_TYPE, plugin.id);
                if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
                setDrag({ id: plugin.id, targetId: "", placement: "before" });
              }}
              onDragOver={(event) => {
                if (!drag) return;
                event.preventDefault();
                if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
                const placement = dropPlacement(event);
                setDrag((current) => current && (current.targetId !== plugin.id || current.placement !== placement) ? { ...current, targetId: plugin.id, placement } : current);
              }}
              onDrop={(event) => {
                if (!drag) return;
                event.preventDefault();
                reorder(movePlugin(order, drag.id, plugin.id, dropPlacement(event)));
                setDrag(null);
              }}
              onDragEnd={() => setDrag(null)}
            >
              {dropTarget ? <span aria-hidden="true" className={`pointer-events-none absolute inset-x-2 h-0.5 rounded-full bg-brand ${dropTarget === "after" ? "-bottom-px" : "-top-px"}`} /> : null}
              <button
                aria-label={`Move ${plugin.label}`}
                className={`grid h-7 w-5 shrink-0 place-items-center rounded text-muted outline-none transition focus-visible:ring-2 focus-visible:ring-brand/40 disabled:opacity-30 ${locked ? "cursor-not-allowed" : "cursor-grab hover:text-ink active:cursor-grabbing"}`}
                disabled={locked}
                title={locked ? "Unlock plugin order to move plugins" : "Drag to reorder, or use the arrow keys"}
                type="button"
                onKeyDown={(event) => {
                  if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
                  event.preventDefault();
                  reorder(stepPlugin(order, plugin.id, event.key === "ArrowUp" ? -1 : 1));
                  window.setTimeout(() => listRef.current?.querySelector?.(`[aria-label="Move ${plugin.label}"]`)?.focus(), 0);
                }}
              ><GripVertical aria-hidden="true" size={14} /></button>
              <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-md border border-line ${enabled ? "text-ink" : "text-muted"}`}>
                <Icon aria-hidden="true" size={15} strokeWidth={1.5} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-1.5 text-xs font-semibold text-ink">
                  {plugin.label}
                  {plugin.experimental ? <span className="rounded-full bg-amber-50 px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide text-amber-700">Experimental</span> : null}
                </p>
                <p className="mt-0.5 truncate text-[10px] leading-4 text-muted" title={plugin.description}>{plugin.description}</p>
              </div>
              {plugin.required ? (
                <span className="shrink-0 pr-1 text-[10px] text-muted">Always shown</span>
              ) : (
                <SwitchControl
                  checked={enabled}
                  label={`Show ${plugin.label}`}
                  onChange={(next) => onChange("plugins.enabled", withPluginEnabled(value, plugin.id, next))}
                />
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function NumberControl({ max, min, onCommit, step = 1, suffix = "", value }) {
  const [draft, setDraft] = useState(String(value));
  const focusedRef = useRef(false);
  useEffect(() => {
    if (!focusedRef.current) setDraft(String(value));
  }, [value]);
  function commit() {
    focusedRef.current = false;
    const parsed = Number(draft);
    if (!Number.isFinite(parsed)) {
      setDraft(String(value));
      return;
    }
    const next = Math.min(max, Math.max(min, parsed));
    setDraft(String(next));
    onCommit(next);
  }
  return (
    <div className="relative">
      <input
        className="h-8 w-full rounded-md border border-line bg-white px-2 pr-10 text-right text-xs text-ink outline-none transition focus:border-brand"
        inputMode="decimal"
        value={draft}
        onBlur={commit}
        onChange={(event) => setDraft(event.target.value)}
        onFocus={() => { focusedRef.current = true; }}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            setDraft(String(value));
            event.currentTarget.blur();
          }
          if (event.key === "ArrowUp" || event.key === "ArrowDown") {
            event.preventDefault();
            const parsed = Number(draft);
            const base = Number.isFinite(parsed) ? parsed : value;
            setDraft(String(base + (event.key === "ArrowUp" ? step : -step)));
          }
        }}
      />
      {suffix ? <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-muted">{suffix}</span> : null}
    </div>
  );
}

function TextControl({ onCommit, placeholder, value }) {
  const [draft, setDraft] = useState(value);
  const focusedRef = useRef(false);
  useEffect(() => {
    if (!focusedRef.current) setDraft(value);
  }, [value]);
  return (
    <input
      className="h-8 w-full rounded-md border border-line bg-white px-2 text-xs text-ink outline-none transition placeholder:text-muted focus:border-brand"
      placeholder={placeholder}
      spellCheck={false}
      value={draft}
      onBlur={() => {
        focusedRef.current = false;
        onCommit(draft);
      }}
      onChange={(event) => setDraft(event.target.value)}
      onFocus={() => { focusedRef.current = true; }}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
        if (event.key === "Escape") {
          setDraft(value);
          event.currentTarget.blur();
        }
      }}
    />
  );
}

function KeybindingControl({ binding, command, conflict = "", onChange }) {
  const [recording, setRecording] = useState(false);
  const [pendingChord, setPendingChord] = useState("");
  const chordTimeoutRef = useRef(null);

  function stopRecording() {
    window.clearTimeout(chordTimeoutRef.current);
    chordTimeoutRef.current = null;
    setPendingChord("");
    setRecording(false);
  }

  function commit(next) {
    onChange(next);
    stopRecording();
  }

  return (
    <div>
      <div className="flex items-center justify-end gap-1">
        <button
          aria-label={`${recording ? "Recording" : "Change"} ${command.label} keybinding`}
          className={`min-w-28 rounded-md border px-2 py-1.5 text-right font-mono text-[10px] transition ${
            recording
              ? "border-brand bg-indigo-50 text-indigo-700"
              : conflict
                ? "border-red-300 bg-red-50 text-red-700"
                : "border-line bg-white text-ink hover:bg-slate-50"
          }`}
          title={conflict ? `Also assigned to ${conflict}` : undefined}
          type="button"
          onBlur={stopRecording}
          onClick={() => setRecording(true)}
          onKeyDown={(event) => {
            if (!recording) return;
            event.preventDefault();
            event.stopPropagation();
            if (event.key === "Escape") {
              stopRecording();
              return;
            }
            if (
              (event.key === "Backspace" || event.key === "Delete")
              && !event.ctrlKey && !event.metaKey && !event.altKey && !pendingChord
            ) {
              commit("");
              return;
            }
            const next = eventToKeybinding(event);
            if (!next) return;
            if (!CHORD_COMMAND_IDS.has(command.id)) { commit(next); return; }
            window.clearTimeout(chordTimeoutRef.current);
            if (pendingChord) {
              commit(`${pendingChord} ${next}`);
              return;
            }
            setPendingChord(next);
            chordTimeoutRef.current = window.setTimeout(() => commit(next), 900);
          }}
        >
          {recording
            ? (pendingChord ? `${formatKeybinding(pendingChord)}, then…` : "Press keys…")
            : formatKeybinding(binding)}
        </button>
        <button
          aria-label={`Reset ${command.label} keybinding`}
          className="grid h-7 w-7 place-items-center rounded-md text-muted transition hover:bg-slate-100 hover:text-ink disabled:opacity-30"
          disabled={binding === command.defaultBinding}
          title="Reset keybinding"
          type="button"
          onClick={() => onChange(command.defaultBinding)}
        ><RotateCcw aria-hidden="true" size={12} /></button>
      </div>
      {conflict ? <p className="mt-1 text-right text-[10px] text-red-600">Conflicts with {conflict}</p> : null}
    </div>
  );
}

function EmptySettingsSearch({ query }) {
  return (
    <div className="grid min-h-64 place-items-center text-center">
      <div>
        <p className="text-sm font-semibold">No settings found</p>
        <p className="mt-1 text-xs text-muted">Nothing matches “{query}”.</p>
      </div>
    </div>
  );
}

export function settingsCategoriesForQuery(query) {
  const normalized = String(query ?? "").trim().toLowerCase();
  if (!normalized) return CATEGORIES.map((category) => category.id);
  return CATEGORIES.filter((category) => {
    if (category.label.toLowerCase().includes(normalized)) return true;
    if (category.id === "keybindings") {
      return KEYBINDING_COMMANDS.some((command) => `${command.label} ${command.group}`.toLowerCase().includes(normalized));
    }
    return categoryKeywords(category.id).includes(normalized);
  }).map((category) => category.id);
}

function categoryKeywords(category) {
  return {
    general: "startup default workspace run target update queue local data directory storage artifacts autosave files archived threads delete cleanup retention",
    devices: "microphone mic audio input device recording transcription test signal level paired devices phone mobile desktop pairing qr trust revoke",
    appearance: "theme dark light system motion animation",
    editor: "font line tab wrap minimap autosave markdown html preview code",
    browser: "homepage new tab search engine url web rem agent playwright chrome automation testing sign-in cookies sites allowlist blocklist downloads memory approval approve prompts full access permission scripts",
    terminal: "font line cursor blink scrollback shell",
    providers: "providers enabled disabled executable coding harness path apps default model reasoning effort",
    usage: "usage tokens allowance limits remaining quotas credits billing spend accounts providers history refresh",
    assistant: "commit messages auto commit template format default scope current directory global projects rem provider model effort codex claude conversation skills tools mcp servers resources swarm access teams runs",
    memory: "rem memory conversation archive folder second brain knowledge notes reports html markdown",
    developer: "developer diagnostics logs logging app data storage version backend restart tools",
    layout: "width pane sidebar panel inspector",
    plugins: "plugins sidebar rail activity bar icons order reorder drag lock unlock show hide enable disable experimental",
  }[category] ?? "";
}

function scopeLabel(scope) {
  return scope === "global" ? "Works anywhere" : `When ${scope} is focused`;
}

export { defaultSettingsSnapshot } from "../lib/settings.js";
