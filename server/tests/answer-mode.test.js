import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { ConversationStore } from './owned-fixtures.js';
import { ChatService } from './owned-fixtures.js';
import { answerWithSources } from '../citations.js';

test('saved mode controls retrieval and generation, including retries with a different requested mode', async () => {
  const store = new ConversationStore(':memory:');
  const chat = store.create('doc');
  const counts = [];
  const modes = [];
  const service = new ChatService(store, async (_id, _query, _signal, count) => {
    counts.push(count);
    return { documents: [{}], selectedDocument: {} };
  }, async (_q, _docs, _doc, { answerMode }) => {
    modes.push(answerMode);
    if (modes.length === 2) throw Error('Synthetic failure');
    return { message: 'Answer', sources: [] };
  });
  try {
    await service.send(chat.id, 'doc', { turnId: randomUUID(), question: 'Default' });
    const turnId = randomUUID();
    await service.send(chat.id, 'doc', { turnId, question: 'More', answerMode: 'detailed' });
    const result = await service.send(chat.id, 'doc', { turnId, retry: true, answerMode: 'balanced' });
    assert.deepEqual(counts, [2, 5, 5]);
    assert.deepEqual(modes, ['balanced', 'detailed', 'detailed']);
    assert.equal(result.turns.length, 2);
    assert.equal(result.turns[1].answerMode, 'detailed');
    await assert.rejects(service.send(chat.id, 'doc', { turnId: randomUUID(), question: 'Bad', answerMode: 'invalid' }), { status: 400 });
    assert.equal(store.get(chat.id, 'doc').turns.length, 2);
  } finally { store.close(); }
});

test('old SQLite history migrates without losing answers; detailed mode survives reopening', async () => {
  const folder = await mkdtemp(path.join(tmpdir(), 'docquery-mode-'));
  const filename = path.join(folder, 'test.sqlite');
  let store = new ConversationStore(filename);
  try {
    const chat = store.create('doc');
    const turnId = randomUUID();
    store.beginTurn(chat.id, 'doc', { turnId, question: 'Old question' });
    store.finishTurn(turnId, { status: 'completed', answer: 'Old answer', sources: [{ id: 1 }] });
    store.db.exec('ALTER TABLE turns DROP COLUMN answerMode');
    store.close();
    store = new ConversationStore(filename);
    const old = store.get(chat.id, 'doc').turns[0];
    assert.equal(old.answer, 'Old answer');
    assert.equal(old.answerMode, 'balanced');
    assert.deepEqual(old.sources, [{ id: 1 }]);
    store.beginTurn(chat.id, 'doc', { turnId: randomUUID(), question: 'New', answerMode: 'detailed' });
    store.close();
    store = new ConversationStore(filename);
    assert.equal(store.get(chat.id, 'doc').turns[1].answerMode, 'detailed');
  } finally { store.close(); await rm(folder, { recursive: true }); }
});

test('only detailed mode adds thorough explanation instructions, on both model paths', async () => {
  const prompts = [];
  const model = {
    async invoke(input) { prompts.push(input[0][1]); return { content: 'Answer [1]' }; },
    async *stream(input) { prompts.push(input[0][1]); yield { content: 'Answer [1]' }; },
  };
  const args = [model, 'Question', [{ pageContent: 'Evidence' }], { filename: 'Sample' }];
  await answerWithSources(...args);
  await answerWithSources(...args, { answerMode: 'balanced' });
  await answerWithSources(...args, { answerMode: 'detailed', onEvent() {} });
  assert.equal(prompts[0], prompts[1]);
  assert.ok(prompts[2].startsWith(prompts[0]));
  assert.match(prompts[2], /only when the\nsupplied passages support them/);
  assert.match(prompts[2], /Do not invent examples/);
});
