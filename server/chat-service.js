import { chatError, conversationContext, contextualQuery } from './conversations.js';
import { finalizeAnswer } from './citations.js';

// Stop waiting even if a provider/transport cannot cancel its underlying request.
function waitForAnswer(run, signal) {
  signal.throwIfAborted();
  let onAbort;
  const cancelled = new Promise((_, reject) => {
    onAbort = () => reject(signal.reason);
    signal.addEventListener('abort', onAbort, { once: true });
  });
  const answer = Promise.resolve().then(() => { signal.throwIfAborted(); return run(); });
  return Promise.race([answer, cancelled]).finally(() => signal.removeEventListener('abort', onAbort));
}

export class ChatService {
  constructor(store, retrieve, answer) {
    this.store = store;
    this.retrieve = retrieve;
    this.answer = answer;
    this.active = new Map();
    this.deletingDocuments = new Set();
  }

  checkDocument(documentId) {
    if (this.deletingDocuments.has(documentId)) throw chatError(409, 'This document is being deleted.');
  }

  beginDocumentDeletion(documentId) {
    this.checkDocument(documentId);
    if ([...this.active.values()].some((entry) => entry.documentId === documentId)) {
      throw chatError(409, 'Stop active answers for this document before deleting it.');
    }
    this.deletingDocuments.add(documentId);
  }

  stop(conversationId, documentId) {
    this.store.get(conversationId, documentId);
    this.active.get(conversationId)?.controller.abort();
  }

  async send(conversationId, documentId, input, signal = new AbortController().signal, onEvent) {
    this.checkDocument(documentId);
    if (this.active.has(conversationId)) throw chatError(409, 'An answer is already being generated in this chat.');
    const turn = this.store.beginTurn(conversationId, documentId, input);
    const controller = new AbortController();
    const combined = AbortSignal.any([controller.signal, signal]);
    this.active.set(conversationId, { controller, documentId });
    let partial = '';
    let sources = [];
    const progress = onEvent ? (event) => {
      combined.throwIfAborted();
      if (event.type === 'sources') sources = event.sources;
      if (event.type === 'delta') partial += event.text;
      onEvent(event);
    } : undefined;
    try {
      onEvent?.({ type: 'started', turnId: turn.id });
      const history = conversationContext(this.store.get(conversationId, documentId).turns);
      const query = contextualQuery(turn.question, history);
      const { documents, selectedDocument } = await waitForAnswer(() => this.retrieve(documentId, query, combined), combined);
      combined.throwIfAborted();
      const result = documents.length
        ? await waitForAnswer(() => this.answer(turn.question, documents, selectedDocument, { history, signal: combined, onEvent: progress }), combined)
        : { message: 'No passages were found in the selected document.', sources: [] };
      combined.throwIfAborted();
      this.store.finishTurn(turn.id, { status: 'completed', answer: result.message, sources: result.sources, citationNote: result.citationNote });
    } catch (error) {
      const stopped = combined.aborted;
      const message = stopped ? 'Answer stopped. You can retry this question.'
        : error.publicMessage || (Number(error.status) === 429
          ? 'Gemini quota reached. Wait and retry.'
          : 'Unable to generate an answer. Check Gemini and local services, then retry.');
      const incomplete = finalizeAnswer(partial, sources);
      this.store.finishTurn(turn.id, { status: stopped ? 'stopped' : 'failed', error: message,
        answer: partial, sources: incomplete.sources, citationNote: partial ? 'Incomplete answer. ' + (incomplete.citationNote || '') : null });
    } finally {
      controller.abort();
      this.active.delete(conversationId);
    }
    return this.store.get(conversationId, documentId);
  }
}
