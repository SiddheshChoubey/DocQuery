import { DatabaseSync, backup } from 'node:sqlite';
import { mkdir, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import net from 'node:net';
import { Queue } from 'bullmq';
import { vectorStoreConfig } from './ai.js';
import { requireOwner } from './conversations.js';

// Run only with the API and worker stopped. No vectors or PDF files are rewritten.
export async function migrateOwnership({ ownerId, queue, config = vectorStoreConfig,
  filename = fileURLToPath(new URL('./data/conversations.sqlite', import.meta.url)),
  backupDirectory = fileURLToPath(new URL('../.local-backups', import.meta.url)), apply = false }) {
  requireOwner(ownerId);
  if ((await queue.getWorkers()).length) throw new Error('Stop the indexing worker before migration.');
  const jobs = await queue.getJobs(['waiting', 'paused', 'active', 'delayed', 'prioritized', 'waiting-children', 'completed', 'failed']);
  for (const job of jobs) if (await job.getState() === 'active') throw new Error('Wait for active jobs to stop before migration.');
  const unownedJobs = jobs.filter((job) => !JSON.parse(job.data).ownerId);
  const base = `${config.url}/collections/${encodeURIComponent(config.collectionName)}/points`;
  const points = [];
  let offset;
  do {
    const response = await fetch(`${base}/scroll`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ limit: 256, offset, with_vector: false, with_payload: ['metadata'] }), signal: AbortSignal.timeout(10000) });
    if (response.status === 404) break;
    if (!response.ok) throw new Error('Could not inspect Qdrant for migration.');
    const { result } = await response.json();
    points.push(...result.points.filter((point) => !point.payload?.metadata?.ownerId));
    offset = result.next_page_offset;
  } while (offset !== null && offset !== undefined);
  const db = existsSync(filename) ? new DatabaseSync(filename) : null;
  try {
    const hasOwner = db?.prepare('PRAGMA table_info(conversations)').all().some((column) => column.name === 'ownerId');
    const chats = db ? Number(db.prepare(`SELECT count(*) AS count FROM conversations ${hasOwner ? "WHERE ownerId IS NULL OR ownerId = ''" : ''}`).get().count) : 0;
    const counts = { chunks: points.length, jobs: unownedJobs.length, conversations: chats };
    if (!apply || !Object.values(counts).some(Boolean)) return counts;
    const directory = path.join(backupDirectory, `ownership-${Date.now()}`);
    await mkdir(directory, { recursive: true });
    await writeFile(path.join(directory, 'ownership.json'), JSON.stringify({ ownerId, points, jobs: unownedJobs.map((job) => ({ id: job.id, data: job.data })) }));
    if (db) await backup(db, path.join(directory, 'conversations.sqlite'));
    for (let i = 0; i < points.length; i += 256) {
      const response = await fetch(`${base}/payload?wait=true`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'metadata', payload: { ownerId }, points: points.slice(i, i + 256).map((point) => point.id) }), signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error('Qdrant migration interrupted. Keep services stopped and rerun with the same owner.');
    }
    for (const job of unownedJobs) await job.updateData(JSON.stringify({ ...JSON.parse(job.data), ownerId }));
    if (db) {
      if (!hasOwner) db.exec('ALTER TABLE conversations ADD COLUMN ownerId TEXT');
      db.prepare("UPDATE conversations SET ownerId = ? WHERE ownerId IS NULL OR ownerId = ''").run(ownerId);
    }
    return { ...counts, backupDirectory: directory };
  } finally { db?.close(); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const queue = new Queue('file-upload-queue', { connection: { host: 'localhost', port: 6379 } });
  try {
    const listening = await new Promise((resolve) => {
      const socket = net.createConnection({ host: '127.0.0.1', port: 8000 });
      socket.once('connect', () => { socket.destroy(); resolve(true); });
      socket.once('error', () => resolve(false));
    });
    if (listening) throw new Error('Stop the API on port 8000 before migration.');
    const result = await migrateOwnership({ ownerId: process.argv[2], queue, apply: process.argv.includes('--apply') });
    console.log(JSON.stringify(result));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  } finally { await queue.close(); }
}
