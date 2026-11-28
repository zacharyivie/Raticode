import { autoLayoutWorkflow } from "../lib/workflowLayout.js";
import { nodeDeclarationsText, pastedNodePositions, readClipboardText, rememberCopiedNodes } from "../lib/graphClipboard.js";
export { autoLayoutWorkflow } from "../lib/workflowLayout.js";
import { NodePicker } from "./NodePicker.jsx";
import { nextNodeId, pickedNodePosition } from "../lib/nodeCatalog.js";
export { PathNameDialog } from "./PathNameDialog.jsx";
import brandCompat from "../lib/brandCompat.js";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertCircle,
  Bell,
  Braces,
  Check,
  ClipboardCopy,
  ClipboardPaste,
  ChevronDown,
  ChevronRight,
  Command,
  Copy,
  Database,
  Download,
  ExternalLink,
  FilePenLine,
  Files,
  FileText,
  FileX,
  FolderOpen,
  Globe2,
  LocateFixed,
  Layers,
  ListChecks,
  Loader2,
  Maximize2,
  MessageCircleQuestion,
  Minimize2,
  MoreHorizontal,
  MoveRight,
  PencilLine,
  Play,
  Plug,
  Plus,
  Repeat2,
  RotateCw,
  Route,
  Scissors,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  Undo2,
  Redo2,
  Upload,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { apiUrl } from "../lib/api.js";
import { DEFAULT_APP_SETTINGS, matchesCommand } from "../lib/settings.js";
import { Dialog } from "./Dialog.jsx";
import { useOffReason } from "./OffReason.jsx";
import GraphOutline, { edgeLabel } from "./GraphOutline.jsx";
import { useProviderAccountList } from "../lib/useProviderAccountList.js";
import { resolveDisplayPath } from "../lib/displayPaths.js";
import {
  ApprovalOptionButtons,
  ApprovalReplyField,
  approvalIdentity,
  approvalReplyMode,
  TextFileDialog,
} from "./InspectorFields.jsx";
import { formatRunLabel, RunPickerLabel, RunStatusDot, StopRequestedLabel } from "./RunTimelinePanel.jsx";
import { dateTime } from "../lib/timeText.js";
import { diagnosticsByTarget, rattishRouteValue, workflowValidationDiagnostics } from "../lib/rattishSource.js";
import { ProviderAccountsContext, RattishInspector } from "./RattishInspector.jsx";

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
  jev: {
    icon: ListChecks,
    accent: "bg-teal-700",
    border: "border-teal-200",
    chip: "bg-teal-50 text-teal-700 border-teal-100",
  },
  http_request: {
    icon: Globe2,
    accent: "bg-blue-700",
    border: "border-blue-200",
    chip: "bg-blue-50 text-blue-700 border-blue-100",
  },
  read_web_page: {
    icon: FileText,
    accent: "bg-blue-700",
    border: "border-blue-200",
    chip: "bg-blue-50 text-blue-700 border-blue-100",
  },
  mcp_tool: {
    icon: Plug,
    accent: "bg-violet-700",
    border: "border-violet-200",
    chip: "bg-violet-50 text-violet-700 border-violet-100",
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
  rem_prompt: {
    icon: MessageCircleQuestion,
    accent: "bg-violet-700",
    border: "border-violet-200",
    chip: "bg-violet-50 text-violet-700 border-violet-100",
  },
  rem_task: {
    icon: ListChecks,
    accent: "bg-violet-700",
    border: "border-violet-200",
    chip: "bg-violet-50 text-violet-700 border-violet-100",
  },
};

const minZoom = 0.45;
const maxZoom = 1.8;
// Fitting a whole graph never enlarges it; a one-node workflow stays readable
// at its normal size instead of filling the canvas.
export const graphFitMaxZoom = 1;
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

export function nodeStackIndex(
  nodeId,
  { draggingNodeId = null, selectedNodeId = null, selectedNodeIds = [] } = {},
) {
  if (draggingNodeId === nodeId) return nodeStack.dragging;
  if (selectedNodeId === nodeId) return nodeStack.activeSelected;
  if (selectedNodeIds.includes(nodeId)) return nodeStack.selected;
  return nodeStack.base;
}

// The one-line summary under a node's name, from its Rattish configuration.
export function nodeMetaFromOperation(operation = {}, pathBasePath = "") {
  switch (operation.type) {
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
      return `${operation.task || "summarize"} with ${operation.provider || "agent"}`;
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
    case "read_web_page":
      return `read ${operation.url || "web page"}`;
    case "mcp_tool":
      return `${operation.server || "server"} · ${operation.tool || "tool"}`;
    case "jev":
      return `${operation.question_type === "choice" ? "choice" : "yes/no"} · ${operation.question || "question"}`;
    case "approval_gate":
      return operation.decision_timeout
        ? `approval timeout ${operation.decision_timeout}`
        : "approval required";
    case "notification":
      return `${operation.channel || "desktop"} · ${operation.title || "notification"}`;
    case "rem_prompt":
      return `ask Rem · ${operation.prompt || "prompt"}`;
    case "rem_task":
      return operation.wait === false ? "start Rem task" : "run Rem task and wait";
    case "agent": {
      const provider = operation.provider || "agent";
      if (operation.skill) return `${provider} · /${operation.skill}`;
      return operation.prompt_path
        ? `${provider} · ${resolveDisplayPath(operation.prompt_path, pathBasePath)}`
        : provider;
    }
    default:
      return "operation";
  }
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

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function resizerValueForKey(
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

const GRAPH_COMMAND_IDS = ["graph.selectAll", "graph.deleteSelection", "graph.zoomIn", "graph.zoomOut", "graph.fit"];
const GRAPH_HISTORY_COMMAND_IDS = ["graph.undo", "graph.redo"];
const GRAPH_CLIPBOARD_COMMAND_IDS = ["graph.copy", "graph.cut", "graph.paste"];

// Ctrl+Z undoes a graph edit only while the graph pane, or nothing in particular, has
// focus. Text fields, the code editor and Rem's composer keep their own undo.
function graphHasFocus(target) {
  if (!target || target === document.body || target === document.documentElement) return true;
  for (let node = target; node; node = node.parentNode) {
    if (node.getAttribute?.("data-graph-active") === "true") return true;
  }
  return false;
}

// Ctrl+C, Ctrl+X and Ctrl+V act on nodes only while the graph itself has focus, and Copy
// and Cut leave text selected on the page to the browser.
function graphOwnsClipboard(target, canvas) {
  if (!target || target === document.body || target === document.documentElement) return false;
  if (!canvas?.contains?.(target) && !graphHasFocus(target)) return false;
  const selection = window.getSelection?.();
  return !selection || selection.isCollapsed !== false || !String(selection).trim();
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
  graphHistory = null,
  onGraphUndo,
  onGraphRedo,
  onGraphNotice,
  onRunWorkflow,
  onSettingChange,
  onResumeRunLog,
  onRerunRunLog,
  onRunFromNode,
  onSelectRunLog,
  onStopRunLog,
  onStopWorkflow,
  stopDisabled,
  stopTitle = "Stop all runs",
  onValidateWorkflow,
  onWorkflowChange,
}) {
  const canvasRef = useRef(null);
  const graphActionsRef = useRef(null);
  const importInputRef = useRef(null);
  const deleteSelectedNodeRef = useRef(null);
  const graphHistoryActionsRef = useRef({});
  graphHistoryActionsRef.current = { undo: onGraphUndo, redo: onGraphRedo };
  const graphClipboardActionsRef = useRef({});
  const addNodeRef = useRef(null);
  // Positions to save, by node ID, once those nodes show up in the graph.
  const [pendingPlacement, setPendingPlacement] = useState(null);
  const canvasRightClickRef = useRef(null);
  const nodeDragMovedRef = useRef(false);
  const nodeDragSelectionRef = useRef([]);
  const nodeDragRef = useRef(null);
  const [selectedNodeId, setSelectedNodeId] = useState();
  const [selectedNodeIds, setSelectedNodeIds] = useState([]);
  const [draggingNodeId, setDraggingNodeId] = useState(null);
  const [panningPointerId, setPanningPointerId] = useState(null);
  const [selectionBox, setSelectionBox] = useState(null);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(true);
  const [inspectorWidth, setInspectorWidth] = useState(settings.layout.graphInspectorWidth);
  const [expandedFolderNodes, setExpandedFolderNodes] = useState({});
  const [providerProfiles, setProviderProfiles] = useState([]);
  const { accounts: accountRows, providers: accountProviders } = useProviderAccountList();
  const providerAccounts = useMemo(
    () => ({ accounts: accountRows, providers: accountProviders }),
    [accountRows, accountProviders],
  );

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
  const [runMenuOpen, setRunMenuOpen] = useState(false);
  const [selectedEdgeId, setSelectedEdgeId] = useState(null);
  const [draftEdge, setDraftEdge] = useState(null);
  const [keyboardConnectionFrom, setKeyboardConnectionFrom] = useState(null);
  const [outlineFocusRequest, setOutlineFocusRequest] = useState(null);
  const [viewport, setViewport] = useState({ x: 0, y: 0, scale: 1 });
  const workflowNodesRef = useRef([]);
  const initialFitWorkflowIdRef = useRef(null);
  const [minimapDragging, setMinimapDragging] = useState(false);
  const [mapOpen, setMapOpen] = useState(false);
  const [mapTab, setMapTab] = useState("outline");
  const [graphFullscreen, setGraphFullscreen] = useState(false);

  useEffect(() => {
    if (!active) setGraphFullscreen(false);
  }, [active]);
  const [nodeContextMenu, setNodeContextMenu] = useState(null);
  const [canvasContextMenu, setCanvasContextMenu] = useState(null);
  // Open node picker: { afterNodeId } names the node a picked node is placed after.
  const [nodePicker, setNodePicker] = useState(null);
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
  // Rattish runs resolve relative paths against the project, not the app data folder.
  const pathBaseDir =
    workflow.sourceFormat === "rattish" && workflow.projectRoot ? workflow.projectRoot : dataDir;
  const editingDisabled = invalidWorkflow || readOnly;
  const graphHistoryEditable = rattishMode && !editingDisabled;
  const undoEntry = graphHistoryEditable ? graphHistory?.undo?.at(-1) : null;
  const redoEntry = graphHistoryEditable ? graphHistory?.redo?.at(-1) : null;
  addNodeRef.current = rattishMode && !editingDisabled && !nodePicker ? () => addNode() : null;
  const validationDiagnostics = workflowValidationDiagnostics(workflow);
  const blockingValidationErrors = validationDiagnostics.filter(
    (diagnostic) => diagnostic.severity === "error",
  );
  const runDisabled = invalidWorkflow || blockingValidationErrors.length > 0;
  const workflowNodes = useMemo(() => workflow.nodes ?? [], [workflow.nodes]);
  workflowNodesRef.current = workflowNodes;
  const workflowEdges = useMemo(() => workflow.edges ?? [], [workflow.edges]);
  const needsLinks = useMemo(() => rattishNeedsLinks(workflowNodes, workflowEdges), [workflowEdges, workflowNodes]);
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
  const selectedNodeOutput = selectedNodeId
    ? runResult?.nodeOutputs?.[selectedNodeId] ?? historicalNodeOutputs?.[selectedNodeId] ?? null
    : null;
  const selectedRunNode = selectedNodeId ? runNodes?.[selectedNodeId] ?? null : null;
  const nodesById = useMemo(() => {
    return Object.fromEntries(workflowNodes.map((node) => [node.id, node]));
  }, [workflowNodes]);
  const pendingApprovals = useMemo(() => {
    return (
      approvalState?.approvals?.filter(
        (approval) => approval.status === "pending" && nodesById[approval.nodeId],
      ) ?? []
    )
      .slice()
      .sort((first, second) =>
        String(first.requestedAt || "").localeCompare(String(second.requestedAt || "")),
      );
  }, [approvalState?.approvals, nodesById]);
  const pendingApproval = pendingApprovals[0] ?? null;
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
      return `Connection selected from ${nodesById[selectedEdge.from]?.label ?? selectedEdge.from} to ${nodesById[selectedEdge.to]?.label ?? selectedEdge.to}. Condition ${edgeLabel(selectedEdge)}.`;
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

      if (deletedEdge) {
        const routes = remainingEdges
          .filter((edge) => edge.from === deletedEdge.from)
          .map((edge) => rattishRouteValue(edge, rattishDocument?.source));
        void onRattishMutation?.([
          { kind: "set_routes", node: deletedEdge.from, routes },
        ]);
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
      rattishDocument?.source,
      selectedEdgeId,
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
    nodeDragRef.current = null;
    setDraggingNodeId(null);
    setPanningPointerId(null);
    setSelectionBox(null);
    const rect = canvasRef.current?.getBoundingClientRect();
    const nodes = workflowNodesRef.current;
    initialFitWorkflowIdRef.current = nodes.length ? workflow.id : null;
    setViewport(nodes.length
      ? fitViewportToNodes(nodes, {
          width: rect?.width || 960,
          height: rect?.height || 640,
        }, { maxZoom: graphFitMaxZoom })
      : { x: 0, y: 0, scale: 1 });
  }, [workflow.id]);

  // Source documents often load after the canvas opens. Fit once when the
  // first nodes arrive so the graph does not sit at an arbitrary origin, unless
  // the user already moved the empty canvas.
  useEffect(() => {
    if (initialFitWorkflowIdRef.current === workflow.id || !workflowNodes.length) return;
    initialFitWorkflowIdRef.current = workflow.id;
    const rect = canvasRef.current?.getBoundingClientRect();
    const fitted = fitViewportToNodes(workflowNodes, {
      width: rect?.width || 960,
      height: rect?.height || 640,
    }, { maxZoom: graphFitMaxZoom });
    setViewport((current) => (current.x === 0 && current.y === 0 && current.scale === 1 ? fitted : current));
  }, [workflow.id, workflowNodes]);

  // Place added and pasted nodes once they show up in the graph. Positions live in
  // workflow metadata, so this never changes workflow.rattish.
  useEffect(() => {
    if (!pendingPlacement) return;
    const ids = Object.keys(pendingPlacement);
    if (!ids.every((id) => workflowNodes.some((node) => node.id === id))) return;
    setPendingPlacement(null);
    onWorkflowChange({
      ...workflow,
      nodes: workflowNodes.map((node) =>
        pendingPlacement[node.id] ? { ...node, ...pendingPlacement[node.id] } : node,
      ),
    });
  }, [onWorkflowChange, pendingPlacement, workflow, workflowNodes]);

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
    function handleGraphCommand(event) {
      if (active && !readOnly && selectedEdgeId && event?.detail?.commandId === "graph.deleteSelection") deleteEdge(selectedEdgeId);
    }

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("gofer:graph-command", handleGraphCommand);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("gofer:graph-command", handleGraphCommand);
    };
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
        setNodeContextMenu(null);
        setCanvasContextMenu(null);
        return;
      }

      if (editingText || canvasOverlay) return;

      // Only while the graph canvas itself has focus, so Tab still moves between controls
      // everywhere else. Shift+Tab leaves the graph as usual.
      if (
        matchesCommand(event, settings, "graph.addNode") &&
        canvasRef.current &&
        (target === canvasRef.current || canvasRef.current.contains?.(target))
      ) {
        if (!addNodeRef.current) return;
        event.preventDefault();
        addNodeRef.current();
        return;
      }

      const historyCommandId = GRAPH_HISTORY_COMMAND_IDS.find((id) => matchesCommand(event, settings, id));
      if (historyCommandId) {
        if (!graphHasFocus(target)) return;
        event.preventDefault();
        runGraphCommand(historyCommandId);
        return;
      }

      const clipboardCommandId = GRAPH_CLIPBOARD_COMMAND_IDS.find((id) => matchesCommand(event, settings, id));
      if (clipboardCommandId) {
        const actions = graphClipboardActionsRef.current;
        if (!graphOwnsClipboard(target, canvasRef.current)) return;
        if (clipboardCommandId === "graph.paste" ? !actions.canPaste?.() : !actions.canCopy?.()) return;
        event.preventDefault();
        runGraphCommand(clipboardCommandId);
        return;
      }

      const commandId = GRAPH_COMMAND_IDS.find((id) => (id !== "graph.deleteSelection" || selectedNodeId)
        && matchesCommand(event, settings, id));
      if (!commandId) return;
      event.preventDefault();
      runGraphCommand(commandId);
    }

    function zoomBy(factor) {
      setViewport((current) => {
        const rect = canvasRef.current?.getBoundingClientRect();
        const centerX = (rect?.width || 960) / 2;
        const centerY = (rect?.height || 640) / 2;
        const nextScale = clamp(current.scale * factor, minZoom, maxZoom);
        const contentX = (centerX - current.x) / current.scale;
        const contentY = (centerY - current.y) / current.scale;
        return {
          scale: nextScale,
          x: centerX - contentX * nextScale,
          y: centerY - contentY * nextScale,
        };
      });
    }

    function runGraphCommand(commandId) {
      if (commandId === "graph.selectAll") {
        setSelectedEdgeId(null);
        setSelectedNodeIds(workflowNodes.map((node) => node.id));
        setSelectedNodeId(workflowNodes.at(-1)?.id);
      }
      if (commandId === "graph.deleteSelection" && selectedNodeId) deleteSelectedNodeRef.current?.();
      if (commandId === "graph.undo" && graphHistoryEditable) void graphHistoryActionsRef.current.undo?.();
      if (commandId === "graph.redo" && graphHistoryEditable) void graphHistoryActionsRef.current.redo?.();
      if (GRAPH_CLIPBOARD_COMMAND_IDS.includes(commandId)) void graphClipboardActionsRef.current[commandId]?.();
      if (commandId === "graph.zoomIn") zoomBy(1.14);
      if (commandId === "graph.zoomOut") zoomBy(0.88);
      if (commandId === "graph.fit") {
        const rect = canvasRef.current?.getBoundingClientRect();
        setViewport(
          fitViewportToNodes(workflowNodes, {
            width: rect?.width || 960,
            height: rect?.height || 640,
          }, { maxZoom: graphFitMaxZoom }),
        );
      }
    }

    // The command palette runs graph commands by name, for the graph on screen.
    function handleGraphCommand(event) {
      if (!active || canvasOverlay) return;
      const commandId = event?.detail?.commandId;
      if (commandId === "graph.addNode") addNodeRef.current?.();
      if ([...GRAPH_COMMAND_IDS, ...GRAPH_HISTORY_COMMAND_IDS, ...GRAPH_CLIPBOARD_COMMAND_IDS].includes(commandId)) runGraphCommand(commandId);
    }

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("gofer:graph-command", handleGraphCommand);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("gofer:graph-command", handleGraphCommand);
    };
  }, [active, canvasOverlay, graphFullscreen, graphHistoryEditable, settings, workflowNodes, selectedNodeId]);

  useEffect(() => {
    if (panningPointerId === null) return undefined;

    const previousCursor = document.body.style.cursor;
    document.body.style.cursor = "grabbing";
    return () => {
      document.body.style.cursor = previousCursor;
    };
  }, [panningPointerId]);

  function canvasViewportSize() {
    const rect = canvasRef.current?.getBoundingClientRect();
    return {
      width: rect?.width || 960,
      height: rect?.height || 640,
    };
  }

  function fitNodes(nodes, options) {
    if (!nodes.length) return;
    setViewport(fitViewportToNodes(nodes, canvasViewportSize(), options));
  }

  function fitGraph() {
    fitNodes(workflowNodes, { maxZoom: graphFitMaxZoom });
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

  function addNode() {
    if (editingDisabled || !rattishMode) return;
    // Add node opens the picker; the node is added once a type is chosen.
    const afterNodeId =
      selectedNodeId && nodesById[selectedNodeId] && selectedNodeIds.length <= 1
        ? selectedNodeId
        : null;
    setNodeContextMenu(null);
    setNodePicker({ afterNodeId });
  }

  function closeNodePicker() {
    setNodePicker(null);
    canvasRef.current?.focus?.();
  }

  // A picked node is one Rattish mutation batch: the route from the selected node first,
  // so a Break node can find the loop before it, then the node itself. The backend refuses
  // the whole batch if it would stop the workflow compiling.
  function addPickedNode(nodeType) {
    const afterNodeId = nodePicker?.afterNodeId ?? null;
    setNodePicker(null);
    const nodeId = nextNodeId(workflowNodes, nodeType);
    const position = pickedNodePosition(workflowNodes, afterNodeId);
    const afterNode = afterNodeId ? nodesById[afterNodeId] : null;
    const canRouteOn =
      afterNode &&
      afterNode.type !== "break" &&
      !afterNode.rattish?.authoredFields?.finish;
    const mutations = [];
    if (canRouteOn) {
      mutations.push({
        kind: "set_routes",
        node: afterNodeId,
        routes: [
          ...workflowEdges
            .filter((edge) => edge.from === afterNodeId)
            .map((edge) => rattishRouteValue(edge, rattishDocument?.source)),
          nodeId,
        ],
      });
    }
    mutations.push({ kind: "add_node", node: nodeId, node_type: nodeType });
    // Saved to workflow metadata once the new node shows up in the graph.
    setPendingPlacement({ [nodeId]: { x: position.x, y: position.y } });
    void Promise.resolve(onRattishMutation?.(mutations)).then((document) => {
      canvasRef.current?.focus?.();
      if (!document || document.error) {
        setPendingPlacement((current) => (current?.[nodeId] ? null : current));
        return;
      }
      setSelectedNodeId(nodeId);
      setSelectedNodeIds([nodeId]);
      setSelectedEdgeId(null);
      setInspectorCollapsed(false);
    });
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

    void onRattishMutation?.([{ kind: "delete_node", node: nodeId }]);
    if (deletingSelectedNode) {
      setSelectedNodeId(nextSelectedId);
      setSelectedNodeIds(nextSelectedId ? [nextSelectedId] : []);
      setSelectedEdgeId(null);
    }
    setNodeContextMenu((current) => (current?.nodeId === nodeId ? null : current));
    return deletingSelectedNode && nextSelectedId ? `node:${nextSelectedId}` : null;
  }

  function duplicateNode(nodeId) {
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
      if (!document || document.error) return;
      setSelectedNodeId(nextId);
      setSelectedNodeIds([nextId]);
      setSelectedEdgeId(null);
    });
    setNodeContextMenu(null);
    return `node:${nextId}`;
  }

  function clipboardNodes() {
    const ids = selectedNodeIds.length ? selectedNodeIds : selectedNodeId ? [selectedNodeId] : [];
    return workflowNodes.filter((node) => ids.includes(node.id) && node.rattish?.sourceSpan);
  }

  function countedNodes(nodes) {
    return nodes.length === 1 ? `node ${nodes[0].label ?? nodes[0].id}` : `${nodes.length} nodes`;
  }

  // Copy puts the nodes' workflow.rattish declarations on the clipboard as plain text.
  async function copyNodes({ cut = false } = {}) {
    setNodeContextMenu(null);
    setCanvasContextMenu(null);
    if (!rattishMode || (cut && editingDisabled)) return false;
    const nodes = clipboardNodes();
    const text = nodeDeclarationsText(rattishDocument?.source, nodes);
    if (!text) {
      onGraphNotice?.({ type: "info", message: `Select the nodes to ${cut ? "cut" : "copy"} first.` });
      return false;
    }
    try {
      await navigator.clipboard.writeText(text);
    } catch (error) {
      onGraphNotice?.({ type: "error", message: `Couldn't copy to the clipboard: ${error instanceof Error ? error.message : error}` });
      return false;
    }
    rememberCopiedNodes(text, nodes);
    if (!cut) {
      onGraphNotice?.({ type: "success", message: `Copied ${countedNodes(nodes)}` });
      return true;
    }
    // Cut is one edit: Undo brings every cut node back.
    setSelectedNodeId(undefined);
    setSelectedNodeIds([]);
    setSelectedEdgeId(null);
    await onRattishMutation?.(nodes.map((node) => ({ kind: "delete_node", node: node.id })), { cut: true });
    return true;
  }

  // Paste sends the clipboard text to the backend, which renames taken names and drops
  // links to nodes this workflow doesn't have. The pasted nodes land as a group beside
  // the selection, at the view centre, or where the canvas menu was opened.
  async function pasteNodes(at = null) {
    setNodeContextMenu(null);
    setCanvasContextMenu(null);
    if (!rattishMode || editingDisabled) return;
    let text;
    try {
      text = await readClipboardText();
    } catch (error) {
      onGraphNotice?.({ type: "error", message: `Couldn't read the clipboard: ${error instanceof Error ? error.message : error}` });
      return;
    }
    if (!text.trim()) {
      onGraphNotice?.({ type: "info", message: "The clipboard is empty." });
      return;
    }
    const selectedNodes = workflowNodes.filter((node) => selectedNodeIds.includes(node.id));
    const view = viewport;
    const saved = await onRattishMutation?.([{ kind: "paste_nodes", source: text }]);
    const pastedIds = saved && !saved.error ? saved.pastedNodes ?? [] : [];
    canvasRef.current?.focus?.({ preventScroll: true });
    if (!pastedIds.length) return;
    const pasted = new Set(pastedIds);
    const edges = (saved.graph?.edges ?? []).filter((edge) => pasted.has(edge.from) && pasted.has(edge.to));
    setPendingPlacement(pastedNodePositions({
      text, pastedIds, edges, selectedNodes, viewport: view, size: canvasViewportSize(), nodeWidth, nodeHeight, at,
    }));
    setSelectedNodeIds(pastedIds);
    setSelectedNodeId(pastedIds.at(-1));
    setSelectedEdgeId(null);
  }

  graphClipboardActionsRef.current = {
    canCopy: () => rattishMode && clipboardNodes().length > 0,
    canPaste: () => rattishMode && !editingDisabled,
    "graph.copy": () => copyNodes(),
    "graph.cut": () => copyNodes({ cut: true }),
    "graph.paste": () => pasteNodes(),
  };

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
    void renameRattishNode(nodeId, trimmedLabel);
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
      // A refused rename passes { error } on so the ID field can show it.
      if (!document || document.error) {
        return document ?? null;
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
    // A right-click inside a selection keeps it, so Copy and Cut take every selected node.
    setSelectedNodeId(nodeId);
    setSelectedNodeIds((current) => (current.includes(nodeId) ? current : [nodeId]));
    setSelectedEdgeId(null);
    setCanvasContextMenu(null);
    canvasRightClickRef.current = null;
    setNodeContextMenu({
      nodeId,
      x: event.clientX,
      y: event.clientY,
    });
  }

  function handleNodePointerDown(event, nodeId) {
    if (event.button !== 0) return;
    // Stop the browser from starting a text selection while the node is dragged.
    event.preventDefault();
    window.getSelection?.()?.removeAllRanges();
    // preventDefault also stops the click from focusing the graph, so focus it here:
    // graph shortcuts such as Tab (Add node) then act on the node just selected.
    canvasRef.current?.focus?.({ preventScroll: true });
    setNodeContextMenu(null);
    setCanvasContextMenu(null);
    event.currentTarget.setPointerCapture(event.pointerId);
    nodeDragMovedRef.current = false;
    const nextSelection = selectedNodeIds.includes(nodeId) ? selectedNodeIds : [nodeId];
    nodeDragSelectionRef.current = nextSelection;
    const moving = new Set(nextSelection);
    nodeDragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origins: new Map(
        workflowNodes
          .filter((node) => moving.has(node.id))
          .map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]),
      ),
    };
    setSelectedNodeId(nodeId);
    setSelectedNodeIds(nextSelection);
    setSelectedEdgeId(null);
    setInspectorCollapsed(false);
    setDraggingNodeId(nodeId);
  }

  function handleNodePointerMove(event) {
    const drag = nodeDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (event.buttons === 0) {
      // The release happened outside the node, so finish the drag instead of following hover.
      handleNodePointerUp(event);
      return;
    }
    event.preventDefault();
    // Position from the drag origin, not per-event deltas, so missed or scaled
    // pointer events cannot make the node drift away from the cursor.
    const screenDx = event.clientX - drag.startX;
    const screenDy = event.clientY - drag.startY;
    if (Math.abs(screenDx) > 2 || Math.abs(screenDy) > 2) {
      nodeDragMovedRef.current = true;
    }
    const dx = screenDx / viewport.scale;
    const dy = screenDy / viewport.scale;

    onWorkflowChange({
      ...workflow,
      nodes: workflowNodes.map((node) => {
        const origin = drag.origins.get(node.id);
        return origin ? { ...node, x: origin.x + dx, y: origin.y + dy } : node;
      }),
    });
  }

  function handleNodePointerUp(event, nodeId) {
    if (draftEdge && nodeId) {
      event.preventDefault();
      event.stopPropagation();
      addEdge(draftEdge.from, nodeId);
      setDraftEdge(null);
      return;
    }
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    nodeDragRef.current = null;
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
        setFilePreviewPath(resolveDisplayPath(path, pathBaseDir));
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
    setCanvasContextMenu(null);
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
      event.currentTarget.focus({ preventScroll: true });
      event.currentTarget.setPointerCapture(event.pointerId);
      setPanningPointerId(event.pointerId);
      canvasRightClickRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
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
    // A right-click that didn't drag the view opens the canvas menu.
    const rightClick = canvasRightClickRef.current;
    canvasRightClickRef.current = null;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (
      rect && rattishMode && !readOnly && !invalidWorkflow && event.type === "pointerup" &&
      rightClick?.pointerId === event.pointerId &&
      Math.hypot(event.clientX - rightClick.x, event.clientY - rightClick.y) < 4
    ) {
      setCanvasContextMenu({
        x: event.clientX,
        y: event.clientY,
        at: {
          x: (event.clientX - rect.left - viewport.x) / viewport.scale,
          y: (event.clientY - rect.top - viewport.y) / viewport.scale,
        },
      });
    }
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
      addEdge(draftEdge.from, nodeId);
    }
    setDraftEdge(null);
  }

  function handleCanvasWheel(event) {
    event.preventDefault();
    if (!event.ctrlKey && !event.metaKey) {
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? event.currentTarget.clientHeight : 1;
      setViewport(current => ({ ...current, x: current.x - event.deltaX * unit, y: current.y - event.deltaY * unit }));
      return;
    }
    if (!event.deltaY) return;
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

  function addEdge(fromNodeId, toNodeId) {
    if (!fromNodeId || !toNodeId) return null;

    const nextEdgeId = uniqueEdgeId(workflowEdges, fromNodeId, toNodeId);
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
              accept={brandCompat.BUNDLE_ACCEPT}
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
              className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white text-muted transition enabled:text-red-600 enabled:hover:border-red-200 enabled:hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40"
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
                  className={`validation-pop absolute left-0 top-10 z-40 w-72 max-w-[calc(100vw-2rem)] rounded-lg border px-3 py-2 text-sm font-medium shadow-panel ${
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
              aria-expanded={rattishMode ? Boolean(nodePicker) : undefined}
              aria-haspopup={rattishMode ? "dialog" : undefined}
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
            {rattishMode ? (
              <>
                <button
                  aria-label={undoEntry ? `Undo ${undoEntry.label}` : "Nothing to undo"}
                  className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={!undoEntry}
                  title={undoEntry ? `Undo ${undoEntry.label}` : "Nothing to undo"}
                  type="button"
                  onClick={() => void onGraphUndo?.()}
                >
                  <Undo2 aria-hidden="true" size={17} />
                </button>
                <button
                  aria-label={redoEntry ? `Redo ${redoEntry.label}` : "Nothing to redo"}
                  className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white text-muted transition hover:border-slate-300 hover:bg-slate-50 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={!redoEntry}
                  title={redoEntry ? `Redo ${redoEntry.label}` : "Nothing to redo"}
                  type="button"
                  onClick={() => void onGraphRedo?.()}
                >
                  <Redo2 aria-hidden="true" size={17} />
                </button>
              </>
            ) : null}
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
                  onResumeRun={onResumeRunLog}
                  onRerunRun={onRerunRunLog}
                  onStopRun={onStopRunLog}
                />
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
          onWheel={handleCanvasWheel}
        >
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
                  {needsLinks.map((link) => {
                    const from = nodesById[link.from];
                    const to = nodesById[link.to];
                    if (!from || !to) return null;
                    const geometry = edgeGeometry(from, to, false, 0);
                    return (
                      <g key={link.id} aria-label={`${link.to} needs ${link.from}`} role="img">
                        <path
                          d={geometry.path}
                          fill="none"
                          markerEnd="url(#arrowhead)"
                          stroke="#94a3b8"
                          strokeDasharray="5 5"
                          strokeLinecap="round"
                          strokeWidth="2"
                          style={{ pointerEvents: "none" }}
                        />
                        <text
                          x={geometry.label.x}
                          y={geometry.label.y}
                          className="fill-slate-500 text-[12px] font-medium"
                          style={{ pointerEvents: "none" }}
                          textAnchor="middle"
                        >
                          needs
                        </text>
                      </g>
                    );
                  })}
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
                          {edgeLabel(edge)}
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
                    meta: nodeMetaFromOperation(node.operation, pathBaseDir),
                  }}
                  selected={selectedNodeIds.includes(node.id)}
                  status={nodeStatuses[node.id]}
                  statusSummary={runNodes?.[node.id]?.activationSummary}
                  autoStart={isRattishAutoStartNode(node)}
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
              onCopy={rattishMode ? () => void copyNodes() : null}
              onCut={rattishMode ? () => void copyNodes({ cut: true }) : null}
              onPaste={rattishMode ? () => void pasteNodes() : null}
              onDelete={() => deleteNode(nodeContextMenu.nodeId)}
              onDuplicate={() => duplicateNode(nodeContextMenu.nodeId)}
              onRename={() => renameNode(nodeContextMenu.nodeId)}
              onRunFromHere={
                rattishMode && onRunFromNode
                  ? () => {
                      const nodeId = nodeContextMenu.nodeId;
                      setNodeContextMenu(null);
                      onRunFromNode(nodeId);
                    }
                  : null
              }
              runFromDisabled={Boolean(runState?.running)}
            />
          ) : null}
          {canvasContextMenu ? (
            <CanvasContextMenu
              x={canvasContextMenu.x}
              y={canvasContextMenu.y}
              hasSelection={clipboardNodes().length > 0}
              onCopy={() => void copyNodes()}
              onCut={() => void copyNodes({ cut: true })}
              onPaste={() => void pasteNodes(canvasContextMenu.at)}
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
          {!invalidWorkflow && pendingApproval ? (
            <ApprovalDecisionOverlay
              approval={pendingApproval}
              node={pendingApprovalNode}
              onDecideApproval={onDecideApproval}
              pendingCount={pendingApprovals.length}
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
                      onConnect={(fromNodeId, toNodeId) => addEdge(fromNodeId, toNodeId)}
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
        {rattishMode && !canvasOverlay && !invalidWorkflow && !workflowNodes.length && !nodePicker ? (
          <div
            className="pointer-events-none absolute inset-0 z-30 grid place-items-center p-4"
            data-graph-empty-state="true"
          >
            <div className="pointer-events-auto max-w-xs rounded-[10px] border border-line bg-white p-4 text-center shadow-panel">
              <p className="text-sm font-semibold text-ink">This workflow has no nodes yet</p>
              <p className="mt-1 text-xs text-muted">
                Pick the first step: run code, work with files, ask AI, call the web and more.
              </p>
              <button
                className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-lg bg-indigo-600 px-3 text-xs font-semibold text-white transition hover:bg-indigo-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-40"
                data-graph-empty-add-node="true"
                disabled={editingDisabled}
                type="button"
                onClick={() => addNode()}
              >
                <Plus aria-hidden="true" size={14} />
                Add node
              </button>
            </div>
          </div>
        ) : null}
        {nodePicker && rattishMode ? (
          <div className="absolute left-1/2 top-3 z-[60] -translate-x-1/2">
            <NodePicker
              contracts={rattishDocument?.nodeContracts ?? []}
              label={
                nodePicker.afterNodeId ? `Add node after ${nodePicker.afterNodeId}` : "Add node"
              }
              placeholder={
                nodePicker.afterNodeId
                  ? `Search nodes to add after ${nodePicker.afterNodeId}`
                  : "Search nodes to add"
              }
              onClose={closeNodePicker}
              onPick={addPickedNode}
            />
          </div>
        ) : null}
        {canvasOverlay ? (
          <div className="absolute inset-0 z-40 grid place-items-center overflow-auto bg-slate-100/60 p-4 dark:bg-zinc-900/60" data-graph-error-overlay="true">
            {canvasOverlay}
          </div>
        ) : null}
        </div>
      </div>

        <ProviderAccountsContext.Provider value={providerAccounts}>
        {!canvasOverlay && !invalidWorkflow && rattishMode ? (
          <RattishInspector
            collapsed={inspectorCollapsed}
            providerProfiles={providerProfiles}
            document={rattishDocument}
            edge={selectedEdge}
            node={selectedNode}
            nodeOutput={selectedNodeOutput}
            nodeOutputsTruncated={Boolean(
              runResult?.nodeOutputsTruncated || logState?.nodeOutputsTruncated,
            )}
            nodeRun={selectedRunNode}
            runEvents={runEvents}
            width={inspectorWidth}
            onMutate={onRattishMutation}
            onRenameNode={renameRattishNode}
            onResizeStart={startInspectorResize}
            onResizeKeyDown={handleInspectorResizeKeyDown}
            onToggleCollapsed={() => setInspectorCollapsed((current) => !current)}
          />
        ) : null}
        </ProviderAccountsContext.Provider>
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
  const sourcePath = workflow.sourcePath || "";
  const message =
    workflow.validationError ||
    workflow.description ||
    "The workflow could not be parsed or checked.";
  const markdown = [
    "# Raticode workflow error",
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
              Invalid workflow
            </h3>
            <button
              className="mt-1 block max-w-full truncate text-left text-xs text-red-700/80 underline-offset-2 transition hover:text-red-800 hover:underline dark:text-red-200/70 dark:hover:text-red-200"
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
          <pre className="whitespace-pre-wrap font-mono text-xs leading-5 text-red-900 dark:text-red-200">
            {message}
          </pre>
        </div>
      </section>
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

// Rattish starts every node without needs, so the canvas marks those nodes.
function isRattishAutoStartNode(node) {
  const projection = node?.rattish;
  if (!projection || projection.status === "invalid") return false;
  return !(projection.needs ?? projection.readiness?.needs ?? []).length;
}

// Needs that no route already draws, shown as dashed readiness links.
function rattishNeedsLinks(nodes, edges) {
  const routed = new Set(edges.map((edge) => `${edge.from}\u0000${edge.to}`));
  return nodes.flatMap((node) =>
    (node.rattish?.needs ?? [])
      .filter((needed) => !routed.has(`${needed}\u0000${node.id}`))
      .map((needed) => ({ id: `${node.id}:needs:${needed}`, from: needed, to: node.id })),
  );
}

function getNodeStatuses(nodes, runResult, logText, runNodes = {}, runEvents = []) {
  const statuses = {};

  for (const event of runEvents ?? []) {
    const status = normalizeRunStatus(event?.status);
    if (event?.nodeId && event.nodeId !== "workflow" && status) {
      statuses[event.nodeId] = status;
    }
  }

  // The run summary accounts for concurrent activations of the same node.
  for (const [nodeId, nodeRun] of Object.entries(runNodes ?? {})) {
    const status = normalizeRunStatus(nodeRun?.status);
    if (status) {
      statuses[nodeId] =
        status === "success" && (nodeRun?.reused || nodeRun?.data?.reused)
          ? "reused"
          : status === "success" && nodeRun?.cached
            ? "cached"
            : status;
    }
  }

  if (runResult?.nodeOutputs) {
    for (const [nodeId, output] of Object.entries(runResult.nodeOutputs)) {
      if (statuses[nodeId]) continue;
      if (output.skipped) {
        statuses[nodeId] = "skipped";
      } else {
        statuses[nodeId] = output.success
          ? "success"
          : output.outcome === "allowed_failure" ? "allowed_failure" : "error";
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
  if (["running", "success", "error", "allowed_failure", "disconnected"].includes(status)) return status;
  if (["queued", "started", "retried"].includes(status)) return status;
  if (status === "completed") return "success";
  if (status === "failed") return "error";
  if (status === "stopped") return "stopped";
  if (status === "skipped") return "skipped";
  if (status === "reused") return "reused";
  // A result reused from the result cache: no model call, 0 tokens and $0.
  if (status === "cached") return "cached";
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
  onRerunRun,
  onResumeRun,
  onSelectRun,
  onShowLatest,
  onStopRun,
  open,
  runs,
  selectedRunId,
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
              onRerunRun={onRerunRun}
              onResumeRun={onResumeRun}
            />
          ) : null}
          <div className="workflow-scrollbar max-h-60 overflow-y-auto py-1">
            <button
              className={`flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition hover:bg-slate-50 ${
                selectedRunId ? "" : "bg-indigo-50"
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
                    selectedRunId === run.id ? "bg-indigo-50" : ""
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
                    <RunPickerLabel run={run} />
                  </button>
                  {run.status === "running" && run.stopRequested ? (
                    <StopRequestedLabel />
                  ) : run.status === "running" ? (
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

function RunHistoryActions({ onRerunRun, onResumeRun, run }) {
  const finished = run?.id && run.status !== "running";
  // Omitting onResumeRun hides resume actions for runs the backend cannot resume.
  // Runs say whether they can resume, and resume only where they stopped.
  const resume = run?.resume;
  const canResume = finished && Boolean(onResumeRun) && (!resume || resume.allowed);
  // Runs say whether their saved inputs and trigger events can start a new run.
  const rerun = onRerunRun ? run?.rerun : null;
  const rerunOff = useOffReason(rerun && !rerun.allowed ? rerun.reason || "This run can't start again with the same inputs." : "");
  const resumeOff = useOffReason(canResume ? "" : runResumeOffReason(run));
  if (!onResumeRun && !rerun) return null;
  return (
    <div className="grid gap-1 border-b border-line bg-slate-50 px-2 py-2">
      {rerun ? (
        <div>
          <button
            {...rerunOff.props}
            className={RUN_HISTORY_ACTION_CLASS}
            title={rerunOff.off ? undefined : "Start a new run of the workflow as it is now, with this run's inputs and trigger events"}
            type="button"
            onClick={rerunOff.guard(() => onRerunRun(run.id))}
          >
            <RotateCw size={13} />
            <span className="truncate">Run again with the same inputs</span>
          </button>
          {rerunOff.line ? <div className="pb-1 pl-[29px] pr-2">{rerunOff.line}</div> : null}
        </div>
      ) : null}
      {onResumeRun ? (
        <div>
          <button
            {...resumeOff.props}
            className={RUN_HISTORY_ACTION_CLASS}
            title={resumeOff.off ? undefined : "Resume this run"}
            type="button"
            onClick={resumeOff.guard(() => onResumeRun(run.id, {}))}
          >
            <Repeat2 size={13} />
            <span className="truncate">Resume</span>
          </button>
          {resumeOff.line ? <div className="pb-1 pl-[29px] pr-2">{resumeOff.line}</div> : null}
        </div>
      ) : null}
    </div>
  );
}

const RUN_HISTORY_ACTION_CLASS = "flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-xs font-medium text-ink transition hover:bg-white aria-disabled:cursor-not-allowed aria-disabled:opacity-40 aria-disabled:hover:bg-transparent";

// Why a finished run's Resume is off: the backend's reason, or that the run is still going.
function runResumeOffReason(run) {
  if (!run?.id) return "Choose a run to resume.";
  if (run.status === "running") return "This run is still going. Stop it before resuming.";
  if (run.resume && !run.resume.allowed) return run.resume.reason || "This run can't resume.";
  return "";
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
  statusSummary,
  autoStart = false,
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
      className={`absolute w-[220px] select-none rounded-lg border bg-white p-3 shadow-node transition ${readOnly ? "cursor-default" : "cursor-grab active:cursor-grabbing"} ${borderClass}`}
      style={{ left: node.x, top: node.y, zIndex }}
      title={title}
      data-selected={selected ? "true" : undefined}
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
      onPointerCancel={(event) => {
        if (!readOnly) onPointerUp(event);
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
        <div className="flex min-w-0 items-center gap-1.5">
          <h3 className="truncate text-sm font-semibold leading-5">{node.label}</h3>
          {autoStart ? (
            <span
              className="flex shrink-0 items-center gap-0.5 rounded border border-emerald-200 bg-emerald-50 px-1 py-0.5 text-[9px] font-semibold uppercase leading-3 text-emerald-800"
              title="Runs when the workflow starts because it has no needs"
            >
              <Play size={8} aria-hidden="true" />
              start
            </span>
          ) : null}
        </div>
        <p className="mt-1 truncate text-xs leading-5 text-muted">{node.meta}</p>
        {statusSummary ? (
          <p className="truncate text-[11px] font-medium leading-4 text-slate-600" title={statusSummary}>
            {statusSummary}
          </p>
        ) : null}
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
          {node.rattish?.type || node.type}
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

const MENU_ITEM_CLASS = "flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50 hover:text-ink disabled:cursor-not-allowed disabled:text-slate-400 disabled:hover:bg-transparent";

function ClipboardMenuItems({ hasSelection = true, onCopy, onCut, onPaste }) {
  return (
    <>
      <button className={MENU_ITEM_CLASS} disabled={!hasSelection} type="button" onClick={onCut}>
        <Scissors size={15} />
        Cut
      </button>
      <button className={MENU_ITEM_CLASS} disabled={!hasSelection} type="button" onClick={onCopy}>
        <ClipboardCopy size={15} />
        Copy
      </button>
      <button className={MENU_ITEM_CLASS} type="button" onClick={onPaste}>
        <ClipboardPaste size={15} />
        Paste
      </button>
    </>
  );
}

// The menu a right-click on empty canvas opens. Paste puts the nodes where it was opened.
function CanvasContextMenu({ hasSelection, onCopy, onCut, onPaste, x, y }) {
  return (
    <div
      aria-label="Graph menu"
      className="fixed z-[90] w-48 rounded-lg border border-line bg-white p-1 text-sm shadow-panel"
      role="menu"
      style={{ left: x, top: y }}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.preventDefault()}
    >
      <ClipboardMenuItems hasSelection={hasSelection} onCopy={onCopy} onCut={onCut} onPaste={onPaste} />
    </div>
  );
}

function NodeContextMenu({ onCopy = null, onCut = null, onDelete, onDuplicate, onPaste = null, onRename, onRunFromHere = null, runFromDisabled = false, x, y }) {
  return (
    <div
      className="fixed z-[90] w-48 rounded-lg border border-line bg-white p-1 text-sm shadow-panel"
      style={{ left: x, top: y }}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.preventDefault()}
    >
      {onRunFromHere ? (
        <>
          <button
            className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50 hover:text-ink disabled:cursor-not-allowed disabled:text-slate-400 disabled:hover:bg-transparent"
            type="button"
            disabled={runFromDisabled}
            title={
              runFromDisabled
                ? "Wait for the current run to finish"
                : "Reuse the last run's results for the unchanged steps before this one, then run this step and everything after it"
            }
            onClick={onRunFromHere}
          >
            <Play size={15} />
            Run from here
          </button>
          <div className="my-1 border-t border-line" />
        </>
      ) : null}
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
      {onCopy ? (
        <>
          <div className="my-1 border-t border-line" />
          <ClipboardMenuItems onCopy={onCopy} onCut={onCut} onPaste={onPaste} />
        </>
      ) : null}
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
      overlayClassName="fixed inset-0 z-[95] grid place-items-center ui-scrim px-4"
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
              className="mt-1 h-10 w-full rounded-lg border border-line px-3 text-sm outline-none transition focus:border-brand"
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
            className="inline-flex h-9 items-center gap-2 rounded-lg bg-brand px-3 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
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
        {status === "retried" ? "retrying" : "running"}
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
    allowed_failure: "border-orange-200 bg-orange-50 text-orange-700",
    stopped: "border-amber-200 bg-amber-50 text-amber-700",
    skipped: "border-slate-200 bg-slate-50 text-slate-500",
    reused: "border-teal-200 bg-teal-50 text-teal-700",
    cached: "border-sky-200 bg-sky-50 text-sky-700",
    disconnected: "border-slate-200 bg-slate-50 text-slate-500",
  }[status];

  const label = status === "allowed_failure" ? "allowed failure" : status;
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[9px] font-semibold uppercase ${className}`} title={label}>
      {label}
    </span>
  );
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

function uniqueEdgeId(edges, fromNodeId, toNodeId) {
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

function formatApprovalRequestedAt(requestedAt) {
  if (!requestedAt) return "";
  const date = new Date(requestedAt);
  if (Number.isNaN(date.getTime())) return String(requestedAt);
  return dateTime(date);
}

export function ApprovalDecisionOverlay({ approval, node, onDecideApproval, pendingCount = 1 }) {
  const [notes, setNotes] = useState("");
  const [reply, setReply] = useState("");
  const [approver, setApprover] = useState(approval?.approvers?.[0] || "ui");
  const [dismissed, setDismissed] = useState(false);
  const identity = approvalIdentity(approval);
  const defaultApprover = approval?.approvers?.[0] || "ui";
  useEffect(() => {
    setNotes("");
    setReply("");
    setApprover(defaultApprover);
    setDismissed(false);
  }, [identity, defaultApprover]);
  if (!approval || dismissed) return null;
  const replyMissing = approvalReplyMode(approval) === "required" && !reply.trim();
  const answer = (decision, choice = null) =>
    onDecideApproval?.(approval, decision, notes, approver, { choice, reply });
  const nodeLabel = node?.label || node?.id || approval.nodeId;
  const requestedAt = formatApprovalRequestedAt(approval.requestedAt);
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
              <p className="truncate text-xs text-slate-600" title={approval.runId}>
                Run <span className="font-mono">{approval.runId}</span>
                {requestedAt ? ` · requested ${requestedAt}` : ""}
                {pendingCount > 1 ? ` · ${pendingCount} approvals waiting, oldest first` : ""}
              </p>
            </div>
          </div>
        </div>
        <div className="space-y-4 px-5 py-5">
          {approval.subject ? (
            <p className="break-words text-sm text-slate-600">
              <span className="font-medium text-ink">Subject:</span> {approval.subject}
            </p>
          ) : null}
          <div className="max-h-[180px] overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-slate-50 px-3 py-3 text-sm leading-6 text-ink">
            {approval.message}
          </div>
          <div className="grid gap-3 sm:grid-cols-[minmax(0,180px)_1fr]">
            <label className="space-y-1 text-xs font-medium text-slate-600">
              <span>Approver</span>
              <input
                className="h-10 w-full rounded-md border border-line bg-white px-3 text-sm text-ink outline-none transition placeholder:text-muted/70 focus:border-brand focus:ring-2 focus:ring-brand/15"
                placeholder="Approver identity"
                value={approver}
                onChange={(event) => setApprover(event.target.value)}
              />
            </label>
            <label className="space-y-1 text-xs font-medium text-slate-600">
              <span>Notes</span>
              <textarea
                className="min-h-[82px] w-full resize-y rounded-md border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition placeholder:text-muted/70 focus:border-brand focus:ring-2 focus:ring-brand/15"
                placeholder="Decision notes"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </label>
          </div>
          <ApprovalReplyField approval={approval} value={reply} onChange={setReply} />
          {approval.options?.length ? (
            <ApprovalOptionButtons
              approval={approval}
              disabled={replyMissing}
              onChoose={(option) => answer("approved", option)}
            />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <button
                className="flex h-14 items-center justify-center gap-2 rounded-md bg-emerald-600 px-4 text-base font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={replyMissing}
                title="Approve pending approval"
                type="button"
                onClick={() => answer("approved")}
              >
                <Check size={22} />
                Approve
              </button>
              <button
                className="flex h-14 items-center justify-center gap-2 rounded-md border border-red-300 bg-red-50 px-4 text-base font-semibold text-red-700 shadow-sm transition hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={replyMissing}
                title="Reject pending approval"
                type="button"
                onClick={() => answer("rejected")}
              >
                <X size={22} />
                Reject
              </button>
            </div>
          )}
          {replyMissing ? (
            <p className="text-xs text-slate-600">This workflow needs a reply before you answer.</p>
          ) : null}
        </div>
    </Dialog>
  );
}
