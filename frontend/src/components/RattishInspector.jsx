// The Rattish inspector: the side panel that edits the selected node, route or the
// workflow's settings, with the fields only it uses.
import { createContext, useContext, useEffect, useId, useRef, useState } from "react";
import { AlertCircle, Braces, ChevronDown, ChevronRight, Route, Settings2, X } from "lucide-react";

import { apiUrl } from "../lib/api.js";
import { nodeCatalogEntries } from "../lib/nodeCatalog.js";
import { profileSelectOptions, profileSubscription } from "../lib/profileOptions.js";
import { rattishFieldEditorKind } from "../lib/rattishFieldEditors.js";
import { rattishRouteValue, sourceTextForSpan } from "../lib/rattishSource.js";
import { useRemLibrary } from "../lib/remLibrary.js";
import {
  InspectorDraftScope,
  InspectorSection,
  JsonBodyField,
  ListField,
  NumberField,
  SelectField,
  TextareaField,
  TextField,
  ToggleField,
} from "./InspectorFields.jsx";
import { NodePicker } from "./NodePicker.jsx";
import { RattishRowsField } from "./RattishRowFields.jsx";
import { RattishTriggersSection } from "./RattishTriggersSection.jsx";
import { RawRunData, rattishRunTextFields, ScrollableFieldViewer } from "./RunOutputViews.jsx";
import { WorkflowResultCacheSection } from "./WorkflowResultCacheSection.jsx";
import { WorkflowSecretsSection } from "./WorkflowSecretsSection.jsx";

// The provider accounts list (sign-in states) for the inspectors' Profile menus.
export const ProviderAccountsContext = createContext({ accounts: [], providers: [] });

export function handleInspectorTabKeyDown(event, onChange) {
  const tabs = [...event.currentTarget.parentNode.childNodes].filter(
    (candidate) => candidate.getAttribute?.("role") === "tab",
  );
  const currentIndex = tabs.indexOf(event.currentTarget);
  let nextIndex;

  if (["ArrowRight", "ArrowDown"].includes(event.key)) {
    nextIndex = (currentIndex + 1) % tabs.length;
  } else if (["ArrowLeft", "ArrowUp"].includes(event.key)) {
    nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
  } else if (event.key === "Home") {
    nextIndex = 0;
  } else if (event.key === "End") {
    nextIndex = tabs.length - 1;
  } else {
    return;
  }

  event.preventDefault();
  const nextTab = tabs[nextIndex];
  onChange(nextTab.getAttribute("data-inspector-tab"));
  nextTab.focus();
}

export function RattishInspector({
  collapsed,
  document,
  edge,
  node,
  nodeOutput,
  nodeOutputsTruncated = false,
  nodeRun,
  onMutate,
  onRenameNode,
  onResizeKeyDown,
  onResizeStart,
  onToggleCollapsed,
  providerProfiles = [],
  runEvents = [],
  width,
}) {
  const [nodeTab, setNodeTab] = useState("general");
  const graphNode = document?.graph?.nodes?.find((candidate) => candidate.id === node?.id);
  const graphEdge = document?.graph?.edges?.find((candidate) => candidate.id === edge?.id);
  const contract = document?.nodeContracts?.find(
    (candidate) => candidate.nodeType === graphNode?.type,
  );
  const schemaProperties = contract?.configurationSchema?.properties ?? {};
  const workflowFields = document?.workflow?.fields ?? {};
  const diagnostics = graphNode?.diagnostics ?? [];

  useEffect(() => setNodeTab("general"), [node?.id]);

  const setNodeField = (field, value, { source = false } = {}) =>
    onMutate?.([
      {
        kind: "set_field",
        target: { node: node.id },
        field,
        ...(source ? { valueSource: value } : { value }),
      },
    ]);
  const removeNodeField = (field) =>
    onMutate?.([{ kind: "remove_field", target: { node: node.id }, field }]);
  const setWorkflowField = (field, value, { source = false } = {}) =>
    onMutate?.([
      {
        kind: "set_field",
        target: "workflow",
        field,
        ...(source ? { valueSource: value } : { value }),
      },
    ]);

  return (
    <aside
      id="workflow-inspector"
      aria-label="Rattish workflow settings and node inspector"
      className="absolute bottom-0 right-0 top-0 z-40 shrink-0 overflow-visible border-l border-line bg-white shadow-panel transition-[width] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]"
      style={{ width: collapsed ? 0 : width }}
      tabIndex={-1}
    >
      {!collapsed ? (
        <div
          aria-label="Resize Rattish inspector"
          aria-orientation="vertical"
          aria-valuemax={520}
          aria-valuemin={280}
          aria-valuenow={width}
          className="absolute left-[-3px] top-0 z-20 h-full w-1.5 cursor-col-resize transition hover:bg-brand/40"
          role="separator"
          tabIndex={0}
          onKeyDown={onResizeKeyDown}
          onPointerDown={onResizeStart}
        />
      ) : null}
      <div
        className={`flex h-full min-h-0 flex-col overflow-hidden transition-opacity duration-200 ${
          collapsed ? "pointer-events-none opacity-0" : "opacity-100 delay-100"
        }`}
      >
        <header className="flex h-[52px] shrink-0 items-center justify-between border-b border-line px-3.5">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-slate-100 text-muted">
              {node ? <Braces size={14} /> : edge ? <Route size={14} /> : <Settings2 size={14} />}
            </span>
            <div className="min-w-0">
              <h2 className="truncate text-[13px] font-semibold text-ink">
                {node ? "Node inspector" : edge ? "Route inspector" : "Workflow settings"}
              </h2>
              <p className="truncate font-mono text-[10px] text-muted">
                {node?.id ?? (edge ? `${edge.from} -> ${edge.to}` : document?.workflowId)}
              </p>
            </div>
          </div>
          <button
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
            title="Hide inspector"
            type="button"
            onClick={onToggleCollapsed}
          >
            <X size={15} />
          </button>
        </header>

        {node ? (
          <>
            <nav className="grid shrink-0 grid-cols-5 gap-0.5 border-b border-line px-2 pt-2" role="tablist" aria-label="Node inspector sections">
              {[
                ["general", "General"],
                ["action", "Action"],
                ["inputs", "Inputs"],
                ["run", "Run"],
                ["routes", "Routes"],
              ].map(([id, label]) => (
                <button
                  key={id}
                  aria-selected={nodeTab === id}
                  className={`h-8 border-b-2 px-1 text-[11px] font-semibold transition ${
                    nodeTab === id
                      ? "border-indigo-600 text-ink"
                      : "border-transparent text-muted hover:text-ink"
                  }`}
                  data-inspector-tab={id}
                  role="tab"
                  tabIndex={nodeTab === id ? 0 : -1}
                  type="button"
                  onClick={() => setNodeTab(id)}
                  onKeyDown={(event) => handleInspectorTabKeyDown(event, setNodeTab)}
                >
                  {label}
                </button>
              ))}
            </nav>
            <InspectorDraftScope value={`node:${node.id}`}>
            <div className="workflow-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
              {nodeTab === "general" ? (
                <InspectorSection title="Node">
                  <TextField
                    commitOnBlur
                    label="ID"
                    value={node.id}
                    parseDraft={parseRattishIdentifierDraft}
                    onChange={(value) => onRenameNode?.(node.id, value)}
                  />
                  <NodeTypeField
                    contracts={document?.nodeContracts ?? []}
                    value={graphNode?.type ?? ""}
                    onChange={(value) =>
                      onMutate?.([
                        { kind: "change_node_type", node: node.id, node_type: value },
                      ])
                    }
                  />
                  <HealthDiagnosticList diagnostics={diagnostics} />
                </InspectorSection>
              ) : null}

              {nodeTab === "action" ? (
                <InspectorSection title={humanizeRattishField(graphNode?.type || "Action")}>
                  {Object.entries(schemaProperties).map(([field, fieldSchema]) =>
                    graphNode?.type === "rem-task" && field === "task_id" ? (
                      <RemTaskField
                        key={field}
                        description={fieldSchema?.description}
                        value={graphNode?.configuration?.[field]}
                        onChange={(value) => setNodeField("task-id", value)}
                      />
                    ) : graphNode?.type === "mcp-tool" && field === "server" ? (
                      <McpServerField
                        key={field}
                        description={fieldSchema?.description}
                        value={graphNode?.configuration?.server}
                        onChange={(value) => setNodeField("server", value)}
                      />
                    ) : graphNode?.type === "mcp-tool" && field === "tool" ? (
                      <McpToolField
                        key={field}
                        description={fieldSchema?.description}
                        configuration={graphNode?.configuration ?? {}}
                        value={graphNode?.configuration?.tool}
                        onChange={(value) => setNodeField("tool", value)}
                      />
                    ) : field === "profile" && schemaProperties.provider?.["x-rattish-role"] === "provider_id" ? (
                      <RattishProfileField
                        key={field}
                        description={fieldSchema?.description}
                        label="Profile"
                        provider={graphNode?.configuration?.provider}
                        providerProfiles={providerProfiles}
                        value={graphNode?.configuration?.profile}
                        onChange={(value) =>
                          value ? setNodeField("profile", value) : removeNodeField("profile")
                        }
                      />
                    ) : field === "fallback" && fieldSchema?.items?.properties?.profile ? (
                      <div key={field} className="space-y-3">
                        <RattishContractField
                          authored={Boolean(graphNode?.authoredFields?.fallback)}
                          field={field}
                          schema={fieldSchema}
                          value={graphNode?.configuration?.fallback}
                          onChange={(value, options) => setNodeField("fallback", value, options)}
                          onReset={() => removeNodeField("fallback")}
                        />
                        <RattishFallbackProfiles
                          entries={graphNode?.configuration?.fallback}
                          providerProfiles={providerProfiles}
                          onChange={(entries) => setNodeField("fallback", entries)}
                        />
                      </div>
                    ) : (
                    <RattishContractField
                      key={field}
                      authored={Boolean(graphNode?.authoredFields?.[field.replaceAll("_", "-")])}
                      field={field}
                      schema={fieldSchema}
                      value={graphNode?.configuration?.[field]}
                      onChange={(value, options) =>
                        setNodeField(field.replaceAll("_", "-"), value, options)
                      }
                      onReset={() => removeNodeField(field.replaceAll("_", "-"))}
                    />
                    ),
                  )}
                  {rattishNodeHelp[graphNode?.type] ? (
                    <p className="text-xs leading-5 text-muted">{rattishNodeHelp[graphNode.type]}</p>
                  ) : null}
                  {!Object.keys(schemaProperties).length ? (
                    <p className="text-xs leading-5 text-muted">
                      This node contract has no action fields.
                    </p>
                  ) : null}
                </InspectorSection>
              ) : null}

              {nodeTab === "inputs" ? (
                <>
                  <InspectorSection title="Readiness">
                    <ListField
                      label="Needs"
                      value={graphNode?.needs ?? []}
                      placeholder="node-id"
                      onChange={(nodes) =>
                        onMutate?.([{ kind: "set_needs", node: node.id, nodes }])
                      }
                    />
                    <p className="text-xs leading-5 text-muted">
                      Every listed node must complete at least once before an incoming activation can run this node.
                    </p>
                  </InspectorSection>
                  <InspectorSection title="Bindings">
                    <RattishBindingSummary bindings={graphNode?.bindings ?? []} />
                    <p className="text-xs leading-5 text-muted">
                      Rich binding editing is available in Code for now. Graph changes preserve the existing with block.
                    </p>
                  </InspectorSection>
                </>
              ) : null}

              {nodeTab === "run" ? (
                <>
                <InspectorSection title="Execution">
                  <ToggleField
                    checked={Boolean(graphNode?.execution?.allow_fail)}
                    label="Allow failure"
                    onChange={(value) => setNodeField("allow-fail", value)}
                  />
                  <ToggleField
                    checked={Boolean(graphNode?.execution?.start_declared)}
                    label="Start marker"
                    onChange={(value) => setNodeField("start", value)}
                  />
                  <SelectField
                    label="Finish"
                    value={graphNode?.execution?.finish ?? "none"}
                    options={[["none", "None"], ["pass", "Pass"], ["fail", "Fail"]]}
                    onChange={(value) => setNodeField("finish", value, { source: true })}
                  />
                  <TextField
                    commitOnBlur
                    label="Timeout"
                    placeholder="none or 30m"
                    value={rattishDurationFromMilliseconds(graphNode?.execution?.timeout_ms)}
                    onChange={(value) => setNodeField("timeout", value || "none", { source: true })}
                  />
                  <NumberField
                    label="Max runs"
                    min="1"
                    placeholder="None"
                    value={graphNode?.execution?.max_runs ?? ""}
                    onChange={(value) =>
                      setNodeField("max-runs", value ? String(value) : "none", { source: true })
                    }
                  />
                  <NumberField
                    label="Max concurrency"
                    min="1"
                    value={graphNode?.execution?.max_concurrency ?? 1}
                    onChange={(value) => setNodeField("max-concurrency", String(value || 1), { source: true })}
                  />
                  <NumberField
                    label="Retry count"
                    min="0"
                    value={graphNode?.execution?.retry_count ?? 0}
                    onChange={(value) => setNodeField("retry-count", String(value || 0), { source: true })}
                  />
                  <TextField
                    commitOnBlur
                    label="Retry delay"
                    value={rattishDurationFromMilliseconds(graphNode?.execution?.retry_delay_ms) || "1s"}
                    onChange={(value) => setNodeField("retry-delay", value || "1s", { source: true })}
                  />
                </InspectorSection>
                {nodeRun || nodeOutput ? (
                  <RattishLatestActivation
                    nodeOutput={nodeOutput}
                    nodeOutputsTruncated={nodeOutputsTruncated}
                    nodeRun={nodeRun}
                  />
                ) : null}
                {runEvents.some((event) => event.nodeId === node.id) ? (
                  <InspectorSection title="Activation history">
                    {runEvents
                      .filter((event) => event.nodeId === node.id)
                      .slice()
                      .reverse()
                      .map((event) => (
                        <RattishActivationRow
                          key={`${event.sequence}-${event.attempt}`}
                          attempt={rattishEventAttempt(event, nodeRun)}
                          event={event}
                        />
                      ))}
                  </InspectorSection>
                ) : null}
                </>
              ) : null}

              {nodeTab === "routes" ? (
                <InspectorSection title="Outgoing routes">
                  {(document?.graph?.edges ?? [])
                    .filter((candidate) => candidate.from === node.id)
                    .map((route) => (
                      <div key={route.id} className="rounded-lg border border-line bg-slate-50 p-3 text-xs">
                        <div className="font-mono font-semibold text-ink">{route.to}</div>
                        <div className="mt-1 text-muted">
                          {route.mode === "when" ? `when ${route.predicateSource}` : route.mode}
                        </div>
                      </div>
                    ))}
                  {!document?.graph?.edges?.some((candidate) => candidate.from === node.id) ? (
                    <p className="text-xs text-muted">This branch ends after the node completes.</p>
                  ) : null}
                  <p className="text-xs leading-5 text-muted">
                    Drag from a node connector to add an unconditional route. Select a route to edit its target or condition.
                  </p>
                </InspectorSection>
              ) : null}
            </div>
            </InspectorDraftScope>
          </>
        ) : edge ? (
          <InspectorDraftScope value={`edge:${edge.id}`}>
          <div className="workflow-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
            <InspectorSection title="Route">
              <TextField label="Source" value={edge.from} readOnly />
              <TextField label="Target" value={edge.to} readOnly />
              <SelectField
                label="Mode"
                value={graphEdge?.mode ?? "unconditional"}
                options={[["unconditional", "Always"], ["when", "When"], ["otherwise", "Otherwise"]]}
                onChange={(mode) =>
                  updateRattishRoute(document, graphEdge, { mode }, onMutate)
                }
              />
              {graphEdge?.mode === "when" ? (
                <TextField
                  commitOnBlur
                  label="Condition"
                  value={graphEdge.predicateSource ?? ""}
                  onChange={(predicateSource) =>
                    updateRattishRoute(document, graphEdge, { predicateSource }, onMutate)
                  }
                />
              ) : null}
              <p className="text-xs leading-5 text-muted">
                Route edits replace only the selected node&apos;s to block in workflow.rattish.
              </p>
            </InspectorSection>
          </div>
          </InspectorDraftScope>
        ) : (
          <div className="workflow-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
            <InspectorSection title="General">
              <TextField
                commitOnBlur
                label="Name"
                value={document?.workflow?.name ?? ""}
                onChange={(value) => setWorkflowField("name", value)}
              />
              <TextField label="Workflow ID" value={document?.workflowId ?? ""} readOnly />
              <TextField label="Source path" value={document?.sourcePath ?? ""} readOnly pathLink />
              <NumberField
                label="Interface version"
                min="1"
                placeholder="None"
                value={workflowFields["interface-version"]?.value ?? ""}
                onChange={(value) =>
                  value
                    ? setWorkflowField("interface-version", String(value), { source: true })
                    : onMutate?.([{ kind: "remove_field", target: "workflow", field: "interface-version" }])
                }
              />
              <TextField
                commitOnBlur
                label="Timeout"
                placeholder="none or 18h"
                value={workflowFields.timeout?.source ?? "none"}
                onChange={(value) => setWorkflowField("timeout", value || "none", { source: true })}
              />
              <NumberField
                label="Max total node runs"
                min="1"
                placeholder="None"
                value={workflowFields["max-runs"]?.value ?? ""}
                onChange={(value) =>
                  setWorkflowField("max-runs", value ? String(value) : "none", { source: true })
                }
              />
              <NumberField
                label="Max tokens per run"
                min="1"
                placeholder="None"
                value={workflowFields["max-tokens"]?.value ?? ""}
                onChange={(value) =>
                  value
                    ? setWorkflowField("max-tokens", String(value), { source: true })
                    : onMutate?.([{ kind: "remove_field", target: "workflow", field: "max-tokens" }])
                }
              />
              <TextField
                commitOnBlur
                label="Max estimated cost per run (USD)"
                placeholder="None, or 2.50"
                value={workflowFields["max-cost-usd"]?.source ?? ""}
                onChange={(value) =>
                  value.trim()
                    ? setWorkflowField("max-cost-usd", value.trim(), { source: true })
                    : onMutate?.([{ kind: "remove_field", target: "workflow", field: "max-cost-usd" }])
                }
              />
              <p className="text-xs leading-5 text-muted">
                Limits are checked after each model call ends, so a call already running can take
                a run past them. A provider that doesn&apos;t report cost ends a run that has a cost limit.
              </p>
            </InspectorSection>
            <InspectorSection title="Workspace">
              <ToggleField
                checked={workflowFields.workspace?.value === "worktree"}
                label="Run in a worktree"
                onChange={(checked) =>
                  checked
                    ? setWorkflowField("workspace", "worktree", { source: true })
                    : onMutate?.([{ kind: "remove_field", target: "workflow", field: "workspace" }])
                }
              />
              <p className="text-xs leading-5 text-muted">
                Each run gets its own git worktree of the project at its current commit, after the
                project&apos;s approved worktree setup. Raticode commits what the run changed; review
                it in the run&apos;s Changes tab, then land or discard it.
              </p>
            </InspectorSection>
            <InspectorSection title="Public interface">
              <TextareaField
                commitOnBlur
                label="Inputs"
                rows={6}
                value={sourceTextForSpan(document?.source, workflowFields.inputs?.span)}
                onChange={(value) => setWorkflowField("inputs", value || "{}", { source: true })}
              />
              <TextareaField
                commitOnBlur
                label="Outputs"
                rows={6}
                value={sourceTextForSpan(document?.source, workflowFields.outputs?.span)}
                onChange={(value) => setWorkflowField("outputs", value || "{}", { source: true })}
              />
              <p className="text-xs leading-5 text-muted">
                Output references and schemas are checked again by the Rattish compiler after every change.
              </p>
            </InspectorSection>
            <RattishTriggersSection
              active={!collapsed}
              showInputs={Boolean(workflowFields.inputs)}
              workflowId={document?.workflowId}
            />
            <WorkflowSecretsSection active={!collapsed} workflowId={document?.workflowId} />
            <WorkflowResultCacheSection active={!collapsed} workflowId={document?.workflowId} />
          </div>
        )}
      </div>
    </aside>
  );
}

// A step's Profile menu: the provider's profiles, each account with its sign-in state.
function RattishLatestActivation({ nodeOutput, nodeOutputsTruncated, nodeRun }) {
  const latestAttempt = nodeRun?.attempts?.at(-1) ?? null;
  const recorded = Boolean(nodeOutput || latestAttempt);
  const fields = nodeOutput
    ? rattishRunTextFields({
        data: nodeOutput.data,
        output: nodeOutput.output,
        error: nodeOutput.error ?? latestAttempt?.error,
      })
    : rattishRunTextFields(latestAttempt ?? { error: nodeRun?.message });
  return (
    <InspectorSection title="Latest activation">
      <div className="grid grid-cols-2 gap-2 rounded-lg border border-line bg-slate-50 p-3 text-xs">
        <span className="text-muted">Status</span>
        <span className="text-right font-semibold text-ink">{nodeRun?.status ?? (nodeOutput?.success ? "success" : "error")}</span>
        <span className="text-muted">Duration</span>
        <span className="text-right font-mono text-ink">{nodeRun?.durationMs == null ? "-" : `${nodeRun.durationMs} ms`}</span>
      </div>
      {nodeOutputsTruncated ? (
        <p className="text-xs leading-5 text-amber-700">
          This run's output was too large to show in full, so some of it was cut here.
        </p>
      ) : null}
      {fields.length ? (
        <ScrollableFieldViewer fields={fields} />
      ) : recorded ? (
        <p className="text-xs text-muted">No output recorded.</p>
      ) : null}
      {nodeOutput ? <RawRunData value={nodeOutput.data} /> : null}
    </InspectorSection>
  );
}

// The runNodes attempt a finished activation recorded, matched by run number.
// Started and retried rows have no output of their own yet.
function rattishEventAttempt(event, nodeRun) {
  if (event.runNumber == null || event.status === "started" || event.status === "retried") return null;
  return nodeRun?.attempts?.find((attempt) => attempt.runNumber === event.runNumber) ?? null;
}

function RattishActivationRow({ attempt, event }) {
  const [open, setOpen] = useState(false);
  const fields = open && attempt ? rattishRunTextFields(attempt) : [];
  const title = [
    `Activation ${event.runNumber ?? "-"}`,
    event.attempt > 1 ? `attempt ${event.attempt}` : "",
  ].filter(Boolean).join(" · ");
  const heading = (
    <>
      <span className="flex min-w-0 items-center gap-1 font-semibold text-ink">
        {attempt ? (
          open ? <ChevronDown aria-hidden="true" size={12} /> : <ChevronRight aria-hidden="true" size={12} />
        ) : null}
        {title}
      </span>
      <span className="text-muted">{event.status ?? event.outcome}</span>
    </>
  );
  return (
    <div className="rounded-lg border border-line bg-slate-50 p-3 text-xs">
      {attempt ? (
        <button
          aria-expanded={open}
          className="flex w-full items-center justify-between gap-2 text-left"
          title={open ? "Hide this activation's output" : "Show this activation's output"}
          type="button"
          onClick={() => setOpen((current) => !current)}
        >
          {heading}
        </button>
      ) : (
        <div className="flex items-center justify-between gap-2">{heading}</div>
      )}
      {event.type === "node_output_truncated" && event.message ? (
        <p className="mt-1 text-amber-800 dark:text-amber-300" data-testid="rattish-output-truncated">
          {event.message}
        </p>
      ) : null}
      <div className="mt-1 truncate font-mono text-[10px] text-muted" title={event.activationLineageId ?? ""}>
        {event.activationLineageId ?? "No lineage recorded"}
      </div>
      {open ? (
        fields.length ? <ScrollableFieldViewer fields={fields} /> : <p className="mt-2 text-muted">No output recorded.</p>
      ) : null}
    </div>
  );
}

function RattishProfileField({ description, label, onChange, provider, providerProfiles = [], value }) {
  const accountList = useContext(ProviderAccountsContext);
  const current = typeof value === "string" ? value : "";
  return (
    <SelectField
      description={description}
      label={label}
      value={current}
      options={profileSelectOptions(providerProfiles, profileSubscription(provider), {
        ...accountList,
        current,
      })}
      onChange={onChange}
    />
  );
}

// A Profile menu for each fallback entry, numbered as preflight numbers them.
function RattishFallbackProfiles({ entries, onChange, providerProfiles }) {
  if (!Array.isArray(entries)) return null;
  const withProfile = (index, profile) =>
    entries.map((entry, position) => {
      if (position !== index) return entry;
      const next = { ...entry };
      if (profile) next.profile = profile;
      else delete next.profile;
      return next;
    });
  return entries.map((entry, index) =>
    entry && typeof entry === "object" && typeof entry.provider === "string" ? (
      <RattishProfileField
        key={index}
        label={`Fallback entry ${index + 1} (${entry.provider}) profile`}
        provider={entry.provider}
        providerProfiles={providerProfiles}
        value={entry.profile}
        onChange={(profile) => onChange(withProfile(index, profile))}
      />
    ) : null,
  );
}

function RattishContractField({ authored, field, onChange, onReset, schema, value }) {
  const label = humanizeRattishField(field);
  const description = schema.description;
  const reset = authored ? (
    <button
      className="text-[11px] font-medium text-indigo-700 hover:underline"
      type="button"
      onClick={onReset}
    >
      Use default
    </button>
  ) : null;
  if (schema.enum) {
    return (
      <div className="space-y-1.5">
        <SelectField
          description={description}
          label={label}
          value={value ?? schema.enum[0]}
          options={schema.enum.map((item) => [String(item), humanizeRattishField(String(item))])}
          onChange={(next) => onChange(next, { source: true })}
        />
        {reset}
      </div>
    );
  }
  const types = Array.isArray(schema.type) ? schema.type : [schema.type];
  if (types.includes("boolean")) {
    return (
      <div className="space-y-1.5">
        <ToggleField description={description} checked={Boolean(value)} label={label} onChange={onChange} />
        {reset}
      </div>
    );
  }
  if (types.includes("integer") || types.includes("number")) {
    return (
      <div className="space-y-1.5">
        <NumberField
          description={description}
          label={label}
          min={schema.minimum}
          value={value ?? ""}
          onChange={onChange}
        />
        {reset}
      </div>
    );
  }
  const editorKind = rattishFieldEditorKind(schema);
  if (editorKind === "list" || editorKind === "map") {
    return (
      <div className="space-y-1.5">
        <RattishRowsField description={description} field={field} label={label} schema={schema} value={value} onChange={onChange} />
        {reset}
      </div>
    );
  }
  if (editorKind === "json") {
    return (
      <div className="space-y-1.5">
        <JsonBodyField description={description} label={label} value={value ?? (types.includes("array") ? [] : {})} onChange={onChange} />
        {reset}
      </div>
    );
  }
  const longText = ["body", "command", "content", "instructions", "prompt", "question", "state", "template"].some(
    (part) => field.includes(part),
  );
  return (
    <div className="space-y-1.5">
      {longText ? (
        <TextareaField
          description={description}
          commitOnBlur
          label={label}
          rows={field === "prompt" ? 6 : 4}
          value={value ?? ""}
          onChange={onChange}
        />
      ) : (
        <TextField description={description} commitOnBlur label={label} value={value ?? ""} onChange={onChange} />
      )}
      {reset}
    </div>
  );
}

// How a node behaves where its contract fields alone don't say.
// Bash and PowerShell lines shared by both shell step types.
const SHELL_BASH_HELP =
  "Bash runs bash everywhere, using Git for Windows' bash on Windows (never WSL); preflight links to its download when it's missing. Powershell runs PowerShell, or pwsh off Windows.";

const rattishNodeHelp = {
  "bash-command": `Shell auto runs PowerShell on Windows and bash elsewhere. ${SHELL_BASH_HELP}`,
  "shell-script": `Shell auto runs bash from PATH. ${SHELL_BASH_HELP}`,
  jev:
    "Asks Jev one yes/no or multiple-choice question about State and returns label and score, so routes can branch on node.ID.output.label. Bind State with with: state: in Code. The text is sent to TypeSafe. Save a key in Settings > Providers first.",
  "read-web-page":
    "Reads one web page as Markdown or plain text, with its title, description and links. Bind the address with with: url: in Code. Private and loopback addresses need network-allowlist. Render auto or always runs the page's JavaScript in an installed Chrome, Chromium or Edge, under the same address rules. The page text is untrusted: don't let a later step follow instructions in it.",
  "mcp-tool":
    "Calls one tool on a server from Rem's MCP server list, with no model. Write Arguments as a JSON object; {{name}} values come from with: bindings. Output is the tool's structured content, or its text. A local server runs in the sandbox: it writes only to Working dir and reaches only the hosts in Network. Treat the tool's answer as untrusted.",
  "rem-prompt":
    "Sends the prompt to Rem's chat and passes Rem's reply to the next steps. Unless Allow actions is on, Rem only answers: it won't start, change, remember or send anything for this workflow. Wait for tasks waits for any work Rem starts.",
  "rem-task":
    "Runs the task as its Run button in Rem does, so Rem checks its project access first. With Wait on, the next steps get the task's result, and a task that fails or is stopped fails this node. Stopping the workflow stops the run it started. Instructions apply to this run only.",
};

// Lists Rem's tasks by title so a Rem task node never needs a typed ID.
function RemTaskField({ description, onChange, value }) {
  const [tasks, setTasks] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    fetch(apiUrl("/rem/tasks"))
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error())))
      .then((payload) => {
        if (live) setTasks((payload.tasks ?? []).filter((task) => !task.deleted_at));
      })
      .catch(() => {
        if (!live) return;
        setTasks([]);
        setError("Rem's tasks couldn't be loaded. Open Rem and try again.");
      });
    return () => {
      live = false;
    };
  }, []);
  const known = (tasks ?? []).some((task) => task.id === value);
  const options = [
    ...(value && !known
      ? [[value, tasks === null ? "Loading Rem's tasks…" : "Task not found in Rem"]]
      : []),
    ...(tasks ?? []).map((task) => [
      task.id,
      `${task.title}${task.type ? ` · ${task.type}` : ""}`,
    ]),
  ];
  return (
    <div className="space-y-1.5">
      <SelectField description={description} label="Rem task" value={value ?? ""} options={options} onChange={onChange} />
      {tasks !== null && value && !known && !error ? (
        <p className="text-xs leading-5 text-red-700">
          This task was deleted or isn&apos;t in Rem. Choose another task.
        </p>
      ) : null}
      {error ? <p className="text-xs leading-5 text-muted">{error}</p> : null}
      {tasks !== null && !tasks.length && !error ? (
        <p className="text-xs leading-5 text-muted">Rem has no tasks yet. Ask Rem to create one.</p>
      ) : null}
    </div>
  );
}

function McpServerField({ description, onChange, value }) {
  const library = useRemLibrary();
  const servers = library.mcpServers ?? [];
  const known = servers.some((server) => server.name === value);
  const options = [
    ...(value && !known
      ? [[value, library.loaded ? "Not in Rem's MCP server list" : "Loading servers…"]]
      : []),
    ...servers.map((server) => [
      server.name,
      `${server.name} · ${server.type === "stdio" ? "local" : "HTTP"}`,
    ]),
  ];
  return (
    <div className="space-y-1.5">
      <SelectField description={description} label="Server" value={value ?? ""} options={options} onChange={onChange} />
      {library.loaded && value && !known ? (
        <p className="text-xs leading-5 text-red-700">
          Rem&apos;s MCP server list has no server named {value}. Choose another, or add it in
          Thread tools or with Raticode rem mcp add.
        </p>
      ) : null}
      {library.loaded && !servers.length ? (
        <p className="text-xs leading-5 text-muted">
          Rem&apos;s MCP server list is empty. Add a server in Thread tools or with Raticode rem
          mcp add.
        </p>
      ) : null}
    </div>
  );
}

// The tool list request for an mcp-tool step: the server starts with the step's own
// sandbox and network settings, as it would in a run.
export function mcpToolsQuery(configuration = {}) {
  const params = new URLSearchParams({ name: String(configuration.server ?? "") });
  const network = configuration.network;
  if (network === "any") params.append("network", "any");
  else if (Array.isArray(network)) network.forEach((host) => params.append("network", String(host)));
  if (configuration.sandbox === "off") params.set("sandbox", "off");
  return `/rem-resources/mcp-tools?${params.toString()}`;
}

export function mcpArgumentSummary(tool) {
  const schema = tool?.inputSchema ?? {};
  const required = new Set(schema.required ?? []);
  const entries = Object.entries(schema.properties ?? {});
  if (!entries.length) return "This tool takes no arguments.";
  const shown = entries.map(
    ([name, item]) => `${name}${required.has(name) ? "" : "?"} (${item?.type ?? "any"})`,
  );
  return `Arguments: ${shown.join(", ")}`;
}

function McpToolField({ configuration, description, onChange, value }) {
  const query = configuration.server ? mcpToolsQuery(configuration) : "";
  const [listing, setListing] = useState({ query: "", tools: null, error: "" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!query) return undefined;
    let live = true;
    setListing({ query, tools: null, error: "" });
    fetch(apiUrl(query))
      .then(async (response) => {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || "The server's tools couldn't be listed.");
        return payload;
      })
      .then((payload) => {
        if (live) setListing({ query, tools: payload.tools ?? [], error: "" });
      })
      .catch((error) => {
        if (live) setListing({ query, tools: null, error: error.message });
      });
    return () => {
      live = false;
    };
  }, [query, attempt]);
  const current = listing.query === query ? listing : { tools: null, error: "" };
  if (!query || current.error) {
    // Without a list the name is typed, so a server that won't start still saves.
    return (
      <div className="space-y-1.5">
        <TextField commitOnBlur description={description} label="Tool" value={value ?? ""} onChange={onChange} />
        {current.error ? (
          <p className="text-xs leading-5 text-muted">
            {current.error}{" "}
            <button
              className="font-medium text-indigo-700 hover:underline"
              type="button"
              onClick={() => setAttempt((count) => count + 1)}
            >
              Try again
            </button>
          </p>
        ) : null}
      </div>
    );
  }
  const tools = current.tools;
  const selected = (tools ?? []).find((tool) => tool.name === value);
  const options = [
    ...(value && !selected
      ? [[value, tools === null ? "Listing tools…" : "Not a tool on this server"]]
      : []),
    ...(tools ?? []).map((tool) => [tool.name, tool.title || tool.name]),
  ];
  return (
    <div className="space-y-1.5">
      <SelectField description={description} label="Tool" value={value ?? ""} options={options} onChange={onChange} />
      {selected?.description ? (
        <p className="text-xs leading-5 text-muted">{selected.description}</p>
      ) : null}
      {selected ? <p className="text-xs leading-5 text-muted">{mcpArgumentSummary(selected)}</p> : null}
    </div>
  );
}

function RattishBindingSummary({ bindings }) {
  if (!bindings.length) return <p className="text-xs text-muted">No with bindings.</p>;
  return (
    <div className="space-y-2">
      {bindings.map((binding, index) => (
        <div key={`${binding.name ?? "binding"}-${index}`} className="rounded-lg border border-line bg-slate-50 p-2.5 text-xs">
          <div className="font-mono font-semibold text-ink">{binding.name ?? `binding-${index + 1}`}</div>
          <div className="mt-1 truncate font-mono text-[11px] text-muted">
            {binding.reference?.source ?? binding.value ?? "Bound value"}
          </div>
        </div>
      ))}
    </div>
  );
}

function humanizeRattishField(value) {
  return String(value || "")
    .replaceAll("_", "-")
    .split("-")
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

function rattishDurationFromMilliseconds(value) {
  if (value === null || value === undefined || value === "") return "none";
  if (value % 3_600_000 === 0) return `${value / 3_600_000}h`;
  if (value % 60_000 === 0) return `${value / 60_000}m`;
  if (value % 1_000 === 0) return `${value / 1_000}s`;
  return `${value}ms`;
}

function parseRattishIdentifierDraft(text) {
  const value = String(text).trim();
  if (!value) return { ok: false, error: "Enter a node ID." };
  if (!/^[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*$/.test(value)) {
    return {
      ok: false,
      error: "Use letters, numbers, and single hyphens, starting with a letter.",
    };
  }
  return { ok: true, value };
}

function updateRattishRoute(document, edge, patch, onMutate) {
  if (!edge) return;
  const routes = (document?.graph?.edges ?? [])
    .filter((candidate) => candidate.from === edge.from)
    .map((candidate) =>
      rattishRouteValue(
        candidate.id === edge.id ? { ...candidate, ...patch } : candidate,
        document?.source,
      ),
    );
  return onMutate?.([{ kind: "set_routes", node: edge.from, routes }]);
}

export function HealthDiagnosticList({ diagnostics = [], onApplyFix }) {
  const visibleDiagnostics = diagnostics.filter((diagnostic) =>
    diagnostic?.severity === "error" || diagnostic?.severity === "warning",
  );
  if (!visibleDiagnostics.length) return null;
  return (
    <div className="space-y-2">
      {visibleDiagnostics.map((diagnostic, index) => {
        const error = diagnostic.severity === "error";
        return (
          <div
            key={`${diagnostic.id}-${diagnostic.subject ?? "workflow"}-${index}`}
            className={`rounded-md border px-3 py-2 text-xs leading-5 ${
              error
                ? "border-red-200 bg-red-50 text-red-700"
                : "border-amber-200 bg-amber-50 text-amber-800"
            }`}
          >
            <div className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 shrink-0" size={14} />
              <span>{diagnostic.message}</span>
            </div>
            {diagnostic.fixes?.length ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {diagnostic.fixes.map((fix) => (
                  <button
                    key={`${fix.action}-${fix.label}`}
                    className="rounded border border-current/20 bg-white/70 px-2 py-1 text-[11px] font-semibold transition hover:bg-white"
                    type="button"
                    onClick={() => onApplyFix?.(fix)}
                  >
                    {fix.label}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}


// The Rattish inspector's Type field opens the same picker as Add node.
function NodeTypeField({ contracts, onChange, value }) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef(null);
  const labelId = useId();
  const current = nodeCatalogEntries(contracts).find((entry) => entry.nodeType === value);
  function close() {
    setOpen(false);
    buttonRef.current?.focus?.();
  }
  return (
    <div className="relative block">
      <span className="text-xs font-medium text-muted" id={labelId}>
        Type
      </span>
      <button
        ref={buttonRef}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-labelledby={`${labelId} ${labelId}-value`}
        className="mt-1 flex h-10 w-full items-center gap-2 rounded-lg border border-line bg-white px-3 text-left text-sm text-ink outline-none transition hover:border-slate-300 focus-visible:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-100"
        data-node-type-field="true"
        title={current?.summary || undefined}
        type="button"
        onClick={() => setOpen((isOpen) => !isOpen)}
      >
        <span className="min-w-0 flex-1 truncate" id={`${labelId}-value`}>
          {current?.label || humanizeRattishField(value) || "Choose a type"}
        </span>
        <span className="shrink-0 font-mono text-[10px] text-muted">{value}</span>
        <ChevronDown aria-hidden="true" className="shrink-0 text-muted" size={15} />
      </button>
      {open ? (
        <div className="mt-1">
          <NodePicker
            contracts={contracts}
            sizeClass="max-h-[22rem]"
            currentType={value}
            widthClass="w-full"
            label="Change node type"
            placeholder="Search node types"
            onClose={close}
            onPick={(nodeType) => {
              close();
              if (nodeType !== value) onChange?.(nodeType);
            }}
          />
        </div>
      ) : null}
    </div>
  );
}
