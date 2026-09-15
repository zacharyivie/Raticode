import { autoLayoutWorkflow } from "../lib/workflowLayout.js";
export { autoLayoutWorkflow } from "../lib/workflowLayout.js";
import { PathNameDialog } from "./PathNameDialog.jsx";
export { PathNameDialog } from "./PathNameDialog.jsx";
import brandCompat from "../lib/brandCompat.js";
import { cloneJson } from "../lib/jsonValue.js";
import { PROVIDER_PERMISSIONS } from "../lib/providerPermissions.js";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertCircle,
  Bell,
  Braces,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Command,
  Copy,
  Database,
  Download,
  ExternalLink,
  Eye,
  FilePenLine,
  Files,
  FileText,
  FileX,
  FolderOpen,
  Globe2,
  LocateFixed,
  Layers,
  Loader2,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  MoveRight,
  PencilLine,
  Play,
  Plus,
  RefreshCw,
  Repeat2,
  Route,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Terminal,
  Trash2,
  Upload,
  Webhook,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { apiUrl } from "../lib/api.js";
import { DEFAULT_APP_SETTINGS, matchesCommand } from "../lib/settings.js";
import { Dialog } from "./Dialog.jsx";
import GraphOutline from "./GraphOutline.jsx";
import {
  ProviderModelEffortFields,
  useProviderCapabilities,
} from "./ProviderModelEffortFields.jsx";

const DEFAULT_RETENTION_SETTINGS = {
  keepDays: 14,
  keepFailedDays: 30,
  keepLast: 100,
};

const nodeStyles = {
  start: {
    icon: Play,
    accent: "bg-blue-700",
    border: "border-blue-200",
    chip: "bg-blue-50 text-blue-700 border-blue-100",
  },
  pass: {
    icon: Check,
    accent: "bg-emerald-700",
    border: "border-emerald-200",
    chip: "bg-emerald-50 text-emerald-700 border-emerald-100",
  },
  fail: {
    icon: X,
    accent: "bg-red-700",
    border: "border-red-200",
    chip: "bg-red-50 text-red-700 border-red-100",
  },
  break: {
    icon: Square,
    accent: "bg-orange-700",
    border: "border-orange-200",
    chip: "bg-orange-50 text-orange-700 border-orange-100",
  },
  loop: {
    icon: Repeat2,
    accent: "bg-indigo-700",
    border: "border-indigo-200",
    chip: "bg-indigo-50 text-indigo-700 border-indigo-100",
  },
  agent: {
    icon: Braces,
    accent: "bg-indigo-600",
    border: "border-indigo-200",
    chip: "bg-indigo-50 text-indigo-700 border-indigo-100",
  },
  bash_command: {
    icon: Command,
    accent: "bg-slate-900",
    border: "border-slate-200",
    chip: "bg-slate-100 text-slate-700 border-slate-200",
  },
  python_script: {
    icon: Route,
    accent: "bg-amber-600",
    border: "border-amber-200",
    chip: "bg-amber-50 text-amber-700 border-amber-100",
  },
  shell_script: {
    icon: Command,
    accent: "bg-sky-700",
    border: "border-sky-200",
    chip: "bg-sky-50 text-sky-700 border-sky-100",
  },
  read_file: {
    icon: FileText,
    accent: "bg-cyan-700",
    border: "border-cyan-200",
    chip: "bg-cyan-50 text-cyan-700 border-cyan-100",
  },
  write_file: {
    icon: FilePenLine,
    accent: "bg-emerald-700",
    border: "border-emerald-200",
    chip: "bg-emerald-50 text-emerald-700 border-emerald-100",
  },
  copy_file: {
    icon: Files,
    accent: "bg-indigo-700",
    border: "border-indigo-200",
    chip: "bg-indigo-50 text-indigo-700 border-indigo-100",
  },
  move_file: {
    icon: MoveRight,
    accent: "bg-violet-700",
    border: "border-violet-200",
    chip: "bg-violet-50 text-violet-700 border-violet-100",
  },
  delete_file: {
    icon: FileX,
    accent: "bg-rose-700",
    border: "border-rose-200",
    chip: "bg-rose-50 text-rose-700 border-rose-100",
  },
  file: {
    icon: FileText,
    accent: "bg-slate-700",
    border: "border-slate-200",
    chip: "bg-slate-100 text-slate-700 border-slate-200",
  },
  folder: {
    icon: FolderOpen,
    accent: "bg-amber-700",
    border: "border-amber-200",
    chip: "bg-amber-50 text-amber-700 border-amber-100",
  },
  open_resource: {
    icon: ExternalLink,
    accent: "bg-blue-700",
    border: "border-blue-200",
    chip: "bg-blue-50 text-blue-700 border-blue-100",
  },
  prompt_file: {
    icon: FilePenLine,
    accent: "bg-fuchsia-700",
    border: "border-fuchsia-200",
    chip: "bg-fuchsia-50 text-fuchsia-700 border-fuchsia-100",
  },
  common_llm_task: {
    icon: Sparkles,
    accent: "bg-orange-700",
    border: "border-orange-200",
    chip: "bg-orange-50 text-orange-700 border-orange-100",
  },
  local_vectorize: {
    icon: Database,
    accent: "bg-lime-700",
    border: "border-lime-200",
    chip: "bg-lime-50 text-lime-700 border-lime-100",
  },
  local_search: {
    icon: Search,
    accent: "bg-purple-700",
    border: "border-purple-200",
    chip: "bg-purple-50 text-purple-700 border-purple-100",
  },
  http_request: {
    icon: Globe2,
    accent: "bg-blue-700",
    border: "border-blue-200",
    chip: "bg-blue-50 text-blue-700 border-blue-100",
  },
  approval_gate: {
    icon: ShieldCheck,
    accent: "bg-amber-700",
    border: "border-amber-200",
    chip: "bg-amber-50 text-amber-700 border-amber-100",
  },
  notification: {
    icon: Bell,
    accent: "bg-cyan-700",
    border: "border-cyan-200",
    chip: "bg-cyan-50 text-cyan-700 border-cyan-100",
  },
};

const defaultSettings = {
  allowFailure: false,
  awaitAllInputs: true,
  failFast: false,
  forEach: "",
  maxConcurrency: 1,
  pipeOutput: false,
  retryCount: 0,
  retryDelaySeconds: 1,
  timeoutSeconds: "",
};
const minZoom = 0.45;
const maxZoom = 1.8;
const graphWorldSize = 20000;
const graphWorldOffset = graphWorldSize / 2;
const nodeWidth = 220;
const nodeHeight = 96;
const minimapWidth = 124;
const minimapHeight = 86;
const nodeStack = {
  base: 10,
  selected: 30,
  activeSelected: 40,
  dragging: 50,
};
const isWindows =
  typeof navigator !== "undefined" &&
  /win/i.test(`${navigator.userAgent} ${navigator.platform}`);
const commandNodeLabel = isWindows ? "PowerShell command" : "Bash command";
const defaultCommand = isWindows ? 'Write-Output "hello"' : "echo hello";
const specialNodeLabels = {
  start: "START",
  pass: "PASS",
  fail: "FAIL",
};

function isSpecialNodeType(type) {
  return Object.hasOwn(specialNodeLabels, type);
}

export function nodeStackIndex(
  nodeId,
  { draggingNodeId = null, selectedNodeId = null, selectedNodeIds = [] } = {},
) {
  if (draggingNodeId === nodeId) return nodeStack.dragging;
  if (selectedNodeId === nodeId) return nodeStack.activeSelected;
  if (selectedNodeIds.includes(nodeId)) return nodeStack.selected;
  return nodeStack.base;
}

function specialNodeLabel(type) {
  return specialNodeLabels[type] ?? null;
}

export function defaultOperation(type, nodeNumber = 1) {
  switch (type) {
    case "start":
      return { type };
    case "pass":
      return { type, message: "Workflow completed successfully" };
    case "fail":
      return { type, message: "Workflow failed" };
    case "break":
      return { type, message: "Stop this loop" };
    case "loop":
      return {
        type,
        source: defaultFanSource("count"),
      };
    case "bash_command":
      return {
        type,
        command: defaultCommand,
        working_dir: "",
        env: {},
      };
    case "python_script":
      return {
        type,
        script_path: `scripts/step_${nodeNumber}.py`,
        args: [],
        env: {},
      };
    case "shell_script":
      return {
        type,
        script_path: `scripts/step_${nodeNumber}.sh`,
        args: [],
        env: {},
      };
    case "read_file":
      return {
        type,
        path: "data/input.txt",
        encoding: "utf-8",
        errors: "strict",
      };
    case "write_file":
      return {
        type,
        path: "data/output.txt",
        content: "",
        encoding: "utf-8",
        create_dirs: true,
        overwrite: true,
        append: false,
      };
    case "copy_file":
      return {
        type,
        source_path: "data/input.txt",
        destination_path: "data/output.txt",
        create_dirs: true,
        overwrite: false,
      };
    case "move_file":
      return {
        type,
        source_path: "data/input.txt",
        destination_path: "data/archive/input.txt",
        create_dirs: true,
        overwrite: false,
      };
    case "delete_file":
      return {
        type,
        path: "data/old.txt",
        use_trash: true,
        recursive: false,
        missing_ok: false,
      };
    case "file":
      return {
        type,
        path: "",
      };
    case "folder":
      return {
        type,
        path: "",
      };
    case "open_resource":
      return {
        type,
        target: "https://example.com",
        resource_type: "auto",
        args: [],
      };
    case "prompt_file":
      return {
        type,
        output_path: `prompts/generated-${nodeNumber}.md`,
        template: "Use this context:\n\n{{_piped_input}}",
        template_path: "",
        variables: {},
        encoding: "utf-8",
        create_dirs: true,
        overwrite: true,
      };
    case "common_llm_task":
      return {
        type,
        agent_id: `agent-${nodeNumber}`,
        task: "summarize",
        target: "",
        instructions: "",
        working_dir: ".",
        input_mapping: {},
      };
    case "local_vectorize":
      return {
        type,
        source_path: "docs",
        index_path: "indexes/docs.json",
        glob: "**/*",
        recursive: true,
        chunk_size: 1200,
        chunk_overlap: 120,
        encoding: "utf-8",
        mode: "incremental",
      };
    case "local_search":
      return {
        type,
        index_path: "indexes/docs.json",
        query: "",
        top_k: 5,
        score_threshold: 0,
        include_snippets: true,
        include_file_metadata: true,
      };
    case "http_request":
      return {
        type,
        method: "GET",
        url: "https://api.example.com/resource",
        headers: {},
        params: {},
        json: null,
        body: "",
        timeout_seconds: 30,
        retry: { attempts: 1, backoff_seconds: 0, retry_on_statuses: [] },
        expected_statuses: [200],
        response_mode: "auto",
        output_mapping: {},
        secret_fields: [],
      };
    case "approval_gate":
      return {
        type,
        message: "Approve continuing this workflow?",
        timeout_seconds: null,
        timeout_decision: "timeout",
        approvers: [],
        notify: false,
        notification_title: "Raticode approval needed",
      };
    case "notification":
      return {
        type,
        title: "Raticode notification",
        body: "",
        channel: "desktop",
        urgency: "normal",
        webhook_url: "",
        headers: {},
        payload: null,
        email_from: "",
        email_to: [],
        smtp_host: "",
        smtp_port: 587,
        smtp_username: "",
        smtp_password: "",
        smtp_starttls: true,
        timeout_seconds: 30,
        retry: { attempts: 1, backoff_seconds: 0, retry_on_statuses: [] },
        expected_statuses: [200, 201, 202, 204],
        network_allowlist: [],
      };
    case "workflow":
      return {
        type,
        workflow_id: "",
        input_bindings: {},
      };
    case "subflow":
      return {
        type,
        component_id: "",
        source_path: "",
        input_bindings: {},
        output_contract: {},
      };
    case "agent":
    default:
      return {
        type: "agent",
        agent_id: `agent-${nodeNumber}`,
        prompt_path: "",
        working_dir: ".",
        skill_name: "",
        memory: "none",
        input_mapping: {},
      };
  }
}

export function defaultAgentConfig(agentId, overrides = {}) {
  return {
    agent_id: agentId,
    subscription: "codex",
    profile: "",
    model: "",
    effort: "",
    working_dir: ".",
    prompt_path: "",
    tools: [],
    mcp_servers: [],
    env: {},
    ...overrides,
  };
}

function nodeMetaFromOperation(operation = {}, pathBasePath = "") {
  switch (operation.type) {
    case "start":
      return "starting point";
    case "pass":
      return operation.message || "stop with success";
    case "fail":
      return operation.message || "stop with error";
    case "break":
      return operation.message || "stop loop";
    case "loop":
      return `loop ${operation.source?.type || "items"}`;
    case "bash_command":
      return operation.command || commandNodeLabel.toLowerCase();
    case "python_script":
    case "shell_script":
      return operation.script_path
        ? resolveDisplayPath(operation.script_path, pathBasePath)
        : "script";
    case "read_file":
      return `read ${operation.path ? resolveDisplayPath(operation.path, pathBasePath) : "file"}`;
    case "write_file":
      return `write ${operation.path ? resolveDisplayPath(operation.path, pathBasePath) : "file"}`;
    case "copy_file":
      return `copy ${
        operation.source_path ? resolveDisplayPath(operation.source_path, pathBasePath) : "source"
      } to ${
        operation.destination_path
          ? resolveDisplayPath(operation.destination_path, pathBasePath)
          : "destination"
      }`;
    case "move_file":
      return `move ${
        operation.source_path ? resolveDisplayPath(operation.source_path, pathBasePath) : "source"
      } to ${
        operation.destination_path
          ? resolveDisplayPath(operation.destination_path, pathBasePath)
          : "destination"
      }`;
    case "delete_file":
      return `delete ${operation.path ? resolveDisplayPath(operation.path, pathBasePath) : "file"}`;
    case "file":
      return operation.path ? resolveDisplayPath(operation.path, pathBasePath) : "file";
    case "folder":
      return operation.path ? resolveDisplayPath(operation.path, pathBasePath) : "folder";
    case "open_resource":
      return `open ${operation.target ? resolveDisplayPath(operation.target, pathBasePath) : "target"}`;
    case "prompt_file":
      return `prompt ${
        operation.output_path ? resolveDisplayPath(operation.output_path, pathBasePath) : "file"
      }`;
    case "common_llm_task":
      return `${operation.task || "summarize"} with ${operation.agent_id || "agent"}`;
    case "local_vectorize":
      return `index ${
        operation.source_path ? resolveDisplayPath(operation.source_path, pathBasePath) : "files"
      }`;
    case "local_search":
      return `search ${
        operation.index_path ? resolveDisplayPath(operation.index_path, pathBasePath) : "index"
      }`;
    case "http_request":
      return `${operation.method || "GET"} ${operation.url || "url"}`;
    case "approval_gate":
      return operation.timeout_seconds
        ? `approval timeout ${operation.timeout_seconds}s`
        : "approval required";
    case "notification":
      return `${operation.channel || "desktop"} · ${operation.title || "notification"}`;
    case "agent":
      if (operation.skill_name) return `${operation.agent_id || "agent"} · /${operation.skill_name}`;
      return operation.prompt_path
        ? `${operation.agent_id || "agent"} · ${resolveDisplayPath(operation.prompt_path, pathBasePath)}`
        : operation.agent_id || "agent";
    default:
      return "operation";
  }
}

function buildInputSourceOptions(node, nodes, edges) {
  const nodesById = Object.fromEntries(nodes.map((candidate) => [candidate.id, candidate]));
  const upstreamNodes = edges
    .filter((edge) => edge.to === node.id)
    .map((edge) => nodesById[edge.from])
    .filter(Boolean);
  const options = [
    ["previous.text", "Previous node text"],
    ["previous.data.message", "Previous message"],
    ["previous.data.stdout", "Previous stdout"],
    ["previous.data.stderr", "Previous stderr"],
    ["loop.current.file_path", "Loop current file path"],
    ["loop.current.file_name", "Loop current file name"],
    ["loop.current.file_stem", "Loop current file stem"],
    ["loop.current.file_extension", "Loop current file extension"],
    ["loop.current.directory", "Loop current directory"],
    ["loop.current.parent_path", "Loop current parent path"],
    ["loop.current.file_content", "Loop current file content"],
    ["loop.current._row", "Loop current row JSON"],
    ["loop.current.event_json", "Loop current event JSON"],
    ["loop.current.kind", "Loop current event kind"],
    ["loop.current.size", "Loop current file size"],
    ["loop.current.mtime_ns", "Loop current modified time"],
    ["loop.current.index", "Loop current index"],
  ];

  upstreamNodes.forEach((upstream) => {
    const label = upstream.label || upstream.id;
    nodeOutputFields(upstream).forEach(([path, fieldLabel]) => {
      options.push([`${upstream.id}.${path}`, `${label} ${fieldLabel}`]);
    });
  });

  return dedupeOptions(options);
}

export function nodeOutputFields(node) {
  const type = node?.type || node?.operation?.type;
  const common = [
    ["text", "text"],
    ["success", "success"],
  ];
  switch (type) {
    case "bash_command":
    case "python_script":
    case "shell_script":
      return [
        ...common,
        ["data.stdout", "stdout"],
        ["data.stderr", "stderr"],
        ["data.command", "command"],
        ["data.script_path", "script path"],
      ];
    case "read_file":
    case "prompt_file":
    case "write_file":
      return [
        ...common,
        ["data.file_path", "file path"],
        ["data.file_name", "file name"],
        ["data.file_stem", "file stem"],
        ["data.file_extension", "file extension"],
        ["data.parent_path", "parent folder"],
        ["data.directory", "directory"],
        ["data.content", "content"],
        ["data.characters_written", "characters written"],
        ["data.bytes_written", "bytes written"],
      ];
    case "copy_file":
    case "move_file":
      return [
        ...common,
        ["data.source_path", "source path"],
        ["data.source_name", "source name"],
        ["data.destination_path", "destination path"],
        ["data.destination_name", "destination name"],
        ["data.destination_directory", "destination directory"],
      ];
    case "delete_file":
      return [
        ...common,
        ["data.path", "path"],
        ["data.file_path", "file path"],
        ["data.folder_path", "folder path"],
        ["data.trash_path", "trash path"],
        ["data.deleted", "deleted"],
      ];
    case "file":
      return [
        ...common,
        ["data.file_path", "file path"],
        ["data.file_name", "file name"],
        ["data.file_stem", "file stem"],
        ["data.file_extension", "file extension"],
        ["data.parent_path", "parent folder"],
        ["data.directory", "directory"],
      ];
    case "folder":
      return [
        ...common,
        ["data.folder_path", "folder path"],
        ["data.folder_name", "folder name"],
        ["data.parent_path", "parent folder"],
        ["data.directory", "directory"],
      ];
    case "loop":
      return [
        ...common,
        ["items", "all items"],
        ["data.count", "item count"],
        ["data.source_type", "source type"],
        ["data.source_path", "source path"],
        ["data.glob", "glob"],
      ];
    case "agent":
    case "common_llm_task":
      return [
        ...common,
        ["data.message", "message"],
        ["data.agent_id", "agent ID"],
        ["data.thoughts", "thoughts"],
      ];
    case "local_vectorize":
      return [
        ...common,
        ["data.source_path", "source path"],
        ["data.index_path", "index path"],
        ["data.file_count", "file count"],
        ["data.indexed_file_count", "indexed files"],
        ["data.chunk_count", "chunk count"],
        ["data.current", "index current"],
        ["data.status", "index status"],
        ["data.added_files", "added files"],
        ["data.updated_files", "updated files"],
        ["data.deleted_files", "deleted files"],
        ["data.stale_files", "stale files"],
        ["data.strategy", "embedding strategy"],
      ];
    case "local_search":
      return [
        ...common,
        ["items", "results"],
        ["data.results", "results"],
        ["data.index_path", "index path"],
        ["data.query", "query"],
        ["data.score_threshold", "score threshold"],
        ["data.strategy", "search strategy"],
      ];
    case "http_request":
      return [
        ...common,
        ["data.status", "status"],
        ["data.headers", "headers"],
        ["data.body", "body"],
        ["data.json", "JSON"],
        ["data.selected", "selected outputs"],
      ];
    case "approval_gate":
      return [
        ...common,
        ["data.decision", "decision"],
        ["data.approved", "approved"],
        ["data.decidedBy", "decided by"],
        ["data.notes", "notes"],
        ["data.message", "message"],
        ["data.runId", "run ID"],
      ];
    case "notification":
      return [
        ...common,
        ["data.title", "title"],
        ["data.body", "body"],
        ["data.channel", "channel"],
        ["data.urgency", "urgency"],
      ];
    case "open_resource":
      return [...common, ["data.target", "target"], ["data.resource_type", "resource type"]];
    case "pass":
    case "fail":
    case "break":
      return [...common, ["data.message", "message"]];
    default:
      return common;
  }
}

function dedupeOptions(options) {
  const seen = new Set();
  return options.filter(([value]) => {
    if (seen.has(value)) return false;
    seen.add(value);
    return true;
  });
}

function pathBasename(pathValue = "") {
  const normalized = String(pathValue).replace(/\\/g, "/");
  return normalized.split("/").filter(Boolean).at(-1) || normalized || "path";
}

function fileExtension(pathValue = "") {
  const name = pathBasename(pathValue);
  const index = name.lastIndexOf(".");
  if (index <= 0 || index === name.length - 1) return "";
  return name.slice(index + 1).toUpperCase();
}

function isUrlPath(pathValue = "") {
  return /^[a-z][a-z0-9+.-]*:/i.test(String(pathValue));
}

function isAbsolutePath(pathValue = "") {
  const value = String(pathValue);
  return (
    value.startsWith("/") ||
    value.startsWith("\\\\") ||
    /^[A-Za-z]:[\\/]/.test(value)
  );
}

function joinPath(basePath = "", pathValue = "") {
  const base = String(basePath);
  const value = String(pathValue);
  const separator = base.includes("\\") && !base.includes("/") ? "\\" : "/";
  if (!base) return value;
  if (value === ".") return base;
  return `${base.replace(/[\\/]+$/, "")}${separator}${value.replace(/^[\\/]+/, "")}`;
}

function normalizeDisplayPath(pathValue = "") {
  const value = String(pathValue);
  if (!value) return "";
  return value.replace(/\/\.(?=\/|$)/g, "").replace(/\\/g, "\\");
}

function resolveDisplayPath(pathValue = "", basePath = "") {
  const value = String(pathValue ?? "").trim();
  if (!value || isUrlPath(value) || isAbsolutePath(value)) {
    return value;
  }
  return normalizeDisplayPath(joinPath(basePath, value));
}

function canonicalPath(pathValue = "") {
  return String(pathValue ?? "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/\/+$/, "");
}

function pathParent(pathValue = "") {
  const value = canonicalPath(pathValue);
  if (!value) return "";
  const index = value.lastIndexOf("/");
  if (index <= 0) return value.startsWith("/") ? "/" : "";
  return value.slice(0, index);
}

function isPathInsideRoot(pathValue = "", rootValue = "") {
  const path = canonicalPath(pathValue);
  const root = canonicalPath(rootValue);
  if (!path || !root) return false;
  return path === root || path.startsWith(`${root}/`);
}

function workflowAccessCoversPath(workflow, pathValue, dataDir = "") {
  if (dataDir && isPathInsideRoot(pathValue, dataDir)) return true;
  return (workflow.filesystemAccess ?? []).some((entry) =>
    entry?.path ? isPathInsideRoot(pathValue, entry.path) : false,
  );
}

function pathsMatch(pathValue = "", otherPathValue = "") {
  const path = canonicalPath(pathValue);
  const otherPath = canonicalPath(otherPathValue);
  return Boolean(path && otherPath && path === otherPath);
}

function uniqueAccessEntries(entries = []) {
  const seen = new Set();
  return entries
    .map((entry) => ({
      path: String(entry?.path ?? "").trim(),
      read: entry?.read ?? true,
      write: entry?.write ?? true,
      execute: entry?.execute ?? false,
    }))
    .filter((entry) => {
      const path = canonicalPath(entry?.path ?? "");
      if (!path || seen.has(path)) return false;
      seen.add(path);
      return true;
    });
}

function mergeWorkflowFilesystemAccess(workflow, entries) {
  return uniqueAccessEntries([...(workflow.filesystemAccess ?? []), ...entries]);
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function resizerValueForKey(
  key,
  currentValue,
  { defaultValue, max, min, orientation = "vertical", shiftKey = false, step = 10 },
) {
  if (key === "Enter") return clamp(defaultValue, min, max);
  if (key === "Home") return min;
  if (key === "End") return max;
  const amount = shiftKey ? step * 4 : step;
  if (orientation === "horizontal") {
    if (key === "ArrowUp") return clamp(currentValue + amount, min, max);
    if (key === "ArrowDown") return clamp(currentValue - amount, min, max);
  } else {
    if (key === "ArrowRight") return clamp(currentValue + amount, min, max);
    if (key === "ArrowLeft") return clamp(currentValue - amount, min, max);
  }
  return null;
}

function normalizedSelectionBox(selectionBox) {
  const left = Math.min(selectionBox.start.x, selectionBox.current.x);
  const top = Math.min(selectionBox.start.y, selectionBox.current.y);
  const right = Math.max(selectionBox.start.x, selectionBox.current.x);
  const bottom = Math.max(selectionBox.start.y, selectionBox.current.y);
  return {
    left,
    top,
    width: right - left,
    height: bottom - top,
  };
}

function selectionBoxArea(box) {
  return box.width * box.height;
}

function nodeIntersectsBox(node, box) {
  const nodeLeft = node.x ?? 0;
  const nodeTop = node.y ?? 0;
  const nodeRight = nodeLeft + nodeWidth;
  const nodeBottom = nodeTop + nodeHeight;
  const boxRight = box.left + box.width;
  const boxBottom = box.top + box.height;

  return (
    nodeLeft < boxRight &&
    nodeRight > box.left &&
    nodeTop < boxBottom &&
    nodeBottom > box.top
  );
}

function nextAvailableNodeNumber(nodes) {
  const usedNumbers = new Set(
    nodes
      .map((node) => String(node.id).match(/^node-(\d+)$/)?.[1])
      .filter(Boolean)
      .map(Number),
  );
  let nextNumber = 1;
  while (usedNumbers.has(nextNumber)) {
    nextNumber += 1;
  }
  return nextNumber;
}

function nextAvailableAgentNumber(nodes, agents, usedAgentIds = []) {
  const usedNumbers = new Set([
    ...usedAgentIds
      .map((agentId) => String(agentId).match(/^agent-(\d+)$/)?.[1])
      .filter(Boolean)
      .map(Number),
    ...Object.keys(agents ?? {})
      .map((agentId) => String(agentId).match(/^agent-(\d+)$/)?.[1])
      .filter(Boolean)
      .map(Number),
    ...nodes
      .map((node) => String(node.operation?.agent_id ?? "").match(/^agent-(\d+)$/)?.[1])
      .filter(Boolean)
      .map(Number),
  ]);
  let nextNumber = 1;
  while (usedNumbers.has(nextNumber)) {
    nextNumber += 1;
  }
  return nextNumber;
}

function structuredCloneCompatible(value) {
  return cloneJson(value);
}

export default function DagCanvas({
  active = true,
  approvalState,
  dataDir = "",
  logState,
  notice,
  rattishDocument = null,
  readOnly = false,
  runState,
  settings = DEFAULT_APP_SETTINGS,
  workflow,
  toolbarTarget = null,
  canvasOverlay = null,
  onExportWorkflow,
  onImportWorkflow,
  onLoadLatestLog,
  onDecideApproval,
  onRattishMutation,
  onRunWorkflow,
  onReplayRunLog,
  onSettingChange,
  onResumeRunLog,
  onSelectRunLog,
  onStopRunLog,
  onStopWorkflow,
  stopDisabled,
  stopTitle = "Stop all runs",
  onValidateWorkflow,
  onWorkflowChange,
  usedAgentIds = [],
}) {
  const canvasRef = useRef(null);
  const graphActionsRef = useRef(null);
  const importInputRef = useRef(null);
  const deleteSelectedNodeRef = useRef(null);
  const nodeDragMovedRef = useRef(false);
  const nodeDragSelectionRef = useRef([]);
  const [selectedNodeId, setSelectedNodeId] = useState();
  const [selectedNodeIds, setSelectedNodeIds] = useState([]);
  const [draggingNodeId, setDraggingNodeId] = useState(null);
  const [panningPointerId, setPanningPointerId] = useState(null);
  const [selectionBox, setSelectionBox] = useState(null);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(true);
  const [inspectorWidth, setInspectorWidth] = useState(settings.layout.graphInspectorWidth);
  const [expandedFolderNodes, setExpandedFolderNodes] = useState({});
  const [providerProfiles, setProviderProfiles] = useState([]);
  const {
    capabilities: providerCapabilities,
    refresh: refreshProviderCapabilities,
  } = useProviderCapabilities();

  useEffect(() => {
    async function loadProviderProfiles() {
      try {
        const response = await fetch(apiUrl("/provider/profiles"));
        if (!response.ok) return;
        const payload = await response.json();
        setProviderProfiles(payload.profiles ?? []);
      } catch {
        setProviderProfiles([]);
      }
    }
    loadProviderProfiles();
  }, []);
  const [folderNodeEntries, setFolderNodeEntries] = useState({});
  const [filePreviewPath, setFilePreviewPath] = useState(null);
  const [pendingTrustPrompt, setPendingTrustPrompt] = useState(null);
  const [runMenuOpen, setRunMenuOpen] = useState(false);
  const [selectedEdgeId, setSelectedEdgeId] = useState(null);
  const [draftEdge, setDraftEdge] = useState(null);
  const [keyboardConnectionFrom, setKeyboardConnectionFrom] = useState(null);
  const [outlineFocusRequest, setOutlineFocusRequest] = useState(null);
  const [viewport, setViewport] = useState({ x: 0, y: 0, scale: 1 });
  const workflowNodesRef = useRef([]);
  const [minimapDragging, setMinimapDragging] = useState(false);
  const [mapOpen, setMapOpen] = useState(false);
  const [mapTab, setMapTab] = useState("outline");
  const [graphFullscreen, setGraphFullscreen] = useState(false);

  useEffect(() => {
    if (!active) setGraphFullscreen(false);
  }, [active]);
  const [nodeContextMenu, setNodeContextMenu] = useState(null);
  const [nodeRenameDialog, setNodeRenameDialog] = useState(null);
  deleteSelectedNodeRef.current = deleteSelectedNode;

  useEffect(() => {
    setInspectorWidth(settings.layout.graphInspectorWidth);
  }, [settings.layout.graphInspectorWidth]);

  useEffect(() => {
    function handlePointerDown(event) {
      const details = graphActionsRef.current;
      if (details?.open && !details.contains(event.target)) {
        details.removeAttribute("open");
      }
    }

    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, []);

  const invalidWorkflow = Boolean(workflow.invalid);
  const rattishMode = Boolean(rattishDocument);
  const editingDisabled = invalidWorkflow || readOnly;
  const validationDiagnostics = workflowValidationDiagnostics(workflow);
  const blockingValidationErrors = validationDiagnostics.filter(
    (diagnostic) => diagnostic.severity === "error",
  );
  const runDisabled = invalidWorkflow || blockingValidationErrors.length > 0;
  const workflowNodes = useMemo(
    () =>
      (workflow.nodes ?? []).map((node) => {
        const forcedLabel = specialNodeLabel(node.type);
        return forcedLabel && node.label !== forcedLabel
          ? { ...node, label: forcedLabel }
          : node;
      }),
    [workflow.nodes],
  );
  workflowNodesRef.current = workflowNodes;
  const workflowEdges = useMemo(() => workflow.edges ?? [], [workflow.edges]);
  const edgeDiagnostics = useMemo(
    () => diagnosticsByTarget(validationDiagnostics, "edge"),
    [validationDiagnostics],
  );
  const nodeDiagnostics = useMemo(
    () => diagnosticsByTarget(validationDiagnostics, "node"),
    [validationDiagnostics],
  );

  const selectedNode = workflowNodes.find((node) => node.id === selectedNodeId);
  const selectedEdge = workflowEdges.find((edge) => edge.id === selectedEdgeId);
  const runResult = runState?.result?.workflowId === workflow.id ? runState.result : null;
  const historicalNodeOutputs = logState?.nodeOutputs ?? null;
  const runEvents = useMemo(
    () => (logState?.runEvents?.length ? logState.runEvents : runResult?.runEvents ?? []),
    [logState?.runEvents, runResult?.runEvents],
  );
  const runNodes = useMemo(
    () =>
      logState?.runNodes && Object.keys(logState.runNodes).length
        ? logState.runNodes
        : runResult?.runNodes ?? {},
    [logState?.runNodes, runResult?.runNodes],
  );
  const selectedRunId =
    logState?.selectedRunId ??
    (logState?.path ? logState.path.split(/[\\/]/).pop() : null) ??
    (runResult?.logPath ? runResult.logPath.split(/[\\/]/).pop() : null);
  const selectedNodeOutput = selectedNodeId
    ? runResult?.nodeOutputs?.[selectedNodeId] ?? historicalNodeOutputs?.[selectedNodeId] ?? null
    : null;
  const selectedRunNode = selectedNodeId ? runNodes?.[selectedNodeId] ?? null : null;
  const nodesById = useMemo(() => {
    return Object.fromEntries(workflowNodes.map((node) => [node.id, node]));
  }, [workflowNodes]);
  const selectedApproval = selectedNodeId
    ? approvalState?.approvals?.find(
        (approval) =>
          approval.nodeId === selectedNodeId &&
          selectedRunId &&
          approval.runId === selectedRunId,
      ) ?? null
    : null;
  const pendingApproval = useMemo(() => {
    const pendingApprovals = approvalState?.approvals?.filter(
      (approval) => approval.status === "pending" && nodesById[approval.nodeId],
    ) ?? [];
    if (!pendingApprovals.length) return null;
    return pendingApprovals
      .slice()
      .sort((first, second) =>
        String(first.requestedAt || "").localeCompare(String(second.requestedAt || "")),
      )[0];
  }, [approvalState?.approvals, nodesById]);
  const pendingApprovalNode = pendingApproval ? nodesById[pendingApproval.nodeId] : null;
  const workflowLogText =
    logState?.text || runResult?.logText || formatWorkflowRunLog(runResult);
  const nodeStatuses = useMemo(() => {
    return getNodeStatuses(workflowNodes, runResult, workflowLogText, runNodes, runEvents);
  }, [runEvents, runNodes, runResult, workflowNodes, workflowLogText]);
  const graphAnnouncement = useMemo(() => {
    if (keyboardConnectionFrom) {
      const source = nodesById[keyboardConnectionFrom];
      return `Connection started from ${source?.label ?? keyboardConnectionFrom}. Choose a target node and press Enter.`;
    }
    if (selectedNode) {
      const incoming = workflowEdges.filter((edge) => edge.to === selectedNode.id);
      const outgoing = workflowEdges.filter((edge) => edge.from === selectedNode.id);
      const diagnostics = nodeDiagnostics[selectedNode.id] ?? [];
      const validation = diagnostics.some((item) => item.severity === "error")
        ? "validation error"
        : diagnostics.some((item) => item.severity === "warning")
          ? "validation warning"
          : "valid";
      return `${selectedNode.label} selected. Status ${nodeStatuses[selectedNode.id] ?? "not run"}. ${incoming.length} incoming and ${outgoing.length} outgoing connections. ${validation}.`;
    }
    if (selectedEdge) {
      return `Connection selected from ${nodesById[selectedEdge.from]?.label ?? selectedEdge.from} to ${nodesById[selectedEdge.to]?.label ?? selectedEdge.to}. Condition ${selectedEdge.label ?? selectedEdge.condition ?? "always"}.`;
    }
    return "";
  }, [keyboardConnectionFrom, nodeDiagnostics, nodeStatuses, nodesById, selectedEdge, selectedNode, workflowEdges]);
  const currentWorkflowRunning =
    runState?.running && runState.workflowId === workflow.id;
  const workflowHasRunningRuns = currentWorkflowRunning || logState?.runs?.some(
    (run) => run.status === "running",
  );
  const runTitle = blockingValidationErrors.length
    ? blockingValidationErrors[0].message
    : workflowHasRunningRuns
      ? "Start another workflow run"
      : "Run workflow now";
  const deleteEdge = useCallback(
    (edgeId) => {
      const edgeIndex = workflowEdges.findIndex((edge) => edge.id === edgeId);
      const deletedEdge = workflowEdges[edgeIndex];
      const remainingEdges = workflowEdges.filter((edge) => edge.id !== edgeId);
      const nextEdge = remainingEdges[Math.min(edgeIndex, remainingEdges.length - 1)];
      const nextNode = deletedEdge ? nodesById[deletedEdge.from] : workflowNodes[0];

      if (rattishMode && deletedEdge) {
        const routes = remainingEdges
          .filter((edge) => edge.from === deletedEdge.from)
          .map((edge) => rattishRouteValue(edge, rattishDocument?.source));
        void onRattishMutation?.([
          { kind: "set_routes", node: deletedEdge.from, routes },
        ]);
      } else {
        onWorkflowChange({
          ...workflow,
          edges: remainingEdges,
        });
      }
      if (selectedEdgeId === edgeId) {
        if (nextEdge) {
          setSelectedEdgeId(nextEdge.id);
          setSelectedNodeId(undefined);
          setSelectedNodeIds([]);
          return `edge:${nextEdge.id}`;
        }
        setSelectedEdgeId(null);
        setSelectedNodeId(nextNode?.id);
        setSelectedNodeIds(nextNode ? [nextNode.id] : []);
        return nextNode ? `node:${nextNode.id}` : null;
      }
      return null;
    },
    [
      nodesById,
      onRattishMutation,
      onWorkflowChange,
      rattishDocument?.source,
      rattishMode,
      selectedEdgeId,
      workflow,
      workflowEdges,
      workflowNodes,
    ],
  );

  useEffect(() => {
    setSelectedNodeId(undefined);
    setSelectedNodeIds([]);
    setSelectedEdgeId(null);
    setDraftEdge(null);
    setKeyboardConnectionFrom(null);
    setOutlineFocusRequest(null);
    setDraggingNodeId(null);
    setPanningPointerId(null);
    setSelectionBox(null);
    const rect = canvasRef.current?.getBoundingClientRect();
    const nodes = workflowNodesRef.current;
    setViewport(nodes.length
      ? fitViewportToNodes(nodes, {
          width: rect?.width || 960,
          height: rect?.height || 640,
        })
      : { x: 0, y: 0, scale: 1 });
  }, [workflow.id]);

  useEffect(() => {
    if (selectedNodeId && !nodesById[selectedNodeId]) {
      setSelectedNodeId(undefined);
    }
    setSelectedNodeIds((currentIds) =>
      currentIds.filter((nodeId) => Boolean(nodesById[nodeId])),
    );
  }, [nodesById, selectedNodeId]);

  useEffect(() => {
    if (selectedEdgeId && !workflowEdges.some((edge) => edge.id === selectedEdgeId)) {
      setSelectedEdgeId(null);
    }
  }, [selectedEdgeId, workflowEdges]);

  useEffect(() => {
    function handleKeyDown(event) {
      if (!active || readOnly || !selectedEdgeId || event.defaultPrevented) return;
      const target = event.target;
      const tagName = target?.tagName?.toLowerCase?.();
      if (
        target?.isContentEditable ||
        tagName === "input" ||
        tagName === "textarea" ||
        tagName === "select"
      ) {
        return;
      }
      if (matchesCommand(event, settings, "graph.deleteSelection")) {
        event.preventDefault();
        deleteEdge(selectedEdgeId);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [active, deleteEdge, readOnly, selectedEdgeId, settings]);

  useEffect(() => {
    function handleKeyDown(event) {
      if (!active || event.defaultPrevented) return;
      const target = event.target;
      const tagName = target?.tagName?.toLowerCase?.();
      const editingText =
        target?.isContentEditable ||
        tagName === "input" ||
        tagName === "textarea" ||
        tagName === "select";

      if (event.key === "Escape") {
        if (graphFullscreen) {
          setGraphFullscreen(false);
          return;
        }
        setDraftEdge(null);
        setSelectedEdgeId(null);
        setSelectedNodeId(undefined);
        setSelectedNodeIds([]);
        setSelectionBox(null);
        return;
      }

      if (editingText || canvasOverlay) return;

      if (matchesCommand(event, settings, "graph.selectAll")) {
        event.preventDefault();
        setSelectedEdgeId(null);
        setSelectedNodeIds(workflowNodes.map((node) => node.id));
        setSelectedNodeId(workflowNodes.at(-1)?.id);
        return;
      }

      if (matchesCommand(event, settings, "graph.deleteSelection") && selectedNodeId) {
        event.preventDefault();
        deleteSelectedNodeRef.current?.();
        return;
      }

      if (matchesCommand(event, settings, "graph.zoomIn")) {
        event.preventDefault();
        setViewport((current) => {
          const rect = canvasRef.current?.getBoundingClientRect();
          const centerX = (rect?.width || 960) / 2;
          const centerY = (rect?.height || 640) / 2;
          const nextScale = clamp(current.scale * 1.14, minZoom, maxZoom);
          const contentX = (centerX - current.x) / current.scale;
          const contentY = (centerY - current.y) / current.scale;
          return {
            scale: nextScale,
            x: centerX - contentX * nextScale,
            y: centerY - contentY * nextScale,
          };
        });
        return;
      }

      if (matchesCommand(event, settings, "graph.zoomOut")) {
        event.preventDefault();
        setViewport((current) => {
          const rect = canvasRef.current?.getBoundingClientRect();
          const centerX = (rect?.width || 960) / 2;
          const centerY = (rect?.height || 640) / 2;
          const nextScale = clamp(current.scale * 0.88, minZoom, maxZoom);
          const contentX = (centerX - current.x) / current.scale;
          const contentY = (centerY - current.y) / current.scale;
          return {
            scale: nextScale,
            x: centerX - contentX * nextScale,
            y: centerY - contentY * nextScale,
          };
        });
        return;
      }

      if (matchesCommand(event, settings, "graph.fit")) {
        event.preventDefault();
        const rect = canvasRef.current?.getBoundingClientRect();
        setViewport(
          fitViewportToNodes(workflowNodes, {
            width: rect?.width || 960,
            height: rect?.height || 640,
          }),
        );
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [active, canvasOverlay, graphFullscreen, settings, workflowNodes, selectedNodeId]);

  useEffect(() => {
    if (panningPointerId === null) return undefined;

    const previousCursor = document.body.style.cursor;
    document.body.style.cursor = "grabbing";
    return () => {
      document.body.style.cursor = previousCursor;
    };
  }, [panningPointerId]);

  function updateNode(nodeId, patch) {
    onWorkflowChange({
      ...workflow,
      nodes: workflowNodes.map((node) => {
        if (node.id !== nodeId) return node;
        const nextNode = { ...node, ...patch };
        const forcedLabel = specialNodeLabel(nextNode.type);
        return forcedLabel ? { ...nextNode, label: forcedLabel } : nextNode;
      }),
    });
  }

  function updateNodeOperation(nodeId, patch) {
    const node = nodesById[nodeId];
    const operation = { ...(node.operation ?? defaultOperation(node.type)), ...patch };
    const nextNodePatch = {
      operation,
      type: operation.type,
      meta: nodeMetaFromOperation(operation),
    };
    const forcedLabel = specialNodeLabel(operation.type);
    if (forcedLabel) {
      nextNodePatch.label = forcedLabel;
    }
    if (
      (operation.type === "file" || operation.type === "folder") &&
      Object.hasOwn(patch, "path") &&
      operation.path
    ) {
      nextNodePatch.label = pathBasename(operation.path);
    }
    const syncAgentPatch = {};
    if (operation.type === "agent" || operation.type === "common_llm_task") {
      if (Object.hasOwn(patch, "prompt_path")) {
        syncAgentPatch.prompt_path = operation.prompt_path;
      }
      if (Object.hasOwn(patch, "working_dir")) {
        syncAgentPatch.working_dir = operation.working_dir;
      }
    }
    const shouldSyncAgent =
      (operation.type === "agent" || operation.type === "common_llm_task") &&
      operation.agent_id &&
      (patch.agent_id || Object.keys(syncAgentPatch).length);

    if (shouldSyncAgent) {
      const currentAgent =
        workflow.agents?.[operation.agent_id] ??
        defaultAgentConfig(operation.agent_id, {
          prompt_path: operation.prompt_path,
          working_dir: operation.working_dir,
        });
      onWorkflowChange({
        ...workflow,
        agents: {
          ...(workflow.agents ?? {}),
          [operation.agent_id]: {
            ...currentAgent,
            ...syncAgentPatch,
          },
        },
        nodes: workflowNodes.map((currentNode) =>
          currentNode.id === nodeId ? { ...currentNode, ...nextNodePatch } : currentNode,
        ),
      });
      return;
    }

    updateNode(nodeId, nextNodePatch);
  }

  function updateNodeSettings(nodeId, patch) {
    const node = nodesById[nodeId];
    updateNode(nodeId, {
      settings: {
        ...defaultSettings,
        ...(node.settings ?? {}),
        ...patch,
      },
    });
  }

  function updateNodeType(nodeId, type) {
    if (
      isSpecialNodeType(type) &&
      workflowNodes.some((node) => node.id !== nodeId && node.type === type)
    ) {
      return;
    }
    const nextAgentNumber = nextAvailableAgentNumber(workflowNodes, workflow.agents, usedAgentIds);
    const nextOperation = defaultOperation(
      type,
      type === "agent" || type === "common_llm_task"
        ? nextAgentNumber
        : workflowNodes.length + 1,
    );
    const nextNode = {
      type,
      label: specialNodeLabel(type) ?? nodesById[nodeId].label,
      operation: nextOperation,
      settings: {
        ...defaultSettings,
        ...(nodesById[nodeId].settings ?? {}),
      },
      meta: nodeMetaFromOperation(nextOperation),
    };
    if (
      (type === "agent" || type === "common_llm_task") &&
      !workflow.agents?.[nextOperation.agent_id]
    ) {
      onWorkflowChange({
        ...workflow,
        agents: {
          ...(workflow.agents ?? {}),
          [nextOperation.agent_id]: defaultAgentConfig(nextOperation.agent_id, {
            prompt_path: nextOperation.prompt_path,
            working_dir: nextOperation.working_dir,
          }),
        },
        nodes: workflowNodes.map((node) => (node.id === nodeId ? { ...node, ...nextNode } : node)),
      });
      return;
    }
    updateNode(nodeId, nextNode);
  }

  function updateAgentConfig(agentId, patch) {
    const currentAgent = workflow.agents?.[agentId] ?? defaultAgentConfig(agentId);
    onWorkflowChange({
      ...workflow,
      agents: {
        ...(workflow.agents ?? {}),
        [agentId]: {
          ...currentAgent,
          ...patch,
        },
      },
    });
  }

  function canvasViewportSize() {
    const rect = canvasRef.current?.getBoundingClientRect();
    return {
      width: rect?.width || 960,
      height: rect?.height || 640,
    };
  }

  function fitNodes(nodes) {
    if (!nodes.length) return;
    setViewport(fitViewportToNodes(nodes, canvasViewportSize()));
  }

  function fitGraph() {
    fitNodes(workflowNodes);
  }

  function fitSelection() {
    const selectedNodes = workflowNodes.filter((node) => selectedNodeIds.includes(node.id));
    fitNodes(selectedNodes.length ? selectedNodes : workflowNodes);
  }

  function zoomViewport(multiplier) {
    const size = canvasViewportSize();
    const centerX = size.width / 2;
    const centerY = size.height / 2;
    setViewport((current) => {
      const nextScale = clamp(current.scale * multiplier, minZoom, maxZoom);
      const contentX = (centerX - current.x) / current.scale;
      const contentY = (centerY - current.y) / current.scale;
      return {
        scale: nextScale,
        x: centerX - contentX * nextScale,
        y: centerY - contentY * nextScale,
      };
    });
  }

  function applyAutoLayout() {
    const nextWorkflow = autoLayoutWorkflow(workflow);
    onWorkflowChange(nextWorkflow);
    const schedule = window.requestAnimationFrame ?? ((callback) => callback());
    schedule(() => {
      fitNodes(nextWorkflow.nodes ?? []);
    });
  }

  function addNode(event) {
    const nextNumber = nextAvailableNodeNumber(workflowNodes);
    if (rattishMode) {
      const nodeId = `node-${nextNumber}`;
      void Promise.resolve(onRattishMutation?.([
        {
          kind: "add_node",
          node: nodeId,
          node_type: "bash-command",
          fields: { command: "" },
        },
      ])).then((document) => {
        if (!document) return;
        setSelectedNodeId(nodeId);
        setSelectedNodeIds([nodeId]);
        setSelectedEdgeId(null);
        setInspectorCollapsed(false);
      });
      return;
    }
    const nextWorkflow = addDefaultNodeToWorkflow(workflow, {
      usedAgentIds,
      x: 180 + nextNumber * 34,
      y: 180 + nextNumber * 24,
    });
    const newNode = nextWorkflow.nodes.at(-1);
    onWorkflowChange(nextWorkflow);
    setSelectedNodeId(newNode.id);
    setSelectedNodeIds([newNode.id]);
    setSelectedEdgeId(null);
    if (event?.detail === 0) {
      setOutlineFocusRequest((current) => ({
        id: (current?.id ?? 0) + 1,
        itemKey: `node:${newNode.id}`,
      }));
    }
  }

  async function applyValidationFix(fix) {
    const action = fix?.action;
    const payload = fix?.payload ?? {};
    if (!action) return;

    if (action === "remove_edge") {
      const edgeId = payload.edgeId;
      onWorkflowChange({
        ...workflow,
        edges: (workflow.edges ?? []).filter(
          (edge) =>
            edge.id !== edgeId &&
            !(edge.from === payload.from && edge.to === payload.to),
        ),
      });
      return;
    }

    if (action === "replace_edge_pattern") {
      const edgeId = payload.edgeId;
      onWorkflowChange({
        ...workflow,
        edges: (workflow.edges ?? []).map((edge) => {
          const matches =
            edge.id === edgeId || (edge.from === payload.from && edge.to === payload.to);
          if (!matches) return edge;
          const outputPattern = payload.outputPattern ?? "";
          return {
            ...edge,
            condition: "output_matches",
            outputPattern,
            label: edgeLabel("output_matches", outputPattern),
          };
        }),
      });
      return;
    }

    if (action === "create_agent") {
      const agentId = payload.agentId;
      if (!agentId || workflow.agents?.[agentId]) return;
      onWorkflowChange({
        ...workflow,
        agents: {
          ...(workflow.agents ?? {}),
          [agentId]: defaultAgentConfig(agentId),
        },
      });
      return;
    }

    if (action === "create_prompt_file") {
      const response = await fetch(apiUrl(`/workflows/${workflow.id}/validate/fix`), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(fix),
      });
      if (response.ok) {
        onValidateWorkflow?.();
      }
      return;
    }

    if (action === "disable_schedule") {
      onWorkflowChange({ ...workflow, schedule: null });
      return;
    }

    if (action === "set_schedule_timezone") {
      onWorkflowChange({
        ...workflow,
        schedule: {
          ...(workflow.schedule ?? { cron_expression: "0 9 * * *" }),
          timezone: payload.timezone ?? "UTC",
        },
      });
      return;
    }

    if (action === "disable_conflicting_triggers") {
      onWorkflowChange({ ...workflow, schedule: null, watch: null });
      return;
    }

    if (action === "disable_continuous") {
      onWorkflowChange({ ...workflow, runContinuously: false });
    }
  }

  async function handleCanvasDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    if (invalidWorkflow) return;

    const droppedFiles = Array.from(event.dataTransfer?.files ?? []);
    if (!droppedFiles.length) return;

    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;

    const newNodes = [];
    const untrustedDrops = [];
    let nextNumber = nextAvailableNodeNumber(workflowNodes);
    const usedNodeIds = new Set(workflowNodes.map((node) => node.id));

    for (const [index, file] of droppedFiles.entries()) {
      let droppedPath =
        window.goferDesktop?.getDroppedFilePath?.(file) ||
        file.path ||
        file.webkitRelativePath;
      if (!droppedPath) continue;
      try {
        droppedPath = await window.goferDesktop?.grantDroppedPath?.(file) ?? droppedPath;
      } catch (error) {
        console.error("Failed to grant dropped path", error);
      }

      let info = null;
      try {
        info = await window.goferDesktop?.workspace?.getPathInfo?.(droppedPath);
      } catch (error) {
        console.error("Failed to inspect dropped path", error);
      }

      const path = info?.path ?? droppedPath;
      const kind = info?.isDirectory ? "folder" : "file";
      if (!workflowAccessCoversPath(workflow, path, dataDir)) {
        untrustedDrops.push({ path, parent: pathParent(path), kind });
      }
      const operation = { type: kind, path };
      const worldX = (event.clientX - rect.left - viewport.x) / viewport.scale;
      const worldY = (event.clientY - rect.top - viewport.y) / viewport.scale;
      while (usedNodeIds.has(`node-${nextNumber}`)) {
        nextNumber += 1;
      }
      const nodeId = `node-${nextNumber}`;
      usedNodeIds.add(nodeId);
      newNodes.push({
        id: nodeId,
        label: info?.basename ?? pathBasename(path),
        type: kind,
        operation,
        settings: defaultSettings,
        meta: nodeMetaFromOperation(operation),
        x: worldX + index * 28,
        y: worldY + index * 28,
      });
      nextNumber += 1;
    }

    if (newNodes.length) {
      if (untrustedDrops.length) {
        setPendingTrustPrompt({
          nodes: newNodes,
          drops: untrustedDrops,
          parentPath: untrustedDrops[0].parent || untrustedDrops[0].path,
        });
        return;
      }
      addDroppedNodes(newNodes);
    }
  }

  function addDroppedNodes(newNodes, accessEntries = []) {
    if (!newNodes.length) return;
    onWorkflowChange({
      ...workflow,
      filesystemAccess: mergeWorkflowFilesystemAccess(workflow, accessEntries),
      nodes: [...workflowNodes, ...newNodes],
    });
    setSelectedNodeId(newNodes.at(-1).id);
    setSelectedNodeIds(newNodes.map((node) => node.id));
  }

  function trustPendingDroppedNodes(trustParents) {
    if (!pendingTrustPrompt) return;
    const accessEntries = uniqueAccessEntries(
      pendingTrustPrompt.drops.map((drop) => ({
        path: trustParents ? drop.parent || drop.path : drop.path,
        read: true,
        write: true,
        execute: false,
      })),
    );
    addDroppedNodes(pendingTrustPrompt.nodes, accessEntries);
    setPendingTrustPrompt(null);
  }

  function cancelPendingDroppedNodes() {
    setPendingTrustPrompt(null);
  }

  function deleteSelectedNode() {
    if (!selectedNode) return;
    deleteNode(selectedNode.id);
  }

  function deleteNode(nodeId) {
    const nodeIndex = workflowNodes.findIndex((node) => node.id === nodeId);
    const remainingNodes = workflowNodes.filter((node) => node.id !== nodeId);
    const nextSelectedId = remainingNodes[Math.min(nodeIndex, remainingNodes.length - 1)]?.id;
    const deletingSelectedNode = selectedNodeId === nodeId || selectedNodeIds.includes(nodeId);

    if (rattishMode) {
      void onRattishMutation?.([{ kind: "delete_node", node: nodeId }]);
    } else {
      onWorkflowChange({
        ...workflow,
        nodes: remainingNodes,
        edges: workflowEdges.filter(
          (edge) => edge.from !== nodeId && edge.to !== nodeId,
        ),
      });
    }
    if (deletingSelectedNode) {
      setSelectedNodeId(nextSelectedId);
      setSelectedNodeIds(nextSelectedId ? [nextSelectedId] : []);
      setSelectedEdgeId(null);
    }
    setNodeContextMenu((current) => (current?.nodeId === nodeId ? null : current));
    return deletingSelectedNode && nextSelectedId ? `node:${nextSelectedId}` : null;
  }

  function duplicateNode(nodeId) {
    if (rattishMode) {
      const existing = new Set(workflowNodes.map((node) => node.id));
      let suffix = 2;
      let nextId = `${nodeId}-copy`;
      while (existing.has(nextId)) {
        nextId = `${nodeId}-copy-${suffix}`;
        suffix += 1;
      }
      void Promise.resolve(onRattishMutation?.([
        { kind: "duplicate_node", node: nodeId, name: nextId },
      ])).then((document) => {
        if (!document) return;
        setSelectedNodeId(nextId);
        setSelectedNodeIds([nextId]);
        setSelectedEdgeId(null);
      });
      setNodeContextMenu(null);
      return `node:${nextId}`;
    }
    const nextWorkflow = duplicateWorkflowNode(workflow, nodeId, { usedAgentIds });
    const duplicatedNode = nextWorkflow.nodes.at(-1);
    if (!duplicatedNode || nextWorkflow === workflow) return null;
    onWorkflowChange(nextWorkflow);
    setSelectedNodeId(duplicatedNode.id);
    setSelectedNodeIds([duplicatedNode.id]);
    setSelectedEdgeId(null);
    setNodeContextMenu(null);
    return `node:${duplicatedNode.id}`;
  }

  function selectOutlineNode(nodeId) {
    setSelectedNodeId(nodeId);
    setSelectedNodeIds([nodeId]);
    setSelectedEdgeId(null);
  }

  function selectOutlineEdge(edgeId) {
    setSelectedNodeId(undefined);
    setSelectedNodeIds([]);
    setSelectedEdgeId(edgeId);
  }

  function focusInspector() {
    setInspectorCollapsed(false);
    const inspector = document.getElementById("workflow-inspector");
    if (inspector) {
      inspector.focus();
      return;
    }
    window.requestAnimationFrame?.(() => document.getElementById("workflow-inspector")?.focus());
  }

  function showWorkflowSettings() {
    setSelectedNodeId(undefined);
    setSelectedNodeIds([]);
    setSelectedEdgeId(null);
    setDraftEdge(null);
    setKeyboardConnectionFrom(null);
    focusInspector();
  }

  function renameNode(nodeId) {
    const node = workflowNodes.find((candidate) => candidate.id === nodeId);
    if (!node) return;
    setNodeContextMenu(null);
    setNodeRenameDialog({
      nodeId,
      label: node.label ?? node.id,
    });
  }

  function confirmRenameNode(nodeId, nextLabel) {
    const trimmedLabel = nextLabel.trim();
    if (!trimmedLabel) {
      setNodeRenameDialog(null);
      return;
    }
    if (rattishMode) {
      void renameRattishNode(nodeId, trimmedLabel);
    } else {
      updateNode(nodeId, { label: trimmedLabel });
      setSelectedNodeId(nodeId);
      setSelectedNodeIds([nodeId]);
    }
    setSelectedEdgeId(null);
    setNodeRenameDialog(null);
  }

  function renameRattishNode(nodeId, nextId) {
    const trimmedId = String(nextId ?? "").trim();
    if (!trimmedId || trimmedId === nodeId) return null;
    const canonicalId = trimmedId.toLowerCase();
    return Promise.resolve(
      onRattishMutation?.([
        { kind: "rename_node", node: nodeId, name: trimmedId },
      ]),
    ).then((document) => {
      if (!document) {
        return null;
      }
      const renamedId =
        document.graph?.nodes?.find(
          (candidate) => candidate.id.toLowerCase() === canonicalId,
        )?.id ?? canonicalId;
      setSelectedNodeId(renamedId);
      setSelectedNodeIds([renamedId]);
      setSelectedEdgeId(null);
      return document;
    });
  }

  function showNodeContextMenu(event, nodeId) {
    event.preventDefault();
    event.stopPropagation();
    setSelectedNodeId(nodeId);
    setSelectedNodeIds([nodeId]);
    setSelectedEdgeId(null);
    setNodeContextMenu({
      nodeId,
      x: event.clientX,
      y: event.clientY,
    });
  }

  function handleNodePointerDown(event, nodeId) {
    if (event.button !== 0) return;
    setNodeContextMenu(null);
    event.currentTarget.setPointerCapture(event.pointerId);
    nodeDragMovedRef.current = false;
    const nextSelection = selectedNodeIds.includes(nodeId) ? selectedNodeIds : [nodeId];
    nodeDragSelectionRef.current = nextSelection;
    setSelectedNodeId(nodeId);
    setSelectedNodeIds(nextSelection);
    setSelectedEdgeId(null);
    setInspectorCollapsed(false);
    setDraggingNodeId(nodeId);
  }

  function handleNodePointerMove(event, nodeId) {
    if (draggingNodeId !== nodeId) return;
    if (Math.abs(event.movementX) > 1 || Math.abs(event.movementY) > 1) {
      nodeDragMovedRef.current = true;
    }
    const movingNodeIds = nodeDragSelectionRef.current.length
      ? nodeDragSelectionRef.current
      : [nodeId];
    const movingSet = new Set(movingNodeIds);
    const dx = event.movementX / viewport.scale;
    const dy = event.movementY / viewport.scale;

    onWorkflowChange({
      ...workflow,
      nodes: workflowNodes.map((node) =>
        movingSet.has(node.id)
          ? {
              ...node,
              x: (node.x ?? 0) + dx,
              y: (node.y ?? 0) + dy,
            }
          : node,
      ),
    });
  }

  function handleNodePointerUp(event, nodeId) {
    if (draftEdge && nodeId) {
      event.preventDefault();
      event.stopPropagation();
      addEdge(draftEdge.from, nodeId, "always");
      setDraftEdge(null);
      return;
    }
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDraggingNodeId(null);
    nodeDragSelectionRef.current = [];
  }

  async function handleNodeDoubleClick(node) {
    if (nodeDragMovedRef.current) {
      nodeDragMovedRef.current = false;
      return;
    }

    if (node.type === "file") {
      const path = node.operation?.path;
      if (path) {
        setFilePreviewPath(resolveDisplayPath(path, dataDir));
      }
      return;
    }

    if (node.type === "folder") {
      const folderPath = node.operation?.path;
      if (!folderPath) {
        setFolderNodeEntries((current) => ({
          ...current,
          [node.id]: {
            loaded: true,
            error: "Choose an absolute folder path.",
            entries: [],
          },
        }));
        setExpandedFolderNodes((current) => ({
          ...current,
          [node.id]: true,
        }));
        return;
      }
      const nextExpanded = !expandedFolderNodes[node.id];
      setExpandedFolderNodes((current) => ({
        ...current,
        [node.id]: nextExpanded,
      }));
      if (nextExpanded && !folderNodeEntries[node.id]?.loaded) {
        try {
          const listing = await window.goferDesktop?.workspace?.listDirectory?.({
            currentPath: folderPath,
            create: false,
          });
          setFolderNodeEntries((current) => ({
            ...current,
            [node.id]: {
              loaded: true,
              entries: listing?.entries ?? [],
            },
          }));
        } catch (error) {
          setFolderNodeEntries((current) => ({
            ...current,
            [node.id]: {
              loaded: true,
              error: error instanceof Error ? error.message : "Unable to read folder",
              entries: [],
            },
          }));
        }
      }
    }
  }

  function handleCanvasPointerDown(event) {
    if (draftEdge) return;
    setNodeContextMenu(null);
    if (event.button === 0) {
      event.preventDefault();
      event.currentTarget.focus({ preventScroll: true });
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return;
      const start = {
        x: (event.clientX - rect.left - viewport.x) / viewport.scale,
        y: (event.clientY - rect.top - viewport.y) / viewport.scale,
      };
      event.currentTarget.setPointerCapture(event.pointerId);
      setSelectedNodeId(undefined);
      setSelectedNodeIds([]);
      setSelectedEdgeId(null);
      setSelectionBox({
        pointerId: event.pointerId,
        start,
        current: start,
      });
      return;
    }

    if (event.button === 2) {
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      setPanningPointerId(event.pointerId);
    }
  }

  function handleCanvasPointerMove(event) {
    if (draftEdge) {
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return;
      setDraftEdge((current) =>
        current
          ? {
              ...current,
              to: {
                x: (event.clientX - rect.left - viewport.x) / viewport.scale,
                y: (event.clientY - rect.top - viewport.y) / viewport.scale,
              },
            }
          : current,
      );
      return;
    }
    if (selectionBox?.pointerId === event.pointerId) {
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return;
      event.preventDefault();
      setSelectionBox((current) =>
        current
          ? {
              ...current,
              current: {
                x: (event.clientX - rect.left - viewport.x) / viewport.scale,
                y: (event.clientY - rect.top - viewport.y) / viewport.scale,
              },
            }
          : current,
      );
      return;
    }
    if (panningPointerId !== event.pointerId) return;
    event.preventDefault();
    setViewport((current) => ({
      ...current,
      x: current.x + event.movementX,
      y: current.y + event.movementY,
    }));
  }

  function handleCanvasPointerUp(event) {
    if (draftEdge) {
      setDraftEdge(null);
      return;
    }
    if (selectionBox?.pointerId === event.pointerId) {
      event.preventDefault();
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      const box = normalizedSelectionBox(selectionBox);
      const selectedIds = selectionBoxArea(box) > 9
        ? workflowNodes
            .filter((node) => nodeIntersectsBox(node, box))
            .map((node) => node.id)
        : [];
      setSelectedNodeIds(selectedIds);
      setSelectedNodeId(selectedIds.at(-1));
      setSelectedEdgeId(null);
      setSelectionBox(null);
      return;
    }
    if (panningPointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setPanningPointerId(null);
  }

  function handleConnectorPointerDown(event, nodeId) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const fromNode = nodesById[nodeId];
    const start = {
      x: fromNode.x + nodeWidth,
      y: fromNode.y + nodeHeight / 2,
    };
    setSelectedNodeId(undefined);
    setSelectedNodeIds([]);
    setSelectedEdgeId(null);
    setDraftEdge({
      from: nodeId,
      start,
      to: {
        x: (event.clientX - rect.left - viewport.x) / viewport.scale,
        y: (event.clientY - rect.top - viewport.y) / viewport.scale,
      },
    });
  }

  function handleConnectorPointerUp(event, nodeId) {
    if (!draftEdge || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    if (draftEdge.from && nodeId) {
      addEdge(draftEdge.from, nodeId, "always");
    }
    setDraftEdge(null);
  }

  function handleCanvasWheel(event) {
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    const pointerX = event.clientX - rect.left;
    const pointerY = event.clientY - rect.top;
    const zoomMultiplier = event.deltaY < 0 ? 1.08 : 0.92;
    zoomViewportAtPoint(pointerX, pointerY, zoomMultiplier);
  }

  function zoomViewportAtPoint(pointerX, pointerY, zoomMultiplier) {
    setViewport((current) => {
      const nextScale = clamp(current.scale * zoomMultiplier, minZoom, maxZoom);
      const contentX = (pointerX - current.x) / current.scale;
      const contentY = (pointerY - current.y) / current.scale;
      return {
        scale: nextScale,
        x: pointerX - contentX * nextScale,
        y: pointerY - contentY * nextScale,
      };
    });
  }

  function viewportFromMinimapPointer(event) {
    const rect = event.currentTarget.getBoundingClientRect();
    const bounds = graphBounds(workflowNodes, 160);
    const { x: worldX, y: worldY } = minimapPointToWorld(event, rect, bounds);
    const size = canvasViewportSize();
    setViewport((current) => ({
      ...current,
      x: size.width / 2 - worldX * current.scale,
      y: size.height / 2 - worldY * current.scale,
    }));
  }

  function handleMinimapPointerDown(event) {
    if (event.button !== 0 || !workflowNodes.length) return;
    event.preventDefault();
    event.stopPropagation();
    setMinimapDragging(true);
    viewportFromMinimapPointer(event);
  }

  function handleMinimapPointerMove(event) {
    if (!minimapDragging) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const withinMinimap =
      event.clientX >= rect.left &&
      event.clientX <= rect.right &&
      event.clientY >= rect.top &&
      event.clientY <= rect.bottom;
    if (!withinMinimap) {
      setMinimapDragging(false);
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    viewportFromMinimapPointer(event);
  }

  function handleMinimapPointerUp(event) {
    event.stopPropagation();
    setMinimapDragging(false);
  }

  function handleMinimapWheel(event) {
    event.preventDefault();
    event.stopPropagation();
    const size = canvasViewportSize();
    const zoomMultiplier = event.deltaY < 0 ? 1.08 : 0.92;
    zoomViewportAtPoint(size.width / 2, size.height / 2, zoomMultiplier);
  }

  function addEdge(
    fromNodeId,
    toNodeId,
    condition,
    outputPattern = null,
    field = null,
    operator = null,
    value = null,
  ) {
    if (!fromNodeId || !toNodeId) return null;

    const nextCondition = condition || "always";
    const nextOutputPattern = nextCondition === "output_matches" ? outputPattern || "" : null;
    const nextEdgeId = uniqueEdgeId(workflowEdges, fromNodeId, toNodeId);
    if (rattishMode) {
      const routes = [
        ...workflowEdges
          .filter((edge) => edge.from === fromNodeId)
          .map((edge) => rattishRouteValue(edge, rattishDocument?.source)),
        toNodeId,
      ];
      void onRattishMutation?.([{ kind: "set_routes", node: fromNodeId, routes }]);
      setSelectedNodeId(undefined);
      setSelectedNodeIds([]);
      setSelectedEdgeId(nextEdgeId);
      setKeyboardConnectionFrom(null);
      return `edge:${nextEdgeId}`;
    }
    onWorkflowChange({
      ...workflow,
      edges: [
        ...workflowEdges,
        {
          id: nextEdgeId,
          from: fromNodeId,
          to: toNodeId,
          label: edgeLabel(
            nextCondition,
            nextOutputPattern,
            field,
            operator,
            value,
          ),
          condition: nextCondition,
          outputPattern: nextOutputPattern,
          field: nextCondition === "output_field" ? field || "" : null,
          operator: nextCondition === "output_field" ? operator || "equals" : null,
          value: nextCondition === "output_field" ? value : null,
        },
      ],
    });
    setSelectedNodeId(undefined);
    setSelectedNodeIds([]);
    setSelectedEdgeId(nextEdgeId);
    setKeyboardConnectionFrom(null);
    return `edge:${nextEdgeId}`;
  }

  function updateEdge(edgeId, patch) {
    if (rattishMode) {
      const edge = workflowEdges.find((candidate) => candidate.id === edgeId);
      if (!edge) return;
      const nextEdge = { ...edge, ...patch };
      const routes = workflowEdges
        .filter((candidate) => candidate.from === edge.from)
        .map((candidate) =>
          rattishRouteValue(candidate.id === edgeId ? nextEdge : candidate, rattishDocument?.source),
        );
      void onRattishMutation?.([{ kind: "set_routes", node: edge.from, routes }]);
      return;
    }
    onWorkflowChange({
      ...workflow,
      edges: workflowEdges.map((edge) => {
        if (edge.id !== edgeId) return edge;
        const nextEdge = { ...edge, ...patch };
        return {
          ...nextEdge,
          label: edgeLabel(
            nextEdge.condition,
            nextEdge.outputPattern,
            nextEdge.field,
            nextEdge.operator,
            nextEdge.value,
          ),
        };
      }),
    });
  }

  function startInspectorResize(event) {
    event.preventDefault();
    event.stopPropagation();

    const startX = event.clientX;
    const startWidth = inspectorWidth;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    let nextWidth = startWidth;

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    function handlePointerMove(moveEvent) {
      nextWidth = clamp(startWidth - (moveEvent.clientX - startX), 280, 520);
      setInspectorWidth(nextWidth);
    }

    function handlePointerUp() {
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      onSettingChange?.("layout.graphInspectorWidth", nextWidth);
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
  }

  function handleInspectorResizeKeyDown(event) {
    const nextWidth = resizerValueForKey(event.key, inspectorWidth, {
      defaultValue: DEFAULT_APP_SETTINGS.layout.graphInspectorWidth,
      max: 520,
      min: 280,
      shiftKey: event.shiftKey,
    });
    if (nextWidth === null) return;
    event.preventDefault();
    setInspectorWidth(nextWidth);
    onSettingChange?.("layout.graphInspectorWidth", nextWidth);
  }

  return (
    <div
      className={`flex min-h-0 flex-1 flex-col ${
        graphFullscreen ? "fixed inset-0 z-[100] bg-white" : ""
      }`}
      data-graph-fullscreen={graphFullscreen || undefined}
    >
      <div className="relative flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
        <GraphToolbarPortal fullscreen={graphFullscreen} target={toolbarTarget}>
          <div
            className={`relative z-[60] flex shrink-0 items-center gap-2 overflow-visible ${
              graphFullscreen ? "border-b border-line bg-white px-4 py-2" : ""
            }`}
            data-toolbar="graph-editor"
          >
          <div
            className="flex min-w-0 items-center gap-2 overflow-visible"
            data-toolbar-row="primary"
          >
            <input
              ref={importInputRef}
              accept={rattishMode ? brandCompat.BUNDLE_ACCEPT : ".toml,.zip,.gof"}
              className="hidden"
              type="file"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) {
                  onImportWorkflow(file);
                }
                event.target.value = "";
              }}
            />
            <button
              className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink disabled:cursor-not-allowed disabled:opacity-60"
              disabled={runDisabled}
              title={runTitle}
              type="button"
              onClick={() => onRunWorkflow(workflow)}
            >
              <Play size={17} />
            </button>
            <button
              className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
              disabled={stopDisabled ?? (invalidWorkflow || !workflowHasRunningRuns || Boolean(runState?.stopping))}
              title={stopTitle}
              type="button"
              onClick={() => onStopWorkflow(workflow)}
            >
              <Square size={15} fill="currentColor" strokeWidth={1.7} />
            </button>
            <div className="relative shrink-0">
              <button
                className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                disabled={invalidWorkflow}
                title="Validate workflow"
                type="button"
                onClick={onValidateWorkflow}
              >
                <Check size={17} />
              </button>
              {notice?.message ? (
                <div
                  aria-atomic="true"
                  aria-live={notice.type === "error" ? "assertive" : "polite"}
                  className={`validation-pop absolute right-0 top-10 z-40 w-64 max-w-[calc(100vw-2rem)] rounded-lg border px-3 py-2 text-sm font-medium shadow-panel ${
                    notice.type === "error"
                      ? "border-red-200 bg-red-50 text-red-700"
                      : "border-emerald-200 bg-emerald-50 text-emerald-700"
                  }`}
                  role={notice.type === "error" ? "alert" : "status"}
                >
                  {notice.message}
                </div>
              ) : null}
            </div>
          </div>
          <span aria-hidden="true" className="h-5 w-px shrink-0 bg-line" />
          <div
            className="flex min-w-0 flex-1 items-center gap-2 overflow-visible [&>button]:shrink-0"
            data-toolbar-row="secondary"
          >
            <button
              className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
              disabled={editingDisabled}
              title="Add node"
              type="button"
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  addNode({ detail: 0 });
                }
              }}
              onClick={addNode}
            >
              <Plus size={17} />
            </button>
            <button
              className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink"
              disabled={readOnly}
              title={readOnly ? "Rattish graph positions are managed in workflow metadata" : "Auto-layout graph"}
              type="button"
              onClick={applyAutoLayout}
            >
              <Route size={17} />
            </button>
            <button
              className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink"
              title="Fit graph"
              type="button"
              onClick={fitGraph}
            >
              <Maximize2 size={16} />
            </button>
            <button
              className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
              disabled={!selectedNodeIds.length}
              title="Fit selection"
              type="button"
              onClick={fitSelection}
            >
              <LocateFixed size={17} />
            </button>
            <button
              className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink"
              title="Zoom out"
              type="button"
              onClick={() => zoomViewport(0.88)}
            >
              <ZoomOut size={17} />
            </button>
            <button
              className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink"
              title="Zoom in"
              type="button"
              onClick={() => zoomViewport(1.14)}
            >
              <ZoomIn size={17} />
            </button>
            <button
              className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
              disabled={editingDisabled || !selectedNode}
              title="Delete selected node"
              type="button"
              onClick={deleteSelectedNode}
            >
              <Trash2 size={17} />
            </button>
            <button
              className="hidden"
              title="Import workflow TOML or bundle"
              type="button"
              onClick={() => importInputRef.current?.click()}
            >
              <Upload size={17} />
            </button>
            <button
              className="hidden"
              disabled={editingDisabled}
              title="Export workflow bundle"
              type="button"
              onClick={onExportWorkflow}
            >
              <Download size={17} />
            </button>
            {rattishMode ? (
              <div className="flex items-center rounded-lg border border-line bg-white p-0.5">
                <button
                  className="inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-slate-600 transition hover:bg-slate-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600"
                  title="Import a .raticode workflow"
                  type="button"
                  onClick={() => importInputRef.current?.click()}
                >
                  <Upload aria-hidden="true" size={14} />
                  Import
                </button>
                <span aria-hidden="true" className="h-4 w-px bg-line" />
                <button
                  className="inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-slate-600 transition hover:bg-slate-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={editingDisabled}
                  title="Export this workflow as a .raticode bundle"
                  type="button"
                  onClick={onExportWorkflow}
                >
                  <Download aria-hidden="true" size={14} />
                  Export
                </button>
              </div>
            ) : null}
            <details ref={graphActionsRef} className="group/tools relative shrink-0">
              <summary
                className="grid h-8 w-8 list-none place-items-center rounded-lg border border-line bg-white text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink [&::-webkit-details-marker]:hidden"
                title="More graph actions"
              >
                <MoreHorizontal size={17} />
              </summary>
              <div className="absolute right-0 top-10 z-[70] w-52 rounded-[10px] border border-line bg-white p-1 shadow-panel">
                <button
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-ink hover:bg-slate-50"
                  type="button"
                  onClick={() => {
                    graphActionsRef.current?.removeAttribute("open");
                    showWorkflowSettings();
                  }}
                >
                  <Settings2 size={14} /> Workflow settings
                </button>
                <div className="my-1 border-t border-line" role="separator" />
                <ToolbarRunSelector
                  menu
                  open={runMenuOpen}
                  runs={logState?.runs ?? []}
                  selectedRunId={logState?.selectedRunId}
                  onOpenChange={setRunMenuOpen}
                  onSelectRun={onSelectRunLog}
                  onShowLatest={onLoadLatestLog}
                  onReplayRun={onReplayRunLog}
                  onResumeRun={onResumeRunLog}
                  onStopRun={onStopRunLog}
                  selectedNodeId={selectedNodeId}
                />
                {!rattishMode ? (
                  <>
                    <button className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-ink hover:bg-slate-50" type="button" onClick={() => importInputRef.current?.click()}>
                      <Upload size={14} /> Import legacy workflow
                    </button>
                    <button className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-ink hover:bg-slate-50 disabled:opacity-40" disabled={invalidWorkflow} type="button" onClick={onExportWorkflow}>
                      <Download size={14} /> Export legacy workflow
                    </button>
                  </>
                ) : null}
              </div>
            </details>
          </div>
          </div>
        </GraphToolbarPortal>

        <div className="relative isolate flex min-h-0 flex-1" data-graph-canvas-region="true">
        <div
          inert={canvasOverlay ? "" : undefined}
          className={`flex min-h-0 min-w-0 flex-1 ${canvasOverlay ? "pointer-events-none opacity-30 grayscale" : ""}`}
        >
        <div
          ref={canvasRef}
          aria-label="Workflow graph visualization"
          className={`relative min-h-0 flex-1 overflow-hidden bg-[#f9fbfd] bg-[radial-gradient(circle_at_1px_1px,#d5dee8_1px,transparent_0)] [touch-action:none] ${
            panningPointerId !== null ? "cursor-grabbing" : "cursor-default"
          }`}
          data-graph-visualization="true"
          tabIndex={0}
          style={{
            backgroundPosition: `${viewport.x}px ${viewport.y}px`,
            backgroundSize: "22px 22px",
          }}
          onContextMenu={(event) => event.preventDefault()}
          onPointerDown={handleCanvasPointerDown}
          onPointerMove={handleCanvasPointerMove}
          onPointerUp={handleCanvasPointerUp}
          onPointerCancel={handleCanvasPointerUp}
          onDragOver={(event) => {
            if (!invalidWorkflow) {
              event.preventDefault();
            }
          }}
          onDrop={handleCanvasDrop}
          onWheel={handleCanvasWheel}
        >
          <WorkflowTriggerStrip
            dataDir={dataDir}
            runContinuously={workflow.runContinuously}
            schedule={workflow.schedule}
            webhooks={workflow.webhooks}
            watch={workflow.watch}
          />
          {invalidWorkflow ? (
            <InvalidWorkflowCanvas workflow={workflow} />
          ) : (
            <div
              className="absolute left-0 top-0 h-0 w-0 origin-top-left overflow-visible"
              style={{
                transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.scale})`,
              }}
            >
              <svg
                className="absolute overflow-visible"
                aria-hidden="true"
                style={{
                  left: -graphWorldOffset,
                  top: -graphWorldOffset,
                  width: graphWorldSize,
                  height: graphWorldSize,
                  pointerEvents: "none",
                }}
                viewBox={`0 0 ${graphWorldSize} ${graphWorldSize}`}
              >
                <defs>
                  <marker
                    id="arrowhead"
                    markerHeight="10"
                    markerWidth="10"
                    orient="auto"
                    refX="8"
                    refY="3"
                  >
                    <path d="M0,0 L0,6 L9,3 z" fill="#718096" />
                  </marker>
                </defs>
                <g transform={`translate(${graphWorldOffset} ${graphWorldOffset})`}>
                  {workflowEdges.map((edge) => {
                    const from = nodesById[edge.from];
                    const to = nodesById[edge.to];
                    if (!from || !to) return null;

                    const reciprocal = workflowEdges.some(
                      (candidate) =>
                        candidate.id !== edge.id &&
                        candidate.from === edge.to &&
                        candidate.to === edge.from,
                    );
                    const laneOffset = reciprocal
                      ? stableEdgeDirection(edge.from, edge.to) * 44
                      : 0;
                    const geometry = edgeGeometry(from, to, edge.from === edge.to, laneOffset);

                    return (
                      <g key={edge.id}>
                        <path
                          d={geometry.path}
                          fill="none"
                          stroke="transparent"
                          strokeLinecap="round"
                          strokeWidth="16"
                          style={{ pointerEvents: "stroke" }}
                          onPointerDown={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            setSelectedNodeId(undefined);
                            setSelectedNodeIds([]);
                            setSelectedEdgeId(edge.id);
                            setInspectorCollapsed(false);
                          }}
                        />
                        <path
                          d={geometry.path}
                          fill="none"
                          markerEnd="url(#arrowhead)"
                          stroke={
                            edgeDiagnostics[edge.id]?.some(
                              (diagnostic) => diagnostic.severity === "error",
                            )
                              ? "#dc2626"
                              : selectedEdgeId === edge.id
                                ? "#4f46e5"
                                : "#718096"
                          }
                          strokeLinecap="round"
                          strokeWidth={
                            edgeDiagnostics[edge.id]?.length || selectedEdgeId === edge.id
                              ? "4"
                              : "2.5"
                          }
                          style={{ pointerEvents: "none" }}
                        />
                        <text
                          x={geometry.label.x}
                          y={geometry.label.y}
                          className={`text-[12px] font-medium ${
                            selectedEdgeId === edge.id ? "fill-indigo-700" : "fill-slate-500"
                          }`}
                          style={{ pointerEvents: "none" }}
                          textAnchor="middle"
                        >
                          {edgeLabel(
                            edge.displayLabel ?? edge.condition,
                            edge.outputPattern,
                            edge.field,
                            edge.operator,
                            edge.value,
                          )}
                        </text>
                      </g>
                    );
                  })}
                  {draftEdge ? (
                    <path
                      d={draftEdgePath(draftEdge)}
                      fill="none"
                      markerEnd="url(#arrowhead)"
                      stroke="#4f46e5"
                      strokeDasharray="6 6"
                      strokeLinecap="round"
                      strokeWidth="3"
                      style={{ pointerEvents: "none" }}
                    />
                  ) : null}
                </g>
              </svg>

              {selectionBox ? (
                <SelectionRectangle box={normalizedSelectionBox(selectionBox)} />
              ) : null}

              {workflowNodes.map((node) => (
                <WorkflowNode
                  key={node.id}
                  node={{
                    ...node,
                    meta: nodeMetaFromOperation(node.operation ?? defaultOperation(node.type), dataDir),
                  }}
                  selected={selectedNodeIds.includes(node.id)}
                  status={nodeStatuses[node.id]}
                  zIndex={nodeStackIndex(node.id, {
                    draggingNodeId,
                    selectedNodeId,
                    selectedNodeIds,
                  })}
                  expanded={Boolean(expandedFolderNodes[node.id])}
                  folderEntries={folderNodeEntries[node.id]}
                  diagnostics={nodeDiagnostics[node.id] ?? []}
                  readOnly={readOnly}
                  onDelete={deleteNode}
                  onDoubleClick={handleNodeDoubleClick}
                  onConnectorPointerDown={handleConnectorPointerDown}
                  onConnectorPointerUp={handleConnectorPointerUp}
                  onContextMenu={showNodeContextMenu}
                  onPointerDown={handleNodePointerDown}
                  onPointerMove={handleNodePointerMove}
                  onPointerUp={handleNodePointerUp}
                />
              ))}
            </div>
          )}
          {nodeContextMenu && !readOnly ? (
            <NodeContextMenu
              x={nodeContextMenu.x}
              y={nodeContextMenu.y}
              onDelete={() => deleteNode(nodeContextMenu.nodeId)}
              onDuplicate={() => duplicateNode(nodeContextMenu.nodeId)}
              onRename={() => renameNode(nodeContextMenu.nodeId)}
            />
          ) : null}
          {nodeRenameDialog ? (
            <NodeRenameDialog
              initialLabel={nodeRenameDialog.label}
              onCancel={() => setNodeRenameDialog(null)}
              onRename={(nextLabel) =>
                confirmRenameNode(nodeRenameDialog.nodeId, nextLabel)
              }
            />
          ) : null}
          {pendingTrustPrompt ? (
            <FilesystemTrustPrompt
              parentPath={pendingTrustPrompt.parentPath}
              onCancel={cancelPendingDroppedNodes}
              onConfirm={trustPendingDroppedNodes}
            />
          ) : null}
          {!invalidWorkflow && pendingApproval ? (
            <ApprovalDecisionOverlay
              approval={pendingApproval}
              node={pendingApprovalNode}
              onDecideApproval={onDecideApproval}
            />
          ) : null}
          {!invalidWorkflow ? (
            <div
              className="absolute bottom-4 z-30 flex items-center gap-2 transition-[right] duration-200"
              style={{ right: inspectorCollapsed ? 16 : inspectorWidth + 16 }}
              onPointerDown={(event) => event.stopPropagation()}
            >
              <div className="relative">
                {mapOpen ? (
                  <div className="absolute bottom-10 right-0 w-72 overflow-hidden rounded-[14px] border border-line bg-white shadow-panel">
                  <div className="flex border-b border-line p-1.5">
                    {[
                      ["outline", "Outline"],
                      ["minimap", "Minimap"],
                    ].map(([id, label]) => (
                      <button
                        key={id}
                        className={`h-7 flex-1 rounded-lg text-[11px] font-semibold transition ${
                          mapTab === id ? "bg-indigo-50 text-indigo-700" : "text-muted hover:bg-slate-50 hover:text-ink"
                        }`}
                        type="button"
                        onClick={() => setMapTab(id)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  {mapTab === "outline" ? (
                    <GraphOutline
                      embedded
                      announcement={graphAnnouncement}
                      connectionFrom={keyboardConnectionFrom}
                      edgeDiagnostics={edgeDiagnostics}
                      edges={workflowEdges}
                      focusRequest={outlineFocusRequest}
                      nodeDiagnostics={nodeDiagnostics}
                      nodes={workflowNodes}
                      nodeStatuses={nodeStatuses}
                      selectedEdgeId={selectedEdgeId}
                      selectedNodeId={selectedNodeId}
                      onCancelConnection={() => setKeyboardConnectionFrom(null)}
                      onConnect={(fromNodeId, toNodeId) => addEdge(fromNodeId, toNodeId, "always")}
                      onDeleteEdge={deleteEdge}
                      onDeleteNode={deleteNode}
                      onDuplicateNode={duplicateNode}
                      onOpenEdge={(edgeId) => {
                        selectOutlineEdge(edgeId);
                        focusInspector();
                      }}
                      onOpenNode={(nodeId) => {
                        selectOutlineNode(nodeId);
                        focusInspector();
                      }}
                      onSelectEdge={selectOutlineEdge}
                      onSelectNode={selectOutlineNode}
                      onStartConnection={(nodeId) => {
                        selectOutlineNode(nodeId);
                        setKeyboardConnectionFrom(nodeId);
                      }}
                    />
                  ) : (
                    <div className="h-[174px] w-full p-2">
                      <GraphMinimap
                        embedded
                        nodes={workflowNodes}
                        selectedNodeIds={selectedNodeIds}
                        viewport={viewport}
                        viewportSize={canvasViewportSize()}
                        onPointerDown={handleMinimapPointerDown}
                        onPointerLeave={handleMinimapPointerUp}
                        onPointerMove={handleMinimapPointerMove}
                        onPointerUp={handleMinimapPointerUp}
                        onWheel={handleMinimapWheel}
                      />
                    </div>
                  )}
                  </div>
                ) : null}
                <button
                  aria-expanded={mapOpen}
                  className={`flex h-8 items-center gap-1.5 rounded-[10px] border px-2.5 text-xs font-semibold shadow-panel transition ${
                    mapOpen
                      ? "border-indigo-200 bg-indigo-50 text-indigo-700"
                      : "border-line bg-white text-muted hover:border-slate-300 hover:text-ink"
                  }`}
                  title="Map"
                  type="button"
                  onClick={() => setMapOpen((current) => !current)}
                >
                  <Layers aria-hidden="true" size={14} />
                  Map
                </button>
              </div>
              <button
                aria-label={graphFullscreen ? "Exit graph full screen" : "Open graph full screen"}
                aria-pressed={graphFullscreen}
                className="grid h-8 w-8 place-items-center rounded-[10px] border border-line bg-white text-muted shadow-panel transition hover:border-slate-300 hover:text-ink"
                title={graphFullscreen ? "Exit full screen" : "Enter full screen"}
                type="button"
                onClick={() => setGraphFullscreen((current) => !current)}
              >
                {graphFullscreen ? (
                  <Minimize2 aria-hidden="true" size={14} />
                ) : (
                  <Maximize2 aria-hidden="true" size={14} />
                )}
              </button>
            </div>
          ) : null}
        </div>
        </div>
        {canvasOverlay ? (
          <div className="absolute inset-0 z-40 grid place-items-center overflow-auto bg-slate-100/60 p-4 dark:bg-zinc-900/60" data-graph-error-overlay="true">
            {canvasOverlay}
          </div>
        ) : null}
        </div>
      </div>

        {!canvasOverlay && !invalidWorkflow && rattishMode ? (
          <RattishInspector
            collapsed={inspectorCollapsed}
            document={rattishDocument}
            edge={selectedEdge}
            node={selectedNode}
            nodeOutput={selectedNodeOutput}
            nodeRun={selectedRunNode}
            runEvents={runEvents}
            width={inspectorWidth}
            onMutate={onRattishMutation}
            onRenameNode={renameRattishNode}
            onResizeStart={startInspectorResize}
            onResizeKeyDown={handleInspectorResizeKeyDown}
            onToggleCollapsed={() => setInspectorCollapsed((current) => !current)}
          />
        ) : !canvasOverlay && !invalidWorkflow && !readOnly ? (
          <Inspector
            agents={workflow.agents ?? {}}
            approval={selectedApproval}
            edges={workflowEdges}
            collapsed={inspectorCollapsed}
            edge={selectedEdge}
            node={selectedNode}
            nodeRun={selectedRunNode}
            nodeOutput={selectedNodeOutput}
            nodes={workflowNodes}
            providerProfiles={providerProfiles}
            providerCapabilities={providerCapabilities}
            workflow={workflow}
            dataDir={dataDir}
            width={inspectorWidth}
            onAddEdge={addEdge}
            onDeleteEdge={deleteEdge}
            onAgentChange={updateAgentConfig}
            onDecideApproval={onDecideApproval}
            onEdgeChange={updateEdge}
            onResizeStart={startInspectorResize}
            onResizeKeyDown={handleInspectorResizeKeyDown}
            onNodeChange={(patch) => updateNode(selectedNode.id, patch)}
            onOperationChange={(patch) => updateNodeOperation(selectedNode.id, patch)}
            onProviderProfilesChange={setProviderProfiles}
            onProviderCapabilitiesRefresh={refreshProviderCapabilities}
            onSettingsChange={(patch) => updateNodeSettings(selectedNode.id, patch)}
            onToggleCollapsed={() => setInspectorCollapsed((current) => !current)}
            onTypeChange={(type) => updateNodeType(selectedNode.id, type)}
            onWorkflowChange={(patch) => onWorkflowChange({ ...workflow, ...patch })}
            onApplyFix={applyValidationFix}
          />
        ) : null}
      </div>
      {filePreviewPath ? (
        <TextFileDialog
          mode="preview"
          path={filePreviewPath}
          onClose={() => setFilePreviewPath(null)}
        />
      ) : null}
    </div>
  );
}

function GraphToolbarPortal({ children, fullscreen, target }) {
  if (fullscreen || !target) return children;
  return createPortal(children, target);
}

function InvalidWorkflowCanvas({ workflow }) {
  const [copied, setCopied] = useState(false);
  const sourcePath = workflow.sourcePath || `${workflow.id}.toml`;
  const message =
    workflow.validationError ||
    workflow.description ||
    "The workflow TOML could not be parsed or validated.";
  const markdown = [
    "# Raticode workflow TOML validation error",
    "",
    `Workflow file: \`${sourcePath}\``,
    "",
    "```text",
    message,
    "```",
    "",
  ].join("\n");

  async function copyMarkdown() {
    try {
      await navigator.clipboard?.writeText(markdown);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  async function openSourcePath() {
    if (!sourcePath) return;
    try {
      await window.goferDesktop?.workspace?.revealPath?.(sourcePath);
    } catch (error) {
      console.error("Failed to reveal workflow source path", error);
    }
  }

  return (
    <div className="absolute inset-0 z-10 grid place-items-center p-6">
      <section className="flex max-h-[72%] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-red-200 bg-red-50/95 shadow-panel backdrop-blur dark:border-red-950 dark:bg-[#241b1b]/95">
        <div className="flex items-start justify-between gap-4 border-b border-red-200 px-4 py-3 dark:border-red-950">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-red-800 dark:text-red-200">
              Invalid workflow TOML
            </h3>
            <button
              className="mt-1 block max-w-full truncate text-left text-xs text-red-700/80 underline-offset-2 transition hover:text-red-800 hover:underline dark:text-red-200/70 dark:hover:text-red-100"
              title={sourcePath}
              type="button"
              onClick={openSourcePath}
            >
              {sourcePath}
            </button>
          </div>
          <button
            className="inline-flex shrink-0 items-center gap-2 rounded-md border border-red-200 bg-white px-2.5 py-1.5 text-xs font-medium text-red-700 transition hover:bg-red-100 dark:border-red-900 dark:bg-[#2b2222] dark:text-red-200 dark:hover:bg-[#362828]"
            type="button"
            onClick={copyMarkdown}
          >
            <Copy size={14} />
            {copied ? "Copied" : "Copy markdown"}
          </button>
        </div>
        <div className="workflow-scrollbar min-h-0 overflow-y-auto px-4 py-3">
          <pre className="whitespace-pre-wrap font-mono text-xs leading-5 text-red-900 dark:text-red-100">
            {message}
          </pre>
        </div>
      </section>
    </div>
  );
}

function WorkflowTriggerStrip({ dataDir, runContinuously, schedule, webhooks, watch }) {
  const enabledWebhooks = Object.entries(webhooks ?? {}).filter(([, config]) => config?.enabled);
  if (!runContinuously && !schedule && !watch && !enabledWebhooks.length) return null;
  const watchPath = watch?.path ? resolveDisplayPath(watch.path, dataDir) : "";

  return (
    <div className="pointer-events-none absolute left-5 top-5 z-20 flex max-w-[calc(100%-40px)] flex-wrap gap-2">
      {runContinuously ? (
        <div className="inline-flex items-center gap-2 rounded-lg border border-line bg-white/90 px-3 py-2 text-xs font-medium text-ink shadow-sm backdrop-blur dark:bg-[#252526]/95">
          <RefreshCw size={14} className="text-teal-600" />
          <span className="truncate">Runs continuously</span>
        </div>
      ) : null}
      {!runContinuously && schedule ? (
        <div className="inline-flex items-center gap-2 rounded-lg border border-line bg-white/90 px-3 py-2 text-xs font-medium text-ink shadow-sm backdrop-blur dark:bg-[#252526]/95">
          <CalendarDays size={14} className="text-teal-600" />
          <span className="truncate">
            Starts on schedule: {schedule.cron_expression}
          </span>
        </div>
      ) : null}
      {!runContinuously && watch ? (
        <div className="inline-flex items-center gap-2 rounded-lg border border-line bg-white/90 px-3 py-2 text-xs font-medium text-ink shadow-sm backdrop-blur dark:bg-[#252526]/95">
          <FolderOpen size={14} className="text-teal-600" />
          <span className="truncate">
            Starts when files change: {watchPath}{watch.glob ? `/${watch.glob}` : ""}
            {watch.mode ? ` (${watch.mode})` : ""}
          </span>
        </div>
      ) : null}
      {enabledWebhooks.map(([triggerId, config]) => (
        <div
          key={triggerId}
          className="inline-flex items-center gap-2 rounded-lg border border-line bg-white/90 px-3 py-2 text-xs font-medium text-ink shadow-sm backdrop-blur dark:bg-[#252526]/95"
        >
          <Webhook size={14} className="text-teal-600" />
          <span className="truncate">
            API trigger: {triggerId}
            {config.source ? ` (${config.source})` : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

function GraphMinimap({
  embedded = false,
  nodes,
  onPointerDown,
  onPointerLeave,
  onPointerMove,
  onPointerUp,
  onWheel,
  selectedNodeIds,
  viewport,
  viewportSize,
}) {
  const fallbackSize = embedded
    ? { width: 252, height: 156 }
    : { width: minimapWidth, height: minimapHeight };
  const surfaceRef = useRef(null);
  const [surfaceSize, setSurfaceSize] = useState(fallbackSize);

  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return undefined;

    const measure = () => {
      const rect = surface.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      setSurfaceSize((current) =>
        current.width === rect.width && current.height === rect.height
          ? current
          : { width: rect.width, height: rect.height },
      );
    };

    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(surface);
    return () => observer.disconnect();
  }, [embedded]);

  if (!nodes.length) return null;
  const bounds = graphBounds(nodes, 160);
  const mapWidth = surfaceSize.width;
  const mapHeight = surfaceSize.height;
  const scale = Math.min(
    mapWidth / Math.max(1, bounds.width),
    mapHeight / Math.max(1, bounds.height),
  );
  const viewportWorld = {
    left: -viewport.x / viewport.scale,
    top: -viewport.y / viewport.scale,
    width: viewportSize.width / viewport.scale,
    height: viewportSize.height / viewport.scale,
  };
  const selectedSet = new Set(selectedNodeIds);
  const toMinimapRect = (rect) => ({
    left: (rect.left - bounds.left) * scale,
    top: (rect.top - bounds.top) * scale,
    width: rect.width * scale,
    height: rect.height * scale,
  });
  const viewportRect = toMinimapRect(viewportWorld);

  return (
    <div
      className={embedded ? "h-full w-full rounded-lg bg-slate-50" : "absolute left-4 top-4 z-20 rounded-lg border border-line bg-white/70 p-2 opacity-80 shadow-panel backdrop-blur transition-opacity hover:opacity-100 dark:border-[#3a3a3d] dark:bg-[#252526]/70"}
      title="Minimap"
      onWheel={onWheel}
    >
      <div
        ref={surfaceRef}
        className="relative cursor-crosshair overflow-hidden rounded-md bg-[#edf3f8]/80 dark:bg-[#1b1f22]/80"
        style={{ width: embedded ? "100%" : minimapWidth, height: embedded ? "100%" : minimapHeight }}
        onPointerDown={onPointerDown}
        onPointerLeave={onPointerLeave}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onWheel={onWheel}
      >
        {nodes.map((node) => {
          const rect = toMinimapRect({
            left: node.x ?? 0,
            top: node.y ?? 0,
            width: nodeWidth,
            height: nodeHeight,
          });
          return (
            <div
              key={node.id}
              className={`absolute rounded-sm ${
                selectedSet.has(node.id) ? "bg-indigo-600 dark:bg-indigo-400" : "bg-slate-500 dark:bg-slate-500"
              }`}
              style={{
                left: rect.left,
                top: rect.top,
                width: Math.max(3, rect.width),
                height: Math.max(3, rect.height),
              }}
            />
          );
        })}
        <div
          className="absolute rounded-sm border-2 border-indigo-700 bg-indigo-500/10 dark:border-indigo-300 dark:bg-indigo-300/15"
          style={{
            left: viewportRect.left,
            top: viewportRect.top,
            width: Math.max(8, viewportRect.width),
            height: Math.max(8, viewportRect.height),
          }}
        />
      </div>
    </div>
  );
}

function formatWorkflowRunLog(result) {
  if (!result) return "";

  const lines = [
    `Workflow ${result.workflowId} ${result.success ? "completed successfully" : "failed"}`,
    `Duration: ${Number(result.durationSeconds ?? 0).toFixed(2)}s`,
  ];

  for (const [nodeId, output] of Object.entries(result.nodeOutputs ?? {})) {
    const status = output.success ? "success" : "failed";
    lines.push("");
    lines.push(`${nodeId} - ${status} - exit ${output.exitCode ?? 0}`);
    if (output.output) {
      lines.push(output.output);
    }
    for (const fanOutput of output.fanOutputs ?? []) {
      lines.push("");
      lines.push(`${fanOutput.label}:`);
      lines.push(fanOutput.output);
    }
  }

  return lines.join("\n").trim();
}

function getNodeStatuses(nodes, runResult, logText, runNodes = {}, runEvents = []) {
  const statuses = {};

  for (const [nodeId, nodeRun] of Object.entries(runNodes ?? {})) {
    const status = normalizeRunStatus(nodeRun?.status);
    if (status) {
      statuses[nodeId] = status;
    }
  }

  for (const event of runEvents ?? []) {
    const status = normalizeRunStatus(event?.status);
    if (event?.nodeId && event.nodeId !== "workflow" && status) {
      statuses[event.nodeId] = status;
    }
  }

  if (runResult?.nodeOutputs) {
    for (const [nodeId, output] of Object.entries(runResult.nodeOutputs)) {
      if (statuses[nodeId]) continue;
      if (output.skipped) {
        statuses[nodeId] = "skipped";
      } else {
        statuses[nodeId] = output.success ? "success" : "error";
      }
    }
  }

  for (const node of nodes) {
    if (statuses[node.id]) continue;
    const logStatus = getNodeStatusFromLog(logText, node.id);
    if (logStatus) {
      statuses[node.id] = logStatus;
    }
  }

  return statuses;
}

function normalizeRunStatus(status) {
  if (["queued", "started", "retried"].includes(status)) return status;
  if (status === "completed") return "success";
  if (status === "failed") return "error";
  if (status === "stopped") return "stopped";
  if (status === "skipped") return "skipped";
  if (status === "reused") return "reused";
  return null;
}

function getNodeStatusFromLog(logText, nodeId) {
  if (!logText || !nodeId) return null;
  const nodePrefix = ` - NODE - ${nodeId} - `;
  const events = [];

  for (const [lineIndex, line] of logText.split("\n").entries()) {
    const prefixIndex = line.indexOf(nodePrefix);
    if (prefixIndex === -1) continue;

    const message = line.slice(prefixIndex + nodePrefix.length).trim();
    if (message === "skipped") {
      events.push({ index: lineIndex, status: "skipped" });
      continue;
    }

    if (/(?:run \d+ )?attempt \d+ started/i.test(message)) {
      events.push({ index: lineIndex, status: "running" });
      continue;
    }

    const finishedMatch = message.match(
      /(?:run \d+ )?attempt \d+ finished success=(true|false)/i,
    );
    if (finishedMatch) {
      events.push({
        index: lineIndex,
        status: finishedMatch[1].toLowerCase() === "true" ? "success" : "error",
      });
    }
  }

  return events.at(-1)?.status ?? null;
}

function ToolbarRunSelector({
  menu = false,
  onOpenChange,
  onReplayRun,
  onResumeRun,
  onSelectRun,
  onShowLatest,
  onStopRun,
  open,
  runs,
  selectedRunId,
  selectedNodeId,
}) {
  const menuRef = useRef(null);
  const selectedRun = runs.find((run) => run.id === selectedRunId);
  const activeRun = selectedRun ?? runs[0];
  const label = selectedRun ? formatRunLabel(selectedRun) : "Latest run";
  const status = activeRun?.status ?? "idle";

  useEffect(() => {
    if (!open) return undefined;

    function handlePointerDown(event) {
      if (!menuRef.current?.contains(event.target)) {
        onOpenChange(false);
      }
    }

    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [onOpenChange, open]);

  return (
    <div ref={menuRef} className="relative shrink-0">
      <button
        className={`${
          menu
            ? "flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-ink hover:bg-slate-50"
            : `flex h-8 max-w-[10rem] items-center gap-2 rounded-lg border px-2 text-xs font-medium transition ${
                open
                  ? "border-slate-300 bg-white text-ink"
                  : "border-line bg-white text-muted hover:border-slate-300 hover:bg-slate-50 hover:text-ink"
              }`
        }`}
        title="Select workflow run"
        type="button"
        onClick={() => onOpenChange(!open)}
      >
        <RunStatusDot status={status} />
        <span className={`${menu ? "min-w-0 flex-1 text-left" : "max-w-[112px]"} truncate`}>
          {label}
        </span>
        {menu ? (
          <ChevronRight size={14} className="shrink-0 text-muted" />
        ) : (
          <ChevronDown size={14} className="shrink-0" />
        )}
      </button>
      {open ? (
        <div className={`absolute z-[80] w-[300px] overflow-hidden rounded-lg border border-line bg-white shadow-panel ${menu ? "right-full top-0 mr-1" : "left-0 top-9"}`}>
          <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
            <span className="truncate text-xs font-semibold text-ink">
              {selectedRun ? formatRunLabel(selectedRun) : "Latest run"}
            </span>
            {selectedRun?.status === "running" ? (
              <button
                className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-line bg-white text-red-600 transition hover:border-red-200 hover:bg-red-50"
                title="Stop this run"
                type="button"
                onClick={() => onStopRun?.(selectedRun.id)}
              >
                <Square size={12} fill="currentColor" strokeWidth={1.7} />
              </button>
            ) : null}
          </div>
          {selectedRun ? (
            <RunHistoryActions
              run={selectedRun}
              selectedNodeId={selectedNodeId}
              onReplayRun={onReplayRun}
              onResumeRun={onResumeRun}
            />
          ) : null}
          <div className="workflow-scrollbar max-h-60 overflow-y-auto py-1">
            <button
              className={`flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition hover:bg-slate-50 ${
                selectedRunId ? "" : "bg-teal-50"
              }`}
              type="button"
              onClick={() => {
                onOpenChange(false);
                onShowLatest?.();
              }}
            >
              <RunStatusDot status={runs[0]?.status ?? "idle"} />
              <span className="truncate font-medium text-ink">Latest run</span>
            </button>
            {runs.length ? (
              runs.map((run) => (
                <div
                  key={run.id}
                  className={`flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition hover:bg-slate-50 ${
                    selectedRunId === run.id ? "bg-teal-50" : ""
                  }`}
                >
                  <button
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    type="button"
                    onClick={() => {
                      onOpenChange(false);
                      onSelectRun?.(run.id);
                    }}
                  >
                    <RunStatusDot status={run.status} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium text-ink">
                        {formatRunLabel(run)}
                      </div>
                      <div className="truncate text-[11px] text-muted">{run.id}</div>
                    </div>
                  </button>
                  {run.status === "running" ? (
                    <button
                      className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-line bg-white text-red-600 transition hover:border-red-200 hover:bg-red-50"
                      title="Stop this run"
                      type="button"
                      onClick={() => onStopRun?.(run.id)}
                    >
                      <Square size={12} fill="currentColor" strokeWidth={1.7} />
                    </button>
                  ) : null}
                </div>
              ))
            ) : (
              <div className="px-3 py-3 text-xs text-muted">No workflow runs yet.</div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function RunTimelinePanel({
  collapsed,
  embedded = false,
  error,
  height,
  loading,
  logPath,
  runEvents = [],
  runs = [],
  selectedRunId,
  onReplayRun,
  onResumeRun,
  onPruneRuns,
  onRetentionSettingsChange,
  onResizeStart,
  onResizeKeyDown,
  onSelectRun,
  onShowLatest,
  onStopRun,
  onToggle,
  retentionSettings = DEFAULT_RETENTION_SETTINGS,
  text,
  title,
  usageSummary,
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const [retentionOpen, setRetentionOpen] = useState(false);
  const [retentionDraft, setRetentionDraft] = useState(() => ({
    ...DEFAULT_RETENTION_SETTINGS,
    ...(retentionSettings ?? {}),
  }));
  const [expandedRowIds, setExpandedRowIds] = useState({});
  const [filters, setFilters] = useState({
    attempt: "",
    datetime: "",
    fanOut: "",
    message: "",
    node: "",
    status: "",
  });
  const historyRef = useRef(null);
  const displayText = error
    ? error
    : loading
      ? "Loading log..."
      : text?.trim()
        ? text.trim()
        : "No run log available.";
  const timelineRows = useMemo(() => parseTimelineRows(runEvents), [runEvents]);
  const logRows = useMemo(
    () => (timelineRows.length ? timelineRows : parseLogRows(displayText)),
    [displayText, timelineRows],
  );
  const resolvedRetentionSettings = retentionDraft;
  const updateRetentionSetting = (key, value) => {
    const parsed = Number.parseInt(value, 10);
    const nextSettings = {
      ...resolvedRetentionSettings,
      [key]: Number.isNaN(parsed) ? 0 : Math.max(0, parsed),
    };
    setRetentionDraft(nextSettings);
    onRetentionSettingsChange?.(nextSettings);
  };
  const pruneWithRetention = (dryRun) => {
    setRetentionOpen(false);
    onPruneRuns?.({
      dryRun,
      keepDays: resolvedRetentionSettings.keepDays,
      keepFailedDays: resolvedRetentionSettings.keepFailedDays,
      keepLast: resolvedRetentionSettings.keepLast,
    });
  };
  const usingTimeline = timelineRows.length > 0;
  const filteredRows = useMemo(() => {
    return logRows.filter((row) => {
      const attempt = filters.attempt.trim().toLowerCase();
      const datetime = filters.datetime.trim().toLowerCase();
      const fanOut = filters.fanOut.trim().toLowerCase();
      const node = filters.node.trim().toLowerCase();
      const message = filters.message.trim().toLowerCase();
      const status = filters.status.trim().toLowerCase();
      return (
        (!attempt || row.attempt.toLowerCase().includes(attempt)) &&
        (!datetime || row.datetime.toLowerCase().includes(datetime)) &&
        (!fanOut || row.fanOut.toLowerCase().includes(fanOut)) &&
        (!node || row.node.toLowerCase().includes(node)) &&
        (!message || row.message.toLowerCase().includes(message)) &&
        (!status || row.status.toLowerCase().includes(status))
      );
    });
  }, [filters, logRows]);

  useEffect(() => {
    setExpandedRowIds({});
  }, [displayText]);

  useEffect(() => {
    setRetentionDraft({
      ...DEFAULT_RETENTION_SETTINGS,
      ...(retentionSettings ?? {}),
    });
  }, [retentionSettings]);

  useEffect(() => {
    if (!historyOpen) return undefined;

    function handlePointerDown(event) {
      if (historyRef.current?.contains(event.target)) return;
      setHistoryOpen(false);
    }

    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [historyOpen]);

  return (
    <section
      className={embedded
        ? "flex h-full min-h-0 flex-col overflow-hidden bg-white text-ink"
        : "relative z-30 shrink-0 overflow-hidden border-t border-line bg-white text-ink shadow-[0_-12px_30px_rgba(15,23,42,0.08)] transition-[height]"}
      style={embedded ? undefined : { height: collapsed ? 44 : height }}
    >
      {!embedded && !collapsed ? (
        <div
          aria-label="Resize workflow log"
          aria-orientation="horizontal"
          aria-valuemax={420}
          aria-valuemin={140}
          aria-valuenow={height}
          aria-valuetext={`${height} pixels high`}
          className="absolute left-0 top-[-3px] z-20 h-1.5 w-full cursor-row-resize transition hover:bg-brand/40"
          role="separator"
          tabIndex={0}
          title="Resize workflow log"
          onKeyDown={onResizeKeyDown}
          onPointerDown={onResizeStart}
        />
      ) : null}
      <div
        className="flex h-11 w-full shrink-0 items-center justify-between border-b border-line bg-[#f9fbfd] px-4 text-left"
        role={embedded ? undefined : "button"}
        tabIndex={embedded ? undefined : 0}
        title={embedded ? undefined : collapsed ? "Expand log" : "Collapse log"}
        onClick={embedded ? undefined : onToggle}
        onKeyDown={(event) => {
          if (embedded) return;
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onToggle();
          }
        }}
      >
        <div className="flex min-w-0 items-center gap-2">
          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-teal-100 bg-teal-50 text-teal-700">
            <Terminal size={15} />
          </span>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold">{usingTimeline ? "Run timeline" : title}</h2>
            {logPath ? <p className="truncate text-[11px] text-muted">{logPath}</p> : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {!collapsed || embedded ? (
            <div
              ref={historyRef}
              className="relative flex items-center gap-1"
              onClick={(event) => event.stopPropagation()}
            >
              <button
                className={`h-7 rounded-md border px-2 text-[11px] font-medium transition ${
                  selectedRunId
                    ? "border-line bg-white text-muted hover:bg-slate-50"
                    : "border-teal-200 bg-teal-50 text-teal-700"
                }`}
                type="button"
                onClick={() => {
                  setHistoryOpen(false);
                  onShowLatest?.();
                }}
              >
                Latest run
              </button>
              <button
                className={`h-7 rounded-md border px-2 text-[11px] font-medium transition ${
                  historyOpen
                    ? "border-slate-300 bg-white text-ink"
                    : "border-line bg-white text-muted hover:bg-slate-50"
                }`}
                type="button"
                onClick={() => setHistoryOpen((current) => !current)}
              >
                All runs
              </button>
              {historyOpen ? (
                <div className="absolute right-0 top-9 z-50 max-h-72 w-[310px] overflow-hidden rounded-lg border border-line bg-white shadow-panel">
                  <div className="border-b border-line px-3 py-2 text-xs font-semibold text-muted">
                    Previous runs
                  </div>
                  <div className="workflow-scrollbar max-h-60 overflow-y-auto">
                    {runs.length ? (
                      runs.map((run) => (
                        <div
                          key={run.id}
                          className={`flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition hover:bg-slate-50 ${
                            selectedRunId === run.id ? "bg-teal-50" : ""
                          }`}
                        >
                          <button
                            className="flex min-w-0 flex-1 items-center gap-2 text-left"
                            type="button"
                            onClick={() => {
                              setHistoryOpen(false);
                              onSelectRun?.(run.id);
                            }}
                          >
                            <RunStatusDot status={run.status} />
                            <div className="min-w-0 flex-1">
                              <div className="truncate font-medium text-ink">
                                {formatRunLabel(run)}
                              </div>
                              <div className="truncate text-[11px] text-muted">{run.id}</div>
                            </div>
                          </button>
                          {run.status === "running" ? (
                            <button
                              className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-line bg-white text-red-600 transition hover:border-red-200 hover:bg-red-50"
                              title="Stop this run"
                              type="button"
                              onClick={() => onStopRun?.(run.id)}
                            >
                              <Square size={12} fill="currentColor" strokeWidth={1.7} />
                            </button>
                          ) : (
                            <button
                              className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-line bg-white text-teal-700 transition hover:border-teal-200 hover:bg-teal-50 disabled:cursor-not-allowed disabled:opacity-40"
                              title="Resume this run"
                              type="button"
                              onClick={() => onResumeRun?.(run.id, {})}
                            >
                              <Repeat2 size={13} />
                            </button>
                          )}
                          {run.hasTriggerReplay ? (
                            <button
                              className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-line bg-white text-blue-700 transition hover:border-blue-200 hover:bg-blue-50"
                              title="Replay saved webhook payload"
                              type="button"
                              onClick={() => onReplayRun?.(run.id, run.triggerId)}
                            >
                              <Webhook size={13} />
                            </button>
                          ) : null}
                        </div>
                      ))
                    ) : (
                      <div className="px-3 py-4 text-xs text-muted">No previous runs.</div>
                    )}
                  </div>
                </div>
              ) : null}
              <div className="relative">
                <button
                  className={`grid h-7 w-7 place-items-center rounded-md border text-muted transition ${
                    retentionOpen
                      ? "border-slate-300 bg-white text-ink"
                      : "border-line bg-white hover:bg-slate-50"
                  }`}
                  title="Run retention settings"
                  type="button"
                  onClick={() => setRetentionOpen((current) => !current)}
                >
                  <Trash2 size={13} />
                </button>
                {retentionOpen ? (
                  <div className="absolute right-0 top-9 z-50 w-[280px] rounded-lg border border-line bg-white p-3 text-xs shadow-panel">
                    <div className="font-semibold text-ink">Retention</div>
                    <div className="mt-1 text-muted">Preview cleanup before pruning logs.</div>
                    <div className="mt-3 grid gap-2">
                      <label className="grid gap-1">
                        <span className="font-medium text-muted">Keep latest runs</span>
                        <input
                          className="h-8 rounded-md border border-line px-2 text-ink outline-none focus:border-teal-300"
                          min="0"
                          type="number"
                          value={resolvedRetentionSettings.keepLast}
                          onChange={(event) =>
                            updateRetentionSetting("keepLast", event.target.value)
                          }
                        />
                      </label>
                      <label className="grid gap-1">
                        <span className="font-medium text-muted">Keep runs for days</span>
                        <input
                          className="h-8 rounded-md border border-line px-2 text-ink outline-none focus:border-teal-300"
                          min="0"
                          type="number"
                          value={resolvedRetentionSettings.keepDays}
                          onChange={(event) =>
                            updateRetentionSetting("keepDays", event.target.value)
                          }
                        />
                      </label>
                      <label className="grid gap-1">
                        <span className="font-medium text-muted">Keep failed runs for days</span>
                        <input
                          className="h-8 rounded-md border border-line px-2 text-ink outline-none focus:border-teal-300"
                          min="0"
                          type="number"
                          value={resolvedRetentionSettings.keepFailedDays}
                          onChange={(event) =>
                            updateRetentionSetting("keepFailedDays", event.target.value)
                          }
                        />
                      </label>
                    </div>
                    <div className="mt-3 flex items-center justify-end gap-2">
                      <button
                        className="h-7 rounded-md border border-line px-2 font-medium text-muted transition hover:bg-slate-50"
                        type="button"
                        onClick={() => pruneWithRetention(true)}
                      >
                        Preview
                      </button>
                      <button
                        className="h-7 rounded-md border border-red-200 bg-red-50 px-2 font-medium text-red-700 transition hover:bg-red-100"
                        type="button"
                        onClick={() => pruneWithRetention(false)}
                      >
                        Prune
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
          {!embedded ? (
            <span className="grid h-8 w-8 place-items-center rounded-md text-muted">
              {collapsed ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </span>
          ) : null}
        </div>
      </div>
      <div
        className={`workflow-scrollbar overflow-auto bg-white dark:bg-[#1e1e1e] ${embedded ? "min-h-0 flex-1" : ""}`}
        style={embedded ? undefined : { height: Math.max(0, height - 44) }}
      >
        <UsageSummaryStrip summary={usageSummary} />
        <table className="w-full table-fixed border-collapse text-left text-xs">
          <thead className="sticky top-0 z-10 border-b border-line bg-[#f9fbfd] dark:bg-[#252526]">
            <tr className="text-[11px] uppercase tracking-wide text-muted">
              <th className="w-[180px] px-3 pb-1 pt-2 font-semibold">Datetime</th>
              <th className="w-[140px] px-3 pb-1 pt-2 font-semibold">Node</th>
              {usingTimeline ? (
                <>
                  <th className="w-[105px] px-3 pb-1 pt-2 font-semibold">Status</th>
                  <th className="w-[90px] px-3 pb-1 pt-2 font-semibold">Attempt</th>
                  <th className="w-[110px] px-3 pb-1 pt-2 font-semibold">Fan-out</th>
                </>
              ) : null}
              <th className="px-3 pb-1 pt-2 font-semibold">Message</th>
            </tr>
            <tr className="border-t border-line/70">
              <th className="px-3 pb-2 pt-1">
                <LogFilterInput
                  label="Filter datetime"
                  value={filters.datetime}
                  onChange={(value) =>
                    setFilters((current) => ({ ...current, datetime: value }))
                  }
                />
              </th>
              <th className="px-3 pb-2 pt-1">
                <LogFilterInput
                  label="Filter node"
                  value={filters.node}
                  onChange={(value) =>
                    setFilters((current) => ({ ...current, node: value }))
                  }
                />
              </th>
              {usingTimeline ? (
                <>
                  <th className="px-3 pb-2 pt-1">
                    <LogFilterInput
                      label="Filter status"
                      value={filters.status}
                      onChange={(value) =>
                        setFilters((current) => ({ ...current, status: value }))
                      }
                    />
                  </th>
                  <th className="px-3 pb-2 pt-1">
                    <LogFilterInput
                      label="Filter attempt"
                      value={filters.attempt}
                      onChange={(value) =>
                        setFilters((current) => ({ ...current, attempt: value }))
                      }
                    />
                  </th>
                  <th className="px-3 pb-2 pt-1">
                    <LogFilterInput
                      label="Filter item"
                      value={filters.fanOut}
                      onChange={(value) =>
                        setFilters((current) => ({ ...current, fanOut: value }))
                      }
                    />
                  </th>
                </>
              ) : null}
              <th className="px-3 pb-2 pt-1">
                <LogFilterInput
                  label="Filter message"
                  value={filters.message}
                  onChange={(value) =>
                    setFilters((current) => ({ ...current, message: value }))
                  }
                />
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line/70">
            {filteredRows.map((row) => {
              const expanded = Boolean(expandedRowIds[row.id]);
              return (
                <tr
                  key={row.id}
                  className="h-10 cursor-pointer align-top text-slate-700 transition hover:bg-slate-50 dark:text-[#cccccc] dark:hover:bg-[#2a2d2e]"
                  title={expanded ? "Collapse log row" : "Expand log row"}
                  onClick={() =>
                    setExpandedRowIds((current) => ({
                      ...current,
                      [row.id]: !current[row.id],
                    }))
                  }
                >
                  <td className="px-3 py-2 font-mono text-[11px] text-muted">
                    <div className={expanded ? "whitespace-pre-wrap" : "truncate"}>
                      {row.datetime || "-"}
                    </div>
                  </td>
                  <td className="px-3 py-2 font-mono text-[11px] text-muted">
                    <div className={expanded ? "whitespace-pre-wrap" : "truncate"}>
                      {row.node || "-"}
                    </div>
                  </td>
                  {usingTimeline ? (
                    <>
                      <td className="px-3 py-2 font-mono text-[11px] text-muted">
                        <div className={expanded ? "whitespace-pre-wrap" : "truncate"}>
                          {row.status || "-"}
                        </div>
                      </td>
                      <td className="px-3 py-2 font-mono text-[11px] text-muted">
                        <div className={expanded ? "whitespace-pre-wrap" : "truncate"}>
                          {row.attempt || "-"}
                        </div>
                      </td>
                      <td className="px-3 py-2 font-mono text-[11px] text-muted">
                        <div className={expanded ? "whitespace-pre-wrap" : "truncate"}>
                          {row.fanOut || "-"}
                        </div>
                      </td>
                    </>
                  ) : null}
                  <td className="px-3 py-2 font-mono text-[11px] leading-5">
                    <div
                      className={
                        expanded
                          ? "whitespace-pre-wrap break-words"
                          : "truncate whitespace-nowrap"
                      }
                    >
                      {row.message || "-"}
                    </div>
                  </td>
                </tr>
              );
            })}
            {!filteredRows.length ? (
              <tr>
                <td className="px-4 py-6 text-center text-xs text-muted" colSpan={usingTimeline ? 6 : 3}>
                  {logRows.length ? "No log rows match the current filters." : displayText}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RunHistoryActions({ onReplayRun, onResumeRun, run, selectedNodeId }) {
  const canResume = run?.id && run.status !== "running";
  const canReplay = canResume && run?.hasTriggerReplay;
  return (
    <div className="grid gap-1 border-b border-line bg-slate-50 px-2 py-2">
      <button
        className="flex h-8 items-center gap-2 rounded-md px-2 text-left text-xs font-medium text-ink transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
        disabled={!canResume}
        title="Resume this run"
        type="button"
        onClick={() => onResumeRun?.(run.id, {})}
      >
        <Repeat2 size={13} />
        <span className="truncate">Resume</span>
      </button>
      <button
        className="flex h-8 items-center gap-2 rounded-md px-2 text-left text-xs font-medium text-ink transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
        disabled={!canResume}
        title="Rerun failed nodes"
        type="button"
        onClick={() => onResumeRun?.(run.id, { skipCache: true })}
      >
        <RefreshCw size={13} />
        <span className="truncate">Rerun failed nodes</span>
      </button>
      <button
        className="flex h-8 items-center gap-2 rounded-md px-2 text-left text-xs font-medium text-ink transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
        disabled={!canResume || !selectedNodeId}
        title={selectedNodeId ? "Rerun from selected node" : "Select a node to rerun from it"}
        type="button"
        onClick={() => onResumeRun?.(run.id, { fromNode: selectedNodeId })}
      >
        <Route size={13} />
        <span className="truncate">Rerun from selected node</span>
      </button>
      <button
        className="flex h-8 items-center gap-2 rounded-md px-2 text-left text-xs font-medium text-ink transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
        disabled={!canReplay}
        title={canReplay ? "Replay saved webhook payload" : "This run has no saved webhook payload"}
        type="button"
        onClick={() => onReplayRun?.(run.id, run.triggerId)}
      >
        <Webhook size={13} />
        <span className="truncate">Replay webhook payload</span>
      </button>
    </div>
  );
}

export function UsageSummaryStrip({ summary }) {
  const totals = summary?.totals;
  const calls = Number(totals?.agent_calls ?? 0);
  if (!summary || !totals || !calls) return null;

  const mostExpensive = firstUsageNode(summary.most_expensive_nodes);
  const slowest = firstUsageNode(summary.slowest_nodes);
  return (
    <div className="border-b border-line bg-slate-50 px-3 py-2 text-xs text-slate-700 dark:bg-[#252526] dark:text-[#cccccc]">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="font-semibold text-ink dark:text-white">LLM usage</span>
        <span>{calls} calls</span>
        <span>{formatInteger(totals.total_tokens)} tokens</span>
        <span>cost~{formatCurrency(totals.estimated_cost)}</span>
        <span>{formatUsageSeconds(totals.agent_time_seconds)} agent time</span>
        {mostExpensive ? (
          <span>
            Most expensive: {mostExpensive.node_id} ({formatCurrency(mostExpensive.estimated_cost)})
          </span>
        ) : null}
        {slowest ? (
          <span>
            Slowest: {slowest.node_id} ({formatUsageSeconds(slowest.duration_seconds)})
          </span>
        ) : null}
      </div>
    </div>
  );
}

function firstUsageNode(nodes) {
  return Array.isArray(nodes) && nodes.length ? nodes[0] : null;
}

function formatCurrency(value) {
  const number = Number(value ?? 0);
  return `$${number.toFixed(number >= 1 ? 2 : 6)}`;
}

function formatInteger(value) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(Number(value ?? 0));
}

function formatUsageSeconds(value) {
  return `${Number(value ?? 0).toFixed(2)}s`;
}

function LogFilterInput({ label, onChange, value }) {
  return (
    <input
      aria-label={label}
      className="h-7 w-full rounded-md border border-line bg-white px-2 text-[11px] font-normal text-ink outline-none transition placeholder:text-muted/70 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/10 dark:bg-[#1e1e1e]"
      placeholder={label}
      type="text"
      value={value}
      onChange={(event) => onChange(event.target.value)}
      onClick={(event) => event.stopPropagation()}
    />
  );
}

function parseLogRows(logText) {
  if (!logText?.trim()) return [];
  const rows = [];
  const timestampPattern =
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+-\s+(.*)$/;

  function pushRow(row) {
    if (!row) return;
    rows.push({
      attempt: "",
      fanOut: "",
      ...row,
      message: row.message.trimEnd(),
      status: "",
    });
  }

  let current = null;
  logText.split("\n").forEach((line, index) => {
    const match = line.match(timestampPattern);
    if (match) {
      pushRow(current);
      const parsed = parseLogPayload(match[2]);
      current = {
        id: `log-row-${index}`,
        datetime: match[1],
        node: parsed.node,
        message: parsed.message,
      };
      return;
    }

    if (current) {
      current.message = current.message ? `${current.message}\n${line}` : line;
    } else if (line.trim()) {
      current = {
        id: `log-row-${index}`,
        datetime: "",
        node: "",
        message: line,
      };
    }
  });

  pushRow(current);
  return rows;
}

function parseTimelineRows(events = []) {
  if (!Array.isArray(events) || !events.length) return [];
  return events.map((event, index) => {
    const fanOut = event?.fanOutItem?.index ?? event?.fanOutItem?.file_name ?? "";
    return {
      id: `timeline-row-${index}`,
      attempt: event?.attempt == null ? "" : String(event.attempt),
      datetime: event?.occurredAt ?? "",
      fanOut: fanOut === "" ? "" : String(fanOut),
      message: event?.message ?? "",
      node: event?.nodeId ?? "",
      status: event?.status ?? "",
    };
  });
}

function parseLogPayload(payload) {
  const nodeMatch = payload.match(/^NODE\s+-\s+(.+?)\s+-\s+(.*)$/);
  if (nodeMatch) {
    return {
      node: nodeMatch[1],
      message: nodeMatch[2],
    };
  }

  const levelMatch = payload.match(/^(INFO|ERROR|WARN|WARNING|DEBUG)\s+-\s+(.*)$/);
  if (levelMatch) {
    return {
      node: levelMatch[1],
      message: levelMatch[2],
    };
  }

  return {
    node: "workflow",
    message: payload,
  };
}

function RunStatusDot({ status }) {
  if (["running", "started", "retried"].includes(status)) {
    return <Loader2 className="shrink-0 animate-spin text-blue-500" size={13} />;
  }
  if (status === "queued") {
    return <span className="h-2.5 w-2.5 shrink-0 rounded-full border border-blue-400 bg-blue-50" />;
  }
  const color =
    status === "success"
      ? "bg-emerald-500"
      : status === "reused"
        ? "bg-teal-500"
      : status === "error"
        ? "bg-red-500"
        : status === "stopped"
          ? "bg-amber-500"
          : "bg-slate-400";
  return <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${color}`} />;
}

function RunNodeInspector({ nodeRun }) {
  const attempts = Array.isArray(nodeRun?.attempts) ? nodeRun.attempts : [];
  const data = nodeRun?.data ?? {};
  const edgeDecisions = Array.isArray(data.edgeDecisions) ? data.edgeDecisions : [];
  const fanOut = data.fanOut ?? null;
  const fanOutItems = Array.isArray(fanOut?.items) ? fanOut.items : [];
  const [selectedFanOutIndex, setSelectedFanOutIndex] = useState(null);
  const [attemptPage, setAttemptPage] = useState(0);
  const [selectedTextFieldId, setSelectedTextFieldId] = useState("output");
  const attemptsPerPage = 1;

  useEffect(() => {
    setSelectedFanOutIndex(null);
    setAttemptPage(0);
    setSelectedTextFieldId("output");
  }, [nodeRun?.nodeId]);

  const selectedFanOutItem =
    selectedFanOutIndex === null
      ? null
      : fanOutItems.find((item) => Number(item.index) === Number(selectedFanOutIndex));
  const visibleAttempts =
    selectedFanOutIndex === null
      ? attempts
      : attempts.filter((attempt) => Number(attempt.fanOutItem?.index) === Number(selectedFanOutIndex));
  const attemptPageCount = Math.max(1, Math.ceil(visibleAttempts.length / attemptsPerPage));
  const clampedAttemptPage = Math.min(attemptPage, attemptPageCount - 1);
  const selectedAttempt = visibleAttempts[clampedAttemptPage] ?? null;

  useEffect(() => {
    setAttemptPage(0);
  }, [selectedFanOutIndex]);

  useEffect(() => {
    if (attemptPage !== clampedAttemptPage) {
      setAttemptPage(clampedAttemptPage);
    }
  }, [attemptPage, clampedAttemptPage]);

  return (
    <InspectorSection title="Last run">
      <KeyValueRows
        rows={[
          ["Status", nodeRun.status ?? ""],
          ["Duration", formatSeconds(nodeRun.durationSeconds)],
          ["Exit code", nodeRun.exitCode ?? ""],
          ["Attempts", attempts.length || ""],
          ["Reused", data.reused ? "Yes" : ""],
          ["Message", data.message ?? nodeRun.message ?? ""],
        ]}
      />
      {fanOut ? (
        <KeyValueRows
          rows={[
            ["Fan-out items", fanOut.itemCount ?? ""],
            ["Succeeded", fanOut.successCount ?? ""],
            ["Failed", fanOut.failureCount ?? ""],
          ]}
        />
      ) : null}
      {fanOutItems.length ? (
        <div className="space-y-2">
          <div className="workflow-scrollbar flex max-h-28 flex-wrap gap-1 overflow-auto rounded-md border border-line bg-slate-50 p-2">
            <button
              className={`rounded border px-2 py-1 text-[11px] ${selectedFanOutIndex === null ? "border-teal-400 bg-teal-50 text-teal-700" : "border-line bg-white text-slate-600"}`}
              type="button"
              onClick={() => setSelectedFanOutIndex(null)}
            >
              All
            </button>
            {fanOutItems.map((item) => (
              <button
                key={item.index}
                className={`rounded border px-2 py-1 text-[11px] ${Number(selectedFanOutIndex) === Number(item.index) ? "border-teal-400 bg-teal-50 text-teal-700" : "border-line bg-white text-slate-600"}`}
                type="button"
                onClick={() => setSelectedFanOutIndex(item.index)}
              >
                {item.index}: {item.status}
              </button>
            ))}
          </div>
          {selectedFanOutItem ? (
            <div className="rounded-md border border-line bg-slate-50 p-2 text-xs">
              <KeyValueRows
                rows={[
                  ["Item", selectedFanOutItem.index],
                  ["Status", selectedFanOutItem.status ?? ""],
                  ["Node", selectedFanOutItem.nodeId ?? ""],
                  ["Duration", formatSeconds(selectedFanOutItem.durationSeconds)],
                  ["Exit code", selectedFanOutItem.exitCode ?? ""],
                ]}
              />
              <ScrollableFieldViewer
                fields={fanOutItemTextFields(selectedFanOutItem)}
                selectedFieldId={selectedTextFieldId}
                onSelectField={setSelectedTextFieldId}
              />
            </div>
          ) : null}
        </div>
      ) : null}
      {attempts.length ? (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3 text-xs text-muted">
            <span>
              Attempt {visibleAttempts.length ? clampedAttemptPage + 1 : 0} of {visibleAttempts.length}
            </span>
            {attemptPageCount > 1 ? (
              <div className="flex items-center gap-1">
                <button
                  className="rounded border border-line bg-white px-2 py-1 text-[11px] font-medium text-slate-600 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={clampedAttemptPage <= 0}
                  type="button"
                  onClick={() => setAttemptPage((current) => Math.max(0, current - 1))}
                >
                  Previous
                </button>
                <span className="px-1 text-[11px]">
                  {clampedAttemptPage + 1}/{attemptPageCount}
                </span>
                <button
                  className="rounded border border-line bg-white px-2 py-1 text-[11px] font-medium text-slate-600 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={clampedAttemptPage >= attemptPageCount - 1}
                  type="button"
                  onClick={() =>
                    setAttemptPage((current) => Math.min(attemptPageCount - 1, current + 1))
                  }
                >
                  Next
                </button>
              </div>
            ) : null}
          </div>
          {selectedAttempt ? (
            <div
              key={`${selectedAttempt.runNumber}-${selectedAttempt.attempt}-${clampedAttemptPage}`}
              className="rounded-md border border-line bg-slate-50 p-2 text-xs"
            >
              <div className="flex items-center justify-between gap-2 font-medium text-ink">
                <span>{attemptRunLabel(selectedAttempt, clampedAttemptPage)}</span>
                <span>{formatSeconds(selectedAttempt.durationSeconds)}</span>
              </div>
              <ScrollableFieldViewer
                fields={attemptTextFields(selectedAttempt)}
                selectedFieldId={selectedTextFieldId}
                onSelectField={setSelectedTextFieldId}
              />
            </div>
          ) : null}
        </div>
      ) : null}
      {edgeDecisions.length ? (
        <div className="space-y-1">
          {edgeDecisions.map((decision, index) => (
            <div key={`${decision.to}-${index}`} className="flex items-center justify-between gap-2 rounded-md border border-line px-2 py-1.5 text-xs">
              <span className="truncate">
                {decision.from} {"->"} {decision.to}
              </span>
              <span className={decision.matched ? "text-emerald-700" : "text-muted"}>
                {decision.matched ? "matched" : "skipped"}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </InspectorSection>
  );
}

function attemptRunLabel(attempt, fallbackIndex) {
  const attemptNumber = attempt.attempt ?? fallbackIndex + 1;
  const iteration = attemptIterationNumber(attempt);
  return iteration === null
    ? `Attempt ${attemptNumber}`
    : `Iteration ${iteration} - Attempt ${attemptNumber}`;
}

function attemptIterationNumber(attempt) {
  const rawIndex = attempt?.fanOutItem?.index ?? attempt?.loopItem?.index ?? attempt?.loop?.index;
  if (rawIndex === null || rawIndex === undefined || rawIndex === "") return null;
  const number = Number(rawIndex);
  if (!Number.isFinite(number)) return String(rawIndex);
  return number + 1;
}

function attemptTextFields(attempt) {
  return [
    attempt.inputs && Object.keys(attempt.inputs).length
      ? { id: "inputs", label: "Inputs", text: JSON.stringify(attempt.inputs, null, 2) }
      : null,
    attempt.output ? { id: "output", label: "Output", text: attempt.output } : null,
    attempt.stdout ? { id: "stdout", label: "Stdout", text: attempt.stdout } : null,
    attempt.stderr ? { id: "stderr", label: "Stderr", text: attempt.stderr, tone: "error" } : null,
    attempt.error && attempt.error !== attempt.stderr
      ? { id: "error", label: "Error", text: attempt.error, tone: "error" }
      : null,
    attempt.prompt ? { id: "prompt", label: "Prompt", text: attempt.prompt } : null,
  ].filter(Boolean);
}

function fanOutItemTextFields(item) {
  return [
    item.item ? { id: "item", label: "Iteration item", text: JSON.stringify(item.item, null, 2) } : null,
    item.output ? { id: "output", label: "Output", text: item.output } : null,
    item.error ? { id: "error", label: "Error", text: item.error, tone: "error" } : null,
  ].filter(Boolean);
}

function ScrollableFieldViewer({ fields, selectedFieldId, onSelectField }) {
  if (!fields.length) return null;
  const selectedField = fields.find((field) => field.id === selectedFieldId) ?? fields[0];
  const errorTone = selectedField.tone === "error";
  return (
    <div className="mt-2 overflow-hidden rounded-md border border-line bg-white">
      <div className="flex flex-wrap gap-1 border-b border-line bg-slate-50 p-1">
        {fields.map((field) => {
          const selected = field.id === selectedField.id;
          return (
            <button
              key={field.id}
              className={`rounded px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] transition ${
                selected
                  ? "bg-white text-ink shadow-sm"
                  : "text-muted hover:bg-white/70 hover:text-ink"
              }`}
              title={`Show ${field.label}`}
              type="button"
              onClick={() => onSelectField(field.id)}
            >
              {field.label}
            </button>
          );
        })}
      </div>
      <pre
        className={`workflow-scrollbar max-h-[min(48vh,520px)] min-h-72 overflow-auto whitespace-pre-wrap p-2 font-mono text-[11px] leading-5 ${
          errorTone ? "bg-red-50 text-red-700" : "bg-white text-slate-700"
        }`}
      >
        {selectedField.text}
      </pre>
    </div>
  );
}

function formatSeconds(value) {
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return `${number.toFixed(2)}s`;
}

function KeyValueRows({ rows }) {
  const visibleRows = rows.filter(([, value]) => value !== "" && value !== null && value !== undefined);
  if (!visibleRows.length) return null;
  return (
    <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
      {visibleRows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted">{label}</dt>
          <dd className="min-w-0 truncate font-medium text-slate-700">{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function formatRunLabel(run) {
  if (!run?.startedAt) return "Run";
  const date = new Date(run.startedAt);
  if (Number.isNaN(date.getTime())) return run.startedAt;
  return date.toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function SelectionRectangle({ box }) {
  return (
    <div
      className="pointer-events-none absolute z-20 border border-indigo-500 bg-indigo-500/10"
      style={{
        left: box.left,
        top: box.top,
        width: box.width,
        height: box.height,
      }}
    />
  );
}

function WorkflowNode({
  expanded,
  folderEntries,
  diagnostics = [],
  node,
  readOnly = false,
  onConnectorPointerDown,
  onConnectorPointerUp,
  onContextMenu,
  onDoubleClick,
  selected,
  status,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  zIndex,
}) {
  const style = nodeStyles[node.type] ?? nodeStyles.agent;
  const Icon = style.icon;
  const isFileNode = node.type === "file";
  const isFolderNode = node.type === "folder";
  const extension = isFileNode ? fileExtension(node.operation?.path) : "";
  const hasError = diagnostics.some((diagnostic) => diagnostic.severity === "error");
  const hasWarning = diagnostics.some((diagnostic) => diagnostic.severity === "warning");
  const title = isFileNode
    ? "Double click to preview"
    : isFolderNode
      ? "Double click to expand"
      : diagnostics[0]?.message;
  const borderClass = hasError
    ? "border-red-500 ring-4 ring-red-100"
    : hasWarning
      ? "border-amber-400 ring-4 ring-amber-100"
      : selected
        ? "border-indigo-500 ring-4 ring-indigo-100"
        : style.border;

  return (
    <article
      className={`absolute w-[220px] rounded-lg border bg-white p-3 shadow-node transition ${readOnly ? "cursor-default" : "cursor-grab active:cursor-grabbing"} ${borderClass}`}
      style={{ left: node.x, top: node.y, zIndex }}
      title={title}
      onDoubleClick={(event) => {
        event.stopPropagation();
        onDoubleClick?.(node);
      }}
      onPointerDown={(event) => {
        event.stopPropagation();
        if (event.button !== 0) {
          if (event.button === 2) {
            event.preventDefault();
          }
          return;
        }
        if (!readOnly) onPointerDown(event, node.id);
      }}
      onPointerMove={(event) => {
        if (!readOnly) onPointerMove(event, node.id);
      }}
      onPointerUp={(event) => {
        if (!readOnly) onPointerUp(event, node.id);
      }}
      onContextMenu={(event) => {
        if (!readOnly) onContextMenu?.(event, node.id);
      }}
    >
      {!readOnly ? <button
        className="absolute -right-2 top-1/2 z-10 h-4 w-4 -translate-y-1/2 rounded-full border border-indigo-300 bg-white shadow-sm transition hover:scale-110 hover:border-indigo-500 hover:bg-indigo-50"
        title="Drag to connect"
        type="button"
        onPointerDown={(event) => onConnectorPointerDown?.(event, node.id)}
        onPointerUp={(event) => onConnectorPointerUp?.(event, node.id)}
      /> : null}
      <div className="min-w-0">
        <h3 className="truncate text-sm font-semibold leading-5">{node.label}</h3>
        <p className="mt-1 truncate text-xs leading-5 text-muted">{node.meta}</p>
      </div>
      <div className="mt-3 flex items-end justify-between gap-2">
        <span className={`relative grid h-8 w-8 shrink-0 place-items-center rounded-lg text-white ${style.accent}`}>
          <Icon size={16} />
          {extension ? (
            <span className="absolute -bottom-1 -right-1 rounded bg-white px-1 text-[8px] font-bold leading-3 text-slate-700 shadow-sm">
              {extension.slice(0, 4)}
            </span>
          ) : null}
        </span>
        <span className={`rounded-md border px-2 py-1 text-[11px] font-medium ${style.chip}`}>
          {node.type}
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {hasError || hasWarning ? (
            <AlertCircle
              className={hasError ? "text-red-600" : "text-amber-600"}
              size={15}
            />
          ) : null}
          <NodeStatusBadge status={status} />
        </div>
      </div>
      {isFolderNode && expanded ? (
        <FolderNodePreview state={folderEntries} />
      ) : null}
    </article>
  );
}

function NodeContextMenu({ onDelete, onDuplicate, onRename, x, y }) {
  return (
    <div
      className="fixed z-[90] w-48 rounded-lg border border-line bg-white p-1 text-sm shadow-panel"
      style={{ left: x, top: y }}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.preventDefault()}
    >
      <button
        className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50 hover:text-ink"
        type="button"
        onClick={onDuplicate}
      >
        <Copy size={15} />
        Duplicate node
      </button>
      <button
        className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50 hover:text-ink"
        type="button"
        onClick={onRename}
      >
        <PencilLine size={15} />
        Rename node
      </button>
      <div className="my-1 border-t border-line" />
      <button
        className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-red-700 transition hover:bg-red-50"
        type="button"
        onClick={onDelete}
      >
        <Trash2 size={15} />
        Delete node
      </button>
    </div>
  );
}

export function NodeRenameDialog({ initialLabel, onCancel, onRename }) {
  const [label, setLabel] = useState(initialLabel || "");

  function handleSubmit(event) {
    event.preventDefault();
    onRename(label);
  }

  return (
    <Dialog
      description="Update the label shown on the graph."
      onClose={onCancel}
      overlayClassName="fixed inset-0 z-[95] grid place-items-center bg-slate-950/25 px-4"
      panelClassName="w-full max-w-sm rounded-lg border border-line bg-white shadow-panel"
      title="Rename node"
    >
      <form onSubmit={handleSubmit}>
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-ink">Rename node</h2>
            <p className="text-xs text-muted">Update the label shown on the graph.</p>
          </div>
          <button
            className="grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
            title="Close"
            type="button"
            onClick={onCancel}
          >
            <X size={16} />
          </button>
        </div>
        <div className="px-4 py-4">
          <label className="block">
            <span className="text-xs font-medium text-muted">Node label</span>
            <input
              autoFocus
              className="mt-1 h-10 w-full rounded-lg border border-line px-3 text-sm outline-none transition focus:border-teal-500"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
          </label>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-line px-4 py-3">
          <button
            className="h-9 rounded-lg border border-line bg-white px-3 text-sm font-medium text-slate-700 transition hover:border-slate-300"
            type="button"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="inline-flex h-9 items-center gap-2 rounded-lg bg-brand px-3 text-sm font-medium text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={!label.trim()}
            title="Confirm node rename"
            type="submit"
          >
            <PencilLine size={15} />
            Rename
          </button>
        </div>
      </form>
    </Dialog>
  );
}

export function FilesystemTrustPrompt({ parentPath, onCancel, onConfirm }) {
  const [trustParent, setTrustParent] = useState(true);
  return (
    <Dialog
      description={parentPath}
      onClose={onCancel}
      overlayClassName="absolute inset-0 z-50 grid place-items-center bg-slate-950/20 px-4 backdrop-blur-sm"
      panelClassName="w-full max-w-lg rounded-lg border border-line bg-white p-5 shadow-panel"
      title="Trust files"
    >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-ink">Trust the files in</h2>
            <p className="mt-1 break-all text-sm text-muted">{parentPath}</p>
          </div>
          <button
            className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-line text-muted transition hover:bg-slate-50 hover:text-ink"
            title="Cancel"
            type="button"
            onClick={onCancel}
          >
            <X size={15} />
          </button>
        </div>
        <label className="mt-4 flex items-start gap-3 rounded-md border border-line bg-slate-50 px-3 py-2 text-sm text-ink">
          <input
            checked={trustParent}
            className="mt-0.5 h-4 w-4 rounded border-slate-300"
            type="checkbox"
            onChange={(event) => setTrustParent(event.target.checked)}
          />
          <span>Trust this parent folder and all files and subfolders inside it.</span>
        </label>
        <div className="mt-5 flex justify-end gap-2">
          <button
            className="inline-flex h-9 items-center justify-center rounded-md border border-line bg-white px-3 text-sm font-medium text-ink transition hover:bg-slate-50"
            type="button"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-brand px-3 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-dark"
            type="button"
            onClick={() => onConfirm(trustParent)}
          >
            <Check size={15} />
            Add access
          </button>
        </div>
    </Dialog>
  );
}

function FolderNodePreview({ state }) {
  if (!state?.loaded) {
    return <div className="mt-3 text-[11px] text-muted">Loading folder...</div>;
  }

  if (state.error) {
    return <div className="mt-3 text-[11px] text-red-600">{state.error}</div>;
  }

  const entries = state.entries ?? [];

  return (
    <div className="workflow-scrollbar mt-3 max-h-36 overflow-auto rounded-md border border-line bg-slate-50 p-2">
      {entries.length ? (
        <div className="space-y-1">
          {entries.map((entry) => (
            <div key={entry.path} className="flex min-w-0 items-center gap-2 text-[11px] text-slate-700">
              {entry.isDirectory ? (
                <FolderOpen size={12} className="shrink-0 text-amber-600" />
              ) : (
                <FileText size={12} className="shrink-0 text-slate-500" />
              )}
              <span className="truncate">{entry.name}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-[11px] text-muted">No visible children.</div>
      )}
    </div>
  );
}

function NodeStatusBadge({ status }) {
  if (!status) return null;

  if (["running", "started", "retried"].includes(status)) {
    return (
      <span
        className="flex items-center gap-1 rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-blue-700 dark:border-sky-700/70 dark:bg-sky-950/70 dark:text-sky-200"
        title={status}
      >
        <Loader2 size={10} className="animate-spin text-blue-600 dark:text-sky-300" />
        {status === "running" ? "run" : status}
      </span>
    );
  }

  if (status === "queued") {
    return (
      <span
        className="rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-blue-700 dark:border-sky-700/70 dark:bg-sky-950/70 dark:text-sky-200"
        title="queued"
      >
        queued
      </span>
    );
  }

  const className = {
    success: "border-emerald-200 bg-emerald-50 text-emerald-700",
    error: "border-red-200 bg-red-50 text-red-700",
    stopped: "border-amber-200 bg-amber-50 text-amber-700",
    skipped: "border-slate-200 bg-slate-50 text-slate-500",
    reused: "border-teal-200 bg-teal-50 text-teal-700",
  }[status];

  return (
    <span className={`rounded border px-1.5 py-0.5 text-[9px] font-semibold uppercase ${className}`} title={status}>
      {status}
    </span>
  );
}

const edgeConditionOptions = [
  ["always", "Always"],
  ["on_success", "On success"],
  ["on_failure", "On failure"],
  ["output_matches", "Output matches"],
  ["output_field", "Structured field"],
  ["after_loop", "After loop finishes"],
];

const edgeOutputFieldOperatorOptions = [
  ["equals", "Equals"],
  ["not_equals", "Not equal"],
  ["in", "In list"],
  ["not_in", "Not in list"],
  ["greater_than", "Greater than"],
  ["greater_than_or_equal", "At least"],
  ["less_than", "Less than"],
  ["less_than_or_equal", "At most"],
  ["exists", "Exists"],
  ["matches", "Regex matches"],
];

const compactEdgeConditionOptions = [
  ["always", "Always"],
  ["on_success", "Success"],
  ["on_failure", "Failure"],
  ["output_matches", "Matches"],
  ["output_field", "Structured field"],
  ["after_loop", "After loop"],
];

function handleInspectorTabKeyDown(event, onChange) {
  const tabs = [...event.currentTarget.parentNode.childNodes].filter(
    (candidate) => candidate.getAttribute?.("role") === "tab",
  );
  const currentIndex = tabs.indexOf(event.currentTarget);
  let nextIndex = currentIndex;

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

function RattishInspector({
  collapsed,
  document,
  edge,
  node,
  nodeOutput,
  nodeRun,
  onMutate,
  onRenameNode,
  onResizeKeyDown,
  onResizeStart,
  onToggleCollapsed,
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
                  <SelectField
                    label="Type"
                    value={graphNode?.type ?? ""}
                    options={(document?.nodeContracts ?? []).map((item) => [
                      item.nodeType,
                      humanizeRattishField(item.nodeType),
                    ])}
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
                  {Object.entries(schemaProperties).map(([field, fieldSchema]) => (
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
                  ))}
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
                  <InspectorSection title="Latest activation">
                    <div className="grid grid-cols-2 gap-2 rounded-lg border border-line bg-slate-50 p-3 text-xs">
                      <span className="text-muted">Status</span>
                      <span className="text-right font-semibold text-ink">{nodeRun?.status ?? (nodeOutput?.success ? "success" : "error")}</span>
                      <span className="text-muted">Duration</span>
                      <span className="text-right font-mono text-ink">{nodeRun?.durationMs == null ? "-" : `${nodeRun.durationMs} ms`}</span>
                    </div>
                    {nodeOutput?.data !== undefined ? (
                      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-slate-950 p-3 font-mono text-[11px] leading-5 text-slate-100">
                        {JSON.stringify(nodeOutput.data, null, 2)}
                      </pre>
                    ) : null}
                  </InspectorSection>
                ) : null}
                {runEvents.filter((event) => event.nodeId === node.id).length ? (
                  <InspectorSection title="Activation history">
                    {runEvents
                      .filter((event) => event.nodeId === node.id)
                      .slice()
                      .reverse()
                      .map((event) => (
                        <div key={`${event.sequence}-${event.attempt}`} className="rounded-lg border border-line bg-slate-50 p-3 text-xs">
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-semibold text-ink">Activation {event.attempt ?? "-"}</span>
                            <span className="text-muted">{event.status ?? event.outcome}</span>
                          </div>
                          <div className="mt-1 truncate font-mono text-[10px] text-muted" title={event.activationLineageId ?? ""}>
                            {event.activationLineageId ?? "No lineage recorded"}
                          </div>
                        </div>
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
          </>
        ) : edge ? (
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
          </div>
        )}
      </div>
    </aside>
  );
}

function RattishContractField({ authored, field, onChange, onReset, schema, value }) {
  const label = humanizeRattishField(field);
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
        <ToggleField checked={Boolean(value)} label={label} onChange={onChange} />
        {reset}
      </div>
    );
  }
  if (types.includes("integer") || types.includes("number")) {
    return (
      <div className="space-y-1.5">
        <NumberField
          label={label}
          min={schema.minimum}
          value={value ?? ""}
          onChange={onChange}
        />
        {reset}
      </div>
    );
  }
  if (types.includes("object") || types.includes("array")) {
    return (
      <div className="space-y-1.5">
        <JsonBodyField label={label} value={value ?? (types.includes("array") ? [] : {})} onChange={onChange} />
        {reset}
      </div>
    );
  }
  const longText = ["body", "command", "content", "instructions", "prompt", "template"].some(
    (part) => field.includes(part),
  );
  return (
    <div className="space-y-1.5">
      {longText ? (
        <TextareaField
          commitOnBlur
          label={label}
          rows={field === "prompt" ? 6 : 4}
          value={value ?? ""}
          onChange={onChange}
        />
      ) : (
        <TextField commitOnBlur label={label} value={value ?? ""} onChange={onChange} />
      )}
      {reset}
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

function rattishRouteValue(edge, source = "") {
  const mode = edge.mode ?? edge.displayLabel ?? "unconditional";
  if (mode === "unconditional" || mode === "always") return edge.to;
  if (mode === "otherwise") return { target: edge.to, mode: "otherwise" };
  const predicateSource = edge.predicateSource ?? sourceTextForSpan(source, edge.predicate?.span);
  return {
    target: edge.to,
    mode: "when",
    predicateSource: predicateSource || "succeeded",
  };
}

function sourceTextForSpan(source, span) {
  if (!source || !span) return "";
  const bytes = new TextEncoder().encode(source);
  return new TextDecoder().decode(bytes.slice(span.start.offset, span.end.offset));
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
  onMutate?.([{ kind: "set_routes", node: edge.from, routes }]);
}

function Inspector({
  agents,
  approval,
  collapsed,
  dataDir,
  edge,
  edges,
  node,
  nodeRun,
  nodeOutput,
  nodes,
  providerCapabilities = [],
  providerProfiles = [],
  workflow,
  onAddEdge,
  onAgentChange,
  onDecideApproval,
  onDeleteEdge,
  onEdgeChange,
  onNodeChange,
  onOperationChange,
  onApplyFix,
  onProviderProfilesChange,
  onProviderCapabilitiesRefresh,
  onResizeStart,
  onResizeKeyDown,
  onSettingsChange,
  onToggleCollapsed,
  onTypeChange,
  onWorkflowChange,
  width,
}) {
  const [workflowTab, setWorkflowTab] = useState("general");
  const [nodeTab, setNodeTab] = useState("general");
  const [edgeInspectorOpen, setEdgeInspectorOpen] = useState(Boolean(edge));
  const [cronPickerOpen, setCronPickerOpen] = useState(false);
  const [draftEdge, setDraftEdge] = useState(null);
  const [addingFilesystemPath, setAddingFilesystemPath] = useState(false);
  const [filesystemPathDraft, setFilesystemPathDraft] = useState("");
  const operation = node?.operation ?? defaultOperation(node?.type ?? "agent");
  const settings = { ...defaultSettings, ...(node?.settings ?? {}) };
  const existingSpecialTypes = new Set(
    nodes
      .filter((candidate) => candidate.id !== node?.id && isSpecialNodeType(candidate.type))
      .map((candidate) => candidate.type),
  );
  const nodeTypeOptions = [
    ["start", "START"],
    ["pass", "PASS"],
    ["fail", "FAIL"],
    ["break", "BREAK"],
    ["loop", "Loop"],
    ["agent", "Agent"],
    ["bash_command", commandNodeLabel],
    ["python_script", "Python script"],
    ["shell_script", "Shell script"],
    ["read_file", "Read file"],
    ["write_file", "Write file"],
    ["copy_file", "Copy file"],
    ["move_file", "Move file"],
    ["delete_file", "Delete file"],
    ["file", "File path"],
    ["folder", "Folder path"],
    ["open_resource", "Open app / URL / file"],
    ["prompt_file", "Prompt file"],
    ["common_llm_task", "Common LLM task"],
    ["local_vectorize", "Local vector index"],
    ["local_search", "Local search"],
    ["http_request", "HTTP request"],
    ["approval_gate", "Approval gate"],
    ["notification", "Notification"],
    ["workflow", "Workflow call"],
    ["subflow", "Subflow call"],
  ].filter(([type]) => type === node?.type || !existingSpecialTypes.has(type));
  const agentConfig =
    operation.type === "agent" || operation.type === "common_llm_task"
      ? agents[operation.agent_id] ?? defaultAgentConfig(operation.agent_id || "agent")
      : null;
  const schedule = workflow.schedule ?? null;
  const watch = workflow.watch ?? null;
  const webhooks = workflow.webhooks ?? {};
  const filesystemAccess = useMemo(
    () => workflow.filesystemAccess ?? [],
    [workflow.filesystemAccess],
  );
  const manualFilesystemAccess = filesystemAccess
    .map((entry, index) => ({ entry, index }))
    .filter(({ entry }) => !dataDir || !pathsMatch(entry.path, dataDir));
  const connectedEdges = node
    ? edges.filter((edge) => edge.from === node.id || edge.to === node.id)
    : [];
  const inputSourceOptions = node ? buildInputSourceOptions(node, nodes, edges) : [];
  const workflowDiagnostics = workflowDiagnosticsForDisplay(workflow);
  const nodeDiagnostics = node
    ? diagnosticsForNode(workflowDiagnostics, node, agentConfig)
    : [];
  const nodeBindings = node
    ? (workflow.validationBindings ?? []).filter(
        (binding) =>
          binding.destinationNode === node.id &&
          !["invalid", "type-incompatible"].includes(binding.status),
      )
    : [];
  const agentDiagnostics = agentConfig
    ? diagnosticsForAgent(workflowDiagnostics, operation.agent_id, agentConfig)
    : [];
  const edgeDiagnostics = edge ? diagnosticsForEdge(workflowDiagnostics, edge) : [];
  const workflowFieldDiagnostics = (...fields) =>
    diagnosticsForField(workflowDiagnostics, ...fields);
  const nodeFieldDiagnostics = (...fields) => diagnosticsForField(nodeDiagnostics, ...fields);
  const edgeFieldDiagnostics = (...fields) => diagnosticsForField(edgeDiagnostics, ...fields);

  const edgeId = edge?.id;
  const nodeId = node?.id;

  useEffect(() => {
    setEdgeInspectorOpen(Boolean(edgeId));
    setDraftEdge(null);
  }, [edgeId, nodeId]);

  useEffect(() => {
    setNodeTab("general");
  }, [nodeId]);

  useEffect(() => {
    if (!window.goferDesktop?.workspace?.grantPath) return;
    for (const entry of filesystemAccess) {
      if (entry?.path) {
        window.goferDesktop.workspace.grantPath(entry.path).catch((error) => {
          console.error("Failed to trust workflow path", error);
        });
      }
    }
  }, [filesystemAccess]);

  function updateWorkflowSchedule(patch) {
    const currentSchedule = schedule ?? { cron_expression: "0 9 * * *", timezone: "UTC" };
    const nextSchedule = { ...currentSchedule, ...patch };
    onWorkflowChange({ schedule: nextSchedule });
  }

  function updateWorkflowWatch(patch) {
    const currentWatch = watch ?? {
      path: dataDir || "",
      glob: "*",
      recursive: false,
      debounce_seconds: 1,
      mode: "batch",
      max_concurrency: 1,
    };
    onWorkflowChange({ watch: { ...currentWatch, ...patch } });
  }

  function updateWorkflowWebhook(triggerId, patch) {
    const currentWebhook = webhooks[triggerId] ?? {
      id: triggerId,
      enabled: true,
      source: "webhook",
      concurrency_policy: "allow",
    };
    onWorkflowChange({
      webhooks: {
        ...webhooks,
        [triggerId]: { ...currentWebhook, ...patch, id: triggerId },
      },
    });
  }

  function addWorkflowWebhook() {
    let index = 1;
    let triggerId = "default";
    while (webhooks[triggerId]) {
      index += 1;
      triggerId = `webhook-${index}`;
    }
    updateWorkflowWebhook(triggerId, {});
  }

  function removeWorkflowWebhook(triggerId) {
    const nextWebhooks = { ...webhooks };
    delete nextWebhooks[triggerId];
    onWorkflowChange({ webhooks: nextWebhooks });
  }

  function updateFilesystemAccess(index, patch) {
    const current = workflow.filesystemAccess ?? [];
    onWorkflowChange({
      filesystemAccess: uniqueAccessEntries(
        current.map((entry, currentIndex) =>
          currentIndex === index ? { ...entry, ...patch } : entry,
        ),
      ),
    });
  }

  function addFilesystemAccess(pathValue = filesystemPathDraft) {
    const path = String(pathValue ?? "").trim();
    if (!path) return;
    onWorkflowChange({
      filesystemAccess: uniqueAccessEntries([...filesystemAccess, { path }]),
    });
    setFilesystemPathDraft("");
    setAddingFilesystemPath(false);
    window.goferDesktop?.workspace?.grantPath?.(path).catch((error) => {
      console.error("Failed to trust workflow path", error);
    });
  }

  function removeFilesystemAccess(index) {
    onWorkflowChange({
      filesystemAccess: (workflow.filesystemAccess ?? []).filter(
        (_entry, currentIndex) => currentIndex !== index,
      ),
    });
  }

  return (
    <aside
      id="workflow-inspector"
      aria-label="Workflow settings and node inspector"
      className="absolute bottom-0 right-0 top-0 z-40 shrink-0 overflow-visible border-l border-line bg-white shadow-panel transition-[width] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]"
      style={{ width: collapsed ? 0 : width }}
      tabIndex={-1}
    >
      {!collapsed ? (
        <div
          aria-label="Resize workflow settings and node inspector"
          aria-orientation="vertical"
          aria-valuemax={520}
          aria-valuemin={280}
          aria-valuenow={width}
          aria-valuetext={`${width} pixels wide`}
          className="absolute left-[-3px] top-0 z-20 h-full w-1.5 cursor-col-resize transition hover:bg-brand/40"
          role="separator"
          tabIndex={0}
          title="Resize workflow settings and node inspector"
          onKeyDown={onResizeKeyDown}
          onPointerDown={onResizeStart}
        />
      ) : null}
      {!collapsed && edge ? (
        <button
          className="absolute left-[-40px] top-3 z-40 flex h-8 w-8 items-center justify-center rounded-[10px] border border-line bg-white text-muted shadow-panel transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink"
          title="Hide workflow settings and node inspector"
          type="button"
          onClick={onToggleCollapsed}
        >
          <X size={15} />
        </button>
      ) : null}

      <div
        className={`${node ? "h-full overflow-hidden" : "workflow-scrollbar h-full overflow-y-auto"} transition-opacity duration-200 ${
          collapsed ? "pointer-events-none opacity-0" : "opacity-100 delay-100"
        }`}
      >
        {!node && !edge ? (
          <div className="flex h-full min-h-0 flex-col overflow-hidden">
            <header className="flex h-[52px] shrink-0 items-center justify-between border-b border-line px-3.5">
              <div className="flex min-w-0 items-center gap-2.5">
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-slate-100 text-muted">
                  <Settings2 size={14} />
                </span>
                <div className="min-w-0">
                  <h2 className="truncate text-[13px] font-semibold text-ink">Workflow settings</h2>
                  <p className="truncate font-mono text-[10px] text-muted">{workflow.id}</p>
                </div>
              </div>
              <button
                className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
                title="Hide workflow settings and node inspector"
                type="button"
                onClick={onToggleCollapsed}
              >
                <X size={15} />
              </button>
            </header>
            <nav aria-label="Workflow settings sections" className="grid shrink-0 grid-cols-4 gap-0.5 border-b border-line px-2 pt-2">
              {[
                ["general", "General"],
                ["triggers", "Triggers"],
                ["variables", "Variables"],
                ["access", "Access"],
              ].map(([id, label]) => (
                <button
                  key={id}
                  aria-selected={workflowTab === id}
                  className={`h-8 border-b-2 px-1 text-[11px] font-semibold transition ${
                    workflowTab === id
                      ? "border-indigo-600 text-ink"
                      : "border-transparent text-muted hover:text-ink"
                  }`}
                  role="tab"
                  type="button"
                  onClick={() => setWorkflowTab(id)}
                >
                  {label}
                </button>
              ))}
            </nav>
            <div className="workflow-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
            {workflowTab === "general" ? (
              <WorkflowSettingsSection title="General">
              <TextField
                label="Name"
                value={workflow.name}
                onChange={(value) => onWorkflowChange({ name: value })}
              />
              {workflow.sourcePath ? (
                <TextField label="Source path" value={workflow.sourcePath} readOnly pathLink />
              ) : null}
              <HealthDiagnosticList diagnostics={workflowDiagnostics} onApplyFix={onApplyFix} />
              <NumberField
                label="Max total node runs"
                min="1"
                value={workflow.maxTotalNodeRuns ?? 1000}
                onChange={(value) => onWorkflowChange({ maxTotalNodeRuns: value || 1000 })}
              />
              <ToggleField
                checked={Boolean(workflow.runContinuously)}
                diagnostics={workflowFieldDiagnostics("runContinuously")}
                label="Run continuously"
                onChange={(checked) => onWorkflowChange({ runContinuously: checked })}
              />
              {workflow.runContinuously ? (
                <p className="text-sm leading-6 text-muted">
                  Continuous mode keeps one run active and overrides schedule and file watcher starts.
                  Stop all runs to turn it off.
                </p>
              ) : null}
            </WorkflowSettingsSection>
            ) : null}

            {workflowTab === "variables" ? (
              <WorkflowSettingsSection title="Variables">
                <JsonBodyField
                  label="Input schema (JSON object)"
                  value={workflow.inputs ?? workflow.parameters ?? {}}
                  onChange={(value) => onWorkflowChange({ inputs: value ?? {}, parameters: {} })}
                />
                <JsonBodyField
                  label="Initial variables (JSON object)"
                  value={workflow.variables ?? {}}
                  onChange={(value) => onWorkflowChange({ variables: value ?? {} })}
                />
                <JsonBodyField
                  label="Named output schemas (JSON object)"
                  value={workflow.outputSchemas ?? {}}
                  onChange={(value) => onWorkflowChange({ outputSchemas: value ?? {} })}
                />
                <p className="text-xs leading-5 text-muted">
                  Inputs are immutable for one run. Variable changes stay isolated to that run.
                </p>
              </WorkflowSettingsSection>
            ) : null}

            {workflowTab === "access" ? (
            <WorkflowSettingsSection title="Filesystem access">
              <div className="grid gap-3">
                {dataDir ? (
                  <div className="rounded-lg border border-line bg-slate-50 p-3">
                    <TextField label="Project folder" value={dataDir} readOnly pathLink />
                    <p className="mt-2 text-xs leading-5 text-muted">
                      The project folder is trusted automatically.
                    </p>
                  </div>
                ) : null}
                {manualFilesystemAccess.map(({ entry, index }) => (
                  <div
                    key={`${entry.path}-${index}`}
                    className="rounded-lg border border-line bg-slate-50 p-3"
                  >
                    <div className="flex items-start gap-2">
                      <div className="min-w-0 flex-1">
                        <TextField
                          label="Trusted directory"
                          value={entry.path ?? ""}
                          onChange={(value) => updateFilesystemAccess(index, { path: value })}
                          pathPicker
                          pathBasePath={dataDir}
                          pathLink
                          placeholder="/absolute/path"
                        />
                      </div>
                      <button
                        className="mt-6 grid h-8 w-8 shrink-0 place-items-center rounded-md border border-line bg-white text-muted transition hover:border-red-200 hover:bg-red-50 hover:text-red-700"
                        title="Remove trusted directory"
                        type="button"
                        onClick={() => removeFilesystemAccess(index)}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                    <div className="mt-3 grid gap-2">
                      <ToggleField
                        checked={entry.read ?? true}
                        label="Read files"
                        onChange={(checked) => updateFilesystemAccess(index, { read: checked })}
                      />
                      <ToggleField
                        checked={entry.write ?? true}
                        label="Write files"
                        onChange={(checked) => updateFilesystemAccess(index, { write: checked })}
                      />
                      <p className="text-xs leading-5 text-muted">
                        Write access allows this workflow to create, change, move, and delete files
                        in this directory.
                      </p>
                      <ToggleField
                        checked={entry.execute ?? false}
                        label="Execute files"
                        onChange={(checked) => updateFilesystemAccess(index, { execute: checked })}
                      />
                      <p className="text-xs leading-5 text-muted">
                        Execute access allows this workflow to run programs from this directory.
                      </p>
                    </div>
                  </div>
                ))}
                {addingFilesystemPath ? (
                  <div className="rounded-lg border border-brand/30 bg-white p-3 shadow-sm">
                    <TextField
                      label="Trusted directory"
                      value={filesystemPathDraft}
                      onChange={setFilesystemPathDraft}
                      pathPicker
                      pathBasePath={dataDir}
                      placeholder="/absolute/path"
                    />
                    <div className="mt-3 flex justify-end gap-2">
                      <button
                        className="h-8 rounded-md border border-line bg-white px-3 text-xs font-medium text-muted transition hover:bg-slate-50 hover:text-ink"
                        type="button"
                        onClick={() => {
                          setFilesystemPathDraft("");
                          setAddingFilesystemPath(false);
                        }}
                      >
                        Cancel
                      </button>
                      <button
                        className="h-8 rounded-md bg-brand px-3 text-xs font-semibold text-white transition hover:bg-brand-strong disabled:cursor-not-allowed disabled:opacity-50"
                        type="button"
                        disabled={!filesystemPathDraft.trim()}
                        onClick={() => addFilesystemAccess()}
                      >
                        Add
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    className="inline-flex h-8 items-center justify-center gap-2 rounded-md border border-line bg-white px-3 text-xs font-medium text-ink transition hover:bg-slate-50"
                    type="button"
                    onClick={() => setAddingFilesystemPath(true)}
                  >
                    <Plus size={14} />
                    Add trusted directory
                  </button>
                )}
                <p className="text-xs leading-5 text-muted">
                  Trusted directories and their selected permissions are saved with the workflow.
                  Codex or Claude agent nodes receive eligible directories as sandbox paths.
                </p>
              </div>
            </WorkflowSettingsSection>
            ) : null}

            {workflowTab === "triggers" ? (
            <>
            <InspectorSection
              title="Schedule"
              className={workflow.runContinuously ? "opacity-50" : ""}
            >
              <ToggleField
                checked={Boolean(schedule)}
                disabled={Boolean(workflow.runContinuously)}
                label="Scheduled"
                onChange={(checked) =>
                  onWorkflowChange({
                    schedule: checked
                      ? schedule ?? { cron_expression: "0 9 * * *", timezone: "UTC" }
                      : null,
                  })
                }
              />
              {schedule ? (
                <>
                  <CronExpressionField
                    diagnostics={workflowFieldDiagnostics("cron_expression")}
                    label="Cron expression"
                    value={schedule.cron_expression ?? ""}
                    onChange={(value) => updateWorkflowSchedule({ cron_expression: value })}
                    placeholder="0 9 * * *"
                    pickerOpen={cronPickerOpen}
                    onPickerOpenChange={setCronPickerOpen}
                  />
                  <TextField
                    diagnostics={workflowFieldDiagnostics("timezone")}
                    label="Timezone"
                    value={schedule.timezone ?? "UTC"}
                    onChange={(value) => updateWorkflowSchedule({ timezone: value })}
                    placeholder="UTC"
                  />
                  <JsonBodyField
                    label="Invocation inputs (JSON object)"
                    value={schedule.inputs ?? {}}
                    onChange={(value) =>
                      updateWorkflowSchedule({ inputs: value ?? {}, params: {} })
                    }
                  />
                  <p className="text-xs leading-5 text-muted">
                    Values are validated against the workflow input schema when the schedule runs.
                  </p>
                </>
              ) : (
                <p className="text-sm leading-6 text-muted">
                  Turn scheduling on to persist a cron expression and timezone in the workflow TOML.
                </p>
              )}
            </InspectorSection>

            <InspectorSection
              title="File watcher"
              className={workflow.runContinuously ? "opacity-50" : ""}
            >
              <ToggleField
                checked={Boolean(watch)}
                disabled={Boolean(workflow.runContinuously)}
                label="Watch files"
                onChange={(checked) =>
                  onWorkflowChange({
                    watch: checked
                      ? watch ?? {
                          path: dataDir || "",
                          glob: "*",
                          recursive: false,
                          debounce_seconds: 1,
                          mode: "batch",
                          max_concurrency: 1,
                        }
                      : null,
                  })
                }
              />
              {watch ? (
                <>
                  <TextField
                    diagnostics={workflowFieldDiagnostics("path")}
                    label="Path"
                    value={watch.path ?? ""}
                    onChange={(value) => updateWorkflowWatch({ path: value })}
                    pathPicker
                    pathBasePath={dataDir}
                    placeholder="Absolute folder path"
                  />
                  <TextField
                    diagnostics={workflowFieldDiagnostics("glob")}
                    label="Glob"
                    value={watch.glob ?? "*"}
                    onChange={(value) => updateWorkflowWatch({ glob: value })}
                    placeholder="*"
                  />
                  <ToggleField
                    checked={Boolean(watch.recursive)}
                    label="Recursive"
                    onChange={(checked) => updateWorkflowWatch({ recursive: checked })}
                  />
                  <SelectField
                    label="Mode"
                    value={watch.mode ?? "batch"}
                    options={[
                      ["batch", "Batch changes into one run"],
                      ["queue", "Queue one run per file"],
                      ["fanout", "Fan-out changed files"],
                    ]}
                    onChange={(value) => updateWorkflowWatch({ mode: value })}
                  />
                  <NumberField
                    label="Max concurrency"
                    min="1"
                    value={watch.max_concurrency ?? 1}
                    onChange={(value) => updateWorkflowWatch({ max_concurrency: value || 1 })}
                  />
                  <NumberField
                    label="Debounce seconds"
                    min="0"
                    step="0.1"
                    value={watch.debounce_seconds ?? 1}
                    onChange={(value) => updateWorkflowWatch({ debounce_seconds: value || 0 })}
                  />
                  <JsonBodyField
                    label="Invocation inputs (JSON object)"
                    value={watch.inputs ?? {}}
                    onChange={(value) =>
                      updateWorkflowWatch({ inputs: value ?? {}, params: {} })
                    }
                  />
                  <p className="text-xs leading-5 text-muted">
                    File-event data stays under trigger; these values populate immutable workflow
                    inputs.
                  </p>
                </>
              ) : (
                <p className="text-sm leading-6 text-muted">
                  Turn file watching on to run this workflow when a watched file changes.
                </p>
              )}
            </InspectorSection>

            <InspectorSection title="Webhook/API triggers">
              <div className="grid gap-3">
                {Object.entries(webhooks).map(([triggerId, config]) => (
                  <div key={triggerId} className="rounded-lg border border-line bg-slate-50 p-3">
                    <div className="mb-3 flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-ink">{triggerId}</div>
                        <div className="truncate text-xs text-muted">
                          {config.tokenConfigured || config.token_env ? "Token required" : "No token required"}
                        </div>
                      </div>
                      <button
                        className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-line bg-white text-muted transition hover:border-red-200 hover:bg-red-50 hover:text-red-700"
                        title="Remove webhook trigger"
                        type="button"
                        onClick={() => removeWorkflowWebhook(triggerId)}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                    <ToggleField
                      checked={Boolean(config.enabled)}
                      label="Enabled"
                      onChange={(checked) => updateWorkflowWebhook(triggerId, { enabled: checked })}
                    />
                    <TextField
                      label="Source"
                      value={config.source ?? "webhook"}
                      onChange={(value) => updateWorkflowWebhook(triggerId, { source: value })}
                      placeholder="github"
                    />
                    <TextField
                      label="Fan-out path"
                      value={config.fanout_path ?? ""}
                      onChange={(value) =>
                        updateWorkflowWebhook(triggerId, { fanout_path: value || null })
                      }
                      placeholder="payload.items"
                    />
                    <TextField
                      label="Token environment variable"
                      value={config.token_env ?? ""}
                      onChange={(value) =>
                        updateWorkflowWebhook(triggerId, { token_env: value || null })
                      }
                      placeholder="GOFER_GITHUB_WEBHOOK_TOKEN"
                    />
                    <SelectField
                      label="Concurrency"
                      value={config.concurrency_policy ?? "allow"}
                      options={[
                        ["allow", "Allow concurrent runs"],
                        ["reject_if_running", "Reject while running"],
                      ]}
                      onChange={(value) =>
                        updateWorkflowWebhook(triggerId, { concurrency_policy: value })
                      }
                    />
                    <JsonBodyField
                      label="Input bindings (JSON object)"
                      value={config.input_bindings ?? {}}
                      onChange={(value) =>
                        updateWorkflowWebhook(triggerId, { input_bindings: value ?? {} })
                      }
                    />
                    <p className="mt-2 text-xs leading-5 text-muted">
                      Bind payload data with references such as
                      {" "}<code>{"{{trigger.payload.project_dir}}"}</code>.
                    </p>
                  </div>
                ))}
                <button
                  className="inline-flex h-8 items-center justify-center gap-2 rounded-md border border-line bg-white px-3 text-xs font-medium text-ink transition hover:bg-slate-50"
                  type="button"
                  onClick={addWorkflowWebhook}
                >
                  <Plus size={14} />
                  Add webhook trigger
                </button>
              </div>
            </InspectorSection>
            </>
            ) : null}
          </div>
          </div>
        ) : null}

        {edge ? (
          <InspectorPanel
            open={edgeInspectorOpen}
            subtitle={`${edge.from} -> ${edge.to}`}
            title="Edge inspector"
            onToggle={() => setEdgeInspectorOpen((current) => !current)}
          >
            <div className="space-y-4 p-4">
              <InspectorSection title="Relationship">
                <HealthDiagnosticList diagnostics={edgeDiagnostics} onApplyFix={onApplyFix} />
                <SelectField
                  diagnostics={edgeFieldDiagnostics("condition")}
                  label="Type"
                  value={edge.condition ?? "always"}
                  options={edgeConditionOptions}
                  onChange={(value) =>
                    onEdgeChange(edge.id, {
                      condition: value,
                      outputPattern:
                        value === "output_matches" ? edge.outputPattern ?? "" : null,
                      field: value === "output_field" ? edge.field ?? "" : null,
                      operator:
                        value === "output_field" ? edge.operator ?? "equals" : null,
                      value: value === "output_field" ? edge.value ?? null : null,
                    })
                  }
                />
                {edge.condition === "output_matches" ? (
                  <TextField
                    diagnostics={edgeFieldDiagnostics("outputPattern")}
                    label="Output pattern"
                    value={edge.outputPattern ?? ""}
                    onChange={(value) => onEdgeChange(edge.id, { outputPattern: value })}
                    placeholder="Regex pattern"
                  />
                ) : null}
                {edge.condition === "output_field" ? (
                  <>
                    <TextField
                      diagnostics={edgeFieldDiagnostics("field")}
                      label="Field"
                      value={edge.field ?? ""}
                      onChange={(value) => onEdgeChange(edge.id, { field: value })}
                      placeholder="priority or result.priority"
                    />
                    <SelectField
                      diagnostics={edgeFieldDiagnostics("operator")}
                      label="Operator"
                      value={edge.operator ?? "equals"}
                      options={edgeOutputFieldOperatorOptions}
                      onChange={(value) => onEdgeChange(edge.id, { operator: value })}
                    />
                    {(edge.operator ?? "equals") !== "exists" ? (
                      <JsonBodyField
                        label="Comparison value (JSON)"
                        value={edge.value}
                        onChange={(value) => onEdgeChange(edge.id, { value })}
                      />
                    ) : null}
                  </>
                ) : null}
              </InspectorSection>

              <InspectorSection title="Endpoints">
                <SelectField
                  diagnostics={edgeFieldDiagnostics("from")}
                  label="Source"
                  value={edge.from}
                  options={nodes.map((candidate) => [
                    candidate.id,
                    candidate.label || candidate.id,
                  ])}
                  onChange={(value) => onEdgeChange(edge.id, { from: value })}
                />
                <SelectField
                  diagnostics={edgeFieldDiagnostics("to")}
                  label="Target"
                  value={edge.to}
                  options={nodes.map((candidate) => [
                    candidate.id,
                    candidate.label || candidate.id,
                  ])}
                  onChange={(value) => onEdgeChange(edge.id, { to: value })}
                />
              </InspectorSection>
            </div>
          </InspectorPanel>
        ) : null}

        {node ? (
          <div className="flex h-full min-h-0 flex-col overflow-hidden">
            <header className="flex h-[52px] shrink-0 items-center justify-between border-b border-line px-3.5">
              <div className="flex min-w-0 items-center gap-2.5">
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-slate-100 text-muted">
                  <Braces size={14} />
                </span>
                <div className="min-w-0">
                  <h2 className="truncate text-[13px] font-semibold text-ink">Node inspector</h2>
                  <p className="truncate font-mono text-[10px] text-muted">{node.id}</p>
                </div>
              </div>
              <button
                className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
                title="Hide node inspector"
                type="button"
                onClick={onToggleCollapsed}
              >
                <X size={15} />
              </button>
            </header>
            <nav
              aria-label="Node inspector sections"
              className="grid shrink-0 grid-cols-5 gap-0.5 border-b border-line px-2 pt-2"
              role="tablist"
            >
              {[
                ["general", "General"],
                ["action", "Action"],
                ["inputs", "Inputs"],
                ["run", "Run"],
                ["edges", "Edges"],
              ].map(([id, label]) => (
                <button
                  key={id}
                  id={`node-tab-${id}`}
                  data-inspector-tab={id}
                  aria-controls={`node-tabpanel-${id}`}
                  aria-selected={nodeTab === id}
                  className={`h-8 border-b-2 px-1 text-[11px] font-semibold transition ${
                    nodeTab === id
                      ? "border-indigo-600 text-ink"
                      : "border-transparent text-muted hover:text-ink"
                  }`}
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
            <div className="workflow-scrollbar min-h-0 flex-1 overflow-y-auto">
              <div
                id="node-tabpanel-general"
                aria-labelledby="node-tab-general"
                className="node-inspector-panel space-y-4 p-4"
                hidden={nodeTab !== "general"}
                role="tabpanel"
                tabIndex={0}
              >
          <InspectorSection title="Node">
            <TextField
              label="Label"
              value={specialNodeLabel(node.type) ?? node.label}
              onChange={(value) => onNodeChange({ label: value })}
              readOnly={isSpecialNodeType(node.type)}
            />
            <SelectField
              label="Type"
              value={node.type}
              options={nodeTypeOptions}
              onChange={onTypeChange}
            />
            <HealthDiagnosticList diagnostics={nodeDiagnostics} onApplyFix={onApplyFix} />
            <NodeBindingList bindings={nodeBindings} />
          </InspectorSection>
              </div>

              <div
                id="node-tabpanel-run"
                aria-labelledby="node-tab-run"
                className="node-inspector-panel space-y-4 p-4"
                hidden={nodeTab !== "run"}
                role="tabpanel"
                tabIndex={0}
              >
          <InspectorSection title="Execution">
            <ToggleField
              checked={Boolean(settings.pipeOutput)}
              label="Pipe output"
              onChange={(checked) => onSettingsChange({ pipeOutput: checked })}
            />
            <ToggleField
              checked={settings.awaitAllInputs !== false}
              label="Await all inputs"
              onChange={(checked) => onSettingsChange({ awaitAllInputs: checked })}
            />
            {!settings.awaitAllInputs ? (
              <p className="text-sm leading-6 text-muted">
                This node can run as soon as any incoming edge is ready. Use this for loop entry points.
              </p>
            ) : null}
            <ToggleField
              checked={Boolean(settings.allowFailure)}
              label="Allow failure"
              onChange={(checked) => onSettingsChange({ allowFailure: checked })}
            />
            {settings.allowFailure ? (
              <p className="text-sm leading-6 text-muted">
                Failed output can still trigger on-failure edges, but it will not fail the whole workflow.
              </p>
            ) : null}
            <TextField
              diagnostics={nodeFieldDiagnostics("for_each")}
              label="For each"
              placeholder="{{inputs.items}}"
              value={settings.forEach ?? ""}
              onChange={(value) => onSettingsChange({ forEach: value })}
            />
            {settings.forEach ? (
              <>
                <NumberField
                  allowRuntimeReference
                  diagnostics={nodeFieldDiagnostics("max_concurrency")}
                  label="Fan-out max concurrency"
                  min="1"
                  value={settings.maxConcurrency ?? 1}
                  onChange={(value) =>
                    onSettingsChange({ maxConcurrency: value || 1 })
                  }
                />
                <ToggleField
                  allowRuntimeReference
                  checked={settings.failFast ?? false}
                  diagnostics={nodeFieldDiagnostics("fail_fast")}
                  label="Fan-out fail fast"
                  onChange={(checked) => onSettingsChange({ failFast: checked })}
                />
              </>
            ) : null}
            <NumberField
              label="Retry count"
              min="0"
              value={settings.retryCount}
              onChange={(value) => onSettingsChange({ retryCount: value })}
            />
            <NumberField
              label="Retry delay seconds"
              min="0"
              step="0.1"
              value={settings.retryDelaySeconds}
              onChange={(value) => onSettingsChange({ retryDelaySeconds: value })}
            />
            <NumberField
              label="Timeout seconds"
              min="0"
              value={settings.timeoutSeconds ?? ""}
              onChange={(value) => onSettingsChange({ timeoutSeconds: value })}
              placeholder="None"
            />
          </InspectorSection>

          {nodeRun ? <RunNodeInspector nodeRun={nodeRun} /> : null}
              </div>

              <div
                id="node-tabpanel-inputs"
                aria-labelledby="node-tab-inputs"
                className="node-inspector-panel space-y-4 p-4"
                hidden={nodeTab !== "inputs"}
                role="tabpanel"
                tabIndex={0}
              >
          <InspectorSection title="Inputs">
            <InputMappingField
              nodeType={node.type}
              sourceOptions={inputSourceOptions}
              value={node.inputs ?? {}}
              onChange={(value) => onNodeChange({ inputs: value })}
            />
          </InspectorSection>
              </div>

              <div
                id="node-tabpanel-action"
                aria-labelledby="node-tab-action"
                className="node-inspector-panel space-y-4 p-4"
                hidden={nodeTab !== "action"}
                role="tabpanel"
                tabIndex={0}
              >
          {operation.type === "start" ? (
            <InspectorSection title="START">
              <p className="text-sm leading-6 text-muted">
                This node does no work. It completes successfully and routes to the next matching edge.
              </p>
            </InspectorSection>
          ) : null}

          {operation.type === "pass" ? (
            <InspectorSection title="PASS">
              <TextareaField
                label="Success message"
                rows={3}
                value={operation.message ?? ""}
                onChange={(value) => onOperationChange({ message: value })}
              />
            </InspectorSection>
          ) : null}

          {operation.type === "fail" ? (
            <InspectorSection title="FAIL">
              <TextareaField
                label="Failure message"
                rows={3}
                value={operation.message ?? ""}
                onChange={(value) => onOperationChange({ message: value })}
              />
            </InspectorSection>
          ) : null}

          {operation.type === "break" ? (
            <InspectorSection title="BREAK">
              <TextareaField
                label="Break message"
                rows={3}
                value={operation.message ?? ""}
                onChange={(value) => onOperationChange({ message: value })}
              />
            </InspectorSection>
          ) : null}

          {operation.type === "loop" ? (
            <InspectorSection title="Loop">
              <SelectField
                label="Source"
                value={operation.source?.type ?? "count"}
                options={[
                  ["count", "Count"],
                  ["tabular", "JSONL or CSV rows"],
                  ["directory", "Directory files"],
                  ["trigger_events", "Trigger events"],
                  ["infinite", "Until BREAK"],
                ]}
                onChange={(value) => onOperationChange({ source: defaultFanSource(value) })}
              />
              {operation.source?.type === "count" ? (
                <NumberField
                  allowRuntimeReference
                  label="Count"
                  min="1"
                  value={String(operation.source.count ?? 1)}
                  onChange={(value) =>
                    onOperationChange({ source: { ...operation.source, count: value || 1 } })
                  }
                />
              ) : null}
              {operation.source?.type === "tabular" ? (
                <TextField
                  diagnostics={nodeFieldDiagnostics("operation.source.path")}
                  label="Path"
                  value={operation.source.path ?? ""}
                  onChange={(value) =>
                    onOperationChange({ source: { ...operation.source, path: value } })
                  }
                  pathPicker
                  pathBasePath={dataDir}
                />
              ) : null}
              {operation.source?.type === "directory" ? (
                <>
                  <TextField
                    diagnostics={nodeFieldDiagnostics("operation.source.path")}
                    label="Path"
                    value={operation.source.path ?? ""}
                    onChange={(value) =>
                      onOperationChange({ source: { ...operation.source, path: value } })
                    }
                    pathPicker
                    pathBasePath={dataDir}
                  />
                  <TextField
                    label="Glob"
                    value={operation.source.glob ?? "*"}
                    onChange={(value) =>
                      onOperationChange({ source: { ...operation.source, glob: value } })
                    }
                  />
                  <ToggleField
                    allowRuntimeReference
                    checked={operation.source.include_content ?? false}
                    label="Include content"
                    onChange={(checked) =>
                      onOperationChange({
                        source: { ...operation.source, include_content: checked },
                      })
                    }
                  />
                </>
              ) : null}
              {operation.source?.type === "trigger_events" ? (
                <ToggleField
                  allowRuntimeReference
                  checked={operation.source.include_content ?? false}
                  label="Include file content"
                  onChange={(checked) =>
                    onOperationChange({
                      source: { ...operation.source, include_content: checked },
                    })
                  }
                />
              ) : null}
              <NumberField
                allowRuntimeReference
                diagnostics={nodeFieldDiagnostics("operation.source.max_concurrency")}
                label="Max concurrency"
                min="1"
                value={operation.source?.max_concurrency ?? 1}
                onChange={(value) =>
                  onOperationChange({
                    source: { ...operation.source, max_concurrency: value || 1 },
                  })
                }
              />
              <p className="text-xs leading-5 text-muted">
                Use 1 for sequential loop iterations. Increase this only when child nodes are safe to run in parallel.
              </p>
              <ToggleField
                allowRuntimeReference
                checked={operation.source?.fail_fast ?? false}
                label="Fail fast"
                onChange={(checked) =>
                  onOperationChange({
                    source: { ...operation.source, fail_fast: checked },
                  })
                }
              />
              <p className="text-sm leading-6 text-muted">
                The loop runs its full child chain for one item, then starts the next item. Downstream nodes can use loop variables like loop.index, loop.file_path, loop.file_name, and loop.file_content.
              </p>
            </InspectorSection>
          ) : null}

          {operation.type === "bash_command" ? (
            <InspectorSection title={commandNodeLabel}>
              <TextareaField
                label="Command"
                rows={4}
                value={operation.command ?? ""}
                onChange={(value) => onOperationChange({ command: value })}
              />
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.working_dir")}
                label="Working directory"
                value={operation.working_dir ?? ""}
                onChange={(value) => onOperationChange({ working_dir: value })}
                pathPicker
                pathBasePath={dataDir}
                placeholder="Absolute working directory"
              />
              <KeyValueField
                label="Environment"
                value={operation.env ?? {}}
                onChange={(value) => onOperationChange({ env: value })}
              />
            </InspectorSection>
          ) : null}

          {operation.type === "python_script" || operation.type === "shell_script" ? (
            <InspectorSection
              title={operation.type === "python_script" ? "Python script" : "Shell script"}
            >
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.script_path")}
                label="Script path"
                value={operation.script_path ?? ""}
                onChange={(value) => onOperationChange({ script_path: value })}
                pathPicker
                pathBasePath={dataDir}
              />
              <ListField
                label="Arguments"
                value={operation.args ?? []}
                onChange={(value) => onOperationChange({ args: value })}
                placeholder="--flag, value"
              />
              <KeyValueField
                label="Environment"
                value={operation.env ?? {}}
                onChange={(value) => onOperationChange({ env: value })}
              />
            </InspectorSection>
          ) : null}

          {operation.type === "read_file" ? (
            <InspectorSection title="Read file">
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.path")}
                label="Path"
                value={operation.path ?? ""}
                onChange={(value) => onOperationChange({ path: value })}
                pathPicker
                pathBasePath={dataDir}
              />
              <TextField
                label="Encoding"
                value={operation.encoding ?? "utf-8"}
                onChange={(value) => onOperationChange({ encoding: value })}
              />
              <SelectField
                label="Decode errors"
                value={operation.errors ?? "strict"}
                options={[
                  ["strict", "Fail on invalid text"],
                  ["replace", "Replace invalid text"],
                  ["ignore", "Ignore invalid text"],
                ]}
                onChange={(value) => onOperationChange({ errors: value })}
              />
            </InspectorSection>
          ) : null}

          {operation.type === "write_file" ? (
            <InspectorSection title="Write file">
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.path")}
                label="Path"
                value={operation.path ?? ""}
                onChange={(value) => onOperationChange({ path: value })}
                pathPicker
                pathBasePath={dataDir}
              />
              <TextareaField
                label="Content"
                rows={5}
                value={operation.content ?? ""}
                onChange={(value) => onOperationChange({ content: value })}
                placeholder="Leave empty to write piped input"
              />
              <TextField
                label="Encoding"
                value={operation.encoding ?? "utf-8"}
                onChange={(value) => onOperationChange({ encoding: value })}
              />
              <ToggleField
                allowRuntimeReference
                checked={operation.create_dirs ?? true}
                label="Create parent folders"
                onChange={(checked) => onOperationChange({ create_dirs: checked })}
              />
              <ToggleField
                allowRuntimeReference
                checked={operation.overwrite ?? true}
                label="Overwrite existing file"
                onChange={(checked) => onOperationChange({ overwrite: checked })}
              />
              <ToggleField
                allowRuntimeReference
                checked={operation.append ?? false}
                label="Append instead of replace"
                onChange={(checked) => onOperationChange({ append: checked })}
              />
            </InspectorSection>
          ) : null}

          {operation.type === "copy_file" || operation.type === "move_file" ? (
            <InspectorSection title={operation.type === "copy_file" ? "Copy file" : "Move file"}>
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.source_path")}
                label="Source path"
                value={operation.source_path ?? ""}
                onChange={(value) => onOperationChange({ source_path: value })}
                pathPicker
                pathBasePath={dataDir}
              />
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.destination_path")}
                label="Destination path"
                value={operation.destination_path ?? ""}
                onChange={(value) => onOperationChange({ destination_path: value })}
                pathPicker
                pathBasePath={dataDir}
              />
              <ToggleField
                allowRuntimeReference
                checked={operation.create_dirs ?? true}
                label="Create parent folders"
                onChange={(checked) => onOperationChange({ create_dirs: checked })}
              />
              <ToggleField
                allowRuntimeReference
                checked={operation.overwrite ?? false}
                label="Overwrite existing destination"
                onChange={(checked) => onOperationChange({ overwrite: checked })}
              />
            </InspectorSection>
          ) : null}

          {operation.type === "delete_file" ? (
            <InspectorSection title="Delete file">
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.path")}
                label="Path"
                value={operation.path ?? ""}
                onChange={(value) => onOperationChange({ path: value })}
                pathPicker
                pathBasePath={dataDir}
              />
              <ToggleField
                allowRuntimeReference
                checked={operation.use_trash ?? true}
                label="Move to Raticode trash"
                onChange={(checked) => onOperationChange({ use_trash: checked })}
              />
              <ToggleField
                allowRuntimeReference
                checked={operation.recursive ?? false}
                label="Allow recursive folder delete"
                onChange={(checked) => onOperationChange({ recursive: checked })}
              />
              <ToggleField
                allowRuntimeReference
                checked={operation.missing_ok ?? false}
                label="Succeed if missing"
                onChange={(checked) => onOperationChange({ missing_ok: checked })}
              />
            </InspectorSection>
          ) : null}

          {operation.type === "file" ? (
            <InspectorSection title="File path">
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.path")}
                label="Path"
                value={operation.path ?? ""}
                onChange={(value) => onOperationChange({ path: value })}
                pathPicker
                pathBasePath={dataDir}
                placeholder="/absolute/path/to/file"
              />
            </InspectorSection>
          ) : null}

          {operation.type === "folder" ? (
            <InspectorSection title="Folder path">
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.path")}
                label="Path"
                value={operation.path ?? ""}
                onChange={(value) => onOperationChange({ path: value })}
                pathPicker
                pathBasePath={dataDir}
                placeholder="/absolute/path/to/folder"
              />
            </InspectorSection>
          ) : null}

          {operation.type === "open_resource" ? (
            <InspectorSection title="Open app / URL / file">
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.target")}
                label="Target"
                value={operation.target ?? ""}
                onChange={(value) => onOperationChange({ target: value })}
                pathPicker
                pathBasePath={dataDir}
                placeholder="File, folder, URL, or app path"
              />
              <SelectField
                allowRuntimeReference
                label="Type"
                value={operation.resource_type ?? "auto"}
                options={[
                  ["auto", "Auto"],
                  ["file", "File"],
                  ["folder", "Folder"],
                  ["url", "URL"],
                  ["app", "App"],
                ]}
                onChange={(value) => onOperationChange({ resource_type: value })}
              />
              <ListField
                label="App arguments"
                value={operation.args ?? []}
                onChange={(value) => onOperationChange({ args: value })}
                placeholder="--flag, value"
              />
            </InspectorSection>
          ) : null}

          {operation.type === "prompt_file" ? (
            <InspectorSection title="Prompt file">
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.output_path")}
                label="Output path"
                value={operation.output_path ?? ""}
                onChange={(value) => onOperationChange({ output_path: value })}
                pathPicker
                pathBasePath={dataDir}
              />
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.template_path")}
                label="Template path"
                value={operation.template_path ?? ""}
                onChange={(value) => onOperationChange({ template_path: value })}
                pathPicker
                pathBasePath={dataDir}
                placeholder="Optional"
              />
              <TextareaField
                label="Inline template"
                rows={5}
                value={operation.template ?? ""}
                onChange={(value) => onOperationChange({ template: value })}
                placeholder="Use {{variables}} and {{_piped_input}}"
              />
              <KeyValueField
                label="Variables"
                value={operation.variables ?? {}}
                onChange={(value) => onOperationChange({ variables: value })}
              />
              <TextField
                label="Encoding"
                value={operation.encoding ?? "utf-8"}
                onChange={(value) => onOperationChange({ encoding: value })}
              />
              <ToggleField
                allowRuntimeReference
                checked={operation.create_dirs ?? true}
                label="Create parent folders"
                onChange={(checked) => onOperationChange({ create_dirs: checked })}
              />
              <ToggleField
                allowRuntimeReference
                checked={operation.overwrite ?? true}
                label="Overwrite existing file"
                onChange={(checked) => onOperationChange({ overwrite: checked })}
              />
            </InspectorSection>
          ) : null}

          {operation.type === "workflow" ? (
            <InspectorSection title="Workflow call">
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.workflow_id")}
                label="Workflow ID"
                value={operation.workflow_id ?? ""}
                onChange={(value) => onOperationChange({ workflow_id: value })}
                placeholder="development-checks"
              />
              <JsonBodyField
                label="Input bindings (JSON object or quoted exact reference)"
                value={operation.input_bindings ?? {}}
                onChange={(value) => onOperationChange({ input_bindings: value ?? {} })}
              />
              <p className="text-xs leading-5 text-muted">
                Bind values from this run into the called workflow’s immutable inputs.
              </p>
            </InspectorSection>
          ) : null}

          {operation.type === "subflow" ? (
            <InspectorSection title="Subflow call">
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.component_id")}
                label="Component ID"
                value={operation.component_id ?? ""}
                onChange={(value) => onOperationChange({ component_id: value })}
                placeholder="counter"
              />
              <TextField
                diagnostics={nodeFieldDiagnostics("operation.source_path")}
                label="Source workflow"
                value={operation.source_path ?? ""}
                onChange={(value) => onOperationChange({ source_path: value })}
                pathPicker
                pathBasePath={dataDir}
                placeholder="workflows/child.toml"
              />
              <JsonBodyField
                label="Input bindings (JSON object or quoted exact reference)"
                value={operation.input_bindings ?? operation.parameter_bindings ?? {}}
                onChange={(value) =>
                  onOperationChange({ input_bindings: value ?? {}, parameter_bindings: {} })
                }
              />
              <JsonBodyField
                label="Declared outputs (JSON object)"
                value={operation.output_contract ?? {}}
                onChange={(value) => onOperationChange({ output_contract: value ?? {} })}
              />
              <p className="text-xs leading-5 text-muted">
                The child receives a fresh scope. Only bound inputs and declared outputs cross the
                boundary.
              </p>
            </InspectorSection>
          ) : null}

          {operation.type === "common_llm_task" ? (
            <>
              <InspectorSection title="Common LLM task">
                <TextField
                  diagnostics={nodeFieldDiagnostics("agent_id", "operation.agent_id")}
                  label="Agent ID"
                  value={operation.agent_id ?? ""}
                  onChange={(value) => onOperationChange({ agent_id: value })}
                />
                <SelectField
                  allowRuntimeReference
                  label="Task"
                  value={operation.task ?? "summarize"}
                  options={[
                    ["summarize", "Summarize"],
                    ["review", "Review"],
                    ["explain", "Explain"],
                    ["extract", "Extract"],
                    ["rewrite", "Rewrite"],
                    ["classify", "Classify"],
                  ]}
                  onChange={(value) => onOperationChange({ task: value })}
                />
                <SelectField
                  allowRuntimeReference
                  label="Memory"
                  value={operation.memory ?? "none"}
                  options={[
                    ["none", "None"],
                    ["run", "This run only"],
                    ["all", "All runs"],
                  ]}
                  onChange={(value) => onOperationChange({ memory: value })}
                />
                <TextareaField
                  label="Target"
                  rows={3}
                  value={operation.target ?? ""}
                  onChange={(value) => onOperationChange({ target: value })}
                  placeholder="Text, file path, URL, or leave blank for piped input"
                />
                <TextareaField
                  label="Instructions"
                  rows={4}
                  value={operation.instructions ?? ""}
                  onChange={(value) => onOperationChange({ instructions: value })}
                />
                <TextField
                  diagnostics={nodeFieldDiagnostics("operation.working_dir")}
                  label="Working directory"
                  value={operation.working_dir ?? ""}
                  onChange={(value) => onOperationChange({ working_dir: value })}
                  pathPicker
                  pathBasePath={dataDir}
                />
                <SelectField
                  label="Provider profile"
                  value={operation.profile ?? ""}
                  options={profileSelectOptions(providerProfiles, agentConfig?.subscription)}
                  onChange={(value) => onOperationChange({ profile: value })}
                />
                <ProviderModelEffortFields
                  allowInheritedModel
                  capabilities={providerCapabilities}
                  effort={operation.effort ?? ""}
                  model={operation.model ?? ""}
                  provider={agentConfig?.subscription}
                  showProvider={false}
                  onChange={(patch) => onOperationChange(patch)}
                  onRefresh={onProviderCapabilitiesRefresh}
                />
                <NumberField
                  allowRuntimeReference
                  label="Timeout override"
                  min="0"
                  step="1"
                  value={operation.timeout ?? ""}
                  onChange={(value) => onOperationChange({ timeout: value || "" })}
                  placeholder="Seconds"
                />
                <KeyValueField
                  label="Input mapping"
                  value={operation.input_mapping ?? {}}
                  onChange={(value) => onOperationChange({ input_mapping: value })}
                />
                <JsonBodyField
                  label="Output schema (JSON Schema or quoted schema name)"
                  value={operation.output_schema ?? null}
                  onChange={(value) => onOperationChange({ output_schema: value })}
                />
                <NumberField
                  allowRuntimeReference
                  label="Structured output repair attempts"
                  min="0"
                  max="3"
                  step="1"
                  value={operation.repair_attempts ?? 0}
                  onChange={(value) => onOperationChange({ repair_attempts: value || 0 })}
                />
              </InspectorSection>
              <AgentConfigSection
                agentConfig={agentConfig}
                diagnostics={agentDiagnostics}
                agentId={operation.agent_id}
                pathBasePath={dataDir}
                providerProfiles={providerProfiles}
                providerCapabilities={providerCapabilities}
                onProviderProfilesChange={onProviderProfilesChange}
                onProviderCapabilitiesRefresh={onProviderCapabilitiesRefresh}
                onAgentChange={onAgentChange}
              />
            </>
          ) : null}

          {operation.type === "local_vectorize" ? (
            <>
              <InspectorSection title="Local vector index">
                <TextField
                  diagnostics={nodeFieldDiagnostics("operation.source_path")}
                  label="Source path"
                  value={operation.source_path ?? ""}
                  onChange={(value) => onOperationChange({ source_path: value })}
                  pathPicker
                  pathBasePath={dataDir}
                />
                <TextField
                  diagnostics={nodeFieldDiagnostics("operation.index_path")}
                  label="Index path"
                  value={operation.index_path ?? ""}
                  onChange={(value) => onOperationChange({ index_path: value })}
                  pathPicker
                  pathBasePath={dataDir}
                />
                <TextField
                  label="Glob"
                  value={operation.glob ?? "**/*"}
                  onChange={(value) => onOperationChange({ glob: value })}
                />
                <SelectField
                  allowRuntimeReference
                  label="Mode"
                  value={operation.mode ?? "incremental"}
                  options={[
                    ["incremental", "Incremental"],
                    ["full", "Full rebuild"],
                    ["validate", "Validate only"],
                    ["compact", "Compact deleted"],
                  ]}
                  onChange={(value) => onOperationChange({ mode: value })}
                />
                <ToggleField
                  allowRuntimeReference
                  checked={operation.recursive ?? true}
                  label="Recursive"
                  onChange={(checked) => onOperationChange({ recursive: checked })}
                />
                <NumberField
                  allowRuntimeReference
                  label="Chunk size"
                  min="100"
                  value={operation.chunk_size ?? 1200}
                  onChange={(value) => onOperationChange({ chunk_size: value || 1200 })}
                />
                <NumberField
                  allowRuntimeReference
                  label="Chunk overlap"
                  min="0"
                  value={operation.chunk_overlap ?? 120}
                  onChange={(value) => onOperationChange({ chunk_overlap: value || 0 })}
                />
              </InspectorSection>
              <VectorIndexStats output={nodeOutput} />
            </>
          ) : null}

          {operation.type === "local_search" ? (
            <>
              <InspectorSection title="Local search">
                <TextField
                  diagnostics={nodeFieldDiagnostics("operation.index_path")}
                  label="Index path"
                  value={operation.index_path ?? ""}
                  onChange={(value) => onOperationChange({ index_path: value })}
                  pathPicker
                  pathBasePath={dataDir}
                />
                <TextareaField
                  label="Query"
                  rows={3}
                  value={operation.query ?? ""}
                  onChange={(value) => onOperationChange({ query: value })}
                />
                <NumberField
                  allowRuntimeReference
                  label="Top K"
                  min="1"
                  value={operation.top_k ?? 5}
                  onChange={(value) => onOperationChange({ top_k: value || 5 })}
                />
                <NumberField
                  allowRuntimeReference
                  label="Score threshold"
                  min="0"
                  step="0.01"
                  value={operation.score_threshold ?? 0}
                  onChange={(value) => onOperationChange({ score_threshold: value || 0 })}
                />
                <ToggleField
                  allowRuntimeReference
                  checked={operation.include_snippets ?? true}
                  label="Include snippets"
                  onChange={(checked) => onOperationChange({ include_snippets: checked })}
                />
                <ToggleField
                  allowRuntimeReference
                  checked={operation.include_file_metadata ?? true}
                  label="Include file metadata"
                  onChange={(checked) => onOperationChange({ include_file_metadata: checked })}
                />
              </InspectorSection>
              <VectorSearchStats output={nodeOutput} />
            </>
          ) : null}

          {operation.type === "http_request" ? (
            <>
              <InspectorSection title="HTTP request">
                <SelectField
                  label="Method"
                  value={operation.method ?? "GET"}
                  options={[
                    ["GET", "GET"],
                    ["POST", "POST"],
                    ["PUT", "PUT"],
                    ["PATCH", "PATCH"],
                    ["DELETE", "DELETE"],
                    ["HEAD", "HEAD"],
                  ]}
                  onChange={(value) => onOperationChange({ method: value })}
                />
                <TextField
                  label="URL"
                  value={operation.url ?? ""}
                  onChange={(value) => onOperationChange({ url: value })}
                  placeholder="https://api.example.com/resource"
                />
                <KeyValueField
                  label="Headers"
                  value={operation.headers ?? {}}
                  onChange={(value) => onOperationChange({ headers: value })}
                />
                <KeyValueField
                  label="Query params"
                  value={operation.params ?? {}}
                  onChange={(value) => onOperationChange({ params: value })}
                />
                <JsonBodyField
                  label="JSON body"
                  value={operation.json}
                  onChange={(value) => onOperationChange({ json: value })}
                />
                <TextareaField
                  label="Raw body"
                  rows={4}
                  value={operation.body ?? ""}
                  onChange={(value) => onOperationChange({ body: value })}
                />
                <ListField
                  label="Expected statuses"
                  value={runtimeListEditorValue(operation.expected_statuses ?? [200])}
                  onChange={(value) =>
                    onOperationChange({ expected_statuses: runtimeIntegerListValue(value) })
                  }
                  placeholder="200, 201"
                />
                <SelectField
                  allowRuntimeReference
                  label="Response mode"
                  value={operation.response_mode ?? "auto"}
                  options={[
                    ["auto", "Auto"],
                    ["json", "JSON"],
                    ["text", "Text"],
                    ["none", "None"],
                  ]}
                  onChange={(value) => onOperationChange({ response_mode: value })}
                />
                <NumberField
                  allowRuntimeReference
                  label="Timeout seconds"
                  min="0.1"
                  step="0.1"
                  value={operation.timeout_seconds ?? 30}
                  onChange={(value) => onOperationChange({ timeout_seconds: value || 30 })}
                />
                <NumberField
                  allowRuntimeReference
                  label="Retry attempts"
                  min="1"
                  value={operation.retry?.attempts ?? 1}
                  onChange={(value) =>
                    onOperationChange({
                      retry: { ...(operation.retry ?? {}), attempts: value || 1 },
                    })
                  }
                />
                <NumberField
                  allowRuntimeReference
                  label="Retry backoff seconds"
                  min="0"
                  step="0.1"
                  value={operation.retry?.backoff_seconds ?? 0}
                  onChange={(value) =>
                    onOperationChange({
                      retry: { ...(operation.retry ?? {}), backoff_seconds: value || 0 },
                    })
                  }
                />
                <ListField
                  label="Retry statuses"
                  value={runtimeListEditorValue(operation.retry?.retry_on_statuses ?? [])}
                  onChange={(value) =>
                    onOperationChange({
                      retry: {
                        ...(operation.retry ?? {}),
                        retry_on_statuses: runtimeIntegerListValue(value),
                      },
                    })
                  }
                  placeholder="429, 503"
                />
                <KeyValueField
                  label="Output mapping"
                  value={operation.output_mapping ?? {}}
                  onChange={(value) => onOperationChange({ output_mapping: value })}
                />
                <ListField
                  label="Secret fields"
                  value={operation.secret_fields ?? []}
                  onChange={(value) => onOperationChange({ secret_fields: value })}
                  placeholder="Authorization, api_key"
                />
              </InspectorSection>
              <HttpResponsePreview output={nodeOutput} />
            </>
          ) : null}

          {operation.type === "approval_gate" ? (
            <>
              <InspectorSection title="Approval gate">
                <TextareaField
                  label="Message"
                  rows={4}
                  value={operation.message ?? ""}
                  onChange={(value) => onOperationChange({ message: value })}
                />
                <NumberField
                  allowRuntimeReference
                  label="Timeout seconds"
                  min="0"
                  step="1"
                  value={operation.timeout_seconds ?? ""}
                  onChange={(value) =>
                    onOperationChange({ timeout_seconds: value === "" ? null : value })
                  }
                  placeholder="None"
                />
                <SelectField
                  allowRuntimeReference
                  label="Timeout decision"
                  value={operation.timeout_decision ?? "timeout"}
                  options={[
                    ["timeout", "Timeout"],
                    ["reject", "Reject"],
                  ]}
                  onChange={(value) => onOperationChange({ timeout_decision: value })}
                />
                <ListField
                  label="Approvers"
                  value={operation.approvers ?? []}
                  onChange={(value) => onOperationChange({ approvers: value })}
                  placeholder="alice, ops-team"
                />
                <ToggleField
                  allowRuntimeReference
                  checked={operation.notify ?? false}
                  label="Desktop notification"
                  onChange={(checked) => onOperationChange({ notify: checked })}
                />
                <TextField
                  label="Notification title"
                  value={operation.notification_title ?? "Raticode approval needed"}
                  onChange={(value) => onOperationChange({ notification_title: value })}
                />
              </InspectorSection>
              <ApprovalRuntimePanel approval={approval} onDecideApproval={onDecideApproval} />
            </>
          ) : null}

          {operation.type === "notification" ? (
            <InspectorSection title="Notification">
              <TextField
                label="Title"
                value={operation.title ?? ""}
                onChange={(value) => onOperationChange({ title: value })}
              />
              <TextareaField
                label="Body"
                rows={5}
                value={operation.body ?? ""}
                onChange={(value) => onOperationChange({ body: value })}
              />
              <SelectField
                allowRuntimeReference
                label="Channel"
                value={operation.channel ?? "desktop"}
                options={[
                  ["desktop", "Desktop"],
                  ["slack", "Slack"],
                  ["teams", "Microsoft Teams"],
                  ["webhook", "Webhook"],
                  ["email", "Email"],
                ]}
                onChange={(value) => onOperationChange({ channel: value })}
              />
              <SelectField
                allowRuntimeReference
                label="Urgency"
                value={operation.urgency ?? "normal"}
                options={[
                  ["low", "Low"],
                  ["normal", "Normal"],
                  ["critical", "Critical"],
                ]}
                onChange={(value) => onOperationChange({ urgency: value })}
              />
              {["slack", "teams", "webhook"].includes(operation.channel) ? (
                <>
                  <TextField
                    label="Webhook URL"
                    value={operation.webhook_url ?? ""}
                    onChange={(value) => onOperationChange({ webhook_url: value })}
                    placeholder="https://hooks.example.com/services/..."
                  />
                  <KeyValueField
                    label="Headers"
                    value={operation.headers ?? {}}
                    onChange={(value) => onOperationChange({ headers: value })}
                  />
                  <JsonBodyField
                    label="Payload (JSON)"
                    value={operation.payload}
                    onChange={(value) => onOperationChange({ payload: value })}
                  />
                </>
              ) : null}
              {operation.channel === "email" ? (
                <>
                  <TextField
                    label="From address"
                    value={operation.email_from ?? ""}
                    onChange={(value) => onOperationChange({ email_from: value })}
                    placeholder="gofer@example.com"
                  />
                  <ListField
                    label="Recipients"
                    value={operation.email_to ?? []}
                    onChange={(value) => onOperationChange({ email_to: value })}
                    placeholder="ops@example.com, owner@example.com"
                  />
                  <TextField
                    label="SMTP host"
                    value={operation.smtp_host ?? ""}
                    onChange={(value) => onOperationChange({ smtp_host: value })}
                    placeholder="smtp.example.com"
                  />
                  <NumberField
                    allowRuntimeReference
                    label="SMTP port"
                    min="1"
                    value={operation.smtp_port ?? 587}
                    onChange={(value) => onOperationChange({ smtp_port: value || 587 })}
                  />
                  <TextField
                    label="SMTP username"
                    value={operation.smtp_username ?? ""}
                    onChange={(value) => onOperationChange({ smtp_username: value })}
                  />
                  <TextField
                    label="SMTP password or secret reference"
                    value={operation.smtp_password ?? ""}
                    onChange={(value) => onOperationChange({ smtp_password: value })}
                    placeholder="{{secret.SMTP_PASSWORD}}"
                  />
                  <ToggleField
                    allowRuntimeReference
                    checked={operation.smtp_starttls ?? true}
                    label="Use STARTTLS"
                    onChange={(checked) => onOperationChange({ smtp_starttls: checked })}
                  />
                </>
              ) : null}
              {operation.channel !== "desktop" ? (
                <>
                  <NumberField
                    allowRuntimeReference
                    label="Timeout seconds"
                    min="0.1"
                    value={operation.timeout_seconds ?? 30}
                    onChange={(value) => onOperationChange({ timeout_seconds: value || 30 })}
                  />
                  <NumberField
                    allowRuntimeReference
                    label="Retry attempts"
                    min="1"
                    value={operation.retry?.attempts ?? 1}
                    onChange={(value) =>
                      onOperationChange({
                        retry: { ...(operation.retry ?? {}), attempts: value || 1 },
                      })
                    }
                  />
                  <NumberField
                    allowRuntimeReference
                    label="Retry backoff seconds"
                    min="0"
                    value={operation.retry?.backoff_seconds ?? 0}
                    onChange={(value) =>
                      onOperationChange({
                        retry: { ...(operation.retry ?? {}), backoff_seconds: value || 0 },
                      })
                    }
                  />
                  <ListField
                    label="Retry statuses"
                    value={runtimeListEditorValue(operation.retry?.retry_on_statuses ?? [])}
                    onChange={(value) =>
                      onOperationChange({
                        retry: {
                          ...(operation.retry ?? {}),
                          retry_on_statuses: runtimeIntegerListValue(value),
                        },
                      })
                    }
                    placeholder="429, 503"
                  />
                  <ListField
                    label="Expected statuses"
                    value={runtimeListEditorValue(
                      operation.expected_statuses ?? [200, 201, 202, 204],
                    )}
                    onChange={(value) =>
                      onOperationChange({
                        expected_statuses: runtimeIntegerListValue(value),
                      })
                    }
                    placeholder="200, 201, 202, 204"
                  />
                  <ListField
                    label="Network allowlist"
                    value={operation.network_allowlist ?? []}
                    onChange={(value) => onOperationChange({ network_allowlist: value })}
                    placeholder="hooks.slack.com, outlook.office.com"
                  />
                </>
              ) : null}
            </InspectorSection>
          ) : null}

          {operation.type === "agent" ? (
            <>
              <InspectorSection title="Agent node">
                <TextField
                  diagnostics={nodeFieldDiagnostics("agent_id", "operation.agent_id")}
                  label="Agent ID"
                  value={operation.agent_id ?? ""}
                  onChange={(value) => onOperationChange({ agent_id: value })}
                />
                <TextField
                  diagnostics={nodeFieldDiagnostics("operation.prompt_path")}
                  label="Prompt path"
                  value={operation.prompt_path ?? ""}
                  onChange={(value) => onOperationChange({ prompt_path: value })}
                  pathPicker
                  pathBasePath={dataDir}
                  placeholder="Optional when using a skill"
                />
                <TextField
                  label="Skill name"
                  value={operation.skill_name ?? ""}
                  onChange={(value) => onOperationChange({ skill_name: value })}
                  placeholder="gofer-flow-workflow-builder"
                />
                <TextField
                  diagnostics={nodeFieldDiagnostics("operation.working_dir")}
                  label="Working directory"
                  value={operation.working_dir ?? ""}
                  onChange={(value) => onOperationChange({ working_dir: value })}
                  pathPicker
                  pathBasePath={dataDir}
                />
                <SelectField
                  label="Provider profile"
                  value={operation.profile ?? ""}
                  options={profileSelectOptions(providerProfiles, agentConfig?.subscription)}
                  onChange={(value) => onOperationChange({ profile: value })}
                />
                <ProviderModelEffortFields
                  allowInheritedModel
                  capabilities={providerCapabilities}
                  effort={operation.effort ?? ""}
                  model={operation.model ?? ""}
                  provider={agentConfig?.subscription}
                  showProvider={false}
                  onChange={(patch) => onOperationChange(patch)}
                  onRefresh={onProviderCapabilitiesRefresh}
                />
                <NumberField
                  allowRuntimeReference
                  label="Timeout override"
                  min="0"
                  step="1"
                  value={operation.timeout ?? ""}
                  onChange={(value) => onOperationChange({ timeout: value || "" })}
                  placeholder="Seconds"
                />
                <SelectField
                  allowRuntimeReference
                  label="Memory"
                  value={operation.memory ?? "none"}
                  options={[
                    ["none", "None"],
                    ["run", "This run only"],
                    ["all", "All runs"],
                  ]}
                  onChange={(value) => onOperationChange({ memory: value })}
                />
                <KeyValueField
                  label="Input mapping"
                  value={operation.input_mapping ?? {}}
                  onChange={(value) => onOperationChange({ input_mapping: value })}
                />
                <JsonBodyField
                  label="Output schema (JSON Schema or quoted schema name)"
                  value={operation.output_schema ?? null}
                  onChange={(value) => onOperationChange({ output_schema: value })}
                />
                <NumberField
                  allowRuntimeReference
                  label="Structured output repair attempts"
                  min="0"
                  max="3"
                  step="1"
                  value={operation.repair_attempts ?? 0}
                  onChange={(value) => onOperationChange({ repair_attempts: value || 0 })}
                />
              </InspectorSection>

              <InspectorSection title="Agent config">
                <AgentConfigFields
                  agentConfig={agentConfig}
                  diagnostics={agentDiagnostics}
                  agentId={operation.agent_id}
                  pathBasePath={dataDir}
                  providerProfiles={providerProfiles}
                  providerCapabilities={providerCapabilities}
                  onProviderProfilesChange={onProviderProfilesChange}
                  onProviderCapabilitiesRefresh={onProviderCapabilitiesRefresh}
                  onAgentChange={onAgentChange}
                />
              </InspectorSection>
            </>
          ) : null}
              </div>

              <div
                id="node-tabpanel-edges"
                aria-labelledby="node-tab-edges"
                className="node-inspector-panel space-y-4 p-4"
                hidden={nodeTab !== "edges"}
                role="tabpanel"
                tabIndex={0}
              >
          <InspectorSection title="Edges">
            <div className="space-y-3">
              <div className="grid grid-cols-[1.1fr_1fr_1fr] gap-2 px-1 text-xs font-semibold uppercase tracking-[0.08em] text-muted">
                <span>Type</span>
                <span>To</span>
                <span>From</span>
              </div>

              {connectedEdges.length || draftEdge ? (
                <div className="space-y-2">
                  {connectedEdges.map((edge) => (
                    <ConnectedEdgeEditor
                      key={edge.id}
                      diagnostics={diagnosticsForEdge(workflowDiagnostics, edge)}
                      edge={edge}
                      node={node}
                      nodes={nodes}
                      onDelete={() => onDeleteEdge(edge.id)}
                      onUpdate={(patch) => onEdgeChange(edge.id, patch)}
                    />
                  ))}
                  {draftEdge ? (
                    <ConnectedEdgeEditor
                      draft
                      edge={draftEdge}
                      node={node}
                      nodes={nodes}
                      onCancel={() => setDraftEdge(null)}
                      onCreate={(nextEdge) => {
                        onAddEdge(
                          nextEdge.from,
                          nextEdge.to,
                          nextEdge.condition,
                          nextEdge.outputPattern,
                          nextEdge.field,
                          nextEdge.operator,
                          nextEdge.value,
                        );
                        setDraftEdge(null);
                      }}
                      onUpdate={setDraftEdge}
                    />
                  ) : null}
                </div>
              ) : (
                <p className="rounded-lg border border-dashed border-line bg-slate-50 px-3 py-4 text-sm text-muted">
                  This node has no connected edges.
                </p>
              )}
              {!draftEdge ? (
                <button
                  className="flex h-9 w-full items-center justify-center gap-2 rounded-lg border border-dashed border-line text-sm font-medium text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink"
                  type="button"
                  onClick={() =>
                    setDraftEdge({
                      id: "draft-edge",
                      from: "",
                      to: "",
                      condition: "",
                      outputPattern: null,
                    })
                  }
                >
                  <Plus size={15} />
                  Add edge
                </button>
              ) : null}
            </div>
          </InspectorSection>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

export function defaultFanSource(type) {
  switch (type) {
    case "count":
      return { type, count: 1, max_concurrency: 1, fail_fast: false };
    case "tabular":
      return { type, path: "data/input.csv", max_concurrency: 1, fail_fast: false };
    case "directory":
      return {
        type,
        path: "data",
        glob: "*",
        include_content: false,
        max_concurrency: 1,
        fail_fast: false,
      };
    case "trigger_events":
      return { type, include_content: false, max_concurrency: 1, fail_fast: false };
    case "infinite":
      return { type, max_concurrency: 1, fail_fast: false };
    default:
      return null;
  }
}

function ConnectedEdgeEditor({
  diagnostics = [],
  draft = false,
  edge,
  node,
  nodes,
  onCancel,
  onCreate,
  onDelete,
  onUpdate,
}) {
  const blankOption = [["", "Select"]];
  const typeValue = edge.condition || (draft ? "" : "always");
  const edgeFieldDiagnostics = (...fields) => diagnosticsForField(diagnostics, ...fields);

  function updateDraft(patch) {
    onUpdate({ ...edge, ...patch });
  }

  function handleTypeChange(value) {
    const patch = {
      condition: value,
      outputPattern: value === "output_matches" ? edge.outputPattern || "" : null,
      field: value === "output_field" ? edge.field || "" : null,
      operator: value === "output_field" ? edge.operator || "equals" : null,
      value: value === "output_field" ? edge.value ?? null : null,
    };
    if (draft) {
      updateDraft(patch);
      return;
    }
    onUpdate(patch);
  }

  function handleToChange(value) {
    if (!value) {
      if (draft) updateDraft({ to: "", from: "" });
      return;
    }

    if (draft) {
      onCreate({
        ...edge,
        from: node.id,
        to: value,
        condition: edge.condition || "always",
      });
      return;
    }

    onUpdate({ from: node.id, to: value });
  }

  function handleFromChange(value) {
    if (!value) {
      if (draft) updateDraft({ from: "", to: "" });
      return;
    }

    if (draft) {
      onCreate({
        ...edge,
        from: value,
        to: node.id,
        condition: edge.condition || "always",
      });
      return;
    }

    onUpdate({ from: value, to: node.id });
  }

  function handleBlur(event) {
    if (!draft || edge.from || edge.to) return;
    if (event.currentTarget.contains(event.relatedTarget)) return;
    onCancel?.();
  }

  return (
    <div
      className="space-y-2"
      onBlur={handleBlur}
    >
      <div className="grid min-w-0 grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1fr)_32px] gap-2">
        <EdgeSelect
          diagnostics={edgeFieldDiagnostics("condition")}
          value={typeValue}
          options={draft ? [["", "Select"], ...compactEdgeConditionOptions] : compactEdgeConditionOptions}
          onChange={handleTypeChange}
        />
        <EdgeSelect
          diagnostics={edgeFieldDiagnostics("to")}
          value={edge.to}
          options={
            draft ? [...blankOption, ...nodesForTo(nodes)] : endpointOptions(nodes)
          }
          onChange={handleToChange}
        />
        <EdgeSelect
          diagnostics={edgeFieldDiagnostics("from")}
          value={edge.from}
          options={
            draft
              ? [...blankOption, ...nodesForFrom(nodes)]
              : endpointOptions(nodes)
          }
          onChange={handleFromChange}
        />
        {!draft ? (
          <button
            className="grid h-9 w-8 place-items-center rounded-md text-muted transition hover:bg-red-50 hover:text-red-600"
            title="Delete edge"
            type="button"
            onClick={onDelete}
          >
            <Trash2 size={14} />
          </button>
        ) : (
          <span aria-hidden="true" className="h-9 w-8" />
        )}
      </div>
      {typeValue === "output_matches" ? (
        <InlineTextField
          diagnostics={edgeFieldDiagnostics("outputPattern")}
          value={edge.outputPattern ?? ""}
          onChange={(value) =>
            draft ? updateDraft({ outputPattern: value }) : onUpdate({ outputPattern: value })
          }
          placeholder="regex pattern"
        />
      ) : null}
      {typeValue === "output_field" ? (
        <div className="grid grid-cols-2 gap-2">
          <InlineTextField
            diagnostics={edgeFieldDiagnostics("field")}
            value={edge.field ?? ""}
            onChange={(value) =>
              draft ? updateDraft({ field: value }) : onUpdate({ field: value })
            }
            placeholder="field.path"
          />
          <EdgeSelect
            diagnostics={edgeFieldDiagnostics("operator")}
            value={edge.operator ?? "equals"}
            options={edgeOutputFieldOperatorOptions}
            onChange={(value) =>
              draft ? updateDraft({ operator: value }) : onUpdate({ operator: value })
            }
          />
          {(edge.operator ?? "equals") !== "exists" ? (
            <div className="col-span-2">
              <JsonBodyField
                label="Comparison value (JSON)"
                value={edge.value}
                onChange={(value) =>
                  draft ? updateDraft({ value }) : onUpdate({ value })
                }
              />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function endpointOptions(nodes) {
  return nodes.map((candidate) => [candidate.id, candidate.label || candidate.id]);
}

function nodesForTo(nodes) {
  return nodes.map((candidate) => [candidate.id, candidate.label || candidate.id]);
}

function nodesForFrom(nodes) {
  return nodes.map((candidate) => [candidate.id, candidate.label || candidate.id]);
}

function AgentConfigSection({
  agentConfig,
  diagnostics = [],
  agentId,
  onAgentChange,
  onProviderCapabilitiesRefresh,
  onProviderProfilesChange,
  pathBasePath,
  providerCapabilities = [],
  providerProfiles = [],
}) {
  if (!agentConfig) return null;
  return (
    <InspectorSection title="Agent config">
      <AgentConfigFields
        agentConfig={agentConfig}
        diagnostics={diagnostics}
        agentId={agentId}
        onAgentChange={onAgentChange}
        onProviderCapabilitiesRefresh={onProviderCapabilitiesRefresh}
        onProviderProfilesChange={onProviderProfilesChange}
        pathBasePath={pathBasePath}
        providerCapabilities={providerCapabilities}
        providerProfiles={providerProfiles}
      />
    </InspectorSection>
  );
}

function AgentConfigFields({
  agentConfig,
  diagnostics = [],
  agentId,
  onAgentChange,
  onProviderCapabilitiesRefresh,
  onProviderProfilesChange,
  pathBasePath,
  providerCapabilities = [],
  providerProfiles = [],
}) {
  const agentFieldDiagnostics = (...fields) => diagnosticsForField(diagnostics, ...fields);
  return (
    <>
      <ProviderModelEffortFields
        capabilities={providerCapabilities}
        effort={agentConfig.effort ?? ""}
        model={agentConfig.model ?? ""}
        provider={agentConfig.subscription}
        onChange={(patch) => {
          const nextPatch = { ...patch };
          if (patch.provider !== undefined) {
            nextPatch.subscription = patch.provider;
            nextPatch.profile = "";
            delete nextPatch.provider;
          }
          onAgentChange(agentId, nextPatch);
        }}
        onRefresh={onProviderCapabilitiesRefresh}
      />
      <SelectField
        label="Provider profile"
        value={agentConfig.profile ?? ""}
        options={profileSelectOptions(providerProfiles, agentConfig.subscription)}
        onChange={(value) => onAgentChange(agentId, { profile: value })}
      />
      <ProviderProfileEditor
        agentSubscription={agentConfig.subscription}
        providerCapabilities={providerCapabilities}
        providerProfiles={providerProfiles}
        selectedProfileName={agentConfig.profile ?? ""}
        onAgentChange={(patch) => onAgentChange(agentId, patch)}
        onProviderCapabilitiesRefresh={onProviderCapabilitiesRefresh}
        onProviderProfilesChange={onProviderProfilesChange}
      />
      <HealthDiagnosticList diagnostics={diagnostics} />
      <TextField
        diagnostics={agentFieldDiagnostics("prompt_path")}
        label="Prompt path"
        value={agentConfig.prompt_path ?? ""}
        onChange={(value) => onAgentChange(agentId, { prompt_path: value })}
        pathPicker
        pathBasePath={pathBasePath}
      />
      <TextField
        diagnostics={agentFieldDiagnostics("working_dir")}
        label="Working directory"
        value={agentConfig.working_dir ?? ""}
        onChange={(value) => onAgentChange(agentId, { working_dir: value })}
        pathPicker
        pathBasePath={pathBasePath}
      />
      <ListField
        label="Tools"
        value={agentConfig.tools ?? []}
        onChange={(value) => onAgentChange(agentId, { tools: value })}
        placeholder="Read, Write, Bash"
      />
      <ListField
        label="MCP servers"
        value={agentConfig.mcp_servers ?? []}
        onChange={(value) => onAgentChange(agentId, { mcp_servers: value })}
        placeholder="server-a, server-b"
      />
      <KeyValueField
        label="Environment"
        value={agentConfig.env ?? {}}
        onChange={(value) => onAgentChange(agentId, { env: value })}
      />
    </>
  );
}

function ProviderProfileEditor({
  agentSubscription,
  providerCapabilities = [],
  providerProfiles = [],
  selectedProfileName = "",
  onAgentChange,
  onProviderCapabilitiesRefresh,
  onProviderProfilesChange,
}) {
  const selectedProfile =
    providerProfiles.find((profile) => profile.name === selectedProfileName) ?? null;
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(() =>
    profileEditorDraft(selectedProfile, agentSubscription),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) {
      setDraft(profileEditorDraft(selectedProfile, agentSubscription));
      setError("");
    }
  }, [agentSubscription, open, selectedProfile]);

  async function saveProfile() {
    setSaving(true);
    setError("");
    try {
      const payload = profilePayloadFromDraft(draft);
      const response = await fetch(apiUrl("/provider/profiles"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(body.error || "Could not save provider profile");
      }
      const profile = body.profile ?? payload;
      onProviderProfilesChange?.([
        ...providerProfiles.filter((candidate) => candidate.name !== profile.name),
        profile,
      ].sort((left, right) => left.name.localeCompare(right.name)));
      onAgentChange({ profile: profile.name });
      setOpen(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Could not save provider profile");
    } finally {
      setSaving(false);
    }
  }

  async function removeProfile() {
    if (!selectedProfile) return;
    setSaving(true);
    setError("");
    try {
      const response = await fetch(
        apiUrl(`/provider/profiles/${encodeURIComponent(selectedProfile.name)}`),
        { method: "DELETE" },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(body.error || "Could not remove provider profile");
      }
      onProviderProfilesChange?.(
        providerProfiles.filter((candidate) => candidate.name !== selectedProfile.name),
      );
      onAgentChange({ profile: "" });
      setOpen(false);
    } catch (removeError) {
      setError(removeError instanceof Error ? removeError.message : "Could not remove provider profile");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-line p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted">
          {selectedProfile ? `Editing ${selectedProfile.name}` : "Provider profile editor"}
        </span>
        <button
          className="btn-ghost h-8 px-2 text-xs"
          type="button"
          onClick={() => setOpen((current) => !current)}
        >
          {open ? "Close" : selectedProfile ? "Edit" : "Create"}
        </button>
      </div>
      {open ? (
        <div className="mt-3 space-y-3">
          <TextField
            label="Profile name"
            value={draft.name}
            onChange={(value) => setDraft({ ...draft, name: value })}
            placeholder="fast-review"
          />
          <ProviderModelEffortFields
            capabilities={providerCapabilities}
            effort={draft.effort}
            model={draft.model}
            provider={draft.subscription}
            onChange={(patch) => {
              const { provider, ...profilePatch } = patch;
              setDraft({
                ...draft,
                ...profilePatch,
                ...(provider !== undefined ? {
                  subscription: provider,
                  ...(provider !== draft.subscription ? { approval_mode: "", sandbox_mode: "" } : {}),
                } : {}),
              });
            }}
            onRefresh={onProviderCapabilitiesRefresh}
          />
          <NumberField
            label="Timeout"
            min="0"
            step="1"
            value={draft.timeout}
            onChange={(value) => setDraft({ ...draft, timeout: value })}
            placeholder="Seconds"
          />
          {draft.subscription === "claude_code" ? (
            <SelectField
              label="Permissions"
              value={draft.approval_mode || "default"}
              options={PROVIDER_PERMISSIONS.claude_code}
              onChange={(value) => setDraft({ ...draft, approval_mode: value })}
            />
          ) : null}
          {draft.subscription === "codex" ? (
            <SelectField
              label="Permissions"
              value={draft.sandbox_mode || "default"}
              options={[["default", "Default"], ...PROVIDER_PERMISSIONS.codex]}
              onChange={(value) => setDraft({ ...draft, sandbox_mode: value })}
            />
          ) : null}
          <ListField
            label="Extra args"
            value={draft.extra_args}
            onChange={(value) => setDraft({ ...draft, extra_args: value })}
            placeholder="--flag, value"
          />
          <ListField
            label="Default tools"
            value={draft.tools}
            onChange={(value) => setDraft({ ...draft, tools: value })}
            placeholder="Read, Write"
          />
          <ListField
            label="MCP servers"
            value={draft.mcp_servers}
            onChange={(value) => setDraft({ ...draft, mcp_servers: value })}
            placeholder="docs, repo"
          />
          <KeyValueField
            label="Environment"
            value={draft.env}
            onChange={(value) => setDraft({ ...draft, env: value })}
          />
          <KeyValueField
            label="Secret refs"
            value={draft.secret_refs}
            onChange={(value) => setDraft({ ...draft, secret_refs: value })}
          />
          {error ? <p className="text-xs text-red-600">{error}</p> : null}
          <div className="flex gap-2">
            <button
              className="btn-primary h-9 flex-1 justify-center text-xs"
              disabled={saving}
              type="button"
              onClick={saveProfile}
            >
              {saving ? "Saving" : "Save"}
            </button>
            {selectedProfile ? (
              <button
                className="btn-ghost h-9 px-3 text-xs text-red-700"
                disabled={saving}
                type="button"
                onClick={removeProfile}
              >
                Remove
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function profileEditorDraft(profile, subscription) {
  return {
    name: profile?.name ?? "",
    subscription: profile?.subscription ?? subscription ?? "codex",
    model: profile?.model ?? "",
    timeout: profile?.timeout ?? "",
    effort: profile?.effort ?? "",
    approval_mode: profile?.approval_mode ?? "",
    sandbox_mode: profile?.sandbox_mode ?? "",
    extra_args: profile?.extra_args ?? [],
    tools: profile?.tools ?? [],
    mcp_servers: profile?.mcp_servers ?? [],
    env: profile?.env ?? {},
    secret_refs: profile?.secret_refs ?? {},
  };
}

function profilePayloadFromDraft(draft) {
  const payload = {
    name: draft.name.trim(),
    subscription: draft.subscription,
  };
  for (const key of ["model", "effort", "approval_mode", "sandbox_mode"]) {
    if (draft[key]) payload[key] = draft[key];
  }
  if (draft.timeout) payload.timeout = Number(draft.timeout);
  for (const key of ["extra_args", "tools", "mcp_servers"]) {
    if (draft[key]?.length) payload[key] = draft[key];
  }
  for (const key of ["env", "secret_refs"]) {
    if (Object.keys(draft[key] ?? {}).length) payload[key] = draft[key];
  }
  return payload;
}

function profileSelectOptions(providerProfiles = [], subscription) {
  const options = [["", "None"]];
  providerProfiles
    .filter((profile) => !subscription || profile.subscription === subscription)
    .forEach((profile) => {
      const suffix = profile.model ? ` (${profile.model})` : "";
      options.push([profile.name, `${profile.name}${suffix}`]);
    });
  return options;
}

function workflowDiagnosticsForDisplay(workflow) {
  return [
    ...(workflow?.validationDiagnostics ?? []),
    ...(workflow?.validationErrors ?? []),
    ...(workflow?.validationWarnings ?? []),
    ...(workflow?.healthErrors ?? []),
    ...(workflow?.healthWarnings ?? []),
  ].filter(
    (diagnostic, index, all) =>
      index ===
      all.findIndex(
        (candidate) =>
          candidate.id === diagnostic.id &&
          candidate.subject === diagnostic.subject &&
          candidate.field === diagnostic.field &&
          candidate.message === diagnostic.message,
      ),
  );
}

function workflowValidationDiagnostics(workflow) {
  return [
    ...(workflow?.validationDiagnostics ?? []),
    ...(workflow?.validationErrors ?? []),
    ...(workflow?.validationWarnings ?? []),
  ].filter(
    (diagnostic, index, all) =>
      index ===
      all.findIndex(
        (candidate) =>
          candidate.id === diagnostic.id &&
          candidate.subject === diagnostic.subject &&
          candidate.field === diagnostic.field &&
          candidate.message === diagnostic.message,
      ),
  );
}

function diagnosticsByTarget(diagnostics, targetType) {
  return diagnostics.reduce((grouped, diagnostic) => {
    if (diagnostic.targetType !== targetType || !diagnostic.targetId) return grouped;
    return {
      ...grouped,
      [diagnostic.targetId]: [...(grouped[diagnostic.targetId] ?? []), diagnostic],
    };
  }, {});
}

function diagnosticsForNode(diagnostics, node, agentConfig) {
  if (!node) return [];
  const subjects = new Set([`node:${node.id}`]);
  const agentId = node.operation?.agent_id;
  if (agentId) {
    subjects.add(`agent:${agentId}`);
  }
  return diagnostics.filter((diagnostic) => {
    if (subjects.has(diagnostic.subject)) return true;
    return isProviderDiagnosticForAgent(diagnostic, agentConfig);
  });
}

function diagnosticsForAgent(diagnostics, agentId, agentConfig) {
  return diagnostics.filter((diagnostic) => {
    if (diagnostic.subject === `agent:${agentId}`) return true;
    return isProviderDiagnosticForAgent(diagnostic, agentConfig);
  });
}

function diagnosticsForEdge(diagnostics, edge) {
  if (!edge) return [];
  const subjects = new Set([`edge:${edge.id}`]);
  return diagnostics.filter((diagnostic) => {
    if (subjects.has(diagnostic.subject)) return true;
    if (diagnostic.targetType !== "edge") return false;
    const detail = diagnostic.detail ?? {};
    return (
      diagnostic.targetId === edge.id ||
      (detail.from === edge.from && detail.to === edge.to)
    );
  });
}

function diagnosticsForField(diagnostics, ...fields) {
  const fieldSet = new Set(fields.filter(Boolean));
  return diagnostics.filter((diagnostic) => fieldSet.has(diagnostic.field));
}

function fieldDiagnosticState(diagnostics = []) {
  const visibleDiagnostics = diagnostics.filter((diagnostic) =>
    diagnostic?.severity === "error" || diagnostic?.severity === "warning",
  );
  if (!visibleDiagnostics.length) {
    return { diagnostics: [], severity: null };
  }
  return {
    diagnostics: visibleDiagnostics,
    severity: visibleDiagnostics.some((diagnostic) => diagnostic.severity === "error")
      ? "error"
      : "warning",
  };
}

function fieldBorderClass(diagnostics = [], base = "border-line focus:border-teal-500") {
  const { severity } = fieldDiagnosticState(diagnostics);
  if (severity === "error") return "border-red-300 focus:border-red-500 focus:ring-red-100";
  if (severity === "warning") return "border-amber-300 focus:border-amber-500 focus:ring-amber-100";
  return base;
}

function FieldDiagnosticMessage({ diagnostics = [], id }) {
  const { diagnostics: visibleDiagnostics, severity } = fieldDiagnosticState(diagnostics);
  if (!visibleDiagnostics.length) return null;
  return (
    <p
      id={id}
      className={`mt-1 text-xs leading-5 ${
        severity === "error" ? "text-red-700" : "text-amber-800"
      }`}
    >
      {visibleDiagnostics[0].message}
    </p>
  );
}

function isProviderDiagnosticForAgent(diagnostic, agentConfig) {
  if (!agentConfig?.subscription) return false;
  return (
    (diagnostic.id === "workflow.provider_cli" || diagnostic.id === "provider.cli") &&
    diagnostic.subject === agentConfig.subscription
  );
}

function HealthDiagnosticList({ diagnostics = [], onApplyFix }) {
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

function NodeBindingList({ bindings = [] }) {
  if (!bindings.length) return null;
  return (
    <div className="space-y-2" aria-label="Runtime bindings">
      {bindings.map((binding) => (
        <div
          key={binding.id}
          className="rounded-md border border-cyan-200 bg-cyan-50 px-3 py-2 text-xs leading-5 text-cyan-950"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
            <span className="font-semibold">{binding.destinationField}</span>
            <span className="text-cyan-800">{binding.status}</span>
          </div>
          <p className="mt-1 break-words">
            <code>{binding.expression}</code>
            {` from ${binding.producer} · ${binding.sourceType} → ${binding.destinationType}`}
          </p>
          <p className="text-cyan-800">
            {binding.resolutionPhase}
            {binding.coercion === "string" ? " · string coercion" : ""}
            {binding.readiness ? ` · secret ${binding.readiness}` : ""}
          </p>
        </div>
      ))}
    </div>
  );
}

export function createDefaultWorkflowNode(
  workflow,
  { type = "agent", usedAgentIds = [], x = 214, y = 204 } = {},
) {
  const workflowNodes = workflow.nodes ?? [];
  const nextNumber = nextAvailableNodeNumber(workflowNodes);
  const nextAgentNumber = nextAvailableAgentNumber(
    workflowNodes,
    workflow.agents,
    usedAgentIds,
  );
  const operation = defaultOperation(
    type,
    type === "agent" || type === "common_llm_task" ? nextAgentNumber : nextNumber,
  );
  return {
    id: `node-${nextNumber}`,
    label: specialNodeLabel(type) ?? `New Step ${nextNumber}`,
    type,
    operation,
    settings: defaultSettings,
    meta: nodeMetaFromOperation(operation),
    x,
    y,
  };
}

export function addDefaultNodeToWorkflow(workflow, options = {}) {
  const node = createDefaultWorkflowNode(workflow, options);
  const agents =
    node.operation?.agent_id && !workflow.agents?.[node.operation.agent_id]
      ? {
          ...(workflow.agents ?? {}),
          [node.operation.agent_id]: defaultAgentConfig(node.operation.agent_id, {
            prompt_path: node.operation.prompt_path,
            working_dir: node.operation.working_dir,
          }),
        }
      : workflow.agents ?? {};

  return {
    ...workflow,
    agents,
    nodes: [...(workflow.nodes ?? []), node],
  };
}

export function duplicateWorkflowNode(workflow, nodeId, { usedAgentIds = [], offset = 28 } = {}) {
  const nodes = workflow.nodes ?? [];
  const node = nodes.find((candidate) => candidate.id === nodeId);
  if (!node) return workflow;

  const nextNumber = nextAvailableNodeNumber(nodes);
  let operation = structuredCloneCompatible(node.operation ?? defaultOperation(node.type));
  let agents = workflow.agents ?? {};

  if (operation.agent_id) {
    const nextAgentNumber = nextAvailableAgentNumber(nodes, agents, usedAgentIds);
    const nextAgentId = `agent-${nextAgentNumber}`;
    agents = {
      ...agents,
      [nextAgentId]: {
        ...defaultAgentConfig(nextAgentId),
        ...(agents[operation.agent_id] ?? {}),
      },
    };
    operation = {
      ...operation,
      agent_id: nextAgentId,
    };
  }

  const duplicatedNode = {
    ...structuredCloneCompatible(node),
    id: `node-${nextNumber}`,
    label: `${specialNodeLabel(node.type) ?? node.label ?? "Node"} copy`,
    operation,
    meta: nodeMetaFromOperation(operation),
    x: (node.x ?? 0) + offset,
    y: (node.y ?? 0) + offset,
  };

  return {
    ...workflow,
    agents,
    nodes: [...nodes, duplicatedNode],
  };
}

export function updateWorkflowNodeOperation(workflow, nodeId, patch) {
  return {
    ...workflow,
    nodes: (workflow.nodes ?? []).map((node) => {
      if (node.id !== nodeId) return node;
      const operation = {
        ...defaultOperation(node.type),
        ...(node.operation ?? {}),
        ...patch,
      };
      return {
        ...node,
        operation,
        meta: nodeMetaFromOperation(operation),
      };
    }),
  };
}

export function moveWorkflowNode(workflow, nodeId, delta) {
  return {
    ...workflow,
    nodes: (workflow.nodes ?? []).map((node) =>
      node.id === nodeId
        ? {
            ...node,
            x: (node.x ?? 0) + (delta.x ?? 0),
            y: (node.y ?? 0) + (delta.y ?? 0),
          }
        : node,
    ),
  };
}

export function graphBounds(nodes, padding = 0) {
  if (!nodes.length) {
    return { left: -padding, top: -padding, right: padding, bottom: padding, width: padding * 2, height: padding * 2 };
  }
  const left = Math.min(...nodes.map((node) => node.x ?? 0)) - padding;
  const top = Math.min(...nodes.map((node) => node.y ?? 0)) - padding;
  const right = Math.max(...nodes.map((node) => (node.x ?? 0) + nodeWidth)) + padding;
  const bottom = Math.max(...nodes.map((node) => (node.y ?? 0) + nodeHeight)) + padding;
  return { left, top, right, bottom, width: right - left, height: bottom - top };
}

export function minimapPointToWorld(pointer, rect, bounds) {
  const scale = Math.min(
    Math.max(1, rect.width) / Math.max(1, bounds.width),
    Math.max(1, rect.height) / Math.max(1, bounds.height),
  );
  return {
    x: bounds.left + (pointer.clientX - rect.left) / scale,
    y: bounds.top + (pointer.clientY - rect.top) / scale,
  };
}

export function fitViewportToNodes(nodes, viewportSize, options = {}) {
  const bounds = graphBounds(nodes, options.padding ?? 80);
  const width = Math.max(1, viewportSize.width ?? 1);
  const height = Math.max(1, viewportSize.height ?? 1);
  const scale = clamp(
    Math.min(width / Math.max(1, bounds.width), height / Math.max(1, bounds.height)),
    options.minZoom ?? minZoom,
    options.maxZoom ?? maxZoom,
  );
  return {
    scale,
    x: (width - bounds.width * scale) / 2 - bounds.left * scale,
    y: (height - bounds.height * scale) / 2 - bounds.top * scale,
  };
}

export function matchingNodeIds(nodes, query) {
  const normalizedQuery = String(query ?? "").trim().toLowerCase();
  if (!normalizedQuery) return [];
  return nodes
    .filter((node) => nodeSearchText(node).includes(normalizedQuery))
    .map((node) => node.id);
}

function nodeSearchText(node) {
  const operation = node.operation ?? {};
  return [
    node.id,
    node.label,
    node.type,
    operation.type,
    operation.agent_id,
    operation.path,
    operation.source_path,
    operation.destination_path,
    operation.output_path,
    operation.prompt_path,
    operation.script_path,
    operation.working_dir,
    operation.target,
    operation.url,
    operation.method,
    operation.index_path,
    operation.query,
    operation.command,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export function removeWorkflowNode(workflow, nodeId) {
  return {
    ...workflow,
    nodes: (workflow.nodes ?? []).filter((node) => node.id !== nodeId),
    edges: (workflow.edges ?? []).filter((edge) => edge.from !== nodeId && edge.to !== nodeId),
  };
}

export function addWorkflowEdge(
  workflow,
  fromNodeId,
  toNodeId,
  condition = "always",
  outputPattern = null,
  field = null,
  operator = null,
  value = null,
) {
  const nextCondition = condition || "always";
  const nextOutputPattern = nextCondition === "output_matches" ? outputPattern || "" : null;
  return {
    ...workflow,
    edges: [
      ...(workflow.edges ?? []),
      {
        id: uniqueEdgeId(workflow.edges ?? [], fromNodeId, toNodeId),
        from: fromNodeId,
        to: toNodeId,
        label: edgeLabel(nextCondition, nextOutputPattern, field, operator, value),
        condition: nextCondition,
        outputPattern: nextOutputPattern,
        field: nextCondition === "output_field" ? field || "" : null,
        operator: nextCondition === "output_field" ? operator || "equals" : null,
        value: nextCondition === "output_field" ? value : null,
      },
    ],
  };
}

export function uniqueEdgeId(edges, fromNodeId, toNodeId) {
  const baseId = `${fromNodeId}-${toNodeId}`;
  if (!edges.some((edge) => edge.id === baseId)) {
    return baseId;
  }

  let index = 2;
  while (edges.some((edge) => edge.id === `${baseId}-${index}`)) {
    index += 1;
  }
  return `${baseId}-${index}`;
}

export function edgeLabel(
  condition = "always",
  outputPattern = "",
  field = "",
  operator = "",
  value = null,
) {
  if (condition === "always") return "always";
  if (condition === "output_matches" && outputPattern) return `matches ${outputPattern}`;
  if (condition === "output_field") {
    const fieldLabel = field || "field";
    const operatorLabel = (operator || "equals").replaceAll("_", " ");
    if (operator === "exists") return `${fieldLabel} exists`;
    const serializedValue = JSON.stringify(value);
    return `${fieldLabel} ${operatorLabel} ${serializedValue ?? String(value)}`;
  }
  if (condition === "after_loop") return "after loop";
  return condition.replaceAll("_", " ");
}

function stableEdgeDirection(fromNodeId, toNodeId) {
  return String(fromNodeId).localeCompare(String(toNodeId)) <= 0 ? -1 : 1;
}

function edgeGeometry(fromNode, toNode, selfLoop, laneOffset = 0) {
  if (selfLoop) {
    const start = { x: fromNode.x + nodeWidth - 52, y: fromNode.y + 8 };
    const end = { x: fromNode.x + 52, y: fromNode.y + 8 };
    return {
      path: `M ${start.x} ${start.y} C ${start.x + 76} ${start.y - 84}, ${end.x - 76} ${end.y - 84}, ${end.x} ${end.y}`,
      label: { x: fromNode.x + nodeWidth / 2, y: fromNode.y - 58 },
    };
  }

  const fromCenter = {
    x: fromNode.x + nodeWidth / 2,
    y: fromNode.y + nodeHeight / 2,
  };
  const toCenter = {
    x: toNode.x + nodeWidth / 2,
    y: toNode.y + nodeHeight / 2,
  };
  const dx = toCenter.x - fromCenter.x;
  const dy = toCenter.y - fromCenter.y;
  const horizontal = Math.abs(dx) >= Math.abs(dy);

  const start = horizontal
    ? {
        x: dx >= 0 ? fromNode.x + nodeWidth : fromNode.x,
        y: fromCenter.y,
      }
    : {
        x: fromCenter.x,
        y: dy >= 0 ? fromNode.y + nodeHeight : fromNode.y,
      };
  const end = horizontal
    ? {
        x: dx >= 0 ? toNode.x : toNode.x + nodeWidth,
        y: toCenter.y,
      }
    : {
        x: toCenter.x,
        y: dy >= 0 ? toNode.y : toNode.y + nodeHeight,
      };

  const controlDistance = Math.max(80, (horizontal ? Math.abs(end.x - start.x) : Math.abs(end.y - start.y)) / 2);
  const direction = horizontal ? Math.sign(end.x - start.x) || 1 : Math.sign(end.y - start.y) || 1;
  const c1 = horizontal
    ? { x: start.x + direction * controlDistance, y: start.y + laneOffset }
    : { x: start.x + laneOffset, y: start.y + direction * controlDistance };
  const c2 = horizontal
    ? { x: end.x - direction * controlDistance, y: end.y + laneOffset }
    : { x: end.x + laneOffset, y: end.y - direction * controlDistance };

  return {
    path: `M ${start.x} ${start.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${end.x} ${end.y}`,
    label: horizontal
      ? {
          x: (start.x + end.x) / 2,
          y: (start.y + end.y) / 2 + laneOffset - 12,
        }
      : {
          x: (start.x + end.x) / 2 + laneOffset,
          y: (start.y + end.y) / 2 - 12,
        },
  };
}

function draftEdgePath(draftEdge) {
  const start = draftEdge.start;
  const end = draftEdge.to;
  const dx = end.x - start.x;
  const controlDistance = Math.max(80, Math.abs(dx) / 2);
  const direction = Math.sign(dx) || 1;
  return `M ${start.x} ${start.y} C ${start.x + direction * controlDistance} ${start.y}, ${end.x - direction * controlDistance} ${end.y}, ${end.x} ${end.y}`;
}

function InspectorPanel({ children, open, subtitle, title, onToggle }) {
  const contentId = useId();
  return (
    <section className="border-b border-line">
      <button
        aria-controls={contentId}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-slate-50"
        type="button"
        onClick={onToggle}
      >
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-ink">{title}</span>
          <span className="mt-0.5 block truncate text-xs text-muted">{subtitle}</span>
        </span>
        {open ? (
          <ChevronUp className="shrink-0 text-muted" size={16} />
        ) : (
          <ChevronDown className="shrink-0 text-muted" size={16} />
        )}
      </button>
      {open ? <div id={contentId}>{children}</div> : null}
    </section>
  );
}

function InspectorSection({ children, className = "", title }) {
  return (
    <section className={`space-y-3 rounded-lg border border-line p-3 ${className}`}>
      <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-muted">{title}</h3>
      {children}
    </section>
  );
}

function WorkflowSettingsSection({ children, title }) {
  return (
    <section aria-label={title} className="space-y-3">
      <h3 className="sr-only">{title}</h3>
      {children}
    </section>
  );
}

export function ApprovalDecisionOverlay({ approval, node, onDecideApproval }) {
  const [notes, setNotes] = useState("");
  const [approver, setApprover] = useState(approval?.approvers?.[0] || "ui");
  const [dismissed, setDismissed] = useState(false);
  useEffect(() => {
    setNotes("");
    setApprover(approval?.approvers?.[0] || "ui");
    setDismissed(false);
  }, [approval?.runId, approval?.nodeId, approval?.approvers]);
  if (!approval || dismissed) return null;
  const nodeLabel = node?.label || node?.id || approval.nodeId;
  return (
    <Dialog
      description={`${nodeLabel}: ${approval.message}`}
      onClose={() => setDismissed(true)}
      overlayClassName="pointer-events-none absolute inset-0 z-[90] flex items-center justify-center px-4"
      panelClassName="pointer-events-auto w-full max-w-[560px] rounded-lg border border-amber-300 bg-white shadow-2xl"
      panelProps={{
        onPointerDown: (event) => event.stopPropagation(),
        onPointerMove: (event) => event.stopPropagation(),
        onPointerUp: (event) => event.stopPropagation(),
        onWheel: (event) => event.stopPropagation(),
      }}
      title="Approval required"
    >
        <div className="border-b border-amber-200 bg-amber-50 px-5 py-4">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-amber-600 text-white">
              <ShieldCheck size={22} />
            </span>
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-ink">Approval Required</h2>
              <p className="truncate text-sm text-slate-600">{nodeLabel}</p>
            </div>
          </div>
        </div>
        <div className="space-y-4 px-5 py-5">
          <div className="max-h-[180px] overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-slate-50 px-3 py-3 text-sm leading-6 text-ink">
            {approval.message}
          </div>
          <div className="grid gap-3 sm:grid-cols-[minmax(0,180px)_1fr]">
            <label className="space-y-1 text-xs font-medium text-slate-600">
              <span>Approver</span>
              <input
                className="h-10 w-full rounded-md border border-line bg-white px-3 text-sm text-ink outline-none transition placeholder:text-muted/70 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/10"
                placeholder="Approver identity"
                value={approver}
                onChange={(event) => setApprover(event.target.value)}
              />
            </label>
            <label className="space-y-1 text-xs font-medium text-slate-600">
              <span>Notes</span>
              <textarea
                className="min-h-[82px] w-full resize-y rounded-md border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition placeholder:text-muted/70 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/10"
                placeholder="Decision notes"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </label>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <button
              className="flex h-14 items-center justify-center gap-2 rounded-md bg-emerald-600 px-4 text-base font-semibold text-white shadow-sm transition hover:bg-emerald-700"
              title="Approve pending approval"
              type="button"
              onClick={() => onDecideApproval?.(approval, "approved", notes, approver)}
            >
              <Check size={22} />
              Approve
            </button>
            <button
              className="flex h-14 items-center justify-center gap-2 rounded-md border border-red-300 bg-red-50 px-4 text-base font-semibold text-red-700 shadow-sm transition hover:bg-red-100"
              title="Reject pending approval"
              type="button"
              onClick={() => onDecideApproval?.(approval, "rejected", notes, approver)}
            >
              <X size={22} />
              Reject
            </button>
          </div>
        </div>
    </Dialog>
  );
}

function ApprovalRuntimePanel({ approval, onDecideApproval }) {
  const [notes, setNotes] = useState("");
  const [approver, setApprover] = useState(approval?.approvers?.[0] || "ui");
  useEffect(() => {
    setApprover(approval?.approvers?.[0] || "ui");
  }, [approval?.runId, approval?.nodeId, approval?.approvers]);
  if (!approval) return null;
  const decision = approval.decision;
  const pending = approval.status === "pending";
  return (
    <InspectorSection title="Approval status">
      <div className="space-y-2 text-xs text-slate-700">
        <div className="flex items-center justify-between gap-2">
          <span className="font-medium text-ink">{pending ? "Pending" : "Decided"}</span>
          <span className="font-mono text-[11px] text-muted">{approval.runId}</span>
        </div>
        <div className="whitespace-pre-wrap break-words leading-5">{approval.message}</div>
        <div className="grid grid-cols-2 gap-2 text-[11px] text-muted">
          <span>Requested</span>
          <span className="truncate text-right">{approval.requestedAt || "-"}</span>
          <span>Timeout</span>
          <span className="truncate text-right">
            {approval.timeoutSeconds ? `${approval.timeoutSeconds}s` : "None"}
          </span>
          {approval.timeoutSeconds ? (
            <>
              <span>Timeout action</span>
              <span className="truncate text-right">
                {approval.timeoutDecision === "reject" ? "Reject" : "Timeout"}
              </span>
            </>
          ) : null}
          <span>Approvers</span>
          <span className="truncate text-right">
            {approval.approvers?.length ? approval.approvers.join(", ") : "Any"}
          </span>
          {decision ? (
            <>
              <span>Decision</span>
              <span className="truncate text-right">{decision.decision}</span>
              <span>By</span>
              <span className="truncate text-right">{decision.decidedBy || "-"}</span>
              <span>Notes</span>
              <span className="truncate text-right">{decision.notes || "-"}</span>
            </>
          ) : null}
        </div>
        {pending ? (
          <div className="space-y-2 pt-1">
            <input
              className="h-8 w-full rounded-md border border-line bg-white px-2 text-xs text-ink outline-none transition placeholder:text-muted/70 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/10"
              placeholder="Approver identity"
              value={approver}
              onChange={(event) => setApprover(event.target.value)}
            />
            <textarea
              className="min-h-[72px] w-full resize-y rounded-md border border-line bg-white px-2 py-1.5 text-xs text-ink outline-none transition placeholder:text-muted/70 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/10"
              placeholder="Decision notes"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
            <div className="flex gap-2">
              <button
                className="btn-primary h-8 flex-1 justify-center text-xs"
                type="button"
                onClick={() => onDecideApproval?.(approval, "approved", notes, approver)}
              >
                <Check size={14} />
                Approve
              </button>
              <button
                className="h-8 flex-1 rounded-md border border-red-200 bg-red-50 px-2 text-xs font-medium text-red-700 transition hover:bg-red-100"
                type="button"
                onClick={() => onDecideApproval?.(approval, "rejected", notes, approver)}
              >
                Reject
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </InspectorSection>
  );
}

function VectorIndexStats({ output }) {
  const data = output?.data ?? null;
  if (!data || data.index_path == null || data.chunk_count == null) {
    return null;
  }
  const stats = [
    ["Status", data.status ?? (data.current ? "current" : "updated")],
    ["Files", data.indexed_file_count ?? data.file_count ?? "-"],
    ["Chunks", data.chunk_count ?? "-"],
    ["Added", data.added_files ?? 0],
    ["Updated", data.updated_files ?? 0],
    ["Deleted", data.deleted_files ?? 0],
    ["Stale", data.stale_files ?? 0],
    ["Size", data.index_size_bytes ? `${data.index_size_bytes} B` : "-"],
  ];
  return (
    <InspectorSection title="Index stats">
      <div className="grid grid-cols-2 gap-2 text-xs">
        {stats.map(([label, value]) => (
          <div key={label} className="rounded-md border border-line px-2 py-1.5">
            <span className="block text-muted">{label}</span>
            <span className="font-semibold text-slate-700 dark:text-slate-200">{value}</span>
          </div>
        ))}
      </div>
      <div className="space-y-1 text-xs">
        <div>
          <span className="font-medium text-muted">Strategy</span>
          <span className="ml-2 text-slate-700 dark:text-slate-200">
            {data.strategy ?? "hash_token_v1"}
          </span>
        </div>
        <div className="break-all">
          <span className="font-medium text-muted">Index</span>
          <span className="ml-2 text-slate-700 dark:text-slate-200">{data.index_path}</span>
        </div>
        {data.last_update_time ? (
          <div>
            <span className="font-medium text-muted">Updated</span>
            <span className="ml-2 text-slate-700 dark:text-slate-200">
              {data.last_update_time}
            </span>
          </div>
        ) : null}
      </div>
    </InspectorSection>
  );
}

function VectorSearchStats({ output }) {
  const data = output?.data ?? null;
  if (!data || data.index_path == null || !Array.isArray(data.results)) {
    return null;
  }
  return (
    <InspectorSection title="Search stats">
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="rounded-md border border-line px-2 py-1.5">
          <span className="block text-muted">Results</span>
          <span className="font-semibold text-slate-700 dark:text-slate-200">
            {data.results.length}
          </span>
        </div>
        <div className="rounded-md border border-line px-2 py-1.5">
          <span className="block text-muted">Threshold</span>
          <span className="font-semibold text-slate-700 dark:text-slate-200">
            {data.score_threshold ?? 0}
          </span>
        </div>
      </div>
      <div className="space-y-1 text-xs">
        <div>
          <span className="font-medium text-muted">Strategy</span>
          <span className="ml-2 text-slate-700 dark:text-slate-200">
            {data.strategy ?? "cosine_v1"}
          </span>
        </div>
        <div className="break-all">
          <span className="font-medium text-muted">Index</span>
          <span className="ml-2 text-slate-700 dark:text-slate-200">{data.index_path}</span>
        </div>
      </div>
    </InspectorSection>
  );
}

function HttpResponsePreview({ output }) {
  const data = output?.data ?? null;
  if (!data || data.status == null) {
    return null;
  }
  const body = typeof data.body === "string" ? data.body : "";
  const headers = data.headers && typeof data.headers === "object" ? data.headers : {};
  const selected = data.selected && typeof data.selected === "object" ? data.selected : {};
  return (
    <InspectorSection title="Response preview">
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="rounded-lg border border-line px-3 py-2">
          <span className="block text-muted">Status</span>
          <span className="font-semibold text-slate-700">{data.status}</span>
        </div>
        <div className="rounded-lg border border-line px-3 py-2">
          <span className="block text-muted">Method</span>
          <span className="font-semibold text-slate-700">{data.method ?? "HTTP"}</span>
        </div>
      </div>
      {data.url ? (
        <div>
          <span className="text-xs font-medium text-muted">URL</span>
          <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-slate-50 px-3 py-2 text-xs text-slate-700">
            {data.url}
          </pre>
        </div>
      ) : null}
      {Object.keys(selected).length ? (
        <div>
          <span className="text-xs font-medium text-muted">Output mapping</span>
          <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-slate-50 px-3 py-2 text-xs text-slate-700">
            {JSON.stringify(selected, null, 2)}
          </pre>
        </div>
      ) : null}
      {Object.keys(headers).length ? (
        <div>
          <span className="text-xs font-medium text-muted">Headers</span>
          <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-slate-50 px-3 py-2 text-xs text-slate-700">
            {JSON.stringify(headers, null, 2)}
          </pre>
        </div>
      ) : null}
      {body ? (
        <div>
          <span className="text-xs font-medium text-muted">Body</span>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-slate-50 px-3 py-2 text-xs text-slate-700">
            {body}
          </pre>
        </div>
      ) : null}
    </InspectorSection>
  );
}

function useCommittedDraft({ enterCommits = true, format, onCommit, parse, value }) {
  const committedText = format(value);
  const committedRef = useRef(committedText);
  const draftRef = useRef(committedText);
  const focusedRef = useRef(false);
  const [draft, setDraft] = useState(committedText);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  useEffect(() => {
    const previousCommitted = committedRef.current;
    if (committedText === previousCommitted) return;

    committedRef.current = committedText;
    if (focusedRef.current && draftRef.current !== previousCommitted) {
      setConflict(true);
      return;
    }

    draftRef.current = committedText;
    setDraft(committedText);
    setError("");
    setConflict(false);
  }, [committedText]);

  function validate(text) {
    const parsed = parse(text);
    setError(parsed.ok ? "" : parsed.error);
    return parsed;
  }

  function commitText(text = draftRef.current) {
    const parsed = validate(text);
    if (!parsed.ok) return false;

    const normalized = format(parsed.value);
    const changed = normalized !== committedRef.current;
    committedRef.current = normalized;
    draftRef.current = normalized;
    setDraft(normalized);
    setConflict(false);
    if (changed) onCommit(parsed.value);
    return true;
  }

  function restore() {
    draftRef.current = committedRef.current;
    setDraft(committedRef.current);
    setError("");
    setConflict(false);
  }

  return {
    conflict,
    draft,
    error,
    commitText,
    onBlur() {
      focusedRef.current = false;
      commitText();
    },
    onChange(nextDraft) {
      draftRef.current = nextDraft;
      setDraft(nextDraft);
      validate(nextDraft);
    },
    onFocus() {
      focusedRef.current = true;
    },
    onKeyDown(event) {
      if (enterCommits && event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        commitText();
      } else if (event.key === "Escape") {
        event.preventDefault();
        restore();
      }
    },
  };
}

function DraftFieldMessage({ id, state }) {
  if (!state.error && !state.conflict) return null;
  return (
    <div id={id}>
      {state.error ? (
        <p className="mt-1 text-xs text-red-600" role="alert">
          {state.error}
        </p>
      ) : null}
      {state.conflict ? (
        <p className="mt-1 text-xs text-amber-700" role="status">
          This value changed elsewhere. Your draft is preserved; press Escape to use the latest value.
        </p>
      ) : null}
    </div>
  );
}

export function TextField({
  commitOnBlur = false,
  diagnostics = [],
  label,
  onChange,
  pathBasePath = "",
  pathLink = false,
  pathPicker = false,
  placeholder,
  parseDraft = (text) => ({ ok: true, value: text }),
  readOnly = false,
  value,
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pathInfo, setPathInfo] = useState(null);
  const [textFileDialog, setTextFileDialog] = useState(null);
  const isPathField = pathPicker || pathLink;
  const canPickPath = pathPicker && !readOnly && typeof onChange === "function";
  const canDraftPath = isPathField && !readOnly && typeof onChange === "function";
  const canDraftText = commitOnBlur && !readOnly && typeof onChange === "function";
  const canDraft = canDraftPath || canDraftText;
  const draftState = useCommittedDraft({
    format: (nextValue) => String(nextValue ?? ""),
    onCommit: (nextValue) => onChange?.(nextValue),
    parse: parseDraft,
    value,
  });
  const storedPath = canDraft ? draftState.draft : String(value ?? "");
  const displayValue = isPathField ? resolveDisplayPath(storedPath, pathBasePath) : value ?? "";
  const inputValue = canDraft ? draftState.draft : displayValue;
  const canOpenPath = isPathField && displayValue && !isUrlPath(displayValue);
  const canEditTextPath = canOpenPath && pathInfo?.isFile;
  const diagnosticId = useId();
  const draftDiagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;
  const describedBy = [
    hasFieldDiagnostics ? diagnosticId : null,
    canDraft && (draftState.error || draftState.conflict) ? draftDiagnosticId : null,
  ]
    .filter(Boolean)
    .join(" ") || undefined;

  useEffect(() => {
    let cancelled = false;

    async function loadPathInfo() {
      if (!canOpenPath) {
        setPathInfo(null);
        return;
      }
      try {
        const info = await window.goferDesktop?.workspace?.getPathInfo?.(displayValue);
        if (!cancelled) {
          setPathInfo(info ?? null);
        }
      } catch {
        if (!cancelled) {
          setPathInfo(null);
        }
      }
    }

    loadPathInfo();
    return () => {
      cancelled = true;
    };
  }, [canOpenPath, displayValue]);

  async function handlePathPick(event) {
    event.preventDefault();
    event.stopPropagation();

    if (window.goferDesktop?.workspace?.listDirectory) {
      setPickerOpen(true);
      return;
    }

    try {
      const selectedPath = await window.goferDesktop?.workspace?.selectPath?.({
        currentPath: displayValue,
      });
      if (selectedPath) {
        draftState.commitText(selectedPath);
      }
    } catch (error) {
      console.error("Failed to select path", error);
    }
  }

  async function handlePathOpen(event) {
    event.preventDefault();
    event.stopPropagation();
    if (!displayValue) return;

    try {
      const info = await window.goferDesktop?.workspace?.getPathInfo?.(displayValue);
      if (info?.isDirectory) {
        await window.goferDesktop?.workspace?.openPath?.(displayValue);
      } else {
        await window.goferDesktop?.workspace?.revealPath?.(displayValue);
      }
    } catch (error) {
      console.error("Failed to reveal path", error);
    }
  }

  return (
    <>
      <label className="block">
        <span className="text-xs font-medium text-muted">{label}</span>
        <span className="relative mt-1 block">
          <input
            aria-describedby={describedBy}
            aria-invalid={
              draftState.error || fieldDiagnosticState(diagnostics).severity === "error" || undefined
            }
            className={`h-10 w-full rounded-lg border bg-white px-3 text-sm outline-none transition read-only:bg-slate-50 ${fieldBorderClass(diagnostics)} ${
              canPickPath && canOpenPath ? "pr-[4.5rem]" : canPickPath || canOpenPath ? "pr-10" : ""
            }`}
            placeholder={placeholder}
            readOnly={readOnly}
            title={displayValue}
            value={inputValue}
            onBlur={canDraft ? draftState.onBlur : undefined}
            onChange={
              canDraft
                ? (event) => draftState.onChange(event.target.value)
                : (event) => onChange?.(event.target.value)
            }
            onFocus={canDraft ? draftState.onFocus : undefined}
            onKeyDown={canDraft ? draftState.onKeyDown : undefined}
          />
          {canOpenPath ? (
            <button
              aria-label={`Open ${label?.toLowerCase?.() ?? "path"}`}
              className={`absolute top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md text-muted transition hover:bg-slate-100 hover:text-strong dark:hover:bg-[#2a2a2a] ${
                canPickPath ? "right-9" : "right-2"
              }`}
              title={`Open ${label?.toLowerCase?.() ?? "path"} in file browser`}
              type="button"
              onClick={handlePathOpen}
            >
              <ExternalLink size={16} strokeWidth={1.9} />
            </button>
          ) : null}
          {canPickPath ? (
            <button
              aria-label={`Choose ${label?.toLowerCase?.() ?? "path"}`}
              className="absolute right-2 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md text-muted transition hover:bg-slate-100 hover:text-strong dark:hover:bg-[#2a2a2a]"
              title={`Choose ${label?.toLowerCase?.() ?? "path"}`}
              type="button"
              onClick={handlePathPick}
            >
              <FolderOpen size={17} strokeWidth={1.9} />
            </button>
          ) : null}
        </span>
        <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
        {canDraft ? <DraftFieldMessage id={draftDiagnosticId} state={draftState} /> : null}
      </label>
      {canEditTextPath ? (
        <div className="mt-1 flex justify-end gap-2">
          <button
            className="inline-flex items-center gap-1 text-[11px] font-medium text-muted underline-offset-2 transition hover:text-ink hover:underline"
            type="button"
            onClick={() => setTextFileDialog({ mode: "edit", path: displayValue })}
          >
            <FilePenLine size={12} />
            edit
          </button>
          <button
            className="inline-flex items-center gap-1 text-[11px] font-medium text-muted underline-offset-2 transition hover:text-ink hover:underline"
            type="button"
            onClick={() => setTextFileDialog({ mode: "preview", path: displayValue })}
          >
            <Eye size={12} />
            preview
          </button>
        </div>
      ) : null}
      {pickerOpen ? (
        <PathPickerDialog
          currentPath={displayValue}
          label={label}
          onClose={() => setPickerOpen(false)}
          onSelect={(selectedPath) => {
            draftState.commitText(selectedPath);
            setPickerOpen(false);
          }}
        />
      ) : null}
      {textFileDialog ? (
        <TextFileDialog
          mode={textFileDialog.mode}
          path={textFileDialog.path}
          onClose={() => setTextFileDialog(null)}
        />
      ) : null}
    </>
  );
}

export function TextFileDialog({ mode, path, onClose }) {
  const [content, setContent] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const readOnly = mode === "preview";

  useEffect(() => {
    let cancelled = false;

    async function loadFile() {
      setLoading(true);
      setError("");
      try {
        const payload = await window.goferDesktop?.textFiles?.read?.(path);
        if (!cancelled) {
          setContent(payload?.content ?? "");
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : String(loadError));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadFile();
    return () => {
      cancelled = true;
    };
  }, [path]);

  async function saveFile() {
    setSaving(true);
    setError("");
    try {
      await window.goferDesktop?.textFiles?.write?.({ targetPath: path, content });
      onClose();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save file");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      description={path}
      onClose={onClose}
      overlayClassName="fixed inset-0 z-[80] grid place-items-center bg-slate-950/35 px-4"
      panelClassName="flex max-h-[82vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-line bg-white shadow-panel"
      panelProps={{ "aria-busy": loading || saving || undefined }}
      title={readOnly ? "Preview file" : "Edit file"}
    >
        <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-strong">
              {readOnly ? "Preview file" : "Edit file"}
            </h2>
            <p className="mt-1 truncate text-xs text-muted" title={path}>
              {path}
            </p>
          </div>
          <button
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink dark:hover:bg-[#2a2a2a]"
            title="Close"
            type="button"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>

        <div className="min-h-[340px] flex-1 p-4">
          {loading ? (
            <div className="flex h-48 items-center justify-center text-sm text-muted">
              <Loader2 size={16} className="mr-2 animate-spin" />
              Loading file
            </div>
          ) : error ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {error}
            </div>
          ) : (
            <textarea
              className="h-[52vh] min-h-[320px] w-full resize-none rounded-lg border border-line bg-slate-50 p-3 font-mono text-xs leading-5 text-ink outline-none transition focus:border-teal-500 read-only:bg-slate-50"
              readOnly={readOnly}
              spellCheck={false}
              value={content}
              onChange={(event) => setContent(event.target.value)}
            />
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line px-4 py-3">
          <button
            className="h-9 rounded-lg border border-line bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
            type="button"
            onClick={onClose}
          >
            Close
          </button>
          {!readOnly ? (
            <button
              className="inline-flex h-9 items-center gap-2 rounded-lg bg-brand px-3 text-sm font-medium text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={loading || saving || Boolean(error)}
              type="button"
              onClick={saveFile}
            >
              {saving ? <Loader2 size={15} className="animate-spin" /> : <FilePenLine size={15} />}
              Save
            </button>
          ) : null}
        </div>
    </Dialog>
  );
}

export function PathPickerDialog({ currentPath, label, onClose, onSelect }) {
  const [contextMenu, setContextMenu] = useState(null);
  const [copiedEntry, setCopiedEntry] = useState(null);
  const [nameRequest, setNameRequest] = useState(null);
  const [directory, setDirectory] = useState("");
  const [entries, setEntries] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [parent, setParent] = useState(null);
  const [pathCopied, setPathCopied] = useState(false);
  const [selectedPath, setSelectedPath] = useState(currentPath ?? "");
  const titleLabel = typeof label === "string" && label.trim() ? label : "path";

  useEffect(() => {
    loadDirectory(currentPath);
  }, [currentPath]);

  async function loadDirectory(nextPath) {
    setContextMenu(null);
    setLoading(true);
    setError("");
    try {
      const payload = await window.goferDesktop.workspace.listDirectory({
        currentPath: nextPath ?? "",
      });
      setDirectory(payload.directory);
      setParent(payload.parent);
      setEntries(payload.entries ?? []);
      setSelectedPath(payload.directory);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    } finally {
      setLoading(false);
    }
  }

  async function copyCurrentPath() {
    if (!directory) return;

    try {
      await navigator.clipboard.writeText(directory);
      setPathCopied(true);
      window.setTimeout(() => setPathCopied(false), 1400);
    } catch (copyError) {
      setError(copyError instanceof Error ? copyError.message : "Unable to copy path");
    }
  }

  async function openCurrentPath() {
    if (!directory) return;

    try {
      await window.goferDesktop?.workspace?.openPath?.(directory);
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "Unable to open path");
    }
  }

  function showContextMenu(event, entry = null) {
    event.preventDefault();
    event.stopPropagation();
    if (!directory) return;
    setContextMenu({
      entry,
      x: event.clientX,
      y: event.clientY,
    });
  }

  function requestCreateChild(kind) {
    setContextMenu(null);
    setNameRequest({ kind, mode: "create" });
  }

  function requestRenameEntry(entry) {
    setContextMenu(null);
    if (!entry) return;
    setNameRequest({
      entry,
      initialName: entry.name,
      kind: entry.isDirectory ? "folder" : "file",
      mode: "rename",
    });
  }

  async function createChild(kind, name) {
    if (!name) return;

    try {
      let result;
      if (kind === "file") {
        result = await window.goferDesktop?.workspace?.createFile?.({ directory, name });
      } else {
        result = await window.goferDesktop?.workspace?.createFolder?.({ directory, name });
      }
      await loadDirectory(directory);
      if (result?.path) {
        setSelectedPath(result.path);
      }
      setNameRequest(null);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : `Unable to create ${kind}`);
    }
  }

  async function renameEntry(entry, name) {
    if (!entry || !name) return;

    try {
      const result = await window.goferDesktop?.workspace?.renamePath?.({
        sourcePath: entry.path,
        name,
      });
      await loadDirectory(directory);
      if (result?.path) {
        setSelectedPath(result.path);
      }
      setNameRequest(null);
    } catch (renameError) {
      setError(renameError instanceof Error ? renameError.message : "Unable to rename path");
    }
  }

  async function copyEntry(entry) {
    setContextMenu(null);
    if (!entry) return;
    setCopiedEntry(entry);
  }

  async function pasteEntry() {
    setContextMenu(null);
    if (!copiedEntry) return;

    const existingNames = new Set(entries.map((entry) => entry.name));
    const nextName = nextCopyName(copiedEntry.name, existingNames);
    try {
      await window.goferDesktop?.workspace?.copyPath?.({
        sourcePath: copiedEntry.path,
        destinationPath: joinPath(directory, nextName),
      });
      await loadDirectory(directory);
      setSelectedPath(joinPath(directory, nextName));
    } catch (copyError) {
      setError(copyError instanceof Error ? copyError.message : "Unable to copy path");
    }
  }

  async function deleteEntry(entry) {
    setContextMenu(null);
    if (!entry) return;
    if (!window.confirm(`Delete ${entry.name}?`)) return;

    try {
      await window.goferDesktop?.workspace?.deletePath?.(entry.path);
      if (selectedPath === entry.path) {
        setSelectedPath(directory);
      }
      await loadDirectory(directory);
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete path");
    }
  }

  return (
    <Dialog
      description="Browse the local workspace and select a path"
      onClose={onClose}
      overlayClassName="fixed inset-0 z-[70] grid place-items-center bg-slate-950/35 px-4"
      panelClassName="flex max-h-[78vh] w-full max-w-[680px] flex-col rounded-lg border border-line bg-white shadow-panel"
      panelProps={{ "aria-busy": loading || undefined }}
      title={`Choose ${titleLabel.toLowerCase()}`}
    >
        <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-strong">
              Choose {titleLabel.toLowerCase()}
            </h2>
            <div className="mt-1 flex min-w-0 items-center gap-1.5">
              <button
                className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-muted transition hover:bg-slate-100 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40 dark:hover:bg-[#2a2a2a]"
                disabled={!directory}
                title={pathCopied ? "Copied" : "Copy path"}
                type="button"
                onClick={copyCurrentPath}
              >
                {pathCopied ? <Check size={13} /> : <Copy size={13} />}
              </button>
              <button
                className="min-w-0 truncate text-left text-xs text-teal-700 underline-offset-2 transition hover:text-teal-800 hover:underline disabled:cursor-not-allowed disabled:text-muted disabled:no-underline"
                disabled={!directory}
                title={directory}
                type="button"
                onClick={openCurrentPath}
              >
                {directory || "Loading..."}
              </button>
            </div>
          </div>
          <button
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink dark:hover:bg-[#2a2a2a]"
            title="Close"
            type="button"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex items-center gap-2 border-b border-line px-4 py-2">
          <button
            className="h-8 rounded-md border border-line bg-white px-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!parent || loading}
            type="button"
            onClick={() => loadDirectory(parent)}
          >
            Up
          </button>
          <button
            className="h-8 rounded-md border border-line bg-white px-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={loading}
            type="button"
            onClick={async () => {
              const dataDir = await window.goferDesktop.getDataDir();
              loadDirectory(dataDir);
            }}
          >
            Gofer data
          </button>
          <button
            className="h-8 rounded-md border border-line bg-white px-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!directory || loading}
            type="button"
            onClick={() => requestCreateChild("file")}
          >
            New file
          </button>
          <button
            className="h-8 rounded-md border border-line bg-white px-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!directory || loading}
            type="button"
            onClick={() => requestCreateChild("folder")}
          >
            New folder
          </button>
          <button
            className="h-8 rounded-md border border-line bg-white px-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!directory}
            type="button"
            onClick={() => onSelect(directory)}
          >
            Choose current folder
          </button>
        </div>

        <div
          className="min-h-[260px] flex-1 overflow-y-auto p-2"
          onContextMenu={(event) => showContextMenu(event)}
        >
          {loading ? (
            <div className="flex h-40 items-center justify-center text-sm text-muted">
              <Loader2 size={16} className="mr-2 animate-spin" />
              Loading folder
            </div>
          ) : null}
          {error ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {error}
            </div>
          ) : null}
          {!loading && !error && entries.length === 0 ? (
            <div className="flex h-40 items-center justify-center text-sm text-muted">
              This folder is empty.
            </div>
          ) : null}
          {!loading && !error
            ? entries.map((entry) => (
                <button
                  key={entry.path}
                  className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition hover:bg-slate-50 ${
                    selectedPath === entry.path ? "bg-slate-100 text-strong" : "text-slate-700"
                  }`}
                  type="button"
                  onContextMenu={(event) => showContextMenu(event, entry)}
                  onClick={() =>
                    entry.isDirectory ? loadDirectory(entry.path) : setSelectedPath(entry.path)
                  }
                >
                  {entry.isDirectory ? (
                    <FolderOpen className="text-teal-600" size={16} />
                  ) : (
                    <FileText className="text-muted" size={16} />
                  )}
                  <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                  {entry.hidden ? <span className="text-[11px] text-muted">hidden</span> : null}
                </button>
              ))
            : null}
        </div>
        {contextMenu ? (
          <PathContextMenu
            canPaste={Boolean(copiedEntry)}
            copiedName={copiedEntry?.name}
            entry={contextMenu.entry}
            x={contextMenu.x}
            y={contextMenu.y}
            onCopy={() => copyEntry(contextMenu.entry)}
            onCreateFile={() => requestCreateChild("file")}
            onCreateFolder={() => requestCreateChild("folder")}
            onDelete={() => deleteEntry(contextMenu.entry)}
            onPaste={pasteEntry}
            onRename={() => requestRenameEntry(contextMenu.entry)}
          />
        ) : null}
        {nameRequest ? (
          <PathNameDialog
            directory={directory}
            initialName={nameRequest.initialName}
            kind={nameRequest.kind}
            mode={nameRequest.mode}
            onClose={() => setNameRequest(null)}
            onSubmit={(name) =>
              nameRequest.mode === "rename"
                ? renameEntry(nameRequest.entry, name)
                : createChild(nameRequest.kind, name)
            }
          />
        ) : null}

        <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-3">
          <p className="min-w-0 truncate text-xs text-muted">{selectedPath || directory}</p>
          <div className="flex shrink-0 items-center gap-2">
            <button
              className="h-9 rounded-lg border border-line bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
              type="button"
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              className="inline-flex h-9 items-center gap-2 rounded-lg bg-brand px-3 text-sm font-medium text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={!selectedPath}
              type="button"
              onClick={() => onSelect(selectedPath)}
            >
              <Check size={15} />
              Choose
            </button>
          </div>
        </div>
    </Dialog>
  );
}

function PathContextMenu({
  canPaste,
  copiedName,
  entry,
  onCopy,
  onCreateFile,
  onCreateFolder,
  onDelete,
  onPaste,
  onRename,
  x,
  y,
}) {
  return (
    <div
      className="fixed z-[90] min-w-44 overflow-hidden rounded-lg border border-line bg-white py-1 text-sm shadow-panel"
      style={{ left: x, top: y }}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
    >
      {entry ? (
        <>
          <button
            className="block w-full px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50"
            type="button"
            onClick={onRename}
          >
            Rename
          </button>
          <button
            className="block w-full px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50"
            type="button"
            onClick={onCopy}
          >
            Copy
          </button>
          <button
            className="block w-full px-3 py-2 text-left text-red-600 transition hover:bg-red-50"
            type="button"
            onClick={onDelete}
          >
            Delete
          </button>
          <div className="my-1 border-t border-line" />
        </>
      ) : null}
      <button
        className="block w-full px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400 disabled:hover:bg-transparent"
        disabled={!canPaste}
        title={canPaste ? `Paste ${copiedName}` : "Copy a file or folder first"}
        type="button"
        onClick={onPaste}
      >
        Paste
      </button>
      <div className="my-1 border-t border-line" />
      <div className="px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
        Create new
      </div>
      <button
        className="block w-full px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50"
        type="button"
        onClick={onCreateFile}
      >
        File
      </button>
      <button
        className="block w-full px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50"
        type="button"
        onClick={onCreateFolder}
      >
        Folder
      </button>
    </div>
  );
}

function nextCopyName(name = "", existingNames = new Set()) {
  let candidate = defaultCopyName(name);
  let index = 2;
  while (existingNames.has(candidate)) {
    candidate = defaultCopyName(name, index);
    index += 1;
  }
  return candidate;
}

function defaultCopyName(name = "", index = null) {
  const value = String(name || "copy");
  const dotIndex = value.lastIndexOf(".");
  const suffix = index ? ` copy ${index}` : " copy";
  if (dotIndex > 0) {
    return `${value.slice(0, dotIndex)}${suffix}${value.slice(dotIndex)}`;
  }
  return `${value}${suffix}`;
}

function InlineTextField({ diagnostics = [], onChange, placeholder, value }) {
  const diagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;
  return (
    <div className="min-w-0">
      <input
        aria-describedby={hasFieldDiagnostics ? diagnosticId : undefined}
        aria-invalid={fieldDiagnosticState(diagnostics).severity === "error" || undefined}
        className={`h-9 w-full rounded-lg border bg-white px-2 text-sm outline-none transition placeholder:text-slate-400 ${fieldBorderClass(diagnostics)}`}
        placeholder={placeholder}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value)}
      />
      <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
    </div>
  );
}

function CronExpressionField({
  diagnostics = [],
  label,
  onChange,
  onPickerOpenChange,
  pickerOpen,
  placeholder,
  value,
}) {
  const today = new Date();
  const defaultDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(
    2,
    "0",
  )}-${String(today.getDate()).padStart(2, "0")}`;
  const defaultTime = `${String(today.getHours()).padStart(2, "0")}:00`;
  const [date, setDate] = useState(defaultDate);
  const [time, setTime] = useState(defaultTime);
  const [recurrence, setRecurrence] = useState("weekly");
  const generatedCron = cronFromPicker({ date, recurrence, time });
  const diagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;

  return (
    <div className="relative">
      <span className="text-xs font-medium text-muted">{label}</span>
      <div className={`mt-1 flex h-10 overflow-hidden rounded-lg border bg-white transition focus-within:border-teal-500 ${fieldBorderClass(diagnostics, "border-line")}`}>
        <input
          aria-describedby={hasFieldDiagnostics ? diagnosticId : undefined}
          aria-invalid={fieldDiagnosticState(diagnostics).severity === "error" || undefined}
          className="min-w-0 flex-1 bg-transparent px-3 text-sm outline-none"
          placeholder={placeholder}
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value)}
        />
        <button
          className="grid w-10 shrink-0 place-items-center border-l border-line text-muted transition hover:bg-slate-50 hover:text-ink"
          title="Pick schedule"
          type="button"
          onClick={() => onPickerOpenChange(!pickerOpen)}
        >
          <CalendarDays size={17} />
        </button>
      </div>
      <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />

      {pickerOpen ? (
        <div className="absolute right-0 top-[68px] z-40 w-[270px] rounded-lg border border-line bg-white p-3 shadow-panel">
          <div className="space-y-3">
            <label className="block">
              <span className="text-xs font-medium text-muted">Recurrence</span>
              <select
                className="mt-1 h-9 w-full rounded-lg border border-line bg-white px-3 text-sm outline-none transition focus:border-teal-500"
                value={recurrence}
                onChange={(event) => setRecurrence(event.target.value)}
              >
                <option value="daily">Daily at this time</option>
                <option value="weekly">Weekly on this weekday</option>
                <option value="monthly">Monthly on this day</option>
                <option value="yearly">Yearly on this date</option>
              </select>
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="text-xs font-medium text-muted">Date</span>
                <input
                  className="mt-1 h-9 w-full rounded-lg border border-line bg-white px-2 text-sm outline-none transition focus:border-teal-500"
                  type="date"
                  value={date}
                  onChange={(event) => setDate(event.target.value)}
                />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-muted">Time</span>
                <input
                  className="mt-1 h-9 w-full rounded-lg border border-line bg-white px-2 text-sm outline-none transition focus:border-teal-500"
                  type="time"
                  value={time}
                  onChange={(event) => setTime(event.target.value)}
                />
              </label>
            </div>
            <div className="rounded-lg border border-line bg-slate-50 px-3 py-2">
              <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
                Cron
              </p>
              <p className="mt-1 font-mono text-sm text-ink">{generatedCron}</p>
            </div>
            <div className="flex justify-end gap-2">
              <button
                className="h-8 rounded-lg border border-line px-3 text-sm text-muted transition hover:bg-slate-50 hover:text-ink"
                type="button"
                onClick={() => onPickerOpenChange(false)}
              >
                Cancel
              </button>
              <button
                className="h-8 rounded-lg border border-teal-700 bg-teal-700 px-3 text-sm font-medium text-white transition hover:bg-teal-800"
                type="button"
                onClick={() => {
                  onChange(generatedCron);
                  onPickerOpenChange(false);
                }}
              >
                Apply
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function cronFromPicker({ date, recurrence, time }) {
  const [hour = "0", minute = "0"] = String(time || "00:00").split(":");
  const [, month = "1", day = "1"] = String(date || "").split("-");
  const weekday = date ? new Date(`${date}T00:00:00`).getDay() : "*";
  const cronMinute = Number(minute);
  const cronHour = Number(hour);
  const cronDay = Number(day);
  const cronMonth = Number(month);

  switch (recurrence) {
    case "daily":
      return `${cronMinute} ${cronHour} * * *`;
    case "monthly":
      return `${cronMinute} ${cronHour} ${cronDay} * *`;
    case "yearly":
      return `${cronMinute} ${cronHour} ${cronDay} ${cronMonth} *`;
    case "weekly":
    default:
      return `${cronMinute} ${cronHour} * * ${weekday}`;
  }
}

export function NumberField({
  allowRuntimeReference = false,
  diagnostics = [],
  label,
  min,
  onChange,
  placeholder,
  value,
}) {
  const diagnosticId = useId();
  const draftDiagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;
  const draftState = useCommittedDraft({
    format: (nextValue) => String(nextValue ?? ""),
    onCommit: onChange,
    parse: (text) => parseNumberDraft(text, min, allowRuntimeReference),
    value,
  });
  const describedBy = [
    hasFieldDiagnostics ? diagnosticId : null,
    draftState.error || draftState.conflict ? draftDiagnosticId : null,
  ]
    .filter(Boolean)
    .join(" ") || undefined;
  return (
    <label className="block">
      <span className="text-xs font-medium text-muted">{label}</span>
      <input
        aria-describedby={describedBy}
        aria-invalid={
          draftState.error || fieldDiagnosticState(diagnostics).severity === "error" || undefined
        }
        className={`mt-1 h-10 w-full rounded-lg border bg-white px-3 text-sm outline-none transition ${fieldBorderClass(diagnostics)} ${
          draftState.error ? "border-red-400 focus:border-red-500" : ""
        }`}
        inputMode={allowRuntimeReference ? "text" : "decimal"}
        placeholder={placeholder}
        type="text"
        value={draftState.draft}
        onBlur={draftState.onBlur}
        onChange={(event) => draftState.onChange(event.target.value)}
        onFocus={draftState.onFocus}
        onKeyDown={draftState.onKeyDown}
      />
      <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
      <DraftFieldMessage id={draftDiagnosticId} state={draftState} />
    </label>
  );
}

export function parseNumberDraft(text, min, allowRuntimeReference = false) {
  const trimmed = String(text).trim();
  if (!trimmed) return { ok: true, value: "" };
  if (allowRuntimeReference && isExactRuntimeReference(trimmed)) {
    return { ok: true, value: trimmed };
  }
  if (!/^[+-]?(?:(?:\d+\.?\d*)|(?:\.\d+))(?:[eE][+-]?\d+)?$/.test(trimmed)) {
    return {
      ok: false,
      error: allowRuntimeReference
        ? "Enter a complete number or an exact {{...}} reference."
        : "Enter a complete number.",
    };
  }

  const number = Number(trimmed);
  if (!Number.isFinite(number)) {
    return { ok: false, error: "Enter a finite number." };
  }
  if (min !== undefined && number < Number(min)) {
    return { ok: false, error: `Enter ${min} or greater.` };
  }
  return { ok: true, value: number };
}

function RuntimeReferenceField({ label, onChange, value }) {
  const state = useCommittedDraft({
    format: (nextValue) => String(nextValue ?? ""),
    onCommit: onChange,
    parse: (text) => {
      const trimmed = text.trim();
      return isExactRuntimeReference(trimmed)
        ? { ok: true, value: trimmed }
        : { ok: false, error: "Enter an exact reference such as {{inputs.value}}." };
    },
    value,
  });
  const messageId = useId();
  return (
    <label className="mt-2 block">
      <span className="text-xs text-muted">{label} reference</span>
      <input
        aria-describedby={state.error || state.conflict ? messageId : undefined}
        aria-invalid={state.error || undefined}
        className={`mt-1 h-10 w-full rounded-lg border bg-white px-3 font-mono text-sm outline-none transition ${
          state.error ? "border-red-400 focus:border-red-500" : "border-line focus:border-teal-600"
        }`}
        placeholder="{{inputs.value}}"
        type="text"
        value={state.draft}
        onBlur={state.onBlur}
        onChange={(event) => state.onChange(event.target.value)}
        onFocus={state.onFocus}
        onKeyDown={state.onKeyDown}
      />
      <DraftFieldMessage id={messageId} state={state} />
    </label>
  );
}

function SelectField({
  allowRuntimeReference = false,
  diagnostics = [],
  label,
  onChange,
  options,
  value,
}) {
  const diagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;
  const usesRuntimeReference = allowRuntimeReference && isExactRuntimeReference(value);
  const selectedValue = usesRuntimeReference ? "__runtime_reference__" : (value ?? "");
  return (
    <div className="block">
      <label>
        <span className="text-xs font-medium text-muted">{label}</span>
        <select
          aria-describedby={hasFieldDiagnostics ? diagnosticId : undefined}
          aria-invalid={fieldDiagnosticState(diagnostics).severity === "error" || undefined}
          className={`mt-1 h-10 w-full rounded-lg border bg-white px-3 text-sm outline-none transition ${fieldBorderClass(diagnostics)}`}
          value={selectedValue}
          onChange={(event) =>
            onChange(
              event.target.value === "__runtime_reference__"
                ? (usesRuntimeReference ? value : "{{inputs.value}}")
                : event.target.value,
            )
          }
        >
          {options.map(([optionValue, labelText]) => (
            <option key={optionValue} value={optionValue}>
              {labelText}
            </option>
          ))}
          {allowRuntimeReference ? (
            <option value="__runtime_reference__">Exact runtime reference…</option>
          ) : null}
        </select>
      </label>
      {usesRuntimeReference ? (
        <RuntimeReferenceField label={label} value={value} onChange={onChange} />
      ) : null}
      <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
    </div>
  );
}

function EdgeSelect({ diagnostics = [], onChange, options, value }) {
  const selectedLabel = options.find(([optionValue]) => optionValue === value)?.[1] ?? "";
  const diagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;

  return (
    <div>
      <select
        aria-describedby={hasFieldDiagnostics ? diagnosticId : undefined}
        aria-invalid={fieldDiagnosticState(diagnostics).severity === "error" || undefined}
        className={`h-9 w-full min-w-0 max-w-full rounded-lg border bg-white px-1.5 text-xs outline-none transition ${fieldBorderClass(diagnostics)}`}
        title={selectedLabel}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map(([optionValue, labelText]) => (
          <option key={optionValue} value={optionValue}>
            {labelText}
          </option>
        ))}
      </select>
      <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
    </div>
  );
}

function TextareaField({
  commitOnBlur = false,
  diagnostics = [],
  label,
  onChange,
  placeholder,
  rows = 3,
  value,
}) {
  const diagnosticId = useId();
  const draftDiagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;
  const draftState = useCommittedDraft({
    enterCommits: false,
    format: (nextValue) => String(nextValue ?? ""),
    onCommit: onChange,
    parse: (text) => ({ ok: true, value: text }),
    value,
  });
  const describedBy =
    [
      hasFieldDiagnostics ? diagnosticId : null,
      commitOnBlur && (draftState.error || draftState.conflict) ? draftDiagnosticId : null,
    ]
      .filter(Boolean)
      .join(" ") || undefined;
  return (
    <label className="block">
      <span className="text-xs font-medium text-muted">{label}</span>
      <textarea
        aria-describedby={describedBy}
        aria-invalid={fieldDiagnosticState(diagnostics).severity === "error" || undefined}
        className={`mt-1 w-full resize-none rounded-lg border px-3 py-2 text-sm outline-none transition ${fieldBorderClass(diagnostics)}`}
        placeholder={placeholder}
        rows={rows}
        value={commitOnBlur ? draftState.draft : value ?? ""}
        onBlur={commitOnBlur ? draftState.onBlur : undefined}
        onChange={(event) =>
          commitOnBlur ? draftState.onChange(event.target.value) : onChange(event.target.value)
        }
        onFocus={commitOnBlur ? draftState.onFocus : undefined}
        onKeyDown={
          commitOnBlur
            ? (event) => {
                if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                  event.preventDefault();
                  draftState.commitText();
                  return;
                }
                draftState.onKeyDown(event);
              }
            : undefined
        }
      />
      <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
      {commitOnBlur ? <DraftFieldMessage id={draftDiagnosticId} state={draftState} /> : null}
    </label>
  );
}

function ToggleField({
  allowRuntimeReference = false,
  checked,
  diagnostics = [],
  disabled = false,
  label,
  onChange,
}) {
  const diagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;
  const usesRuntimeReference = allowRuntimeReference && isExactRuntimeReference(checked);
  return (
    <div>
      <label
        className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-2 ${fieldBorderClass(diagnostics, "border-line")} ${
          disabled ? "cursor-not-allowed bg-slate-50 text-muted dark:bg-[#252526]" : ""
        }`}
      >
        <span className="text-sm font-medium text-slate-700">{label}</span>
        <input
          aria-describedby={hasFieldDiagnostics ? diagnosticId : undefined}
          aria-invalid={fieldDiagnosticState(diagnostics).severity === "error" || undefined}
          checked={usesRuntimeReference ? false : Boolean(checked)}
          className="h-4 w-4 accent-teal-700"
          disabled={disabled}
          type="checkbox"
          onChange={(event) => onChange(event.target.checked)}
        />
      </label>
      {allowRuntimeReference && !disabled ? (
        <button
          className="mt-1 text-xs font-medium text-teal-700 hover:text-teal-900"
          type="button"
          onClick={() => onChange(usesRuntimeReference ? false : "{{inputs.value}}")}
        >
          {usesRuntimeReference ? "Use a literal toggle" : "Use an exact runtime reference"}
        </button>
      ) : null}
      {usesRuntimeReference ? (
        <RuntimeReferenceField label={label} value={checked} onChange={onChange} />
      ) : null}
      <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
    </div>
  );
}

export function ListField({ diagnostics = [], label, onChange, placeholder, value }) {
  const state = useCommittedDraft({
    format: (nextValue) =>
      Array.isArray(nextValue) ? nextValue.join(", ") : String(nextValue ?? ""),
    onCommit: onChange,
    parse: (text) => {
      const trimmed = text.trim();
      if (isExactRuntimeReference(trimmed)) return { ok: true, value: trimmed };
      return {
        ok: true,
        value: text
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      };
    },
    value,
  });
  return (
    <DraftTextareaField
      diagnostics={diagnostics}
      label={label}
      placeholder={placeholder}
      rows={2}
      state={state}
    />
  );
}

export function KeyValueField({ diagnostics = [], label, onChange, value }) {
  const state = useCommittedDraft({
    format: objectToKeyValueText,
    onCommit: onChange,
    parse: parseKeyValueDraft,
    value,
  });
  return (
    <DraftTextareaField diagnostics={diagnostics} label={label} rows={3} state={state} />
  );
}

function DraftTextareaField({ diagnostics, label, placeholder, rows, state }) {
  const diagnosticId = useId();
  const draftDiagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;
  const describedBy = [
    hasFieldDiagnostics ? diagnosticId : null,
    state.error || state.conflict ? draftDiagnosticId : null,
  ]
    .filter(Boolean)
    .join(" ") || undefined;
  return (
    <label className="block">
      <span className="text-xs font-medium text-muted">{label}</span>
      <textarea
        aria-describedby={describedBy}
        aria-invalid={
          state.error || fieldDiagnosticState(diagnostics).severity === "error" || undefined
        }
        className={`mt-1 w-full resize-none rounded-lg border px-3 py-2 text-sm outline-none transition ${fieldBorderClass(diagnostics)} ${
          state.error ? "border-red-400 focus:border-red-500" : ""
        }`}
        placeholder={placeholder}
        rows={rows}
        value={state.draft}
        onBlur={state.onBlur}
        onChange={(event) => state.onChange(event.target.value)}
        onFocus={state.onFocus}
        onKeyDown={state.onKeyDown}
      />
      <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
      <DraftFieldMessage id={draftDiagnosticId} state={state} />
    </label>
  );
}

function parseKeyValueDraft(text) {
  if (isExactRuntimeReference(String(text).trim())) {
    return { ok: true, value: String(text).trim() };
  }
  const entries = [];
  const lines = String(text).split("\n");
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line) continue;
    const separatorIndex = line.indexOf("=");
    if (separatorIndex === -1) {
      return { ok: false, error: `Line ${index + 1} needs an “=” between the key and value.` };
    }
    const key = line.slice(0, separatorIndex).trim();
    if (!key) {
      return { ok: false, error: `Line ${index + 1} needs a key before “=”.` };
    }
    entries.push([key, line.slice(separatorIndex + 1).trim()]);
  }
  return { ok: true, value: Object.fromEntries(entries) };
}

export function formatJsonBodyEditorValue(value) {
  if (value === null || value === undefined || value === "") return "";
  return JSON.stringify(value, null, 2);
}

export function parseJsonBodyEditorValue(text) {
  if (!text.trim()) return { ok: true, value: null };
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "Invalid JSON",
    };
  }
}

function JsonBodyField({ label, onChange, value }) {
  const draftState = useCommittedDraft({
    format: formatJsonBodyEditorValue,
    onCommit: onChange,
    parse: parseJsonBodyEditorValue,
    value,
  });
  const draftDiagnosticId = useId();

  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-slate-600">
      {label}
      <textarea
        aria-describedby={
          draftState.error || draftState.conflict ? draftDiagnosticId : undefined
        }
        aria-invalid={draftState.error || undefined}
        className={`min-h-[8rem] rounded-lg border bg-white px-2 py-1.5 font-mono text-xs text-slate-900 outline-none transition focus:ring-2 focus:ring-brand/20 ${
          draftState.error
            ? "border-red-300 focus:border-red-400"
            : "border-line focus:border-brand"
        }`}
        rows={6}
        value={draftState.draft}
        onBlur={draftState.onBlur}
        onChange={(event) => draftState.onChange(event.target.value)}
        onFocus={draftState.onFocus}
        onKeyDown={draftState.onKeyDown}
      />
      <DraftFieldMessage id={draftDiagnosticId} state={draftState} />
    </label>
  );
}

const inputTargetOptions = [
  ["stdin", "Standard input"],
  ["env.FILE_PATH", "Env: FILE_PATH"],
  ["env.FILE_NAME", "Env: FILE_NAME"],
  ["env.FILE_STEM", "Env: FILE_STEM"],
  ["env.FILE_EXTENSION", "Env: FILE_EXTENSION"],
  ["env.FOLDER_PATH", "Env: FOLDER_PATH"],
  ["env.CONTENT", "Env: CONTENT"],
  ["env.INDEX", "Env: INDEX"],
  ["file_path", "Prompt variable: file_path"],
  ["file_name", "Prompt variable: file_name"],
  ["file_stem", "Prompt variable: file_stem"],
  ["file_extension", "Prompt variable: file_extension"],
  ["folder_path", "Prompt variable: folder_path"],
  ["content", "Prompt variable: content"],
  ["index", "Prompt variable: index"],
  ["row", "Prompt variable: row"],
  ["query", "Prompt variable: query"],
];

function InputMappingField({ nodeType, onChange, sourceOptions, value }) {
  const entries = Object.entries(value ?? {});
  const hasLoopFileSources = sourceOptions.some(([source]) => source === "loop.current.file_path");
  const targetOptions = useMemo(() => {
    const usedTargets = new Set(inputTargetOptions.map(([optionValue]) => optionValue));
    const customTargets = Object.keys(value ?? {})
      .filter((target) => target && !usedTargets.has(target))
      .map((target) => [target, target]);
    return [...inputTargetOptions, ...customTargets];
  }, [value]);
  const sourceOptionsWithCustom = useMemo(() => {
    const usedSources = new Set(sourceOptions.map(([optionValue]) => optionValue));
    const customSources = Object.values(value ?? {})
      .filter((source) => source && !usedSources.has(source))
      .map((source) => [source, source]);
    return [...sourceOptions, ...customSources];
  }, [sourceOptions, value]);

  function updateEntry(index, nextKey, nextValue) {
    const next = {};
    entries.forEach(([key, item], entryIndex) => {
      if (entryIndex === index) {
        if (nextKey.trim()) {
          next[nextKey.trim()] = nextValue;
        }
      } else {
        next[key] = item;
      }
    });
    onChange(next);
  }

  function removeEntry(index) {
    onChange(Object.fromEntries(entries.filter((_, entryIndex) => entryIndex !== index)));
  }

  function addEntry() {
    const key = nextInputKey(value ?? {});
    onChange({ ...(value ?? {}), [key]: "previous.text" });
  }

  function addLoopFileInputs() {
    const mappings =
      nodeType === "agent" || nodeType === "common_llm_task" || nodeType === "prompt_file"
        ? {
            file_path: "loop.current.file_path",
            file_name: "loop.current.file_name",
            file_stem: "loop.current.file_stem",
            file_extension: "loop.current.file_extension",
          }
        : {
            "env.FILE_PATH": "loop.current.file_path",
            "env.FILE_NAME": "loop.current.file_name",
            "env.FILE_STEM": "loop.current.file_stem",
            "env.FILE_EXTENSION": "loop.current.file_extension",
          };
    onChange({ ...(value ?? {}), ...mappings });
  }

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-[minmax(0,0.9fr)_minmax(0,1.2fr)_32px] gap-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
        <span>Input</span>
        <span>Source output</span>
        <span />
      </div>
      {entries.length ? (
        entries.map(([key, source], index) => (
          <div
            key={`${key}-${index}`}
            className="grid grid-cols-[minmax(0,0.9fr)_minmax(0,1.2fr)_32px] gap-2"
          >
            <select
              className="h-9 min-w-0 rounded-lg border border-line bg-white px-2 text-xs outline-none transition focus:border-teal-500"
              value={key}
              onChange={(event) => updateEntry(index, event.target.value, source)}
            >
              {targetOptions.map(([optionValue, label]) => (
                <option key={optionValue} value={optionValue}>
                  {label}
                </option>
              ))}
            </select>
            <select
              className="h-9 min-w-0 rounded-lg border border-line bg-white px-2 text-xs outline-none transition focus:border-teal-500"
              value={source}
              onChange={(event) => updateEntry(index, key, event.target.value)}
            >
              {sourceOptionsWithCustom.map(([optionValue, label]) => (
                <option key={optionValue} value={optionValue}>
                  {label}
                </option>
              ))}
            </select>
            <button
              className="grid h-9 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-red-600 dark:hover:bg-[#2a2a2a]"
              title="Remove input"
              type="button"
              onClick={() => removeEntry(index)}
            >
              <X size={14} />
            </button>
          </div>
        ))
      ) : (
        <p className="rounded-lg border border-dashed border-line px-3 py-2 text-xs leading-5 text-muted">
          Map parent outputs into stdin, environment variables, or prompt variables.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-white px-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
          type="button"
          onClick={addEntry}
        >
          <Plus size={13} />
          Add input
        </button>
        {hasLoopFileSources ? (
          <button
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-white px-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
            title={
              nodeType === "agent" || nodeType === "common_llm_task" || nodeType === "prompt_file"
                ? "Map loop file fields as prompt variables"
                : "Map loop file fields as environment variables"
            }
            type="button"
            onClick={addLoopFileInputs}
          >
            <Files size={13} />
            Loop file inputs
          </button>
        ) : null}
      </div>
    </div>
  );
}

function nextInputKey(value = {}) {
  if (!Object.hasOwn(value, "stdin")) return "stdin";
  let index = 1;
  while (Object.hasOwn(value, `input_${index}`)) {
    index += 1;
  }
  return `input_${index}`;
}

function objectToKeyValueText(value = {}) {
  if (typeof value === "string") return value;
  return Object.entries(value)
    .map(([key, item]) => `${key}=${item}`)
    .join("\n");
}

function isExactRuntimeReference(value) {
  return /^\s*\{\{\s*[^{}]+?\s*\}\}\s*$/.test(String(value));
}

function runtimeListEditorValue(value) {
  return Array.isArray(value) ? value.map((item) => String(item)) : value;
}

function runtimeIntegerListValue(value) {
  if (typeof value === "string") return value;
  return value.map((item) => Number(item));
}
