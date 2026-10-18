import test from 'node:test';
import assert from 'node:assert/strict';
import { answerWithSources, buildSources, citationNumbers } from '../citations.js';

const document = { id: 'selected-pdf', filename: 'Node Notes.pdf' };
const chunks = [
  { pageContent: 'Use fs.readFile to read a file.', metadata: { loc: { pageNumber: 12 }, source: 'uploads/internal-path.pdf' } },
  { pageContent: 'The callback receives an error and data.', metadata: { loc: { pageNumber: 13 } } },
];

test('source IDs preserve retrieval order, filenames, pages and exact original passages', () => {
  const sources = buildSources(chunks, document);
  assert.deepEqual(sources, [
    { id: 1, filename: 'Node Notes.pdf', page: 12, passage: chunks[0].pageContent },
    { id: 2, filename: 'Node Notes.pdf', page: 13, passage: chunks[1].pageContent },
  ]);
  assert.ok(!JSON.stringify(sources).includes('internal-path'));
});

test('missing or invalid page numbers stay unknown rather than being invented', () => {
  const docs = [undefined, 0, -1, '4'].map((pageNumber) => ({ pageContent: 'text', metadata: { loc: { pageNumber } } }));
  assert.ok(buildSources(docs, document).every((source) => source.page === null));
});

test('citation parsing supports repeated and grouped references, ignoring code indexes', () => {
  assert.deepEqual(citationNumbers('One [1]. Both [1, 2]. `items[9]`\n```js\nitems[8]\n```\nTwo [2].'), [1, 1, 2, 2]);
});

test('model receives the exact numbered sources returned to the UI', async () => {
  let sentMessages;
  const model = { invoke: async (messages) => {
    sentMessages = messages;
    return { content: 'Read a file with fs.readFile [1]. The callback takes error and data [2].' };
  } };
  const answer = await answerWithSources(model, 'How do I read a file?', chunks, document);
  assert.deepEqual(sentMessages[1], ['human', 'How do I read a file?']);
  assert.ok(sentMessages[0][1].includes(JSON.stringify(buildSources(chunks, document))));
  assert.ok(answer.sources.every((source) => source.cited));
  assert.equal(answer.citationNote, undefined);
  assert.equal(answer.sources[1].passage, chunks[1].pageContent);
});

test('unknown references are flagged and never remapped to an unrelated source', async () => {
  const model = { invoke: async () => ({ content: 'A claim [2]. Another claim [99].' }) };
  const answer = await answerWithSources(model, 'Question', chunks, document);
  assert.equal(answer.message, 'A claim [2]. Another claim [99].');
  assert.match(answer.citationNote, /unavailable references \(\[99\]\)/);
  assert.equal(answer.sources[0].cited, false);
  assert.equal(answer.sources[1].cited, true);
  assert.equal(answer.sources.length, 2);
});

test('uncited refusals keep original passages visible without adding fabricated citations', async () => {
  const model = { invoke: async () => ({ content: [{ type: 'text', text: 'The supplied passages do not contain the answer.' }] }) };
  const answer = await answerWithSources(model, 'Unrelated question', chunks, document);
  assert.match(answer.citationNote, /no inline references/);
  assert.ok(answer.sources.every((source) => !source.cited));
  assert.ok(!answer.message.includes('[1]'));
});
