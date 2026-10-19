import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { answerWithSources } from '../citations.js';
import { ChatService } from './owned-fixtures.js';
import { ConversationStore } from './owned-fixtures.js';

test('Gemini chunks arrive before completion with stable sources and final citation checks', async () => {
  const events = [];
  const model = { async *stream() {
    assert.equal(events[0].type, 'sources');
    yield { content: 'Hello ' };
    assert.equal(events[1].text, 'Hello ');
    yield { content: [{ type: 'text', text: '[1].' }] };
  } };
  const result = await answerWithSources(model, 'Question', [{ pageContent: 'Hello' }], { filename: 'Test' }, { onEvent: (event) => events.push(event) });
  assert.equal(result.message, 'Hello [1].');
  assert.equal(result.sources[0].cited, true);
  assert.equal(events.length, 3);
});

for (const mode of ['stop', 'disconnect', 'failure']) {
  test(`stream ${mode} preserves partial text, excludes it from context, and ignores late chunks`, async () => {
    const store = new ConversationStore(':memory:');
    const conversation = store.create('doc');
    const controller = new AbortController();
    let late;
    const events = [];
    const service = new ChatService(store, async () => ({ documents: [{}], selectedDocument: {} }),
      async (_q, _d, _s, { onEvent }) => {
        late = onEvent;
        onEvent({ type: 'sources', sources: [{ id: 1, passage: 'Synthetic' }] });
        onEvent({ type: 'delta', text: 'Partial [1]' });
        if (mode === 'failure') throw Error('secret');
        if (mode === 'stop') service.stop(conversation.id, 'doc');
        else controller.abort();
        return new Promise(() => {});
      });
    try {
      const turnId = randomUUID();
      const result = await service.send(conversation.id, 'doc', { turnId, question: 'Question' }, controller.signal, (event) => events.push(event));
      assert.equal(result.turns[0].answer, 'Partial [1]');
      assert.equal(result.turns[0].status, mode === 'failure' ? 'failed' : 'stopped');
      assert.match(result.turns[0].citationNote, /Incomplete/);
      if (mode !== 'failure') assert.throws(() => late({ type: 'delta', text: 'late' }));
      service.answer = async (_q, _d, _s, { history }) => {
        assert.deepEqual(history, []);
        assert.equal(store.get(conversation.id, 'doc').turns[0].answer, '');
        return { message: 'Fresh', sources: [] };
      };
      const retried = await service.send(conversation.id, 'doc', { turnId, retry: true });
      assert.equal(retried.turns.length, 1);
      assert.equal(retried.turns[0].answer, 'Fresh');
    } finally { store.close(); }
  });
}
