'use client';

import * as React from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import CitedAnswer, { type Source } from './cited-answer';
import { readChatStream } from '@/lib/chat-stream';

const API = 'http://localhost:8000/conversations';
interface Turn {
  id: string;
  question: string;
  answer: string;
  sources: Source[];
  citationNote?: string;
  status: 'pending' | 'completed' | 'failed' | 'stopped';
  error?: string;
}
interface ConversationSummary {
  id: string;
  title: string;
  updatedAt: string;
}
interface Conversation extends ConversationSummary {
  turns: Turn[];
}

async function api(url: string, options?: RequestInit) {
  const response = await fetch(url, { cache: 'no-store', ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || 'The request failed. Try again.');
  return data;
}

export default function ConversationChat({ uploadBusy, documentId, documentName, documentsLoading }: {
  uploadBusy: boolean;
  documentId: string;
  documentName?: string;
  documentsLoading: boolean;
}) {
  const [message, setMessage] = React.useState('');
  const [conversations, setConversations] = React.useState<ConversationSummary[]>([]);
  const [conversation, setConversation] = React.useState<Conversation | null>(null);
  const [loading, setLoading] = React.useState(Boolean(documentId));
  const [sending, setSending] = React.useState(false);
  const [stopping, setStopping] = React.useState(false);
  const [error, setError] = React.useState('');
  const [copied, setCopied] = React.useState('');
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const [nearBottom, setNearBottom] = React.useState(true);
  const request = React.useRef<AbortController | null>(null);
  const navigation = React.useRef<AbortController | null>(null);
  const activeId = React.useRef('');
  const viewport = React.useRef<HTMLDivElement>(null);
  const storageKey = `docquery-conversation-${documentId}`;
  const query = `documentId=${encodeURIComponent(documentId)}`;
  const pending = conversation?.turns.some((turn) => turn.status === 'pending') || false;
  const busy = loading || sending || stopping || pending;
  const disabled = busy || uploadBusy || documentsLoading || !documentId;

  function remember(id: string) {
    try { localStorage.setItem(storageKey, id); } catch { /* Optional browser preference. */ }
  }

  const refreshList = React.useCallback(async (signal?: AbortSignal) => {
    const data = await api(`${API}?${query}`, { signal });
    if (!signal?.aborted) setConversations(data.conversations);
    return data.conversations as ConversationSummary[];
  }, [query]);

  React.useEffect(() => {
    if (!documentId) return;
    const controller = new AbortController();
    navigation.current = controller;
    void (async () => {
      try {
        const list = await refreshList(controller.signal);
        let saved: string | null = null;
        try { saved = localStorage.getItem(storageKey); } catch { /* Optional browser preference. */ }
        const id = saved === '' ? '' : list.find((item) => item.id === saved)?.id || list[0]?.id || '';
        if (id) {
          const data = await api(`${API}/${id}?${query}`, { signal: controller.signal });
          if (!controller.signal.aborted) { setConversation(data.conversation); activeId.current = id; }
        }
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Could not load chat history.');
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => { controller.abort(); request.current?.abort(); navigation.current?.abort(); };
  }, [documentId, query, storageKey, refreshList]);

  // Another tab or a page refresh may leave a request still being resolved.
  React.useEffect(() => {
    if (!pending || sending || !conversation) return;
    const controller = new AbortController();
    const timer = setInterval(() => {
      void api(`${API}/${conversation.id}?${query}`, { signal: controller.signal })
        .then((data) => { if (!controller.signal.aborted) setConversation(data.conversation); })
        .catch(() => { if (!controller.signal.aborted) setError('Could not refresh the active answer. Check the backend.'); });
    }, 2000);
    return () => { clearInterval(timer); controller.abort(); };
  }, [pending, sending, conversation, query]);

  React.useEffect(() => {
    if (nearBottom && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [conversation, sending, nearBottom]);

  async function openConversation(id: string) {
    if (busy) return;
    setError('');
    setConfirmDelete(false);
    setNearBottom(true);
    setMessage('');
    if (!id) { setConversation(null); activeId.current = ''; remember(''); return; }
    navigation.current?.abort();
    const controller = new AbortController();
    navigation.current = controller;
    setLoading(true);
    try {
      const data = await api(`${API}/${id}?${query}`, { signal: controller.signal });
      if (!controller.signal.aborted) { setConversation(data.conversation); activeId.current = id; remember(id); }
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Could not open this conversation.');
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  async function send(retryTurn?: Turn) {
    if (disabled || (!retryTurn && !message.trim())) return;
    const question = retryTurn?.question || message.trim();
    const turnId = retryTurn?.id || crypto.randomUUID();
    const controller = new AbortController();
    request.current = controller;
    setSending(true);
    setError('');
    setConfirmDelete(false);
    setNearBottom(true);
    if (!retryTurn) setMessage('');
    let id = conversation?.id || '';
    try {
      if (!id) {
        const data = await api(API, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ documentId }), signal: controller.signal });
        id = data.conversation.id;
        activeId.current = id;
        remember(id);
        setConversation(data.conversation);
      }
      const optimistic: Turn = { id: turnId, question, answer: '', sources: [], status: 'pending' };
      setConversation((current) => current && ({ ...current, turns: retryTurn
        ? current.turns.map((turn) => turn.id === turnId ? optimistic : turn)
        : [...current.turns, optimistic] }));
      const response = await fetch(`${API}/${id}/messages`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/x-ndjson' },
        body: JSON.stringify({ documentId, turnId, question, retry: Boolean(retryTurn) }), signal: controller.signal,
      });
      await readChatStream(response, (event) => {
        if (controller.signal.aborted || request.current !== controller) return;
        if (event.type === 'done') setConversation(event.conversation as Conversation);
        else if (event.type === 'sources' || event.type === 'delta') {
          setConversation((current) => current && ({ ...current, turns: current.turns.map((turn) => {
            if (turn.id !== turnId) return turn;
            return event.type === 'sources' ? { ...turn, sources: event.sources as Source[] }
              : { ...turn, answer: turn.answer + String(event.text) };
          }) }));
        }
      });
      if (!controller.signal.aborted) {
        await refreshList(controller.signal);
      }
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof Error ? err.message : 'Unable to reach the backend.');
        // Check what was actually saved before offering a retry; don't duplicate a turn.
        try {
          if (!id) { setMessage(question); return; }
          const data = await api(`${API}/${id}?${query}`, { signal: controller.signal });
          setConversation(data.conversation);
          if (!data.conversation.turns.some((turn: Turn) => turn.id === turnId)) setMessage(question);
        } catch {
          setConversation((current) => current && ({ ...current, turns: current.turns.filter((turn) => turn.id !== turnId) }));
          setMessage(question);
        }
      }
    } finally {
      if (request.current === controller) { setSending(false); request.current = null; }
    }
  }

  async function stop() {
    setStopping(true);
    const controller = request.current;
    try {
      const id = activeId.current;
      if (id) await api(`${API}/${id}/stop`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ documentId }) });
      controller?.abort();
      if (id) {
        const data = await api(`${API}/${id}?${query}`);
        setConversation(data.conversation);
        await refreshList();
      }
    } catch (err) {
      controller?.abort();
      setError(err instanceof Error ? err.message : 'Could not confirm that the answer stopped.');
    } finally {
      if (request.current === controller) request.current = null;
      setSending(false);
      setStopping(false);
    }
  }

  async function deleteConversation() {
    if (!conversation || disabled) return;
    setLoading(true);
    setError('');
    try {
      await api(`${API}/${conversation.id}?${query}`, { method: 'DELETE' });
      setConversation(null); activeId.current = ''; remember(''); setConfirmDelete(false);
      await refreshList();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete this conversation.');
    } finally { setLoading(false); }
  }

  async function copyAnswer(turn: Turn) {
    try { await navigator.clipboard.writeText(turn.answer); setCopied(turn.id); }
    catch { setError('Could not copy the answer. Select the text and copy it manually.'); }
  }

  return <section className="h-screen min-w-0 flex flex-col gap-4 p-4" aria-label="Document chat">
    <header className="space-y-3">
      <h2 className="font-semibold break-words">{documentName ? `Chat with ${documentName}` : 'Select an indexed document to start chatting.'}</h2>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="conversation-selection" className="text-sm">Conversation</label>
        <select id="conversation-selection" value={conversation?.id || ''} disabled={disabled}
          onChange={(event) => void openConversation(event.target.value)} className="min-w-0 max-w-full flex-1 rounded border bg-background p-2 text-sm">
          <option value="">New conversation</option>
          {conversations.map((item) => <option key={item.id} value={item.id}>{item.title} · {new Date(item.updatedAt).toLocaleString()}</option>)}
          {conversation && !conversations.some((item) => item.id === conversation.id) && <option value={conversation.id}>{conversation.title}</option>}
        </select>
        <Button variant="outline" disabled={disabled} onClick={() => void openConversation('')}>New chat</Button>
        <Button variant="outline" disabled={disabled || !conversation} onClick={() => setConfirmDelete(true)}>Delete chat</Button>
      </div>
      {confirmDelete && <div className="flex flex-wrap items-center gap-3 text-sm">
        <p>Delete this conversation and its saved messages?</p>
        <Button variant="destructive" size="sm" disabled={disabled} onClick={() => void deleteConversation()}>Confirm delete</Button>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => setConfirmDelete(false)}>Cancel</Button>
      </div>}
    </header>
    <div ref={viewport} className="min-h-0 flex-1 overflow-y-auto space-y-4" onScroll={(event) => {
      const element = event.currentTarget;
      setNearBottom(element.scrollHeight - element.scrollTop - element.clientHeight < 100);
    }}>
      {loading && <p role="status">Loading conversation…</p>}
      {!loading && !conversation?.turns.length && <p className="text-sm text-muted-foreground">Ask about the selected document. Your conversation is saved on this computer, and recent messages provide context for follow-ups.</p>}
      {conversation?.turns.map((turn, index) => <div key={turn.id} className="space-y-3">
        <article className="rounded-lg border p-3"><p className="mb-2 text-sm font-semibold">You</p><p className="whitespace-pre-wrap break-words">{turn.question}</p></article>
        <article className="min-w-0 rounded-lg border p-3">
          <p className="mb-2 text-sm font-semibold">Assistant</p>
          {(turn.answer || turn.status === 'completed') && <>
            <CitedAnswer content={turn.answer} sources={turn.sources} citationNote={turn.citationNote} />
            <Button variant="ghost" size="sm" className="mt-2" onClick={() => void copyAnswer(turn)}>{copied === turn.id ? 'Copied!' : 'Copy answer'}</Button>
          </>}
          {turn.status === 'pending' ? <p role="status">{turn.answer ? 'Generating…' : 'Thinking…'}</p> : turn.status !== 'completed' && <>
            <p role="status" className="text-sm text-muted-foreground">{turn.error || 'The answer did not finish.'}</p>
            {index === conversation.turns.length - 1 && <Button variant="outline" size="sm" className="mt-2" disabled={disabled} onClick={() => void send(turn)}>Retry</Button>}
          </>}
        </article>
      </div>)}
    </div>
    {!nearBottom && <button type="button" className="text-sm underline" onClick={() => setNearBottom(true)}>Scroll to latest</button>}
    {uploadBusy && <p role="status" className="text-sm">Your document is still processing. Wait until indexing finishes.</p>}
    {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}
    <form className="flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <Input aria-label="Question" value={message} maxLength={4000} onChange={(event) => setMessage(event.target.value)}
        placeholder="Ask about this document…" disabled={disabled} className="min-w-0 flex-1" />
      {sending || pending ? <Button type="button" variant="outline" disabled={stopping} onClick={() => void stop()}>{stopping ? 'Stopping…' : 'Stop'}</Button>
        : <Button type="submit" disabled={disabled || !message.trim()}>Send</Button>}
    </form>
  </section>;
}
