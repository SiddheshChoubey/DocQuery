import { browser } from './dom-setup';
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { render, fireEvent, waitFor, cleanup } from '@testing-library/react';
import Chat from '../app/components/chat';

after(async () => { cleanup(); await browser.happyDOM.close(); });

test('saved chats reload, citations/copy work, failed turns retry, and stop/new/delete controls update history', { timeout: 10000 }, async (t) => {
  localStorage.clear();
  const seed = {
    id: 'saved-chat', documentId: 'doc-a', title: 'Saved question', updatedAt: '2026-10-18T10:00:00Z',
    turns: [{ id: 'saved-turn', question: 'Saved question', answer: 'Saved answer [1].', status: 'completed',
      sources: [{ id: 1, filename: 'Guide.pdf', page: 2, passage: 'Original saved source.', cited: true }] }],
  };
  const chats = new Map([[seed.id, seed]]);
  let release: (() => void) | undefined;
  let messageCalls = 0;
  const requestedModes: string[] = [];
  let copied = '';
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (value: string) => { copied = value; } } });
  const response = (data: object) => new Response(JSON.stringify(structuredClone(data)), { headers: { 'Content-Type': 'application/json' } });
  t.mock.method(globalThis, 'fetch', async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = new URL(String(input));
    const id = url.pathname.split('/')[2];
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    const method = options?.method || 'GET';
    if (!id && method === 'GET') return response({ conversations: [...chats.values()].filter((chat) => chat.documentId === url.searchParams.get('documentId')) });
    if (!id && method === 'POST') {
      const chat = { ...seed, id: 'new-chat', title: 'New chat', turns: [] };
      chats.set(chat.id, chat); return response({ conversation: chat });
    }
    const chat = chats.get(id);
    assert.ok(chat, `Known fake conversation ${id}`);
    if (url.pathname.endsWith('/stop')) {
      Object.assign(chat.turns.at(-1)!, { status: 'stopped', error: 'Answer stopped.' });
      release?.(); return response({ message: 'Stop requested.' });
    }
    if (url.pathname.endsWith('/messages')) {
      messageCalls++;
      requestedModes.push(body.answerMode);
      const turn = { id: body.turnId, question: body.question, answer: '', status: 'pending', sources: [], answerMode: body.answerMode };
      if (body.retry) chat.turns[chat.turns.length - 1] = turn;
      else chat.turns.push(turn);
      const encoder = new TextEncoder();
      return new Response(new ReadableStream({
        start(controller) {
          const emit = (event: object) => controller.enqueue(encoder.encode(JSON.stringify(event) + '\n'));
          emit({ type: 'sources', sources: [] });
          if (body.question === 'New topic') {
            turn.answer = 'Partial streamed answer.';
            emit({ type: 'delta', text: turn.answer });
            release = () => { emit({ type: 'done', conversation: chat }); controller.close(); };
          } else {
            if (messageCalls === 1) Object.assign(turn, { status: 'failed', error: 'Temporary failure. Retry.' });
            else Object.assign(turn, { status: 'completed', answer: 'Retried answer.' });
            emit({ type: 'done', conversation: chat }); controller.close();
          }
        },
      }), { headers: { 'Content-Type': 'application/x-ndjson' } });
    }
    if (method === 'DELETE') { chats.delete(id); return response({ message: 'Deleted' }); }
    return response({ conversation: chat });
  });
  const props = { uploadBusy: false, documentId: 'doc-a', documentName: 'Guide.pdf', documentsLoading: false };
  let view = render(<Chat {...props} />);
  try {
    await view.findByText('Saved answer', { exact: false });
    const modeButton = view.getByRole('button', { name: 'Detailed answers' });
    assert.equal(modeButton.getAttribute('aria-pressed'), 'false');
    fireEvent.click(modeButton);
    assert.equal(modeButton.getAttribute('aria-pressed'), 'true');
    fireEvent.click(view.getByRole('link', { name: 'Show source 1: Guide.pdf, page 2' }));
    await waitFor(() => assert.equal(view.container.querySelector('details')?.open, true));
    fireEvent.click(view.getByRole('button', { name: 'Copy answer' }));
    await waitFor(() => assert.equal(copied, 'Saved answer [1].'));
    fireEvent.change(view.getByRole('textbox', { name: 'Question' }), { target: { value: 'Explain more' } });
    fireEvent.click(view.getByRole('button', { name: 'Send' }));
    await view.findByText('Temporary failure. Retry.');
    fireEvent.click(modeButton);
    fireEvent.click(view.getByRole('button', { name: 'Retry' }));
    await view.findByText('Retried answer.');
    assert.deepEqual(requestedModes, ['detailed', 'detailed']);
    assert.equal(view.getAllByText('Explain more').length, 1);
    assert.equal(chats.get(seed.id)?.turns.length, 2);
    fireEvent.click(view.getByRole('button', { name: 'New chat' }));
    assert.ok(!view.queryByText('Saved question'));
    fireEvent.change(view.getByRole('textbox', { name: 'Question' }), { target: { value: 'New topic' } });
    fireEvent.click(view.getByRole('button', { name: 'Send' }));
    await waitFor(() => assert.equal(chats.get('new-chat')?.turns.at(-1)?.status, 'pending'));
    await view.findByText('Partial streamed answer.');
    await view.findByText(/Generating/);
    assert.deepEqual(requestedModes, ['detailed', 'detailed', 'balanced']);
    assert.equal((view.getByRole('button', { name: 'Detailed answers' }) as HTMLButtonElement).disabled, true);
    fireEvent.click(view.getByRole('button', { name: 'Stop' }));
    await view.findByText('Answer stopped.');
    await waitFor(() => assert.equal((view.getByRole('combobox', { name: 'Conversation' }) as HTMLSelectElement).disabled, false));
    fireEvent.change(view.getByRole('combobox', { name: 'Conversation' }), { target: { value: seed.id } });
    await view.findByText('Retried answer.');
    view.unmount();
    view = render(<Chat {...props} />);
    await view.findByText('Retried answer.');
    fireEvent.click(view.getByRole('button', { name: 'Delete chat' }));
    fireEvent.click(view.getByRole('button', { name: 'Confirm delete' }));
    await waitFor(() => assert.equal(chats.has(seed.id), false));
    await waitFor(() => assert.ok(!view.queryByText('Retried answer.')));
    view.unmount();
    view = render(<Chat {...props} documentId="doc-b" />);
    await waitFor(() => assert.ok(!view.queryByText('Loading conversation…')));
    assert.ok(!view.queryByText('New topic'));
  } finally { view.unmount(); cleanup(); }
});

