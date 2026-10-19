import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import express from 'express';
import { ConversationStore } from './owned-fixtures.js';
import { ChatService } from './owned-fixtures.js';
import { createChatRouter } from '../chat-routes.js';

test('conversation HTTP routes create, reload, stop, retry and delete without crossing documents', { timeout: 10000 }, async () => {
  const store = new ConversationStore(':memory:');
  let holdAnswer = false;
  let notifyStarted;
  const started = new Promise((resolve) => { notifyStarted = resolve; });
  const service = new ChatService(store,
    async (id) => ({ documents: [{ pageContent: 'Synthetic source' }], selectedDocument: { id } }),
    async () => {
      if (holdAnswer) { notifyStarted(); return new Promise(() => {}); }
      return { message: 'Synthetic answer [1]', sources: [{ id: 1, passage: 'Synthetic source' }] };
    });
  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => { req.ownerId = 'user_test'; next(); });
  app.use('/conversations', createChatRouter(store, service, async () => [{ id: 'a' }, { id: 'b' }]));
  app.use((err, _req, res, _next) => res.status(err.status || 500).json({ message: err.publicMessage || 'Request failed' }));
  const server = await new Promise((resolve) => { const instance = app.listen(0, '127.0.0.1', () => resolve(instance)); });
  const base = `http://127.0.0.1:${server.address().port}/conversations`;
  const request = async (url, method = 'GET', body) => {
    const response = await fetch(base + url, { method, headers: { 'Content-Type': 'application/json', Accept: 'application/x-ndjson' }, body: body === undefined ? undefined : JSON.stringify(body) });
    if (response.headers.get('content-type')?.includes('application/x-ndjson')) {
      const events = (await response.text()).trim().split('\n').map((line) => JSON.parse(line));
      assert.equal(events[0].type, 'started');
      assert.equal(events.at(-1).type, 'done');
      return { status: response.status, data: events.at(-1) };
    }
    return { status: response.status, data: await response.json() };
  };
  try {
    assert.equal((await request('', 'POST', { documentId: 'unknown' })).status, 404);
    const created = await request('', 'POST', { documentId: 'a' });
    assert.equal(created.status, 201);
    const id = created.data.conversation.id;
    assert.equal((await request(`/${id}?documentId=b`)).status, 404);
    assert.equal((await request('?documentId=b')).data.conversations.length, 0);
    const turnId = randomUUID();
    const first = await request(`/${id}/messages`, 'POST', { documentId: 'a', turnId, question: 'Explain the source' });
    assert.equal(first.data.conversation.turns[0].status, 'completed');
    assert.equal((await request(`/${id}?documentId=a`)).data.conversation.turns[0].sources[0].passage, 'Synthetic source');
    assert.equal((await request(`/${id}/messages`, 'POST', { documentId: 'a', turnId, question: 'Duplicate' })).status, 409);
    holdAnswer = true;
    const nextId = randomUUID();
    const running = request(`/${id}/messages`, 'POST', { documentId: 'a', turnId: nextId, question: 'More detail' });
    await started;
    assert.equal((await request(`/${id}?documentId=a`, 'DELETE')).status, 409);
    assert.equal((await request(`/${id}/stop`, 'POST', { documentId: 'b' })).status, 404);
    assert.equal((await request(`/${id}/stop`, 'POST', { documentId: 'a' })).status, 200);
    assert.equal((await running).data.conversation.turns[1].status, 'stopped');
    holdAnswer = false;
    const retried = await request(`/${id}/messages`, 'POST', { documentId: 'a', turnId: nextId, retry: true });
    assert.equal(retried.data.conversation.turns.length, 2);
    assert.equal(retried.data.conversation.turns[1].status, 'completed');
    assert.equal((await request(`/${id}?documentId=a`, 'DELETE')).status, 200);
    assert.equal((await request(`/${id}?documentId=a`)).status, 404);
    assert.deepEqual((await request('?documentId=a')).data.conversations, []);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    store.close();
  }
});
