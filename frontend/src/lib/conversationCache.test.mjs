import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createConversationCache } from './conversationCache.js';

test('visible phone thought deltas replace prior imported text without duplicating messages', () => {
  const f = fixture();
  const user = { id: 'phone-user', role: 'user', body: 'Question', origin: 'phone' };
  const thought = { id: 'trace', role: 'assistant', kind: 'thought', body: 'First', deviceRequestId: user.id, deviceSequence: 1 };
  f.cache.hydrate('phone', [user, thought], { recent: true, device: true });
  f.cache.hydrate('phone', [{ ...thought, body: 'First, then second' }], { recent: true, device: true });
  assert.equal(f.snapshot.phone.length, 2);
  assert.equal(f.snapshot.phone[1].body, 'First, then second');
});

function fixture(options = {}) {
  const stored = new Map();
  const writes = [];
  let snapshot = {};
  let reads = 0;
  const cache = createConversationCache({
    load(id) { reads++; return stored.get(id) || [{ role: 'user', body: id.repeat(100) }]; },
    save(id, messages) { writes.push(id); stored.set(id, messages); },
    changed(value) { snapshot = value; },
    ...options,
  });
  return { cache, stored, writes, get snapshot() { return snapshot; }, get reads() { return reads; } };
}

test('visiting 50 histories keeps active plus four recent histories and reloads evicted data once', () => {
  const f = fixture();
  for (let i = 0; i < 50; i++) { f.cache.get(String(i)); f.cache.activate([String(i)]); }
  assert.equal(Object.keys(f.snapshot).length, 5);
  assert.equal(f.reads, 50);
  f.cache.get('0');
  f.cache.activate(['0']);
  f.cache.get('0');
  assert.equal(f.reads, 51);
  assert.equal(Object.keys(f.snapshot).length, 5);
});

test('byte budget evicts large inactive histories while running histories remain available', () => {
  const f = fixture({ maxBytes: 1000 });
  f.cache.get('running');
  f.cache.activate(['running', 'active']);
  f.cache.get('large-history');
  f.cache.get('active');
  f.cache.activate(['running', 'active']);
  assert.deepEqual(Object.keys(f.snapshot).sort(), ['active', 'running']);
});

test('100 events in one reader chunk persist once with all events in order', () => {
  const f = fixture();
  f.cache.activate(['stream']);
  f.cache.batch(() => {
    for (let i = 0; i < 100; i++) f.cache.update('stream', (history) => [...history, { role: 'assistant', body: String(i) }]);
  });
  assert.deepEqual(f.writes, ['stream']);
  assert.equal(f.stored.get('stream').length, 101);
  assert.equal(f.stored.get('stream').at(-1).body, '99');
});

test('error event still flushes earlier messages before leaving the reader chunk', () => {
  const f = fixture();
  assert.throws(() => f.cache.batch(() => {
    f.cache.update('stream', [{ role: 'assistant', body: 'before error' }]);
    throw new Error('provider failed');
  }), /provider failed/);
  assert.equal(f.stored.get('stream')[0].body, 'before error');
});

test('quota failure pins unsaved histories and later successful writes allow eviction', () => {
  let failed = true;
  const f = fixture({ maxInactive: 0, save: () => !failed });
  f.cache.update('failed', [{ role: 'assistant', body: 'unsaved' }]);
  f.cache.activate([]);
  assert.equal(f.snapshot.failed[0].body, 'unsaved');
  failed = false;
  f.cache.update('failed', (history) => history);
  assert.deepEqual(f.snapshot, {});
});

test('deletion discards pending writes without resurrecting the thread', () => {
  const f = fixture();
  f.cache.batch(() => { f.cache.update('deleted', []); f.cache.remove('deleted'); });
  assert.deepEqual(f.writes, []);
  assert.deepEqual(f.snapshot, {});
});

test('recent-page hydration keeps a running conversation in order and preserves unsaved text', () => {
  const messages = Array.from({ length: 60 }, (_, index) => ({ id: String(index), role: 'assistant', body: String(index) }));
  const f = fixture({ load: () => messages });
  f.cache.get('thread');
  f.cache.activate(['thread']);
  f.cache.hydrate('thread', messages.slice(-40), { recent: true });
  assert.deepEqual(f.snapshot.thread.map(message => message.id), messages.map(message => message.id));
  f.cache.trim('thread', 40);
  f.cache.hydrate('thread', messages.slice(0, 20));
  assert.deepEqual(f.snapshot.thread.map(message => message.id), messages.map(message => message.id));
});

test('a pending asynchronous save remains pinned until it commits', async () => {
  let complete;
  const f = fixture({ maxInactive: 0, save: () => new Promise(resolve => { complete = resolve; }) });
  f.cache.update('thread', [{ id: 'one', role: 'user', body: 'Unsaved' }]);
  f.cache.activate([]);
  assert.equal(f.snapshot.thread[0].body, 'Unsaved');
  complete(true);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(f.snapshot.thread, undefined);
});


test('hydration during an asynchronous save does not leave the cache permanently pinned', async () => {
  let complete;
  const f = fixture({ load: () => [], maxInactive: 0, save: () => new Promise(resolve => { complete = resolve; }) });
  f.cache.activate(['thread']);
  f.cache.update('thread', [{ id: 'new', role: 'user', body: 'New' }]);
  f.cache.hydrate('thread', [{ id: 'old', role: 'user', body: 'Old' }]);
  f.cache.activate([]);
  assert.equal(f.snapshot.thread.length, 2);
  complete(true);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(f.snapshot.thread, undefined);
});
