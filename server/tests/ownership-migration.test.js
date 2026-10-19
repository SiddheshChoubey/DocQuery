import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readdir } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { DatabaseSync } from 'node:sqlite';
import { migrateOwnership } from '../migrate-ownership.js';

test('ownership migration previews, backs up, preserves metadata, and never reassigns owned data', async (t) => {
  const folder = await mkdtemp(path.join(tmpdir(), 'docquery-migration-'));
  const filename = path.join(folder, 'history.sqlite');
  const db = new DatabaseSync(filename);
  db.exec("CREATE TABLE conversations(id TEXT, ownerId TEXT); INSERT INTO conversations VALUES ('old', NULL), ('other', 'user_b')");
  db.close();
  const points = [{ id: 1, payload: { metadata: { documentId: 'old', source: 'uploads/old.pdf' } } },
    { id: 2, payload: { metadata: { documentId: 'other', ownerId: 'user_b' } } }];
  const jobs = [{ id: 'old', data: JSON.stringify({ documentId: 'old' }) }, { id: 'other', data: JSON.stringify({ ownerId: 'user_b' }) }];
  for (const job of jobs) { job.getState = async () => 'completed'; job.updateData = async (data) => { job.data = data; }; }
  let writes = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    const body = JSON.parse(options.body);
    if (url.endsWith('/scroll')) return new Response(JSON.stringify({ result: { points, next_page_offset: null } }));
    assert.equal(body.key, 'metadata');
    assert.deepEqual(body.points, [1]);
    points[0].payload.metadata.ownerId = body.payload.ownerId;
    writes++;
    return new Response('{}');
  });
  const args = { ownerId: 'user_a', queue: { getWorkers: async () => [], getJobs: async () => jobs }, filename, backupDirectory: path.join(folder, 'backups') };
  try {
    assert.deepEqual(await migrateOwnership(args), { chunks: 1, jobs: 1, conversations: 1 });
    assert.equal(writes, 0);
    const result = await migrateOwnership({ ...args, apply: true });
    assert.ok((await readdir(result.backupDirectory)).includes('conversations.sqlite'));
    assert.equal(points[0].payload.metadata.source, 'uploads/old.pdf');
    assert.equal(points[1].payload.metadata.ownerId, 'user_b');
    assert.equal(JSON.parse(jobs[0].data).ownerId, 'user_a');
    assert.equal(JSON.parse(jobs[1].data).ownerId, 'user_b');
    assert.deepEqual(await migrateOwnership({ ...args, apply: true }), { chunks: 0, jobs: 0, conversations: 0 });
    const reopened = new DatabaseSync(filename);
    assert.deepEqual(reopened.prepare('SELECT ownerId FROM conversations ORDER BY id').all().map((row) => row.ownerId), ['user_a', 'user_b']);
    reopened.close();
    await assert.rejects(migrateOwnership({ ...args, queue: { getWorkers: async () => [{}] } }), /Stop the indexing worker/);
  } finally { await rm(folder, { recursive: true }); }
});
