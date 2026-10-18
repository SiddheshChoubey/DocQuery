import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, rmdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { deleteDocument, storedPdfPath } from '../delete-document.js';
import { documentFromMetadata } from '../documents.js';

test('deleting a note removes only its retained job and exact vector filter', async (t) => {
  const document = documentFromMetadata({ documentId: 'a', fileName: 'Same title', sourceType: 'text' });
  const removed = [];
  const jobs = ['a', 'b'].map((documentId) => ({
    data: JSON.stringify({ documentId }), getState: async () => 'completed', remove: async () => removed.push(documentId),
  }));
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.match(url, /points\/delete\?wait=true$/);
    assert.deepEqual(JSON.parse(options.body), { filter: document.filter });
    return new Response('{}');
  });
  await deleteDocument(document, [document], { getJobs: async () => jobs });
  assert.deepEqual(removed, ['a']);
});

test('queued or active indexing blocks deletion before any mutation', async (t) => {
  const document = documentFromMetadata({ documentId: 'a' });
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => { throw Error('must not call'); });
  for (const state of ['waiting', 'active', 'delayed']) {
    const job = { data: JSON.stringify({ documentId: 'a' }), getState: async () => state, remove: async () => assert.fail('must not remove') };
    await assert.rejects(deleteDocument(document, [document], { getJobs: async () => [job] }), { status: 409 });
  }
  assert.equal(fetchMock.mock.callCount(), 0);
});

test('stored PDF validation rejects paths outside uploads and directories; missing PDFs are allowed', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'document-path-test-'));
  const uploads = path.join(root, 'uploads');
  await mkdir(uploads);
  const outside = path.join(root, 'keep.pdf');
  await writeFile(outside, 'keep this file');
  try {
    await assert.rejects(storedPdfPath(outside, uploads), { status: 409 });
    await assert.rejects(storedPdfPath(uploads, uploads), { status: 409 });
    assert.equal(await storedPdfPath(path.join(uploads, 'missing.pdf'), uploads), null);
    assert.equal(await readFile(outside, 'utf8'), 'keep this file');
  } finally {
    await rm(outside);
    await rmdir(uploads);
    await rmdir(root);
  }
});

test('legacy PDF cleanup deletes its file and surfaces Qdrant failures for retry', async (t) => {
  const uploads = fileURLToPath(new URL('../uploads/', import.meta.url));
  const folder = await mkdtemp(path.join(uploads, 'delete-test-'));
  const file = path.join(folder, '1792274133998-123-same.pdf');
  await writeFile(file, 'synthetic test input');
  const document = documentFromMetadata({ source: file });
  const queue = { getJobs: async () => [] };
  try {
    const mock = t.mock.method(globalThis, 'fetch', async (_url, options) => {
      assert.deepEqual(JSON.parse(options.body), { filter: document.filter });
      return new Response('', { status: 503 });
    });
    await assert.rejects(deleteDocument(document, [document], queue), /Unable to remove document vectors/);
    await assert.rejects(readFile(file), { code: 'ENOENT' });
    mock.mock.mockImplementation(async () => new Response('{}'));
    await deleteDocument(document, [document], queue);
  } finally {
    await rm(file, { force: true });
    await rmdir(folder);
  }
});
