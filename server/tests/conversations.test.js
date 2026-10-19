import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, rm, rmdir } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { ConversationStore } from './owned-fixtures.js';
import { conversationContext, contextualQuery } from '../conversations.js';
import { ChatService } from './owned-fixtures.js';

function setup(t, retrieve, answer) {
  const store = new ConversationStore(':memory:');
  t.after(() => store.close());
  const conversation = store.create('doc-a');
  const service = new ChatService(store,
    retrieve || (async () => ({ documents: [{ pageContent: 'Synthetic passage' }], selectedDocument: { id: 'doc-a' } })),
    answer || (async () => ({ message: 'Synthetic answer [1].', sources: [{ id: 1, passage: 'Synthetic passage' }] })));
  return { store, service, conversation };
}
const input = (question = 'First question') => ({ turnId: randomUUID(), question });

test('completed messages and source mappings persist after reopening; pending turns recover as stopped', async () => {
  const folder = await mkdtemp(path.join(tmpdir(), 'docquery-chat-test-'));
  const filename = path.join(folder, 'chat.sqlite');
  let store = new ConversationStore(filename);
  try {
    const conversation = store.create('doc-a');
    const first = input();
    store.beginTurn(conversation.id, 'doc-a', first);
    store.finishTurn(first.turnId, { status: 'completed', answer: 'Answer [1]', sources: [{ id: 1, page: 3, passage: 'Original passage' }] });
    store.beginTurn(conversation.id, 'doc-a', input('Another question'));
    store.close();
    store = new ConversationStore(filename);
    const saved = store.get(conversation.id, 'doc-a');
    assert.equal(saved.turns[0].sources[0].passage, 'Original passage');
    assert.equal(saved.turns[1].status, 'stopped');
    assert.match(saved.turns[1].error, /server restarted/);
    assert.equal(saved.title, 'First question');
    assert.throws(() => store.get(conversation.id, 'doc-b'), { status: 404 });
    assert.deepEqual(store.list('doc-b'), []);
    store.remove(conversation.id, 'doc-a');
    assert.equal(store.db.prepare('SELECT count(*) AS count FROM turns').get().count, 0);
  } finally {
    store.close(); await rm(filename); await rmdir(folder);
  }
});

test('follow-ups use server-saved history for retrieval and generation without crossing documents', async (t) => {
  const queries = [];
  const histories = [];
  const { store, service, conversation } = setup(t, async (documentId, query) => {
    assert.equal(documentId, 'doc-a'); queries.push(query);
    return { documents: [{ pageContent: 'Synthetic passage' }], selectedDocument: { id: documentId } };
  }, async (_question, _docs, _document, options) => {
    histories.push(options.history);
    return { message: 'An answer about streams [1].', sources: [{ id: 1 }] };
  });
  await service.send(conversation.id, 'doc-a', input('What are streams?'));
  await service.send(conversation.id, 'doc-a', input('Give an example.'));
  assert.equal(queries[0], 'What are streams?');
  assert.match(queries[1], /What are streams\?/);
  assert.match(queries[1], /Current question: Give an example/);
  assert.deepEqual(histories[0], []);
  assert.equal(histories[1].length, 2);
  assert.equal(histories[1][0].content, 'What are streams?');
  await assert.rejects(service.send(conversation.id, 'doc-b', input()), { status: 404 });
  assert.equal(store.get(conversation.id, 'doc-a').turns.length, 2);
});

test('context is bounded and excludes failed, stopped and pending turns', () => {
  const turns = Array.from({ length: 20 }, (_, i) => ({ status: 'completed', question: `Q${i}` + 'x'.repeat(3000), answer: 'y'.repeat(3000) }));
  turns.push({ status: 'failed', question: 'bad' }, { status: 'stopped', question: 'stop' }, { status: 'pending', question: 'waiting' });
  const context = conversationContext(turns);
  assert.equal(context.length, 12);
  assert.ok(context.every((message) => message.content.length <= 2000));
  assert.match(context[0].content, /^Q14/);
  assert.equal(contextualQuery('new', []), 'new');
});

test('stopping saves a stopped turn; a late provider result cannot overwrite it; retry reuses its ID', async (t) => {
  let release;
  let started;
  const ready = new Promise((resolve) => { started = resolve; });
  const provider = new Promise((resolve) => { release = resolve; });
  const { store, service, conversation } = setup(t, undefined, async () => { started(); return provider; });
  const first = input();
  const running = service.send(conversation.id, 'doc-a', first);
  await ready;
  await assert.rejects(service.send(conversation.id, 'doc-a', input()), { status: 409 });
  assert.throws(() => service.beginDocumentDeletion('doc-a'), { status: 409 });
  service.stop(conversation.id, 'doc-a');
  assert.equal((await running).turns[0].status, 'stopped');
  release({ message: 'Late answer', sources: [] });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(store.get(conversation.id, 'doc-a').turns[0].answer, '');
  service.answer = async () => ({ message: 'Retried answer', sources: [] });
  const saved = await service.send(conversation.id, 'doc-a', { turnId: first.turnId, retry: true });
  assert.equal(saved.turns.length, 1);
  assert.equal(saved.turns[0].status, 'completed');
  assert.equal(saved.turns[0].answer, 'Retried answer');
});

test('disconnects stop generation, failures are sanitized, and duplicate submissions are rejected', async (t) => {
  const { store, service, conversation } = setup(t, async () => { throw Error('secret provider URL and API key'); });
  const first = input();
  const failed = await service.send(conversation.id, 'doc-a', first);
  assert.equal(failed.turns[0].status, 'failed');
  assert.doesNotMatch(failed.turns[0].error, /secret|API key/);
  await assert.rejects(service.send(conversation.id, 'doc-a', first), { status: 409 });
  const controller = new AbortController(); controller.abort();
  const stopped = await service.send(conversation.id, 'doc-a', input('Stopped by disconnect'), controller.signal);
  assert.equal(stopped.turns.at(-1).status, 'stopped');
  assert.throws(() => store.beginTurn(conversation.id, 'doc-a', input(' '.repeat(4001))), { status: 400 });
  service.beginDocumentDeletion('doc-a');
  await assert.rejects(service.send(conversation.id, 'doc-a', input()), { status: 409 });
  store.removeDocument('doc-a');
  assert.deepEqual(store.list('doc-a'), []);
});
