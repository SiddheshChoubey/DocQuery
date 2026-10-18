import express from 'express';
import cors from 'cors';
import multer from 'multer';
import { Queue } from 'bullmq';
import { QdrantVectorStore } from '@langchain/qdrant';
import { createChatModel, PdfEmbeddings, vectorStoreConfig } from './ai.js';
import { uploadFailureMessage } from './upload-status.js';
import { randomUUID } from 'node:crypto';
import { legacyDocumentId, listDocuments } from './documents.js';
import { answerWithSources } from './citations.js';
import { validateTextInput } from './ingestion.js';
import { deleteDocument } from './delete-document.js';

const client = createChatModel();
const embeddings = new PdfEmbeddings();
const asyncRoute = (handler) => (req, res, next) =>
  Promise.resolve(handler(req, res, next)).catch(next);
const queue = new Queue('file-upload-queue', {
  connection: {
    host: 'localhost',
    port: '6379',
  },
});

const storage = multer.diskStorage({
  destination: function (req, file, cb) {
    cb(null, 'uploads/');
  },
  filename: function (req, file, cb) {
    const uniqueSuffix = Date.now() + '-' + Math.round(Math.random() * 1e9);
    cb(null, `${uniqueSuffix}-${file.originalname}`);
  },
});

const upload = multer({ storage: storage });

const app = express();
app.use(cors());
app.use(express.json({ limit: '512kb' }));

app.get('/', (req, res) => {
  return res.json({ status: 'All Good!' });
});

app.get('/documents', asyncRoute(async (req, res) => {
  res.set('Cache-Control', 'no-store');
  const documents = await listDocuments();
  return res.json({ documents: documents.map(({ id, filename, chunkCount, sourceType, uploadedAt }) => ({ id, filename, chunkCount, sourceType, uploadedAt })) });
}));

app.delete('/documents/:documentId', asyncRoute(async (req, res) => {
  const documents = await listDocuments();
  const document = documents.find(({ id }) => id === req.params.documentId);
  if (!document) return res.status(404).json({ message: 'Document not found. Refresh the document list.' });
  await deleteDocument(document, documents, queue);
  return res.json({ message: 'Document deleted.', documentId: document.id });
}));

app.post('/upload/pdf', upload.single('pdf'), asyncRoute(async (req, res) => {
  if (!req.file) return res.status(400).json({ message: 'Select a PDF to upload.' });
  const documentId = randomUUID();
  const job = await queue.add(
    'file-ready',
    JSON.stringify({
      documentId,
      uploadedAt: new Date().toISOString(),
      filename: req.file.originalname,
      destination: req.file.destination,
      path: req.file.path,
    })
  );
  return res.status(202).json({ message: 'PDF uploaded; queued for indexing.', jobId: job.id, documentId });
}));

app.post('/upload/text', asyncRoute(async (req, res) => {
  const error = validateTextInput(req.body);
  if (error) return res.status(400).json({ message: error });
  const documentId = randomUUID();
  const job = await queue.add('text-ready', JSON.stringify({
    documentId, sourceType: 'text', filename: req.body.title.trim(), text: req.body.text.trim(), uploadedAt: new Date().toISOString(),
  }));
  return res.status(202).json({ message: 'Text queued for indexing.', jobId: job.id, documentId });
}));

app.get('/upload/status/:jobId', asyncRoute(async (req, res) => {
  res.set('Cache-Control', 'no-store');
  const job = await queue.getJob(req.params.jobId);
  if (!job) return res.status(404).json({ message: 'Upload job not found. Submit the document again.' });
  const state = await job.getState();
  const data = JSON.parse(job.data);
  return res.json({
    state,
    documentId: data.documentId || legacyDocumentId(data.path),
    progress: job.progress,
    message: state === 'failed' ? uploadFailureMessage(job.failedReason) : undefined,
  });
}));

app.get('/chat', asyncRoute(async (req, res) => {
  const userQuery = req.query.message;
  if (typeof userQuery !== 'string' || !userQuery.trim()) {
    return res.status(400).json({ message: 'Enter a question.' });
  }

  const documentId = req.query.documentId;
  if (typeof documentId !== 'string' || !documentId.trim()) {
    return res.status(400).json({ message: 'Select a document before asking a question.' });
  }
  const selectedDocument = (await listDocuments()).find((document) => document.id === documentId);
  if (!selectedDocument) {
    return res.status(404).json({ message: 'This document is not indexed. Refresh the document list or wait for indexing.' });
  }

  const vectorStore = await QdrantVectorStore.fromExistingCollection(
    embeddings,
    vectorStoreConfig
  );
  const ret = vectorStore.asRetriever({
    k: 2,
    filter: selectedDocument.filter,
  });
  const result = await ret.invoke(userQuery);
  if (!result.length) {
    return res.json({ message: 'No passages were found in the selected document.', docs: [], sources: [], documentId });
  }

  const answer = await answerWithSources(client, userQuery, result, selectedDocument);

  return res.json({
    documentId,
    ...answer,
    docs: result,
  });
}));

app.use((err, req, res, next) => {
  if (err.publicMessage) return res.status(err.status).json({ message: err.publicMessage });
  if (err.type === 'entity.too.large') return res.status(413).json({ message: 'Pasted text is too large. Use at most 50,000 characters.' });
  if (err.type === 'entity.parse.failed') return res.status(400).json({ message: 'Send valid JSON with a title and text.' });
  // Provider errors can contain request details; don't log credentials or PDF text.
  const status = Number(err.status) === 429 ? 429 : 500;
  console.error(`Request failed (status ${status}). Check Gemini access/quota and local services.`);
  res.status(status).json({ message: status === 429
    ? 'Gemini quota reached. Wait and retry, or check your free-tier limits in AI Studio.'
    : 'Request failed. Check the API key, model access, Qdrant, and Valkey.' });
});

app.listen(8000, () => console.log(`Server started on PORT:${8000}`));
