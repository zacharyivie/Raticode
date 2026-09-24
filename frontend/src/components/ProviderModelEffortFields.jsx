import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, LoaderCircle, Sparkles } from "lucide-react";

import { apiUrl } from "../lib/api";

const activePickerStyle = {
  backgroundColor: "var(--model-picker-active-bg)",
  color: "var(--model-picker-active-fg)",
};

export function useProviderCapabilities(projectRoot = "") {
  const requestRef = useRef(0);
  const abortRef = useRef(null);
  const rootRef = useRef(projectRoot);
  rootRef.current = projectRoot;
  const [capabilities, setCapabilities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (refresh = false) => {
    const requestId = ++requestRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const current = () => requestId === requestRef.current && rootRef.current === projectRoot && !controller.signal.aborted;
    setLoading(true);
    setError("");
    try {
      const suffix = refresh ? "?refresh=1" : "";
      const response = await fetch(apiUrl(`/provider/capabilities${suffix}`), { signal: controller.signal });
      if (!response.ok) throw new Error("Could not discover provider capabilities");
      const payload = await response.json();
      if (!current()) return;
      setCapabilities(Array.isArray(payload.providers) ? payload.providers : []);
    } catch (loadError) {
      if (!current() || loadError?.name === "AbortError") return;
      setCapabilities([]);
      setError(loadError instanceof Error ? loadError.message : "Could not discover providers");
    } finally {
      if (current()) setLoading(false);
    }
  }, [projectRoot]);

  useEffect(() => {
    load();
    const reload = (event) => load(event.detail?.refresh === true);
    window.addEventListener("raticode:providers-changed", reload);
    return () => {
      abortRef.current?.abort();
      window.removeEventListener("raticode:providers-changed", reload);
    };
  }, [load]);

  return { capabilities, error, loading, refresh: () => load(true) };
}

export function ProviderModelEffortFields({
  allowInheritedModel = false,
  capabilities = [],
  className = "",
  disabled = false,
  effort,
  loading = false,
  model,
  onChange,
  onRefresh,
  provider,
  showProvider = true,
}) {
  const selectedProvider = useMemo(
    () => capabilities.find((item) => item.id === provider) ?? (provider ? { id: provider, models: [], error: `Provider ${provider} is not in the current catalog. Refresh or choose another provider.` } : capabilities[0] ?? null),
    [capabilities, provider],
  );
  const concreteDefaultModel = useMemo(
    () =>
      selectedProvider?.models?.find(
        (item) => item.id === selectedProvider.defaultModel && item.id.toLowerCase() !== "default",
      ) ?? null,
    [selectedProvider],
  );
  const antigravityFamily = provider === "antigravity" && model
    ? selectedProvider?.models?.find(item => item.id.replace(/-(low|medium|high)$/, "") === model.replace(/-(low|medium|high)$/, "")) : null;
  const nativeEffort = antigravityFamily && model !== antigravityFamily.id
    ? model.match(/-(low|medium|high)$/)?.[1] : "";
  const configuredModelValue = antigravityFamily?.id ||
    (model?.toLowerCase() === "default" && concreteDefaultModel ? concreteDefaultModel.id : model);
  const selectedModel = useMemo(
    () =>
      selectedProvider?.models?.find((item) => item.id === configuredModelValue) ??
      selectedProvider?.models?.find((item) => item.id === selectedProvider.defaultModel) ??
      selectedProvider?.models?.[0] ??
      null,
    [configuredModelValue, selectedProvider],
  );
  const modelOptions = selectableModels(
    selectedProvider,
    configuredModelValue,
    allowInheritedModel,
  );
  const effortOptions = selectableEfforts(selectedModel, effort);
  const selectedModelValue =
    configuredModelValue || (allowInheritedModel ? "" : selectedModel?.id ?? "");
  const selectedEffortValue = effort || nativeEffort || selectedModel?.defaultEffort || "";
  const deniedSelection = selectedProvider?.deniedModels?.includes(configuredModelValue);
  const discoveryMessage = deniedSelection ? "This model is denied. Choose an allowed model."
    : ["unauthenticated", "access_denied"].includes(selectedProvider?.discoveryStatus)
    ? null : selectedProvider?.error || (!selectedProvider?.models?.length ? "No models available. Refresh to try discovery again." : "");

  useEffect(() => {
    if (disabled || model?.toLowerCase() !== "default" || !concreteDefaultModel) return;
    onChange({
      model: concreteDefaultModel.id,
      effort: effort || concreteDefaultModel.defaultEffort || "",
    });
  }, [concreteDefaultModel, disabled, effort, model, onChange]);

  useEffect(() => {
    if (disabled || !antigravityFamily || model === antigravityFamily.id) return;
    onChange({ model: antigravityFamily.id, effort: effort || nativeEffort || antigravityFamily.defaultEffort || "" });
  }, [antigravityFamily, disabled, effort, model, nativeEffort, onChange]);

  function updateProvider(nextProviderId, requestedModelId = "") {
    const nextProvider = capabilities.find((item) => item.id === nextProviderId);
    const nextModel =
      nextProvider?.models?.find((item) => item.id === requestedModelId) ??
      nextProvider?.models?.find((item) => item.id === nextProvider.defaultModel) ??
      nextProvider?.models?.[0] ??
      null;
    onChange({
      provider: nextProviderId,
      model: nextModel?.id ?? "",
      effort: nextModel?.defaultEffort ?? "",
    });
  }

  function updateModel(nextModelId) {
    if (!nextModelId) {
      onChange({ model: "", effort: "" });
      return;
    }
    const nextModel = selectedProvider?.models?.find((item) => item.id === nextModelId);
    const supportedEfforts = new Set((nextModel?.efforts ?? []).map((item) => item.id));
    onChange({
      model: nextModelId,
      effort: effort && supportedEfforts.has(effort) ? effort : nextModel?.defaultEffort ?? "",
    });
  }

  if (loading) return <ProviderDiscoveryLoading className={className} />;

  return (
    <div className={`space-y-2 ${className}`.trim()}>
      <ModelPicker
        allowInheritedModel={allowInheritedModel}
        capabilities={capabilities}
        disabled={disabled}
        effortOptions={effortOptions}
        modelOptions={modelOptions}
        selectedEffortValue={selectedEffortValue}
        selectedModel={selectedModel}
        selectedModelValue={selectedModelValue}
        selectedProvider={selectedProvider}
        showProvider={showProvider}
        onEffortChange={(nextEffort) => onChange({ effort: nextEffort })}
        onModelChange={updateModel}
        onProviderChange={updateProvider}
      />
      <ProviderAuthentication key={selectedProvider?.id} provider={selectedProvider} disabled={disabled} />
      {discoveryMessage ? (
        <div className="flex items-center justify-between gap-3 rounded-[10px] border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
          <span className="min-w-0">{discoveryMessage}</span>
          {onRefresh ? (
            <button
              className="shrink-0 font-semibold text-amber-800 underline underline-offset-2 hover:text-amber-950 disabled:opacity-60 dark:text-amber-200"
              disabled={disabled}
              type="button"
              onClick={onRefresh}
            >
              Refresh
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function ProviderDiscoveryLoading({ className = "" }) {
  return <div role="status" aria-live="polite" aria-busy="true"
    className={`flex min-h-10 items-center gap-3 rounded-xl border border-line bg-white px-3 py-2 text-xs text-muted ${className}`}>
    <LoaderCircle aria-hidden="true" className="h-4 w-4 shrink-0 text-brand motion-safe:animate-spin" />
    <span>Loading providers and models…</span>
  </div>;
}


function ModelPicker({
  allowInheritedModel,
  capabilities,
  disabled,
  effortOptions,
  modelOptions,
  onEffortChange,
  onModelChange,
  onProviderChange,
  selectedEffortValue,
  selectedModel,
  selectedModelValue,
  selectedProvider,
  showProvider,
}) {
  const pickerRef = useRef(null);
  const providerTriggerRef = useRef(null);
  const modelTriggerRef = useRef(null);
  const effortTriggerRef = useRef(null);
  const [openMenu, setOpenMenu] = useState(null);

  useEffect(() => {
    if (!openMenu) return undefined;
    window.requestAnimationFrame(() => {
      pickerRef.current
        ?.querySelector(`[data-picker-menu="${openMenu}"] [role="option"]:not(:disabled)`)
        ?.focus();
    });
    function handlePointerDown(event) {
      if (!pickerRef.current?.contains(event.target)) setOpenMenu(null);
    }
    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [openMenu]);

  const displayedEffortValue = selectedEffortValue || selectedModel?.defaultEffort || "";
  const selectedEffort = effortOptions.find((item) => item.id === displayedEffortValue);
  const providerName = selectedProvider?.displayName ?? selectedProvider?.id ?? "Provider";
  const modelName = selectedProvider?.deniedModels?.includes(selectedModelValue) ? "Model denied" :
    modelOptions.find((item) => item.id === selectedModelValue)?.label ??
    selectedModel?.displayName ??
    selectedModel?.id ??
    (allowInheritedModel ? "Inherit agent" : "Models unavailable");

  function triggerRefFor(menu) {
    if (menu === "provider") return providerTriggerRef;
    if (menu === "effort") return effortTriggerRef;
    return modelTriggerRef;
  }

  function closePicker({ restoreFocus = true } = {}) {
    const closingMenu = openMenu;
    setOpenMenu(null);
    if (restoreFocus && closingMenu) {
      window.requestAnimationFrame(() => triggerRefFor(closingMenu).current?.focus());
    }
  }

  function toggleMenu(menu) {
    setOpenMenu((current) => current === menu ? null : menu);
  }

  function handlePickerKeyDown(event) {
    if (event.key === "Escape") {
      if (!openMenu) return;
      event.preventDefault();
      closePicker();
      return;
    }
    const menu = event.target.closest?.("[data-picker-trigger]")?.dataset?.pickerTrigger;
    if (menu && ["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault();
      setOpenMenu(menu);
    }
  }

  function selectValue(callback, value) {
    callback(value);
    closePicker({ restoreFocus: true });
  }

  return (
    <div ref={pickerRef} className="relative w-full min-w-0" onKeyDown={handlePickerKeyDown}>
      <div
        className={`model-picker-trigger grid h-9 w-full min-w-0 items-stretch overflow-hidden rounded-full border border-line bg-slate-50 text-xs font-semibold text-ink ${
          showProvider
            ? "grid-cols-[minmax(0,1.34fr)_minmax(0,1.57fr)_minmax(0,0.89fr)]"
            : "grid-cols-[minmax(0,1.57fr)_minmax(0,0.89fr)]"
        }`}
      >
        {showProvider ? (
          <button
            ref={providerTriggerRef}
            aria-expanded={openMenu === "provider"}
            aria-haspopup="listbox"
            className={`grid min-w-0 grid-cols-[1rem_minmax(0,1fr)_1rem] items-center gap-0.5 px-1.5 text-center text-xs font-semibold text-ink transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60 ${openMenu === "provider" ? "model-picker-option--active" : ""}`}
            data-model-picker-part="provider"
            data-picker-trigger="provider"
            disabled={disabled}
            style={openMenu === "provider" ? activePickerStyle : undefined}
            type="button"
            onClick={() => toggleMenu("provider")}
          >
            <span>
              <ProviderMark providerId={selectedProvider?.id} />
            </span>
            <span className="min-w-0 truncate" data-picker-label>{providerName}</span>
            <ChevronDown aria-hidden="true" className="justify-self-center text-muted" size={12} />
          </button>
        ) : null}
        <button
          ref={modelTriggerRef}
          aria-expanded={openMenu === "model"}
          aria-haspopup="listbox"
          className={`grid min-w-0 grid-cols-[0.625rem_minmax(0,1fr)_0.625rem] items-center gap-0.5 border-l border-line px-1.5 text-center text-xs font-semibold text-ink transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60 ${openMenu === "model" ? "model-picker-option--active" : ""}`}
          data-model-picker-part="model"
          data-picker-trigger="model"
          disabled={disabled || !modelOptions.some(option => !option.disabled)}
          style={openMenu === "model" ? activePickerStyle : undefined}
          type="button"
          onClick={() => toggleMenu("model")}
        >
          <span aria-hidden="true" />
          <span className="min-w-0 truncate" data-picker-label>{modelName}</span>
          <ChevronDown aria-hidden="true" className="justify-self-center text-muted" size={12} />
        </button>
        <button
          ref={effortTriggerRef}
          aria-expanded={openMenu === "effort"}
          aria-haspopup="listbox"
          className={`grid min-w-0 grid-cols-[0.625rem_minmax(0,1fr)_0.625rem] items-center border-l border-line px-0.5 text-center text-xs font-semibold text-ink transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60 ${openMenu === "effort" ? "model-picker-option--active" : ""}`}
          data-model-picker-part="effort"
          data-picker-trigger="effort"
          disabled={disabled}
          style={openMenu === "effort" ? activePickerStyle : undefined}
          type="button"
          onClick={() => toggleMenu("effort")}
        >
          <span aria-hidden="true" />
          <span className="min-w-0 truncate" data-picker-label>{(selectedEffort?.label ?? displayedEffortValue) || "Default"}</span>
          <ChevronDown aria-hidden="true" className="justify-self-center text-muted" size={12} />
        </button>
      </div>

      {openMenu === "provider" ? (
        <PickerMenu label="Provider" menu="provider">
          {capabilities.filter((item) => item.enabled !== false).map((providerOption) => (
            <PickerOption
              key={providerOption.id}
              active={providerOption.id === selectedProvider?.id}
              disabled={!providerOption.available}
              label={providerOption.displayName ?? providerOption.id}
              meta={providerOption.available ? "Ready" : "Unavailable"}
              onSelect={() => selectValue(onProviderChange, providerOption.id)}
            />
          ))}
          <button type="button" role="option" aria-selected={false} className="w-full border-t border-line px-3 py-2 text-left text-xs font-semibold text-brand" onClick={() => {
            closePicker({ restoreFocus: false });
            window.dispatchEvent(new CustomEvent("raticode:open-provider-settings"));
          }}>Add a provider</button>
        </PickerMenu>
      ) : null}

      {openMenu === "model" ? (
        <PickerMenu label="Model" menu="model">
          <div className="workflow-scrollbar max-h-64 overflow-y-auto p-1.5">
            {modelOptions.map((option) => (
              <PickerOption
                key={option.id || "inherit"}
              active={option.id === selectedModelValue}
              disabled={option.disabled}
              label={option.menuLabel ?? option.label}
              onSelect={() => selectValue(onModelChange, option.id)}
              />
            ))}
          </div>
        </PickerMenu>
      ) : null}

      {openMenu === "effort" ? (
        <PickerMenu label="Effort" menu="effort">
          {effortOptions.map((option) => (
            <PickerOption
              key={option.id || "default"}
              active={option.id === displayedEffortValue}
              disabled={option.disabled}
              label={option.menuLabel ?? option.label}
              onSelect={() => selectValue(onEffortChange, option.id)}
            />
          ))}
        </PickerMenu>
      ) : null}
    </div>
  );
}

function PickerMenu({ children, label, menu }) {
  return (
    <div
      aria-label={`${label} options`}
      className="model-picker-popover absolute bottom-[calc(100%+8px)] left-0 right-0 z-50 w-full min-w-0 overflow-hidden rounded-[14px] border border-line bg-white p-1.5 shadow-panel"
      data-picker-menu={menu}
      role="listbox"
    >
      {children}
    </div>
  );
}

function PickerOption({ active, disabled = false, label, meta, onSelect }) {
  return (
    <button
      aria-selected={active}
      className={`model-picker-option flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-xs transition ${
        active
          ? "model-picker-option--active font-semibold"
          : "text-ink hover:bg-slate-50"
      } disabled:cursor-not-allowed disabled:opacity-40`}
      disabled={disabled}
      role="option"
      style={active ? activePickerStyle : undefined}
      type="button"
      onClick={onSelect}
    >
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {meta ? <span className="shrink-0 text-[10px] text-muted">{meta}</span> : null}
      {active ? <Check aria-hidden="true" className="shrink-0" size={14} /> : null}
    </button>
  );
}

function ProviderMark({ providerId }) {
  const color = providerId === "claude_code" ? "bg-[#d97757]" : providerId === "codex" ? "bg-[#10a37f]" : "bg-indigo-500";
  return (
    <span className={`grid h-4 w-4 shrink-0 place-items-center rounded-[5px] text-white ${color}`}>
      <Sparkles aria-hidden="true" size={9} />
    </span>
  );
}

function selectableModels(provider, configuredModel, allowInheritedModel) {
  const options = allowInheritedModel ? [{ id: "", label: "Inherit agent" }] : [];
  const providerModels = provider?.models ?? [];
  const concreteDefaultModel = providerModels.find(
    (model) => model.id === provider?.defaultModel && model.id.toLowerCase() !== "default",
  );
  const models = providerModels.filter(
    (model) => model.id.toLowerCase() !== "default" || !concreteDefaultModel,
  );
  for (const model of models) {
    const label = model.displayName ?? model.id;
    options.push({
      id: model.id,
      label,
      menuLabel: model.id === concreteDefaultModel?.id ? `${label} (default)` : label,
    });
  }
  if (configuredModel && !provider?.deniedModels?.includes(configuredModel) && !models.some((item) => item.id === configuredModel)) {
    options.push({
      id: configuredModel,
      label: `${configuredModel} — configured, not reported by this host`,
      disabled: true,
    });
  }
  return options;
}

function selectableEfforts(model, configuredEffort) {
  const efforts = model?.efforts ?? [];
  const defaultEffort = model?.defaultEffort ?? "";
  const hasReportedDefault = Boolean(
    defaultEffort && efforts.some((effort) => effort.id === defaultEffort),
  );
  const options = hasReportedDefault ? [] : [{ id: "", label: "Default" }];
  for (const effort of efforts) {
    const label = effort.displayName ?? effort.id;
    options.push({
      id: effort.id,
      label,
      menuLabel: effort.id === defaultEffort ? `${label} (default)` : label,
    });
  }
  if (configuredEffort && !efforts.some((item) => item.id === configuredEffort)) {
    options.push({
      id: configuredEffort,
      label: `${configuredEffort} — configured, not reported by this host`,
      disabled: true,
    });
  }
  return options;
}

export function ProviderAuthentication({ provider, disabled = false, alwaysShow = false }) {
  const [pending, setPending] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => () => { generation.current += 1; }, []);
  useEffect(() => {
    if (!pending) return undefined;
    let active = true;
    let timer;
    async function poll() {
      try {
        const response = await fetch(apiUrl(`/provider/auth?provider=${encodeURIComponent(provider.id)}`));
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Could not check sign-in status.");
        if (!active) return;
        if (result.status === "complete") {
          setPending(false);
          // Every picker shares this event, including settings, Rem and agent editors.
          window.dispatchEvent(new CustomEvent("raticode:providers-changed", { detail: { refresh: true } }));
          return;
        }
        if (result.status === "error" || result.status === "idle") {
          setPending(false);
          setError(result.error || "Sign-in was cancelled.");
          return;
        }
        timer = window.setTimeout(poll, 1000);
      } catch (failure) {
        if (active) { setPending(false); setError(failure.message || "Could not check sign-in status."); }
      }
    }
    void poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [pending, provider?.id]);
  async function authenticate(action = "start") {
    const current = ++generation.current;
    setError("");
    setStarting(true);

    try {
      const response = await fetch(apiUrl("/provider/auth"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: provider.id, action }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not start sign-in.");
      if (generation.current === current) setPending(action === "start");
    } catch (failure) {
      if (generation.current === current) { setPending(false); setError(failure.message || "Could not start sign-in."); }
    } finally { if (generation.current === current) setStarting(false); }
  }
  if (provider?.discoveryStatus === "access_denied") return <div className="space-y-2 rounded-xl border border-line bg-white px-3 py-2 text-xs">
    <p className="text-muted">{provider.error || "This account does not have access to the provider's model catalog."}</p>
    <div className="flex flex-wrap items-center gap-3">
      {provider.id === "copilot" ? <a className="font-semibold text-brand" href="https://github.com/settings/copilot" target="_blank" rel="noopener noreferrer">Open Copilot settings</a> : null}
      <button type="button" disabled={disabled} className="font-semibold text-brand disabled:opacity-60" onClick={() => window.dispatchEvent(new CustomEvent("raticode:providers-changed", { detail: { refresh: true } }))}>Refresh providers</button>
    </div>
  </div>;
  if (!provider || (!alwaysShow && provider.discoveryStatus !== "unauthenticated")) return null;
  if (!provider.supportsBrowserLogin) return provider.discoveryStatus === "unauthenticated"
    ? <div className="space-y-2 text-xs text-muted"><p>{provider.id === "antigravity" ? "Run agy in a terminal to sign in to Antigravity, then refresh providers." : <>Sign in through {provider.displayName || provider.id}&apos;s CLI setup, then refresh providers.</>}</p>
      <button type="button" disabled={disabled} className="font-semibold text-brand" onClick={() => window.dispatchEvent(new CustomEvent("raticode:providers-changed", { detail: { refresh: true } }))}>Refresh providers</button></div> : null;
  return <div className="space-y-2 rounded-xl border border-line bg-white px-3 py-2 text-xs">
    {!pending && !starting && provider.discoveryStatus === "unauthenticated" && provider.error ? <p className="text-muted">{provider.error}</p> : null}
    {pending || starting ? <div role="status" className="flex items-center gap-2"><LoaderCircle aria-hidden="true" className="h-4 w-4 motion-safe:animate-spin" />{starting ? "Opening sign-in…" : "Complete sign-in in your browser."}</div> : null}
    <button type="button" disabled={disabled || starting} className="font-semibold text-brand disabled:opacity-60"
      onClick={() => authenticate(pending ? "cancel" : "start")}>
      {pending ? "Cancel sign-in" : `Sign in to ${provider.displayName || provider.id}`}
    </button>
    {error ? <p role="alert" className="text-red-600 dark:text-red-300">{error}</p> : null}
  </div>;
}
