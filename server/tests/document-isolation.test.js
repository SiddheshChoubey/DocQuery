import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { QdrantVectorStore } from '@langchain/qdrant';
import { listDocuments } from '../documents.js';
import { deleteDocument } from '../delete-document.js';

// Opt-in integration test using an isolated, temporary collection and artificial
// vectors. No PDFs, API keys, or external AI calls are involved.
test('Qdrant retrieval and deletion isolate documents even with identical names and vectors', {
  skip: !process.env.QDRANT_TEST_URL,
}, async () => {
  const config = { url: process.env.QDRANT_TEST_URL, collectionName: `document-selection-test-${randomUUID()}` };
  const endpoint = `${config.url}/collections/${config.collectionName}`;
  const create = await fetch(endpoint, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ vectors: { size: 2, distance: 'Cosine' } }),
  });
  assert.ok(create.ok, 'Temporary collection created');
  try {
    const store = await QdrantVectorStore.fromExistingCollection({ embedQuery: async () => [1, 0] }, config);
    await store.addVectors([[1, 0], [1, 0], [1, 0]], [
      { pageContent: 'Document A only', metadata: { documentId: 'a', fileName: 'same.pdf', ownerId: 'user_a' } },
      { pageContent: 'Document B only', metadata: { documentId: 'b', fileName: 'same.pdf', ownerId: 'user_b' } },
      { pageContent: 'Legacy document only', metadata: { source: 'uploads/123-456-same.pdf' } },
    ]);
    const documents = await listDocuments(config);
    assert.equal(documents.length, 3);
    assert.deepEqual((await listDocuments(config, 'user_a')).map((doc) => doc.id), ['a']);
    assert.deepEqual((await listDocuments(config, 'user_b')).map((doc) => doc.id), ['b']);
    assert.deepEqual(await listDocuments(config, 'user_empty'), []);
    const wrongOwner = { must: [{ key: 'metadata.documentId', match: { value: 'a' } }, { key: 'metadata.ownerId', match: { value: 'user_b' } }] };
    assert.deepEqual(await store.asRetriever({ k: 5, filter: wrongOwner }).invoke('question'), []);
    for (const document of documents) {
      const results = await store.asRetriever({ k: 10, filter: document.filter }).invoke('any question');
      assert.equal(results.length, 1);
      const expected = document.id === 'a' ? 'Document A only' : document.id === 'b' ? 'Document B only' : 'Legacy document only';
      assert.equal(results[0].pageContent, expected);
    }
    const missing = await store.asRetriever({ k: 10, filter: { must: [{ key: 'metadata.documentId', match: { value: 'missing' } }] } }).invoke('question');
    assert.deepEqual(missing, []);
    const selected = documents.find((document) => document.id === 'b');
    await deleteDocument(selected, documents, { getJobs: async () => [] }, config);
    const remaining = await listDocuments(config);
    assert.equal(remaining.length, 2);
    assert.ok(remaining.some((document) => document.id === 'a'), 'Same-name document is preserved');
    assert.ok(!remaining.some((document) => document.id === 'b'), 'Deleted document is no longer listed');
    assert.deepEqual(await store.asRetriever({ k: 10, filter: selected.filter }).invoke('question'), []);
  } finally {
    const cleanup = await fetch(endpoint, { method: 'DELETE' });
    assert.ok(cleanup.ok, 'Only the temporary test collection is removed');
  }
});
