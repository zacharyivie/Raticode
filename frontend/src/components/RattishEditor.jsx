import { installRemActions } from "../lib/editorRem.js";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { AlertTriangle, Check, FileCode2, Loader2, RefreshCw, Save } from "lucide-react";
import { apiUrl } from "../lib/api.js";
import { diagnosticToMarker, diagnosticsToMarkers } from "../lib/rattishRanges.js";

const ANALYSIS_DELAY_MS = 300;
const editorSessions = new Map();

const RattishEditor = forwardRef(function RattishEditor({
  active,
  initialDocument = null,
  saveRequest = 0,
  showFileChrome = true,
  theme,
  workflow,
  onDocumentStateChange,
}, ref) {
  const containerRef = useRef(null);
  const editorRef = useRef(null);
  const initialDocumentRef = useRef(initialDocument);
  const monacoRef = useRef(null);
  const modelRef = useRef(null);
  const themeRef = useRef(theme);
  const documentRef = useRef(null);
  const stateRef = useRef(null);
  const analysisTimerRef = useRef(null);
  const requestRef = useRef(0);
  const suppressChangeRef = useRef(false);
  const savedSourceRef = useRef("");
  const handledSaveRequestRef = useRef(saveRequest);
  const discardOnUnmountRef = useRef(false);
  const [state, setState] = useState({
    document: null,
    error: "",
    loading: true,
    saving: false,
  });

  const publishState = useCallback(
    (nextState) => {
      stateRef.current = nextState;
      setState(nextState);
      onDocumentStateChange?.(nextState);
    },
    [onDocumentStateChange],
  );

  const analyzeSource = useCallback(
    async (source) => {
      const requestId = ++requestRef.current;
      try {
        const response = await fetch(
          apiUrl(`/workflows/${encodeURIComponent(workflow.id)}/document/analyze`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source }),
          },
        );
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `Analysis returned ${response.status}`);
        if (requestId !== requestRef.current) return;
        documentRef.current = payload.document;
        const nextState = { document: payload.document, error: "", loading: false, saving: false };
        publishState(nextState);
        updateMarkers(monacoRef.current, modelRef.current, payload.document);
      } catch (error) {
        if (requestId !== requestRef.current) return;
        publishState({
          document: documentRef.current,
          error: error instanceof Error ? error.message : "Unable to analyze Rattish source",
          loading: false,
          saving: false,
        });
      }
    },
    [publishState, workflow.id],
  );

  const saveSource = useCallback(async () => {
    const currentDocument = documentRef.current;
    const model = modelRef.current;
    if (!currentDocument || !model || stateRef.current?.saving) return null;
    const source = model.getValue();
    const requestId = ++requestRef.current;
    publishState({ ...stateRef.current, error: "", saving: true });
    try {
      const response = await fetch(
        apiUrl(`/workflows/${encodeURIComponent(workflow.id)}/document/save`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source,
            expectedRevision: currentDocument.savedRevision,
          }),
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        if (response.status === 409) {
          throw new Error("workflow.rattish changed on disk. Reload before saving your changes.");
        }
        throw new Error(payload.error || `Save returned ${response.status}`);
      }
      if (requestId !== requestRef.current) return;
      documentRef.current = payload.document;
      savedSourceRef.current = source;
      const nextState = { document: payload.document, error: "", loading: false, saving: false };
      publishState(nextState);
      updateMarkers(monacoRef.current, modelRef.current, payload.document);
      return payload.document;
    } catch (error) {
      if (requestId !== requestRef.current) return;
      publishState({
        document: documentRef.current,
        error: error instanceof Error ? error.message : "Unable to save workflow.rattish",
        loading: false,
        saving: false,
      });
      return null;
    }
  }, [publishState, workflow.id]);

  const loadDocument = useCallback(async () => {
    const requestId = ++requestRef.current;
    publishState({
      document: initialDocumentRef.current,
      error: "",
      loading: !initialDocumentRef.current,
      saving: false,
    });
    try {
      const readTextFile = window.goferDesktop?.textFiles?.read;
      if (readTextFile && workflow.sourcePath) {
        try {
          const payload = await readTextFile(workflow.sourcePath);
          if (requestId !== requestRef.current) return;
          const source = payload?.content ?? "";
          savedSourceRef.current = source;
          suppressChangeRef.current = true;
          modelRef.current?.setValue(source);
          suppressChangeRef.current = false;
          const provisionalDocument = {
            ...(initialDocumentRef.current ?? {}),
            diagnostics: [],
            dirty: false,
            preflight: { diagnostics: [] },
            source,
          };
          documentRef.current = provisionalDocument;
          publishState({
            document: provisionalDocument,
            error: "",
            loading: false,
            saving: false,
          });
          void analyzeSource(source);
          return;
        } catch {
          // The workflow API remains a fallback if the desktop file grant expired.
        }
      }
      const response = await fetch(
        apiUrl(`/workflows/${encodeURIComponent(workflow.id)}/document`),
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `Editor API returned ${response.status}`);
      if (requestId !== requestRef.current) return;
      const session = editorSessions.get(workflow.id);
      const restoredSource = session?.source ?? payload.document.source;
      const restoredDocument =
        restoredSource === payload.document.source
          ? payload.document
          : {
              ...payload.document,
              diagnostics: [],
              dirty: true,
              preflight: null,
              source: restoredSource,
            };
      documentRef.current = restoredDocument;
      savedSourceRef.current = payload.document.source;
      const model = modelRef.current;
      if (model) {
        suppressChangeRef.current = true;
        model.setValue(restoredSource);
        suppressChangeRef.current = false;
        updateMarkers(monacoRef.current, model, restoredDocument);
        if (session?.viewState) editorRef.current?.restoreViewState(session.viewState);
      }
      publishState({ document: restoredDocument, error: "", loading: false, saving: false });
      if (restoredSource !== payload.document.source) void analyzeSource(restoredSource);
    } catch (error) {
      if (requestId !== requestRef.current) return;
      publishState({
        document: null,
        error: error instanceof Error ? error.message : "Unable to open workflow.rattish",
        loading: false,
        saving: false,
      });
    }
  }, [analyzeSource, publishState, workflow.id, workflow.sourcePath]);

  const acceptExternalDocument = useCallback(
    (nextDocument) => {
      if (!nextDocument) return;
      const model = modelRef.current;
      const previousSource = model?.getValue() ?? documentRef.current?.source ?? "";
      documentRef.current = nextDocument;
      savedSourceRef.current = nextDocument.source;
      if (model && previousSource !== nextDocument.source) {
        const editor = editorRef.current;
        suppressChangeRef.current = true;
        editor?.pushUndoStop();
        editor?.executeEdits("rattish.graph", [
          {
            range: model.getFullModelRange(),
            text: nextDocument.source,
            forceMoveMarkers: true,
          },
        ]);
        editor?.pushUndoStop();
        suppressChangeRef.current = false;
      }
      updateMarkers(monacoRef.current, model, nextDocument);
      publishState({ document: nextDocument, error: "", loading: false, saving: false });
    },
    [publishState],
  );

  useEffect(() => {
    let disposed = false;
    let contentListener;
    let resizeObserver;
    documentRef.current = null;
    publishState({ document: null, error: "", loading: true, saving: false });
    import("../lib/monaco.js").then(({ loadRattishMonaco }) => {
      if (disposed || !containerRef.current) return;
      const monaco = loadRattishMonaco();
      monacoRef.current = monaco;
      const model = monaco.editor.createModel(
        "",
        "rattish",
        monaco.Uri.parse(`raticode://workflow/${encodeURIComponent(workflow.id)}/workflow.rattish`),
      );
      modelRef.current = model;
      const editor = monaco.editor.create(containerRef.current, {
        automaticLayout: false,
        cursorBlinking: "smooth",
        fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
        fontSize: 13,
        folding: true,
        glyphMargin: true,
        lineHeight: 20,
        minimap: { enabled: true, maxColumn: 80, renderCharacters: false },
        model,
        padding: { top: 12, bottom: 20 },
        renderValidationDecorations: "on",
        scrollBeyondLastLine: false,
        smoothScrolling: true,
        tabSize: 2,
        theme: themeRef.current === "dark" ? "gofer-rattish-dark" : "gofer-rattish-light",
        wordWrap: "off",
      });
      editorRef.current = editor;
      const remActions = installRemActions(editor, () => ({ path: documentRef.current?.sourcePath || workflow?.sourcePath, projectRoot: workflow?.projectRoot }));
      editor.onDidDispose?.(() => remActions.dispose());
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => saveSource());
      contentListener = model.onDidChangeContent(() => {
        if (suppressChangeRef.current) return;
        const source = model.getValue();
        const currentDocument = documentRef.current;
        if (currentDocument) {
          const provisionalDocument = editorDocumentAfterChange(
            currentDocument,
            source,
            savedSourceRef.current,
          );
          documentRef.current = provisionalDocument;
          publishState({
            document: provisionalDocument,
            error: "",
            loading: false,
            saving: false,
          });
        }
        window.clearTimeout(analysisTimerRef.current);
        analysisTimerRef.current = window.setTimeout(
          () => analyzeSource(source),
          ANALYSIS_DELAY_MS,
        );
      });
      resizeObserver = new ResizeObserver(() => editor.layout());
      resizeObserver.observe(containerRef.current);
      loadDocument();
    });
    return () => {
      disposed = true;
      window.clearTimeout(analysisTimerRef.current);
      if (discardOnUnmountRef.current) {
        editorSessions.delete(workflow.id);
      } else if (documentRef.current && modelRef.current) {
        editorSessions.set(workflow.id, {
          source: modelRef.current.getValue(),
          viewState: editorRef.current?.saveViewState() ?? null,
        });
      }
      contentListener?.dispose();
      resizeObserver?.disconnect();
      editorRef.current?.dispose();
      modelRef.current?.dispose();
      editorRef.current = null;
      modelRef.current = null;
      monacoRef.current = null;
    };
  }, [analyzeSource, loadDocument, publishState, saveSource, workflow.id, workflow?.projectRoot, workflow?.sourcePath]);

  useEffect(() => {
    themeRef.current = theme;
    const monaco = monacoRef.current;
    if (!monaco) return;
    monaco.editor.setTheme(theme === "dark" ? "gofer-rattish-dark" : "gofer-rattish-light");
  }, [theme]);

  useEffect(() => {
    if (active) window.requestAnimationFrame(() => editorRef.current?.layout());
  }, [active]);

  useEffect(() => {
    if (saveRequest === handledSaveRequestRef.current) return;
    handledSaveRequestRef.current = saveRequest;
    void saveSource();
  }, [saveRequest, saveSource]);

  useImperativeHandle(
    ref,
    () => ({
      acceptDocument: acceptExternalDocument,
      discard: () => {
        discardOnUnmountRef.current = true;
        editorSessions.delete(workflow.id);
      },
      revealDiagnostic: (diagnostic) => revealDiagnostic(
        monacoRef.current,
        editorRef.current,
        modelRef.current,
        documentRef.current,
        diagnostic,
      ),
      save: saveSource,
    }),
    [acceptExternalDocument, saveSource, workflow.id],
  );

  const diagnostics = state.document?.diagnostics ?? [];
  const problemDiagnostics = [
    ...diagnostics,
    ...(state.document?.preflight?.diagnostics ?? []),
  ];
  const warningCount = problemDiagnostics.filter((item) => item.severity === "warning").length;
  const errorCount = problemDiagnostics.filter((item) => item.severity === "error").length;
  const dirty = Boolean(state.document?.dirty);

  return (
    <section className="rattish-editor-shell flex min-h-0 flex-1 flex-col bg-white" aria-label="Rattish code editor">
      {showFileChrome ? <><div className="flex h-9 shrink-0 items-center border-b border-line bg-slate-50">
        <div className="relative flex h-full items-center gap-2 border-r border-line bg-white px-3 text-xs font-semibold">
          <FileCode2 aria-hidden="true" className="shrink-0 text-brand" size={14} />
          workflow.rattish
          {dirty ? <span aria-label="Unsaved changes" className="h-1.5 w-1.5 rounded-full bg-brand" /> : null}
          <span className="absolute inset-x-0 top-0 h-0.5 bg-brand" />
        </div>
      </div>
      <div className="flex h-7 shrink-0 items-center gap-1 border-b border-line bg-white px-3 text-[11px] text-muted">
        <span className="truncate">{workflow.projectName || workflow.projectRoot}</span>
        <span aria-hidden="true">/</span>
        <span className="truncate">.raticode/{workflow.id}</span>
        <span aria-hidden="true">/</span>
        <strong className="font-semibold text-ink">workflow.rattish</strong>
      </div></> : null}
      <div className="relative min-h-0 flex-1">
        <div ref={containerRef} className="absolute inset-0" />
        {state.loading ? (
          <div className="absolute inset-0 z-10 grid place-items-center bg-white/90 text-sm text-muted dark:bg-[#19191b]/90">
            <span className="flex items-center gap-2"><Loader2 className="animate-spin" size={16} />Opening workflow.rattish</span>
          </div>
        ) : null}
        {state.error && !state.document ? (
          <div className="absolute inset-0 z-10 grid place-items-center bg-white p-8 dark:bg-[#19191b]">
            <div className="max-w-md text-center">
              <AlertTriangle className="mx-auto mb-3 text-amber-600" size={24} />
              <p className="text-sm font-semibold">Could not open workflow.rattish</p>
              <p className="mt-1 text-xs leading-5 text-muted">{state.error}</p>
              <button className="mt-4 inline-flex h-8 items-center gap-2 rounded-md border border-line bg-white px-3 text-xs font-semibold hover:bg-slate-50" type="button" onClick={loadDocument}>
                <RefreshCw size={14} />Retry
              </button>
            </div>
          </div>
        ) : null}
      </div>
      <div className="flex min-h-8 shrink-0 items-center gap-3 border-t border-line bg-white px-3 text-[11px] text-muted">
        <button className="inline-flex items-center gap-1.5 font-medium text-ink hover:text-brand disabled:opacity-50" disabled={!state.document || state.saving || !dirty} type="button" onClick={saveSource}>
          {state.saving ? <Loader2 className="animate-spin" size={13} /> : dirty ? <Save size={13} /> : <Check size={13} />}
          {state.saving ? "Saving" : dirty ? "Save" : "Saved"}
        </button>
        {errorCount ? <span className="text-red-700">{errorCount} {errorCount === 1 ? "error" : "errors"}</span> : null}
        {warningCount ? <span className="text-amber-700">{warningCount} {warningCount === 1 ? "warning" : "warnings"}</span> : null}
        <span className="ml-auto">Rattish</span>
        <span>Spaces: 2</span>
      </div>
      {state.error && state.document ? (
        <div className="border-t border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800" role="alert">{state.error}</div>
      ) : null}
    </section>
  );
});

export default RattishEditor;

export function editorDocumentAfterChange(document, source, savedSource) {
  return {
    ...document,
    dirty: source !== savedSource,
    source,
  };
}

function updateMarkers(monaco, model, document) {
  if (!monaco || !model || !document) return;
  const diagnostics = [
    ...(document.diagnostics ?? []),
    ...(document.preflight?.diagnostics ?? []),
  ];
  monaco.editor.setModelMarkers(
    model,
    "rattish",
    diagnosticsToMarkers(monaco, model, document.source, diagnostics),
  );
}

function revealDiagnostic(monaco, editor, model, document, diagnostic) {
  if (!monaco || !editor || !model || !document) return;
  const marker = diagnosticToMarker(monaco, model, document.source, diagnostic);
  const range = {
    endColumn: marker.endColumn,
    endLineNumber: marker.endLineNumber,
    startColumn: marker.startColumn,
    startLineNumber: marker.startLineNumber,
  };
  editor.setSelection(range);
  editor.revealRangeInCenter(range);
  editor.focus();
}
