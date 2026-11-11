// The real desktop entry point and a real backend fill Settings > Developer > Application log.
//
// Needs a built `dist/` and a backend executable. Set GOFER_BACKEND_PATH to test a
// particular backend; by default the repository's `.venv/bin/Raticode` is used.
// Set RATICODE_APP_LOG_SCREENSHOT to a .png path to save the Developer tab.
require('./background-display.cjs').runInBackgroundDisplay();
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { app, clipboard } = require('electron');

const backend = process.env.GOFER_BACKEND_PATH || path.resolve(__dirname, '../../../.venv/bin/Raticode');
if (!fs.existsSync(backend)) {
  console.log(`Skipping the application log test: no backend at ${backend}.`);
  process.exit(0);
}
if (!fs.existsSync(path.resolve(__dirname, '../../dist/index.html'))) {
  console.error('Build the frontend first (npm run build).');
  process.exit(1);
}
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'raticode-app-log-test-'));
app.setPath('userData', profile);
fs.mkdirSync(path.join(profile, 'logs'));
app.setPath('logs', path.join(profile, 'logs'));
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-dev-shm-usage');
// Earlier-launch entries from yesterday, at every visible level, carry a date as well as a time.
const yesterday = new Date();
yesterday.setDate(yesterday.getDate() - 1);
yesterday.setHours(17, 14, 5, 157);
// Four refusals in a row, as a full phone used to cause, show as one row with a count.
const REFUSED = 'Device action workspace_exchange refused: workspace_history_quota';
const seeded = [['info', 'backend', 'Backend ready at http://127.0.0.1:41000 after 2.1 s'], ...Array(4).fill(['info', 'backend', REFUSED, 'gofer.ui.server']),
  ['warn', 'renderer', 'The ScriptProcessorNode is deprecated. Use AudioWorkletNode instead.'], ['error', 'backend', 'Backend stopped unexpectedly (exit code 1)']]
  .map(([level, source, message, component], index) => JSON.stringify({ time: new Date(yesterday.getTime() + index * 1000).toISOString(), level, source, message, ...(component ? { component } : {}) })).join('\n') + '\n';
fs.writeFileSync(path.join(profile, 'logs', 'app.jsonl'), seeded, { mode: 0o600 });
const pad = (value) => String(value).padStart(2, '0');
const yesterdayLabel = `${yesterday.getFullYear()}-${pad(yesterday.getMonth() + 1)}-${pad(yesterday.getDate())}`;
process.env.GOFER_ELECTRON_MODE = 'production';
process.env.GOFER_DATA_DIR = path.join(profile, 'data');
process.env.GOFER_BACKEND_PATH = backend;
for (const name of ['GOFER_API_BASE_URL', 'VITE_API_BASE_URL', 'GOFER_LOG_LEVEL', 'RATICODE_DEVICE_EXPERIMENTAL_NETWORK', 'RATICODE_DEVICE_HOST']) delete process.env[name];
const timer = setTimeout(() => { console.error('Application log test timed out'); app.exit(1); }, 150000);
const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));

async function until(read, check, label) {
  for (let i = 0; i < 300; i++) {
    const value = await read();
    if (check(value)) return value;
    await wait(100);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

async function run() {
  const created = new Promise(resolve => app.once('browser-window-created', (_event, window) => resolve(window)));
  await import('../main.js');
  const window = await created;
  const page = (script) => window.webContents.executeJavaScript(script);
  await until(() => page('Boolean(window.goferDesktop?.developer?.entries)'), Boolean, 'the desktop bridge');
  const read = () => page('window.goferDesktop.developer.entries({ after: 0, history: true })');
  const log = await until(read, value => value.entries.some(entry => entry.message.startsWith('Backend ready at') && entry.seq), 'the backend to be ready');
  const find = (prefix) => log.entries.find(entry => entry.message.startsWith(prefix));
  assert.equal(find('Starting Raticode ').source, 'desktop');
  const backendStart = find('Starting Raticode backend (Python');
  assert.ok(backendStart, 'the Python backend reports through GOFER_LOG');
  assert.deepEqual([backendStart.level, backendStart.source, backendStart.component], ['info', 'backend', 'gofer.ui.server']);
  assert.ok(log.entries.every(entry => !entry.message.includes('GOFER_LOG')), 'structured lines are decoded, not shown raw');
  assert.ok(log.history, 'the first read includes earlier launches');

  // A page error in the main window becomes an error entry.
  await page('console.error("Application log test: page error")');
  const after = await until(() => page(`window.goferDesktop.developer.entries({ after: ${log.cursor} })`),
    value => value.entries.some(entry => entry.message.includes('page error')), 'the page error');
  const pageError = after.entries.find(entry => entry.message.includes('page error'));
  assert.deepEqual([pageError.level, pageError.source], ['error', 'renderer']);

  // Settings > Developer shows the live log.
  await until(() => page('Boolean(document.querySelector(\'[aria-label="Open settings"]\'))'), Boolean, 'the settings button');
  await page('document.querySelector(\'[aria-label="Open settings"]\').click()');
  await until(() => page('[...document.querySelectorAll("button")].some(b => b.textContent.trim() === "Developer")'), Boolean, 'the settings categories');
  await page('[...document.querySelectorAll("button")].find(b => b.textContent.trim() === "Developer").click()');
  const rows = await until(() => page('[...document.querySelectorAll(".app-log-message")].map(node => node.textContent)'),
    value => value.some(text => text.startsWith('Starting Raticode backend')), 'log rows in Settings > Developer');
  assert.ok(rows.some(text => text.includes('page error')));

  // Layout: no row's time (or date) runs into its level, whatever the level or date.
  const earlierRows = `[...document.querySelectorAll('.app-log-time')].filter(node => new Date(node.dateTime).toDateString() !== new Date().toDateString()).length`;
  await until(() => page(earlierRows), count => count >= 3, 'earlier-launch rows from yesterday');
  const layout = await page(`[...document.querySelectorAll('.app-log-line')].map(line => {
    const time = line.querySelector('.app-log-time');
    const level = line.querySelector('.app-log-level');
    const source = line.querySelector('.app-log-source');
    const textRight = (node) => { const range = document.createRange(); range.selectNodeContents(node); return Math.max(...[...range.getClientRects()].map(rect => rect.right)); };
    return {
      time: time.textContent, earlier: new Date(time.dateTime).toDateString() !== new Date().toDateString(),
      day: line.querySelector('.app-log-day')?.textContent || '', level: level.textContent,
      timeOverlap: Math.round(textRight(time) - level.getBoundingClientRect().left),
      levelOverlap: Math.round(textRight(level) - source.getBoundingClientRect().left),
    };
  })`);
  const overlapping = layout.filter(row => row.timeOverlap > 0 || row.levelOverlap > 0);
  assert.deepEqual(overlapping, [], `rows whose text overlaps the next column: ${JSON.stringify(overlapping)}`);
  const dated = layout.filter(row => row.earlier);
  assert.deepEqual([...new Set(dated.map(row => row.level))].sort(), ['Error', 'Info', 'Warning'], 'dated rows at every level were measured');
  assert.ok(dated.every(row => row.day === yesterdayLabel), 'earlier rows show their date on its own line');
  assert.ok(layout.filter(row => !row.earlier).every(row => !row.day), "today's rows show only the time");
  console.log(`Layout: ${layout.length} rows, ${dated.length} with a date, none overlapping.`);
  if (process.env.RATICODE_APP_LOG_SCREENSHOT) {
    await wait(400);
    const image = await window.webContents.capturePage();
    fs.writeFileSync(process.env.RATICODE_APP_LOG_SCREENSHOT, image.toPNG());
  }
  // Real mouse input from here on.
  window.webContents.focus();
  const send = (event) => window.webContents.sendInputEvent(event);

  // The repeated refusals share one row with their count; clicking the count shows each one.
  const repeat = `(() => { const node = [...document.querySelectorAll('.app-log-repeat')].find(button => button.closest('[role="option"]').textContent.includes(${JSON.stringify(REFUSED)})); if (!node) return null;
    node.closest('[role="option"]').scrollIntoView({ block: 'center' }); const box = node.getBoundingClientRect();
    return { count: node.textContent.replace(/\\D/g, ''), expanded: node.getAttribute('aria-expanded'), point: [Math.round(box.left + box.width / 2), Math.round(box.top + box.height / 2)],
      rows: [...document.querySelectorAll('[role="option"]')].filter(row => row.textContent.includes(${JSON.stringify(REFUSED)})).length,
      times: [...document.querySelectorAll('.app-log-row-member time')].map(time => time.dateTime) }; })()`;
  const grouped = await until(() => page(repeat), Boolean, 'the repeated refusals');
  assert.deepEqual([grouped.count, grouped.expanded, grouped.rows], ['4', 'false', 1], 'four refusals show as one row with a count');
  await wait(200);
  const point = (await page(repeat)).point;
  send({ type: 'mouseDown', x: point[0], y: point[1], button: 'left', clickCount: 1 }); await wait(60);
  send({ type: 'mouseUp', x: point[0], y: point[1], button: 'left', clickCount: 1 });
  const opened = await until(() => page(repeat), (state) => state.expanded === 'true', 'the group to open');
  assert.equal(opened.rows, 5, 'the group and each of its four entries');
  assert.equal(new Set(opened.times).size, 4, 'each entry keeps its own time');
  assert.equal(await page('document.querySelectorAll(\'[role="option"][aria-selected="true"]\').length'), 0, 'opening a group does not select it');
  const memberLayout = await page(`[...document.querySelectorAll('.app-log-row-member .app-log-line, .app-log-repeats > [role="option"] > .app-log-line')].map(line => {
    const time = line.querySelector('.app-log-time'); const level = line.querySelector('.app-log-level');
    const range = document.createRange(); range.selectNodeContents(time);
    return Math.round(Math.max(...[...range.getClientRects()].map(rect => rect.right)) - level.getBoundingClientRect().left); })`);
  assert.ok(memberLayout.length === 5 && memberLayout.every(overlap => overlap <= 0), `group rows overlap: ${memberLayout}`);
  if (process.env.RATICODE_APP_LOG_SCREENSHOT) {
    await wait(400);
    const image = await window.webContents.capturePage();
    fs.writeFileSync(process.env.RATICODE_APP_LOG_SCREENSHOT.replace(/\.png$/, '-repeats.png'), image.toPNG());
  }
  console.log('Four repeated refusals showed as one row counting 4, and opened to four entries with their own times.');
  await page('document.querySelector(".app-log-list").scrollTop = 0');
  await wait(200);

  // Press on the newest row, drag down two rows, release.
  const rowPoints = () => page(`[...document.querySelectorAll('[role="option"]')].slice(0, 4).map(row => { const box = row.querySelector('.app-log-time').getBoundingClientRect(); return [Math.round(box.left + 20), Math.round(box.top + box.height / 2)]; })`);
  const selectedCount = () => page('document.querySelectorAll(\'[role="option"][aria-selected="true"]\').length');
  const pointer = async (type, [x, y], extra = {}) => { send({ type, x, y, button: 'left', clickCount: 1, ...extra }); await wait(60); };
  await page(`(() => { const button = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Following'); button?.click(); })()`);
  await wait(300);
  const points = await rowPoints();
  assert.ok(points.length >= 3, 'at least three rows to select');
  await pointer('mouseDown', points[0]);
  await pointer('mouseMove', points[1], { modifiers: ['leftButtonDown'] });
  await pointer('mouseMove', points[2], { modifiers: ['leftButtonDown'] });
  await pointer('mouseUp', points[2]);
  await until(selectedCount, count => count === 3, 'three rows selected by dragging');
  assert.ok(await page('document.body.textContent.includes("3 entries selected")'));
  const selectedText = await page('[...document.querySelectorAll(\'[role="option"][aria-selected="true"] .app-log-message\')].map(node => node.textContent)');

  // Escape clears the selection and leaves Settings open.
  send({ type: 'keyDown', keyCode: 'Escape' }); send({ type: 'keyUp', keyCode: 'Escape' });
  await until(selectedCount, count => count === 0, 'Escape to clear the selection');
  assert.ok(await page('Boolean(document.querySelector(".settings-popover"))'), 'Settings stays open');
  await pointer('mouseDown', points[0]);
  await pointer('mouseMove', points[2], { modifiers: ['leftButtonDown'] });
  await pointer('mouseUp', points[2]);
  await until(selectedCount, count => count === 3, 'the selection again');

  // Right-click a selected row and copy through the menu.
  const menuItem = (label) => page(`(() => { const item = [...document.querySelectorAll('[role="menuitem"]')].find(node => node.textContent.trim() === ${JSON.stringify(label)}); if (!item) return null; const box = item.getBoundingClientRect(); return [Math.round(box.left + 10), Math.round(box.top + box.height / 2)]; })()`);
  const rightClick = async (point) => { await pointer('mouseDown', point, { button: 'right' }); await pointer('mouseUp', point, { button: 'right' }); };
  await rightClick(points[1]);
  const copyPoint = await until(() => menuItem('Copy 3 entries'), Boolean, 'the Copy menu item');
  assert.ok(await menuItem('Ask Rem about 3 entries'));
  if (process.env.RATICODE_APP_LOG_SCREENSHOT) {
    await wait(400);
    const image = await window.webContents.capturePage();
    fs.writeFileSync(process.env.RATICODE_APP_LOG_SCREENSHOT.replace(/\.png$/, '-menu.png'), image.toPNG());
  }
  await pointer('mouseDown', copyPoint); await pointer('mouseUp', copyPoint);
  const copiedText = await until(async () => clipboard.readText(), text => /^\d{4}-/.test(text || ''), 'copied log text');
  const copiedLines = copiedText.split('\n').filter(line => /^\d{4}-\d{2}-\d{2}T/.test(line));
  assert.equal(copiedLines.length, 3);
  assert.deepEqual([...copiedLines].sort(), copiedLines, 'copied oldest first');
  for (const text of selectedText) assert.ok(copiedText.includes(text.replace(/\+\d+ lines?$/, '')), `copied: ${text}`);
  assert.ok(await page('Boolean(document.querySelector(".settings-popover"))'), 'Settings stays open after using the menu');

  // Ask Rem: Settings closes, the entries are attached, and the cursor waits in an empty message box.
  await rightClick(points[0]);
  const askPoint = await until(() => menuItem('Ask Rem about 3 entries'), Boolean, 'the Ask Rem menu item');
  await pointer('mouseDown', askPoint); await pointer('mouseUp', askPoint);
  await until(() => page('!document.querySelector(".settings-popover")'), Boolean, 'Settings to close');
  const composerState = (name) => page(`(() => {
    const chip = document.querySelector('[aria-label=${JSON.stringify(`Remove ${name}`)}]');
    const box = document.querySelector('textarea[aria-label="Message Rem"]');
    return chip && box ? { value: box.value, focused: document.activeElement === box, chips: document.querySelectorAll('[aria-label^="Remove raticode-log"]').length } : null;
  })()`);
  const first = await until(() => composerState('raticode-log-3-entries.txt'), (state) => Boolean(state?.focused), 'the attachment and the cursor in Rem\'s message box');
  assert.equal(first.value, '', 'no question is written for the person');

  // Typed text survives a second Ask Rem, and nothing is sent until the person sends it.
  window.webContents.insertText('Why are these warnings?');
  await until(() => page('document.querySelector(\'textarea[aria-label="Message Rem"]\').value'), (value) => value === 'Why are these warnings?', 'the typed question');
  await page('document.querySelector(\'[aria-label="Open settings"]\').click()');
  await until(() => page('[...document.querySelectorAll("button")].some(b => b.textContent.trim() === "Developer")'), Boolean, 'Settings again');
  await page('[...document.querySelectorAll("button")].find(b => b.textContent.trim() === "Developer").click()');
  await until(() => page('document.querySelectorAll(\'[role="option"]\').length'), (count) => count > 0, 'log rows again');
  const again = await rowPoints();
  await rightClick(again[0]);
  const askOne = await until(() => menuItem('Ask Rem about this entry'), Boolean, 'Ask Rem for one entry');
  await pointer('mouseDown', askOne); await pointer('mouseUp', askOne);
  const second = await until(() => composerState('raticode-log-entry.txt'), (state) => Boolean(state?.focused), 'the second attachment and the cursor');
  assert.equal(second.value, 'Why are these warnings?', 'the typed question is kept');
  assert.equal(second.chips, 2, 'both selections stay attached, unsent');
  console.log('Ask Rem attached the selections to an empty, focused "Message Rem" box and kept typed text.');
  if (process.env.RATICODE_APP_LOG_SCREENSHOT) {
    await wait(400);
    const image = await window.webContents.capturePage();
    fs.writeFileSync(process.env.RATICODE_APP_LOG_SCREENSHOT.replace(/\.png$/, '-rem.png'), image.toPNG());
  }

  const file = path.join(profile, 'logs', 'app.jsonl');
  await until(async () => fs.existsSync(file) && fs.readFileSync(file, 'utf8'), text => text && text.includes('gofer.ui.server'), 'the log file');
  if (process.platform !== 'win32') assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  clearTimeout(timer);
  console.log(`Application log captured ${log.entries.length} startup entries from the desktop and backend.`);
  app.quit();
}

run().catch(error => { console.error(error); app.exit(1); });
