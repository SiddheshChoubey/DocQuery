import { createApp } from './app.js';
import { createRequireAuth } from './auth.js';
import { Queue } from 'bullmq';
import { QdrantVectorStore } from '@langchain/qdrant';
import { createChatModel, PdfEmbeddings, vectorStoreConfig } from './ai.js';
import { listDocuments } from './documents.js';
import { answerWithSources } from './citations.js';
import { deleteDocument } from './delete-document.js';
import { ConversationStore, chatError } from './conversations.js';
import { ChatService } from './chat-service.js';
import { rewriteQuery } from './query-rewrite.js';

const client = createChatModel();
const rewriteModel = createChatModel({ maxRetries: 0 });
const embeddings = new PdfEmbeddings();
const conversations = new ConversationStore();
async function retrievePassages(documentId, query, signal, passageCount = 2, ownerId) {
  const selectedDocument = (await listDocuments(vectorStoreConfig, ownerId)).find((document) => document.id === documentId);
  if (!selectedDocument) throw chatError(404, 'This document is not indexed. Refresh the document list.');
  const store = await QdrantVectorStore.fromExistingCollection(embeddings, vectorStoreConfig);
  const documents = await store.asRetriever({ k: passageCount, filter: selectedDocument.filter }).invoke(query, { signal });
  return { documents, selectedDocument };
}
const chatService = new ChatService(conversations, retrievePassages,
  (question, docs, document, options) => answerWithSources(client, question, docs, document, options),
  (question, history, options) => rewriteQuery(rewriteModel, question, history, options));
const queue = new Queue('file-upload-queue', {
  connection: {
    host: 'localhost',
    port: '6379',
  },
});

const clientOrigins = (process.env.CLIENT_ORIGINS || 'http://localhost:3000').split(',').map((origin) => origin.trim()).filter(Boolean);
const requireAuth = createRequireAuth({ secretKey: process.env.CLERK_SECRET_KEY,
  jwtKey: process.env.CLERK_JWT_KEY?.replace(/\\n/g, '\n'), issuer: process.env.CLERK_ISSUER,
  authorizedParties: clientOrigins });
const app = createApp({ requireAuth, clientOrigins, conversations, chatService, queue,
  listDocuments, deleteDocument, client, embeddings });
app.listen(8000, () => console.log('Server started on PORT:8000'));
