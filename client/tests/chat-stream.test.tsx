import test from 'node:test';
import assert from 'node:assert/strict';
import { readChatStream } from '../lib/chat-stream';

test('stream parser handles split UTF-8 characters and combined event lines', async () => {
  const bytes = new TextEncoder().encode('{"type":"delta","text":"café"}\n{"type":"done"}\n');
  const response = new Response(new ReadableStream({ start(controller) {
    for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
    controller.close();
  } }), { headers: { 'Content-Type': 'application/x-ndjson' } });
  const events: Record<string, unknown>[] = [];
  await readChatStream(response, (event) => events.push(event));
  assert.equal(events[0].text, 'café');
  assert.equal(events[1].type, 'done');
});

test('stream parser rejects truncated streams and reports pre-stream API errors', async () => {
  await assert.rejects(readChatStream(new Response('{"type":"delta","text":"partial"}\n', {
    headers: { 'Content-Type': 'application/x-ndjson' },
  }), () => {}), /interrupted/);
  await assert.rejects(readChatStream(new Response('{"message":"Invalid question"}', { status: 400 }), () => {}), /Invalid question/);
});
