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
    const modules = { "/storage.js": source, "/conversationStorage.js": source, "/repository.js": fs.readFileSync(path.join(__dirname, "../../src/lib/conversationRepository.js")) };
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
  assert.deepEqual(result, { pageCount: 40, first: '85', hasMore: true, olderFirst: '45', olderLast: '84', migrated: true, context: ['Summary', 'New reply'], matches: ['1'], total: 126, aborted: true, afterFailure: 126 });
  assert.deepEqual(await studio.webContents.executeJavaScript(`(async () => {
    const { createConversationRepository } = await import('/repository.js');
    const repository = createConversationRepository();
    const beginning = await repository.around('retained', '1');
    const middle = await repository.around('retained', '60');
    const older = await repository.page('retained', middle.before);
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
    return { count: restored.length, matches: match.results.length, stale: noStaleMatch.results.length, removed: (await repository.all('retained')).length };
  })()`), { count: 126, matches: 1, stale: 0, removed: 0 });
  console.log("IndexedDB migration, pagination, compaction retention, full-history search, failed transaction, reload and deletion checks passed.");
  console.log("Real Chromium localStorage compatibility, reload, and deletion checks passed.");
  console.log(JSON.stringify({fixture: {messages: 1000, bodyBytes: 2048, updates: 40}, timing, caveat: "Generated fixture; elapsed time includes browser timer yielding; maxTurnMs measures synchronous save cost on this host. Full array joining and write volume are retained to preserve atomicity and quota capacity."}, null, 2));
  finish();
}).catch(finish);
