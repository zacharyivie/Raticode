import { useState } from "react";
import { X } from "lucide-react";
import { Dialog } from "./Dialog.jsx";
import { initialWorkflowParameters, normalizeWorkflowFilesystemAccess } from "../lib/workflowRunSetup.js";

function formatPreviewValue(value) {
  if (value && typeof value === "object") {
    if (typeof value.path === "string") return value.path;
    if (typeof value.name === "string") return value.name;
    return JSON.stringify(value);
  }
  return String(value);
}

function formatFanOutCount(fanOut) {
  if (!fanOut || fanOut.count == null) return "unknown";
  if (fanOut.countExact === false) {
    return `at least ${fanOut.countLowerBound ?? fanOut.count}`;
  }
  return String(fanOut.count);
}

function formatTriggerContextItems(triggerContext) {
  if (!triggerContext || typeof triggerContext !== "object") return [];
  const items = [];
  if (triggerContext.schedule) {
    const schedule = triggerContext.schedule;
    items.push(
      `Schedule: ${schedule.cron_expression ?? schedule.cron ?? "configured"} timezone=${schedule.timezone ?? "local"}`,
    );
  }
  if (triggerContext.watch) {
    const watch = triggerContext.watch;
    items.push(
      `Watch: ${watch.path ?? ""} glob=${watch.glob ?? "*"} mode=${watch.mode ?? "batch"}`,
    );
  }
  if (triggerContext.runContinuously) {
    items.push("Run continuously: enabled");
  }
  if (triggerContext.provided) {
    items.push(`Provided trigger context: ${JSON.stringify(triggerContext.provided)}`);
  }
  return items;
}

const JSON_PARAMETER_TYPES = new Set(["array", "object"]);

// Text shown in a Run dialog field. List and object values are edited as JSON.
export function runParameterDraftValue(spec, value) {
  if (value === undefined || value === null) return "";
  if (JSON_PARAMETER_TYPES.has(spec?.type) && typeof value !== "string") {
    return JSON.stringify(value);
  }
  return value;
}

function schemaTypeMatches(type, value) {
  if (type === "integer") return Number.isInteger(value);
  if (type === "number") return typeof value === "number" && Number.isFinite(value);
  if (type === "array") return Array.isArray(value);
  if (type === "object") return value !== null && typeof value === "object" && !Array.isArray(value);
  if (type === "null") return value === null;
  return typeof value === type;
}

function schemaValueError(schema, value) {
  if (!schema || typeof schema !== "object") return "";
  const types = Array.isArray(schema.type) ? schema.type : schema.type ? [schema.type] : [];
  if (types.length && !types.some((type) => schemaTypeMatches(type, value))) {
    return `Expected ${types.filter((type) => type !== "null").join(" or ")}`;
  }
  if (Array.isArray(schema.enum) && !schema.enum.some((choice) => Object.is(choice, value))) {
    return "Choose a valid option";
  }
  if (typeof value === "number") {
    if (typeof schema.minimum === "number" && value < schema.minimum) {
      return `Must be at least ${schema.minimum}`;
    }
    if (typeof schema.maximum === "number" && value > schema.maximum) {
      return `Must be at most ${schema.maximum}`;
    }
  }
  if (typeof value === "string") {
    if (typeof schema.minLength === "number" && value.length < schema.minLength) {
      return `Must be at least ${schema.minLength} characters`;
    }
    if (typeof schema.maxLength === "number" && value.length > schema.maxLength) {
      return `Must be at most ${schema.maxLength} characters`;
    }
    if (typeof schema.pattern === "string" && !new RegExp(schema.pattern, "u").test(value)) {
      return "Does not match the required format";
    }
  }
  if (Array.isArray(value)) {
    if (typeof schema.minItems === "number" && value.length < schema.minItems) {
      return `Needs at least ${schema.minItems} items`;
    }
    if (typeof schema.maxItems === "number" && value.length > schema.maxItems) {
      return `Allows at most ${schema.maxItems} items`;
    }
    if (schema.items && typeof schema.items === "object") {
      const index = value.findIndex((item) => schemaValueError(schema.items, item));
      if (index >= 0) return `Item ${index + 1}: ${schemaValueError(schema.items, value[index])}`;
    }
  }
  return "";
}

function parseRunParameter(spec, value) {
  if (typeof value !== "string") return { value };
  const text = value.trim();
  if (spec.type === "integer") {
    return /^[-+]?\d+$/.test(text) ? { value: Number(text) } : { error: "Enter a whole number" };
  }
  if (spec.type === "number") {
    const parsed = Number(text);
    return text && Number.isFinite(parsed) ? { value: parsed } : { error: "Enter a number" };
  }
  if (JSON_PARAMETER_TYPES.has(spec.type)) {
    try {
      return { value: JSON.parse(text) };
    } catch {
      return {
        error: spec.type === "array" ? 'Enter a JSON list, e.g. ["a", "b"]' : "Enter a JSON object",
      };
    }
  }
  return { value };
}

// Convert Run dialog drafts to the input types declared by the workflow.
export function coerceWorkflowParameters(workflow, values) {
  const errors = {};
  const parameters = { ...values };
  for (const [name, spec] of Object.entries(workflow.inputs ?? workflow.parameters ?? {})) {
    const value = values[name];
    const empty = value === undefined || value === null || value === "";
    if (spec.required && empty) {
      errors[name] = "Required";
      continue;
    }
    if (empty) {
      // Leave typed inputs unset so the workflow default applies.
      if (spec.schema && spec.type !== "string" && spec.type !== "enum") delete parameters[name];
      continue;
    }
    if (!spec.schema) {
      // Legacy workflow parameters are converted by the backend.
      if (spec.type === "number" && Number.isNaN(Number(value))) errors[name] = "Enter a number";
      if (spec.type === "enum" && Array.isArray(spec.choices) && !spec.choices.includes(value)) {
        errors[name] = "Choose a valid option";
      }
      continue;
    }
    const parsed = parseRunParameter(spec, value);
    if (parsed.error) {
      errors[name] = parsed.error;
      continue;
    }
    parameters[name] = parsed.value;
    if (spec.type === "enum" && Array.isArray(spec.choices) && !spec.choices.includes(parsed.value)) {
      errors[name] = "Choose a valid option";
      continue;
    }
    const schemaError = schemaValueError(spec.schema, parsed.value);
    if (schemaError) errors[name] = schemaError;
  }
  return { errors, parameters };
}

export default function RunPreviewDialog({
  plan,
  workflow,
  onCancel,
  onRun,
  initialParameters = {},
  executionMode = "local",
  onExecutionModeChange = () => {},
  queueState = { runners: [] },
}) {
  const parameterSchema = workflow.inputs ?? workflow.parameters ?? {};
  const [parameters, setParameters] = useState(() => ({
    ...initialWorkflowParameters(workflow),
    ...initialParameters,
  }));
  const [parameterErrors, setParameterErrors] = useState({});
  const warnings = plan?.warnings ?? [];
  const blockingDiagnostics = plan?.blockingDiagnostics ?? [];
  const destructiveActions = plan?.destructiveActions ?? [];
  const providers = plan?.providerRequirements ?? [];
  const requiredSecrets = plan?.requiredSecrets ?? [];
  const bindings = plan?.bindings ?? [];
  const triggerItems = formatTriggerContextItems(plan?.triggerContext);
  const generations = plan?.generations ?? [];
  const filesystemAccess = workflow.filesystemAccess ?? [];

  return (
    <Dialog
      description={`Review execution details for ${workflow.name}`}
      onClose={onCancel}
      panelClassName="flex max-h-[86vh] w-full max-w-[760px] flex-col rounded-lg border border-line bg-white shadow-panel"
      title={`Run preview: ${workflow.name}`}
    >
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold">Run preview: {workflow.name}</h2>
            <p className="text-xs text-muted">{workflow.id}</p>
          </div>
          <button
            className="grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
            title="Close"
            type="button"
            onClick={onCancel}
          >
            <X size={17} />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-auto px-5 py-4">
          {blockingDiagnostics.length > 0 ? (
            <PreviewSection title="Cannot run" tone="danger" items={blockingDiagnostics} />
          ) : null}
          {destructiveActions.length > 0 ? (
            <PreviewSection title="Destructive actions" tone="danger" items={destructiveActions} />
          ) : null}
          {warnings.length > 0 ? (
            <PreviewSection title="Warnings" tone="warning" items={warnings} />
          ) : null}
          {requiredSecrets.length > 0 ? (
            <PreviewSection title="Required secrets" items={requiredSecrets} />
          ) : null}
          {triggerItems.length > 0 ? (
            <PreviewSection title="Trigger context" items={triggerItems} />
          ) : null}
          {filesystemAccess.length > 0 ? (
            <PreviewSection
              title="Filesystem access"
              items={normalizeWorkflowFilesystemAccess(filesystemAccess).map((entry) => {
                const permissions = [
                  entry.read ? "read" : null,
                  entry.write ? "write" : null,
                  entry.execute ? "execute" : null,
                ].filter(Boolean);
                return `${entry.path}: ${permissions.length > 0 ? permissions.join(", ") : "no access"}`;
              })}
            />
          ) : null}
          {Object.keys(parameterSchema).length > 0 ? (
            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase text-muted">
                Run inputs
              </h3>
              <div className="space-y-3 rounded-lg border border-line bg-slate-50 p-3">
                {Object.entries(parameterSchema).map(([name, spec]) => (
                  <RunParameterField
                    key={name}
                    name={name}
                    spec={spec}
                    value={parameters[name]}
                    error={parameterErrors[name]}
                    onChange={(value) =>
                      setParameters((current) => ({ ...current, [name]: value }))
                    }
                  />
                ))}
              </div>
            </section>
          ) : null}
          {providers.length > 0 ? (
            <PreviewSection
              title="Provider CLI requirements"
              items={providers.map((provider) => {
                const profile = provider.profile ? ` profile=${provider.profile}` : "";
                const model = provider.model ? ` model=${provider.model}` : "";
                const timeout =
                  provider.timeout !== undefined && provider.timeout !== null
                    ? ` timeout=${provider.timeout}s`
                    : "";
                const extraPaths = provider.extraPaths?.length
                  ? ` extra_paths=${provider.extraPaths.join(", ")}`
                  : "";
                const binary = provider.binary ?? "unknown";
                const availability = provider.available ? "available" : "missing";
                return `${provider.agentId}: ${provider.subscription} binary=${binary} (${availability}) cwd=${provider.workingDir}${profile}${model}${timeout}${extraPaths}`;
              })}
            />
          ) : null}
          {bindings.length > 0 ? <BindingPreviewSection bindings={bindings} /> : null}

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase text-muted">
              Execution target
            </h3>
            <div className="inline-flex rounded-lg border border-line bg-slate-50 p-1">
              <button
                className={`rounded-md px-3 py-1.5 text-sm ${
                  executionMode === "local" ? "bg-white font-semibold shadow-sm" : "text-muted"
                }`}
                type="button"
                onClick={() => onExecutionModeChange("local")}
              >
                Local
              </button>
              <button
                className={`rounded-md px-3 py-1.5 text-sm ${
                  executionMode === "remote" ? "bg-white font-semibold shadow-sm" : "text-muted"
                }`}
                type="button"
                onClick={() => onExecutionModeChange("remote")}
              >
                Remote
              </button>
            </div>
            {executionMode === "remote" ? (
              <p className="mt-2 text-xs text-muted">
                {(queueState.runners ?? []).length
                  ? `${queueState.runners.length} runner${queueState.runners.length === 1 ? "" : "s"} registered`
                  : "No runners registered yet"}
              </p>
            ) : null}
          </section>

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase text-muted">
              Execution order
            </h3>
            <div className="space-y-2">
              {generations.map((generation) => (
                <details
                  key={generation.index}
                  className="rounded-lg border border-line bg-slate-50 px-3 py-2"
                  open={generation.index === 0}
                >
                  <summary className="cursor-pointer text-sm font-semibold">
                    Generation {generation.index} · {(generation.nodes ?? []).length} node
                    {(generation.nodes ?? []).length === 1 ? "" : "s"}
                  </summary>
                  <div className="mt-2 space-y-2">
                    {(generation.nodes ?? []).map((node) => (
                      <div
                        key={node.id}
                        className="rounded-md border border-line bg-white px-3 py-2 text-sm"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="font-semibold">{node.id}</p>
                            <p className="break-words text-xs text-muted">{node.detail}</p>
                          </div>
                          <span className="shrink-0 rounded-md bg-slate-100 px-2 py-1 text-xs text-muted">
                            {node.type}
                          </span>
                        </div>
                        {(node.sideEffects ?? []).length > 0 ? (
                          <p className="mt-2 text-xs text-slate-600">
                            {(node.sideEffects ?? []).join("; ")}
                          </p>
                        ) : null}
                        {node.workingDir ? (
                          <p className="mt-2 break-words text-xs text-slate-600">
                            Working directory: {node.workingDir}
                          </p>
                        ) : null}
                        {node.fanOut ? (
                          <div className="mt-2 text-xs text-slate-600">
                            <p>
                              Fan-out {node.fanOut.sourceType}:{" "}
                              {formatFanOutCount(node.fanOut)} item
                              {node.fanOut.count === 1 && node.fanOut.countExact !== false
                                ? ""
                                : "s"}
                            </p>
                            {(node.fanOut.sampleItems ?? []).length > 0 ? (
                              <ul className="mt-1 space-y-0.5">
                                {(node.fanOut.sampleItems ?? []).map((sample, index) => (
                                  <li key={`${node.id}-sample-${index}`}>
                                    Sample: {formatPreviewValue(sample)}
                                  </li>
                                ))}
                              </ul>
                            ) : null}
                          </div>
                        ) : null}
                        {(node.bindings ?? []).length > 0 ? (
                          <div className="mt-2 text-xs text-slate-600">
                            <p className="font-medium text-ink">Runtime bindings</p>
                            <ul className="mt-1 space-y-1">
                              {(node.bindings ?? []).map((binding) => (
                                <li key={binding.id} className="break-words">
                                  <span className="font-medium">{binding.destinationField}</span>
                                  {" ← "}
                                  <code>{binding.expression}</code>
                                  {` · ${binding.status} · ${binding.resolutionPhase}`}
                                  {binding.readiness ? ` · secret ${binding.readiness}` : ""}
                                </li>
                              ))}
                            </ul>
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </details>
              ))}
            </div>
          </section>
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-5 py-4">
          <button className="btn-ghost" type="button" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="btn-primary inline-flex items-center justify-center gap-2 whitespace-nowrap"
            disabled={plan?.runnable === false}
            title={plan?.runnable === false ? "Resolve the blocking preflight errors first" : "Run workflow"}
            type="button"
            onClick={() => {
              const { errors, parameters: typedParameters } = coerceWorkflowParameters(
                workflow,
                parameters,
              );
              setParameterErrors(errors);
              if (Object.keys(errors).length === 0) {
                onRun(typedParameters);
              }
            }}
          >
            Run workflow
          </button>
        </div>
    </Dialog>
  );
}

function BindingPreviewSection({ bindings }) {
  const hasShellConsumer = bindings.some((binding) =>
    ["shell", "process-or-shell"].includes(binding.consumer),
  );
  return (
    <section>
      <div className="mb-2">
        <h3 className="text-xs font-semibold uppercase text-muted">Runtime bindings</h3>
        <p className="mt-1 text-xs text-slate-600">
          Deferred values resolve automatically at the phase shown below.
          {hasShellConsumer
            ? " Raticode resolves {{...}} first; the shell owns expressions such as ${FILE_NAME}."
            : ""}
        </p>
      </div>
      <div className="overflow-hidden rounded-lg border border-line bg-slate-50">
        <ul className="divide-y divide-slate-200">
          {bindings.map((binding) => {
            const isError = ["invalid", "type-incompatible"].includes(binding.status);
            return (
              <li key={binding.id} className="px-3 py-2.5 text-xs">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <p className="min-w-0 break-words text-sm font-medium text-ink">
                    {binding.destinationNode}.{binding.destinationField}
                  </p>
                  <span className={isError ? "font-semibold text-red-700" : "font-medium text-cyan-700"}>
                    {binding.status}
                  </span>
                </div>
                <p className="mt-1 break-words text-slate-600">
                  <code>{binding.expression}</code>
                  {` from ${binding.producer} · ${binding.sourceType} → ${binding.destinationType} · ${binding.resolutionPhase}`}
                  {binding.coercion === "string" ? " · string coercion" : ""}
                  {binding.readiness ? ` · secret ${binding.readiness}` : ""}
                </p>
                {binding.message ? <p className="mt-1 text-red-700">{binding.message}</p> : null}
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}

function RunParameterField({ error, name, onChange, spec, value }) {
  const id = `run-param-${name}`;
  const label = spec.label || name;
  const commonClass =
    "mt-1 w-full rounded-md border border-line bg-white px-3 py-2 text-sm outline-none focus:border-brand";
  const inputType =
    spec.type === "number" || spec.type === "integer"
      ? "number"
      : spec.type === "date"
        ? "date"
        : spec.type === "time"
          ? "time"
          : spec.type === "datetime"
            ? "datetime-local"
            : spec.type === "secret"
              ? "password"
              : "text";
  return (
    <label className="block text-sm" htmlFor={id}>
      <span className="font-medium">
        {label}
        {spec.required ? <span className="text-rose-600"> *</span> : null}
      </span>
      {spec.description ? (
        <span className="mt-0.5 block text-xs text-muted">{spec.description}</span>
      ) : null}
      {spec.type === "boolean" ? (
        <input
          id={id}
          className="mt-2 h-4 w-4 rounded border-line"
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
        />
      ) : spec.type === "enum" ? (
        <select
          id={id}
          className={commonClass}
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">Select...</option>
          {(spec.choices ?? []).map((choice) => (
            <option key={String(choice)} value={choice}>
              {String(choice)}
            </option>
          ))}
        </select>
      ) : spec.type === "text" || spec.type === "multiline" ? (
        <textarea
          id={id}
          className={`${commonClass} min-h-24 resize-y`}
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : spec.type === "array" || spec.type === "object" ? (
        <textarea
          id={id}
          className={`${commonClass} min-h-16 resize-y font-mono`}
          spellCheck={false}
          value={runParameterDraftValue(spec, value)}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <input
          id={id}
          className={commonClass}
          type={inputType}
          value={value ?? ""}
          min={spec.min ?? spec.schema?.minimum}
          max={spec.max ?? spec.schema?.maximum}
          step={spec.type === "integer" ? 1 : spec.type === "number" ? "any" : undefined}
          pattern={spec.pattern}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      {spec.type === "array" || spec.type === "object" ? (
        <span className="mt-1 block text-xs text-muted">
          {spec.type === "array" ? 'JSON list, e.g. ["a", "b"]' : 'JSON object, e.g. {"key": "value"}'}
        </span>
      ) : null}
      {spec.type === "file" || spec.type === "folder" ? (
        <span className="mt-1 block text-xs text-muted">
          Enter a path accessible to the runner.
        </span>
      ) : null}
      {error ? <span className="mt-1 block text-xs text-rose-700">{error}</span> : null}
    </label>
  );
}

function PreviewSection({ title, items, tone = "default" }) {
  const toneClass =
    tone === "danger"
      ? "border-red-200 bg-red-50 text-red-800"
      : tone === "warning"
        ? "border-amber-200 bg-amber-50 text-amber-800"
        : "border-line bg-slate-50 text-slate-700";

  return (
    <section className={`rounded-lg border px-3 py-2 ${toneClass}`}>
      <h3 className="text-xs font-semibold uppercase">{title}</h3>
      <ul className="mt-2 space-y-1 text-sm">
        {items.map((item) => (
          <li key={item}>- {item}</li>
        ))}
      </ul>
    </section>
  );
}
