import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Clipboard,
  ExternalLink,
  Moon,
  RefreshCw,
  Sun,
  Terminal,
  X,
} from "lucide-react";
import { useRouteError } from "react-router-dom";
import packageMetadata from "../../package.json";
import RaticodeMark from "./RaticodeMark.jsx";
import { newIssueUrl } from "../lib/issueReport.js";

const THEME_STORAGE_KEY = "gofer-ui-theme";
const SUPPORT_EMAIL = packageMetadata.bugs.email;

function currentLocation() {
  return typeof window !== "undefined" && window.location?.href
    ? window.location.href
    : "Unavailable";
}

function currentUserAgent() {
  return typeof navigator !== "undefined" && navigator.userAgent
    ? navigator.userAgent
    : "Unavailable";
}

function errorSummary(error) {
  if (error instanceof Error) {
    const name = error.name || "Error";
    const message = error.message || "The application stopped unexpectedly.";
    return message.startsWith(`${name}:`) ? message : `${name}: ${message}`;
  }
  if (typeof error === "string" && error.trim()) return `Error: ${error.trim()}`;
  if (error && typeof error === "object") {
    const name = error.name || (error.status ? `Error ${error.status}` : "Error");
    const message = error.message || error.statusText;
    if (message) return `${name}: ${message}`;
  }
  return "Error: The application stopped unexpectedly.";
}

export function createCrashDetails(error, componentStack = "", overrides = {}) {
  const summary = errorSummary(error);
  return {
    componentStack: componentStack.trim(),
    stack: error instanceof Error && error.stack ? error.stack : summary,
    summary,
    timestamp: new Date().toISOString(),
    url: currentLocation(),
    userAgent: currentUserAgent(),
    ...overrides,
  };
}

export function formatCrashReport(crash) {
  const sections = [
    crash.stack || crash.summary,
    crash.componentStack ? `React component stack:\n${crash.componentStack}` : "",
    crash.view ? `View: ${crash.view}` : "",
    `URL: ${crash.url}`,
    `Time: ${crash.timestamp}`,
    `Raticode v${packageMetadata.version}`,
    `User agent: ${crash.userAgent}`,
  ];
  return sections.filter(Boolean).join("\n\n");
}

// A public issue shouldn't name the user's account or folders: the packaged app loads from
// file:///C:/Users/<name>/AppData/..., and error messages often hold a project path.
const APP_FILE = /file:\/\/\/?[^\s"'()]*?\/app\.asar\//gi;
const HOME_FOLDER = /(?:[A-Za-z]:[\\/]Users|\/Users|\/home)[\\/][^\\/\s"'()]+/g;
const withoutLocalPaths = text => String(text).replace(APP_FILE, "app.asar/").replace(HOME_FOLDER, "~");

export function issueUrlForCrash(crash) {
  const title = withoutLocalPaths(`Crash: ${crash.summary}`).slice(0, 180);
  const report = withoutLocalPaths(formatCrashReport(crash)).slice(0, 6000);
  const body = [
    "### What happened",
    "",
    "<!-- What were you doing immediately before the crash? -->",
    "",
    "### Error details",
    "",
    "```text",
    report,
    "```",
  ].join("\n");
  return newIssueUrl({ title, body });
}

function initialTheme() {
  let saved = null;
  try {
    saved = typeof window !== "undefined" ? window.localStorage?.getItem(THEME_STORAGE_KEY) : null;
  } catch {
    // Storage can be disabled in hardened browser contexts. The crash page must still render.
  }
  if (saved === "dark" || saved === "light") return saved;
  if (typeof document !== "undefined" && document.documentElement?.classList?.contains("dark")) return "dark";
  if (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches) return "dark";
  return "light";
}

async function copyText(text) {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  if (typeof document === "undefined") throw new Error("Clipboard access is unavailable.");
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("Clipboard access was rejected.");
}

export class AppCrashBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { crash: null };
  }

  static getDerivedStateFromError(error) {
    return { crash: createCrashDetails(error) };
  }

  componentDidCatch(_error, errorInfo) {
    void window.goferDesktop?.developer?.log?.(`${_error?.stack || _error}\n${errorInfo?.componentStack || ""}`).catch(() => {});
    if (!errorInfo?.componentStack) return;
    this.setState(({ crash }) => ({
      crash: crash ? { ...crash, componentStack: errorInfo.componentStack.trim() } : crash,
    }));
  }

  render() {
    if (this.state.crash) return <AppCrashPage crash={this.state.crash} />;
    return this.props.children;
  }
}

// Catches a render error inside one view (Posts, the editor, Rem…) and shows a
// fallback in that view's place, so the rail, the other panes and the rest of the
// window keep working. AppCrashBoundary in main.jsx stays the last resort for
// errors outside every view. Reload this view remounts the view's tree; a change
// of resetKey (the view being hidden and shown again) does the same.
export class ViewCrashBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { attempt: 0, crash: null, resetKey: props.resetKey };
    this.reload = this.reload.bind(this);
  }

  static getDerivedStateFromError(error) {
    return { crash: createCrashDetails(error) };
  }

  static getDerivedStateFromProps(props, state) {
    if (Object.is(props.resetKey, state.resetKey)) return null;
    if (!state.crash) return { resetKey: props.resetKey };
    return { attempt: state.attempt + 1, crash: null, resetKey: props.resetKey };
  }

  componentDidCatch(error, errorInfo) {
    const componentStack = errorInfo?.componentStack || "";
    void window.goferDesktop?.developer?.log?.(`${this.props.name} view: ${error?.stack || error}\n${componentStack}`)?.catch?.(() => {});
    this.setState(({ crash }) => ({
      crash: crash ? { ...crash, componentStack: componentStack.trim() } : crash,
    }));
  }

  reload() {
    this.setState(({ attempt }) => ({ attempt: attempt + 1, crash: null }));
  }

  render() {
    const { crash, attempt } = this.state;
    if (crash) {
      return <ViewCrashFallback className={this.props.className} crash={{ ...crash, view: this.props.name }} name={this.props.name} onReload={this.reload} style={this.props.style} />;
    }
    return <React.Fragment key={attempt}>{this.props.children}</React.Fragment>;
  }
}

function ViewCrashFallback({ className = "", crash, name, onReload, style }) {
  const [copyState, setCopyState] = useState("idle");
  const copyTimerRef = useRef(null);
  const report = useMemo(() => formatCrashReport(crash), [crash]);
  const issueUrl = useMemo(() => issueUrlForCrash(crash), [crash]);

  useEffect(() => () => window.clearTimeout(copyTimerRef.current), []);

  const handleCopy = async () => {
    try {
      await copyText(report);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
    window.clearTimeout(copyTimerRef.current);
    copyTimerRef.current = window.setTimeout(() => setCopyState("idle"), 1800);
  };

  const titleId = `view-crash-${String(name).toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  const secondary = "inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-white px-3 text-[12px] font-semibold text-ink transition-colors hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand";
  return (
    <section aria-labelledby={titleId} className={`flex min-h-0 min-w-0 flex-1 items-center justify-center overflow-y-auto bg-canvas px-5 py-8 text-ink ${className}`} data-view-crash={name} role="alert" style={style}>
      <div className="w-full max-w-[460px] text-center">
        <span aria-hidden="true" className="mx-auto mb-3 grid h-9 w-9 place-items-center rounded-full border border-red-200 bg-red-50 text-red-700 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-300">
          <AlertTriangle size={17} />
        </span>
        <h2 className="mb-1.5 text-[17px] font-semibold" id={titleId}>{name} stopped working</h2>
        <p className="mx-auto mb-3 max-w-[44ch] text-[13px] leading-5 text-muted">
          The rest of Raticode is still running. Reloading this view usually clears it. If it happens again, copy the details and report it.
        </p>
        <div className="mx-auto mb-4 flex max-w-full items-center gap-2 overflow-x-auto whitespace-nowrap rounded-md border border-line bg-slate-50 px-3 py-1.5 text-left font-mono text-[11px] text-muted">
          <AlertTriangle aria-hidden="true" className="shrink-0 text-red-600 dark:text-red-300" size={12} />
          <span>{crash.summary}</span>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-2">
          <button
            className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-brand px-3 text-[12px] font-semibold text-white transition-colors hover:bg-indigo-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
            onClick={onReload}
            type="button"
          >
            <RefreshCw aria-hidden="true" size={13} />
            Reload this view
          </button>
          <button className={secondary} onClick={handleCopy} type="button">
            {copyState === "copied" ? <Check aria-hidden="true" size={13} /> : <Clipboard aria-hidden="true" size={13} />}
            {copyState === "copied" ? "Copied" : copyState === "failed" ? "Copy failed" : "Copy details"}
          </button>
          <a className={secondary} href={issueUrl} rel="noreferrer" target="_blank">
            <ExternalLink aria-hidden="true" size={13} />
            Report
          </a>
        </div>
      </div>
      <div aria-live="polite" className="sr-only" role="status">
        {copyState === "copied" ? "Copied error details" : null}
        {copyState === "failed" ? "Could not copy error details" : null}
      </div>
    </section>
  );
}

export function RouteCrashPage() {
  const routeError = useRouteError();
  const crash = useMemo(() => createCrashDetails(routeError), [routeError]);
  return <AppCrashPage crash={crash} />;
}

export function AppCrashPage({ crash }) {
  const [theme, setTheme] = useState(initialTheme);
  const [copyState, setCopyState] = useState("idle");
  const [reloading, setReloading] = useState(false);
  const copyTimerRef = useRef(null);
  const report = useMemo(() => formatCrashReport(crash), [crash]);
  const issueUrl = useMemo(() => issueUrlForCrash(crash), [crash]);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    try {
      window.localStorage?.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Keep the selected theme for this session when storage is unavailable.
    }
  }, [theme]);

  useEffect(() => () => window.clearTimeout(copyTimerRef.current), []);

  const handleCopy = async () => {
    try {
      await copyText(report);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
    window.clearTimeout(copyTimerRef.current);
    copyTimerRef.current = window.setTimeout(() => setCopyState("idle"), 1800);
  };

  const handleReload = () => {
    setReloading(true);
    window.location.reload();
  };

  return (
    <div className="flex min-h-screen flex-col bg-canvas text-ink">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-line px-4 sm:px-[18px]">
        <div className="flex items-center gap-2.5">
          <RaticodeMark className="h-[26px] w-[26px]" />
          <span className="text-[13px] font-semibold">Raticode</span>
        </div>
        <button
          aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          className="grid h-8 w-8 place-items-center rounded-lg border border-line text-muted transition-colors hover:bg-slate-100 hover:text-ink"
          onClick={() => setTheme((value) => (value === "dark" ? "light" : "dark"))}
          title="Toggle theme"
          type="button"
        >
          {theme === "dark" ? <Sun aria-hidden="true" size={16} /> : <Moon aria-hidden="true" size={16} />}
        </button>
      </header>

      <main className="flex flex-1 items-center justify-center overflow-y-auto px-5 py-8 sm:py-12">
        <section aria-labelledby="crash-title" className="w-full max-w-[600px] text-center" role="alert">
          <div aria-hidden="true" className="mb-7 flex items-center justify-center sm:mb-8">
            <CrashNode icon={Terminal} label="render" status="Done" type="react-component" />
            <div className="relative w-8 shrink-0 border-t-2 border-dashed border-red-500 sm:w-12">
              <span className="absolute left-1/2 top-1/2 grid h-[22px] w-[22px] -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-red-200 bg-red-50 text-red-700 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-300">
                <X size={13} strokeWidth={2} />
              </span>
            </div>
            <CrashNode crashed icon={AlertTriangle} label="App" status="Crashed" type={crash.summary.split(":", 1)[0]} />
          </div>

          <span className="inline-flex h-[22px] items-center gap-1.5 rounded-full border border-red-200 bg-red-50 px-2.5 text-[10px] font-bold uppercase tracking-[0.04em] text-red-700 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-300">
            <AlertTriangle aria-hidden="true" size={12} />
            Crash report
          </span>
          <h1 className="mb-2.5 mt-3.5 text-[28px] font-bold tracking-[-0.02em]" id="crash-title">
            Something snapped.
          </h1>
          <p className="mx-auto mb-4 max-w-[48ch] text-[15px] leading-6 text-muted">
            Raticode hit an unexpected error and could not keep the studio running. Reloading usually clears it. If it happens again, copy the details and open an issue, or email{" "}
            <a className="font-semibold text-ink underline" href={`mailto:${SUPPORT_EMAIL}`} rel="noreferrer" target="_blank">{SUPPORT_EMAIL}</a>.
          </p>

          <div className="mx-auto mb-6 inline-flex max-w-full items-center gap-2 overflow-x-auto whitespace-nowrap rounded-md border border-line bg-slate-50 px-3 py-1.5 font-mono text-[11px] text-muted">
            <AlertTriangle aria-hidden="true" className="shrink-0 text-red-600 dark:text-red-300" size={13} />
            <span>{crash.summary}</span>
          </div>

          <div className="mb-7 flex flex-wrap items-center justify-center gap-2">
            <button
              className="inline-flex h-9 items-center gap-2 rounded-lg bg-brand px-3.5 text-[13px] font-semibold text-white transition-colors hover:bg-indigo-700 disabled:cursor-wait disabled:opacity-70"
              disabled={reloading}
              onClick={handleReload}
              type="button"
            >
              <RefreshCw aria-hidden="true" className={reloading ? "animate-spin" : ""} size={14} />
              {reloading ? "Reloading" : "Reload Raticode"}
            </button>
            <button
              className="inline-flex h-9 items-center gap-2 rounded-lg border border-line bg-white px-3.5 text-[13px] font-semibold text-ink transition-colors hover:bg-slate-50"
              onClick={handleCopy}
              type="button"
            >
              {copyState === "copied" ? <Check aria-hidden="true" size={14} /> : <Clipboard aria-hidden="true" size={14} />}
              {copyState === "copied" ? "Copied" : copyState === "failed" ? "Copy failed" : "Copy error details"}
            </button>
            <a
              className="inline-flex h-9 items-center gap-2 rounded-lg border border-line bg-white px-3.5 text-[13px] font-semibold text-ink transition-colors hover:bg-slate-50"
              href={issueUrl}
              rel="noreferrer"
              target="_blank"
            >
              <ExternalLink aria-hidden="true" size={14} />
              Open an issue
            </a>
          </div>

          <details className="group mb-5 overflow-hidden rounded-xl border border-line bg-white text-left">
            <summary className="flex h-10 cursor-pointer list-none items-center gap-2 px-3.5 text-[13px] font-semibold text-muted transition-colors hover:bg-slate-50 hover:text-ink [&::-webkit-details-marker]:hidden">
              <Terminal aria-hidden="true" size={13} />
              Technical details
              <ChevronDown aria-hidden="true" className="ml-auto transition-transform group-open:rotate-180" size={13} />
            </summary>
            <div className="border-t border-line bg-slate-50">
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words px-4 py-3.5 font-mono text-[11px] leading-[1.7] text-ink">{report}</pre>
              <div className="flex flex-col gap-1 border-t border-line px-4 py-2 font-mono text-[10px] text-muted sm:flex-row sm:items-center sm:justify-between">
                <span className="truncate" title={crash.url}>{crash.url}</span>
                <span className="shrink-0">Thrown {crash.timestamp}</span>
              </div>
            </div>
          </details>

          <footer className="mx-auto max-w-[48ch] text-xs leading-5 text-muted">
            Raticode is an open source project. If reloading does not help, an issue report is the fastest way to get this fixed.
            <span className="mt-2 block font-mono text-[10px]">v{packageMetadata.version}</span>
          </footer>
        </section>
      </main>

      <div aria-live="polite" className="sr-only" role="status">
        {copyState === "copied" ? "Copied error details" : null}
        {copyState === "failed" ? "Could not copy error details" : null}
      </div>
    </div>
  );
}

function CrashNode({ crashed = false, icon: Icon, label, status, type }) {
  return (
    <div className={`w-[min(9.25rem,38vw)] rounded-xl border border-line border-l-[3px] bg-white p-2.5 text-left shadow-sm ${crashed ? "border-l-red-500" : "border-l-slate-400 dark:border-l-slate-500"}`}>
      <div className="mb-1.5 flex items-center justify-between gap-1.5">
        <span className={`grid h-[22px] w-[22px] shrink-0 place-items-center rounded-md ${crashed ? "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300" : "bg-slate-100 text-muted"}`}>
          <Icon size={13} strokeWidth={1.8} />
        </span>
        <span className={`rounded-full px-1.5 py-0.5 text-[9px] font-bold ${crashed ? "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300" : "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"}`}>
          {status}
        </span>
      </div>
      <div className="truncate text-[12px] font-semibold text-ink">{label}</div>
      <div className="truncate font-mono text-[10px] text-muted">{type}</div>
    </div>
  );
}
