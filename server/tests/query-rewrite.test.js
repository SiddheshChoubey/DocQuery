import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { rewriteQuery } from '../query-rewrite.js';
import { ConversationStore } from './owned-fixtures.js';
import { contextualQuery } from '../conversations.js';
import { ChatService } from './owned-fixtures.js';

const history = [{ role: 'user', content: 'Explain PDF indexing' }, { role: 'assistant', content: 'Chunks are indexed.' }];

test('first question skips Gemini; follow-up uses bounded history and returns only the search text', async () => {
  let calls = 0;
  const model = { async invoke(messages, { signal }) {
    calls++;
    const data = JSON.parse(messages[1][1]);
    assert.equal(data.question, 'Where is it stored?');
    assert.equal(data.history.length, 4);
    assert.ok(data.history.every((message) => message.content.length <= 2000));
    assert.match(messages[0][1], /filenames, and identifiers/);
    assert.equal(signal.aborted, false);
    return { content: [{ type: 'text', text: 'Where are indexed PDF chunks stored?' }] };
  } };
  assert.equal(await rewriteQuery(model, 'Original question', []), 'Original question');
  assert.equal(calls, 0);
  assert.equal(await rewriteQuery(model, 'Where is it stored?', [...history, ...history, ...history]), 'Where are indexed PDF chunks stored?');
  assert.equal(calls, 1);
});

test('empty, oversized, multiline, malformed and failed rewrites use the existing contextual query', async () => {
  const fallback = contextualQuery('Where?', history);
  for (const content of ['', 'x'.repeat(4001), 'one\ntwo', null, '```query```']) {
    assert.equal(await rewriteQuery({ invoke: async () => ({ content }) }, 'Where?', history), fallback);
  }
  assert.equal(await rewriteQuery({ invoke: async () => { throw Error('private provider error'); } }, 'Where?', history), fallback);
});

test('timeout falls back even if provider ignores abort; explicit Stop does not fall back', async () => {
  const model = { invoke: () => new Promise(() => {}) };
  assert.equal(await rewriteQuery(model, 'Where?', history, { timeoutMs: 10 }), contextualQuery('Where?', history));
  const controller = new AbortController();
  const result = rewriteQuery(model, 'Where?', history, { signal: controller.signal });
  controller.abort();
  await assert.rejects(result, { name: 'AbortError' });
});

test('follow-up rewriting preserves original questions, selected document, and each answer mode', async () => {
  const store = new ConversationStore(':memory:');
  const chat = store.create('doc');
  const queries = [];
  const questions = [];
  let rewrites = 0;
  const service = new ChatService(store, async (id, query, _signal, count) => {
    assert.equal(id, 'doc'); queries.push([query, count]);
    return { documents: [{}], selectedDocument: {} };
  }, async (question) => {
    questions.push(question); return { message: 'Answer', sources: [] };
  }, async (question, savedHistory) => {
    rewrites++; assert.ok(savedHistory.length); return `Standalone ${question}`;
  });
  try {
    for (const [question, answerMode] of [['First', 'balanced'], ['Next', 'balanced'], ['More', 'detailed']]) {
      await service.send(chat.id, 'doc', { turnId: randomUUID(), question, answerMode });
    }
    assert.equal(rewrites, 2);
    assert.deepEqual(queries, [['First', 2], ['Standalone Next', 2], ['Standalone More', 5]]);
    assert.deepEqual(questions, ['First', 'Next', 'More']);
    assert.deepEqual(store.get(chat.id, 'doc').turns.map((turn) => turn.question), questions);
  } finally { store.close(); }
});

test('stopping during rewriting prevents retrieval and saves a stopped follow-up', async () => {
  const store = new ConversationStore(':memory:');
  const chat = store.create('doc');
  const turnId = randomUUID();
  store.beginTurn(chat.id, 'doc', { turnId, question: 'First' });
  store.finishTurn(turnId, { status: 'completed', answer: 'Prior answer' });
  let searches = 0;
  let started;
  const ready = new Promise((resolve) => { started = resolve; });
  const service = new ChatService(store, async () => { searches++; }, async () => {},
    (question, saved, options) => rewriteQuery({ invoke: () => { started(); return new Promise(() => {}); } }, question, saved, options));
  try {
    const result = service.send(chat.id, 'doc', { turnId: randomUUID(), question: 'Follow-up' });
    await ready;
    service.stop(chat.id, 'doc');
    assert.equal((await result).turns[1].status, 'stopped');
    assert.equal(searches, 0);
  } finally { store.close(); }
});
