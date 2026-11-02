require("./background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const { app, BrowserWindow } = require("electron");
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-dev-shm-usage");
// This isolated regression process has the same display-container requirement
// as studio-policy.browser.cjs; production sandbox settings are unchanged.
app.commandLine.appendSwitch("no-sandbox");
let server;
const timeout = setTimeout(() => finish(new Error("Conversation storage browser test timed out")), 30000);
function finish(error) {
  clearTimeout(timeout);
  for (const window of BrowserWindow.getAllWindows()) window.destroy();
  server?.close();
  if (error) console.error(error);
  app.exit(error ? 1 : 0);
}
app.whenReady().then(async () => {
  const source = fs.readFileSync(path.join(__dirname, "../../src/lib/conversationStorage.js"));
  server = http.createServer((request, response) => {
    const modules = { "/storage.js": source, "/conversationStorage.js": source, "/repository.js": fs.readFileSync(path.join(__dirname, "../../src/lib/conversationRepository.js")), "/conversationPages.js": fs.readFileSync(path.join(__dirname, "../../src/lib/conversationPages.js")) };
    response.setHeader("Content-Type", modules[request.url] ? "text/javascript" : "text/html");
    response.end(modules[request.url] || "<!doctype html><title>Conversation storage regression</title>");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  const studio = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, partition: "conversation-storage-regression" } });
  await studio.loadURL(url);
  const timing = await studio.webContents.executeJavaScript(`(async () => {
    const { loadConversationMessages, saveConversationMessages } = await import('/storage.js');
    const key = 'test-conversation';
    const history = Array.from({length: 1000}, (_, id) => ({id: String(id), role: 'assistant', body: 'x'.repeat(2048)}));
    localStorage.setItem(key, JSON.stringify(history));
    const recovered = loadConversationMessages(key);
    saveConversationMessages(key, recovered);
    const metrics = {};
    for (const incremental of [false, true]) {
      let bytesWritten = 0;
      let maxTurnMs = 0;
      let messages = recovered;
      const measuredStorage = {
        getItem: key => localStorage.getItem(key),
        setItem: (key, value) => { bytesWritten += value.length; localStorage.setItem(key, value); },
        removeItem: key => localStorage.removeItem(key),
      };
      // Prime object identities outside the timed update loop.
      if (incremental) saveConversationMessages(key, messages, measuredStorage);
      bytesWritten = 0;
      const start = performance.now();
      for (let index = 0; index < 40; index++) {
        const turnStart = performance.now();
        messages = [...messages.slice(0, -1), {...messages.at(-1), body: 'Final update ' + index}];
        if (incremental) saveConversationMessages(key, messages, measuredStorage);
        else measuredStorage.setItem('baseline', JSON.stringify(messages));
        maxTurnMs = Math.max(maxTurnMs, performance.now() - turnStart);
        await new Promise(resolve => setTimeout(resolve, 0));
      }
      metrics[incremental ? 'cachedMessageSerialization' : 'fullArray'] = {elapsedMs: performance.now() - start, maxTurnMs, bytesWritten};
      localStorage.removeItem('baseline');
    }
    return metrics;
  })()`);
  // Navigation discards the module's identity cache, exercising disk recovery.
  await studio.loadURL(`${url}/reopened`);
  assert.deepEqual(await studio.webContents.executeJavaScript(`(async () => {
    const { loadConversationMessages, removeConversationMessages } = await import('/storage.js');
    const messages = loadConversationMessages('test-conversation');
    const result = {count: messages.length, final: messages.at(-1)?.body};
    removeConversationMessages('test-conversation');
    result.remainingKeys = localStorage.length;
    return result;
  })()`), { count: 1000, final: "Final update 39", remainingKeys: 0 });
  const result = await studio.webContents.executeJavaScript(`(async () => {
    const { createConversationRepository } = await import('/repository.js');
    const repository = createConversationRepository();
    const history = Array.from({ length: 125 }, (_, i) => ({ id: String(i), role: 'user', body: i === 1 ? 'ancient needle saved before compaction' : 'Message ' + i }));
    localStorage.setItem('gofer-flow-chat-thread:retained', JSON.stringify(history));
    const page = await repository.page('retained');
    const older = await repository.page('retained', page.before);
    await repository.checkpoint('retained', [{ id: 'memory', role: 'system', body: 'Summary' }], '124');
    const reply = { id: 'reply', role: 'assistant', body: 'New reply' };
    await repository.save('retained', [...page.messages, reply], page.messages);
    const context = await repository.context('retained', [...page.messages, reply]);
    const threads = [{ id: 'retained', title: 'Retained thread' }];
    const search = await repository.search(threads, 'needle');
    const retained = await repository.all('retained');
    // A failed transaction must leave both the message and search index intact.
    let aborted = false;
    try { await repository.save('retained', [{ id: 'bad', role: 'user', body: 'Invalid', uncloneable: () => {} }]); } catch { aborted = true; }
    const afterFailure = await repository.all('retained');
    return { pageCount: page.messages.length, first: page.messages[0].id, hasMore: page.hasMore, olderFirst: older.messages[0].id, olderLast: older.messages.at(-1).id, migrated: localStorage.getItem('gofer-flow-chat-thread:retained') === null, context: context.map(message => message.body), matches: search.results.map(result => result.messageId), total: retained.length, aborted, afterFailure: afterFailure.length };
  })()`);
  assert.deepEqual(result, { pageCount: 15, first: '110', hasMore: true, olderFirst: '95', olderLast: '109', migrated: true, context: ['Summary', 'New reply'], matches: ['1'], total: 126, aborted: true, afterFailure: 126 });
  assert.deepEqual(await studio.webContents.executeJavaScript(`(async () => {
    const { createConversationRepository } = await import('/repository.js');
    const repository = createConversationRepository();
    const beginning = await repository.around('retained', '1');
    const middle = await repository.around('retained', '60');
    const older = await repository.older('retained', middle.before);
    const newer = await repository.newer('retained', middle.after);
    const last = await repository.around('retained', 'reply');
    const titleAndBody = await repository.search([{ id: 'retained', title: 'needle' }], 'needle');
    let missing = false;
    try { await repository.around('retained', 'missing'); } catch { missing = true; }
    // Exercise the localStorage compatibility path too.
    localStorage.setItem('gofer-flow-chat-thread:fallback', JSON.stringify(Array.from({length: 100}, (_, id) => ({id, role: 'assistant', body: 'Saved ' + id}))));
    const fallback = createConversationRepository({indexedDB: null});
    const legacy = await fallback.around('fallback', 45);
    const following = await fallback.newer('fallback', legacy.after);
    return {
      beginning: [beginning.messages.length, beginning.messages[0].id, beginning.hasMore, beginning.hasNewer],
      middle: [middle.messages.length, middle.messages[0].id, middle.messages.at(-1).id],
      adjacent: [older.messages.at(-1).id, newer.messages[0].id],
      last: [last.messages.at(-1).id, last.hasNewer], titleAndBody: titleAndBody.results[0].messageId, missing,
      legacy: [legacy.messages[0].id, legacy.messages.at(-1).id, following.messages[0].id, following.hasNewer],
    };
  })()`), { beginning: [40, '0', false, true], middle: [40, '40', '79'], adjacent: ['39', '80'], last: ['reply', false], titleAndBody: '1', missing: true, legacy: [25, 64, 65, false] });
  assert.deepEqual(await studio.webContents.executeJavaScript(`(async () => {
    // Version 1 rows have no lane; opening the repository indexes them.
    const v1 = await new Promise((resolve, reject) => {
      const request = indexedDB.open('lane-upgrade', 1);
      request.onupgradeneeded = () => {
        const messages = request.result.createObjectStore('messages', { keyPath: ['threadId', 'id'] });
        messages.createIndex('order', ['threadId', 'sequence'], { unique: true });
        messages.createIndex('search', 'grams', { multiEntry: true });
        request.result.createObjectStore('threads', { keyPath: 'id' });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const turns = [];
    for (let n = 1; n <= 20; n++) {
      turns.push({ id: 'u' + n, role: 'user', body: 'Question ' + n });
      for (let k = 0; k < 5; k++) turns.push({ id: 't' + n + '-' + k, role: 'assistant', kind: 'thought', groupId: 'turn-' + n, body: 'Turn ' + n + ' thought ' + k });
      if (n === 18) turns.push({ id: 'memory', role: 'system', kind: 'memory', body: 'Summary' });
      if (n < 20) turns.push({ id: 'a' + n, role: 'assistant', body: 'Answer ' + n });
    }
    const tx = v1.transaction(['messages', 'threads'], 'readwrite');
    turns.forEach((message, index) => tx.objectStore('messages').put({ threadId: 'turns', id: message.id, sequence: index + 1, message, text: message.body.toLowerCase(), grams: [] }));
    tx.objectStore('threads').put({ id: 'turns', sequence: turns.length });
    await new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); });
    v1.close();
    const { createConversationRepository } = await import('/repository.js');
    const repository = createConversationRepository({ name: 'lane-upgrade' });
    const ids = page => page.messages.map(message => message.id).join(',');
    const runs = page => page.thoughts.map(run => run.anchorId + ':' + run.ids.length).join(',');
    const latest = await repository.page('turns');
    const middle = await repository.page('turns', latest.before);
    const first = await repository.page('turns', middle.before);
    const expanded = (await repository.thoughts('turns', latest.thoughts[0].ids)).map(message => message.body);
    const database = await new Promise(resolve => { const request = indexedDB.open('lane-upgrade'); request.onsuccess = () => resolve(request.result); });
    const lanes = await new Promise(resolve => { const request = database.transaction('messages').objectStore('messages').getAll(); request.onsuccess = () => resolve(request.result.reduce((count, row) => ({ ...count, [row.lane]: (count[row.lane] || 0) + 1 }), {})); });
    const version = database.version;
    database.close();
    // A reply written after the upgrade closes the unfinished turn.
    await repository.save('turns', [{ id: 'a20', role: 'assistant', body: 'Answer 20' }]);
    const answered = await repository.page('turns');
    // Pages from localStorage count the same way and keep their thoughts.
    localStorage.setItem('gofer-flow-chat-thread:fallback-turns', JSON.stringify(turns));
    const fallback = await createConversationRepository({ indexedDB: null }).page('fallback-turns');
    return {
      version, lanes,
      latest: [ids(latest), runs(latest), latest.hasMore],
      middle: [middle.messages[0].id, middle.messages.at(-1).id, runs(middle), middle.hasMore],
      first: [first.messages[0].id, first.messages.at(-1).id, runs(first), first.hasMore],
      expanded, answered: [answered.messages.at(-1).id, answered.thoughts.at(-1).anchorId, answered.messages.some(message => message.kind === 'thought')],
      fallback: [fallback.messages[0].id, fallback.messages.filter(message => !message.kind).length, fallback.messages.length],
    };
  })()`), {
    version: 2, lanes: { 0: 100, 1: 39, 2: 1 },
    latest: ["u13,a13,u14,a14,u15,a15,u16,a16,u17,a17,u18,memory,a18,u19,a19,u20,t20-0,t20-1,t20-2,t20-3,t20-4", "a13:5,a14:5,a15:5,a16:5,a17:5,a18:5,a19:5", true],
    middle: ["a5", "a12", "a5:5,a6:5,a7:5,a8:5,a9:5,a10:5,a11:5,a12:5", true],
    first: ["u1", "u5", "a1:5,a2:5,a3:5,a4:5", false],
    expanded: ["Turn 13 thought 0", "Turn 13 thought 1", "Turn 13 thought 2", "Turn 13 thought 3", "Turn 13 thought 4"],
    answered: ["a20", "a20", false],
    fallback: ["u13", 15, 56],
  });
  await studio.loadURL(`${url}/durable-reopen`);
  assert.deepEqual(await studio.webContents.executeJavaScript(`(async () => {
    const { createConversationRepository } = await import('/repository.js');
    const repository = createConversationRepository();
    const restored = await repository.all('retained');
    const match = await repository.search([{ id: 'retained', title: 'Retained thread' }], 'NEEDLE');
    const edited = { ...restored.at(-1), body: 'Updated reply' };
    await repository.save('retained', [edited], [restored.at(-1)]);
    const noStaleMatch = await repository.search([{ id: 'retained', title: 'Retained thread' }], 'New reply');
    await repository.remove('retained');
    // A managed thread's early message that arrives late is stored in the server's order.
    const later = [{ id: 'reply', role: 'assistant', body: 'Report' }, { id: 'review', role: 'user', body: 'Findings' }];
    await repository.save('ordered', later);
    const saved = await repository.all('ordered');
    await repository.save('ordered', [{ id: 'prompt', role: 'user', body: 'Investigate' }, ...saved], saved, saved, { resequence: true });
    const order = (await repository.all('ordered')).map(message => message.id).join(',');
    await repository.remove('ordered');
    return { count: restored.length, matches: match.results.length, stale: noStaleMatch.results.length, removed: (await repository.all('retained')).length, order };
  })()`), { count: 126, matches: 1, stale: 0, removed: 0, order: "prompt,reply,review" });
  console.log("IndexedDB migration, lane upgrade, message-counted pagination, lazy thoughts, compaction retention, full-history search, failed transaction, reload and deletion checks passed.");
  console.log("Real Chromium localStorage compatibility, reload, and deletion checks passed.");
  console.log(JSON.stringify({fixture: {messages: 1000, bodyBytes: 2048, updates: 40}, timing, caveat: "Generated fixture; elapsed time includes browser timer yielding; maxTurnMs measures synchronous save cost on this host. Full array joining and write volume are retained to preserve atomicity and quota capacity."}, null, 2));
  finish();
}).catch(finish);
