/* global getComputedStyle, __dirname, clearTimeout, console, document, process, setTimeout, window */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { URL } = require('node:url');
const {
  app,
  BrowserWindow
} = require('electron');
const distRoot = path.resolve(__dirname, '../dist');
const artifactRoot = process.env.GOFER_BROWSER_ARTIFACTS || path.join(require('node:os').tmpdir(), 'taskurotta-workflow-tabs');
fs.mkdirSync(artifactRoot, {
  recursive: true
});
let server, windowRef;
const rendererErrors = [],
  requests = [],
  checks = [];
const timeout = setTimeout(() => fail(new Error('Tabs smoke timed out')), 90000);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-dev-shm-usage');
process.on('uncaughtException', fail);
process.on('unhandledRejection', fail);
const workflows = [makeWorkflow('atlas-a', 'Atlas review', 'atlas'), makeWorkflow('atlas-b', 'Atlas release', 'atlas'), makeWorkflow('beacon-a', 'Beacon check', 'beacon')];
const sources = new Map(workflows.map(w => [w.id, `Rattish: 1\n\nWorkflow:\n  name: ${w.name}\n\nNode prepare:\n  type: bash-command\n  command: echo ${w.id}\n`]));
const saved = new Map(sources);
function makeWorkflow(id, name, project) {
  return {
    id,
    name,
    sourceFormat: 'rattish',
    sourcePath: `/workspace/${project}/.raticode/${id}/workflow.rattish`,
    workflowRoot: `/workspace/${project}/.raticode/${id}`,
    projectRoot: `/workspace/${project}`,
    projectName: project,
    nodes: [],
    edges: [],
    agents: {},
    inputs: {},
    status: 'Ready'
  };
}
function documentFor(id) {
  const w = workflows.find(w => w.id === id);
  const d = rattishDocumentFixture();
  return {
    ...d,
    runnable: id !== 'beacon-a',
    compilation: id === 'beacon-a' ? { ...d.compilation, state: 'invalid' } : d.compilation,
    diagnostics: id === 'beacon-a' ? [{ severity: 'error', message: 'Unknown node type: missing', code: 'unknown-node-type' }] : d.diagnostics,
    source: sources.get(id),
    savedRevision: saved.get(id),
    sourceRevision: sources.get(id),
    dirty: sources.get(id) !== saved.get(id),
    workflowId: id,
    workflow: {
      name: w.name
    },
    projectRoot: w.projectRoot,
    sourcePath: w.sourcePath,
    graph: {
      ...d.graph,
      nodes: d.graph.nodes.map(n => ({
        ...n,
        label: w.name + ' node'
      }))
    }
  };
}
async function startServer() {
  server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname.startsWith('/api/')) {
      let raw = '';
      for await (const chunk of req) raw += chunk;
      const body = raw ? JSON.parse(raw) : {};
      requests.push({
        method: req.method,
        path: url.pathname,
        body
      });
      res.setHeader('Access-Control-Allow-Origin', '*');
      if (/\/(run|resume|stop)(\/|$)/.test(url.pathname)) {
        res.writeHead(403, {
          'Content-Type': 'application/json'
        });
        res.end(JSON.stringify({
          error: 'Execution forbidden in smoke test'
        }));
        return;
      }
      if (url.pathname === '/api/chat/attachments') return json(res, {
        attachments: body.files.map(file => ({ id: 'source', name: file.name, type: file.type, storageName: 'source.txt' }))
      });
      if (url.pathname === '/api/chat/stream') {
        res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
        res.end(JSON.stringify({ type: 'final', message: { body: 'Workflow repair received.' } }) + '\n');
        return;
      }
      if (url.pathname === '/api/workflows') return json(res, {
        dataDir: '/workspace',
        workflows,
        promptAgentIds: []
      });
      if (url.pathname === '/api/projects/open') return json(res, {
        workflows: workflows.filter(w => w.projectRoot === body.projectRoot)
      });
      const match = url.pathname.match(/^\/api\/workflows\/([^/]+)\/document(?:\/(.*))?$/);
      if (match) {
        const id = decodeURIComponent(match[1]);
        if (!sources.has(id)) return json(res, {});
        if (body.source !== undefined) sources.set(id, body.source);
        if (match[2] === 'save') saved.set(id, sources.get(id));
        return json(res, {
          document: documentFor(id)
        });
      }
      return routeApi(url.pathname, res);
    }
    const requested = path.resolve(distRoot, url.pathname === '/' ? 'index.html' : '.' + url.pathname);
    if (!requested.startsWith(distRoot + path.sep)) {
      res.writeHead(404).end();
      return;
    }
    fs.readFile(requested, (err, data) => {
      if (err) {
        res.writeHead(404).end();
        return;
      }
      res.writeHead(200, {
        'Content-Type': {
          '.html': 'text/html',
          '.css': 'text/css',
          '.js': 'text/javascript',
          '.svg': 'image/svg+xml'
        }[path.extname(requested)] || 'application/octet-stream'
      });
      res.end(data);
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return `http://127.0.0.1:${server.address().port}/`;
}
async function screenshot(name) {
  await wait(250);
  const capture = await windowRef.webContents.capturePage();
  assert.equal(capture.isEmpty(), false, 'Screenshot must contain painted pixels');
  fs.writeFileSync(path.join(artifactRoot, name + '.png'), capture.toPNG());
}
async function check(name, action) {
  await action();
  checks.push(name);
  console.log('PASS ' + name);
}
async function clickActivity(label) {
  await evaluate(label => {
    const el = [...document.querySelectorAll('[aria-label="Project sidebar views"] [role="tab"]')].find(el => el.getAttribute('aria-label') === label);
    if (!el) throw new Error('Missing activity ' + label);
    el.click();
  }, label);
}
async function openWorkflow(name) {
  await clickActivity('Workflows');
  await waitFor(() => evaluate(name => [...document.querySelectorAll('[role="button"]')].some(el => el.textContent.includes(name)), name));
  await evaluate(name => {
    const el = [...document.querySelectorAll('[role="button"]')].find(el => el.textContent.includes(name));
    if (!el) throw new Error('Missing workflow ' + name);
    el.click();
  }, name);
  await waitFor(() => evaluate(name => [...document.querySelectorAll('[aria-label="Editor tabs"] [role="tab"]')].some(el => el.textContent.includes(name) && el.getAttribute('aria-selected') === 'true'), name), 25, 'workflow tab ' + name);
}
async function activateTab(name) {
  await evaluate(name => {
    const el = [...document.querySelectorAll('[aria-label="Editor tabs"] [role="tab"]')].find(el => el.textContent.includes(name));
    if (!el) throw new Error('Missing tab ' + name);
    el.click();
  }, name);
}
async function selectedTab() {
  return evaluate(() => document.querySelector('[aria-label="Editor tabs"] [aria-selected="true"]')?.textContent.trim());
}
async function run() {
  const base = await startServer();
  windowRef = new BrowserWindow({
    width: 1440,
    height: 900,
    show: true,
    webPreferences: {
      partition: 'tabs-smoke-' + Date.now(),
      contextIsolation: false,
      nodeIntegration: false,
      sandbox: false,
      preload: path.join(__dirname, 'workflow-tabs-preload.cjs')
    }
  });
  windowRef.webContents.on('console-message', details => {
    if (details.level === 'error') rendererErrors.push(details.message);
  });
  await windowRef.loadURL(base);
  windowRef.show();
  windowRef.focus();
  windowRef.webContents.focus();
  await waitFor(() => evaluate(() => Boolean(document.querySelector('[aria-label="Project sidebar views"]'))));
  await waitFor(() => evaluate(() => window.innerWidth >= 1000));
  await check('Graph/Code toggle removed and five activities exposed', async () => {
    assert.equal(await evaluate(() => Boolean(document.querySelector('[aria-label="Studio view"]'))), false);
    assert.deepEqual(await evaluate(() => [...document.querySelectorAll('[aria-label="Project sidebar views"] [role="tab"]')].map(el => el.getAttribute('aria-label')).sort()), ['File explorer', 'Search', 'Source control', 'Swarms', 'Workflows'].sort());
  });
  await check('Two workflow graphs open as persistent editor tabs', async () => {
    await openWorkflow('Atlas review');
    await openWorkflow('Atlas release');
    await activateTab('Atlas review');
    assert.match(await selectedTab(), /Atlas review/);
    assert.equal(await evaluate(() => [...document.querySelectorAll('[aria-label="Editor tabs"] [role="tab"]')].filter(el => /Atlas review|Atlas release/.test(el.textContent)).length), 2);
  });
  await check('Browsing activity preserves active graph and rail position', async () => {
    const railTop = await evaluate(() => document.querySelector('[aria-label="Project sidebar views"]').getBoundingClientRect().top);
    assert.equal(await evaluate(() => {
      const panel = document.querySelector('.studio-sidebar #sidebar-panel-workflows');
      const button = panel.querySelector('[title="New Workflow"]');
      const picker = document.querySelector('button[aria-label="Recent projects"]');
      return button.getBoundingClientRect().left > picker.getBoundingClientRect().left
        && button.getBoundingClientRect().top > picker.getBoundingClientRect().bottom;
    }), true);
    await clickActivity('File explorer');
    assert.match(await selectedTab(), /Atlas review/);
    assert.equal(await evaluate(() => document.querySelector('[aria-label="Project sidebar views"]').getBoundingClientRect().top), railTop);
    await clickActivity('Search');
    assert.match(await selectedTab(), /Atlas review/);
    await clickActivity('Source control');
    assert.match(await selectedTab(), /Atlas review/);
    await clickActivity('Workflows');
  });
  await screenshot('tabs-light');
  await check('Project switch retains active graph and tabs', async () => {
    await evaluate(() => document.querySelector('button[aria-label="Recent projects"]').click());
    assert.equal(await evaluate(() => {
      const menu = document.querySelector('[role="menu"][aria-label="Recent projects"]').getBoundingClientRect();
      const sidebar = document.querySelector('.studio-sidebar').getBoundingClientRect();
      return menu.left >= sidebar.left && menu.right <= sidebar.right && menu.bottom <= sidebar.bottom;
    }), true);
    await screenshot('sidebar-project-menu');
    await evaluate(() => {
      const button = [...document.querySelectorAll('[role="menu"] button')].find(el => el.title === '/workspace/beacon');
      if (!button) throw new Error('Missing Beacon recent project');
      button.click();
    });
    await waitFor(() => evaluate(() => [...document.querySelectorAll('[role="button"]')].some(el => el.textContent.includes('Beacon check'))));
    assert.match(await selectedTab(), /Atlas review/);
    await openWorkflow('Beacon check');
    assert.equal(await evaluate(() => document.querySelectorAll('[aria-label="Editor tabs"] [role="tab"]').length), 3);
  });
  await check('Invalid graph warning opens its source; dirty last-view close asks', async () => {
    await waitFor(() => evaluate(() => [...document.querySelectorAll('[role="alert"]')].some(el => el.textContent.includes('Unable to render the graph'))));
    assert.equal(await evaluate(() => {
      const overlay = document.querySelector('[data-graph-error-overlay]');
      const region = overlay.closest('[data-graph-canvas-region]');
      const graph = region.querySelector('[data-graph-visualization]');
      const inert = graph.closest('[inert]');
      const a = overlay.getBoundingClientRect();
      const b = graph.getBoundingClientRect();
      const warning = overlay.querySelector('[role="alert"]').getBoundingClientRect();
      const toolbar = overlay.closest('section').querySelector('[data-toolbar="graph-editor"]');
      return Boolean(inert) && getComputedStyle(inert).filter === 'grayscale(1)'
        && Math.abs(a.top - b.top) < 1 && Math.abs(a.bottom - b.bottom) < 1
        && Math.abs(warning.left + warning.width / 2 - (a.left + a.width / 2)) < 1
        && Math.abs(warning.top + warning.height / 2 - (a.top + a.height / 2)) < 1
        && !toolbar.closest('[inert]') && toolbar.getBoundingClientRect().bottom <= a.top;
    }), true, 'Only the graph should be dimmed and inert, with a centered overlay');
    await evaluate(() => document.querySelector('[data-graph-error-overlay]').closest('section').querySelector('[data-toolbar="graph-editor"] [title="More graph actions"]').click());
    await evaluate(() => document.querySelector('[data-graph-error-overlay]').closest('section').querySelector('[data-toolbar="graph-editor"] [title="Select workflow run"]').click());
    await waitFor(() => evaluate(() => document.body.textContent.includes('No workflow runs yet.')));
    await evaluate(() => document.querySelector('[data-graph-error-overlay]').closest('section').querySelector('[data-toolbar="graph-editor"] [title="More graph actions"]').click());
    await evaluate(() => [...document.querySelectorAll('[aria-label="Bottom panel views"] button')].find(el => el.textContent.includes('Run Timeline')).click());
    await waitFor(() => evaluate(() => [...document.querySelectorAll('[role="tab"]')].some(el => el.textContent.includes('Run Timeline') && el.getAttribute('aria-selected') === 'true')));
    assert.equal(await evaluate(() => {
      const panel = document.querySelector('[aria-label="Bottom panel"]');
      const overlay = document.querySelector('[data-graph-error-overlay]');
      const button = panel.querySelector('[role="tab"][aria-selected="true"]');
      const rect = button.getBoundingClientRect();
      return !panel.closest('[inert]') && overlay.getBoundingClientRect().bottom <= panel.getBoundingClientRect().top
        && button.contains(document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2));
    }), true, 'Run timeline stays outside the overlay and accepts pointer input');
    // Keep Beacon's editor open while browsing a different project.
    await evaluate(() => document.querySelector('button[aria-label="Recent projects"]').click());
    await evaluate(() => [...document.querySelectorAll('[role="menu"] button')].find(el => el.title === '/workspace/atlas').click());
    await waitFor(() => evaluate(() => [...document.querySelectorAll('[role="button"]')].some(el => el.textContent.includes('Atlas review'))));
    await evaluate(() => [...document.querySelectorAll('[data-graph-error-overlay] button')].find(el => el.textContent === 'Ask Rem to fix').click());
    await waitFor(() => requests.some(request => request.path === '/api/chat/stream'));
    const repairRequests = requests.filter(request => request.path === '/api/chat/stream');
    assert.equal(repairRequests.length, 1);
    assert.equal(repairRequests[0].body.workflow.projectRoot, '/workspace/beacon');
    assert.match(JSON.stringify(repairRequests[0].body.messages), /Fix the invalid workflow definition in \/workspace\/beacon/);
    await waitFor(() => evaluate(() => Boolean(document.querySelector('[aria-label="Scoped to beacon. Change project scope"]:not(:disabled)'))));
    await evaluate(() => document.querySelector('[aria-label="Scoped to beacon. Change project scope"]').click());
    await waitFor(() => evaluate(() => Boolean(document.querySelector('[aria-label="Rem project scope"] button[title="/worktrees/beacon-fix"]'))));
    await evaluate(() => document.querySelector('[aria-label="Rem project scope"] button[title="/worktrees/beacon-fix"]').click());
    await waitFor(() => evaluate(() => Boolean(document.querySelector('[aria-label="Scoped to beacon-fix. Change project scope"]'))));
    await screenshot('invalid-graph-warning');
    await evaluate(() => document.querySelector('button[title="Dark mode"]').click());
    await screenshot('invalid-graph-dark');
    windowRef.setSize(960, 700);
    await wait(250);
    await screenshot('invalid-graph-compact');
    assert.equal(await evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    windowRef.setSize(1440, 1000);
    await evaluate(() => document.querySelector('button[title="Light mode"]').click());
    await evaluate(() => document.querySelector('[aria-label="Collapse bottom panel"]')?.click());
    await evaluate(() => {
      const warning = [...document.querySelectorAll('[role="alert"]')].find(el => el.textContent.includes('Unable to render the graph'));
      const button = [...warning.querySelectorAll('button')].find(el => el.textContent === 'Open workflow');
      if (!button) throw new Error('Missing source action in graph warning');
      button.click();
    });
    await waitFor(() => evaluate(() => Boolean(document.querySelector('.monaco-editor textarea'))
      && document.querySelector('.monaco-editor')?.textContent.includes('beacon-a')));
    windowRef.focus();
    windowRef.webContents.focus();
    await waitFor(() => evaluate(() => document.hasFocus()));
    await evaluate(() => document.querySelector('.monaco-editor textarea').focus());
    windowRef.webContents.sendInputEvent({
      type: 'keyDown',
      keyCode: 'End',
      modifiers: ['control']
    });
    windowRef.webContents.sendInputEvent({
      type: 'keyUp',
      keyCode: 'End',
      modifiers: ['control']
    });
    await windowRef.webContents.insertText('\n# smoke draft\n');
    await waitFor(() => evaluate(() => Boolean(document.querySelector('[aria-label="Editor tabs"] [aria-label="Unsaved changes"]'))));
    await activateTab('Beacon check');
    await evaluate(() => document.querySelector('button[aria-label="Close Beacon check"]').click());
    await activateTab('workflow.rattish');
    await evaluate(() => document.querySelector('button[aria-label="Close workflow.rattish"]').click());
    await waitFor(() => evaluate(() => Boolean(document.querySelector('[role="dialog"]') || window.__lastConfirm)));
    assert.equal(requests.some(r => /\/(run|resume|stop)(\/|$)/.test(r.path)), false);
    await screenshot('dirty-close');
    await evaluate(() => {
      const b = [...document.querySelectorAll('[role="dialog"] button')].find(el => el.textContent.trim() === 'Cancel');
      b?.click();
    });
  });
  await check('Code keyboard input does not mutate hidden graph', async () => {
    const before = requests.filter(r => /\/document\/(mutate|metadata)/.test(r.path)).length;
    await evaluate(() => document.querySelector('.monaco-editor textarea')?.focus());
    windowRef.webContents.sendInputEvent({
      type: 'keyDown',
      keyCode: 'Delete'
    });
    windowRef.webContents.sendInputEvent({
      type: 'keyUp',
      keyCode: 'Delete'
    });
    await wait(200);
    assert.equal(requests.filter(r => /\/document\/(mutate|metadata)/.test(r.path)).length, before);
  });
  await activateTab('Atlas review');
  await check('Dark and compact viewport stay usable', async () => {
    await evaluate(() => document.querySelector('button[title="Dark mode"]').click());
    await screenshot('tabs-dark');
    windowRef.setSize(960, 700);
    await wait(250);
    await screenshot('tabs-compact');
    assert.equal(await evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    assert.ok(await evaluate(() => [...document.querySelectorAll('[data-graph-visualization]')].some(el => el.getBoundingClientRect().width >= 440)), 'Compact layout must collapse a side pane before graph becomes narrower than 440px');
  });
  assert.equal(requests.some(r => /\/(run|resume|stop)(\/|$)/.test(r.path)), false);
  fs.writeFileSync(path.join(artifactRoot, 'result.json'), JSON.stringify({
    checks,
    requests,
    rendererErrors
  }, null, 2));
  assert.deepEqual(rendererErrors, []);
  clearTimeout(timeout);
  await cleanup(0);
}
app.whenReady().then(run).catch(fail);
function routeApi(pathname, response) {
  if (pathname === "/api/provider/capabilities") {
    return json(response, { providers: [{
      id: "codex", displayName: "Codex", available: true, discoveryStatus: "ready",
      defaultModel: "gpt-5.6-sol",
      models: [{ id: "gpt-5.6-sol", displayName: "GPT-5.6-Sol", defaultEffort: "medium", efforts: [{ id: "medium", displayName: "Medium" }] }],
    }] });
  }
  if (pathname.endsWith("/logs")) return json(response, { runs: [] });
  if (pathname.endsWith("/approvals")) return json(response, { approvals: [] });
  if (pathname === "/api/workflow-templates") return json(response, { templates: [] });
  if (pathname === "/api/doctor") return json(response, { errors: [], warnings: [] });
  return json(response, {});
}

function rattishDocumentFixture() {
  const source = "Rattish: 1\n\nWorkflow:\n  name: Rattish editor\n\nNode prepare:\n  type: bash-command\n  command: echo ready\n";
  return {
    compilation: {
      fingerprint: "sha256:test",
      irVersion: 1,
      lastValidFingerprint: "sha256:test",
      state: "valid"
    },
    diagnostics: [],
    dirty: false,
    graph: {
      edges: [],
      nodes: [{
        configuration: {
          command: "echo ready"
        },
        diagnostics: [],
        execution: {
          allow_fail: false,
          max_concurrency: 1,
          retry_count: 0,
          retry_delay_ms: 0,
          timeout_ms: null
        },
        id: "prepare",
        label: "Prepare",
        status: "valid",
        type: "bash-command"
      }]
    },
    invalidRegions: [],
    metadata: {
      metadataVersion: 1,
      canvas: {
        nodes: {},
        pan: {
          x: 0,
          y: 0
        },
        zoom: 1
      },
      editor: {
        foldedDeclarations: []
      }
    },
    metadataRevision: "sha256:metadata",
    preflight: {
      diagnostics: [],
      ready: true
    },
    projectRoot: "/workspace/gofer-flow",
    runnable: true,
    savedRevision: "sha256:source",
    source,
    sourcePath: "/workspace/gofer-flow/.raticode/rattish-editor/workflow.rattish",
    sourceRevision: "sha256:source",
    workflow: {
      name: "Rattish editor"
    },
    workflowId: "rattish-editor"
  };
}
function json(response, payload) {
  response.writeHead(200, {
    "Content-Type": "application/json"
  });
  response.end(JSON.stringify(payload));
}
async function evaluate(callback, argument) {
  const result = await windowRef.webContents.executeJavaScript(`(async () => {
    try {
      return { value: await (${callback.toString()})(${JSON.stringify(argument) ?? "undefined"}) };
    } catch (error) {
      return { error: String(error?.stack || error) };
    }
  })()`);
  if (result?.error) throw new Error(result.error);
  return result?.value;
}
async function waitFor(predicate, delay = 25, description = "browser condition") {
  const deadline = Date.now() + 7000;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await wait(delay);
  }
  const pageState = await evaluate(() => ({
    focusedElement: document.activeElement?.outerHTML.slice(0, 1000),
    tabs: [...document.querySelectorAll("[role='tab']")].map(tab => ({
      label: tab.textContent.trim(),
      selected: tab.getAttribute("aria-selected"),
      disabled: tab.disabled
    })),
    text: document.body.textContent.slice(-10000),
    editor: [...document.querySelectorAll(".monaco-editor")].map(editor => ({
      width: editor.clientWidth,
      height: editor.clientHeight,
      text: editor.textContent
    })),
    bridgeCalls: window.__goferBridgeCalls
  }));
  throw new Error(`Timed out waiting for ${description}.\nPredicate: ${predicate}\nPage: ${JSON.stringify(pageState, null, 2)}`);
}
function wait(milliseconds) {
  return new Promise(resolve => setTimeout(resolve, milliseconds));
}
async function cleanup(exitCode) {
  if (windowRef && !windowRef.isDestroyed()) windowRef.destroy();
  if (server) await new Promise(resolve => server.close(resolve));
  app.exit(exitCode);
}
async function fail(error) {
  clearTimeout(timeout);
  console.error(error);
  if (windowRef && !windowRef.isDestroyed()) await screenshot("failure").catch(() => {});
  fs.writeFileSync(path.join(artifactRoot, "result.json"), JSON.stringify({
    checks,
    requests,
    rendererErrors,
    error: String(error.stack || error)
  }, null, 2));
  await cleanup(1);
}
