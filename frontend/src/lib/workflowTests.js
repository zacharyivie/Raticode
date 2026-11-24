import { apiUrl } from "./api.js";

// Test sets for Rattish workflows: the Studio Tests panel's API calls and the pure
// helpers behind its case editor. The backend owns validation; these helpers only
// turn the editor's text fields into a case and back.

export const CHECK_TYPES = [
  ["text", "Text match"],
  ["json", "JSON field"],
  ["command", "Command exit code"],
  ["judge", "Model judgment"],
];
export const TEXT_OPERATORS = [
  ["contains", "contains"],
  ["equals", "is exactly"],
  ["notContains", "doesn't contain"],
  ["matches", "matches regex"],
];
export const JSON_OPERATORS = [
  ["equals", "equals"],
  ["notEquals", "is not"],
  ["exists", "exists"],
  ["missing", "is missing"],
  ["oneOf", "is one of"],
];
export const EXPECT_STATUS = [
  ["passed", "Run passes"],
  ["failed", "Run fails"],
  ["any", "Either"],
];

async function send(path, options) {
  const response = await fetch(apiUrl(path), options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

function post(path, body) {
  return send(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

const base = (workflowId) => `/workflows/${encodeURIComponent(workflowId)}/tests`;

export const workflowTestsApi = {
  load: (workflowId) => send(base(workflowId)),
  save: (workflowId, testSet) => post(base(workflowId), { testSet }),
  run: (workflowId, options = {}) => post(`${base(workflowId)}/run`, options),
  result: (workflowId, resultId) => send(`${base(workflowId)}/results/${encodeURIComponent(resultId)}`),
  stop: (workflowId, resultId) => post(`${base(workflowId)}/results/${encodeURIComponent(resultId)}/stop`),
  estimate: (workflowId, options = {}) => post(`${base(workflowId)}/estimate`, options),
};

// Provider capabilities use runtime IDs (claude_code); Rattish sources use claude-code.
export function rattishProviderId(capabilityId) {
  return String(capabilityId || "").replaceAll("_", "-");
}

// "write: codex gpt-5.5" for each tried node, joined.
export function variantLabel(variant) {
  return (variant?.models ?? []).map((item) => `${item.node}: ${item.provider} ${item.model}`).join(", ");
}

// The graph editor's edits that put each tried model on its node, as Use this model saves them.
export function useModelMutations(models) {
  return (models ?? []).flatMap((item) => [
    { kind: "set_field", target: { node: item.node }, field: "provider", valueSource: item.provider },
    { kind: "set_field", target: { node: item.node }, field: "model", value: item.model },
  ]);
}

export function formatSeconds(durationMs) {
  if (durationMs == null) return "";
  return `${(durationMs / 1000).toFixed(1)}s`;
}

export function formatUsd(value) {
  if (value == null || Number.isNaN(value)) return "unknown";
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

// The short cost shown on the Run button: "~$0.12", "$0", "cost unknown".
export function costLabel(totals) {
  if (!totals) return "";
  if (totals.modelCalls === 0) return "$0";
  if (totals.costComplete && totals.costUsd != null) return `~${formatUsd(totals.costUsd)}`;
  if (totals.costUsd) return `≥${formatUsd(totals.costUsd)}`;
  return "cost unknown";
}

export function shortRevision(revision) {
  return String(revision || "").replace(/^sha256:/, "").slice(0, 12) || "unknown";
}

export const CASE_STATUS_LABELS = {
  passed: "Passed",
  failed: "Failed",
  error: "Could not run",
  skipped: "Skipped",
  pending: "Waiting",
  running: "Running",
};

export const CHANGE_LABELS = {
  broke: "Broke",
  fixed: "Fixed",
  changed: "Changed",
  new: "New",
  removed: "Removed",
};

export function comparisonSentence(comparison) {
  if (!comparison) return "";
  const summary = comparison.summary || {};
  const parts = ["broke", "fixed", "changed", "new", "removed"]
    .filter((key) => summary[key])
    .map((key) => `${summary[key]} ${key}`);
  return `Compared with revision ${shortRevision(comparison.previousRevision)}: ${parts.join(", ") || "no case changed"}.`;
}

export function caseChanges(comparison) {
  return Object.fromEntries((comparison?.cases ?? []).map((item) => [item.id, item.change]));
}

export function checkSummary(check) {
  if (check.label) return check.label;
  const target = check.target || "outputs";
  if (check.type === "text") {
    const operator = TEXT_OPERATORS.find(([key]) => key in check);
    return operator ? `${target} ${operator[1]} "${check[operator[0]]}"` : target;
  }
  if (check.type === "json") {
    if ("exists" in check) return `${target} ${check.exists ? "exists" : "is missing"}`;
    const operator = JSON_OPERATORS.find(([key]) => key in check);
    return operator ? `${target} ${operator[1]} ${JSON.stringify(check[operator[0]])}` : target;
  }
  if (check.type === "command") return `\`${check.command}\` exits ${check.exitCode ?? 0}`;
  return `Model judges ${target}: ${check.criteria}`;
}

function jsonText(value) {
  return value === undefined ? "" : JSON.stringify(value);
}

// A command check's shell, like the shell field of bash-command steps.
export const SHELL_CHOICES = [
  ["auto", "Auto"],
  ["bash", "Bash"],
  ["powershell", "PowerShell"],
];

// One check as editable strings. Values that are JSON stay text until save.
export function draftFromCheck(check = { type: "text", target: "outputs", contains: "" }) {
  const draft = {
    type: check.type,
    label: check.label ?? "",
    target: check.target ?? "outputs",
    operator: "",
    value: "",
    ignoreCase: Boolean(check.ignoreCase),
    command: check.command ?? "",
    exitCode: String(check.exitCode ?? 0),
    timeoutSeconds: check.timeoutSeconds == null ? "" : String(check.timeoutSeconds),
    shell: SHELL_CHOICES.some(([value]) => value === check.shell) ? check.shell : "auto",
    criteria: check.criteria ?? "",
    provider: check.provider ?? "",
    model: check.model ?? "",
  };
  if (check.type === "text") {
    const operator = TEXT_OPERATORS.find(([key]) => key in check)?.[0] ?? "contains";
    draft.operator = operator;
    draft.value = String(check[operator] ?? "");
  } else if (check.type === "json") {
    if ("exists" in check) {
      draft.operator = check.exists ? "exists" : "missing";
    } else {
      const operator = JSON_OPERATORS.find(([key]) => key in check)?.[0] ?? "equals";
      draft.operator = operator;
      draft.value = jsonText(check[operator]);
    }
  }
  return draft;
}

export function newCheckDraft(type) {
  const draft = draftFromCheck({ type, target: "outputs" });
  if (type === "text") draft.operator = "contains";
  if (type === "json") draft.operator = "equals";
  return draft;
}

function checkFromDraft(draft, index) {
  const where = `Check ${index + 1}`;
  const check = { type: draft.type };
  if (draft.label.trim()) check.label = draft.label.trim();
  if (draft.target.trim() && draft.target.trim() !== "outputs") check.target = draft.target.trim();
  if (draft.type === "text") {
    check[draft.operator] = draft.value;
    if (draft.ignoreCase) check.ignoreCase = true;
    return { check };
  }
  if (draft.type === "json") {
    if (draft.operator === "exists" || draft.operator === "missing") {
      check.exists = draft.operator === "exists";
      return { check };
    }
    let value;
    try {
      value = JSON.parse(draft.value);
    } catch {
      return { error: `${where}: the value must be JSON, like "ok", 3, true or ["a", "b"].` };
    }
    if (draft.operator === "oneOf" && !Array.isArray(value)) {
      return { error: `${where}: "is one of" needs a JSON list, like ["a", "b"].` };
    }
    check[draft.operator] = value;
    return { check };
  }
  if (draft.type === "command") {
    if (!draft.command.trim()) return { error: `${where}: enter a command.` };
    check.command = draft.command;
    const exitCode = Number(draft.exitCode || 0);
    if (!Number.isInteger(exitCode) || exitCode < 0 || exitCode > 255) {
      return { error: `${where}: the exit code must be a whole number from 0 to 255.` };
    }
    if (exitCode !== 0) check.exitCode = exitCode;
    if (draft.timeoutSeconds.trim()) {
      const timeout = Number(draft.timeoutSeconds);
      if (!(timeout > 0)) return { error: `${where}: the timeout must be a positive number of seconds.` };
      check.timeoutSeconds = timeout;
    }
    if (draft.shell && draft.shell !== "auto") check.shell = draft.shell;
    return { check };
  }
  if (!draft.criteria.trim()) return { error: `${where}: describe what the model should check.` };
  check.criteria = draft.criteria.trim();
  if (draft.provider.trim()) check.provider = draft.provider.trim();
  if (draft.model.trim()) check.model = draft.model.trim();
  return { check };
}

export function draftFromCase(testCase) {
  return {
    id: testCase?.id ?? "",
    name: testCase?.name ?? "",
    inputs: JSON.stringify(testCase?.inputs ?? {}, null, 2),
    expectStatus: testCase?.expectStatus ?? "passed",
    checks: (testCase?.checks ?? []).map((check) => draftFromCheck(check)),
    // The form has no field for trigger events; keep the file's list as it is.
    triggerEvents: Array.isArray(testCase?.triggerEvents) ? testCase.triggerEvents : undefined,
  };
}

// Turns an edited case back into test-set JSON, or explains the first problem.
export function caseFromDraft(draft, otherIds = []) {
  const id = draft.id.trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(id)) {
    return { error: "The case ID must start with a letter or digit and use only letters, digits, dots, dashes and underscores." };
  }
  if (otherIds.includes(id)) return { error: `Another case already uses the ID ${id}.` };
  let inputs;
  try {
    inputs = draft.inputs.trim() ? JSON.parse(draft.inputs) : {};
  } catch {
    return { error: "Inputs must be a JSON object, like {\"topic\": \"cats\"}." };
  }
  if (!inputs || typeof inputs !== "object" || Array.isArray(inputs)) {
    return { error: "Inputs must be a JSON object, like {\"topic\": \"cats\"}." };
  }
  const testCase = { id };
  if (draft.name.trim()) testCase.name = draft.name.trim();
  testCase.inputs = inputs;
  if (Array.isArray(draft.triggerEvents)) testCase.triggerEvents = draft.triggerEvents;
  if (draft.expectStatus !== "passed") testCase.expectStatus = draft.expectStatus;
  const checks = [];
  for (const [index, checkDraft] of draft.checks.entries()) {
    const converted = checkFromDraft(checkDraft, index);
    if (converted.error) return { error: converted.error };
    checks.push(converted.check);
  }
  testCase.checks = checks;
  return { case: testCase };
}

export function nextCaseId(cases) {
  const taken = new Set((cases ?? []).map((item) => item.id));
  for (let index = (cases?.length ?? 0) + 1; ; index += 1) {
    const id = `case-${index}`;
    if (!taken.has(id)) return id;
  }
}

// Starting inputs for a new case: each declared workflow input with its default or "".
export function starterInputs(previousCase) {
  return previousCase?.inputs ? { ...previousCase.inputs } : {};
}

export function isRunning(result) {
  return result?.status === "running";
}
