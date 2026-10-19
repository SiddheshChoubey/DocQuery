import { Router } from 'express';
import { chatError } from './conversations.js';

export function createChatRouter(store, service, listDocuments) {
  const router = Router();
  router.use((_req, res, next) => { res.set('Cache-Control', 'no-store'); next(); });
  const route = (handler) => (req, res, next) => Promise.resolve().then(() => handler(req, res)).catch(next);
  const documentId = (value) => {
    if (typeof value !== 'string' || !value.trim() || value.length > 200) throw chatError(400, 'Select a document.');
    return value;
  };

  router.get('/', route((req, res) => res.json({ conversations: store.list(documentId(req.query.documentId), req.ownerId) })));
  router.post('/', route(async (req, res) => {
    const id = documentId(req.body?.documentId);
    if (!(await listDocuments(req.ownerId)).some((document) => document.id === id)) throw chatError(404, 'This document is not indexed.');
    service.checkDocument(id);
    res.status(201).json({ conversation: store.create(id, req.ownerId) });
  }));
  router.get('/:id', route((req, res) => res.json({ conversation: store.get(req.params.id, documentId(req.query.documentId), req.ownerId) })));
  router.delete('/:id', route((req, res) => {
    const id = documentId(req.query.documentId);
    store.get(req.params.id, id, req.ownerId);
    if (service.active.has(req.params.id)) throw chatError(409, 'Stop the answer before deleting this chat.');
    store.remove(req.params.id, id, req.ownerId);
    res.json({ message: 'Conversation deleted.' });
  }));
  router.post('/:id/stop', route((req, res) => {
    service.stop(req.params.id, documentId(req.body?.documentId), req.ownerId);
    res.json({ message: 'Stop requested.' });
  }));
  router.post('/:id/messages', route(async (req, res) => {
    const id = documentId(req.body?.documentId);
    if (req.body.retry !== undefined && typeof req.body.retry !== 'boolean') throw chatError(400, 'Invalid retry flag.');
    const controller = new AbortController();
    const disconnected = () => { if (!res.writableEnded) controller.abort(); };
    res.on('close', disconnected);
    const streaming = req.get('accept') === 'application/x-ndjson';
    const emit = (event) => {
      if (res.destroyed) return;
      if (!res.headersSent) {
        res.set({ 'Content-Type': 'application/x-ndjson; charset=utf-8', 'X-Accel-Buffering': 'no' });
        res.flushHeaders();
      }
      res.write(JSON.stringify(event) + '\n');
    };
    try {
      const conversation = await service.send(req.params.id, id, req.body, controller.signal, streaming ? emit : undefined, req.ownerId);
      if (!res.destroyed) {
        if (streaming) { emit({ type: 'done', conversation }); res.end(); }
        else res.json({ conversation });
      }
    } finally {
      res.off('close', disconnected);
    }
  }));
  return router;
}
