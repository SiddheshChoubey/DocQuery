import test from 'node:test';
import assert from 'node:assert/strict';
import { createAuthenticatedFetch } from '../lib/authenticated-fetch';

test('API fetch gets a fresh session token and preserves stream headers', async (t) => {
  const requests: RequestInit[] = [];
  t.mock.method(globalThis, 'fetch', async (_url: string, options: RequestInit) => { requests.push(options); return new Response('{}'); });
  let token = 'session-a';
  const api = createAuthenticatedFetch(async () => token);
  await api('/test', { headers: { Accept: 'application/x-ndjson' } });
  token = 'refreshed-a';
  await api('/test');
  assert.equal(new Headers(requests[0].headers).get('Authorization'), 'Bearer session-a');
  assert.equal(new Headers(requests[0].headers).get('Accept'), 'application/x-ndjson');
  assert.equal(new Headers(requests[1].headers).get('Authorization'), 'Bearer refreshed-a');
  await assert.rejects(createAuthenticatedFetch(async () => null)('/test'), /Sign in/);
  assert.equal(requests.length, 2);
});

test('account cleanup abort prevents a delayed token from sending an old request', async (t) => {
  let called = false;
  t.mock.method(globalThis, 'fetch', async () => { called = true; return new Response('{}'); });
  const controller = new AbortController();
  let release!: (token: string) => void;
  const api = createAuthenticatedFetch(() => new Promise((resolve) => { release = resolve; }));
  const response = api('/test', { signal: controller.signal });
  controller.abort(); release('old-session');
  await assert.rejects(response, { name: 'AbortError' });
  assert.equal(called, false);
});
