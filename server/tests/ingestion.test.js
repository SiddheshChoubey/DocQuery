import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareDocuments, validateTextInput } from '../ingestion.js';
import { documentFromMetadata } from '../documents.js';
import { answerWithSources } from '../citations.js';

test('text input rejects blank, malformed and oversized values', () => {
  for (const body of [undefined, {}, { title: 42, text: 'notes' }, { title: ' ', text: 'notes' },
    { title: 'Notes', text: [] }, { title: 'Notes', text: '  ' },
    { title: 'a'.repeat(121), text: 'notes' }, { title: 'Notes', text: 'a'.repeat(50001) }]) {
    assert.equal(typeof validateTextInput(body), 'string');
  }
  assert.equal(validateTextInput({ title: 'a'.repeat(120), text: 'a'.repeat(50000) }), null);
});

test('notes are chunked, selectable by unique ID, and cited without PDF pages', async () => {
  const docs = await prepareDocuments({ sourceType: 'text', documentId: 'note-a', filename: 'Study notes', text: 'A useful study passage. '.repeat(200) });
  assert.ok(docs.length > 1);
  for (const doc of docs) {
    assert.ok(doc.pageContent.length <= 1500);
    assert.equal(doc.metadata.documentId, 'note-a');
    assert.equal(doc.metadata.sourceType, 'text');
    assert.equal(doc.metadata.loc?.pageNumber, undefined);
  }
  const selected = documentFromMetadata(docs[0].metadata);
  assert.equal(selected.filename, 'Study notes');
  assert.equal(selected.sourceType, 'text');
  const other = documentFromMetadata({ ...docs[0].metadata, documentId: 'note-b' });
  assert.notDeepEqual(selected.filter, other.filter);
  const model = { invoke: async (messages) => {
    assert.match(messages[0][1], /Study notes/);
    return { content: 'A useful study passage. [1]' };
  } };
  const answer = await answerWithSources(model, 'What is here?', docs.slice(0, 1), selected);
  assert.equal(answer.sources[0].sourceType, 'text');
  assert.equal(answer.sources[0].page, null);
  assert.equal(answer.sources[0].cited, true);
  assert.equal(answer.sources[0].passage, docs[0].pageContent);
});
