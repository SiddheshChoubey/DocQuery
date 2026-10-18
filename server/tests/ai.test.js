import test from 'node:test';
import assert from 'node:assert/strict';
import { PdfEmbeddings, createChatModel } from '../ai.js';

function setEnv(t, name, value) {
  const original = process.env[name];
  process.env[name] = value;
  t.after(() => {
    if (original === undefined) delete process.env[name];
    else process.env[name] = original;
  });
}

test('Gemini integration sends separate document vectors and a retrieval query', async (t) => {
  setEnv(t, 'GEMINI_API_KEY', 'test-key-not-real');
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url: String(url), body: JSON.parse(options.body) });
    return new Response(JSON.stringify({ embedding: { values: [1, 2, 3] } }), {
      headers: { 'Content-Type': 'application/json' },
    });
  });
  const progress = [];
  const embeddings = new PdfEmbeddings(async (completed, total) => progress.push({ completed, total }));
  assert.deepEqual(await embeddings.embedDocuments(['first', 'second']), [[1, 2, 3], [1, 2, 3]]);
  assert.deepEqual(await embeddings.embedQuery('question'), [1, 2, 3]);
  assert.deepEqual(progress, [{ completed: 1, total: 2 }, { completed: 2, total: 2 }]);
  assert.equal(requests.length, 3);
  assert.ok(requests.every((request) => request.url.includes('gemini-embedding-2:embedContent')));
  assert.deepEqual(requests.map((request) => request.body.content.parts[0].text), [
    'title: none | text: first',
    'title: none | text: second',
    'task: question answering | query: question',
  ]);
  assert.ok(requests.every((request) => !('taskType' in request.body)));
});

test('failed embeddings propagate instead of silently storing empty vectors', async (t) => {
  setEnv(t, 'GEMINI_API_KEY', 'test-key-not-real');
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({
    error: { message: 'Invalid API key', status: 'INVALID_ARGUMENT' },
  }), { status: 400, headers: { 'Content-Type': 'application/json' } }));
  const embeddings = new PdfEmbeddings();
  await assert.rejects(embeddings.embedDocuments(['text']), /Invalid API key/);
});

test('chat uses Gemini and preserves system and user messages', async (t) => {
  setEnv(t, 'GEMINI_API_KEY', 'test-key-not-real');
  setEnv(t, 'GEMINI_CHAT_MODEL', 'gemini-3.5-flash-lite');
  let request;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    request = { url: String(url), body: JSON.parse(options.body) };
    return new Response(JSON.stringify({ candidates: [{
      content: { role: 'model', parts: [{ text: 'Test answer' }] }, finishReason: 'STOP',
    }] }), { headers: { 'Content-Type': 'application/json' } });
  });
  const response = await createChatModel().invoke([['system', 'Use the PDF.'], ['human', 'Question?']]);
  assert.equal(response.content, 'Test answer');
  assert.ok(request.url.includes('gemini-3.5-flash-lite:generateContent'));
  assert.equal(request.body.systemInstruction.parts[0].text, 'Use the PDF.');
  assert.equal(request.body.contents[0].parts[0].text, 'Question?');
});

test('missing key gives an actionable configuration error', (t) => {
  setEnv(t, 'GEMINI_API_KEY', '');
  assert.throws(() => new PdfEmbeddings(), /server\/\.env/);
  assert.throws(() => createChatModel(), /server\/\.env/);
});
