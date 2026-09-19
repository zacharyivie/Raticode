const test = require('node:test');
const assert = require('node:assert/strict');
const { createPathGrantQueue, GRANT_CACHE_MS } = require('../path-grant-queue.cjs');
const handle = i => ({ grantId: `grant-${i}`, path: `/worktree/${i}` });
const tick = () => new Promise(resolve => setImmediate(resolve));

test('71 worktrees across simultaneous callers stay within four requests; failure does not stop the queue', async () => {
  const queue = createPathGrantQueue();
  let active = 0, peak = 0, calls = 0;
  const register = i => queue.register(handle(i), async () => {
    calls++;
    peak = Math.max(peak, ++active);
    await tick();
    active--;
    if (i === 3) throw new Error('HTTP 503');
  });
  const jobs = Array.from({ length: 71 }, (_, i) => register(i));
  const duplicates = Array.from({ length: 71 }, (_, i) => register(i));
  const results = await Promise.allSettled([...jobs, ...duplicates]);
  assert.equal(peak, 4);
  assert.equal(calls, 71);
  assert.equal(results.filter(r => r.status === 'rejected').length, 2);
  await register(72);
  assert.equal(calls, 72);
});

test('duplicates share the same promise and successful acknowledgments are reused until expiry', async () => {
  let time = 0, calls = 0;
  const queue = createPathGrantQueue({ now: () => time });
  const send = async () => { calls++; };
  const first = queue.register(handle(1), send);
  assert.equal(queue.register(handle(1), send), first);
  await first;
  time = GRANT_CACHE_MS - 1;
  await queue.register(handle(1), send);
  assert.equal(calls, 1);
  time++;
  await queue.register(handle(1), send);
  assert.equal(calls, 2);
});

test('timeout removes in-flight state and permits immediate retry', async () => {
  const queue = createPathGrantQueue();
  let calls = 0;
  const send = async () => {
    if (++calls === 1) throw Object.assign(new Error('timeout'), { name: 'TimeoutError' });
  };
  await assert.rejects(queue.register(handle(1), send), /timeout/);
  await queue.register(handle(1), send);
  await queue.register(handle(1), send);
  assert.equal(calls, 2);
});

test('backend restart invalidates cached, queued and late acknowledgments', async () => {
  const queue = createPathGrantQueue();
  let calls = 0, release;
  const send = async () => { calls++; };
  await queue.register(handle(0), send);
  const gate = new Promise(resolve => { release = resolve; });
  const old = Array.from({ length: 5 }, (_, i) => queue.register(handle(i + 1), () => gate));
  const results = Promise.allSettled(old);
  await tick();
  queue.reset();
  const next = queue.register(handle(1), send);
  release();
  assert.ok((await results).every(r => r.status === 'rejected'));
  await next;
  await queue.register(handle(0), send);
  await queue.register(handle(1), send);
  assert.equal(calls, 3);
});

test('different paths sharing a grant ID serialize and cannot reuse the previous path acknowledgment', async () => {
  const queue = createPathGrantQueue();
  let active = 0, peak = 0, calls = 0;
  const send = async () => { calls++; peak = Math.max(peak, ++active); await tick(); active--; };
  const first = handle(1), other = { ...first, path: '/other' };
  await Promise.all([queue.register(first, send), queue.register(other, send)]);
  await queue.register(first, send);
  assert.equal(peak, 1);
  assert.equal(calls, 3);
});

test('main registration preserves timeout diagnostics, retries, and rejects mismatched acknowledgments', async () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const vm = require('node:vm');
  const source = fs.readFileSync(path.join(__dirname, '../main.js'), 'utf8');
  const logs = [];
  let mode = 'timeout', calls = 0;
  const context = vm.createContext({
    backendReady: Promise.resolve(),
    backendPathGrants: createPathGrantQueue(),
    getIpcSecurity: () => ({ isUserGrant: id => id === 'user' }),
    activeApiBaseUrl: 'http://localhost:1234', desktopGrantSecret: 'secret', activeUiApiToken: 'token',
    AbortSignal, Date,
    writeBackendLog: line => logs.push(line),
    fetch: async (_url, options) => {
      calls++;
      assert.ok(options.signal instanceof AbortSignal);
      if (mode === 'timeout') throw Object.assign(new Error('private details'), { name: 'TimeoutError' });
      const body = JSON.parse(options.body);
      return { ok: true, status: 201, json: async () => ({ ...body, path: mode === 'mismatch' ? '/other' : body.path }) };
    },
  });
  vm.runInContext(source.slice(source.indexOf('async function registerBackendPathGrant('), source.indexOf('function getIpcSecurity(')), context);
  await assert.rejects(context.registerBackendPathGrant({ path: '/user', grantId: 'user' }), /does not grant agent access/);
  assert.equal(calls, 0);
  await assert.rejects(context.registerBackendPathGrant(handle(1)), /backend timed out/);
  assert.match(logs[0], /"reason":"timeout"/);
  assert.doesNotMatch(logs.join(''), /private details|secret|token|grant-1/);
  mode = 'mismatch';
  await assert.rejects(context.registerBackendPathGrant(handle(1)), /could not confirm folder access/);
  mode = 'success';
  await context.registerBackendPathGrant(handle(1));
  await context.registerBackendPathGrant(handle(1));
  assert.equal(calls, 3);
});
