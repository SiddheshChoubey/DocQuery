import test from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync, sign, randomUUID } from 'node:crypto';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createRequireAuth } from '../auth.js';
import { createApp } from '../app.js';
import { ConversationStore } from '../conversations.js';
import { ChatService } from '../chat-service.js';
import { documentFromMetadata, listDocuments } from '../documents.js';
import { prepareDocuments } from '../ingestion.js';

const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const issuer = 'https://synthetic.clerk.accounts.dev';
const origin = 'http://localhost:3000';
function token(owner = 'user_a', claims = {}) {
  const now = Math.floor(Date.now() / 1000);
  const header = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
  const payload = Buffer.from(JSON.stringify({ sub: owner, sid: 'sess_test', iss: issuer, azp: origin,
    iat: now, nbf: now - 5, exp: now + 120, ...claims })).toString('base64url');
  const data = `${header}.${payload}`;
  return `${data}.${sign('RSA-SHA256', Buffer.from(data), privateKey).toString('base64url')}`;
}

test('real signed session verification and all HTTP operations reject cross-account access', { timeout: 20000 }, async () => {
  const store = new ConversationStore(':memory:');
  const folder = await mkdtemp(path.join(tmpdir(), 'docquery-upload-'));
  const documents = ['a', 'b'].map((id) => documentFromMetadata({ documentId: id, fileName: 'Same.pdf', ownerId: `user_${id}` }));
  const jobs = new Map();
  const queue = { async add(_name, data) { const job = { id: randomUUID(), data, progress: 50, getState: async () => 'completed' }; jobs.set(job.id, job); return job; }, async getJob(id) { return jobs.get(id); } };
  let deleted = 0;
  let hold = false;
  let started;
  let ready;
  const service = new ChatService(store, async (id, _query, _signal, _count, owner) => {
    assert.equal(owner, 'user_a'); assert.equal(id, 'a');
    return { documents: [{ pageContent: 'Synthetic' }], selectedDocument: documents[0] };
  }, async (_q, _docs, _doc, { onEvent }) => {
    if (hold) { started(); return new Promise(() => {}); }
    onEvent?.({ type: 'delta', text: 'Synthetic answer' });
    return { message: 'Synthetic answer', sources: [] };
  });
  const requireAuth = createRequireAuth({ jwtKey: publicKey.export({ type: 'spki', format: 'pem' }), issuer, authorizedParties: [origin] });
  const app = createApp({ requireAuth, clientOrigins: [origin], conversations: store, chatService: service, queue,
    listDocuments: async (_config, owner) => owner ? documents.filter((doc) => doc.ownerId === owner) : documents,
    deleteDocument: async (doc) => { assert.equal(doc.ownerId, 'user_a'); deleted++; }, uploadDirectory: folder });
  const server = await new Promise((resolve) => { const instance = app.listen(0, '127.0.0.1', () => resolve(instance)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const request = async (url, owner = 'user_a', method = 'GET', body, extra = {}) => {
    const headers = { ...(owner ? { Authorization: `Bearer ${token(owner)}` } : {}), ...extra };
    if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
    const response = await fetch(base + url, { method, headers, body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined });
    const text = await response.text();
    const data = response.headers.get('content-type')?.includes('ndjson') ? JSON.parse(text.trim().split('\n').at(-1)) : JSON.parse(text);
    return { status: response.status, data };
  };
  try {
    for (const url of ['/documents', '/conversations?documentId=a', '/upload/status/unknown', '/chat?documentId=a&message=hello']) {
      assert.equal((await request(url, null)).status, 401);
    }
    for (const bad of ['bad.token', token('user_a', { exp: 1 }), token('user_a', { iss: 'https://another-instance.test' }), token('user_a', { azp: 'https://evil.test' }), token('user_a', { sid: null }), token('user_a', { azp: null })]) {
      assert.equal((await request('/documents', null, 'GET', undefined, { Authorization: `Bearer ${bad}` })).status, 401);
    }
    assert.equal((await request('/documents')).data.documents[0].id, 'a');
    assert.equal((await request('/documents', 'user_b')).data.documents[0].id, 'b');
    assert.deepEqual((await request('/documents', 'user_empty')).data.documents, []);
    assert.equal((await request('/chat?documentId=a&message=hello', 'user_b')).status, 404);
    assert.equal((await request('/documents/a', 'user_b', 'DELETE')).status, 404);
    assert.equal(deleted, 0);
    const uploaded = await request('/upload/text', 'user_a', 'POST', { title: 'Note', text: 'Synthetic', ownerId: 'user_b' });
    assert.equal(uploaded.status, 202);
    assert.equal(JSON.parse(jobs.get(uploaded.data.jobId).data).ownerId, 'user_a');
    assert.equal((await request(`/upload/status/${uploaded.data.jobId}`)).status, 200);
    assert.equal((await request(`/upload/status/${uploaded.data.jobId}`, 'user_b')).status, 404);
    const form = new FormData(); form.append('pdf', new Blob(['synthetic fixture'], { type: 'application/pdf' }), 'fixture.pdf');
    assert.equal((await request('/upload/pdf', null, 'POST', form)).status, 401);
    const pdf = await request('/upload/pdf', 'user_a', 'POST', form);
    assert.equal(pdf.status, 202);
    assert.equal(JSON.parse(jobs.get(pdf.data.jobId).data).ownerId, 'user_a');
    assert.equal((await request('/conversations', 'user_b', 'POST', { documentId: 'a' })).status, 404);
    const created = await request('/conversations', 'user_a', 'POST', { documentId: 'a', ownerId: 'user_b' });
    const id = created.data.conversation.id;
    assert.equal(created.data.conversation.ownerId, 'user_a');
    assert.deepEqual((await request('/conversations?documentId=a', 'user_b')).data.conversations, []);
    assert.equal((await request(`/conversations/${id}?documentId=a`, 'user_b')).status, 404);
    for (const retry of [false, true]) {
      assert.equal((await request(`/conversations/${id}/messages`, 'user_b', 'POST', { documentId: 'a', question: 'Intrusion', turnId: randomUUID(), retry })).status, 404);
    }
    const input = { documentId: 'a', question: 'Question', turnId: randomUUID() };
    const complete = await request(`/conversations/${id}/messages`, 'user_a', 'POST', input, { Accept: 'application/x-ndjson' });
    assert.equal(complete.data.conversation.turns[0].status, 'completed');
    hold = true; ready = new Promise((resolve) => { started = resolve; });
    const next = { ...input, turnId: randomUUID() };
    const pending = request(`/conversations/${id}/messages`, 'user_a', 'POST', next);
    await ready;
    assert.equal((await request(`/conversations/${id}/stop`, 'user_b', 'POST', { documentId: 'a' })).status, 404);
    assert.equal((await request(`/conversations/${id}?documentId=a`, 'user_b', 'DELETE')).status, 404);
    assert.ok(service.active.has(id));
    assert.equal((await request(`/conversations/${id}/stop`, 'user_a', 'POST', { documentId: 'a' })).status, 200);
    assert.equal((await pending).data.conversation.turns[1].status, 'stopped');
    hold = false;
    assert.equal((await request(`/conversations/${id}/messages`, 'user_a', 'POST', { ...next, retry: true })).data.conversation.turns[1].status, 'completed');
    assert.equal((await request(`/conversations/${id}?documentId=a`, 'user_a', 'DELETE')).status, 200);
    const other = store.create('a', 'user_b');
    assert.equal((await request('/documents/a', 'user_a', 'DELETE')).status, 200);
    assert.equal(store.get(other.id, 'a', 'user_b').id, other.id);
  } finally {
    server.closeAllConnections(); await new Promise((resolve) => server.close(resolve));
    store.close(); await rm(folder, { recursive: true });
  }
});

test('Qdrant owner filter survives document selection and ingestion', async (t) => {
  const chunks = await prepareDocuments({ ownerId: 'user_a', documentId: 'a', sourceType: 'text', filename: 'Synthetic', text: 'Evidence' });
  assert.equal(chunks[0].metadata.ownerId, 'user_a');
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    const body = JSON.parse(options.body);
    assert.deepEqual(body.filter, { must: [{ key: 'metadata.ownerId', match: { value: 'user_a' } }] });
    return new Response(JSON.stringify({ result: { points: [
      { payload: { metadata: chunks[0].metadata } },
      { payload: { metadata: { documentId: 'b', ownerId: 'user_b' } } },
      { payload: { metadata: { documentId: 'legacy' } } },
    ], next_page_offset: null } }));
  });
  const docs = await listDocuments(undefined, 'user_a');
  assert.equal(docs.length, 1);
  assert.deepEqual(docs[0].filter.must, [
    { key: 'metadata.documentId', match: { value: 'a' } },
    { key: 'metadata.ownerId', match: { value: 'user_a' } },
  ]);
});

test('SQLite refuses missing owners and hides legacy ownerless conversations', () => {
  const store = new ConversationStore(':memory:');
  try {
    assert.throws(() => store.list('a'), { status: 401 });
    const legacy = store.create('a', 'user_a');
    store.db.prepare('UPDATE conversations SET ownerId = NULL WHERE id = ?').run(legacy.id);
    assert.deepEqual(store.list('a', 'user_a'), []);
    assert.throws(() => store.get(legacy.id, 'a', 'user_a'), { status: 404 });
  } finally { store.close(); }
});
