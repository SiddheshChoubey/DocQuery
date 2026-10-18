import test from 'node:test';
import assert from 'node:assert/strict';
import { documentFromMetadata, legacyDocumentId, listDocuments } from '../documents.js';

test('same filenames from separate uploads remain separate documents', () => {
  const first = documentFromMetadata({ documentId: 'a', fileName: 'notes.pdf', source: 'uploads/a.pdf' });
  const second = documentFromMetadata({ documentId: 'b', fileName: 'notes.pdf', source: 'uploads/b.pdf' });
  assert.notEqual(first.id, second.id);
  assert.deepEqual(first.filter, { must: [{ key: 'metadata.documentId', match: { value: 'a' } }] });
  assert.notDeepEqual(first.filter, second.filter);
});

test('legacy selection uses exact paths, keeping duplicate filenames isolated', () => {
  const source = 'uploads\\1792274133998-532279793-mead.pdf';
  const first = documentFromMetadata({ source });
  assert.equal(first.filename, 'mead.pdf');
  assert.equal(first.id, legacyDocumentId(source));
  assert.deepEqual(first.filter, { must: [{ key: 'metadata.source', match: { value: source } }] });
  assert.notEqual(first.id, documentFromMetadata({ source: 'uploads/1792274181870-662800962-mead.pdf' }).id);
  assert.equal(documentFromMetadata({}), null);
});

test('document listing reads every page and counts chunks without reading text or vectors', async (t) => {
  const requests = [];
  const pages = [
    { points: [{ payload: { metadata: { documentId: 'a', fileName: 'A.pdf' } } }], next_page_offset: 'next' },
    { points: [
      { payload: { metadata: { documentId: 'a', fileName: 'A.pdf' } } },
      { payload: { metadata: { documentId: 'b', fileName: 'B.pdf' } } },
    ], next_page_offset: null },
  ];
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    requests.push(JSON.parse(options.body));
    return new Response(JSON.stringify({ result: pages.shift() }));
  });
  const documents = await listDocuments();
  assert.deepEqual(documents.map(({ id, chunkCount }) => ({ id, chunkCount })), [
    { id: 'a', chunkCount: 2 }, { id: 'b', chunkCount: 1 },
  ]);
  assert.equal(requests[1].offset, 'next');
  assert.ok(requests.every((request) => request.with_vector === false));
  assert.deepEqual(requests[0].with_payload, ['metadata.documentId', 'metadata.fileName', 'metadata.source', 'metadata.sourceType', 'metadata.uploadedAt']);
});

test('upload dates use saved metadata or the legacy upload filename; unknown dates stay unknown', () => {
  assert.equal(documentFromMetadata({ documentId: 'a', uploadedAt: '2026-10-18T10:00:00Z' }).uploadedAt, '2026-10-18T10:00:00.000Z');
  assert.equal(documentFromMetadata({ source: 'uploads/1792274133998-123-notes.pdf' }).uploadedAt, new Date(1792274133998).toISOString());
  assert.equal(documentFromMetadata({ documentId: 'a' }).uploadedAt, null);
  assert.equal(documentFromMetadata({ documentId: 'a', uploadedAt: 'invalid' }).uploadedAt, null);
});

test('empty collection is an empty list; service failures are not hidden as empty lists', async (t) => {
  const mock = t.mock.method(globalThis, 'fetch', async () => new Response('', { status: 404 }));
  assert.deepEqual(await listDocuments(), []);
  mock.mock.mockImplementation(async () => new Response('', { status: 503 }));
  await assert.rejects(listDocuments(), /Unable to list/);
});
