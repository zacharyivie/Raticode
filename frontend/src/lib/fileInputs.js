// File inputs: a workflow input whose schema is {"type": "string", "format": "file"}.
// The backend lists them with type "file" and file: {accept, maxSize}; the value is a path.
// The run copies the file into its own folder when it starts, so this only checks early.

const DEFAULT_FILE_MAX_SIZE = 20 * 1024 * 1024;

function isFileInput(spec) {
  return spec?.type === "file" || (spec?.schema?.type === "string" && spec?.schema?.format === "file");
}

export function fileInputOptions(spec) {
  const accept = Array.isArray(spec?.file?.accept) ? spec.file.accept.filter((item) => typeof item === "string") : [];
  const maxSize = Number.isFinite(spec?.file?.maxSize) && spec.file.maxSize > 0 ? spec.file.maxSize : DEFAULT_FILE_MAX_SIZE;
  return { accept, maxSize };
}

export function fileInputNames(workflow) {
  return Object.entries(workflow?.inputs ?? {})
    .filter(([, spec]) => isFileInput(spec))
    .map(([name]) => name);
}

// Workflows in a project that can take a file, one entry per file input.
export function workflowsForFile(workflows, projectRoot = "") {
  const same = (left, right) => String(left || "").replace(/[\\/]+$/, "") === String(right || "").replace(/[\\/]+$/, "");
  return (workflows ?? [])
    .filter((workflow) => !projectRoot || same(workflow.projectRoot, projectRoot))
    .flatMap((workflow) => fileInputNames(workflow).map((input) => ({ workflow, input })));
}

export function formatFileSize(size) {
  for (const [unit, scale] of [["GB", 1024 ** 3], ["MB", 1024 ** 2], ["KB", 1024]]) {
    if (size >= scale) return `${(size / scale).toFixed(1).replace(/\.0$/, "")} ${unit}`;
  }
  return `${size} byte${size === 1 ? "" : "s"}`;
}

export function describeFileInput(spec) {
  const { accept, maxSize } = fileInputOptions(spec);
  const limit = `up to ${formatFileSize(maxSize)}`;
  return accept.length ? `${accept.join(", ")}, ${limit}` : `Any file ${limit}`;
}

// The same matching the backend does: extensions end the name, MIME patterns may end in /*.
export function fileAccepted(accept, name, type = "") {
  if (!accept.length) return true;
  const lowered = String(name || "").toLowerCase();
  const kind = String(type || "").toLowerCase();
  return accept.some((pattern) => {
    const value = pattern.toLowerCase();
    if (value.startsWith(".")) return lowered.endsWith(value);
    if (!kind) return false;
    return value.endsWith("/*") ? kind.startsWith(value.slice(0, -1)) : kind === value;
  });
}

// Why a chosen file can't fill the input, or "". Size and type are known only for
// files picked or dropped in the window; a typed path is checked when the run starts.
export function fileInputProblem(spec, file) {
  if (!file) return "";
  const { accept, maxSize } = fileInputOptions(spec);
  if (Number.isFinite(file.size) && file.size > maxSize) {
    return `${file.name} is ${formatFileSize(file.size)}, more than the ${formatFileSize(maxSize)} this input takes`;
  }
  if (!fileAccepted(accept, file.name, file.type)) {
    // Extensions alone can't tell a MIME pattern; leave that to the run's check.
    if (!file.type && accept.some((pattern) => !pattern.startsWith("."))) return "";
    return `${file.name} isn't a kind of file this input takes (${accept.join(", ")})`;
  }
  return "";
}

export function fileName(path) {
  return String(path || "").split(/[\\/]/).filter(Boolean).pop() || "";
}
