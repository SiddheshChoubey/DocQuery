import { Worker } from 'bullmq';
import { QdrantVectorStore } from '@langchain/qdrant';
import { PdfEmbeddings, vectorStoreConfig } from './ai.js';
import { uploadFailureMessage } from './upload-status.js';
import { requireOwner } from './conversations.js';
import { prepareDocuments } from './ingestion.js';

const worker = new Worker(
  'file-upload-queue',
  async (job) => {
    console.log(`Processing upload job ${job.id}`);
    await job.updateProgress({ phase: 'reading', completed: 0, total: 0 });
    const data = JSON.parse(job.data);
    requireOwner(data.ownerId);
    const docs = await prepareDocuments(data);
    await job.updateProgress({ phase: 'embedding', completed: 0, total: docs.length });
    const embeddings = new PdfEmbeddings(async (completed, total) => {
      await job.updateProgress({ phase: 'embedding', completed, total });
    });

    const vectorStore = await QdrantVectorStore.fromExistingCollection(
      embeddings,
      vectorStoreConfig
    );
    const vectors = await embeddings.embedDocuments(docs.map((doc) => doc.pageContent));
    await job.updateProgress({ phase: 'saving', completed: docs.length, total: docs.length });
    await vectorStore.addVectors(vectors, docs);
    await job.updateProgress({ phase: 'ready', completed: docs.length, total: docs.length });
    console.log(`All docs are added to vector store`);
  },
  {
    concurrency: 1,
    connection: {
      host: 'localhost',
      port: '6379',
    },
  }
);

worker.on('ready', () => console.log('PDF worker ready'));
worker.on('failed', (job, err) => console.error(`Upload job ${job?.id} failed. ${uploadFailureMessage(err.message)}`));
worker.on('error', () => console.error('Worker connection error. Check that Valkey is running.'));
